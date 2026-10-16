#!/usr/bin/env python
"""entrypoints/create_novel.py — 新建一本小说：创建并初始化它的工作目录。

用法：
    python entrypoints/create_novel.py --title 《青云传》
    python entrypoints/create_novel.py --title 青云传 --workspace D:\\novels\\青云传 --gear G1 --quality standard

M0 只负责建目录与初始文件；蓝图等内容由后续里程碑的流水线生成。
默认工作目录：data/novels/<书名>_<时间戳>/。
"""

from __future__ import annotations

import argparse

import _common  # noqa: F401

from novel_writer import tool_api
from novel_writer.config import ensure_dirs
from novel_writer.settings import GEARS, QUALITIES


def main() -> int:
    parser = argparse.ArgumentParser(description="新建小说工作目录")
    parser.add_argument("--title", required=True, help="书名")
    parser.add_argument("--workspace", default=None, help="工作目录（默认 data/novels/<书名>_<时间戳>/）")
    parser.add_argument("--gear", default="G0", choices=list(GEARS), help="控制挡位，默认 G0 手动挡")
    parser.add_argument("--quality", default="standard", choices=list(QUALITIES), help="质量档位，默认 standard")
    parser.add_argument("--total-chapters", type=int, default=0, help="计划章节数（可选，蓝图生成后会更新）")
    args = parser.parse_args()

    ensure_dirs()
    result = tool_api.create_novel(
        args.title,
        workspace=args.workspace,
        gear=args.gear,
        quality=args.quality,
        total_chapters_planned=args.total_chapters,
    )
    if not result["ok"]:
        print(f"创建失败：{result['error']['message']}")
        return 1
    data = result["data"]
    print(f"workspace={data['workspace']}")
    print(f"title={data['title']}")
    print("already_initialized=" + str(data["already_initialized"]).lower())
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("create_novel", main, trigger="manual"))
