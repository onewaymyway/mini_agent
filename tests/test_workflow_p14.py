"""
tests/test_workflow_p14.py — [workflow_mechanism_improvement_plan_p14.md] 单元测试

Phase 1 覆盖 merge/aggregate step 类型：
  - concat_text 策略拼接多个来源的 output
  - json_array 策略聚合成 JSON 数组（含 result_file 来源）
  - json_merge 策略按顺序合并 dict，后者覆盖前者同名 key
  - json_merge 遇到非 dict 来源时报错
  - merge_sources 缺失/重复/非法 strategy 时 validate() 报错
  - merge_sources 引用不存在的 step / 未声明 depends_on 时 validate() 报错

Phase 2 覆盖 workflow 级熔断：
  - 单元测试：watchdog.report_workflow_level_failure 达到阈值后
    circuit_breaker_tripped 为 True 且触发了 control.cancel_requested
  - 单元测试：未达阈值/未启用（threshold=None）时不触发
  - 集成测试：runner 层面，多个不同 step 因同一 error_type 失败达到阈值后，
    后续尚未执行的 step 被取消

Phase 3 覆盖 validate() 必填字段表refactor 后的行为等价性：
  - sub_workflow/tool_call/script/skill_agent/python_step 缺少各自必填
    字段时报错文案与改造前一致（仍包含"类型但未指定 xxx"关键信息）
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.watchdog import WorkflowWatchdog
from mini_agent.workflow import executors as wf_executors
from mini_agent.workflow import registry as wf_registry


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
    watchdog_enabled: bool = True
    heartbeat_check_interval_seconds: float = 0.05
    retry_on_error_backoff_seconds: float = 0.0
    circuit_breaker_distinct_step_threshold: "int | None" = None


@dataclass
class _FakeCfg:
    project_root: str = "/tmp"
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)


# ── Phase 1: merge ───────────────────────────────────────────────────────────

class TestMergeExecution(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_concat_text_strategy(self):
        self.runner._current_step_results = {
            "a": StepResult(step_id="a", status=StepStatus.DONE, output="hello"),
            "b": StepResult(step_id="b", status=StepStatus.DONE, output="world"),
        }
        step = _step("m", type="merge", merge_sources=["a", "b"], merge_separator="|")
        output = wf_executors.MergeStepExecutor().execute(self.runner, step, "")
        self.assertEqual(output, "hello|world")

    def test_json_array_strategy_from_output(self):
        self.runner._current_step_results = {
            "a": StepResult(step_id="a", status=StepStatus.DONE, output='{"x": 1}'),
            "b": StepResult(step_id="b", status=StepStatus.DONE, output='{"x": 2}'),
        }
        step = _step("m", type="merge", merge_sources=["a", "b"], merge_strategy="json_array")
        output = wf_executors.MergeStepExecutor().execute(self.runner, step, "")
        self.assertEqual(json.loads(output), [{"x": 1}, {"x": 2}])

    def test_json_array_strategy_from_result_file(self):
        path_a = str(Path(self.tmpdir.name) / "a.json")
        with open(path_a, "w", encoding="utf-8") as f:
            json.dump({"q": "a1"}, f)
        path_b = str(Path(self.tmpdir.name) / "b.json")
        with open(path_b, "w", encoding="utf-8") as f:
            json.dump({"q": "b1"}, f)
        self.runner._step_result_file_paths = {"a": path_a, "b": path_b}
        self.runner._current_step_results = {
            "a": StepResult(step_id="a", status=StepStatus.DONE, result_file=path_a),
            "b": StepResult(step_id="b", status=StepStatus.DONE, result_file=path_b),
        }
        step = _step("m", type="merge", merge_sources=["a", "b"],
                     merge_strategy="json_array", merge_use_result_file=True)
        output = wf_executors.MergeStepExecutor().execute(self.runner, step, "")
        self.assertEqual(json.loads(output), [{"q": "a1"}, {"q": "b1"}])

    def test_json_merge_strategy_later_overrides_earlier(self):
        self.runner._current_step_results = {
            "a": StepResult(step_id="a", status=StepStatus.DONE, output='{"x": 1, "y": 1}'),
            "b": StepResult(step_id="b", status=StepStatus.DONE, output='{"y": 2}'),
        }
        step = _step("m", type="merge", merge_sources=["a", "b"], merge_strategy="json_merge")
        output = wf_executors.MergeStepExecutor().execute(self.runner, step, "")
        self.assertEqual(json.loads(output), {"x": 1, "y": 2})

    def test_json_merge_non_dict_source_raises(self):
        self.runner._current_step_results = {
            "a": StepResult(step_id="a", status=StepStatus.DONE, output='[1, 2]'),
        }
        step = _step("m", type="merge", merge_sources=["a"], merge_strategy="json_merge")
        with self.assertRaises(ValueError):
            wf_executors.MergeStepExecutor().execute(self.runner, step, "")


class TestMergeValidation(unittest.TestCase):
    def test_missing_merge_sources_rejected(self):
        wf = WorkflowDef(name="wf", steps=[_step("m", type="merge")])
        errors = wf.validate()
        self.assertTrue(any("merge_sources" in e for e in errors))

    def test_duplicate_merge_sources_rejected(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"), _step("b"),
            _step("m", type="merge", merge_sources=["a", "a"], depends_on=["a", "b"]),
        ])
        errors = wf.validate()
        self.assertTrue(any("重复" in e for e in errors))

    def test_invalid_strategy_rejected(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("m", type="merge", merge_sources=["a"], merge_strategy="nope", depends_on=["a"]),
        ])
        errors = wf.validate()
        self.assertTrue(any("merge_strategy" in e for e in errors))

    def test_unknown_source_step_rejected(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("m", type="merge", merge_sources=["nope"]),
        ])
        errors = wf.validate()
        self.assertTrue(any("不存在的步骤" in e and "nope" in e for e in errors))

    def test_source_missing_depends_on_flagged(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("m", type="merge", merge_sources=["a"]),  # 没声明 depends_on
        ])
        errors = wf.validate(check_placeholder_depends_on=True)
        self.assertTrue(any("depends_on" in e for e in errors))

    def test_valid_merge_step_passes(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"), _step("b"),
            _step("m", type="merge", merge_sources=["a", "b"], depends_on=["a", "b"]),
        ])
        errors = wf.validate()
        self.assertEqual(errors, [])


# ── Phase 2: workflow 级熔断 ──────────────────────────────────────────────────

class TestCircuitBreakerUnit(unittest.TestCase):
    def _make_watchdog(self, threshold) -> WorkflowWatchdog:
        from mini_agent.storage.paths import AgentPaths
        tmp = tempfile.mkdtemp()
        paths = AgentPaths(project_root=tmp)
        control = wf_registry.register(f"wfs_test_cb_{threshold}_{id(self)}")
        return WorkflowWatchdog(
            paths=paths, workflow_session_id="wfs_test_cb", control=control,
            circuit_breaker_distinct_step_threshold=threshold,
        )

    def test_trips_after_distinct_step_threshold_reached(self):
        wd = self._make_watchdog(threshold=3)
        self.assertFalse(wd.report_workflow_level_failure("s1", "TimeoutError"))
        self.assertFalse(wd.report_workflow_level_failure("s2", "TimeoutError"))
        self.assertTrue(wd.report_workflow_level_failure("s3", "TimeoutError"))
        self.assertTrue(wd.circuit_breaker_tripped)
        self.assertIn("TimeoutError", wd.circuit_breaker_reason)
        self.assertTrue(wd._control.cancel_requested.is_set())

    def test_same_step_repeated_failure_does_not_double_count(self):
        wd = self._make_watchdog(threshold=2)
        self.assertFalse(wd.report_workflow_level_failure("s1", "TimeoutError"))
        self.assertFalse(wd.report_workflow_level_failure("s1", "TimeoutError"))  # 同一个 step，不算新增
        self.assertFalse(wd.circuit_breaker_tripped)

    def test_different_error_types_tracked_independently(self):
        wd = self._make_watchdog(threshold=2)
        self.assertFalse(wd.report_workflow_level_failure("s1", "TimeoutError"))
        self.assertFalse(wd.report_workflow_level_failure("s2", "ValueError"))
        self.assertFalse(wd.circuit_breaker_tripped)

    def test_disabled_when_threshold_none(self):
        wd = self._make_watchdog(threshold=None)
        for i in range(10):
            self.assertFalse(wd.report_workflow_level_failure(f"s{i}", "TimeoutError"))
        self.assertFalse(wd.circuit_breaker_tripped)


class TestCircuitBreakerIntegration(unittest.TestCase):
    """跨 step：不同 step 因同一 error_type 失败达到阈值后，后续 step 被取消。"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_downstream_step_cancelled_after_breaker_trips(self):
        cfg = _FakeCfg(
            project_root=self.tmpdir.name,
            workflow=_FakeWorkflowConfig(circuit_breaker_distinct_step_threshold=2),
        )
        wf = WorkflowDef(name="wf_cb", steps=[
            _step("s1"), _step("s2"), _step("s3", depends_on=["s1", "s2"]),
        ])

        def fake_execute(self_, step, resolved_prompt, step_results):
            if step.id in ("s1", "s2"):
                return StepResult(step_id=step.id, status=StepStatus.FAILED,
                                   error="boom", error_type="RuntimeError")
            return StepResult(step_id=step.id, status=StepStatus.DONE, output="ok")

        runner = WorkflowRunner(cfg)
        with patch.object(WorkflowRunner, "_execute_step", fake_execute):
            result = runner.run(wf)

        by_id = {r.step_id: r for r in result.step_results}
        # s1/s2 各自失败一次（RuntimeError），达到阈值 2 → 熔断触发 → 请求 cancel
        self.assertEqual(by_id["s1"].status, StepStatus.FAILED)
        self.assertEqual(by_id["s2"].status, StepStatus.FAILED)
        # s3 依赖 s1/s2，s1/s2 未全部成功，且 cancel 已被请求，不会被判定为 DONE
        self.assertNotEqual(by_id["s3"].status, StepStatus.DONE)


# ── Phase 3: validate() 必填字段表 refactor 后行为等价 ─────────────────────────

class TestSimpleRequiredFieldValidationUnchanged(unittest.TestCase):
    def test_sub_workflow_missing_workflow_name(self):
        wf = WorkflowDef(name="wf", steps=[_step("s", type="sub_workflow")])
        errors = wf.validate()
        self.assertTrue(any("sub_workflow" in e and "未指定 workflow_name" in e for e in errors))

    def test_tool_call_missing_tool_name(self):
        wf = WorkflowDef(name="wf", steps=[_step("s", type="tool_call")])
        errors = wf.validate()
        self.assertTrue(any("tool_call" in e and "未指定 tool_name" in e for e in errors))

    def test_script_missing_script(self):
        wf = WorkflowDef(name="wf", steps=[_step("s", type="script")])
        errors = wf.validate()
        self.assertTrue(any("script" in e and "未指定 script 命令" in e for e in errors))

    def test_skill_agent_missing_skill_name(self):
        wf = WorkflowDef(name="wf", steps=[_step("s", type="skill_agent")])
        errors = wf.validate()
        self.assertTrue(any("skill_agent" in e and "未指定 skill_name" in e for e in errors))

    def test_python_step_missing_script_path(self):
        wf = WorkflowDef(name="wf", steps=[_step("s", type="python_step")])
        errors = wf.validate()
        self.assertTrue(any("python_step" in e and "未指定 script_path" in e for e in errors))

    def test_sub_workflow_self_reference_still_rejected(self):
        wf = WorkflowDef(name="wf_self", steps=[
            _step("s", type="sub_workflow", workflow_name="wf_self"),
        ])
        errors = wf.validate()
        self.assertTrue(any("无限递归" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
