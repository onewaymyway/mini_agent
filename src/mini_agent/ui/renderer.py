"""
renderer.py — 渲染适配层

将历史 API（print_tool_call 等函数）映射到 terminal.term。
所有调用方都保持不变，这里做转发即可。

terminal.term 是唯一写屏幕的地方。
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from rich.syntax import Syntax
from rich.text import Text

from .terminal import term, Terminal

# 向后兼容：其他模块直接 import R.console 后调用 .print()
# 提供一个轻量的 _ConsoleProxy 代理到 terminal
class _ConsoleProxy:
    def print(self, *args, **kwargs):
        term.print(*args, **kwargs)
    def rule(self, *args, **kwargs):
        term.rule(*args, **kwargs)

console = _ConsoleProxy()


# ── StreamWriter（向后兼容 agent.py 的调用方式）────────────────────────────

class StreamWriter:
    """
    兼容层：agent.py 持有 StreamWriter 实例，逐 token write()，最后 flush()。
    内部全部委托给 terminal.term。
    """
    def __init__(self) -> None:
        self._buffer: list[str] = []

    def write(self, token: str) -> None:
        self._buffer.append(token)
        term.stream_token(token)

    def flush(self) -> str:
        term.stream_end()
        full = "".join(self._buffer)
        self._buffer.clear()
        return full


# ── 工具输出 ──────────────────────────────────────────────────────────────────

def print_tool_call(tool_name: str, tool_input: dict, verbose: bool = False) -> None:
    icon = _tool_icon(tool_name)
    summary = _tool_summary(tool_name, tool_input)
    term.print(f"\n{icon} [bold cyan]{tool_name}[/bold cyan]  [dim]{summary}[/dim]")
    if verbose:
        term.syntax(json.dumps(tool_input, indent=2), "json",
                    theme="ansi_dark", line_numbers=False)


def print_tool_result(tool_name: str, result: str, truncate: int = 2000) -> None:
    if not result or not result.strip():
        term.print("  [dim](empty result)[/dim]")
        return
    display = result if len(result) <= truncate else result[:truncate] + "\n…[truncated]"
    lang = _result_lang(tool_name, result)
    if lang:
        term.syntax(display, lang, theme="ansi_dark",
                    line_numbers=False, background_color="default")
    else:
        term.print(Text(display, style="dim"))


def print_tool_error(tool_name: str, error: str) -> None:
    term.print(f"  [red]✗ {tool_name} error:[/red] {error}")


# ── 布局 ──────────────────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    term.rule(f"[bold blue]{title}[/bold blue]")


def print_assistant_prefix(agent_name: str = "orzooo") -> None:
    term.print(f"\n[bold blue]{agent_name}[/bold blue] ", end="")


def print_reasoning(token: str) -> None:
    # reasoning token 也走流式通道
    term.stream_token(token)


def print_reasoning_header() -> None:
    term.print("\n[dim]── Reasoning ──────────────────────────────[/dim]")


def print_reasoning_footer() -> None:
    term.print("[dim]──────────────────────────────────────────[/dim]\n")


def print_stats(summary: str) -> None:
    term.print(f"\n[dim]─── {summary} ───[/dim]")


def print_skill_loaded(skill_name: str) -> None:
    term.print(f"[dim]📚 Skill loaded: {skill_name}[/dim]")


def print_info(msg: str) -> None:
    term.print(f"[blue]ℹ[/blue]  {msg}")


def print_warning(msg: str) -> None:
    term.print(f"[yellow]⚠[/yellow]  {msg}")


def print_error(msg: str) -> None:
    term.print(f"[red]✗[/red]  {msg}")


def print_success(msg: str) -> None:
    term.print(f"[green]✓[/green]  {msg}")


def print_diff(diff_text: str) -> None:
    term.syntax(diff_text, "diff", theme="ansi_dark", background_color="default")


def print_markdown(md: str) -> None:
    term.markdown(md)


def print_interrupt() -> None:
    term.print("\n[yellow]⚡ Interrupted (Ctrl-C). Type 'exit' to quit.[/yellow]")


def print_retry_banner(turn: int) -> None:
    """重试上一轮时的分隔提示。"""
    term.print(f"\n[bold yellow]↻  Retrying turn {turn} — discarding previous response …[/bold yellow]")
    term.print("[dim]─────────────────────────────────────────────────────[/dim]")


def print_rollback_banner(turn_before: int, turn_after: int) -> None:
    """回退成功时的分隔提示。"""
    term.print(
        f"\n[bold magenta]◀  Rolled back: turn {turn_before} → {turn_after} "
        f"(last assistant response removed)[/bold magenta]"
    )
    term.print("[dim]─────────────────────────────────────────────────────[/dim]")


def print_user_prompt() -> None:
    term.print("\n[bold green]You[/bold green] > ", end="")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tool_icon(name: str) -> str:
    return {
        "bash": "⚡", "read_file": "📄", "write_file": "✏️",
        "create_file": "🆕", "delete_file": "🗑️", "list_dir": "📁",
        "glob": "🔍", "grep": "🔎", "patch_file": "🩹", "web_search": "🌐",
        "create_plan": "📋", "start_task": "▶", "complete_task": "✓",
        "fail_task": "✗", "add_task": "➕",
    }.get(name, "🔧")


def _tool_summary(tool_name: str, inp: dict) -> str:
    if tool_name == "bash":
        cmd = inp.get("command", "")
        return cmd[:80] + ("…" if len(cmd) > 80 else "")
    if tool_name in ("read_file", "write_file", "create_file", "delete_file", "patch_file"):
        return inp.get("path", inp.get("file_path", ""))
    if tool_name == "list_dir":
        return inp.get("path", ".")
    if tool_name in ("glob", "grep"):
        return inp.get("pattern", inp.get("query", ""))
    if tool_name == "web_search":
        return inp.get("query", "")
    if tool_name == "create_plan":
        return inp.get("goal", "")[:60]
    if tool_name in ("start_task", "complete_task", "fail_task"):
        return inp.get("task_id", "")
    if tool_name == "add_task":
        return inp.get("title", "")[:60]
    return ""


def _result_lang(tool_name: str, result: str) -> Optional[str]:
    if tool_name == "bash":
        return "text"
    r = result.strip()
    if r.startswith("{") or r.startswith("["):
        return "json"
    if r.startswith("diff ") or r.startswith("---"):
        return "diff"
    return None
