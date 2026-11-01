#!/usr/bin/env python
"""运行章节流水线（C-1…C-9，可重入）。

用法：
  python entrypoints/run_chapter.py <项目目录> [--chapters 1-3] [--force-script]
输出 JSON。每章 status 为 done / waiting_assets / waiting_review / failed 之一；
存在 failed 时退出码为 1。waiting_* 不是错误（需要人工确认后再次运行）。
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


def parse_numbers(project_dir, spec):
    if not spec:
        return None
    return ps.parse_chapter_spec(spec, [c["no"] for c in ps.load_project(Path(project_dir))["chapters"]])


def main(argv=None) -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="运行章节流水线")
    ap.add_argument("project_dir")
    ap.add_argument("--chapters", default=None, help="章节范围，默认全部已选中章节")
    ap.add_argument("--force-script", action="store_true", help="强制重新生成剧本")
    args = ap.parse_args(argv)
    numbers = parse_numbers(args.project_dir, args.chapters)
    res = tool_api.run_chapters(args.project_dir, numbers, force_script=args.force_script)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if not res["ok"]:
        return 1
    bad = [no for no, r in res["data"].items() if not r["ok"] or r["data"]["status"] == "failed"]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
