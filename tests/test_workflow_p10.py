"""
tests/test_workflow_p10.py — [workflow_mechanism_improvement_plan_p10.md] 单元测试

覆盖：
  §1 test_workflow_step：
    - agent 类型 step 沙箱执行：不落盘（workflow_sessions/ 目录执行前后不变化）、
      返回结果字段完整
    - human_input / require_approval 类型 step：按预期提示跳过，不阻塞
  §2 resume_workflow_run(step_overrides=...)：
    - 合法字段（timeout）覆盖后，WorkflowStore 中保存的定义文件内容不变
    - 非法字段（prompt）直接报错拒绝，不静默忽略
  §3 Watchdog 连续同类失败提前升级：
    - 连续 2 次同一 error_type 后，第 3 次不再重试，直接 NEEDS_FIX
    - error_type 不同则不触发提前升级，按原有逻辑走满重试预算
    - escalate_after_n_same_failures 自定义阈值生效
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.watchdog import WorkflowWatchdog
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow import api_helpers


def _step(id_, **kw) -> WorkflowStep:
    kw.setdefault("prompt", f"do {id_}")
    kw.setdefault("depends_on", [])
    return WorkflowStep(id=id_, name=id_, **kw)


@dataclass
class _FakeWorkflowConfig:
    parallel_enabled: bool = True
    max_parallel: int = 4
    hooks_enabled: bool = False
    max_sub_workflow_depth: int = 3
    script_step_enabled: bool = False
    script_step_timeout_seconds: float = 5.0
    tool_call_step_auto_approve: bool = True
    human_input_wait_timeout_seconds: float = 2.0
    approval_poll_interval_seconds: float = 0.05
    approval_wait_timeout_seconds: float = 1.0
    validate_placeholders_on_save: bool = True
    validate_role_refs_on_save: bool = True
    watchdog_enabled: bool = True
    heartbeat_check_interval_seconds: float = 0.05
    retry_on_error_backoff_seconds: float = 0.0
    background_execution_default: bool = False
    git_hint_enabled: bool = False


@dataclass
class _FakeCfg:
    project_root: str
    verbose: bool = False
    sandbox: bool = False
    model: str = "test-model"
    llm_provider: str = "anthropic"
    llm_base_url: str = ""
    api_key: str = "test"
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)


class TestWorkflowStepSandbox(unittest.TestCase):
    """§1 test_workflow_step"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _save_workflow(self, wf: WorkflowDef) -> None:
        store = WorkflowStore(Path(self.cfg.project_root))
        store.save(wf, cfg=self.cfg)

    def test_agent_step_sandbox_no_disk_writes(self):
        wf = WorkflowDef(
            name="wf_sandbox",
            steps=[_step("greet", prompt="say hi to {name}")],
        )
        self._save_workflow(wf)

        from mini_agent.storage.paths import AgentPaths
        paths = AgentPaths(project_root=self.cfg.project_root)
        sessions_dir = paths.workflow_sessions_dir
        before = list(sessions_dir.glob("**/*")) if sessions_dir.exists() else []

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="hello!") as mock_exec:
            result = api_helpers.test_workflow_step(
                self.cfg, "wf_sandbox", "greet",
                mock_step_results={}, mock_inputs={"name": "Otz"},
            )

        self.assertFalse(result["skipped"])
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["output"], "hello!")
        self.assertIn("duration_seconds", result)
        mock_exec.assert_called_once()
        # prompt 占位符应已被正确替换
        called_prompt = mock_exec.call_args[0][1]
        self.assertIn("Otz", called_prompt)

        after = list(sessions_dir.glob("**/*")) if sessions_dir.exists() else []
        self.assertEqual(before, after, "沙箱测试不应产生任何 workflow_sessions 落盘文件")

    def test_agent_step_sandbox_uses_mock_step_results(self):
        wf = WorkflowDef(
            name="wf_sandbox2",
            steps=[
                _step("fetch", prompt="fetch data"),
                _step("analyze", prompt="analyze: {fetch.output}", depends_on=["fetch"]),
            ],
        )
        self._save_workflow(wf)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="ok") as mock_exec:
            api_helpers.test_workflow_step(
                self.cfg, "wf_sandbox2", "analyze",
                mock_step_results={"fetch": {"output": "MOCK_DATA", "passed": True}},
            )
        called_prompt = mock_exec.call_args[0][1]
        self.assertIn("MOCK_DATA", called_prompt)

    def test_human_input_step_skipped(self):
        wf = WorkflowDef(
            name="wf_human",
            steps=[_step("ask", type="human_input", input_prompt="请输入")],
        )
        self._save_workflow(wf)

        result = api_helpers.test_workflow_step(self.cfg, "wf_human", "ask")
        self.assertTrue(result["skipped"])
        self.assertIn("resume_workflow_run", result["reason"])

    def test_require_approval_step_skipped(self):
        wf = WorkflowDef(
            name="wf_approval",
            steps=[_step("deploy", require_approval=True)],
        )
        self._save_workflow(wf)

        result = api_helpers.test_workflow_step(self.cfg, "wf_approval", "deploy")
        self.assertTrue(result["skipped"])

    def test_missing_mock_data_raises_clear_error(self):
        wf = WorkflowDef(
            name="wf_missing_mock",
            steps=[
                _step("a", prompt="do a"),
                _step("b", prompt="use {a.output}", depends_on=["a"]),
            ],
        )
        self._save_workflow(wf)

        with self.assertRaises(api_helpers.WorkflowApiError) as ctx:
            api_helpers.test_workflow_step(self.cfg, "wf_missing_mock", "b")
        self.assertEqual(ctx.exception.code, "bad_mock_data")


class TestResumeStepOverrides(unittest.TestCase):
    """§2 resume_workflow_run(step_overrides=...)"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.store = WorkflowStore(Path(self.cfg.project_root))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _start_and_fail_a_run(self, wf: WorkflowDef) -> str:
        """跑一次会失败的 workflow（human_input 无 input_key，前台同步会走
        没有 control 上下文的兜底路径直接返回展示文本——这里改用一个必然
        产生 session 快照、可用于 resume 的简单场景：直接用 runner.run()
        跑一次单 step 的 agent workflow 并 mock 掉执行，返回 session id。"""
        from mini_agent.storage.paths import AgentPaths
        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="done"):
            runner = WorkflowRunner(self.cfg)
            result = runner.run(wf, inputs={})
        return result.workflow_session_id

    def test_legal_override_does_not_touch_persisted_yaml(self):
        wf = WorkflowDef(name="wf_override", steps=[_step("solo", timeout=30)])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._start_and_fail_a_run(wf)

        before_yaml = self.store.export_yaml("wf_override")

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="done again"):
            outcome = api_helpers.resume_workflow_run(
                self.cfg, wf_session_id,
                step_overrides={"solo": {"timeout": 999}},
            )
        self.assertEqual(outcome["mode"], "sync")

        after_yaml = self.store.export_yaml("wf_override")
        self.assertEqual(before_yaml, after_yaml, "step_overrides 不应写回持久化的 workflow 定义")

        from mini_agent.workflow.session import WorkflowSession
        from mini_agent.storage.paths import AgentPaths
        paths = AgentPaths(project_root=self.cfg.project_root)
        s = WorkflowSession.load(paths, wf_session_id)
        self.assertEqual(s.last_step_overrides, {"solo": {"timeout": 999}})

    def test_illegal_field_rejected(self):
        wf = WorkflowDef(name="wf_override_bad", steps=[_step("solo")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._start_and_fail_a_run(wf)

        with self.assertRaises(api_helpers.WorkflowApiError) as ctx:
            api_helpers.resume_workflow_run(
                self.cfg, wf_session_id,
                step_overrides={"solo": {"prompt": "改逻辑，不允许"}},
            )
        self.assertEqual(ctx.exception.code, "bad_override")

    def test_unknown_step_id_rejected(self):
        wf = WorkflowDef(name="wf_override_unknown", steps=[_step("solo")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._start_and_fail_a_run(wf)

        with self.assertRaises(api_helpers.WorkflowApiError) as ctx:
            api_helpers.resume_workflow_run(
                self.cfg, wf_session_id,
                step_overrides={"no_such_step": {"timeout": 10}},
            )
        self.assertEqual(ctx.exception.code, "bad_override")


class TestWatchdogEscalation(unittest.TestCase):
    """§3 Watchdog 连续同类失败提前升级"""

    def _make_watchdog(self) -> WorkflowWatchdog:
        import tempfile as _tf
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.workflow import registry as wf_registry

        tmp = _tf.mkdtemp()
        paths = AgentPaths(project_root=tmp)
        control = wf_registry.register("wfs_test_escalate")
        return WorkflowWatchdog(paths=paths, workflow_session_id="wfs_test_escalate", control=control)

    def test_consecutive_same_error_escalates_at_threshold(self):
        wd = self._make_watchdog()
        self.assertFalse(wd.report_attempt_failure("s1", "TimeoutError", threshold=2))
        self.assertTrue(wd.report_attempt_failure("s1", "TimeoutError", threshold=2))

    def test_different_error_type_resets_count(self):
        wd = self._make_watchdog()
        self.assertFalse(wd.report_attempt_failure("s1", "TimeoutError", threshold=2))
        self.assertFalse(wd.report_attempt_failure("s1", "ValueError", threshold=2))
        # 计数被打断，第三次哪怕又是 TimeoutError，也只是重新计数的第 1 次
        self.assertFalse(wd.report_attempt_failure("s1", "TimeoutError", threshold=2))

    def test_custom_threshold(self):
        wd = self._make_watchdog()
        self.assertFalse(wd.report_attempt_failure("s1", "TimeoutError", threshold=3))
        self.assertFalse(wd.report_attempt_failure("s1", "TimeoutError", threshold=3))
        self.assertTrue(wd.report_attempt_failure("s1", "TimeoutError", threshold=3))

    def test_reset_clears_counter(self):
        wd = self._make_watchdog()
        wd.report_attempt_failure("s1", "TimeoutError", threshold=2)
        wd.reset_step_failures("s1")
        self.assertFalse(wd.report_attempt_failure("s1", "TimeoutError", threshold=2))


class TestRunnerEscalationIntegration(unittest.TestCase):
    """§3 集成：runner._execute_step_with_error_retry 通过 watchdog 短路重试"""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_escalates_before_exhausting_retry_budget(self):
        step = _step("flaky", retry_on_error=5, escalate_after_n_same_failures=2)
        runner = WorkflowRunner(self.cfg)
        runner._current_wf = WorkflowDef(name="wf", steps=[step])
        runner._current_watchdog = WorkflowWatchdog(
            paths=__import__("mini_agent.storage.paths", fromlist=["AgentPaths"]).AgentPaths(
                project_root=self.cfg.project_root
            ),
            workflow_session_id="wfs_int",
            control=__import__("mini_agent.workflow.registry", fromlist=["register"]).register("wfs_int"),
        )

        call_count = {"n": 0}

        def _fake_bounded(step_, resolved_prompt, step_results):
            call_count["n"] += 1
            return StepResult(step_id=step_.id, status=StepStatus.FAILED,
                               error="boom", error_type="TimeoutError")

        with patch.object(WorkflowRunner, "_execute_step_bounded", side_effect=_fake_bounded):
            sr = runner._execute_step_with_error_retry(step, "prompt", {})

        self.assertEqual(sr.status, StepStatus.NEEDS_FIX)
        # 阈值 2：第 1 次失败 attempt 计数=1（不升级），第 2 次 attempt 计数=2
        # （升级），之后不应再有第 3 次 _execute_step_bounded 调用。
        self.assertEqual(call_count["n"], 2)
        self.assertIn("连续", sr.error)

    def test_no_escalation_without_watchdog(self):
        """没有 watchdog（如单测直接调用）时，不应因为没有上报对象而报错，
        按原有 retry_on_error 逻辑正常走。"""
        step = _step("flaky2", retry_on_error=1)
        runner = WorkflowRunner(self.cfg)
        runner._current_wf = WorkflowDef(name="wf", steps=[step])
        # 不设置 _current_watchdog

        with patch.object(
            WorkflowRunner, "_execute_step_bounded",
            return_value=StepResult(step_id="flaky2", status=StepStatus.FAILED,
                                     error="boom", error_type="TimeoutError"),
        ) as mock_bounded:
            sr = runner._execute_step_with_error_retry(step, "prompt", {})

        self.assertEqual(sr.status, StepStatus.FAILED)
        self.assertEqual(sr.retries_used, 1)
        self.assertEqual(mock_bounded.call_count, 2)  # 初次 + 1 次 retry_on_error


if __name__ == "__main__":
    unittest.main()
