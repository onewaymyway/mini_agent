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

import os
import platform
import subprocess
import time
from typing import Optional

from ..events import ActivityEvent
from .base import BaseCollector


_WIN_BACKEND_LOGGED = False


def _is_windows() -> bool:
    return os.name == "nt"


def _pywin32_importable() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("win32gui", "win32process"))


def _detect_windows_pywin32() -> Optional[tuple[str, str]]:
    """原有实现：依赖 pywin32 + psutil。"""
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
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.active_window._detect_windows')
            pass
        return (app_name or "unknown", title)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.active_window._detect_windows')
        return None


class _Win32Api:
    """ctypes 直接调用 user32 / kernel32，不依赖 pywin32。只在 Windows 上实例化。"""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._user32, self._kernel32 = user32, kernel32

    def foreground(self) -> Optional[int]:
        return self._user32.GetForegroundWindow() or None

    def title(self, hwnd: int) -> str:
        n = self._user32.GetWindowTextLengthW(hwnd)
        buf = self._ctypes.create_unicode_buffer(n + 1)
        self._user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value

    def pid(self, hwnd: int) -> int:
        pid = self._wintypes.DWORD()
        self._user32.GetWindowThreadProcessId(hwnd, self._ctypes.byref(pid))
        return pid.value

    def image_path(self, pid: int) -> str:
        """进程映像完整路径。无权限（如提权进程）时返回空串，不抛异常。"""
        if not pid:
            return ""
        handle = self._kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = self._wintypes.DWORD(32768)
            buf = self._ctypes.create_unicode_buffer(size.value)
            ok = self._kernel32.QueryFullProcessImageNameW(handle, 0, buf, self._ctypes.byref(size))
            return buf.value if ok else ""
        finally:
            self._kernel32.CloseHandle(handle)


def _foreground_from_api(api) -> Optional[tuple[str, str]]:
    """纯逻辑：由 api 提供的四个原语组合出 (程序名, 窗口标题)。可用假 api 单测。"""
    from pathlib import PureWindowsPath

    hwnd = api.foreground()
    if not hwnd:
        return None
    title = api.title(hwnd) or ""
    pid = api.pid(hwnd)
    path = api.image_path(pid) if pid else ""
    name = PureWindowsPath(path).name if path else ""
    return (name or "unknown", title)


_WIN32_API = None


def _detect_windows_ctypes() -> Optional[tuple[str, str]]:
    global _WIN32_API
    if not _is_windows():
        return None
    try:
        if _WIN32_API is None:
            _WIN32_API = _Win32Api()
        return _foreground_from_api(_WIN32_API)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.active_window._detect_windows_ctypes')
        return None


def windows_backend() -> Optional[str]:
    """当前 Windows 前台窗口采集使用的后端：'pywin32' / 'ctypes'；非 Windows 返回 None。"""
    if not _is_windows():
        return None
    return "pywin32" if _pywin32_importable() else "ctypes"


def _detect_windows() -> Optional[tuple[str, str]]:
    """优先 pywin32（保持原行为）；未安装时回退到 ctypes。首次成功时记录一次所用后端。"""
    global _WIN_BACKEND_LOGGED
    backend = windows_backend()
    result = _detect_windows_pywin32() if backend == "pywin32" else _detect_windows_ctypes()
    if result is not None and not _WIN_BACKEND_LOGGED:
        _WIN_BACKEND_LOGGED = True
        import logging
        logging.getLogger(__name__).info("前台窗口采集后端：%s", backend)
    return result


def _detect_macos() -> Optional[tuple[str, str]]:
    # 优先 AppKit，环境没有 pyobjc 时退化为 osascript（需要用户授予"辅助功能"权限）
    try:
        from AppKit import NSWorkspace  # type: ignore

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = app.localizedName() if app else "unknown"
        # AppKit 拿不到窗口标题，标题留空，交由 osascript 兜底（可选，成本更高，默认不做）
        return (app_name or "unknown", "")
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.active_window._detect_macos')
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
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.active_window._detect_macos')
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
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.active_window._detect_linux')
        pass
    try:
        out = subprocess.run(["wmctrl", "-a", ":ACTIVE:"], capture_output=True, text=True, timeout=2)
        # wmctrl 本身不直接给"当前活跃窗口"，这里仅作为占位，实际环境建议装 xdotool
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.behavior.collectors.active_window._detect_linux')
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
