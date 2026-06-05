"""
tools/user_input.py — 用户输入工具

让 agent 可以主动向用户提问，获取反馈、确认、或补充信息。

工具：
  ask_user(question, hint)          — 开放式提问，返回用户的文本回答
  ask_user_confirm(question)        — 是/否确认，返回 True/False
  ask_user_choice(question, options)— 多选一，返回用户选择的选项文字
"""

from __future__ import annotations

import json
from typing import Optional

import sys
from . import tool
from mini_agent.ui.terminal import term


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
    term.print(f"\n[bold yellow]❓ Question from agent:[/bold yellow]")
    term.print(f"   [bold]{question}[/bold]")
    if hint:
        term.print(f"   [dim]{hint}[/dim]")

    # _enter_input_mode 确保提示文字渲染完毕、状态栏擦除后再阻塞
    term._enter_input_mode()
    try:
        sys.stdout.write("\nYour answer: ")
        sys.stdout.flush()
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
    finally:
        term._exit_input_mode()

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
    term.print(f"\n[bold yellow]❓ Confirmation needed:[/bold yellow]")
    term.print(f"   [bold]{question}[/bold]")

    default_char = "y" if default.lower() in ("yes", "y") else "n"
    hint_str = "[Y/n]" if default_char == "y" else "[y/N]"

    choice = term.confirm(
        prompt_lines=[],
        choices=hint_str,
        default=default_char,
    )
    confirmed = choice in ("y", "yes", "")
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
    term.print(f"\n[bold yellow]❓ Please choose:[/bold yellow]")
    term.print(f"   [bold]{question}[/bold]")
    if hint:
        term.print(f"   [dim]{hint}[/dim]")
    for i, opt in enumerate(options, 1):
        term.print(f"   [cyan]{i}.[/cyan] {opt}")

    choices_str = f"Enter number (1-{len(options)})"
    term._enter_input_mode()
    try:
        sys.stdout.write(f"\n  {choices_str} : ")
        sys.stdout.flush()
        while True:
            try:
                raw = input().strip()
            except (EOFError, KeyboardInterrupt):
                chosen = options[0]
                break
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    chosen = options[idx]
                    break
            # 也允许直接输入选项文字（模糊匹配）
            matches = [o for o in options if o.lower().startswith(raw.lower())]
            if len(matches) == 1:
                chosen = matches[0]
                break
            sys.stdout.write(f"  Please enter a number between 1 and {len(options)}: ")
            sys.stdout.flush()
    finally:
        term._exit_input_mode()

    term.print(f"[dim]User chose: {chosen}[/dim]")
    return json.dumps({"choice": chosen, "index": options.index(chosen)}, ensure_ascii=False)

