"""
tests/test_workspace.py — 阶段 1（Workspace 抽象）验收测试

对应 next_doc/external_projects_workspace_plan.md 阶段 1 最后一项：
"同一份代码用两个不同 Workspace.root 分别跑一次，验证 memory/session/
skill 解析结果完全隔离、互不污染"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.workspace import Workspace


def _make_two_roots(tmp_path: Path):
    root_a = tmp_path / "project_a"
    root_b = tmp_path / "project_b"
    root_a.mkdir()
    root_b.mkdir()
    return root_a, root_b


def test_memory_store_path_is_isolated_per_workspace(tmp_path):
    root_a, root_b = _make_two_roots(tmp_path)
    ws_a = Workspace(root=root_a)
    ws_b = Workspace(root=root_b)

    assert ws_a.memory_store_path != ws_b.memory_store_path
    assert ws_a.memory_store_path == root_a / ".agent" / "memory.jsonl"
    assert ws_b.memory_store_path == root_b / ".agent" / "memory.jsonl"


def test_sessions_dir_is_isolated_per_workspace(tmp_path):
    root_a, root_b = _make_two_roots(tmp_path)
    ws_a = Workspace(root=root_a)
    ws_b = Workspace(root=root_b)

    assert ws_a.sessions_dir != ws_b.sessions_dir
    assert str(ws_a.sessions_dir).startswith(str(root_a))
    assert str(ws_b.sessions_dir).startswith(str(root_b))


def test_skills_search_dirs_local_priority_and_global_fallback(tmp_path):
    root_a, root_b = _make_two_roots(tmp_path)
    global_skills = tmp_path / "global_skills"
    global_skills.mkdir()

    ws_a = Workspace(root=root_a, global_skills_dir=global_skills)
    ws_b = Workspace(root=root_b, global_skills_dir=global_skills)

    # 全局目录在前（兜底），本地目录在后（优先，同名覆盖）
    assert ws_a.skills_search_dirs == [global_skills, ws_a.skills_dir]
    assert ws_b.skills_search_dirs == [global_skills, ws_b.skills_dir]
    # 两个 workspace 的本地 skills 目录互不相同
    assert ws_a.skills_dir != ws_b.skills_dir


def test_no_global_skills_dir_only_local(tmp_path):
    root_a, _ = _make_two_roots(tmp_path)
    ws = Workspace(root=root_a)
    assert ws.skills_search_dirs == [ws.skills_dir]


def test_build_skill_loader_respects_local_override(tmp_path):
    root_a, _ = _make_two_roots(tmp_path)
    global_skills = tmp_path / "global_skills"
    local_skills = root_a / "skills"
    (global_skills / "shared").mkdir(parents=True)
    (local_skills / "shared").mkdir(parents=True)
    (global_skills / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: global version\n---\nglobal body\n",
        encoding="utf-8",
    )
    (local_skills / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: local version\n---\nlocal body\n",
        encoding="utf-8",
    )

    ws = Workspace(root=root_a, global_skills_dir=global_skills)
    loader = ws.build_skill_loader()

    assert "shared" in loader.available
    assert loader._all["shared"].description == "local version"


def test_build_skill_loader_isolated_between_workspaces(tmp_path):
    root_a, root_b = _make_two_roots(tmp_path)
    skills_a = root_a / "skills" / "only_a"
    skills_a.mkdir(parents=True)
    (skills_a / "SKILL.md").write_text(
        "---\nname: only_a\ndescription: a-only skill\n---\nbody\n", encoding="utf-8",
    )

    ws_a = Workspace(root=root_a)
    ws_b = Workspace(root=root_b)
    loader_a = ws_a.build_skill_loader()
    loader_b = ws_b.build_skill_loader()

    assert "only_a" in loader_a.available
    assert "only_a" not in loader_b.available


def test_apply_to_only_sets_project_root(tmp_path):
    root_a, _ = _make_two_roots(tmp_path)

    class _FakeCfg:
        project_root = Path("/somewhere/else")
        memory = type("M", (), {"store_path": None})()
        session = type("S", (), {"dir": None})()

    cfg = _FakeCfg()
    ws = Workspace(root=root_a)
    ws.apply_to(cfg)

    assert cfg.project_root == ws.root
    # 不碰其它字段，保持 None = 从 project_root 派生的既有约定
    assert cfg.memory.store_path is None
    assert cfg.session.dir is None


def test_root_is_resolved_and_expanded(tmp_path):
    root_a, _ = _make_two_roots(tmp_path)
    ws = Workspace(root=str(root_a))
    assert ws.root.is_absolute()
    assert ws.root == root_a.resolve()
