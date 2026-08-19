"""
history_manager.py — 对话历史管理器

职责：
- 追加消息（用户消息、assistant 响应、工具结果）—— 所有追加操作同时写 _type 字段
- to_llm_messages()：将 active history 转为 LLM API 可接受的格式（剥离 _type）
- 历史压缩（委托给 CompressionStrategy，可热插拔）
- 快照 / 恢复（用于 retry / rollback）—— 只影响 active history，raw 不回滚
- skill 标签剥离
- Raw history 维护（同步追加，不压缩，不回滚）

设计变更（类型化版本）：
  每条 history 条目均附加 _type 字段（HType 枚举），明确区分消息来源：
    user_input / tool_result / compressed / compact_summary /
    skill_context / reminder / role_agent / session_resume 等

  好处：
  - 压缩策略无需靠字符串前缀猜测 turn 边界，可精确切割
  - 反思机制可以通过 _type 区分"用户意图"与"工具噪音"
  - raw history 保存完整原始信息，active history 可通过 replay() 精确还原

  发给 LLM 的消息：
    必须调用 to_llm_messages(history) 剥离 _type，不能直接传含 _type 的列表。
    HistoryManager.for_llm() 属性封装此操作。
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Optional

import mini_agent.ui.renderer as R
from mini_agent.history.entry import (
    HType,
    make_user_input,
    make_tool_result,
    make_assistant_reply,
    make_compressed,
    make_compact_summary,
    make_session_resume,
    make_skill_context,
    make_reminder,
    make_format_correction,
    make_role_agent,
    to_llm_messages,
)
from mini_agent.history.raw_history import RawHistory

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.llm import LLMResponse, LLMClient
    from mini_agent.skills import SkillLoader
    from mini_agent.history.compression import CompressionStrategy


class HistoryManager:
    """
    管理对话历史的所有读写操作。

    外部通过 .history 属性访问 active history 列表（含 _type，只读语义），
    发给 LLM 前必须通过 .for_llm 属性或 to_llm_messages() 剥离 _type。
    所有修改必须通过 HistoryManager 的方法进行。
    """

    def __init__(
        self,
        cfg: "AppConfig",
        skill_loader: Optional["SkillLoader"] = None,
        strategy: Optional["CompressionStrategy"] = None,
    ) -> None:
        self.cfg = cfg
        self.skill_loader = skill_loader
        self._history: list[dict] = []    # active history（含 _type）
        self._raw = RawHistory()           # raw history（只追加，不压缩）
        self._snapshot: Optional[dict] = None   # 用于 retry/rollback
        # 压缩策略：未传入时根据 cfg.compress.strategy 惰性创建
        self._strategy = strategy

    # ── 对外访问 ──────────────────────────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        """返回 active history 列表的浅拷贝（含 _type，防止外部意外修改）。"""
        return list(self._history)

    @property
    def raw(self) -> list[dict]:
        """返回 active history 原始列表引用（性能敏感路径使用，调用方不得修改）。"""
        return self._history

    @property
    def for_llm(self) -> list[dict]:
        """返回剥离了 _type 字段的 LLM 可接受格式列表。"""
        return to_llm_messages(self._history)

    @property
    def raw_history(self) -> RawHistory:
        """返回 raw history 管理器（外部只读访问）。"""
        return self._raw

    def clear(self) -> None:
        """清空 active history（raw history 不受影响）。"""
        self._history.clear()

    def __len__(self) -> int:
        return len(self._history)

    # ── 追加 ──────────────────────────────────────────────────────────────────

    def append_user(self, content: str) -> None:
        """追加真实用户输入（_type=user_input）。"""
        msg = make_user_input(content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_assistant(self, response: "LLMResponse") -> None:
        """
        将 LLMResponse 转换为对话历史条目（_type=assistant_reply）。
        <skill_used> 标签在此处剥离，不写入历史。
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
        msg = make_assistant_reply(content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_tool_results(self, tool_calls, result_strs: list[str]) -> None:
        """构造并追加工具结果消息（_type=tool_result）。"""
        from mini_agent.llm.system_tool_call import render_tool_results
        content = render_tool_results(tool_calls, result_strs)
        msg = make_tool_result(content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_skill_context(self, content: str) -> None:
        """追加 skill 上下文重附消息（_type=skill_context）。"""
        msg = make_skill_context(content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_session_resume(self, content: str = "[Previous session summary]") -> None:
        """追加跨 session 恢复标记（_type=session_resume）。"""
        msg = make_session_resume(content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_reminder(self, role: str, content: str) -> None:
        """追加 reminder 注入消息（_type=reminder）。"""
        msg = make_reminder(role, content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_format_correction(self, content: str) -> None:
        """追加工具调用格式纠错提示消息（_type=format_correction，始终 user 角色）。

        用于：模型输出中检测到"看起来想调用工具但格式损坏、解析失败"的痕迹时，
        自动以 user 身份告知模型重新输出，让 agentic loop 继续而不是把半成品
        输出当成最终答案直接结束。
        """
        msg = make_format_correction(content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_role_agent(self, role: str, content) -> None:
        """追加 role agent 反馈消息（_type=role_agent）。"""
        msg = make_role_agent(role, content)
        self._history.append(msg)
        self._raw.append(msg)

    def append_raw_dict(self, msg: dict) -> None:
        """
        直接追加一个已构造好的 dict（外部已设置 _type 的情况）。
        用于 load_session 时批量恢复历史、reminder 系统等特殊路径。
        """
        self._history.append(msg)
        self._raw.append(msg)

    # ── 快照（retry / rollback）——只影响 active history ────────────────────

    def save_snapshot(self, stats) -> None:
        """
        在 run_turn 开始前保存快照（用户消息追加前）。
        stats：传入 SessionStats 实例，快照记录其当前数值。
        注意：raw history 不快照，不回滚（raw 是只追加的事件日志）。
        """
        self._snapshot = {
            "history":      copy.deepcopy(self._history),
            "stats_turns":  stats.turns,
            "stats_input":  stats.input_tokens,
            "stats_output": stats.output_tokens,
            "stats_tool":   stats.tool_calls,
        }

    def restore_snapshot(self, stats) -> bool:
        """还原到快照时刻（active history），同时恢复 stats。返回是否成功。

        修复：改用原地 clear()+extend() 而非重新赋值 self._history = ...,
        重新赋值会断开 agent.py 中 self._history = self._hist._history 的共享引用。
        """
        if self._snapshot is None:
            return False
        # 原地替换，保持所有外部引用（包括 agent.py 的 self._history）指向同一列表
        self._history.clear()
        self._history.extend(copy.deepcopy(self._snapshot["history"]))
        stats.turns          = self._snapshot["stats_turns"]
        stats.input_tokens   = self._snapshot["stats_input"]
        stats.output_tokens  = self._snapshot["stats_output"]
        stats.tool_calls     = self._snapshot["stats_tool"]
        return True

    def has_snapshot(self) -> bool:
        return self._snapshot is not None

    def clear_snapshot(self) -> None:
        self._snapshot = None

    def snapshot_history_len(self) -> int:
        """快照时的历史长度（用于 retry 定位用户消息）。"""
        if self._snapshot is None:
            return 0
        return len(self._snapshot["history"])

    # ── 压缩 ──────────────────────────────────────────────────────────────────

    def auto_compress(
        self,
        skill_compact_fn=None,
        llm_client: Optional["LLMClient"] = None,
    ) -> None:
        """
        [SYS-COMPRESS] 自动压缩历史，委托给 CompressionStrategy。

        策略由 cfg.compress.strategy 指定（默认 "turn_aligned"），
        也可在构造时传入自定义 strategy 实例。
        切换策略无需修改此方法。

        Args:
            skill_compact_fn: 可选，压缩后重附 skill 上下文（无参数，返回 str）
            llm_client:       可选，LLMSummaryStrategy 需要；其他策略忽略
        """
        if len(self._history) < 6:
            return

        # 惰性创建策略实例
        if self._strategy is None:
            from mini_agent.history.compression import create_strategy
            self._strategy = create_strategy(self.cfg)

        before_count = len(self._history)

        # 委托给策略：得到新历史列表（策略不修改原列表）
        new_history = self._strategy.compress(self._history, self.cfg, llm_client)

        # 记录 compact 事件到 raw history（在替换前记录 before_count）
        after_count = len(new_history)
        self._raw.append_compact_event(before_count, after_count, self._strategy.name)

        # 原地替换，保持 agent.py 中 self._history 共享引用有效
        self._history.clear()
        self._history.extend(new_history)

        # 重附 skill 上下文
        if skill_compact_fn:
            skill_block = skill_compact_fn()
            if skill_block:
                msg = make_skill_context(skill_block)
                self._history.append(msg)
                self._raw.append(msg)

        R.print_info(
            f"[compress] History compressed via {self._strategy.name} "
            f"→ {len(self._history)} messages."
        )

    # ── 独立抽取触发（wiki 提取层与组织层改进计划 E1）───────────────────────

    def maybe_trigger_extraction(
        self,
        llm_client: Optional["LLMClient"] = None,
        *,
        force: bool = False,
    ) -> None:
        """独立于 compact 的轻量抽取触发入口（E1 §1.2.2）。

        每轮工具调用批次结束后调用（成本极低：命中判断纯规则扫描，见
        history/extraction_trigger.py::scan_for_extraction_window）。命中
        候选窗口时，异步排队一次"仅抽取、不压缩"的 LLM 调用——抽取结果
        依然走现有的 pending 队列（decision_writer/world_writer），落盘
        判断逻辑完全复用，不新增巩固路径。

        `force=True`：跳过规则判定，只要 cursor 之后还有任何新内容就
        视为命中（session 结束兜底，计划 §1.2.1 触发规则 3），调用方是
        agent/lifecycle.py::close()。

        [2026-07 默认开启]（`cfg.compress.extraction_trigger_enabled=True`，
        `cfg.compress.extraction_trigger_dispatch_enabled=True`）：应用户
        明确要求提前打开，跳过了原计划 §1.4"先只记录候选窗口到
        `extraction_trigger_log.jsonl`、观测阈值合理后再打开实际抽取"的
        观察期。两个开关仍然独立存在，需要临时退回只记录不抽取，可以单独
        把 `extraction_trigger_dispatch_enabled` 设为 `False`。

        任何异常都静默吞掉，不影响调用方——这是"锦上添花"的观测/增强
        路径，不能因为它失败影响 agent 主循环。
        """
        cfg_compress = getattr(self.cfg, "compress", None)
        if cfg_compress is None or not getattr(cfg_compress, "extraction_trigger_enabled", False):
            return
        try:
            self._maybe_trigger_extraction_impl(cfg_compress, llm_client, force=force)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager.maybe_trigger_extraction')
            pass

    def is_extraction_caught_up(self) -> bool:
        """[session 清理功能] 判断抽取游标是否已经追上当前 session 的 raw_history 末尾。

        用于 save_session() 时把结果打到 meta.json（Session.knowledge_extracted），
        供 evolution/session_cleanup.py 判断"这个 session 删除前是否还需要先补一次
        离线知识抽取"。功能未开启（extraction_trigger_enabled=False）时无法判断，
        保守返回 False（session_cleanup 会退化为按 turns 数量的启发式阈值判断）。

        注意：见 session.py::Session.knowledge_extracted 的局限性说明——这是一个
        进程内单调游标，不是精确的跨 session 证明。
        """
        cfg_compress = getattr(self.cfg, "compress", None)
        if cfg_compress is None or not getattr(cfg_compress, "extraction_trigger_enabled", False):
            return False
        try:
            from pathlib import Path
            from mini_agent.storage.paths import AgentPaths
            from mini_agent.history.extraction_trigger import load_extraction_cursor

            paths = AgentPaths(Path(getattr(self.cfg, "project_root", None) or Path.cwd()))
            last_index = load_extraction_cursor(paths)
            return last_index >= len(self._raw.entries)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager.is_extraction_caught_up')
            return False

    def _maybe_trigger_extraction_impl(
        self, cfg_compress, llm_client: Optional["LLMClient"], *, force: bool
    ) -> None:
        from pathlib import Path

        from mini_agent.storage.paths import AgentPaths
        from mini_agent.history.extraction_trigger import (
            ExtractionWindowCandidate,
            scan_for_extraction_window,
            load_extraction_cursor,
            save_extraction_cursor,
            log_extraction_trigger_event,
        )

        paths = AgentPaths(Path(getattr(self.cfg, "project_root", None) or Path.cwd()))
        raw_entries = self._raw.entries
        last_index = load_extraction_cursor(paths)

        candidate: Optional[ExtractionWindowCandidate]
        if force:
            if last_index >= len(raw_entries):
                return
            candidate = ExtractionWindowCandidate(
                start_index=last_index,
                end_index=len(raw_entries),
                trigger_reason="session_end",
                signal_score=float(len(raw_entries) - last_index),
            )
        else:
            candidate = scan_for_extraction_window(
                raw_entries,
                last_extracted_index=last_index,
                min_window_turns=getattr(cfg_compress, "extraction_trigger_min_window_turns", 6),
            )
        if candidate is None:
            return

        dispatch_enabled = bool(getattr(cfg_compress, "extraction_trigger_dispatch_enabled", False))
        log_extraction_trigger_event(paths, candidate, dispatched=dispatch_enabled)

        if not dispatch_enabled:
            # §1.4 校准阶段：只记录候选窗口，不实际发起 LLM 调用，也不推进
            # cursor（下次仍会看到同一段新增内容，日志会重复记录同一窗口的
            # "又变大了一点"，这是刻意的——校准阶段关心的是"命中频率"，不
            # 是精确的窗口边界）。
            return
        if llm_client is None:
            return

        # [BUGFIX / 保底推进游标] 之前这里是两条顺序语句，游标能否推进
        # 完全依赖"_dispatch_lightweight_extraction 内部不会让异常逃逸"
        # 这个隐含前提——一旦未来改动不小心让某个异常分支漏加 try/except，
        # 这里就会直接抛出，导致 save_extraction_cursor 永远执行不到，
        # 游标卡死在同一个位置，此后每次触发都重新计算出同一个（只会更大
        # 不会更小）的超大窗口，陷入死循环。改成 try/finally 后，
        # 不管 _dispatch_lightweight_extraction 是正常返回、内部吞掉异常
        # 后返回、还是意外让异常逃逸，游标推进这一步都保证会执行——
        # 抽取失败顶多是"这段内容没抽到"，绝不应该连累游标卡死。
        try:
            self._dispatch_lightweight_extraction(paths, raw_entries, candidate, llm_client)
        finally:
            save_extraction_cursor(paths, candidate.end_index)

    def dispatch_extraction_for_entries(
        self,
        raw_entries: list[dict],
        llm_client: "LLMClient",
        *,
        trigger_reason: str = "offline_cleanup",
    ) -> bool:
        """[session 清理功能] 离线抽取入口：对一段任意来源的 raw_entries（比如从磁盘
        重新读出的、已经不在运行中的旧 session 的 raw_history.jsonl）触发一次和
        `maybe_trigger_extraction(force=True)` 完全同款的"仅抽取、不压缩"LLM 调用。

        与 `_maybe_trigger_extraction_impl` 的区别只是数据来源：那边读的是当前
        存活进程里 `self._raw.entries`（增量游标），这里读调用方直接传入的整段
        entries（一次性全量），不涉及 extraction_cursor 的读写——调用方
        （evolution/session_cleanup.py）自己负责在成功后调用
        `SessionManager.mark_knowledge_extracted()` 记录结果，避免污染其它
        session 的全局游标坐标系。

        返回是否发起了一次实际抽取（成功解析到内容不代表一定有 decision/entity
        产出，只要 LLM 调用本身走完流程即视为成功；调用方据此决定是否可以安全
        删除该 session）。
        """
        if not raw_entries:
            return True  # 空历史，无需抽取，视为"已完成"
        from pathlib import Path
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.history.extraction_trigger import ExtractionWindowCandidate

        paths = AgentPaths(Path(getattr(self.cfg, "project_root", None) or Path.cwd()))
        candidate = ExtractionWindowCandidate(
            start_index=0,
            end_index=len(raw_entries),
            trigger_reason=trigger_reason,
            signal_score=float(len(raw_entries)),
        )
        try:
            self._dispatch_lightweight_extraction(paths, raw_entries, candidate, llm_client)
            return True
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager.dispatch_extraction_for_entries')
            return False

    #: [BUGFIX / next_doc/extraction_window_oversize_chunking_fix.md]
    #: `scan_for_extraction_window()` 的 `end_index` 永远是"游标之后的全部
    #: 新增内容"，没有任何上限——如果这段时间里连接词/实体密度/轮次计数
    #: 三条规则一直没命中（比如长时间只在跑工具、用户输入轮次很少，但
    #: 工具输出本身很大），未处理的原始条目会持续累积；一旦最终触发
    #: （或 session 结束时 force=True 兜底），单次窗口就可能远超模型的
    #: 上下文上限。递归二分重试到这个深度上限（即最多切成 2**6=64 份）
    #: 仍然超限，视为无法处理，跳过对应片段（保留在日志里，不再继续切）。
    _MAX_EXTRACTION_SPLIT_DEPTH = 6

    def _dispatch_lightweight_extraction(
        self, paths, raw_entries: list[dict], candidate, llm_client: "LLMClient"
    ) -> None:
        """实际发起一次"仅抽取、不压缩"的 LLM 调用并入队结果。

        与 history/compression.py::LLMSummaryStrategy 共用同一套
        cap_oversized_messages/parse_decision_response/parse_world_response/
        queue_candidates/queue_entities/queue_facts，只是 prompt 换成
        §2 新增的"轻量抽取"专用模板（不含摘要要求），保持与 compact 路径
        产出格式一致，落盘逻辑完全复用。

        [BUGFIX] 之前整段窗口一次性发给 LLM，遇到 LLMContextWindowError
        （窗口没有上限，长期未触发时会越攒越大，见上面 `_MAX_EXTRACTION_
        SPLIT_DEPTH` 的说明）会被内部 try/except 直接吞掉、这一大段内容
        直接永久丢失，不会再被抽取。现在改为把 [start_index, end_index)
        委托给 `_dispatch_extraction_window()`，遇到超限就按条目数二分，
        两半分别递归重试，直到成功、缩到 1 条仍超限（跳过）、或达到切分
        深度上限（同样跳过并记录）——尽量多抢救一部分内容，而不是整段
        放弃。
        """
        self._dispatch_extraction_window(
            paths, raw_entries, candidate.start_index, candidate.end_index,
            candidate.trigger_reason, llm_client, depth=0,
        )

    def _dispatch_extraction_window(
        self, paths, raw_entries: list[dict],
        start_index: int, end_index: int,
        trigger_reason: str, llm_client: "LLMClient", depth: int,
    ) -> None:
        """`_dispatch_lightweight_extraction` 的实际执行体，支持递归二分。

        Args:
            start_index/end_index: 本次要抽取的 raw_entries 切片范围
                （半开区间 [start_index, end_index)）。
            depth: 当前递归深度（首次调用为 0），仅用于限制最大切分次数
                和丰富日志，不影响功能正确性——即使某个子窗口本身仍然
                超限，只要条目数 > 1 就会继续二分，直到缩到 1 条或撞到
                `_MAX_EXTRACTION_SPLIT_DEPTH`。
        """
        from mini_agent.prompts import pm
        from mini_agent.history.entry import to_llm_messages
        from mini_agent.history.compression import (
            cap_oversized_messages,
            DEFAULT_MAX_MESSAGE_CHARS_FOR_COMPACT,
        )
        from mini_agent.llm.base import LLMContextWindowError

        window_entries = raw_entries[start_index:end_index]
        if not window_entries:
            return
        max_chars = getattr(
            self.cfg.compress, "max_message_chars_for_compact", DEFAULT_MAX_MESSAGE_CHARS_FOR_COMPACT
        )
        window_messages = cap_oversized_messages(to_llm_messages(window_entries), max_chars) + [
            {"role": "user", "content": pm.render("user/lightweight_extraction_request")}
        ]

        # wiki 提取层与组织层改进计划 E3：与 compact 路径一致，注入已知实体
        # 索引，供模型判断是否应复用已有实体 id。
        entity_digest_section = ""
        if getattr(self.cfg.compress, "entity_digest_enabled", True):
            try:
                from mini_agent.wiki.entity_digest import build_entity_digest_section

                max_entities = getattr(self.cfg.compress, "entity_digest_max_entities", 40)
                entity_digest_section = build_entity_digest_section(
                    paths,
                    max_entities=max_entities,
                    relevance_hint=str(getattr(self.cfg, "project_root", "") or ""),
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager._dispatch_extraction_window')
                entity_digest_section = ""

        try:
            response = llm_client.chat_with_retry(
                messages=window_messages,
                system=pm.render(
                    "system/lightweight_extractor", entity_digest_section=entity_digest_section
                ),
                tools=[],
                max_retries=10,
            )
        except LLMContextWindowError as _mini_agent_exc:
            window_len = end_index - start_index
            if window_len <= 1 or depth >= self._MAX_EXTRACTION_SPLIT_DEPTH:
                # 缩无可缩（单条目仍超限，多半是这一条本身极端巨大，比如
                # 一次性读了个超大文件的工具结果）或者切分次数已经用尽——
                # 放弃这个片段，记录清楚跳过了哪个范围方便事后排查，但不
                # 影响其它片段/后续正常抽取（cursor 由调用方统一推进）。
                from mini_agent.errors import log_exception
                log_exception(
                    _mini_agent_exc,
                    where='mini_agent.history_manager.HistoryManager._dispatch_extraction_window',
                    extra={
                        "skipped_range": f"{start_index}-{end_index}",
                        "depth": depth,
                        "reason": "context_window_exceeded_after_split" if window_len <= 1
                                  else "max_split_depth_reached",
                    },
                )
                return
            mid = start_index + window_len // 2
            self._dispatch_extraction_window(
                paths, raw_entries, start_index, mid, trigger_reason, llm_client, depth + 1,
            )
            self._dispatch_extraction_window(
                paths, raw_entries, mid, end_index, trigger_reason, llm_client, depth + 1,
            )
            return
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager._dispatch_extraction_window')
            return

        raw_text = (response.text or "").strip()
        if not raw_text:
            return

        from mini_agent.history.decision_extraction import parse_decision_response
        extraction = parse_decision_response(raw_text)
        decisions = extraction.decisions

        world_entities: list = []
        world_facts: list = []
        try:
            from mini_agent.history.world_extraction import parse_world_response
            world_extraction = parse_world_response(raw_text)
            world_entities = world_extraction.entities
            world_facts = world_extraction.facts
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager._dispatch_extraction_window')
            pass

        source_entries = [
            f"extraction_trigger@{trigger_reason}@{start_index}-{end_index}"
        ]

        if decisions and getattr(self.cfg.compress, "extract_decisions", True):
            try:
                from mini_agent.wiki.decision_writer import queue_candidates
                queue_candidates(paths, decisions, source_entries=source_entries)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager._dispatch_extraction_window')
                pass

        if (world_entities or world_facts) and getattr(self.cfg.compress, "extract_world_model", True):
            try:
                from mini_agent.wiki.world_writer import queue_entities, queue_facts
                queue_entities(paths, world_entities, source_entries=source_entries)
                queue_facts(paths, world_facts, source_entries=source_entries)
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.history_manager.HistoryManager._dispatch_extraction_window')
                pass

    def compact_with_llm(self, compact_prompt: str, run_turn_fn) -> str:
        """
        [SYS-SKILL-COMPACT] 用 LLM 生成摘要，然后重附 skill 上下文。

        Args:
            compact_prompt: 触发压缩的 prompt 文本
            run_turn_fn:    agent.run_turn 的引用（执行 LLM 调用）
        """
        if not self._history:
            R.print_info("[compact] History is empty, nothing to compact.")
            return ""

        R.print_info("[compact] Generating summary…")
        try:
            result = run_turn_fn(compact_prompt)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where='mini_agent.history_manager.HistoryManager.compact_with_llm')
            R.print_error(f"[compact] Summary generation failed: {e}")
            return ""

        # 记录 compact 事件到 raw history
        before_count = len(self._history)
        self._raw.append_compact_event(before_count, 2, "compact_with_llm")

        # 构建新历史：摘要 + skill 块
        skill_block = self._build_skill_compact_block()
        new_history: list[dict] = [
            make_session_resume("[Previous session summary]"),
            make_compact_summary(result),
        ]
        if skill_block:
            new_history.append(make_skill_context(skill_block))

        # 原地替换，保持 agent.py 中 self._history 共享引用不断裂
        self._history.clear()
        self._history.extend(new_history)
        # 追加新条目到 raw
        for msg in new_history:
            self._raw.append(msg)

        R.print_success("[compact] History compacted with skill context re-attached.")
        return result

    def _build_skill_compact_block(self) -> str:
        """按 LRU 顺序、受 budget 约束构建 skill 重附上下文块。"""
        if not self.skill_loader:
            return ""
        compact_text, included, dropped = self.skill_loader.build_compact_context(
            include_inactive=True
        )
        budget = getattr(self.cfg, "skill_compact_budget", 25_000)
        per_sk = getattr(self.cfg, "skill_compact_per_skill", 5_000)

        if dropped:
            R.print_warning(
                f"[skill-compact] budget exhausted — "
                f"{len(included)} skill(s) included, "
                f"{len(dropped)} dropped: {dropped}"
            )
        if not compact_text:
            return ""
        if not dropped:
            R.print_info(
                f"[skill-compact] {len(included)} skill(s) re-attached after compression."
            )

        header = (
            f"\n\n## Skill Context (re-attached after compression)\n"
            f"_Budget: {budget} tokens total / {per_sk} per skill. "
            f"Included: {included}. "
            + (f"Dropped (budget exhausted): {dropped}." if dropped else "")
            + "_\n\n"
        )
        return header + compact_text
