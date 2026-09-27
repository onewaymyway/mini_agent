"""tests/test_phase8_runtime.py

对应 `next_doc/refactor_plan/09-phase8-runtime-convergence-sprint-plan.md`
Sprint 8-1 验收标准：\"通过 CLI 发起一个 Goal，能看到完整走了一遍
Phase 1-7 打通的链路（不是重新实现一遍逻辑，而是复用），且旧的 CLI
相关测试仍然通过\"。

这里不经过真实 CLI（`goal_mode_cmd.py` 没有专门的单元测试基础设施），
直接构造 `AgentRuntime` 验证：
  - `run_once()` 内部确实调用了 `GoalRunner`（而不是重新实现判定逻辑）
  - Event 总线上出现 `RuntimeCycleStarted` → ... → `RuntimeCycleCompleted`，
    且中间夹着 Phase 2 已经打通的 `GoalCreated`/`ActionStarted`/
    `ActionCompleted`/`GoalUpdated`/`ExperienceCreated` 序列
  - `gap detect` 步骤真的执行了（`AgentRuntimeResult.gap` 非空，因为
    测试用的旧版 `GoalSpec` 没有 `current_state`/`ideal_state`）
  - 返回值字段名与 `GoalRunResult` 保持一致，兼容现有 CLI 读取方式
"""

from __future__ import annotations

import pytest

from mini_agent.core import get_event_bus, reset_event_bus
from mini_agent.core.state_manager import reset_state_manager
from mini_agent.runtime import AgentRuntime, AgentRuntimeResult
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset_bus_and_manager():
    reset_event_bus()
    reset_state_manager()
    yield
    reset_event_bus()
    reset_state_manager()


def test_run_once_delegates_to_goal_runner_and_reaches_done(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )

    runtime = AgentRuntime(agent=agent, cfg=cfg)
    result = runtime.run_once(spec)

    assert isinstance(result, AgentRuntimeResult)
    assert result.status == "done"
    assert result.rounds_used == 0
    assert agent._call_idx == 1  # 真的调用了一次 run_turn，说明走的是同一个 GoalRunner
    assert result.goal_run_result is not None
    assert result.goal_run_result.status == "done"


def test_run_once_emits_runtime_cycle_events_wrapping_goal_events(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE",
    )

    kinds: list[str] = []
    get_event_bus().subscribe("RuntimeCycleStarted", lambda e: kinds.append(e.kind))
    get_event_bus().subscribe("RuntimeCycleCompleted", lambda e: kinds.append(e.kind))
    get_event_bus().subscribe("GoalCreated", lambda e: kinds.append(e.kind))
    get_event_bus().subscribe("ExperienceCreated", lambda e: kinds.append(e.kind))

    runtime = AgentRuntime(agent=agent, cfg=cfg)
    runtime.run_once(spec)

    assert kinds[0] == "RuntimeCycleStarted"
    assert kinds[-1] == "RuntimeCycleCompleted"
    assert "GoalCreated" in kinds
    assert "ExperienceCreated" in kinds


def test_run_once_runs_gap_detect_and_reports_missing_current_and_ideal_state(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE",
    )

    runtime = AgentRuntime(agent=agent, cfg=cfg)
    result = runtime.run_once(spec)

    # 旧版 GoalSpec 没有 current_state/ideal_state 字段，detect_gap 应该
    # 据实记录成 problems（见 goals/gap.py），gap 本身走 acceptance_criteria
    # 分支（非空），验证 gap detect 步骤确实执行了，不是被跳过的空列表。
    assert result.gap == [f"未验证达成：{c}" for c in spec.acceptance_criteria]


def test_run_once_keyboard_interrupt_exposes_last_runner_for_pause(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    def _raise_interrupt(**kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge", _raise_interrupt
    )

    runtime = AgentRuntime(agent=agent, cfg=cfg)
    assert runtime.last_runner is None
    with pytest.raises(KeyboardInterrupt):
        runtime.run_once(spec)
    assert runtime.last_runner is not None  # 供 cli 层调用 runtime.last_runner.pause()
