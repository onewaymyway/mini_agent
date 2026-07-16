"""
tests/test_recall_history.py

覆盖 compact_mechanism_improvement_plan.md P2-B：
  Raw history 按需找回工具（tools/recall_history.py::recall_from_raw_history）。

验证点：
  - 未配置/关闭时返回明确的错误提示，不抛异常
  - 关键词命中能正确检索回对应片段，并给出近似 turn 编号
  - compact_event / compressed 等占位符类型条目不参与匹配
  - query 为空时返回错误提示而不是抛异常
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from mini_agent.tools.recall_history import (
    configure_recall_history,
    is_recall_history_enabled,
    recall_from_raw_history,
    reset_recall_history_config,
)


def _sample_entries():
    return [
        {"role": "user", "content": "帮我修复登录页面的 bug", "_type": "user_input"},
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "好的，我先看看登录相关代码"}],
            "_type": "assistant_reply",
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "1", "name": "read_file", "input": {"path": "login.py"}}],
            "_type": "assistant_reply",
        },
        {
            "role": "user",
            "content": "<tool_result>found bug in validate_password function</tool_result>",
            "_type": "tool_result",
        },
        {"role": "assistant", "content": "[compressed placeholder]", "_type": "compressed"},
        {"role": "user", "content": "继续下一个任务，帮我优化数据库查询", "_type": "user_input"},
    ]


def setup_function(_fn):
    reset_recall_history_config()


def teardown_function(_fn):
    reset_recall_history_config()


def test_disabled_by_default_returns_error_not_exception():
    assert is_recall_history_enabled() is False
    result = recall_from_raw_history("登录")
    assert "not enabled" in result


def test_enabled_getter_false_returns_error():
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: False)
    result = recall_from_raw_history("登录")
    assert "not enabled" in result


def test_finds_matching_fragments_with_turn_info():
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    result = recall_from_raw_history("登录 密码 bug")
    assert "Found" in result
    assert "登录" in result or "validate_password" in result
    assert "turn ~1" in result


def test_skips_compressed_placeholder_entries():
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    result = recall_from_raw_history("compressed placeholder")
    assert "No matching fragments found" in result


def test_empty_query_returns_error():
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    result = recall_from_raw_history("   ")
    assert "error" in result


def test_no_match_returns_friendly_message():
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    result = recall_from_raw_history("量子物理学论文")
    assert "No matching fragments found" in result


def test_provider_exception_does_not_crash():
    def _boom():
        raise RuntimeError("boom")

    configure_recall_history(_boom, enabled_getter=lambda: True)
    result = recall_from_raw_history("登录")
    assert "not enabled" in result  # provider 异常时降级为未配置提示，不抛出
