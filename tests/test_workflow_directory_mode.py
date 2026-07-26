"""
tests/test_workflow_directory_mode.py
（next_doc/workflow_directory_mode_design.md）单元测试

覆盖：
  1. schema.py：prompt_file/skill_name 字段、skill_agent 类型、
     validate() 的新增校验（skill_agent 必填 skill_name，prompt_file
     有值时不要求内嵌 prompt 非空）
  2. store.py：文件夹模式 save_as_dir/load（source_dir 设置、优先级高于
     单文件模式）、prompt_file 解析、to_dict 往返只写 prompt_file、
     to_dir() 迁移单文件到文件夹模式、delete() 删除整个目录、
     list_all()/exists() 同时识别两种模式
  3. resource_bundle.py：本地 agents/skills 目录合并、本地同名覆盖全局、
     wf.source_dir 为 None 时 build_resource_bundle 返回 None
  4. runner.py：
     - _execute_with_main_agent 把 bundle 的 skill_loader/
       agent_profile_loader 传给 Agent()
     - _execute_with_role_agent 优先使用 bundle 里的本地 profile，
       不再查询全局 dispatcher
  5. executors.py：SkillAgentStepExecutor 优先使用 bundle 里的本地
     skill，缺少 skill_name 时报错，找不到 skill 时报错
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, STEP_TYPES
from mini_agent.workflow.store import WorkflowStore
from mini_agent.workflow.resource_bundle import (
    WorkflowResourceBundle,
    build_resource_bundle,
)
from mini_agent.workflow import executors as wf_executors
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
    max_sub_workflow_depth: int = 3
    script_step_enabled: bool = False
    tool_call_step_auto_approve: bool = False


@dataclass
class _FakeCfg:
    project_root: str = "/tmp"
    verbose: bool = False
    sandbox: bool = True
    model: str = "fake-model"
    llm_provider: str = "fake"
    llm_base_url: str = ""
    api_key: str = "fake-key"
    debug_llm: bool = False
    debug_llm_console: bool = False
    skills_dir: str = ""
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)


def _write_agent_profile(path: Path, name: str, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: test profile\n---\n{marker}",
        encoding="utf-8",
    )


def _write_skill(path: Path, name: str, marker: str) -> None:
    """path 形如 <skills_dir>/<name>/SKILL.md"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: test skill\ntriggers: {name}\n---\n{marker}",
        encoding="utf-8",
    )


# ── 1. schema.py ──────────────────────────────────────────────────────────

class TestSchemaExtensions(unittest.TestCase):
    def test_skill_agent_in_step_types(self):
        self.assertIn("skill_agent", STEP_TYPES)

    def test_prompt_file_and_skill_name_default_none(self):
        step = _step("s1")
        self.assertIsNone(step.prompt_file)
        self.assertIsNone(step.skill_name)

    def test_source_dir_default_none(self):
        wf = WorkflowDef(name="wf", steps=[_step("s1")])
        self.assertIsNone(wf.source_dir)

    def test_validate_requires_skill_name_for_skill_agent(self):
        wf = WorkflowDef(name="wf", steps=[_step("s1", type="skill_agent")])
        errors = wf.validate(check_placeholders=False)
        self.assertTrue(any("skill_name" in e for e in errors))

    def test_validate_passes_with_skill_name(self):
        wf = WorkflowDef(
            name="wf",
            steps=[_step("s1", type="skill_agent", skill_name="pdf-diff")],
        )
        errors = wf.validate(check_placeholders=False)
        self.assertEqual(errors, [])

    def test_validate_allows_empty_prompt_when_prompt_file_set(self):
        step = WorkflowStep(id="s1", name="s1", prompt="", prompt_file="prompts/s1.md")
        wf = WorkflowDef(name="wf", steps=[step])
        errors = wf.validate(check_placeholders=False)
        self.assertEqual(errors, [])

    def test_validate_still_rejects_empty_prompt_without_prompt_file(self):
        step = WorkflowStep(id="s1", name="s1", prompt="")
        wf = WorkflowDef(name="wf", steps=[step])
        errors = wf.validate(check_placeholders=False)
        self.assertTrue(any("prompt" in e and "为空" in e for e in errors))

    def test_to_dict_writes_prompt_file_not_expanded_prompt(self):
        step = WorkflowStep(
            id="s1", name="s1", prompt="展开后的文本，不应该被写回",
            prompt_file="prompts/s1.md",
        )
        wf = WorkflowDef(name="wf", steps=[step])
        d = wf.to_dict()
        step_dict = d["steps"][0]
        self.assertEqual(step_dict.get("prompt_file"), "prompts/s1.md")
        self.assertNotIn("prompt", step_dict)

    def test_to_dict_writes_prompt_when_no_prompt_file(self):
        wf = WorkflowDef(name="wf", steps=[_step("s1", prompt="hello")])
        step_dict = wf.to_dict()["steps"][0]
        self.assertEqual(step_dict.get("prompt"), "hello")
        self.assertNotIn("prompt_file", step_dict)

    def test_from_dict_roundtrip_skill_name(self):
        wf = WorkflowDef(
            name="wf",
            steps=[_step("s1", type="skill_agent", skill_name="pdf-diff")],
        )
        wf2 = WorkflowDef.from_dict(wf.to_dict())
        self.assertEqual(wf2.steps[0].skill_name, "pdf-diff")
        self.assertEqual(wf2.steps[0].effective_type, "skill_agent")


# ── 2. store.py ───────────────────────────────────────────────────────────

class TestWorkflowStoreDirectoryMode(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.store = WorkflowStore(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _wf(self, name="demo", prompt="inline text"):
        return WorkflowDef(name=name, steps=[_step("s1", prompt=prompt)], description="t")

    def test_save_as_dir_creates_subdirs(self):
        path = self.store.save_as_dir(self._wf())
        wf_dir = self.root / ".agent" / "workflows" / "demo"
        self.assertEqual(path, wf_dir / "workflow.yaml")
        for sub in ("agents", "skills", "prompts"):
            self.assertTrue((wf_dir / sub).is_dir())

    def test_load_prefers_directory_mode_over_flat(self):
        # 先写一个同名单文件，再升级为文件夹模式，加载应命中文件夹版本。
        wf = self._wf(prompt="flat version")
        self.store.save(wf)
        flat_path = self.root / ".agent" / "workflows" / "demo.yaml"
        self.assertTrue(flat_path.exists())

        dir_wf = self._wf(prompt="dir version")
        self.store.save_as_dir(dir_wf)

        loaded = self.store.load("demo")
        self.assertEqual(loaded.steps[0].prompt, "dir version")
        self.assertIsNotNone(loaded.source_dir)

    def test_prompt_file_resolved_on_load(self):
        wf_dir = self.root / ".agent" / "workflows" / "demo"
        step = WorkflowStep(id="s1", name="s1", prompt="", prompt_file="prompts/s1.md")
        wf = WorkflowDef(name="demo", steps=[step])
        self.store.save_as_dir(wf)
        (wf_dir / "prompts" / "s1.md").write_text("hello {input}", encoding="utf-8")

        loaded = self.store.load("demo")
        self.assertEqual(loaded.steps[0].prompt, "hello {input}")
        self.assertEqual(loaded.steps[0].prompt_file, "prompts/s1.md")

    def test_prompt_file_missing_warns_but_does_not_raise(self):
        step = WorkflowStep(id="s1", name="s1", prompt="", prompt_file="prompts/missing.md")
        wf = WorkflowDef(name="demo", steps=[step])
        self.store.save_as_dir(wf)
        # 不写 prompts/missing.md，加载不应抛异常
        loaded = self.store.load("demo")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.steps[0].prompt, "")

    def test_to_dir_migrates_flat_workflow(self):
        self.store.save(self._wf(prompt="flat"))
        flat_path = self.root / ".agent" / "workflows" / "demo.yaml"
        self.assertTrue(flat_path.exists())

        new_path = self.store.to_dir("demo")
        self.assertFalse(flat_path.exists(), "旧的单文件应该被删除，避免两种模式共存")
        self.assertTrue(new_path.exists())
        loaded = self.store.load("demo")
        self.assertEqual(loaded.steps[0].prompt, "flat")
        self.assertIsNotNone(loaded.source_dir)

    def test_to_dir_is_idempotent_if_already_dir_mode(self):
        self.store.save_as_dir(self._wf())
        path1 = self.store.to_dir("demo")
        path2 = self.store.to_dir("demo")
        self.assertEqual(path1, path2)

    def test_to_dir_raises_for_missing_workflow(self):
        with self.assertRaises(ValueError):
            self.store.to_dir("does_not_exist")

    def test_delete_removes_whole_directory(self):
        self.store.save_as_dir(self._wf())
        wf_dir = self.root / ".agent" / "workflows" / "demo"
        self.assertTrue(wf_dir.exists())
        self.assertTrue(self.store.delete("demo"))
        self.assertFalse(wf_dir.exists())

    def test_list_all_includes_both_modes(self):
        self.store.save(self._wf(name="flat_one"))
        self.store.save_as_dir(self._wf(name="dir_one"))
        names = {w["name"] for w in self.store.list_all()}
        self.assertEqual(names, {"flat_one", "dir_one"})

    def test_exists_and_export_yaml_work_for_directory_mode(self):
        self.store.save_as_dir(self._wf())
        self.assertTrue(self.store.exists("demo"))
        text = self.store.export_yaml("demo")
        self.assertIn("demo", text)


# ── 3. resource_bundle.py ─────────────────────────────────────────────────

class TestWorkflowResourceBundle(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self._tmpdir.name) / "my_pipeline"
        self.source_dir.mkdir(parents=True)
        self.cfg = _FakeCfg(project_root=self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_build_resource_bundle_none_when_no_source_dir(self):
        wf = WorkflowDef(name="wf", steps=[_step("s1")])
        self.assertIsNone(wf.source_dir)
        self.assertIsNone(build_resource_bundle(self.cfg, wf))

    def test_local_agent_profile_discovered(self):
        _write_agent_profile(self.source_dir / "agents" / "reviewer.md", "reviewer", "LOCAL")
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        profile = bundle.get_agent_profile("reviewer")
        self.assertIsNotNone(profile)
        self.assertIn("LOCAL", profile.system_prompt)

    def test_local_agent_profile_not_found_returns_none(self):
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        self.assertIsNone(bundle.get_agent_profile("does_not_exist"))

    def test_local_skill_discovered(self):
        _write_skill(self.source_dir / "skills" / "pdf-diff" / "SKILL.md", "pdf-diff", "LOCAL SKILL")
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        skill = bundle.get_skill("pdf-diff")
        self.assertIsNotNone(skill)
        self.assertIn("LOCAL SKILL", skill.content)

    def test_local_agent_overrides_same_name_project_profile(self):
        # 项目级 .agent/agents/reviewer.md 和本地 agents/reviewer.md 同名，
        # 本地目录在合并顺序里排在最后，应当覆盖生效。
        project_root = Path(self.cfg.project_root)
        _write_agent_profile(
            project_root / ".agent" / "agents" / "reviewer.md", "reviewer", "PROJECT",
        )
        _write_agent_profile(self.source_dir / "agents" / "reviewer.md", "reviewer", "LOCAL")
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        profile = bundle.get_agent_profile("reviewer")
        self.assertIn("LOCAL", profile.system_prompt)


# ── 4. runner.py 集成点 ────────────────────────────────────────────────────

class TestRunnerLocalResourceIntegration(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self._tmpdir.name) / "my_pipeline"
        self.source_dir.mkdir(parents=True)
        self.cfg = _FakeCfg(project_root=self._tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("mini_agent.config.load_config")
    @patch("mini_agent.agent.Agent")
    def test_execute_with_main_agent_passes_bundle_loaders(self, mock_agent_cls, mock_load_config):
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        self.runner._current_resource_bundle = bundle
        mock_load_config.return_value = MagicMock()
        mock_agent_cls.return_value.run_turn.return_value = "OUT"

        step = _step("s1")
        result = self.runner._execute_with_main_agent(step, "hi")

        self.assertEqual(result, "OUT")
        _, kwargs = mock_agent_cls.call_args
        self.assertIs(kwargs.get("skill_loader"), bundle.skill_loader)
        self.assertIs(kwargs.get("agent_profile_loader"), bundle.agent_loader)

    @patch("mini_agent.config.load_config")
    @patch("mini_agent.agent.Agent")
    def test_execute_with_main_agent_no_bundle_keeps_old_behavior(self, mock_agent_cls, mock_load_config):
        # 单文件模式（无 resource bundle）：不传 skill_loader/agent_profile_loader，
        # 与改动前的行为保持一致。
        self.runner._current_resource_bundle = None
        mock_load_config.return_value = MagicMock()
        mock_agent_cls.return_value.run_turn.return_value = "OUT"

        result = self.runner._execute_with_main_agent(_step("s1"), "hi")

        self.assertEqual(result, "OUT")
        _, kwargs = mock_agent_cls.call_args
        self.assertIsNone(kwargs.get("skill_loader"))
        self.assertIsNone(kwargs.get("agent_profile_loader"))

    def test_execute_with_role_agent_prefers_local_bundle_profile(self):
        _write_agent_profile(self.source_dir / "agents" / "reviewer.md", "reviewer", "LOCAL")
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        self.runner._current_resource_bundle = bundle

        fake_dispatcher = MagicMock()
        fake_dispatcher._loader.get.return_value = None  # 全局没有同名 profile 也无所谓
        fake_dispatcher._run_custom_role.return_value = "ROLE OUTPUT"

        with patch("mini_agent.role_agents.get_dispatcher", return_value=fake_dispatcher):
            step = _step("s1", type="role_agent", role="reviewer")
            result = self.runner._execute_with_role_agent(step, "hi")

        self.assertEqual(result, "ROLE OUTPUT")
        fake_dispatcher._loader.get.assert_not_called()
        called_profile = fake_dispatcher._run_custom_role.call_args[0][0]
        self.assertIn("LOCAL", called_profile.system_prompt)

    def test_execute_with_role_agent_falls_back_to_global_dispatcher(self):
        # 本地没有该 profile 时，应退回全局 dispatcher._loader。
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        self.runner._current_resource_bundle = bundle

        fake_global_profile = MagicMock(role_type="custom")
        fake_dispatcher = MagicMock()
        fake_dispatcher._loader.get.return_value = fake_global_profile
        fake_dispatcher._run_custom_role.return_value = "GLOBAL OUTPUT"

        with patch("mini_agent.role_agents.get_dispatcher", return_value=fake_dispatcher):
            step = _step("s1", type="role_agent", role="global_only")
            result = self.runner._execute_with_role_agent(step, "hi")

        self.assertEqual(result, "GLOBAL OUTPUT")
        fake_dispatcher._loader.get.assert_called_once_with("global_only")
        fake_dispatcher._run_custom_role.assert_called_once_with(
            fake_global_profile, "hi", "工作流步骤：s1", parent_session_dir=None,
        )


# ── 5. executors.py: SkillAgentStepExecutor ────────────────────────────────

class TestSkillAgentStepExecutor(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.source_dir = Path(self._tmpdir.name) / "my_pipeline"
        self.source_dir.mkdir(parents=True)
        self.cfg = _FakeCfg(project_root=self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _mock_runner(self, bundle=None):
        runner = MagicMock()
        runner._cfg = self.cfg
        runner._current_resource_bundle = bundle
        return runner

    def test_missing_skill_name_raises(self):
        step = _step("s1", type="skill_agent")  # 没有 skill_name
        with self.assertRaises(ValueError):
            wf_executors.SkillAgentStepExecutor().execute(self._mock_runner(), step, "hi")

    def test_unknown_skill_without_bundle_or_global_dir_raises(self):
        # [next_doc/workflow_python_step_and_zhihu_publish_plan.md §B3]
        # "未知 skill" 的校验逻辑已下沉到 agent_spawn.build_minimal_agent()
        # （SkillAgentStepExecutor 现在只委托给 runner._spawn_minimal_agent，
        # 不再自己做 skill 查找），这里改成让 runner._spawn_minimal_agent 走
        # 真实实现（不 mock 掉），验证异常仍然会被抛出到调用方。
        from mini_agent.workflow.runner import WorkflowRunner
        step = _step("s1", type="skill_agent", skill_name="does_not_exist")
        runner = self._mock_runner()
        runner._spawn_minimal_agent = WorkflowRunner._spawn_minimal_agent.__get__(runner, WorkflowRunner)
        runner._effective_step_field = lambda step, name, default: default
        with self.assertRaises(ValueError):
            wf_executors.SkillAgentStepExecutor().execute(runner, step, "hi")

    @patch("mini_agent.config.load_config")
    @patch("mini_agent.agent.Agent")
    def test_uses_local_skill_from_bundle(self, mock_agent_cls, mock_load_config):
        from mini_agent.workflow.runner import WorkflowRunner
        _write_skill(self.source_dir / "skills" / "pdf-diff" / "SKILL.md", "pdf-diff", "LOCAL SKILL")
        bundle = WorkflowResourceBundle(self.cfg, self.source_dir)
        runner = self._mock_runner(bundle=bundle)
        runner._spawn_minimal_agent = WorkflowRunner._spawn_minimal_agent.__get__(runner, WorkflowRunner)
        runner._effective_step_field = lambda step, name, default: default
        mock_load_config.return_value = MagicMock()
        mock_agent_cls.return_value.run_turn.return_value = "SKILL OUT"

        step = _step("s1", type="skill_agent", skill_name="pdf-diff")
        result = wf_executors.SkillAgentStepExecutor().execute(runner, step, "hi")

        self.assertEqual(result, "SKILL OUT")
        _, kwargs = mock_agent_cls.call_args
        self.assertIs(kwargs.get("skill_loader"), bundle.skill_loader)

    def test_get_executor_returns_skill_agent_executor(self):
        executor = wf_executors.get_executor("skill_agent")
        self.assertIsInstance(executor, wf_executors.SkillAgentStepExecutor)


if __name__ == "__main__":
    unittest.main()
