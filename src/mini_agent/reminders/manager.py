"""
reminders/manager.py
~~~~~~~~~~~~~~~~~~~~
ReminderManager：对外统一接口，被 Agent 持有。

职责：
- 初始化 Loader + Matcher；
- 根据 ReminderConfig 开关决定是否执行各类触发；
- 将匹配结果截取到 max_per_turn；
- 提供 inject_reminder() 工具方法，将 reminder 格式化为消息 dict。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, TYPE_CHECKING

from .loader import Reminder, ReminderLoader
from .matcher import ReminderMatcher

if TYPE_CHECKING:
    from ..config import AppConfig, ReminderConfig


# ── 单例缓存（每个进程一个实例）──────────────────────────────────────────────
_manager_instance: Optional["ReminderManager"] = None


def get_reminder_manager(cfg: "AppConfig") -> "ReminderManager":
    """获取（或创建）全局 ReminderManager 实例。"""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = ReminderManager(cfg)
    return _manager_instance


def reset_reminder_manager() -> None:
    """测试时用于重置单例。"""
    global _manager_instance
    _manager_instance = None


class ReminderManager:
    """
    Reminder 系统对外门面。
    Agent 持有此对象，在各情境下调用对应 check_* 方法，
    再调用 format_injection() 将 Reminder 转成消息 dict。
    """

    def __init__(self, cfg: "AppConfig") -> None:
        self._cfg: "ReminderConfig" = cfg.reminder
        self._verbose = self._cfg.verbose

        # 确定系统默认目录
        system_dir: Optional[Path] = None
        if hasattr(cfg, "prompts_dir") and cfg.prompts_dir:
            candidate = Path(cfg.prompts_dir) / "reminders"
            if candidate.is_dir():
                system_dir = candidate
        # 若 prompts_dir 未配置，尝试相对于本文件的默认位置
        if system_dir is None:
            _default = Path(__file__).parent.parent / "prompts" / "reminders"
            if _default.is_dir():
                system_dir = _default

        self._loader = ReminderLoader(
            system_dir=system_dir,
            custom_dir=self._cfg.custom_dir,
            verbose=self._verbose,
        )
        self._reminders = self._loader.load()
        self._matcher = ReminderMatcher(self._reminders, verbose=self._verbose)

        if self._verbose:
            print(
                f"[ReminderManager] 初始化完成，共 {len(self._reminders)} 条 reminder。"
                f"  system_dir={system_dir}  custom_dir={self._cfg.custom_dir}"
            )

    # ── 是否启用 ──────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled

    # ── 四类触发检查 ──────────────────────────────────────────────────────────

    def check_tool_error(
        self, tool_name: str, error_str: str
    ) -> List[Reminder]:
        """工具出错时调用。返回待注入的 reminder 列表。"""
        if not self.enabled or not self._cfg.tool_error_enabled:
            return []
        matched = self._matcher.match_tool_error(tool_name, error_str)
        return self._limit(matched)

    def check_post_tool(
        self, tool_name: str, output_str: str
    ) -> List[Reminder]:
        """工具成功后调用。返回待注入的 reminder 列表。"""
        if not self.enabled or not self._cfg.post_tool_enabled:
            return []
        matched = self._matcher.match_post_tool(tool_name, output_str)
        return self._limit(matched)

    def check_user_intent(self, user_message: str) -> List[Reminder]:
        """用户消息进入时调用。返回待注入的 reminder 列表。"""
        if not self.enabled or not self._cfg.user_intent_enabled:
            return []
        matched = self._matcher.match_user_intent(user_message)
        return self._limit(matched)

    def check_assistant_text(self, text: str) -> List[Reminder]:
        """assistant 输出后调用。返回待注入的 reminder 列表。"""
        if not self.enabled or not self._cfg.pattern_enabled:
            return []
        matched = self._matcher.match_pattern(text)
        return self._limit(matched)

    # ── 注入格式化 ────────────────────────────────────────────────────────────

    @staticmethod
    def format_injection(reminder: Reminder) -> dict:
        """
        将 Reminder 格式化为对话 history 条目 dict。

        inject_as="user"      → {"role": "user",      "content": "[Reminder: <name>]\n<content>"}
        inject_as="assistant" → {"role": "assistant",  "content": "[Note]\n<content>"}
        """
        if reminder.inject_as == "assistant":
            return {
                "role": "assistant",
                "content": f"[Note]\n{reminder.content}",
            }
        return {
            "role": "user",
            "content": f"[Reminder: {reminder.name}]\n{reminder.content}",
        }

    # ── 重载 ──────────────────────────────────────────────────────────────────

    def reload(self) -> None:
        """热重载 reminder 文件（无需重启进程）。"""
        self._reminders = self._loader.reload()
        self._matcher.update(self._reminders)
        if self._verbose:
            print(f"[ReminderManager] 重载完成，共 {len(self._reminders)} 条 reminder。")

    # ── 内部工具 ──────────────────────────────────────────────────────────────

    def _limit(self, reminders: List[Reminder]) -> List[Reminder]:
        """截取 max_per_turn 条（已按 priority 降序排列）。"""
        return reminders[: self._cfg.max_per_turn]

    # ── 调试 ──────────────────────────────────────────────────────────────────

    def list_all(self) -> List[Reminder]:
        """返回当前加载的所有 reminder（用于调试/生成）。"""
        return list(self._reminders)
