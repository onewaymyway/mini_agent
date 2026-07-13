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

