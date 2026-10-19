#!/usr/bin/env python3
"""scripts/output_registry_link.py — 产出注册表 P2 命令行入口。

对应 next_doc/management_output_project_model_plan.md 第 9 章 P2。

用法（PYTHONPATH=src）：
    python scripts/output_registry_link.py --project <项目根> register <slug>
    python scripts/output_registry_link.py --project <项目根> mark-management <goal_id> [--title T]
    python scripts/output_registry_link.py --project <项目根> link <management_id> <slug> --role build|iterate|reference
    python scripts/output_registry_link.py --project <项目根> unlink <management_id> <slug> --role ...
    python scripts/output_registry_link.py --project <项目根> bind <slug> <management_id> --role ...
    python scripts/output_registry_link.py --project <项目根> derive <slug> --parent <goal_id> --title T [--role ...]
    python scripts/output_registry_link.py --project <项目根> show
    python scripts/output_registry_link.py --project <项目根> rebuild

只写 output_projects_root/_registry/；derive 额外在目标树中新增一个 goal 节点。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mini_agent.output_registry import (  # noqa: E402
    RegistryError,
    bind_floating_project,
    derive_management_from_project,
    link_management_output,
    list_links,
    load_index,
    mark_management_root,
    rebuild_index,
    register_project,
    unlink_management_output,
)
from mini_agent.perception.goal_backlog import GoalBacklog  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402


def _roles(p: argparse.ArgumentParser) -> None:
    p.add_argument("--role", required=True, choices=["build", "iterate", "reference"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="产出注册表 P2 操作入口")
    parser.add_argument("--project", default=".", help="项目根目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("register").add_argument("slug")

    m = sub.add_parser("mark-management")
    m.add_argument("goal_id")
    m.add_argument("--title", default="")

    lk = sub.add_parser("link")
    lk.add_argument("management_id")
    lk.add_argument("slug")
    _roles(lk)

    ul = sub.add_parser("unlink")
    ul.add_argument("management_id")
    ul.add_argument("slug")
    _roles(ul)

    bd = sub.add_parser("bind")
    bd.add_argument("slug")
    bd.add_argument("management_id")
    _roles(bd)

    dv = sub.add_parser("derive")
    dv.add_argument("slug")
    dv.add_argument("--parent", required=True)
    dv.add_argument("--title", required=True)
    dv.add_argument("--role", default="build", choices=["build", "iterate", "reference"])

    sub.add_parser("show")
    sub.add_parser("rebuild")

    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    try:
        if args.cmd == "register":
            print(json.dumps(register_project(project, args.slug), ensure_ascii=False, indent=2))
        elif args.cmd == "mark-management":
            print(json.dumps(mark_management_root(project, args.goal_id, args.title), ensure_ascii=False, indent=2))
        elif args.cmd == "link":
            print(json.dumps(link_management_output(project, args.management_id, args.slug, args.role),
                             ensure_ascii=False, indent=2))
        elif args.cmd == "unlink":
            print(json.dumps(unlink_management_output(project, args.management_id, args.slug, args.role),
                             ensure_ascii=False, indent=2))
        elif args.cmd == "bind":
            print(json.dumps(bind_floating_project(project, args.slug, args.management_id, args.role),
                             ensure_ascii=False, indent=2))
        elif args.cmd == "derive":
            backlog = GoalBacklog(AgentPaths(project_root=project))
            backlog.load()
            new_id = derive_management_from_project(project, args.slug, backlog, args.parent,
                                                    args.title, role=args.role)
            print(json.dumps({"management_id": new_id}, ensure_ascii=False, indent=2))
        elif args.cmd == "show":
            print(json.dumps({"index": load_index(project), "links": list_links(project)},
                             ensure_ascii=False, indent=2))
        elif args.cmd == "rebuild":
            print(json.dumps(rebuild_index(project), ensure_ascii=False, indent=2))
    except (RegistryError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
