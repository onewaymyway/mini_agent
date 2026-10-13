#!/usr/bin/env python
"""entrypoints/run_price_position_screener.py — 基于历史价格位置的选股策略。

策略逻辑：
  - 统计最近 N 天（默认 200 天）的历史最高价、最低价
  - 计算当前价格相对历史区间的位置
  - 预估分数 = 上涨空间 - 2×下跌空间（风险厌恶型）
  - 筛选出"接近历史高点、远离历史低点"的强势股

用法：
    # 默认：从 kline.db 全量标的中筛选 Top 100
    python entrypoints/run_price_position_screener.py

    # 指定标的列表
    python entrypoints/run_price_position_screener.py --codes 600519 000001 300750

    # 自定义统计天数和输出数量
    python entrypoints/run_price_position_screener.py --lookback 300 --top-n 50

    # 使用候选池
    python entrypoints/run_price_position_screener.py --pool-type algo

设计原则：
  - 全本地，不发起网络请求；数据来自 kline.db
  - 简单直观的价格位置分析，适合趋势跟踪策略
  - 输出 Markdown 报告 + JSON 结果
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import _common  # noqa: F401 — 把项目根加入 sys.path

from stock_watch.daily_kline_db import DailyKlineDB
from stock_watch.price_position import analyze_price_position, to_scorecard

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)
logger = logging.getLogger("stock_watch.price_position_screener")


def ensure_dirs():
    """确保输出目录存在。"""
    output_dir = Path(__file__).resolve().parent.parent / "reports" / "price_position"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def run_price_position_screener(
    symbols: Optional[List[str]] = None,
    *,
    pool_type: str = "algo",
    use_all_symbols: bool = False,
    lookback_days: int = 200,
    top_n: int = 100,
    output_dir: Optional[Path] = None,
) -> List[Dict]:
    """核心选股逻辑。"""
    output_dir = output_dir or ensure_dirs()
    
    with DailyKlineDB() as db:
        info = db.table_info()
        logger.info("数据库状态: %s 只标的, %s 行, 最新日期 %s",
                    info["symbol_count"], info["total_rows"], info["latest_date"])

        # 加载股票名称映射：优先从候选池文件，全量扫描时补充 DB 中的名称
        name_map = {}
        try:
            from run_local_screener import load_name_mapping
            name_map = load_name_mapping(pool_type)
            logger.info("已从候选池加载 %d 只股票名称映射", len(name_map))
        except Exception as e:
            logger.debug("加载股票名称映射失败: %s", e)

        # 从数据库补充名称（适用于全量扫描）
        if use_all_symbols:
            try:
                db_name_map = db.get_symbol_names()
                overlapped = sum(1 for s in db_name_map if s in name_map)
                name_map.update(db_name_map)
                logger.info("已从数据库补充 %d 只名称（与候选池重叠 %d 只）", len(db_name_map), overlapped)
                # 获取全量标的列表
                symbols = db.fetch_all_symbols()
                symbols = [s for s in symbols if s in name_map]  # 只保留有名称的
                logger.info("全量扫描 %d 只标的", len(symbols))
            except Exception as e:
                logger.debug("从数据库加载名称失败: %s", e)
        else:
            from stock_watch.candidate_pool import load_pool
            pool_path = (
                Path(__file__).resolve().parent.parent / "data"
                / ("algo_pool.json" if pool_type == "algo" else "manual_pool.json")
            )
            pool = load_pool(pool_path)
            import re
            symbols = []
            for s in pool.keys():
                clean = re.sub(r"^(SH|SZ|BJ)", "", s)
                symbols.append(clean)
            symbols = list(dict.fromkeys(symbols))
            logger.info("从 %s 候选池读取 %d 只标的", pool_type, len(symbols))
        
        # 执行价格位置分析
        results = analyze_price_position(
            db,
            symbols,
            lookback_days=lookback_days,
            min_lookback_days=lookback_days,
            top_n=top_n,
            name_map=name_map,
        )
        
        # 生成报告
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Markdown 报告
        md_path = output_dir / f"price_position_{now_str}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# 价格位置选股报告\n\n")
            f.write(f"生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
            f.write(f"统计天数: {lookback_days} 天\n")
            f.write(f"输出数量: Top {top_n}\n\n")
            f.write("---\n\n")
            
            f.write("## 候选股列表\n\n")
            f.write("| 排名 | 代码 | 名称 | 预估分数 | 收盘 | 历史区间 | 价格分位 | 涨幅空间 | 跌幅空间 | 解读 |\n")
            f.write("|------|------|------|---------|------|---------|---------|---------|---------|------|\n")
            
            for i, r in enumerate(results, 1):
                interpretation = "强势" if r.price_percentile > 0.8 else (
                    "超跌" if r.price_percentile < 0.2 else "中性"
                )
                name_str = r.name if hasattr(r, 'name') and r.name else '-'  # 获取股票名称
                f.write(f"| {i} | {r.symbol} | {name_str} | {r.estimated_score:.3f} | "
                        f"{r.latest_close:.2f} | [{r.hist_low:.2f}, {r.hist_high:.2f}] | "
                        f"{r.price_percentile:.1%} | {r.upside_pct:.1%} | "
                        f"{r.downside_pct:.1%} | {interpretation} |\n")
        
        logger.info("Markdown 报告已保存: %s", md_path)
        
        # JSON 结果
        json_path = output_dir / f"price_position_{now_str}.json"
        scorecards = [to_scorecard(r) for r in results]
        for i, sc in enumerate(scorecards, 1):
            sc["rank"] = i
        
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(scorecards, f, ensure_ascii=False, indent=2)
        
        logger.info("JSON 结果已保存: %s", json_path)
        
        return scorecards


def main():
    parser = argparse.ArgumentParser(
        description="基于历史价格位置的选股策略",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python run_price_position_screener.py                    # 默认参数，全量筛选 Top 100
  python run_price_position_screener.py --lookback 300     # 使用 300 天数据
  python run_price_position_screener.py --top-n 50         # 只输出 Top 50
        """,
    )
    parser.add_argument("--codes", nargs="+", help="手动指定要分析的标的代码列表")
    parser.add_argument("--pool-type", choices=["algo", "manual"], default="algo", help="从哪个候选池读取标的（默认 algo）")
    parser.add_argument("--all", action="store_true", dest="use_all_symbols", help="使用 kline.db 全量标的")
    parser.add_argument("--lookback", type=int, default=200, help="统计天数（默认 200）")
    parser.add_argument("--top-n", type=int, default=100, help="输出 Top N 候选股（默认 100）")
    parser.add_argument("--output-dir", type=str, help="报告输出目录")
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir) if args.output_dir else None
    
    results = run_price_position_screener(
        symbols=args.codes,
        pool_type=args.pool_type,
        use_all_symbols=args.use_all_symbols,
        lookback_days=args.lookback,
        top_n=args.top_n,
        output_dir=output_dir,
    )
    
    if not results:
        logger.warning("未找到满足条件的候选股，请检查 kline.db 数据是否充足")
        return 1
    
    print(f"\n✅ 选股完成：共 {len(results)} 只候选股")
    print(f"策略：价格位置分析（统计 {args.lookback} 天，风险厌恶权重 2.0）\n")
    print(f"{'排名':<4} {'代码':<8} {'名称':<10} {'预估分数':<10} {'收盘':<10} {'价格分位':<10} {'解读'}")
    print("-" * 80)
    for i, r in enumerate(results, 1):
        interpretation = "强势" if r["price_percentile"] > 0.8 else (
            "超跌" if r["price_percentile"] < 0.2 else "中性"
        )
        name_str = r.get('name', '') or '-'  # 获取股票名称
        print(f"{i:<4} {r['symbol']:<8} {name_str:<10} {r['estimated_score']:<10.3f} "
              f"{r['latest_close']:<10.2f} {r['price_percentile']:<10.1%} {interpretation}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())