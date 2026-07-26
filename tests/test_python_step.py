"""
tests/test_python_step.py — python_step 相关单测
（next_doc/workflow_python_step_and_zhihu_publish_plan.md §B）

覆盖范围：
  - schema.py：script_path/params/output_file 字段的序列化/反序列化 + validate()
  - executors.py：PythonStepExecutor 的 python_step_enabled 开关拦截
  - py_context.py：PyStepLLM.ask_json 的解析/重试逻辑（不触发真实网络请求，
    用 mock helper 模拟 LLMHelper.ask 的返回）
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from mini_agent.workflow import executors as wf_executors
from mini_agent.workflow.py_context import PyStepLLM
from mini_agent.workflow.schema import WorkflowDef, WorkflowStep


def _step(step_id: str, **kwargs) -> WorkflowStep:
    kwargs.setdefault("prompt", "")
    return WorkflowStep(id=step_id, name=step_id, **kwargs)


class TestSchemaPythonStepFields(unittest.TestCase):
    def test_round_trip_script_path_params_output_file(self):
        wf = WorkflowDef(
            name="wf",
            steps=[
                _step(
                    "s1",
                    type="python_step",
                    script_path="steps/01.py",
                    params={"doc_path": "/tmp/a.md"},
                    output_file="doc_analysis.json",
                )
            ],
        )
        data = wf.to_dict()
        self.assertEqual(data["steps"][0]["script_path"], "steps/01.py")
        self.assertEqual(data["steps"][0]["params"], {"doc_path": "/tmp/a.md"})
        self.assertEqual(data["steps"][0]["output_file"], "doc_analysis.json")

        wf2 = WorkflowDef.from_dict(data)
        s = wf2.steps[0]
        self.assertEqual(s.script_path, "steps/01.py")
        self.assertEqual(s.params, {"doc_path": "/tmp/a.md"})
        self.assertEqual(s.output_file, "doc_analysis.json")

    def test_validate_requires_script_path_for_python_step(self):
        wf = WorkflowDef(name="wf", steps=[_step("s1", type="python_step")])
        errors = wf.validate(check_placeholders=False, check_condition=False)
        self.assertTrue(any("script_path" in e for e in errors))

    def test_validate_does_not_require_prompt_for_python_step(self):
        wf = WorkflowDef(
            name="wf",
            steps=[_step("s1", type="python_step", script_path="steps/01.py")],
        )
        errors = wf.validate(check_placeholders=False, check_condition=False)
        self.assertFalse(any("prompt 为空" in e for e in errors))

    def test_validate_warns_on_long_inline_prompt(self):
        long_prompt = "\n".join(f"line {i}" for i in range(8))
        wf = WorkflowDef(name="wf", steps=[_step("s1", prompt=long_prompt)])
        wf.validate(check_placeholders=False, check_condition=False)
        self.assertTrue(
            any("prompt_file" in w for w in getattr(wf, "last_validate_warnings", []))
        )


class TestPythonStepExecutorGating(unittest.TestCase):
    def test_disabled_by_default_raises_permission_error(self):
        step = _step("s1", type="python_step", script_path="steps/01.py")
        runner = MagicMock()
        runner._cfg = SimpleNamespace(workflow=SimpleNamespace(python_step_enabled=False))
        with self.assertRaises(PermissionError):
            wf_executors.PythonStepExecutor().execute(runner, step, "hi")

    def test_missing_script_path_raises(self):
        step = _step("s1", type="python_step")
        runner = MagicMock()
        runner._cfg = SimpleNamespace(workflow=SimpleNamespace(python_step_enabled=True))
        with self.assertRaises(ValueError):
            wf_executors.PythonStepExecutor().execute(runner, step, "hi")

    def test_get_executor_returns_python_step_executor(self):
        executor = wf_executors.get_executor("python_step")
        self.assertIsInstance(executor, wf_executors.PythonStepExecutor)


class TestPyStepLLMAskJson(unittest.TestCase):
    def test_parses_clean_json(self):
        helper = MagicMock()
        helper.ask.return_value = '{"decisions": [{"id": "q1", "keep": true}]}'
        llm = PyStepLLM(helper)
        result = llm.ask_json("判断问题是否符合要求", schema_hint="{...}")
        self.assertEqual(result["decisions"][0]["id"], "q1")
        self.assertTrue(result["decisions"][0]["keep"])

    def test_strips_markdown_code_fence(self):
        helper = MagicMock()
        helper.ask.return_value = '```json\n{"ok": true}\n```'
        llm = PyStepLLM(helper)
        result = llm.ask_json("test")
        self.assertEqual(result, {"ok": True})

    def test_retries_on_unparseable_output_then_succeeds(self):
        helper = MagicMock()
        helper.ask.side_effect = ["not json at all {{{", '{"ok": true}']
        llm = PyStepLLM(helper)
        result = llm.ask_json("test", max_retries=2)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(helper.ask.call_count, 2)

    def test_raises_after_exhausting_retries(self):
        helper = MagicMock()
        helper.ask.return_value = "definitely not json {{{"
        llm = PyStepLLM(helper)
        with self.assertRaises(ValueError):
            llm.ask_json("test", max_retries=2)
        self.assertEqual(helper.ask.call_count, 2)


if __name__ == "__main__":
    unittest.main()
