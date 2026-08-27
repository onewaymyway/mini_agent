#!/usr/bin/env python
"""entrypoints/run_hotlist_scan.py — 功能 1：热点候选池抓取。

从多个数据源抓取热点/有前景标的，合并进候选池账本
（`data/candidate_pool.json`），并对候选池内标的生成一份 Markdown 报告
（`reports/candidate_pool/<date>.md`）。

单次执行、无状态依赖 daemon，可被 OS cron 直接调用：
    python entrypoints/run_hotlist_scan.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import _common  # noqa: F401 - 引导 sys.path + 提供 tracked_run

from stock_watch.candidate_pool import (
    DEFAULT_STATE,
    apply_decay,
    backfill_entry_price,
    enforce_max_size,
    ensure_seeds,
    load_pool,
    merge_hot_items,
    save_pool,
    save_pool_snapshot,
)
from stock_watch.config import (
    DATA_DIR,
    POOL_SNAPSHOTS_DIR,
    REPORTS_DIR,
    SOURCE_HEALTH_PATH,
    ensure_dirs,
    load_config,
)
from stock_watch.data_sources import (
    DataSourceError,
    fetch_eastmoney_guba_hot,
    fetch_eastmoney_hot_rank,
    fetch_latest_close,
    fetch_xueqiu_hot_stock,
)
from stock_watch.report import render_candidate_pool_report
from stock_watch.source_health import tracked_source

logger = logging.getLogger("stock_watch.hotlist_scan")

POOL_PATH = DATA_DIR / "candidate_pool.json"

_SOURCE_FETCHERS = {
    "eastmoney_hot_rank": fetch_eastmoney_hot_rank,
    "eastmoney_guba_hot": fetch_eastmoney_guba_hot,
    "xueqiu_hot_stock": fetch_xueqiu_hot_stock,
}


def main() -> int:
    ensure_dirs()
    cfg = load_config()
    pool = load_pool(POOL_PATH)
    pool = ensure_seeds(pool, cfg.seeds)

    failures = []
    for source_name, fetcher in _SOURCE_FETCHERS.items():
        if not cfg.source_enabled(source_name):
            continue
        try:
            with tracked_source(SOURCE_HEALTH_PATH, source=source_name, entrypoint="hotlist_scan") as h:
                items = fetcher()
                h.item_count = len(items)
        except DataSourceError as exc:
            logger.warning("数据源 %s 抓取失败，跳过: %s", source_name, exc)
            failures.append(f"{source_name}: {exc}")
            continue
        pool = merge_hot_items(pool, items)
        logger.info("数据源 %s 抓取到 %d 条", source_name, len(items))

    # 阶段2（stock_watch_pool_state_tracking_and_kanban_plan.md）：
    # candidate_pool.py 是纯逻辑模块，不发起网络请求，新标的进池时
    # price_at_entry 先留空，这里统一回填一次。只查刚新建的 watching
    # 标的（`state_history` 只有一条且价格为空），已经存在的标的不重复
    # 查价；单只失败不影响其它标的，不影响本次抓取任务的整体退出码。
    for entry in pool.values():
        if (
            entry.state == DEFAULT_STATE
            and len(entry.state_history) == 1
            and entry.state_history[0].price_at_entry is None
        ):
            try:
                price = fetch_latest_close(entry.code, entry.type)
                backfill_entry_price(entry, price)
            except DataSourceError as exc:
                logger.info("回填 %s(%s) 进池价格失败（不影响本次抓取）: %s", entry.name, entry.code, exc)

    pool = apply_decay(pool, decay_days=cfg.score_decay_days)
    pool = enforce_max_size(pool, cfg.max_pool_size)
    save_pool(POOL_PATH, pool)
    save_pool_snapshot(POOL_SNAPSHOTS_DIR, pool)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path = REPORTS_DIR / "candidate_pool" / f"{datetime.now().strftime('%Y%m%d')}.md"
    render_candidate_pool_report(list(pool.values()), out_path, generated_at=generated_at)
    logger.info("候选池报告已生成: %s（%d 只标的）", out_path, len(pool))

    if failures and len(failures) == len(
        [s for s in _SOURCE_FETCHERS if cfg.source_enabled(s)]
    ):
        # 所有数据源都失败才算本次执行失败；部分失败视为"降级成功"，
        # 报告里已如实体现哪些标的来自哪些来源，不额外掩盖失败信息。
        logger.error("所有数据源均抓取失败: %s", failures)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("hotlist_scan", main))
