#!/usr/bin/env python
"""entrypoints/run_pool_tracking.py — 候选池状态区间每日跟踪。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段2：
候选池内每只标的（跳过已 `dropped` 的终态标的）都取一次最新收盘价，
算出"自进入当前状态以来"以及"历史每一段状态各自"的涨跌幅，渲染成
Markdown 报告（`reports/pool_tracking/<日期>.md`），同时落一份结构化
JSON（`data/pool_tracking_latest.json`）供未来看板直接读取。

单只标的取价失败（停牌/退市/接口抖动）不影响其它标的继续跟踪，报告里
如实标注"取价失败"，与 `reconcile_outcomes.py` 现有的容错风格一致。

    python entrypoints/run_pool_tracking.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import _common  # noqa: F401 - 引导 sys.path + 提供 tracked_run

from stock_watch.candidate_pool import compute_state_returns, load_pool
from stock_watch.config import (
    DATA_DIR,
    POOL_TRACKING_LATEST_PATH,
    REPORTS_DIR,
    ensure_dirs,
)
from stock_watch.data_sources import DataSourceError, fetch_latest_close
from stock_watch.report import render_pool_tracking_report, write_pool_tracking_json

logger = logging.getLogger("stock_watch.pool_tracking")

POOL_PATH = DATA_DIR / "candidate_pool.json"


def main() -> int:
    ensure_dirs()
    pool = load_pool(POOL_PATH)
    if not pool:
        logger.info("候选池为空，跳过本次状态跟踪")
        return 0

    tracked = []
    ok_count = 0
    for entry in pool.values():
        if entry.state == "dropped":
            continue  # 终态标的不参与每日跟踪，历史仍保留在账本里
        current_price = None
        price_error = None
        try:
            current_price = fetch_latest_close(entry.code, entry.type)
            ok_count += 1
        except DataSourceError as exc:
            price_error = str(exc)
            logger.warning("跟踪 %s(%s) 取价失败: %s", entry.name, entry.code, exc)
        state_returns = compute_state_returns(entry, current_price)
        tracked.append((entry, current_price, state_returns, price_error))

    if not tracked:
        logger.info("候选池内没有非 dropped 状态的标的，跳过本次状态跟踪")
        return 0

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path = REPORTS_DIR / "pool_tracking" / f"{datetime.now().strftime('%Y%m%d')}.md"
    render_pool_tracking_report(tracked, out_path, generated_at=generated_at)
    write_pool_tracking_json(tracked, POOL_TRACKING_LATEST_PATH, generated_at=generated_at)
    logger.info(
        "状态跟踪报告已生成: %s（%d 只标的，%d 只取价成功）",
        out_path, len(tracked), ok_count,
    )

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("pool_tracking", main))
