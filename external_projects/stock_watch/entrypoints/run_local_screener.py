#!/usr/bin/env python
"""entrypoints/run_local_screener.py — 本地日K多因子选股引擎。

与 `run_screener.py`（问财自然语言查询）不同，本脚本完全本地运行，不依赖
网络：读取 `data/kline.db` 里已积累的日 K 数据，用 `indicators.py` 算出技术
信号，按综合评分排序输出 Top N 候选股。

用法：
    # 默认：从候选池标的中筛选 Top 10
    python entrypoints/run_local_screener.py

    # 指定标的列表
    python entrypoints/run_local_screener.py --codes 600519 000001 300750

    # 指定候选池文件（algo 或 manual）
    python entrypoints/run_local_screener.py --pool-type algo
    python entrypoints/run_local_screener.py --pool-type manual

    # 自定义 Top N 和输出目录
    python entrypoints/run_local_screener.py --top-n 20 --output-dir ./reports/screener

设计原则：
  - 全本地，不发起任何网络请求；数据来自 daily_kline_db.py 积累的 SQLite。
  - 多因子打分：MACD/RSI/KDJ/MA/布林带/量能 六维度加权求和，可配置权重。
  - 输出 Markdown 报告 + JSON 结果，供人工复核后加入候选池。
  - 兼容现有候选池格式，结果可直接导入 candidate_pool.json。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import _common  # noqa: F401 — 把项目根加入 sys.path

from stock_watch.config import ensure_dirs, load_config
from stock_watch.daily_kline_db import DailyKlineDB
from stock_watch.indicators import compute_price_signals, score_signals, top_signals
from stock_watch.signals import Signal

logger = logging.getLogger("stock_watch.local_screener")

# ── 因子权重配置（可调参）────────────────────────────────────────────────────
# 每种信号类型对综合评分的贡献权重；sum 不需要等于 1，仅用于相对排序。
DEFAULT_FACTOR_WEIGHTS: Dict[str, float] = {
    "ma_golden_cross":        8.0,
    "ma_death_cross":        -8.0,
    "macd_golden_cross":      7.0,
    "macd_death_cross":      -7.0,
    "rsi_overbought":        -5.0,
    "rsi_oversold":           5.0,
    "rsi_exit_overbought":    4.0,
    "rsi_exit_oversold":      4.0,
    "kdj_golden_cross":       6.0,
    "kdj_death_cross":      -6.0,
    "kdj_golden_cross_weak":  3.0,
    "kdj_death_cross_weak":  -3.0,
    "kdj_extreme_overbought": -4.0,
    "kdj_extreme_oversold":   4.0,
    "volume_spike":           6.0,
    "volume_spike_down":     -6.0,
    "bollinger_squeeze_breakout_up":    7.0,
    "bollinger_squeeze_breakout_down": -7.0,
}


def _build_signal_weight_map() -> Dict[str, float]:
    """返回 signal_name -> weight 的字典，用于查找单条信号的贡献分。"""
    return dict(DEFAULT_FACTOR_WEIGHTS)


# ── 核心：对单只标的计算多因子评分 ──────────────────────────────────────────

def analyze_symbol(
    db: DailyKlineDB,
    symbol: str,
    *,
    lookback_days: int = 120,
    min_lookback_days: int = 60,
) -> Optional[Dict[str, Any]]:
    """对单只标的计算技术指标信号 + 综合评分。

    返回 dict（含 score/reasons/top_signals 等），数据不足时返回 None。
    """
    df = db.get_kline(symbol, days=lookback_days)
    if df is None or len(df) < min_lookback_days:
        logger.debug("%s K线不足 %d 条（实际 %d），跳过", symbol, min_lookback_days, len(df) if df is not None else 0)
        return None

    # 用 indicators.py 算出所有信号
    signals = compute_price_signals(df)
    if not signals:
        return None

    total_score = score_signals(signals)
    top = top_signals(signals, n=5)

    return {
        "symbol": symbol,
        "rows": len(df),
        "latest_date": str(df["date"].iloc[-1]) if "date" in df.columns else "",
        "latest_close": float(df["close"].iloc[-1]) if "close" in df.columns else None,
        "total_score": round(total_score, 2),
        "signal_count": len(signals),
        "top_signals": [s.to_dict() for s in top],
        "all_signals": [s.to_dict() for s in signals],
    }


# ── 选股主流程 ───────────────────────────────────────────────────────────────

def run_local_screener(
    symbols: Optional[List[str]] = None,
    *,
    pool_type: str = "algo",
    top_n: int = 10,
    lookback_days: int = 120,
    min_lookback_days: int = 60,
    output_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """核心选股逻辑：分析给定标的列表，按综合评分排序返回 Top N。"""
    ensure_dirs()
    with DailyKlineDB() as db:
        info = db.table_info()
        logger.info("数据库状态: %s 只标的, %s 行, 最新日期 %s",
                    info["symbol_count"], info["total_rows"], info["latest_date"])

        # 确定分析范围
        if symbols is None:
            # 从候选池读取
            from stock_watch.candidate_pool import load_pool
            pool_path = (
                Path(__file__).resolve().parent.parent / "data"
                / ("algo_pool.json" if pool_type == "algo" else "manual_pool.json")
            )
            pool = load_pool(pool_path)
            raw_symbols = list(pool.keys())
            # 统一格式：去掉交易所前缀（如 SZ300059 -> 300059），与数据库一致
            import re
            symbols = []
            for s in raw_symbols:
                clean = re.sub(r"^(SH|SZ|BJ)", "", s)
                symbols.append(clean)
            symbols = list(dict.fromkeys(symbols))  # 去重，保留顺序
            logger.info("从 %s 候选池读取 %d 只标的（原始=%d，归一化=%d）", pool_type, len(symbols), len(raw_symbols), len(raw_symbols))

        # 批量分析
        results: List[Dict[str, Any]] = []
        skipped = 0
        for sym in symbols:
            r = analyze_symbol(
                db, sym,
                lookback_days=lookback_days,
                min_lookback_days=min_lookback_days,
            )
            if r is None:
                skipped += 1
                continue
            results.append(r)

        logger.info("分析完成: %d 只有足够数据, %d 只数据不足被跳过", len(results), skipped)

        # 按综合评分排序，取 Top N
        results.sort(key=lambda x: x["total_score"], reverse=True)
        top_results = results[:top_n]

        # 生成报告
        if output_dir is None:
            output_dir = (
                Path(__file__).resolve().parent.parent / "reports" / "screener"
            )
        output_dir.mkdir(parents=True, exist_ok=True)
        _save_report(output_dir, top_results, skipped, len(results))

        return top_results


def _save_report(
    output_dir: Path,
    results: List[Dict[str, Any]],
    skipped: int,
    total_with_data: int,
) -> Path:
    """生成 Markdown 报告 + JSON 结果文件。"""
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    ts_label = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Markdown 报告 ─────────────────────────────────────────────────────
    lines: List[str] = []
    lines.append(f"# 本地选股报告（日K多因子）")
    lines.append(f"")
    lines.append(f"生成时间: {ts_label}")
    lines.append(f"分析标的: {total_with_data + skipped} 只 | 有足够数据: {total_with_data} 只 | 数据不足跳过: {skipped} 只")
    lines.append(f"Top N: {len(results)}")
    lines.append(f"")
    lines.append("---")
    lines.append("")

    if not results:
        lines.append("> ⚠️ 无数据：请确认 kline.db 已积累足够历史 K 线数据，或手动指定 `--codes` 重试。")
    else:
        # 排名表
        lines.append("## Top 候选股")
        lines.append("")
        lines.append("| 排名 | 代码 | 最新收盘 | 综合评分 | 信号数 | 主要信号 |")
        lines.append("|------|------|---------|---------|-------|---------|")
        for i, r in enumerate(results, 1):
            sig_names = ", ".join(s["name"] for s in r["top_signals"][:3])
            close_str = f"{r['latest_close']:.2f}" if r.get("latest_close") else "-"
            lines.append(
                f"| {i} | {r['symbol']} | {close_str} | **{r['total_score']:.1f}** | {r['signal_count']} | {sig_names} |"
            )
        lines.append("")

        # 逐只详情
        lines.append("## 详细分析")
        for r in results:
            lines.append(f"### {r['symbol']}  （评分: {r['total_score']:.1f}）")
            lines.append("")
            lines.append(f"- 数据行数: {r['rows']}")
            lines.append(f"- 最新日期: {r.get('latest_date', '-')}")
            lines.append(f"- 最新收盘: {r.get('latest_close', '-')}")
            lines.append(f"- 触发信号: {r['signal_count']} 条")
            lines.append("")
            lines.append("| 信号名 | 分类 | 分数 | 说明 |")
            lines.append("|--------|------|-----|------|")
            for s in r["all_signals"]:
                lines.append(f"| {s['name']} | {s['category']} | {s['score']:+.1f} | {s['reason']} |")
            lines.append("")

    report_path = output_dir / f"local_screener_{now_str}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown 报告已保存: %s", report_path)

    # ── JSON 结果（供程序读取）────────────────────────────────────────────
    json_path = output_dir / f"local_screener_{now_str}.json"
    json_path.write_text(
        json.dumps({
            "generated_at": ts_label,
            "total_analyzed": total_with_data,
            "skipped": skipped,
            "top_n": len(results),
            "results": results,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("JSON 结果已保存: %s", json_path)

    return report_path


# ── 命令行入口 ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="本地日K多因子选股引擎（完全离线，依赖 data/kline.db）",
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        help="手动指定要分析的标的代码列表（覆盖 --pool-type）",
    )
    parser.add_argument(
        "--pool-type",
        choices=["algo", "manual"],
        default="algo",
        help="从哪个候选池读取标的（默认 algo）",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=10,
        help="输出 Top N 候选股（默认 10）",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=120,
        help="用于计算指标的历史天数（默认 120，建议 >= 60）",
    )
    parser.add_argument(
        "--min-days",
        type=int,
        default=60,
        help="最低所需 K 线天数，不足则跳过（默认 60）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="报告输出目录（默认 reports/screener）",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else None

    results = run_local_screener(
        symbols=args.codes,
        pool_type=args.pool_type,
        top_n=args.top_n,
        lookback_days=args.lookback_days,
        min_lookback_days=args.min_days,
        output_dir=output_dir,
    )

    if not results:
        logger.warning("未找到满足条件的候选股，请检查 kline.db 数据是否充足")
        return 1

    print(f"\n✅ 选股完成：共 {len(results)} 只候选股")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r['symbol']}  评分={r['total_score']:.1f}  收盘={r.get('latest_close', '-')}  信号数={r['signal_count']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("local_screener", main))
