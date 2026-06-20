"""
tests/test_evolution_workspace.py — Stage 2.3 验证

对应 self_evolution_implementation_plan.md Stage 2.3：
  EvolutionWorkspace（git worktree 创建/销毁骨架 + smoke_boot 最低验证 +
  eval_result.json 落盘机制），第一版不做"自动跑 eval 场景"。

性能说明：smoke_boot() 会真的 spawn 一个子进程 import mini_agent 全套模块，
比纯单测慢（约 1-2 秒/次），因此本文件只在少数关键用例里真正调用 smoke_boot()，
其余用例聚焦 worktree 生命周期管理本身（创建/销毁/分支/异常路径），不重复
触发子进程。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from mini_agent.evolution.state_repo import StateRepo
from mini_agent.evolution.workspace import (
    EvolutionWorkspace,
    EvolutionWorkspaceError,
)

# 仓库根目录（本测试文件所在仓库），用于构造一个"真实可 import"的 worktree 副本。
_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def seeded_project(tmp_path_factory) -> Path:
    """
    复制一份精简但功能完整的项目快照（仅 src/ + pyproject.toml + requirements.txt），
    初始化为独立 git 仓库，作为本文件内全部 worktree 测试的共享基底。

    用 module 级 fixture 避免每个测试都重新复制一遍 src/ 目录（IO 较重）。
    """
    base = tmp_path_factory.mktemp("evo_ws_project")
    shutil.copytree(_REPO_ROOT / "src", base / "src")
    shutil.copy(_REPO_ROOT / "pyproject.toml", base / "pyproject.toml")
    if (_REPO_ROOT / "requirements.txt").exists():
        shutil.copy(_REPO_ROOT / "requirements.txt", base / "requirements.txt")

    repo = StateRepo(base)
    repo._run_git(["add", "-A"])
    repo._run_git(["commit", "-m", "seed snapshot"])
    return base


@pytest.fixture
def repo(seeded_project) -> StateRepo:
    return StateRepo(seeded_project)


@pytest.fixture
def ws_root(tmp_path) -> Path:
    """每个测试独立的 workspace 临时目录，避免测试间 worktree 路径冲突。"""
    d = tmp_path / "workspaces"
    d.mkdir()
    return d


def _cleanup_branches(repo: StateRepo, prefix: str = "evolve/") -> None:
    for b in repo.list_branches(prefix=prefix):
        repo.delete_branch(b, force=True)


# ── 创建 / 销毁 ──────────────────────────────────────────────────────────────

def test_create_makes_new_branch_and_worktree_dir(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-create", workspace_root=ws_root)
    try:
        assert ws.path.exists()
        assert ws.path.is_dir()
        assert (ws.path / "src" / "mini_agent" / "__init__.py").exists()
        assert "evolve/test-create" in repo.list_branches()
    finally:
        ws.destroy(delete_branch=True)


def test_destroy_removes_worktree_dir(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-destroy", workspace_root=ws_root)
    path = ws.path
    assert path.exists()
    ws.destroy(delete_branch=True)
    assert not path.exists()


def test_destroy_without_delete_branch_keeps_branch(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-keep-branch", workspace_root=ws_root)
    ws.destroy(delete_branch=False)
    assert "evolve/test-keep-branch" in repo.list_branches()
    _cleanup_branches(repo, "evolve/test-keep-branch")


def test_destroy_is_idempotent(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-idempotent", workspace_root=ws_root)
    ws.destroy(delete_branch=True)
    ws.destroy(delete_branch=True)  # 第二次调用不应抛异常


def test_context_manager_destroys_on_exit(repo, ws_root):
    captured_path = None
    with EvolutionWorkspace.create(repo, branch="evolve/test-ctx", workspace_root=ws_root) as ws:
        captured_path = ws.path
        assert captured_path.exists()
    assert not captured_path.exists()
    _cleanup_branches(repo, "evolve/test-ctx")


def test_create_duplicate_target_path_raises(repo, ws_root):
    ws1 = EvolutionWorkspace.create(repo, branch="evolve/dup-a", workspace_root=ws_root)
    try:
        with pytest.raises(EvolutionWorkspaceError):
            EvolutionWorkspace.create(repo, branch="evolve/dup-a", workspace_root=ws_root)
    finally:
        ws1.destroy(delete_branch=True)


def test_create_reuses_existing_branch(repo, ws_root):
    repo.create_branch("evolve/preexisting")
    try:
        ws = EvolutionWorkspace.create(repo, branch="evolve/preexisting", workspace_root=ws_root)
        try:
            assert ws.path.exists()
        finally:
            ws.destroy(delete_branch=False)
    finally:
        _cleanup_branches(repo, "evolve/preexisting")


def test_worktree_changes_isolated_from_main_repo(repo, ws_root, seeded_project):
    """在 worktree 里改动文件，不应影响主仓库工作区内容（进程级隔离的核心保证）。"""
    main_file = seeded_project / "src" / "mini_agent" / "agent.py"
    original_content = main_file.read_text()

    ws = EvolutionWorkspace.create(repo, branch="evolve/test-isolation", workspace_root=ws_root)
    try:
        ws_file = ws.path / "src" / "mini_agent" / "agent.py"
        ws_file.write_text("# modified in worktree only\n")
        assert main_file.read_text() == original_content
        assert ws_file.read_text() == "# modified in worktree only\n"
    finally:
        ws.destroy(delete_branch=True)


# ── needs_isolated_venv() ─────────────────────────────────────────────────────

def test_needs_isolated_venv_false_when_deps_unchanged(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-venv-same", workspace_root=ws_root)
    try:
        assert ws.needs_isolated_venv() is False
    finally:
        ws.destroy(delete_branch=True)


def test_needs_isolated_venv_true_when_pyproject_changed(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-venv-diff", workspace_root=ws_root)
    try:
        pyproject = ws.path / "pyproject.toml"
        pyproject.write_text(pyproject.read_text() + "\n# a harmless comment change\n")
        assert ws.needs_isolated_venv() is True
    finally:
        ws.destroy(delete_branch=True)


# ── smoke_boot()（真实子进程，较慢，仅覆盖关键路径） ──────────────────────────

def test_smoke_boot_succeeds_on_unmodified_snapshot(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-smoke-ok", workspace_root=ws_root)
    try:
        result = ws.smoke_boot(timeout=60)
        assert result.ok, f"smoke boot failed unexpectedly: {result.stderr}"
        assert result.returncode == 0
        assert "SMOKE_BOOT_OK" in result.stdout
        assert result.duration_seconds >= 0
    finally:
        ws.destroy(delete_branch=True)


def test_smoke_boot_fails_on_syntax_error(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-smoke-broken", workspace_root=ws_root)
    try:
        (ws.path / "src" / "mini_agent" / "agent.py").write_text("this is not valid python !!! ###")
        result = ws.smoke_boot(timeout=60)
        assert not result.ok
        assert result.returncode != 0
        assert "SyntaxError" in result.stderr or "syntax" in result.reason.lower()
    finally:
        ws.destroy(delete_branch=True)


def test_smoke_boot_on_destroyed_workspace_fails_gracefully(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-smoke-destroyed", workspace_root=ws_root)
    ws.destroy(delete_branch=True)
    result = ws.smoke_boot(timeout=10)
    assert not result.ok
    assert "does not exist" in result.reason


# ── eval_result.json 落盘 ─────────────────────────────────────────────────────

def test_write_eval_result_creates_file(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-eval-result", workspace_root=ws_root)
    try:
        path = ws.write_eval_result({"smoke_boot_ok": True, "duration": 1.23})
        assert path == ws.eval_result_path()
        assert path.exists()
        import json
        data = json.loads(path.read_text())
        assert data["smoke_boot_ok"] is True
    finally:
        ws.destroy(delete_branch=True)


def test_eval_result_path_under_dot_agent(repo, ws_root):
    ws = EvolutionWorkspace.create(repo, branch="evolve/test-eval-path", workspace_root=ws_root)
    try:
        assert ws.eval_result_path() == ws.path / ".agent" / "eval_result.json"
    finally:
        ws.destroy(delete_branch=True)
