#!/usr/bin/env python
"""按挡位批量运行选中章节（Phase 3）。

用法：
  python entrypoints/run_queue.py <项目目录> [--chapters 1-3] [--gear G2]
输出 JSON。某章 failed 时退出码为 1；因预算停止时退出码为 2。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import _common  # noqa: F401
from _common import force_utf8_stdio

from novel_to_video import project_store as ps
from novel_to_video import tool_api


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="按挡位批量运行章节")
    ap.add_argument("project_dir")
    ap.add_argument("--chapters", default=None)
    ap.add_argument("--gear", default=None, help="覆盖项目挡位（G0–G3）")
    args = ap.parse_args(argv)
    numbers = None
    if args.chapters:
        numbers = ps.parse_chapter_spec(args.chapters, [c["no"] for c in ps.load_project(Path(args.project_dir))["chapters"]])
    res = tool_api.run_queue(args.project_dir, numbers, gear=args.gear)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res["ok"]:
        return 1
    if res["data"]["halted"]:
        return 2
    bad = [no for no, r in res["data"]["results"].items() if r.get("status") == "failed"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
