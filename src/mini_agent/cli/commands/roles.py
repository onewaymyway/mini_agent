"""
cli/commands/roles.py — /role slash 命令处理（角色扮演 Persona 系统）

/role list           — 列出已发现的角色（项目级 + 全局级，同名项目级优先）
/role use <name>     — 激活角色，写入 agent.active_persona
/role show <name>    — 预览角色渲染后的完整 prompt 片段（含强制安全边界声明），不激活
/role exit | off     — 清空 active_persona，回到默认人格
/role status         — 显示当前是否处于角色扮演及角色名
/role reload         — 重新扫描 .agent/personas/ 和 ~/.agent/personas/

详见 next_doc/roleplay_persona_design.md。
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_role_cmd(args: list[str], agent=None) -> None:
    from mini_agent.orchestrator.persona_profiles import (
        get_persona_loader, init_personas, render_persona_prompt,
    )

    loader = get_persona_loader()
    if loader is None:
        R.print_error("Persona system not initialized.")
        return

    sub = args[0] if args else "status"

    if sub == "list":
        if not loader.available:
            R.print_info("No personas found "
                          "(place .md files under .agent/personas/ or ~/.agent/personas/)")
            return

        from rich.table import Table
        from rich import box as rbox

        active = getattr(agent, "active_persona", None) if agent is not None else None

        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Name", style="cyan", min_width=16)
        t.add_column("Display Name", min_width=12)
        t.add_column("Description", min_width=30, max_width=56)
        t.add_column("Active", min_width=6)

        for name in loader.available:
            p = loader.get(name)
            mark = "✓" if name == active else ""
            t.add_row(name, p.display_name, p.description[:56], mark)

        R.console.print("\n[bold]Personas[/bold]")
        R.console.print(t)
        R.console.print()

    elif sub == "use" and len(args) >= 2:
        name = args[1]
        p = loader.get(name)
        if p is None:
            R.print_error(f"Unknown persona: {name}. Use /role list to see available personas.")
            return
        if agent is None:
            R.print_error("No active agent/session to apply persona to.")
            return
        agent.active_persona = name
        R.print_success(
            f"Persona activated: {p.display_name} ({name}). "
            f"Takes effect from the next turn. Use /role exit to leave the role."
        )

    elif sub == "show" and len(args) >= 2:
        p = loader.get(args[1])
        if p is None:
            R.print_error(f"Unknown persona: {args[1]}")
            return
        R.console.print(f"\n[bold cyan]{p.display_name}[/bold cyan]  ({p.source_path})")
        R.console.print(f"[dim]{p.description}[/dim]")
        if p.tone:
            R.console.print(f"[dim]tone: {p.tone}[/dim]")
        R.console.print(f"break_character_policy: {p.break_character_policy}")
        if p.allowed_tools:
            R.console.print(f"allowed_tools: {p.allowed_tools}")
        R.console.print()
        R.console.print("[bold]Rendered system prompt fragment:[/bold]")
        R.console.print(render_persona_prompt(p))
        R.console.print()

    elif sub in ("exit", "off"):
        if agent is None:
            R.print_error("No active agent/session to clear persona from.")
            return
        prev = getattr(agent, "active_persona", None)
        agent.active_persona = None
        if prev:
            R.print_success(f"Persona '{prev}' deactivated. Back to default assistant identity.")
        else:
            R.print_info("No persona was active.")

    elif sub == "status":
        active = getattr(agent, "active_persona", None) if agent is not None else None
        if active:
            p = loader.get(active)
            label = p.display_name if p else active
            R.print_info(f"Active persona: {label} ({active})")
        else:
            R.print_info("No persona active (default assistant identity).")

    elif sub == "reload":
        if agent is not None:
            cfg = agent.cfg
        else:
            from mini_agent.config import AppConfig
            cfg = AppConfig()
        loader = init_personas(cfg)
        R.print_success(f"Reloaded. Available personas: {loader.available}")

    else:
        R.print_error("Usage: /role [list|use <name>|show <name>|exit|status|reload]")
