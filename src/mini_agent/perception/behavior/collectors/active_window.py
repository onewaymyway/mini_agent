"""
perception/behavior/collectors/active_window.py — 跨平台前台窗口/程序采集器

只感知"用户当前在用哪个 App / 窗口标题是什么"，不涉及窗口内容读取。
对聊天类软件（微信/QQ/Slack 等）同样只当作普通前台窗口处理——
即只知道"用户在用微信"，不解析、不读取其消息内容。

平台适配：
  Windows : pywin32（GetForegroundWindow / GetWindowText），未安装则降级为不可用
  macOS   : 优先 AppKit(NSWorkspace)，否则退化到 osascript 调 System Events
  Linux   : 优先 xdotool，其次 wmctrl（仅 X11；Wayland 下大多数合成器不支持
            全局查询前台窗口，会返回不可用状态，这是已知平台限制）

事件语义：
  只在"前台窗口发生切换"时产出一条上一个窗口的 app_focus 事件（含它的停留
  时长），而不是每次轮询都写一条，避免刷屏式的重复记录。
"""

from __future__ import annotations

import platform
import subprocess
import time
from typing import Optional

from ..events import ActivityEvent
from .base import BaseCollector


def _detect_windows() -> Optional[tuple[str, str]]:
    try:
        import win32gui  # type: ignore
        import win32process  # type: ignore
        import psutil  # type: ignore

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        app_name = ""
        try:
            app_name = psutil.Process(pid).name()
        except Exception:
            pass
        return (app_name or "unknown", title)
    except Exception:
        return None


def _detect_macos() -> Optional[tuple[str, str]]:
    # 优先 AppKit，环境没有 pyobjc 时退化为 osascript（需要用户授予"辅助功能"权限）
    try:
        from AppKit import NSWorkspace  # type: ignore

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = app.localizedName() if app else "unknown"
        # AppKit 拿不到窗口标题，标题留空，交由 osascript 兜底（可选，成本更高，默认不做）
        return (app_name or "unknown", "")
    except Exception:
        pass
    try:
        script = (
            'tell application "System Events" to get name of first application process '
            'whose frontmost is true'
        )
        out = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=2
        )
        if out.returncode == 0:
            return (out.stdout.strip() or "unknown", "")
    except Exception:
        pass
    return None


def _detect_linux() -> Optional[tuple[str, str]]:
    # xdotool 优先，wmctrl 兜底；两者都依赖 X11，Wayland 下通常拿不到，属已知限制
    try:
        win_id = subprocess.run(
            ["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=2
        )
        if win_id.returncode == 0 and win_id.stdout.strip():
            wid = win_id.stdout.strip()
            title = subprocess.run(
                ["xdotool", "getwindowname", wid], capture_output=True, text=True, timeout=2
            ).stdout.strip()
            cls = subprocess.run(
                ["xdotool", "getwindowclassname", wid], capture_output=True, text=True, timeout=2
            ).stdout.strip()
            return (cls or "unknown", title)
    except Exception:
        pass
    try:
        out = subprocess.run(["wmctrl", "-a", ":ACTIVE:"], capture_output=True, text=True, timeout=2)
        # wmctrl 本身不直接给"当前活跃窗口"，这里仅作为占位，实际环境建议装 xdotool
    except Exception:
        pass
    return None


def detect_active_window() -> Optional[tuple[str, str]]:
    """返回 (app_name, window_title)；无法检测时返回 None。"""
    system = platform.system()
    if system == "Windows":
        return _detect_windows()
    if system == "Darwin":
        return _detect_macos()
    if system == "Linux":
        return _detect_linux()
    return None


class ActiveWindowCollector(BaseCollector):
    """前台窗口采集器：轮询当前活跃窗口，切换时产出上一个窗口的停留事件。"""

    name = "active_window"

    def __init__(self, store, interval_sec: float = 2.0, redact_title: bool = True) -> None:
        super().__init__(store, interval_sec)
        self._redact_title = redact_title
        self._current: Optional[tuple[str, str]] = None
        self._since: float = time.time()
        self._source = f"{platform.system().lower()}_active_window"
        self._unavailable_warned = False

    def poll(self):
        detected = detect_active_window()
        now = time.time()

        if detected is None:
            if not self._unavailable_warned:
                self._unavailable_warned = True
            return None

        app_name, title = detected
        if self._current is None:
            self._current = (app_name, title)
            self._since = now
            return None

        if detected[0] == self._current[0] and (self._redact_title or detected[1] == self._current[1]):
            # 应用未切换（脱敏模式下不比较标题变化，避免同应用内切标签就疯狂产生事件）
            return None

        prev_app, prev_title = self._current
        duration = now - self._since
        event = ActivityEvent(
            timestamp=self._since,
            source=self._source,
            event_type="app_focus",
            app_name=prev_app,
            window_title=None if self._redact_title else prev_title,
            duration_sec=round(duration, 1),
        )
        self._current = (app_name, title)
        self._since = now
        return [event]

    def is_available(self) -> bool:
        return detect_active_window() is not None
