"""core/event_log_store.py — Event 的 JSONL 落盘 + 按 correlation_id 检索。

见 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md` Sprint 2-2：
"可视化：一个简单 CLI：`mini_agent events trace <correlation_id>`，按时间
顺序打印一次 Goal 执行的完整事件链"。

`core/event_bus.py` 本身只做进程内分发（`publish()` 只记一次 trace 日志，
不落盘），CLI 命令是独立进程调用，必须先有落盘记录才能在事后重放一次
已经结束的 Goal 执行。本模块提供两样东西：

1. `EventLogStore`：与 `core/experience_store.py` 完全一致的 JSONL
   append-only 落盘 + 检索实现（同样的存储技术选型理由：量级低、
   不需要索引/事务，见该文件顶部说明），新增 `trace(correlation_id)`
   按时间戳升序返回一次完整事件链。
2. `ensure_event_log_subscribed()`：把一个 `EventLogStore` 挂到
   `EventBus` 上，**只订阅** `EVENT_KINDS` 里已经落地的几种 kind，
   不是通配订阅所有 kind（避免把未来新增的、还没设计好落盘格式的
   event kind 也悄悄写进同一个文件）。订阅是纯旁路（`EventBus.publish()`
   本身已经保证"订阅者异常不传播"，见 `event_bus.py`），不改变发布方
   （`goal_mode/runner.py`）的任何返回值或控制流，符合 Sprint 2-1
   定下的止损条件（Event 只能是旁路记录）。

幂等性：`ensure_event_log_subscribed()` 按 `(id(bus), 落盘路径)` 去重，
同一个 bus 对同一个路径只会被订阅一次；`core/event_bus.py::reset_event_bus()`
产生新总线实例后（主要用于测试隔离），下一次调用会用新的 `id(bus)` 重新
订阅一次，不会因为"看起来订阅过"而漏订阅。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import List, Optional, Set, Tuple

from .event_bus import EventBus, get_event_bus
from .events import EVENT_KINDS, Event


class EventLogStore:
    """Event 的最小持久化 + 按 correlation_id 检索实现（JSONL，append-only）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        if path is None:
            from mini_agent.storage.paths import AgentPaths

            path = AgentPaths().workdir_event_log
        self.path = Path(path)
        self._write_lock = threading.Lock()

    def append(self, event: Event) -> None:
        """把一条 Event 追加写入存储文件（供 `EventBus` 当订阅者回调直接用）。

        写失败（例如磁盘只读）不应该影响发布方主流程——`EventBus.publish()`
        本身已经把订阅者异常挡住了（见 `event_bus.py::EventBus.publish()`
        的 `except Exception` 分支），这里不需要重复 try/except，但仍然
        加锁保证多线程场景下不会交叉写坏同一行。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def all(self) -> List[dict]:
        """读出全部已持久化的事件（原始 dict 形式），按写入顺序（旧→新）。"""
        if not self.path.exists():
            return []
        results: List[dict] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    # 与 experience_store.py / memory.jsonl 的既有容错策略
                    # 一致：单行损坏不影响其它记录的读取，跳过即可。
                    continue
        return results

    def trace(self, correlation_id: str) -> List[dict]:
        """按 `correlation_id` 过滤出一次完整事件链，按 `at` 时间戳升序
        排列（即实际发生顺序，供 `events trace` 命令按顺序重放打印）。
        """
        matched = [d for d in self.all() if d.get("correlation_id") == correlation_id]
        matched.sort(key=lambda d: d.get("at", 0.0))
        return matched


_subscribed: Set[Tuple[int, str]] = set()
_subscribe_lock = threading.Lock()


def ensure_event_log_subscribed(
    store: Optional[EventLogStore] = None, bus: Optional[EventBus] = None
) -> EventLogStore:
    """把 `store` 挂到 `bus`（默认全局单例）上，订阅 `EVENT_KINDS` 里的
    每一种 kind。幂等：同一个 `(bus 实例, 落盘路径)` 组合只会真正
    `subscribe()` 一次，重复调用直接返回同一份 `store` 不重复挂载
    （避免同一个 kind 被同一个 store 重复订阅、导致同一事件被写两遍）。
    """
    bus = bus or get_event_bus()
    store = store or EventLogStore()
    key = (id(bus), str(store.path))
    with _subscribe_lock:
        if key in _subscribed:
            return store
        for kind in EVENT_KINDS:
            bus.subscribe(kind, store.append)
        _subscribed.add(key)
    return store


def reset_event_log_subscriptions() -> None:
    """清空订阅去重记录（仅供测试使用，配合
    `core/event_bus.py::reset_event_bus()` 一起在测试间重置，避免
    "旧总线实例的 id 恰好被新实例复用"这种边界情况残留脏状态）。
    """
    with _subscribe_lock:
        _subscribed.clear()
