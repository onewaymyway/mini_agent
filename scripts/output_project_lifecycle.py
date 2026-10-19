#!/usr/bin/env python3
"""scripts/output_project_lifecycle.py — 产出项目 P3 生命周期命令行入口。

对应 next_doc/management_output_project_model_plan.md 第 9 章 P3。

用法（PYTHONPATH=src）：
    python scripts/output_project_lifecycle.py --project <项目根> create <slug> [--title T] [--intro 文本]
    python scripts/output_project_lifecycle.py --project <项目根> publish <slug> <version> --confirm [--changelog T] [--goal <goal_id>]
    python scripts/output_project_lifecycle.py --project <项目根> reopen <goal_id> --reason 原因 --confirm

发布版本与重新打开管理项目都是用户确认的动作，必须带 --confirm。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mini_agent.output_registry import RegistryError  # noqa: E402
from mini_agent.output_registry.lifecycle import (  # noqa: E402
    create_output_project,
    publish_version,
    reopen_management_root,
)
from mini_agent.perception.goal_backlog import GoalBacklog  # noqa: E402
from mini_agent.storage.paths import AgentPaths  # noqa: E402


def _dump(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="产出项目 P3 生命周期操作")
    parser.add_argument("--project", default=".", help="项目根目录")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("slug")
    c.add_argument("--title", default="")
    c.add_argument("--intro", default="")

    p = sub.add_parser("publish")
    p.add_argument("slug")
    p.add_argument("version")
    p.add_argument("--changelog", default="")
    p.add_argument("--goal", default=None, help="来源管理项目（目标节点 id）")
    p.add_argument("--confirm", action="store_true", help="确认发布（必需）")

    r = sub.add_parser("reopen")
    r.add_argument("goal_id")
    r.add_argument("--reason", required=True)
    r.add_argument("--confirm", action="store_true", help="确认重新打开（必需）")

    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    try:
        if args.cmd == "create":
            _dump(create_output_project(project, args.slug, title=args.title, intro=args.intro))
        elif args.cmd == "publish":
            if not args.confirm:
                print("错误：发布版本需要 --confirm 确认", file=sys.stderr)
                return 3
            _dump(publish_version(project, args.slug, args.version,
                                  changelog=args.changelog, source_goal_id=args.goal))
        elif args.cmd == "reopen":
            if not args.confirm:
                print("错误：重新打开管理项目需要 --confirm 确认", file=sys.stderr)
                return 3
            backlog = GoalBacklog(AgentPaths(project_root=project))
            backlog.load()
            _dump(reopen_management_root(project, args.goal_id, args.reason, backlog))
    except (RegistryError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
