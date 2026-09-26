"""core/experience_recorder.py — ExperienceRecorder：订阅 ExperienceCreated 落库。

见 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
Sprint 3-1：“订阅 Phase 2 的 `ExperienceCreated` 事件，落地写入 store，
替代 Sprint 2 里手写脚本式的记录方式”。

Sprint 2 阶段 `goal_mode/runner.py::_finish()` 在 `publish(ExperienceCreated)`
之后，紧接着又手写了一次 `ExperienceStore(...).append(...)`——事件总线
publish 和实际落盘是两段独立代码，`publish()` 本身并不保证任何人真的把
数据存下来。本模块把“落盘”做成 `ExperienceCreated` 的一个订阅者，接入
方式与 `core/event_log_store.py::ensure_event_log_subscribed()` 完全一致
（同一个止损条件：订阅是纯旁路，`EventBus.publish()` 已保证订阅者异常
不传播，见 `event_bus.py`），这样 `goal_mode/runner.py` 只需要
“publish 一次”，不再需要额外手写落盘调用，避免两处逻辑漂移。

幂等性：与 `ensure_event_log_subscribed()` 同款实现——按
`(id(bus), 落盘路径)` 去重，重复调用不会导致同一事件被写两遍。
"""

from __future__ import annotations

import logging
import threading
from typing import Optional, Set, Tuple

from .event_bus import EventBus, get_event_bus
from .experience import Experience
from .experience_store import ExperienceStore

_logger = logging.getLogger("mini_agent.core.trace")


class ExperienceRecorder:
    """把 `ExperienceCreated` 事件的 payload 还原成 `Experience` 并落库。"""

    def __init__(self, store: Optional[ExperienceStore] = None) -> None:
        self.store = store or ExperienceStore()

    def on_experience_created(self, event) -> None:  # noqa: ANN001 — Event 类型见 events.py
        """`EventBus` 订阅回调：`event.payload` 应为 `Experience.to_dict()`
        的产物（`goal_mode/runner.py` 目前唯一的发布点就是这样构造的）。

        写失败（例如磁盘只读）不应该影响发布方主流程——`EventBus.publish()`
        本身已经把订阅者异常挡住了，这里额外 try/except 只是为了在
        `_logger.warning` 里留下比“订阅者调用失败”更具体的诊断信息。
        """
        try:
            experience = Experience.from_dict(event.payload)
            self.store.append(experience)
        except Exception:  # noqa: BLE001 — 订阅者异常不应向 publish() 传播
            _logger.warning(
                "ExperienceRecorder 落库失败（不影响 Goal 结果本身）", exc_info=True
            )


_subscribed: Set[Tuple[int, str]] = set()
_subscribe_lock = threading.Lock()


def ensure_experience_recorder_subscribed(
    recorder: Optional[ExperienceRecorder] = None, bus: Optional[EventBus] = None
) -> ExperienceRecorder:
    """把 `recorder` 挂到 `bus`（默认全局单例）上，订阅 `ExperienceCreated`。

    幂等：同一个 `(bus 实例, 落盘路径)` 组合只会真正 `subscribe()` 一次，
    模式与 `event_log_store.py::ensure_event_log_subscribed()` 完全一致。
    """
    bus = bus or get_event_bus()
    recorder = recorder or ExperienceRecorder()
    key = (id(bus), str(recorder.store.path))
    with _subscribe_lock:
        if key in _subscribed:
            return recorder
        bus.subscribe("ExperienceCreated", recorder.on_experience_created)
        _subscribed.add(key)
    return recorder


def reset_experience_recorder_subscriptions() -> None:
    """清空订阅去重记录（仅供测试使用，配合
    `core/event_bus.py::reset_event_bus()` 一起在测试间重置）。
    """
    with _subscribe_lock:
        _subscribed.clear()
