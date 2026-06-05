"""
cli/commands/skills.py — /skills 和 /skill slash 命令处理
"""

from __future__ import annotations

import time

from mini_agent.skills import SkillLoader
from mini_agent.prompts import pm
import mini_agent.ui.renderer as R


def handle_skills_list(skill_loader: SkillLoader) -> None:
    """
    /skills — 显示所有可用技能的完整列表，含激活状态和 token 估算。
    """
    from rich.table import Table
    from rich import box as rbox

    catalog = skill_loader.get_catalog()
    if not catalog:
        R.console.print(f"[dim]{pm.fragment('cli_messages', 'NO_SKILLS_FOUND')}[/dim]")
        return

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("",        width=3)
    t.add_column("Name",    style="cyan",  min_width=18)
    t.add_column("Description", min_width=36, max_width=56)
    t.add_column("~Tokens", width=8, justify="right")

    for s in catalog:
        skill_obj = skill_loader.get(s["name"])
        token_est = len(skill_obj.content) // 4 if skill_obj else 0
        marker = "[green]✓[/green]" if s["active"] else " "
        name_style = f"[bold]{s['name']}[/bold]" if s["active"] else s["name"]
        t.add_row(
            marker,
            name_style,
            s["description"][:56],
            str(token_est),
        )

    active_count   = sum(1 for s in catalog if s["active"])
    inactive_count = len(catalog) - active_count
    total_tokens   = sum(
        len(skill_loader.get(s["name"]).content) // 4
        for s in catalog if s["active"] and skill_loader.get(s["name"])
    )

    R.console.print(f"\n[bold]{pm.fragment('cli_messages', 'SKILLS_LIST_HEADER')}[/bold]")
    R.console.print(t)
    R.console.print(
        f"  [dim]{active_count} active / {inactive_count} inactive  "
        f"· active skills consuming ~{total_tokens} tokens[/dim]\n"
    )


def handle_skill_cmd(args: list[str], skill_loader: SkillLoader) -> None:
    """
    /skill on  <name> [name2 ...]  — 激活一个或多个技能
    /skill off <name> [name2 ...]  — 卸载一个或多个技能
    /skill info <name>             — 显示技能全文内容
    /skill stats                   — 显示 LRU 调用统计
    /skill reset                   — 卸载所有当前激活的技能
    """
    if not args:
        R.print_error(pm.fragment("cli_messages", "SKILL_CMD_USAGE"))
        return

    action = args[0].lower()
    names  = args[1:]

    if action == "on":
        if not names:
            R.print_error("Usage: /skill on <name> [name2 ...]")
            return
        ok_list, bad_list, dup_list = [], [], []
        for n in names:
            if n not in skill_loader.available:
                bad_list.append(n)
            elif not skill_loader.activate(n):
                dup_list.append(n)
            else:
                ok_list.append(n)
                R.print_success(pm.fragment("cli_messages", "SKILL_ACTIVATED", name=n))
        for n in dup_list:
            R.print_info(pm.fragment("cli_messages", "SKILL_ALREADY_ACTIVE", name=n))
        for n in bad_list:
            _suggest_skill(n, skill_loader)

    elif action == "off":
        if not names:
            R.print_error("Usage: /skill off <name> [name2 ...]")
            return
        ok_list, bad_list, idle_list = [], [], []
        for n in names:
            if n not in skill_loader.available:
                bad_list.append(n)
            elif not skill_loader.deactivate(n):
                idle_list.append(n)
            else:
                ok_list.append(n)
                R.print_success(pm.fragment("cli_messages", "SKILL_DEACTIVATED", name=n))
        for n in idle_list:
            R.print_info(pm.fragment("cli_messages", "SKILL_NOT_ACTIVE", name=n))
        for n in bad_list:
            _suggest_skill(n, skill_loader)

    elif action == "stats":
        _handle_skill_stats(skill_loader)

    elif action == "info":
        if not names:
            R.print_error("Usage: /skill info <name>")
            return
        skill_name = names[0]
        skill_obj  = skill_loader.get(skill_name)
        if skill_obj is None:
            _suggest_skill(skill_name, skill_loader)
            return
        status = "[green]active[/green]" if skill_name in skill_loader.active else "[dim]inactive[/dim]"
        token_est = len(skill_obj.content) // 4
        R.console.print(f"\n[bold cyan]{skill_obj.name}[/bold cyan]  {status}  [dim]~{token_est} tokens[/dim]")
        R.console.print(f"[dim]Location: {skill_obj.location}[/dim]")
        R.console.print(f"Description: {skill_obj.description}\n")
        R.print_markdown(skill_obj.content)

    elif action == "reset":
        active_now = list(skill_loader.active)
        if not active_now:
            R.print_info("No active skills to reset.")
            return
        for n in active_now:
            skill_loader.deactivate(n)
            R.print_success(pm.fragment("cli_messages", "SKILL_DEACTIVATED", name=n))
        R.print_info(f"All {len(active_now)} skill(s) deactivated.")

    else:
        R.print_error(pm.fragment("cli_messages", "SKILL_CMD_USAGE"))


def _handle_skill_stats(skill_loader: SkillLoader) -> None:
    """
    /skill stats — 显示 skill 调用追踪：LRU 排序、调用次数、上次使用时间、budget 设置。
    """
    from rich.table import Table
    from rich import box as rbox

    tracker    = skill_loader.tracker
    records    = tracker.records
    active_set = set(skill_loader.active)
    tracked_set = {r.name for r in records}

    R.console.print("\n[bold]Skill Usage Tracking[/bold]")
    R.console.print(
        f"  Budget: [cyan]{tracker.total_budget:,}[/cyan] tokens total  "
        f"/ [cyan]{tracker.per_skill_tokens:,}[/cyan] per skill\n"
    )

    all_names = list(dict.fromkeys(
        [r.name for r in records] + list(active_set)
    ))

    if not all_names:
        R.console.print("  [dim](no skills activated yet)[/dim]\n")
        return

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("LRU",       width=4,  justify="right")
    t.add_column("Skill",     style="cyan", min_width=18)
    t.add_column("Status",    width=14)
    t.add_column("Used",      width=5,  justify="right")
    t.add_column("Last used", width=10)
    t.add_column("~Tokens",   width=8,  justify="right")

    for rank, name in enumerate(all_names, 1):
        rec       = tracker.get_record(name)
        skill_obj = skill_loader.get(name)
        tok_est   = len(skill_obj.content) // 4 if skill_obj else 0
        is_active = name in active_set
        was_used  = name in tracked_set

        if is_active and was_used:
            status = "[green]active+used[/green]"
        elif is_active and not was_used:
            status = "[yellow]active/unused[/yellow]"
        elif not is_active and was_used:
            status = "[dim]inactive/used[/dim]"
        else:
            status = "[dim]inactive[/dim]"

        calls_str = str(rec.call_count) if rec else "-"
        ts        = time.strftime("%H:%M:%S", time.localtime(rec.last_called)) if rec else "-"
        rank_str  = f"[bold cyan]{rank}[/bold cyan]" if (rec and rank <= 3) else (str(rank) if rec else "-")

        t.add_row(rank_str, name, status, calls_str, ts, str(tok_est))

    R.console.print(t)
    R.console.print(
        "  [dim]LRU rank = priority during compression. "
        "'active/unused' = loaded but no evidence of actual use this session.[/dim]"
    )

    _, included, dropped = skill_loader.build_compact_context(include_inactive=True)
    R.console.print("\n[dim]  If compacted now:[/dim]")
    if included:
        R.console.print(f"  [green]✓ Would include:[/green] {', '.join(included)}")
    if dropped:
        R.console.print(f"  [red]✗ Would drop (budget full):[/red] {', '.join(dropped)}")
    if not included and not dropped:
        R.console.print("  [dim](nothing to compact)[/dim]")
    R.console.print()


def _suggest_skill(name: str, skill_loader: SkillLoader) -> None:
    """打印'找不到'错误，并给出相近名称提示。"""
    R.print_error(pm.fragment("cli_messages", "SKILL_NOT_FOUND", name=name))
    candidates = [
        a for a in skill_loader.available
        if a.startswith(name) or name in a or a.startswith(name[:3])
    ]
    if candidates:
        R.print_info(f"  Did you mean: {', '.join(candidates)}?")
