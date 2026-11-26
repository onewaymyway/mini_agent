"""E3 流式输出：token 累积、按 turn 订阅、run_task 回调、快速面板增量显示。"""

import threading
import time

import httpx
import pytest

from mini_agent_desktop.api import DaemonApi, SseEvent
from mini_agent_desktop.quick import STREAM_END_EVENTS, QuickSession, TokenAccumulator, run_task


def _sse(*frames: str) -> bytes:
    return "".join(frames).encode("utf-8")


def _token(text: str, eid: int = 1) -> str:
    return f'id: {eid}\nevent: token\ndata: {{"turn_id": "t1", "text": {text!r}}}\n\n'.replace("'", '"')


def _end(eid: int = 9) -> str:
    return f'id: {eid}\nevent: turn_done\ndata: {{"turn_id": "t1"}}\n\n'


class _Fake:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        value = self.routes.get((request.method, request.url.path))
        if value is None:
            return httpx.Response(404, json={"detail": "nf"})
        if callable(value):
            value = value(request)
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)


# ── 纯逻辑 ────────────────────────────────────────────────────────────────

def test_accumulator_collects_only_token_text():
    acc = TokenAccumulator()
    assert acc.feed(SseEvent(event="token", data={"text": "你"})) == "你"
    assert acc.feed(SseEvent(event="token", data={"text": "好"})) == "好"
    assert acc.feed(SseEvent(event="status", data={"text": "x"})) == ""
    assert acc.feed(SseEvent(event="token", data={"text": ""})) == ""
    assert acc.feed(SseEvent(event="token", data={"text": 3})) == ""
    assert acc.feed(SseEvent(event="token", data="not-a-dict")) == ""
    assert acc.text == "你好"


def test_end_events_include_turn_done():
    assert "turn_done" in STREAM_END_EVENTS and "token" not in STREAM_END_EVENTS


# ── HTTP 客户端 ───────────────────────────────────────────────────────────

def test_stream_turn_hits_turn_path_and_yields_frames():
    body = _sse(_token("hi", 1), _end(2))
    fake = _Fake({("GET", "/v1/stream/t1"): httpx.Response(
        200, content=body, headers={"content-type": "text/event-stream"})})
    api = DaemonApi("http://127.0.0.1:8765", "tok", transport=httpx.MockTransport(fake))
    evs = list(api.stream_turn("t1", threading.Event()))
    assert [e.event for e in evs] == ["token", "turn_done"]
    assert evs[0].data["text"] == "hi"
    req = fake.calls[0]
    assert req.url.params["replay"] == "true"
    assert req.headers["authorization"] == "Bearer tok"


def test_stream_turn_stops_when_stop_is_set():
    body = _sse(_token("a", 1), _token("b", 2))
    fake = _Fake({("GET", "/v1/stream/t1"): httpx.Response(200, content=body)})
    api = DaemonApi("http://127.0.0.1:8765", "tok", transport=httpx.MockTransport(fake))
    stop = threading.Event()
    stop.set()
    assert list(api.stream_turn("t1", stop)) == []


def test_run_task_reports_turn_id_via_callback():
    fake = _Fake({
        ("POST", "/v1/chat"): {"turn_id": "t9", "queued": True},
        ("GET", "/v1/turns/t9"): {"turn_id": "t9", "state": "done"},
        ("GET", "/v1/history"): {"messages": [{"role": "assistant", "content": "ok"}]},
    })
    session = QuickSession(DaemonApi("http://x", "tok", transport=httpx.MockTransport(fake)))
    seen = []
    clock = iter(range(100))
    out = run_task(session, "问", on_turn=seen.append, clock=lambda: next(clock),
                   sleep=lambda _s: None)
    assert seen == ["t9"] and out.state == "done" and out.reply == "ok"


# ── 快速面板（离屏 Qt）─────────────────────────────────────────────────────

def test_panel_shows_streamed_tokens_then_final_markdown():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.quick_panel import QuickPanel

    app = QApplication.instance() or QApplication([])
    body = _sse(_token("磁盘", 1), _token("还剩", 2), _end(3))
    fake = _Fake({
        ("GET", "/v1/turns"): {"turns": []},
        ("POST", "/v1/chat"): {"turn_id": "t1", "queued": True},
        ("GET", "/v1/turns/t1"): {"turn_id": "t1", "state": "done"},
        ("GET", "/v1/stream/t1"): httpx.Response(200, content=body),
        ("GET", "/v1/history"): {"messages": [{"role": "assistant", "content": "**磁盘**还剩 40%"}]},
    })

    def factory():
        return QuickSession(DaemonApi("http://x", "tok", transport=httpx.MockTransport(fake)))

    panel = QuickPanel(factory)
    panel.input.setPlainText("磁盘还剩多少")
    panel._submit()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        app.processEvents()
        if panel.send_btn.isEnabled() and "40%" in panel.reply.toPlainText():
            break
        time.sleep(0.02)
    app.processEvents()
    assert "40%" in panel.reply.toPlainText()
    assert panel._streaming is False
    panel.hide()


def test_late_tokens_after_done_are_ignored():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.quick_panel import QuickPanel

    QApplication.instance() or QApplication([])
    panel = QuickPanel(lambda: None)
    panel._streaming = False
    panel._on_token("迟到的片段")
    assert panel.reply.toPlainText() == ""
