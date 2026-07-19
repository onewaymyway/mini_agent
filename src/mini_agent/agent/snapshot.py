from __future__ import annotations

import copy
import re as _re
import threading
from typing import Optional

from mini_agent.config import AppConfig, SessionStats, build_system_prompt
from mini_agent.llm import (
    LLMClient, LLMConfig, LLMResponse, ToolSchema,
    create_client, LLMError,
)
from mini_agent.llm.retry import RetryPolicy, default_retry_policy, no_retry_policy, parse_backoff
from mini_agent.llm.client_pool import LLMClientPool
from mini_agent.permissions import PermissionGuard
from mini_agent.skills import SkillLoader
from mini_agent.tools import ToolRegistry, get_default_registry
from mini_agent.session import SessionManager, Session
import mini_agent.ui.renderer as R
from mini_agent.perception.token_counter import estimate_messages_tokens
from mini_agent.perception.project_scanner import ProjectScanner
from mini_agent.perception.file_watcher import FileWatcher
from mini_agent.perception.tool_cache import ToolResultCache
from mini_agent.perception.memory_base import MemoryBackend
from mini_agent.perception.memory_store import MemoryStore, MemoryEntry
from mini_agent.perception.memory_factory import create_memory_backend
from mini_agent.context_builder import ContextBuilder
from mini_agent.tool_executor import ToolExecutor
from mini_agent.history_manager import HistoryManager
from mini_agent.reminders import ReminderManager

from mini_agent.agent._helpers import (
    _term_write_lock_ctx, _NullCtx, _locked_print_info, _locked_print_warning,
    _is_tool_error, _clamp_confidence, _parse_lesson_candidates, _parse_timeline_summary,
)


class SnapshotMixin:
    """轮次快照、重试与回滚。"""

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
        # 原地替换，保持 self._history 与 self._hist._history 指向同一对象
        restored = copy.deepcopy(self._turn_snapshot["history"])
        self._history.clear()
        self._history.extend(restored)
        self.stats.turns          = self._turn_snapshot["stats_turns"]
        self.stats.input_tokens   = self._turn_snapshot["stats_input"]
        self.stats.output_tokens  = self._turn_snapshot["stats_output"]
        self.stats.tool_calls     = self._turn_snapshot["stats_tool"]
        return True

    def retry_to_turn(self, n: int = 1) -> tuple[bool, str, str]:
        """
        [SYS-UNDO-N] 基于 turn 边界回退到第 N 轮并重新执行，不依赖内存快照。

        与 retry_last_turn()（单层快照重试）的区别：
          - retry_last_turn：只能重试"最近一轮"，且依赖 `_turn_snapshot`，
                              resume 后在发出第一条新消息之前 `_turn_snapshot`
                              就是 resume 时刻的状态，找不到快照之后的用户
                              消息，无法重试。
          - retry_to_turn  ：直接在当前 `_history` 里定位真实用户输入
                              （turn 边界），提取出该轮的用户消息文本，
                              截断历史到该轮开始前，再用相同消息重新调用
                              run_turn()。可以重试 resume 之前 session
                              历史中的任意一轮——只要该轮还留在 `_history`
                              里、没有被 /compact 折叠进摘要。

        限制：
          - /compact 是不可逆的，折叠掉的轮次无法重试。
          - 仅支持纯文本用户消息（content 为 str）；非文本内容
            （例如带图片的多模态消息）暂不支持，会失败并给出提示。
          - token 统计同 rollback_to_turn，是 best-effort。

        Args:
            n: 从最近往前数第 n 轮，默认为 1（即最近一轮）。

        Returns:
            (成功与否, 提示信息, 新生成的 assistant 文本（失败时为空字符串）)
        """
        from mini_agent.history.entry import is_turn_boundary

        if n < 1:
            return False, "回退轮数必须 >= 1。", ""

        turn_starts = [i for i, m in enumerate(self._history) if is_turn_boundary(m)]

        if not turn_starts:
            return False, (
                "当前 history 中没有找到可识别的用户输入轮次"
                "（可能已被 /compact 全部折叠进摘要，不可重试）。"
            ), ""

        if n > len(turn_starts):
            return False, (
                f"当前 history 中只有 {len(turn_starts)} 个可重试的轮次"
                f"（更早的轮次可能已被 /compact 折叠进摘要，不可逆），"
                f"无法回退 {n} 轮。"
            ), ""

        target = turn_starts[-n]
        user_entry = self._history[target]
        user_msg = user_entry.get("content")

        if not isinstance(user_msg, str):
            return False, (
                "目标轮次的用户消息不是纯文本（可能是多模态内容），"
                "暂不支持 retry。"
            ), ""

        turns_before = self.stats.turns
        R.print_retry_banner(turns_before)

        before_count = len(self._history)
        # 截断到该轮用户消息之前（含该轮用户消息本身也一并丢弃，
        # run_turn(user_msg) 会重新把它作为新一轮的用户输入追加进去）
        del self._history[target:]

        # best-effort：轮数计数相应减少
        self.stats.turns = max(0, self.stats.turns - n)

        # 清除单层快照，避免与新状态不一致
        self._turn_snapshot = None

        _hist = getattr(self, "_hist", None)
        if _hist is not None:
            try:
                _hist._raw.append_compact_event(
                    before_count=before_count,
                    after_count=len(self._history),
                    strategy="retry_to_turn",
                    trigger_reason=f"manual_retry_{n}",
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent.snapshot.SnapshotMixin.retry_to_turn')
                pass

        result = self.run_turn(user_msg)
        return True, f"Retried turn (rolled back {n}, then resent).", result

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

    def rollback_to_turn(self, n: int = 1) -> tuple[bool, str]:
        """
        [SYS-UNDO-N] 基于 turn 边界回退 N 轮，不依赖内存快照。

        与 rollback_turn()（单层快照回退）的区别：
          - rollback_turn   ：只能撤销"最近一轮"，且依赖 `_turn_snapshot`
                               （resume 后 / compact 后，快照会被覆盖或重置，
                               无法跨越更早的轮次）
          - rollback_to_turn：直接在当前 `_history` 里定位真实用户输入
                               （turn 边界），可以回退到 resume 之前 session
                               历史中的任意一轮——只要那一轮还留在 `_history`
                               里、没有被 /compact 折叠进摘要。

        限制：
          - /compact 是不可逆的——折叠掉的轮次只以摘要文字形式保留，
            不再是可识别的 turn 边界，无法回退回去。
          - token 统计（input/output tokens）不会精确回滚到目标轮次的
            真实值（历史上每一轮的 token 快照没有逐条持久化），这里只
            做 best-effort：把 stats.turns 相应减少，input/output/tool_calls
            计数保持不变（仅供参考，不代表回退后的真实累计值）。

        Args:
            n: 回退的轮数，默认为 1。

        Returns:
            (成功与否, 提示信息)
        """
        from mini_agent.history.entry import is_turn_boundary

        if n < 1:
            return False, "回退轮数必须 >= 1。"

        turn_starts = [i for i, m in enumerate(self._history) if is_turn_boundary(m)]

        if not turn_starts:
            return False, (
                "当前 history 中没有找到可识别的用户输入轮次"
                "（可能已被 /compact 全部折叠进摘要，不可回退）。"
            )

        if n > len(turn_starts):
            return False, (
                f"当前 history 中只有 {len(turn_starts)} 个可回退的轮次"
                f"（更早的轮次可能已被 /compact 折叠进摘要，不可逆），"
                f"无法回退 {n} 轮。"
            )

        target = turn_starts[-n]
        before_count = len(self._history)

        # 原地截断，保持 self._history 与 self._hist._history 指向同一对象
        del self._history[target:]
        after_count = len(self._history)

        # best-effort：轮数计数相应减少，token 统计无法精确回滚，保持不变
        self.stats.turns = max(0, self.stats.turns - n)

        # 单层快照与新截断状态可能不一致，清除避免误用
        self._turn_snapshot = None

        # 记录一次事件到 raw history（复用 compact_event 的通用字段结构）
        _hist = getattr(self, "_hist", None)
        if _hist is not None:
            try:
                _hist._raw.append_compact_event(
                    before_count=before_count,
                    after_count=after_count,
                    strategy="rollback_to_turn",
                    trigger_reason=f"manual_rollback_{n}",
                )
            except Exception as _mini_agent_exc:
                from mini_agent.errors import log_exception
                log_exception(_mini_agent_exc, where='mini_agent.agent.snapshot.SnapshotMixin.rollback_to_turn')
                pass

        if getattr(self.cfg, "auto_save_session", True):
            self.save_session()

        return True, (
            f"Rolled back {n} turn(s): history {before_count} → {after_count} messages."
        )

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

