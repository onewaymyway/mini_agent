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
import tools.builtin          # noqa: F401
import tools.orchestration    # noqa: F401
import tools.plan             # noqa: F401  ← 执行计划工具

from agent import Agent
from config import load_config
from permissions import PermissionGuard
from prompts import pm                        # ← PromptManager singleton
from repl_input import get_repl_input
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
              /plan              Show current execution plan
              /plan clear        Clear the active plan
              /plan summary      Print completed plan summary
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
    p.add_argument("--system-tool-call", action="store_true",
                   help="Use system-prompt tool call mode (max compatibility, no SDK tools)")
    p.add_argument("--debug-llm", action="store_true",
                   help="Enable LLM request/response debug logging to file")
    p.add_argument("--debug-llm-console", action="store_true",
                   help="Also print LLM debug info to console (implies --debug-llm)")
    p.add_argument("--workers", type=int, default=4,
                   help="Max concurrent sub-agents (default: 4)")
    p.add_argument("--max-llm-calls", type=int, default=8,
                   help="Max concurrent LLM API calls (default: 8)")
    p.add_argument("--session-dir", default=None,
                   help="Directory to save session files (default: ./sessions)")
    p.add_argument("--session-fmt", choices=["json", "jsonl"], default="json",
                   help="Session file format: json (default) or jsonl")
    p.add_argument("--no-save-session", action="store_true",
                   help="Disable automatic session saving")
    p.add_argument("--resume", default=None, metavar="SESSION_ID",
                   help="Resume a previous session by id (or id prefix)")
    p.add_argument("--agent-name", default=None,
                   help="Agent display name (default: orzooo)")
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
    if agent.session_id:
        R.print_info(f"Session: [{agent.session_id}] — /session list to browse history")

    repl = get_repl_input()
    from orchestrator.status_bar import pause, resume

    while True:
        # 等待用户输入前：擦除状态栏，暂停重绘
        pause()
        try:
            user_input = repl.prompt()
        except KeyboardInterrupt:
            resume()  # 先恢复状态栏，再打印中断信息
            R.print_interrupt()
            continue
        except EOFError:
            resume()  # 先恢复状态栏
            print()
            R.print_info(pm.fragment("cli_messages", "BYE_MSG"))
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            resume()  # 先恢复状态栏，再打印退出信息
            R.print_stats(agent.stats.summary())
            R.print_info(pm.fragment("cli_messages", "BYE_MSG"))
            break

        if user_input.startswith("/"):
            _handle_slash(user_input, agent, skill_loader)
            continue

        # 用户回车，agent 开始运行：恢复状态栏
        resume()
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

    elif name in ("session", "sessions"):
        _handle_session_cmd(parts[1:], agent)

    elif name == "tasks":
        _handle_tasks_cmd(parts[1:], agent)

    elif name == "plan":
        _handle_plan_cmd(parts[1:])

    elif name == "concurrency" or name == "cc":
        _handle_concurrency_cmd(parts[1:])

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


def _handle_tasks_cmd(args: list[str], agent) -> None:
    """
    /tasks                 — show all tasks table
    /tasks dashboard       — live dashboard until all done
    /tasks log <id>        — show task log
    /tasks cancel <id>     — cancel a task
    /tasks cancel-all      — cancel all pending/running tasks
    /tasks workers <n>     — change max_workers
    """
    from tools.orchestration import get_task_manager
    from orchestrator.task_display import print_task_table, print_task_log, TaskDashboard
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
        R.print_error("Usage: /tasks | /tasks dashboard | /tasks log <id> | /tasks cancel <id> | /tasks cancel-all | /tasks workers <n>")


def _handle_session_cmd(args: list[str], agent) -> None:
    """
    /session              — 显示当前 session 信息
    /session list         — 列出最近 20 个 session
    /session save         — 立即保存当前 session
    /session resume <id>  — 加载一个旧 session（追加到当前历史）
    /session new          — 清空历史，开始新 session
    /session delete <id>  — 删除一个 session 文件
    /session dir          — 显示 session 目录
    """
    from session import SessionManager
    mgr = agent.session_manager
    if mgr is None:
        R.print_warning("Session saving is disabled (--no-save-session).")
        return

    sub = args[0].lower() if args else "info"

    if sub in ("info", "status", ""):
        sid = agent.session_id or "(none)"
        sf  = agent.session_file or "(not saved yet)"
        R.console.print(f"\n[bold]Current session:[/bold]")
        R.console.print(f"  ID   : [cyan]{sid}[/cyan]")
        R.console.print(f"  File : [dim]{sf}[/dim]")
        R.console.print(f"  Turns: {agent.stats.turns}  "
                        f"Tokens: {agent.stats.input_tokens}↑ {agent.stats.output_tokens}↓")

    elif sub == "list":
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
        metas = mgr.list_sessions(limit=limit)
        if not metas:
            R.console.print("[dim]No sessions found.[/dim]")
            return
        from rich.table import Table
        from rich import box as rbox
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("ID",      style="cyan",  width=10)
        t.add_column("Title",   min_width=28, max_width=40)
        t.add_column("Age",     width=12)
        t.add_column("Model",   width=20)
        t.add_column("Turns",   width=6,  justify="right")
        t.add_column("Tokens",  width=12, justify="right")
        for m in metas:
            t.add_row(
                m.id,
                m.title,
                m.age_str,
                m.model[:20],
                str(m.turns),
                f"{m.input_tokens}/{m.output_tokens}",
            )
        R.console.print(t)

    elif sub == "save":
        path = agent.save_session()
        if path:
            R.print_success(f"Saved → {path}")
        else:
            R.print_error("Save failed or nothing to save.")

    elif sub == "resume" and len(args) >= 2:
        sid = args[1]
        if agent.load_session(sid):
            R.print_success(
                f"Resumed [{agent.session_id}] — {len(agent.history)} messages loaded"
            )
        else:
            R.print_error(f"Session '{sid}' not found.")

    elif sub == "new":
        agent.clear_history()
        agent._session = mgr.new_session(
            provider=getattr(agent.cfg, "llm_provider", "unknown"),
            model=agent.cfg.model,
        )
        R.print_success("New session started.")

    elif sub == "delete" and len(args) >= 2:
        sid = args[1]
        ok = mgr.delete(sid)
        if ok:
            R.print_success(f"Deleted session '{sid}'.")
        else:
            R.print_error(f"Session '{sid}' not found.")

    elif sub == "dir":
        R.console.print(f"Session directory: [cyan]{mgr.session_dir}[/cyan]")

    else:
        R.print_error(
            "Usage: /session | /session list [n] | /session save | "
            "/session resume <id> | /session new | /session delete <id> | /session dir"
        )


def _handle_plan_cmd(args: list[str]) -> None:
    """
    /plan              — 显示当前执行计划（树形）
    /plan clear        — 清除当前计划
    /plan summary      — 打印完成摘要表格
    """
    from orchestrator.plan import get_plan, clear_plan
    from orchestrator.plan_display import print_plan_tree, print_plan_summary

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


def _handle_concurrency_cmd(args: list[str]) -> None:
    """
    /concurrency           — show current limits and queue state
    /concurrency tasks <n> — set max concurrent tasks
    /concurrency llm <n>   — set max concurrent LLM calls
    """
    from orchestrator.concurrency import concurrency_snapshot, set_max_tasks, set_max_llm_calls
    snap = concurrency_snapshot()

    if not args or args[0] == "status":
        t = snap["tasks"]
        l = snap["llm"]
        R.console.print(f"\n[bold]Concurrency status:[/bold]")
        R.console.print(
            f"  Tasks  : [cyan]{t['active']} running[/cyan] / "
            f"{t['limit']} max  "
            f"({t['waiting']} queued)"
        )
        R.console.print(
            f"  LLM    : [blue]{l['active']} active[/blue] / "
            f"{l['limit']} max  "
            f"({l['waiting']} queued)"
        )
        if t["waiters"]:
            R.console.print("  Queued tasks: " + ", ".join(
                f"[dim]{w['label']} ({w['waited_s']}s)[/dim]"
                for w in t["waiters"]
            ))
        if l["waiters"]:
            R.console.print("  Queued LLM : " + ", ".join(
                f"[dim]{w['label']} ({w['waited_s']}s)[/dim]"
                for w in l["waiters"]
            ))
    elif args[0] == "tasks" and len(args) >= 2:
        try:
            n = int(args[1])
            set_max_tasks(n)
            from tools.orchestration import get_task_manager
            mgr = get_task_manager()
            if mgr: mgr.max_workers = n
            R.print_success(f"Max concurrent tasks → {n}")
        except ValueError:
            R.print_error("Usage: /concurrency tasks <number>")
    elif args[0] == "llm" and len(args) >= 2:
        try:
            n = int(args[1])
            set_max_llm_calls(n)
            R.print_success(f"Max concurrent LLM calls → {n}")
        except ValueError:
            R.print_error("Usage: /concurrency llm <number>")
    else:
        R.print_error("Usage: /concurrency | /concurrency tasks <n> | /concurrency llm <n>")


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
    debug_console = getattr(args, "debug_llm_console", False)
    cfg = load_config(
        project_root=project_root,
        extra_system=args.system,
        verbose=args.verbose,
        sandbox=args.sandbox,
        auto_approve=args.yes,
        model=args.model,
        llm_provider=getattr(args, "provider", None),
        llm_base_url=getattr(args, "base_url", None),
        use_system_tool_call=getattr(args, "system_tool_call", False),
        debug_llm=getattr(args, "debug_llm", False) or debug_console,
        debug_llm_console=debug_console,
        max_llm_calls=getattr(args, "max_llm_calls", 8),
        session_dir=Path(args.session_dir) if getattr(args, "session_dir", None) else None,
        session_fmt=getattr(args, "session_fmt", "json"),
        auto_save_session=not getattr(args, "no_save_session", False),
        agent_name=getattr(args, "agent_name", None),
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

    # Concurrency control (task slots + LLM call slots)
    from orchestrator.concurrency import init_concurrency
    from orchestrator.status_bar import start_status_bar
    max_workers = getattr(args, "workers", 4)
    max_llm_calls = getattr(args, "max_llm_calls", 8)
    init_concurrency(max_tasks=max_workers, max_llm_calls=max_llm_calls)
    start_status_bar()

    # Task manager (for concurrent sub-agents)
    from tools.orchestration import init_task_manager
    init_task_manager(cfg, max_workers=max_workers)
    R.print_info(f"Task manager ready (max {max_workers} concurrent workers)")

    # Agent
    agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)

    # Resume session if requested
    if getattr(args, "resume", None):
        if agent.load_session(args.resume):
            R.print_success(f"Resumed session [{agent.session_id}] — {len(agent.history)} messages loaded")
        else:
            R.print_error(f"Session '{args.resume}' not found. Starting fresh.")

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
