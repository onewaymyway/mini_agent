"""
cli/commands/concurrency.py — /concurrency slash 命令处理

/concurrency           — 显示当前并发状态
/concurrency tasks <n> — 设置最大并发任务数
/concurrency llm <n>   — 设置最大并发 LLM 调用数
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_concurrency_cmd(args: list[str]) -> None:
    from mini_agent.orchestrator.concurrency import concurrency_snapshot, set_max_tasks, set_max_llm_calls
    snap = concurrency_snapshot()

    if not args or args[0] == "status":
        t = snap["tasks"]
        l = snap["llm"]
        R.console.print("\n[bold]Concurrency status:[/bold]")
        R.console.print(
            f"  Tasks  : [cyan]{t['active']} running[/cyan] / "
            f"{t['limit']} max  "
            f"({t['waiting']} queued)"
        )
        R.console.print(
            f"  LLM    : [blue]{l['active']} active[/blue] / "
            f"{l['limit']} max  "
            f"({l['waiting']} queued)"
        )
        if t["waiters"]:
            R.console.print("  Queued tasks: " + ", ".join(
                f"[dim]{w['label']} ({w['waited_s']}s)[/dim]"
                for w in t["waiters"]
            ))
        if l["waiters"]:
            R.console.print("  Queued LLM : " + ", ".join(
                f"[dim]{w['label']} ({w['waited_s']}s)[/dim]"
                for w in l["waiters"]
            ))

    elif args[0] == "tasks" and len(args) >= 2:
        try:
            n = int(args[1])
            set_max_tasks(n)
            from mini_agent.tools.orchestration import get_task_manager
            mgr = get_task_manager()
            if mgr:
                mgr.max_workers = n
            R.print_success(f"Max concurrent tasks → {n}")
        except ValueError:
            R.print_error("Usage: /concurrency tasks <number>")

    elif args[0] == "llm" and len(args) >= 2:
        try:
            n = int(args[1])
            set_max_llm_calls(n)
            R.print_success(f"Max concurrent LLM calls → {n}")
        except ValueError:
            R.print_error("Usage: /concurrency llm <number>")

    else:
        R.print_error("Usage: /concurrency | /concurrency tasks <n> | /concurrency llm <n>")
