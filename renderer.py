"""
Terminal output renderer.
Handles streaming text, tool call display, markdown, diffs, and status lines.

状态栏集成：
  所有可见输出（print_tool_call / print_info / StreamWriter 等）在打印前
  调用 _sb_before()，打印后调用 _sb_after()。
  这让状态栏能在 agent 运行过程中始终显示在底部，而正常输出显示在上方。
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


# ── 状态栏让路辅助 ─────────────────────────────────────────────────────────────

def _sb_before() -> None:
    """在打印任何内容前调用，让状态栏暂时让路。"""
    try:
        from orchestrator.status_bar import before_print
        before_print()
    except Exception:
        pass


def _sb_after() -> None:
    """打印完成后调用，把状态栏重绘到底部。"""
    try:
        from orchestrator.status_bar import after_print
        after_print()
    except Exception:
        pass


# ── Streaming text ─────────────────────────────────────────────────────────────

class StreamWriter:
    """
    Write streaming text tokens to stdout.
    Automatically suppresses <tool_use>...</tool_use> blocks from display.

    首个可见 token 到来时调用 _sb_before()（擦除状态栏），
    flush() 结束时调用 _sb_after()（重绘状态栏）。
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._started = False
        self._suppress = False
        self._pending: str = ""

    def write(self, token: str) -> None:
        self._buffer.append(token)
        display = self._filter(token)
        if not display:
            return
        if not self._started:
            _sb_before()           # ← 第一个可见字符前，擦除状态栏
            console.print()        # blank line before first token
            self._started = True
        print(display, end="", flush=True)

    def _filter(self, token: str) -> str:
        result = []
        text = self._pending + token
        self._pending = ""
        i = 0
        while i < len(text):
            if self._suppress:
                end = text.find("</tool_use>", i)
                if end == -1:
                    tail = text[i:]
                    if len(tail) <= 11:
                        self._pending = tail
                    i = len(text)
                else:
                    self._suppress = False
                    i = end + len("</tool_use>")
            else:
                start = text.find("<tool_use>", i)
                if start == -1:
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
        if full:
            _sb_after()            # ← 流式输出结束，重绘状态栏
        return full


# ── Tool events ────────────────────────────────────────────────────────────────

def print_tool_call(tool_name: str, tool_input: dict, verbose: bool = False) -> None:
    icon = _tool_icon(tool_name)
    summary = _tool_summary(tool_name, tool_input)
    _sb_before()
    console.print(f"\n{icon} [bold cyan]{tool_name}[/bold cyan]  [dim]{summary}[/dim]")
    if verbose:
        import json
        console.print(
            Syntax(json.dumps(tool_input, indent=2), "json", theme="ansi_dark", line_numbers=False)
        )
    _sb_after()


def print_tool_result(tool_name: str, result: str, truncate: int = 2000) -> None:
    if not result or not result.strip():
        _sb_before()
        console.print("  [dim](empty result)[/dim]")
        _sb_after()
        return
    display = result if len(result) <= truncate else result[:truncate] + "\n…[truncated]"
    lang = _result_lang(tool_name, result)
    _sb_before()
    if lang:
        console.print(Syntax(display, lang, theme="ansi_dark", line_numbers=False, background_color="default"))
    else:
        console.print(Text(display, style="dim"))
    _sb_after()


def print_tool_error(tool_name: str, error: str) -> None:
    _sb_before()
    console.print(f"  [red]✗ {tool_name} error:[/red] {error}")
    _sb_after()


# ── Status lines ───────────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    _sb_before()
    console.rule(f"[bold blue]{title}[/bold blue]")
    _sb_after()


def print_user_prompt() -> None:
    console.print("\n[bold green]You[/bold green] > ", end="")


def print_assistant_prefix(agent_name: str = "orzooo") -> None:
    console.print(f"\n[bold blue]{agent_name}[/bold blue]", end="")


def print_reasoning(token: str) -> None:
    """流式打印思维链 token（无需让路，已在 StreamWriter 流程内）。"""
    print(token, end="", flush=True)


def print_reasoning_header() -> None:
    _sb_before()
    console.print()
    console.print("[dim]── Reasoning ──────────────────────────────[/dim]")
    _sb_after()


def print_reasoning_footer() -> None:
    _sb_before()
    console.print()
    console.print("[dim]──────────────────────────────────────────[/dim]")
    console.print()
    _sb_after()


def print_stats(summary: str) -> None:
    _sb_before()
    console.print(f"\n[dim]─── {summary} ───[/dim]")
    _sb_after()


def print_skill_loaded(skill_name: str) -> None:
    _sb_before()
    console.print(f"[dim]📚 Skill loaded: {skill_name}[/dim]")
    _sb_after()


def print_info(msg: str) -> None:
    _sb_before()
    console.print(f"[blue]ℹ[/blue]  {msg}")
    _sb_after()


def print_warning(msg: str) -> None:
    _sb_before()
    console.print(f"[yellow]⚠[/yellow]  {msg}")
    _sb_after()


def print_error(msg: str) -> None:
    _sb_before()
    console.print(f"[red]✗[/red]  {msg}")
    _sb_after()


def print_success(msg: str) -> None:
    _sb_before()
    console.print(f"[green]✓[/green]  {msg}")
    _sb_after()


def print_diff(diff_text: str) -> None:
    _sb_before()
    console.print(Syntax(diff_text, "diff", theme="ansi_dark", background_color="default"))
    _sb_after()


def print_markdown(md: str) -> None:
    _sb_before()
    console.print(Markdown(md))
    _sb_after()


def print_interrupt() -> None:
    _sb_before()
    console.print("\n[yellow]⚡ Interrupted (Ctrl-C). Type 'exit' to quit.[/yellow]")
    _sb_after()


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
        "create_plan": "📋",
        "start_task": "▶",
        "complete_task": "✓",
        "fail_task": "✗",
        "add_task": "➕",
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
    if tool_name in ("create_plan",):
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
