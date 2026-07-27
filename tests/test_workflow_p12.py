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

from mini_agent.workflow.schema import (
    WorkflowStep, StepResult, StepStatus, ConditionEvalError,
)
from mini_agent.workflow.runner import WorkflowRunner


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


if __name__ == "__main__":
    unittest.main()
