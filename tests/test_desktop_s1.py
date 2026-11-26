"""tests/test_desktop_s1.py

覆盖 next_doc/windows_desktop_resident_plan.md S1（托盘）的非 UI 逻辑，以及
与真实 daemon 路由的接口契约：

- config / discovery：配置容错、token/端口/崩溃告警发现、拉起命令
- api：SSE 解析、鉴权头、HTTP 错误
- state：TrayModel 图标优先级、菜单文案、事件→刷新集合
- controller：快照后订阅、事件刷新、断线重连退避、离线崩溃发现、认证失败、自动拉起
- 集成：真实 routes + 真实 AgentBridge 起 uvicorn，验证客户端端到端
  （含 `attention` 事件经 attention_hub → SSE → 客户端 → 托盘状态）
- Qt 冒烟（离屏）：PySide6 可用时验证图标/菜单刷新，不可用则跳过

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_desktop_s1.py -q
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "mini_agent_desktop"))

from mini_agent_desktop import discovery  # noqa: E402
from mini_agent_desktop.api import DaemonApi, SseEvent, parse_sse  # noqa: E402
from mini_agent_desktop.app import parse_args, resolve_project_root, run_check  # noqa: E402
from mini_agent_desktop.config import DesktopConfig, load_config, save_config  # noqa: E402
from mini_agent_desktop.controller import DesktopController  # noqa: E402
from mini_agent_desktop.state import (  # noqa: E402
    REASON_AUTH, REASON_LOST, REASON_NOT_RUNNING, TrayIcon, TrayModel,
)


# ════════════════════════════════════════════════════════════════════
# config
# ════════════════════════════════════════════════════════════════════

def test_config_defaults_are_conservative():
    cfg = DesktopConfig()
    assert cfg.auto_start_daemon is False


def test_config_missing_or_corrupt_falls_back(tmp_path):
    assert load_config(tmp_path / "nope.json") == DesktopConfig()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert load_config(bad) == DesktopConfig()
    bad.write_text("[1,2]", encoding="utf-8")
    assert load_config(bad) == DesktopConfig()


def test_config_roundtrip_and_type_validation(tmp_path):
    p = tmp_path / "sub" / "desktop.json"
    save_config(DesktopConfig(project_root="D:/x", http_port=9000, auto_start_daemon=True), p)
    cfg = load_config(p)
    assert (cfg.project_root, cfg.http_port, cfg.auto_start_daemon) == ("D:/x", 9000, True)
    # 类型错误的字段被忽略；bool 不能冒充端口号
    p.write_text(json.dumps({"http_port": True, "auto_start_daemon": "yes",
                             "poll_interval_seconds": 5}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.http_port == 8765
    assert cfg.auto_start_daemon is False
    assert cfg.poll_interval_seconds == 5.0


def test_config_path_env_override(tmp_path, monkeypatch):
    from mini_agent_desktop.config import config_path
    monkeypatch.setenv("MINI_AGENT_DESKTOP_CONFIG", str(tmp_path / "c.json"))
    assert config_path() == tmp_path / "c.json"


# ════════════════════════════════════════════════════════════════════
# discovery
# ════════════════════════════════════════════════════════════════════

def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_read_token_prefers_single_user_then_owner(tmp_path):
    assert discovery.read_token(tmp_path) is None
    _write(tmp_path / ".agent/users/tokens/owner.key", "owner-tok\n")
    assert discovery.read_token(tmp_path) == "owner-tok"
    _write(tmp_path / ".agent/agent_api.key", "single-tok\n")
    assert discovery.read_token(tmp_path) == "single-tok"
    _write(tmp_path / ".agent/agent_api.key", "  \n")      # 空文件 → 退到 owner
    assert discovery.read_token(tmp_path) == "owner-tok"


def test_resolve_endpoint_uses_info_port_else_default(tmp_path):
    assert discovery.resolve_endpoint(tmp_path, 8765).port == 8765
    _write(tmp_path / ".agent/daemon_info.json", json.dumps({"http_port": 19100, "pid": 1}))
    ep = discovery.resolve_endpoint(tmp_path, 8765)
    assert ep.base_url == "http://127.0.0.1:19100"
    _write(tmp_path / ".agent/daemon_info.json", json.dumps({"http_port": "x"}))
    assert discovery.resolve_endpoint(tmp_path, 8765).port == 8765
    _write(tmp_path / ".agent/daemon_info.json", json.dumps({"http_port": 99999}))
    assert discovery.resolve_endpoint(tmp_path, 8765).port == 8765


def test_count_unacked_crash_alerts(tmp_path):
    assert discovery.count_unacked_crash_alerts(tmp_path) == 0
    f = tmp_path / ".agent/notification/daemon_crash_alerts.jsonl"
    _write(f, "\n".join([
        json.dumps({"alert_id": "a", "acknowledged": False}),
        json.dumps({"alert_id": "b", "acknowledged": True}),
        "garbage",
        json.dumps({"alert_id": "c"}),
        "",
    ]))
    assert discovery.count_unacked_crash_alerts(tmp_path) == 2


def test_spawn_daemon_builds_detached_command(tmp_path, monkeypatch):
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"], captured["kw"] = cmd, kw
        return SimpleNamespace()

    monkeypatch.setattr(discovery.subprocess, "Popen", fake_popen)
    discovery.spawn_daemon(tmp_path, 8765, python="py")
    assert captured["cmd"] == ["py", "-m", "mini_agent", "daemon", "start",
                               "--detach", "--http-port", "8765"]
    assert captured["kw"]["cwd"] == str(tmp_path)


# ════════════════════════════════════════════════════════════════════
# api / SSE
# ════════════════════════════════════════════════════════════════════

def test_parse_sse_events_comments_and_bad_json():
    text = (
        ": heartbeat 1\n\n"
        "id: 7\nevent: attention\ndata: {\"kind\": \"report\", \"ref_id\": \"r1\"}\n\n"
        "event: weird\ndata: not-json\n\n"
        "data: {\"a\": 1}\ndata: \n\n"
    )
    evs = list(parse_sse(text.splitlines(keepends=True)))
    assert [e.event for e in evs] == ["heartbeat", "attention", "weird", "message"]
    assert evs[1].id == "7" and evs[1].data["ref_id"] == "r1"
    assert evs[2].data == {"raw": "not-json"}


def test_api_sends_bearer_and_raises_on_401():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"detail": "bad"})

    api = DaemonApi("http://x", "tok", transport=httpx.MockTransport(handler))
    assert api.health() is True
    with pytest.raises(httpx.HTTPStatusError):
        api.status()
    assert seen["auth"] == "Bearer tok"


def test_api_health_false_on_connect_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    assert DaemonApi("http://x", None, transport=httpx.MockTransport(handler)).health() is False


# ════════════════════════════════════════════════════════════════════
# state
# ════════════════════════════════════════════════════════════════════

def _connected_model(**counts) -> TrayModel:
    m = TrayModel()
    m.set_connected()
    m.apply_status({"state": "idle", "queue_depth": 0})
    m.apply_summary({"counts": counts, "attention_events_enabled": True})
    return m


def test_icon_priority():
    m = TrayModel()
    assert m.icon() is TrayIcon.OFFLINE                      # 初始未连接
    m.set_offline(REASON_NOT_RUNNING, crash_unacked=1)
    assert m.icon() is TrayIcon.ERROR                        # 离线 + 有崩溃告警
    m = _connected_model()
    assert m.icon() is TrayIcon.IDLE
    m.apply_status({"state": "running", "queue_depth": 2})
    assert m.icon() is TrayIcon.WORKING
    m.apply_summary({"counts": {"pending_reports": 1}})
    assert m.icon() is TrayIcon.ATTENTION                    # 需要处理优先于工作中
    m.apply_summary({"counts": {"pending_reports": 1, "crash_alerts_unacked": 1}})
    assert m.icon() is TrayIcon.ERROR                        # 崩溃告警优先级最高


def test_menu_state_texts_and_badge():
    m = _connected_model(pending_permissions=1, pending_reports=2)
    m.apply_status({"state": "running", "queue_depth": 2})
    s = m.menu_state()
    assert s.badge == 3
    assert s.tasks_text == "当前任务：3 个"
    assert s.attention_text == "待处理事项：3 个"
    assert "待审批 1" in s.tooltip and "未读汇报 2" in s.tooltip
    assert s.can_start_daemon is False


def test_menu_state_offline_offers_start_only_when_not_running():
    m = TrayModel()
    m.set_offline(REASON_NOT_RUNNING, 2)
    s = m.menu_state()
    assert s.can_start_daemon is True
    assert s.badge == 0 and "2 条未确认崩溃告警" in s.status_text
    m.set_offline(REASON_AUTH)
    assert m.menu_state().can_start_daemon is False          # 认证问题拉起 daemon 没用


def test_offline_clears_stale_counts():
    m = _connected_model(pending_reports=5)
    m.set_offline(REASON_LOST)
    assert m.menu_state().badge == 0


def test_apply_event_returns_refresh_sets_and_optimistic_working():
    m = _connected_model()
    assert m.apply_event("turn_start", {}) == {"status"}
    assert m.icon() is TrayIcon.WORKING
    assert m.apply_event("turn_done", {}) == {"status"}
    assert m.icon() is TrayIcon.IDLE
    assert m.apply_event("permission_req", {}) == {"summary"}
    assert m.apply_event("attention", {}) == {"summary"}
    assert m.apply_event("token", {}) == set()               # 高频事件不触发刷新
    assert m.apply_event("objective_progress", {}) == set()


# ════════════════════════════════════════════════════════════════════
# controller（假 API，不走网络）
# ════════════════════════════════════════════════════════════════════

class FakeApi:
    def __init__(self, *, healthy=True, status_exc=None, events=(), stream_exc=None,
                 summary=None, status=None):
        self.healthy = healthy
        self.status_exc = status_exc
        self.events = list(events)
        self.stream_exc = stream_exc
        self._summary = summary or {"counts": {}, "attention_events_enabled": True}
        self._status = status or {"state": "idle", "queue_depth": 0}
        self.calls = {"status": 0, "summary": 0}
        self.closed = False

    def health(self):
        return self.healthy

    def status(self):
        self.calls["status"] += 1
        if self.status_exc:
            raise self.status_exc
        return self._status

    def summary(self):
        self.calls["summary"] += 1
        return self._summary

    def stream_events(self, stop):
        for e in self.events:
            yield e
        if self.stream_exc:
            raise self.stream_exc

    def close(self):
        self.closed = True


def _http_status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "http://x/v1/status")
    return httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))


def _run_controller(apis, *, steps_before_stop=None, **kw):
    """依次用 apis 里的假 API 跑控制器；apis 用完即停止。返回 (controller, model, sleeps, changes)。"""
    model = TrayModel()
    sleeps: list[float] = []
    changes: list[str] = []
    it = iter(apis)
    box = {}

    def factory():
        try:
            return next(it)
        except StopIteration:
            box["c"].stop()      # 假 API 用完：让控制器收尾退出（工厂返回 None 即跳过本轮）
            return None

    c = DesktopController(
        model, factory, on_change=lambda: changes.append(model.menu_state().icon.value),
        backoff=(1.0, 2.0, 4.0), **kw)
    box["c"] = c
    c._sleep = lambda s: sleeps.append(s)            # 不真的等待
    c.run()
    return c, model, sleeps, changes


def test_controller_snapshot_then_events_refresh():
    attention = []
    api = FakeApi(
        events=[SseEvent(event="turn_start"), SseEvent(event="attention", data={"kind": "report"})],
        summary={"counts": {"pending_reports": 1}, "attention_events_enabled": True},
    )
    c, model, sleeps, changes = _run_controller([api], on_attention=attention.append)
    assert api.calls["summary"] >= 2 and api.calls["status"] >= 2   # 快照 + 事件触发的刷新
    assert attention == [{"kind": "report"}]
    assert "attention" in changes
    assert api.closed is True
    assert model.menu_state().connected is False        # 流结束后进入重连
    assert model.menu_state().status_text.endswith(f"（{REASON_LOST}）")


def test_controller_heartbeat_polls_only_after_interval():
    now = {"t": 0.0}
    api = FakeApi(events=[SseEvent(event="heartbeat"), SseEvent(event="heartbeat")])

    class Clk:
        def __call__(self):
            return now["t"]

    clock = Clk()
    # 第一次心跳：距上次快照 0s < 30s，不刷新；之后拨快时钟再来一次
    orig_events = api.events

    def gen(stop):
        yield orig_events[0]
        now["t"] = 31.0
        yield orig_events[1]

    api.stream_events = gen
    _run_controller([api], poll_interval=30.0, clock=clock)
    assert api.calls["summary"] == 2        # 快照 1 次 + 第二个心跳兜底 1 次


def test_controller_offline_when_daemon_not_running_reads_crash_file():
    c, model, sleeps, _ = _run_controller(
        [FakeApi(healthy=False)], offline_crashes=lambda: 3)
    s = model.menu_state()
    assert s.icon is TrayIcon.ERROR and s.can_start_daemon is True
    assert "3 条未确认崩溃告警" in s.status_text


def test_controller_backoff_grows_and_resets_after_connection():
    seq = [FakeApi(healthy=False), FakeApi(healthy=False), FakeApi(healthy=False),
           FakeApi(healthy=False), FakeApi(events=[]), FakeApi(healthy=False)]
    c, model, sleeps, _ = _run_controller(seq)
    # 失败 4 次：1,2,4,4(封顶)；第 5 次连上后断开 → 重置为 1；最后一次失败 → 2
    assert sleeps[:6] == [1.0, 2.0, 4.0, 4.0, 1.0, 1.0]


def test_controller_auth_failure_is_reported_not_retried_as_down():
    c, model, _, _ = _run_controller([FakeApi(status_exc=_http_status_error(401))])
    s = model.menu_state()
    assert REASON_AUTH in s.status_text and s.can_start_daemon is False


def test_controller_stream_error_goes_offline():
    c, model, _, _ = _run_controller([FakeApi(stream_exc=httpx.ReadTimeout("t"))])
    assert REASON_LOST in model.menu_state().status_text


def test_controller_auto_start_only_once_per_offline_episode():
    started = []
    seq = [FakeApi(healthy=False), FakeApi(healthy=False), FakeApi(events=[]),
           FakeApi(healthy=False)]
    _run_controller(seq, start_daemon=lambda: started.append(1), auto_start_daemon=True)
    # 第 1 次离线拉起一次；第 2 次离线不重复；连上后再掉线，再拉起一次
    assert len(started) == 2


def test_controller_no_auto_start_by_default():
    started = []
    _run_controller([FakeApi(healthy=False)], start_daemon=lambda: started.append(1))
    assert started == []


def test_controller_survives_factory_exception():
    model = TrayModel()
    box = {}
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        box["c"].stop()
        return FakeApi(healthy=False)

    c = DesktopController(model, factory, backoff=(1.0,))
    box["c"] = c
    c._sleep = lambda s: None
    c.run()
    assert calls["n"] == 2


# ════════════════════════════════════════════════════════════════════
# 集成：真实 routes + 真实 AgentBridge + uvicorn
# ════════════════════════════════════════════════════════════════════

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_daemon(tmp_path):
    uvicorn = pytest.importorskip("uvicorn")
    from fastapi import FastAPI
    from mini_agent.api.bridge import AgentBridge
    from mini_agent.api.routes import router
    from mini_agent.notification import attention_hub

    bridge = AgentBridge(ring_maxlen=200)
    bridge.agent = SimpleNamespace(cfg=SimpleNamespace(project_root=tmp_path), history=[])
    app = FastAPI()
    app.include_router(router)
    app.state.bridge = bridge
    app.state.session_pool = None
    http_server = SimpleNamespace(bridge=bridge, autonomous_loop=None,
                                  desktop_attention_events_enabled=True)
    app.state.http_server = http_server

    async def _bind_loop():
        bridge.broadcaster.set_loop(asyncio.get_running_loop())

    app.router.on_startup.append(_bind_loop)

    def _on_attention(ev):
        bridge.emit_attention(ev["kind"], ev["title"], ev["ref_id"], ev["level"], ev["extra"])

    attention_hub.register_listener(_on_attention)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn 未能启动"
    yield SimpleNamespace(port=port, root=tmp_path, bridge=bridge)
    attention_hub.unregister_listener(_on_attention)
    server.should_exit = True
    t.join(timeout=5)


def _wait_for(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_live_run_check_against_real_routes(live_daemon, capsys):
    _write(live_daemon.root / ".agent/daemon_info.json",
           json.dumps({"http_port": live_daemon.port}))
    code = run_check(live_daemon.root, 8765)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "health       : ok" in out and "attention_total" in out


def test_live_attention_event_reaches_tray_model(live_daemon):
    """端到端：新增汇报 → attention_hub → SSE → 客户端 → 托盘角标。"""
    from mini_agent.notification.reports_store import append_report
    from mini_agent.storage.paths import AgentPaths

    model = TrayModel()
    got = []

    def factory():
        return DaemonApi(f"http://127.0.0.1:{live_daemon.port}", None)

    c = DesktopController(model, factory, on_attention=got.append, poll_interval=999)
    c.start()
    try:
        assert _wait_for(lambda: model.menu_state().connected), "未能连上 daemon"
        assert model.menu_state().badge == 0
        # SSE 订阅可能比"connected"稍晚一拍，持续重发直到收到事件
        paths = AgentPaths(live_daemon.root)
        n = {"i": 0}

        def push_until_seen():
            if not got:
                n["i"] += 1
                append_report(paths, {"report_id": f"r{n['i']}", "title": f"汇报{n['i']}",
                                      "acknowledged": False, "created_at": time.time()})
            return bool(got)

        assert _wait_for(push_until_seen), "没收到 attention 事件"
        assert got[0]["kind"] == "report"
        assert _wait_for(lambda: model.menu_state().badge >= 1), "summary 未刷新"
        assert model.menu_state().icon is TrayIcon.ATTENTION
    finally:
        c.stop()


def test_live_controller_connects_without_token(live_daemon):
    model = TrayModel()
    c = DesktopController(
        model, lambda: DaemonApi(f"http://127.0.0.1:{live_daemon.port}", None),
        backoff=(0.1,), poll_interval=999)
    c.start()
    try:
        assert _wait_for(lambda: model.menu_state().connected)
    finally:
        c.stop()


# ════════════════════════════════════════════════════════════════════
# app 参数解析
# ════════════════════════════════════════════════════════════════════

def test_project_root_resolution_order(tmp_path):
    cfg = DesktopConfig(project_root=str(tmp_path / "from_cfg"))
    assert resolve_project_root(parse_args([]), cfg) == (tmp_path / "from_cfg").resolve()
    args = parse_args(["--project-root", str(tmp_path / "cli")])
    assert resolve_project_root(args, cfg) == (tmp_path / "cli").resolve()
    # 未配置时不再静默使用当前目录：返回自动查找结果（找不到则 None），见 test_desktop_locator.py
    found = resolve_project_root(parse_args([]), DesktopConfig())
    assert found is None or (found / "src" / "mini_agent" / "__init__.py").is_file()


def test_run_check_reports_not_running(tmp_path, capsys):
    port = _free_port()          # 没人监听
    assert run_check(tmp_path, port) == 2
    assert "daemon 未运行" in capsys.readouterr().out


# ════════════════════════════════════════════════════════════════════
# Qt 冒烟（离屏）
# ════════════════════════════════════════════════════════════════════

def test_qt_tray_ui_refresh_offscreen():
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from mini_agent_desktop.tray import TrayUi, make_icon

    app = QApplication.instance() or QApplication([])
    model = TrayModel()
    calls = []
    ui = TrayUi(model, "http://localhost:8501",
                on_start_daemon=lambda: calls.append("start"),
                on_reconnect=lambda: calls.append("reconnect"),
                on_quit=lambda: calls.append("quit"))
    # 初始未连接：显示"启动 Agent"需要 reason==未运行；初始 reason 是"连接中…"
    assert "未连接" in ui.status_action.text()
    model.set_offline(REASON_NOT_RUNNING, 1)
    s = ui.refresh()
    assert ui.start_action.isVisible() is True
    assert "崩溃告警" in ui.status_action.text()
    model.set_connected()
    model.apply_status({"state": "running", "queue_depth": 1})
    model.apply_summary({"counts": {"pending_permissions": 2}})
    ui.refresh()
    assert ui.start_action.isVisible() is False
    assert ui.tasks_action.text() == "当前任务：2 个"
    assert ui.attention_action.text() == "待处理事项：2 个"
    assert "待审批 2" in ui.tray.toolTip()
    # 每种状态都能画出非空图标
    for st in TrayIcon:
        assert not make_icon(st, 12).pixmap(32, 32).isNull()
    ui.quit_action.trigger()
    ui.reconnect_action.trigger()
    assert calls == ["quit", "reconnect"]
    # 跨线程 notify 经信号投递，不崩溃
    t = threading.Thread(target=ui.notify_changed)
    t.start(); t.join()
    app.processEvents()
