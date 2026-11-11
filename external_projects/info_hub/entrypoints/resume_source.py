#!/usr/bin/env python
"""entrypoints/resume_source.py — 手动恢复被暂停/连续失败的信息源。

用法：python entrypoints/resume_source.py <source_id>      （等价于 python cli.py resume <source_id>）
没有需要恢复的状态时退出码为 1。
"""
from __future__ import annotations

import sys

import _common


def main() -> int:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("用法: resume_source.py <source_id>", file=sys.stderr)
        return 2
    return _common.cli_main(["resume", sys.argv[1].strip()])


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("resume_source", main))
