"""tests/test_phase9_sprint9_3_deployment.py

对应 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-3 验收标准：完整走一次 Experience → Pattern → Problem →
Hypothesis → Experiment → Evaluation → Proposal → Sandbox → Validation →
Deploy → Observe → Promote/Rollback，且 **Rollback 场景经过真实验证**。

“真实”的含义：
  - Experience 来自真实 `ExperienceStore`（SQLite），Problem 由 Sprint 9-1
    的 Analyzer 识别；
  - Sandbox 是真实 git worktree，Deploy 是真实 `merge_branch()`；
  - Rollback 后断言的是 **磁盘上文件真的消失** 和 **git 历史里真的有 revert
    commit、原 commit 仍在历史中**，而不是断言某个状态字段。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mini_agent.core.experience import Experience
from mini_agent.core.experience_patterns import Problem, detect_problems_from_experience
from mini_agent.core.experience_store import ExperienceStore
from mini_agent.evolution import deployment as dep
from mini_agent.evolution.deployment import (
    DeployRecord,
    Observation,
    deploy_proposal,
    make_experience_observer,
    rollback_deployment,
    settle_deployment,
)
from mini_agent.evolution.proposals import propose_and_evaluate, generate_hypothesis, build_proposal
from mini_agent.evolution.state_repo import StateRepo, StateRepoError
from mini_agent.evolution.workspace import EvolutionWorkspace, SmokeBootResult

GOAL = "部署新版本到生产环境"


# ── 夹具与辅助 ─────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path: Path) -> StateRepo:
    root = tmp_path / "proj"
    root.mkdir()
    r = StateRepo(root)
    r.ensure_initial_commit()
    return r


@pytest.fixture
def ws_root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _git(repo: StateRepo, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo.root, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _head(repo: StateRepo) -> str:
    return _git(repo, "rev-parse", "HEAD")


def _worktrees(repo: StateRepo) -> list[str]:
    return [l for l in _git(repo, "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]


def _add_exp(store: ExperienceStore, status: str, created_at: float | None = None, goal: str = GOAL):
    kwargs = {} if created_at is None else {"created_at": created_at}
    store.append(Experience(
        source="goal_mode", goal_text=goal, status=status, rounds_used=2,
        final_report="", lesson="部署前先检查环境变量" if status != "done" else "", **kwargs,
    ))


def _seed_problem(tmp_path: Path) -> tuple[ExperienceStore, Problem]:
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    _add_exp(store, "stuck")
    _add_exp(store, "failed")
    problems = detect_problems_from_experience(store=store, min_occurrence=2)
    assert len(problems) == 1
    return store, problems[0]


def _deployed(tmp_path: Path, repo: StateRepo, ws_root: Path):
    store, problem = _seed_problem(tmp_path)
    proposal, evaluation = propose_and_evaluate(problem, repo)
    assert evaluation.accepted
    result = deploy_proposal(proposal, repo, approve=lambda p, r: True, workspace_root=ws_root)
    assert result.deployed, result.errors
    return store, problem, proposal, result


# ── 完整闭环：Rollback 真实验证 ────────────────────────────────────────

def test_full_loop_ends_in_real_rollback(tmp_path: Path, repo: StateRepo, ws_root: Path):
    store, problem, proposal, result = _deployed(tmp_path, repo, ws_root)
    record = result.record
    (rel_path,) = proposal.changes.keys()
    target = repo.root / rel_path

    # Deploy 之后：文件真的在主工作区，且风险被分级为 low
    assert target.is_file()
    assert result.risk is not None and result.risk.risk == "low"
    assert record.state == "observing"
    assert len(record.applied_commits) == 1
    head_after_deploy = _head(repo)
    assert head_after_deploy == record.merge_commit

    # Observe：部署之后同类任务仍在失败（问题依旧）
    _add_exp(store, "failed", created_at=record.deployed_at + 1)
    _add_exp(store, "stuck", created_at=record.deployed_at + 2)
    observe = make_experience_observer(problem, store)

    settled, obs = settle_deployment(record, repo, observe)

    assert obs.verdict == "persists" and obs.failure_count == 2
    assert settled.state == "rolled_back"
    assert len(settled.revert_commits) == 1

    # —— 真实性断言：磁盘与 git 历史，而不是状态字段 ——
    assert not target.exists(), "Rollback 后文件必须真的从工作区消失"
    assert _git(repo, "status", "--porcelain") == "", "Rollback 后工作区必须干净"
    log = _git(repo, "log", "--format=%H %s")
    assert record.applied_commits[0] in log, "原 commit 必须仍在历史里（回退不改写历史）"
    assert settled.revert_commits[0] in log
    assert "Revert" in _git(repo, "log", "-1", "--format=%s")
    assert _head(repo) != head_after_deploy


def test_full_loop_ends_in_promote_and_repo_untouched(tmp_path: Path, repo: StateRepo, ws_root: Path):
    store, problem, proposal, result = _deployed(tmp_path, repo, ws_root)
    record = result.record
    (rel_path,) = proposal.changes.keys()
    head = _head(repo)

    for i in range(3):
        _add_exp(store, "done", created_at=record.deployed_at + 1 + i)

    settled, obs = settle_deployment(record, repo, make_experience_observer(problem, store))

    assert obs.verdict == "improved"
    assert settled.state == "promoted"
    assert (repo.root / rel_path).is_file()
    assert _head(repo) == head, "Promote 不应产生任何新 commit"


def test_inconclusive_observation_neither_promotes_nor_rolls_back(tmp_path: Path, repo: StateRepo, ws_root: Path):
    store, problem, proposal, result = _deployed(tmp_path, repo, ws_root)
    record = result.record
    head = _head(repo)
    _add_exp(store, "done", created_at=record.deployed_at + 1)  # 样本不足

    settled, obs = settle_deployment(record, repo, make_experience_observer(problem, store))

    assert obs.verdict == "inconclusive"
    assert settled.state == "observing"
    assert _head(repo) == head
    assert (repo.root / next(iter(proposal.changes))).is_file()


def test_observer_ignores_experiences_before_deploy_and_other_categories(tmp_path: Path, repo: StateRepo, ws_root: Path):
    store, problem, _proposal, result = _deployed(tmp_path, repo, ws_root)
    record = result.record
    # 部署前的旧失败（Seed 里已有 2 条）不能算进观察窗口；其它类别的失败也不算
    _add_exp(store, "failed", created_at=record.deployed_at + 1, goal="写一份周报发给团队")
    _add_exp(store, "failed", created_at=record.deployed_at + 2, goal="写一份周报发给团队")

    obs = make_experience_observer(problem, store)(record)

    assert obs.sample_count == 0 and obs.failure_count == 0
    assert obs.verdict == "inconclusive"


def test_settled_record_is_not_rolled_back_twice(tmp_path: Path, repo: StateRepo, ws_root: Path):
    store, problem, _p, result = _deployed(tmp_path, repo, ws_root)
    record = result.record
    _add_exp(store, "failed", created_at=record.deployed_at + 1)
    _add_exp(store, "failed", created_at=record.deployed_at + 2)
    observe = make_experience_observer(problem, store)
    settle_deployment(record, repo, observe)
    head = _head(repo)

    again, obs = settle_deployment(record, repo, observe)

    assert again.state == "rolled_back"
    assert _head(repo) == head, "已回退的记录不能被重复回退"
    assert "rolled_back" in obs.detail


# ── 固化 Rollback 实现方式的依据 ───────────────────────────────────────

def test_reverting_a_merge_commit_directly_fails(tmp_path: Path, repo: StateRepo, ws_root: Path):
    """这是 deployment.py 逐 commit 回退、而不是 revert(merge_commit) 的依据。
    如果哪天 `StateRepo.revert()` 支持了 `-m`，这条测试会失败，提醒我们可以简化。"""
    _store, _problem, _proposal, result = _deployed(tmp_path, repo, ws_root)
    with pytest.raises(StateRepoError, match="merge"):
        repo.revert(result.record.merge_commit)


def test_multi_commit_deployment_rolls_back_in_reverse_order(repo: StateRepo, ws_root: Path):
    """分支上有多个 commit 时，全部被回退，且工作区干净。"""
    h = generate_hypothesis(_problem_stub())
    proposal = build_proposal(h)
    ws = EvolutionWorkspace.create(repo, "evolve/multi", workspace_root=ws_root)
    wrepo = StateRepo(ws.path)
    wrepo.apply({".agent/lessons/one.md": "# one\n"}, "one", {"source": "t"}, "T1", auto_validators=True)
    wrepo.apply({".agent/lessons/two.md": "# two\n"}, "two", {"source": "t"}, "T1", auto_validators=True)
    ws.destroy()
    result = dep._merge_and_record(proposal, repo, "evolve/multi", dep.DeployResult(proposal.proposal_id, ""))
    record = result.record
    assert len(record.applied_commits) == 2
    assert (repo.root / ".agent/lessons/one.md").exists() and (repo.root / ".agent/lessons/two.md").exists()

    rollback_deployment(record, repo)

    assert record.state == "rolled_back" and len(record.revert_commits) == 2
    assert not (repo.root / ".agent/lessons/one.md").exists()
    assert not (repo.root / ".agent/lessons/two.md").exists()
    assert _git(repo, "status", "--porcelain") == ""


def test_rollback_failure_is_reported_not_swallowed(tmp_path: Path, repo: StateRepo, ws_root: Path):
    """后续提交改了同一个文件 → 回退会冲突。必须如实报告 rollback_failed，
    且不能把仓库留在“回退进行中”的半途状态。"""
    _store, _problem, proposal, result = _deployed(tmp_path, repo, ws_root)
    (rel_path,) = proposal.changes.keys()
    repo.apply({rel_path: "# 被人手工改过的内容\n"}, "manual edit", {"source": "t"}, "T1", auto_validators=True)
    head = _head(repo)

    rollback_deployment(result.record, repo)

    assert result.record.state == "rollback_failed"
    assert "失败" in result.record.settle_reason
    assert _head(repo) == head
    assert _git(repo, "status", "--porcelain") == "", "冲突的 revert 必须被中止，不能残留半途状态"
    assert (repo.root / rel_path).read_text(encoding="utf-8") == "# 被人手工改过的内容\n"


# ── 人审门与既有 CLI 的互操作 ──────────────────────────────────────────

def test_without_approve_stops_at_pending_and_keeps_branch(tmp_path: Path, repo: StateRepo, ws_root: Path):
    _store, problem = _seed_problem(tmp_path)
    proposal, _ = propose_and_evaluate(problem, repo)
    head = _head(repo)

    result = deploy_proposal(proposal, repo, workspace_root=ws_root)

    assert result.status == "pending_approval" and not result.deployed
    assert _head(repo) == head, "没有人审就不能合并"
    assert not (repo.root / next(iter(proposal.changes))).exists()
    assert result.branch in repo.list_branches()
    assert len(_worktrees(repo)) == 1, "沙盒 worktree 必须已被销毁，只剩主仓库"
    # 分支可被既有 `/evolution merge` 同款调用接手
    repo.merge_branch(result.branch)
    assert (repo.root / next(iter(proposal.changes))).is_file()


def test_rejected_by_approver_deletes_branch(tmp_path: Path, repo: StateRepo, ws_root: Path):
    _store, problem = _seed_problem(tmp_path)
    proposal, _ = propose_and_evaluate(problem, repo)
    seen = {}

    def deny(p, risk):
        seen["risk"] = risk
        return False

    result = deploy_proposal(proposal, repo, approve=deny, workspace_root=ws_root)

    assert result.status == "rejected"
    assert result.branch not in repo.list_branches()
    assert seen["risk"].risk == "low", "approve 回调必须收到风险分级结果"
    assert not (repo.root / next(iter(proposal.changes))).exists()


def test_high_risk_proposal_still_reaches_human_gate_with_risk_visible(repo: StateRepo, ws_root: Path):
    def llm(problem):
        return {"statement": "调一个脚本", "changes": {"scripts/tool.py": "x = 1\n"}}

    proposal = build_proposal(generate_hypothesis(_problem_stub(), llm_propose=llm))
    seen = {}

    def deny(p, risk):
        seen["risk"] = risk.risk
        return False

    # 非文档路径 → 请求 T2 → 会在沙盒里跑 T2 校验（无 tests/ 目录则放行）
    deploy_proposal(proposal, repo, approve=deny, workspace_root=ws_root)

    assert seen["risk"] == "high"


# ── 失败路径：不留孤儿沙盒/分支，也不动主仓库 ─────────────────────────

def test_evaluation_rejected_never_creates_sandbox(repo: StateRepo, ws_root: Path):
    def llm(problem):
        return {"statement": "坏 profile", "changes": {".agent/agents/x.md": "---\ndescription: 无 name\n---\n"}}

    proposal = build_proposal(generate_hypothesis(_problem_stub(), llm_propose=llm))
    head = _head(repo)

    result = deploy_proposal(proposal, repo, approve=lambda p, r: True, workspace_root=ws_root)

    assert result.status == "evaluation_rejected"
    assert result.branch == ""
    assert not [b for b in repo.list_branches() if b.startswith("evolve/")]
    assert len(_worktrees(repo)) == 1
    assert _head(repo) == head


def test_sandbox_validation_is_an_independent_second_gate(tmp_path: Path, repo: StateRepo, ws_root: Path, monkeypatch):
    """即使 dry-run 评估被绕过（这里用 monkeypatch 强行放行），沙盒内 apply()
    的真实校验仍必须拦住坏提案，并清理沙盒与分支。"""
    from mini_agent.evolution.proposals import ProposalEvaluation

    def llm(problem):
        return {"statement": "坏 profile", "changes": {".agent/agents/x.md": "---\ndescription: 无 name\n---\n"}}

    proposal = build_proposal(generate_hypothesis(_problem_stub(), llm_propose=llm))
    monkeypatch.setattr(dep, "evaluate_proposal", lambda *a, **k: ProposalEvaluation(
        proposal_id=proposal.proposal_id, accepted=True, requested_tier="T1", effective_tier="T1", forced_tier=False,
    ))
    head = _head(repo)

    result = deploy_proposal(proposal, repo, approve=lambda p, r: True, workspace_root=ws_root)

    assert result.status == "validation_failed"
    assert any("name" in e for e in result.errors)
    assert not [b for b in repo.list_branches() if b.startswith("evolve/")]
    assert len(_worktrees(repo)) == 1
    assert _head(repo) == head


def test_smoke_boot_failure_blocks_deploy_and_cleans_up(tmp_path: Path, repo: StateRepo, ws_root: Path, monkeypatch):
    _store, problem = _seed_problem(tmp_path)
    proposal, _ = propose_and_evaluate(problem, repo)
    monkeypatch.setattr(
        EvolutionWorkspace, "smoke_boot",
        lambda self, timeout=60.0: SmokeBootResult(ok=False, reason="boom", stderr="traceback..."),
    )
    head = _head(repo)

    result = deploy_proposal(proposal, repo, approve=lambda p, r: True, run_smoke_boot=True, workspace_root=ws_root)

    assert result.status == "smoke_failed"
    assert "boom" in result.errors[0]
    assert not [b for b in repo.list_branches() if b.startswith("evolve/")]
    assert len(_worktrees(repo)) == 1
    assert _head(repo) == head


def test_smoke_boot_not_run_by_default(tmp_path: Path, repo: StateRepo, ws_root: Path, monkeypatch):
    _store, problem = _seed_problem(tmp_path)
    proposal, _ = propose_and_evaluate(problem, repo)

    def must_not_be_called(self, timeout=60.0):
        raise AssertionError("默认不应启动 smoke boot 子进程")

    monkeypatch.setattr(EvolutionWorkspace, "smoke_boot", must_not_be_called)
    assert deploy_proposal(proposal, repo, workspace_root=ws_root).status == "pending_approval"


# ── 数据结构 ──────────────────────────────────────────────────────────

def test_deploy_record_roundtrips_through_dict():
    r = DeployRecord(
        proposal_id="prop:x", hypothesis_id="hyp:x", problem_id="p", expected_effect="e",
        branch="evolve/x", merge_commit="abc", applied_commits=["c1", "c2"], deployed_at=12.5,
    )
    assert DeployRecord.from_dict(r.to_dict()) == r
    # 向前兼容：多余字段被忽略而不是抛错
    assert DeployRecord.from_dict({**r.to_dict(), "future_field": 1}) == r


def test_observation_rejects_unknown_verdict():
    with pytest.raises(ValueError):
        Observation("maybe")


def _problem_stub() -> Problem:
    return Problem(
        problem_id="experience:stub", source="experience", category="stub",
        description="“stub”类 Goal 反复失败 3 次", occurrence_count=3,
    )
