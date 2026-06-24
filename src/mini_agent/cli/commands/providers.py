"""
cli/commands/providers.py — /provider slash 命令处理

/provider                        — 显示当前 provider 信息
/provider list                   — 列出所有注册的 providers
/provider models                 — 列出 fallback chain 中所有已配置的模型，标记当前正在使用的
/provider switch <name> [model]  — 运行时切换 provider
"""

from __future__ import annotations

import os

from mini_agent.agent import Agent
import mini_agent.ui.renderer as R


def handle_provider_cmd(args: list[str], agent: Agent) -> None:
    from mini_agent.llm import list_providers, LLMConfig, create_client

    if not args or args[0] == "info":
        R.print_info(f"Current LLM: {agent.llm_client}")

    elif args[0] == "list":
        R.console.print("\n[bold]Registered providers:[/bold]")
        for p in list_providers():
            R.console.print(f"  [cyan]{p}[/cyan]")

    elif args[0] == "models":
        _handle_models(agent)

    elif args[0] == "switch" and len(args) >= 2:
        provider = args[1]
        model    = args[2] if len(args) >= 3 else agent.cfg.model
        if provider in ("anthropic", "claude"):
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        elif provider in ("openai", "azure"):
            api_key = os.environ.get("OPENAI_API_KEY", "")
        else:
            api_key = ""
        cfg = LLMConfig(
            provider=provider, model=model, api_key=api_key,
            requires_api_key=(provider not in ("ollama", "local")),
        )
        try:
            agent.switch_provider(cfg)
            R.print_success(f"Switched to {provider} / {model}")
        except Exception as e:
            R.print_error(str(e))

    else:
        R.print_error("Usage: /provider | /provider list | /provider models | /provider switch <name> [model]")


def _handle_models(agent: Agent) -> None:
    """
    /provider models — 列出 fallback chain 中所有已配置的模型。

    信息来源：LLMClientPool.snapshot()，无需重复读取配置文件。
    当前正在使用的条目用 ● 标记；其余条目用 ○ 标记。
    若某条目配置了多 key 轮转，额外显示 key 数量。
    """
    from rich.table import Table
    from rich import box as rbox

    pool = agent._client_pool
    snap = pool.snapshot()
    entries = snap["entries"]
    current_idx = snap["current"]

    # 同时拿到实际 entry 对象，用于读取 key_pool 的 key 数量
    raw_entries = pool._entries

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("",          width=3)
    t.add_column("#",         width=3, justify="right")
    t.add_column("Provider",  style="cyan", min_width=12)
    t.add_column("Model",     min_width=28)
    t.add_column("Keys",      width=6, justify="right")
    t.add_column("Rotation",  width=12)

    for i, (info, raw) in enumerate(zip(entries, raw_entries)):
        is_current = (i == current_idx)
        marker     = "[green]●[/green]" if is_current else "[dim]○[/dim]"
        idx_str    = f"[bold]{i + 1}[/bold]" if is_current else str(i + 1)

        provider, _, model = info["label"].partition("/")
        provider_str = f"[bold]{provider}[/bold]" if is_current else provider
        model_str    = f"[bold green]{model}[/bold green]" if is_current else model

        kp = raw.key_pool
        if kp is not None:
            key_count  = str(len(kp._states))
            rotation   = kp._rotation
        else:
            key_count  = "1"
            rotation   = "—"

        t.add_row(marker, idx_str, provider_str, model_str, key_count, rotation)

    current_label = entries[current_idx]["label"] if entries else "—"
    R.console.print(f"\n[bold]Configured models[/bold]  [dim](active: [green]{current_label}[/green])[/dim]")
    R.console.print(t)
    R.console.print(
        "  [dim]● = currently active  "
        "· Keys = number of API keys in rotation  "
        "· switch with [bold]/provider switch <provider> <model>[/bold][/dim]\n"
    )

