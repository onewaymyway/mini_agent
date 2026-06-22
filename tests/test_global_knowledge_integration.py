"""
tests/test_global_knowledge_integration.py — Stage 5 验证（agent.py 接入）

对应 self_evolution_stage4plus_plan.md Stage 5：
  - 5.2 Agent.__init__ -> _init_session() -> _maybe_register_global_project()：
    agent 进程启动时把当前 workdir 注册进 projects_index.json，并顺手跑一遍
    dormant 检查
  - 5.3 + 5.5 trigger_session_end() -> _update_workdir_knowledge_on_session_end()
    末尾追加 activity_log.jsonl 一行 + 更新 self_profile.json.operating_state
  - 5.5 _reflect_and_save_lessons() 事件驱动更新
    evolution_state.lifetime_lessons_generated

复用 tests/test_session_end_workdir_knowledge.py 的 make_minimal_agent 构造
模式（Agent.__new__ + 手动设置字段），并额外通过 monkeypatch Path.home()
隔离真实 ~/.agent/ 目录。
"""

from __future__ import annotations

import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mini_agent.llm.base import LLMResponse, LLMUsage


# ════════════════════════════════════════════════════════════════════════════
# Agent 启动接入：_maybe_register_global_project
# ════════════════════════════════════════════════════════════════════════════

def make_cfg(project_root: Path):
    from mini_agent.config import load_config
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    return cfg


class TestAgentStartupRegistersGlobalProject(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self._home_tmpdir = tempfile.TemporaryDirectory()
        self.home_dir = Path(self._home_tmpdir.name)
        self._home_patch = unittest.mock.patch.object(Path, "home", return_value=self.home_dir)
        self._home_patch.start()

        from mini_agent.storage.paths import AgentPaths
        self.paths = AgentPaths(self.project_root)

    def tearDown(self):
        self._home_patch.stop()
        self._tmpdir.cleanup()
        self._home_tmpdir.cleanup()

    def test_agent_construction_registers_project(self):
        from mini_agent.agent import Agent
        from mini_agent.perception.global_knowledge import load_projects_index

        self.assertEqual(load_projects_index(self.paths).projects, [])
        Agent(cfg=make_cfg(self.project_root))
        index = load_projects_index(self.paths)
        self.assertEqual(len(index.projects), 1)
        self.assertEqual(index.projects[0].total_sessions, 1)

    def test_second_agent_increments_total_sessions(self):
        from mini_agent.agent import Agent
        from mini_agent.perception.global_knowledge import load_projects_index

        Agent(cfg=make_cfg(self.project_root))
        Agent(cfg=make_cfg(self.project_root))
        index = load_projects_index(self.paths)
        self.assertEqual(len(index.projects), 1)
        self.assertEqual(index.projects[0].total_sessions, 2)

    def test_disabled_config_skips_registration(self):
        from mini_agent.agent import Agent
        from mini_agent.config import GlobalKnowledgeConfig
        from mini_agent.perception.global_knowledge import load_projects_index

        cfg = make_cfg(self.project_root)
        cfg.global_knowledge = GlobalKnowledgeConfig(enabled=False)
        Agent(cfg=cfg)
        index = load_projects_index(self.paths)
        self.assertEqual(index.projects, [])

    def test_two_different_workdirs_create_two_entries(self):
        from mini_agent.agent import Agent
        from mini_agent.perception.global_knowledge import load_projects_index

        other_root = Path(tempfile.mkdtemp())
        try:
            Agent(cfg=make_cfg(self.project_root))
            Agent(cfg=make_cfg(other_root))
            index = load_projects_index(self.paths)
            self.assertEqual(len(index.projects), 2)
        finally:
            import shutil
            shutil.rmtree(other_root, ignore_errors=True)

    def test_failure_does_not_block_agent_construction(self):
        """projects_index.json 所在路径不可写时，Agent 构造也不应抛异常。"""
        from mini_agent.agent import Agent

        global_dir = self.paths.global_dir
        global_dir.mkdir(parents=True, exist_ok=True)
        (global_dir / "projects_index.json").mkdir()  # 用目录占住这个路径名

        try:
            Agent(cfg=make_cfg(self.project_root))  # 不应抛异常
        except Exception as e:
            self.fail(f"Agent construction raised unexpectedly: {e}")

    def test_runs_dormant_refresh_on_startup(self):
        """启动时顺手跑一遍 dormant 检查：已注册的旧项目超过阈值天数应被标记。"""
        from mini_agent.agent import Agent
        from mini_agent.perception.global_knowledge import (
            register_or_touch_project, load_projects_index, save_projects_index,
        )
        import time as _time

        other_root = Path(tempfile.mkdtemp())
        try:
            register_or_touch_project(self.paths, other_root)
            index = load_projects_index(self.paths)
            index.projects[0].last_active = _time.time() - 31 * 86400
            save_projects_index(self.paths, index)

            Agent(cfg=make_cfg(self.project_root))  # 启动当前项目，顺手触发巡检

            index2 = load_projects_index(self.paths)
            other_entry = next(p for p in index2.projects if p.path == str(other_root.resolve()))
            self.assertEqual(other_entry.status, "dormant")
        finally:
            import shutil
            shutil.rmtree(other_root, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# SessionEnd 接入：activity_log + self_profile 更新
# ════════════════════════════════════════════════════════════════════════════

def make_minimal_agent(tmp_path: Path, global_knowledge_enabled: bool = True):
    from mini_agent.config import AppConfig, SessionConfig, MemoryConfig, SessionStats
    from mini_agent.config import WorkdirKnowledgeConfig, GlobalKnowledgeConfig
    from mini_agent.agent import Agent
    from mini_agent.session import Session

    cfg = AppConfig(
        auto_approve=True, project_root=tmp_path, model="test-model",
        session=SessionConfig(auto_save=False),
        memory=MemoryConfig(enabled=False),
        workdir_knowledge=WorkdirKnowledgeConfig(enabled=True),
        global_knowledge=GlobalKnowledgeConfig(enabled=global_knowledge_enabled),
    )

    agent = Agent.__new__(Agent)
    agent.cfg = cfg
    agent.stats = SessionStats()
    agent._history = []
    agent._memory = None
    agent._global_memory = None
    agent._session = Session(
        id="sess_test1", title="t", created_at="", updated_at="",
        provider="anthropic", model="test-model", stats={}, history=[],
    )
    agent._llm = MagicMock()
    agent._append_memory_delta = MagicMock()
    return agent


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


class TestSessionEndUpdatesGlobalActivityLog:

    def test_appends_activity_log_entry(self, home_dir, project_root):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import load_recent_activity

        agent = make_minimal_agent(project_root)
        agent._reflect_timeline_summary = MagicMock(return_value=("实现了功能X", ["完成了Y"]))
        agent._update_workdir_knowledge_on_session_end()

        paths = AgentPaths(project_root)
        records = load_recent_activity(paths)
        assert len(records) == 1
        assert records[0]["sid"] == "sess_test1"
        assert records[0]["theme"] == "实现了功能X"

    def test_disabled_skips_activity_log(self, home_dir, project_root):
        from mini_agent.storage.paths import AgentPaths

        agent = make_minimal_agent(project_root, global_knowledge_enabled=False)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))
        agent._update_workdir_knowledge_on_session_end()

        paths = AgentPaths(project_root)
        assert not paths.global_activity_log.exists()

    def test_updates_self_profile_operating_state(self, home_dir, project_root):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import load_self_profile

        agent = make_minimal_agent(project_root)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))
        agent.stats.input_tokens = 100
        agent.stats.output_tokens = 50
        agent._update_workdir_knowledge_on_session_end()

        paths = AgentPaths(project_root)
        profile = load_self_profile(paths)
        assert profile.operating_state.total_sessions_lifetime == 1
        assert profile.operating_state.active_project == str(project_root.resolve())
        assert profile.resource_budget.used_today == 150

    def test_activity_log_failure_does_not_block_self_profile_update(self, home_dir, project_root):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import load_self_profile
        import mini_agent.perception.global_knowledge as gk_mod

        agent = make_minimal_agent(project_root)
        agent._reflect_timeline_summary = MagicMock(return_value=("", []))

        real_append = gk_mod.append_activity_log
        gk_mod.append_activity_log = MagicMock(side_effect=RuntimeError("disk full"))
        try:
            agent._update_workdir_knowledge_on_session_end()  # 不应抛异常
        finally:
            gk_mod.append_activity_log = real_append

        paths = AgentPaths(project_root)
        profile = load_self_profile(paths)
        assert profile.operating_state.total_sessions_lifetime == 1

    def test_workdir_knowledge_still_works_when_global_disabled(self, home_dir, project_root):
        """W2（workdir_knowledge）与 W3（global_knowledge）互相独立——
        关闭 W3 不应影响 W2 的 timeline.jsonl 写入。"""
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.workdir_knowledge import load_recent_timeline

        agent = make_minimal_agent(project_root, global_knowledge_enabled=False)
        agent._reflect_timeline_summary = MagicMock(return_value=("主题", []))
        agent._update_workdir_knowledge_on_session_end()

        paths = AgentPaths(project_root)
        entries = load_recent_timeline(paths)
        assert len(entries) == 1
        assert entries[0]["theme"] == "主题"


# ════════════════════════════════════════════════════════════════════════════
# _reflect_and_save_lessons 事件驱动更新 evolution_state.lifetime_lessons_generated
# ════════════════════════════════════════════════════════════════════════════

class TestReflectAndSaveLessonsUpdatesEvolutionState:

    def test_increments_lifetime_lessons_generated(self, home_dir, project_root):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import load_self_profile

        agent = make_minimal_agent(project_root)
        agent._memory = MagicMock()
        agent._history = [{"role": "user", "content": "做点事", "_type": "user_input"}]
        agent.stats.tool_stats = {"bash": {"calls": 1, "success": 1, "fail": 0}}
        agent._llm.chat_with_retry.return_value = LLMResponse(
            text=json.dumps([
                {"trigger": "t1", "outcome": "o1", "root_cause": "r1",
                 "suggested_action": "a1", "confidence": 0.8},
                {"trigger": "t2", "outcome": "o2", "root_cause": "r2",
                 "suggested_action": "a2", "confidence": 0.7},
            ]),
            tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
        )

        saved = agent._reflect_and_save_lessons()
        assert saved == 2

        paths = AgentPaths(project_root)
        profile = load_self_profile(paths)
        assert profile.evolution_state.lifetime_lessons_generated == 2
        assert profile.evolution_state.last_reflection_at > 0

    def test_no_lessons_saved_does_not_touch_profile(self, home_dir, project_root):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import load_self_profile

        agent = make_minimal_agent(project_root)
        agent._history = []
        agent.stats.tool_stats = {}

        saved = agent._reflect_and_save_lessons()
        assert saved == 0

        paths = AgentPaths(project_root)
        # 没有任何 lesson 产生：不应该创建 self_profile.json
        assert load_self_profile(paths) is None

    def test_disabled_global_knowledge_skips_profile_update(self, home_dir, project_root):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.perception.global_knowledge import load_self_profile

        agent = make_minimal_agent(project_root, global_knowledge_enabled=False)
        agent._memory = MagicMock()
        agent._history = [{"role": "user", "content": "做点事", "_type": "user_input"}]
        agent.stats.tool_stats = {}
        agent._llm.chat_with_retry.return_value = LLMResponse(
            text=json.dumps([
                {"trigger": "t1", "outcome": "o1", "root_cause": "r1",
                 "suggested_action": "a1", "confidence": 0.8},
            ]),
            tool_calls=[], usage=LLMUsage(), stop_reason="end_turn",
        )

        saved = agent._reflect_and_save_lessons()
        assert saved == 1

        paths = AgentPaths(project_root)
        assert load_self_profile(paths) is None


if __name__ == "__main__":
    unittest.main()
