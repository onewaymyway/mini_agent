#!/usr/bin/env python
"""导入 novel_writer 导出目录并创建项目。

用法：
  python entrypoints/import_novel.py <导出目录> [--projects-root DIR] [--title 书名]
         [--chapters "1-10,12"] [--duration 90] [--dry-run]
输出 JSON（UTF-8）到 stdout；导入阻断错误时退出码 1。
"""

from __future__ import annotations

import argparse
import json
import sys

import _common  # noqa: F401
from _common import PROJECT_ROOT, force_utf8_stdio

from novel_to_video import tool_api


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="导入 novel_writer 导出目录")
    ap.add_argument("source_dir")
    ap.add_argument("--projects-root", default=str(PROJECT_ROOT / "novel_output"))
    ap.add_argument("--title", default=None)
    ap.add_argument("--chapters", default=None, help="章节范围，默认全部")
    ap.add_argument("--duration", type=int, default=None, help="单章目标时长（秒）；0 = 不限制时长与字数")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不创建项目")
    args = ap.parse_args(argv)

    options = {}
    if args.duration is not None:
        options["target_chapter_duration_sec"] = args.duration

    result = tool_api.create_project_from_source(
        args.projects_root, args.source_dir,
        title=args.title, chapter_spec=args.chapters,
        options=options or None, dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
