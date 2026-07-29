"""notification/dispatcher.py — NotificationDispatcher 骨架（P1）。

设计背景见 next_doc/watchlist_notification_goal_design.md §5。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from mini_agent.notification.config import ALWAYS_ON_CHANNEL, NotificationConfig, load_notification_config

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths


@dataclass
class NotificationMessage:
    title: str
    body: str
    source: str            # "watchlist_report" | "gateway" | ...
    url: Optional[str] = None
    meta: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class NotificationChannel(ABC):
    channel_type: str = ""

    @abstractmethod
    def send(self, message: NotificationMessage, cfg: dict, paths: "AgentPaths") -> bool:
        """发送一条通知。返回是否发送成功。失败时不抛异常给调用方——
        NotificationDispatcher.dispatch() 统一 try/except 兜底，channel
        实现内部也可以自己 log_exception，但不应该让异常向上传播炸穿
        其它渠道的发送。"""
        raise NotImplementedError


_REGISTRY: dict[str, type[NotificationChannel]] = {}


def register_channel(name: str):
    """装饰器：注册一个 NotificationChannel 子类。跟 external_input 的
    @register_source 完全一致风格。"""

    def _decorator(cls: type[NotificationChannel]):
        cls.channel_type = name
        _REGISTRY[name] = cls
        return cls

    return _decorator


def get_channel_class(name: str) -> Optional[type[NotificationChannel]]:
    return _REGISTRY.get(name)


class NotificationDispatcher:
    """按渠道名分发一条通知消息。kanban 永远尝试发送（兜底渠道，§3.3
    "恒真、不可关闭"），其它渠道逐个 try/except，一个渠道失败不影响其它
    渠道，失败记 log_exception，不重试（跟项目里"单点故障不拖垮整体"的
    一贯风格一致）。"""

    def __init__(self, paths: "AgentPaths", config: Optional[NotificationConfig] = None) -> None:
        self._paths = paths
        self._config = config if config is not None else load_notification_config(paths)

    def dispatch(self, message: NotificationMessage, channels: Optional[list[str]] = None) -> dict:
        """返回 {channel_name: bool(是否发送成功)}。"""
        names = list(channels) if channels else list(self._config.default_channels)
        # §9.3 #8：kanban 隐式兜底，哪怕调用方没在 channels 里显式列出，
        # 也要保证"至少在看板能看到"——避免唯一渠道（比如只配了 email）
        # 发送失败时这次通知彻底消失、用户毫无感知。
        if ALWAYS_ON_CHANNEL not in names:
            names = names + [ALWAYS_ON_CHANNEL]

        results: dict[str, bool] = {}
        for name in names:
            if not self._config.is_enabled(name):
                results[name] = False
                continue
            channel_cls = get_channel_class(name)
            if channel_cls is None:
                results[name] = False
                continue
            try:
                channel = channel_cls()
                ok = channel.send(message, self._config.channel_config(name), self._paths)
            except Exception as exc:
                from mini_agent.errors import log_exception
                log_exception(exc, where=f"mini_agent.notification.dispatcher.dispatch[{name}]")
                ok = False
            results[name] = ok
        return results
