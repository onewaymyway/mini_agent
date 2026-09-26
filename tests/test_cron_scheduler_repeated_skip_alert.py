"""
tests/test_cron_scheduler_repeated_skip_alert.py

覆盖 next_doc/goal_cycle_orphan_execution_recovery_plan.md 2.2（连续跳过
告警从"只在恰好等于 threshold 那一次发"改为"每 N 次跳过重复提醒一次"）：

- 第 5、10、15…次连续跳过（threshold=5）各发一次告警，而不是只在第 5 次。
- 中间没跨过整数倍的次数（6、7、8、9）不发告警。
- 一旦成功触发一次，consecutive_skip_count 清零，重新从零开始累积，
  再次跨越阈值时又能正常告警（不会因为"历史上已经告警过"而永久沉默）。
- threshold<=0 时完全不告警（既有行为不变）。

运行方式（仓库暂无 pytest.ini/conftest.py 设置 PYTHONPATH，手动指定 src）：
    PYTHONPATH=src python3 -m pytest tests/test_cron_scheduler_repeated_skip_alert.py -q
"""

from __future__ import annotations

import time

from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.storage.paths import AgentPaths


class _CronCfg:
    def __init__(self, skip_alert_threshold=5):
        self.skip_alert_threshold = skip_alert_threshold


class _FakeBaseCfg:
    def __init__(self, skip_alert_threshold=5):
        self.cron = _CronCfg(skip_alert_threshold=skip_alert_threshold)


class _FakeJobRunner:
    """`_maybe_alert_consecutive_skip()` 只读取 `_base_cfg.cron.
    skip_alert_threshold`；`_fire()` 在注入了 job_runner 时会优先走
    `job_runner.submit(job)` 而不是旧的 submit_fn 路径，所以这个替身也要
    提供 `submit()`，默认转发给外部给定的函数（不给则恒定返回
    submit_result）。"""

    def __init__(self, base_cfg, submit_fn=None, submit_result=False):
        self._base_cfg = base_cfg
        self._submit_fn = submit_fn
        self._submit_result = submit_result

    def submit(self, job):
        if self._submit_fn is not None:
            return self._submit_fn(job)
        return self._submit_result


def _make_scheduler(tmp_path, threshold=5, submit_result=False):
    scheduler = CronScheduler(
        paths=AgentPaths(project_root=tmp_path),
        job_runner=_FakeJobRunner(_FakeBaseCfg(skip_alert_threshold=threshold), submit_result=submit_result),
    )
    return scheduler


class TestRepeatedSkipAlert:
    def test_alerts_fire_on_every_multiple_of_threshold(self, tmp_path, monkeypatch):
        sent = []

        class _FakeDispatcher:
            def __init__(self, paths):
                pass

            def dispatch(self, message):
                sent.append(message)

        import mini_agent.notification.dispatcher as dispatcher_mod
        monkeypatch.setattr(dispatcher_mod, "NotificationDispatcher", _FakeDispatcher)

        scheduler = _make_scheduler(tmp_path, threshold=5, submit_result=False)
        job = scheduler.add_job(name="j1", schedule="interval:1", task_template="hi")

        skip_counts_that_alerted = []
        for _ in range(15):
            scheduler.get(job.id).next_run_at = time.time() - 1
            before = len(sent)
            scheduler.tick()
            if len(sent) > before:
                skip_counts_that_alerted.append(scheduler.get(job.id).consecutive_skip_count)

        # 第 5、10、15 次各发一次，而不是只发一次。
        assert skip_counts_that_alerted == [5, 10, 15]

    def test_reset_after_success_then_alerts_again(self, tmp_path, monkeypatch):
        sent = []

        class _FakeDispatcher:
            def __init__(self, paths):
                pass

            def dispatch(self, message):
                sent.append(message)

        import mini_agent.notification.dispatcher as dispatcher_mod
        monkeypatch.setattr(dispatcher_mod, "NotificationDispatcher", _FakeDispatcher)

        call_results = iter([False, False, False, False, False, True, False, False, False, False, False])
        scheduler = CronScheduler(
            paths=AgentPaths(project_root=tmp_path),
            job_runner=_FakeJobRunner(_FakeBaseCfg(skip_alert_threshold=5), submit_fn=lambda job: next(call_results)),
        )
        job = scheduler.add_job(name="j1", schedule="interval:1", task_template="hi")

        for _ in range(11):
            scheduler.get(job.id).next_run_at = time.time() - 1
            scheduler.tick()

        # 第 5 次跳过告警一次；第 6 次成功清零；随后再跳过 5 次（第 7~11
        # 次 tick，对应清零后的第 1~5 次跳过）在清零后重新累积到 5 又告警
        # 一次——总共两次告警，而不是清零前告警过就永久沉默。
        assert len(sent) == 2
        assert scheduler.get(job.id).consecutive_skip_count == 5

    def test_no_alert_when_threshold_non_positive(self, tmp_path, monkeypatch):
        sent = []

        class _FakeDispatcher:
            def __init__(self, paths):
                pass

            def dispatch(self, message):
                sent.append(message)

        import mini_agent.notification.dispatcher as dispatcher_mod
        monkeypatch.setattr(dispatcher_mod, "NotificationDispatcher", _FakeDispatcher)

        scheduler = _make_scheduler(tmp_path, threshold=0, submit_result=False)
        job = scheduler.add_job(name="j1", schedule="interval:1", task_template="hi")

        for _ in range(10):
            scheduler.get(job.id).next_run_at = time.time() - 1
            scheduler.tick()

        assert sent == []
