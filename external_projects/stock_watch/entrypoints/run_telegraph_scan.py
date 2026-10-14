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
import sys
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
from stock_watch.news_signals import (
    classify_telegraph_news,
    filter_dedup_news,
    analyze_telegraph_news_llm,
    load_analysis_state,
    save_analysis_state,
    get_all_analyses_summary,
)
from stock_watch.source_health import tracked_source
from stock_watch.signals import Signal

logger = logging.getLogger("stock_watch.telegraph_scan")

# 报告输出路径
TELEGRAPH_REPORT_DIR = REPORTS_DIR / "telegraph"
# 分析状态路径
TELEGRAPH_ANALYSIS_STATE_PATH = DATA_DIR / "telegraph_analysis_state.json"


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
            lines.append(f"{i}. **{news['time']}** {news['content']}\n")
            if news.get("codes"):
                lines.append(f"   相关股票: {', '.join(news['codes'])}\n")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("报告已生成: %s", report_path)


def main() -> int:
    cfg = load_config()
    ensure_dirs()

    # 加载股票名称映射
    stock_name_map = _load_stock_name_map()
    logger.info("已加载股票名称映射")

    # 加载候选池（用于构建股票池参考）
    algo_pool = load_pool(ALGO_POOL_PATH)
    algo_pool = ensure_seeds(algo_pool, cfg.seeds)
    logger.info(f"已加载候选池 (算法池: {len(algo_pool)} 只)")

    # 抓取新浪 7x24 直播流
    try:
        logger.info("正在抓取新浪 7x24 新闻...")
        with tracked_source(SOURCE_HEALTH_PATH, source="sina_telegraph", entrypoint="telegraph_scan") as h:
            news_items = fetch_sina_telegraph(top_n=cfg.telegraph_top_n)
            h.item_count = len(news_items)
    except DataSourceError as exc:
        logger.error("抓取新浪 7x24 失败: %s", exc)
        return 1

    if not news_items:
        logger.warning("未获取到任何新闻，跳过信号分析")
        logger.info("未获取到新闻，扫描结束")
        return 0

    logger.info("抓取到 %d 条新闻", len(news_items))
    logger.info(f"抓取到 {len(news_items)} 条新闻")

    # 去重过滤：只处理新新闻
    TELEGRAPH_HISTORY_PATH = DATA_DIR / "telegraph_history.json"
    new_news, new_count = filter_dedup_news(news_items, TELEGRAPH_HISTORY_PATH)
    logger.info("去重后新增 %d 条新闻 (总数 %d)", new_count, len(news_items))
    logger.info(f"去重后新增 {new_count} 条新闻")

    # LLM分析新新闻
    llm_analyses = []
    if new_news and cfg.sources.get("sina_telegraph", False):
        try:
            # 构建股票池代码列表
            pool_codes = list(algo_pool.keys())
            logger.info(f"正在调用 LLM 分析 {len(new_news)} 条新闻...")
            # 分析新闻机会
            llm_analyses = analyze_telegraph_news_llm(
                new_news,
                stock_name_map=stock_name_map,
                pool_codes=pool_codes,
            )
            # 保存分析状态（cursor = 上次位置 + 本次新增数量）
            from stock_watch.news_signals import load_analysis_state as _load_state
            _prev_state = _load_state(TELEGRAPH_ANALYSIS_STATE_PATH)
            _prev_index = _prev_state.get('last_analysis_index', 0)
            _new_index = _prev_index + len(new_news)
            save_analysis_state(
                TELEGRAPH_ANALYSIS_STATE_PATH,
                _new_index,
                llm_analyses,
            )
            logger.info("LLM分析完成，识别%d条投资机会", len(llm_analyses))
            logger.info(f"LLM分析完成，识别 {len(llm_analyses)} 条投资机会")
        except Exception as e:
            logger.warning("LLM分析失败，跳过: %s", e)
            logger.error(f"LLM分析失败: {e}")

    # 分析新闻，提取股票信号（使用去重后的新闻）
    stats = classify_telegraph_news(new_news, stock_name_map=stock_name_map)
    logger.info(f"新闻分析完成，提及 {len(stats['stock_mentions'])} 只股票")

    # 生成报告
    report_path = TELEGRAPH_REPORT_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    _generate_report(stats, report_path)
    logger.info("报告已生成: %s", report_path)
    logger.info("报告已生成")

    # 将正面信号合并进候选池（仅算法池）
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
    
    # 写入最终进度
    logger.info("✅ 扫描完成")

    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("telegraph_scan", main))
