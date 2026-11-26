"""E2 托盘健康面板：统计模型、行生成、控制器埋点、窗口刷新（离屏 Qt）。"""

import pytest

from mini_agent_desktop.health import HealthStats, build_health_rows


def _labels(rows):
    return {label: (value, level) for label, value, level in rows}


def test_stats_transitions_count_connections_and_reconnects():
    s = HealthStats()
    s.on_connected(100.0)
    s.on_event()
    s.on_offline("daemon 未运行", 150.0)
    s.on_connected(200.0)
    assert s.connected_count == 2 and s.reconnects == 1
    assert s.connected_since == 200.0 and s.events_received == 1
    assert s.last_offline_reason == "daemon 未运行" and s.last_offline_at == 150.0


def test_rows_when_connected_show_duration_and_ok_level():
    s = HealthStats()
    s.on_connected(1000.0)
    rows = _labels(build_health_rows({"now": 1125.0, "stats": s, "toast_enabled": True,
                                      "hotkey_active": True, "hotkey": "Ctrl+Alt+Space"}))
    assert rows["连接状态"][1] == "ok" and "2分5秒" in rows["连接状态"][0]
    assert rows["桌面弹窗提醒"] == ("已开启", "ok")
    assert rows["全局热键"][0] == "已注册 Ctrl+Alt+Space"


def test_rows_when_offline_show_reason_and_error_level():
    s = HealthStats()
    s.on_offline("认证失败", 5.0)
    rows = _labels(build_health_rows({"now": 10.0, "stats": s, "crashes": 2}))
    assert rows["连接状态"] == ("未连接：认证失败", "error")
    assert rows["未确认崩溃告警"] == ("2", "error")


def test_push_row_reflects_summary_flag():
    s = HealthStats()
    assert _labels(build_health_rows({"stats": s}))["daemon 事件推送"][1] == "warn"
    s.attention_events_enabled = True
    assert _labels(build_health_rows({"stats": s}))["daemon 事件推送"][0] == "已开启"


def test_controller_records_offline_and_connection_times():
    from mini_agent_desktop.controller import DesktopController
    from mini_agent_desktop.state import TrayModel

    c = DesktopController(TrayModel(), api_factory=lambda: None, wall=lambda: 42.0)
    c._go_offline("daemon 未运行")
    assert c.health.last_offline_reason == "daemon 未运行"
    assert c.health.last_offline_at == 42.0
    c.health.on_connected(50.0)
    assert c.health.connected_count == 1 and c.health.connected_since == 50.0


def test_health_window_populates_rows_offscreen():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.health_window import HealthWindow

    QApplication.instance() or QApplication([])
    s = HealthStats()
    s.on_connected(1.0)
    win = HealthWindow(lambda: {"stats": s, "now": 2.0, "endpoint": "http://x"})
    win.refresh()
    assert win.table.rowCount() >= 10
    names = [win.table.item(r, 0).text() for r in range(win.table.rowCount())]
    assert "连接状态" in names and "端点" in names
    win.hide()
