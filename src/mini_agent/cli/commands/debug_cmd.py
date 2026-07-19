"""
cli/commands/debug_cmd.py — /debug slash 命令

打印/导出当前 system prompt 与对话 history，便于分析调试
（例如排查 prompt 注入是否生效、history 是否按预期压缩/截断、
某条消息的 _type 归类是否正确等）。

用法：
  /debug                 等同于 /debug all
  /debug system          打印完整 system prompt（含估算 token 数）
  /debug history [n]     以表格形式打印 history（默认全部，内容截断预览）
  /debug history full [n]  同上，但不截断内容（可能刷屏，谨慎使用）
  /debug tokens          打印 system / history / 合计 三者各自的估算 token 数
                          （与 _agentic_loop 里 "[token] ~X tokens" 打印口径完全一致，
                          用于排查该数字里 system 和 history 各占多少）
  /debug all [n]         system + history + tokens 摘要一起打印
  /debug save [path]     将 system + 完整 history 落盘为 Markdown 文件，
                          不指定 path 时写入 <project_root>/.agent/debug/
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from mini_agent.agent import Agent
import mini_agent.ui.renderer as R

_PREVIEW_LEN = 200


def handle_debug_cmd(args: list[str], agent: Agent) -> None:
    sub = args[0].lower() if args else "all"
    rest = args[1:] if args else []

    if sub == "system":
        _print_system(agent)
    elif sub == "history":
        _print_history(agent, rest)
    elif sub == "tokens":
        _print_tokens(agent)
    elif sub == "all":
        _print_system(agent)
        _print_history(agent, rest)
        _print_tokens(agent)
    elif sub == "save":
        _save_debug_dump(agent, rest[0] if rest else None)
    else:
        R.print_error(
            "Usage: /debug [system | history [full] [n] | tokens | all [n] | save [path]]"
        )


# ── 内部实现 ─────────────────────────────────────────────────────────────

def _get_system_text(agent: Agent) -> str:
    """取当前 turn 会实际发给 LLM 的 system prompt（若已缓存则复用同一份）。"""
    try:
        return agent._build_system()
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.debug_cmd._get_system_text')
        return f"[error building system prompt: {e}]"


def _print_system(agent: Agent) -> None:
    from mini_agent.perception.token_counter import estimate_tokens

    text = _get_system_text(agent)
    tokens = estimate_tokens(text)
    R.console.print(
        f"\n[bold]── System Prompt ──[/bold]  "
        f"[dim]({len(text)} chars, ~{tokens} tokens)[/dim]\n"
    )
    R.console.print(text)


def _print_tokens(agent: Agent) -> None:
    """
    打印 system / history / 合计 三者各自的估算 token 数。

    刻意复用与 turn_loop.py::_agentic_loop() 里 "[token] ~X tokens" 打印
    完全相同的计算路径（先 convert_tool_use_to_text 再 estimate_messages_tokens），
    这样这里打印出来的 total 应该和运行中日志里看到的数字一致 —— 如果 total 和
    日志对不上，说明是别的原因（比如两次打印时机不同、历史又新增了消息），
    而不是这里的估算口径有问题。

    system 和 history 两个子项分开算，方便定位"到底是 system prompt 大，
    还是 history 大"：compact 只会影响 history，不会影响 system prompt。
    """
    from mini_agent.perception.token_counter import estimate_tokens, estimate_messages_tokens
    from mini_agent.llm.system_tool_call import convert_tool_use_to_text

    system_text = _get_system_text(agent)
    history = agent.history

    sys_tokens = estimate_tokens(system_text)
    msgs_for_estimate = convert_tool_use_to_text(history)
    total_tokens = estimate_messages_tokens(msgs_for_estimate, system_text)
    # 与 total 使用同一份 convert_tool_use_to_text 处理后的消息，
    # 只是不传 system，这样 total - hist_tokens 应该正好等于 sys_tokens
    # （允许 estimate_tokens("") == 0 时的浮点/取整误差在 1 以内）。
    hist_tokens = estimate_messages_tokens(msgs_for_estimate, "")

    ctx_window = None
    pct_str = ""
    try:
        ctx_window = agent._resolve_context_window()
        if ctx_window:
            pct_str = f"  [dim]({total_tokens / ctx_window:.0%} of {ctx_window:,})[/dim]"
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.debug_cmd._print_tokens')
        pass

    R.console.print("\n[bold]── Token Breakdown ──[/bold]\n")
    R.console.print(f"  system prompt : ~{sys_tokens:,} tokens")
    R.console.print(f"  history ({len(history)} msgs): ~{hist_tokens:,} tokens")
    R.console.print(f"  [bold]total{'':<6}[/bold]: ~{total_tokens:,} tokens{pct_str}")
    if sys_tokens > hist_tokens:
        R.console.print(
            "\n[dim]提示：system prompt 占比更大。/compact 只会压缩 history，"
            "不会缩小 system prompt（基础指令 + 工具 schema + 重附的 skill 内容等），"
            "如果 total 压缩后仍然偏高，多半是这部分没降。可用 /debug system "
            "查看具体内容找出占用大头。[/dim]"
        )


def _parse_history_args(rest: list[str]) -> tuple[bool, Optional[int]]:
    full = False
    n: Optional[int] = None
    for a in rest:
        if a in ("full", "-f", "--full"):
            full = True
        elif a.isdigit():
            n = int(a)
    return full, n


def _content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.cli.commands.debug_cmd._content_to_text')
        return str(content)


def _print_history(agent: Agent, rest: list[str]) -> None:
    from mini_agent.perception.token_counter import estimate_tokens
    from rich.table import Table
    from rich import box as rbox

    full, n = _parse_history_args(rest)
    history = agent.history
    total = len(history)

    if not history:
        R.print_info("History is empty.")
        return

    shown = history[-n:] if n else history

    R.console.print(
        f"\n[bold]── History ──[/bold]  [dim]({total} messages total"
        + (f", showing last {len(shown)}" if n else "")
        + ")[/dim]\n"
    )

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("#", width=4, justify="right")
    t.add_column("role", width=10)
    t.add_column("type", width=16)
    t.add_column("tokens", width=7, justify="right")
    t.add_column("content", overflow="fold")

    offset = total - len(shown)
    for i, msg in enumerate(shown):
        idx = offset + i
        role = str(msg.get("role", "?"))
        mtype = str(msg.get("_type", "-"))
        text = _content_to_text(msg.get("content", ""))
        tok = estimate_tokens(text)
        preview = text if full else (
            text[:_PREVIEW_LEN] + "…" if len(text) > _PREVIEW_LEN else text
        )
        preview = preview.replace("\n", "\\n")
        t.add_row(str(idx), role, mtype, str(tok), preview)

    R.console.print(t)


def _save_debug_dump(agent: Agent, path_arg: Optional[str]) -> None:
    from mini_agent.perception.token_counter import estimate_tokens, estimate_messages_tokens

    system_text = _get_system_text(agent)
    history = agent.history

    if path_arg:
        out_path = Path(path_arg).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        try:
            from mini_agent.storage.paths import AgentPaths
            out_dir = AgentPaths(agent.cfg.project_root).workdir_dir / "debug"
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.commands.debug_cmd._save_debug_dump')
            out_dir = Path(".agent") / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        import time
        out_path = out_dir / f"debug_dump_{int(time.time())}.md"

    total_tokens = estimate_messages_tokens(history, system_text)

    lines: list[str] = []
    lines.append(f"# mini_agent debug dump\n")
    lines.append(f"- session: {agent.session_id or '(no session)'}")
    lines.append(f"- model: {agent.cfg.model}")
    lines.append(f"- system tokens: ~{estimate_tokens(system_text)}")
    lines.append(f"- history messages: {len(history)}")
    lines.append(f"- total tokens (system + history, rough estimate): ~{total_tokens}\n")

    lines.append("## System Prompt\n")
    lines.append("```")
    lines.append(system_text)
    lines.append("```\n")

    lines.append("## History\n")
    for i, msg in enumerate(history):
        role = msg.get("role", "?")
        mtype = str(msg.get("_type", "-"))
        content = msg.get("content", "")
        if isinstance(content, (list, dict)):
            content_text = json.dumps(content, ensure_ascii=False, indent=2)
        else:
            content_text = str(content)
        lines.append(f"### [{i}] role={role} type={mtype}\n")
        lines.append("```")
        lines.append(content_text)
        lines.append("```\n")

    try:
        out_path.write_text("\n".join(lines), encoding="utf-8")
        R.print_success(f"Debug dump saved → {out_path}")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.cli.commands.debug_cmd._save_debug_dump')
        R.print_error(f"Failed to save debug dump: {e}")