"""tests/test_phase9_sprint9_2_proposals.py

对应 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-2 验收标准：“一个真实的问题模式（Sprint 9-1 产出）能走到生成
Proposal 并被现有 Validators 接受评估，全程不需要绕开安全设施”。

“真实”指：Problem 不是手写的假对象，而是用 Sprint 9-1 的
`detect_problems_from_experience()` 从真实 `ExperienceStore` 里识别出来的；
评估用的是真实 `StateRepo` + 真实 `validators_for_tier()`，不 mock 校验。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mini_agent.core.experience import Experience
from mini_agent.core.experience_patterns import Problem, detect_problems_from_experience
from mini_agent.core.experience_store import ExperienceStore
from mini_agent.evolution.proposals import (
    EvolutionProposal,
    Hypothesis,
    ProposalError,
    build_proposal,
    evaluate_proposal,
    generate_hypothesis,
    propose_and_evaluate,
)
from mini_agent.evolution.state_repo import StateRepo


# ── 夹具 ──────────────────────────────────────────────────────────────

@pytest.fixture
def repo(tmp_path: Path) -> StateRepo:
    root = tmp_path / "proj"
    root.mkdir()
    r = StateRepo(root)
    r.ensure_initial_commit()
    return r


def _git_head(repo: StateRepo) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo.root, capture_output=True, text=True, check=True,
    ).stdout.strip()


def _worktree_files(repo: StateRepo) -> set[str]:
    return {
        str(p.relative_to(repo.root)).replace("\\", "/")
        for p in repo.root.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo.root).parts
    }


def _real_problem(tmp_path: Path) -> Problem:
    """从真实 ExperienceStore 里由 Sprint 9-1 的 Analyzer 识别出一个 Problem。"""
    store = ExperienceStore(path=tmp_path / "experience_store.db")
    for status in ("stuck", "failed"):
        store.append(Experience(
            source="goal_mode",
            goal_text="部署新版本到生产环境",
            status=status,
            rounds_used=3,
            final_report="",
            lesson="部署前先检查环境变量",
        ))
    problems = detect_problems_from_experience(store=store, min_occurrence=2)
    assert len(problems) == 1, "夹具前提：Sprint 9-1 应能识别出恰好一个重复失败问题"
    return problems[0]


def _problem(category: str = "demo task", pid: str = "experience:demo task") -> Problem:
    return Problem(
        problem_id=pid, source="experience", category=category,
        description=f"“{category}”类 Goal 反复失败 3 次", occurrence_count=3,
    )


# ── 验收标准：真实 Problem → Proposal → 现有 Validators 接受 ─────────────

def test_real_problem_flows_to_proposal_and_is_accepted_by_existing_validators(
    tmp_path: Path, repo: StateRepo,
):
    problem = _real_problem(tmp_path)

    proposal, evaluation = propose_and_evaluate(problem, repo)

    assert isinstance(proposal, EvolutionProposal)
    assert isinstance(proposal.hypothesis, Hypothesis)
    assert proposal.hypothesis.problem_id == problem.problem_id
    # 默认假设是 lesson 规则文档，文档类路径 → 请求 T1；autonomous 发起，
    # T1 不会被上浮，也没命中受保护路径。
    assert proposal.tier == "T1"
    assert all(p.startswith(".agent/lessons/") and p.endswith(".md") for p in proposal.changes)
    # 教训内容真的被带进了规则文件（不是空模板）
    (content,) = proposal.changes.values()
    assert "部署前先检查环境变量" in content

    assert evaluation.accepted is True
    assert evaluation.effective_tier == "T1"
    assert evaluation.forced_tier is False
    assert evaluation.validation_errors == []
    # 跑的确实是现有 T1 校验函数（不是本模块自己实现的替身）
    assert evaluation.validators_run == ["validate_t0_schema", "validate_t1_load"]


def test_evaluation_is_dry_run_no_commit_no_files(tmp_path: Path, repo: StateRepo):
    head_before = _git_head(repo)
    files_before = _worktree_files(repo)

    propose_and_evaluate(_real_problem(tmp_path), repo)

    assert _git_head(repo) == head_before, "评估阶段不允许产生 commit"
    assert _worktree_files(repo) == files_before, "评估阶段不允许落盘"


def test_proposal_fields_are_directly_compatible_with_state_repo_apply(
    tmp_path: Path, repo: StateRepo,
):
    """Sprint 9-3 会直接 `repo.apply(**proposal.apply_kwargs())`，这里只
    验证接口形状兼容、且 commit message 能从 meta 反查到 Problem 来源。
    （真实 sandbox 部署与 Promote/Rollback 属于 Sprint 9-3，不在此验收。）"""
    problem = _real_problem(tmp_path)
    proposal, evaluation = propose_and_evaluate(problem, repo)
    assert evaluation.accepted

    result = repo.apply(**proposal.apply_kwargs(), auto_validators=True)

    assert result.ok is True
    log = repo.log(limit=1)[0]
    assert problem.problem_id in log.body
    assert "occurrence_count: 2" in log.body


# ── 安全设施没有被绕开：校验失败必须真的拒绝 ───────────────────────────

def test_validator_failure_rejects_proposal(repo: StateRepo):
    # subagent profile 缺少 name → 现有 validate_t1_load 必须拒绝
    def bad_llm(problem):
        return {
            "statement": "新增一个 subagent",
            "changes": {".agent/agents/helper.md": "---\ndescription: no name here\n---\nbody\n"},
        }

    proposal, evaluation = propose_and_evaluate(_problem(), repo, llm_propose=bad_llm)

    assert proposal.hypothesis.generated_by == "llm"
    assert evaluation.accepted is False
    assert any("name" in err for err in evaluation.validation_errors)


def test_invalid_json_data_rejected_by_t0_schema(repo: StateRepo):
    def llm(problem):
        return {"statement": "调整统计数据", "changes": {"data/stats.json": "{not json"}}

    proposal = build_proposal(generate_hypothesis(_problem(), llm_propose=llm), tier="T0")
    evaluation = evaluate_proposal(proposal, repo)

    assert evaluation.accepted is False
    assert any("JSON" in err for err in evaluation.validation_errors)


def test_protected_path_is_forced_to_t3_and_syntax_error_is_rejected(repo: StateRepo):
    def llm(problem):
        return {
            "statement": "改主循环",
            "changes": {"src/mini_agent/agent/core.py": "def broken(:\n    pass\n"},
        }

    proposal = build_proposal(generate_hypothesis(_problem(), llm_propose=llm))
    # 非文档路径 → 请求 T2，但命中受保护路径，生效 tier 必须被安全设施强制升 T3
    assert proposal.tier == "T2"

    evaluation = evaluate_proposal(proposal, repo)

    assert evaluation.effective_tier == "T3"
    assert evaluation.forced_tier is True
    assert evaluation.accepted is False
    assert any("语法错误" in err for err in evaluation.validation_errors)


def test_autonomous_initiator_lifts_t0_to_t1(repo: StateRepo):
    def llm(problem):
        return {"statement": "写数据", "changes": {"data/stats.json": '{"ok": true}'}}

    hypothesis = generate_hypothesis(_problem(), llm_propose=llm)

    auto = evaluate_proposal(build_proposal(hypothesis, tier="T0", initiator="autonomous"), repo)
    user = evaluate_proposal(build_proposal(hypothesis, tier="T0", initiator="user"), repo)

    assert auto.effective_tier == "T1" and auto.forced_tier is True
    assert user.effective_tier == "T0" and user.forced_tier is False


# ── eval 对比 ─────────────────────────────────────────────────────────

def _report(with_rate: float, without_rate: float, with_ok: int = 3, without_ok: int = 3) -> dict:
    return {"summary": {
        "with_skill": {"tool_failure_rate": with_rate, "scenarios_ok": with_ok},
        "without_skill": {"tool_failure_rate": without_rate, "scenarios_ok": without_ok},
    }}


def test_eval_regression_rejects_even_when_validators_pass(repo: StateRepo):
    _, evaluation = propose_and_evaluate(
        _problem(), repo, eval_fn=lambda p: _report(with_rate=0.4, without_rate=0.1),
    )
    assert evaluation.validation_errors == []
    assert evaluation.eval_regression is True
    assert evaluation.accepted is False


def test_eval_without_regression_is_accepted(repo: StateRepo):
    _, evaluation = propose_and_evaluate(
        _problem(), repo, eval_fn=lambda p: _report(with_rate=0.1, without_rate=0.1),
    )
    assert evaluation.eval_regression is False
    assert evaluation.accepted is True


def test_eval_without_data_is_not_counted_against_proposal(repo: StateRepo):
    _, evaluation = propose_and_evaluate(_problem(), repo, eval_fn=lambda p: {})
    assert evaluation.eval_regression is None
    assert evaluation.accepted is True


# ── Hypothesis 生成的边界 ─────────────────────────────────────────────

@pytest.mark.parametrize("bad_path", ["/etc/passwd", "../outside.md", "C:\\Windows\\x.md", "a/../../b.md"])
def test_llm_hypothesis_with_escaping_path_is_refused_early(bad_path: str):
    def llm(problem):
        return {"statement": "越权", "changes": {bad_path: "x"}}

    with pytest.raises(ProposalError):
        generate_hypothesis(_problem(), llm_propose=llm)


def test_llm_hypothesis_missing_required_fields_is_refused():
    with pytest.raises(ProposalError):
        generate_hypothesis(_problem(), llm_propose=lambda p: {"statement": "只有陈述"})
    with pytest.raises(ProposalError):
        generate_hypothesis(_problem(), llm_propose=lambda p: "not a dict")  # type: ignore[arg-type,return-value]


def test_rule_template_is_idempotent_and_filename_safe_for_chinese_category():
    problem = _problem(category="部署 新版本: 生产/环境?", pid="experience:部署 新版本: 生产/环境?")

    h1 = generate_hypothesis(problem)
    h2 = generate_hypothesis(problem)

    assert h1.changes.keys() == h2.changes.keys(), "同一 Problem 必须生成同一文件名"
    (path,) = h1.changes.keys()
    name = path.rsplit("/", 1)[-1]
    assert not any(ch in name for ch in ':/\\?*"<>| '), f"文件名含不安全字符：{name}"


def test_build_proposal_refuses_empty_hypothesis():
    empty = Hypothesis(
        hypothesis_id="hyp:x", problem_id="p", statement="s", rationale="", expected_effect="",
    )
    with pytest.raises(ProposalError):
        build_proposal(empty)
