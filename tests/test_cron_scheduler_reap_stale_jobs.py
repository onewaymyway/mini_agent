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


class _FakeJobRunnerWithPhase(_FakeJobRunner):
    def __init__(self, phase="not_running"):
        super().__init__()
        self._phase = phase
        self.phase_calls = []

    def execution_phase(self, job_id):
        self.phase_calls.append(job_id)
        return self._phase


def test_execution_phase_returns_not_running_without_job_runner(tmp_path):
    scheduler = CronScheduler(_FakePaths(tmp_path))
    assert scheduler.execution_phase("user:job1") == "not_running"


def test_execution_phase_delegates_to_job_runner(tmp_path):
    fake_runner = _FakeJobRunnerWithPhase(phase="queued")
    scheduler = CronScheduler(_FakePaths(tmp_path), job_runner=fake_runner)
    assert scheduler.execution_phase("user:job1") == "queued"
    assert fake_runner.phase_calls == ["user:job1"]


class TestRemoveJobPurgesQuestions:
    """[cron_async_feedback_hardening_plan.md D5] remove_job() 应该顺带
    清掉该 job 名下的所有问答记录，不留孤儿数据。"""

    def test_remove_job_purges_associated_questions(self, tmp_path):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.notification import questions_store

        paths = AgentPaths(tmp_path)
        scheduler = CronScheduler(paths)
        job = scheduler.add_job(name="测试任务", schedule="0 9 * * *", task_template="做点什么")

        rec = questions_store.append_question(paths, job.id, "要不要继续？")
        assert questions_store.get_question(paths, rec["question_id"]) is not None

        assert scheduler.remove_job(job.id) is True
        assert questions_store.get_question(paths, rec["question_id"]) is None
