"""
perception/behavior/collectors/idle.py — 空闲/在场检测

只用于判断"用户是否还在电脑前"，不记录任何按键内容。
跨平台拿"距上次输入时长"的方式不统一，这里做尽力而为：
  Windows : GetLastInputInfo (ctypes)
  macOS   : Quartz.CGEventSourceSecondsSinceLastEventType（需要 pyobjc）
  Linux   : 尝试 xprintidle；没有则该采集器不可用（不影响其它采集器）
"""

from __future__ import annotations

import platform
import subprocess
from typing import Optional

from ..events import ActivityEvent
from .base import BaseCollector


def _idle_seconds_windows() -> Optional[float]:
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
            millis = ctypes.windll.kernel32.GetTickCount() - info.dwTime  # type: ignore[attr-defined]
            return millis / 1000.0
    except Exception:
        pass
    return None


def _idle_seconds_macos() -> Optional[float]:
    try:
        import Quartz  # type: ignore

        return Quartz.CGEventSourceSecondsSinceLastEventType(
            Quartz.kCGEventSourceStateHIDSystemState, Quartz.kCGAnyInputEventType
        )
    except Exception:
        return None


def _idle_seconds_linux() -> Optional[float]:
    try:
        out = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            return float(out.stdout.strip()) / 1000.0
    except Exception:
        pass
    return None


def get_idle_seconds() -> Optional[float]:
    system = platform.system()
    if system == "Windows":
        return _idle_seconds_windows()
    if system == "Darwin":
        return _idle_seconds_macos()
    if system == "Linux":
        return _idle_seconds_linux()
    return None


class IdleCollector(BaseCollector):
    name = "idle"

    def __init__(self, store, interval_sec: float = 5.0, threshold_sec: float = 120.0) -> None:
        super().__init__(store, interval_sec)
        self._threshold = threshold_sec
        self._is_idle = False
        self._source = f"{platform.system().lower()}_idle"

    def poll(self):
        idle_sec = get_idle_seconds()
        if idle_sec is None:
            return None

        events = []
        if idle_sec >= self._threshold and not self._is_idle:
            self._is_idle = True
            events.append(ActivityEvent(source=self._source, event_type="idle_start"))
        elif idle_sec < self._threshold and self._is_idle:
            self._is_idle = False
            events.append(ActivityEvent(source=self._source, event_type="idle_end"))
        return events or None

    def is_available(self) -> bool:
        return get_idle_seconds() is not None
