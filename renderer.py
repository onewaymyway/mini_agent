"""
Terminal output renderer.
Handles streaming text, tool call display, markdown, diffs, and status lines.
"""

from __future__ import annotations

import sys
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich import box

console = Console(highlight=False)
err_console = Console(stderr=True)


# ── Streaming text ─────────────────────────────────────────────────────────────

class StreamWriter:
    """
    Write streaming text tokens to stdout.
    Automatically suppresses <tool_use>...</tool_use> blocks from display
    (they will be parsed from the full buffered text by postprocess_response).
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._started = False
        # State for tool_use block suppression
        self._suppress = False          # currently inside a <tool_use> block
        self._pending: str = ""         # partial tag accumulation

    def write(self, token: str) -> None:
        self._buffer.append(token)
        # Process token through suppression filter
        display = self._filter(token)
        if not display:
            return
        if not self._started:
            console.print()  # blank line before first visible token
            self._started = True
        print(display, end="", flush=True)

    def _filter(self, token: str) -> str:
        """
        Filter out <tool_use>...</tool_use> blocks from display output.
        Returns the portion of token that should be printed (may be empty).
        """
        result = []
        # Accumulate pending + new token for tag detection
        text = self._pending + token
        self._pending = ""

        i = 0
        while i < len(text):
            if self._suppress:
                # Look for closing tag
                end = text.find("</tool_use>", i)
                if end == -1:
                    # Not found yet — keep suppressing, save tail as pending in case tag spans tokens
                    tail = text[i:]
                    # Keep up to 12 chars as pending (len("</tool_use>") = 11)
                    if len(tail) <= 11:
                        self._pending = tail
                    # else discard (we're mid-block with no tag boundary)
                    i = len(text)
                else:
                    # Found closing tag — exit suppress mode
                    self._suppress = False
                    i = end + len("</tool_use>")
            else:
                # Look for opening tag
                start = text.find("<tool_use>", i)
                if start == -1:
                    # No opening tag — keep up to 10 chars as pending in case tag spans tokens
                    visible = text[i:]
                    if len(visible) > 10:
                        result.append(visible[:-10])
                        self._pending = visible[-10:]
                    else:
                        self._pending = visible
                    i = len(text)
                elif start > i:
                    result.append(text[i:start])
                    self._suppress = True
                    i = start + len("<tool_use>")
                else:
                    self._suppress = True
                    i = start + len("<tool_use>")

        return "".join(result)

    def flush(self) -> str:
        # Flush any pending text (might be a partial non-tag string)
        if self._pending and not self._suppress:
            print(self._pending, end="", flush=True)
            self._pending = ""
        full = "".join(self._buffer)
        if self._buffer:
            print()  # final newline
        self._buffer.clear()
        self._started = False
        self._suppress = False
        self._pending = ""
        return full


# ── Tool events ────────────────────────────────────────────────────────────────

def print_tool_call(tool_name: str, tool_input: dict, verbose: bool = False) -> None:
    icon = _tool_icon(tool_name)
    summary = _tool_summary(tool_name, tool_input)
    console.print(f"\n{icon} [bold cyan]{tool_name}[/bold cyan]  [dim]{summary}[/dim]")
    if verbose:
        import json
        console.print(
            Syntax(json.dumps(tool_input, indent=2), "json", theme="ansi_dark", line_numbers=False)
        )


def print_tool_result(tool_name: str, result: str, truncate: int = 2000) -> None:
    if not result or not result.strip():
        console.print("  [dim](empty result)[/dim]")
        return
    display = result if len(result) <= truncate else result[:truncate] + "\n…[truncated]"
    lang = _result_lang(tool_name, result)
    if lang:
        console.print(Syntax(display, lang, theme="ansi_dark", line_numbers=False, background_color="default"))
    else:
        console.print(Text(display, style="dim"))


def print_tool_error(tool_name: str, error: str) -> None:
    console.print(f"  [red]✗ {tool_name} error:[/red] {error}")


# ── Status lines ───────────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    console.rule(f"[bold blue]{title}[/bold blue]")


def print_user_prompt() -> None:
    console.print("\n[bold green]You[/bold green] > ", end="")


def print_assistant_prefix() -> None:
    console.print("\n[bold blue]Claude[/bold blue]", end="")


def print_reasoning(token: str) -> None:
    """流式打印思维链 token（暗色显示，与正文区分）。"""
    print(token, end="", flush=True)


def print_reasoning_header() -> None:
    """思维链开始前打印分隔标题。"""
    console.print()
    console.print("[dim]── Reasoning ──────────────────────────────[/dim]")


def print_reasoning_footer() -> None:
    """思维链结束后打印分隔线。"""
    console.print()
    console.print("[dim]──────────────────────────────────────────[/dim]")
    console.print()


def print_stats(summary: str) -> None:
    console.print(f"\n[dim]─── {summary} ───[/dim]")


def print_skill_loaded(skill_name: str) -> None:
    console.print(f"[dim]📚 Skill loaded: {skill_name}[/dim]")


def print_info(msg: str) -> None:
    console.print(f"[blue]ℹ[/blue]  {msg}")


def print_warning(msg: str) -> None:
    console.print(f"[yellow]⚠[/yellow]  {msg}")


def print_error(msg: str) -> None:
    console.print(f"[red]✗[/red]  {msg}")


def print_success(msg: str) -> None:
    console.print(f"[green]✓[/green]  {msg}")


def print_diff(diff_text: str) -> None:
    console.print(Syntax(diff_text, "diff", theme="ansi_dark", background_color="default"))


def print_markdown(md: str) -> None:
    console.print(Markdown(md))


def print_interrupt() -> None:
    console.print("\n[yellow]⚡ Interrupted (Ctrl-C). Type 'exit' to quit.[/yellow]")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _tool_icon(name: str) -> str:
    icons = {
        "bash": "⚡",
        "read_file": "📄",
        "write_file": "✏️",
        "create_file": "🆕",
        "delete_file": "🗑️",
        "list_dir": "📁",
        "glob": "🔍",
        "grep": "🔎",
        "patch_file": "🩹",
        "web_search": "🌐",
    }
    return icons.get(name, "🔧")


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
