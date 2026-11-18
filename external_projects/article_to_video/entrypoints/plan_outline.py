"""entrypoints/plan_outline.py — 生成大纲（P1）：分节摘要 + 主线大纲。

用法：python entrypoints/plan_outline.py --id <项目目录名> --engine rules|miniagent [--force]
      python entrypoints/plan_outline.py --project <项目目录> --engine rules
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.planning.errors import PlanError  # noqa: E402
from article_to_video.planning.runner import plan_outline  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="生成大纲（分节摘要 + 主线）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="项目目录名（位于项目集合目录下）")
    g.add_argument("--project", help="项目目录路径")
    p.add_argument("--engine", default="rules", choices=["rules", "miniagent"])
    p.add_argument("--force", action="store_true", help="覆盖已确认的大纲")
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    llm = None
    engine = "rules"
    if args.engine == "miniagent":
        from article_to_video.providers.text import make_text_llm
        llm, engine = make_text_llm("miniagent"), "llm"
    try:
        spine = plan_outline(pdir, engine=engine, llm=llm, force=args.force)
    except PlanError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "ok": True,
        "engine": spine["engine"],
        "thesis": spine["thesis"],
        "target_minutes": spine["target_minutes"],
        "parts": [{"id": x["id"], "title": x["title"], "sections": len(x["section_ids"]),
                   "budget_minutes": x["budget_minutes"]} for x in spine["parts"]],
        "cuts": spine["cuts"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
