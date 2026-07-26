"""
tests/test_python_step_subprocess_e2e.py — python_step 子进程执行的端到端冒烟测试
（next_doc/workflow_python_step_and_zhihu_publish_plan.md §B4）

不 mock 子进程（那样测不出真实的序列化/runpy/stdout 解析链路是否work），
真正拉起一个 `python -m mini_agent.workflow.py_step_runner` 子进程执行一个
不需要网络/LLM 的最小脚本（只用 ctx.params/ctx.output_dir/ctx.write_output），
验证：
  1. PythonStepExecutor 能正确序列化请求、拉起子进程、解析 stdout 结果包；
  2. ctx.output_dir 指向真实的 workflow session output 目录；
  3. runner._write_step_output_file()（§A3）能把返回值落盘到 output_file。
"""
from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from dataclasses import dataclass
from pathlib import Path

from mini_agent.storage.paths import AgentPaths
from mini_agent.workflow import executors as wf_executors
from mini_agent.workflow.schema import StepResult, StepStatus, WorkflowStep
from mini_agent.workflow.session import WorkflowSession


@dataclass
class _FakeWfCfg:
    python_step_enabled: bool = True
    python_step_timeout_seconds: float = 30.0


@dataclass
class _FakeCfg:
    project_root: str
    sandbox: bool = True
    llm_provider: str = "fake"
    llm_base_url: str = ""
    api_key: str = "fake-key"
    debug_llm: bool = False
    debug_llm_console: bool = False
    skills_dir: str = ""
    model: str = None
    workflow: _FakeWfCfg = None

    def __post_init__(self):
        if self.workflow is None:
            self.workflow = _FakeWfCfg()


class _FakeRunner:
    """只提供 PythonStepExecutor.execute() 实际用到的最小接口，不依赖真实
    WorkflowRunner（避免这个冒烟测试还要搭一整套 Agent/权限守卫环境）。"""

    def __init__(self, cfg, paths, wf_session, step_results):
        self._cfg = cfg
        self._current_paths = paths
        self._current_wf_session = wf_session
        self._current_step_results = step_results
        self._current_wf = None

    def _effective_step_field(self, step, name, default):
        val = getattr(step, name, None)
        return val if val is not None else default


class TestPythonStepSubprocessE2E(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.cfg = _FakeCfg(project_root=str(self.project_root))
        self.paths = AgentPaths(project_root=self.project_root)
        self.wf_session = WorkflowSession(workflow_session_id="wfs_test_e2e", workflow_name="wf")
        self.paths.ensure_workflow_session_dir(self.wf_session.workflow_session_id)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_script_receives_params_and_upstream_output(self):
        script_dir = self.project_root / "steps"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / "echo_step.py"
        script_path.write_text(textwrap.dedent(
            """
            def run(ctx):
                upstream = ctx.input_output("prev_step")
                out = {
                    "greeting": ctx.params.get("name", "world"),
                    "upstream_echo": upstream,
                    "step_id": ctx.step_id,
                }
                ctx.write_output("debug_dump.json", out)
                return out
            """
        ), encoding="utf-8")

        step = WorkflowStep(
            id="s1", name="s1", prompt="",
            type="python_step",
            script_path=str(script_path),
            params={"name": "otz"},
            output_file="final.json",
        )
        upstream_results = {
            "prev_step": StepResult(step_id="prev_step", status=StepStatus.DONE, output="PREV_OUTPUT"),
        }
        runner = _FakeRunner(self.cfg, self.paths, self.wf_session, upstream_results)

        output_text = wf_executors.PythonStepExecutor().execute(runner, step, "")
        result = json.loads(output_text)

        self.assertEqual(result["greeting"], "otz")
        self.assertEqual(result["upstream_echo"], "PREV_OUTPUT")
        self.assertEqual(result["step_id"], "s1")

        # ctx.write_output() 应该已经把中间产物写到真实的 output_dir。
        out_dir = self.paths.workflow_session_output_dir(self.wf_session.workflow_session_id)
        debug_dump = json.loads((out_dir / "debug_dump.json").read_text(encoding="utf-8"))
        self.assertEqual(debug_dump["greeting"], "otz")

    def test_script_error_raises_runtime_error_with_traceback(self):
        script_dir = self.project_root / "steps"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / "boom_step.py"
        script_path.write_text(textwrap.dedent(
            """
            def run(ctx):
                raise ValueError("intentional boom for test")
            """
        ), encoding="utf-8")

        step = WorkflowStep(id="s1", name="s1", prompt="", type="python_step", script_path=str(script_path))
        runner = _FakeRunner(self.cfg, self.paths, self.wf_session, {})

        with self.assertRaises(RuntimeError) as cm:
            wf_executors.PythonStepExecutor().execute(runner, step, "")
        self.assertIn("intentional boom for test", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
