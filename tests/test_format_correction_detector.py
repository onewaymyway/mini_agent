"""
tests/test_format_correction_detector.py — 工具调用格式纠错检测器验证

背景：模型有时会"意图"调用工具（输出中含 <tool_use> / <tool_result> 等
协议关键字），但因为标签未闭合、标签角色混淆、JSON 截断等格式问题，
导致 parse_tool_calls() 解析失败（tool_calls=[]）。

本模块覆盖：
  - 用户报告的两个真实失败案例（必须命中对应规则）
  - 各类规则的正例 / 负例
  - 与 correction_detector.py 同样的克制原则：宁可漏检，不可误判
    （误判会对一句正常的最终回复强行要求模型重试，打断对话）
"""

from __future__ import annotations

import pytest

from mini_agent.perception.format_correction_detector import (
    FormatIssue,
    detect_format_issue,
)


# ── 真实失败案例（来自用户报告，必须被正确识别）──────────────────────────────

def test_real_case_1_unclosed_duplicated_open_tag():
    """案例1：<tool_use> 出现两次，JSON 未写完，没有任何 </tool_use> 闭合标签。"""
    text = (
        "我来帮你探索控制手机振动的方案。首先让我了解一下当前环境的情况。\n\n"
        "<tool_use>\n"
        '{"name": "bash",\n'
        "<tool_use>"
    )
    issue = detect_format_issue(text)
    assert issue is not None
    assert issue.issue_type == "unclosed_tool_use"
    assert isinstance(issue.message, str) and issue.message


def test_real_case_2_tag_role_confusion():
    """案例2：开标签错用 <tool_result>，内容是 input 请求格式，闭标签又错用 </tool_use>。"""
    text = (
        "<tool_result>"
        '{"name": "bash", "input": {"command": "echo hi", "timeout": 10}}\n'
        "</tool_use>"
    )
    issue = detect_format_issue(text)
    assert issue is not None
    assert issue.issue_type == "tag_role_confusion"


# ── 正例：应该被检测为格式问题（按规则类型分组）──────────────────────────────

@pytest.mark.parametrize("text", [
    # 开标签后什么都没有
    "好的，我来执行一下。\n<tool_use>",
    # 开标签出现两次，第二次也没闭合
    '<tool_use>\n{"name": "bash", "input":\n<tool_use>\n{"name": "read_file"',
    # 只有一个孤立的开标签，JSON 完整但缺收尾标签
    '<tool_use>\n{"name": "write_file", "input": {"path": "./a.py", "content": "x"}}',
])
def test_unclosed_tool_use_positive(text):
    issue = detect_format_issue(text)
    assert issue is not None
    assert issue.issue_type == "unclosed_tool_use"


@pytest.mark.parametrize("text", [
    '<tool_result>{"name": "bash", "input": {"command": "ls"}}</tool_use>',
    '<tool_use>{"name": "bash", "input": {"command": "ls"}}</tool_result>',
])
def test_tag_role_confusion_positive(text):
    issue = detect_format_issue(text)
    assert issue is not None
    assert issue.issue_type == "tag_role_confusion"


def test_invalid_json_in_closed_tags():
    """标签闭合正常，但 JSON 本身损坏，且 json_repair 修复后 name 字段仍为空，
    与真实 parse_tool_calls()._parse_single_call() 的"name 为空则返回 None"逻辑一致。
    """
    text = '<tool_use>\n{"name": }\n</tool_use>'
    issue = detect_format_issue(text)
    assert issue is not None
    assert issue.issue_type == "invalid_json_in_tool_use"


def test_legacy_fence_unclosed():
    text = '我来执行：\n```tool_call\n{"name": "bash", "input": {"command": "ls"}}\n'
    issue = detect_format_issue(text)
    assert issue is not None
    assert issue.issue_type == "legacy_fence_unclosed"


# ── 负例：不应被误判为格式问题 ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    # 完全正常的最终回复，不含任何协议关键字
    "已经帮你完成了任务，文件已创建在 ./hello.py。",
    "",
    "   ",
    # 正常、能被 parse_tool_calls 解析的完整工具调用（标签配对完整、JSON 合法）
    '<tool_use>\n{"name": "bash", "input": {"command": "ls -la"}}\n</tool_use>',
    '<tool_use>\n{"name": "create_file", "input": {"path": "./hello.py", "content": "print(1)"}}\n</tool_use>',
    # 文本里提到"tool_use"这个词但不是协议标签格式（例如在解释概念）
    "工具调用协议使用 tool_use 标签来标记请求。",
    # 合法的、先描述工具调用再描述工具结果的文本（两组标签各自完整闭合）
    (
        '<tool_use>\n{"name": "bash", "input": {"command": "ls"}}\n</tool_use>\n'
        "执行结果如下：\n"
        '<tool_result>\n{"output": "file1.txt"}\n</tool_result>'
    ),
])
def test_negative_cases_no_false_positive(text):
    assert detect_format_issue(text) is None


def test_none_and_non_string_input_safe():
    assert detect_format_issue(None) is None  # type: ignore[arg-type]


# ── 纠错提示文本基本约束 ──────────────────────────────────────────────────────

def test_message_explicitly_marks_system_notice_and_gives_correct_example():
    text = "<tool_use>\n{\"name\": \"bash\","
    issue = detect_format_issue(text)
    assert issue is not None
    # 明确标注这是系统反馈而非用户的新请求，避免模型把它当成普通用户输入来回应
    assert "[System Notice]" in issue.message
    # 提供了一份格式正确的示例，模型能直接照着学
    assert "<tool_use>" in issue.message and "</tool_use>" in issue.message


def test_format_issue_is_frozen_dataclass():
    issue = FormatIssue(issue_type="x", message="y")
    with pytest.raises(Exception):
        issue.issue_type = "z"  # type: ignore[misc]
