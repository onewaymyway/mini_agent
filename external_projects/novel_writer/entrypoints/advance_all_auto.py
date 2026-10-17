#!/usr/bin/env python
"""entrypoints/advance_all_auto.py — 批量推进所有自动挡（G2/G3）小说（M5，方案 6.5）。

用法：
    python entrypoints/advance_all_auto.py                  # 每本最多推进 1 章
    python entrypoints/advance_all_auto.py --max-chapters 2
    python entrypoints/advance_all_auto.py --dry-run        # 只预览，不调用 LLM

默认**不**接入定时任务（cron 默认关闭，需要时在 daemon 配置里手动添加一行）。
G0 / G1 挡位的书不会被批量推进。退出码：0 = 正常结束（含部分书被跳过/停在审批）；1 = 参数错误。
输出一行 JSON 摘要。
"""

from __future__ import annotations

import argparse
import json

import _common  # noqa: F401

from novel_writer import runner as runner_mod
from novel_writer import tool_api
from novel_writer.config import ensure_dirs


def main() -> int:
    parser = argparse.ArgumentParser(description="批量推进自动挡小说（每本最多 N 章）")
    parser.add_argument("--max-chapters", type=int, default=1, help="每本书本次最多推进的章数，默认 1")
    parser.add_argument("--dry-run", action="store_true", help="只列出将要推进的书，不调用执行器")
    args = parser.parse_args()

    ensure_dirs()
    invoker = None if args.dry_run else runner_mod.FrameworkInvoker()
    result = tool_api.advance_all_auto(max_chapters=args.max_chapters, dry_run=args.dry_run, invoker=invoker)
    print(json.dumps(result.get("data") or result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("advance_all_auto", main))
