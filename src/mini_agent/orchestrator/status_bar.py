"""
orchestrator/status_bar.py

只负责构建状态栏内容并推送给 Terminal，不直接写屏幕。

Terminal 是唯一写屏幕的地方。
"""

from __future__ import annotations

import threading
import time
from typing import Optional


def _build_lines() -> list[str]:
    lines: list[str] = []
    try:
        from .concurrency import concurrency_snapshot
        snap = concurrency_snapshot()
        t, ll = snap["tasks"], snap["llm"]
        if t["active"] + ll["active"] + t["waiting"] + ll["waiting"] > 0:
            for snap_, icon, label, ac, wc in [
                (t,  "⚡", "Tasks", "\033[36m", "\033[33m"),
                (ll, "🤖", "LLM  ", "\033[34m", "\033[33m"),
            ]:
                active, waiting, limit = snap_["active"], snap_["waiting"], snap_["limit"]
                bar = ac + "█" * min(active, limit) + "\033[90m" + "░" * max(0, limit - active) + "\033[0m"
                status = f"{ac}{active} running\033[0m" if active else "\033[90midle\033[0m"
                queue_str = ""
                if waiting > 0:
                    names = ", ".join(w["label"][:20] for w in snap_["waiters"][:3])
                    extra = f" +{waiting-3}" if waiting > 3 else ""
                    queue_str = f"  {wc}⏳ {waiting} queued\033[0m: \033[90m{names}{extra}\033[0m"
                lines.append(f"  {icon} {label} [{bar}] {active}/{limit}   {status}{queue_str}")
    except Exception:
        pass

    try:
        from .plan_display import build_plan_status_lines
        lines.extend(build_plan_status_lines())
    except Exception:
        pass
    return lines


def _push_loop() -> None:
    from mini_agent.ui.terminal import get_terminal
    while not _stop.is_set():
        time.sleep(0.25)
        if not _stop.is_set():
            lines = _build_lines()
            get_terminal().update_statusbar(lines)
            get_terminal().redraw_statusbar()


_stop = threading.Event()
_thread: Optional[threading.Thread] = None


def start_status_bar(**_kwargs) -> "StatusBar":
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_push_loop, daemon=True, name="statusbar-push")
    _thread.start()
    return _bar_compat


def stop_status_bar() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=2)


def get_status_bar() -> "StatusBar":
    return _bar_compat


def suppress_bar() -> None: pass
def unsuppress_bar() -> None: pass


class StatusBar:
    def start(self): start_status_bar()
    def stop(self): stop_status_bar()
    def pause(self): pass
    def resume(self): pass
    def suppress(self): pass
    def unsuppress(self): pass
    @property
    def suppressed(self): return False
    def __enter__(self): return self
    def __exit__(self, *_): pass


_bar_compat = StatusBar()
