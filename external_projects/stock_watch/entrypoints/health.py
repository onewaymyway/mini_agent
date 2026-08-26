#!/usr/bin/env python
"""entrypoints/health.py — project.yaml 的 `health_check.cmd`。

约定：退出码 0 = 健康，非 0 = 不健康。这里的"健康"定义为"依赖能正常
导入 + 候选池账本文件可读（不校验网络连通性，避免健康检查本身又依赖
外部网站可用性——那样一旦某个数据源临时抖动就会把整个项目误判为不
健康）"。
"""

from __future__ import annotations

import sys

import _common  # noqa: F401

from stock_watch.candidate_pool import load_pool
from stock_watch.config import DATA_DIR


def main() -> int:
    try:
        import akshare  # noqa: F401
        import bs4  # noqa: F401
        import requests  # noqa: F401
    except ImportError as exc:
        print(f"依赖未安装: {exc}", file=sys.stderr)
        return 1

    try:
        load_pool(DATA_DIR / "candidate_pool.json")
    except Exception as exc:  # noqa: BLE001
        print(f"候选池账本读取异常: {exc}", file=sys.stderr)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
