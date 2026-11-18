"""entrypoints/plan_storyboard.py — 生成分镜（P4，规则版，不调用模型）。

用法：python entrypoints/plan_storyboard.py --id <项目目录名> [--part P01 | 全部] [--force] [--engine rules|llm]
前提：大纲与口播稿均已确认（C0、C1）。已确认且未过期的分镜会被复用；--force 覆盖。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.planning.errors import PlanError  # noqa: E402
from article_to_video.planning.runner import load_spine  # noqa: E402
from article_to_video.visual import storyboard as sbm  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="生成分镜（规则版）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="项目目录名（位于项目集合目录下）")
    g.add_argument("--project", help="项目目录路径")
    p.add_argument("--part", default="all", help="章 ID（如 P01），默认全部")
    p.add_argument("--force", action="store_true", help="覆盖已有分镜（包括未确认的人工编辑）")
    p.add_argument("--engine", default="rules", choices=["rules", "llm"], help="rules 离线；llm 需要模型")
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    try:
        spine = load_spine(pdir)
        if spine is None:
            raise PlanError("还没有大纲")
        ids = [x["id"] for x in spine["parts"]] if args.part == "all" else [args.part]
        llm = None
        if args.engine == "llm":
            from article_to_video import cancel
            from article_to_video.providers.text import make_text_llm
            cancel.bind(cancel.begin(pdir))
            llm = make_text_llm("miniagent", pdir=pdir)
        out = {}
        for pid in ids:
            sb = sbm.plan_storyboard(pdir, pid, force=args.force, engine=args.engine, llm=llm)
            out[pid] = {"scenes": len(sb["scenes"]), "images": sb["images_planned"],
                        "allowance": sb["image_allowance"], "errors": sb.get("errors", [])}
    except (PlanError, Exception) as exc:  # noqa: BLE001 — 命令行统一输出错误文字
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "parts": out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
