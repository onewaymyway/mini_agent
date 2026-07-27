"""
tests/test_workflow_p13.py — [workflow_mechanism_improvement_plan_p13.md] 单元测试

Phase 1 覆盖 foreach/map step 类型：
  - 字面量列表 + 串行执行 + 聚合成功
  - items 是占位符（引用另一个 step 的 result_file 字段）
  - 并发执行（foreach_max_concurrency > 1）
  - 单元素失败但 foreach_stop_on_error=False 时不影响整体
  - foreach_stop_on_error=True 时单元素失败导致整体失败
  - 嵌套 foreach 被 validate() 拒绝
  - foreach 缺少 items/foreach_step 时 validate() 报错

Phase 2 覆盖 wait step 类型：
  - 正常等待后返回
  - 等待期间收到 cancel 请求提前退出并报错
  - wait_seconds 缺失/非正数时 validate() 报错
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow import executors as wf_executors
from mini_agent.workflow.registry import ControlState


def _step(id_, **kw) -> WorkflowStep:
    kw.setdefault("prompt", f"do {id_}")
    kw.setdefault("depends_on", [])
    return WorkflowStep(id=id_, name=id_, **kw)


@dataclass
class _FakeWorkflowConfig:
    parallel_enabled: bool = True
    max_parallel: int = 4
    hooks_enabled: bool = False
    debug_log_enabled: bool = False


@dataclass
class _FakeCfg:
    project_root: str = "/tmp"
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)


class TestForeachExecution(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)
        self.runner._current_step_results = {}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_literal_items_serial_execution_aggregates(self):
        step = _step(
            "batch", type="foreach",
            items=["a", "b", "c"],
            foreach_step={"type": "tool_call", "tool_name": "echo_tool", "prompt": "{item}"},
        )
        captured_inputs = []

        def _fake_tool_call_execute(self_, runner, inner_step, prompt):
            captured_inputs.append(inner_step.prompt)
            return f"echo:{inner_step.prompt}"

        orig = wf_executors.ToolCallStepExecutor.execute
        wf_executors.ToolCallStepExecutor.execute = _fake_tool_call_execute
        try:
            output = wf_executors.ForeachStepExecutor().execute(self.runner, step, "")
        finally:
            wf_executors.ToolCallStepExecutor.execute = orig

        data = json.loads(output)
        self.assertEqual(len(data), 3)
        self.assertEqual([d["item_index"] for d in data], [0, 1, 2])
        self.assertEqual([d["output"] for d in data], ["echo:a", "echo:b", "echo:c"])
        self.assertEqual(captured_inputs, ["a", "b", "c"])

    def test_items_from_result_file_placeholder(self):
        result_path = str(Path(self.tmpdir.name) / "search.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({"questions": ["q1", "q2"]}, f)
        self.runner._step_result_file_paths = {"search": result_path}
        self.runner._current_step_results = {
            "search": StepResult(step_id="search", status=StepStatus.DONE, result_file=result_path),
        }
        step = _step(
            "batch", type="foreach",
            items="{search.result_file:questions}",
            foreach_step={"type": "tool_call", "tool_name": "echo_tool", "prompt": "{item}"},
            depends_on=["search"],
        )

        def _fake_tool_call_execute(self_, runner, inner_step, prompt):
            return inner_step.prompt.upper()

        orig = wf_executors.ToolCallStepExecutor.execute
        wf_executors.ToolCallStepExecutor.execute = _fake_tool_call_execute
        try:
            output = wf_executors.ForeachStepExecutor().execute(self.runner, step, "")
        finally:
            wf_executors.ToolCallStepExecutor.execute = orig

        data = json.loads(output)
        self.assertEqual([d["output"] for d in data], ["Q1", "Q2"])

    def test_concurrent_execution_preserves_order(self):
        step = _step(
            "batch", type="foreach",
            items=list(range(5)),
            foreach_step={"type": "tool_call", "tool_name": "echo_tool", "prompt": "{item_index}"},
            foreach_max_concurrency=3,
        )

        def _fake_tool_call_execute(self_, runner, inner_step, prompt):
            time.sleep(0.01)
            return inner_step.prompt

        orig = wf_executors.ToolCallStepExecutor.execute
        wf_executors.ToolCallStepExecutor.execute = _fake_tool_call_execute
        try:
            output = wf_executors.ForeachStepExecutor().execute(self.runner, step, "")
        finally:
            wf_executors.ToolCallStepExecutor.execute = orig

        data = json.loads(output)
        self.assertEqual([d["item_index"] for d in data], [0, 1, 2, 3, 4])
        self.assertEqual([d["output"] for d in data], ["0", "1", "2", "3", "4"])

    def test_single_item_failure_does_not_abort_by_default(self):
        step = _step(
            "batch", type="foreach",
            items=["ok", "boom", "ok2"],
            foreach_step={"type": "tool_call", "tool_name": "echo_tool", "prompt": "{item}"},
        )

        def _fake_tool_call_execute(self_, runner, inner_step, prompt):
            if inner_step.prompt == "boom":
                raise RuntimeError("kaboom")
            return inner_step.prompt

        orig = wf_executors.ToolCallStepExecutor.execute
        wf_executors.ToolCallStepExecutor.execute = _fake_tool_call_execute
        try:
            output = wf_executors.ForeachStepExecutor().execute(self.runner, step, "")
        finally:
            wf_executors.ToolCallStepExecutor.execute = orig

        data = json.loads(output)
        self.assertEqual(data[0]["output"], "ok")
        self.assertIn("kaboom", data[1]["error"])
        self.assertEqual(data[2]["output"], "ok2")

    def test_stop_on_error_true_raises(self):
        step = _step(
            "batch", type="foreach",
            items=["ok", "boom"],
            foreach_step={"type": "tool_call", "tool_name": "echo_tool", "prompt": "{item}"},
            foreach_stop_on_error=True,
        )

        def _fake_tool_call_execute(self_, runner, inner_step, prompt):
            if inner_step.prompt == "boom":
                raise RuntimeError("kaboom")
            return inner_step.prompt

        orig = wf_executors.ToolCallStepExecutor.execute
        wf_executors.ToolCallStepExecutor.execute = _fake_tool_call_execute
        try:
            with self.assertRaises(RuntimeError):
                wf_executors.ForeachStepExecutor().execute(self.runner, step, "")
        finally:
            wf_executors.ToolCallStepExecutor.execute = orig


class TestForeachValidation(unittest.TestCase):
    def test_missing_items_and_foreach_step_rejected(self):
        wf = WorkflowDef(name="wf", steps=[_step("batch", type="foreach")])
        errors = wf.validate()
        self.assertTrue(any("items" in e for e in errors))
        self.assertTrue(any("foreach_step" in e for e in errors))

    def test_nested_foreach_rejected(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("batch", type="foreach", items=[1, 2],
                  foreach_step={"type": "foreach", "prompt": "x"}),
        ])
        errors = wf.validate()
        self.assertTrue(any("不能是 foreach" in e for e in errors))

    def test_valid_foreach_step_passes(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("batch", type="foreach", items=[1, 2],
                  foreach_step={"type": "tool_call", "tool_name": "echo_tool", "prompt": "{item}"}),
        ])
        errors = wf.validate()
        self.assertEqual(errors, [])


class TestWaitExecution(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_wait_returns_after_duration(self):
        step = _step("pause", type="wait", wait_seconds=0.05, prompt="")
        t0 = time.monotonic()
        output = wf_executors.WaitStepExecutor().execute(self.runner, step, "")
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.04)
        self.assertIn("完成", output)

    def test_wait_cancel_raises_and_exits_early(self):
        control = ControlState()
        self.runner._current_control = control
        step = _step("pause", type="wait", wait_seconds=5, prompt="")

        def _cancel_soon():
            time.sleep(0.05)
            control.request_cancel()

        threading.Thread(target=_cancel_soon, daemon=True).start()
        t0 = time.monotonic()
        with self.assertRaises(RuntimeError):
            wf_executors.WaitStepExecutor().execute(self.runner, step, "")
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 3.0)  # 远小于 5 秒，证明是提前退出而不是等满


class TestWaitValidation(unittest.TestCase):
    def test_missing_wait_seconds_rejected(self):
        wf = WorkflowDef(name="wf", steps=[_step("pause", type="wait", prompt="")])
        errors = wf.validate()
        self.assertTrue(any("wait_seconds" in e for e in errors))

    def test_negative_wait_seconds_rejected(self):
        wf = WorkflowDef(name="wf", steps=[_step("pause", type="wait", wait_seconds=-1, prompt="")])
        errors = wf.validate()
        self.assertTrue(any("wait_seconds" in e for e in errors))

    def test_valid_wait_seconds_passes(self):
        wf = WorkflowDef(name="wf", steps=[_step("pause", type="wait", wait_seconds=1.5, prompt="")])
        errors = wf.validate()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
