"""
tests/test_recall_cli_command.py

覆盖 /recall slash 命令（compact_mechanism_improvement_plan.md P2-B 的手动
CLI 入口，`cli/commands/recall.py::handle_recall_cmd`）。

用 monkeypatch 拦截 `mini_agent.ui.renderer` 的输出函数，验证：
  - 未启用时提示配置关闭，不报错
  - 无参数时提示 usage
  - 正常查询能返回命中片段
  - `--max N` 能正确解析并传递 max_results
  - 无效 --max 值有明确报错，不抛异常
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import mini_agent.ui.renderer as R
from mini_agent.cli.commands.recall import handle_recall_cmd
from mini_agent.tools.recall_history import (
    configure_recall_history,
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
    ]


class _Capture:
    def __init__(self):
        self.info = []
        self.error = []
        self.printed = []


def _patch_renderer(monkeypatch, cap: _Capture):
    monkeypatch.setattr(R, "print_info", lambda msg: cap.info.append(msg))
    monkeypatch.setattr(R, "print_error", lambda msg: cap.error.append(msg))
    monkeypatch.setattr(R.console, "print", lambda *a, **kw: cap.printed.append(a[0] if a else ""))


def setup_function(_fn):
    reset_recall_history_config()


def teardown_function(_fn):
    reset_recall_history_config()


def test_recall_disabled_prints_info_not_error(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    handle_recall_cmd(["登录"])
    assert any("disabled" in m for m in cap.info)
    assert not cap.error
    assert not cap.printed


def test_recall_no_args_prints_usage(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    handle_recall_cmd([])
    assert any("Usage" in m for m in cap.error)


def test_recall_finds_match(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    handle_recall_cmd(["登录", "bug"])
    assert cap.printed
    assert "Found" in cap.printed[0]


def test_recall_max_flag_parsed(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    handle_recall_cmd(["--max", "1", "登录"])
    assert cap.printed
    # 只应该出现一条 "--- turn" 片段块（max_results=1）
    assert cap.printed[0].count("--- turn") <= 1


def test_recall_invalid_max_value_reports_error(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    handle_recall_cmd(["--max", "not_a_number", "登录"])
    assert any("Invalid --max" in m for m in cap.error)
    assert not cap.printed


def test_recall_max_without_query_falls_back_to_usage(monkeypatch):
    cap = _Capture()
    _patch_renderer(monkeypatch, cap)
    configure_recall_history(lambda: _sample_entries(), enabled_getter=lambda: True)
    handle_recall_cmd(["--max", "3"])
    assert any("Usage" in m for m in cap.error)
