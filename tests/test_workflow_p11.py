"""
tests/test_workflow_p11.py —
[workflow_input_passing_and_debug_logging_improvement_plan.md] 单元测试

覆盖：
  §1 WorkflowDef.validate()：prompt 占位符引用了存在但未声明 depends_on
     的 step_id 时报错；声明了依赖则通过。
  §3 {step_id.output_file} 占位符：解析为该 step 落盘文件的绝对路径。
  §4 PythonStepExecutor：ctx.inputs 默认按 depends_on 过滤上游结果；
     关闭 python_step_inputs_filtered_by_depends_on 开关后回退到旧行为。
  §6 StepResult.debug_log：debug_log_enabled=True 时，run() 跑完后
     resolved_prompt / unresolved_placeholders / upstream_step_ids_used /
     batch_index 等字段被正确填充；默认关闭时不产生任何 debug_log。
  §6.4 依赖声明与实际引用不一致时，记录进 debug_log.undeclared_dependency_usage
     并上报 WorkflowWatchdog.report_dependency_mismatch。
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
from mini_agent.workflow import executors as wf_executors


def _step(id_, depends_on=None, **kw) -> WorkflowStep:
    kw.setdefault("prompt", f"do {id_}")
    return WorkflowStep(id=id_, name=id_, depends_on=depends_on or [], **kw)


@dataclass
class _FakeWorkflowConfig:
    parallel_enabled: bool = True
    max_parallel: int = 4
    debug_log_enabled: bool = False
    debug_log_max_chars: int = 4000
    python_step_enabled: bool = True
    python_step_inputs_filtered_by_depends_on: bool = True
    placeholder_depends_on_check_enabled: bool = True


@dataclass
class _FakeCfg:
    project_root: str = "/tmp"
    verbose: bool = False
    sandbox: bool = True
    model: str = "test-model"
    llm_provider: str = "anthropic"
    llm_base_url: str = ""
    api_key: str = "test-key"
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)


class TestPlaceholderDependsOnValidation(unittest.TestCase):
    """§1"""

    def test_missing_depends_on_is_rejected(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("b", prompt="use {a.output}"),  # 没有声明 depends_on=["a"]
        ])
        errors = wf.validate()
        self.assertTrue(any("depends_on" in e and "b" in e for e in errors), errors)

    def test_declared_depends_on_passes(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("b", depends_on=["a"], prompt="use {a.output}"),
        ])
        errors = wf.validate()
        self.assertEqual(errors, [])

    def test_check_can_be_disabled(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("b", prompt="use {a.output}"),
        ])
        errors = wf.validate(check_placeholder_depends_on=False)
        self.assertEqual(errors, [])

    def test_output_file_field_is_a_recognized_placeholder(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a", output_file="a.json"),
            _step("b", depends_on=["a"], prompt="see {a.output_file}"),
        ])
        errors = wf.validate()
        self.assertEqual(errors, [])


class TestOutputFilePlaceholder(unittest.TestCase):
    """§3"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)

    def test_resolves_to_absolute_path(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a", output_file="a.json"),
            _step("b", depends_on=["a"], prompt="see {a.output_file}"),
        ])

        captured = {}

        def fake_exec_with_main_agent(step, resolved_prompt):
            if step.id == "b":
                captured["b_prompt"] = resolved_prompt
                return "ok"
            return '{"x":1}'

        runner = WorkflowRunner(self.cfg)
        with patch.object(WorkflowRunner, "_execute_with_main_agent", side_effect=fake_exec_with_main_agent):
            result = runner.run(wf)

        self.assertEqual(result.status, "done")
        self.assertIn(str(Path(self.tmpdir.name)), captured["b_prompt"])
        self.assertTrue(captured["b_prompt"].endswith("a.json"))


class _FakeRunner:
    """最小化 runner 替身，只提供 PythonStepExecutor.execute 需要的属性。"""

    def __init__(self, cfg, step_results):
        self._cfg = cfg
        self._current_step_results = step_results
        self._last_subprocess_debug = None

    def _effective_step_field(self, step, field_name, default):
        return getattr(step, field_name, None) or default


class TestPythonStepInputsFilteredByDependsOn(unittest.TestCase):
    """§4"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.script_dir = Path(self.tmpdir.name) / "steps"
        self.script_dir.mkdir(parents=True, exist_ok=True)
        self.script_path = self.script_dir / "dump_inputs.py"
        self.script_path.write_text(
            "def run(ctx):\n"
            "    return sorted(ctx.inputs.keys())\n",
            encoding="utf-8",
        )
        self.upstream = {
            "declared": StepResult(step_id="declared", status=StepStatus.DONE, output="A"),
            "undeclared": StepResult(step_id="undeclared", status=StepStatus.DONE, output="B"),
        }

    def _make_step(self):
        return WorkflowStep(
            id="s1", name="s1", prompt="", type="python_step",
            script_path=str(self.script_path), depends_on=["declared"],
        )

    def test_filtered_by_default(self):
        cfg = _FakeCfg(project_root=self.tmpdir.name)
        runner = _FakeRunner(cfg, self.upstream)
        step = self._make_step()
        output = wf_executors.PythonStepExecutor().execute(runner, step, "")
        self.assertEqual(json.loads(output), ["declared"])

    def test_unfiltered_when_disabled(self):
        cfg = _FakeCfg(
            project_root=self.tmpdir.name,
            workflow=_FakeWorkflowConfig(python_step_inputs_filtered_by_depends_on=False),
        )
        runner = _FakeRunner(cfg, self.upstream)
        step = self._make_step()
        output = wf_executors.PythonStepExecutor().execute(runner, step, "")
        self.assertEqual(json.loads(output), ["declared", "undeclared"])


class TestDebugLog(unittest.TestCase):
    """§6"""

    def test_disabled_by_default_produces_no_debug_log(self):
        wf = WorkflowDef(name="wf", steps=[_step("a")])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output="ok")

        runner = WorkflowRunner(_FakeCfg())
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        sr = {r.step_id: r for r in result.step_results}["a"]
        self.assertEqual(sr.debug_log, {})

    def test_enabled_populates_resolved_prompt_and_unresolved_placeholders(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a", prompt="hello {missing_var}"),
        ])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output="ok")

        cfg = _FakeCfg(workflow=_FakeWorkflowConfig(debug_log_enabled=True))
        runner = WorkflowRunner(cfg)
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        sr = {r.step_id: r for r in result.step_results}["a"]
        self.assertIn("hello", sr.debug_log.get("resolved_prompt", ""))
        self.assertEqual(sr.debug_log.get("unresolved_placeholders"), ["missing_var"])
        self.assertEqual(sr.debug_log.get("batch_index"), 0)
        self.assertIn("started_at", sr.debug_log)
        self.assertIn("finished_at", sr.debug_log)

    def test_enabled_tracks_upstream_step_ids_used(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("b", depends_on=["a"], prompt="use {a.output}"),
        ])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output=f"out-{step.id}")

        cfg = _FakeCfg(workflow=_FakeWorkflowConfig(debug_log_enabled=True))
        runner = WorkflowRunner(cfg)
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        sr = {r.step_id: r for r in result.step_results}["b"]
        self.assertEqual(sr.debug_log.get("upstream_step_ids_used"), ["a"])

    def test_undeclared_dependency_usage_is_flagged_and_reported_to_watchdog(self):
        """§6.4：placeholder_depends_on_check_enabled=False 时，validate() 放行
        了未声明依赖的占位符引用，但运行期 debug_log 应该把这个 diff 记下来，
        并上报给 watchdog（不改变本次执行结果本身）。"""
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("b", prompt="use {a.output}"),  # 故意不声明 depends_on=["a"]
        ])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output=f"out-{step.id}")

        cfg = _FakeCfg(workflow=_FakeWorkflowConfig(
            debug_log_enabled=True,
            placeholder_depends_on_check_enabled=False,
        ))
        runner = WorkflowRunner(cfg)
        reported = []
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute), \
             patch(
                 "mini_agent.workflow.watchdog.WorkflowWatchdog.report_dependency_mismatch",
                 side_effect=lambda step_id, ids: reported.append((step_id, ids)),
             ):
            result = runner.run(wf)

        sr = {r.step_id: r for r in result.step_results}["b"]
        self.assertEqual(sr.debug_log.get("undeclared_dependency_usage"), ["a"])
        self.assertEqual(result.status, "done")  # 只记录，不影响执行结果
        self.assertIn(("b", ["a"]), reported)


class TestDependencyMismatchDetection(unittest.TestCase):
    """§6.4：debug_log 与 watchdog 打通——upstream_step_ids_used 与
    depends_on 不一致时，记录进 debug_log 并上报 watchdog。"""

    def test_undeclared_prompt_reference_recorded_when_check_disabled(self):
        # 关闭 §1 静态校验开关，构造一个"存在但未声明依赖"的 prompt 引用，
        # 让它能存活到运行期（正常情况下这条路径已被 validate() 拦下）。
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("b", prompt="use {a.output}"),  # 未声明 depends_on=["a"]
        ])
        self.assertEqual(wf.validate(check_placeholder_depends_on=False), [])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output=f"out-{step.id}")

        cfg = _FakeCfg(workflow=_FakeWorkflowConfig(debug_log_enabled=True))
        runner = WorkflowRunner(cfg)
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        sr = {r.step_id: r for r in result.step_results}["b"]
        self.assertEqual(sr.debug_log.get("undeclared_dependency_usage"), ["a"])

    def test_declared_dependency_has_no_mismatch(self):
        wf = WorkflowDef(name="wf", steps=[
            _step("a"),
            _step("b", depends_on=["a"], prompt="use {a.output}"),
        ])

        def fake_execute(step, resolved_prompt, step_results):
            return StepResult(step_id=step.id, status=StepStatus.DONE, output=f"out-{step.id}")

        cfg = _FakeCfg(workflow=_FakeWorkflowConfig(debug_log_enabled=True))
        runner = WorkflowRunner(cfg)
        with patch.object(WorkflowRunner, "_execute_step", side_effect=fake_execute):
            result = runner.run(wf)

        sr = {r.step_id: r for r in result.step_results}["b"]
        self.assertNotIn("undeclared_dependency_usage", sr.debug_log)


if __name__ == "__main__":
    unittest.main()
