"""
renderer.py — 终端输出

所有输出都通过 printing_context() 包裹，确保：
  擦除状态栏 → 输出内容 → 重绘状态栏

stdout 上只有主线程在写，完全顺序，无竞态。
"""

from __future__ import annotations

import sys
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text

from orchestrator.status_bar import printing_context

console = Console(highlight=False)


# ── StreamWriter ───────────────────────────────────────────────────────────────

class StreamWriter:
    """
    流式写入 LLM token。

    第一个可见 token 到来时擦除状态栏，flush() 完成后重绘状态栏。
    整个流式输出过程是一个大的 printing_context。
    """

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._erased = False
        self._had_output = False
        self._suppress = False
        self._pending = ""

    def write(self, token: str) -> None:
        self._buffer.append(token)
        display = self._filter(token)
        if not display:
            return
        if not self._erased:
            # 第一个可见 token：手动擦除状态栏（不用 context manager，因为是流式的）
            from orchestrator.status_bar import _erase
            _erase()
            self._erased = True
            self._had_output = True
        sys.stdout.write(display)
        sys.stdout.flush()

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
                    self._pending = tail if len(tail) <= 11 else ""
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
            sys.stdout.write(self._pending)
            self._pending = ""
        full = "".join(self._buffer)
        if self._had_output:
            sys.stdout.write("\n")
            sys.stdout.flush()
            # 流结束：重绘状态栏
            from orchestrator.status_bar import _draw
            _draw()
        self._buffer.clear()
        self._erased = False
        self._had_output = False
        self._suppress = False
        self._pending = ""
        return full


# ── 工具输出 ───────────────────────────────────────────────────────────────────

def print_tool_call(tool_name: str, tool_input: dict, verbose: bool = False) -> None:
    icon = _tool_icon(tool_name)
    summary = _tool_summary(tool_name, tool_input)
    with printing_context():
        console.print(f"\n{icon} [bold cyan]{tool_name}[/bold cyan]  [dim]{summary}[/dim]")
        if verbose:
            import json
            console.print(Syntax(json.dumps(tool_input, indent=2), "json",
                                 theme="ansi_dark", line_numbers=False))


def print_tool_result(tool_name: str, result: str, truncate: int = 2000) -> None:
    with printing_context():
        if not result or not result.strip():
            console.print("  [dim](empty result)[/dim]")
            return
        display = result if len(result) <= truncate else result[:truncate] + "\n…[truncated]"
        lang = _result_lang(tool_name, result)
        if lang:
            console.print(Syntax(display, lang, theme="ansi_dark",
                                 line_numbers=False, background_color="default"))
        else:
            console.print(Text(display, style="dim"))


def print_tool_error(tool_name: str, error: str) -> None:
    with printing_context():
        console.print(f"  [red]✗ {tool_name} error:[/red] {error}")


# ── 状态和提示 ─────────────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    with printing_context():
        console.rule(f"[bold blue]{title}[/bold blue]")


def print_assistant_prefix(agent_name: str = "orzooo") -> None:
    # 只擦除，不重绘——后续 StreamWriter 负责整个流式输出完成后重绘
    from orchestrator.status_bar import _erase
    _erase()
    console.print(f"\n[bold blue]{agent_name}[/bold blue] ", end="")


def print_reasoning(token: str) -> None:
    sys.stdout.write(token)
    sys.stdout.flush()


def print_reasoning_header() -> None:
    with printing_context():
        console.print()
        console.print("[dim]── Reasoning ──────────────────────────────[/dim]")


def print_reasoning_footer() -> None:
    with printing_context():
        console.print("[dim]──────────────────────────────────────────[/dim]")
        console.print()


def print_stats(summary: str) -> None:
    with printing_context():
        console.print(f"\n[dim]─── {summary} ───[/dim]")


def print_skill_loaded(skill_name: str) -> None:
    with printing_context():
        console.print(f"[dim]📚 Skill loaded: {skill_name}[/dim]")


def print_info(msg: str) -> None:
    with printing_context():
        console.print(f"[blue]ℹ[/blue]  {msg}")


def print_warning(msg: str) -> None:
    with printing_context():
        console.print(f"[yellow]⚠[/yellow]  {msg}")


def print_error(msg: str) -> None:
    with printing_context():
        console.print(f"[red]✗[/red]  {msg}")


def print_success(msg: str) -> None:
    with printing_context():
        console.print(f"[green]✓[/green]  {msg}")


def print_diff(diff_text: str) -> None:
    with printing_context():
        console.print(Syntax(diff_text, "diff", theme="ansi_dark", background_color="default"))


def print_markdown(md: str) -> None:
    with printing_context():
        console.print(Markdown(md))


def print_interrupt() -> None:
    with printing_context():
        console.print("\n[yellow]⚡ Interrupted (Ctrl-C). Type 'exit' to quit.[/yellow]")


def print_user_prompt() -> None:
    console.print("\n[bold green]You[/bold green] > ", end="")


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
