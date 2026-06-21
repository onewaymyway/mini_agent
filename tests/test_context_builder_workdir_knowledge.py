"""
tests/test_context_builder_workdir_knowledge.py — Stage 4 验证（4.6 context 注入）

对应 self_evolution_stage4plus_plan.md Stage 4.6：
  ContextBuilder._build_workdir_knowledge_block() 及其在 build() 中的接入——
  project.json 身份信息 / active WorkThread 进度 / 高优先级 open_threads
  三者 always-on 注入到 system prompt。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.config import AppConfig, WorkdirKnowledgeConfig
from mini_agent.context_builder import ContextBuilder
from mini_agent.storage.paths import AgentPaths
from mini_agent.perception import workdir_knowledge as wk


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def paths(project_root: Path) -> AgentPaths:
    return AgentPaths(project_root=project_root)


def make_builder(project_root: Path, enabled: bool = True, **wk_kwargs) -> ContextBuilder:
    cfg = AppConfig(
        project_root=project_root,
        workdir_knowledge=WorkdirKnowledgeConfig(enabled=enabled, **wk_kwargs),
    )
    return ContextBuilder(cfg=cfg)


# ── _build_workdir_knowledge_block 单独测试 ──────────────────────────────────

class TestBuildWorkdirKnowledgeBlock:

    def test_empty_when_nothing_exists(self, project_root):
        builder = make_builder(project_root)
        assert builder._build_workdir_knowledge_block() == ""

    def test_disabled_returns_empty_even_with_data(self, project_root, paths):
        wk.ensure_project_meta(paths, project_root, fallback_name="proj")
        builder = make_builder(project_root, enabled=False)
        assert builder._build_workdir_knowledge_block() == ""

    def test_includes_project_identity(self, project_root, paths):
        wk.ensure_project_meta(paths, project_root, fallback_name="my-proj")
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "Project identity" in block
        assert "my-proj" in block

    def test_includes_active_work_thread_progress(self, project_root, paths):
        t = wk.WorkThread(
            id="wt_1", title="实现W2", status="active",
            cumulative_progress="已完成4.1-4.4", next_suggested="开始4.5",
        )
        wk.upsert_work_thread(paths, t)
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "Active work threads" in block
        assert "实现W2" in block
        assert "已完成4.1-4.4" in block
        assert "开始4.5" in block

    def test_excludes_non_active_work_threads(self, project_root, paths):
        wk.upsert_work_thread(paths, wk.WorkThread(id="wt_1", title="done one", status="done"))
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "done one" not in block

    def test_includes_open_questions_in_work_thread(self, project_root, paths):
        t = wk.WorkThread(id="wt_1", title="T", status="active", open_questions=["q1", "q2"])
        wk.upsert_work_thread(paths, t)
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "q1" in block
        assert "q2" in block

    def test_includes_high_priority_open_threads(self, project_root, paths):
        wk.add_open_thread(paths, "紧急bug", "sess1", type="bug", priority="high")
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "High-priority open threads" in block
        assert "紧急bug" in block

    def test_excludes_low_medium_priority_open_threads(self, project_root, paths):
        wk.add_open_thread(paths, "low item", "s1", priority="low")
        wk.add_open_thread(paths, "medium item", "s1", priority="medium")
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "low item" not in block
        assert "medium item" not in block

    def test_respects_open_threads_inject_limit(self, project_root, paths):
        for i in range(10):
            wk.add_open_thread(paths, f"item{i}", "s1", priority="high")
        builder = make_builder(project_root, open_threads_inject_limit=2)
        block = builder._build_workdir_knowledge_block()
        count = sum(1 for i in range(10) if f"item{i}" in block)
        assert count == 2

    def test_combines_all_three_sections(self, project_root, paths):
        wk.ensure_project_meta(paths, project_root, fallback_name="proj")
        wk.upsert_work_thread(paths, wk.WorkThread(id="wt_1", title="工作线", status="active"))
        wk.add_open_thread(paths, "高优先级问题", "s1", priority="high")
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "Project identity" in block
        assert "Active work threads" in block
        assert "High-priority open threads" in block

    def test_corrupted_project_meta_does_not_block_other_sections(self, project_root, paths):
        paths.workdir_project_meta.parent.mkdir(parents=True, exist_ok=True)
        paths.workdir_project_meta.write_text("{broken", encoding="utf-8")
        wk.add_open_thread(paths, "高优先级问题", "s1", priority="high")
        builder = make_builder(project_root)
        block = builder._build_workdir_knowledge_block()
        assert "高优先级问题" in block


# ── build() 整体集成 ──────────────────────────────────────────────────────────

class TestBuildIntegration:

    def test_build_includes_workdir_knowledge_block(self, project_root, paths):
        wk.ensure_project_meta(paths, project_root, fallback_name="integration-proj")
        builder = make_builder(project_root)
        system = builder.build(history=[])
        assert "integration-proj" in system

    def test_build_without_workdir_data_does_not_break(self, project_root):
        builder = make_builder(project_root)
        system = builder.build(history=[])
        assert isinstance(system, str)
        assert len(system) > 0

    def test_build_disabled_omits_block(self, project_root, paths):
        wk.ensure_project_meta(paths, project_root, fallback_name="should-not-appear")
        builder = make_builder(project_root, enabled=False)
        system = builder.build(history=[])
        assert "should-not-appear" not in system
