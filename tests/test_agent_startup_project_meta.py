"""
tests/test_agent_startup_project_meta.py — Stage 4 验证（4.1 Agent 启动接入）

对应 self_evolution_stage4plus_plan.md Stage 4.1：
  Agent.__init__ -> _init_session() -> _maybe_ensure_project_meta()：
  agent 进程启动时确保 project.json 存在，且只在进程启动时计入一次
  total_sessions（不在 load_session() / new_session() 里重复计入）。
"""

from __future__ import annotations

import tempfile
import unittest
import unittest.mock
from pathlib import Path

import mini_agent.tools.builtin       # noqa: F401
import mini_agent.tools.evolution     # noqa: F401
import mini_agent.tools.workdir_knowledge  # noqa: F401

from mini_agent.config import load_config, WorkdirKnowledgeConfig
from mini_agent.agent import Agent
from mini_agent.storage.paths import AgentPaths
from mini_agent.perception.workdir_knowledge import load_project_meta


def make_cfg(project_root: Path):
    cfg = load_config(project_root=project_root)
    cfg.api_key = "test"
    cfg.stream = False
    return cfg


class TestAgentStartupCreatesProjectMeta(unittest.TestCase):

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.project_root)

    def tearDown(self):
        # 显式关闭 Agent 持有的文件句柄，防止 Windows 下 PermissionError
        if hasattr(self, '_agent') and self._agent is not None:
            self._agent.close()
        self._tmpdir.cleanup()

    def test_agent_construction_creates_project_meta(self):
        self.assertIsNone(load_project_meta(self.paths))
        self._agent = Agent(cfg=make_cfg(self.project_root))
        meta = load_project_meta(self.paths)
        self.assertIsNotNone(meta)
        self.assertEqual(meta.total_sessions, 1)

    def test_second_agent_in_same_dir_increments_total_sessions(self):
        agent1 = Agent(cfg=make_cfg(self.project_root))
        agent1.close()
        self._agent = Agent(cfg=make_cfg(self.project_root))
        meta = load_project_meta(self.paths)
        self.assertEqual(meta.total_sessions, 2)

    def test_disabled_config_skips_creation(self):
        cfg = make_cfg(self.project_root)
        cfg.workdir_knowledge = WorkdirKnowledgeConfig(enabled=False)
        self._agent = Agent(cfg=cfg)
        self.assertIsNone(load_project_meta(self.paths))

    def test_load_session_does_not_increment_total_sessions(self):
        """resume 一个已有 session 不是"启动一次新的 agent 进程"，
        不应重复计入 total_sessions（_maybe_ensure_project_meta 只在
        _init_session 里调用一次）。"""
        self._agent = Agent(cfg=make_cfg(self.project_root))
        meta_before = load_project_meta(self.paths)
        self.assertEqual(meta_before.total_sessions, 1)

        session_id = self._agent._session.id
        self._agent.load_session(session_id)

        meta_after = load_project_meta(self.paths)
        self.assertEqual(meta_after.total_sessions, 1)

    def test_failure_does_not_block_agent_construction(self):
        """即便 project.json 所在目录不可写，Agent 构造也不应抛异常
        （_maybe_ensure_project_meta 内部捕获所有异常）。"""
        # 让 workdir_dir 已存在但 project.json 路径被一个同名目录占用，
        # 触发写入失败（IsADirectoryError），验证不会向上抛出。
        workdir = self.paths.workdir_dir
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "project.json").mkdir()  # 用目录占住这个路径名

        try:
            self._agent = Agent(cfg=make_cfg(self.project_root))  # 不应抛异常
        except Exception as e:
            self.fail(f"Agent construction raised unexpectedly: {e}")

    def test_environment_fingerprint_populated_on_startup(self):
        self._agent = Agent(cfg=make_cfg(self.project_root))
        meta = load_project_meta(self.paths)
        self.assertIn("python_version", meta.environment_fingerprint)

    def test_environment_drift_detected_prints_info(self):
        """12.2 横向加固：第二次启动时若 fingerprint 变化，应打印提醒
        （通过 mock capture_environment_fingerprint 模拟"环境变了"）。"""
        agent1 = Agent(cfg=make_cfg(self.project_root))  # 第一次：建立 baseline fingerprint
        agent1.close()

        def fake_capture(project_root):
            return {"python_version": "999.0.0", "os": "FakeOS", "key_deps": {}, "captured_at": 0.0}

        import mini_agent.perception.workdir_knowledge as wk_mod
        real_capture = wk_mod.capture_environment_fingerprint
        wk_mod.capture_environment_fingerprint = fake_capture
        try:
            with unittest.mock.patch("mini_agent.ui.renderer.print_info") as mock_print:
                self._agent = Agent(cfg=make_cfg(self.project_root))  # 第二次：fingerprint 已变化
                self.assertTrue(mock_print.called)
                call_text = " ".join(str(c) for c in mock_print.call_args_list)
                self.assertIn("环境变化", call_text)
        finally:
            wk_mod.capture_environment_fingerprint = real_capture


if __name__ == "__main__":
    unittest.main()
