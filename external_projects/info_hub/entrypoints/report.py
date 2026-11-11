#!/usr/bin/env python
"""entrypoints/report.py — 立即生成一份报告。

用法：python entrypoints/report.py <plan_id>      （等价于 python cli.py report <plan_id>）
plan_id 见 config/settings.yaml 的 reports（默认 rolling / daily）。
"""
from __future__ import annotations

import sys

import _common


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("用法: report.py <plan_id>", file=sys.stderr)
        return 2
    return _common.cli_main(["report", sys.argv[1].strip()])


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("report", main))
