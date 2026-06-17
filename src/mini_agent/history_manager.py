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
