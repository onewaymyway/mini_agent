#!/usr/bin/env python
"""全书合成（F-3/F-4）。必须先在界面或 confirm 中完成人工确认。

用法：
  python entrypoints/compose_book.py <项目目录> --check            # F-1 检查，不合成
  python entrypoints/compose_book.py <项目目录> --confirm "备注"    # 记录人工确认
  python entrypoints/compose_book.py <项目目录> [--transition fade] [--no-cover]
退出码：0 成功，1 失败/未就绪/未确认。
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
    ap = argparse.ArgumentParser(description="全书合成")
    ap.add_argument("project_dir")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--confirm", default=None, metavar="备注")
    ap.add_argument("--transition", choices=["cut", "fade"], default="cut")
    ap.add_argument("--no-cover", action="store_true")
    args = ap.parse_args(argv)
    if args.check:
        res = tool_api.final_check(args.project_dir)
    elif args.confirm is not None:
        res = tool_api.confirm_final(args.project_dir, args.confirm)
    else:
        res = tool_api.compose_book(args.project_dir, transition=args.transition,
                                    use_cover=False if args.no_cover else None)
    print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
