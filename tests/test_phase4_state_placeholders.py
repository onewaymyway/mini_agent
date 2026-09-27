"""tests/test_phase4_state_placeholders.py

对应 `next_doc/refactor_plan/05-phase4-unified-state-sprint-plan.md`
Sprint 4-2 验收标准：

1. `SelfState`/`WorldState`/`CapabilityState`/`RuntimeState` 已有结构
   占位，且占位 State（空 dataclass）不会导致 `StateManager` 调用报错。
2. `snapshot()` 输出的结构和 Sprint 4-1 里 `GoalState` 的真实数据一致
   （同样是"kind -> dict"的形状），哪怕占位 State 内容是空 dict。

`SelfState` 在 Sprint 1.5 已经是真实字段的领域模型，不是本 Sprint 新建
的占位（这点与计划文档字面表述有出入，`README.md`/本文件顶部按实情
如实记录），因此额外补一条断言：`agent/lifecycle.py` 唯一接入点确实
把它 seed 进了全局 `StateManager`，不只是转换一次就丢弃。
"""

from __future__ import annotations

import logging

import pytest

from mini_agent.core import (
    CapabilityState,
    Event,
    RuntimeState,
    SelfAdapter,
    SelfState,
    WorldState,
    get_state_manager,
    reset_state_manager,
)
from mini_agent.core.state_manager import StateManager
from mini_agent.perception.self_model import AgentSelfModel


@pytest.fixture(autouse=True)
def _reset_manager():
    reset_state_manager()
    yield
    reset_state_manager()


@pytest.mark.parametrize(
    "cls, kind",
    [
        (WorldState, "world"),
        (CapabilityState, "capability"),
        (RuntimeState, "runtime"),
    ],
)
def test_placeholder_state_has_no_fabricated_fields(cls, kind):
    """占位 State 必须是真正的"空"，不能提前编造字段（止损条件）。"""
    state = cls()
    assert state.__dict__ == {}


@pytest.mark.parametrize(
    "cls, kind",
    [
        (WorldState, "world"),
        (CapabilityState, "capability"),
        (RuntimeState, "runtime"),
    ],
)
def test_placeholder_state_can_be_managed_without_error(cls, kind):
    """占位 State 通过 StateManager 托管 + 读回 + snapshot 都不应该报错。"""
    manager = StateManager()
    state = cls()

    manager.update_state(kind, state)

    assert manager.get_state(kind) is state
    snap = manager.snapshot()
    assert snap[kind] == {}


def test_snapshot_shape_consistent_across_real_and_placeholder_states():
    """`snapshot()` 对"有真实数据的 GoalState"和"空占位 State"输出的都是
    同一种形状（dict），而不是真实的走一套格式、占位的走另一套。
    """
    from mini_agent.core.goal import GoalState

    manager = StateManager()
    manager.update_state("goal", GoalState(goal_text="写周报"))
    manager.update_state("world", WorldState())
    manager.update_state("capability", CapabilityState())
    manager.update_state("runtime", RuntimeState())

    snap = manager.snapshot()

    assert isinstance(snap["goal"], dict) and snap["goal"]["goal_text"] == "写周报"
    for kind in ("world", "capability", "runtime"):
        assert isinstance(snap[kind], dict)
        assert snap[kind] == {}


def test_self_state_is_seeded_into_state_manager_like_lifecycle_does(caplog):
    """复刻 `agent/lifecycle.py` 唯一接入点新增的
    `get_state_manager().update_state("self", ...)` 调用，验证 Sprint 4-2
    真正把（已存在的）`SelfState` 接进了 `StateManager`，而不是只转换一次
    就丢弃——与 `tests/test_core_self_adapter.py` 里对 Adapter 本身转换
    正确性的既有覆盖互补，这里只验证"托管"这一步。
    """
    caplog.set_level(logging.DEBUG, logger="mini_agent.core.trace")

    old = AgentSelfModel(capability_snapshot={"python": 0.8}, active_skill_count=3)
    new = SelfAdapter.to_new(old)

    manager = get_state_manager()
    manager.update_state("self", new)

    fetched = manager.get_state("self")
    assert isinstance(fetched, SelfState)
    assert fetched.capability_snapshot == {"python": 0.8}
    assert fetched.active_skill_count == 3

    snap = manager.snapshot()
    assert snap["self"]["active_skill_count"] == 3
