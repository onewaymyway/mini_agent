"""
tests/test_cron_job_runner_resource_arbiter.py

对应 next_doc/scheduling_unification_and_kanban_visibility_improvement_plan.md
P1：CronJobRunner.submit() 接入 ResourceArbiter.gating_state()，只对
非 "sys:" 前缀的用户自定义 job 做仲裁检查（不用 initiator 字段区分——
读码确认 CronScheduler.add_job() 把 initiator 硬编码成 "cron"，这个
字段不代表"谁创建的 job"，只有 job_id 前缀能区分系统/用户 job）。

不依赖真实 ResourceArbiter 的具体判定逻辑（那部分有自己的单测），这里直接
monkeypatch mini_agent.evolution.resource_arbiter.ResourceArbiter，只验证
CronJobRunner 一侧的调用/放行/跳过语义。
"""

from __future__ import annotations

import threading
import time

import pytest

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_job_executor import RunOutcome
from mini_agent.evolution.cron_scheduler import CronJob


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)


class _FakeBaseCfg:
    def __init__(self, cron=None):
        self.cron = cron


def _make_job(job_id="user:job1", name="Test Job", initiator="cron"):
    # initiator 默认就是 "cron"（与 CronScheduler.add_job() 的真实行为一致，
    # 见上方模块 docstring 说明）——门控只看 job_id 是否为 "sys:" 前缀。
    return CronJob(
        id=job_id, name=name, schedule="interval:60",
        task_template="do the thing", initiator=initiator,
    )


class _FakeArbiter:
    """monkeypatch 用的假 ResourceArbiter：state 由测试用例控制。"""

    _next_state = "full"
    call_count = 0

    def __init__(self, paths, cfg):
        pass

    def gating_state(self):
        _FakeArbiter.call_count += 1
        return {"state": _FakeArbiter._next_state}


@pytest.fixture(autouse=True)
def _patch_agent_bridge(monkeypatch):
    import mini_agent.evolution.cron_agent_bridge as bridge_mod

    def _fake_build_cron_agent(base_cfg, job, inner_max_turns=None):
        return object()

    def _fake_make_submit_step_fn(agent):
        def _submit(prompt_text):
            from mini_agent.evolution.cron_job_executor import StepResult
            return StepResult(text="ok", done=True)
        return _submit

    monkeypatch.setattr(bridge_mod, "build_cron_agent", _fake_build_cron_agent)
    monkeypatch.setattr(bridge_mod, "make_submit_step_fn", _fake_make_submit_step_fn)


@pytest.fixture(autouse=True)
def _reset_fake_arbiter():
    _FakeArbiter._next_state = "full"
    _FakeArbiter.call_count = 0
    yield


@pytest.fixture(autouse=True)
def _patch_executor(monkeypatch):
    import mini_agent.evolution.cron_job_executor as executor_mod

    class _FastExecutor:
        def __init__(self, paths):
            pass

        def run_job(self, job, submit_step_fn, default_config=None):
            return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

    monkeypatch.setattr(executor_mod, "CronJobExecutor", _FastExecutor)


def _patch_arbiter(monkeypatch):
    import mini_agent.evolution.resource_arbiter as arbiter_mod
    monkeypatch.setattr(arbiter_mod, "ResourceArbiter", _FakeArbiter)


class TestArbiterGatesUserJobs:
    def test_blocked_state_skips_user_job(self, tmp_path, monkeypatch):
        _patch_arbiter(monkeypatch)
        _FakeArbiter._next_state = "blocked"

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="user:job1")

        assert runner.submit(job) is False
        assert runner.is_running(job.id) is False
        assert runner.arbiter_skipped_count == 1
        assert _FakeArbiter.call_count == 1

    def test_full_state_allows_user_job(self, tmp_path, monkeypatch):
        _patch_arbiter(monkeypatch)
        _FakeArbiter._next_state = "full"

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="user:job1")

        assert runner.submit(job) is True
        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)
        assert runner.arbiter_skipped_count == 0

    def test_degraded_state_still_allows_user_job(self, tmp_path, monkeypatch):
        _patch_arbiter(monkeypatch)
        _FakeArbiter._next_state = "degraded"

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="user:job1")

        assert runner.submit(job) is True
        assert runner.arbiter_skipped_count == 0


class TestArbiterDoesNotGateSystemJobs:
    def test_sys_prefixed_job_bypasses_arbiter_even_when_blocked(self, tmp_path, monkeypatch):
        _patch_arbiter(monkeypatch)
        _FakeArbiter._next_state = "blocked"

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="sys:digest_trim")

        assert runner.submit(job) is True
        assert runner.arbiter_skipped_count == 0
        assert _FakeArbiter.call_count == 0


class TestArbiterFailureIsPermissive:
    def test_arbiter_raising_exception_does_not_block_job(self, tmp_path, monkeypatch):
        import mini_agent.evolution.resource_arbiter as arbiter_mod

        class _RaisingArbiter:
            def __init__(self, paths, cfg):
                raise RuntimeError("boom")

        monkeypatch.setattr(arbiter_mod, "ResourceArbiter", _RaisingArbiter)

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="user:job1")

        # 仲裁模块本身异常时保守放行，不能因为仲裁检查失败导致所有
        # 用户 cron job 停摆。
        assert runner.submit(job) is True
        assert runner.arbiter_skipped_count == 0
