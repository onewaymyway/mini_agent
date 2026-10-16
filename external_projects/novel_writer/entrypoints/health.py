#!/usr/bin/env python
"""entrypoints/health.py — project.yaml 的 `health_check.cmd`。

退出码 0 = 健康。只验证"23 个 skill 完整且已登记 + 数据目录可写"，不依赖
网络/LLM（健康检查不应因一次 LLM 超时就把项目判为不健康）。
"""

from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))


def main() -> int:
    try:
        from novel_writer import tool_api
    except ImportError as exc:
        print(f"依赖未安装: {exc}", file=sys.stderr)
        return 1
    result = tool_api.project_health()
    if not result["ok"]:
        print(f"健康检查异常: {result['error']['message']}", file=sys.stderr)
        return 1
    data = result["data"]
    if not data["ok"]:
        for p in data["problems"]:
            print(p, file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
