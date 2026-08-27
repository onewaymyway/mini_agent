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
from stock_watch.config import DATA_DIR, REPORTS_DIR, ensure_dirs, load_config
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

POOL_PATH = DATA_DIR / "candidate_pool.json"


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

    pool = load_pool(POOL_PATH)
    if not pool:
        logger.info("候选池为空，跳过本次自算信号扫描")
        return 0

    targets = _select_targets(pool, cfg.signal_scan_max_targets)
    if not targets:
        logger.info("候选池内没有可分析的标的（均为 dropped 状态），跳过")
        return 0

    failures: list = []
    for entry in targets:
        if categories["price"]:
            _scan_price_signals(pool, entry, cfg, failures)
        if categories["announcement"]:
            _scan_announcement_signals(pool, entry, cfg, failures)
        if categories["news"]:
            _scan_news_signals(pool, entry, failures)

    save_pool(POOL_PATH, pool)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out_path = REPORTS_DIR / "candidate_pool" / f"{datetime.now().strftime('%Y%m%d')}_signal_scan.md"
    render_candidate_pool_report(list(pool.values()), out_path, generated_at=generated_at)
    logger.info(
        "自算信号扫描完成: %s（分析 %d 只标的，%d 次抓取失败）",
        out_path, len(targets), len(failures),
    )

    # 与 hotlist_scan 一致的容错基调：单只标的/单类信号失败不影响其它，
    # 只有"分析对象数 > 0 但每一次抓取都失败"才判整体失败。
    total_attempts = len(targets) * sum(1 for v in categories.values() if v)
    if total_attempts > 0 and len(failures) >= total_attempts:
        logger.error("本次信号扫描全部抓取失败")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("signal_scan", main))
