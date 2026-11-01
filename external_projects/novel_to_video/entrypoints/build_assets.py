#!/usr/bin/env python
"""全书级素材：G-2 视觉/声音补全 与 G-3 定妆图生成。

用法：
  python entrypoints/build_assets.py <项目目录> [--stage text|portrait|all]
         [--ids char_001,loc_01] [--regenerate] [--all-entities] [--text-provider X] [--image-provider Y]

默认只处理缺失项（--all-entities 时重新处理全部）。输出 JSON；存在失败项时退出码为 1。
"""

from __future__ import annotations

import argparse
import json
import sys

import _common  # noqa: F401
from _common import force_utf8_stdio

from novel_to_video import tool_api


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="G-2 / G-3 全书级素材生成")
    ap.add_argument("project_dir")
    ap.add_argument("--stage", choices=["text", "portrait", "all"], default="all")
    ap.add_argument("--ids", default=None, help="逗号分隔的实体 id，默认全部")
    ap.add_argument("--regenerate", action="store_true", help="对 --ids 强制重新生成定妆图")
    ap.add_argument("--all-entities", action="store_true", help="G-2 重新处理全部实体（默认只补缺失）")
    ap.add_argument("--text-provider", default=None)
    ap.add_argument("--image-provider", default=None)
    args = ap.parse_args(argv)

    ids = [x.strip() for x in args.ids.split(",") if x.strip()] if args.ids else None
    out = {"ok": True, "text": None, "portrait": None}
    if args.stage in ("text", "all"):
        out["text"] = tool_api.run_visual_text(args.project_dir, ids=ids,
                                               only_missing=not args.all_entities,
                                               text_provider=args.text_provider)
    if args.stage in ("portrait", "all"):
        out["portrait"] = tool_api.run_portraits(args.project_dir, ids=ids, regenerate=args.regenerate,
                                                 image_provider=args.image_provider)
    out["status"] = tool_api.get_visual_status(args.project_dir)
    for key in ("text", "portrait"):
        res = out[key]
        if res is None:
            continue
        if not res["ok"] or res["data"].get("failed") or res["data"].get("stopped"):
            out["ok"] = False
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
