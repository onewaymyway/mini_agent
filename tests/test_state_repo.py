"""
tests/test_state_repo.py — Stage 2.1 验证

对应 self_evolution_implementation_plan.md Stage 2.1：
  StateRepo 类（apply/log/diff/revert/checkout_file），
  T3 强制升级（命中受保护路径清单），结构化 commit message 规范。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.evolution.state_repo import (
    StateRepo,
    StateRepoError,
    ValidationResult,
    VALID_TIERS,
)


@pytest.fixture
def repo(tmp_path: Path) -> StateRepo:
    return StateRepo(tmp_path)


# ── 初始化 ────────────────────────────────────────────────────────────────────

def test_init_creates_git_repo(tmp_path):
    assert not (tmp_path / ".git").exists()
    StateRepo(tmp_path)
    assert (tmp_path / ".git").is_dir()


def test_init_reuses_existing_git_repo(tmp_path):
    repo1 = StateRepo(tmp_path)
    repo1.apply(changes={"a.txt": "1"}, message="first", meta={}, tier="T0")
    repo2 = StateRepo(tmp_path)  # 重新打开同一个仓库
    logs = repo2.log()
    assert len(logs) == 1


def test_init_nonexistent_root_raises():
    with pytest.raises(StateRepoError):
        StateRepo(Path("/this/path/does/not/exist/at/all"))


def test_init_sets_local_git_identity_if_missing(tmp_path):
    """容器/CI 环境通常没有全局 git user.name/user.email，apply() 不应因此失败。"""
    r = StateRepo(tmp_path)
    proc = r._run_git(["config", "user.name"])
    assert proc.stdout.strip()
    proc = r._run_git(["config", "user.email"])
    assert proc.stdout.strip()


# ── apply() 基本行为 ──────────────────────────────────────────────────────────

def test_apply_writes_file_and_commits(repo, tmp_path):
    result = repo.apply(
        changes={"skills/foo/SKILL.md": "---\nname: foo\n---\nbody"},
        message="Add foo skill",
        meta={"proposed_by": "evolution-agent"},
        tier="T1",
    )
    assert result.ok
    assert result.tier == "T1"
    assert not result.forced_tier
    assert result.commit
    assert (tmp_path / "skills/foo/SKILL.md").read_text() == "---\nname: foo\n---\nbody"


def test_apply_empty_changes_raises(repo):
    with pytest.raises(StateRepoError):
        repo.apply(changes={}, message="nothing", meta={}, tier="T0")


def test_apply_invalid_tier_raises(repo):
    with pytest.raises(StateRepoError):
        repo.apply(changes={"a.txt": "x"}, message="bad tier", meta={}, tier="T9")


def test_apply_none_content_deletes_file(repo, tmp_path):
    repo.apply(changes={"a.txt": "hello"}, message="add", meta={}, tier="T0")
    assert (tmp_path / "a.txt").exists()
    result = repo.apply(changes={"a.txt": None}, message="delete", meta={}, tier="T0")
    assert result.ok
    assert not (tmp_path / "a.txt").exists()


def test_apply_relative_paths_resolved_under_root(repo, tmp_path):
    result = repo.apply(
        changes={str(tmp_path / "b.txt"): "abs-path-content"},
        message="absolute path within root",
        meta={}, tier="T0",
    )
    assert result.ok
    assert (tmp_path / "b.txt").read_text() == "abs-path-content"


def test_apply_absolute_path_outside_root_rejected(repo):
    with pytest.raises(StateRepoError):
        repo.apply(changes={"/etc/passwd": "pwned"}, message="evil", meta={}, tier="T0")


def test_apply_all_tiers_accepted(repo):
    for i, tier in enumerate(VALID_TIERS):
        result = repo.apply(changes={f"f{i}.txt": "x"}, message=f"tier {tier}", meta={}, tier=tier)
        assert result.ok
        assert result.tier == tier


# ── 受保护路径强制升级（T3 红线） ─────────────────────────────────────────────

def test_protected_path_forces_t3(repo):
    result = repo.apply(
        changes={"src/mini_agent/agent.py": "print('sneaky')"},
        message="try to sneak past tier",
        meta={},
        tier="T0",
    )
    assert result.ok
    assert result.tier == "T3"
    assert result.forced_tier


def test_protected_hooks_dir_forces_t3(repo):
    result = repo.apply(
        changes={"src/mini_agent/hooks/loader.py": "x = 1"},
        message="touch hooks loader",
        meta={}, tier="T1",
    )
    assert result.tier == "T3"
    assert result.forced_tier


def test_evolution_package_itself_is_protected(repo):
    """scripts/protected_paths.py 已经为 evolution/ 预留了正则规则（Stage 0.1），
    StateRepo 自身的代码也不能被自我演化绕过 T3 红线。"""
    result = repo.apply(
        changes={"src/mini_agent/evolution/state_repo.py": "x = 1"},
        message="touch state_repo itself",
        meta={}, tier="T0",
    )
    assert result.tier == "T3"
    assert result.forced_tier


def test_unprotected_path_keeps_requested_tier(repo):
    result = repo.apply(
        changes={"skills/foo/SKILL.md": "content"},
        message="normal skill add",
        meta={}, tier="T1",
    )
    assert result.tier == "T1"
    assert not result.forced_tier


def test_resolve_tier_mixed_paths_forces_t3_if_any_protected(repo):
    tier, forced = repo.resolve_tier(
        ["skills/foo/SKILL.md", "src/mini_agent/agent.py"], "T1"
    )
    assert tier == "T3"
    assert forced


def test_resolve_tier_invalid_tier_raises(repo):
    with pytest.raises(StateRepoError):
        repo.resolve_tier(["a.txt"], "bogus")


# ── 校验失败：不落盘、不 commit ──────────────────────────────────────────────

def test_validator_failure_blocks_write_and_commit(repo, tmp_path):
    def always_fail(root, changes):
        return ValidationResult.failure("intentional test failure")

    result = repo.apply(
        changes={"x.txt": "should not be written"},
        message="should fail", meta={}, tier="T0",
        validators=[always_fail],
    )
    assert not result.ok
    assert result.validation_errors == ["intentional test failure"]
    assert not (tmp_path / "x.txt").exists()
    assert repo.log() == []  # 没有任何 commit 产生


def test_validator_success_allows_write(repo, tmp_path):
    def always_pass(root, changes):
        return ValidationResult.success()

    result = repo.apply(
        changes={"x.txt": "ok"}, message="should pass", meta={}, tier="T0",
        validators=[always_pass],
    )
    assert result.ok
    assert (tmp_path / "x.txt").exists()


def test_multiple_validators_all_must_pass(repo):
    def pass_one(root, changes):
        return ValidationResult.success()

    def fail_one(root, changes):
        return ValidationResult.failure("nope")

    result = repo.apply(
        changes={"x.txt": "y"}, message="multi validator", meta={}, tier="T0",
        validators=[pass_one, fail_one],
    )
    assert not result.ok
    assert result.validation_errors == ["nope"]


def test_auto_validators_uses_effective_tier_for_protected_path(repo, tmp_path):
    """请求 T0 但命中受保护路径被升级为 T3 时，auto_validators 应该按
    生效的 T3（而非请求的 T0）选择校验函数——这里用语法错误的 .py 验证
    T2/T3 lint 校验确实被触发了，而不是只跑了宽松的 T0 schema 校验。"""
    result = repo.apply(
        changes={"src/mini_agent/agent.py": "def f(:\n  pass"},
        message="broken syntax via protected path",
        meta={}, tier="T0", auto_validators=True,
    )
    assert not result.ok
    assert result.tier == "T3"
    assert any("语法错误" in err for err in result.validation_errors)
    assert not (tmp_path / "src/mini_agent/agent.py").exists()


# ── log() ─────────────────────────────────────────────────────────────────────

def test_log_empty_repo_returns_empty_list(repo):
    assert repo.log() == []


def test_log_returns_commits_newest_first(repo):
    repo.apply(changes={"a.txt": "1"}, message="first", meta={}, tier="T0")
    repo.apply(changes={"b.txt": "2"}, message="second", meta={}, tier="T0")
    logs = repo.log()
    assert len(logs) == 2
    assert "second" in logs[0].subject
    assert "first" in logs[1].subject


def test_log_includes_structured_meta_in_body(repo):
    repo.apply(
        changes={"skills/foo/SKILL.md": "x"},
        message="Add foo skill",
        meta={
            "source_lessons": ["lesson_1", "lesson_2"],
            "session_id": "sess_x",
            "confidence": 0.82,
            "occurrence_count": 4,
            "proposed_by": "evolution-agent",
        },
        tier="T1",
    )
    logs = repo.log()
    body = logs[0].body
    assert "source_lessons: lesson_1, lesson_2" in body
    assert "session_id: sess_x" in body
    assert "confidence: 0.82" in body
    assert "occurrence_count: 4" in body
    assert "proposed_by: evolution-agent" in body


def test_log_subject_includes_tier_and_source(repo):
    repo.apply(
        changes={"a.txt": "x"}, message="hello world",
        meta={"source": "skill_propose"}, tier="T1",
    )
    logs = repo.log()
    assert logs[0].subject == "[T1][skill_propose] hello world"


def test_log_tracks_files_per_commit(repo):
    repo.apply(changes={"a.txt": "1", "b.txt": "2"}, message="two files", meta={}, tier="T0")
    logs = repo.log()
    assert set(logs[0].files) == {"a.txt", "b.txt"}


def test_log_respects_limit(repo):
    for i in range(5):
        repo.apply(changes={f"f{i}.txt": "x"}, message=f"commit {i}", meta={}, tier="T0")
    logs = repo.log(limit=2)
    assert len(logs) == 2


# ── diff() ───────────────────────────────────────────────────────────────────

def test_diff_between_commits(repo):
    repo.apply(changes={"a.txt": "v1"}, message="v1", meta={}, tier="T0")
    repo.apply(changes={"a.txt": "v2"}, message="v2", meta={}, tier="T0")
    diff_text = repo.diff("HEAD~1", "HEAD")
    assert "-v1" in diff_text
    assert "+v2" in diff_text


def test_diff_nonexistent_ref_does_not_raise_unexpectedly(repo):
    repo.apply(changes={"a.txt": "v1"}, message="v1", meta={}, tier="T0")
    # diff() uses check=False internally; invalid ref returns empty/garbage, not a crash
    result = repo.diff("HEAD~5", "HEAD")
    assert isinstance(result, str)


# ── revert() ─────────────────────────────────────────────────────────────────

def test_revert_creates_new_commit_undoing_change(repo, tmp_path):
    repo.apply(changes={"a.txt": "original"}, message="add a", meta={}, tier="T0")
    result2 = repo.apply(changes={"a.txt": None}, message="delete a", meta={}, tier="T0")

    revert_hash = repo.revert(result2.commit)
    assert revert_hash
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "a.txt").read_text() == "original"

    # revert 使用 `git revert`（生成新 commit），不是 reset --hard——
    # 历史里应该能看到全部 3 条 commit（add / delete / revert delete）
    logs = repo.log()
    assert len(logs) == 3


def test_revert_invalid_commit_raises(repo):
    with pytest.raises(StateRepoError):
        repo.revert("0123456789abcdef0123456789abcdef01234567")


# ── checkout_file() ──────────────────────────────────────────────────────────

def test_checkout_file_restores_single_file_without_committing(repo, tmp_path):
    result1 = repo.apply(changes={"a.txt": "v1", "b.txt": "keep-me"}, message="v1", meta={}, tier="T0")
    repo.apply(changes={"a.txt": "v2", "b.txt": "changed"}, message="v2", meta={}, tier="T0")

    repo.checkout_file(result1.commit, "a.txt")

    # a.txt 恢复到 v1（工作区内容，未自动 commit）
    assert (tmp_path / "a.txt").read_text() == "v1"
    # b.txt 不受影响，仍是 v2 阶段写入的内容
    assert (tmp_path / "b.txt").read_text() == "changed"
    # 没有自动产生新 commit
    assert len(repo.log()) == 2


# ── 分支辅助方法 ──────────────────────────────────────────────────────────────

def test_create_and_list_and_delete_branch(repo):
    repo.apply(changes={"a.txt": "1"}, message="seed", meta={}, tier="T0")
    repo.create_branch("evolve/test-branch")
    assert "evolve/test-branch" in repo.list_branches()
    assert "evolve/test-branch" in repo.list_branches(prefix="evolve/")
    repo.delete_branch("evolve/test-branch")
    assert "evolve/test-branch" not in repo.list_branches()


def test_current_branch_on_fresh_repo_does_not_raise(repo):
    # 仓库刚 init，尚无任何 commit；current_branch() 不应抛异常
    branch = repo.current_branch()
    assert isinstance(branch, str)
