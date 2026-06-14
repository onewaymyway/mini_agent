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
              /memory            Force-generate/refresh session memory now (bypass interval)
              /profile           Force-refresh user profile now (bypass interval)
              /prompts           List all managed prompt files
              exit / quit        Exit
        """),
    )
    p.add_argument("prompt", nargs="?", help="Single prompt (non-interactive mode)")
    p.add_argument("--model", "-m", default=None, help="Model name (overrides CLAUDE_MODEL env)")
    p.add_argument("--system", "-s", default="", help="Extra system prompt text")
    p.add_argument("--project", "-p", default=None, help="Project root directory")
    p.add_argument("--skills-dir", default=None, help="Additional skills directory")
    p.add_argument("--prompts-dir", default=None, help="Custom prompts directory (overrides built-in prompts)")
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

    # ── Reminder 系统 ──────────────────────────────────────────────────────
    rem = p.add_argument_group("Reminder system")
    rem.add_argument("--reminders-dir", default=None, metavar="DIR",
                     help="[SYS-REMINDER] 用户自定义 reminder 目录（优先级高于系统默认）")
    rem.add_argument("--no-reminders", action="store_true", default=None,
                     help="[SYS-REMINDER] 禁用 reminder 注入机制")
    rem.add_argument("--reminder-verbose", action="store_true", default=None,
                     help="[SYS-REMINDER] 打印 reminder 匹配和注入详情（调试用）")

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
    return p
