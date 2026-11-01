#!/usr/bin/env python
"""project.yaml 的 health_check.cmd。退出码 0 = 健康。"""

from __future__ import annotations

import sys

import _common  # noqa: F401  (引导 sys.path)
from _common import force_utf8_stdio

from novel_to_video import tool_api


def main() -> int:
    force_utf8_stdio()
    result = tool_api.project_health()
    if not result["ok"]:
        print(result["error"]["message"], file=sys.stderr)
        return 1
    if not result["data"]["ok"]:
        for p in result["data"]["problems"]:
            print(p, file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
