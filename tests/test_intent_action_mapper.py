"""
tests/test_intent_action_mapper.py — [具身改进 工具透明性] IntentActionMapper 测试

覆盖：
  1. 单一意图类别的连续工具调用被合并为一个 ActionEvent
  2. 意图类别切换时正确开启新的 ActionEvent
  3. bash 工具按命令内容细分类别（test_run / env_setup / vcs_op / other）
  4. error_count 统计正确（依赖 is_tool_error 判断）
  5. 空输入 / 单个调用的边界情况
  6. summarize() 生成的摘要文本格式
"""

from __future__ import annotations

import unittest

from mini_agent.llm.base import ToolCall
from mini_agent.perception.intent_action_mapper import ActionEvent, IntentActionMapper


def _tc(name: str, **input_kwargs) -> ToolCall:
    return ToolCall(id="x", name=name, input=input_kwargs)


class TestGroupCallsBasic(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        self.assertEqual(IntentActionMapper.group_calls([]), [])

    def test_single_call_creates_single_event(self):
        events = IntentActionMapper.group_calls([_tc("read_file", path="a.py")])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "exploration")
        self.assertEqual(events[0].call_count, 1)
        self.assertEqual(events[0].tool_names, ["read_file"])

    def test_consecutive_same_intent_merged(self):
        calls = [
            _tc("read_file", path="a.py"),
            _tc("read_file", path="b.py"),
            _tc("grep", pattern="x"),
        ]
        events = IntentActionMapper.group_calls(calls)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].intent, "exploration")
        self.assertEqual(events[0].call_count, 3)
        self.assertEqual(events[0].tool_names, ["read_file", "grep"])

    def test_intent_switch_creates_new_event(self):
        calls = [
            _tc("read_file", path="a.py"),
            _tc("patch_file", path="a.py"),
            _tc("read_file", path="b.py"),
        ]
        events = IntentActionMapper.group_calls(calls)
        self.assertEqual(len(events), 3)
        self.assertEqual([e.intent for e in events], ["exploration", "code_edit", "exploration"])
        self.assertEqual(events[0].start_index, 0)
        self.assertEqual(events[1].start_index, 1)
        self.assertEqual(events[2].start_index, 2)


class TestBashClassification(unittest.TestCase):
    def test_pytest_command_is_test_run(self):
        events = IntentActionMapper.group_calls([_tc("bash", command="pytest tests/")])
        self.assertEqual(events[0].intent, "test_run")

    def test_pip_install_is_env_setup(self):
        events = IntentActionMapper.group_calls([_tc("bash", command="pip install requests")])
        self.assertEqual(events[0].intent, "env_setup")

    def test_git_commit_is_vcs_op(self):
        events = IntentActionMapper.group_calls([_tc("bash", command="git commit -m 'x'")])
        self.assertEqual(events[0].intent, "vcs_op")

    def test_generic_bash_is_other(self):
        events = IntentActionMapper.group_calls([_tc("bash", command="echo hello")])
        self.assertEqual(events[0].intent, "other")

    def test_unknown_tool_name_is_other(self):
        events = IntentActionMapper.group_calls([_tc("totally_unknown_tool")])
        self.assertEqual(events[0].intent, "other")


class TestErrorCounting(unittest.TestCase):
    def test_error_count_tracks_tool_errors(self):
        calls = [_tc("read_file", path="a"), _tc("read_file", path="b")]
        results = ["ok content", "[tool error: file not found]"]
        events = IntentActionMapper.group_calls(calls, results)
        self.assertEqual(events[0].call_count, 2)
        self.assertEqual(events[0].error_count, 1)

    def test_missing_results_defaults_to_zero_errors(self):
        calls = [_tc("read_file", path="a")]
        events = IntentActionMapper.group_calls(calls, None)
        self.assertEqual(events[0].error_count, 0)

    def test_short_results_list_does_not_crash(self):
        calls = [_tc("read_file", path="a"), _tc("read_file", path="b")]
        events = IntentActionMapper.group_calls(calls, ["ok"])  # 比 calls 短一个
        self.assertEqual(events[0].call_count, 2)
        self.assertEqual(events[0].error_count, 0)


class TestSummaryAndDict(unittest.TestCase):
    def test_to_summary_text_includes_error_count(self):
        event = ActionEvent(intent="code_edit", tool_names=["patch_file"], call_count=2, error_count=1)
        text = event.to_summary_text()
        self.assertIn("代码编辑", text)
        self.assertIn("×2", text)
        self.assertIn("1 次出错", text)

    def test_to_summary_text_without_errors_omits_error_clause(self):
        event = ActionEvent(intent="exploration", tool_names=["read_file"], call_count=1, error_count=0)
        text = event.to_summary_text()
        self.assertNotIn("出错", text)

    def test_summarize_joins_multiple_events(self):
        events = [
            ActionEvent(intent="exploration", tool_names=["read_file"], call_count=1),
            ActionEvent(intent="code_edit", tool_names=["patch_file"], call_count=1),
        ]
        text = IntentActionMapper.summarize(events)
        self.assertIn("；", text)

    def test_summarize_empty_list_returns_empty_string(self):
        self.assertEqual(IntentActionMapper.summarize([]), "")

    def test_to_dict_contains_expected_keys(self):
        event = ActionEvent(intent="exploration", tool_names=["read_file"], call_count=1)
        d = event.to_dict()
        self.assertEqual(set(d.keys()), {
            "intent", "label", "tool_names", "call_count", "error_count", "start_index",
        })


if __name__ == "__main__":
    unittest.main()
