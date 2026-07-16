"""
cli/commands/recall.py — /recall slash 命令处理
（compact_mechanism_improvement_plan.md P2-B 的手动 CLI 入口）

/recall <query>            — 按关键词在当前 session 的 raw history（含已被
                              compact 掉的片段）里检索，最多返回 5 条
/recall --max N <query>    — 自定义返回条数（1~20）

与 agent 自己调用的 `recall_from_raw_history` 工具走同一套底层实现
（`tools/recall_history.py`），这里只是给用户一个不用等模型决定调不调用、
自己随时手动查的入口——和 `/notepad show` 之于 `notepad_*` 工具是同一种关系。
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_recall_cmd(args: list[str]) -> None:
    from mini_agent.tools.recall_history import (
        is_recall_history_enabled,
        recall_from_raw_history,
    )

    if not is_recall_history_enabled():
        R.print_info(
            "recall_from_raw_history is disabled (recall_history_enabled=false in config)."
        )
        return

    if not args:
        R.print_error("Usage: /recall <query> | /recall --max N <query>")
        return

    max_results = 5
    query_parts = list(args)
    if query_parts and query_parts[0] == "--max" and len(query_parts) >= 2:
        try:
            max_results = int(query_parts[1])
        except ValueError:
            R.print_error(f"Invalid --max value: {query_parts[1]!r}")
            return
        query_parts = query_parts[2:]

    query = " ".join(query_parts).strip()
    if not query:
        R.print_error("Usage: /recall <query> | /recall --max N <query>")
        return

    result = recall_from_raw_history(query, max_results=max_results)
    R.console.print(result, markup=False)
