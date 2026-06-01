"""
orchestrator/plan_display.py — 执行计划 CLI 渲染

展示两种关系：
  父子关系（parent_id）→ 树形缩进
  依赖关系（depends_on）→ 箭头/标注

来源标注（source）：
  plan     → 无标注（默认）
  task     → 橙色 ← from:xxx
  user     → 紫色 [user]
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree
from rich import box

from orchestrator.plan import ExecutionPlan, PlanTask, PlanTaskStatus, TaskSource, get_plan

from terminal import term as _term
class _C:
    def print(self, *a, **kw): _term.print(*a, **kw)
console = _C()


# ── 完整树形展示（/plan 命令） ─────────────────────────────────────────────────

def print_plan_tree(plan: ExecutionPlan | None = None) -> None:
    p = plan or get_plan()
    if p is None:
        console.print("[dim]No active execution plan.[/dim]")
        return

    stats = p.stats()
    done, total = stats["done"], stats["total"]
    filled = int(8 * done / total) if total else 0
    bar = "█" * filled + "░" * (8 - filled)
    progress = f" [{bar}] {done}/{total}"

    stat_parts = []
    if stats["running"]:  stat_parts.append(f"[cyan]{stats['running']} running[/cyan]")
    if stats["failed"]:   stat_parts.append(f"[red]{stats['failed']} failed[/red]")
    if stats["skipped"]:  stat_parts.append(f"[yellow]{stats['skipped']} skipped[/yellow]")
    stat_str = ("  " + "  ".join(stat_parts)) if stat_parts else ""

    header = f"[bold]{p.goal}[/bold][dim]{progress}[/dim]{stat_str}"
    tree = Tree(header)

    for root in p.roots():
        _add_task_node(tree, root, p)

    console.print()
    console.print(Panel(tree, title="[bold blue]Execution Plan[/bold blue]",
                        border_style="blue", expand=False))

    # 图例
    console.print(
        "[dim]  Legend: "
        "○ pending  ◉ running  ✓ done  ✗ failed  — skipped  │  "
        "[/dim][orange3]← from:id[/orange3][dim] spawned by task  "
        "[/dim][magenta][user][/magenta][dim] added by user[/dim]"
    )

    running = [t for t in p.all_tasks() if t.status == PlanTaskStatus.RUNNING]
    if running:
        console.print(f"\n[cyan]▶ Currently:[/cyan] [{running[0].id}] {running[0].title}")
    next_t = p.next_ready()
    if next_t and not running:
        console.print(f"[dim]  Next:[/dim] [{next_t.id}] {next_t.title}")
    console.print()


def _add_task_node(parent_node, task: PlanTask, plan: ExecutionPlan) -> None:
    icon = task.status_icon()
    color = task.status_color()
    elapsed = f" [dim]{task.elapsed}s[/dim]" if task.elapsed else ""

    # 依赖标注（只在 pending 时显示，避免干扰完成后的展示）
    dep_str = ""
    if task.depends_on and task.status == PlanTaskStatus.PENDING:
        dep_str = f" [dim]→ after {', '.join(task.depends_on)}[/dim]"

    # 来源标注
    src_str = _source_markup(task)

    # 结果/错误摘要
    extra = ""
    if task.status == PlanTaskStatus.DONE and task.result:
        short = task.result[:55] + ("…" if len(task.result) > 55 else "")
        extra = f"\n  [dim]↳ {short}[/dim]"
    elif task.status == PlanTaskStatus.FAILED and task.error:
        extra = f"\n  [red]✗ {task.error[:55]}[/red]"

    label = (
        f"[{color}]{icon}[/{color}] "
        f"[[dim]{task.id}[/dim]] "
        f"[{color}]{task.title}[/{color}]"
        f"{elapsed}{dep_str}{src_str}{extra}"
    )

    node = parent_node.add(label)
    for child in plan.children_of(task.id):
        _add_task_node(node, child, plan)


def _source_markup(task: PlanTask) -> str:
    if task.source == TaskSource.USER:
        return "  [magenta][user][/magenta]"
    if task.source == TaskSource.TASK and task.created_by:
        return f"  [orange3]← from:{task.created_by}[/orange3]"
    return ""


# ── status_bar 集成行（紧凑格式，ANSI 原生） ──────────────────────────────────

def build_plan_status_lines() -> list[str]:
    """
    构建注入 status_bar 的紧凑展示行。
    同时体现父子关系（缩进）、依赖关系（after标注）、来源（颜色前缀）。
    """
    plan = get_plan()
    if plan is None:
        return []
    tasks = plan.all_tasks()
    if not tasks:
        return []

    stats = plan.stats()
    done, total = stats["done"], stats["total"]

    CYAN   = "\033[36m"
    GREEN  = "\033[32m"
    RED    = "\033[31m"
    YELLOW = "\033[33m"
    ORANGE = "\033[33m"   # 近似橙色（256色终端更佳）
    MAGENTA= "\033[35m"
    DIM    = "\033[90m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"

    # 进度条
    filled = int(10 * done / total) if total else 0
    bar = f"{GREEN}{'█' * filled}{DIM}{'░' * (10 - filled)}{RESET}"

    goal_short = plan.goal[:40] + ("…" if len(plan.goal) > 40 else "")
    stat_parts = [f"{GREEN}{done}/{total} done{RESET}"]
    if stats["running"]:  stat_parts.append(f"{CYAN}{stats['running']} running{RESET}")
    if stats["failed"]:   stat_parts.append(f"{RED}{stats['failed']} failed{RESET}")
    if stats["skipped"]:  stat_parts.append(f"{YELLOW}{stats['skipped']} skipped{RESET}")

    header = (
        f"  {BOLD}📋 Plan{RESET}  [{bar}]  "
        f"{DIM}{goal_short}{RESET}  "
        + "  ".join(stat_parts)
    )
    lines = [header]

    # 构建扁平显示列表（按树形顺序，缩进体现层级）
    display = _flatten_for_statusbar(plan)

    # 只显示活跃或最近的任务，最多 8 行
    visible = _pick_visible(display)

    for depth, task in visible:
        color, icon = _ansi_status(task)
        elapsed = f"{DIM} {task.elapsed}s{RESET}" if task.elapsed else ""

        # 依赖标注（只对 pending 且有依赖的显示）
        dep = ""
        if task.depends_on and task.status == PlanTaskStatus.PENDING:
            dep = f" {DIM}(after {', '.join(task.depends_on)}){RESET}"

        # 来源标注
        src = ""
        if task.source == TaskSource.USER:
            src = f" {MAGENTA}[user]{RESET}"
        elif task.source == TaskSource.TASK and task.created_by:
            src = f" {ORANGE}←{task.created_by}{RESET}"

        indent = "  " * depth
        # 树形连接符
        connector = "└─ " if depth > 0 else "   "

        line = (
            f"  {indent}{connector}"
            f"{color}{icon} [{task.id}]{RESET} "
            f"{task.title[:36]}"
            f"{elapsed}{dep}{src}"
        )
        lines.append(line)

    return lines


def _ansi_status(task: PlanTask) -> tuple[str, str]:
    CYAN  = "\033[36m"
    GREEN = "\033[32m"
    RED   = "\033[31m"
    YELLOW= "\033[33m"
    DIM   = "\033[90m"
    RESET = "\033[0m"
    table = {
        PlanTaskStatus.RUNNING:  (CYAN,                  "◉"),
        PlanTaskStatus.DONE:     (f"{GREEN}{DIM}",       "✓"),
        PlanTaskStatus.FAILED:   (RED,                   "✗"),
        PlanTaskStatus.SKIPPED:  (YELLOW,                "—"),
        PlanTaskStatus.PENDING:  (DIM,                   "○"),
    }
    return table.get(task.status, (DIM, "?"))


def _flatten_for_statusbar(plan: ExecutionPlan) -> list[tuple[int, PlanTask]]:
    """按树形顺序返回 (depth, task) 列表。"""
    result: list[tuple[int, PlanTask]] = []
    for root in plan.roots():
        _collect(root, plan, result, 0)
    return result


def _collect(task: PlanTask, plan: ExecutionPlan,
             out: list[tuple[int, PlanTask]], depth: int) -> None:
    out.append((depth, task))
    for child in plan.children_of(task.id):
        _collect(child, plan, out, depth + 1)


def _pick_visible(items: list[tuple[int, PlanTask]]) -> list[tuple[int, PlanTask]]:
    """优先显示 running > pending > 最后一个 done，最多 8 行。"""
    running  = [(d, t) for d, t in items if t.status == PlanTaskStatus.RUNNING]
    pending  = [(d, t) for d, t in items if t.status == PlanTaskStatus.PENDING]
    failed   = [(d, t) for d, t in items if t.status == PlanTaskStatus.FAILED]
    last_done= [(d, t) for d, t in items if t.status == PlanTaskStatus.DONE][-1:]

    visible = last_done + failed + running + pending
    # 去重（保持顺序）
    seen: set[str] = set()
    result = []
    for item in visible:
        if item[1].id not in seen:
            seen.add(item[1].id)
            result.append(item)
    return result[:8]


# ── 完成摘要表格 ───────────────────────────────────────────────────────────────

def print_plan_summary(plan: ExecutionPlan | None = None) -> None:
    p = plan or get_plan()
    if p is None:
        return

    table = Table(
        title=f"Plan complete: {p.goal[:60]}",
        box=box.SIMPLE,
        show_header=True,
        header_style="bold dim",
        expand=False,
    )
    table.add_column("ID",      style="dim",  width=8)
    table.add_column("Status",  width=12)
    table.add_column("Task",    min_width=20, max_width=36)
    table.add_column("Source",  width=12)
    table.add_column("Parent",  width=8,  style="dim")
    table.add_column("Deps",    width=12, style="dim")
    table.add_column("Time",    width=7,  justify="right")
    table.add_column("Result",  min_width=16, max_width=36)

    for task in p.all_tasks():
        source_str = task.source.value
        if task.created_by:
            source_str += f":{task.created_by}"
        result_str = (
            (task.result[:34] + "…" if len(task.result) > 34 else task.result)
            or (task.error[:34] if task.error else "—")
        )
        table.add_row(
            task.id,
            Text(f"{task.status_icon()} {task.status.value}", style=task.status_color()),
            task.title,
            Text(source_str, style="dim"),
            task.parent_id or "—",
            ", ".join(task.depends_on) or "—",
            f"{task.elapsed}s" if task.elapsed else "—",
            Text(result_str, style="dim"),
        )

    console.print()
    console.print(table)
