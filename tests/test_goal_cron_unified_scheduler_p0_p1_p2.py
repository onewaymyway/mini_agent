"""
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py

对应 next_doc/goal_cron_unified_scheduler_improvement_plan.md：

P0 — CronJobRunner.set_gating_degraded()/effective_max_concurrent()：degraded
     态收紧并发上限（不是整体拒绝提交），full 态恢复。
P1 — cron job 执行完毕后把 token 计入 ResourceArbiter 的 used_today_cron，
     gating_state() 的 blocked 原因附带三类消耗的分项数字。
P2 — CronJob.consecutive_skip_count 由 CronScheduler.tick() 维护，跨越
     cron.skip_alert_threshold 时发一次通知，成功触发后清零。
"""

from __future__ import annotations

import time

import pytest

from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_job_executor import RunOutcome
from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler


class _FakePaths:
    def __init__(self, root):
        self.project_root = str(root)
        self._root = root

    @property
    def workdir_dir(self):
        d = self._root / ".agent"
        d.mkdir(parents=True, exist_ok=True)
        return d


class _AutonomyCfg:
    def __init__(self, degraded_enabled=True):
        self.resource_gating_degraded_enabled = degraded_enabled


class _CronCfg:
    def __init__(self, degraded_max_concurrent=1, skip_alert_threshold=5):
        self.degraded_max_concurrent = degraded_max_concurrent
        self.skip_alert_threshold = skip_alert_threshold


class _FakeBaseCfg:
    def __init__(self, cron=None, autonomy=None):
        self.cron = cron if cron is not None else _CronCfg()
        self.autonomy = autonomy if autonomy is not None else _AutonomyCfg()


def _make_job(job_id="user:job1", name="Test Job"):
    return CronJob(id=job_id, name=name, schedule="interval:60", task_template="do the thing")


# ── P0：degraded 并发收紧 / full 恢复 ──────────────────────────────────────

class TestP0DegradedConcurrency:
    def test_full_state_effective_cap_is_construction_value(self, tmp_path):
        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=3)
        assert runner.effective_max_concurrent() == 3

    def test_degraded_state_tightens_to_cron_config_value(self, tmp_path):
        runner = CronJobRunner(_FakeBaseCfg(cron=_CronCfg(degraded_max_concurrent=1)), _FakePaths(tmp_path), max_concurrent=3)
        runner.set_gating_degraded(True)
        assert runner.effective_max_concurrent() == 1

    def test_degraded_never_exceeds_construction_cap(self, tmp_path):
        # degraded_max_concurrent 配置得比构造上限还大时，仍以构造上限为准
        # （只降不升）。
        runner = CronJobRunner(_FakeBaseCfg(cron=_CronCfg(degraded_max_concurrent=10)), _FakePaths(tmp_path), max_concurrent=2)
        runner.set_gating_degraded(True)
        assert runner.effective_max_concurrent() == 2

    def test_degraded_disabled_globally_keeps_full_cap(self, tmp_path):
        runner = CronJobRunner(
            _FakeBaseCfg(autonomy=_AutonomyCfg(degraded_enabled=False)),
            _FakePaths(tmp_path), max_concurrent=3,
        )
        runner.set_gating_degraded(True)
        assert runner.effective_max_concurrent() == 3

    def test_toggle_back_to_full_restores_cap(self, tmp_path):
        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=3)
        runner.set_gating_degraded(True)
        assert runner.effective_max_concurrent() == 1
        runner.set_gating_degraded(False)
        assert runner.effective_max_concurrent() == 3


class TestP0DegradedStillTriggersJobs(object):
    """degraded 状态下到期的普通 cron job 仍应被触发（并发收紧到 1，不是
    完全不跑）——不是 P0 之前那种\"blocked 就整体跳过\"的行为。"""

    def test_job_still_submits_and_runs_when_degraded(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_agent_bridge as bridge_mod
        import mini_agent.evolution.cron_job_executor as executor_mod

        monkeypatch.setattr(bridge_mod, "build_cron_agent", lambda base_cfg, job, inner_max_turns=None: object())
        monkeypatch.setattr(bridge_mod, "make_submit_step_fn", lambda agent: (lambda p: None))

        class _FastExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _FastExecutor)

        runner = CronJobRunner(_FakeBaseCfg(), _FakePaths(tmp_path), max_concurrent=2)
        runner.set_gating_degraded(True)
        job = _make_job()

        assert runner.submit(job) is True
        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)
        assert runner.is_running(job.id) is False


# ── P1：cron token 记账 ──────────────────────────────────────────────────

class TestP1CronTokenAccounting:
    def test_cron_job_execution_records_used_today_cron(self, tmp_path, monkeypatch):
        import mini_agent.evolution.cron_agent_bridge as bridge_mod
        import mini_agent.evolution.cron_job_executor as executor_mod

        class _StatsAgent:
            class _Stats:
                input_tokens = 120
                output_tokens = 80

            stats = _Stats()

        monkeypatch.setattr(bridge_mod, "build_cron_agent", lambda base_cfg, job, inner_max_turns=None: _StatsAgent())
        monkeypatch.setattr(bridge_mod, "make_submit_step_fn", lambda agent: (lambda p: None))

        class _FastExecutor:
            def __init__(self, paths):
                pass

            def run_job(self, job, submit_step_fn, default_config=None):
                return RunOutcome(run_id="r1", status="idle", steps_executed=1, duration_seconds=0.01)

        monkeypatch.setattr(executor_mod, "CronJobExecutor", _FastExecutor)

        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import ensure_self_profile
        paths = AgentPaths(project_root=tmp_path)
        ensure_self_profile(paths)

        runner = CronJobRunner(_FakeBaseCfg(), paths, max_concurrent=2)
        job = _make_job()
        assert runner.submit(job) is True
        for _ in range(50):
            if not runner.is_running(job.id):
                break
            time.sleep(0.05)

        from mini_agent.perception.global_knowledge import load_self_profile
        profile = load_self_profile(paths)
        assert profile is not None
        rb = profile.resource_budget
        assert getattr(rb, "used_today_cron", 0) == 200
        assert rb.used_today == 200

    def test_gating_state_blocked_reason_includes_breakdown(self, tmp_path):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import ensure_self_profile, save_self_profile
        from mini_agent.evolution.resource_arbiter import ResourceArbiter

        paths = AgentPaths(project_root=tmp_path)
        profile = ensure_self_profile(paths)
        profile.resource_budget.daily_token_budget = 100
        profile.resource_budget.used_today = 150
        save_self_profile(paths, profile)

        arbiter = ResourceArbiter(paths, _FakeBaseCfg())
        # record_autonomous_token_usage 分别写三类分项，供 reason 拼接展示
        arbiter.record_autonomous_token_usage(50, usage_type="cron")
        state = arbiter.gating_state()
        assert state["state"] == "blocked"
        assert "cron=" in state["reason"]
        assert "goals=" in state["reason"]
        assert "exploration=" in state["reason"]


# ── P2：连续跳过记账 + 告警 ──────────────────────────────────────────────

class TestP2ConsecutiveSkipTracking:
    def _make_scheduler(self, tmp_path, submit_result):
        from mini_agent.storage.paths import AgentPaths
        paths = AgentPaths(project_root=tmp_path)
        scheduler = CronScheduler(paths, submit_fn=lambda *a, **k: submit_result)
        return scheduler

    def test_skip_count_increments_on_failed_fire_and_resets_on_success(self, tmp_path):
        scheduler = self._make_scheduler(tmp_path, submit_result=False)
        job = scheduler.add_job(name="j1", schedule="interval:1", task_template="hi")
        job.next_run_at = time.time() - 1

        scheduler.tick()
        assert scheduler.get(job.id).consecutive_skip_count == 1

        scheduler.get(job.id).next_run_at = time.time() - 1
        scheduler.tick()
        assert scheduler.get(job.id).consecutive_skip_count == 2

        # 换成能成功触发的 submit_fn，验证清零
        scheduler._submit_fn = lambda *a, **k: True
        scheduler.get(job.id).next_run_at = time.time() - 1
        scheduler.tick()
        assert scheduler.get(job.id).consecutive_skip_count == 0

    def test_alert_fires_once_when_crossing_threshold(self, tmp_path, monkeypatch):
        sent = []

        class _FakeDispatcher:
            def __init__(self, paths):
                pass

            def dispatch(self, message):
                sent.append(message)

        import mini_agent.notification.dispatcher as dispatcher_mod
        monkeypatch.setattr(dispatcher_mod, "NotificationDispatcher", _FakeDispatcher)

        scheduler = self._make_scheduler(tmp_path, submit_result=False)
        job = scheduler.add_job(name="j1", schedule="interval:1", task_template="hi")

        for _ in range(5):
            scheduler.get(job.id).next_run_at = time.time() - 1
            scheduler.tick()

        assert len(sent) == 1
        assert scheduler.get(job.id).consecutive_skip_count == 5

        # 再跳过一次不应重复告警（还没跨越下一个阈值）
        scheduler.get(job.id).next_run_at = time.time() - 1
        scheduler.tick()
        assert len(sent) == 1

    def test_priority_field_default_and_roundtrip_unaffected(self, tmp_path):
        # 回归：consecutive_skip_count 新增字段不影响既有 priority 字段的
        # 序列化/反序列化行为。
        job = CronJob(id="user:job1", name="n", schedule="interval:60", task_template="x", priority=7)
        d = job.to_dict()
        assert d["consecutive_skip_count"] == 0
        restored = CronJob.from_dict(d)
        assert restored.priority == 7
        assert restored.consecutive_skip_count == 0
