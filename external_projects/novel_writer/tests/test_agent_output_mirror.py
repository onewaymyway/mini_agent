"""子进程的原始输出原样镜像进 run.log（不解析、不过滤、不截断）；完整输出同时保存在 agent.log。"""

from __future__ import annotations

import io

import pytest

from fakes import FakeInvoker, approve_waiting
from novel_writer import outlog
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _run_log(ws):
    """run.log 中每行去掉时间戳后的内容（保留空行）。"""
    path = ws.path("studio_log")
    if not path.exists():
        return []
    return [line.split("] ", 1)[1] if line.startswith("[") and "] " in line else line
            for line in path.read_text(encoding="utf-8").split("\n")[:-1]]


def test_linepump_calls_on_line_for_every_line_in_order(tmp_path):
    seen = []
    pump = outlog.LinePump(io.BytesIO("第一行\nsecond\n第三行\n".encode("utf-8")), tmp_path / "agent.log",
                           on_line=seen.append)
    assert pump.join(5)
    assert seen == ["第一行", "second", "第三行"]
    assert (tmp_path / "agent.log").read_text(encoding="utf-8") == "第一行\nsecond\n第三行\n"


def test_failing_sink_does_not_break_full_log(tmp_path):
    def boom(_text):
        raise RuntimeError("sink down")

    pump = outlog.LinePump(io.BytesIO(b"a\nb\n"), tmp_path / "agent.log", on_line=boom)
    assert pump.join(5)
    assert pump.line_count == 2
    assert (tmp_path / "agent.log").read_bytes() == b"a\nb\n"


def test_every_raw_line_is_mirrored_unchanged(isolated):
    """不解析：任何一行（正文、JSON、堆栈、空行）都原样写入 run.log。"""
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    raw = [
        "📄 read_file  大纲规划/蓝图/第一幕/第045章/章节细纲与伏笔安排_完整版.md",
        '{',
        '  "path": "大纲规划/蓝图/第一幕/第045章/章节细纲与伏笔安排_完整版.md"',
        '}',
        "1\t# 第45章 《双重身份》",
        "",
        "林远读完最后一行字。",
        "in execute_all",
    ]
    for line in raw:
        sink(line)
    assert _run_log(ws) == raw


def test_long_lines_are_not_truncated(isolated):
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    long_line = "正文" * 3000
    sink(long_line)
    assert _run_log(ws) == [long_line]


def test_pump_mirrors_raw_stream_to_run_log(isolated, tmp_path):
    ws = new_book(isolated)
    text = "✏️ write_file  正文.md\n   ↳ content：\n      │ 第一段\n      │ \n"
    pump = outlog.LinePump(io.BytesIO(text.encode("utf-8")), tmp_path / "agent.log",
                           on_line=outlog.run_log_sink(ws))
    assert pump.join(5)
    assert _run_log(ws) == ["✏️ write_file  正文.md", "   ↳ content：", "      │ 第一段", "      │ "]
    assert (tmp_path / "agent.log").read_text(encoding="utf-8") == text


def test_child_result_hook_disables_truncation(monkeypatch):
    """子进程里的 print_tool_result 必须以 truncate=None 调用，返回内容才完整打印。"""
    pytest.importorskip("rich")
    import sys
    from pathlib import Path

    from novel_writer import config
    from novel_writer.runner import _ensure_framework_on_path

    _ensure_framework_on_path()
    main_root = config._detect_in_place_main_project_root()
    if main_root is not None and str(Path(main_root) / "src") not in sys.path:
        sys.path.insert(0, str(Path(main_root) / "src"))
    try:
        from mini_agent.ui import renderer as R
    except ImportError:
        pytest.skip("mini_agent 框架不可用")
    from novel_writer import invoke_child

    seen = {}

    def fake_orig(tool_name, result, truncate=2000):
        seen["truncate"] = truncate

    monkeypatch.setattr(R, "print_tool_result", fake_orig)
    invoke_child._install_full_result_hook()
    R.print_tool_result("read_file", "x" * 5000)
    assert seen["truncate"] is None


def test_sink_is_installed_while_skill_step_invokes_agent(isolated):
    ws = new_book(isolated, total=2)
    inv = FakeInvoker(ws.root)
    seen = {}

    def hook(step_id):
        if step_id == "plan_blueprint":
            seen["sink"] = outlog.current_line_sink()

    inv.hook = hook
    for _ in range(50):
        res = run_worker(ws, mode="until_gate", invoker=inv, handlers=DEFAULT_PY_HANDLERS, heartbeat_interval=0.01)
        assert res["ok"], res
        if res["reason"] == "completed":
            break
        approve_waiting(ws)
    assert seen.get("sink") is not None
    assert outlog.current_line_sink() is None
