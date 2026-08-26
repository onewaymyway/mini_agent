#!/usr/bin/env python
"""entrypoints/run_screener.py — 功能 3：条件选股。

不带参数时，跑 `config/watchlist.yaml` 里的 `screener.default_queries`
全部查询；带参数时，把命令行参数拼成一条自然语言查询直接跑（用于手动
临时筛选，如"探底回升的ETF"）。

    python entrypoints/run_screener.py                # 默认查询集合
    python entrypoints/run_screener.py 今日涨停且换手率大于5%   # 单条自定义查询
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import _common  # noqa: F401

from stock_watch.config import REPORTS_DIR, ensure_dirs, load_config
from stock_watch.report import render_screener_report
from stock_watch.screener import run_queries

logger = logging.getLogger("stock_watch.screener_entry")


def main() -> int:
    ensure_dirs()
    cfg = load_config()

    if len(sys.argv) > 1:
        queries = [" ".join(sys.argv[1:])]
    else:
        queries = cfg.default_screener_queries
        if not queries:
            logger.warning("未配置默认查询集合（config/watchlist.yaml screener.default_queries）")
            return 0

    results = run_queries(queries)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path = REPORTS_DIR / "screener" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    render_screener_report(results, out_path, generated_at=generated_at)

    ok_count = sum(1 for r in results if r.ok)
    logger.info("选股完成: %d/%d 条查询成功，报告: %s", ok_count, len(results), out_path)
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("screener", main))
