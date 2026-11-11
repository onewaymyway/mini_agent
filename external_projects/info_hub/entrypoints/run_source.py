#!/usr/bin/env python
"""entrypoints/run_source.py — 立即抓取一个信息源。

用法：python entrypoints/run_source.py <source_id>      （等价于 python cli.py run <source_id>）
"""
from __future__ import annotations

import sys

import _common


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("用法: run_source.py <source_id>", file=sys.stderr)
        return 2
    return _common.cli_main(["run", sys.argv[1].strip()])


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("run_source", main))
