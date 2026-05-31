"""
orchestrator/status_bar.py — 底部固定状态栏

设计目标：
  状态栏常驻终端底部，agent 运行过程中持续可见。
  agent 打印正常内容时，状态栏自动上移让路，内容打印后状态栏回到底部。
  用户始终能同时看到：agent 的输出 + plan 进度。

实现原理（"让路-重绘"模式）：
  1. 后台线程每 250ms 刷新状态栏（重绘到底部）
  2. 任何有输出的地方调用 before_print() → 擦除状态栏
     打印内容后调用 after_print() → 重绘状态栏
  3. 不再使用 suppress/全局静默，因为那会让用户在 agent 运行时什么都看不到

三步原子操作（before_print / after_print）：
  before_print()  ─ 上移 N 行 + 擦除状态栏区域
  [ 你的 print() ]
  after_print()   ─ 重绘状态栏到当前光标之后

这两个函数是线程安全的，加锁保证与后台刷新不冲突。
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

from orchestrator.concurrency import concurrency_snapshot


class StatusBar:
    """
    底部固定状态栏。

    外部调用方式（renderer.py / agent.py）：
      bar.before_print()  # 准备打印正常内容前调用
      ... 正常 print / rich.console.print ...
      bar.after_print()   # 打印完成后调用，恢复状态栏

    或使用上下文管理器：
      with bar.printing():
          console.print("hello")
    """

    def __init__(self, refresh_hz: int = 4) -> None:
        self._interval = 1.0 / refresh_hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_lines = 0        # 上次画了几行状态栏
        self._printing = False      # 是否正在打印正常内容（临时让路状态）

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="status-bar", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._erase()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    # ── 让路接口（供 renderer.py 和 agent.py 调用） ──────────────────────────

    def before_print(self) -> None:
        """
        在打印任何内容前调用。
        擦除底部状态栏，让光标回到内容区末尾，使后续 print 出现在状态栏"上方"。
        线程安全。
        """
        with self._lock:
            self._printing = True
            self._erase_locked()

    def after_print(self) -> None:
        """
        打印内容结束后调用。
        把状态栏重新画到当前光标位置之后（即内容的下方）。
        线程安全。
        """
        with self._lock:
            self._printing = False
            self._render_locked()

    class _PrintCtx:
        def __init__(self, bar: "StatusBar") -> None:
            self._bar = bar
        def __enter__(self):
            self._bar.before_print()
        def __exit__(self, *_):
            self._bar.after_print()

    def printing(self):
        """上下文管理器版本：with bar.printing(): ..."""
        return self._PrintCtx(self)

    # ── 向后兼容：suppress/unsuppress → 现在是空操作 ─────────────────────────
    # agent.py 里有 suppress_bar() 调用，保留接口避免报错，但不再静默状态栏

    def suppress(self) -> None:
        pass  # 不再静默，状态栏始终显示

    def unsuppress(self) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    @property
    def suppressed(self) -> bool:
        return False

    # ── 刷新循环 ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if not self._printing:
                    self._render_locked()
            time.sleep(self._interval)
        self._erase()

    def _render_locked(self) -> None:
        lines = _build_all_lines()
        if not lines:
            self._erase_locked()
            return
        stderr = sys.stderr
        # 先擦掉上一次的状态栏
        if self._last_lines > 0:
            stderr.write(f"\x1b[{self._last_lines}A\x1b[0J")
        for line in lines:
            stderr.write(line + "\n")
        stderr.flush()
        self._last_lines = len(lines)

    def _erase_locked(self) -> None:
        if self._last_lines > 0:
            sys.stderr.write(f"\x1b[{self._last_lines}A\x1b[0J")
            sys.stderr.flush()
            self._last_lines = 0

    def _erase(self) -> None:
        with self._lock:
            self._erase_locked()


# ── 行构建 ────────────────────────────────────────────────────────────────────

def _build_all_lines() -> list[str]:
    """组合并发状态行 + plan 状态行。"""
    lines: list[str] = []

    snap = concurrency_snapshot()
    t  = snap["tasks"]
    ll = snap["llm"]
    active_total = t["active"] + ll["active"] + t["waiting"] + ll["waiting"]
    if active_total > 0:
        lines.extend(_build_concurrency_lines(t, ll))

    lines.extend(_build_plan_lines())
    return lines


def _build_concurrency_lines(t: dict, ll: dict) -> list[str]:
    lines = []
    for snap, icon, label, a_color, w_color in [
        (t,  "⚡", "Tasks", "\033[36m", "\033[33m"),
        (ll, "🤖", "LLM  ", "\033[34m", "\033[33m"),
    ]:
        active  = snap["active"]
        waiting = snap["waiting"]
        limit   = snap["limit"]
        filled  = min(active, limit)
        empty   = max(0, limit - filled)
        bar     = a_color + "█" * filled + "\033[90m" + "░" * empty + "\033[0m"
        status  = f"{a_color}{active} running\033[0m" if active else "\033[90midle\033[0m"
        queue   = ""
        if waiting > 0:
            names = ", ".join(w["label"][:20] for w in snap["waiters"][:3])
            extra = f" +{waiting - 3}" if waiting > 3 else ""
            queue = f"  {w_color}⏳ {waiting} queued\033[0m: \033[90m{names}{extra}\033[0m"
        lines.append(f"  {icon} {label} [{bar}] {active}/{limit}   {status}{queue}")
    return lines


def _build_plan_lines() -> list[str]:
    try:
        from orchestrator.plan_display import build_plan_status_lines
        return build_plan_status_lines()
    except Exception:
        return []


# ── 模块级单例 ────────────────────────────────────────────────────────────────

_bar: Optional[StatusBar] = None


def start_status_bar(refresh_hz: int = 4) -> StatusBar:
    global _bar
    _bar = StatusBar(refresh_hz=refresh_hz)
    _bar.start()
    return _bar


def stop_status_bar() -> None:
    global _bar
    if _bar:
        _bar.stop()
        _bar = None


def get_status_bar() -> Optional[StatusBar]:
    return _bar


def suppress_bar() -> None:
    """向后兼容，现已为空操作。"""
    pass


def unsuppress_bar() -> None:
    """向后兼容，现已为空操作。"""
    pass


def before_print() -> None:
    """全局便捷函数：打印前让状态栏让路。"""
    if _bar:
        _bar.before_print()


def after_print() -> None:
    """全局便捷函数：打印后重绘状态栏。"""
    if _bar:
        _bar.after_print()
