"""notification/ — 可扩展通知渠道系统（P1）。

设计背景见 next_doc/watchlist_notification_goal_design.md §5。
第一批实现 kanban（必达兜底）+ email 两个渠道；渠道注册表模式跟
external_input 的 @register_source 完全一致风格，以后新增渠道只需要
新增一个 NotificationChannel 子类 + 装饰器注册。
"""

from __future__ import annotations

from mini_agent.notification.dispatcher import (
    NotificationChannel,
    NotificationDispatcher,
    NotificationMessage,
    get_channel_class,
    register_channel,
)

# 触发内置渠道的装饰器注册（import 副作用，跟 external_input/builtin 的模式一致）
from mini_agent.notification.channels import kanban as _kanban_channel  # noqa: F401
from mini_agent.notification.channels import email as _email_channel  # noqa: F401

__all__ = [
    "NotificationChannel",
    "NotificationDispatcher",
    "NotificationMessage",
    "get_channel_class",
    "register_channel",
]
