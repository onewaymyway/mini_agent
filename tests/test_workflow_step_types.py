"""
tests/test_workflow_step_types.py — [workflow机制改进计划.md P5/P6] 单元测试

覆盖：
  1. WorkflowStep.effective_type：旧 YAML（未设置 type）的向后兼容推断
  2. WorkflowDef.validate()：类型专属必填字段校验、自引用检测、
     占位符引用完整性校验（P6）、可选角色引用校验（P6）
  3. executors.get_executor()：分发表基本行为、未知类型报错
  4. ScriptStepExecutor：默认关闭（PermissionError）、开启后能正常执行
  5. ToolCallStepExecutor：引用不存在的工具报错
  6. SubWorkflowStepExecutor：递归深度保护
  7. HumanInputStepExecutor：无 control 上下文时的兜底行为、
     有 control 上下文时阻塞等待并返回送入的文本
  8. runner.step_requires_approval()：tool_call 默认更谨慎的审批判定
  9. WorkflowStore：内置模板库 list_templates / instantiate_template
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

from mini_agent.workflow.schema import WorkflowDef, WorkflowStep
from mini_agent.workflow import executors as wf_executors
from mini_agent.workflow.runner import WorkflowRunner, step_requires_approval
from mini_agent.workflow.store import WorkflowStore


def _step(id_, **kw) -> WorkflowStep:
    kw.setdefault("prompt", f"do {id_}")
    kw.setdefault("depends_on", [])
    return WorkflowStep(id=id_, name=id_, **kw)


@dataclass
class _FakeWorkflowConfig:
    parallel_enabled: bool = True
    max_parallel: int = 4
    hooks_enabled: bool = True
    max_sub_workflow_depth: int = 3
    script_step_enabled: bool = False
    script_step_timeout_seconds: float = 5.0
    tool_call_step_auto_approve: bool = False
    human_input_wait_timeout_seconds: float = 2.0
    approval_poll_interval_seconds: float = 0.05
    validate_placeholders_on_save: bool = True
    validate_role_refs_on_save: bool = True


@dataclass
class _FakeCfg:
    project_root: str = "/tmp"
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)


# ── 1. effective_type 向后兼容推断 ─────────────────────────────────────────

class TestEffectiveType(unittest.TestCase):
    def test_no_role_no_type_is_agent(self):
        s = _step("a")
        self.assertEqual(s.effective_type, "agent")

    def test_role_no_type_is_role_agent(self):
        s = _step("a", role="evaluator")
        self.assertEqual(s.effective_type, "role_agent")

    def test_explicit_type_wins(self):
        s = _step("a", role="evaluator", type="agent")
        self.assertEqual(s.effective_type, "agent")

    def test_new_types_roundtrip_through_dict(self):
        s = WorkflowStep(
            id="sw", name="sw", prompt="", type="sub_workflow", workflow_name="other_wf",
        )
        wf = WorkflowDef(name="wf", description="", steps=[s])
        d = wf.to_dict()
        wf2 = WorkflowDef.from_dict(d)
        self.assertEqual(wf2.steps[0].type, "sub_workflow")
        self.assertEqual(wf2.steps[0].workflow_name, "other_wf")


# ── 2. validate() ───────────────────────────────────────────────────────────

class TestValidate(unittest.TestCase):
    def test_sub_workflow_requires_workflow_name(self):
        s = WorkflowStep(id="a", name="a", prompt="x", type="sub_workflow")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate()
        self.assertTrue(any("workflow_name" in e for e in errors))

    def test_sub_workflow_self_reference_rejected(self):
        s = WorkflowStep(id="a", name="a", prompt="x", type="sub_workflow", workflow_name="wf")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate()
        self.assertTrue(any("自身" in e for e in errors))

    def test_tool_call_requires_tool_name(self):
        s = WorkflowStep(id="a", name="a", prompt="x", type="tool_call")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate()
        self.assertTrue(any("tool_name" in e for e in errors))

    def test_script_requires_script(self):
        s = WorkflowStep(id="a", name="a", prompt="x", type="script")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate()
        self.assertTrue(any("script" in e for e in errors))

    def test_placeholder_referencing_unknown_step_rejected(self):
        s = _step("a", prompt="see {ghost.output}")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate()
        self.assertTrue(any("ghost" in e for e in errors))

    def test_placeholder_referencing_known_step_ok(self):
        s1 = _step("a")
        s2 = _step("b", prompt="see {a.output}", depends_on=["a"])
        wf = WorkflowDef(name="wf", description="", steps=[s1, s2])
        errors = wf.validate()
        self.assertEqual(errors, [])

    def test_placeholder_unknown_field_rejected(self):
        s1 = _step("a")
        s2 = _step("b", prompt="see {a.bogus_field}", depends_on=["a"])
        wf = WorkflowDef(name="wf", description="", steps=[s1, s2])
        errors = wf.validate()
        self.assertTrue(any("bogus_field" in e for e in errors))

    def test_param_placeholder_not_checked(self):
        # {topic} 这种没有 "." 的占位符属于运行时 inputs，不应被误判为步骤引用
        s = _step("a", prompt="about {topic}")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate()
        self.assertEqual(errors, [])

    def test_role_checker_used_when_provided(self):
        s = _step("a", role="nonexistent_role")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate(role_checker=lambda name: name in ("evaluator",))
        self.assertTrue(any("nonexistent_role" in e for e in errors))

    def test_role_checker_not_used_when_absent(self):
        s = _step("a", role="anything")
        wf = WorkflowDef(name="wf", description="", steps=[s])
        errors = wf.validate()  # role_checker=None -> 跳过角色校验
        self.assertEqual(errors, [])


# ── 3. executors 分发表 ──────────────────────────────────────────────────────

class TestExecutorDispatch(unittest.TestCase):
    def test_all_step_types_registered(self):
        for t in ("agent", "role_agent", "sub_workflow", "tool_call", "human_input", "script"):
            executor = wf_executors.get_executor(t)
            self.assertIsInstance(executor, wf_executors.StepExecutor)

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            wf_executors.get_executor("no_such_type")

    def test_agent_executor_delegates_to_runner(self):
        runner = MagicMock()
        runner._execute_with_main_agent.return_value = "ok"
        step = _step("a")
        out = wf_executors.AgentStepExecutor().execute(runner, step, "hi")
        self.assertEqual(out, "ok")
        runner._execute_with_main_agent.assert_called_once_with(step, "hi")

    def test_role_agent_executor_delegates_to_runner(self):
        runner = MagicMock()
        runner._execute_with_role_agent.return_value = "ok2"
        step = _step("a", role="evaluator")
        out = wf_executors.RoleAgentStepExecutor().execute(runner, step, "hi")
        self.assertEqual(out, "ok2")


# ── 4. ScriptStepExecutor ────────────────────────────────────────────────────

class TestScriptStepExecutor(unittest.TestCase):
    def test_disabled_by_default_raises_permission_error(self):
        runner = MagicMock()
        runner._cfg = _FakeCfg()
        step = _step("a", type="script", script="echo hi")
        with self.assertRaises(PermissionError):
            wf_executors.ScriptStepExecutor().execute(runner, step, "hi")

    def test_enabled_runs_command(self):
        cfg = _FakeCfg()
        cfg.workflow.script_step_enabled = True
        runner = MagicMock()
        runner._cfg = cfg
        step = _step("a", type="script", script="echo hello_world")
        out = wf_executors.ScriptStepExecutor().execute(runner, step, "hi")
        self.assertIn("hello_world", out)

    def test_nonzero_exit_raises(self):
        cfg = _FakeCfg()
        cfg.workflow.script_step_enabled = True
        runner = MagicMock()
        runner._cfg = cfg
        step = _step("a", type="script", script="exit 1")
        with self.assertRaises(RuntimeError):
            wf_executors.ScriptStepExecutor().execute(runner, step, "hi")


# ── 5. ToolCallStepExecutor ──────────────────────────────────────────────────

class TestToolCallStepExecutor(unittest.TestCase):
    def test_unknown_tool_raises(self):
        runner = MagicMock()
        step = _step("a", type="tool_call", tool_name="no_such_tool_xyz")
        with self.assertRaises(ValueError):
            wf_executors.ToolCallStepExecutor().execute(runner, step, "hi")


# ── 6. SubWorkflowStepExecutor 深度保护 ──────────────────────────────────────

class TestSubWorkflowDepth(unittest.TestCase):
    def test_depth_limit_enforced(self):
        cfg = _FakeCfg()
        cfg.workflow.max_sub_workflow_depth = 2
        runner = MagicMock()
        runner._cfg = cfg
        runner._sub_workflow_depth = 2  # 已达上限
        step = _step("a", type="sub_workflow", workflow_name="other")
        with self.assertRaises(RuntimeError):
            wf_executors.SubWorkflowStepExecutor().execute(runner, step, "hi")


# ── 7. HumanInputStepExecutor ────────────────────────────────────────────────

class TestHumanInputStepExecutor(unittest.TestCase):
    def test_without_control_falls_back_to_prompt(self):
        runner = MagicMock()
        runner._current_control = None
        step = _step("a", type="human_input", input_prompt="请输入你的名字")
        out = wf_executors.HumanInputStepExecutor().execute(runner, step, "hi")
        self.assertEqual(out, "请输入你的名字")

    def test_with_control_waits_and_returns_provided_text(self):
        from mini_agent.workflow.registry import ControlState

        control = ControlState()
        cfg = _FakeCfg()
        runner = MagicMock()
        runner._cfg = cfg
        runner._current_control = control
        runner._current_wf_session = None
        runner._current_paths = None
        step = _step("a", type="human_input")

        def _provide_later():
            time.sleep(0.1)
            control.request_provide_input("a", "张三")

        t = threading.Thread(target=_provide_later, daemon=True)
        t.start()
        out = wf_executors.HumanInputStepExecutor().execute(runner, step, "your name?")
        t.join()
        self.assertEqual(out, "张三")

    def test_timeout_raises(self):
        from mini_agent.workflow.registry import ControlState

        control = ControlState()
        cfg = _FakeCfg()
        cfg.workflow.human_input_wait_timeout_seconds = 0.1
        cfg.workflow.approval_poll_interval_seconds = 0.02
        runner = MagicMock()
        runner._cfg = cfg
        runner._current_control = control
        runner._current_wf_session = None
        runner._current_paths = None
        step = _step("a", type="human_input")
        with self.assertRaises(TimeoutError):
            wf_executors.HumanInputStepExecutor().execute(runner, step, "your name?")


# ── 8. step_requires_approval ────────────────────────────────────────────────

class TestStepRequiresApproval(unittest.TestCase):
    def test_explicit_true_always_wins(self):
        s = _step("a", require_approval=True)
        self.assertTrue(step_requires_approval(s, _FakeWorkflowConfig()))

    def test_agent_type_defaults_false(self):
        s = _step("a")
        self.assertFalse(step_requires_approval(s, _FakeWorkflowConfig()))

    def test_tool_call_defaults_true(self):
        s = _step("a", type="tool_call", tool_name="x")
        self.assertTrue(step_requires_approval(s, _FakeWorkflowConfig()))

    def test_tool_call_auto_approve_config_disables_it(self):
        s = _step("a", type="tool_call", tool_name="x")
        cfg = _FakeWorkflowConfig(tool_call_step_auto_approve=True)
        self.assertFalse(step_requires_approval(s, cfg))


# ── 9. WorkflowStore 模板库 ───────────────────────────────────────────────────

class TestWorkflowTemplates(unittest.TestCase):
    def test_list_templates_returns_builtin_templates(self):
        with tempfile.TemporaryDirectory() as td:
            store = WorkflowStore(Path(td))
            names = {t["name"] for t in store.list_templates()}
            self.assertEqual(
                names, {"code_review", "research_report", "multi_perspective_debate"}
            )

    def test_instantiate_template_renames_and_validates(self):
        with tempfile.TemporaryDirectory() as td:
            store = WorkflowStore(Path(td))
            wf = store.instantiate_template("code_review", "my_review")
            self.assertEqual(wf.name, "my_review")
            self.assertEqual(wf.validate(), [])

    def test_instantiate_unknown_template_raises(self):
        with tempfile.TemporaryDirectory() as td:
            store = WorkflowStore(Path(td))
            with self.assertRaises(ValueError):
                store.instantiate_template("no_such_template", "x")

    def test_save_after_instantiate_roundtrips(self):
        with tempfile.TemporaryDirectory() as td:
            store = WorkflowStore(Path(td))
            wf = store.instantiate_template("research_report", "my_research")
            path = store.save(wf)
            self.assertTrue(path.exists())
            loaded = store.load("my_research")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.name, "my_research")


if __name__ == "__main__":
    unittest.main()
