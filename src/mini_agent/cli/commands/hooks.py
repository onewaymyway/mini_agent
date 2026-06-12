"""
cli/commands/hooks.py — /hooks slash 命令处理

/hooks            — 列出已加载的 hooks（按事件分组）
/hooks reload     — 重新加载 .agent/hooks.json (project) 和 ~/.agent/hooks.json (global)
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_hooks_cmd(args: list[str], agent=None) -> None:
    from mini_agent.hooks import get_hook_manager, init_hooks
    from mini_agent.hooks.loader import KNOWN_EVENTS

    sub = args[0] if args else "list"

    if sub == "reload":
        project_root = agent.cfg.project_root if agent is not None else None
        mgr = init_hooks(project_root)
        R.print_success("Hooks reloaded.")
        sub = "list"
        target = mgr
    else:
        target = get_hook_manager()

    if sub == "list":
        if target is None or not target.has_any:
            R.print_info("No hooks configured "
                          "(create .agent/hooks.json or ~/.agent/hooks.json)")
            return

        from rich.table import Table
        from rich import box as rbox

        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Event", style="cyan", min_width=16)
        t.add_column("Matcher", min_width=10)
        t.add_column("Command", min_width=24, max_width=50)
        t.add_column("Source", min_width=8)

        any_rows = False
        for event in KNOWN_EVENTS:
            for spec in target._all_specs(event):
                t.add_row(event, spec.matcher, spec.command[:50], spec.source)
                any_rows = True

        if not any_rows:
            R.print_info("No hooks configured.")
            return

        R.console.print("\n[bold]Configured Hooks[/bold]")
        R.console.print(t)
        R.console.print()
    else:
        R.print_error("Usage: /hooks [list|reload]")
