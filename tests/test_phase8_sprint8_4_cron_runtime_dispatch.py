"""tests/test_phase8_sprint8_4_cron_runtime_dispatch.py

对应 `next_doc/refactor_plan/09-phase8-runtime-convergence-sprint-plan.md`
Sprint 8-4："接入 `cron_job_runner`：新增 `cron.runtime_dispatch_enabled`
开关（默认 False），开启后 run_mode="message" cron job 改走
`AgentRuntime.run_once()`"。

覆盖点：
  - 默认（`runtime_dispatch_enabled` 未设置/False）：完全走旧路径，
    `AgentRuntime.run_once()` 不会被调用（新代码路径不触发）。
  - 开启后：`AgentRuntime.run_once()` 被调用一次，返回 status="done"
    时 workspace 状态写为 `STATUS_IDLE`，`on_finished` 回调收到的
    `RunOutcome.status` 也是 `STATUS_IDLE`。
  - 开启后 `run_once()` 返回非 "done" 状态：workspace 写为
    `STATUS_NEEDS_REVIEW`，`last_error` 有值。
  - 开启后 `run_once()` 抛异常：不崩溃，workspace 写为
    `STATUS_NEEDS_REVIEW`，异常信息被记录，`on_finished` 仍然被调用。
  - token 记账：`agent.stats` 存在时会调用
    `ResourceArbiter.record_autonomous_token_usage(usage_type="cron")`。
"""

from __future__ import annotations

import time

import pytest

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_job_workspace import (
    CronJobWorkspace, STATUS_IDLE, STATUS_NEEDS_REVIEW,
)
from mini_agent.runtime.runtime import AgentRuntime, AgentRuntimeResult


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)


class _FakeCronConfig:
    def __init__(self, runtime_dispatch_enabled=False):
        self.default_timeout_seconds = 1200
        self.default_max_steps = 60
        self.runtime_dispatch_enabled = runtime_dispatch_enabled


class _FakeBaseCfg:
    def __init__(self, runtime_dispatch_enabled=False):
        self.cron = _FakeCronConfig(runtime_dispatch_enabled=runtime_dispatch_enabled)


class _FakeAgentStats:
    def __init__(self, input_tokens=10, output_tokens=5):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeAgent:
    def __init__(self):
        self.stats = _FakeAgentStats()


def _make_job(job_id="user:job1"):
    from mini_agent.evolution.cron_scheduler import CronJob
    return CronJob(id=job_id, name="Test Job", schedule="interval:60", task_template="do the thing")


@pytest.fixture(autouse=True)
def _patch_agent_bridge(monkeypatch):
    """统一 mock 掉真实 Agent 构造，避免依赖网络/API key，与
    test_cron_job_runner.py 保持同样的 mock 策略。"""
    import mini_agent.evolution.cron_agent_bridge as bridge_mod

    def _fake_build_cron_agent(base_cfg, job, inner_max_turns=None):
        return _FakeAgent()

    monkeypatch.setattr(bridge_mod, "build_cron_agent", _fake_build_cron_agent)


def _wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_flag_off_does_not_call_agent_runtime(tmp_path, monkeypatch):
    """默认关闭：新路径完全不触发。"""
    import mini_agent.evolution.cron_job_executor as executor_mod
    from mini_agent.evolution.cron_job_executor import RunOutcome

    class _FastExecutor:
        def __init__(self, paths):
            pass

        def run_job(self, job, submit_step_fn, default_config=None):
            return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

    monkeypatch.setattr(executor_mod, "CronJobExecutor", _FastExecutor)

    called = {"n": 0}
    monkeypatch.setattr(AgentRuntime, "run_once", lambda self, *a, **kw: called.__setitem__("n", called["n"] + 1))

    runner = CronJobRunner(_FakeBaseCfg(runtime_dispatch_enabled=False), _FakePaths(tmp_path), max_concurrent=2)
    job = _make_job()
    assert runner.submit(job) is True
    assert _wait_until(lambda: not runner.is_running(job.id))
    assert called["n"] == 0


def test_flag_on_done_status_writes_idle_and_calls_on_finished(tmp_path, monkeypatch):
    finished = {}

    def _on_finished(job_id, outcome):
        finished["job_id"] = job_id
        finished["outcome"] = outcome

    def _fake_run_once(self, goal_spec, **kw):
        return AgentRuntimeResult(
            status="done", rounds_used=3, compacts_done=0,
            final_report="all good", goal_spec=goal_spec,
        )

    monkeypatch.setattr(AgentRuntime, "run_once", _fake_run_once)

    runner = CronJobRunner(
        _FakeBaseCfg(runtime_dispatch_enabled=True), _FakePaths(tmp_path),
        max_concurrent=2, on_finished=_on_finished,
    )
    job = _make_job()
    assert runner.submit(job) is True
    assert _wait_until(lambda: not runner.is_running(job.id))
    assert _wait_until(lambda: "outcome" in finished)

    state = CronJobWorkspace(_FakePaths(tmp_path), job.id).read_state()
    assert state.status == STATUS_IDLE
    assert state.last_error is None
    assert finished["job_id"] == job.id
    assert finished["outcome"].status == STATUS_IDLE
    assert finished["outcome"].steps_executed == 3


def test_flag_on_non_done_status_writes_needs_review(tmp_path, monkeypatch):
    def _fake_run_once(self, goal_spec, **kw):
        return AgentRuntimeResult(
            status="gave_up", rounds_used=5, compacts_done=0,
            final_report="stuck", goal_spec=goal_spec,
        )

    monkeypatch.setattr(AgentRuntime, "run_once", _fake_run_once)

    runner = CronJobRunner(_FakeBaseCfg(runtime_dispatch_enabled=True), _FakePaths(tmp_path), max_concurrent=2)
    job = _make_job(job_id="user:job2")
    assert runner.submit(job) is True
    assert _wait_until(lambda: not runner.is_running(job.id))

    state = CronJobWorkspace(_FakePaths(tmp_path), job.id).read_state()
    assert state.status == STATUS_NEEDS_REVIEW
    assert "gave_up" in (state.last_error or "")


def test_flag_on_run_once_exception_does_not_crash_and_marks_needs_review(tmp_path, monkeypatch):
    def _raise(self, goal_spec, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(AgentRuntime, "run_once", _raise)

    finished = {}
    runner = CronJobRunner(
        _FakeBaseCfg(runtime_dispatch_enabled=True), _FakePaths(tmp_path),
        max_concurrent=2, on_finished=lambda jid, o: finished.__setitem__("outcome", o),
    )
    job = _make_job(job_id="user:job3")
    assert runner.submit(job) is True
    assert _wait_until(lambda: not runner.is_running(job.id))
    assert _wait_until(lambda: "outcome" in finished)

    state = CronJobWorkspace(_FakePaths(tmp_path), job.id).read_state()
    assert state.status == STATUS_NEEDS_REVIEW
    assert "boom" in (state.last_error or "")
    assert finished["outcome"].status == STATUS_NEEDS_REVIEW


def test_flag_on_records_token_usage(tmp_path, monkeypatch):
    recorded = {}

    class _FakeArbiter:
        def __init__(self, paths, cfg):
            pass

        def record_autonomous_token_usage(self, tokens_used, usage_type):
            recorded["tokens_used"] = tokens_used
            recorded["usage_type"] = usage_type

    import mini_agent.evolution.resource_arbiter as arbiter_mod
    monkeypatch.setattr(arbiter_mod, "ResourceArbiter", _FakeArbiter)

    def _fake_run_once(self, goal_spec, **kw):
        return AgentRuntimeResult(
            status="done", rounds_used=1, compacts_done=0,
            final_report="ok", goal_spec=goal_spec,
        )

    monkeypatch.setattr(AgentRuntime, "run_once", _fake_run_once)

    runner = CronJobRunner(_FakeBaseCfg(runtime_dispatch_enabled=True), _FakePaths(tmp_path), max_concurrent=2)
    job = _make_job(job_id="user:job4")
    assert runner.submit(job) is True
    assert _wait_until(lambda: not runner.is_running(job.id))
    assert _wait_until(lambda: "tokens_used" in recorded)

    assert recorded["tokens_used"] == 15  # _FakeAgentStats: 10 + 5
    assert recorded["usage_type"] == "cron"
