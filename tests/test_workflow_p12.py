"""
tests/test_workflow_p12.py — [workflow_mechanism_improvement_plan_p12.md] 单元测试

Phase 1 覆盖：
  - condition 表达式求值抛异常（引用字段类型不匹配等）→ StepStatus.NEEDS_FIX，
    而不是 StepStatus.SKIPPED
  - condition 表达式正常求值为 False（无异常）→ 仍然是 StepStatus.SKIPPED，
    与 Phase 1 之前的行为保持一致，防止把两种情况的处理路径混淆
  - ConditionEvalError 携带 condition 原文和原始异常，便于诊断
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from unittest.mock import MagicMock, patch

from mini_agent.workflow.schema import (
    WorkflowDef, WorkflowStep, StepResult, StepStatus, ConditionEvalError,
)
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow import executors as wf_executors


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


class TestConditionEvalErrorClassification(unittest.TestCase):
    """Phase 1: _eval_condition 求值异常应包装成 ConditionEvalError 上抛。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_eval_condition_raises_condition_eval_error_on_exception(self):
        # analyze.output 是字符串，.startswith 存在但传参类型错误 → TypeError
        step_results = {
            "analyze": StepResult(step_id="analyze", status=StepStatus.DONE, output="hello"),
        }
        with self.assertRaises(ConditionEvalError) as cm:
            self.runner._eval_condition("analyze.output.startswith(123)", step_results, {})
        err = cm.exception
        self.assertEqual(err.condition, "analyze.output.startswith(123)")
        self.assertIsInstance(err.original_exception, Exception)

    def test_eval_condition_normal_false_does_not_raise(self):
        step_results = {
            "analyze": StepResult(step_id="analyze", status=StepStatus.DONE, output="hello", score=0.3),
        }
        # 语法/引用都合法，只是求值结果为 False，不应该抛异常
        self.assertFalse(
            self.runner._eval_condition("analyze.score >= 60", step_results, {})
        )

    def test_eval_condition_unknown_step_raises(self):
        with self.assertRaises(ConditionEvalError):
            self.runner._eval_condition("nonexistent.passed", {}, {})


class TestRunOneStepConditionStatus(unittest.TestCase):
    """Phase 1: _run_one_step 里 condition 异常 → NEEDS_FIX；False → SKIPPED。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)
        self.lock = threading.Lock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_condition_eval_exception_marks_needs_fix(self):
        step = _step("report", condition="analyze.output.startswith(123)",
                      depends_on=["analyze"])
        step_results = {
            "analyze": StepResult(step_id="analyze", status=StepStatus.DONE, output="hello"),
        }
        self.runner._run_one_step(step, step_results, {}, self.lock)
        sr = step_results["report"]
        self.assertEqual(sr.status, StepStatus.NEEDS_FIX)
        self.assertIsNotNone(sr.error)
        self.assertTrue(sr.error_type)

    def test_condition_false_still_skipped(self):
        step = _step("report", condition="analyze.score >= 60", depends_on=["analyze"])
        step_results = {
            "analyze": StepResult(step_id="analyze", status=StepStatus.DONE, output="hello", score=0.3),
        }
        self.runner._run_one_step(step, step_results, {}, self.lock)
        sr = step_results["report"]
        self.assertEqual(sr.status, StepStatus.SKIPPED)
        self.assertIsNone(sr.error)


class TestToolCallArgsPlaceholderResolution(unittest.TestCase):
    """Phase 2: tool_call 的 tool_args 支持 {step_id.output} 等占位符。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)
        self.runner._current_step_results = {
            "search": StepResult(step_id="search", status=StepStatus.DONE, output="hello world"),
        }
        self.runner._current_inputs = {"env": "prod"}

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fake_registry(self, captured: dict):
        tool_def = MagicMock()
        tool_def.fn = lambda query: query  # 供 inspect.signature 使用
        registry = MagicMock()
        registry.get.return_value = tool_def

        def _call(name, tool_input):
            captured["tool_input"] = tool_input
            return "ok"

        registry.call.side_effect = _call
        return registry

    def test_tool_args_placeholder_resolved_before_call(self):
        step = _step(
            "s", type="tool_call", tool_name="fake_tool",
            tool_args={"query": "{search.output}", "limit": 5},
            depends_on=["search"],
        )
        captured: dict = {}
        registry = self._fake_registry(captured)
        with patch("mini_agent.tools.get_default_registry", return_value=registry):
            output = wf_executors.ToolCallStepExecutor().execute(self.runner, step, "unused prompt")
        self.assertEqual(output, "ok")
        self.assertEqual(captured["tool_input"], {"query": "hello world", "limit": 5})

    def test_tool_args_without_placeholder_unchanged(self):
        step = _step("s", type="tool_call", tool_name="fake_tool",
                      tool_args={"query": "literal text", "limit": 3})
        captured: dict = {}
        registry = self._fake_registry(captured)
        with patch("mini_agent.tools.get_default_registry", return_value=registry):
            wf_executors.ToolCallStepExecutor().execute(self.runner, step, "unused prompt")
        self.assertEqual(captured["tool_input"], {"query": "literal text", "limit": 3})


class TestToolArgsPlaceholderValidation(unittest.TestCase):
    """Phase 2: WorkflowDef.validate() 扫描 tool_args 里的占位符引用。"""

    def test_tool_args_referencing_unknown_step_rejected(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("s", type="tool_call", tool_name="fake_tool",
                  tool_args={"query": "{nope.output}"}),
        ])
        errors = wf.validate(check_placeholders=True)
        self.assertTrue(any("nope" in e for e in errors))

    def test_tool_args_referencing_known_step_ok(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("search"),
            _step("s", type="tool_call", tool_name="fake_tool",
                  tool_args={"query": "{search.output}"}, depends_on=["search"]),
        ])
        errors = wf.validate(check_placeholders=True)
        self.assertEqual(errors, [])

    def test_tool_args_missing_depends_on_flagged(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("search"),
            _step("s", type="tool_call", tool_name="fake_tool",
                  tool_args={"query": "{search.output}"}),  # 没声明 depends_on
        ])
        errors = wf.validate(check_placeholders=True, check_placeholder_depends_on=True)
        self.assertTrue(any("depends_on" in e for e in errors))


class TestResultFileJsonPathPlaceholder(unittest.TestCase):
    """Phase 3: {step_id.result_file:a.b[0].c} 从结果文件里取字段值。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)

        import json
        self.result_path = str(Path(self.tmpdir.name) / "search_result.json")
        with open(self.result_path, "w", encoding="utf-8") as f:
            json.dump({"questions": [{"title": "第一题"}, {"title": "第二题"}],
                       "meta": {"count": 2}}, f)
        self.runner._step_result_file_paths = {"search": self.result_path}

        self.step_results = {
            "search": StepResult(step_id="search", status=StepStatus.DONE,
                                  output="done", result_file=self.result_path),
        }

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_extracts_nested_field_via_json_path(self):
        text = self.runner._resolve_prompt(
            "标题是：{search.result_file:questions[0].title}",
            self.step_results, {},
        )
        self.assertEqual(text, "标题是：第一题")

    def test_extracts_simple_nested_field(self):
        text = self.runner._resolve_prompt(
            "共 {search.result_file:meta.count} 条",
            self.step_results, {},
        )
        self.assertEqual(text, "共 2 条")

    def test_plain_result_file_still_returns_path(self):
        text = self.runner._resolve_prompt(
            "{search.result_file}", self.step_results, {},
        )
        self.assertEqual(text, self.result_path)

    def test_missing_path_falls_back_to_original_placeholder(self):
        placeholder = "{search.result_file:questions[99].title}"
        text = self.runner._resolve_prompt(placeholder, self.step_results, {})
        self.assertEqual(text, placeholder)

    def test_unparseable_json_falls_back_to_original_placeholder(self):
        bad_path = str(Path(self.tmpdir.name) / "bad.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("not json")
        self.runner._step_result_file_paths["bad_step"] = bad_path
        step_results = dict(self.step_results)
        step_results["bad_step"] = StepResult(step_id="bad_step", status=StepStatus.DONE,
                                               result_file=bad_path)
        placeholder = "{bad_step.result_file:a.b}"
        text = self.runner._resolve_prompt(placeholder, step_results, {})
        self.assertEqual(text, placeholder)


class TestResultFileJsonPathValidation(unittest.TestCase):
    """Phase 3: WorkflowDef.validate() 对 result_file:<path> 语法做静态检查。"""

    def test_valid_json_path_syntax_ok(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("search"),
            _step("s", prompt="{search.result_file:questions[0].title}",
                  depends_on=["search"]),
        ])
        errors = wf.validate(check_placeholders=True)
        self.assertEqual(errors, [])

    def test_invalid_json_path_syntax_rejected(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("search"),
            _step("s", prompt="{search.result_file:..bad[[}",
                  depends_on=["search"]),
        ])
        errors = wf.validate(check_placeholders=True)
        self.assertTrue(any("语法不合法" in e for e in errors))

    def test_json_path_missing_depends_on_flagged(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("search"),
            _step("s", prompt="{search.result_file:questions[0].title}"),
        ])
        errors = wf.validate(check_placeholders=True, check_placeholder_depends_on=True)
        self.assertTrue(any("depends_on" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
