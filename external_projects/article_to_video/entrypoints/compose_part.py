"""entrypoints/compose_part.py — 章级合成（P5）：音轨 + 分段图层 + ffmpeg 编码 + V6 校验。

用法：python entrypoints/compose_part.py --id <项目目录名> [--part P01 | 全部]
前提：画面已渲染（P4）且与当前分镜一致；配音（P3）已完成。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video import cancel  # noqa: E402
from article_to_video.compose.ffrun import ComposeError  # noqa: E402
from article_to_video.compose.part import compose_part  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402
from article_to_video.planning.runner import load_spine  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="章级合成")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="项目目录名（位于项目集合目录下）")
    g.add_argument("--project", help="项目目录路径")
    p.add_argument("--part", default="all", help="章 ID（如 P01），默认全部")
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    cancel.bind(cancel.begin(pdir))
    try:
        spine = load_spine(pdir)
        if spine is None:
            raise ComposeError("还没有大纲")
        ids = [x["id"] for x in spine["parts"]] if args.part == "all" else [args.part]
        out = {}
        for pid in ids:
            info = compose_part(pdir, pid)
            out[pid] = {"duration": info["duration"], "segments": info["segments"],
                        "subtitles": len(info["subtitles"])}
    except (ComposeError, cancel.Cancelled, Exception) as exc:  # noqa: BLE001 — 统一输出错误文字
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "parts": out}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
