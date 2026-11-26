"""E6：上下文感知（最小版）——只读取前台窗口的程序名与标题。

边界（写死在这里，也写在文档里）：
  - 只在用户打开快速面板的那一刻读取一次；不轮询、不监听；
  - 不读取 URL、页面内容、编辑器文件内容、剪贴板或键盘输入；
  - 结果只保存在面板内存中，发送后即清空，不写日志、不写配置；
  - 默认关闭，由用户在托盘中显式开启，且面板内可以逐次取消勾选。

classify_app / format_context 为纯函数；read_foreground_window 仅 Windows 有效（其它系统返回 None）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Optional

KIND_BROWSER = "browser"
KIND_EDITOR = "editor"
KIND_TERMINAL = "terminal"
KIND_OTHER = "other"
KIND_LABEL = {KIND_BROWSER: "浏览器", KIND_EDITOR: "编辑器", KIND_TERMINAL: "终端", KIND_OTHER: "应用"}

_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "safari"}
_EDITORS = {"code.exe", "cursor.exe", "devenv.exe", "notepad++.exe", "sublime_text.exe",
            "pycharm64.exe", "idea64.exe", "webstorm64.exe", "notepad.exe"}
_TERMINALS = {"windowsterminal.exe", "wt.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "bash.exe"}
TITLE_LIMIT = 120


@dataclass(frozen=True)
class ActiveWindow:
    exe: str          # 程序文件名，如 chrome.exe
    title: str        # 窗口标题（已截断）
    kind: str         # browser / editor / terminal / other


def classify_app(exe: str, title: str = "") -> str:
    """按程序文件名归类。标题暂不参与判断，避免把任意文字误当成应用类型。"""
    name = PureWindowsPath(exe or "").name.lower() if exe else ""
    if name in _BROWSERS:
        return KIND_BROWSER
    if name in _EDITORS:
        return KIND_EDITOR
    if name in _TERMINALS:
        return KIND_TERMINAL
    return KIND_OTHER


def make_window(exe: str, title: str) -> ActiveWindow:
    clean = " ".join((title or "").split())
    if len(clean) > TITLE_LIMIT:
        clean = clean[:TITLE_LIMIT] + "…"
    name = PureWindowsPath(exe or "").name or "未知程序"
    return ActiveWindow(exe=name, title=clean, kind=classify_app(name, clean))


def format_context(win: Optional[ActiveWindow]) -> str:
    """生成附在消息前的一行上下文；没有窗口信息时返回空串。"""
    if win is None:
        return ""
    label = KIND_LABEL.get(win.kind, "应用")
    title = f"，标题「{win.title}」" if win.title else ""
    return (f"[用户当前前台窗口：{label}，程序 {win.exe}{title}"
            f"（仅窗口标题与程序名，未读取页面或文件内容）]")


def read_foreground_window() -> Optional[ActiveWindow]:  # pragma: no cover - 仅 Windows 实机验证
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)     # PROCESS_QUERY_LIMITED_INFORMATION
    exe = ""
    if handle:
        try:
            size = wintypes.DWORD(1024)
            path_buf = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size)):
                exe = path_buf.value
        finally:
            kernel32.CloseHandle(handle)
    return make_window(exe, buf.value)
