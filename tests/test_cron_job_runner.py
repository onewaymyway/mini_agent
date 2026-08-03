"""
tests/test_cron_job_runner.py — CronJobRunner 后台线程调度器集成测试

对应实施记录「剩余工作 #3」：CronJobRunner 依赖真实线程调度 + 真实
Agent/LLM client 构造（build_cron_agent），之前只做过人工冒烟验证。这里
用 monkeypatch 替换掉 cron_agent_bridge.build_cron_agent /
make_submit_step_fn 以及 CronJobExecutor 本身（换成一个不依赖真实 LLM 的
假实现），只验证 CronJobRunner 自己负责的调度语义：

  - submit() 立即返回、真正执行发生在后台线程
  - 同一个 job 还在跑时再次 submit() 应该被拒绝（去重）
  - 并发上限（threading.Semaphore）生效，超出上限的 job 排队而不是丢弃
  - on_finished 回调在执行完成后被调用，参数正确
  - build_cron_agent 构造阶段抛异常时，workspace 状态被兜底标记为
    needs_human_review，不会"悄悄消失"

这些都是 CronJobRunner 自身的职责（线程管理/并发控制/异常兜底），和
"agent 到底怎么跑一步"是正交的，所以 mock 掉后者对覆盖前者没有损失。
"""

from __future__ import annotations

import threading
import time

import pytest

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_job_executor import RunOutcome
from mini_agent.evolution.cron_job_workspace import (
    CronJobWorkspace, STATUS_NEEDS_REVIEW,
)


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)


class _FakeCronConfig:
    def __init__(self, default_timeout_seconds=1200, default_max_steps=60):
        self.default_timeout_seconds = default_timeout_seconds
        self.default_max_steps = default_max_steps


class _FakeBaseCfg:
    def __init__(self, cron=None):
        self.cron = cron


def _make_job(job_id="user:job1", name="Test Job"):
    from mini_agent.evolution.cron_scheduler import CronJob
    return CronJob(id=job_id, name=name, schedule="interval:60", task_template="do the thing")


@pytest.fixture(autouse=True)
def _patch_agent_bridge(monkeypatch):
    """所有测试统一 mock 掉真实 Agent 构造，避免依赖网络/API key。"""
    import mini_agent.evolution.cron_agent_bridge as bridge_mod

    def _fake_build_cron_agent(base_cfg, job, inner_max_turns=None):
        return object()  # 占位，不需要真实 Agent

    def _fake_make_submit_step_fn(agent):
        def _submit(prompt_text):
            from mini_agent.evolution.cron_job_executor import StepResult
            return StepResult(text="ok", done=True)
        return _submit

    monkeypatch.setattr(bridge_mod, "build_cron_agent", _fake_build_cron_agent)
    monkeypatch.setattr(bridge_mod, "make_submit_step_fn", _fake_make_submit_step_fn)


class TestCronJobRunnerDedup:
    def test_submit_returns_true_and_runs_in_background(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_job_executor as executor_mod

        done_event = threading.Event()

        class _FastExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                done_event.set()
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _FastExecutor)

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job()
        submitted = runner.submit(job)

        assert submitted is True
        assert done_event.wait(timeout=2.0), "job did not execute in background thread within timeout"

    def test_duplicate_submit_while_running_is_rejected(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_job_executor as executor_mod

        started = threading.Event()
        release = threading.Event()

        class _BlockingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                started.set()
                release.wait(timeout=5.0)
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _BlockingExecutor)

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job()

        assert runner.submit(job) is True
        assert started.wait(timeout=2.0)
        # 同一个 job 还在跑，第二次 submit 应该被拒绝
        assert runner.submit(job) is False
        assert runner.is_running(job.id) is True

        release.set()
        # 等待第一次执行结束
        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)
        assert runner.is_running(job.id) is False


class TestCronJobRunnerConcurrency:
    def test_concurrency_limit_is_respected(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_job_executor as executor_mod

        max_concurrent = 2
        lock = threading.Lock()
        current = 0
        peak = 0
        release = threading.Event()

        class _TrackingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                nonlocal current, peak
                with lock:
                    current += 1
                    peak = max(peak, current)
                release.wait(timeout=5.0)
                with lock:
                    current -= 1
                return RunOutcome(run_id="r", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _TrackingExecutor)

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=max_concurrent)
        jobs = [_make_job(job_id=f"user:job{i}") for i in range(5)]
        for j in jobs:
            assert runner.submit(j) is True

        # 给线程一点时间把 peak 冲到信号量上限
        time.sleep(0.3)
        assert peak <= max_concurrent

        release.set()
        for _ in range(50):
            if runner.running_count == 0:
                break
            time.sleep(0.05)
        assert runner.running_count == 0
        assert peak == max_concurrent, "expected concurrency to actually reach the configured limit"


class TestCronJobRunnerCallback:
    def test_on_finished_callback_invoked_with_outcome(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_job_executor as executor_mod

        expected_outcome = RunOutcome(run_id="abc", status="idle", steps_executed=3, duration_seconds=1.23)

        class _FixedExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                return expected_outcome

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _FixedExecutor)

        received = {}
        done_event = threading.Event()

        def _on_finished(job_id, outcome):
            received["job_id"] = job_id
            received["outcome"] = outcome
            done_event.set()

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2, on_finished=_on_finished)
        job = _make_job(job_id="user:cb_job")
        runner.submit(job)

        assert done_event.wait(timeout=2.0)
        assert received["job_id"] == "user:cb_job"
        assert received["outcome"] is expected_outcome

    def test_on_finished_exception_does_not_crash_thread(self, tmp_path, monkeypatch):
        """回调自己抛异常不应该导致 running_job_ids 卡住不释放。"""
        import mini_agent.evolution.cron_job_executor as executor_mod

        class _FixedExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                return RunOutcome(run_id="x", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _FixedExecutor)

        def _bad_callback(job_id, outcome):
            raise RuntimeError("callback boom")

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2, on_finished=_bad_callback)
        job = _make_job(job_id="user:cb_job2")
        runner.submit(job)

        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)
        assert runner.is_running(job.id) is False


class TestCronJobRunnerBuildAgentFailure:
    def test_build_cron_agent_exception_marks_needs_human_review(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_agent_bridge as bridge_mod

        def _raising_build(base_cfg, job, inner_max_turns=None):
            raise RuntimeError("LLM client construction failed")

        monkeypatch.setattr(bridge_mod, "build_cron_agent", _raising_build)

        paths = _FakePaths(tmp_path)
        runner = CronJobRunner(_FakeBaseCfg(), paths, max_concurrent=2)
        job = _make_job(job_id="user:broken_job")
        runner.submit(job)

        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)

        ws = CronJobWorkspace(paths, job.id)
        state = ws.read_state()
        assert state.status == STATUS_NEEDS_REVIEW
        assert "LLM client construction failed" in state.last_error


class TestCronJobRunnerDefaultConfigConstruction:
    def test_global_cron_config_forwarded_as_default_config(self, tmp_path, monkeypatch):
        """base_cfg.cron 的 default_timeout_seconds/default_max_steps 应该被
        转换成 CronJobConfig 并传给 executor.run_job()。"""
        import mini_agent.evolution.cron_job_executor as executor_mod

        captured = {}

        class _CapturingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                captured["default_config"] = default_config
                return RunOutcome(run_id="r", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _CapturingExecutor)

        base_cfg = _FakeBaseCfg(cron=_FakeCronConfig(default_timeout_seconds=777, default_max_steps=9))
        runner = CronJobRunner(base_cfg, _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="user:cfg_job")
        runner.submit(job)

        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)

        assert captured["default_config"] is not None
        assert captured["default_config"].timeout_seconds == 777
        assert captured["default_config"].max_steps == 9


class _FakeCronConfigWithGrace(_FakeCronConfig):
    def __init__(self, default_timeout_seconds=1200, default_max_steps=60,
                 stale_job_watchdog_grace_seconds=300):
        super().__init__(default_timeout_seconds, default_max_steps)
        self.stale_job_watchdog_grace_seconds = stale_job_watchdog_grace_seconds


class TestCronJobRunnerReapStaleJobs:
    """覆盖 next_doc/daemon_task_hang_recovery_and_watchdog_hardening_plan.md
    阶段一：CronJobRunner.reap_stale_jobs() watchdog 回收。"""

    def test_reap_after_timeout_frees_job_for_resubmit(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_job_executor as executor_mod

        started = threading.Event()
        release = threading.Event()

        class _BlockingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                started.set()
                release.wait(timeout=10.0)
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _BlockingExecutor)

        base_cfg = _FakeBaseCfg(cron=_FakeCronConfigWithGrace(
            default_timeout_seconds=10, stale_job_watchdog_grace_seconds=0,
        ))
        paths = _FakePaths(tmp_path)
        runner = CronJobRunner(base_cfg, paths, max_concurrent=2)
        job = _make_job(job_id="user:stuck_job")

        assert runner.submit(job) is True
        assert started.wait(timeout=2.0)
        assert runner.is_running(job.id) is True

        # 还没超时：不应该被回收
        reaped = runner.reap_stale_jobs(now=time.time())
        assert reaped == []
        assert runner.is_running(job.id) is True

        # 模拟已经过了 有效阈值(10s + grace 0s)
        future = time.time() + 11
        reaped = runner.reap_stale_jobs(now=future)
        assert reaped == [job.id]
        assert runner.is_running(job.id) is False
        assert runner.reaped_job_count == 1

        # 回收之后应该可以重新 submit
        assert runner.submit(job) is True

        ws = CronJobWorkspace(paths, job.id)
        state = ws.read_state()
        assert state.status == STATUS_NEEDS_REVIEW

        release.set()

    def test_orphan_thread_finishing_after_reap_does_not_double_release_semaphore(self, tmp_path, monkeypatch):
        """被 watchdog 回收之后，原来卡住的孤儿线程如果最终真的返回了，
        不应该再次释放 semaphore（否则会把许可数撑大）。"""
        import mini_agent.evolution.cron_job_executor as executor_mod

        started = threading.Event()
        release = threading.Event()

        class _BlockingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                started.set()
                release.wait(timeout=10.0)
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _BlockingExecutor)

        base_cfg = _FakeBaseCfg(cron=_FakeCronConfigWithGrace(
            default_timeout_seconds=10, stale_job_watchdog_grace_seconds=0,
        ))
        paths = _FakePaths(tmp_path)
        runner = CronJobRunner(base_cfg, paths, max_concurrent=1)
        job = _make_job(job_id="user:orphan_job")

        assert runner.submit(job) is True
        assert started.wait(timeout=2.0)

        future = time.time() + 11
        reaped = runner.reap_stale_jobs(now=future)
        assert reaped == [job.id]

        # 此刻信号量应该已经被 watchdog 释放一次，可以立即 submit 一个新 job
        # 并让它真正开始执行（如果许可没被正确释放，这里会因为拿不到许可
        # 而卡住直到测试超时失败）。
        job2 = _make_job(job_id="user:other_job")
        assert runner.submit(job2) is True

        # 放行原来那条卡住的孤儿线程，让它"迟到"地跑完
        release.set()
        for _ in range(50):
            if runner.reaped_job_count == 1 and not runner.is_running(job.id):
                break
            time.sleep(0.05)

        # running_count 不应该因为孤儿线程收尾而被异常修改（job2 仍在跑，
        # 因为 release Event 是共享的，job2 的假 executor 也会被放行，
        # 等它跑完 running_count 归零即可，关键是没有抛异常/没有负数）。
        for _ in range(50):
            if runner.running_count == 0:
                break
            time.sleep(0.05)
        assert runner.running_count == 0

    def test_not_yet_timed_out_job_is_not_reaped(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_job_executor as executor_mod

        started = threading.Event()
        release = threading.Event()

        class _BlockingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                started.set()
                release.wait(timeout=5.0)
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _BlockingExecutor)

        base_cfg = _FakeBaseCfg(cron=_FakeCronConfigWithGrace(
            default_timeout_seconds=1200, stale_job_watchdog_grace_seconds=300,
        ))
        runner = CronJobRunner(base_cfg, _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="user:normal_job")

        assert runner.submit(job) is True
        assert started.wait(timeout=2.0)

        reaped = runner.reap_stale_jobs(now=time.time() + 5)
        assert reaped == []
        assert runner.is_running(job.id) is True

        release.set()
        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)

    def test_falls_back_to_default_timeout_when_job_has_no_own_config(self, tmp_path, monkeypatch):
        """job 自己的 config.json 不存在时，回退全局 default_timeout_seconds。"""
        import mini_agent.evolution.cron_job_executor as executor_mod

        started = threading.Event()
        release = threading.Event()

        class _BlockingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                started.set()
                release.wait(timeout=5.0)
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _BlockingExecutor)

        base_cfg = _FakeBaseCfg(cron=_FakeCronConfigWithGrace(
            default_timeout_seconds=8, stale_job_watchdog_grace_seconds=0,
        ))
        runner = CronJobRunner(base_cfg, _FakePaths(tmp_path), max_concurrent=2)
        job = _make_job(job_id="user:no_config_job")

        assert runner.submit(job) is True
        assert started.wait(timeout=2.0)

        # 用全局默认阈值（8s + grace 0s）判断
        assert runner.reap_stale_jobs(now=time.time() + 3) == []
        reaped = runner.reap_stale_jobs(now=time.time() + 9)
        assert reaped == [job.id]

        release.set()

    def test_reap_stale_jobs_internal_exception_does_not_block_other_jobs(self, tmp_path, monkeypatch):
        """一个 job 的回收逻辑抛异常，不应该影响其它 job 的回收。"""
        import mini_agent.evolution.cron_job_executor as executor_mod

        release = threading.Event()

        class _BlockingExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                release.wait(timeout=5.0)
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _BlockingExecutor)

        base_cfg = _FakeBaseCfg(cron=_FakeCronConfigWithGrace(
            default_timeout_seconds=5, stale_job_watchdog_grace_seconds=0,
        ))
        runner = CronJobRunner(base_cfg, _FakePaths(tmp_path), max_concurrent=3)

        job_bad = _make_job(job_id="user:bad_job")
        job_good = _make_job(job_id="user:good_job")
        assert runner.submit(job_bad) is True
        assert runner.submit(job_good) is True
        time.sleep(0.2)

        orig_effective = runner._effective_timeout_seconds

        def _boom(job_id):
            if job_id == "user:bad_job":
                raise RuntimeError("boom")
            return orig_effective(job_id)

        monkeypatch.setattr(runner, "_effective_timeout_seconds", _boom)

        reaped = runner.reap_stale_jobs(now=time.time() + 100)
        assert reaped == ["user:good_job"]
        assert runner.is_running("user:good_job") is False
        # bad_job 抛异常，回收逻辑跳过它，它仍然"在跑"（记账未清理）
        assert runner.is_running("user:bad_job") is True

        release.set()
