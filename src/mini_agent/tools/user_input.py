"""
tools/user_input.py — 用户输入工具

让 agent 可以主动向用户提问，获取反馈、确认、或补充信息。

工具：
  ask_user(question, hint)          — 开放式提问，返回用户的文本回答
  ask_user_confirm(question)        — 是/否确认，返回 True/False
  ask_user_choice(question, options)— 多选一，返回用户选择的选项文字

daemon 适配：
  三个工具都通过 mini_agent.interaction.ask() 走"CLI + HTTP 双路"提问——
  daemon 模式下会把问题广播给 connected 的远程客户端（INTERACTION_REQ 事件），
  客户端答完之后 POST /v1/interactions/{req_id} 传回来；本地终端（如果有）
  同时也能直接回答，谁先答就用谁的。之前这里是裸调用 input()/sys.stdin，
  daemon 进程没有本地终端时会永久阻塞或立刻拿到空答案，远程客户端完全看
  不到问题——这是本次改造要修的问题之一。
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from . import tool
from mini_agent.ui.terminal import term
from mini_agent import interaction


@tool(
    name="ask_user",
    description=(
        "Ask the user a question and wait for their text response. "
        "Use this when you need clarification, additional information, "
        "or user preference before proceeding. "
        "The agent will pause and display the question to the user."
    ),
    schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question to ask the user (displayed prominently)",
            },
            "hint": {
                "type": "string",
                "description": "Optional hint or context shown below the question (e.g. expected format)",
            },
        },
        "required": ["question"],
    },
    requires_approval=False,
)
def ask_user(question: str, hint: str = "") -> str:
    """向用户提开放式问题，返回用户输入的文本。"""
    term.print(f"\n[bold yellow]\u2753 Question from agent:[/bold yellow]")
    term.print(f"   [bold]{question}[/bold]")
    if hint:
        term.print(f"   [dim]{hint}[/dim]")
    term.print("[dim]  \uff08HTTP 端也已收到此问题，可在 Web/connected 客户端回答\uff09[/dim]")

    def _local_read(interrupt_event) -> Optional[dict]:
        term._enter_input_mode()
        try:
            sys.stdout.write("\nYour answer: ")
            sys.stdout.flush()
            answer = interaction.interruptible_readline(interrupt_event)
        finally:
            term._exit_input_mode()
        if answer is None:
            return None
        return {"answer": answer}

    result = interaction.ask(
        "ask_user", {"question": question, "hint": hint}, _local_read,
    )
    answer = (result or {}).get("answer", "") or ""

    term.print(f"[dim]User answered: {answer[:100]}[/dim]")
    return json.dumps({"answer": answer}, ensure_ascii=False)


@tool(
    name="ask_user_confirm",
    description=(
        "Ask the user a yes/no question. Returns true if the user confirms, false otherwise. "
        "Use for critical decisions that need explicit user approval before proceeding."
    ),
    schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The yes/no question to ask",
            },
            "default": {
                "type": "string",
                "enum": ["yes", "no"],
                "description": "Default answer if user just presses Enter (default: 'yes')",
            },
        },
        "required": ["question"],
    },
    requires_approval=False,
)
def ask_user_confirm(question: str, default: str = "yes") -> str:
    """向用户提是/否确认问题，返回 {"confirmed": true/false}。"""
    term.print(f"\n[bold yellow]\u2753 Confirmation needed:[/bold yellow]")
    term.print(f"   [bold]{question}[/bold]")
    term.print("[dim]  \uff08HTTP 端也已收到此确认请求，可在 Web/connected 客户端回答\uff09[/dim]")

    default_char = "y" if default.lower() in ("yes", "y") else "n"
    hint_str = "[Y/n]" if default_char == "y" else "[y/N]"

    def _local_read(interrupt_event) -> Optional[dict]:
        try:
            choice = term.confirm(
                prompt_lines=[],
                choices=hint_str,
                default=default_char,
                interrupt_event=interrupt_event,
            )
        except Exception:
            return None
        if interrupt_event.is_set():
            return None
        confirmed = choice in ("y", "yes", "")
        return {"confirmed": confirmed}

    result = interaction.ask(
        "ask_user_confirm",
        {"question": question, "default": default_char},
        _local_read,
    )
    if result and "confirmed" in result:
        confirmed = bool(result["confirmed"])
    elif result and "answer" in result:
        confirmed = str(result["answer"]).strip().lower() in ("y", "yes", "true", "1", "confirm", "确认", "是")
    else:
        confirmed = default_char == "y"

    term.print(f"[dim]User {'confirmed' if confirmed else 'declined'}[/dim]")
    return json.dumps({"confirmed": confirmed}, ensure_ascii=False)


@tool(
    name="ask_user_choice",
    description=(
        "Present the user with a list of options to choose from. "
        "Returns the text of the chosen option. "
        "Use when there are multiple distinct paths and user preference matters."
    ),
    schema={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The question or prompt shown above the options",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of options to choose from (2-10 items)",
                "minItems": 2,
                "maxItems": 10,
            },
            "hint": {
                "type": "string",
                "description": "Optional additional context",
            },
        },
        "required": ["question", "options"],
    },
    requires_approval=False,
)
def ask_user_choice(question: str, options: list, hint: str = "") -> str:
    """向用户展示多个选项，返回用户选择的选项。"""
    term.print(f"\n[bold yellow]\u2753 Please choose:[/bold yellow]")
    term.print(f"   [bold]{question}[/bold]")
    if hint:
        term.print(f"   [dim]{hint}[/dim]")
    for i, opt in enumerate(options, 1):
        term.print(f"   [cyan]{i}.[/cyan] {opt}")
    term.print("[dim]  \uff08HTTP 端也已收到此选择请求，可在 Web/connected 客户端回答\uff09[/dim]")

    choices_str = f"Enter number (1-{len(options)})"

    def _local_read(interrupt_event) -> Optional[dict]:
        term._enter_input_mode()
        try:
            sys.stdout.write(f"\n  {choices_str} : ")
            sys.stdout.flush()
            while True:
                raw = interaction.interruptible_readline(interrupt_event)
                if raw is None:
                    return None
                raw = raw.strip()
                if raw.isdigit():
                    idx = int(raw) - 1
                    if 0 <= idx < len(options):
                        return {"choice_index": idx}
                matches = [o for o in options if o.lower().startswith(raw.lower())]
                if len(matches) == 1:
                    return {"choice_index": options.index(matches[0])}
                sys.stdout.write(f"  Please enter a number between 1 and {len(options)}: ")
                sys.stdout.flush()
        finally:
            term._exit_input_mode()

    result = interaction.ask(
        "ask_user_choice",
        {"question": question, "options": options, "hint": hint},
        _local_read,
    )

    chosen = options[0]
    if result:
        if "choice_index" in result and result["choice_index"] is not None:
            idx = int(result["choice_index"])
            if 0 <= idx < len(options):
                chosen = options[idx]
        elif result.get("answer"):
            raw = str(result["answer"]).strip()
            if raw.isdigit() and 0 <= int(raw) - 1 < len(options):
                chosen = options[int(raw) - 1]
            else:
                matches = [o for o in options if o.lower().startswith(raw.lower())]
                if len(matches) == 1:
                    chosen = matches[0]

    term.print(f"[dim]User chose: {chosen}[/dim]")
    return json.dumps({"choice": chosen, "index": options.index(chosen)}, ensure_ascii=False)
