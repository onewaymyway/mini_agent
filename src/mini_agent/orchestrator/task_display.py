"""
orchestrator/task_display.py — 任务监控终端 UI

提供两种显示模式：
  1. 实时 Live 看板（Rich Live）— 并发运行时的动态刷新面板
  2. 摘要表格（Summary）     — 打印当前所有任务状态
  3. 日志视图（Logs）         — 打印单个任务的详细日志
"""

from __future__ import annotations

import time
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress, SpinnerColumn, TextColumn, TimeElapsedColumn, BarColumn,
)
from rich.table import Table
from rich.text import Text
from rich import box

from .task import TaskRecord, TaskStatus

from mini_agent.ui.terminal import term as _term
class _C:
    def print(self, *a, **kw): _term.print(*a, **kw)
console = _C()


# ── 摘要表格 ──────────────────────────────────────────────────────────────────

def print_task_table(records: list[TaskRecord], title: str = "Tasks") -> None:
    """打印所有任务的状态摘要表格。"""
    if not records:
        console.print("[dim]No tasks.[/dim]")
        return

    table = Table(
        title=title,
        box=box.ROUNDED,
        show_header=True,
        header_style="bold",
        expand=False,
        min_width=70,
    )
    table.add_column("ID",       style="dim",  width=9)
    table.add_column("Status",   width=11)
    table.add_column("Name",     min_width=24, max_width=40)
    table.add_column("Elapsed",  width=8,  justify="right")
    table.add_column("Tokens",   width=12, justify="right")
    table.add_column("Tools",    width=6,  justify="right")

    for rec in records:
        icon  = rec.status_icon()
        color = rec.status_color()
        elapsed = f"{rec.elapsed}s" if rec.elapsed is not None else "—"

        tokens = "—"
        tools  = "—"
        if rec.result:
            t_in  = rec.result.input_tokens
            t_out = rec.result.output_tokens
            tokens = f"{t_in}/{t_out}"
            tools  = str(rec.result.tool_calls)

        table.add_row(
            rec.task_id,
            Text(f"{icon} {rec.status.value}", style=color),
            Text(rec.task.name, overflow="ellipsis"),
            elapsed,
            tokens,
            tools,
        )

    console.print(table)


# ── 单任务日志 ────────────────────────────────────────────────────────────────

def print_task_log(rec: TaskRecord, max_lines: int = 50) -> None:
    """打印单个任务的详细日志。"""
    icon  = rec.status_icon()
    color = rec.status_color()

    header = f"{icon} [{color}]{rec.status.value.upper()}[/{color}]  "
    header += f"[bold]{rec.task.name}[/bold]  [dim]({rec.task_id})[/dim]"
    if rec.elapsed is not None:
        header += f"  [dim]{rec.elapsed}s[/dim]"

    console.print(header)

    if rec.result and rec.result.error:
        console.print(f"[red]Error:[/red] {rec.result.error}")

    lines = rec.log_lines[-max_lines:]
    if lines:
        console.print(Panel(
            "\n".join(f"[dim]{l}[/dim]" for l in lines),
            title="[dim]Log[/dim]",
            border_style="dim",
            expand=False,
        ))

    if rec.result and rec.result.output:
        console.print(Panel(
            rec.result.output[:2000] + ("…" if len(rec.result.output) > 2000 else ""),
            title="[dim]Output[/dim]",
            border_style="green" if rec.result.success else "red",
            expand=False,
        ))


# ── 实时看板 ──────────────────────────────────────────────────────────────────

class TaskDashboard:
    """
    使用 Rich Live 实时刷新的任务监控看板。

    使用方式：
        dashboard = TaskDashboard(task_manager)
        dashboard.run_until_done()   # 阻塞，直到所有任务完成
    """

    def __init__(
        self,
        manager,                        # TaskManager（避免循环导入）
        refresh_per_second: int = 4,
        show_logs_n: int = 3,           # 每个任务显示最后 N 行日志
    ) -> None:
        self._mgr = manager
        self._refresh = refresh_per_second
        self._show_logs = show_logs_n

    def run_until_done(self, timeout: Optional[float] = None) -> None:
        """阻塞并实时刷新，直到所有任务终态或超时。"""
        deadline = time.time() + timeout if timeout else None

        with Live(
            self._render(),
            refresh_per_second=self._refresh,
            console=console,
        ) as live:
            while True:
                live.update(self._render())
                stats = self._mgr.stats()
                active = stats["pending"] + stats["running"]
                if active == 0:
                    break
                if deadline and time.time() > deadline:
                    break
                time.sleep(1 / self._refresh)

        # 最终打印完整表格
        self._print_final_summary()

    def _render(self):
        """构建当前帧的 Rich Renderable。"""
        stats = self._mgr.stats()
        records = self._mgr.list_records()

        # 顶部统计行
        stat_parts = [
            f"[bold]Tasks:[/bold] {stats['total']}",
            f"[cyan]Running: {stats['running']}[/cyan]",
            f"[dim]Pending: {stats['pending']}[/dim]",
            f"[green]Done: {stats['done']}[/green]",
        ]
        if stats["failed"]:
            stat_parts.append(f"[red]Failed: {stats['failed']}[/red]")
        if stats["cancelled"]:
            stat_parts.append(f"[yellow]Cancelled: {stats['cancelled']}[/yellow]")

        header = "  ".join(stat_parts)

        # 任务行
        task_lines: list[str] = []
        for rec in records:
            icon  = rec.status_icon()
            color = rec.status_color()
            elapsed = f" {rec.elapsed}s" if rec.elapsed is not None else ""
            line = (
                f"  [{color}]{icon} {rec.task_id}[/{color}]"
                f"  {rec.task.name[:42]}{elapsed}"
            )
            task_lines.append(line)

            # 运行中任务显示最后几行日志
            if rec.status == TaskStatus.RUNNING and self._show_logs > 0:
                for log_line in rec.log_lines[-self._show_logs:]:
                    short = log_line[:78]
                    task_lines.append(f"      [dim]{short}[/dim]")

        body = "\n".join(task_lines) if task_lines else "  [dim](no tasks)[/dim]"

        return Panel(
            header + "\n\n" + body,
            title="[bold blue]Task Dashboard[/bold blue]",
            border_style="blue",
            expand=True,
        )

    def _print_final_summary(self) -> None:
        records = self._mgr.list_records()
        console.print()
        print_task_table(records, title="Completed")


# ── 进度条（单次多任务） ──────────────────────────────────────────────────────

def make_progress_bar() -> Progress:
    """创建一个适合任务跟踪的 Progress 实例。"""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(bar_width=None),
        TimeElapsedColumn(),
        console=console,
        expand=True,
    )
