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
        from mini_agent.tools.orchestration import get_task_manager
        mgr = get_task_manager()

        if mgr:
            # 使用 TaskManager 的真实任务状态
            stats = mgr.stats()
            running = stats["running"]
            pending = stats["pending"]
            done = stats["done"]
            failed = stats["failed"]
            cancelled = stats["cancelled"]
            total = stats["total"]

            max_workers = mgr.max_workers

            # Tasks 行
            active_bar = "\033[36m" + "█" * min(running, max_workers) + "\033[90m" + "░" * max(0, max_workers - running) + "\033[0m"
            task_status = f"\033[36m{running} running\033[0m" if running else "\033[90midle\033[0m"

            queue_str = ""
            if pending > 0:
                # 获取排队中的任务名称
                pending_records = mgr.list_records(status=None)
                pending_tasks = [r for r in pending_records if r.status.name == "PENDING"][:3]
                names = ", ".join(r.task.name[:20] for r in pending_tasks)
                extra = f" +{pending-3}" if pending > 3 else ""
                queue_str = f"  \033[33m⏳ {pending} queued\033[0m: \033[90m{names}{extra}\033[0m"

            lines.append(f"  ⚡ Tasks [{active_bar}] {running}/{max_workers}   {task_status}{queue_str}")

            # 如果有排队任务，显示详细信息
            if pending > 0:
                lines.append("")
                lines.append("  [Queue details]")
                pending_records = mgr.list_records(status=None)
                pending_tasks = [r for r in pending_records if r.status.name == "PENDING"]
                for i, r in enumerate(pending_tasks[:5], 1):
                    lines.append(f"    {i}. {r.task.name[:50]}")
                if len(pending_tasks) > 5:
                    lines.append(f"    ... and {len(pending_tasks)-5} more")
        else:
            # TaskManager 未初始化时显示空闲
            lines.append("  ⚡ Tasks [\033[90m░░░░\033[0m] 0/4   \033[90midle\033[0m")
    except Exception as e:
        # 出错时显示简单状态
        lines.append(f"  \033[90m⚡ Tasks: error getting status\033[0m")

    try:
        from .concurrency import concurrency_snapshot
        snap = concurrency_snapshot()
        ll = snap["llm"]
        active, waiting, limit = ll["active"], ll["waiting"], ll["limit"]
        if active > 0 or waiting > 0:
            bar = "\033[34m" + "█" * min(active, limit) + "\033[90m" + "░" * max(0, limit - active) + "\033[0m"
            status = f"\033[34m{active} running\033[0m" if active else "\033[90midle\033[0m"
            queue_str = ""
            if waiting > 0:
                names = ", ".join(w["label"][:20] for w in ll["waiters"][:3])
                extra = f" +{waiting-3}" if waiting > 3 else ""
                queue_str = f"  \033[33m⏳ {waiting} queued\033[0m: \033[90m{names}{extra}\033[0m"
            lines.append(f"  🤖 LLM   [{bar}] {active}/{limit}   {status}{queue_str}")
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
