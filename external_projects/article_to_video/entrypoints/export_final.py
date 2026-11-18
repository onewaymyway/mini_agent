"""entrypoints/export_final.py — 成片导出（P5）：章视频按转场拼接，写出 mp4、srt、封面与报告。

用法：python entrypoints/export_final.py --id <项目目录名>
前提：所有章已合成（compose_part.py）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import force_utf8_stdio  # noqa: E402

from article_to_video import cancel  # noqa: E402
from article_to_video.compose.export import export_final  # noqa: E402
from article_to_video.paths import projects_root  # noqa: E402


def main(argv=None) -> int:
    force_utf8_stdio()
    p = argparse.ArgumentParser(description="导出成片")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", help="项目目录名（位于项目集合目录下）")
    g.add_argument("--project", help="项目目录路径")
    args = p.parse_args(argv)

    pdir = Path(args.project) if args.project else projects_root() / args.id
    cancel.bind(cancel.begin(pdir))
    try:
        rep = export_final(pdir)
    except Exception as exc:  # noqa: BLE001 — 统一输出错误文字
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "report": rep}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
