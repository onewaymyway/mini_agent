"""tests/test_phase9_sprint9_4_learn_and_persistence.py

对应 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-4（Phase 9 收尾）：

  1. `DeployRecord` 持久化（`DeployRecordStore`）——使 Observe 可以跨进程发生；
  2. `AgentRuntime` 的 `learn` 步骤（`runtime/learn.py`）——默认关闭、只 Observe
     与汇总、回退需额外 opt-in、永不影响 Goal 结果。

与 9-3 一样坚持“真实性”：git 仓库、worktree 沙盒、SQLite 的 ExperienceStore 都是
真实组件；回退断言的是磁盘与 git 历史，而不是状态字段。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mini_agent.core import get_event_bus, reset_event_bus
from mini_agent.core.experience import Experience
from mini_agent.core.experience_patterns import Problem, detect_problems_from_experience
from mini_agent.core.experience_store import ExperienceStore
from mini_agent.core.state_manager import reset_state_manager
from mini_agent.evolution.deploy_record_store import DeployRecordStore
from mini_agent.evolution.deployment import (
    DeployRecord,
    deploy_proposal,
    make_category_observer,
    make_experience_observer,
)
from mini_agent.evolution.proposals import propose_and_evaluate
from mini_agent.evolution.state_repo import StateRepo
from mini_agent.runtime import AgentRuntime, run_learn_step
from mini_agent.runtime import learn as learn_mod
from mini_agent.storage.paths import AgentPaths
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec

GOAL = "部署新版本到生产环境"


# ── 夹具与辅助 ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_bus_and_manager():
    reset_event_bus()
    reset_state_manager()
    yield
    reset_event_bus()
    reset_state_manager()


@pytest.fixture
def repo(tmp_path: Path) -> StateRepo:
    root = tmp_path / "proj"
    root.mkdir()
    r = StateRepo(root)
    r.ensure_initial_commit()
    return r


@pytest.fixture
def paths(repo: StateRepo) -> AgentPaths:
    return AgentPaths(project_root=repo.root)


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _git(repo: StateRepo, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo.root, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _head(repo: StateRepo) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _add_exp(store: ExperienceStore, status: str, created_at: float | None = None, goal: str = GOAL):
    kwargs = {} if created_at is None else {"created_at": created_at}
    store.append(Experience(
        source="goal_mode", goal_text=goal, status=status, rounds_used=2,
        final_report="", lesson="部署前先检查环境变量" if status != "done" else "", **kwargs,
    ))


def _deploy_with_store(paths: AgentPaths, repo: StateRepo, ws_root: Path):
    """在 `paths` 指向的真实 ExperienceStore 上灌入 2 条失败 → 识别 Problem →
    提案 → 人审通过 → 合并，并把 DeployRecord 落盘。"""
    exp_store = ExperienceStore(path=paths.workdir_experience_store)
    _add_exp(exp_store, "stuck")
    _add_exp(exp_store, "failed")
    (problem,) = detect_problems_from_experience(store=exp_store, min_occurrence=2)
    proposal, evaluation = propose_and_evaluate(problem, repo)
    assert evaluation.accepted
    rec_store = DeployRecordStore(paths.workdir_deploy_records)
    result = deploy_proposal(
        proposal, repo, approve=lambda p, r: True,
        workspace_root=ws_root, record_store=rec_store,
    )
    assert result.deployed, result.errors
    return exp_store, rec_store, problem, proposal, result


def _record(**kw) -> DeployRecord:
    base = dict(
        proposal_id="prop:x", hypothesis_id="hyp:x", problem_id="experience:x",
        expected_effect="e", branch="evolve/x", merge_commit="m" * 40,
        applied_commits=["a" * 40], problem_category="部署新版本到生产环境",
    )
    base.update(kw)
    return DeployRecord(**base)


# ═══ 一、DeployRecordStore ═══════════════════════════════════════════

def test_store_roundtrip_keeps_all_fields(tmp_path: Path):
    store = DeployRecordStore(tmp_path / ".agent" / "deploy_records.jsonl")  # 父目录不存在
    rec = _record(revert_commits=["r1"], settle_reason="why")
    store.save(rec)

    got = store.get("prop:x")
    assert got is not None
    assert got.to_dict() == rec.to_dict()
    assert got.problem_category == "部署新版本到生产环境"


def test_store_latest_snapshot_wins_but_history_is_kept(tmp_path: Path):
    path = tmp_path / "d.jsonl"
    store = DeployRecordStore(path)
    rec = _record()
    store.save(rec)
    rec.state = "promoted"
    rec.settle_reason = "ok"
    store.save(rec)

    assert store.get("prop:x").state == "promoted"
    assert len(store.all()) == 1
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "状态迁移是审计信息，必须追加而不是覆盖"
    assert json.loads(lines[0])["state"] == "observing"


def test_store_survives_across_instances(tmp_path: Path):
    """模拟“部署在一个进程、Observe 在另一个进程”。"""
    path = tmp_path / "d.jsonl"
    DeployRecordStore(path).save(_record())
    fresh = DeployRecordStore(path)
    assert [r.proposal_id for r in fresh.list_observing()] == ["prop:x"]


def test_store_skips_corrupt_and_truncated_lines(tmp_path: Path):
    path = tmp_path / "d.jsonl"
    store = DeployRecordStore(path)
    store.save(_record(proposal_id="prop:a"))
    with path.open("a", encoding="utf-8") as f:
        f.write("not json at all\n")
        f.write('{"no_proposal_id": 1}\n')
        f.write('["a list"]\n')
        f.write('{"proposal_id": "prop:trunc", "state": "obs')  # 崩溃时被截断的末行
    store.save(_record(proposal_id="prop:b"))

    assert sorted(r.proposal_id for r in store.all()) == ["prop:a", "prop:b"]


def test_store_missing_file_is_empty(tmp_path: Path):
    store = DeployRecordStore(tmp_path / "nope.jsonl")
    assert store.all() == [] and store.list_observing() == [] and store.get("x") is None


def test_store_list_observing_excludes_settled(tmp_path: Path):
    store = DeployRecordStore(tmp_path / "d.jsonl")
    store.save(_record(proposal_id="prop:a"))
    store.save(_record(proposal_id="prop:b", state="promoted"))
    store.save(_record(proposal_id="prop:c", state="rolled_back"))
    store.save(_record(proposal_id="prop:d", state="rollback_failed"))
    assert [r.proposal_id for r in store.list_observing()] == ["prop:a"]


def test_old_records_without_category_load_with_empty_default(tmp_path: Path):
    path = tmp_path / "d.jsonl"
    d = _record().to_dict()
    d.pop("problem_category")
    path.write_text(json.dumps(d) + "\n", encoding="utf-8")
    assert DeployRecordStore(path).get("prop:x").problem_category == ""


# ═══ 二、deploy_proposal 接入持久化 ═══════════════════════════════════

def test_deploy_persists_record_with_category(paths, repo, ws_root):
    _exp, rec_store, problem, _proposal, result = _deploy_with_store(paths, repo, ws_root)

    saved = rec_store.get(result.record.proposal_id)
    assert saved is not None
    assert saved.problem_category == problem.category != ""
    assert saved.merge_commit == _head(repo)
    assert saved.state == "observing"
    assert paths.workdir_deploy_records.is_file()


def test_pending_approval_persists_nothing(paths, repo, ws_root):
    exp_store = ExperienceStore(path=paths.workdir_experience_store)
    _add_exp(exp_store, "stuck"); _add_exp(exp_store, "failed")
    (problem,) = detect_problems_from_experience(store=exp_store, min_occurrence=2)
    proposal, _ = propose_and_evaluate(problem, repo)
    rec_store = DeployRecordStore(paths.workdir_deploy_records)

    result = deploy_proposal(proposal, repo, workspace_root=ws_root, record_store=rec_store)

    assert result.status == "pending_approval"
    assert rec_store.all() == [] and not paths.workdir_deploy_records.exists()


def test_save_failure_does_not_rewrite_a_successful_deploy(paths, repo, ws_root, monkeypatch):
    exp_store = ExperienceStore(path=paths.workdir_experience_store)
    _add_exp(exp_store, "stuck"); _add_exp(exp_store, "failed")
    (problem,) = detect_problems_from_experience(store=exp_store, min_occurrence=2)
    proposal, _ = propose_and_evaluate(problem, repo)
    rec_store = DeployRecordStore(paths.workdir_deploy_records)
    monkeypatch.setattr(rec_store, "save", lambda r: (_ for _ in ()).throw(OSError("disk full")))

    result = deploy_proposal(proposal, repo, approve=lambda p, r: True,
                             workspace_root=ws_root, record_store=rec_store)

    assert result.deployed, "仓库确实已经合并，不能谎报为失败"
    assert result.record is not None
    assert any("落盘失败" in e and "disk full" in e for e in result.errors)
    assert (repo.root / next(iter(proposal.changes))).is_file()


# ═══ 三、按 category 的 Observe ═══════════════════════════════════════

def test_category_observer_reads_category_from_record(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "e.db")
    rec = _record(deployed_at=100.0)
    for i in range(3):
        _add_exp(store, "done", created_at=101.0 + i)
    _add_exp(store, "failed", created_at=99.0)           # 部署前，不计
    _add_exp(store, "failed", created_at=105.0, goal="完全无关的另一类任务")  # 别的类别，不计

    obs = make_category_observer(store)(rec)
    assert (obs.verdict, obs.sample_count, obs.failure_count) == ("improved", 3, 0)


def test_category_observer_without_category_is_inconclusive_not_guessing(tmp_path: Path):
    store = ExperienceStore(path=tmp_path / "e.db")
    for i in range(5):
        _add_exp(store, "failed", created_at=200.0 + i)
    obs = make_category_observer(store)(_record(deployed_at=100.0, problem_category=""))
    assert obs.verdict == "inconclusive" and "problem_category" in obs.detail


def test_problem_based_observer_is_unchanged(tmp_path: Path):
    """9-3 的入口行为不变：与 category 版本对同一批数据给出相同结论。"""
    store = ExperienceStore(path=tmp_path / "e.db")
    _add_exp(store, "stuck"); _add_exp(store, "failed")
    (problem,) = detect_problems_from_experience(store=store, min_occurrence=2)
    rec = _record(deployed_at=100.0, problem_category=problem.category)
    _add_exp(store, "failed", created_at=101.0); _add_exp(store, "stuck", created_at=102.0)

    a = make_experience_observer(problem, store)(rec)
    b = make_category_observer(store)(rec)
    assert (a.verdict, a.sample_count, a.failure_count) == (b.verdict, b.sample_count, b.failure_count)
    assert a.verdict == "persists"


# ═══ 四、run_learn_step ═══════════════════════════════════════════════

def test_learn_improved_promotes_record_and_leaves_repo_alone(paths, repo, ws_root):
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    at = result.record.deployed_at
    for i in range(3):
        _add_exp(exp, "done", created_at=at + 1 + i)
    head = _head(repo)

    report = run_learn_step(paths)

    (o,) = report.observed
    assert (o.verdict, o.action) == ("improved", "promoted")
    assert rec_store.get(result.record.proposal_id).state == "promoted"
    assert _head(repo) == head and (repo.root / next(iter(proposal.changes))).is_file()
    assert report.errors == []


def test_learn_persists_without_auto_rollback_only_recommends(paths, repo, ws_root):
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    at = result.record.deployed_at
    _add_exp(exp, "failed", created_at=at + 1); _add_exp(exp, "stuck", created_at=at + 2)
    head = _head(repo)

    report = run_learn_step(paths)  # auto_rollback 默认 False

    (o,) = report.observed
    assert (o.verdict, o.action) == ("persists", "rollback_recommended")
    assert report.rollback_recommended == [o]
    assert _head(repo) == head, "未开启自动回退时绝不能动仓库"
    assert (repo.root / next(iter(proposal.changes))).is_file()
    assert rec_store.get(result.record.proposal_id).state == "observing", "留待人决定，下一轮仍会再提示"
    assert report.summary()["rollback_recommended"] == [result.record.proposal_id]


def test_learn_persists_with_auto_rollback_really_reverts(paths, repo, ws_root):
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    at = result.record.deployed_at
    _add_exp(exp, "failed", created_at=at + 1); _add_exp(exp, "stuck", created_at=at + 2)
    target = repo.root / next(iter(proposal.changes))
    head_after_deploy = _head(repo)

    report = run_learn_step(paths, auto_rollback=True)

    (o,) = report.observed
    assert (o.verdict, o.action) == ("persists", "rolled_back")
    # —— 真实性断言：磁盘与 git 历史 ——
    assert not target.exists()
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
    log = _git(repo, "log", "--format=%H %s")
    assert result.record.applied_commits[0] in log
    assert "Revert" in _git(repo, "log", "-1", "--format=%s")
    assert _head(repo) != head_after_deploy
    saved = rec_store.get(result.record.proposal_id)
    assert saved.state == "rolled_back" and len(saved.revert_commits) == 1

    # 再跑一轮：已定论的记录不会被重复回退
    head_after_rollback = _head(repo)
    again = run_learn_step(paths, auto_rollback=True)
    assert again.observed == [] and _head(repo) == head_after_rollback


def test_learn_inconclusive_changes_nothing(paths, repo, ws_root):
    exp, rec_store, _p, _proposal, result = _deploy_with_store(paths, repo, ws_root)
    _add_exp(exp, "done", created_at=result.record.deployed_at + 1)  # 样本不足
    head = _head(repo)

    report = run_learn_step(paths, auto_rollback=True)

    (o,) = report.observed
    assert (o.verdict, o.action) == ("inconclusive", "none")
    assert _head(repo) == head
    assert rec_store.get(result.record.proposal_id).state == "observing"


def test_learn_rollback_conflict_is_reported_and_persisted(paths, repo, ws_root):
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    rel = next(iter(proposal.changes))
    repo.apply({rel: "# 被人手工改过的内容\n"}, "manual edit", {"source": "t"}, "T1", auto_validators=True)
    at = result.record.deployed_at
    _add_exp(exp, "failed", created_at=at + 1); _add_exp(exp, "stuck", created_at=at + 2)
    head = _head(repo)

    report = run_learn_step(paths, auto_rollback=True)

    (o,) = report.observed
    assert o.action == "rollback_failed" and "失败" in o.detail
    assert report.summary()["rollback_failed"] == [result.record.proposal_id]
    assert _head(repo) == head
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == "", "冲突的 revert 必须被中止"
    assert rec_store.get(result.record.proposal_id).state == "rollback_failed"
    # rollback_failed 不再是 observing：不会被下一轮无限重试
    assert run_learn_step(paths, auto_rollback=True).observed == []


def test_learn_reports_recurring_problems(paths):
    exp = ExperienceStore(path=paths.workdir_experience_store)
    _add_exp(exp, "failed"); _add_exp(exp, "stuck")
    report = run_learn_step(paths)
    assert len(report.problems) == 1 and isinstance(report.problems[0], Problem)
    assert report.summary()["problem_count"] == 1


def test_learn_observation_never_creates_a_git_repo(tmp_path: Path):
    """观察阶段不能有 `git init` 这种副作用（`StateRepo(root)` 会在无 .git 时 init）。"""
    root = tmp_path / "plain_dir"
    root.mkdir()
    paths = AgentPaths(project_root=root)
    rec_store = DeployRecordStore(paths.workdir_deploy_records)
    rec_store.save(_record(deployed_at=1.0))
    exp = ExperienceStore(path=paths.workdir_experience_store)
    for i in range(3):
        _add_exp(exp, "done", created_at=10.0 + i)

    report = run_learn_step(paths)  # improved → promoted，不需要仓库

    assert report.observed[0].action == "promoted"
    assert not (root / ".git").exists()


def test_learn_with_nothing_to_do_is_empty_and_quiet(tmp_path: Path):
    report = run_learn_step(AgentPaths(project_root=tmp_path))
    assert report.observed == [] and report.problems == [] and report.errors == []


def test_learn_never_raises_and_still_reports_other_parts(paths, monkeypatch):
    exp = ExperienceStore(path=paths.workdir_experience_store)
    _add_exp(exp, "failed"); _add_exp(exp, "stuck")
    monkeypatch.setattr(DeployRecordStore, "list_observing",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

    report = run_learn_step(paths)

    assert any("boom" in e for e in report.errors)
    assert len(report.problems) == 1, "一部分失败不应拖垮另一部分"


def test_learn_one_bad_record_does_not_block_the_others(paths, monkeypatch, tmp_path):
    rec_store = DeployRecordStore(paths.workdir_deploy_records)
    rec_store.save(_record(proposal_id="prop:bad", deployed_at=1.0))
    rec_store.save(_record(proposal_id="prop:good", deployed_at=1.0))
    exp = ExperienceStore(path=paths.workdir_experience_store)
    for i in range(3):
        _add_exp(exp, "done", created_at=10.0 + i)

    real = learn_mod._settle_one

    def flaky(record, *a, **kw):
        if record.proposal_id == "prop:bad":
            raise RuntimeError("bad record")
        return real(record, *a, **kw)

    monkeypatch.setattr(learn_mod, "_settle_one", flaky)
    report = run_learn_step(paths)

    assert [o.proposal_id for o in report.observed] == ["prop:good"]
    assert any("prop:bad" in e for e in report.errors)


# ═══ 五、AgentRuntime 接入 ════════════════════════════════════════════

def _stub_judge(monkeypatch):
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE",
    )


def _completed_payloads() -> list:
    seen: list = []
    get_event_bus().subscribe("RuntimeCycleCompleted", lambda e: seen.append(e.payload))
    return seen


def test_learn_is_off_by_default_and_changes_nothing(monkeypatch, tmp_path):
    _stub_judge(monkeypatch)
    payloads = _completed_payloads()
    called = []
    monkeypatch.setattr("mini_agent.runtime.runtime.run_learn_step", lambda *a, **k: called.append(1))

    result = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=_FakeCfg(tmp_path)).run_once(_confirmed_spec())

    assert result.status == "done"
    assert result.learn_report is None and called == []
    assert "learn" not in payloads[0], "关闭时 RuntimeCycleCompleted 的 payload 必须与 Sprint 8-1 一致"


def test_learn_enabled_by_constructor_runs_and_reports(monkeypatch, tmp_path):
    _stub_judge(monkeypatch)
    payloads = _completed_payloads()

    rt = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=_FakeCfg(tmp_path), enable_learn_stage=True)
    result = rt.run_once(_confirmed_spec())

    assert result.status == "done"
    assert result.learn_report is not None
    assert payloads[0]["learn"]["observed"] == 0
    assert payloads[0]["learn"]["error_count"] == 0


def test_learn_enabled_by_config_flag(monkeypatch, tmp_path):
    _stub_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_learn_enabled = True
    result = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=cfg).run_once(_confirmed_spec())
    assert result.learn_report is not None


def test_learn_failure_never_changes_goal_result(monkeypatch, tmp_path):
    _stub_judge(monkeypatch)
    monkeypatch.setattr("mini_agent.runtime.runtime.run_learn_step",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("learn exploded")))

    result = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=_FakeCfg(tmp_path),
                          enable_learn_stage=True).run_once(_confirmed_spec())

    assert result.status == "done" and result.learn_report is None


def test_config_defaults_are_conservative():
    from mini_agent.config.models import AppConfig

    gm = AppConfig().goal_mode
    assert gm.runtime_learn_enabled is False
    assert gm.runtime_learn_auto_rollback is False


def test_end_to_end_runtime_learn_reverts_a_bad_deployment(monkeypatch, paths, repo, ws_root):
    """全链路：真实部署 → 之后同类任务仍失败 → 下一次 `run_once()` 的 learn 步骤
    在两个开关都打开时真的回退了仓库。"""
    _stub_judge(monkeypatch)
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    at = result.record.deployed_at
    _add_exp(exp, "failed", created_at=at + 1); _add_exp(exp, "stuck", created_at=at + 2)
    target = repo.root / next(iter(proposal.changes))
    assert target.is_file()

    cfg = _FakeCfg(repo.root)
    payloads = _completed_payloads()
    rt = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=cfg,
                      enable_learn_stage=True, learn_auto_rollback=True)
    out = rt.run_once(_confirmed_spec())

    assert out.status == "done"
    assert not target.exists(), "Rollback 必须真的发生在磁盘上"
    assert "Revert" in _git(repo, "log", "-1", "--format=%s")
    assert rec_store.get(result.record.proposal_id).state == "rolled_back"
    assert payloads[0]["learn"]["rolled_back"] == 1


def test_end_to_end_runtime_learn_default_does_not_touch_repo(monkeypatch, paths, repo, ws_root):
    """只开 learn、不开自动回退：只给出建议。"""
    _stub_judge(monkeypatch)
    exp, rec_store, _p, proposal, result = _deploy_with_store(paths, repo, ws_root)
    at = result.record.deployed_at
    _add_exp(exp, "failed", created_at=at + 1); _add_exp(exp, "stuck", created_at=at + 2)
    head = _head(repo)

    out = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=_FakeCfg(repo.root),
                       enable_learn_stage=True).run_once(_confirmed_spec())

    assert _head(repo) == head and (repo.root / next(iter(proposal.changes))).is_file()
    assert out.learn_report.summary()["rollback_recommended"] == [result.record.proposal_id]
