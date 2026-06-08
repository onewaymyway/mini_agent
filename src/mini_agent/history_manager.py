"""
history_manager.py — 对话历史管理器

职责：
- 追加消息（用户消息、assistant 响应、工具结果）
- 消息格式转换（SDK 对象 → dict）
- 历史压缩（委托给 CompressionStrategy，可热插拔）
- 快照 / 恢复（用于 retry / rollback）
- skill 标签剥离

从 Agent 中拆出，Agent 只需持有一个 HistoryManager 实例。

重构（v2）：
  压缩逻辑委托给 CompressionStrategy。
  切换策略只需传入不同的 strategy 参数。
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Optional

import mini_agent.ui.renderer as R

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.llm import LLMResponse, LLMClient
    from mini_agent.skills import SkillLoader
    from mini_agent.history.compression import CompressionStrategy


class HistoryManager:
    """
    管理对话历史的所有读写操作。

    外部通过 .history 属性访问原始列表（只读语义），
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
        self._history: list[dict] = []
        self._snapshot: Optional[dict] = None   # 用于 retry/rollback
        # 压缩策略：未传入时根据 cfg.compress.strategy 惰性创建
        self._strategy = strategy

    # ── 对外访问 ──────────────────────────────────────────────────────────

    @property
    def history(self) -> list[dict]:
        """返回历史列表的浅拷贝（防止外部意外修改）。"""
        return list(self._history)

    @property
    def raw(self) -> list[dict]:
        """返回原始列表引用（性能敏感路径使用，调用方不得修改）。"""
        return self._history

    def clear(self) -> None:
        self._history.clear()

    def __len__(self) -> int:
        return len(self._history)

    # ── 追加 ──────────────────────────────────────────────────────────────

    def append_user(self, content: str) -> None:
        self._history.append({"role": "user", "content": content})

    def append_assistant(self, response: "LLMResponse") -> None:
        """
        将 LLMResponse 转换为对话历史条目。
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
        self._history.append({"role": "assistant", "content": content})

    def append_tool_results(self, tool_calls, result_strs: list[str]) -> None:
        """构造并追加工具结果消息。"""
        from mini_agent.llm.system_tool_call import render_tool_results
        content = render_tool_results(tool_calls, result_strs)
        self._history.append({"role": "user", "content": content})

    # ── 快照（retry / rollback）──────────────────────────────────────────

    def save_snapshot(self, stats) -> None:
        """
        在 run_turn 开始前保存快照（用户消息追加前）。
        stats：传入 SessionStats 实例，快照记录其当前数值。
        """
        self._snapshot = {
            "history":      copy.deepcopy(self._history),
            "stats_turns":  stats.turns,
            "stats_input":  stats.input_tokens,
            "stats_output": stats.output_tokens,
            "stats_tool":   stats.tool_calls,
        }

    def restore_snapshot(self, stats) -> bool:
        """还原到快照时刻，同时恢复 stats。返回是否成功。

        修复：改用原地 clear()+extend() 而非重新赋值 self._history = ...
        重新赋值会断开 agent.py 中 self._history = self._hist._history 的共享引用，
        导致 agent 侧持有旧列表，rollback 实际上不生效。
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

    # ── 压缩 ──────────────────────────────────────────────────────────────

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

        # 委托给策略：得到新历史列表（策略不修改原列表）
        new_history = self._strategy.compress(self._history, self.cfg, llm_client)

        # 原地替换，保持 agent.py 中 self._history 共享引用有效
        self._history.clear()
        self._history.extend(new_history)

        # 重附 skill 上下文
        if skill_compact_fn:
            skill_block = skill_compact_fn()
            if skill_block:
                self._history.append({"role": "user", "content": skill_block})

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

        # 构建新历史：摘要 + skill 块
        skill_block = self._build_skill_compact_block()
        new_history: list[dict] = [
            {"role": "user",      "content": "[Previous session summary]"},
            {"role": "assistant", "content": result},
        ]
        if skill_block:
            new_history.append({"role": "user", "content": skill_block})

        # 原地替换，保持 agent.py 中 self._history 共享引用不断裂
        self._history.clear()
        self._history.extend(new_history)
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
