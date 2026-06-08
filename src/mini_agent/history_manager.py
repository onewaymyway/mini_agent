"""
history_manager.py — 对话历史管理器

职责：
- 追加消息（用户消息、assistant 响应、工具结果）
- 消息格式转换（SDK 对象 → dict）
- 历史压缩（保留摘要 + 重附 skill）
- 快照 / 恢复（用于 retry / rollback）
- skill 标签剥离

从 Agent 中拆出，Agent 只需持有一个 HistoryManager 实例。
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Optional

import mini_agent.ui.renderer as R

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.llm import LLMResponse
    from mini_agent.skills import SkillLoader


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
    ) -> None:
        self.cfg = cfg
        self.skill_loader = skill_loader
        self._history: list[dict] = []
        self._snapshot: Optional[dict] = None   # 用于 retry/rollback

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

    def auto_compress(self, skill_compact_fn=None) -> None:
        """
        [SYS-COMPRESS] 自动压缩最老一半的历史。

        修复：不再使用 role="system"（部分 provider 不支持历史中的 system 消息）。
        改为标准 user/assistant 对，所有 provider 均能正确处理。

        Args:
            skill_compact_fn: 可选的 skill 上下文重附函数（无参数，返回 str）
        """
        if len(self._history) < 6:
            return
        cutoff = len(self._history) // 2
        old_turns = self._history[:cutoff]

        # 构建摘要
        user_msgs = [
            m["content"] for m in old_turns
            if m.get("role") == "user" and isinstance(m.get("content"), str)
            and not m["content"].startswith("<tool_result")
            and not m["content"].startswith("[Previous session")
        ]
        tool_call_count = sum(
            len(m.get("content", [])) if isinstance(m.get("content"), list) else 0
            for m in old_turns if m.get("role") == "assistant"
        )
        summary_parts = []
        if user_msgs:
            summary_parts.append("User requests: " + "; ".join(
                (msg[:80] + "…" if len(msg) > 80 else msg)
                for msg in user_msgs[:6]
            ))
            if len(user_msgs) > 6:
                summary_parts.append(f"... and {len(user_msgs)-6} more user turns")
        if tool_call_count:
            summary_parts.append(f"({tool_call_count} tool calls executed)")
        summary_text = " ".join(summary_parts) if summary_parts else f"({cutoff} turns)"

        # 智能遗忘：剔除纯工具结果消息
        if self.cfg.forget_policy_enabled:
            keep = [
                m for m in self._history[cutoff:]
                if not (
                    m.get("role") == "user"
                    and isinstance(m.get("content"), str)
                    and m["content"].startswith("<tool_result")
                )
            ]
        else:
            keep = self._history[cutoff:]

        # 原地替换，保持 agent.py 中 self._history 共享引用不断裂
        new_content = [
            {"role": "user",      "content": "[Previous conversation compressed]"},
            {"role": "assistant", "content": f"[Compressed summary: {summary_text}]"},
        ] + keep
        self._history.clear()
        self._history.extend(new_content)

        # 重附 skill 上下文
        if skill_compact_fn:
            skill_block = skill_compact_fn()
            if skill_block:
                self._history.append({"role": "user", "content": skill_block})

        R.print_info(f"[compress] History compressed ({cutoff} turns → summary).")

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
