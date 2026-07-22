"""
tests/test_session_to_workflow.py — session_to_workflow_design.md（P8）覆盖测试

覆盖：
  1. build_timeline_text()：用户轮次 / ActionEvent 摘要 / assistant 阶段文本
     交替拼接是否符合 2.1-2.3 节格式
  2. TaskSummary.from_dict() / to_markdown()：结构解析与人工确认展示格式
  3. _parse_task_summary()：```json 围栏剥离、非法 JSON 静默降级为空 dict
  4. summarize_session_for_workflow()：mock Agent.run_turn，验证起临时 Agent
     + 解析 + 空结果报错的整体链路
  5. WorkflowGenerator._downgrade_unknown_tool_types()：未注册 tool_name 的
     step 被降级为 type: agent
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mini_agent.history.entry import make_user_input, make_assistant_reply, make_tool_result
from mini_agent.workflow.session_summarizer import (
    TaskSummary,
    TaskStage,
    build_timeline_text,
)
from mini_agent.agent._helpers import _parse_task_summary


def _tool_use_msg(calls: list[tuple[str, dict]], text: str = ""):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for name, inp in calls:
        content.append({"type": "tool_use", "id": f"id_{name}", "name": name, "input": inp})
    return make_assistant_reply(content)


def _tool_result_msg(entries: list[tuple[str, str]]):
    """entries: [(tool_name, output_str), ...] — 拼成 render_tool_results() 风格的文本。"""
    import json
    parts = []
    for name, output in entries:
        parts.append(
            "<tool_result>\n" + json.dumps({"name": name, "output": output}, ensure_ascii=False) + "\n</tool_result>"
        )
    return make_tool_result("\n\n".join(parts))


class TestBuildTimelineText(unittest.TestCase):
    def test_interleaves_user_actions_and_assistant_text(self):
        history = [
            make_user_input("帮我修一下 xxx 报的这个 bug"),
            _tool_use_msg([("read_file", {"path": "foo.py"}), ("grep", {"q": "null"})], text=""),
            _tool_result_msg([("read_file", "...ok..."), ("grep", "no match")]),
            _tool_use_msg([], text="定位到问题在 foo.py 的空指针检查缺失"),
            _tool_use_msg([("patch_file", {"path": "foo.py"})]),
            _tool_result_msg([("patch_file", "[error] syntax error")]),
            _tool_use_msg([("patch_file", {"path": "foo.py"})]),
            _tool_result_msg([("patch_file", "ok")]),
            _tool_use_msg([], text="测试通过，修复完成"),
        ]
        text = build_timeline_text(history)

        self.assertIn("[用户] 帮我修一下 xxx 报的这个 bug", text)
        self.assertIn("探索/检索代码", text)
        self.assertIn("[assistant] 定位到问题在 foo.py 的空指针检查缺失", text)
        self.assertIn("代码编辑", text)
        # 第一次 patch_file 出错，应体现在摘要里
        self.assertIn("1 次出错", text)
        self.assertIn("[assistant] 测试通过，修复完成", text)

    def test_empty_history_yields_empty_text(self):
        self.assertEqual(build_timeline_text([]), "")


class TestParseTaskSummary(unittest.TestCase):
    def test_parses_plain_json(self):
        data = _parse_task_summary('{"goal": "g", "stages": []}')
        self.assertEqual(data["goal"], "g")

    def test_strips_markdown_fence(self):
        raw = '```json\n{"goal": "g", "stages": []}\n```'
        data = _parse_task_summary(raw)
        self.assertEqual(data["goal"], "g")

    def test_invalid_json_returns_empty_dict(self):
        self.assertEqual(_parse_task_summary("not json at all"), {})

    def test_empty_text_returns_empty_dict(self):
        self.assertEqual(_parse_task_summary(""), {})


class TestTaskSummary(unittest.TestCase):
    def test_from_dict_and_markdown(self):
        summary = TaskSummary.from_dict({
            "goal": "修复空指针 bug",
            "final_outcome": "修复完成，测试通过",
            "stages": [
                {"id": "analyze", "purpose": "定位问题", "approach": "检索代码",
                 "depends_on_stage_ids": [], "had_retries": False, "gate_candidate": False},
                {"id": "fix", "purpose": "修复代码", "approach": "补边界条件",
                 "depends_on_stage_ids": ["analyze"], "had_retries": True,
                 "retry_note": "漏了一个边界条件", "gate_candidate": True},
            ],
            "candidate_parameters": [
                {"name": "bug_description", "example_value": "空指针问题", "source": "首个用户输入"},
            ],
            "repeated_pattern": None,
        })

        self.assertEqual(len(summary.stages), 2)
        self.assertTrue(summary.stages[1].gate_candidate)
        md = summary.to_markdown()
        self.assertIn("修复空指针 bug", md)
        self.assertIn("analyze", md)
        self.assertIn("⚠️", md)  # gate_candidate 提示
        self.assertIn("bug_description", md)

    def test_missing_fields_default_safely(self):
        summary = TaskSummary.from_dict({})
        self.assertEqual(summary.goal, "")
        self.assertEqual(summary.stages, [])
        self.assertIn("未提取到", summary.to_markdown())


class TestSummarizeSessionForWorkflow(unittest.TestCase):
    def test_raises_on_empty_timeline(self):
        from mini_agent.workflow.session_summarizer import summarize_session_for_workflow

        with self.assertRaises(ValueError):
            summarize_session_for_workflow([], cfg=None)  # timeline 为空应在起 Agent 前就报错

    def test_raises_when_llm_returns_unusable_summary(self):
        from mini_agent.workflow import session_summarizer as mod

        history = [make_user_input("do something")]

        class _FakeAgent:
            def __init__(self, *a, **kw):
                pass

            def run_turn(self, prompt):
                return "not valid json"

        fake_cfg = type("Cfg", (), {
            "project_root": ".", "sandbox": None, "model": "m",
            "llm_provider": "anthropic", "llm_base_url": None, "api_key": "k",
        })()

        with patch("mini_agent.config.load_config", return_value=fake_cfg), \
             patch("mini_agent.agent.Agent", _FakeAgent), \
             patch("mini_agent.permissions.PermissionGuard", lambda **kw: None), \
             patch("mini_agent.tools.get_default_registry") as mock_registry:
            mock_registry.return_value.filtered.return_value = None
            with self.assertRaises(ValueError):
                mod.summarize_session_for_workflow(history, fake_cfg)


class TestDowngradeUnknownToolTypes(unittest.TestCase):
    def test_downgrades_unregistered_tool_name(self):
        from mini_agent.workflow.generator import WorkflowGenerator

        gen = WorkflowGenerator.__new__(WorkflowGenerator)  # 不需要真实 cfg
        yaml_str = (
            "name: wf\n"
            "steps:\n"
            "  - id: s1\n"
            "    name: s1\n"
            "    prompt: p\n"
            "    type: tool_call\n"
            "    tool_name: made_up_tool\n"
        )
        result = gen._downgrade_unknown_tool_types(yaml_str, registered_tool_names={"read_file", "bash"})
        self.assertNotIn("made_up_tool", result)
        self.assertIn("type: agent", result)

    def test_keeps_registered_tool_name(self):
        from mini_agent.workflow.generator import WorkflowGenerator

        gen = WorkflowGenerator.__new__(WorkflowGenerator)
        yaml_str = (
            "name: wf\n"
            "steps:\n"
            "  - id: s1\n"
            "    name: s1\n"
            "    prompt: p\n"
            "    type: tool_call\n"
            "    tool_name: bash\n"
        )
        result = gen._downgrade_unknown_tool_types(yaml_str, registered_tool_names={"read_file", "bash"})
        self.assertIn("tool_name: bash", result)


if __name__ == "__main__":
    unittest.main()
