"""cli/commands/events_cmd.py — `mini-agent events` 独立命令行入口

见 `next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md` Sprint 2-2：
"一个简单 CLI：`mini_agent events trace <correlation_id>`，按时间顺序打印
一次 Goal 执行的完整事件链"。

用法（与 `experience`/`projects`/`workflow` 等短路子命令方式完全一致，见
`cli/app.py::main()` 里的对应分支）：
    mini-agent events trace <correlation_id>
    mini-agent events list [--limit N]

只读检索，不提供写入/删除子命令——Event 的写入只应该发生在
`goal_mode/runner.py` 的 publish() 接入点（见 Phase 2 Sprint 2-1），CLI
层不提供绕过该接入点直接写入的入口。落盘依赖 `run()` 里挂载的
`ensure_event_log_subscribed()`（见 `core/event_log_store.py`），如果目标
项目从未跑过任何 Goal 执行，`.agent/events.jsonl` 不存在，命令会给出明确
提示而不是报错。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Optional


def _print(msg: str) -> None:
    print(msg)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _parse_limit(args: List[str], default: int = 20) -> tuple[int, List[str]]:
    """从参数列表里取出可选的 `--limit N`，返回 (limit, 剩余参数)。"""
    limit = default
    rest: List[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                _err(f"无效的 --limit 值：{args[i + 1]!r}")
            i += 2
            continue
        rest.append(args[i])
        i += 1
    return limit, rest


def _render_event(d: dict) -> str:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d.get("at", 0.0))) if d.get("at") else "-"
    causation = d.get("causation_id") or "-"
    return (
        f"[{ts}] kind={d.get('kind')} id={d.get('id')} "
        f"causation_id={causation} actor={d.get('actor') or '-'}\n"
        f"    payload: {d.get('payload')}"
    )


def _cmd_trace(args: List[str], project_root: Path) -> int:
    if not args:
        _err("用法: mini-agent events trace <correlation_id>")
        return 1
    correlation_id = args[0].strip()
    if not correlation_id:
        _err("用法: mini-agent events trace <correlation_id>")
        return 1

    from mini_agent.core.event_log_store import EventLogStore
    from mini_agent.storage.paths import AgentPaths

    store = EventLogStore(path=AgentPaths(project_root=project_root).workdir_event_log)
    if not store.path.exists():
        _print(
            f"当前项目还没有任何事件落盘记录（{store.path} 不存在）。"
            "只有跑过至少一次 Goal 执行之后才会产生（见 goal_mode/runner.py"
            " 的 run() 接入点）。"
        )
        return 0

    events = store.trace(correlation_id)
    if not events:
        _print(f"未找到 correlation_id={correlation_id!r} 对应的事件链。")
        return 0
    _print(f"correlation_id={correlation_id} 共 {len(events)} 个事件（按发生顺序）：")
    _print("")
    for d in events:
        _print(_render_event(d))
        _print("")
    return 0


def _cmd_list(args: List[str], project_root: Path) -> int:
    limit, _rest = _parse_limit(args)

    from mini_agent.core.event_log_store import EventLogStore
    from mini_agent.storage.paths import AgentPaths

    store = EventLogStore(path=AgentPaths(project_root=project_root).workdir_event_log)
    if not store.path.exists():
        _print(f"当前项目还没有任何事件落盘记录（{store.path} 不存在）。")
        return 0

    all_events = list(reversed(store.all()))[:limit]
    if not all_events:
        _print("事件落盘文件存在但为空。")
        return 0
    for d in all_events:
        corr = d.get("correlation_id") or "-"
        _print(f"correlation_id={corr}  " + _render_event(d))
        _print("")
    return 0


_SUBCOMMANDS = {
    "trace": _cmd_trace,
    "list": _cmd_list,
}


def run_events_cli(argv: List[str], project_root: Optional[Path] = None) -> int:
    """`mini-agent events <sub> ...` 的入口，由 `cli/app.py::main()` 短路调用。"""
    root = project_root or Path.cwd()

    if not argv or argv[0] in ("-h", "--help"):
        _print(__doc__ or "")
        return 0

    sub, rest = argv[0], argv[1:]
    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        _err(f"未知子命令 '{sub}'。可用: {', '.join(sorted(_SUBCOMMANDS))}")
        return 1
    return handler(rest, root)
