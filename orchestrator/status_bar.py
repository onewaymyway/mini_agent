"""
orchestrator/status_bar.py — 并发状态栏

核心设计：
  - AI 输出期间（run_turn 运行时）完全静默，不写任何字符
  - 用户输入期间（等待输入时）显示状态
  - 通过 suppress() / unsuppress() 控制输出时机，彻底避免和正文混合

显示位置：写到 stderr（与 stdout 的 prompt_toolkit 输入分离）
显示内容（仅在有并发任务/LLM 请求时）：
  ⚡ Tasks   [██░░] 2/4   2 running   ⏳ 1 queued: a1b2 task…
  🤖 LLM    [████░░░░] 4/8   4 running
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

from orchestrator.concurrency import concurrency_snapshot


class StatusBar:
    """
    并发状态栏。

    生命周期：
      start() → 启动后台刷新线程
      suppress() → AI 输出期间静默（不写 stderr）
      unsuppress() → 恢复显示
      stop() → 停止并清除

    suppress/unsuppress 应成对调用，支持嵌套计数。
    """

    def __init__(self, refresh_hz: int = 4) -> None:
        self._interval = 1.0 / refresh_hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # suppress 计数：>0 时不向 stderr 写任何内容
        self._suppress_count = 0
        # 上次写了几行（用于上移擦除）
        self._last_lines = 0

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

    # ── 抑制控制（对外接口）──────────────────────────────────────────────────

    def suppress(self) -> None:
        """
        进入静默模式：立即擦除当前状态栏，停止刷新。
        在 AI 开始输出前调用。
        """
        with self._lock:
            self._suppress_count += 1
            if self._suppress_count == 1:
                self._erase_locked()

    def unsuppress(self) -> None:
        """
        退出静默模式：恢复刷新。
        在 AI 输出结束后调用。
        """
        with self._lock:
            self._suppress_count = max(0, self._suppress_count - 1)

    # pause/resume 保持向后兼容（repl_input.py 调用）
    def pause(self) -> None:
        self.suppress()

    def resume(self) -> None:
        self.unsuppress()

    @property
    def suppressed(self) -> bool:
        with self._lock:
            return self._suppress_count > 0

    # ── 刷新循环 ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if self._suppress_count == 0:
                    self._render_locked()
            time.sleep(self._interval)
        self._erase()

    def _render_locked(self) -> None:
        """在 _lock 持有的情况下渲染（从 _loop 调用）。"""
        snap = concurrency_snapshot()
        t = snap["tasks"]
        ll = snap["llm"]

        active_total = t["active"] + ll["active"] + t["waiting"] + ll["waiting"]

        # 收集执行计划展示行
        plan_lines = _build_plan_lines()

        if active_total == 0 and not plan_lines:
            # 完全空闲，擦除已有输出
            self._erase_locked()
            return

        lines = []
        if active_total > 0:
            lines.extend(_build_lines(t, ll))
        lines.extend(plan_lines)

        stderr = sys.stderr
        if self._last_lines > 0:
            stderr.write(f"\x1b[{self._last_lines}A\x1b[0J")
        for line in lines:
            stderr.write(line + "\n")
        stderr.flush()
        self._last_lines = len(lines)

    def _erase_locked(self) -> None:
        """在 _lock 持有的情况下擦除（内部调用）。"""
        if self._last_lines > 0:
            sys.stderr.write(f"\x1b[{self._last_lines}A\x1b[0J")
            sys.stderr.flush()
            self._last_lines = 0

    def _erase(self) -> None:
        """在 _lock 未持有的情况下擦除（生命周期调用）。"""
        with self._lock:
            self._erase_locked()


# ── 行构建 ────────────────────────────────────────────────────────────────────

def _build_lines(t: dict, ll: dict) -> list[str]:
    lines = []
    for snap, icon, label, a_color, w_color in [
        (t,  "⚡", "Tasks", "\033[36m", "\033[33m"),
        (ll, "🤖", "LLM  ", "\033[34m", "\033[33m"),
    ]:
        active  = snap["active"]
        waiting = snap["waiting"]
        limit   = snap["limit"]

        filled = min(active, limit)
        empty  = max(0, limit - filled)
        bar = a_color + "█" * filled + "\033[90m" + "░" * empty + "\033[0m"

        status = f"{a_color}{active} running\033[0m" if active else "\033[90midle\033[0m"

        queue = ""
        if waiting > 0:
            names = ", ".join(w["label"][:20] for w in snap["waiters"][:3])
            extra = f" +{waiting - 3}" if waiting > 3 else ""
            queue = f"  {w_color}⏳ {waiting} queued\033[0m: \033[90m{names}{extra}\033[0m"

        lines.append(f"  {icon} {label} [{bar}] {active}/{limit}   {status}{queue}")
    return lines



def _build_plan_lines() -> list[str]:
    """从执行计划获取紧凑展示行（直接委托给 plan_display）。"""
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
    """全局便捷函数：静默状态栏。"""
    if _bar:
        _bar.suppress()


def unsuppress_bar() -> None:
    """全局便捷函数：恢复状态栏。"""
    if _bar:
        _bar.unsuppress()
