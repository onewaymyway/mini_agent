"""
mini_agent.reminders
~~~~~~~~~~~~~~~~~~~~
动态 Reminder 提示注入机制。

在特定情境下（工具出错、特定工具输出、用户意图、assistant 输出模式）
将 reminder 内容追加到对话历史，帮助模型更好地处理当前问题。

reminder 不注入 system prompt，而是以 user 或 assistant 消息的形式
追加到对话 history，模型在下次推理时可感知。
"""

from .loader import ReminderLoader, Reminder
from .matcher import ReminderMatcher
from .manager import ReminderManager, get_reminder_manager

__all__ = [
    "Reminder",
    "ReminderLoader",
    "ReminderMatcher",
    "ReminderManager",
    "get_reminder_manager",
]
