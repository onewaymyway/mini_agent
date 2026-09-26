"""goal_mode/runner.py 的 Phase 2 Sprint 2-1 事件埋点集成测试。

对应 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`
Sprint 2-1 验收标准：
  1. Goal 执行一次完整闭环时，能在事件总线上看到至少 4 个 Event 被
     publish（Created → ActionStarted → ActionCompleted/Failed →
     ExperienceCreated）。
  2. Phase 1 的特征测试全部仍然通过（本文件不修改 `tests/test_goal_mode.py`
     本身，只新增独立测试文件，验证"Event 只是旁路记录，不改变
     `GoalRunner.run()` 原有返回值"）。

复用 `tests/test_goal_mode.py` 里已有的 `FakeAgent`/`_FakeCfg`/
`_confirmed_spec` 测试替身（与 `test_compact_autopilot_improvements.py`
/ `test_judge_verdict.py` 的既有做法一致），不重新发明一套 fixture。
"""

from __future__ import annotations

import pytest

from mini_agent.core import Event, get_event_bus, reset_event_bus
from mini_agent.goal_mode.runner import GoalRunner
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset_default_bus():
    reset_event_bus()
    yield
    reset_event_bus()


def test_goal_runner_done_on_first_round_publishes_closed_loop_events(monkeypatch, tmp_path):
    """DONE 于第一轮判定：GoalCreated → ActionStarted → ActionCompleted →
    ExperienceCreated，且顺序、correlation_id 均正确，`run()` 返回值不变。
    """
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )

    received: list[Event] = []
    bus = get_event_bus()
    for kind in ("GoalCreated", "ActionStarted", "ActionCompleted", "ActionFailed", "ExperienceCreated"):
        bus.subscribe(kind, received.append)

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    # 原有业务逻辑的返回值不受影响（止损条件核心要求）。
    assert result.status == "done"
    assert result.rounds_used == 0

    kinds = [ev.kind for ev in received]
    assert kinds == ["GoalCreated", "ActionStarted", "ActionCompleted", "ExperienceCreated"]
    assert len(received) >= 4

    # 同一次 run() 产生的事件共享同一个 correlation_id。
    correlation_ids = {ev.correlation_id for ev in received}
    assert len(correlation_ids) == 1
    assert next(iter(correlation_ids)) is not None

    # causation_id 构成一条因果链：GoalCreated ← ActionStarted ←
    # ActionCompleted ← ExperienceCreated。
    goal_created, action_started, action_completed, experience_created = received
    assert action_started.causation_id == goal_created.id
    assert action_completed.causation_id == action_started.id
    assert experience_created.causation_id == action_completed.id


def test_goal_runner_action_exception_publishes_action_failed_and_reraises(monkeypatch, tmp_path):
    """执行器抛异常时：publish ActionFailed 旁路记录，异常照常向上抛出
    （对应 Sprint 2-1 接入点说明"不改变异常的传播方式"）。
    """
    agent = FakeAgent(outputs=["irrelevant"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    def _boom(self, agent, prompt):
        raise RuntimeError("executor exploded")

    monkeypatch.setattr(
        "mini_agent.goal_mode.executor.CoarseStepExecutor.execute", _boom, raising=True,
    )

    received: list[Event] = []
    get_event_bus().subscribe("ActionFailed", received.append)

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    with pytest.raises(RuntimeError, match="executor exploded"):
        runner.run()

    assert len(received) == 1
    assert received[0].kind == "ActionFailed"
    assert received[0].payload["error"] == "executor exploded"
