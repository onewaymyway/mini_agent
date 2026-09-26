"""core/event_bus.py — Phase 2 Sprint 2-1 最小事件总线。

见 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`
Sprint 2-1："一个进程内的 `publish(event)` / `subscribe(type, handler)`，
不引入外部消息队列"。

止损条件对应关系：Sprint 2-1 止损条件要求"如果引入 Event 后发现某个
现有测试因为『多了一次 publish 调用的副作用』而失败，说明 Event 目前还在
侵入业务逻辑，应回退为纯旁路（observer）"。为此 `publish()` 做了两点
保证：

1. 订阅者抛出的异常只记录日志，不向发布方传播——发布方（比如
   `goal_mode/runner.py`）的主流程不应该因为一个订阅者写坏了而中断。
2. 没有任何订阅者时，`publish()` 只做一次 trace 日志记录，等价于
   Sprint 1 里直接 `_core_logger.debug(...)` 的效果，不改变可观察行为。

进程内单例：Sprint 2-1 范围只需要一个全局总线，不做多总线/命名空间/
跨进程分发（那些属于"外部消息队列"，明确在本 Sprint 范围之外）。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, DefaultDict, List, Optional

from .events import Event

_logger = logging.getLogger("mini_agent.core.trace")

EventHandler = Callable[[Event], None]


class EventBus:
    """一个进程内的最小发布/订阅总线。"""

    def __init__(self) -> None:
        self._subscribers: DefaultDict[str, List[EventHandler]] = defaultdict(list)

    def subscribe(self, kind: str, handler: EventHandler) -> None:
        """订阅某一种 `Event.kind`。不支持通配订阅（Sprint 2-1 范围内不需要）。"""
        self._subscribers[kind].append(handler)

    def unsubscribe(self, kind: str, handler: EventHandler) -> None:
        """从某一种 `Event.kind` 取消订阅（主要供测试清理用）。"""
        handlers = self._subscribers.get(kind)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Event) -> None:
        """发布一个事件：先记 trace 日志，再逐个通知订阅者（异常不传播）。"""
        _logger.debug("%s", event.to_dict())
        for handler in list(self._subscribers.get(event.kind, ())):
            try:
                handler(event)
            except Exception:  # noqa: BLE001 — 订阅者异常不应影响发布方主流程
                _logger.warning(
                    "event handler for kind=%r failed", event.kind, exc_info=True
                )


_default_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """返回进程内单例 `EventBus`（懒初始化）。"""
    global _default_bus
    if _default_bus is None:
        _default_bus = EventBus()
    return _default_bus


def reset_event_bus() -> None:
    """重置单例（仅供测试使用，避免测试之间的订阅关系互相污染）。"""
    global _default_bus
    _default_bus = None
