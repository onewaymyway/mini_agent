"""回归测试：default_executor() 独立执行路径下，llm_explorer/llm_repairer/
fallback 必须共用同一个 LLMHelper（进而共用同一条 LLMClientPool），而不是
各自在每次 .ask() 时惰性重建一条新的——后者会丢失多 key 轮转/cooldown 状态，
与主 Agent（agent/core.py 只在启动时 LLMClientPool.from_config(cfg) 一次）
的行为不一致。见 next_doc/hybrid_exec_design_plan.md §11 "P0 bug" 说明。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mini_agent.hybrid_exec.executor import default_executor


class TestDefaultExecutorSharesLLMHelper(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_llm_helper_called_exactly_once_when_llm_not_passed(self):
        """不传 llm= 时，default_executor() 应该只在构造期间调用一次
        build_llm_helper()，并把结果对象共享给 llm_explorer/llm_repairer/
        fallback 三者；而不是让三者各自持有 llm=None、在真正 .ask() 时才
        各自惰性重建（那样每次探索/修复/兜底都会是一条全新的
        LLMClientPool，多 key 轮转与 cooldown 状态在调用之间完全丢失）。
        """
        sentinel_helper = mock.Mock(name="shared_llm_helper")
        with mock.patch(
            "mini_agent.hybrid_exec._llm.build_llm_helper", return_value=sentinel_helper
        ) as mocked:
            executor = default_executor(self.project_root)

        mocked.assert_called_once()
        self.assertIs(executor.llm_explorer._llm, sentinel_helper)
        self.assertIs(executor.llm_repairer._llm, sentinel_helper)
        self.assertIs(executor.fallback._llm, sentinel_helper)

    def test_passed_llm_is_reused_verbatim_without_calling_build_llm_helper(self):
        """传入 llm= 时（嵌入 workflow 场景），不应该触碰 build_llm_helper /
        load_config() 分毫——直接原样复用调用方传入的对象。"""
        fake_llm = mock.Mock(name="external_llm")
        with mock.patch("mini_agent.hybrid_exec._llm.build_llm_helper") as mocked:
            executor = default_executor(self.project_root, llm=fake_llm)

        mocked.assert_not_called()
        self.assertIs(executor.llm_explorer._llm, fake_llm)
        self.assertIs(executor.llm_repairer._llm, fake_llm)
        self.assertIs(executor.fallback._llm, fake_llm)

    def test_build_llm_helper_failure_degrades_to_none_without_raising(self):
        """provider 未配置好（build_llm_helper 抛异常）时，default_executor()
        本身不应该崩——退回 llm=None，交给三者各自在真正调用时报出更明确的
        错误（与 workflow_integration.py::HybridStepExecutor.execute 的
        容错方式保持一致）。"""
        with mock.patch(
            "mini_agent.hybrid_exec._llm.build_llm_helper", side_effect=RuntimeError("no providers.json")
        ):
            executor = default_executor(self.project_root)

        self.assertIsNone(executor.llm_explorer._llm)
        self.assertIsNone(executor.llm_repairer._llm)
        self.assertIsNone(executor.fallback._llm)


if __name__ == "__main__":
    unittest.main()
