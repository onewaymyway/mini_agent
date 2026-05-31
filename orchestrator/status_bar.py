"""
orchestrator/status_bar.py

【最终方案】单流主线程控制

核心原则：
  - 状态栏和所有输出都写同一个流：stdout
  - 没有后台刷新线程
  - 所有输出必须通过 printing_context() 上下文管理器，
    它负责"擦除状态栏 → 你的输出 → 重绘状态栏"三步
  - 状态栏只在"无输出的空闲期"保持可见；有输出时先让路
  - 等待用户输入时调用 pause()，resume() 在 agent 开始运行时调用

这样：stdout 上只有一个写者按顺序操作，完全没有竞态。
"""

from __future__ import annotations

import sys
import os
from contextlib import contextmanager
from typing import Optional

# ── 终端检测 ──────────────────────────────────────────────────────────────────

def _is_tty() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# ── 状态栏内容构建 ─────────────────────────────────────────────────────────────

def _build_status_lines() -> list[str]:
    lines: list[str] = []
    try:
        from orchestrator.concurrency import concurrency_snapshot
        snap = concurrency_snapshot()
        t, ll = snap["tasks"], snap["llm"]
        if t["active"] + ll["active"] + t["waiting"] + ll["waiting"] > 0:
            lines.extend(_build_concurrency_lines(t, ll))
    except Exception:
        pass
    try:
        from orchestrator.plan_display import build_plan_status_lines
        lines.extend(build_plan_status_lines())
    except Exception:
        pass
    return lines


def _build_concurrency_lines(t: dict, ll: dict) -> list[str]:
    lines = []
    for snap, icon, label, a_color, w_color in [
        (t,  "⚡", "Tasks", "\033[36m", "\033[33m"),
        (ll, "🤖", "LLM  ", "\033[34m", "\033[33m"),
    ]:
        active, waiting, limit = snap["active"], snap["waiting"], snap["limit"]
        bar = (a_color + "█" * min(active, limit)
               + "\033[90m" + "░" * max(0, limit - active) + "\033[0m")
        status = f"{a_color}{active} running\033[0m" if active else "\033[90midle\033[0m"
        queue = ""
        if waiting > 0:
            names = ", ".join(w["label"][:20] for w in snap["waiters"][:3])
            extra = f" +{waiting - 3}" if waiting > 3 else ""
            queue = f"  {w_color}⏳ {waiting} queued\033[0m: \033[90m{names}{extra}\033[0m"
        lines.append(f"  {icon} {label} [{bar}] {active}/{limit}   {status}{queue}")
    return lines


# ── 状态栏状态 ────────────────────────────────────────────────────────────────

class _State:
    last_lines: int = 0      # 当前屏幕上状态栏占的行数
    paused: bool = False     # True = 状态栏已擦除且不重绘（等待用户输入）


_st = _State()


def _draw() -> None:
    """把状态栏画到 stdout 当前位置。"""
    if not _is_tty() or _st.paused:
        return
    lines = _build_status_lines()
    if not lines:
        # 没有状态栏内容可显示，但保留 last_lines 计数器
        # 只在有内容时清除之前的状态栏
        return
    out = sys.stdout
    if _st.last_lines > 0:
        out.write(f"\x1b[{_st.last_lines}A\x1b[0J")
    for line in lines:
        out.write(line + "\n")
    out.flush()
    _st.last_lines = len(lines)


def _erase() -> None:
    """擦除屏幕上的状态栏。"""
    if not _is_tty():
        return
    if _st.last_lines > 0:
        sys.stdout.write(f"\x1b[{_st.last_lines}A\x1b[0J")
        sys.stdout.flush()
        _st.last_lines = 0


# ── 核心接口 ──────────────────────────────────────────────────────────────────

@contextmanager
def printing_context():
    """
    所有有输出的操作都要包在这个上下文里：
      with printing_context():
          console.print("hello")

    自动做：擦状态栏 → 你的输出 → 重绘状态栏
    """
    _erase()
    try:
        yield
    finally:
        _draw()


def pause() -> None:
    """
    准备等待用户输入前调用。
    擦除状态栏，阻止重绘，让用户提示符干净显示。
    """
    _erase()
    _st.paused = True


def resume() -> None:
    """
    用户输入完毕，agent 开始运行后调用。
    恢复状态栏重绘。
    """
    _st.paused = False
    _draw()


def redraw() -> None:
    """手动触发重绘（例如 task 状态变更后）。"""
    _draw()


# ── 兼容旧接口 ────────────────────────────────────────────────────────────────

class StatusBar:
    """兼容旧的 StatusBar 对象接口。"""
    def start(self): pass
    def stop(self): _erase()
    def pause(self): pause()
    def resume(self): resume()
    def suppress(self): pause()
    def unsuppress(self): resume()
    @property
    def suppressed(self): return _st.paused
    def __enter__(self): return self
    def __exit__(self, *_): _erase()


_bar_instance = StatusBar()


def start_status_bar(refresh_hz: int = 4) -> StatusBar:
    """兼容旧调用，返回 StatusBar 实例。"""
    return _bar_instance


def stop_status_bar() -> None:
    _erase()


def get_status_bar() -> StatusBar:
    return _bar_instance


def suppress_bar() -> None:
    pause()


def unsuppress_bar() -> None:
    resume()
