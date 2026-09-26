"""tests/test_phase4_state_manager.py

对应 `next_doc/refactor_plan/05-phase4-unified-state-sprint-plan.md`
Sprint 4-1 验收标准："goal_mode/runner.py 不再自己持有 GoalState 的可变
引用，而是每次通过 `state_manager.get_state("goal")` 读取"。
"""

from __future__ import annotations

import pytest

from mini_agent.core import Event, get_event_bus, reset_event_bus
from mini_agent.core.goal import GoalState
from mini_agent.core.state_manager import (
    StateManager,
    ensure_state_manager_subscribed,
    get_state_manager,
    reset_state_manager,
)
from mini_agent.goal_mode.runner import GoalRunner
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset_bus_and_manager():
    reset_event_bus()
    reset_state_manager()
    yield
    reset_event_bus()
    reset_state_manager()


def test_get_state_returns_none_before_any_seed():
    manager = StateManager()
    assert manager.get_state("goal") is None


def test_update_state_then_get_state_roundtrip():
    manager = StateManager()
    state = GoalState(goal_text="写周报")
    manager.update_state("goal", state)
    assert manager.get_state("goal") is state


def test_goal_updated_event_auto_updates_managed_goal_state():
    """未经过 GoalRunner，直接验证'事件驱动更新'本身：seed 一次后，
    publish GoalUpdated 应该自动把 round/status 同步进去，不需要再手动
    调用 update_state。"""
    manager = StateManager()
    manager.update_state("goal", GoalState(goal_text="写周报", status="running", round=0))
    manager.subscribe_to_bus(get_event_bus())

    get_event_bus().publish(
        Event(kind="GoalUpdated", payload={"round": 3, "status": "running"})
    )

    state = manager.get_state("goal")
    assert state.round == 3
    assert state.status == "running"


def test_goal_updated_event_ignored_when_not_seeded_yet():
    """还没 seed 过 GoalState 时收到 GoalUpdated：只应该记警告、不报错、
    不会凭空生造一个 GoalState。"""
    manager = StateManager()
    manager.subscribe_to_bus(get_event_bus())

    get_event_bus().publish(Event(kind="GoalUpdated", payload={"round": 1}))

    assert manager.get_state("goal") is None


def test_snapshot_reflects_currently_managed_states():
    manager = StateManager()
    manager.update_state("goal", GoalState(goal_text="写周报", round=2))
    snap = manager.snapshot()
    assert snap["goal"]["goal_text"] == "写周报"
    assert snap["goal"]["round"] == 2


def test_goal_runner_run_done_seeds_state_manager_and_marks_status_done(monkeypatch, tmp_path):
    """端到端：跑一次 GoalRunner.run()（首轮 DONE），全局 StateManager 单例
    应该托管着与本次执行一致的 GoalState（goal_text 对齐、status 变为
    "done"），且这份状态是通过 StateManager 读到的，不是 runner 自己另外
    维护的一份。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()
    assert result.status == "done"

    state = get_state_manager().get_state("goal")
    assert state is not None
    assert state.goal_text == spec.goal_text
    assert state.status == "done"


def test_ensure_state_manager_subscribed_is_idempotent_per_bus_and_manager():
    """同一个 (bus, manager) 组合重复调用不会导致同一个回调被订阅两次
    （模式与 `ensure_event_log_subscribed`/`ensure_experience_recorder_subscribed`
    一致）。"""
    bus = get_event_bus()
    manager = StateManager()
    manager.update_state("goal", GoalState(goal_text="x", round=0))

    ensure_state_manager_subscribed(manager, bus=bus)
    ensure_state_manager_subscribed(manager, bus=bus)  # 重复调用

    assert len(bus._subscribers["GoalUpdated"]) == 1

    bus.publish(Event(kind="GoalUpdated", payload={"round": 5}))
    assert manager.get_state("goal").round == 5
