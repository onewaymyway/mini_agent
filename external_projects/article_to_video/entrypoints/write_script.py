"""entrypoints/write_script.py — 写口播稿（P2，需要模型）。

用法：python entrypoints/write_script.py --id <项目目录名> [--part P01 | 全部] [--force]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video import cancel  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.planning.errors import PlanError  # noqa: E402
from article_to_video.providers.text import make_text_llm  # noqa: E402
from article_to_video.script import writer as w  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="写口播稿（按章）")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="项目目录名（位于项目集合目录下）")
    g.add_argument("--project", help="项目目录路径")
    p.add_argument("--part", default="all", help="章 ID（如 P01），默认全部")
    p.add_argument("--force", action="store_true")
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    cancel.bind(cancel.begin(pdir))
    llm = make_text_llm("miniagent", pdir=pdir)
    try:
        if args.part == "all":
            done = w.write_all(pdir, llm, force=args.force)
        else:
            w.write_part(pdir, args.part, llm, force=args.force)
            done = [args.part]
    except (PlanError, cancel.Cancelled) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "parts": done}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
