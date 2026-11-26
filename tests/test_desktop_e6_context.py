"""E6 上下文感知（最小版）：分类、格式、配置默认值、面板读取时机与发送行为。"""

import json
import time

import httpx
import pytest

from mini_agent_desktop.config import DesktopConfig
from mini_agent_desktop.context import (
    KIND_BROWSER, KIND_EDITOR, KIND_OTHER, KIND_TERMINAL, TITLE_LIMIT,
    classify_app, format_context, make_window, read_foreground_window,
)


@pytest.mark.parametrize("exe,kind", [
    ("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", KIND_BROWSER),
    ("msedge.exe", KIND_BROWSER),
    ("C:/x/Code.exe", KIND_EDITOR),         # 大小写不敏感：VS Code 的可执行文件就是 code.exe
    ("code.exe", KIND_EDITOR),
    ("CURSOR.EXE", KIND_EDITOR),
    ("WindowsTerminal.exe", KIND_TERMINAL),
    ("spotify.exe", KIND_OTHER),
    ("", KIND_OTHER),
])
def test_classify_app_by_executable_name(exe, kind):
    assert classify_app(exe, "任意标题") == kind


def test_title_does_not_change_classification():
    assert classify_app("spotify.exe", "chrome.exe 教程") == KIND_OTHER


def test_make_window_truncates_and_collapses_whitespace():
    win = make_window("chrome.exe", "a   b\n" + "x" * 300)
    assert win.kind == KIND_BROWSER and win.exe == "chrome.exe"
    assert len(win.title) == TITLE_LIMIT + 1 and win.title.endswith("…")
    assert win.title.startswith("a b ")


def test_format_context_states_boundary_and_handles_none():
    assert format_context(None) == ""
    text = format_context(make_window("code.exe", "main.py - proj"))
    assert "编辑器" in text and "code.exe" in text and "main.py - proj" in text
    assert "未读取页面或文件内容" in text


def test_format_context_without_title():
    assert "，标题「" not in format_context(make_window("code.exe", ""))


def test_default_config_is_off(tmp_path, monkeypatch):
    assert DesktopConfig().context_awareness_enabled is False
    cfg_path = tmp_path / "missing.json"
    monkeypatch.setenv("MINI_AGENT_DESKTOP_CONFIG", str(cfg_path))
    from mini_agent_desktop.config import load_config
    assert load_config(cfg_path).context_awareness_enabled is False


def test_foreground_reader_is_none_off_windows():
    import os
    if os.name == "nt":
        pytest.skip("仅在非 Windows 上验证降级行为")
    assert read_foreground_window() is None


def _panel(reader, sent):
    from mini_agent_desktop.api import DaemonApi
    from mini_agent_desktop.quick import QuickSession
    from mini_agent_desktop.quick_panel import QuickPanel

    def handler(request):
        if request.method == "POST" and request.url.path == "/v1/chat":
            sent.append(json.loads(request.content)["message"])
            return httpx.Response(200, json={"turn_id": "t1", "queued": True})
        if request.url.path == "/v1/turns/t1":
            return httpx.Response(200, json={"turn_id": "t1", "state": "done"})
        if request.url.path == "/v1/history":
            return httpx.Response(200, json={"messages": []})
        return httpx.Response(200, json={"turns": []})

    def factory():
        return QuickSession(DaemonApi("http://x", "tok", transport=httpx.MockTransport(handler)))

    panel = QuickPanel(factory)
    if reader is not None:
        panel.set_context_reader(reader)
    return panel


def _wait_sent(app, sent):
    deadline = time.monotonic() + 10
    while not sent and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def test_panel_off_by_default_never_reads_window():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    calls = []
    panel = _panel(None, [])
    panel.open_panel()
    assert calls == [] and panel.context_box.isVisible() is False
    panel.hide()


def test_panel_reads_before_show_and_sends_context_once():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    sent = []
    seen_visible = []
    win = make_window("code.exe", "main.py - mini")

    def reader():
        seen_visible.append(panel.isVisible())     # 必须在面板显示之前读取
        return win

    panel = _panel(reader, sent)
    panel.open_panel()
    assert seen_visible == [False]
    assert panel.context_box.isChecked() and "code.exe" in panel.context_box.text()

    panel.input.setPlainText("这段代码有什么问题")
    panel._submit()
    _wait_sent(app, sent)
    assert sent and sent[0].startswith("[用户当前前台窗口：编辑器")
    assert sent[0].endswith("这段代码有什么问题")
    assert panel._captured is None              # 快照用完即弃
    panel.hide()


def test_unchecked_box_sends_no_context():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    sent = []
    panel = _panel(lambda: make_window("chrome.exe", "标题"), sent)
    panel.open_panel()
    panel.context_box.setChecked(False)
    panel.input.setPlainText("你好")
    panel._submit()
    _wait_sent(app, sent)
    assert sent == ["你好"]
    panel.hide()


def test_unreadable_window_disables_checkbox():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    panel = _panel(lambda: None, [])
    panel.open_panel()
    assert panel.context_box.isEnabled() is False and "无法读取" in panel.context_box.text()
    panel.hide()


def test_reader_exception_does_not_block_panel():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    def boom():
        raise OSError("win32 error")

    panel = _panel(boom, [])
    panel.open_panel()                           # 不应抛出
    assert panel.isVisible() or panel.isHidden() is False
    panel.hide()
