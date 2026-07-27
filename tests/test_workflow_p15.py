"""
tests/test_workflow_p15.py — [workflow_mechanism_improvement_plan_p15.md] 单元测试

覆盖 script 类型的 result_file structured 模式：
  - 未声明 result_file 时行为不变（回归）
  - 脚本正确把 JSON 写入 $WORKFLOW_RESULT_FILE_PATH 时校验通过，
    runner._step_result_file_paths 被填充
  - 脚本未写文件时，即使 returncode==0 也报错
  - result_file_required_keys 缺失字段时报错
  - 端到端集成：走真实 WorkflowRunner.run()，script step 产出 result_file
    后下游 python_step 能读到该路径并解析出内容
"""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from dataclasses import dataclass, field
from pathlib import Path

from mini_agent.workflow.schema import WorkflowDef, WorkflowStep, StepResult, StepStatus
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
    watchdog_enabled: bool = False
    script_step_enabled: bool = True
    script_step_timeout_seconds: float = 10.0
    python_step_enabled: bool = True
    python_step_timeout_seconds: float = 30.0
    python_step_inputs_filtered_by_depends_on: bool = True


@dataclass
class _FakeCfg:
    project_root: str = "/tmp"
    workflow: _FakeWorkflowConfig = field(default_factory=_FakeWorkflowConfig)
    sandbox: bool = True
    llm_provider: str = "fake"
    llm_base_url: str = ""
    api_key: str = "fake-key"
    debug_llm: bool = False
    debug_llm_console: bool = False
    skills_dir: str = ""
    model: str = None


# ── Phase 1: script result_file structured 模式（单元） ───────────────────────

class TestScriptResultFile(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cfg = _FakeCfg(project_root=self.tmpdir.name)
        self.runner = WorkflowRunner(self.cfg)
        # resolve_result_file_path 需要 _current_wf_session/_current_paths；
        # 用真实 WorkflowSession/AgentPaths 搭一个最小上下文，不依赖 run()。
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.workflow.session import WorkflowSession

        self.paths = AgentPaths(project_root=Path(self.tmpdir.name))
        self.wf_session = WorkflowSession(workflow_session_id="wfs_p15", workflow_name="wf")
        self.paths.ensure_workflow_session_dir(self.wf_session.workflow_session_id)
        self.runner._current_paths = self.paths
        self.runner._current_wf_session = self.wf_session

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_no_result_file_behaves_unchanged(self):
        step = _step("a", type="script", script="echo hello_world")
        out = wf_executors.ScriptStepExecutor().execute(self.runner, step, "hi")
        self.assertIn("hello_world", out)

    def test_result_file_written_correctly_passes_validation(self):
        step = _step(
            "a", type="script",
            script='python3 -c "import os,json; json.dump({\'total\': 3}, '
                   'open(os.environ[\'WORKFLOW_RESULT_FILE_PATH\'], \'w\'))"',
            result_file="a_result.json",
        )
        out = wf_executors.ScriptStepExecutor().execute(self.runner, step, "hi")
        self.assertIsInstance(out, str)
        recorded = self.runner._step_result_file_paths.get("a")
        self.assertIsNotNone(recorded)
        data = json.loads(Path(recorded).read_text(encoding="utf-8"))
        self.assertEqual(data["total"], 3)

    def test_result_file_not_written_raises_even_if_returncode_zero(self):
        step = _step("a", type="script", script="echo did_not_write_file", result_file="a_result.json")
        with self.assertRaises(RuntimeError):
            wf_executors.ScriptStepExecutor().execute(self.runner, step, "hi")

    def test_result_file_missing_required_keys_raises(self):
        step = _step(
            "a", type="script",
            script='python3 -c "import os,json; json.dump({\'other\': 1}, '
                   'open(os.environ[\'WORKFLOW_RESULT_FILE_PATH\'], \'w\'))"',
            result_file="a_result.json",
            result_file_required_keys=["total"],
        )
        with self.assertRaises(RuntimeError):
            wf_executors.ScriptStepExecutor().execute(self.runner, step, "hi")


# ── Phase 1: 端到端集成（真实 WorkflowRunner.run()） ───────────────────────────

class TestScriptResultFileIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_downstream_python_step_reads_script_result_file(self):
        script_dir = Path(self.tmpdir.name) / "steps"
        script_dir.mkdir(parents=True, exist_ok=True)
        consumer_path = script_dir / "consumer.py"
        consumer_path.write_text(textwrap.dedent(
            """
            import json

            def run(ctx):
                # ctx.input_output() 优先读 result_file 内容（见
                # py_context.py::PyStepContext.input_output），不用关心
                # 上游 producer 是 script 还是 skill_agent 产出的。
                data = json.loads(ctx.input_output("producer"))
                return {"total_seen": data["total"]}
            """
        ), encoding="utf-8")

        cfg = _FakeCfg(project_root=self.tmpdir.name)
        wf = WorkflowDef(name="wf_script_result_file", steps=[
            _step(
                "producer", type="script",
                script='python3 -c "import os,json; json.dump({\'total\': 3}, '
                       'open(os.environ[\'WORKFLOW_RESULT_FILE_PATH\'], \'w\'))"',
                result_file="producer_result.json",
            ),
            _step(
                "consumer", type="python_step",
                script_path=str(consumer_path),
                depends_on=["producer"],
            ),
        ])

        runner = WorkflowRunner(cfg)
        result = runner.run(wf)

        by_id = {r.step_id: r for r in result.step_results}
        self.assertEqual(by_id["producer"].status, StepStatus.DONE)
        self.assertIsNotNone(by_id["producer"].result_file)
        self.assertEqual(by_id["consumer"].status, StepStatus.DONE)
        consumer_out = json.loads(by_id["consumer"].output)
        self.assertEqual(consumer_out["total_seen"], 3)


if __name__ == "__main__":
    unittest.main()
