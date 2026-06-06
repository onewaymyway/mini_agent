"""
orchestrator/status_bar.py

架构改进：不再用独立线程 push update+redraw 消息，
而是向 Terminal 注册一个内容提供者回调（_build_lines）。
Terminal 的 _refresh_loop 在每个刷新周期内调用该回调拉取内容，
然后自己决定是否重绘。

优势：
- _refresh_paused 一个标志即可彻底静止所有状态栏活动，
  消除了旧 push_loop 与 _enter_input_mode 之间的竞态。
- 减少一个后台线程（status_bar 不再需要 _push_loop 线程）。
- 内容构建逻辑与推送时机解耦，更易测试。
"""

from __future__ import annotations


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


def start_status_bar(**_kwargs) -> "StatusBar":
    """启动状态栏：向 Terminal 注册内容提供者回调。"""
    from mini_agent.ui.terminal import get_terminal
    get_terminal().set_statusbar_provider(_build_lines)
    return _bar_compat


def stop_status_bar() -> None:
    """停止状态栏：清除 Terminal 的内容提供者回调。"""
    from mini_agent.ui.terminal import get_terminal
    get_terminal().set_statusbar_provider(None)


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
