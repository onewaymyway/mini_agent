"""
tests/test_orchestration_llm_helper_provider.py

跟进 next_doc/llm_helper_unification_plan.md 第 0 节已知限制的收尾测试：
run_ensemble_llm / run_ensemble_subagents 此前只能拿到 TaskManager.base_cfg
（启动时静态配置），不会跟随 /model 切换。本文件验证新增的 thread-local
"当前 agent.llm_helper"provider 机制（与 _active_skills_local 同款写法）：

  - set_current_llm_helper_provider 未注册时，_get_current_llm_helper() 返回 None
    （调用方应退化为 LLMHelper.from_config(cfg)，行为与迁移前一致）
  - 注册后，_get_current_llm_helper() 返回 provider() 的结果
  - provider() 抛异常时静默返回 None，不向上传播（与 _get_active_skills 同款降级语义）
  - run_ensemble_llm / run_ensemble_subagents 会把 _get_current_llm_helper() 的结果
    透传给 ensemble/runner.py 的 run_llm_ensemble / run_subagent_ensemble
  - Agent.__init__ 会为当前线程注册 provider，且不依赖 skill_loader 是否启用
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import mini_agent.tools.orchestration as orch


class TestCurrentLLMHelperProvider(unittest.TestCase):
    def tearDown(self):
        orch.set_current_llm_helper_provider(None)

    def test_returns_none_when_not_registered(self):
        orch.set_current_llm_helper_provider(None)
        self.assertIsNone(orch._get_current_llm_helper())

    def test_returns_provider_result_when_registered(self):
        fake_helper = MagicMock(name="fake_llm_helper")
        orch.set_current_llm_helper_provider(lambda: fake_helper)
        self.assertIs(orch._get_current_llm_helper(), fake_helper)

    def test_swallows_exception_and_returns_none(self):
        def boom():
            raise RuntimeError("no agent bound to this thread")
        orch.set_current_llm_helper_provider(boom)
        self.assertIsNone(orch._get_current_llm_helper())


class TestRunEnsembleLLMPropagatesHelper(unittest.TestCase):
    def tearDown(self):
        orch.set_current_llm_helper_provider(None)

    def _make_cfg_with_ensemble(self):
        cfg = MagicMock()
        cfg.ensemble.mode = "manual"
        cfg.ensemble.granularity = "both"
        cfg.ensemble.judge_strategy = "llm_judge"
        return cfg

    def test_run_ensemble_llm_passes_current_helper(self):
        cfg = self._make_cfg_with_ensemble()
        mgr = MagicMock()
        mgr.base_cfg = cfg
        fake_helper = MagicMock(name="fake_llm_helper")
        orch.set_current_llm_helper_provider(lambda: fake_helper)

        fake_result = MagicMock()
        fake_result.final_content = "ok"
        fake_result.chosen_idx = 0
        fake_result.judge_strategy = "llm_judge"
        fake_result.judge_reason = ""
        fake_result.execution = "serial"
        fake_result.early_stopped = False
        fake_result.candidates = []
        fake_result.total_latency_s = 0.1

        with patch.object(orch, "get_task_manager", return_value=mgr):
            with patch(
                "mini_agent.ensemble.run_llm_ensemble", return_value=fake_result
            ) as mock_run, patch(
                "mini_agent.ensemble.classify_task_type", return_value="open_ended"
            ):
                orch.run_ensemble_llm(prompt="hello")

        _, kwargs = mock_run.call_args
        self.assertIs(kwargs["llm_helper"], fake_helper)

    def test_run_ensemble_llm_defaults_to_none_when_no_agent_bound(self):
        """未注册 provider（如 TaskManager 独立运行、无关联 Agent）时，
        应传 llm_helper=None，由 runner.py 内部退化为 from_config(cfg)，
        不影响迁移前的既有行为。"""
        cfg = self._make_cfg_with_ensemble()
        mgr = MagicMock()
        mgr.base_cfg = cfg
        orch.set_current_llm_helper_provider(None)

        fake_result = MagicMock()
        fake_result.final_content = "ok"
        fake_result.chosen_idx = 0
        fake_result.judge_strategy = "llm_judge"
        fake_result.judge_reason = ""
        fake_result.execution = "serial"
        fake_result.early_stopped = False
        fake_result.candidates = []
        fake_result.total_latency_s = 0.1

        with patch.object(orch, "get_task_manager", return_value=mgr):
            with patch(
                "mini_agent.ensemble.run_llm_ensemble", return_value=fake_result
            ) as mock_run, patch(
                "mini_agent.ensemble.classify_task_type", return_value="open_ended"
            ):
                orch.run_ensemble_llm(prompt="hello")

        _, kwargs = mock_run.call_args
        self.assertIsNone(kwargs["llm_helper"])


class TestRunEnsembleSubagentsPropagatesHelper(unittest.TestCase):
    def tearDown(self):
        orch.set_current_llm_helper_provider(None)
        orch.set_active_skills_provider(None)

    def _make_cfg_with_ensemble(self):
        cfg = MagicMock()
        cfg.ensemble.mode = "manual"
        cfg.ensemble.granularity = "both"
        cfg.ensemble.judge_strategy = "llm_judge"
        return cfg

    def test_run_ensemble_subagents_passes_current_helper(self):
        cfg = self._make_cfg_with_ensemble()
        mgr = MagicMock()
        mgr.base_cfg = cfg
        fake_helper = MagicMock(name="fake_llm_helper")
        orch.set_current_llm_helper_provider(lambda: fake_helper)

        fake_result = MagicMock()
        fake_result.final_content = "ok"
        fake_result.chosen_idx = 0
        fake_result.judge_strategy = "llm_judge"
        fake_result.judge_reason = ""
        fake_result.execution = "serial"
        fake_result.early_stopped = False
        fake_result.candidates = []
        fake_result.total_latency_s = 0.1

        with patch.object(orch, "get_task_manager", return_value=mgr):
            with patch(
                "mini_agent.ensemble.run_subagent_ensemble", return_value=fake_result
            ) as mock_run, patch(
                "mini_agent.ensemble.classify_task_type", return_value="open_ended"
            ):
                orch.run_ensemble_subagents(prompt="hello")

        _, kwargs = mock_run.call_args
        self.assertIs(kwargs["llm_helper"], fake_helper)


class TestAgentRegistersLLMHelperProvider(unittest.TestCase):
    """Agent.__init__ 应无条件注册 llm_helper provider（不依赖 skill_loader）。"""

    def tearDown(self):
        orch.set_current_llm_helper_provider(None)

    def test_agent_init_registers_provider_even_without_skill_loader(self):
        import mini_agent.tools.builtin  # noqa: ensure tools registered
        from mini_agent.config import load_config
        from mini_agent.agent import Agent
        from mini_agent.llm.base import LLMClient

        orch.set_current_llm_helper_provider(None)

        cfg = load_config()
        cfg.api_key = "test"
        cfg.stream = False
        mock_client = MagicMock(spec=LLMClient)

        agent = Agent(cfg=cfg, llm_client=mock_client, skill_loader=None)

        registered = orch._get_current_llm_helper()
        self.assertIsNotNone(registered)
        # provider 应指向这个 agent 实例自己的 llm_helper（同一个 client_pool）
        self.assertIs(registered._pool, agent._client_pool)


if __name__ == "__main__":
    unittest.main()
