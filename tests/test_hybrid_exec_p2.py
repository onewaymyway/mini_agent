"""
tests/test_hybrid_exec_p2.py — hybrid_exec P2 单元测试

覆盖：
  1. AgentExplorer / AgentRepairer / FallbackExecutor.agent_direct：
     mock 掉 _agent.run_agent_prompt（不发起真实 Agent/LLM 调用），验证
     prompt 拼装、markdown 代码块防御性剥离、agent_fs_write_enabled 透传。
  2. workflow_integration._build_task_spec：从 step.params + 上游依赖输出
     组装 TaskSpec 的各种取值/合并逻辑。
  3. HybridStepExecutor.validate_step：task_id 必填、allow_tiers 合法性校验。
  4. HybridStepExecutor.execute：用 fake runner + mock HybridExecutor，验证
     成功/失败两条路径下 step 输出与异常抛出行为，不依赖真实子进程/LLM。
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

from mini_agent.hybrid_exec.explorer import AgentExplorer
from mini_agent.hybrid_exec.fallback import FallbackExecutor
from mini_agent.hybrid_exec.repairer import AgentRepairer
from mini_agent.hybrid_exec.runner import RunnerAppConfig
from mini_agent.hybrid_exec.spec import ExecutionResult, ExecutionTier, ScriptOutcome, TaskSpec
from mini_agent.hybrid_exec.workflow_integration import (
    HybridStepExecutor,
    _build_task_spec,
)
from mini_agent.workflow.schema import WorkflowStep


def _app_cfg() -> RunnerAppConfig:
    return RunnerAppConfig(project_root="/tmp/fake_project")


def _task(**kw) -> TaskSpec:
    kw.setdefault("task_id", "t1")
    kw.setdefault("description", "demo task")
    kw.setdefault("input_data", {"x": 1})
    return TaskSpec(**kw)


# ---------------------------------------------------------------------------
# Agent 分支（mock 掉真实 Agent 调用）
# ---------------------------------------------------------------------------


class TestAgentBranches(unittest.TestCase):
    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_agent_explorer_returns_stripped_code(self, mock_run):
        mock_run.return_value = "```python\ndef run(ctx):\n    return 'ok'\n```"
        explorer = AgentExplorer(_app_cfg())
        code = explorer.explore(_task())
        self.assertNotIn("```", code)
        self.assertIn("def run(ctx):", code)
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("session_label"), "explore")

    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_agent_repairer_prompt_includes_traceback(self, mock_run):
        mock_run.return_value = "def run(ctx):\n    return 'fixed'\n"
        repairer = AgentRepairer(_app_cfg())
        outcome = ScriptOutcome(ok=False, error="boom", error_type="ValueError", traceback="tb here")
        code = repairer.repair(_task(), "def run(ctx): raise ValueError('x')", outcome)
        self.assertIn("fixed", code)
        args, kwargs = mock_run.call_args
        prompt_arg = args[2]  # run_agent_prompt(app_cfg, task, prompt, ...)
        self.assertIn("tb here", prompt_arg)
        self.assertIn("ValueError", prompt_arg)

    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_fallback_agent_direct(self, mock_run):
        mock_run.return_value = "final answer"
        fb = FallbackExecutor(_app_cfg())
        result = fb.agent_direct(_task())
        self.assertEqual(result, "final answer")
        _, kwargs = mock_run.call_args
        self.assertEqual(kwargs.get("session_label"), "fallback")

    @patch("mini_agent.hybrid_exec._agent.run_agent_prompt")
    def test_agent_fs_write_enabled_is_forwarded_via_task(self, mock_run):
        mock_run.return_value = "code"
        explorer = AgentExplorer(_app_cfg())
        task = _task(agent_fs_write_enabled=True)
        explorer.explore(task)
        args, _ = mock_run.call_args
        # run_agent_prompt(app_cfg, task, prompt, ...) —— task 对象本身携带
        # agent_fs_write_enabled，由 _agent.run_agent_prompt 内部换算成
        # sandbox 参数（该函数本身在此测试里被 mock，不重复验证 sandbox 换算，
        # 只验证 task 被原样透传）。
        self.assertIs(args[1], task)
        self.assertTrue(args[1].agent_fs_write_enabled)


# ---------------------------------------------------------------------------
# workflow_integration._build_task_spec
# ---------------------------------------------------------------------------


def _step(id_="s1", **kw) -> WorkflowStep:
    kw.setdefault("prompt", "")
    kw.setdefault("depends_on", [])
    kw.setdefault("type", "hybrid_step")
    return WorkflowStep(id=id_, name=id_, **kw)


class TestBuildTaskSpec(unittest.TestCase):
    def test_requires_task_id(self):
        step = _step(params={"description": "demo"})
        with self.assertRaises(ValueError):
            _build_task_spec(step, {})

    def test_basic_fields(self):
        step = _step(params={"task_id": "t1", "description": "demo desc"})
        task = _build_task_spec(step, {})
        self.assertEqual(task.task_id, "t1")
        self.assertEqual(task.description, "demo desc")
        self.assertEqual(task.max_script_repair_attempts, 2)  # 默认值
        self.assertEqual(task.allow_tiers, (ExecutionTier.SCRIPT, ExecutionTier.LLM, ExecutionTier.AGENT))

    def test_description_fallback_to_prompt(self):
        step = _step(params={"task_id": "t1"}, prompt="用 prompt 当描述")
        task = _build_task_spec(step, {})
        self.assertEqual(task.description, "用 prompt 当描述")

    def test_upstream_and_literal_input_merge(self):
        step = _step(
            params={"task_id": "t1", "description": "d", "input": {"hint": "只要中文人名"}},
        )
        task = _build_task_spec(step, {"fetch_text": {"text": "张三来自清华"}})
        self.assertEqual(task.input_data["upstream"], {"fetch_text": {"text": "张三来自清华"}})
        self.assertEqual(task.input_data["hint"], "只要中文人名")

    def test_allow_tiers_custom(self):
        step = _step(params={"task_id": "t1", "description": "d", "allow_tiers": ["script", "llm"]})
        task = _build_task_spec(step, {})
        self.assertEqual(task.allow_tiers, (ExecutionTier.SCRIPT, ExecutionTier.LLM))

    def test_result_required_keys_builds_validator(self):
        step = _step(params={"task_id": "t1", "description": "d", "result_required_keys": ["entities"]})
        task = _build_task_spec(step, {})
        self.assertIsNotNone(task.output_validator)
        ok, _ = task.run_validator({"entities": ["张三"]})
        self.assertTrue(ok)
        ok, reason = task.run_validator({"other": 1})
        self.assertFalse(ok)
        ok, reason = task.run_validator("not a dict")
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# HybridStepExecutor
# ---------------------------------------------------------------------------


class TestHybridStepExecutorValidate(unittest.TestCase):
    def test_missing_task_id(self):
        executor = HybridStepExecutor()
        errors = executor.validate_step(_step(params={}))
        self.assertTrue(any("task_id" in e for e in errors))

    def test_invalid_allow_tiers(self):
        executor = HybridStepExecutor()
        errors = executor.validate_step(
            _step(params={"task_id": "t1", "allow_tiers": ["script", "bogus"]})
        )
        self.assertTrue(any("allow_tiers" in e for e in errors))

    def test_valid_step_no_errors(self):
        executor = HybridStepExecutor()
        errors = executor.validate_step(_step(params={"task_id": "t1", "description": "d"}))
        self.assertEqual(errors, [])


@dataclass
class _FakeStepResult:
    output: str = ""


@dataclass
class _FakeWfSession:
    workflow_session_id: str = "wf_123"


@dataclass
class _FakeCfg:
    project_root: str = "/tmp/fake_project"
    sandbox: bool = True
    model: str = None
    llm_provider: str = None
    llm_base_url: str = None
    debug_llm: bool = False
    debug_llm_console: bool = False
    skills_dir: str = None
    api_key: str = None


class _FakePaths:
    def __init__(self, base: Path):
        self.base = base

    def workflow_session_dir(self, wf_session_id):
        return self.base / "sessions" / wf_session_id

    def ensure_workflow_session_output_dir(self, wf_session_id):
        d = self.base / "sessions" / wf_session_id / "output"
        d.mkdir(parents=True, exist_ok=True)
        return d


class _FakeRunner:
    def __init__(self, tmp_path: Path):
        self._cfg = _FakeCfg(project_root=str(tmp_path))
        self._current_step_results = {"fetch": _FakeStepResult(output=json.dumps({"text": "hi"}))}
        self._current_paths = _FakePaths(tmp_path)
        self._current_wf_session = _FakeWfSession()


class TestHybridStepExecutorExecute(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.runner = _FakeRunner(self.tmp_path)

    def tearDown(self):
        self._tmp.cleanup()

    @patch("mini_agent.hybrid_exec.workflow_integration.HybridExecutor")
    def test_execute_success_returns_output(self, mock_executor_cls):
        mock_instance = MagicMock()
        mock_instance.run.return_value = ExecutionResult(
            ok=True, output={"entities": ["张三"]}, tier_used=ExecutionTier.SCRIPT, script_version=1,
        )
        mock_executor_cls.return_value = mock_instance

        step = _step(id_="extract", params={"task_id": "t1", "description": "d"}, depends_on=["fetch"])
        executor = HybridStepExecutor()
        output_text = executor.execute(self.runner, step, prompt="unused")

        parsed = json.loads(output_text)
        self.assertEqual(parsed, {"entities": ["张三"]})
        # 验证 upstream 数据确实从 _current_step_results 里取出并解析为 JSON
        task_arg = mock_instance.run.call_args[0][0]
        self.assertEqual(task_arg.input_data["upstream"]["fetch"], {"text": "hi"})

        # 决策轨迹应落盘
        trace_path = self.tmp_path / "sessions" / "wf_123" / "output" / "hybrid_step_extract_trace.json"
        self.assertTrue(trace_path.exists())

    @patch("mini_agent.hybrid_exec.workflow_integration.HybridExecutor")
    def test_execute_failure_raises(self, mock_executor_cls):
        mock_instance = MagicMock()
        mock_instance.run.return_value = ExecutionResult(
            ok=False, output=None, tier_used=ExecutionTier.AGENT, script_version=None,
        )
        mock_executor_cls.return_value = mock_instance

        step = _step(id_="extract", params={"task_id": "t1", "description": "d"})
        executor = HybridStepExecutor()
        with self.assertRaises(RuntimeError):
            executor.execute(self.runner, step, prompt="unused")


if __name__ == "__main__":
    unittest.main()
