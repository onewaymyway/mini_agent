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
import tools.user_input        # noqa: F401  ← 用户询问工具
# Note: tools.skill_manager is NOT imported here — it is registered lazily
# inside Agent.__init__ because it needs the SkillLoader instance to bind.

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
              /retry             Discard last response, regenerate with same input
              /rollback          Undo entire last turn (input + response), sync session
              /plan              Show current execution plan
              /plan clear        Clear the active plan
              /plan summary      Print completed plan summary
              /skills                    List all available skills with status and token cost
              /skill on <name> [...]     Activate one or more skills
              /skill off <name> [...]    Deactivate one or more skills
              /skill info <name>         Show full skill content
              /skill stats               Show LRU usage tracking and compact budget preview
              /skill reset               Deactivate all active skills
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
    p.add_argument("--system-msg-format",
                   choices=["system_field", "system_role"],
                   default=None,
                   help=(
                       "How to pass the system prompt to the model. "
                       "'system_field' (default): use a top-level system parameter "
                       "{ system: '...', messages: [...] }. "
                       "'system_role': inject system content as the first message "
                       "with role='system' inside the messages list."
                   ))
    p.add_argument("--config", "-c", default=None, metavar="FILE",
                   help=(
                       "Path to a JSON config file. Parameters in this file override "
                       "command-line arguments and environment variables. "
                       "If omitted, agent_config.json in the project root is used when it exists."
                   ))

    # ── 感知与记忆功能开关 ────────────────────────────────────────────────────
    perc = p.add_argument_group("perception & memory (all off by default)")
    perc.add_argument("--memory", action="store_true", default=None,
                      help="[SYS-MEMORY] Enable cross-session long-term memory retrieval")
    perc.add_argument("--memory-top-k", type=int, default=None, metavar="N",
                      help="Max memories injected per turn (default: 3)")
    perc.add_argument("--session-summary", action="store_true", default=None,
                      help="[SYS-SUMMARY] Generate LLM summary at end of each session")
    perc.add_argument("--session-summary-min-turns", type=int, default=None, metavar="N",
                      help="Min turns before generating summary (default: 4)")
    perc.add_argument("--session-search", action="store_true", default=None,
                      help="[SYS-SEARCH] Enable /session search <query> command")
    perc.add_argument("--auto-compress", action="store_true", default=None,
                      help="[SYS-COMPRESS] Auto-compress history when context budget exceeded")
    perc.add_argument("--auto-compress-threshold", type=float, default=None, metavar="0.0-1.0",
                      help="Context budget ratio to trigger compression (default: 0.7)")
    perc.add_argument("--tool-result-trim", action="store_true", default=None,
                      help="[SYS-TRIM] Truncate long tool results to save tokens")
    perc.add_argument("--tool-result-trim-threshold", type=int, default=None, metavar="CHARS",
                      help="Character threshold for tool result trimming (default: 500)")
    perc.add_argument("--forget-policy", action="store_true", default=None,
                      help="[SYS-FORGET] Weight-based forgetting: drop low-value history first")
    perc.add_argument("--skill-semantic", action="store_true", default=None,
                      help="[SYS-SKILL-SEM] Use embedding similarity for skill activation")
    perc.add_argument("--skill-semantic-threshold", type=float, default=None, metavar="0.0-1.0",
                      help="Similarity threshold for semantic skill activation (default: 0.72)")
    perc.add_argument("--skill-tracking", action="store_true", default=None,
                      help="[SYS-SKILL-TRACK] Track skill activation counts per session")
    perc.add_argument("--skill-chunking", action="store_true", default=None,
                      help="[SYS-SKILL-CHUNK] Inject only relevant skill sections (saves tokens)")
    perc.add_argument("--skill-compact-budget", type=int, default=None, metavar="TOKENS",
                      help="[SYS-SKILL-COMPACT] Total token budget for skill re-attachment after compression (default: 25000)")
    perc.add_argument("--skill-compact-per-skill", type=int, default=None, metavar="TOKENS",
                      help="[SYS-SKILL-COMPACT] Max tokens per skill during re-attachment (default: 5000)")
    perc.add_argument("--project-scan", action="store_true", default=None,
                      help="[SYS-PROJ] Scan project structure and inject into system prompt")
    perc.add_argument("--file-watch", action="store_true", default=None,
                      help="[SYS-WATCH] Detect external file changes between turns")
    perc.add_argument("--tool-cache", action="store_true", default=None,
                      help="[SYS-TOOLCACHE] Cache read_file/web_search results within session")
    perc.add_argument("--token-estimate", action="store_true", default=None,
                      help="[SYS-TOKEN] Estimate token usage before each LLM call")
    perc.add_argument("--token-warn-threshold", type=float, default=None, metavar="0.0-1.0",
                      help="Token budget ratio for warning (default: 0.75)")
    perc.add_argument("--tool-stats", action="store_true", default=None,
                      help="[SYS-STATS] Track per-tool call counts, success rates, output sizes")
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

    from terminal import term as _term

    while True:
        try:
            user_input = _term.prompt_user()
        except KeyboardInterrupt:
            R.print_interrupt()
            continue
        except EOFError:
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

    elif name == "retry":
        _handle_retry(agent)

    elif name == "rollback":
        _handle_rollback(agent)

    elif name == "skills":
        _handle_skills_list(skill_loader)

    elif name == "skill":
        _handle_skill_cmd(parts[1:], skill_loader)

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


def _handle_skills_list(skill_loader: SkillLoader) -> None:
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
    t.add_column("",        width=3)                       # 激活标记
    t.add_column("Name",    style="cyan",  min_width=18)
    t.add_column("Description", min_width=36, max_width=56)
    t.add_column("~Tokens", width=8, justify="right")

    for s in catalog:
        skill_obj = skill_loader.get(s["name"])
        token_est = len(skill_obj.content) // 4 if skill_obj else 0   # 粗估 4 chars/token
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


def _handle_skill_cmd(args: list[str], skill_loader: SkillLoader) -> None:
    """
    /skill on  <name> [name2 ...]  — 激活一个或多个技能
    /skill off <name> [name2 ...]  — 卸载一个或多个技能
    /skill info <name>             — 显示技能全文内容
    /skill reset                   — 卸载所有当前激活的技能
    /skill                         — 无子命令时显示帮助
    """
    if not args:
        R.print_error(pm.fragment("cli_messages", "SKILL_CMD_USAGE"))
        return

    action = args[0].lower()
    names  = args[1:]          # 可能为空

    # ── /skill on <name> [name2 ...] ─────────────────────────────────────────
    if action == "on":
        if not names:
            R.print_error("Usage: /skill on <name> [name2 ...]")
            return
        ok_list, bad_list, dup_list = [], [], []
        for n in names:
            if n not in skill_loader.available:
                bad_list.append(n)
            elif not skill_loader.activate(n):   # already active
                dup_list.append(n)
            else:
                ok_list.append(n)
                R.print_success(pm.fragment("cli_messages", "SKILL_ACTIVATED", name=n))
        for n in dup_list:
            R.print_info(pm.fragment("cli_messages", "SKILL_ALREADY_ACTIVE", name=n))
        for n in bad_list:
            _suggest_skill(n, skill_loader)

    # ── /skill off <name> [name2 ...] ────────────────────────────────────────
    elif action == "off":
        if not names:
            R.print_error("Usage: /skill off <name> [name2 ...]")
            return
        ok_list, bad_list, idle_list = [], [], []
        for n in names:
            if n not in skill_loader.available:
                bad_list.append(n)
            elif not skill_loader.deactivate(n):   # not active
                idle_list.append(n)
            else:
                ok_list.append(n)
                R.print_success(pm.fragment("cli_messages", "SKILL_DEACTIVATED", name=n))
        for n in idle_list:
            R.print_info(pm.fragment("cli_messages", "SKILL_NOT_ACTIVE", name=n))
        for n in bad_list:
            _suggest_skill(n, skill_loader)

    # ── /skill stats ──────────────────────────────────────────────────────────
    elif action == "stats":
        _handle_skill_stats(skill_loader)

    # ── /skill info <name> ────────────────────────────────────────────────────
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

    # ── /skill reset ──────────────────────────────────────────────────────────
    elif action == "reset":
        active_now = list(skill_loader.active)   # copy before mutating
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
    区分「已加载」和「实际使用」（tracker 里有记录 = 真正用过）。
    """
    from rich.table import Table
    from rich import box as rbox
    import time

    tracker = skill_loader.tracker
    records = tracker.records
    active_set = set(skill_loader.active)
    tracked_set = {r.name for r in records}

    R.console.print("\n[bold]Skill Usage Tracking[/bold]")
    R.console.print(
        f"  Budget: [cyan]{tracker.total_budget:,}[/cyan] tokens total  "
        f"/ [cyan]{tracker.per_skill_tokens:,}[/cyan] per skill\n"
    )

    # 合并「激活但未使用」的 skill 也显示出来
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

    # 说明
    R.console.print(
        "  [dim]LRU rank = priority during compression. "
        "'active/unused' = loaded but no evidence of actual use this session.[/dim]"
    )

    # 预演压缩
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
    # 模糊匹配：名称前缀或子串
    candidates = [
        a for a in skill_loader.available
        if a.startswith(name) or name in a or a.startswith(name[:3])
    ]
    if candidates:
        R.print_info(f"  Did you mean: {', '.join(candidates)}?")


def _handle_retry(agent: Agent) -> None:
    """
    /retry — 丢弃上一轮模型输出，用相同的用户消息重新生成。

    适用场景：对答案不满意，希望得到一个不同的版本，但不想重新输入问题。
    """
    if agent._turn_snapshot is None:
        R.print_warning("Nothing to retry — no previous turn in this session.")
        return
    try:
        agent.retry_last_turn()
    except KeyboardInterrupt:
        R.print_interrupt()
    except Exception as e:
        R.print_error(f"Retry failed: {e}")
        if agent.cfg.verbose:
            import traceback
            traceback.print_exc()


def _handle_rollback(agent: Agent) -> None:
    """
    /rollback — 完整撤销上一轮（用户消息 + 模型回复），同步 session 文件。

    适用场景：发现上一个问题问错了，或者整轮对话结果都不想要，完全撤回。
    回退后 session 文件会立即更新，终端显示回退分隔线。
    """
    if agent._turn_snapshot is None:
        R.print_warning("Nothing to rollback — no previous turn in this session.")
        return
    ok = agent.rollback_turn()
    if ok:
        R.print_success(
            f"Rollback complete. History now has {len(agent.history)} messages. "
            "Session saved."
        )
    else:
        R.print_error("Rollback failed unexpectedly.")


def _compact_history(agent: Agent) -> None:
    """
    /compact — 压缩对话历史并重附 skill 上下文（类 Claude Code 机制）。
    调用 agent.compact_with_skills()，该方法同时处理摘要生成和 skill 重附。
    """
    if not agent.history:
        R.print_info(pm.fragment("cli_messages", "COMPACT_EMPTY"))
        return
    R.print_info(pm.fragment("cli_messages", "COMPACT_START"))
    try:
        agent.compact_with_skills()
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

    elif sub == "search" and len(args) >= 2:
        # [SYS-SEARCH] 关键词搜索 session
        if not getattr(agent.cfg, "session_search_enabled", False):
            R.print_warning("Session search is disabled. Start with --session-search to enable.")
            return
        query = " ".join(args[1:])
        results = mgr.search(query)
        if not results:
            R.console.print(f"[dim]No sessions found for '{query}'.[/dim]")
            return
        from rich.table import Table
        from rich import box as rbox
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("ID", style="cyan", width=10)
        t.add_column("Title", min_width=24, max_width=36)
        t.add_column("Summary", min_width=30, max_width=50)
        t.add_column("Age", width=12)
        for m in results:
            t.add_row(m.id, m.title, (m.summary[:60] + "…") if len(m.summary) > 60 else m.summary, m.age_str)
        R.console.print(t)

    else:
        R.print_error(
            "Usage: /session | /session list [n] | /session save | "
            "/session resume <id> | /session new | /session delete <id> | "
            "/session dir | /session search <query>"
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
    config_file = Path(args.config).expanduser() if getattr(args, "config", None) else None
    def _flag(name, default=None):
        v = getattr(args, name, default)
        return v if v else default

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
        system_message_format=getattr(args, "system_msg_format", None),
        config_file=config_file,
        # 感知与记忆开关（None 表示未指定，由 load_config 使用默认值）
        memory_enabled=_flag("memory"),
        memory_top_k=_flag("memory_top_k"),
        session_summary_enabled=_flag("session_summary"),
        session_summary_min_turns=_flag("session_summary_min_turns"),
        session_search_enabled=_flag("session_search"),
        auto_compress_enabled=_flag("auto_compress"),
        auto_compress_threshold=_flag("auto_compress_threshold"),
        tool_result_trim_enabled=_flag("tool_result_trim"),
        tool_result_trim_threshold=_flag("tool_result_trim_threshold"),
        forget_policy_enabled=_flag("forget_policy"),
        skill_semantic_enabled=_flag("skill_semantic"),
        skill_semantic_threshold=_flag("skill_semantic_threshold"),
        skill_tracking_enabled=_flag("skill_tracking"),
        skill_chunking_enabled=_flag("skill_chunking"),
        skill_compact_budget=_flag("skill_compact_budget"),
        skill_compact_per_skill=_flag("skill_compact_per_skill"),
        project_scan_enabled=_flag("project_scan"),
        file_watch_enabled=_flag("file_watch"),
        tool_cache_enabled=_flag("tool_cache"),
        token_estimate_enabled=_flag("token_estimate"),
        token_warn_threshold=_flag("token_warn_threshold"),
        tool_stats_enabled=_flag("tool_stats"),
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
    skill_loader = SkillLoader(
        skill_dirs,
        per_skill_tokens=getattr(cfg, "skill_compact_per_skill", 5_000),
        total_budget=getattr(cfg, "skill_compact_budget", 25_000),
    )

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
