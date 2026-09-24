"""
tests/test_workflow_resume_latest_definition.py
[next_doc/workflow_visual_editor_plan.md M1] resume_workflow_run 续跑定义快照问题。

根因（读代码得到，本文件第一个测试用于复现）：
  - `WorkflowRunner.run()` 只在**新建** run（`loaded is None`）时把
    `wf.to_dict()` 写一份快照到 `workflow_session_def_snapshot`；
  - `api_helpers.resume_workflow_run()` 续跑时永远读这份旧快照
    （`generator.parse_yaml(snap_path.read_text(...))`），从不重新读
    `WorkflowStore`；
  - 因此 `patch_workflow_step` 改的是 `.agent/workflows/` 里的原始定义，
    只要不重新读 store，续跑执行的仍是「首次运行时」的旧定义——
    看板“保存修改并从此步骤续跑”这条标准流程实际不生效。

修复：`resume_workflow_run` 新增 `use_latest_definition` 参数（默认 False，
行为完全向后兼容）。为 True 时从 `WorkflowStore` 重新加载当前定义、重写快照，
`force_rerun_from` 必须存在于新定义中，已有 `step_results` 按 id 保留，
新定义里已不存在的 id 的结果保留在 session 里但不参与调度并在返回值
`warnings` 中列出。
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from mini_agent.workflow.schema import WorkflowDef, WorkflowStep
from mini_agent.workflow.runner import WorkflowRunner
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow import api_helpers


def _step(id_, **kw) -> WorkflowStep:
    kw.setdefault("prompt", f"do {id_}")
    kw.setdefault("depends_on", [])
    kw.setdefault("retry_on_error", 0)
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
    watchdog_enabled: bool = False
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


class _ResumeLatestDefinitionTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.store = WorkflowStore(Path(self.cfg.project_root))

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fail_once_and_get_session_id(self, wf: WorkflowDef) -> str:
        """跑一次必然失败的单 step workflow（mock 执行抛异常，
        retry_on_error=0 不重试），返回 workflow_session_id。
        失败后 step 状态是 FAILED（不在 resume 跳过集合内），因此
        resume 时该 step 会被重新执行——用于观察其收到的 prompt。"""
        with patch.object(WorkflowRunner, "_execute_with_main_agent", side_effect=RuntimeError("boom")):
            runner = WorkflowRunner(self.cfg)
            result = runner.run(wf, inputs={})
        return result.workflow_session_id


class TestResumeReproducesStaleSnapshot(_ResumeLatestDefinitionTestBase):
    """第一步：先复现问题是否成立。"""

    def test_resume_without_flag_still_uses_snapshot_prompt(self):
        wf = WorkflowDef(name="wf_stale", steps=[_step("solo", prompt="OLD_PROMPT")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        # 主 Agent / patch_workflow_step 修改了持久化定义
        wf2 = self.store.load("wf_stale")
        wf2.steps[0].prompt = "NEW_PROMPT"
        self.store.save(wf2, cfg=self.cfg)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="ok") as mock_exec:
            api_helpers.resume_workflow_run(self.cfg, wf_session_id)

        used_prompt = mock_exec.call_args[0][1]
        # 默认（向后兼容）行为：resume 仍然使用首次运行时写入的旧快照。
        self.assertIn("OLD_PROMPT", used_prompt)
        self.assertNotIn("NEW_PROMPT", used_prompt)


class TestResumeUseLatestDefinition(_ResumeLatestDefinitionTestBase):
    """M1 修复：use_latest_definition=True 时续跑应使用最新持久化定义。"""

    def test_use_latest_definition_true_picks_up_new_prompt(self):
        wf = WorkflowDef(name="wf_latest", steps=[_step("solo", prompt="OLD_PROMPT")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        wf2 = self.store.load("wf_latest")
        wf2.steps[0].prompt = "NEW_PROMPT"
        self.store.save(wf2, cfg=self.cfg)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="ok") as mock_exec:
            outcome = api_helpers.resume_workflow_run(
                self.cfg, wf_session_id, use_latest_definition=True,
            )

        used_prompt = mock_exec.call_args[0][1]
        self.assertIn("NEW_PROMPT", used_prompt)
        self.assertEqual(outcome["mode"], "sync")

    def test_use_latest_definition_rewrites_snapshot_for_next_resume(self):
        """重写快照后，下一次不带参数的 resume 也应沿用新定义
        （而不是回退到最初的旧快照）。"""
        wf = WorkflowDef(name="wf_latest2", steps=[
            _step("solo", prompt="OLD_PROMPT", retry_on_error=0),
        ])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        wf2 = self.store.load("wf_latest2")
        wf2.steps[0].prompt = "NEW_PROMPT"
        self.store.save(wf2, cfg=self.cfg)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", side_effect=RuntimeError("boom again")):
            api_helpers.resume_workflow_run(self.cfg, wf_session_id, use_latest_definition=True)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="ok") as mock_exec:
            api_helpers.resume_workflow_run(self.cfg, wf_session_id)

        used_prompt = mock_exec.call_args[0][1]
        self.assertIn("NEW_PROMPT", used_prompt)

    def test_existing_step_results_preserved_by_id(self):
        wf = WorkflowDef(name="wf_preserve", steps=[
            _step("first", prompt="first prompt"),
            _step("second", prompt="second: {first.output}", depends_on=["first"]),
        ])
        self.store.save(wf, cfg=self.cfg)

        call_count = {"n": 0}

        def _exec(step, prompt, **kw):
            call_count["n"] += 1
            if step.id == "first":
                return "first_done"
            raise RuntimeError("boom")

        with patch.object(WorkflowRunner, "_execute_with_main_agent", side_effect=_exec):
            runner = WorkflowRunner(self.cfg)
            result = runner.run(wf, inputs={})
        wf_session_id = result.workflow_session_id

        # 定义未变，只是 use_latest_definition=True 走一遍重新加载路径
        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="second_done") as mock_exec:
            api_helpers.resume_workflow_run(self.cfg, wf_session_id, use_latest_definition=True)

        # "first" 不应被重新执行（结果按 id 保留），"second" 应该拿到
        # first 的旧结果拼进 prompt
        used_prompt = mock_exec.call_args[0][1]
        self.assertIn("first_done", used_prompt)
        first_step_calls = [
            c for c in mock_exec.call_args_list if c.args and c.args[0].id == "first"
        ]
        self.assertEqual(len(first_step_calls), 0, "已完成的 step 不应在 use_latest_definition=True 时被重新执行")

    def test_force_rerun_from_must_exist_in_new_definition(self):
        wf = WorkflowDef(name="wf_removed_step", steps=[_step("solo", prompt="OLD_PROMPT")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        # 新定义把这个 step 整个删掉了
        wf2 = WorkflowDef(name="wf_removed_step", steps=[_step("other", prompt="other prompt")])
        self.store.save(wf2, cfg=self.cfg)

        with self.assertRaises(api_helpers.WorkflowApiError) as ctx:
            api_helpers.resume_workflow_run(
                self.cfg, wf_session_id, use_latest_definition=True, force_rerun_from="solo",
            )
        self.assertEqual(ctx.exception.code, "bad_step")

    def test_orphaned_results_listed_in_warnings_not_dropped_silently(self):
        wf = WorkflowDef(name="wf_orphan", steps=[
            _step("first", prompt="first prompt"),
            _step("second", prompt="second prompt", depends_on=["first"]),
        ])
        self.store.save(wf, cfg=self.cfg)

        def _exec(step, prompt, **kw):
            if step.id == "first":
                return "first_done"
            raise RuntimeError("boom")

        with patch.object(WorkflowRunner, "_execute_with_main_agent", side_effect=_exec):
            runner = WorkflowRunner(self.cfg)
            result = runner.run(wf, inputs={})
        wf_session_id = result.workflow_session_id

        # 新定义去掉了 "second"，新增了 "third"
        wf2 = WorkflowDef(name="wf_orphan", steps=[
            _step("first", prompt="first prompt"),
            _step("third", prompt="third prompt", depends_on=["first"]),
        ])
        self.store.save(wf2, cfg=self.cfg)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="third_done"):
            outcome = api_helpers.resume_workflow_run(self.cfg, wf_session_id, use_latest_definition=True)

        warnings_text = " ".join(outcome.get("warnings", []))
        self.assertIn("second", warnings_text)

    def test_definition_load_failure_does_not_fall_back_to_old_snapshot(self):
        wf = WorkflowDef(name="wf_broken_reload", steps=[_step("solo", prompt="OLD_PROMPT")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        # 直接删除持久化定义文件，模拟"当前定义已不可读"
        path = self.store._resolve_path("wf_broken_reload")
        path.unlink()

        with self.assertRaises(api_helpers.WorkflowApiError):
            api_helpers.resume_workflow_run(self.cfg, wf_session_id, use_latest_definition=True)


class TestDefinitionChangedFlag(_ResumeLatestDefinitionTestBase):
    """get_workflow_run_detail 新增的 definition_changed 漂移提示。"""

    def test_definition_changed_false_when_untouched(self):
        wf = WorkflowDef(name="wf_flag_a", steps=[_step("solo", prompt="P")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        detail = api_helpers.get_workflow_run_detail(self.cfg, wf_session_id)
        self.assertFalse(detail["definition_changed"])

    def test_definition_changed_true_after_patch(self):
        wf = WorkflowDef(name="wf_flag_b", steps=[_step("solo", prompt="P")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        wf2 = self.store.load("wf_flag_b")
        wf2.steps[0].prompt = "P2"
        self.store.save(wf2, cfg=self.cfg)

        detail = api_helpers.get_workflow_run_detail(self.cfg, wf_session_id)
        self.assertTrue(detail["definition_changed"])

    def test_definition_changed_none_when_current_definition_unreadable(self):
        wf = WorkflowDef(name="wf_flag_c", steps=[_step("solo", prompt="P")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        path = self.store._resolve_path("wf_flag_c")
        path.unlink()

        detail = api_helpers.get_workflow_run_detail(self.cfg, wf_session_id)
        self.assertIsNone(detail["definition_changed"])


class TestCliResumeLatestFlag(_ResumeLatestDefinitionTestBase):
    """CLI `workflow resume <id> --latest`（_handle_resume）复用
    api_helpers.load_definition_for_resume，行为应与 API/工具一致。"""

    def test_handle_resume_with_latest_flag_uses_new_definition(self):
        from mini_agent.cli.commands import workflow_cmd

        wf = WorkflowDef(name="wf_cli_latest", steps=[_step("solo", prompt="OLD_PROMPT")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        wf2 = self.store.load("wf_cli_latest")
        wf2.steps[0].prompt = "NEW_PROMPT"
        self.store.save(wf2, cfg=self.cfg)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="ok") as mock_exec:
            workflow_cmd._handle_resume(self.cfg, [wf_session_id, "--latest"])

        used_prompt = mock_exec.call_args[0][1]
        self.assertIn("NEW_PROMPT", used_prompt)

    def test_handle_resume_without_latest_flag_uses_snapshot(self):
        from mini_agent.cli.commands import workflow_cmd

        wf = WorkflowDef(name="wf_cli_snap", steps=[_step("solo", prompt="OLD_PROMPT")])
        self.store.save(wf, cfg=self.cfg)
        wf_session_id = self._fail_once_and_get_session_id(wf)

        wf2 = self.store.load("wf_cli_snap")
        wf2.steps[0].prompt = "NEW_PROMPT"
        self.store.save(wf2, cfg=self.cfg)

        with patch.object(WorkflowRunner, "_execute_with_main_agent", return_value="ok") as mock_exec:
            workflow_cmd._handle_resume(self.cfg, [wf_session_id])

        used_prompt = mock_exec.call_args[0][1]
        self.assertIn("OLD_PROMPT", used_prompt)


if __name__ == "__main__":
    unittest.main()
