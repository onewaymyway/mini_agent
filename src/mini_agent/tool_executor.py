"""
tool_executor.py — 工具执行器

职责：
- 权限检查（PermissionGuard）
- 工具调用（ToolRegistry / MCPManager）
- 结果截断（按工具类型分策略）
- 结果缓存（ToolResultCache）
- 文件变化注册（FileWatcher）
- 工具统计（SessionStats）
- Hook 拦截（PreToolUse / PostToolUse）
- Lesson 规则触发（LessonRuleEngine）
- 编辑回调（on_edit_detected）
- Turn 内去重（SYS-DEDUP）
- 因果链追踪（SessionTracer.record_tool_event）

从 Agent 中拆出，Agent 只需持有一个 ToolExecutor 实例并调用 execute_all()。
"""

from __future__ import annotations

import hashlib as _hashlib
import json as _json
import re
from typing import TYPE_CHECKING, Optional

import mini_agent.ui.renderer as R

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.llm import LLMResponse
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import ToolRegistry
    from mini_agent.perception.tool_cache import ToolResultCache
    from mini_agent.perception.file_watcher import FileWatcher
    from mini_agent.config import SessionStats

# 幂等工具集合（非写操作），才参与 turn 内去重（SYS-DEDUP）
_DEDUP_TOOLS = frozenset({"read_file", "grep", "glob", "list_dir", "web_search", "view_raw_result"})


class ToolExecutor:
    """
    执行一批工具调用并返回结果字符串列表。
    所有副作用（权限、缓存、统计、文件追踪、hooks、lesson、dedup、tracer）
    都在这里统一管理。

    关键参数：
      tracer         — 可选的 SessionTracer，用于记录因果链（Stage 6）
      turn_id_getter — 返回当前 turn 计数的 callable，供 tracer 使用
      history_getter — 返回当前 _history 列表的 callable，用于 SYS-DEDUP
                       扫描本 turn 内已有的 tool_result，跨 LLM 调用去重
    """

    def __init__(
        self,
        cfg: "AppConfig",
        registry: "ToolRegistry",
        guard: "PermissionGuard",
        stats: "SessionStats",
        tool_cache: Optional["ToolResultCache"] = None,
        file_watcher: Optional["FileWatcher"] = None,
        file_changes_list: Optional[list] = None,   # _pending_file_changes 的引用
        file_changes_lock=None,
        lesson_engine=None,        # Optional[LessonRuleEngine]，Stage 1.2
        memory_sink=None,          # Optional[MemoryBackend]，lesson 写入目标（通常是项目级 memory）
        on_edit_detected=None,     # Optional[Callable[[dict], None]]，Stage 1.5
        tracer=None,               # Optional[SessionTracer]，Stage 6
        turn_id_getter=None,       # Optional[Callable[[], int]]，供 tracer 用
        history_getter=None,       # Optional[Callable[[], list]]，供 SYS-DEDUP 跨调用去重
        reminder_mgr=None,         # Optional[ReminderManager]，[具身改进 A3] 前馈控制
        inject_reminder=None,      # Optional[Callable[[Reminder], None]]，由 Agent 提供的注入回调
        llm_client=None,           # Optional[LLMClient]，[SYS-SMARTTRIM] 智能摘要用
        raw_result_store=None,     # Optional[RawResultStore]，[SYS-RAWSTORE] 原始输出留存
        persona_getter=None,       # Optional[Callable[[], Optional[str]]]，角色扮演系统 [二期]
    ) -> None:
        self.cfg = cfg
        self.registry = registry
        self.guard = guard
        self.stats = stats
        self.tool_cache = tool_cache
        self.file_watcher = file_watcher
        self._pending_file_changes = file_changes_list  # 共享引用
        self._file_changes_lock = file_changes_lock
        self._mcp_manager = None  # 由 Agent 在 _init_components 后注入
        self._lesson_engine = lesson_engine
        self._memory_sink = memory_sink
        self.on_edit_detected = on_edit_detected
        self._tracer = tracer
        self._turn_id_getter = turn_id_getter
        self._history_getter = history_getter
        self._reminder_mgr = reminder_mgr
        self._inject_reminder = inject_reminder
        self._llm_client = llm_client
        self._raw_result_store = raw_result_store
        self._persona_getter = persona_getter

    def execute_all(
        self,
        response: "LLMResponse",
        history: Optional[list] = None,
    ) -> tuple[list, list[str]]:
        """
        执行 response 中所有工具调用，返回 (tool_calls, result_strs)。

        参数：
          history — 当前 turn 的历史消息列表（可选）。
                    传入时启用 SYS-DEDUP 跨 LLM 调用去重；
                    未传入时仍做 batch 内去重。
                    优先使用构造时注入的 history_getter；此参数作为兼容入口。
        """
        result_strs: list[str] = []

        # ── [SYS-DEDUP] 初始化去重状态 ────────────────────────────────────────
        # _seen_this_batch  : 本次 execute_all 调用内已见的 (name, hash) → result
        # _seen_in_history  : 本 turn 内跨 LLM 调用已有的 tool_result（从 history 扫出）
        _seen_this_batch: dict[tuple[str, str], str] = {}
        _seen_in_history: dict[tuple[str, str], str] = {}

        _TR_OPEN = "<tool_result>"
        _TR_CLOSE = "</tool_result>"
        try:
            from mini_agent.history.entry import HType as _HType
            _have_htype = True
        except Exception:
            _have_htype = False

        # 解析 history：优先 getter，其次参数
        _history: Optional[list] = None
        if self._history_getter is not None:
            try:
                _history = self._history_getter()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tool_executor')
                pass
        if _history is None:
            _history = history

        if _history is not None:
            for _msg in reversed(_history):
                if _msg.get("role") != "user":
                    continue
                _mt = _msg.get("_type")
                _c = _msg.get("content", "")
                _is_tr = (
                    (_mt == _HType.TOOL_RESULT if _have_htype else False)
                    if _mt is not None
                    else (isinstance(_c, str) and _c.startswith(_TR_OPEN))
                )
                if not _is_tr:
                    break  # 碰到非 tool_result 消息即停，本 turn 已全部扫完
                if isinstance(_c, str) and '"name"' in _c and '"output"' in _c:
                    try:
                        _start = len(_TR_OPEN) + 1
                        _end = _c.rfind(_TR_CLOSE) - 1
                        if _end > _start:
                            _entry = _json.loads(_c[_start:_end])
                            _tname = _entry.get("name", "")
                            _tout = _entry.get("output", "")
                            if _tname and not _tout.startswith("[same result"):
                                _h = _hashlib.md5(_tout.encode()).hexdigest()[:12]
                                _seen_in_history[(_tname, _h)] = _tout
                    except Exception as _mini_agent_exc:
                        from mini_agent.errors import log_exception
                        log_exception(_mini_agent_exc, where='mini_agent.tool_executor')
                        pass

        # ── 当前 turn_id（供 tracer 使用）───────────────────────────────────
        _turn_id: int = 0
        if self._turn_id_getter is not None:
            try:
                _turn_id = self._turn_id_getter()
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tool_executor')
                pass

        # [SYS-HOOKS] hook manager 引用在整个 batch 中复用（避免每次 tool call 重复查询）
        from mini_agent.hooks import get_hook_manager
        hook_mgr = get_hook_manager()

        for tc in response.tool_calls:
            R.print_tool_call(tc.name, tc.input, verbose=self.cfg.verbose)
            self.stats.tool_calls += 1

            # [具身改进 A3] pre_tool reminder：前馈控制，在工具真正执行前
            # （甚至在 PreToolUse hook 和权限检查之前）注入警示，而不是
            # 等出错/出结果后再补救。失败不应阻断主流程，故静默吞掉异常。
            if self._reminder_mgr is not None and self._inject_reminder is not None:
                try:
                    for _r in self._reminder_mgr.check_pre_tool(tc.name, tc.input):
                        self._inject_reminder(_r)
                except Exception as _mini_agent_exc:
                    from mini_agent.errors import log_exception
                    log_exception(_mini_agent_exc, where='mini_agent.tool_executor')
                    pass

            # [SYS-HOOKS] PreToolUse：可阻断或修改工具输入
            tool_input = tc.input
            if hook_mgr is not None:
                pre = hook_mgr.run("PreToolUse", {"tool_name": tc.name, "tool_input": tool_input}, tool_name=tc.name)
                if pre.blocked:
                    result_str = f"[blocked by hook: {pre.reason or 'PreToolUse hook denied'}]"
                    R.print_tool_error(tc.name, pre.reason or "blocked by PreToolUse hook")
                    if self.cfg.tool_stats_enabled:
                        self.stats.record_tool_call(tc.name, False, 0)
                    result_strs.append(result_str)
                    continue
                if pre.modified_input:
                    tool_input = pre.modified_input

            # [二期] 角色扮演 allowed_tools 白名单：persona 声明了非空 allowed_tools
            # 时，不在名单内的工具直接拒绝，不进入 guard.check()（代码强制拦截，
            # 不依赖角色文件内容或模型自觉）。空 allowed_tools = 不限制。
            if self._persona_getter is not None:
                try:
                    _persona_name = self._persona_getter()
                except Exception:
                    _persona_name = None
                if _persona_name:
                    try:
                        from mini_agent.orchestrator.persona_profiles import get_persona_loader
                        _p_loader = get_persona_loader()
                        _persona = _p_loader.get(_persona_name) if _p_loader else None
                    except Exception:
                        _persona = None
                    if _persona is not None and _persona.allowed_tools and tc.name not in _persona.allowed_tools:
                        _reason = (
                            f"tool '{tc.name}' is not permitted under persona "
                            f"'{_persona.display_name}' (allowed_tools: {_persona.allowed_tools})"
                        )
                        result_str = f"[blocked by persona allowed_tools: {_reason}]"
                        R.print_tool_error(tc.name, _reason)
                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, False, 0)
                        result_strs.append(result_str)
                        continue

            allowed = self.guard.check(tc.name, tool_input)

            # [SYS-LESSON] (e)dit 审批编辑接入（Stage 1.5）：check() 内部可能
            # in-place 修改了 tool_input（用户编辑了命令/参数），检测并通过回调
            # 转交给 Agent 层处理（写入 user_correction 消息 + 触发纠正检测）。
            # 用回调而非直接操作 history，避免 ToolExecutor 反向依赖 Agent。
            if self.on_edit_detected is not None:
                edit = self.guard.pop_last_edit()
                if edit is not None:
                    try:
                        self.on_edit_detected(edit)
                    except Exception:
                        pass  # 编辑事件处理失败不应影响工具调用主流程

            if not allowed:
                result_str = "[Tool call denied by user]"
                R.print_tool_error(tc.name, "denied by user")
                if self.cfg.tool_stats_enabled:
                    self.stats.record_tool_call(tc.name, False, 0)
            else:
                # [SYS-TOOLCACHE] 检查缓存
                cached = None
                if self.tool_cache:
                    cached = self.tool_cache.get(tc.name, tool_input)

                if cached is not None:
                    result_str = cached
                    R.print_tool_result(tc.name, f"[cache] {result_str[:80]}...")
                    if self.cfg.tool_stats_enabled:
                        self.stats.record_tool_call(tc.name, True, len(result_str))
                else:
                    try:
                        # [SYS-MCP] MCP 工具路由：MCP 工具由 MCPManager 代理调用
                        if (
                            self._mcp_manager is not None
                            and self._mcp_manager.is_mcp_tool(tc.name)
                        ):
                            result = self._mcp_manager.call_tool_sync(tc.name, tool_input)
                        else:
                            result = self.registry.call(tc.name, tool_input)
                        result_str = str(result) if not isinstance(result, str) else result

                        # [SYS-TRIM] 工具调用结果截断（按工具类型分策略）
                        result_str = self._trim_result(tc.name, result_str, tool_input)

                        R.print_tool_result(
                            tc.name, result_str,
                            truncate=None if getattr(self.cfg, "raw_output", False) else 2000,
                        )

                        # [SYS-TOOLCACHE] 写入缓存
                        if self.tool_cache:
                            self.tool_cache.put(tc.name, tool_input, result_str)

                        # [SYS-TOOLCACHE] 写操作执行成功后，立即使目标文件缓存失效。
                        # 覆盖的场景：
                        #   write_file / create_file / patch_file / delete_file
                        # 时机：工具调用成功后（result_str 不以 "[error" 开头）才失效，
                        # 避免把失败的写入也当作有效失效触发器。
                        if (
                            self.tool_cache
                            and tc.name in ("write_file", "create_file", "patch_file", "delete_file")
                            and not result_str.startswith("[error")
                        ):
                            _target_path = tool_input.get("path", "")
                            if _target_path:
                                self.tool_cache.invalidate_file(_target_path)

                        # [SYS-WATCH] 注册 read_file 的文件（供后台线程追踪）
                        if self.file_watcher and tc.name == "read_file":
                            _path = tool_input.get("path", "")
                            if _path:
                                self.file_watcher.register(_path, result_str)

                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, True, len(result_str))
                    except Exception as e:
                        result_str = f"[tool error: {e}]"
                        R.print_tool_error(tc.name, str(e))
                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, False, 0)
                        # [SYS-HOOKS] PostToolUseFailure：工具抛异常后通知
                        if hook_mgr is not None:
                            try:
                                hook_mgr.run(
                                    "PostToolUseFailure",
                                    {
                                        "tool_name": tc.name,
                                        "tool_input": tool_input,
                                        "error": str(e),
                                    },
                                    tool_name=tc.name,
                                )
                            except Exception as _mini_agent_exc:
                                from mini_agent.errors import log_exception
                                log_exception(_mini_agent_exc, where='mini_agent.tool_executor')
                                pass

            # [SYS-HOOKS] PostToolUse：可注入额外上下文（拼接到结果后）
            if hook_mgr is not None:
                post = hook_mgr.run(
                    "PostToolUse",
                    {"tool_name": tc.name, "tool_input": tool_input, "tool_result": result_str},
                    tool_name=tc.name,
                )
                if post.context:
                    result_str = result_str + f"\n\n[hook note] {post.context}"

            # [SYS-LESSON] 规则触发：连续失败 / 权限拒绝后重试成功（Stage 1.2）
            # 不依赖 LLM，纯规则判断；命中时立即写入记忆，不等 SessionEnd。
            if self._lesson_engine is not None and self._memory_sink is not None:
                try:
                    from mini_agent.perception.lesson_rules import is_tool_error
                    entry = self._lesson_engine.observe(
                        tool_name=tc.name,
                        tool_input=tool_input,
                        allowed=allowed,
                        result_str=result_str,
                        is_error=is_tool_error(result_str),
                    )
                    if entry is not None:
                        self._memory_sink.add(entry)
                except Exception:
                    pass  # lesson 生成失败不应影响工具调用主流程

            # [SYS-DEDUP] Turn 内幂等工具结果去重
            # 非写操作工具（read_file / grep / glob / list_dir / web_search）才做去重；
            # 写操作或已是错误/占位符时跳过，确保每次执行的副作用都能被记录。
            if tc.name in _DEDUP_TOOLS and not result_str.startswith("["):
                _h = _hashlib.md5(result_str.encode()).hexdigest()[:12]
                _key = (tc.name, _h)
                if _key in _seen_this_batch or _key in _seen_in_history:
                    _short = _json.dumps(tc.input, ensure_ascii=False)[:60]
                    _dedup_str = f"[same result as above: {tc.name}({_short})]"
                    R.print_info(
                        f"[dedup] {tc.name} result deduplicated "
                        f"({len(result_str)} chars → {len(_dedup_str)} chars)"
                    )
                    result_str = _dedup_str
                else:
                    _seen_this_batch[_key] = result_str

            # [Stage 6 / 6.4] 工具调用因果链记录
            if self._tracer is not None:
                try:
                    from mini_agent.perception.observability import classify_error
                    from mini_agent.perception.lesson_rules import is_tool_error as _ite
                    _is_err = _ite(result_str)
                    _err_cat = classify_error(result_str) if _is_err else None
                    # 因果链：检测"失败后重试成功"——在已有 result_strs 里找同名工具的失败记录
                    _resolves_seq: Optional[int] = None
                    if not _is_err:
                        from mini_agent.perception.lesson_rules import is_tool_error as _ite2
                        for _prev_idx, _prev_r in enumerate(result_strs):
                            if _ite2(_prev_r):
                                _resolves_seq = _prev_idx + 1  # 1-based
                    self._tracer.record_tool_event(
                        turn_id=_turn_id,
                        sequence_in_turn=len(result_strs) + 1,
                        tool_name=tc.name,
                        result_str=result_str,
                        is_error=_is_err,
                        error_category=_err_cat,
                        resolves_seq=_resolves_seq,
                    )
                except Exception:
                    pass  # tracer 失败不影响主流程

            result_strs.append(result_str)

        # [SYS-HOOKS] PostToolBatch：一批工具全部执行完成后触发（通知型，不可阻止）
        if hook_mgr is not None and result_strs:
            try:
                hook_mgr.run(
                    "PostToolBatch",
                    {
                        "tool_names": [tc.name for tc in response.tool_calls],
                        "results": result_strs,
                        "error_count": sum(
                            1 for r in result_strs
                            if r.startswith("[tool error") or r.startswith("[blocked")
                        ),
                    },
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.tool_executor')
                pass

        return response.tool_calls, result_strs

    def _trim_result(self, tool_name: str, result: str, tool_input: Optional[dict] = None) -> str:
        """
        [SYS-TRIM] 按工具类型分策略截断长结果，策略参数可通过 config 调整。
        raw_output 模式下跳过所有截断，返回完整结果。

        [SYS-SMARTTRIM] / [SYS-RAWSTORE] 扩展：
          - 超过 smart_summary_threshold 且开启 smart_summary 时，改用 LLM
            提炼关键信息（而非纯规则截断）；LLM 调用失败自动降级为规则截断。
          - 只要发生了截断/摘要（返回值 != 原文），原始完整结果都会存入
            RawResultStore，并在返回文本末尾附上 result_id 提示，供 agent
            通过 view_raw_result 工具按需回看完整原文。
        """
        if not self.cfg.tool_result_trim_enabled:
            return result
        threshold = self.cfg.tool_result_trim_threshold
        if len(result) <= threshold:
            return result

        trim_cfg = self.cfg.tool_trim
        smart_threshold = getattr(trim_cfg, "smart_summary_threshold", threshold)
        use_smart_summary = (
            getattr(trim_cfg, "smart_summary_enabled", False)
            and self._llm_client is not None
            and len(result) > smart_threshold
        )

        if use_smart_summary:
            summarized = self._smart_summarize(tool_name, tool_input or {}, result)
            if summarized is not None:
                return self._remember_raw(tool_name, result, summarized)
            # LLM 摘要失败：静默降级，继续走下面的规则截断

        trimmed = self._rule_trim(tool_name, result)
        return self._remember_raw(tool_name, result, trimmed)

    def _remember_raw(self, tool_name: str, original: str, trimmed: str) -> str:
        """
        [SYS-RAWSTORE] 若发生了实质性截断/摘要（trimmed != original），
        把完整原文存入 RawResultStore，并在返回文本后附上取回提示。
        raw_result_store 未注入或功能关闭时原样返回 trimmed，不受影响。
        """
        if trimmed == original:
            return trimmed
        if not getattr(self.cfg.tool_trim, "raw_store_enabled", True) or self._raw_result_store is None:
            return trimmed
        try:
            result_id = self._raw_result_store.put(original, tool_name=tool_name)
        except Exception:
            return trimmed
        return (
            f"{trimmed}\n\n"
            f"[full output stored — {len(original)} chars total. "
            f"Use view_raw_result(result_id=\"{result_id}\") to inspect the original, "
            f"optionally with start_line/end_line.]"
        )

    def _smart_summarize(self, tool_name: str, tool_input: dict, result: str) -> Optional[str]:
        """
        [SYS-SMARTTRIM] 调用 LLM 从超长结果里提炼关键信息。
        任何异常（超时、网络错误、prompt 加载失败等）都返回 None，
        由调用方静默降级到规则截断——摘要失败绝不能阻塞工具调用主流程。
        """
        trim_cfg = self.cfg.tool_trim
        max_input_chars = getattr(trim_cfg, "smart_summary_max_input_chars", 60000)
        if len(result) > max_input_chars:
            # 原文本身太大，喂给摘要模型也不现实，直接降级到规则截断
            return None
        try:
            from mini_agent.prompts import pm

            system = pm.render("system/tool_result_summarizer")
            user_msg = pm.render(
                "user/tool_result_summary_request",
                tool_name=tool_name,
                tool_input=_json.dumps(tool_input, ensure_ascii=False),
                tool_output=result,
            )
            client = self._llm_client
            model_override = getattr(trim_cfg, "smart_summary_model", "") or None
            if model_override and hasattr(client, "with_model"):
                # 若 LLMClient 支持临时切换模型（更便宜/更快），优先使用
                try:
                    client = client.with_model(model_override)
                except Exception:
                    client = self._llm_client

            response = client.chat_with_retry(
                messages=[{"role": "user", "content": user_msg}],
                system=system,
                tools=[],
                max_retries=2,
            )
            summary_text = (response.text or "").strip()
            if not summary_text:
                return None
            return (
                f"[LLM-extracted summary of {tool_name} output "
                f"({len(result)} chars original)]\n{summary_text}"
            )
        except Exception:
            return None

    def _rule_trim(self, tool_name: str, result: str) -> str:
        """
        [SYS-TRIM] 原有的按工具类型分策略的规则截断逻辑（默认策略 / 智能摘要
        的兜底降级目标）。调用方已确保 len(result) > threshold。
        """
        threshold = self.cfg.tool_result_trim_threshold
        lines = result.splitlines()
        total = len(lines)

        if tool_name == "bash":
            # [SYS-TRIM] 智能截断：优先保留错误/失败关键行，再做头尾补充
            # 关键行模式：pytest 失败、traceback、error 行、exit code 等
            _KEY_PATTERNS = re.compile(
                r"(FAILED|ERROR|Error|Traceback|assert|AssertionError"
                r"|raised|exception|exit code [^0]"
                r"|✗|✘|FAIL|PASS|passed|failed|warning)",
                re.IGNORECASE,
            )
            key_indices = [i for i, l in enumerate(lines) if _KEY_PATTERNS.search(l)]

            tail_ratio = getattr(self.cfg.tool_trim, "bash_tail_ratio", 0.6)
            tail_n = max(8, int(total * tail_ratio))
            head_n = max(5, int(total * 0.2))

            # 在 head + tail 之外，额外插入关键行（最多 30 行）
            key_extra = [i for i in key_indices
                         if i >= head_n and i < total - tail_n][:30]

            if head_n + tail_n >= total and not key_extra:
                pass  # 不需要截断，走下面通用逻辑
            elif total > 20:
                kept: list[str] = []
                kept.extend(lines[:head_n])

                if key_extra:
                    # 将关键行分组成连续块，每块加分隔符
                    groups: list[list[int]] = []
                    for ki in key_extra:
                        if groups and ki == groups[-1][-1] + 1:
                            groups[-1].append(ki)
                        else:
                            groups.append([ki])
                    omitted_before_first = key_extra[0] - head_n
                    if omitted_before_first > 0:
                        kept.append(f"... [{omitted_before_first} lines omitted] ...")
                    for gi, grp in enumerate(groups):
                        kept.extend(lines[grp[0]: grp[-1] + 1])
                        if gi < len(groups) - 1:
                            between = groups[gi + 1][0] - grp[-1] - 1
                            if between > 0:
                                kept.append(f"... [{between} lines omitted] ...")
                    omitted_after_last = (total - tail_n) - key_extra[-1] - 1
                    if omitted_after_last > 0:
                        kept.append(f"... [{omitted_after_last} lines omitted] ...")
                else:
                    omitted = total - head_n - tail_n
                    if omitted > 0:
                        kept.append(f"... [{omitted} lines omitted] ...")

                kept.extend(lines[-tail_n:])
                return "\n".join(kept)

        elif tool_name == "read_file":
            window = getattr(self.cfg, "tool_trim_read_window_lines", 0)
            if window == 0:
                window = min(total, max(30, threshold // 60))
            if total > 30 and window < total:
                head_n = window // 2
                tail_n = window - head_n
                omitted = total - head_n - tail_n
                return (
                    "\n".join(lines[:head_n])
                    + f"\n... [{omitted} lines omitted — use start_line/end_line to read specific range] ...\n"
                    + "\n".join(lines[-tail_n:])
                )

        elif tool_name in ("grep", "glob"):
            max_lines = getattr(self.cfg, "tool_trim_grep_max_lines", 50)
            if total > max_lines:
                omitted = total - max_lines
                return (
                    "\n".join(lines[:max_lines])
                    + f"\n... [{omitted} more matches omitted] ..."
                )

        # 通用策略
        if total > 30:
            head_n, tail_n = 15, 5
            omitted = total - head_n - tail_n
            if omitted > 0:
                return (
                    "\n".join(lines[:head_n])
                    + f"\n... [{omitted} lines omitted] ...\n"
                    + "\n".join(lines[-tail_n:])
                )

        return result[:threshold] + f"\n... [{len(result)-threshold} chars omitted]"