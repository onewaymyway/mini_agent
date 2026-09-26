"""cli/commands/experience_cmd.py — `mini-agent experience` 独立命令行入口

见 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 2：
"一个 CLI 命令，如 `mini_agent experience search '<关键词>'`，能从存储里
检索出上一次执行的 `Experience`"。

用法（与 `projects`/`workflow` 等短路子命令方式完全一致，见
`cli/app.py::main()` 里的对应分支）：
    mini-agent experience search "<关键词>" [--limit N]
    mini-agent experience list [--limit N]

只读检索 + 列表，不提供写入/删除子命令——Experience 的写入只应该发生在
`goal_mode/runner.py` 的 Adapter 接入点（见 Sprint 1），CLI 层不提供
绕过该接入点直接写入的入口。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional


def _print(msg: str) -> None:
    print(msg)


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _parse_limit(args: List[str], default: int = 10) -> tuple[int, List[str]]:
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


def _render_experience(exp) -> str:
    import time as _time

    ts = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(exp.created_at)) if exp.created_at else "-"
    return (
        f"[{ts}] status={exp.status} rounds={exp.rounds_used} goal={exp.goal_text!r}\n"
        f"    report: {exp.final_report}"
    )


def _cmd_search(args: List[str], project_root: Path) -> int:
    limit, rest = _parse_limit(args)
    query = " ".join(rest).strip()
    if not query:
        _err('用法: mini-agent experience search "<关键词>" [--limit N]')
        return 1

    from mini_agent.core.experience_store import ExperienceStore
    from mini_agent.storage.paths import AgentPaths

    store = ExperienceStore(path=AgentPaths(project_root=project_root).workdir_experience_store)
    results = store.search(query, limit=limit)
    if not results:
        _print(f"未找到与 {query!r} 相关的 Experience 记录。")
        return 0
    for exp in results:
        _print(_render_experience(exp))
        _print("")
    return 0


def _cmd_list(args: List[str], project_root: Path) -> int:
    limit, _rest = _parse_limit(args)

    from mini_agent.core.experience_store import ExperienceStore
    from mini_agent.storage.paths import AgentPaths

    store = ExperienceStore(path=AgentPaths(project_root=project_root).workdir_experience_store)
    all_experiences = list(reversed(store.all()))[:limit]
    if not all_experiences:
        _print("当前项目还没有任何 Experience 记录（Goal 执行结束后才会产生）。")
        return 0
    for exp in all_experiences:
        _print(_render_experience(exp))
        _print("")
    return 0


_SUBCOMMANDS = {
    "search": _cmd_search,
    "list": _cmd_list,
}


def run_experience_cli(argv: List[str], project_root: Optional[Path] = None) -> int:
    """`mini-agent experience <sub> ...` 的入口，由 `cli/app.py::main()` 短路调用。"""
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
