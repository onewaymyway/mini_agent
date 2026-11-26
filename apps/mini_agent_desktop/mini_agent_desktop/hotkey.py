"""全局热键（S2）。

- parse_hotkey()：纯函数，把 "Ctrl+Alt+Space" 之类的字符串解析成 (修饰键, 虚拟键码)，
  可在任何系统上单测；
- GlobalHotkey：Windows 下调用 RegisterHotKey。RegisterHotKey(NULL, …) 把热键绑定到
  调用线程的消息队列，所以必须在一条专用线程里跑 GetMessageW 循环；回调在该线程中
  触发，UI 层须自行切回 Qt 主线程（见 quick_panel.QuickHotkeyBridge）。

热键默认关闭（desktop.json 中 hotkey_enabled=true 才注册），与设计文档的"保守 opt-in"一致。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Callable, Optional

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_MODIFIERS = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D, "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B, "escape": 0x1B,
}


@dataclass(frozen=True)
class HotkeySpec:
    modifiers: int
    vk: int


def _key_vk(name: str) -> Optional[int]:
    if len(name) == 1 and name.isalpha():
        return ord(name.upper())
    if len(name) == 1 and name.isdigit():
        return ord(name)
    if name.startswith("f") and name[1:].isdigit():
        n = int(name[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    return _NAMED_KEYS.get(name)


def parse_hotkey(spec: str) -> HotkeySpec:
    """解析形如 "Ctrl+Alt+Space" / "win+shift+k" 的字符串。

    规则：至少一个修饰键；恰好一个非修饰键；大小写不敏感；不接受空白以外的未知记号。
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("热键不能为空")
    tokens = [t.strip().lower() for t in spec.split("+")]
    if any(not t for t in tokens):
        raise ValueError(f"热键格式错误：{spec!r}")
    modifiers = 0
    keys: list[str] = []
    for tok in tokens:
        if tok in _MODIFIERS:
            modifiers |= _MODIFIERS[tok]
        else:
            keys.append(tok)
    if modifiers == 0:
        raise ValueError("热键必须包含至少一个修饰键（Ctrl/Alt/Shift/Win）")
    if len(keys) != 1:
        raise ValueError(f"热键必须且只能包含一个普通按键：{spec!r}")
    vk = _key_vk(keys[0])
    if vk is None:
        raise ValueError(f"不支持的按键：{keys[0]!r}")
    return HotkeySpec(modifiers=modifiers, vk=vk)


class GlobalHotkey:
    """注册一个全局热键。非 Windows 下 start() 返回 (False, 原因)，不抛异常。"""

    HOTKEY_ID = 0xB5A1  # 进程内唯一即可

    def __init__(self, spec: str) -> None:
        self._spec = parse_hotkey(spec)     # 配置错误在构造时就暴露
        self._callback: Optional[Callable[[], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._result: tuple[bool, str] = (False, "未启动")

    def start(self, callback: Callable[[], None], timeout: float = 3.0) -> tuple[bool, str]:
        if os.name != "nt":
            return False, "全局热键仅支持 Windows"
        if self._thread is not None:
            return self._result
        self._callback = callback
        self._thread = threading.Thread(target=self._run, name="global-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self._result

    def stop(self) -> None:
        if os.name == "nt" and self._thread_id:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None

    # ── Windows 线程体 ───────────────────────────────────────────────────
    def _run(self) -> None:  # pragma: no cover - 仅 Windows 实机验证
        import ctypes
        from ctypes import wintypes

        class _POINT(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        class _MSG(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                ("time", wintypes.DWORD), ("pt", _POINT),
            ]

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        ok = user32.RegisterHotKey(None, self.HOTKEY_ID,
                                   self._spec.modifiers | MOD_NOREPEAT, self._spec.vk)
        if not ok:
            err = kernel32.GetLastError()
            self._result = (False, f"RegisterHotKey 失败（错误码 {err}，可能已被其它程序占用）")
            self._ready.set()
            return
        self._result = (True, "ok")
        self._ready.set()
        msg = _MSG()
        try:
            while True:
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got <= 0:            # 0 = WM_QUIT；-1 = 出错，都退出循环
                    break
                if msg.message == WM_HOTKEY and self._callback is not None:
                    try:
                        self._callback()
                    except Exception:  # noqa: BLE001 - 回调异常不能杀死消息循环
                        pass
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
