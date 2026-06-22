"""
tests/test_context_builder_global_knowledge.py — Stage 5 验证（5.5 context 注入）

对应 self_evolution_stage4plus_plan.md Stage 5.5：
  ContextBuilder._build_global_knowledge_block() 及其在 build() 中的接入——
  self_profile.self_assessment / evolution_state.pending_evolve_branches
  always-on 注入；projects_index + activity_log 仅在 workdir 变化时注入
  （同一个 ContextBuilder 实例第一次 build() 视为"刚切换"）。

所有测试通过 monkeypatch Path.home() 隔离真实 ~/.agent/ 目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.config import AppConfig, GlobalKnowledgeConfig
from mini_agent.context_builder import ContextBuilder
from mini_agent.storage.paths import AgentPaths
from mini_agent.perception import global_knowledge as gk


@pytest.fixture
def home_dir(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def paths(home_dir, project_root: Path) -> AgentPaths:
    return AgentPaths(project_root=project_root)


def make_builder(project_root: Path, enabled: bool = True, **gk_kwargs) -> ContextBuilder:
    cfg = AppConfig(
        project_root=project_root,
        global_knowledge=GlobalKnowledgeConfig(enabled=enabled, **gk_kwargs),
    )
    return ContextBuilder(cfg=cfg)


# ── _build_global_knowledge_block 单独测试 ───────────────────────────────────

class TestBuildGlobalKnowledgeBlock:

    def test_empty_when_nothing_exists(self, home_dir, project_root):
        builder = make_builder(project_root)
        assert builder._build_global_knowledge_block() == ""

    def test_disabled_returns_empty_even_with_data(self, project_root, paths):
        gk.ensure_self_profile(paths)
        builder = make_builder(project_root, enabled=False)
        assert builder._build_global_knowledge_block() == ""

    def test_includes_self_assessment(self, project_root, paths):
        profile = gk.ensure_self_profile(paths)
        profile.self_assessment.strengths = ["python refactoring"]
        profile.self_assessment.weak_areas = ["frontend css"]
        gk.save_self_profile(paths, profile)

        builder = make_builder(project_root)
        block = builder._build_global_knowledge_block()
        assert "Self-assessment" in block
        assert "python refactoring" in block
        assert "frontend css" in block

    def test_includes_pending_evolve_branches(self, project_root, paths):
        profile = gk.ensure_self_profile(paths)
        profile.evolution_state.pending_evolve_branches = ["evolve/bash-safety-skill"]
        gk.save_self_profile(paths, profile)

        builder = make_builder(project_root)
        block = builder._build_global_knowledge_block()
        assert "Pending evolve branches" in block
        assert "evolve/bash-safety-skill" in block

    def test_no_pending_branches_omits_section(self, project_root, paths):
        gk.ensure_self_profile(paths)  # 默认 pending_evolve_branches=[]
        builder = make_builder(project_root)
        block = builder._build_global_knowledge_block()
        assert "Pending evolve branches" not in block

    def test_first_build_injects_recent_activity_as_workdir_changed(self, project_root, paths):
        gk.register_or_touch_project(paths, project_root)
        gk.append_activity_log(paths, project_id="proj_x", session_id="s1", theme="实现了功能X", duration_min=5.0)

        builder = make_builder(project_root)
        block = builder._build_global_knowledge_block()
        assert "Recent cross-project activity" in block
        assert "实现了功能X" in block

    def test_second_build_same_workdir_does_not_repeat_activity_injection(self, project_root, paths):
        gk.register_or_touch_project(paths, project_root)
        gk.append_activity_log(paths, project_id="proj_x", session_id="s1", theme="实现了功能X", duration_min=5.0)

        builder = make_builder(project_root)
        builder._build_global_knowledge_block()  # 第一次：注入一次
        block2 = builder._build_global_knowledge_block()  # 第二次：同一个 workdir，不应重复注入
        assert "Recent cross-project activity" not in block2

    def test_respects_activity_log_inject_limit(self, project_root, paths):
        gk.register_or_touch_project(paths, project_root)
        for i in range(10):
            gk.append_activity_log(paths, project_id="proj_x", session_id=f"s{i}", theme=f"theme{i}", duration_min=1.0)

        builder = make_builder(project_root, activity_log_inject_limit=2)
        block = builder._build_global_knowledge_block()
        count = sum(1 for i in range(10) if f"theme{i}" in block)
        assert count == 2

    def test_total_projects_count_included(self, project_root, paths, tmp_path):
        gk.register_or_touch_project(paths, project_root)
        gk.register_or_touch_project(paths, tmp_path / "other")

        builder = make_builder(project_root)
        block = builder._build_global_knowledge_block()
        assert "2 project" in block

    def test_corrupted_self_profile_does_not_block_activity_section(self, project_root, paths):
        paths.global_self_profile.parent.mkdir(parents=True, exist_ok=True)
        paths.global_self_profile.write_text("{broken", encoding="utf-8")
        gk.register_or_touch_project(paths, project_root)
        gk.append_activity_log(paths, project_id="proj_x", session_id="s1", theme="主题", duration_min=1.0)

        builder = make_builder(project_root)
        block = builder._build_global_knowledge_block()
        assert "主题" in block

    def test_combines_assessment_and_activity_sections(self, project_root, paths):
        profile = gk.ensure_self_profile(paths)
        profile.self_assessment.strengths = ["重构"]
        gk.save_self_profile(paths, profile)
        gk.register_or_touch_project(paths, project_root)
        gk.append_activity_log(paths, project_id="proj_x", session_id="s1", theme="主题A", duration_min=1.0)

        builder = make_builder(project_root)
        block = builder._build_global_knowledge_block()
        assert "Self-assessment" in block
        assert "重构" in block
        assert "Recent cross-project activity" in block
        assert "主题A" in block


# ── build() 整体集成 ──────────────────────────────────────────────────────────

class TestBuildIntegration:

    def test_build_includes_global_knowledge_block(self, project_root, paths):
        profile = gk.ensure_self_profile(paths)
        profile.self_assessment.strengths = ["unique-marker-strength"]
        gk.save_self_profile(paths, profile)

        builder = make_builder(project_root)
        system = builder.build(history=[])
        assert "unique-marker-strength" in system

    def test_build_without_global_data_does_not_break(self, project_root, home_dir):
        builder = make_builder(project_root)
        system = builder.build(history=[])
        assert isinstance(system, str)
        assert len(system) > 0

    def test_build_disabled_omits_block(self, project_root, paths):
        profile = gk.ensure_self_profile(paths)
        profile.self_assessment.strengths = ["should-not-appear-marker"]
        gk.save_self_profile(paths, profile)

        builder = make_builder(project_root, enabled=False)
        system = builder.build(history=[])
        assert "should-not-appear-marker" not in system
