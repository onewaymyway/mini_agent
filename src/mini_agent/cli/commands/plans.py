"""
cli/commands/plans.py — /plan slash 命令处理

/plan              — 显示当前执行计划（树形）
/plan clear        — 清除当前计划
/plan summary      — 打印完成摘要表格
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_plan_cmd(args: list[str]) -> None:
    from mini_agent.orchestrator.plan import get_plan, clear_plan
    from mini_agent.orchestrator.plan_display import print_plan_tree, print_plan_summary

    if not args or args[0] == "show":
        plan = get_plan()
        if plan is None:
            R.print_info("No active execution plan. The agent will create one when needed.")
        else:
            print_plan_tree(plan)

    elif args[0] == "clear":
        clear_plan()
        R.print_success("Execution plan cleared.")

    elif args[0] == "summary":
        plan = get_plan()
        if plan is None:
            R.print_info("No plan to summarize.")
        else:
            print_plan_summary(plan)

    else:
        R.print_error("Usage: /plan | /plan clear | /plan summary")
