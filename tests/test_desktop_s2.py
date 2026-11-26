"""tests/test_desktop_s2.py

覆盖 next_doc/windows_desktop_resident_plan.md S2（热键与快速面板）：

- hotkey：组合键解析（纯函数）、非 Windows 下的降级行为
- config：热键默认关闭、字段类型容错
- quick：reply 提取、QuickSession 请求形态（session_id 路由、中断、最近任务）、
  run_task 状态机（排队→运行→完成/出错/超时）
- 契约：quick 用到的 HTTP 路径与方法必须真的存在于 daemon 的 routes.router
- Qt 离屏冒烟：面板能发送、收到结果并回填，PySide6 不可用则跳过

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_desktop_s2.py -q
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_desktop"))

from mini_agent_desktop.api import DaemonApi  # noqa: E402
from mini_agent_desktop.config import DesktopConfig, load_config, save_config  # noqa: E402
from mini_agent_desktop.hotkey import (  # noqa: E402
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, MOD_WIN, GlobalHotkey, parse_hotkey,
)
from mini_agent_desktop.quick import (  # noqa: E402
    PENDING, QUICK_SESSION_ID, QuickSession, extract_reply, run_task, state_label,
)


# ════════════════════════════════════════════════════════════════════
# hotkey 解析
# ════════════════════════════════════════════════════════════════════

def test_parse_ctrl_alt_space():
    spec = parse_hotkey("Ctrl+Alt+Space")
    assert spec.modifiers == MOD_CONTROL | MOD_ALT
    assert spec.vk == 0x20


def test_parse_is_case_insensitive_and_accepts_win_alias():
    spec = parse_hotkey("win+shift+k")
    assert spec.modifiers == MOD_WIN | MOD_SHIFT
    assert spec.vk == ord("K")


def test_parse_function_and_digit_keys():
    assert parse_hotkey("Ctrl+F12").vk == 0x70 + 11
    assert parse_hotkey("Alt+1").vk == ord("1")


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "Space",                 # 没有修饰键
    "Ctrl",                  # 没有普通键
    "Ctrl+A+B",              # 两个普通键
    "Ctrl+",                 # 空记号
    "Ctrl+Banana",           # 不支持的键
    "Ctrl+F25",              # 超出 F24
])
def test_parse_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_global_hotkey_rejects_bad_spec_at_construction():
    with pytest.raises(ValueError):
        GlobalHotkey("nonsense")


def test_global_hotkey_is_noop_off_windows():
    if os.name == "nt":
        pytest.skip("本用例只针对非 Windows 降级行为")
    hk = GlobalHotkey("Ctrl+Alt+Space")
    ok, reason = hk.start(lambda: None)
    assert ok is False
    assert "Windows" in reason
    hk.stop()   # 不应抛异常


def test_modifier_constants_include_norepeat_flag():
    # RegisterHotKey 的 MOD_NOREPEAT 用于防止按住时连发
    assert MOD_NOREPEAT == 0x4000


# ════════════════════════════════════════════════════════════════════
# config
# ════════════════════════════════════════════════════════════════════

def test_hotkey_disabled_by_default():
    cfg = DesktopConfig()
    assert cfg.hotkey_enabled is False
    assert cfg.hotkey == "Ctrl+Alt+Space"


def test_hotkey_fields_roundtrip(tmp_path):
    p = tmp_path / "d.json"
    save_config(DesktopConfig(hotkey_enabled=True, hotkey="Win+Shift+K"), p)
    cfg = load_config(p)
    assert cfg.hotkey_enabled is True
    assert cfg.hotkey == "Win+Shift+K"


def test_hotkey_wrong_types_fall_back(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"hotkey_enabled": "yes", "hotkey": 42}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.hotkey_enabled is False
    assert cfg.hotkey == "Ctrl+Alt+Space"


# ════════════════════════════════════════════════════════════════════
# reply 提取
# ════════════════════════════════════════════════════════════════════

def test_extract_reply_plain_string():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "你好"}]
    assert extract_reply(msgs) == "你好"


def test_extract_reply_skips_tool_only_and_uses_text_blocks():
    msgs = [
        {"role": "assistant", "content": "先看一下文件"},
        {"role": "assistant", "content": [{"type": "tool_use", "name": "read_file"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "结论：通过"},
            {"type": "text", "text": "共 3 项"},
        ]},
    ]
    assert extract_reply(msgs) == "结论：通过\n共 3 项"


def test_extract_reply_empty_when_no_assistant_text():
    assert extract_reply([{"role": "user", "content": "x"}]) == ""
    assert extract_reply([]) == ""


# ════════════════════════════════════════════════════════════════════
# QuickSession：请求形态（MockTransport，不起真实 daemon）
# ════════════════════════════════════════════════════════════════════

class FakeDaemon:
    """记录请求，并按路径返回预设 JSON。"""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, request.url.path)
        if key not in self.routes:
            return httpx.Response(404, json={"detail": "not found"})
        value = self.routes[key]
        if callable(value):
            value = value(request)
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(200, json=value)


def _session(fake: FakeDaemon) -> QuickSession:
    api = DaemonApi("http://127.0.0.1:8765", "tok", transport=httpx.MockTransport(fake))
    return QuickSession(api)


def test_submit_routes_to_quick_session_and_returns_turn_id():
    fake = FakeDaemon({("POST", "/v1/chat"): {"turn_id": "t1", "queued": True}})
    s = _session(fake)
    assert s.submit("  查一下磁盘  ") == "t1"
    req = fake.calls[-1]
    body = json.loads(req.content)
    assert body == {"message": "查一下磁盘", "session_id": QUICK_SESSION_ID}
    assert req.headers["authorization"] == "Bearer tok"


def test_submit_empty_raises_without_http_call():
    fake = FakeDaemon({})
    with pytest.raises(ValueError):
        _session(fake).submit("   ")
    assert fake.calls == []


def test_interrupt_targets_quick_session_via_query():
    fake = FakeDaemon({("POST", "/v1/interrupt"): {"ok": True}})
    _session(fake).interrupt()
    req = fake.calls[-1]
    assert req.url.params.get("session_id") == QUICK_SESSION_ID


def test_turn_state_404_means_pending():
    fake = FakeDaemon({})   # 任何路径都 404
    assert _session(fake).turn_state("t9") == PENDING


def test_turn_state_reads_state_with_session_query():
    fake = FakeDaemon({("GET", "/v1/turns/t1"): {"turn_id": "t1", "state": "running"}})
    s = _session(fake)
    assert s.turn_state("t1") == "running"
    assert fake.calls[-1].url.params.get("session_id") == QUICK_SESSION_ID


def test_reply_reads_quick_history():
    fake = FakeDaemon({("GET", "/v1/history"): {
        "messages": [{"role": "user", "content": "q"},
                     {"role": "assistant", "content": "answer"}],
        "count": 2, "total": 2, "has_more": False,
    }})
    assert _session(fake).reply() == "answer"
    assert fake.calls[-1].url.params.get("session_id") == QUICK_SESSION_ID


def test_recent_turns_keeps_order_and_limit():
    rows = [{"turn_id": f"t{i}", "input": f"q{i}", "state": "done", "started_at": 0}
            for i in range(15)]
    fake = FakeDaemon({("GET", "/v1/turns"): {"turns": rows}})
    out = _session(fake).recent_turns(limit=3)
    assert [r["turn_id"] for r in out] == ["t0", "t1", "t2"]
    assert set(out[0]) == {"turn_id", "input", "state"}


def test_auth_error_propagates_as_status_error():
    fake = FakeDaemon({("POST", "/v1/chat"): httpx.Response(401, json={"detail": "bad"})})
    with pytest.raises(httpx.HTTPStatusError) as ei:
        _session(fake).submit("hi")
    assert ei.value.response.status_code == 401


# ════════════════════════════════════════════════════════════════════
# run_task 状态机（注入假时钟/假 sleep）
# ════════════════════════════════════════════════════════════════════

class ScriptedSession:
    """按脚本依次返回 turn 状态；reply 固定。"""

    def __init__(self, states, reply="结果"):
        self._states = list(states)
        self._reply = reply
        self.reply_calls = 0
        self.submitted = []

    def submit(self, text):
        self.submitted.append(text)
        return "tX"

    def turn_state(self, turn_id):
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]

    def reply(self):
        self.reply_calls += 1
        return self._reply


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_run_task_waits_through_pending_and_running_then_collects_reply():
    s = ScriptedSession([PENDING, "running", "running", "done"], reply="好的")
    clk = FakeClock()
    seen = []
    out = run_task(s, "hi", on_status=seen.append, clock=clk, sleep=clk.sleep)
    assert out.state == "done"
    assert out.reply == "好的"
    assert seen[0] == "submitted"
    assert PENDING in seen and "running" in seen


def test_run_task_error_does_not_fetch_reply():
    s = ScriptedSession(["error"], reply="不该被读取")
    out = run_task(s, "hi", clock=FakeClock(), sleep=FakeClock().sleep)
    assert out.state == "error"
    assert out.reply == ""
    assert s.reply_calls == 0


def test_run_task_interrupted_state_is_terminal():
    s = ScriptedSession(["running", "interrupted"])
    clk = FakeClock()
    out = run_task(s, "hi", clock=clk, sleep=clk.sleep)
    assert out.state == "interrupted"


def test_run_task_times_out_without_interrupting():
    s = ScriptedSession(["running"])        # 永远不结束
    clk = FakeClock()
    out = run_task(s, "hi", timeout=10, poll_interval=2, clock=clk, sleep=clk.sleep)
    assert out.state == "timeout"
    assert out.reply == ""


def test_state_label_covers_known_states_and_passes_unknown_through():
    assert state_label("done") == "完成"
    assert state_label("weird") == "weird"


# ════════════════════════════════════════════════════════════════════
# 契约：客户端用到的路径/方法必须真实存在于 daemon 路由
# ════════════════════════════════════════════════════════════════════

def test_quick_endpoints_exist_in_real_router():
    pytest.importorskip("fastapi")
    from mini_agent.api import routes

    # router 自带 prefix="/v1"（与 server.py 挂载方式一致）；直接遍历 APIRouter.routes，
    # 避免依赖 FastAPI 各版本 app.routes 的包装类型差异。
    registered = set()
    for r in routes.router.routes:
        for m in getattr(r, "methods", set()):
            registered.add((m, r.path))

    needed = [
        ("POST", "/v1/chat"),
        ("POST", "/v1/interrupt"),
        ("GET", "/v1/turns"),
        ("GET", "/v1/turns/{turn_id}"),
        ("GET", "/v1/history"),
    ]
    for key in needed:
        assert key in registered, f"客户端依赖的接口缺失：{key}"


# ════════════════════════════════════════════════════════════════════
# Qt 离屏冒烟
# ════════════════════════════════════════════════════════════════════

def test_quick_panel_sends_and_shows_reply_offscreen():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.quick_panel import QuickPanel

    app = QApplication.instance() or QApplication([])

    fake = FakeDaemon({
        ("GET", "/v1/turns"): {"turns": [
            {"turn_id": "old", "input": "以前的问题", "state": "done"}]},
        ("POST", "/v1/chat"): {"turn_id": "t1", "queued": True},
        ("GET", "/v1/turns/t1"): {"turn_id": "t1", "state": "done"},
        ("GET", "/v1/history"): {"messages": [
            {"role": "assistant", "content": "**磁盘**还剩 40%"}]},
    })

    def factory():
        return QuickSession(DaemonApi("http://x", "tok", transport=httpx.MockTransport(fake)))

    panel = QuickPanel(factory)
    panel.open_panel()
    panel.input.setPlainText("磁盘还剩多少")
    panel._submit()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        app.processEvents()
        if panel.reply.toPlainText().strip():
            break
        time.sleep(0.02)

    assert "磁盘" in panel.reply.toPlainText()
    assert panel.send_btn.isEnabled() is True
    # 最近任务列表应已加载，且双击语义依赖的 UserRole 数据正确
    deadline = time.monotonic() + 5
    while panel.recent.count() == 0 and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert panel.recent.count() >= 1
    assert panel.recent.item(0).data(0x0100) == "以前的问题"   # Qt.UserRole
    panel.hide()


def test_quick_panel_drag_handle_moves_window_offscreen():
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.quick_panel import QuickPanel

    app = QApplication.instance() or QApplication([])
    panel = QuickPanel(lambda: None)
    panel.move(100, 100)
    panel.show()
    app.processEvents()
    start = panel.frameGeometry().topLeft()

    bar = panel.drag_bar
    press_global = bar.mapToGlobal(bar.rect().center())
    press = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(bar.rect().center()),
                        QPointF(press_global), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    move_global = QPointF(press_global.x() + 60, press_global.y() + 40)
    move = QMouseEvent(QEvent.Type.MouseMove, QPointF(bar.rect().center()), move_global,
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(bar, press)
    QApplication.sendEvent(bar, move)
    app.processEvents()

    end = panel.frameGeometry().topLeft()
    assert end.x() - start.x() == 60
    assert end.y() - start.y() == 40
    panel.hide()
