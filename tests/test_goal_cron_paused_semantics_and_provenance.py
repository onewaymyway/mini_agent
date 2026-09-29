"""
tests/test_goal_cron_paused_semantics_and_provenance.py

覆盖 next_doc/goal_cron_paused_semantics_and_status_provenance_plan.md：

1. 状态变更来源追溯：`status_history` 记录 by/reason/from；未声明来源时自动记
   caller；`update_fields(status=…)` 与 `try_advance_goal` 也留历史；旧格式记录可读。
2. 周期性 Goal 的 paused 语义：默认 heal（拒绝写 paused、遇到 paused 自动拉回并
   触发、progress_notes 留下写入记录）；respect 保持旧语义但静默不计数。
3. 跳过原因分类：intentional 不计数不告警；user_skip 真正跳过一个周期；invalid 自动
   停用 job 并只通知一次；retry_backoff 指数退避且不超过调度周期；retry 保持旧行为。
4. CronJob 启停历史（state_history）带来源，旧 cron_jobs.json 可加载。
5. 告警退避（默认关闭，opt-in）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_goal_cron_paused_semantics_and_provenance.py -q
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from mini_agent.evolution import goal_cron_bridge as bridge
from mini_agent.evolution.cron_scheduler import CronJob, CronScheduler
from mini_agent.evolution.cron_skip_reasons import (
    ADVANCE_ON_INTENTIONAL,
    CATEGORY_INTENTIONAL,
    CATEGORY_INVALID,
    CATEGORY_RETRY,
    CATEGORY_RETRY_BACKOFF,
    SKIP_REASONS,
    skip_category,
)
from mini_agent.perception.goal_backlog import (
    GoalBacklog,
    normalize_paused_policy,
    validate_status_write_for_recurring_goal,
)
from mini_agent.perception import status_provenance as sp
from mini_agent.storage.paths import AgentPaths


# ── 公共夹具 ────────────────────────────────────────────────────────────────

class _Exec:
    """duck-typed ObjectiveExecutor：start_ok=False 时模拟启动失败。"""

    def __init__(self, start_ok: bool = True):
        self.start_ok = start_ok
        self.started: list[str] = []

    def is_running(self, _):
        return False

    def start(self, objective):
        self.started.append(objective.id)
        return "exec_1" if self.start_ok else None


def _maintenance(paths):
    from mini_agent.perception.global_knowledge import SelfProfile, load_self_profile, save_self_profile
    profile = load_self_profile(paths) or SelfProfile()
    profile.operating_state.autonomy_level = "maintenance"
    save_self_profile(paths, profile)


def _capture_dispatch(monkeypatch):
    """只收集与"用户 job"（id 以 user: 开头）相关的通知：CronScheduler.load()
    会注入 sys:* 内置 job，它们在测试里同样会因无 submit_fn 而告警，与被测逻辑无关。"""
    sent = []

    class _FakeDispatcher:
        def __init__(self, paths):
            pass

        def dispatch(self, message):
            if str((message.meta or {}).get("job_id", "")).startswith("sys:"):
                return
            sent.append(message)

    import mini_agent.notification.dispatcher as dispatcher_mod
    monkeypatch.setattr(dispatcher_mod, "NotificationDispatcher", _FakeDispatcher)
    return sent


def _cfg_runner(**cron_fields):
    """只提供 scheduler 读取配置所需的 `_base_cfg.cron`。"""
    cron = SimpleNamespace(skip_alert_threshold=5, **cron_fields)
    return SimpleNamespace(_base_cfg=SimpleNamespace(cron=cron), submit=lambda job: False)


def _setup(tmp_path, *, schedule="interval:43200", policy="heal", executor=None,
           cron_fields=None):
    """建一个周期性 Goal + 绑定 job + 已注册 handler 的 scheduler。"""
    paths = AgentPaths(tmp_path)
    _maintenance(paths)
    gb = GoalBacklog(paths)
    goal = gb.add_goal(title="持续关注 AI 技术")
    cs = CronScheduler(paths, submit_fn=None, job_runner=_cfg_runner(**(cron_fields or {})))
    cs.load()
    job = bridge.make_goal_recurring(gb, cs, goal.id, schedule, actor="user:cli")
    ex = executor or _Exec()
    bridge.register_goal_cycle_handler(cs, gb, ex, paused_policy_provider=lambda: policy)
    return paths, gb, goal, cs, job, ex


def _tick(cs, job_id, n=1):
    for _ in range(n):
        # 只在"还没到期"时才拨到期，保留 tick 自己推进的 next_run_at 以便观察
        job = cs.get(job_id)
        if job.next_run_at > time.time():
            break
        cs.tick()


def _make_due(cs, job_id):
    cs.get(job_id).next_run_at = time.time() - 1


# ── 1. 状态变更来源 ─────────────────────────────────────────────────────────

class TestStatusProvenance:
    def test_set_status_records_actor_reason_and_previous(self, tmp_path):
        gb = GoalBacklog(AgentPaths(tmp_path))
        g = gb.add_goal(title="G")
        gb.set_status(g.id, "paused", actor=sp.ACTOR_USER_CLI, reason="手动暂停")
        entry = gb.get(g.id).status_history[-1]
        assert entry["status"] == "paused"
        assert entry["from"] == "active"
        assert entry["by"] == "user:cli"
        assert entry["reason"] == "手动暂停"
        assert "caller" not in entry  # 声明了来源就不附带调用点

    def test_undeclared_actor_is_unknown_with_caller(self, tmp_path):
        gb = GoalBacklog(AgentPaths(tmp_path))
        g = gb.add_goal(title="G")
        gb.set_status(g.id, "paused")
        entry = gb.get(g.id).status_history[-1]
        assert entry["by"] == "unknown"
        # 调用点应指向本测试函数，而不是 GoalBacklog 内部
        assert "test_undeclared_actor_is_unknown_with_caller" in entry["caller"]

    def test_repeated_same_status_adds_no_entry(self, tmp_path):
        gb = GoalBacklog(AgentPaths(tmp_path))
        g = gb.add_goal(title="G")
        gb.set_status(g.id, "paused", actor="user:cli")
        gb.set_status(g.id, "paused", actor="user:cli")
        assert len([e for e in gb.get(g.id).status_history if e["status"] == "paused"]) == 1

    def test_update_fields_status_now_leaves_history(self, tmp_path):
        gb = GoalBacklog(AgentPaths(tmp_path))
        g = gb.add_goal(title="G")
        gb.update_fields(g.id, status="paused", status_actor="user:api", status_reason="PATCH")
        entry = gb.get(g.id).status_history[-1]
        assert (entry["status"], entry["by"], entry["from"]) == ("paused", "user:api", "active")
        # 不含 status 的 update_fields 不产生历史
        n = len(gb.get(g.id).status_history)
        gb.update_fields(g.id, priority=7)
        assert len(gb.get(g.id).status_history) == n
        # status_actor/status_reason 不会被当成节点字段写进去
        assert not hasattr(gb.get(g.id), "status_actor")

    def test_try_advance_goal_records_system_actor(self, tmp_path):
        gb = GoalBacklog(AgentPaths(tmp_path))
        g = gb.add_goal(title="G")
        gb.set_status(g.id, "paused", actor="user:cli")
        decision = gb.try_advance_goal(g.id, cooldown_seconds=0)
        assert decision.action == "reactivated"
        entry = gb.get(g.id).status_history[-1]
        assert entry["status"] == "active"
        assert entry["by"] == sp.ACTOR_SYSTEM_GOAL_RELEVANCE

    def test_history_persists_through_reload(self, tmp_path):
        paths = AgentPaths(tmp_path)
        gb = GoalBacklog(paths)
        g = gb.add_goal(title="G")
        gb.set_status(g.id, "paused", actor="user:cli")
        gb2 = GoalBacklog(paths)
        gb2.load()
        assert gb2.get(g.id).status_history[-1]["by"] == "user:cli"

    def test_history_is_capped(self):
        history = []
        for i in range(sp.HISTORY_MAX_ENTRIES + 25):
            history = sp.append_history(history, {"status": f"s{i}", "at": float(i)})
        assert len(history) == sp.HISTORY_MAX_ENTRIES
        assert history[-1]["status"] == f"s{sp.HISTORY_MAX_ENTRIES + 24}"

    def test_legacy_entry_without_by_is_formattable(self):
        legacy = {"status": "paused", "at": 1_700_000_000.0}
        text = sp.format_history_entry(legacy)
        assert "paused" in text and "未记录来源" in text

    def test_actor_helpers(self):
        assert sp.actor_kind("user:cli") == "user"
        assert sp.actor_kind("cron:goal_cycle") == "cron"
        assert sp.actor_kind("weird:thing") == "unknown"
        assert sp.actor_kind(None) == "unknown"
        assert "用户操作" in sp.describe_actor("user:api")
        found = sp.last_entry_with(
            [{"status": "paused", "by": "a"}, {"status": "active"}, {"status": "paused", "by": "b"}],
            status="paused",
        )
        assert found["by"] == "b"


# ── 2. paused 语义 ──────────────────────────────────────────────────────────

class TestPausedPolicyValidation:
    def _goal(self, tmp_path, recurring=True):
        gb = GoalBacklog(AgentPaths(tmp_path))
        g = gb.add_goal(title="G")
        if recurring:
            gb.set_recurrence(g.id, recurring=True, cron_job_id="user:x")
        return gb.get(g.id)

    def test_heal_rejects_paused_for_recurring(self, tmp_path):
        node = self._goal(tmp_path)
        reason = validate_status_write_for_recurring_goal(node, "paused")
        assert reason and "不支持暂停" in reason
        # 拒绝信息要给出替代手段
        assert "skip" in reason and "unrecur" in reason

    def test_respect_allows_paused(self, tmp_path):
        node = self._goal(tmp_path)
        assert validate_status_write_for_recurring_goal(node, "paused", paused_policy="respect") is None

    def test_non_recurring_goal_unaffected(self, tmp_path):
        node = self._goal(tmp_path, recurring=False)
        assert validate_status_write_for_recurring_goal(node, "paused") is None

    def test_other_statuses_unchanged(self, tmp_path):
        node = self._goal(tmp_path)
        assert validate_status_write_for_recurring_goal(node, "active") is None
        assert validate_status_write_for_recurring_goal(node, "abandoned") is None
        assert validate_status_write_for_recurring_goal(node, "completed")

    def test_invalid_policy_falls_back_to_heal(self):
        assert normalize_paused_policy("nonsense") == "heal"
        assert normalize_paused_policy(None) == "heal"
        assert normalize_paused_policy("RESPECT") == "respect"


class TestPausedFireBehavior:
    def test_heal_recovers_paused_goal_and_fires(self, tmp_path):
        paths, gb, goal, cs, job, ex = _setup(tmp_path, policy="heal")
        gb.set_status(goal.id, "paused", actor="user:api", reason="PATCH /v1/goals")

        fired = bridge._fire_goal_cycle(cs.get(job.id), gb, ex, paused_policy="heal")

        assert fired is True
        refreshed = gb.get(goal.id)
        assert refreshed.status == "active"
        assert len(ex.started) == 1
        # 自愈动作本身带来源
        last_active = sp.last_entry_with(refreshed.status_history, status="active")
        assert last_active["by"] == sp.ACTOR_CRON_GOAL_CYCLE
        # progress_notes 留下"谁写的 paused"
        assert "paused" in refreshed.progress_notes
        assert "user:api" in refreshed.progress_notes

    def test_default_policy_is_heal(self, tmp_path):
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        gb.set_status(goal.id, "paused", actor="user:cli")
        assert bridge._fire_goal_cycle(cs.get(job.id), gb, ex) is True

    def test_respect_keeps_paused_and_does_not_fire(self, tmp_path):
        paths, gb, goal, cs, job, ex = _setup(tmp_path, policy="respect")
        gb.set_status(goal.id, "paused", actor="user:cli")
        fired = bridge._fire_goal_cycle(cs.get(job.id), gb, ex, paused_policy="respect")
        assert fired is False
        assert gb.get(goal.id).status == "paused"
        assert cs.get(job.id).last_skip_reason == "goal_cycle_goal_paused"
        assert ex.started == []


# ── 3. 跳过原因分类 ─────────────────────────────────────────────────────────

class TestSkipCategories:
    def test_category_mapping(self):
        assert skip_category("goal_cycle_goal_paused") == CATEGORY_INTENTIONAL
        assert skip_category("goal_cycle_goal_abandoned") == CATEGORY_INTENTIONAL
        assert skip_category("goal_cycle_user_skip") == CATEGORY_INTENTIONAL
        assert skip_category("goal_cycle_goal_missing") == CATEGORY_INVALID
        assert skip_category("goal_cycle_no_goal_id") == CATEGORY_INVALID
        assert skip_category("goal_cycle_objective_start_failed") == CATEGORY_RETRY_BACKOFF
        # 暂时性与未知码保持旧行为
        assert skip_category("already_running") == CATEGORY_RETRY
        assert skip_category("arbiter_blocked") == CATEGORY_RETRY
        assert skip_category("goal_cycle_prev_cycle_running") == CATEGORY_RETRY
        assert skip_category("some_new_code") == CATEGORY_RETRY
        assert skip_category("") == CATEGORY_RETRY

    def test_every_categorized_code_is_a_known_reason(self):
        from mini_agent.evolution.cron_skip_reasons import SKIP_CATEGORIES
        assert set(SKIP_CATEGORIES) <= set(SKIP_REASONS)
        assert ADVANCE_ON_INTENTIONAL <= set(SKIP_CATEGORIES)


class TestIntentionalSkips:
    def test_paused_respect_is_silent_and_uncounted(self, tmp_path, monkeypatch):
        sent = _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path, policy="respect")
        gb.set_status(goal.id, "paused", actor="user:cli")

        for _ in range(12):  # 超过告警阈值 5 的两倍
            _make_due(cs, job.id)
            cs.tick()

        j = cs.get(job.id)
        assert j.consecutive_skip_count == 0
        assert sent == []                      # 不告警
        assert j.last_skip_reason == "goal_cycle_goal_paused"  # 原因仍可见
        assert j.next_run_at <= time.time()    # 方案 A：不推进，恢复后立即补跑一轮

    def test_legacy_skip_count_is_reset_on_next_tick(self, tmp_path, monkeypatch):
        """存量数据：已累计上万次跳过的 job，升级后第一次 tick 就归零，无需迁移。"""
        _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path, policy="respect")
        gb.set_status(goal.id, "paused", actor="user:cli")
        cs.get(job.id).consecutive_skip_count = 11195
        _make_due(cs, job.id)
        cs.tick()
        assert cs.get(job.id).consecutive_skip_count == 0

    def test_resume_after_pause_fires_immediately_once(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path, policy="respect")
        gb.set_status(goal.id, "paused", actor="user:cli")
        _make_due(cs, job.id)
        cs.tick()
        gb.set_status(goal.id, "active", actor="user:cli")
        cs.tick()  # next_run_at 没被推进，恢复后这一次就该触发
        assert len(ex.started) == 1
        # 触发成功后推进了 next_run_at，不会连发
        cs.tick()
        assert len(ex.started) == 1

    def test_user_skip_advances_next_run_and_skips_whole_period(self, tmp_path, monkeypatch):
        """回归：此前 skip_next_cycle 只跳过约 1 分钟（False 不推进 next_run_at）。"""
        sent = _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        gb.update_fields(goal.id, skip_next_cycle=True)

        _make_due(cs, job.id)
        before = time.time()
        cs.tick()
        assert ex.started == []
        j = cs.get(job.id)
        assert j.next_run_at >= before + 43200 - 5     # 推进了一个完整周期
        assert j.consecutive_skip_count == 0
        assert sent == []
        assert gb.get(goal.id).skip_next_cycle is False  # 标记被消耗

        cs.tick()                                        # 下一分钟：没到期，不触发
        assert ex.started == []

    def test_abandoned_is_silent(self, tmp_path, monkeypatch):
        sent = _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        gb.set_status(goal.id, "abandoned", actor="user:cli")
        for _ in range(8):
            _make_due(cs, job.id)
            cs.tick()
        assert cs.get(job.id).consecutive_skip_count == 0
        assert sent == []
        assert ex.started == []


class TestInvalidSkips:
    def test_goal_missing_auto_disables_and_notifies_once(self, tmp_path, monkeypatch):
        sent = _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        gb.delete_goal(goal.id)

        _make_due(cs, job.id)
        cs.tick()
        j = cs.get(job.id)
        assert j.enabled is False
        assert j.consecutive_skip_count == 0
        assert [m.source for m in sent] == ["cron_job_auto_disabled"]
        assert sent[0].meta["skip_reason"] == "goal_cycle_goal_missing"

        # 停用后不再参与调度：既不重试也不再通知
        _make_due(cs, job.id)
        cs.tick()
        assert len(sent) == 1

    def test_auto_disable_is_attributed_in_state_history(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        gb.delete_goal(goal.id)
        _make_due(cs, job.id)
        cs.tick()
        entry = cs.get(job.id).state_history[-1]
        assert entry["enabled"] is False
        assert entry["from"] is True
        assert entry["by"] == sp.ACTOR_CRON_SCHEDULER
        assert "goal_cycle_goal_missing" in entry["reason"]

    def test_auto_disable_is_persisted(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        gb.delete_goal(goal.id)
        _make_due(cs, job.id)
        cs.tick()
        cs2 = CronScheduler(paths)
        cs2.load()
        assert cs2.get(job.id).enabled is False


class TestStartFailureBackoff:
    def test_backoff_grows_and_stops_per_minute_child_spam(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)
        ex = _Exec(start_ok=False)
        paths, gb, goal, cs, job, _ = _setup(tmp_path, executor=ex)

        delays = []
        for _ in range(4):
            _make_due(cs, job.id)
            t0 = time.time()
            cs.tick()
            delays.append(round(cs.get(job.id).next_run_at - t0))
        assert delays[0] in (60, 61)
        assert delays[1] in (120, 121)
        assert delays[2] in (240, 241)
        assert delays[3] in (480, 481)

        # 退避窗口内的 tick 不再触发，也就不再新建失败子 Objective
        started_before = len(ex.started)
        for _ in range(5):
            cs.tick()
        assert len(ex.started) == started_before
        failed = [c for c in gb.get(goal.id).children_ids if gb.get(c).status == "failed"]
        assert len(failed) == 4

    def test_backoff_never_exceeds_schedule_period(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)
        ex = _Exec(start_ok=False)
        paths, gb, goal, cs, job, _ = _setup(tmp_path, schedule="interval:300", executor=ex)
        cs.get(job.id).consecutive_skip_count = 30  # 指数早已远超周期
        _make_due(cs, job.id)
        t0 = time.time()
        cs.tick()
        assert cs.get(job.id).next_run_at - t0 <= 300 + 2

    def test_backoff_can_be_disabled(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)
        ex = _Exec(start_ok=False)
        paths, gb, goal, cs, job, _ = _setup(
            tmp_path, executor=ex, cron_fields={"start_failure_backoff_enabled": False},
        )
        _make_due(cs, job.id)
        cs.tick()
        assert cs.get(job.id).next_run_at <= time.time()  # 旧行为：不推进，下一分钟重试
        assert cs.get(job.id).consecutive_skip_count == 1

    def test_start_failure_still_counts_and_alerts(self, tmp_path, monkeypatch):
        sent = _capture_dispatch(monkeypatch)
        ex = _Exec(start_ok=False)
        paths, gb, goal, cs, job, _ = _setup(tmp_path, executor=ex)
        for _ in range(5):
            _make_due(cs, job.id)
            cs.tick()
        assert cs.get(job.id).consecutive_skip_count == 5
        assert [m.source for m in sent] == ["cron_skip_alert"]

    def test_success_after_failures_resets_count(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)
        ex = _Exec(start_ok=False)
        paths, gb, goal, cs, job, _ = _setup(tmp_path, executor=ex)
        for _ in range(2):
            _make_due(cs, job.id)
            cs.tick()
        ex.start_ok = True
        _make_due(cs, job.id)
        cs.tick()
        assert cs.get(job.id).consecutive_skip_count == 0
        assert cs.get(job.id).last_run_at > 0


class TestRetryKeepsLegacyBehavior:
    def test_prev_cycle_running_counts_and_does_not_advance(self, tmp_path, monkeypatch):
        _capture_dispatch(monkeypatch)

        class _Running(_Exec):
            def is_running(self, _):
                return True

        paths, gb, goal, cs, job, ex = _setup(tmp_path, executor=_Running())
        # 造一个"仍在跑"的子节点
        child = gb.add_objective(title="c", parent_id=goal.id, source="cron")
        _make_due(cs, job.id)
        cs.tick()
        j = cs.get(job.id)
        assert j.last_skip_reason == "goal_cycle_prev_cycle_running"
        assert j.consecutive_skip_count == 1
        assert j.next_run_at <= time.time()
        assert child  # 仅为使用变量


# ── 4. 告警退避（opt-in）───────────────────────────────────────────────────

class TestAlertBackoff:
    def _alerts_for(self, tmp_path, monkeypatch, n, **cron_fields):
        sent = _capture_dispatch(monkeypatch)
        cs = CronScheduler(
            AgentPaths(tmp_path), submit_fn=lambda m, i, meta: False,
            job_runner=_cfg_runner(**cron_fields),
        )
        job = cs.add_job(name="j", schedule="interval:1", task_template="hi")
        for _ in range(n):
            cs.get(job.id).next_run_at = time.time() - 1
            cs.tick()
        return [m.meta["consecutive_skip_count"] for m in sent]

    def test_default_keeps_every_threshold_multiple(self, tmp_path, monkeypatch):
        assert self._alerts_for(tmp_path, monkeypatch, 20) == [5, 10, 15, 20]

    def test_backoff_enabled_uses_powers_of_two(self, tmp_path, monkeypatch):
        got = self._alerts_for(tmp_path, monkeypatch, 40, skip_alert_backoff_enabled=True)
        assert got == [5, 10, 20, 40]


# ── 5. CronJob 启停历史 ─────────────────────────────────────────────────────

class TestCronJobStateHistory:
    def test_enable_disable_record_actor(self, tmp_path):
        cs = CronScheduler(AgentPaths(tmp_path))
        job = cs.add_job(name="j", schedule="interval:60", task_template="t")
        cs.disable(job.id, actor="user:api", reason="PUT /v1/cron/jobs")
        cs.enable(job.id, actor="user:cli")
        hist = cs.get(job.id).state_history
        assert [(h["enabled"], h["by"]) for h in hist] == [(False, "user:api"), (True, "user:cli")]
        assert hist[0]["reason"] == "PUT /v1/cron/jobs"

    def test_no_entry_when_state_unchanged(self, tmp_path):
        cs = CronScheduler(AgentPaths(tmp_path))
        job = cs.add_job(name="j", schedule="interval:60", task_template="t")
        cs.enable(job.id, actor="user:cli")  # 本来就是 enabled
        assert cs.get(job.id).state_history == []

    def test_undeclared_disable_records_caller(self, tmp_path):
        cs = CronScheduler(AgentPaths(tmp_path))
        job = cs.add_job(name="j", schedule="interval:60", task_template="t")
        cs.disable(job.id)
        entry = cs.get(job.id).state_history[-1]
        assert entry["by"] == "unknown"
        assert "test_undeclared_disable_records_caller" in entry["caller"]

    def test_persisted_and_old_json_loads(self, tmp_path):
        paths = AgentPaths(tmp_path)
        cs = CronScheduler(paths)
        job = cs.add_job(name="j", schedule="interval:60", task_template="t")
        cs.disable(job.id, actor="user:cli")
        cs2 = CronScheduler(paths)
        cs2.load()
        assert cs2.get(job.id).state_history[-1]["by"] == "user:cli"

        d = CronJob(id="user:x", name="n", schedule="interval:1", task_template="t").to_dict()
        d.pop("state_history")
        assert CronJob.from_dict(d).state_history == []

    def test_stop_goal_recurrence_attributes_disable(self, tmp_path):
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        assert bridge.stop_goal_recurrence(gb, cs, goal.id, actor="user:api")
        entry = cs.get(job.id).state_history[-1]
        assert (entry["enabled"], entry["by"]) == (False, "user:api")

    def test_stop_goal_recurrence_default_actor(self, tmp_path):
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        bridge.stop_goal_recurrence(gb, cs, goal.id)
        assert cs.get(job.id).state_history[-1]["by"] == sp.ACTOR_SYSTEM_GOAL_RECURRENCE


# ── 6. CLI：history / skip / 拒绝暂停 ───────────────────────────────────────

class TestCli:
    def _capture(self, monkeypatch):
        import mini_agent.cli.commands.goals as cli
        out = {"info": [], "error": [], "success": [], "warning": []}
        for level in out:
            monkeypatch.setattr(cli.R, f"print_{level}", lambda m, _l=level: out[_l].append(m))
        return cli, out

    def test_pause_rejected_for_recurring_goal_by_default(self, tmp_path, monkeypatch):
        cli, out = self._capture(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        cli._cmd_set_status(gb, goal.id, "paused")
        assert out["error"] and "不支持暂停" in out["error"][0]
        assert gb.get(goal.id).status == "active"

    def test_pause_allowed_under_respect_and_attributed(self, tmp_path, monkeypatch):
        cli, out = self._capture(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        agent = SimpleNamespace(cfg=SimpleNamespace(
            cron=SimpleNamespace(recurring_goal_paused_policy="respect")))
        cli._cmd_set_status(gb, goal.id, "paused", agent=agent)
        assert gb.get(goal.id).status == "paused"
        assert gb.get(goal.id).status_history[-1]["by"] == "user:cli"

    def test_history_command_lists_provenance(self, tmp_path, monkeypatch):
        cli, out = self._capture(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        gb.set_status(goal.id, "paused", actor="user:api", reason="PATCH")
        cs.disable(job.id, actor="user:cli")
        cli._cmd_history(gb, paths, goal.id)
        text = "\n".join(out["info"])
        assert "user:api" in text and "paused" in text
        assert "绑定 cron job" in text and "user:cli" in text

    def test_skip_command_sets_flag_only_for_recurring(self, tmp_path, monkeypatch):
        cli, out = self._capture(monkeypatch)
        paths, gb, goal, cs, job, ex = _setup(tmp_path)
        cli._cmd_skip(gb, goal.id)
        assert gb.get(goal.id).skip_next_cycle is True
        plain = gb.add_goal(title="非周期")
        cli._cmd_skip(gb, plain.id)
        assert out["error"] and "不是周期性" in out["error"][-1]
        assert gb.get(plain.id).skip_next_cycle is False

    def test_history_of_unknown_node(self, tmp_path, monkeypatch):
        cli, out = self._capture(monkeypatch)
        gb = GoalBacklog(AgentPaths(tmp_path))
        cli._cmd_history(gb, None, "nope")
        assert out["error"]
