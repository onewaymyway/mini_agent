#!/usr/bin/env python
"""entrypoints/run_telegraph_scan.py — 新浪 7x24 直播流扫描。

抓取新浪财经 7x24 实时新闻，提取提及的 A 股标的，将正面信号
合并进候选池（算法池），并生成报告供看板读取。

单次执行、无状态依赖 daemon，可被 OS cron 直接调用：
    python entrypoints/run_telegraph_scan.py
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import _common  # noqa: F401 - 引导 sys.path + 提供 tracked_run

from stock_watch.candidate_pool import (
    DEFAULT_STATE,
    ensure_seeds,
    load_pool,
    merge_signals,
    save_pool,
)
from stock_watch.config import (
    ALGO_POOL_PATH,
    MANUAL_POOL_PATH,
    DATA_DIR,
    REPORTS_DIR,
    SOURCE_HEALTH_PATH,
    ensure_dirs,
    load_config,
)
from stock_watch.data_sources import (
    DataSourceError,
    fetch_sina_telegraph,
)
from stock_watch.news_signals import classify_telegraph_news
from stock_watch.source_health import tracked_source
from stock_watch.signals import Signal

logger = logging.getLogger("stock_watch.telegraph_scan")

# 报告输出路径
TELEGRAPH_REPORT_DIR = REPORTS_DIR / "telegraph"


def _load_stock_name_map() -> dict:
    """加载股票名称映射（从 all_symbols.json 或候选池）。

    返回 {code: name} 字典，用于在新闻中匹配股票名称。
    """
    name_map = {}

    # 优先从 all_symbols.json 加载
    all_symbols_path = DATA_DIR / "all_symbols.json"
    if all_symbols_path.exists():
        try:
            data = json.loads(all_symbols_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        code = item.get("code") or item.get("symbol") or ""
                        name = item.get("name") or item.get("ticker") or ""
                        if code and name:
                            name_map[code] = name
            elif isinstance(data, dict):
                name_map = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except Exception as e:
            logger.warning("加载 all_symbols.json 失败: %s", e)

    # 补充候选池中的股票
    for pool_path in [ALGO_POOL_PATH, MANUAL_POOL_PATH]:
        if pool_path.exists():
            try:
                pool = json.loads(pool_path.read_text(encoding="utf-8"))
                if isinstance(pool, dict):
                    for code, entry in pool.items():
                        if isinstance(entry, dict):
                            name = entry.get("name", "")
                            if name and code not in name_map:
                                name_map[code] = name
            except Exception as e:
                logger.warning("加载候选池失败: %s", e)

    logger.info("已加载 %d 只股票名称映射", len(name_map))
    return name_map


def _generate_report(stats: dict, report_path: Path) -> None:
    """生成 Markdown 格式的报告。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"# 新浪 7x24 直播流扫描报告\n",
        f"生成时间: {now}\n",
        f"---\n",
        f"\n",
        f"## 概览\n",
        f"- **总新闻条数**: {stats['total_news']}\n",
        f"- **提及股票条数**: {stats['stock_related']}\n",
        f"- **正面提及**: {stats['positive_mentions']}\n",
        f"- **负面提及**: {stats['negative_mentions']}\n",
        f"\n",
    ]

    if stats["stock_mentions"]:
        lines.append("## 股票提及统计\n")
        lines.append("| 代码 | 名称 | 提及次数 | 正面 | 负面 | 首次出现 |\n")
        lines.append("|------|------|---------|------|------|---------|\n")
        for m in stats["stock_mentions"][:10]:  # 只显示前10只
            names = ", ".join(m.get("names", [])) or "未知"
            lines.append(f"| {m['code']} | {names} | {m['count']} | {m['positive']} | {m['negative']} | {m.get('first_seen', '')} |\n")
        lines.append("")

    if stats["sample_news"]:
        lines.append("## 样例新闻\n")
        for i, news in enumerate(stats["sample_news"][:5], 1):
            lines.append(f"### {i}. [{news['time']}]\n")
            lines.append(f"{news['content']}\n")
            if news.get("codes"):
                lines.append(f"涉及股票: {', '.join(news['codes'])}\n")
            lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("报告已生成: %s", report_path)


def main() -> int:
    ensure_dirs()
    cfg = load_config()

    # 确保 TELEGRAPH_SCAN 开关开启
    if not cfg.source_enabled("sina_telegraph"):
        logger.info("新浪 7x24 数据源未启用，跳过")
        return 0

    # 加载股票名称映射
    stock_name_map = _load_stock_name_map()

    # 抓取新浪 7x24 直播流
    try:
        with tracked_source(SOURCE_HEALTH_PATH, source="sina_telegraph", entrypoint="telegraph_scan") as h:
            news_items = fetch_sina_telegraph(top_n=cfg.telegraph_top_n)
            h.item_count = len(news_items)
    except DataSourceError as exc:
        logger.error("抓取新浪 7x24 失败: %s", exc)
        return 1

    if not news_items:
        logger.warning("未获取到任何新闻，跳过信号分析")
        return 0

    logger.info("抓取到 %d 条新闻", len(news_items))

    # 分析新闻，提取股票信号
    stats = classify_telegraph_news(news_items, stock_name_map=stock_name_map)

    # 生成报告
    report_path = TELEGRAPH_REPORT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    _generate_report(stats, report_path)

    # 将正面信号合并进候选池（仅算法池）
    algo_pool = load_pool(ALGO_POOL_PATH)
    algo_pool = ensure_seeds(algo_pool, cfg.seeds)

    positive_threshold = cfg.telegraph_positive_threshold
    positive_stocks = [
        m for m in stats["stock_mentions"]
        if m["positive"] > m["negative"] and m["count"] >= positive_threshold
    ]

    signals_added = 0
    for mention in positive_stocks[:10]:  # 最多处理10只股票
        code = mention["code"]
        name = mention.get("names", ["未知"])[0]

        # 构造信号
        signal = Signal(
            name="telegraph_positive",
            category="news",
            score=min(mention["positive"] * 1.5, 8.0),  # 最高8分
            reason=f"新浪 7x24 提及 {mention['count']} 次，正面 {mention['positive']} 次，负面 {mention['negative']} 次",
            evidence_ref=mention.get("first_seen", ""),
        )

        # 合并进候选池
        algo_pool = merge_signals(algo_pool, code, name, [signal])
        signals_added += 1

    save_pool(ALGO_POOL_PATH, algo_pool)

    # 保存分析结果 JSON（供看板读取）
    result_path = DATA_DIR / "telegraph_analysis.json"
    result_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "新浪 7x24 扫描完成: 新闻%d条, 提及股票%d只, 添加信号%d个",
        stats["total_news"],
        len(stats["stock_mentions"]),
        signals_added,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("telegraph_scan", main))
