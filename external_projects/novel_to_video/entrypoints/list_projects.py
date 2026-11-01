#!/usr/bin/env python
"""列出 novel_output/ 下的全部项目。"""

from __future__ import annotations

import argparse
import json

import _common  # noqa: F401
from _common import PROJECT_ROOT, force_utf8_stdio

from novel_to_video import tool_api


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects-root", default=str(PROJECT_ROOT / "novel_output"))
    args = ap.parse_args()
    print(json.dumps(tool_api.list_projects(args.projects_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
