"""
cli/commands/agents.py — /agents slash 命令处理

/agents              — 列出所有自定义子 agent profile
/agents show <name>  — 显示某个 profile 的详细信息（system prompt 模板/工具限制等）
/agents reload       — 重新扫描 .agent/agents/ 和 ~/.agent/agents/
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_agents_cmd(args: list[str], agent=None) -> None:
    from mini_agent.orchestrator.agent_profiles import get_profile_loader, init_agent_profiles

    loader = get_profile_loader()
    if loader is None:
        R.print_error("Agent profiles not initialized.")
        return

    sub = args[0] if args else "list"

    if sub == "list":
        if not loader.available:
            R.print_info("No custom sub-agent profiles found "
                          "(place .md files under .agent/agents/ or ~/.agent/agents/)")
            return

        from rich.table import Table
        from rich import box as rbox

        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Name", style="cyan", min_width=16)
        t.add_column("Description", min_width=30, max_width=56)
        t.add_column("Model", min_width=10)
        t.add_column("Tools", min_width=10)

        for name in loader.available:
            p = loader.get(name)
            tools = ", ".join(p.tools) if p.tools else ("groups: " + ", ".join(p.tool_groups) if p.tool_groups else "all")
            t.add_row(name, p.description[:56], p.model or "(default)", tools)

        R.console.print("\n[bold]Custom Sub-Agent Profiles[/bold]")
        R.console.print(t)
        R.console.print()

    elif sub == "show" and len(args) >= 2:
        p = loader.get(args[1])
        if p is None:
            R.print_error(f"Unknown agent profile: {args[1]}")
            return
        R.console.print(f"\n[bold cyan]{p.name}[/bold cyan]  ({p.source_path})")
        R.console.print(f"[dim]{p.description}[/dim]\n")
        R.console.print(f"model: {p.model or '(default)'}")
        if p.tools:
            R.console.print(f"tools: {p.tools}")
        if p.tool_groups:
            R.console.print(f"tool_groups: {p.tool_groups}")
        if p.inputs:
            R.console.print("\ninputs:")
            for i in p.inputs:
                req = "required" if i.required else "optional"
                R.console.print(f"  - {i.name} ({i.type}, {req}): {i.description}")
        R.console.print("\n[bold]System prompt template:[/bold]")
        R.console.print(p.system_prompt)
        R.console.print()

    elif sub == "reload":
        if agent is not None:
            cfg = agent.cfg
        else:
            from mini_agent.config import AppConfig
            cfg = AppConfig()
        loader = init_agent_profiles(cfg)
        R.print_success(f"Reloaded. Available agent profiles: {loader.available}")

    else:
        R.print_error("Usage: /agents [list|show <name>|reload]")
