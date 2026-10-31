"""agent 的工具调用过程实时镜像进 run.log：结构化事件 → 完整的一行；完整原始输出仍在 agent.log。"""

from __future__ import annotations

import io

import pytest

from fakes import FakeInvoker, approve_waiting
from novel_writer import outlog
from novel_writer.runner import DEFAULT_PY_HANDLERS
from novel_writer.worker import run_worker
from test_m3 import isolated, new_book  # noqa: F401  (fixture)


def _run_log(ws):
    path = ws.path("studio_log")
    return [l.split("] ", 1)[1] for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _mirrored(ws):
    return [l.split("  │ ", 1)[1] for l in _run_log(ws) if "  │ " in l]


def _event(kind, **payload):
    import json
    return outlog.EVENT_PREFIX + json.dumps({"kind": kind, **payload}, ensure_ascii=False)


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


def test_call_event_shows_full_path(isolated):
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    deep = "大纲规划/蓝图/第一幕/第045章/章节细纲与伏笔安排_完整版.md"
    sink(_event("call", name="read_file", icon="📄", input={"path": deep}))
    assert _mirrored(ws) == [f"📄 read_file  {deep}"]


def test_write_file_shows_path_and_full_body_verbatim(isolated):
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    body = "林远读完最后一行字。\n" * 200
    sink(_event("call", name="write_file", icon="✏️", input={"path": "正文/第45章.md", "content": body}))
    lines = _mirrored(ws)
    assert lines[0] == "✏️ write_file  正文/第45章.md"
    assert lines[1] == "   ↳ content："
    # 正文末尾的换行按原样保留为最后一个空行
    assert lines[2:] == ["      │ 林远读完最后一行字。"] * 200 + ["      │ "]


def test_multiline_bash_command_is_shown_verbatim(isolated):
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    cmd = 'powershell -Command "$content = @\'\nline one\nline two\n\'@"'
    sink(_event("call", name="bash", icon="⚡", input={"command": cmd}))
    assert _mirrored(ws) == [
        "⚡ bash",
        "   ↳ command：",
        "      │ powershell -Command \"$content = @'",
        "      │ line one",
        "      │ line two",
        "      │ '@\"",
    ]


def test_unknown_tool_shows_scalar_arguments(isolated):
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    sink(_event("call", name="tree_summary", icon="🔧", input={"depth": 3, "root": "大纲规划"}))
    assert _mirrored(ws) == ["🔧 tree_summary  depth=3，root=大纲规划"]


def test_result_event_shows_full_raw_content(isolated):
    """工具返回内容原样逐行输出，不截断（含框架的行号前缀）。"""
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    text = "1\t# 第一章 林远\n2\t后续\n"
    sink(_event("result", name="read_file", len=len(text), text=text))
    sink(_event("result", name="write_file", len=0, text=""))
    lines = _mirrored(ws)
    assert lines[0] == f"   ↳ 返回 {len(text)} 字符："
    assert lines[1] == "      │ 1\t# 第一章 林远"
    assert lines[2] == "      │ 2\t后续"
    assert lines[3] == "   ↳ 无返回内容"


def test_long_arguments_are_not_truncated(isolated):
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    deep = "大纲规划/" + "子目录/" * 150 + "终.md"
    sink(_event("call", name="read_file", icon="📄", input={"path": deep}))
    assert _mirrored(ws) == [f"📄 read_file  {deep}"]


def test_alerts_pass_through_and_prose_is_dropped(isolated):
    ws = new_book(isolated)
    sink = outlog.run_log_sink(ws)
    for line in ["林远读完最后一行字。", '  "key": 1,', "in execute_all", "⚠  模型超时，重试", "✗  写入失败"]:
        sink(line)
    assert _mirrored(ws) == ["⚠  模型超时，重试", "✗  写入失败"]


def test_mirror_line_cap_is_optional(isolated, monkeypatch):
    """默认不限行数；设置上限后超出只留一行提示。"""
    assert outlog.MIRROR_MAX_LINES is None
    ws = new_book(isolated)
    monkeypatch.setattr(outlog, "MIRROR_MAX_LINES", 3)
    sink = outlog.run_log_sink(ws)
    for i in range(5):
        sink(_event("call", name="grep", icon="🔎", input={"pattern": f"p{i}"}))
    lines = _mirrored(ws)
    assert lines[-2:] == ["🔎 grep  p2", "…（进度行过多，后续见本次调用的 agent.log）"]


def test_full_log_keeps_raw_event_lines(isolated, tmp_path):
    """agent.log 保留原始事件行（完整入参），run.log 只拿到格式化后的一行。"""
    raw = _event("call", name="read_file", icon="📄", input={"path": "a.md"})
    pump = outlog.LinePump(io.BytesIO((raw + "\n").encode("utf-8")), tmp_path / "agent.log",
                           on_line=outlog.run_log_sink(new_book(isolated)))
    assert pump.join(5)
    assert (tmp_path / "agent.log").read_text(encoding="utf-8") == raw + "\n"


def test_child_hooks_emit_structured_events(capsys):
    """子进程里安装的钩子：框架的 print_tool_call / print_tool_result 会先输出结构化事件。"""
    import sys
    from novel_writer import config
    from novel_writer.runner import _ensure_framework_on_path
    _ensure_framework_on_path()
    main_root = config._detect_in_place_main_project_root()
    if main_root is not None and str(main_root / "src") not in sys.path:
        sys.path.insert(0, str(main_root / "src"))
    try:
        from mini_agent.ui import renderer as R
    except ImportError:
        pytest.skip("mini_agent 框架不可用")
    from novel_writer import invoke_child

    invoke_child._install_tool_event_hooks()
    R.print_tool_call("read_file", {"path": "大纲规划/蓝图/_index.md"})
    R.print_tool_result("read_file", "# 标题\n正文")
    out = capsys.readouterr().out
    events = [l for l in out.splitlines() if l.startswith(outlog.EVENT_PREFIX)]
    assert len(events) == 2
    assert outlog.format_event(__import__("json").loads(events[0][len(outlog.EVENT_PREFIX):])) == \
        "📄 read_file  大纲规划/蓝图/_index.md"
    assert "返回 7 字符" in outlog.format_event(__import__("json").loads(events[1][len(outlog.EVENT_PREFIX):]))


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
