"""core/state_manager.py — Phase 4 Sprint 4-1：StateManager 骨架。

见 `next_doc/refactor_plan/05-phase4-unified-state-sprint-plan.md`
Sprint 4-1：本 Phase 明确止损边界——只做 `StateManager` 的骨架 +
`GoalState` 的真实托管，`SelfState`/`WorldState` 等留给 Sprint 4-2
建空 dataclass 占位，不在这里提前实现。

对外只暴露两个核心方法：
  - `get_state(kind) -> State | None`
  - `update_state(kind, state) -> None`

`goal_mode/runner.py` 唯一接入点（与 Sprint 1/2/3 一致的模式）：
  1. `run()` 开始时，把 `GoalAdapter.to_new(spec)` 转出的 `GoalState`
     通过 `update_state("goal", ...)` 交给 `StateManager` 托管——这是
     本次改动里**唯一**一次由 `runner.py` 主动调用 `update_state`，
     之后 `GoalState` 的所有变化都不再由 `runner.py` 直接改字段，
     而是通过下面第 2 点的事件订阅自动完成。
  2. `StateManager` 订阅事件总线上的 `GoalUpdated`（每轮 CONTINUE 推进
     一次 / `_finish()` 终止时各 publish 一次，payload 带最新
     `round`/`status`），收到后自动更新内部持有的 `GoalState`，而不是
     被动等外部再调一次 `update_state`——这正是 Sprint 4-1 任务表里
     “事件驱动更新”一项要求的行为。
  3. `runner.py` 之后如果需要读取“当前 Goal 领域状态”，一律通过
     `state_manager.get_state("goal")` 读取，不再自己另外持有一份
     可变的 `GoalState` 引用（Sprint 4-1 验收标准）。

止损条件（对应 Sprint 4-1 表述）：如果发现 `GoalUpdated` 事件驱动更新
在 Goal 这一条链路上就出现“事件到达顺序与实际执行顺序不一致导致状态
读出来是脏的”问题，暂停继续给其它 State（Self/World/...）接事件驱动，
先把 `GoalState` 这一条链路的事件顺序保证做稳（`EventBus` 目前是同步
进程内调用，Sprint 4-1 范围内暂不需要额外加锁）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from .event_bus import EventBus, get_event_bus
from .events import Event
from .goal import GoalState
from .runtime import RuntimeState

_logger = logging.getLogger("mini_agent.core.trace")

# Sprint 4-1 范围内只有 "goal" 这一种 kind 有真实托管逻辑；其余 kind
# （self/world/capability/runtime）留给 Sprint 4-2 建占位 dataclass 后
# 再补对应的 get_state 支持，本文件不提前假装支持。
_MANAGED_GOAL_KIND = "goal"


class StateManager:
    """`core/` 领域状态的统一持有者（Sprint 4-1：只有 GoalState 是真托管）。

    `_states` 是唯一的状态存储：`get_state`/`update_state`/事件订阅回调
    三者都只读写这一个 dict，不允许调用方在别处另外持有可变引用。
    """

    def __init__(self) -> None:
        self._states: Dict[str, object] = {}
        self._lock = threading.Lock()

    def get_state(self, kind: str) -> Optional[object]:
        """读取当前持有的某一类 State 快照（不存在则返回 None）。"""
        with self._lock:
            return self._states.get(kind)

    def update_state(self, kind: str, state: object) -> None:
        """显式写入/替换某一类 State。

        Sprint 4-1 范围内，`goal_mode/runner.py` 只在 `run()` 开始时调用
        一次（seed 初始状态）；之后 `"goal"` 这个 kind 的更新全部走下面
        `_on_goal_updated` 的事件订阅路径，不再有第二处手写调用。
        """
        with self._lock:
            self._states[kind] = state
        _logger.debug(
            "%s",
            Event(
                kind="StateManager.update_state",
                payload={"state_kind": kind},
            ).to_dict(),
        )

    def snapshot(self) -> dict:
        """一次性导出当前所有 State 的快照（Sprint 4-2 任务，这里先给出
        骨架实现：Sprint 4-1 只有 "goal"，Sprint 4-2 占位 State 加入后
        `snapshot()` 不需要再改代码，遍历 `_states` 即可自动包含）。
        """
        with self._lock:
            out: dict = {}
            for kind, state in self._states.items():
                if hasattr(state, "to_dict"):
                    out[kind] = state.to_dict()
                elif hasattr(state, "__dict__"):
                    out[kind] = dict(state.__dict__)
                else:
                    out[kind] = state
            return out

    # ── 事件驱动更新 ─────────────────────────────────────────────────

    def _on_goal_updated(self, event: Event) -> None:
        """订阅 `GoalUpdated`：收到后自动更新内部持有的 `GoalState`。

        找不到已托管的 `GoalState`（比如 `runner.py` 忘了先
        `update_state("goal", ...)` seed 一次）时只记警告、不报错——
        `EventBus.publish()` 本身已经保证订阅者异常不会向发布方传播，
        这里选择更保守的“记警告后跳过”，避免在事件回调里凭空构造一个
        字段不完整的 `GoalState`。
        """
        with self._lock:
            state = self._states.get(_MANAGED_GOAL_KIND)
            if state is None or not isinstance(state, GoalState):
                _logger.warning(
                    "收到 GoalUpdated 但尚未托管任何 GoalState，忽略：%s",
                    event.to_dict(),
                )
                return
            payload = event.payload or {}
            round_no = payload.get("round")
            if isinstance(round_no, int):
                state.round = round_no
            status = payload.get("status")
            if isinstance(status, str) and status:
                state.status = status
            state.updated_at = time.time()

    def _runtime_state_locked(self) -> RuntimeState:
        """取（必要时新建）RuntimeState。调用方必须已持有 `self._lock`。

        与 GoalState 不同，RuntimeState 完全由事件推导，不需要外部先 seed：
        从零值开始累计不会造成“字段不完整”的状态。
        """
        state = self._states.get("runtime")
        if not isinstance(state, RuntimeState):
            state = RuntimeState()
            self._states["runtime"] = state
        return state

    def _on_runtime_cycle_started(self, event: Event) -> None:
        """订阅 `RuntimeCycleStarted`（Phase 10 A2）。"""
        with self._lock:
            st = self._runtime_state_locked()
            st.cycles_started += 1
            st.last_cycle_started_at = event.at

    def _on_runtime_cycle_completed(self, event: Event) -> None:
        """订阅 `RuntimeCycleCompleted`（Phase 10 A2）。payload 字段缺失/类型不对时只跳过该字段。"""
        payload = event.payload or {}
        with self._lock:
            st = self._runtime_state_locked()
            st.cycles_completed += 1
            st.last_cycle_finished_at = event.at
            status = payload.get("status")
            if isinstance(status, str):
                st.last_cycle_status = status
            gap = payload.get("gap_item_count")
            if isinstance(gap, int) and not isinstance(gap, bool):
                st.last_gap_item_count = gap
            # `learn` 键仅在开启 learn 步骤时才有；没有时保留上一次的摘要，不清空
            learn = payload.get("learn")
            if isinstance(learn, dict):
                st.last_learn_summary = dict(learn)

    def subscribe_to_bus(self, bus: Optional[EventBus] = None) -> None:
        """把本实例的事件回调挂到 `bus`（默认全局单例）上。"""
        bus = bus or get_event_bus()
        bus.subscribe("GoalUpdated", self._on_goal_updated)
        bus.subscribe("RuntimeCycleStarted", self._on_runtime_cycle_started)
        bus.subscribe("RuntimeCycleCompleted", self._on_runtime_cycle_completed)


_default_manager: Optional[StateManager] = None
_subscribed_keys: set = set()
_subscribe_lock = threading.Lock()


def get_state_manager() -> StateManager:
    """返回进程内单例 `StateManager`（懒初始化，模式与 `get_event_bus()`
    一致）。"""
    global _default_manager
    if _default_manager is None:
        _default_manager = StateManager()
    return _default_manager


def ensure_state_manager_subscribed(
    manager: Optional[StateManager] = None, bus: Optional[EventBus] = None
) -> StateManager:
    """把 `manager` 挂到 `bus` 上订阅 `GoalUpdated`（幂等，接入方式与
    `event_log_store.py::ensure_event_log_subscribed()` /
    `experience_recorder.py::ensure_experience_recorder_subscribed()`
    完全一致：按 `(bus 实例, manager 实例)` 去重，重复调用不会导致同一个
    事件被处理两次）。
    """
    manager = manager or get_state_manager()
    bus = bus or get_event_bus()
    key = (id(bus), id(manager))
    with _subscribe_lock:
        if key in _subscribed_keys:
            return manager
        manager.subscribe_to_bus(bus)
        _subscribed_keys.add(key)
    return manager


def reset_state_manager() -> None:
    """重置单例 + 订阅去重记录（仅供测试使用，避免测试之间互相污染，
    与 `reset_event_bus()` 配套调用）。"""
    global _default_manager, _subscribed_keys
    _default_manager = None
    _subscribed_keys = set()
