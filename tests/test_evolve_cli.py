"""
tests/test_evolve_cli.py — Stage 3.1 验证（Phase C 之三）

对应 self_evolution_implementation_plan.md Stage 3.1：
  /evolve review|list slash 命令 —— 扫描 lesson、按阈值分组展示、
  spawn evolution-agent。

不调用 TaskManager.start()（不启动后台调度线程），submit() 仅把 Task
登记为 PENDING，不会真正跑起来——足以验证"/evolve review 正确构造并
提交了 Task"，不需要真实 LLM 调用。
"""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from pathlib import Path

from rich.console import Console

import mini_agent.tools.builtin  # noqa: F401
import mini_agent.tools.orchestration  # noqa: F401
import mini_agent.tools.evolution  # noqa: F401

from mini_agent.config import load_config
from mini_agent.agent import Agent
from mini_agent.perception.memory_store import MemoryEntry
from mini_agent.orchestrator.agent_profiles import init_agent_profiles
from mini_agent.tools import orchestration as orch
from mini_agent.orchestrator.task_manager import TaskManager
from mini_agent.cli.commands.evolve import handle_evolve_cmd
from mini_agent.ui.terminal import term, _Msg

PROJECT_ROOT = Path(__file__).parent.parent
EVOLUTION_AGENT_PROFILE = PROJECT_ROOT / ".agent" / "agents" / "evolution-agent.md"


def make_cfg(project_root: Path):
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    cfg.memory.enabled = True
    return cfg


def add_lesson(memory_backend, session_id, trigger, occurrence_count=1, source="self_reflection"):
    memory_backend.add(MemoryEntry(
        session_id=session_id, summary="", key_outcomes=[], tags=[], model="test-model",
        entry_type="lesson", trigger=trigger, outcome="some outcome",
        suggested_action="some action", occurrence_count=occurrence_count, source=source,
    ))


class _CapturedOutputMixin:
    """复用 test_evolution_cli.py 的输出捕获技巧（term._console + noop/join 同步）。"""

    def _setup_capture(self):
        self._buf = io.StringIO()
        self._orig_console = term._console
        term._console = Console(file=self._buf, width=120, force_terminal=False, highlight=False)

    def _teardown_capture(self):
        term._console = self._orig_console

    def _output(self) -> str:
        term._q.put(_Msg("_noop", None))
        term._q.join()
        return self._buf.getvalue()


class TestEvolveCliBase(_CapturedOutputMixin, unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)

        # 把真实的 evolution-agent.md profile 复制进测试项目，
        # 保证 /evolve review 能找到它（profile 发现路径是 <project_root>/.agent/agents/）
        agents_dir = self.project_root / ".agent" / "agents"
        agents_dir.mkdir(parents=True)
        shutil.copy(EVOLUTION_AGENT_PROFILE, agents_dir / "evolution-agent.md")

        self.cfg = make_cfg(self.project_root)
        init_agent_profiles(self.cfg)

        self.mgr = TaskManager(self.cfg, max_workers=2)  # 不调用 .start()
        orch._task_manager = self.mgr

        self.agent = Agent(cfg=self.cfg)
        self._setup_capture()

    def tearDown(self):
        self._teardown_capture()
        orch._task_manager = None
        if hasattr(self, 'agent') and self.agent is not None:
            self.agent.close()
        self._tmpdir.cleanup()


class TestEvolveList(TestEvolveCliBase):

    def test_no_qualifying_lessons_shows_message(self):
        handle_evolve_cmd(["list"], self.agent)
        out = self._output()
        self.assertIn("No lesson groups currently meet", out)

    def test_qualifying_lessons_shown_in_table(self):
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd(["list"], self.agent)
        out = self._output()
        self.assertIn("meeting T1 threshold", out)
        self.assertIn("1 found", out)

    def test_list_does_not_spawn_task(self):
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd(["list"], self.agent)
        self._output()
        self.assertEqual(len(self.mgr.list_records()), 0)

    def test_list_with_tier_t2_filters_out_non_human_feedback(self):
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=3)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=3)

        handle_evolve_cmd(["list", "--tier", "T2"], self.agent)
        out = self._output()
        self.assertIn("No lesson groups currently meet the T2", out)

    def test_default_subcommand_is_review(self):
        """不带参数的 /evolve 应等价于 /evolve review（会 spawn task）。"""
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd([], self.agent)
        self._output()
        self.assertEqual(len(self.mgr.list_records()), 1)

    def test_unknown_subcommand_shows_usage(self):
        handle_evolve_cmd(["bogus"], self.agent)
        out = self._output()
        self.assertIn("Usage", out)

    def test_no_agent_context_shows_error(self):
        handle_evolve_cmd(["review"], agent=None)
        out = self._output()
        self.assertIn("No active agent context", out)


class TestEvolveReview(TestEvolveCliBase):

    def test_review_spawns_evolution_agent_task(self):
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd(["review"], self.agent)
        out = self._output()

        self.assertIn("evolution-agent spawned", out)
        records = self.mgr.list_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].task.name, "evolve-review")

    def test_spawned_task_has_correct_tool_restrictions(self):
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd(["review"], self.agent)
        self._output()

        task = self.mgr.list_records()[0].task
        self.assertEqual(set(task.allowed_tools), {"skill_propose", "read_file", "grep", "list_dir"})

    def test_spawned_task_prompt_contains_lesson_data(self):
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd(["review"], self.agent)
        self._output()

        task = self.mgr.list_records()[0].task
        self.assertIn("forgot to run tests", task.prompt)

    def test_spawned_task_tagged_correctly(self):
        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd(["review"], self.agent)
        self._output()

        task = self.mgr.list_records()[0].task
        self.assertIn("evolution", task.tags)
        self.assertIn("agent:evolution-agent", task.tags)

    def test_review_with_no_qualifying_lessons_does_not_spawn(self):
        handle_evolve_cmd(["review"], self.agent)
        out = self._output()
        self.assertIn("No lesson groups currently meet", out)
        self.assertEqual(len(self.mgr.list_records()), 0)

    def test_existing_skills_passed_to_evolution_agent(self):
        skills_dir = self.project_root / ".claude" / "skills" / "code-review"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: existing skill\n---\nbody\n"
        )
        from mini_agent.skills import SkillLoader
        cfg2 = make_cfg(self.project_root)
        skill_loader = SkillLoader([cfg2.skills_dir] if cfg2.skills_dir else [])
        self.agent.skill_loader = skill_loader

        add_lesson(self.agent._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
        add_lesson(self.agent._memory, "s2", "did not run tests before commit", occurrence_count=1)

        handle_evolve_cmd(["review"], self.agent)
        self._output()

        task = self.mgr.list_records()[0].task
        self.assertIn("code-review", task.prompt)

    def test_review_without_memory_backend_shows_error(self):
        with tempfile.TemporaryDirectory() as tmp2:
            cfg2 = make_cfg(Path(tmp2))
            cfg2.memory.enabled = False
            agent2 = Agent(cfg=cfg2)
            try:
                handle_evolve_cmd(["review"], agent2)
                out = self._output()
                self.assertIn("No project memory backend available", out)
            finally:
                agent2.close()

    def test_review_global_flag_uses_global_memory(self):
        # 没有 global memory backend 时应明确报错而不是静默用错 backend
        with tempfile.TemporaryDirectory() as tmp2:
            cfg2 = make_cfg(Path(tmp2))
            agent2 = Agent(cfg=cfg2)
            try:
                agent2._global_memory = None
                handle_evolve_cmd(["review", "--global"], agent2)
                out = self._output()
                self.assertIn("No global memory backend available", out)
            finally:
                agent2.close()

    def test_missing_evolution_agent_profile_shows_error(self):
        with tempfile.TemporaryDirectory() as tmp2:
            project_root2 = Path(tmp2)
            cfg2 = make_cfg(project_root2)
            init_agent_profiles(cfg2)  # 没有 .agent/agents/evolution-agent.md
            mgr2 = TaskManager(cfg2, max_workers=2)
            orch._task_manager = mgr2
            agent2 = Agent(cfg=cfg2)
            try:
                add_lesson(agent2._memory, "s1", "forgot to run tests before commit", occurrence_count=2)
                add_lesson(agent2._memory, "s2", "did not run tests before commit", occurrence_count=1)

                handle_evolve_cmd(["review"], agent2)
                out = self._output()
                self.assertIn("evolution-agent profile not found", out)
            finally:
                agent2.close()
                mgr2.stop()


if __name__ == "__main__":
    unittest.main()
