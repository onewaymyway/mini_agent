"""tests/test_lint_no_new_toplevel_concepts.py

Sprint 0（`next_doc/refactor_plan/02-executable-sprint-plan.md`）
"冻结新增一级概念" lint 脚本的测试，确保：
  1. 当前仓库真实状态下跑这个脚本不会误报（回归防护，避免脚本本身
     写挂了却没人发现）。
  2. 脚本确实能识别出新增的违规命名（不是摆设）。
  3. 白名单机制生效（历史遗留模块不会被误伤）。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "lint_no_new_toplevel_concepts.py"


def _load_lint_module():
    spec = importlib.util.spec_from_file_location("lint_no_new_toplevel_concepts", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def lint_mod():
    return _load_lint_module()


def test_real_repo_has_no_violations(lint_mod):
    """当前仓库真实的 src/mini_agent/ 顶层不应有违规命名（history_manager.py
    已在白名单里）。"""
    violations = lint_mod.find_violations(REPO_ROOT / "src" / "mini_agent")
    assert violations == []


def test_whitelist_entry_is_not_flagged(lint_mod, tmp_path):
    src_root = tmp_path / "src" / "mini_agent"
    src_root.mkdir(parents=True)
    (src_root / "history_manager.py").write_text("# ok, whitelisted\n")
    violations = lint_mod.find_violations(src_root)
    assert violations == []


@pytest.mark.parametrize(
    "bad_name",
    ["task_manager.py", "cron_scheduler.py", "growth_advisor.py", "SomeManager.py"],
)
def test_new_toplevel_file_with_forbidden_suffix_is_flagged(lint_mod, tmp_path, bad_name):
    src_root = tmp_path / "src" / "mini_agent"
    src_root.mkdir(parents=True)
    (src_root / bad_name).write_text("# should be flagged\n")
    violations = lint_mod.find_violations(src_root)
    assert bad_name in violations


def test_new_toplevel_directory_with_forbidden_suffix_is_flagged(lint_mod, tmp_path):
    src_root = tmp_path / "src" / "mini_agent"
    (src_root / "resource_manager").mkdir(parents=True)
    (src_root / "resource_manager" / "__init__.py").write_text("")
    violations = lint_mod.find_violations(src_root)
    assert "resource_manager" in violations


def test_nested_module_name_is_not_checked(lint_mod, tmp_path):
    """规则只检查一级，子包内部随便叫什么名字不受本规则约束。"""
    src_root = tmp_path / "src" / "mini_agent"
    nested = src_root / "role_agents"
    nested.mkdir(parents=True)
    (nested / "__init__.py").write_text("")
    (nested / "inner_scheduler.py").write_text("# nested, should not be flagged\n")
    violations = lint_mod.find_violations(src_root)
    assert violations == []


def test_cli_exit_code_nonzero_on_violation(tmp_path):
    """脚本作为独立进程运行时，检测到违规应返回非 0 退出码（CI 拦截依赖这一点）。"""
    import subprocess

    src_root = tmp_path / "src" / "mini_agent"
    src_root.mkdir(parents=True)
    (src_root / "job_scheduler.py").write_text("# violation\n")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "job_scheduler.py" in result.stderr


def test_cli_exit_code_zero_on_clean_repo(tmp_path):
    import subprocess

    src_root = tmp_path / "src" / "mini_agent"
    src_root.mkdir(parents=True)
    (src_root / "goal_mode").mkdir()

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
