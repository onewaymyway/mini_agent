"""Phase 10 S-A A2：RuntimeState / CapabilityState 填入真实字段的测试。

见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第二节。
"""

from __future__ import annotations

import pytest

from mini_agent.core import (
    CapabilityState,
    Event,
    RuntimeState,
    build_capability_state,
    get_event_bus,
    refresh_capability_state,
    reset_event_bus,
)
from mini_agent.core.state_manager import StateManager, get_state_manager, reset_state_manager
from mini_agent.runtime import AgentRuntime
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset():
    reset_event_bus()
    reset_state_manager()
    yield
    reset_event_bus()
    reset_state_manager()


# ── RuntimeState：纯事件驱动 ──────────────────────────────────────────

def _mgr_with_bus():
    bus = get_event_bus()
    mgr = StateManager()
    mgr.subscribe_to_bus(bus)
    return bus, mgr


def test_runtime_state_accumulates_from_events():
    bus, mgr = _mgr_with_bus()
    bus.publish(Event(kind="RuntimeCycleStarted", payload={"goal_text": "g"}, at=100.0))
    st = mgr.get_state("runtime")
    assert isinstance(st, RuntimeState)
    assert (st.cycles_started, st.cycles_completed, st.last_cycle_started_at) == (1, 0, 100.0)

    bus.publish(Event(kind="RuntimeCycleCompleted",
                      payload={"status": "done", "gap_item_count": 2, "learn": {"observed": 3}}, at=105.0))
    st = mgr.get_state("runtime")
    assert (st.cycles_completed, st.last_cycle_status, st.last_gap_item_count) == (1, "done", 2)
    assert st.last_cycle_finished_at == 105.0
    assert st.last_learn_summary == {"observed": 3}


def test_learn_summary_kept_when_next_cycle_has_no_learn_key():
    bus, mgr = _mgr_with_bus()
    bus.publish(Event(kind="RuntimeCycleCompleted", payload={"status": "done", "learn": {"observed": 1}}))
    bus.publish(Event(kind="RuntimeCycleCompleted", payload={"status": "stuck"}))
    st = mgr.get_state("runtime")
    assert st.last_cycle_status == "stuck" and st.last_learn_summary == {"observed": 1}
    assert st.cycles_completed == 2


def test_bad_payload_types_are_skipped_not_raised():
    bus, mgr = _mgr_with_bus()
    bus.publish(Event(kind="RuntimeCycleCompleted",
                      payload={"status": 5, "gap_item_count": True, "learn": "x"}))
    st = mgr.get_state("runtime")
    assert st.cycles_completed == 1
    assert st.last_cycle_status == "" and st.last_gap_item_count is None and st.last_learn_summary == {}


def test_in_flight_cycles_visible_as_started_minus_completed():
    bus, mgr = _mgr_with_bus()
    bus.publish(Event(kind="RuntimeCycleStarted"))
    bus.publish(Event(kind="RuntimeCycleStarted"))
    bus.publish(Event(kind="RuntimeCycleCompleted", payload={"status": "done"}))
    st = mgr.get_state("runtime")
    assert st.cycles_started - st.cycles_completed == 1


def test_runtime_state_to_dict_and_snapshot():
    bus, mgr = _mgr_with_bus()
    bus.publish(Event(kind="RuntimeCycleStarted", at=1.0))
    snap = mgr.snapshot()
    assert snap["runtime"]["cycles_started"] == 1
    assert set(RuntimeState().to_dict()) == {
        "cycles_started", "cycles_completed", "last_cycle_status", "last_cycle_started_at",
        "last_cycle_finished_at", "last_gap_item_count", "last_learn_summary",
    }


# ── CapabilityState：投影三个注册表 ───────────────────────────────────

class _Reg:
    def names(self):
        return ["b_tool", "a_tool"]


class _Skills:
    available = ["s2", "s1"]
    active = ["s1"]


class _Wf:
    def list_all(self):
        return [{"name": "wf_b"}, {"name": "wf_a"}, {"description": "no name"}]


def test_build_capability_state_reads_all_three_sorted():
    st = build_capability_state(_Reg(), _Skills(), _Wf())
    assert st.tools == ["a_tool", "b_tool"]
    assert (st.skills_available, st.skills_active) == (["s1", "s2"], ["s1"])
    assert st.workflows == ["wf_a", "wf_b"]
    assert st.refreshed_at is not None


def test_sources_are_optional_and_failures_are_isolated():
    assert build_capability_state().tools == []

    class _Boom:
        def names(self):
            raise RuntimeError("x")

    st = build_capability_state(_Boom(), _Skills(), None)
    assert st.tools == [] and st.skills_available == ["s1", "s2"]  # 一个来源失败不影响别的


def test_projector_does_not_construct_workflow_store(tmp_path):
    """WorkflowStore.__init__ 会 mkdir；不传 store 时不得产生任何磁盘副作用。"""
    build_capability_state(_Reg(), _Skills(), None)
    assert list(tmp_path.iterdir()) == []


def test_refresh_hands_state_to_manager():
    mgr = StateManager()
    st = refresh_capability_state(_Reg(), None, None, state_manager=mgr)
    assert mgr.get_state("capability") is st and isinstance(st, CapabilityState)
    assert mgr.snapshot()["capability"]["tools"] == ["a_tool", "b_tool"]


def test_real_toolregistry_and_workflowstore(tmp_path):
    from mini_agent.tools import ToolRegistry
    from mini_agent.workflow.store import WorkflowStore

    # 真实类，空注册表：验证 names()/list_all() 的真实签名与本投影兼容
    st = build_capability_state(ToolRegistry(), None, WorkflowStore(tmp_path))
    assert st.tools == [] and st.workflows == []


# ── AgentRuntime.run_once 端到端 ──────────────────────────────────────

def _patch_judge(monkeypatch):
    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge",
                        lambda **kw: "GOAL_STATUS: DONE")


def test_run_once_populates_runtime_state_including_first_cycle(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=_FakeCfg(tmp_path)).run_once(_confirmed_spec())
    st = get_state_manager().get_state("runtime")
    assert isinstance(st, RuntimeState)
    # 第一次周期的 Started 事件也必须被计到（订阅发生在发布之前）
    assert (st.cycles_started, st.cycles_completed, st.last_cycle_status) == (1, 1, "done")


def test_capability_snapshot_off_by_default(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    agent = FakeAgent(outputs=["x"])
    agent.registry = _Reg()
    AgentRuntime(agent=agent, cfg=_FakeCfg(tmp_path)).run_once(_confirmed_spec())
    assert get_state_manager().get_state("capability") is None


def test_capability_snapshot_opt_in(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_capability_snapshot_enabled = True
    agent = FakeAgent(outputs=["x"])
    agent.registry = _Reg()
    agent.skill_loader = _Skills()
    AgentRuntime(agent=agent, cfg=cfg).run_once(_confirmed_spec())
    st = get_state_manager().get_state("capability")
    assert st.tools == ["a_tool", "b_tool"] and st.skills_active == ["s1"]


def test_capability_snapshot_failure_does_not_break_cycle(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    monkeypatch.setattr("mini_agent.core.capability_projector.refresh_capability_state",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_capability_snapshot_enabled = True
    r = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=cfg).run_once(_confirmed_spec())
    assert r.status == "done"


def test_worldstate_stays_empty_placeholder():
    """Q-A2 选 (a)：World 没有真实来源，保持空占位（见 14 号文档）。"""
    from dataclasses import fields
    from mini_agent.core import WorldState

    assert fields(WorldState) == ()
