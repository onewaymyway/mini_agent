"""
tests/test_cron_scheduler_reap_stale_jobs.py

覆盖 next_doc/daemon_task_hang_recovery_and_watchdog_hardening_plan.md
阶段一：CronScheduler.reap_stale_jobs() 对 CronJobRunner.reap_stale_jobs()
的委托——job_runner 未注入（旧路径）时返回空列表；注入时透传返回值。
"""

from __future__ import annotations

from mini_agent.evolution.cron_scheduler import CronScheduler


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)
        self.workdir_dir = root


class _FakeJobRunner:
    def __init__(self, reaped=None):
        self._reaped = reaped or []
        self.calls = 0

    def reap_stale_jobs(self):
        self.calls += 1
        return list(self._reaped)


def test_reap_stale_jobs_returns_empty_without_job_runner(tmp_path):
    scheduler = CronScheduler(_FakePaths(tmp_path))
    assert scheduler.reap_stale_jobs() == []


def test_reap_stale_jobs_delegates_to_job_runner(tmp_path):
    fake_runner = _FakeJobRunner(reaped=["user:job1", "user:job2"])
    scheduler = CronScheduler(_FakePaths(tmp_path), job_runner=fake_runner)
    result = scheduler.reap_stale_jobs()
    assert result == ["user:job1", "user:job2"]
    assert fake_runner.calls == 1
