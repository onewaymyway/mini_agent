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
    ensure_manual_seeds,
    load_pool,
    merge_hot_items,
    save_pool,
    save_pool_snapshot,
)
from stock_watch.config import ALGO_POOL_PATH, MANUAL_POOL_PATH
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



_SOURCE_FETCHERS = {
    "eastmoney_hot_rank": fetch_eastmoney_hot_rank,
    "eastmoney_guba_hot": fetch_eastmoney_guba_hot,
    "xueqiu_hot_stock": fetch_xueqiu_hot_stock,
}


def main() -> int:
    ensure_dirs()
    cfg = load_config()

    # 算法池：从 data/algo_pool.json 加载，受上限和衰减控制
    algo_pool = load_pool(ALGO_POOL_PATH)
    algo_pool = ensure_seeds(algo_pool, cfg.seeds)

    # 手动池：从 data/manual_pool.json 加载，无淘汰上限
    manual_pool = load_pool(MANUAL_POOL_PATH)
    manual_pool = ensure_manual_seeds(manual_pool, cfg.manual_seeds)

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
        algo_pool = merge_hot_items(algo_pool, items)
        logger.info("数据源 %s 抓取到 %d 条", source_name, len(items))

    # 阶段2：对算法池新标的回填价格（手动池由用户管理，不自动回填）
    for entry in algo_pool.values():
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

    # 算法池：应用衰减和上限淘汰
    algo_pool = apply_decay(algo_pool, decay_days=cfg.score_decay_days)
    algo_pool = enforce_max_size(algo_pool, cfg.algo_max_pool_size)
    save_pool(ALGO_POOL_PATH, algo_pool)
    save_pool_snapshot(POOL_SNAPSHOTS_DIR, algo_pool)

    # 手动池：直接保存（无衰减/淘汰）
    save_pool(MANUAL_POOL_PATH, manual_pool)

    # 生成双池合并报告
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path = REPORTS_DIR / "candidate_pool" / f"{datetime.now().strftime('%Y%m%d')}.md"
    all_entries = list(algo_pool.values()) + list(manual_pool.values())
    render_candidate_pool_report(all_entries, out_path, generated_at=generated_at)
    logger.info("候选池报告已生成: %s（算法池%d只 + 手动池%d只）", out_path, len(algo_pool), len(manual_pool))

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
