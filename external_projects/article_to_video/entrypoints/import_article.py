"""entrypoints/import_article.py — 导入一篇 Markdown 文章，登记为新项目（P0，不调用模型）。

用法：python entrypoints/import_article.py --file article.md [--author 名] [--title 标题]
      [--watermark-text 文字 --watermark-position top_right --watermark-opacity 0.35]
      [--projects-root 目录]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402  (同目录引导)

from article_to_video.importer import ArticleImportError, import_article  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="导入 Markdown 文章为新项目")
    p.add_argument("--file", required=True, help="Markdown 文章路径")
    p.add_argument("--title", default="", help="标题（留空取首个一级标题）")
    p.add_argument("--author", default="", help="作者名（留空时尝试从文章读取）")
    p.add_argument("--mode", default="condensed")
    p.add_argument("--target-minutes", type=float, default=10.0)
    p.add_argument("--orientation", default="landscape")
    p.add_argument("--resolution", default="1080P")
    p.add_argument("--watermark-text", default="")
    p.add_argument("--watermark-position", default="top_right")
    p.add_argument("--watermark-opacity", type=float, default=0.35)
    p.add_argument("--projects-root", default="", help="项目集合目录（默认 <项目>/projects）")
    args = p.parse_args(argv)

    path = Path(args.file)
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"无法读取文章（需 UTF-8）：{exc}"}, ensure_ascii=False))
        return 2

    root = Path(args.projects_root).expanduser() if args.projects_root else projects_root()
    try:
        result = import_article(
            root,
            source_text=text,
            source_name=path.name,
            title=args.title,
            author=args.author,
            branding={
                "watermark": {
                    "enabled": bool(args.watermark_text.strip()),
                    "text": args.watermark_text,
                    "position": args.watermark_position,
                    "opacity": args.watermark_opacity,
                },
            },
            settings={
                "mode": args.mode,
                "target_minutes": args.target_minutes,
                "video": {"orientation": args.orientation, "resolution": args.resolution},
            },
        )
    except ArticleImportError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
