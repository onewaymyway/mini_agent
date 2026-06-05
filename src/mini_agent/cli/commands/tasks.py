"""
cli/commands/tasks.py — /tasks slash 命令处理

/tasks                 — 显示所有任务表格
/tasks dashboard       — 实时 dashboard 直到所有任务完成
/tasks log <id>        — 显示任务日志
/tasks cancel <id>     — 取消指定任务
/tasks cancel-all      — 取消所有 pending/running 任务
/tasks workers <n>     — 修改 max_workers
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_tasks_cmd(args: list[str], agent) -> None:
    from mini_agent.tools.orchestration import get_task_manager
    from mini_agent.orchestrator.task_display import print_task_table, print_task_log, TaskDashboard

    mgr = get_task_manager()
    if mgr is None:
        R.print_error("Task manager not running.")
        return

    if not args or args[0] == "list":
        records = mgr.list_records()
        print_task_table(records)

    elif args[0] == "dashboard":
        dash = TaskDashboard(mgr)
        try:
            dash.run_until_done()
        except KeyboardInterrupt:
            R.print_interrupt()

    elif args[0] == "log" and len(args) >= 2:
        rec = mgr.get(args[1])
        if rec:
            print_task_log(rec)
        else:
            R.print_error(f"Task '{args[1]}' not found.")

    elif args[0] == "cancel" and len(args) >= 2:
        ok = mgr.cancel(args[1])
        if ok:
            R.print_success(f"Cancelled task {args[1]}")
        else:
            R.print_error(f"Could not cancel {args[1]} (already terminal or not found).")

    elif args[0] == "cancel-all":
        n = mgr.cancel_all()
        R.print_success(f"Cancelled {n} task(s).")

    elif args[0] == "workers" and len(args) >= 2:
        try:
            n = int(args[1])
            mgr.max_workers = n
            R.print_success(f"Max workers set to {n}.")
        except ValueError:
            R.print_error("Usage: /tasks workers <number>")

    else:
        R.print_error(
            "Usage: /tasks | /tasks dashboard | /tasks log <id> | "
            "/tasks cancel <id> | /tasks cancel-all | /tasks workers <n>"
        )
