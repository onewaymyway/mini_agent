"""
tests/test_workflow_step_session_dir.py

回归测试：`_execute_with_main_agent` 在把 workflow step 的 session 数据
目录绑定到 `.agent/workflow_sessions/<wf_session_id>/step_<id>/` 时，
必须写入可写字段 `step_cfg.session.dir`，而不是只读代理 property
`step_cfg.session_dir`（`AppConfig.session_dir` 没有 setter，直接赋值会
抛 `AttributeError: property 'session_dir' of 'AppConfig' object has no
setter`）。

之前 test_workflow_directory_mode.py 里覆盖同一段代码的用例用
`mock_load_config.return_value = MagicMock()`，MagicMock 允许给任意属性
赋值（包括只读 property），所以没能捕获这个 bug；这里改用真实的
`load_config()` 产出的 AppConfig 实例，才能复现并锁定这个回归。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.schema import WorkflowStep
from mini_agent.workflow.session import WorkflowSession


class TestExecuteWithMainAgentSessionDir(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)

        class _FakeCfg:
            def __init__(self, project_root):
                self.project_root = project_root
                self.verbose = False
                self.sandbox = False
                self.model = "test-model"
                self.llm_provider = "anthropic"
                self.llm_base_url = None
                self.api_key = "test-key"

        self.cfg = _FakeCfg(self.project_root)
        self.runner = WorkflowRunner(self.cfg)
        # 触发 bug 的前提条件：runner 正在一次真实的 workflow 执行中，
        # 即 _current_wf_session 已设置（否则 session_dir 覆盖逻辑整段被跳过）。
        self.runner._current_wf_session = WorkflowSession(
            workflow_session_id="wfs_test0001",
            workflow_name="demo",
        )

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("mini_agent.agent.Agent")
    def test_real_appconfig_session_dir_assignment_does_not_raise(self, mock_agent_cls):
        # 用真实的 load_config()（而不是 MagicMock）构造 step_cfg，
        # 这样只读 property 才会真正生效，能复现
        # "property 'session_dir' of 'AppConfig' object has no setter"。
        mock_agent_cls.return_value.run_turn.return_value = "OUT"

        step = WorkflowStep(id="analyze", name="analyze", prompt="do analyze")

        # 不应抛出 AttributeError
        result = self.runner._execute_with_main_agent(step, "hi")
        self.assertEqual(result, "OUT")

        # 传给 Agent() 的 cfg.session.dir 应该已经被设置为该 step 专属目录，
        # 且路径里带有 step id，便于按 step 排查产物。
        _, kwargs = mock_agent_cls.call_args
        step_cfg = kwargs.get("cfg")
        self.assertIsNotNone(step_cfg)
        self.assertIsNotNone(step_cfg.session.dir)
        self.assertIn("step_analyze", str(step_cfg.session.dir))

        # 只读 property 本身依然没有 setter（确认没有绕过验证这件事本身失效）。
        with self.assertRaises(AttributeError):
            step_cfg.session_dir = "/tmp/should-not-work"


if __name__ == "__main__":
    unittest.main()
