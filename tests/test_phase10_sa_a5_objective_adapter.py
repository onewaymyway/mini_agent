"""Phase 10 S-A A5：Objective → GoalState 拉取式投影测试。

见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第三节。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mini_agent.core import (
    Event,
    GoalState,
    ObjectiveAdapter,
    get_event_bus,
    project_objective_executions,
    reset_event_bus,
    reset_objective_projection_cache,
)
from mini_agent.core.state_manager import get_state_manager, reset_state_manager
from mini_agent.evolution.objective_executor import (
    ExecutionStep,
    ObjectiveExecution,
    ObjectiveExecutor,
)
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


@pytest.fixture(autouse=True)
def _reset():
    reset_event_bus()
    reset_state_manager()
    reset_objective_projection_cache()
    yield
    reset_event_bus()
    reset_state_manager()
    reset_objective_projection_cache()


def _ex(status="running", idx=0, steps=None, **kw) -> ObjectiveExecution:
    steps = steps if steps is not None else [
        ExecutionStep(step_id="s0", step_index=0, description="第一步", status="done"),
        ExecutionStep(step_id="s1", step_index=1, description="第二步", status="running"),
        ExecutionStep(step_id="s2", step_index=2, description="第三步"),
    ]
    return ObjectiveExecution(
        execution_id="e1", objective_id="o1", objective_title="整理报告",
        steps=steps, current_step_idx=idx, status=status,
        started_at=100.0, **kw,
    )


# ── to_new 映射 ────────────────────────────────────────────────────────

def test_to_new_basic_mapping():
    st = ObjectiveAdapter.to_new(_ex(idx=1))
    assert isinstance(st, GoalState)
    assert (st.goal_text, st.status, st.round) == ("整理报告", "running", 1)
    assert st.current_state == "第二步"
    assert st.evidence["steps_done"] == 1 and st.evidence["steps_total"] == 3
    assert st.evidence["level"] == "objective"
    assert st.created_at == 100.0


@pytest.mark.parametrize("old,new", [
    ("pending", "running"), ("running", "running"), ("paused", "running"),
    ("paused_for_fairness", "running"), ("paused_by_user", "running"),
    ("completed", "done"), ("failed", "failed"), ("cancelled", "cancelled"),
])
def test_status_mapping_and_original_preserved(old, new):
    st = ObjectiveAdapter.to_new(_ex(status=old))
    assert st.status == new
    assert st.evidence["objective_status"] == old  # 不丢原始状态


def test_unknown_status_falls_back_to_running_but_keeps_raw():
    st = ObjectiveAdapter.to_new(_ex(status="weird"))
    assert st.status == "running" and st.evidence["objective_status"] == "weird"


def test_problems_come_only_from_failed_or_blocked_steps_with_error():
    steps = [
        ExecutionStep(step_id="a", step_index=0, description="x", status="failed", error_msg="超时"),
        ExecutionStep(step_id="b", step_index=1, description="y", status="blocked", error_msg="路径冲突"),
        ExecutionStep(step_id="c", step_index=2, description="z", status="failed", error_msg="  "),
        ExecutionStep(step_id="d", step_index=3, description="w", status="done", error_msg="旧错误"),
    ]
    st = ObjectiveAdapter.to_new(_ex(steps=steps, idx=0))
    assert st.problems == ["超时", "路径冲突"]


def test_no_steps_and_index_out_of_range_do_not_crash():
    st = ObjectiveAdapter.to_new(_ex(steps=[], idx=0))
    assert st.current_state == "" and st.evidence["steps_total"] == 0


def test_node_enriches_description_priority_parent():
    class N:
        parent_id, description, priority = "g1", "为什么要做", 70
    st = ObjectiveAdapter.to_new(_ex(), N())
    assert st.ideal_state == "为什么要做" and st.priority == 70
    assert st.evidence["parent_goal_id"] == "g1"


def test_node_with_bad_priority_type_is_ignored():
    class N:
        parent_id, description, priority = None, "", "high"
    assert ObjectiveAdapter.to_new(_ex(), N()).priority is None


def test_to_old_is_explicitly_unimplemented():
    with pytest.raises(NotImplementedError):
        ObjectiveAdapter.to_old(GoalState(goal_text="x"))


# ── 真实 ObjectiveExecutor + GoalBacklog ────────────────────────────────

@pytest.fixture
def real(tmp_path):
    paths = AgentPaths(Path(tmp_path))
    backlog = GoalBacklog(paths)
    submitted: list = []

    def submit(msg, initiator, meta):
        submitted.append(msg)
        return f"turn_{len(submitted)}"

    oe = ObjectiveExecutor(
        paths=paths, submit_fn=submit,
        llm_decompose_fn=lambda obj: ["步骤A", "步骤B"], goal_backlog=backlog,
    )
    goal = backlog.add_goal(title="大目标", priority=50)
    obj = backlog.add_objective(title="子目标", parent_id=goal.id, priority=60,
                                description="子目标的原因")
    exec_id = oe.start(obj)
    assert exec_id
    return oe, backlog, obj, goal, exec_id


def test_projects_real_executor_with_backlog(real):
    oe, backlog, obj, goal, exec_id = real
    states = project_objective_executions(oe, backlog, publish_events=False)
    assert len(states) == 1
    st = states[0]
    assert st.goal_text == "子目标" and st.status == "running"
    assert st.evidence["execution_id"] == exec_id
    assert st.evidence["parent_goal_id"] == goal.id
    assert st.ideal_state == "子目标的原因" and st.priority == 60
    assert st.evidence["steps_total"] == 2


def test_projection_reflects_completion(real):
    oe, backlog, obj, goal, exec_id = real
    ex = oe.get_execution(exec_id)
    turn = next(t for t, (e, i) in oe._turn_to_exec.items() if e == exec_id and i == 0)
    oe.on_turn_done(turn, "A 完成")
    st = project_objective_executions(oe, backlog, publish_events=False)[0]
    assert st.evidence["steps_done"] == 1 and st.round == ex.current_step_idx


def test_projection_is_read_only(real):
    oe, backlog, obj, goal, exec_id = real
    f = oe._exec_path
    before = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    ex_before = oe.get_execution(exec_id).to_dict()
    for _ in range(3):
        project_objective_executions(oe, backlog)
    after = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    assert before == after
    assert oe.get_execution(exec_id).to_dict() == ex_before
    # 也没有写入 StateManager 的 goal（避免覆盖 GoalRunner 的托管）
    assert get_state_manager().get_state("goal") is None


def test_works_without_backlog(real):
    oe, *_ = real
    st = project_objective_executions(oe, None, publish_events=False)[0]
    assert st.priority is None and st.ideal_state == ""


# ── 事件：只在变化时发布 ────────────────────────────────────────────────

def _collect():
    got: list[Event] = []
    get_event_bus().subscribe("ObjectiveProjected", got.append)
    return got


def test_event_published_once_then_deduped(real):
    oe, backlog, *_ = real
    got = _collect()
    project_objective_executions(oe, backlog)
    project_objective_executions(oe, backlog)
    project_objective_executions(oe, backlog)
    assert len(got) == 1
    p = got[0].payload
    assert p["objective_status"] == "running" and p["steps_total"] == 2
    assert got[0].correlation_id == p["execution_id"]


def test_event_republished_after_state_change(real):
    oe, backlog, obj, goal, exec_id = real
    got = _collect()
    project_objective_executions(oe, backlog)
    turn = next(t for t, (e, i) in oe._turn_to_exec.items() if e == exec_id and i == 0)
    oe.on_turn_done(turn, "A 完成")
    project_objective_executions(oe, backlog)
    assert len(got) == 2 and got[1].payload["steps_done"] == 1


def test_publish_events_false_publishes_nothing(real):
    oe, backlog, *_ = real
    got = _collect()
    project_objective_executions(oe, backlog, publish_events=False)
    assert got == []


# ── 容错 ───────────────────────────────────────────────────────────────

class _BrokenExecutor:
    def get_status_summary(self):
        raise RuntimeError("boom")


def test_summary_failure_returns_empty_not_raise():
    assert project_objective_executions(_BrokenExecutor()) == []


class _PartlyBrokenExecutor:
    def get_status_summary(self):
        return [{"execution_id": "bad"}, {"execution_id": "e1"}, {"nope": 1}]

    def get_execution(self, eid):
        if eid == "bad":
            raise RuntimeError("x")
        return _ex()


def test_single_bad_record_is_skipped_others_survive():
    out = project_objective_executions(_PartlyBrokenExecutor(), publish_events=False)
    assert len(out) == 1 and out[0].goal_text == "整理报告"


def test_backlog_lookup_failure_is_tolerated():
    class B:
        def get(self, _):
            raise RuntimeError("x")
    out = project_objective_executions(_PartlyBrokenExecutor(), B(), publish_events=False)
    assert len(out) == 1 and out[0].priority is None


def test_event_log_store_persists_projected_event(tmp_path, real):
    """ObjectiveProjected 在 EVENT_KINDS 内，会被 EventLogStore 落盘。"""
    from mini_agent.core import EventLogStore, ensure_event_log_subscribed
    oe, backlog, obj, goal, exec_id = real
    store = EventLogStore(Path(tmp_path) / "ev.jsonl")
    ensure_event_log_subscribed(store)
    project_objective_executions(oe, backlog)
    evs = store.trace(exec_id)
    assert [e["kind"] for e in evs] == ["ObjectiveProjected"]
