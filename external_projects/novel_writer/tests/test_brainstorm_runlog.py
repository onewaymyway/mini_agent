"""对话过程日志：捕获 agent 的全部输出（工具调用/结果/提示/错误），后台运行、实时可读、结束后保留。"""

import time
from pathlib import Path

import pytest

from novel_writer.brainstorm import api as BA
from novel_writer.brainstorm import chat as CH
from novel_writer.brainstorm.runlog import Capture, describe
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

PROJECT = Path(__file__).resolve().parent.parent

from novel_writer.runner import _ensure_framework_on_path  # noqa: E402

_ensure_framework_on_path()
renderer = pytest.importorskip("mini_agent.ui.renderer")


@pytest.fixture
def ws(tmp_path):
    w = BrainstormWorkspace(tmp_path / "湖")
    w.create("湖")
    return w


def _wait(ws, turn, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        st_ = CH.run_state(ws, turn)
        if st_["status"] != "running":
            return st_
        time.sleep(0.02)
    raise AssertionError("对话轮次未在时限内结束")


def _agent_like(prompt, session_id):
    """桩 agent：和框架一样通过 R.print_* 输出过程信息，然后返回一段带提议的回复。"""
    R = renderer.R
    R.print_info("正在读取题材卡")
    R.print_tool_call("read_file", {"path": "working/brief.json"})
    R.print_tool_result("read_file", "x" * 5000)
    R.print_warning("注意：没有世界观条目")
    return ("我建议先补世界规则。\n```proposals\n[]\n```", None)


# ── 捕获机制 ──

def test_capture_records_events_and_restores_renderer(ws):
    events = []
    patched_before = {n: renderer.R.__dict__.get(n) for n in ("print_info", "print_tool_call")}
    with Capture(events.append):
        renderer.R.print_info("hello")
        renderer.R.print_tool_call("grep", {"q": "雾"})
    assert [e["kind"] for e in events] == ["info", "tool_call"]
    assert events[1]["text"].startswith("grep  ") and "雾" in events[1]["text"]
    # 退出后恢复原状：实例上不再残留包装函数
    assert {n: renderer.R.__dict__.get(n) for n in patched_before} == patched_before
    assert all(n not in renderer.R.__dict__ for n in patched_before)


def test_capture_does_not_truncate_tool_results():
    events = []
    with Capture(events.append):
        renderer.R.print_tool_result("read_file", "字" * 5000)
    assert len(events[0]["text"]) > 5000


def test_describe_falls_back_for_unknown_shapes():
    assert describe("print_info", ("a", None, "b")) == {"kind": "info", "text": "a b"}
    assert describe("print_tool_error", ("t", "boom"))["kind"] == "tool_error"


# ── 后台轮次 ──

def test_turn_logs_every_event_in_order(ws):
    turn = CH.start_turn(ws, "我想写一个湖的故事", "追问", runner=lambda p, s: _agent_like(p, s))
    st_ = _wait(ws, turn)
    assert st_["status"] == "done"
    kinds = [e["kind"] for e in st_["events"]]
    assert kinds == ["info", "tool_call", "tool_result", "warning"]
    assert "working/brief.json" in st_["events"][1]["text"]
    assert len(st_["events"][2]["text"]) > 5000                          # 完整结果，不截断
    hist = CH.history(ws)
    assert hist[0]["role"] == "user" and hist[0]["turn"] == turn
    assert hist[1]["role"] == "assistant" and hist[1]["turn"] == turn
    assert hist[1]["text"] == "我建议先补世界规则。"                       # 提议块已去掉


def test_user_message_is_recorded_before_agent_finishes(ws):
    gate = {"go": False}

    def slow(prompt, sid):
        while not gate["go"]:
            time.sleep(0.01)
        return ("好。", None)
    turn = CH.start_turn(ws, "先写下这句", "追问", runner=slow)
    assert CH.run_state(ws, turn)["status"] == "running"
    assert CH.history(ws)[0]["text"] == "先写下这句"                     # 作者的话已经记下
    gate["go"] = True
    assert _wait(ws, turn)["status"] == "done"


def test_turn_error_is_logged_and_recorded(ws):
    def broken(prompt, sid):
        renderer.R.print_info("开始")
        raise RuntimeError("模型超时")
    turn = CH.start_turn(ws, "x", "追问", runner=broken)
    st_ = _wait(ws, turn)
    assert st_["status"] == "error" and "模型超时" in st_["error"]
    assert st_["events"][-1]["kind"] == "error"
    assert CH.history(ws)[-1]["error"] is True


def test_start_turn_validates_before_starting(ws):
    with pytest.raises(BrainstormError):
        CH.start_turn(ws, "  ", "追问", runner=_agent_like)
    with pytest.raises(BrainstormError):
        CH.start_turn(ws, "x", "发散", runner=_agent_like)
    assert CH.history(ws) == []


def test_unknown_turn_is_an_error(ws):
    with pytest.raises(BrainstormError):
        CH.run_state(ws, "t_missing")


def test_send_still_works_synchronously(ws):
    out = CH.send(ws, "同步发送", "追问", runner=lambda p, s: ("回复", None))
    assert out["reply"] == "回复"
    assert [h["role"] for h in CH.history(ws)] == ["user", "assistant"]


# ── 接口与页面 ──

def test_api_start_and_run(tmp_path):
    root = BA.create_project("湖", base=str(tmp_path))["data"]["root"]
    turn = BA.chat_start(root, "你好", "追问", runner=_agent_like)["data"]["turn"]
    ws = BrainstormWorkspace(root)
    _wait(ws, turn)
    res = BA.chat_run(root, turn)
    assert res["ok"] and res["data"]["status"] == "done" and res["data"]["events"]


def test_page_shows_full_log_of_finished_turn(tmp_path, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    root = BA.create_project("湖边", base=str(tmp_path))["data"]["root"]
    ws = BrainstormWorkspace(root)
    turn = CH.start_turn(ws, "测试页面", "追问", runner=_agent_like)
    _wait(ws, turn)
    monkeypatch.setenv("NOVEL_PROJECT", str(PROJECT))
    monkeypatch.setattr("novel_writer.brainstorm.api.default_root", lambda: tmp_path / "none")
    at = AppTest.from_file(str(PROJECT / "tests" / "apps" / "brainstorm_page_app.py"), default_timeout=30)
    at.session_state["bs_root"] = root
    at.run()
    assert not at.exception, at.exception
    assert any("本轮全部过程（4 条）" in e.label for e in at.expander)
