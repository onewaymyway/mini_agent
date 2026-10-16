#!/usr/bin/env python
"""entrypoints/run_novel.py — 后台 worker：推进一本小说的流水线（方案 6.5）。

用法：
    python entrypoints/run_novel.py --workspace <工作目录> [--mode until_gate|single]

通常由 UI / CLI 通过 `tool_api.run_novel()` 拉起（独立进程，不阻塞界面）。
退出码：0 = 正常结束（完成 / 需要人介入 / 暂停 / 预算耗尽均属正常）；
        1 = 参数或工作目录错误；2 = 已有存活的 worker。
输出一行 JSON 摘要，便于脚本读取。
"""

from __future__ import annotations

import argparse
import json

import _common  # noqa: F401

from novel_writer import runner as runner_mod
from novel_writer.config import ensure_dirs
from novel_writer.worker import MODES, run_worker
from novel_writer.workspace import Workspace


def main() -> int:
    parser = argparse.ArgumentParser(description="运行一本小说的流水线（后台 worker）")
    parser.add_argument("--workspace", required=True, help="小说工作目录")
    parser.add_argument("--mode", default="until_gate", choices=list(MODES))
    args = parser.parse_args()

    ensure_dirs()
    ws = Workspace(args.workspace)
    result = run_worker(ws, mode=args.mode, invoker=runner_mod.FrameworkInvoker())
    print(json.dumps(result, ensure_ascii=False))
    if result.get("reason") == "already_running":
        return 2
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
