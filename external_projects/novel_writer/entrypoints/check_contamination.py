#!/usr/bin/env python
"""entrypoints/check_contamination.py — 第 N 章补救后，检查后续章节是否被污染。

用法：python entrypoints/check_contamination.py <工作目录> --garbled 19 --chapters 20 21 22 23
退出码：0 = 未发现污染；1 = 发现污染或无法确认（需要作者查看报告）。报告写入 _studio/inbox/。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    from novel_writer.contamination import scan_contamination, write_contamination_report
    from novel_writer.workspace import Workspace

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--garbled", type=int, required=True, help="已补救的乱码章节号")
    parser.add_argument("--chapters", type=int, nargs="+", required=True, help="需要检查的后续章节号")
    args = parser.parse_args()

    ws = Workspace(args.workspace)
    report = scan_contamination(ws, garbled_chapter=args.garbled, chapters=args.chapters)
    out = write_contamination_report(ws, report)
    if report.get("error"):
        print(report["error"], file=sys.stderr)
        return 1
    for f in report["findings"]:
        print(f"[污染] 第{f['chapter']}章 {f['kind']}: {f['detail']}")
    for u in report["unknown"]:
        print(f"[待确认] 第{u['chapter']}章: {u['detail']}")
    print(f"报告：{out}")
    return 1 if report["contaminated"] or report["unknown"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
