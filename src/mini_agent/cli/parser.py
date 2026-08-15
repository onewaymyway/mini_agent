"""
cli/parser.py — CLI 参数定义

只负责构建 argparse.ArgumentParser，不引入任何业务模块。
"""

from __future__ import annotations

import argparse
import textwrap


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mini-agent",
        description="Simplified Claude Code with skill support",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            Subcommands:
              mini-agent eval --scenario DIR [--skill NAME]
                  Run test_cases/-style scenarios and compare tool-failure-rate
                  /turns/token cost with a skill enabled vs excluded.
                  Run `mini-agent eval --help` for full options.

              mini-agent daemon start|stop|status [--detach] [--http-port N]
                  Manage a background daemon process (Stage 9). See docs/daemon-multi-client-guide.md.

              mini-agent user list|add|remove|role|token [...]
                  Manage daemon multi-user accounts (requires --http-multi-user).
                  See docs/multi-user-guide.md.

              mini-agent self status
                  Owner-only overview of AutonomousLoop / goals / recent activity / session pool
                  on a running daemon. See docs/multi-user-guide.md.

            Slash commands (in REPL):
              /help              Show this help
              /clear             Clear conversation history
              /retry [N]         Discard last N responses (default 1) and
                                 regenerate with same input. Can retry any
                                 turn still in history (including turns
                                 before a --resume), as long as it hasn't
                                 been folded away by /compact.
              /rollback [N]      Undo last N turns (default 1), sync session.
                                 Can roll back to any turn still in history
                                 (including turns before a --resume), as long
                                 as it hasn't been folded away by /compact.
              /plan              Show current execution plan
              /plan clear        Clear the active plan
              /plan summary      Print completed plan summary
              /notepad           Show current notepad (persists across compact)
              /notepad clear     Clear the current notepad (manual, agent won't auto-call this)
              /notepad remove <id>  Remove one notepad entry
              /recall <query>            Search raw history (incl. content removed by compact)
                                         for fragments matching a query (needs recall_history_enabled=true)
              /recall --max N <query>    Same, but return up to N results (default 5, max 20)
              /skills                    List all available skills with status and token cost
              /skill on <name> [...]     Activate one or more skills
              /skill off <name> [...]    Deactivate one or more skills
              /skill info <name>         Show full skill content
              /skill stats               Show LRU usage tracking and compact budget preview
              /skill reset               Deactivate all active skills
              /reload                    Force hot-reload of skills and agent profiles from disk
              /behavior status           Show behavior perception switch/collector status (default: all off)
              /behavior on|off           Toggle the master switch for behavior perception
              /behavior enable <name>    Enable a collector (active_window/idle/browser_report/mobile_report/clipboard_meta/cdp_browser/git_activity/terminal_command/now_playing/app_lifecycle/daily_analysis)
              /behavior disable <name>   Disable a collector
              /behavior token            Show/generate the external-report token (browser ext/git/terminal/mobile)
              /behavior recent [n]       Show the last n collected events
              /behavior clear            Clear all collected events
              /behavior browser start    Launch a dedicated CDP-debug browser and start collecting
              /behavior browser stop     Stop collecting (add --kill to also close the browser)
              /behavior browser status   Show dedicated browser / CDP connection status
              /behavior git install <repo>   Install commit/checkout report hook in a git repo
              /behavior terminal show|install  Print/append shell hook snippet (bash/zsh)
              /behavior mobile android|ios     Print mobile (Tasker/Shortcuts) setup template
              /behavior report [today|<date>]  Show/generate the work & life profile daily summary
              /stats             Show session statistics
              /verbose           Toggle verbose tool output
              /raw-output        Toggle showing raw model output (incl. <tool_use> blocks)
              /reasoning         Toggle showing the model's reasoning/thinking process (default: on)
              /model <name>      Switch model mid-session
              /compact           Compress history into a summary
              /compact_continue  Compress history, then auto-send '继续' to resume the task without waiting
              /goal <text>       Set a goal; agent negotiates acceptance criteria then runs until done
              /goal from-history Auto-derive a goal from the current session's conversation history
              /goal resume [sid] Resume an interrupted goal run (auto-picks latest if sid omitted)
              /goal list         List all resumable goal tasks (status==running, may be more than one)
              /goal status       Show current session's goal state (round/compacts/last verdict)
              /goal cancel       Clear current session's goal state record
              /goal --mode=llm|agent|auto  Optional flag on <text>/from-history/revise: how the
                                 acceptance-criteria draft is generated — llm (single bare LLM call),
                                 agent (read-only restricted Agent that inspects skills/workflows first),
                                 or auto (rule-detect which one to use; default)
              /turnjudge [on|off|status]  Toggle TurnJudge (auto-detect: real end-of-turn vs technical stall)
              /memory            Force-generate/refresh session memory now (bypass interval)
              /profile           Force-refresh user profile now (bypass interval)
              /prompts           List all managed prompt files
              /evolution log [N]          Show recent self-evolution commits (default 10)
              /evolution show <commit>    Show one commit's full info + diff
              /evolution diff <commit>    Show diff for one commit
              /evolution revert <commit>  Revert a self-evolution commit (records a lesson)
              /evolution proposals        List evolve/* proposal branches with risk grading
              /evolution merge <branch> [--force]
                                          One-click merge a proposal branch (low risk only
                                          unless --force)
              /evolve review [--global] [--tier T1|T2]   Scan lessons, spawn evolution-agent on qualifying groups
              /evolve list [--global] [--tier T1|T2]     Preview qualifying lesson groups without spawning
              /evolve consolidate [--dry-run]              Run background consolidation (prune/promote/knowledge consolidation) [alias: phase-g]
              /evolve timeline --entity <id>|--category <code> [--limit N]  Query knowledge lifecycle timeline (library index)
              /agent goals                List Goal Backlog (active goals + objectives)
              /agent goals add <title>    Add a Goal
              /agent goals obj add <title> [--goal <id>] [--thread <id>]  Add an Objective
              /agent goals done <id>      Mark goal/objective as completed
              /agent goals abandon <id>   Mark goal/objective as abandoned
              /agent goals progress <id> <notes>  Update progress notes
              /agent goals status         Show AutonomousLoop tick status
              /goals                      Shortcut for /agent goals
              /digest                     Show autonomous activity summary since last interaction
              /digest daily [YYYY-MM-DD]  Generate/show the fused daily report (behavior + goal progress + commits)
              /next [refresh]             Show (or recompute) the ranked next-action recommendations
              /decision_profile           Show the current decision/value profile (wiki/user_value_profile.md)
              /decision_profile update    Re-summarize the decision profile now (requires LLM helper)
              /growth [list]              Show pending growth-direction candidates (Growth Advisor)
              /growth scan                Run one signal scan + candidate derive + top-N report cycle now
              /growth accept|dismiss <id> Mark a candidate accepted/dismissed
              /growth report <id>         Show (or generate) the research report for a candidate
              /growth material <id>       Show (or generate) the learning material for a candidate
              /growth retrospective       Show the monthly growth retrospective summary
              /capability [list]          Show CapabilityTrack overview (Persona Capability Learning)
              /capability create <title> | <persona_desc>  Create a knowledge-type Track
              /capability cycle           Run one learning cycle now (P1: retriever not wired, skips research)
              /capability questions [track_id]  List pending async questions
              /capability answer <id> <text>  Submit an answer to a pending question
              /debug system               Print current system prompt (with token estimate)
              /debug history [full] [n]   Print history as a table (last n msgs; 'full' = no truncation)
              /debug all [n]              Print system + history together
              /debug save [path]          Dump system + full history to a Markdown file for offline analysis
              /proxy                      Show proxy pool status (shortcut for /proxy status)
              /proxy status               Show latest refreshed proxy list (latency-sorted)
              /proxy refresh              Fetch subscriptions + validate nodes now (blocking, may take a while)
              /proxy sources              List configured subscription sources
              /proxy sources add-mibei77  Add mibei77.com as a subscription source
              /proxy sources add-discovered  Add discovered_sources.json as a source (populated by agent/skill)
              /proxy integration          Show proxy integration switches (all default OFF)
              /proxy integration set <key> <value>  Toggle a switch, e.g. llm_use_proxy true
              /session list|new|save|resume|delete|dir|search   Session management
              /tasks focus|unfocus|dashboard|log|cancel|cancel-all|workers   Task management
              /concurrency tasks|llm      Show/adjust concurrency settings (alias: /cc)
              /ensemble status|on|off|mode|granularity|n|execution|strategy   Best-of-N ensemble settings
              /provider list|models|switch   LLM provider settings
              /agents list|show|reload   Custom sub-agent profile management
              /role list|use|show|exit|status|stats|reload   Roleplay persona: switch/exit agent's persona
              /hooks list|reload         Hook management
              /platform status|filtered|reload   Platform/tag load policy for skill/agent/hook/tool
              /quarantine status|list|remove|clear|reload|enable|disable   Runtime auto-quarantine (default: off)
              /cron list|status|enable|disable|run|add|remove|set-schedule   Manage periodic daemon tasks
              exit / quit        Exit
        """),
    )
    p.add_argument("prompt", nargs="?", help="Single prompt (non-interactive mode)")
    p.add_argument("--model", "-m", default=None, help="Model name (overrides CLAUDE_MODEL env)")
    p.add_argument("--system", "-s", default="", help="Extra system prompt text")
    p.add_argument("--project", "-p", default=None, help="Project root directory")
    p.add_argument("--skills-dir", default=None, help="Additional skills directory")
    p.add_argument("--prompts-dir", default=None, help="Custom prompts directory (overrides built-in prompts)")
    p.add_argument("--verbose", "-v", action="store_true", default=None, help="Show raw tool JSON")
    p.add_argument("--sandbox", action="store_true", default=None, help="Sandbox mode (no destructive ops)")
    p.add_argument("--simple-mode", action="store_true", default=None,
                   help="Simplified display for limited terminals (e.g. Termux): no status-bar "
                        "redraw/erase, no ANSI cursor control, every update is a normal printed "
                        "line (overrides MINI_AGENT_SIMPLE_MODE env)")
    p.add_argument("--raw-output", action="store_true", default=None,
                   help="Disable streaming token filtering: show the model's raw output "
                        "verbatim, including <tool_use>...</tool_use> blocks that are normally "
                        "hidden (overrides MINI_AGENT_RAW_OUTPUT env)")
    p.add_argument("--hide-reasoning", dest="show_reasoning", action="store_false", default=None,
                   help="Do not print the model's reasoning/thinking process "
                        "(overrides MINI_AGENT_SHOW_REASONING env; toggle at runtime with /reasoning)")
    p.add_argument("--yes", "-y", action="store_true", default=None, help="Auto-approve all tool calls")
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
    p.add_argument("--rpm", type=int, default=0,
                   help="Max LLM requests per minute (0 = unlimited, default: 0). "
                        "Useful to avoid hitting platform rate limits.")
    p.add_argument("--retry-backoff", choices=["fixed", "linear", "exponential"], default=None,
                   metavar="MODE",
                   help="Retry wait backoff mode: fixed / linear / exponential (default: fixed). "
                        "fixed: constant delay each retry. "
                        "linear: increases by --retry-backoff-step each retry. "
                        "exponential: multiplied by --retry-backoff-step each retry.")
    p.add_argument("--retry-backoff-step", type=float, default=None,
                   metavar="N",
                   help="Backoff step value (default: 60). "
                        "linear: seconds added per retry. "
                        "exponential: multiplier per retry (e.g. 1.5 means ×1.5 each time).")
    p.add_argument("--retry-backoff-max", type=float, default=None,
                   metavar="SECONDS",
                   help="Max wait cap for backoff in seconds (default: 0 = no cap).")
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
    p.add_argument("--providers-config", default=None, metavar="FILE",
                   help=(
                       "Path to the providers JSON config file containing API keys and "
                       "LLM fallback chain (default: providers.json in the project root). "
                       "This file should be added to .gitignore to avoid leaking secrets."
                   ))
    p.add_argument("--claude-md-file", default=None, metavar="FILENAME",
                   help=(
                       "Name of the project context document to load (default: CLAUDE.md). "
                       "Can also be set via 'claude_md_file' in agent_config.json. "
                       "If the file does not exist, the context will be empty."
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

    # ── Reminder 系统 ──────────────────────────────────────────────────────
    rem = p.add_argument_group("Reminder system")
    rem.add_argument("--reminders-dir", default=None, metavar="DIR",
                     help="[SYS-REMINDER] 用户自定义 reminder 目录（优先级高于系统默认）")
    rem.add_argument("--no-reminders", action="store_true", default=None,
                     help="[SYS-REMINDER] 禁用 reminder 注入机制")
    rem.add_argument("--reminder-verbose", action="store_true", default=None,
                     help="[SYS-REMINDER] 打印 reminder 匹配和注入详情（调试用）")

    # ── Role Agent 系统 ────────────────────────────────────────────────────
    ra = p.add_argument_group("Role Agent system")
    ra.add_argument("--role-agents", action="store_true", default=None,
                    help="[SYS-ROLE-AGENT] 启用多角色 Agent 协作（默认关闭）")
    ra.add_argument("--role-agents-allow", default=None, metavar="NAMES",
                    help="[SYS-ROLE-AGENT] 白名单：仅启用指定角色 Agent，逗号分隔（如 evaluator,coach）；不传表示全部启用")
    ra.add_argument("--role-agents-block", default=None, metavar="NAMES",
                    help="[SYS-ROLE-AGENT] 黑名单：屏蔽指定角色 Agent，逗号分隔（如 coach）；不传表示不屏蔽任何")
    ra.add_argument("--role-agents-dir", default=None, metavar="DIR",
                    help="[SYS-ROLE-AGENT] 仅从指定目录加载角色 Agent profile（覆盖默认 .agent/agents/ 目录）")

    # ── Stage 9: Daemon 模式 ──────────────────────────────────────────────
    daemon_grp = p.add_argument_group("Daemon mode (Stage 9 Phase H)")
    daemon_grp.add_argument("--daemon-mode", action="store_true", default=False,
                             help="[Stage 9] 以 daemon 模式运行（不启动 REPL，持续驻留）。"
                                  "通常由 'mini-agent daemon start' 内部调用，用户无需直接使用。")
    daemon_grp.add_argument("--no-daemon", action="store_true", default=False,
                             help="[Stage 9] 禁用 daemon 模式，回退到传统的进程内直接持有 Agent 行为。"
                                  "适用于 CI、脚本化场景等不需要持续性的一次性执行。")
    daemon_grp.add_argument("--daemon-attach-console", action="store_true", default=False,
                             help="[内部标志] 仅由 'mini-agent daemon start'（不带 --detach 时）传入，"
                                  "表示当前是前台 daemon 进程，拥有真实终端：daemon 主循环应像 "
                                  "'mini-agent daemon connect' 一样订阅并渲染 SSE 事件、接受用户输入，"
                                  "而不是裸等待信号。用户无需直接使用。")

    # ── HTTP API 服务 ──────────────────────────────────────────────────────
    http = p.add_argument_group("HTTP API server (optional)")
    http.add_argument("--http", action="store_true", default=None,
                      help="启动内置 HTTP API 服务，允许外部通过 REST/SSE 与 agent 交互")
    http.add_argument("--http-port", type=int, default=None, metavar="PORT",
                      help="HTTP 服务监听端口（默认 8765）")
    http.add_argument("--http-host", default=None, metavar="HOST",
                      help="HTTP 服务监听地址（默认 127.0.0.1；填 0.0.0.0 对外暴露）")
    http.add_argument("--http-token", default=None, metavar="TOKEN",
                      help="固定 API token（留空则自动生成并保存到 agent_api.key）")
    http.add_argument("--http-allow-ip", default=None, metavar="IP[,IP...]",
                      help="IP 白名单，逗号分隔（默认只允许 127.0.0.1）")
    http.add_argument("--http-fs-readonly", action="store_true", default=None,
                      help="文件系统 API 只读模式（禁止写/删操作）")
    http.add_argument("--http-multi-user", action="store_true", default=None,
                      help="[daemon 多用户 Phase 1] 启用多用户认证（每个用户独立 token/角色），"
                           "默认关闭，关闭时行为与现有单 token 单用户模式完全一致")

    # ── 客户端连接（连到已存在的 daemon，本进程不构建 Agent）────────────────────
    client_grp = p.add_argument_group("Client mode (connect to a running daemon)")
    client_grp.add_argument(
        "--token", "-T", default=None, metavar="TOKEN",
        help=(
            "连接已存在 daemon 时使用的 API token。多用户模式下用它来标识"
            "\"以哪个用户身份连接\"（用 'mini-agent user token <user_id>' 生成/查看）。"
            "不传时按原有优先级回退：project_root/.agent/agent_api.key > "
            "cwd/.agent/agent_api.key。仅影响 REPL 连接到 daemon 的场景，"
            "对 --http（启动 daemon 本身）无效，daemon 自身的 token 请用 --http-token。"
        ),
    )
    return p