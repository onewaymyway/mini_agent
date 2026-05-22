#!/usr/bin/env python3
"""
mini-claude-code — A simplified Claude Code CLI with skill support.

Usage:
  python main.py                          # interactive REPL
  python main.py "fix the bug in app.py" # single-shot
  python main.py --help
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

# ── Ensure local imports work regardless of cwd ───────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

# Register built-in tools (side-effect import)
import tools.builtin  # noqa: F401

from agent import Agent
from config import load_config
from permissions import PermissionGuard
from prompts import pm                        # ← PromptManager singleton
from skills import SkillLoader
import renderer as R


# ── CLI parsing ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mini-claude-code",
        description="Simplified Claude Code with skill support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Slash commands (in REPL):
              /help              Show this help
              /clear             Clear conversation history
              /skills            List all available skills
              /skill on <name>   Activate a skill
              /skill off <name>  Deactivate a skill
              /stats             Show session statistics
              /verbose           Toggle verbose tool output
              /model <name>      Switch model mid-session
              /compact           Compress history into a summary
              /prompts           List all managed prompt files
              exit / quit        Exit
        """),
    )
    p.add_argument("prompt", nargs="?", help="Single prompt (non-interactive mode)")
    p.add_argument("--model", "-m", default=None, help="Model name (overrides CLAUDE_MODEL env)")
    p.add_argument("--system", "-s", default="", help="Extra system prompt text")
    p.add_argument("--project", "-p", default=None, help="Project root directory")
    p.add_argument("--skills-dir", default=None, help="Additional skills directory")
    p.add_argument("--verbose", "-v", action="store_true", help="Show raw tool JSON")
    p.add_argument("--sandbox", action="store_true", help="Sandbox mode (no destructive ops)")
    p.add_argument("--yes", "-y", action="store_true", help="Auto-approve all tool calls")
    p.add_argument("--no-stream", action="store_true", help="Disable streaming")
    p.add_argument("--max-turns", type=int, default=None, help="Max agentic turns per user message")
    p.add_argument("--provider", default=None,
                   help="LLM provider: anthropic|openai|ollama|... (overrides LLM_PROVIDER env)")
    p.add_argument("--base-url", default=None,
                   help="Custom API endpoint (for proxies, Azure, local deployments)")
    return p


# ── REPL ──────────────────────────────────────────────────────────────────────

def run_repl(agent: Agent, skill_loader: SkillLoader) -> None:
    # All display text comes from PromptManager fragments
    R.console.print(pm.fragment("cli_messages", "BANNER"), style="bold blue")
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_MODEL", model=agent.cfg.model))
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_PROJECT", project_root=agent.cfg.project_root))
    R.print_info(pm.fragment("cli_messages", "REPL_STARTUP_SKILLS", skill_count=len(skill_loader.available)))
    if agent.cfg.sandbox:
        R.print_warning(pm.fragment("cli_messages", "REPL_SANDBOX_WARNING"))

    while True:
        try:
            R.print_user_prompt()
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            R.print_info(pm.fragment("cli_messages", "BYE_MSG"))
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            R.print_stats(agent.stats.summary())
            R.print_info(pm.fragment("cli_messages", "BYE_MSG"))
            break

        if user_input.startswith("/"):
            _handle_slash(user_input, agent, skill_loader)
            continue

        try:
            agent.run_turn(user_input)
        except KeyboardInterrupt:
            R.print_interrupt()
        except Exception as e:
            R.print_error(f"API error: {e}")
            if agent.cfg.verbose:
                import traceback
                traceback.print_exc()


# ── Slash command handler ─────────────────────────────────────────────────────

def _handle_slash(cmd: str, agent: Agent, skill_loader: SkillLoader) -> None:
    parts = cmd.lstrip("/").split()
    name = parts[0].lower() if parts else ""

    if name == "help":
        R.console.print(build_parser().format_help())

    elif name == "clear":
        agent.clear_history()
        R.print_success(pm.fragment("cli_messages", "HISTORY_CLEARED"))

    elif name == "skills":
        R.console.print(f"\n[bold]{pm.fragment('cli_messages', 'SKILLS_LIST_HEADER')}[/bold]")
        R.console.print(skill_loader.list_skills() or pm.fragment("cli_messages", "NO_SKILLS_FOUND"))

    elif name == "skill" and len(parts) >= 3:
        action, skill_name = parts[1], parts[2]
        if action == "on":
            ok = skill_loader.activate(skill_name)
            if ok:
                R.print_success(pm.fragment("cli_messages", "SKILL_ACTIVATED", name=skill_name))
            else:
                R.print_error(pm.fragment("cli_messages", "SKILL_NOT_FOUND", name=skill_name))
        elif action == "off":
            ok = skill_loader.deactivate(skill_name)
            if ok:
                R.print_success(pm.fragment("cli_messages", "SKILL_DEACTIVATED", name=skill_name))
            else:
                R.print_error(pm.fragment("cli_messages", "SKILL_NOT_FOUND", name=skill_name))
        else:
            R.print_error(pm.fragment("cli_messages", "SKILL_CMD_USAGE"))

    elif name == "stats":
        R.print_stats(agent.stats.summary())

    elif name == "verbose":
        agent.cfg.verbose = not agent.cfg.verbose
        key = "VERBOSE_ON" if agent.cfg.verbose else "VERBOSE_OFF"
        R.print_info(pm.fragment("cli_messages", key))

    elif name == "model" and len(parts) >= 2:
        agent.cfg.model = parts[1]
        R.print_info(pm.fragment("cli_messages", "MODEL_SWITCHED", model=parts[1]))

    elif name == "compact":
        _compact_history(agent)

    elif name == "prompts":
        _list_prompts()

    elif name == "provider":
        _handle_provider_cmd(parts[1:], agent)

    else:
        R.print_error(pm.fragment("cli_messages", "UNKNOWN_COMMAND", cmd=cmd))


def _compact_history(agent: Agent) -> None:
    """Summarise the conversation to free up context window."""
    if not agent.history:
        R.print_info(pm.fragment("cli_messages", "COMPACT_EMPTY"))
        return

    R.print_info(pm.fragment("cli_messages", "COMPACT_START"))

    # Prompt text entirely from the managed prompt file
    compact_prompt = pm.get_compact_prompt()

    try:
        result = agent.run_turn(compact_prompt)
        agent._history = [
            {"role": "user", "content": "[Previous session summary]"},
            {"role": "assistant", "content": result},
        ]
        R.print_success(pm.fragment("cli_messages", "COMPACT_SUCCESS"))
    except Exception as e:
        R.print_error(f"Compact failed: {e}")


def _list_prompts() -> None:
    """Show all prompt files managed by PromptManager."""
    R.console.print("\n[bold]Managed prompt files:[/bold]")
    for p_name in pm.list_prompts():
        R.console.print(f"  [cyan]{p_name}[/cyan]")
    R.console.print(f"\n[dim]Prompt root: {pm._root}[/dim]")


def _handle_provider_cmd(args: list[str], agent: Agent) -> None:
    """
    /provider                    — show current provider info
    /provider list               — list all registered providers
    /provider switch <name> [model] — switch provider at runtime
    """
    from llm import list_providers, LLMConfig, create_client
    if not args or args[0] == "info":
        R.print_info(f"Current LLM: {agent.llm_client}")
    elif args[0] == "list":
        R.console.print("\n[bold]Registered providers:[/bold]")
        for p in list_providers():
            R.console.print(f"  [cyan]{p}[/cyan]")
    elif args[0] == "switch" and len(args) >= 2:
        provider = args[1]
        model = args[2] if len(args) >= 3 else agent.cfg.model
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY", "") if provider in ("anthropic", "claude") else                   os.environ.get("OPENAI_API_KEY", "") if provider in ("openai", "azure") else ""
        cfg = LLMConfig(provider=provider, model=model, api_key=api_key,
                        requires_api_key=(provider not in ("ollama", "local")))
        try:
            agent.switch_provider(cfg)
            R.print_success(f"Switched to {provider} / {model}")
        except Exception as e:
            R.print_error(str(e))
    else:
        R.print_error("Usage: /provider | /provider list | /provider switch <name> [model]")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Config
    project_root = Path(args.project).expanduser() if args.project else Path.cwd()
    cfg = load_config(
        project_root=project_root,
        extra_system=args.system,
        verbose=args.verbose,
        sandbox=args.sandbox,
        auto_approve=args.yes,
        model=args.model,
        llm_provider=getattr(args, "provider", None),
        llm_base_url=getattr(args, "base_url", None),
    )

    if not cfg.api_key:
        R.print_error("ANTHROPIC_API_KEY is not set.")
        sys.exit(1)

    if args.max_turns:
        cfg.max_turns = args.max_turns
    if args.no_stream:
        cfg.stream = False

    # Skills
    skill_dirs: list[Path] = []
    if cfg.skills_dir:
        skill_dirs.append(cfg.skills_dir)
    if args.skills_dir:
        skill_dirs.append(Path(args.skills_dir).expanduser())
    skill_loader = SkillLoader(skill_dirs)

    # Guard
    guard = PermissionGuard(
        auto_approve=cfg.auto_approve,
        sandbox=cfg.sandbox,
        project_root=cfg.project_root,
    )

    # Agent
    agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)

    # Single-shot mode
    if args.prompt:
        try:
            agent.run_turn(args.prompt)
            R.print_stats(agent.stats.summary())
        except KeyboardInterrupt:
            R.print_interrupt()
        except Exception as e:
            R.print_error(str(e))
            sys.exit(1)
        return

    # Interactive REPL
    import anthropic  # delayed so error message works without key
    run_repl(agent, skill_loader)


if __name__ == "__main__":
    import anthropic  # noqa: F401  (ensure importable before main)
    main()
