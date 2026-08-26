"""
tests/test_external_projects_maintenance.py — 阶段 5（维护类交互标准化）验收测试

对应 next_doc/external_projects_workspace_plan.md 阶段 5：
  1. propose_maintenance_fix()：以目标外部项目 Workspace 为根，复用
     EvolutionWorkspace 的 git worktree 隔离，触发一次独立的提案-验证-
     落地流程（原则四）。
  2. 大管家标准工具集：list_projects / inspect_project / trigger_run /
     propose_fix（tools/external_projects.py）。
  3. 端到端场景：模拟"某个外部项目的抓取脚本因网站改版失效"——
     发现问题（inspect_project 读到失败账本）→ 提案改动（propose_fix）
     → 验证（校验流水线跑过）→ 落地（land_maintenance_fix 合并分支）
     → 再次触发确认修复生效。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from mini_agent.external_projects.maintenance import (
    MaintenanceError,
    propose_maintenance_fix,
    land_maintenance_fix,
)
from mini_agent.external_projects.registry import ExternalProjectRegistry
from mini_agent.evolution.state_repo import StateRepo

import mini_agent.tools.external_projects as ext_tools


def _init_git_project(root: Path, files: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=str(root), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(root), check=True)


# ── propose_maintenance_fix() ────────────────────────────────────────────


def test_propose_maintenance_fix_rejects_missing_dir(tmp_path):
    with pytest.raises(MaintenanceError):
        propose_maintenance_fix(tmp_path / "nope", {"a.py": "x = 1\n"}, "msg")


def test_propose_maintenance_fix_rejects_empty_changes(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with pytest.raises(MaintenanceError):
        propose_maintenance_fix(root, {}, "msg")


def test_propose_maintenance_fix_git_inits_fresh_dir(tmp_path):
    """目标目录没有 .git 时应自动 git init，而不是报错——呼应
    fresh-repo 场景（原则四的评估结论：worktree 机制对外部项目开箱即用）。"""
    root = tmp_path / "fresh_proj"
    root.mkdir()

    result = propose_maintenance_fix(
        root, {"entrypoints/run.py": "print('hello')\n"}, "Add entrypoint",
    )
    assert result.ok, result.error
    assert result.branch.startswith("evolve/")
    assert result.commit
    # main 分支（此时还没有任何 commit，主仓库应仍是"无提交"或只有隔离分支的痕迹，
    # 不应该在当前 checkout 上直接出现改动文件）。
    assert not (root / "entrypoints" / "run.py").exists()


def test_propose_maintenance_fix_branch_isolated_from_current_checkout(tmp_path):
    root = tmp_path / "proj"
    _init_git_project(root, {"entrypoints/scrape.py": "def scrape():\n    return 1\n"})

    result = propose_maintenance_fix(
        root,
        {"entrypoints/scrape.py": "def scrape():\n    return 2\n"},
        "Fix scrape() return value",
        reason="website redesign broke the old selector",
    )
    assert result.ok, result.error
    assert result.tier == "T2"

    # 当前 checkout（main/master）完全不受影响。
    content = (root / "entrypoints" / "scrape.py").read_text(encoding="utf-8")
    assert "return 1" in content

    # 但分支和 commit 确实留在了目标项目自己的仓库里，可供 review。
    repo = StateRepo(root)
    assert result.branch in repo.list_branches()


def test_propose_maintenance_fix_validation_failure_does_not_commit(tmp_path):
    root = tmp_path / "proj"
    _init_git_project(root, {"entrypoints/scrape.py": "def scrape():\n    return 1\n"})

    repo = StateRepo(root)
    branches_before = set(repo.list_branches())

    result = propose_maintenance_fix(
        root,
        {"entrypoints/scrape.py": "def scrape(:\n    return 2\n"},  # 语法错误
        "Broken fix",
    )
    assert not result.ok
    assert result.validation_errors

    # 校验失败：不应该新增任何分支（worktree/分支已被清理），当前 checkout 也不受影响。
    assert set(repo.list_branches()) == branches_before
    content = (root / "entrypoints" / "scrape.py").read_text(encoding="utf-8")
    assert "return 1" in content


def test_land_maintenance_fix_merges_branch(tmp_path):
    root = tmp_path / "proj"
    _init_git_project(root, {"entrypoints/scrape.py": "def scrape():\n    return 1\n"})

    result = propose_maintenance_fix(
        root,
        {"entrypoints/scrape.py": "def scrape():\n    return 2\n"},
        "Fix scrape() return value",
    )
    assert result.ok, result.error

    land_maintenance_fix(root, result.branch)

    content = (root / "entrypoints" / "scrape.py").read_text(encoding="utf-8")
    assert "return 2" in content
    # 落地后提案分支已按 merge_branch() 的默认行为被删除。
    repo = StateRepo(root)
    assert result.branch not in repo.list_branches()


# ── 大管家标准工具集（tools/external_projects.py）────────────────────────


def _register_project(tmp_path, name, root, *, health_cmd=None):
    yaml_text = (
        f"name: {name}\n"
        "entrypoints:\n"
        "  work:\n"
        f'    cmd: "{sys.executable} entrypoints/run.py"\n'
    )
    if health_cmd:
        yaml_text += f'health_check:\n  cmd: "{health_cmd}"\n'
    (root / "project.yaml").write_text(yaml_text, encoding="utf-8")
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    registry.register(name, root)
    return registry


def test_list_projects_tool(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    _init_git_project(root, {"entrypoints/run.py": "print('ok')\n"})
    registry = _register_project(tmp_path, "proj", root)
    monkeypatch.setattr(ext_tools, "_registry", lambda: registry)

    payload = json.loads(ext_tools.list_projects())
    assert payload["ok"] is True
    assert [p["name"] for p in payload["projects"]] == ["proj"]


def test_inspect_project_tool_unregistered(tmp_path, monkeypatch):
    registry = ExternalProjectRegistry(store_path=tmp_path / "registry.json")
    monkeypatch.setattr(ext_tools, "_registry", lambda: registry)

    payload = json.loads(ext_tools.inspect_project(name="nope"))
    assert payload["ok"] is False
    assert "error" in payload


def test_inspect_project_tool_happy_path(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    _init_git_project(root, {"entrypoints/run.py": "print('ok')\n"})
    registry = _register_project(tmp_path, "proj", root)
    monkeypatch.setattr(ext_tools, "_registry", lambda: registry)

    payload = json.loads(ext_tools.inspect_project(name="proj"))
    assert payload["ok"] is True
    assert payload["manifest"]["entrypoints"]["work"]["cmd"].endswith("entrypoints/run.py")
    assert payload["health"] in ("unknown", "healthy", "unhealthy")


def test_trigger_run_tool_records_ledger(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    _init_git_project(root, {"entrypoints/run.py": "print('ok')\n"})
    registry = _register_project(tmp_path, "proj", root)
    monkeypatch.setattr(ext_tools, "_registry", lambda: registry)

    payload = json.loads(ext_tools.trigger_run(name="proj", entrypoint="work"))
    assert payload["ok"] is True
    assert payload["returncode"] == 0

    from mini_agent.external_projects.ledger import read_ledger

    records = read_ledger(root)
    assert len(records) == 1
    assert records[0].entrypoint == "work"
    assert records[0].trigger == "manual"


def test_propose_fix_tool_end_to_end_scrape_repair(tmp_path, monkeypatch):
    """端到端验证（阶段 5 第三项）：模拟"抓取脚本因网站改版失效"场景，
    走完整的"发现问题 → 提案改动 → 验证 → 落地"链路。"""
    root = tmp_path / "stock_watch"
    broken_script = (
        "def scrape_price():\n"
        "    raise RuntimeError('selector .old-price not found')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    scrape_price()\n"
    )
    _init_git_project(root, {"entrypoints/run.py": broken_script})
    registry = _register_project(tmp_path, "stock_watch", root)
    monkeypatch.setattr(ext_tools, "_registry", lambda: registry)

    # 1) 发现问题：手动触发一次，脚本因"网站改版"失败，账本记下失败。
    run_payload = json.loads(ext_tools.trigger_run(name="stock_watch", entrypoint="work"))
    assert run_payload["ok"] is False
    assert run_payload["returncode"] != 0

    inspect_payload = json.loads(ext_tools.inspect_project(name="stock_watch"))
    assert inspect_payload["health"] == "unhealthy"
    assert inspect_payload["recent_runs"][-1]["exit_code"] != 0

    # 2) 提案改动：修好选择器逻辑（这里简化为直接返回固定值）。
    fixed_script = (
        "def scrape_price():\n"
        "    return 123.45\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    scrape_price()\n"
    )
    fix_payload = json.loads(
        ext_tools.propose_fix(
            name="stock_watch",
            changes={"entrypoints/run.py": fixed_script},
            message="Fix scrape_price() selector after site redesign",
            reason="old-price selector no longer exists on the site",
        )
    )
    assert fix_payload["ok"] is True, fix_payload
    branch = fix_payload["branch"]

    # 校验流水线跑过（T2：语法检查 + 若有 tests/ 则跑测试），当前 checkout 不受影响。
    content = (root / "entrypoints" / "run.py").read_text(encoding="utf-8")
    assert "raise RuntimeError" in content  # 主分支还是坏的

    # 3) 落地：人工 review 通过后合并。
    land_maintenance_fix(root, branch)
    content_after = (root / "entrypoints" / "run.py").read_text(encoding="utf-8")
    assert "return 123.45" in content_after

    # 4) 再次触发，确认修复生效。
    run_payload_2 = json.loads(ext_tools.trigger_run(name="stock_watch", entrypoint="work"))
    assert run_payload_2["ok"] is True
    assert run_payload_2["returncode"] == 0

    inspect_payload_2 = json.loads(ext_tools.inspect_project(name="stock_watch"))
    assert inspect_payload_2["health"] == "healthy"
