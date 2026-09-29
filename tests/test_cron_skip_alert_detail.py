"""
tests/test_cron_skip_alert_detail.py

覆盖 `cron_skip_alert` 信息补全：

1. 每个"到点但未能触发"的分支都会留下具体原因码（`CronJob.last_skip_reason`）。
2. 告警标题带 job 名，正文/meta 带 job id、类型、绑定 Goal、原因、上次成功
   触发时间、逾期时长；原因不再是猜测文案。
3. 原因码随 CronJob 持久化，旧 cron_jobs.json（无该字段）仍可加载；触发成功后
   原因清空；先到先得（具体原因不被兜底原因覆盖）。
4. `NotificationDispatcher` 的发送记录落盘 body/url/meta（不可序列化的 meta 值
   不会让整条记录丢失），旧记录格式仍可读。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_cron_skip_alert_detail.py -q
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.evolution.cron_job_runner import CronJobRunner
from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
from mini_agent.evolution.cron_skip_reasons import describe_skip_reason, set_skip_reason
from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.storage.paths import AgentPaths


class _CronCfg:
    skip_alert_threshold = 5


class _BaseCfg:
    cron = _CronCfg()


class _FakeRunner:
    """只提供 scheduler 需要的 `_base_cfg` 与 `submit()`。"""

    def __init__(self, submit):
        self._base_cfg = _BaseCfg()
        self._submit = submit

    def submit(self, job):
        return self._submit(job)


def _capture_dispatch(monkeypatch):
    sent = []

    class _FakeDispatcher:
        def __init__(self, paths):
            pass

        def dispatch(self, message):
            sent.append(message)

    import mini_agent.notification.dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "NotificationDispatcher", _FakeDispatcher)
    return sent


def _tick_n(scheduler, job_id, n):
    for _ in range(n):
        scheduler.get(job_id).next_run_at = time.time() - 1
        scheduler.tick()


# ── 1. 原因码 ─────────────────────────────────────────────────────────────

class TestSkipReasonRecorded:
    def test_submit_fn_rejected(self, tmp_path):
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path), submit_fn=lambda m, i, meta: False)
        job = s.add_job(name="j", schedule="interval:1", task_template="hi")
        _tick_n(s, job.id, 1)
        assert s.get(job.id).last_skip_reason == "submit_fn_rejected"

    def test_no_submit_fn(self, tmp_path):
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path))
        job = s.add_job(name="j", schedule="interval:1", task_template="hi")
        _tick_n(s, job.id, 1)
        assert s.get(job.id).last_skip_reason == "no_submit_fn"

    def test_goal_cycle_handler_missing(self, tmp_path):
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path))
        job = s.add_job(name="g", schedule="interval:1", task_template="t", run_mode="goal_cycle", goal_id="g1")
        _tick_n(s, job.id, 1)
        assert s.get(job.id).last_skip_reason == "goal_cycle_handler_missing"

    def test_handler_exception_keeps_detail(self, tmp_path):
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path))
        job = s.add_job(name="g", schedule="interval:1", task_template="t", run_mode="goal_cycle", goal_id="g1")

        def _boom(j):
            raise RuntimeError("kaboom")

        s.set_goal_cycle_handler(_boom)
        _tick_n(s, job.id, 1)
        j = s.get(job.id)
        assert j.last_skip_reason == "goal_cycle_handler_exception"
        assert "kaboom" in j.last_skip_detail

    def test_specific_reason_not_overwritten_by_fallback(self, tmp_path):
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path))
        job = s.add_job(name="g", schedule="interval:1", task_template="t", run_mode="goal_cycle", goal_id="g1")

        def _handler(j):
            set_skip_reason(j, "goal_cycle_goal_paused", "Goal：x")
            return False

        s.set_goal_cycle_handler(_handler)
        _tick_n(s, job.id, 1)
        assert s.get(job.id).last_skip_reason == "goal_cycle_goal_paused"

    def test_reason_cleared_after_success_and_persisted_while_failing(self, tmp_path):
        results = iter([False, True])
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path), submit_fn=lambda m, i, meta: next(results))
        job = s.add_job(name="j", schedule="interval:1", task_template="hi")
        _tick_n(s, job.id, 1)

        # 失败后落盘：重新 load 仍能读到原因
        s2 = CronScheduler(paths=AgentPaths(project_root=tmp_path))
        s2.load()
        assert s2.get(job.id).last_skip_reason == "submit_fn_rejected"

        _tick_n(s, job.id, 1)
        assert s.get(job.id).last_skip_reason == ""
        assert s.get(job.id).consecutive_skip_count == 0

    def test_old_json_without_field_loads(self):
        d = CronJob(id="user:x", name="n", schedule="interval:1", task_template="t").to_dict()
        d.pop("last_skip_reason")
        d.pop("last_skip_detail")
        job = CronJob.from_dict(d)
        assert job.last_skip_reason == "" and job.last_skip_detail == ""

    def test_describe_unknown_code_passthrough(self):
        assert describe_skip_reason("some_new_code") == "some_new_code"
        assert "未记录" in describe_skip_reason("")


class TestRunnerReasons:
    def _job(self):
        return CronJob(id="user:abc", name="业务job", schedule="interval:1", task_template="t")

    def test_arbiter_blocked(self, tmp_path, monkeypatch):
        from mini_agent.evolution.resource_arbiter import ResourceArbiter
        monkeypatch.setattr(
            ResourceArbiter, "gating_state",
            lambda self: {"state": "blocked", "reason": "预算已耗尽"},
        )

        runner = CronJobRunner(_BaseCfg(), AgentPaths(project_root=tmp_path), max_concurrent=1)
        job = self._job()
        assert runner.submit(job) is False
        assert job.last_skip_reason == "arbiter_blocked"
        assert "预算已耗尽" in job.last_skip_detail

    def test_already_running(self, tmp_path, monkeypatch):
        from mini_agent.evolution.resource_arbiter import ResourceArbiter
        monkeypatch.setattr(ResourceArbiter, "gating_state", lambda self: {"state": "full", "reason": ""})
        runner = CronJobRunner(_BaseCfg(), AgentPaths(project_root=tmp_path), max_concurrent=1)
        job = self._job()
        runner._running_job_ids.add(job.id)
        runner._started_at[job.id] = time.time() - 30
        assert runner.submit(job) is False
        assert job.last_skip_reason == "already_running"
        assert "已运行" in job.last_skip_detail


class TestGoalCycleReasons:
    class _Exec:
        def is_running(self, _):
            return False

        def start(self, _):
            return None

    def _setup(self, tmp_path):
        paths = AgentPaths(tmp_path)
        gb = GoalBacklog(paths)
        goal = gb.add_goal(title="周期目标")
        job = CronJob(id="user:g", name="周期job", schedule="interval:1", task_template="t",
                      run_mode="goal_cycle", goal_id=goal.id)
        return paths, gb, goal, job

    def _maintenance(self, paths):
        from mini_agent.perception.global_knowledge import SelfProfile, load_self_profile, save_self_profile
        profile = load_self_profile(paths) or SelfProfile()
        profile.operating_state.autonomy_level = "maintenance"
        save_self_profile(paths, profile)

    def test_passive_autonomy(self, tmp_path):
        paths, gb, goal, job = self._setup(tmp_path)
        assert bridge._fire_goal_cycle(job, gb, self._Exec()) is False
        assert job.last_skip_reason == "goal_cycle_passive_autonomy"

    def test_no_goal_id(self, tmp_path):
        paths, gb, goal, job = self._setup(tmp_path)
        job.goal_id = None
        assert bridge._fire_goal_cycle(job, gb, self._Exec()) is False
        assert job.last_skip_reason == "goal_cycle_no_goal_id"

    def test_goal_missing(self, tmp_path):
        paths, gb, goal, job = self._setup(tmp_path)
        self._maintenance(paths)
        job.goal_id = "nope"
        assert bridge._fire_goal_cycle(job, gb, self._Exec()) is False
        assert job.last_skip_reason == "goal_cycle_goal_missing"

    def test_paused_and_abandoned(self, tmp_path):
        paths, gb, goal, job = self._setup(tmp_path)
        self._maintenance(paths)
        gb.set_status(goal.id, "paused")
        # [goal_cron_paused_semantics_and_status_provenance_plan.md] 默认策略 heal
        # 下 paused 会被自动拉回；这里验证的是 "respect"（旧语义）下的原因码。
        assert bridge._fire_goal_cycle(job, gb, self._Exec(), paused_policy="respect") is False
        assert job.last_skip_reason == "goal_cycle_goal_paused"
        assert "周期目标" in job.last_skip_detail

        job.last_skip_reason = ""
        gb.set_status(goal.id, "abandoned")
        assert bridge._fire_goal_cycle(job, gb, self._Exec()) is False
        assert job.last_skip_reason == "goal_cycle_goal_abandoned"

    def test_objective_start_failed(self, tmp_path):
        paths, gb, goal, job = self._setup(tmp_path)
        self._maintenance(paths)
        assert bridge._fire_goal_cycle(job, gb, self._Exec()) is False
        assert job.last_skip_reason == "goal_cycle_objective_start_failed"


# ── 2. 告警内容 ───────────────────────────────────────────────────────────

class TestAlertContent:
    def test_alert_names_job_and_reason(self, tmp_path, monkeypatch):
        sent = _capture_dispatch(monkeypatch)
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path))
        job = s.add_job(name="每日巡检", schedule="interval:1", task_template="t",
                        run_mode="goal_cycle", goal_id="goal_42")
        s.set_goal_cycle_handler(lambda j: (set_skip_reason(j, "goal_cycle_passive_autonomy") or False))
        _tick_n(s, job.id, 5)

        assert len(sent) == 1
        msg = sent[0]
        assert "每日巡检" in msg.title
        assert msg.source == "cron_skip_alert"
        for needle in (job.id, "goal_cycle", "goal_42", "passive", "从未成功触发", "已逾期", "连续跳过：5 次"):
            assert needle in msg.body, needle
        assert msg.meta["job_name"] == "每日巡检"
        assert msg.meta["skip_reason"] == "goal_cycle_passive_autonomy"
        assert msg.meta["goal_id"] == "goal_42"
        assert msg.meta["run_mode"] == "goal_cycle"
        assert "overdue_seconds" in msg.meta

    def test_alert_shows_last_success_time(self, tmp_path, monkeypatch):
        sent = _capture_dispatch(monkeypatch)
        s = CronScheduler(paths=AgentPaths(project_root=tmp_path), submit_fn=lambda m, i, meta: False)
        job = s.add_job(name="j", schedule="interval:1", task_template="t")
        s.get(job.id).last_run_at = time.time() - 7200
        _tick_n(s, job.id, 5)
        assert "从未成功触发" not in sent[0].body
        assert "2 小时" in sent[0].body
        assert "submit_fn_rejected" in sent[0].body

    def test_body_not_truncated_at_200_chars(self, tmp_path, monkeypatch):
        sent = _capture_dispatch(monkeypatch)

        def _submit(job):
            set_skip_reason(job, "arbiter_blocked", "预算说明" * 60)  # 240 字，超过旧的 200 字截断
            return False

        s = CronScheduler(paths=AgentPaths(project_root=tmp_path), job_runner=_FakeRunner(_submit))
        job = s.add_job(name="j", schedule="interval:1", task_template="t")
        _tick_n(s, job.id, 5)
        body = sent[0].body
        assert len(body) > 200
        # 补充说明完整保留，没有被截断（正文末尾现在还有一行"本次汇报时间"，
        # 所以不再断言以补充说明结尾）
        assert ("预算说明" * 60) in body
        assert body.rstrip().splitlines()[-1].startswith("本次汇报时间：")


# ── 3. 发送记录 ───────────────────────────────────────────────────────────

class TestDispatchLogFields:
    def _read(self, paths):
        return [json.loads(l) for l in paths.notification_dispatch_log.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_log_contains_body_url_meta(self, tmp_path):
        paths = AgentPaths(project_root=tmp_path)
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title="t", body="正文内容", source="s", url="http://x", meta={"job_id": "user:1"},
        ))
        rec = self._read(paths)[-1]
        assert rec["body"] == "正文内容"
        assert rec["url"] == "http://x"
        assert rec["meta"] == {"job_id": "user:1"}
        assert rec["title"] == "t" and "results" in rec

    def test_unserializable_meta_does_not_drop_record(self, tmp_path):
        paths = AgentPaths(project_root=tmp_path)
        NotificationDispatcher(paths).dispatch(NotificationMessage(
            title="t", body="b", source="s", meta={"p": Path("/tmp/x"), "s": {1, 2}},
        ))
        rec = self._read(paths)[-1]
        assert rec["title"] == "t"
        assert "/tmp/x" in rec["meta"]["p"]

    def test_body_capped(self, tmp_path):
        paths = AgentPaths(project_root=tmp_path)
        NotificationDispatcher(paths).dispatch(NotificationMessage(title="t", body="x" * 5000, source="s"))
        assert len(self._read(paths)[-1]["body"]) == NotificationDispatcher._MAX_LOG_BODY_CHARS
