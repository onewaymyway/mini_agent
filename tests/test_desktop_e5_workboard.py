"""E5 目标与定时任务视图：归一化、API 请求形态、窗口渲染与启停（离屏 Qt）。"""

import json
import time

import httpx
import pytest

from mini_agent_desktop.api import DaemonApi
from mini_agent_desktop.workboard import summarize_cron, summarize_objectives


def test_objectives_sorted_active_first_and_fallback_progress():
    rows = summarize_objectives([
        {"id": "o3", "title": "旧目标", "status": "done", "progress_notes": "已完成"},
        {"id": "o2", "title": "暂停的", "status": "paused"},
        {"id": "o1", "title": "在做", "status": "active", "work_thread_progress": "第 3 步"},
        "not-a-dict",
    ])
    assert [r.id for r in rows] == ["o1", "o2", "o3"]
    assert rows[0].status_label == "进行中" and rows[0].progress == "第 3 步"
    assert rows[1].progress == "（暂无进展记录）"
    assert rows[2].status_label == "已完成"


def test_objective_missing_fields_are_safe():
    rows = summarize_objectives([{}])
    assert rows[0].title == "（未命名目标）" and rows[0].status == "unknown"


def test_cron_order_running_then_enabled_by_next_then_disabled():
    rows = summarize_cron([
        {"id": "a", "name": "停用的", "enabled": False},
        {"id": "b", "name": "晚点", "enabled": True, "next_run_str": "2026-11-27 09:00"},
        {"id": "c", "name": "早点", "enabled": True, "next_run_str": "2026-11-26 10:00"},
        {"id": "d", "name": "正在跑", "enabled": True, "execution_phase": "running",
         "next_run_str": "2026-11-27 23:00"},
    ])
    assert [r.id for r in rows] == ["d", "c", "b", "a"]
    assert rows[0].phase_label == "运行中"
    assert rows[3].next_run == "已停用" and rows[3].enabled is False


class _Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        p = request.url.path
        if p == "/v1/goals":
            return httpx.Response(200, json={"objectives": [{"id": "o1", "title": "T", "status": "active"}]})
        if p == "/v1/cron/jobs":
            return httpx.Response(200, json={"jobs": [{"id": "j1", "name": "J", "enabled": True}]})
        if p.startswith("/v1/cron/jobs/") and request.method == "PUT":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={})


def _api(fake):
    return DaemonApi("http://127.0.0.1:8765", "tok", transport=httpx.MockTransport(fake))


def test_api_goals_and_cron_paths():
    fake = _Fake()
    api = _api(fake)
    assert api.goals()["objectives"][0]["id"] == "o1"
    assert api.cron_jobs()[0]["id"] == "j1"
    assert [c.url.path for c in fake.calls] == ["/v1/goals", "/v1/cron/jobs"]


def test_set_cron_enabled_sends_only_enabled_field():
    fake = _Fake()
    _api(fake).set_cron_enabled("j1", False)
    req = fake.calls[-1]
    assert req.method == "PUT" and req.url.path == "/v1/cron/jobs/j1"
    assert json.loads(req.content) == {"enabled": False}


def test_window_renders_and_toggles_selected_job():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.workboard_window import WorkboardWindow

    app = QApplication.instance() or QApplication([])
    toggled = []
    win = WorkboardWindow(lambda: ([{"id": "o1", "title": "写文档", "status": "active"}],
                                   [{"id": "j1", "name": "每日巡检", "enabled": True,
                                     "next_run_str": "明天 09:00"}]),
                          lambda jid, en: toggled.append((jid, en)))
    win._bridge.loaded.emit(win._fetch())
    app.processEvents()
    assert win.goal_table.rowCount() == 1 and win.goal_table.item(0, 1).text() == "写文档"
    assert win.cron_table.rowCount() == 1

    win.tabs.setCurrentWidget(win.cron_table)
    win.cron_table.selectRow(0)
    app.processEvents()
    assert win.toggle_btn.isEnabled()
    win._toggle_selected()
    deadline = time.monotonic() + 5
    while not toggled and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert toggled == [("j1", False)]        # 当前启用 → 请求停用
    win.hide()
