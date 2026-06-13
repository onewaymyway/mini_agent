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

Task Tab 扩展（路径 B）：
- 底部增加 task tab 栏，每个 task 显示状态点 + 名称 + 耗时
- 当前焦点 task 高亮（下划线 + 亮色）
- 支持键盘 Alt+← / Alt+→ 切换（在 _build_ptk_keybindings 中注册）
- 按 Esc 退出焦点，回到主输出视图
"""

from __future__ import annotations

_FOCUS_HELP = (
    "\033[90m  ← → 切换 task  "
    "ESC 退出焦点\033[0m"
)
_MAIN_HELP = (
    "\033[90m  ← → 进入/切换焦点  "
    "/tasks focus <id> 指定 task\033[0m"
)


def _task_status_icon(status_name: str) -> str:
    return {
        "RUNNING":   "\033[36m●\033[0m",   # 青色实心点
        "PENDING":   "\033[33m○\033[0m",   # 黄色空心点
        "DONE":      "\033[32m✓\033[0m",   # 绿色勾
        "FAILED":    "\033[31m✗\033[0m",   # 红色叉
        "CANCELLED": "\033[90m–\033[0m",   # 灰色短横
    }.get(status_name, "?")


def _build_task_tab_line(records, focus_id: str | None) -> str:
    """构建单行 task tab 条（ANSI 着色）。"""
    if not records:
        return ""

    parts: list[str] = []
    for rec in records:
        icon = _task_status_icon(rec.status.name)
        elapsed = f" {rec.elapsed}s" if rec.elapsed is not None else ""
        name = rec.task.name[:18]
        if len(rec.task.name) > 18:
            name += "…"

        tab_text = f"{icon} {name}{elapsed}"

        if rec.task_id == focus_id:
            # 当前焦点：亮色 + 下划线
            parts.append(f"\033[4;97m {tab_text} \033[0m")
        else:
            # 非焦点：暗色
            parts.append(f"\033[90m {tab_text} \033[0m")

    sep = "\033[90m│\033[0m"
    return "  " + sep.join(parts)


def _build_lines() -> list[str]:
    lines: list[str] = []
    try:
        from mini_agent.tools.orchestration import get_task_manager
        from mini_agent.ui.terminal import get_terminal
        mgr = get_task_manager()
        terminal = get_terminal()
        focus_id = terminal.get_task_focus()

        if mgr:
            stats = mgr.stats()
            running   = stats["running"]
            pending   = stats["pending"]
            done      = stats["done"]
            failed    = stats["failed"]
            cancelled = stats["cancelled"]
            total     = stats["total"]
            max_workers = mgr.max_workers

            # ── 行1：并发概况条（与原来相同） ────────────────────────
            active_bar = (
                "\033[36m" + "█" * min(running, max_workers)
                + "\033[90m" + "░" * max(0, max_workers - running)
                + "\033[0m"
            )
            task_status = (
                f"\033[36m{running} running\033[0m"
                if running else "\033[90midle\033[0m"
            )

            queue_str = ""
            if pending > 0:
                pending_records = mgr.list_records(status=None)
                pending_tasks = [
                    r for r in pending_records if r.status.name == "PENDING"
                ][:3]
                names = ", ".join(r.task.name[:20] for r in pending_tasks)
                extra = f" +{pending-3}" if pending > 3 else ""
                queue_str = (
                    f"  \033[33m⏳ {pending} queued\033[0m: "
                    f"\033[90m{names}{extra}\033[0m"
                )

            lines.append(
                f"  ⚡ Tasks [{active_bar}] {running}/{max_workers}"
                f"   {task_status}{queue_str}"
            )

            # ── 行2：task tab 条（新增） ──────────────────────────────
            all_records = mgr.list_records()
            if all_records:
                tab_line = _build_task_tab_line(all_records, focus_id)
                if tab_line:
                    lines.append(tab_line)

                # ── 行3：操作提示（焦点/非焦点不同文字） ────────────
                if focus_id:
                    lines.append(_FOCUS_HELP)
                elif total > 0:
                    lines.append(_MAIN_HELP)

        else:
            lines.append(
                "  ⚡ Tasks [\033[90m░░░░\033[0m] 0/4   \033[90midle\033[0m"
            )
    except Exception:
        lines.append("  \033[90m⚡ Tasks: error getting status\033[0m")

    try:
        from .concurrency import concurrency_snapshot
        snap = concurrency_snapshot()
        ll = snap["llm"]
        active, waiting, limit = ll["active"], ll["waiting"], ll["limit"]
        if active > 0 or waiting > 0:
            bar = (
                "\033[34m" + "█" * min(active, limit)
                + "\033[90m" + "░" * max(0, limit - active)
                + "\033[0m"
            )
            status = (
                f"\033[34m{active} running\033[0m"
                if active else "\033[90midle\033[0m"
            )
            queue_str = ""
            if waiting > 0:
                names = ", \033[90m".join(
                    w["label"][:20] for w in ll["waiters"][:3]
                )
                extra = f" +{waiting-3}" if waiting > 3 else ""
                queue_str = (
                    f"  \033[33m⏳ {waiting} queued\033[0m: "
                    f"\033[90m{names}{extra}\033[0m"
                )
            lines.append(
                f"  🤖 LLM   [{bar}] {active}/{limit}   {status}{queue_str}"
            )
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