"""Phase 2 Sprint 2-2 测试：Event 落盘（`EventLogStore`）+
`mini-agent events trace <correlation_id>` CLI 命令。

对应 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`
Sprint 2-2 验收标准："能用 `events trace` 命令完整重放 Sprint 2-1 里那次
Goal 执行的事件序列，事件顺序与实际执行顺序一致"。
"""

from __future__ import annotations

import pytest

from mini_agent.core import get_event_bus, reset_event_bus
from mini_agent.core.event_log_store import (
    EventLogStore,
    ensure_event_log_subscribed,
    reset_event_log_subscriptions,
)
from mini_agent.core.events import Event
from mini_agent.goal_mode.runner import GoalRunner
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset_bus_and_subscriptions():
    reset_event_bus()
    reset_event_log_subscriptions()
    yield
    reset_event_bus()
    reset_event_log_subscriptions()


def test_event_log_store_append_and_trace_order(tmp_path):
    """基本读写：trace() 按 at 升序返回，且只匹配对应 correlation_id。"""
    store = EventLogStore(path=tmp_path / "events.jsonl")

    e1 = Event(kind="GoalCreated", at=1.0, correlation_id="corr-a")
    e2 = Event(kind="ActionStarted", at=2.0, correlation_id="corr-a", causation_id=e1.id)
    e_other = Event(kind="GoalCreated", at=1.5, correlation_id="corr-b")

    # 故意乱序写入，验证 trace() 自己按 at 排序，不依赖写入顺序。
    store.append(e2)
    store.append(e_other)
    store.append(e1)

    chain = store.trace("corr-a")
    assert [d["kind"] for d in chain] == ["GoalCreated", "ActionStarted"]
    assert [d["at"] for d in chain] == [1.0, 2.0]

    assert store.trace("corr-does-not-exist") == []


def test_ensure_event_log_subscribed_is_idempotent_per_bus_and_path(tmp_path):
    """同一个 (bus, path) 组合重复调用不会导致同一事件被写两遍。"""
    bus = get_event_bus()
    store = EventLogStore(path=tmp_path / "events.jsonl")

    ensure_event_log_subscribed(store, bus=bus)
    ensure_event_log_subscribed(store, bus=bus)  # 重复调用

    bus.publish(Event(kind="GoalCreated", correlation_id="corr-x"))

    all_events = store.all()
    assert len(all_events) == 1


def test_goal_runner_run_persists_events_and_cli_trace_replays_them(monkeypatch, tmp_path):
    """端到端：跑一次 GoalRunner.run()，事件落盘到
    `<project_root>/.agent/events.jsonl`，`run_events_cli(["trace", cid])`
    能重放出与 `tests/test_goal_mode_phase2_events.py` 里内存断言一致的
    事件序列。
    """
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

    correlation_id = runner._core_correlation_id

    from mini_agent.storage.paths import AgentPaths

    log_path = AgentPaths(project_root=tmp_path).workdir_event_log
    assert log_path.exists()

    store = EventLogStore(path=log_path)
    chain = store.trace(correlation_id)
    kinds = [d["kind"] for d in chain]
    assert kinds == ["GoalCreated", "ActionStarted", "ActionCompleted", "ExperienceCreated"]
    # 时间戳单调不减：CLI 打印顺序与实际执行顺序一致。
    ats = [d["at"] for d in chain]
    assert ats == sorted(ats)

    # 通过 CLI 入口重放，验证命令行路径（不只是直接调 EventLogStore）。
    from mini_agent.cli.commands.events_cmd import run_events_cli

    rc = run_events_cli(["trace", correlation_id], project_root=tmp_path)
    assert rc == 0


def test_events_cli_trace_missing_project_gives_friendly_message(tmp_path, capsys):
    """从未跑过 Goal 的项目：不报错，给出明确提示。"""
    from mini_agent.cli.commands.events_cmd import run_events_cli

    rc = run_events_cli(["trace", "some-correlation-id"], project_root=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "还没有任何事件落盘记录" in out


def test_events_cli_trace_unknown_correlation_id(monkeypatch, tmp_path):
    """项目有事件记录，但 correlation_id 查无匹配。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )
    GoalRunner(agent=agent, cfg=cfg, goal_spec=spec).run()

    from mini_agent.cli.commands.events_cmd import run_events_cli

    rc = run_events_cli(["trace", "no-such-correlation-id"], project_root=tmp_path)
    assert rc == 0
