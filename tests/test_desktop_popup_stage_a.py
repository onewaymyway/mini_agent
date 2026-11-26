"""阶段A（弹窗可见性）测试。

覆盖：
- ToastPolicy 每次判定都记录原因（last_reason），能解释“为什么没弹窗”；
- 运行时可开关（托盘勾选即时生效）；
- 托盘新增“桌面弹窗提醒”勾选项与“发送测试弹窗”，回调与初始状态正确；
- --check 的弹窗/daemon 推送状态行。
"""

import os
from datetime import datetime

import pytest

from mini_agent_desktop.attention import (
    REASON_DISABLED, REASON_DUPLICATE, REASON_NO_REF, REASON_OK,
    REASON_QUICK_SESSION, REASON_QUIET, REASON_SILENT_KIND,
    QuietHours, ToastPolicy,
)


def _ev(kind, ref="r1", title="标题", level="normal", extra=None):
    return {"kind": kind, "ref_id": ref, "title": title, "level": level, "extra": extra or {}}


def test_default_policy_reports_disabled_reason():
    p = ToastPolicy()
    assert p.decide(_ev("permission")) is None
    assert p.last_reason == REASON_DISABLED


def test_reasons_cover_each_silent_path():
    p = ToastPolicy(enabled=True)
    assert p.decide(_ev("report")) is None and p.last_reason == REASON_SILENT_KIND
    assert p.decide(_ev("turn_done", extra={"session_id": "desktop-quick"})) is None
    assert p.last_reason == REASON_QUICK_SESSION
    assert p.decide(_ev("permission", ref="")) is None and p.last_reason == REASON_NO_REF
    assert p.decide(_ev("permission", ref="same")) is not None and p.last_reason == REASON_OK
    assert p.decide(_ev("permission", ref="same")) is None and p.last_reason == REASON_DUPLICATE


def test_reason_quiet_hours_only_for_non_blocking():
    fixed = datetime(2026, 1, 1, 23, 30)
    p = ToastPolicy(enabled=True, quiet=QuietHours.parse("23:00-08:00"), clock=lambda: fixed)
    assert p.decide(_ev("turn_done", extra={"session_id": "main"})) is None
    assert p.last_reason == REASON_QUIET
    assert p.decide(_ev("permission", ref="p1")) is not None
    assert p.last_reason == REASON_OK


def test_enabled_can_be_switched_at_runtime():
    p = ToastPolicy()
    assert p.decide(_ev("permission", ref="a")) is None
    p.enabled = True
    assert p.decide(_ev("permission", ref="b")) is not None
    p.enabled = False
    assert p.decide(_ev("permission", ref="c")) is None
    assert p.last_reason == REASON_DISABLED


def test_non_dict_event_is_silent_without_crash():
    p = ToastPolicy(enabled=True)
    assert p.decide("not-a-dict") is None


def test_tray_popup_toggle_and_test_action():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from mini_agent_desktop.state import TrayModel
    from mini_agent_desktop.tray import TrayUi

    QApplication.instance() or QApplication([])
    calls = []
    ui = TrayUi(
        TrayModel(), "http://x",
        on_start_daemon=lambda: None,
        on_reconnect=lambda: None,
        on_quit=lambda: None,
        on_toggle_toast=lambda v: calls.append(v),
        toast_checked=False,
    )
    texts = [a.text() for a in ui.menu.actions()]
    assert "桌面弹窗提醒" in texts and "发送测试弹窗" in texts
    assert ui.toast_action.isCheckable() and not ui.toast_action.isChecked()
    ui.toast_action.setChecked(True)
    assert calls == [True]
    ui.toast_action.setChecked(False)
    assert calls == [True, False]


def test_popup_status_line_explains_both_switches(tmp_path, monkeypatch):
    from mini_agent_desktop import app as app_mod
    from mini_agent_desktop.config import DesktopConfig, save_config

    cfg_path = tmp_path / "desktop.json"
    monkeypatch.setenv("MINI_AGENT_DESKTOP_CONFIG", str(cfg_path))
    save_config(DesktopConfig(toast_enabled=False), cfg_path)
    line = app_mod._popup_status_line({"attention_events_enabled": False})
    assert "桌面弹窗     : 关闭" in line
    assert "daemon 推送  : 关闭" in line

    save_config(DesktopConfig(toast_enabled=True, quiet_hours="23:00-08:00"), cfg_path)
    line = app_mod._popup_status_line({"attention_events_enabled": True})
    assert "开启" in line and "免打扰       : 23:00-08:00" in line
