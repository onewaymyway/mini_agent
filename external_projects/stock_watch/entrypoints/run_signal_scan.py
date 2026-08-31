#!/usr/bin/env python
"""entrypoints/run_signal_scan.py — 自主挖掘信号扫描（阶段3）。

对应 `next_doc/stock_watch_pool_state_tracking_and_kanban_plan.md` 阶段3：
不依赖外部网站（问财/股吧热榜等）的现成结论，自己分析候选池内标的的
历史行情（`indicators.py`）、公告（`announcement_signals.py`）、新闻
（`news_signals.py`），把算出来的信号合并进候选池分数/理由
（`candidate_pool.merge_signals()`）。

与 `run_hotlist_scan.py`（外部网站热度扫描）是两个独立的 entrypoint，
不合并——职责不同：一个是"抓别人已经发现的热点"，一个是"自己分析已在
候选池里的标的"，混在一起会让单个 entrypoint 的失败排查变复杂。

三类信号各自受 `signals.*_enabled` 开关控制（`config/watchlist.yaml`），
默认全部关闭，需要显式打开；关闭的类别直接跳过，不发起对应的网络请求。

只分析候选池内已有的标的（`config.signal_scan_max_targets` 上限内，
优先选最近更新的标的），不做全市场扫描——自算信号需要行情+公告+新闻
三类抓取，对全市场做代价过高，且候选池本身已经是"值得关注"的子集，
在这个子集上做深入分析是更合理的边界。

    python entrypoints/run_signal_scan.py
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import _common  # noqa: F401 - 引导 sys.path + 提供 tracked_run

from stock_watch.announcement_signals import classify_announcements
from stock_watch.candidate_pool import load_pool, merge_signals, save_pool
from stock_watch.config import ALGO_POOL_PATH, DATA_DIR, MANUAL_POOL_PATH, REPORTS_DIR, ensure_dirs, load_config
from stock_watch.data_sources import (
    DataSourceError,
    fetch_announcements,
    fetch_etf_kline,
    fetch_kline,
    fetch_news,
)
from stock_watch.indicators import compute_price_signals
from stock_watch.news_signals import compute_news_signals
from stock_watch.report import render_candidate_pool_report

logger = logging.getLogger("stock_watch.signal_scan")




def _select_targets(pool, max_targets: int):
    """候选池非 dropped 标的中，按 last_seen 倒序取前 N 个作为本次分析
    对象——优先分析"最近还在被关注"的标的，而不是全池均摊。"""
    candidates = [e for e in pool.values() if e.state != "dropped"]
    candidates.sort(key=lambda e: e.last_seen, reverse=True)
    return candidates[:max_targets]


def _scan_price_signals(pool, entry, cfg, failures):
    try:
        if entry.type == "etf":
            df = fetch_etf_kline(entry.code, days=max(cfg.kline_days, 30), adjust=cfg.kline_adjust)
        else:
            df = fetch_kline(entry.code, market="", days=max(cfg.kline_days, 30), adjust=cfg.kline_adjust)
    except DataSourceError as exc:
        logger.warning("行情信号 %s(%s) 抓取失败: %s", entry.name, entry.code, exc)
        failures.append(f"price:{entry.code}: {exc}")
        return
    signals = compute_price_signals(df)
    merge_signals(pool, entry.code, entry.name, signals, entry_type=entry.type)


def _scan_announcement_signals(pool, entry, cfg, failures):
    try:
        df = fetch_announcements(entry.code)
    except DataSourceError as exc:
        logger.warning("公告信号 %s(%s) 抓取失败: %s", entry.name, entry.code, exc)
        failures.append(f"announcement:{entry.code}: {exc}")
        return
    signals = classify_announcements(df.to_dict("records"), weights=cfg.announcement_weights)
    merge_signals(pool, entry.code, entry.name, signals, entry_type=entry.type)


def _scan_news_signals(pool, entry, failures):
    try:
        df = fetch_news(entry.code)
    except DataSourceError as exc:
        logger.warning("新闻信号 %s(%s) 抓取失败: %s", entry.name, entry.code, exc)
        failures.append(f"news:{entry.code}: {exc}")
        return
    signals = compute_news_signals(df.to_dict("records"))
    merge_signals(pool, entry.code, entry.name, signals, entry_type=entry.type)


def main() -> int:
    ensure_dirs()
    cfg = load_config()
    categories = cfg.signal_categories_enabled
    if not any(categories.values()):
        logger.info("signals.*_enabled 均未开启，跳过本次自算信号扫描（见 config/watchlist.yaml）")
        return 0

    # 分别加载算法池和手动池
    algo_pool = load_pool(ALGO_POOL_PATH)
    manual_pool = load_pool(MANUAL_POOL_PATH)

    if not algo_pool and not manual_pool:
        logger.info("双池均为空，跳过本次自算信号扫描")
        return 0

    # 只对算法池进行信号扫描（手动池由用户管理，不自动扫描）
    if algo_pool:
        targets = _select_targets(algo_pool, cfg.signal_scan_max_targets)
        if not targets:
            logger.info("算法池内没有可分析的标的（均为 dropped 状态），跳过")
        else:
            failures: list = []
            for entry in targets:
                if categories["price"]:
                    _scan_price_signals(algo_pool, entry, cfg, failures)
                if categories["announcement"]:
                    _scan_announcement_signals(algo_pool, entry, cfg, failures)
                if categories["news"]:
                    _scan_news_signals(algo_pool, entry, failures)
            save_pool(ALGO_POOL_PATH, algo_pool)
            logger.info("算法池信号扫描完成: 分析 %d 只标的，%d 次抓取失败", len(targets), len(failures))

            # 只有"分析对象数 > 0 但每一次抓取都失败"才判整体失败
            total_attempts = len(targets) * sum(1 for v in categories.values() if v)
            if total_attempts > 0 and len(failures) >= total_attempts:
                logger.error("算法池信号扫描全部抓取失败")
                return 1

    # 手动池不进行信号扫描
    if manual_pool:
        logger.info("手动池 %d 只标的跳过信号扫描（由用户管理）", len(manual_pool))

    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("signal_scan", main))
