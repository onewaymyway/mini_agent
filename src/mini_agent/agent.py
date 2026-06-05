"""
Agent loop.
Orchestrates the conversation with Claude, dispatches tool calls,
manages history, and streams responses.

与具体 LLM 的交互全部通过 llm.LLMClient 接口进行，
切换 provider 只需修改 LLMConfig.provider，不改动 Agent 代码。
"""

from __future__ import annotations

import copy
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    create_client, LLMError,
)
from mini_agent.llm.retry import RetryPolicy, default_retry_policy, no_retry_policy
from mini_agent.permissions import PermissionGuard
from mini_agent.skills import SkillLoader
from mini_agent.tools import ToolRegistry, get_default_registry
from mini_agent.session import SessionManager, Session
import mini_agent.ui.renderer as R
from mini_agent.perception.token_counter import estimate_messages_tokens
from mini_agent.perception.project_scanner import ProjectScanner
from mini_agent.perception.file_watcher import FileWatcher
from mini_agent.perception.tool_cache import ToolResultCache
from mini_agent.perception.memory_store import MemoryStore, MemoryEntry


class Agent:
    """
    Stateful agent that maintains conversation history and runs the agentic loop.

    Typical usage:
        agent = Agent(cfg, registry, skill_loader, guard)
        agent.run_turn("Fix the bug in app.py")
    """

    def __init__(
        self,
        cfg: AppConfig,
        registry: Optional[ToolRegistry] = None,
        skill_loader: Optional[SkillLoader] = None,
        guard: Optional[PermissionGuard] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.cfg = cfg
        self.registry = registry or get_default_registry()
        self.skill_loader = skill_loader
        self.guard = guard or PermissionGuard(
            auto_approve=cfg.auto_approve,
            sandbox=cfg.sandbox,
            project_root=cfg.project_root,
        )
        self.stats = SessionStats()
        self._history: list[dict] = []
        # LLMClient 可从外部注入（便于测试），否则从 AppConfig 自动创建
        self._llm: LLMClient = llm_client or create_client(
            LLMConfig.from_app_config(cfg)
        )
        # Session 持久化
        self._session_mgr: Optional[SessionManager] = None
        self._session: Optional[Session] = None
        self._init_session()

        # ── [SYS-RETRY] LLM 重试策略初始化 ──────────────────────────────────
        # 默认使用 EmptyOutputCondition（空输出即重试），可通过 cfg 调整参数，
        # 也可在实例化后替换 self._retry_policy 以使用自定义策略。
        _retry_max = getattr(cfg, "llm_retry_max", 2)
        _retry_delay = getattr(cfg, "llm_retry_delay", 0.0)
        self._retry_policy: RetryPolicy = (
            default_retry_policy(max_retries=_retry_max, retry_delay=_retry_delay)
            if _retry_max > 0
            else no_retry_policy()
        )

        # ── [SYS-UNDO] 手动重试 / 回退快照 ──────────────────────────────────
        # 每次 run_turn 开始时保存一份快照，支持：
        #   retry_last_turn()  — 丢弃本轮结果，用同一用户消息重新调用模型
        #   rollback_turn()    — 完全回退到本轮开始前的状态（撤销整个 turn）
        #
        # 快照格式：
        #   {
        #     "history":      list[dict],   # _history 的深拷贝
        #     "stats_turns":  int,          # stats.turns
        #     "stats_input":  int,          # stats.input_tokens
        #     "stats_output": int,          # stats.output_tokens
        #     "stats_tool":   int,          # stats.tool_calls
        #   }
        self._turn_snapshot: Optional[dict] = None

        # ── [SYS-SKILL-TOOL] 注册 skill 管理工具 ──────────────────────────────
        # 让模型可以主动调用 skill_list / skill_activate / skill_deactivate，
        # 而不是依赖关键词自动匹配（关键词匹配作为辅助，两者并存）。
        if self.skill_loader:
            from mini_agent.tools.skill_manager import (
                register_skill_tools,
                register_compact_tool,
                register_skill_stats_tool,
            )
            register_skill_tools(self.registry, self.skill_loader)
            register_compact_tool(self.registry, self)          # 需要 agent 实例
            register_skill_stats_tool(self.registry, self.skill_loader)

        # ── 感知与记忆子系统（按开关初始化）────────────────────────────────
        # [SYS-PROJ] 项目结构感知
        self._project_snapshot: Optional[str] = None
        if cfg.project_scan_enabled:
            try:
                snap = ProjectScanner().scan(cfg.project_root)
                self._project_snapshot = snap.to_prompt_block()
            except Exception as e:
                R.print_warning(f"[perception] project scan failed: {e}")

        # [SYS-WATCH] 文件变化感知
        self._file_watcher: Optional[FileWatcher] = (
            FileWatcher() if cfg.file_watch_enabled else None
        )

        # [SYS-TOOLCACHE] 工具调用结果缓存
        self._tool_cache: Optional[ToolResultCache] = (
            ToolResultCache() if cfg.tool_cache_enabled else None
        )

        # [SYS-MEMORY] 跨 session 长期记忆
        self._memory: Optional[MemoryStore] = None
        if cfg.memory_enabled:
            store_path = cfg.memory_store_path or (cfg.project_root / ".agent" / "memory.jsonl")
            self._memory = MemoryStore(path=store_path)

    # ── Session 管理 ──────────────────────────────────────────────────────────────

    def _init_session(self) -> None:
        """初始化 SessionManager，创建新 Session（尚未写文件）。"""
        if not getattr(self.cfg, "auto_save_session", True):
            return
        try:
            session_dir = getattr(self.cfg, "session_dir", None)
            if session_dir is None:
                session_dir = self.cfg.project_root / "sessions"
            fmt = getattr(self.cfg, "session_fmt", "json")
            self._session_mgr = SessionManager(session_dir=session_dir, fmt=fmt)
            self._session = self._session_mgr.new_session(
                provider=getattr(self.cfg, "llm_provider", "unknown"),
                model=self.cfg.model,
            )
        except Exception as e:
            R.print_warning(f"Session init failed: {e}")

    def save_session(self) -> Optional[str]:
        """保存当前对话历史到 Session 文件，返回路径，失败返回 None。"""
        if not self._session_mgr or not self._session:
            return None
        try:
            stats = {
                "turns":            self.stats.turns,
                "input_tokens":     self.stats.input_tokens,
                "output_tokens":    self.stats.output_tokens,
                "tool_calls":       self.stats.tool_calls,
                "tool_stats":       self.stats.tool_stats,
                "skill_activations": self.stats.skill_activations,
            }
            path = self._session_mgr.save(
                self._session,
                history=self._history,
                stats=stats,
            )

            # [SYS-SUMMARY] 达到门槛后生成 session 摘要
            if (self.cfg.session_summary_enabled
                    and self.stats.turns >= self.cfg.session_summary_min_turns
                    and not getattr(self._session, "summary", "")):
                self._generate_and_save_summary(str(path))

            return str(path)
        except Exception as e:
            R.print_warning(f"Session save failed: {e}")
            return None

    def _generate_and_save_summary(self, session_path: str) -> None:
        """[SYS-SUMMARY] 用 LLM 生成 session 摘要，写回 session JSON，并写入长期记忆。"""
        import json as _json
        try:
            user_turns = [
                m["content"] for m in self._history
                if m.get("role") == "user"
                and isinstance(m.get("content"), str)
                and not m["content"].startswith("<tool_result")
                and not m["content"].startswith("[Compressed")
            ]
            if not user_turns:
                return

            turns_text = "\n".join(f"- {t[:200]}" for t in user_turns[:10])
            prompt = (
                "Summarize this conversation in 2-3 sentences. "
                "Focus on: what was accomplished, key decisions made, and important outcomes. "
                "Be concise. Respond with only the summary.\n\n"
                f"User messages:\n{turns_text}"
            )
            from mini_agent.llm.system_tool_call import convert_tool_use_to_text
            resp = self._llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system="You are a concise summarizer.",
                tools=[],
            )
            summary = resp.text.strip()
            if not summary:
                return

            # 写回 session JSON
            p = __import__("pathlib").Path(session_path)
            if p.exists():
                try:
                    data = _json.loads(p.read_text(encoding="utf-8"))
                    data["summary"] = summary
                    p.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    if self._session:
                        self._session.summary = summary  # type: ignore[attr-defined]
                except Exception:
                    pass

            # [SYS-MEMORY] 写入长期记忆
            if self._memory and self._session:
                import re as _re
                tags = list({
                    w.lower() for w in _re.findall(r"[a-zA-Z一-鿿]{3,}", summary)
                })[:8]
                entry = MemoryEntry(
                    session_id=self._session.id,
                    summary=summary,
                    key_outcomes=user_turns[:3],
                    tags=tags,
                    model=self.cfg.model,
                )
                self._memory.add(entry)
        except Exception as e:
            R.print_warning(f"[summary] generation failed: {e}")

    def load_session(self, session_id: str) -> bool:
        """按 session_id（或其前缀）加载历史到当前 agent，返回是否成功。"""
        if not self._session_mgr:
            return False
        session = self._session_mgr.load(session_id)
        if session is None:
            return False
        self._session = session
        self._history = list(session.history)
        self.stats.turns         = session.stats.get("turns", 0)
        self.stats.input_tokens  = session.stats.get("input_tokens", 0)
        self.stats.output_tokens = session.stats.get("output_tokens", 0)
        self.stats.tool_calls    = session.stats.get("tool_calls", 0)
        return True

    @property
    def session_id(self) -> Optional[str]:
        return self._session.id if self._session else None

    @property
    def session_file(self) -> Optional[str]:
        return self._session.file_path if self._session else None

    @property
    def session_manager(self) -> Optional[SessionManager]:
        return self._session_mgr

    # ── Public interface ───────────────────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        return list(self._history)

    @property
    def llm_client(self) -> LLMClient:
        return self._llm

    def clear_history(self) -> None:
        self._history.clear()

    def switch_provider(self, llm_config: LLMConfig) -> None:
        """
        运行时切换 LLM provider，不影响对话历史。

        Example:
            agent.switch_provider(LLMConfig(provider="openai", model="gpt-4o", api_key="..."))
        """
        self._llm = create_client(llm_config)
        R.print_info(f"Switched to {self._llm}")

    # ── [SYS-UNDO] 手动重试 / 回退 ───────────────────────────────────────────

    def _save_turn_snapshot(self) -> None:
        """在每轮 run_turn 开始（用户消息追加前）保存一份完整快照。"""
        self._turn_snapshot = {
            "history":      copy.deepcopy(self._history),
            "stats_turns":  self.stats.turns,
            "stats_input":  self.stats.input_tokens,
            "stats_output": self.stats.output_tokens,
            "stats_tool":   self.stats.tool_calls,
        }

    def _restore_turn_snapshot(self) -> bool:
        """将历史和统计还原到快照时刻，返回是否成功。"""
        if self._turn_snapshot is None:
            return False
        self._history             = copy.deepcopy(self._turn_snapshot["history"])
        self.stats.turns          = self._turn_snapshot["stats_turns"]
        self.stats.input_tokens   = self._turn_snapshot["stats_input"]
        self.stats.output_tokens  = self._turn_snapshot["stats_output"]
        self.stats.tool_calls     = self._turn_snapshot["stats_tool"]
        return True

    def retry_last_turn(self) -> str:
        """
        [SYS-UNDO] 重试：丢弃上一轮模型输出，用相同的用户消息重新调用。

        行为：
          1. 从快照恢复到本轮「用户消息刚追加前」的状态
          2. 把用户消息从快照之后的 _history 里提取出来
          3. 重新执行 run_turn（包含保存 session、打印结果）

        适用场景：对模型答案不满意，希望重新生成一个不同版本。

        Returns:
            新的 assistant 文本，失败时返回空字符串。
        """
        if self._turn_snapshot is None:
            R.print_warning("[retry] No previous turn snapshot available.")
            return ""

        # 找出本轮用户消息（快照之后第一条 role=user 的消息）
        snap_len = len(self._turn_snapshot["history"])
        user_msg: Optional[str] = None
        for msg in self._history[snap_len:]:
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                user_msg = msg["content"]
                break

        if user_msg is None:
            R.print_warning("[retry] Could not locate last user message in history.")
            return ""

        turns_before = self.stats.turns
        R.print_retry_banner(turns_before)

        # 恢复到快照（撤销本轮所有历史）
        self._restore_turn_snapshot()
        # 清除快照（run_turn 会重新生成一份）
        self._turn_snapshot = None

        # 用相同消息重新执行
        return self.run_turn(user_msg)

    def rollback_turn(self) -> bool:
        """
        [SYS-UNDO] 回退：完全撤销上一轮 turn，恢复到本轮开始前的状态。

        与 retry_last_turn 的区别：
          - retry  ：保留用户消息，只重新生成 assistant 回复
          - rollback：用户消息也一并撤销，彻底回退到上一轮结束时的状态

        同步更新：
          - self._history（核心历史）
          - self.stats（token 统计）
          - session 文件（调用 save_session 写回磁盘）
          - 终端显示（打印回退分隔线）

        Returns:
            True — 回退成功；False — 无快照可回退
        """
        if self._turn_snapshot is None:
            R.print_warning("[rollback] No previous turn snapshot available.")
            return False

        turns_before = self.stats.turns
        self._restore_turn_snapshot()
        turns_after = self.stats.turns

        # 丢弃快照（回退后下次 run_turn 会重新创建）
        self._turn_snapshot = None

        R.print_rollback_banner(turns_before, turns_after)

        # 同步写回 session 文件
        if getattr(self.cfg, "auto_save_session", True):
            self.save_session()

        return True

    # ── [SYS-SKILL-COMPACT] Skill 压缩上下文 ─────────────────────────────────

    def _build_skill_compact_block(self) -> str:
        """
        按 LRU 顺序、受 budget 约束构建 skill 重附上下文块。
        无 skill_loader 或无调用记录时返回空字符串。
        """
        if not self.skill_loader:
            return ""
        compact_text, included, dropped = self.skill_loader.build_compact_context(
            include_inactive=True   # 曾经用过但已卸载的 skill 也参与竞争
        )
        budget  = getattr(self.cfg, "skill_compact_budget",     25_000)
        per_sk  = getattr(self.cfg, "skill_compact_per_skill",   5_000)

        # 有 dropped 时无论是否有 included 都警告
        if dropped:
            R.print_warning(
                f"[skill-compact] budget exhausted — "
                f"{len(included)} skill(s) included, "
                f"{len(dropped)} dropped: {dropped}"
            )

        if not compact_text:
            return ""

        header  = (
            f"\n\n## Skill Context (re-attached after compression)\n"
            f"_Budget: {budget} tokens total / {per_sk} per skill. "
            f"Included: {included}. "
            + (f"Dropped (budget exhausted): {dropped}." if dropped else "")
            + "_\n\n"
        )
        if not dropped:
            R.print_info(
                f"[skill-compact] {len(included)} skill(s) re-attached "
                f"after compression."
            )
        return header + compact_text

    def compact_with_skills(self) -> str:
        """
        [SYS-SKILL-COMPACT] 主动触发：用 LLM 生成对话摘要，然后重附 skill 上下文。

        与 /compact 的区别：
          - /compact（旧）：仅压缩对话历史，不处理 skill
          - compact_with_skills()：先生成 LLM 摘要，再按 LRU + budget 重附 skill 内容

        可以通过以下途径触发：
          1. 命令行 /compact（已升级调用此方法）
          2. tool `compact_history`（供 agent 自主调用）
          3. 直接调用 agent.compact_with_skills()

        Returns:
            摘要文本（assistant 的压缩结果），失败时返回空字符串
        """
        if not self._history:
            R.print_info("[compact] History is empty, nothing to compact.")
            return ""

        from prompts import pm as _pm
        compact_prompt = _pm.get_compact_prompt()

        R.print_info("[compact] Generating summary…")
        try:
            result = self.run_turn(compact_prompt)
        except Exception as e:
            R.print_error(f"[compact] Summary generation failed: {e}")
            return ""

        # 压缩历史：保留摘要 + 重附 skill 块
        skill_block = self._build_skill_compact_block()

        new_history: list[dict] = [
            {"role": "user",      "content": "[Previous session summary]"},
            {"role": "assistant", "content": result},
        ]
        if skill_block:
            new_history.append({"role": "user", "content": skill_block})

        self._history = new_history

        # 同步 session
        if getattr(self.cfg, "auto_save_session", True):
            self.save_session()

        R.print_success("[compact] History compacted with skill context re-attached.")
        return result

    def run_turn(self, user_message: str) -> str:
        """
        Run one user turn. May make multiple API calls (tool loops).
        Returns the final assistant text.
        """
        try:
            # [SYS-WATCH] 检测外部文件变化，注入提示
            if self._file_watcher:
                changed = self._file_watcher.check_changes()
                if changed:
                    notice = self._file_watcher.build_change_notice(
                        changed, self.cfg.project_root
                    )
                    # 让缓存失效
                    if self._tool_cache:
                        for p in changed:
                            self._tool_cache.invalidate_file(p)
                    user_message = user_message + notice

            if self.skill_loader:
                newly = self.skill_loader.auto_activate(user_message)
                for name in newly:
                    R.print_skill_loaded(name)
                    # [SYS-SKILL-TRACK] 记录技能激活
                    if self.cfg.skill_tracking_enabled:
                        self.stats.record_skill_activation(name)

            # [SYS-UNDO] 在追加用户消息前保存快照，用于 retry/rollback
            self._save_turn_snapshot()

            self._history.append({"role": "user", "content": user_message})
            self.stats.turns += 1

            result = self._agentic_loop()

            # [SYS-SUMMARY] session 结束后写入摘要（在 save 前）
            # 摘要写入由 save_session 触发，这里只标记需要摘要
            return result
        finally:
            # 每轮对话后自动保存 session
            if getattr(self.cfg, 'auto_save_session', True) and self._history:
                self.save_session()

    # ── Agentic loop ───────────────────────────────────────────────────────────

    def _agentic_loop(self) -> str:
        """Keep calling the LLM until it produces a final text response (no tool calls)."""
        final_text = ""
        loop_count = 0

        while loop_count < self.cfg.max_turns:
            loop_count += 1

            # [SYS-TOKEN] token 预估 + 自动压缩
            if self.cfg.token_estimate_enabled or self.cfg.auto_compress_enabled:
                from mini_agent.llm.system_tool_call import convert_tool_use_to_text
                _sys_preview = self._build_system()
                _msgs_preview = convert_tool_use_to_text(self._history)
                _est = estimate_messages_tokens(_msgs_preview, _sys_preview)
                _budget_pct = _est / max(self.cfg.max_tokens, 1)
                if self.cfg.token_estimate_enabled and self.cfg.verbose:
                    R.print_info(
                        f"[token] ~{_est:,} tokens "
                        f"({_budget_pct:.0%} of {self.cfg.max_tokens:,})"
                    )
                if self.cfg.auto_compress_enabled and _budget_pct >= self.cfg.auto_compress_threshold:
                    self._auto_compress_history()

            response = self._call_llm()
            final_text = response.text
            self.stats.input_tokens += response.usage.input_tokens
            self.stats.output_tokens += response.usage.output_tokens

            # 将 LLMResponse 写入对话历史（provider 无关格式）
            self._append_assistant_response(response)

            # [SYS-SKILL-DETECT] 推理完成后检测哪些 skill 被真正使用
            # 只有「实际使用」的 skill 才更新 tracker LRU 权重
            if self.skill_loader and response.text:
                used = self.skill_loader.record_usage(response.text)
                if used and self.cfg.verbose:
                    R.print_info(f"[skill-detect] used: {used}")

            if not response.has_tool_calls:
                break

            # 执行工具调用，结果写回历史
            tool_results, result_strs = self._execute_tools(response)
            self._history.append(
                self._build_tool_result_message(response.tool_calls, result_strs)
            )

        if loop_count >= self.cfg.max_turns:
            R.print_warning(f"Reached max turns ({self.cfg.max_turns}).")

        return final_text

    # ── LLM 调用 ───────────────────────────────────────────────────────────────

    def _call_llm(self) -> LLMResponse:
        """
        调用 LLMClient，根据 cfg.stream 选择流式或非流式。
        内置重试策略：当模型返回空响应时自动重试（由 self._retry_policy 控制）。
        """
        system = self._build_system()
        tools = self._build_tool_schemas()

        # 思维链回调：对支持 on_reasoning 参数的 provider（如 NVIDIA）启用流式 reasoning
        import inspect as _inspect
        _stream_sig = _inspect.signature(self._llm.stream)
        _supports_on_reasoning = "on_reasoning" in _stream_sig.parameters

        # 转换消息：将 tool_use 类型转换为 text 类型（用于不支持 tool_use 的模型）
        from mini_agent.llm.system_tool_call import convert_tool_use_to_text
        messages_for_llm = convert_tool_use_to_text(self._history)

        def _do_single_call() -> LLMResponse:
            """单次 LLM 调用，流式/非流式统一封装。重试时每次重新调用此函数。"""
            # 每次重试前重置 reasoning 状态（避免重复输出 header）
            _reasoning_started = [False]

            def _on_reasoning(token: str) -> None:
                if not _reasoning_started[0]:
                    R.print_reasoning_header()
                    _reasoning_started[0] = True
                R.print_reasoning(token)

            try:
                if self.cfg.stream:
                    R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                    writer = R.StreamWriter()
                    stream_kwargs: dict = dict(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                        on_token=writer.write,
                    )
                    if _supports_on_reasoning:
                        stream_kwargs["on_reasoning"] = _on_reasoning
                    resp = self._llm.stream(**stream_kwargs)
                    # postprocess 已提取 <thinking> 块，非流式 reasoning 在这里显示
                    if not _reasoning_started[0] and resp.reasoning:
                        R.print_reasoning_header()
                        R.console.print(resp.reasoning, style="dim")
                    if _reasoning_started[0] or resp.reasoning:
                        R.print_reasoning_footer()
                    writer.flush()
                else:
                    resp = self._llm.chat(
                        messages=messages_for_llm,
                        system=system,
                        tools=tools,
                    )
                    # postprocess 已提取 <thinking> 块，统一在此显示
                    if resp.reasoning:
                        R.print_reasoning_header()
                        R.console.print(resp.reasoning, style="dim")
                        R.print_reasoning_footer()
                    if resp.text:
                        R.print_assistant_prefix(agent_name=self.cfg.agent_name)
                        R.print_markdown(resp.text)
            except LLMError:
                raise
            except Exception as e:
                from llm import LLMProviderError
                raise LLMProviderError(f"Unexpected LLM error: {e}") from e

            return resp

        def _on_retry(attempt: int, reason: str) -> None:
            """重试时的提示回调。"""
            if getattr(self.cfg, "llm_retry_verbose", True):
                R.print_warning(
                    f"[retry {attempt}/{self._retry_policy.max_retries}] {reason}"
                )

        # 使用重试策略执行调用
        response = self._retry_policy.call_with_retry(
            call_fn=_do_single_call,
            on_retry=_on_retry,
        )
        return response

    # ── History management ─────────────────────────────────────────────────────

    def _append_assistant_response(self, response: LLMResponse) -> None:
        """
        将 LLMResponse 转换为对话历史条目。
        使用 provider 无关的通用格式（Anthropic/OpenAI 均可接受）。
        <skill_used> 标签在此处剥离，不写入历史（避免污染后续对话上下文）。
        """
        from mini_agent.skills.usage_detector import strip_skill_tags
        content: list[dict] = []
        if response.text:
            clean_text = strip_skill_tags(response.text)
            if clean_text:
                content.append({"type": "text", "text": clean_text})
        for tc in response.tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.input,
            })
        self._history.append({"role": "assistant", "content": content})

    # ── Tool execution ─────────────────────────────────────────────────────────

    def _execute_tools(self, response: LLMResponse) -> tuple[list, list[str]]:
        """运行所有工具调用，返回 (tool_calls列表, result字符串列表)。"""
        result_strs: list[str] = []

        for tc in response.tool_calls:
            R.print_tool_call(tc.name, tc.input, verbose=self.cfg.verbose)
            self.stats.tool_calls += 1

            allowed = self.guard.check(tc.name, tc.input)
            if not allowed:
                result_str = "[Tool call denied by user]"
                R.print_tool_error(tc.name, "denied by user")
                if self.cfg.tool_stats_enabled:
                    self.stats.record_tool_call(tc.name, False, 0)
            else:
                # [SYS-TOOLCACHE] 检查缓存
                _cached = None
                if self._tool_cache:
                    _cached = self._tool_cache.get(tc.name, tc.input)

                if _cached is not None:
                    result_str = _cached
                    R.print_tool_result(tc.name, f"[cache] {result_str[:80]}...")
                    if self.cfg.tool_stats_enabled:
                        self.stats.record_tool_call(tc.name, True, len(result_str))
                else:
                    try:
                        result = self.registry.call(tc.name, tc.input)
                        result_str = str(result) if not isinstance(result, str) else result

                        # [SYS-TRIM] 工具调用结果截断
                        result_str = self._maybe_trim_result(tc.name, result_str)

                        R.print_tool_result(tc.name, result_str)

                        # [SYS-TOOLCACHE] 写入缓存
                        if self._tool_cache:
                            self._tool_cache.put(tc.name, tc.input, result_str)

                        # [SYS-WATCH] 注册 read_file 的文件
                        if self._file_watcher and tc.name == "read_file":
                            _path = tc.input.get("path", "")
                            if _path:
                                self._file_watcher.register(_path, result_str)

                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, True, len(result_str))
                    except Exception as e:
                        result_str = f"[tool error: {e}]"
                        R.print_tool_error(tc.name, str(e))
                        if self.cfg.tool_stats_enabled:
                            self.stats.record_tool_call(tc.name, False, 0)

            result_strs.append(result_str)

        return response.tool_calls, result_strs

    def _maybe_trim_result(self, tool_name: str, result: str) -> str:
        """[SYS-TRIM] 对长工具结果做智能截断。"""
        if not self.cfg.tool_result_trim_enabled:
            return result
        threshold = self.cfg.tool_result_trim_threshold
        if len(result) <= threshold:
            return result
        lines = result.splitlines()
        if len(lines) > 30:
            kept_head = lines[:15]
            kept_tail = lines[-5:]
            omitted = len(lines) - 20
            return (
                "\n".join(kept_head)
                + f"\n... [{omitted} lines omitted] ...\n"
                + "\n".join(kept_tail)
            )
        # 字符截断
        return result[:threshold] + f"\n... [{len(result)-threshold} chars omitted]"

    def _build_tool_result_message(self, tool_calls, results: list[str]) -> dict:
        """
        构造回注工具结果的 user 消息。
        统一使用 <tool_result> 文本格式（与 tool_call_protocol.md 对应）。
        """
        from mini_agent.llm.system_tool_call import render_tool_results
        content = render_tool_results(tool_calls, results)
        return {"role": "user", "content": content}

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _build_system(self) -> str:
        active = self.skill_loader.active if self.skill_loader else []

        # [SYS-SKILL-CHUNK] 技能内容裁剪：只注入相关段落
        if self.cfg.skill_chunking_enabled and self.skill_loader and self._history:
            last_user = next(
                (m["content"] for m in reversed(self._history)
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                ""
            )
            skill_ctx = self.skill_loader.build_context(query=last_user)
        else:
            skill_ctx = self.skill_loader.build_context() if self.skill_loader else ""

        base = build_system_prompt(self.cfg, active, skill_context=skill_ctx)

        # [SYS-SKILL-TOOL] 注入技能目录：让模型知道有哪些 skill 可主动调用
        # 只有当 skill_loader 存在且有可用技能时才注入，避免无效占用 context
        if self.skill_loader and self.skill_loader.available:
            catalog = self.skill_loader.get_catalog()
            inactive = [s for s in catalog if not s["active"]]
            active_catalog = [s for s in catalog if s["active"]]

            skill_dir_lines = ["## Available Skills (Tool-Managed)\n"]
            skill_dir_lines.append(
                "You can call `skill_list`, `skill_activate`, `skill_deactivate` tools "
                "to manage skills dynamically.\n"
            )

            if active_catalog:
                skill_dir_lines.append("**Currently active:**")
                for s in active_catalog:
                    skill_dir_lines.append(f"  - `{s['name']}`: {s['description']}")

            if inactive:
                skill_dir_lines.append("\n**Available (not yet loaded):**")
                for s in inactive:
                    skill_dir_lines.append(f"  - `{s['name']}`: {s['description']}")

            skill_dir_lines.append(
                "\n> Activate a skill when its domain is relevant to the current task. "
                "Deactivate it once the task phase is complete to keep context lean."
            )

            # [SYS-SKILL-DETECT] Track A 声明约定
            # 只在有激活 skill 时注入，避免无意义干扰
            if active_catalog:
                active_names = [s["name"] for s in active_catalog]
                skill_dir_lines.append(
                    f"\n**Skill usage tracking:** When your response draws on guidance "
                    f"from one or more active skills ({', '.join(active_names)}), "
                    f"append a `<skill_used>name</skill_used>` tag at the very end of your reply "
                    f"(after all content). Use comma-separation for multiple skills: "
                    f"`<skill_used>docx,pdf</skill_used>`. "
                    f"Only declare skills whose guidance you actually applied — "
                    f"not every skill that is merely loaded."
                )

            base += "\n\n" + "\n".join(skill_dir_lines)

        # [SYS-PROJ] 注入项目结构快照
        if self._project_snapshot:
            base += "\n\n" + self._project_snapshot

        # [SYS-MEMORY] 注入跨 session 长期记忆
        if self._memory and self._history:
            last_user = next(
                (m["content"] for m in reversed(self._history)
                 if m.get("role") == "user" and isinstance(m.get("content"), str)),
                ""
            )
            if last_user:
                memories = self._memory.search(last_user, k=self.cfg.memory_top_k)
                if memories:
                    snippets = "\n".join(
                        f"- [{m.session_id[:6]}] {m.summary}"
                        for m in memories
                    )
                    base += f"\n\n## Relevant past experience\n{snippets}"

        return base

    def _auto_compress_history(self) -> None:
        """[SYS-COMPRESS] 自动压缩最老一半的历史，保留最近一半，并重附 skill 上下文。"""
        if len(self._history) < 6:
            return
        cutoff = len(self._history) // 2
        old_turns = self._history[:cutoff]
        # 简单摘要：拼接用户消息
        user_msgs = [
            m["content"] for m in old_turns
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        summary_text = "; ".join(user_msgs[:5])
        if len(user_msgs) > 5:
            summary_text += f" ... and {len(user_msgs)-5} more turns"

        # [SYS-FORGET] 智能遗忘：基于权重决定丢弃顺序
        if self.cfg.forget_policy_enabled:
            # 优先保留用户指令，丢弃工具结果
            keep = [
                m for m in self._history[cutoff:]
                if m.get("role") != "tool" and not (
                    m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].startswith("<tool_result")
                )
            ]
            self._history = [
                {"role": "system",
                 "content": f"[Compressed: {summary_text}]"}
            ] + keep
        else:
            self._history = [
                {"role": "system",
                 "content": f"[Compressed: {summary_text}]"}
            ] + self._history[cutoff:]

        # [SYS-SKILL-COMPACT] 压缩后重附 skill 上下文
        skill_block = self._build_skill_compact_block()
        if skill_block:
            self._history.append({
                "role":    "user",
                "content": skill_block,
            })

        R.print_info(f"[compress] History compressed ({cutoff} turns → summary).")

    def _build_tool_schemas(self) -> list[ToolSchema]:
        """将 ToolRegistry 的工具定义转换为 provider 无关的 ToolSchema 列表。"""
        return [
            ToolSchema(
                name=td.name,
                description=td.description,
                input_schema=td.input_schema,
            )
            for td in (self.registry.get(n) for n in self.registry.names)
            if td is not None
        ]
