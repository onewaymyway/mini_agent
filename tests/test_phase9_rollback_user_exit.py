"""Phase 9 收尾：`rollback_recommended` 的用户可见出口。

见 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md` Sprint 9-4 局限 §2。

覆盖三个出口：
  1. `LearnReport.render_notice()`（`/goal` 结束时打印）
  2. 看板通知（`run_learn_step(notify=True)`，同一个部署只提醒一次）
  3. `/evolution deploys` 只读盘点

夹具复用 Sprint 9-4 的真实 git 仓库 + 真实 ExperienceStore/DeployRecordStore，
不用替身伪造“建议回退”这个判断。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.core import reset_event_bus
from mini_agent.core.state_manager import reset_state_manager
from mini_agent.evolution.deploy_record_store import DeployRecordStore
from mini_agent.evolution.deployment import DeployRecord
from mini_agent.notification.reports_store import categorize_report, list_pending_reports
from mini_agent.runtime import AgentRuntime, run_learn_step
from mini_agent.runtime.learn import (
    LearnReport,
    ObservedDeployment,
    collect_deploy_overview,
)
from mini_agent.storage.paths import AgentPaths
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec
from tests.test_phase9_sprint9_4_learn_and_persistence import (  # noqa: F401  夹具与辅助
    _add_exp,
    _deploy_with_store,
    _head,
    paths,
    repo,
    ws_root,
)


@pytest.fixture(autouse=True)
def _reset_bus_and_manager():
    reset_event_bus()
    reset_state_manager()
    yield
    reset_event_bus()
    reset_state_manager()


def _persisting(paths, repo, ws_root):
    """部署一个改动，之后同类任务连续失败 → learn 会得出 persists。"""
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    at = result.record.deployed_at
    _add_exp(exp, "failed", created_at=at + 1)
    _add_exp(exp, "stuck", created_at=at + 2)
    return exp, rec_store, proposal, result


def _reports(paths) -> list:
    return list_pending_reports(paths)


# ═══ 一、render_notice ═══════════════════════════════════════════════

def _obs(**kw) -> ObservedDeployment:
    base = dict(
        proposal_id="prop:a", verdict="persists", detail="部署后 3 条里 2 条失败",
        action="rollback_recommended", sample_count=3, failure_count=2,
        applied_commits=["1" * 40, "2" * 40],
    )
    base.update(kw)
    return ObservedDeployment(**base)


def test_notice_is_empty_when_nothing_needs_a_human():
    assert LearnReport().render_notice() == ""
    r = LearnReport(observed=[
        _obs(action="promoted", verdict="improved"),
        _obs(action="none", verdict="inconclusive"),
        _obs(action="rolled_back"),
    ])
    assert r.render_notice() == "", "已自动处理/无结论的部署不应打扰用户"


def test_notice_lists_revert_commands_newest_first():
    text = LearnReport(observed=[_obs()]).render_notice()
    assert "prop:a" in text and "建议回退" in text
    assert "/evolution deploys" in text
    # applied_commits 是旧 → 新，回退必须先新后旧
    assert text.index("/evolution revert 22222222") < text.index("/evolution revert 11111111")
    assert "没有自动回退" in text


def test_notice_without_commit_info_points_to_log():
    text = LearnReport(observed=[_obs(applied_commits=[])]).render_notice()
    assert "/evolution log" in text and "/evolution revert" not in text


def test_notice_reports_rollback_failed_separately():
    text = LearnReport(observed=[_obs(action="rollback_failed", detail="revert 冲突")]).render_notice()
    assert "自动回退失败" in text and "git status" in text and "revert 冲突" in text


def test_old_deploy_record_without_notified_field_loads_as_zero():
    rec = DeployRecord.from_dict({
        "proposal_id": "p", "hypothesis_id": "h", "problem_id": "x",
        "expected_effect": "e", "branch": "b", "merge_commit": "m",
    })
    assert rec.rollback_notified_at == 0.0


# ═══ 二、看板通知 ═════════════════════════════════════════════════════

def test_notify_is_off_by_default_and_writes_nothing(paths, repo, ws_root):
    _persisting(paths, repo, ws_root)

    report = run_learn_step(paths)

    assert [o.action for o in report.observed] == ["rollback_recommended"]
    assert report.notified == []
    assert not paths.notification_reports.exists(), "默认不得写任何通知文件"
    # 出口 1（终端提示）不依赖该开关
    assert "建议回退" in report.render_notice()


def test_notify_sends_one_kanban_report_with_revert_hint(paths, repo, ws_root):
    _exp, rec_store, _proposal, result = _persisting(paths, repo, ws_root)
    head = _head(repo)

    report = run_learn_step(paths, notify=True)

    pid = result.record.proposal_id
    assert report.notified == [pid] and report.errors == []
    (row,) = _reports(paths)
    assert row["source"] == "learn_rollback_recommended"
    assert categorize_report(row) == "关注提醒"
    assert pid in row["detail"] and "/evolution revert" in row["detail"]
    assert row["fields"]["proposal_id"] == pid
    assert row["fields"]["applied_commits"] == result.record.applied_commits
    # 只通知，不动仓库、不改状态
    assert _head(repo) == head
    saved = rec_store.get(pid)
    assert saved.state == "observing" and saved.rollback_notified_at > 0


def test_notify_dedupes_across_runs_but_still_recommends(paths, repo, ws_root):
    _persisting(paths, repo, ws_root)
    run_learn_step(paths, notify=True)

    again = run_learn_step(paths, notify=True)

    assert [o.action for o in again.observed] == ["rollback_recommended"], "建议本身仍在，终端提示照常"
    assert again.notified == []
    assert len(_reports(paths)) == 1, "同一个部署只提醒一次，不能每轮刷屏"


def test_failed_notification_is_not_marked_and_is_retried(paths, repo, ws_root, monkeypatch):
    _exp, rec_store, _p, result = _persisting(paths, repo, ws_root)
    from mini_agent.notification.dispatcher import NotificationDispatcher

    real = NotificationDispatcher.dispatch
    monkeypatch.setattr(NotificationDispatcher, "dispatch", lambda self, m, channels=None: {"kanban": False})

    first = run_learn_step(paths, notify=True)
    assert first.notified == [] and any("未写入成功" in e for e in first.errors)
    assert rec_store.get(result.record.proposal_id).rollback_notified_at == 0.0

    monkeypatch.setattr(NotificationDispatcher, "dispatch", real)
    second = run_learn_step(paths, notify=True)
    assert second.notified == [result.record.proposal_id]
    assert len(_reports(paths)) == 1


def test_notify_exception_never_breaks_learn(paths, repo, ws_root, monkeypatch):
    _persisting(paths, repo, ws_root)
    from mini_agent.notification.dispatcher import NotificationDispatcher

    def boom(self, m, channels=None):
        raise RuntimeError("channel exploded")

    monkeypatch.setattr(NotificationDispatcher, "dispatch", boom)

    report = run_learn_step(paths, notify=True)  # 不得抛

    assert [o.action for o in report.observed] == ["rollback_recommended"]
    assert report.notified == [] and report.errors


def test_rollback_failed_is_notified_once_as_execution_failure(paths, repo, ws_root):
    exp, rec_store, _p, proposal, result = _persisting_conflict(paths, repo, ws_root)

    report = run_learn_step(paths, auto_rollback=True, notify=True)

    (o,) = report.observed
    assert o.action == "rollback_failed"
    (row,) = _reports(paths)
    assert row["source"] == "learn_rollback_failed" and categorize_report(row) == "执行失败"
    assert "git status" in row["detail"]
    # 状态已变成 rollback_failed，不再出现在 observing，所以不会重复通知
    assert run_learn_step(paths, auto_rollback=True, notify=True).observed == []
    assert len(_reports(paths)) == 1


def _persisting_conflict(paths, repo, ws_root):
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    rel = next(iter(proposal.changes))
    repo.apply({rel: "# 后续人工修改\n"}, "manual edit", {"source": "t"}, "T1", auto_validators=True)
    at = result.record.deployed_at
    _add_exp(exp, "failed", created_at=at + 1)
    _add_exp(exp, "stuck", created_at=at + 2)
    return exp, rec_store, _p, proposal, result


def test_improved_and_inconclusive_are_never_notified(paths, repo, ws_root):
    exp, _rs, _p, _proposal, result = _deploy_with_store(paths, repo, ws_root)
    _add_exp(exp, "done", created_at=result.record.deployed_at + 1)  # 样本不足 → inconclusive

    report = run_learn_step(paths, notify=True)

    assert [o.action for o in report.observed] == ["none"]
    assert report.notified == [] and not paths.notification_reports.exists()


# ═══ 三、AgentRuntime 与配置 ═══════════════════════════════════════════

def test_config_default_for_notify_is_off():
    from mini_agent.config.models import AppConfig

    assert AppConfig().goal_mode.runtime_learn_notify_enabled is False


def _stub_judge(monkeypatch):
    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: "GOAL_STATUS: DONE")


@pytest.mark.parametrize("ctor,cfg_flag,expected", [
    (None, False, False),   # 默认：不通知
    (None, True, True),     # 配置开启
    (True, False, True),    # 显式参数优先
    (False, True, False),
])
def test_runtime_passes_notify_flag(monkeypatch, tmp_path, ctor, cfg_flag, expected):
    _stub_judge(monkeypatch)
    seen = {}

    def fake_learn(paths, **kw):
        seen.update(kw)
        return LearnReport()

    monkeypatch.setattr("mini_agent.runtime.runtime.run_learn_step", fake_learn)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_learn_notify_enabled = cfg_flag

    AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=cfg, enable_learn_stage=True,
                 learn_notify=ctor).run_once(_confirmed_spec())

    assert seen["notify"] is expected


# ═══ 四、/goal 结束时的终端提示 ═════════════════════════════════════════

class _FakeResult:
    status, rounds_used, compacts_done, final_report = "done", 1, 0, "ok"

    def __init__(self, learn_report):
        self.learn_report = learn_report


def _run_goal_with(monkeypatch, learn_report):
    import mini_agent.cli.commands.goal_mode_cmd as gm
    import mini_agent.runtime as rt

    warnings: list = []

    class _FakeRuntime:
        last_runner = None

        def __init__(self, *a, **k): ...

        def run_once(self, spec):
            return _FakeResult(learn_report)

    monkeypatch.setattr(rt, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(gm.R, "print_warning", lambda m: warnings.append(m))

    class _Cfg:
        class goal_mode:
            max_rounds = 3

    class _Agent:
        cfg = _Cfg()

    gm._run_goal(_Agent(), _confirmed_spec())
    return warnings


def test_goal_run_prints_notice_when_rollback_recommended(monkeypatch):
    warnings = _run_goal_with(monkeypatch, LearnReport(observed=[_obs()]))
    assert len(warnings) == 1 and "建议回退" in warnings[0] and "/evolution revert" in warnings[0]


def test_goal_run_is_quiet_without_learn_or_without_news(monkeypatch):
    assert _run_goal_with(monkeypatch, None) == []
    assert _run_goal_with(monkeypatch, LearnReport()) == []


def test_goal_run_survives_a_broken_notice(monkeypatch):
    class _Bad(LearnReport):
        def render_notice(self):
            raise RuntimeError("bad")

    assert _run_goal_with(monkeypatch, _Bad()) == []


# ═══ 五、/evolution deploys ═══════════════════════════════════════════

def _fake_agent(root: Path):
    class _Cfg:
        project_root = root

    class _Agent:
        cfg = _Cfg()

    return _Agent()


def _capture(monkeypatch):
    import mini_agent.cli.commands.evolution as ev

    out: list = []
    monkeypatch.setattr(ev.R, "print_info", lambda m: out.append(str(m)))
    monkeypatch.setattr(ev.R, "print_warning", lambda m: out.append(str(m)))
    monkeypatch.setattr(ev.R, "print_error", lambda m: out.append(str(m)))
    monkeypatch.setattr(ev.R.console, "print", lambda *a, **k: out.append(" ".join(str(x) for x in a)))
    return ev, out


def test_deploys_with_no_records_says_so_and_touches_nothing(tmp_path, monkeypatch):
    ev, out = _capture(monkeypatch)
    root = tmp_path / "plain"
    root.mkdir()

    ev.handle_evolution_cmd(["deploys"], _fake_agent(root))

    assert any("还没有已部署" in m for m in out)
    assert not (root / ".git").exists(), "只读命令不得 git init"
    assert not (root / ".agent").exists(), "只读命令不得创建 .agent/"


def test_deploys_shows_live_recommendation_without_running_learn(paths, repo, ws_root, monkeypatch):
    _exp, rec_store, _proposal, result = _persisting(paths, repo, ws_root)
    ev, out = _capture(monkeypatch)
    head = _head(repo)

    ev.handle_evolution_cmd(["deploys"], _fake_agent(repo.root))

    text = "\n".join(out)
    assert result.record.proposal_id in text and "观察中" in text and "建议回退" in text
    commits = result.record.applied_commits
    assert f"/evolution revert {commits[-1][:8]}" in text
    assert _head(repo) == head
    assert rec_store.get(result.record.proposal_id).state == "observing", "只读：不结算、不写记录"
    assert not paths.notification_reports.exists()


def test_deploys_shows_settled_states(tmp_path, monkeypatch):
    ev, out = _capture(monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    p = AgentPaths(project_root=root)
    store = DeployRecordStore(p.workdir_deploy_records)
    store.save(DeployRecord(
        proposal_id="prop:done", hypothesis_id="h", problem_id="x", expected_effect="e",
        branch="b", merge_commit="m", state="promoted", settle_reason="部署后 5 条无失败",
    ))

    ev.handle_evolution_cmd(["deploys"], _fake_agent(root))

    text = "\n".join(out)
    assert "prop:done" in text and "已确认有效" in text and "部署后 5 条无失败" in text
    assert "建议回退" not in text


def test_overview_never_raises(tmp_path, monkeypatch):
    p = AgentPaths(project_root=tmp_path)
    monkeypatch.setattr(DeployRecordStore, "all", lambda self: (_ for _ in ()).throw(OSError("disk")))

    ov = collect_deploy_overview(p)

    assert ov.rows == [] and ov.errors


def test_usage_mentions_deploys(monkeypatch, tmp_path):
    ev, out = _capture(monkeypatch)
    root = tmp_path / "g"
    root.mkdir()
    # 未知子命令会走 Usage；此时会打开 StateRepo（既有行为），所以放在临时目录里
    ev.handle_evolution_cmd(["nope"], _fake_agent(root))
    assert any("deploys" in m for m in out)
