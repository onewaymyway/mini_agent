"""
cli/commands/providers.py — /provider slash 命令处理

/provider                        — 显示当前 provider 信息
/provider list                   — 列出所有注册的 providers
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
        R.print_error("Usage: /provider | /provider list | /provider switch <name> [model]")
