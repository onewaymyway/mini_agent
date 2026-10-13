#!/usr/bin/env python
"""entrypoints/run_advanced_screener.py — 高级多因子选股引擎。

新策略设计：
  - 趋势维度：MA多头排列、MACD位置与斜率
  - 动量维度：RSI区间、KDJ交叉
  - 量能维度：成交量放大确认
  - 价格位置：距历史高点的距离
  - 综合评分：各维度加权求和，输出Top N

用法：
    python entrypoints/run_advanced_screener.py --all --top-n 20 --lookback 200
    python entrypoints/run_advanced_screener.py --pool algo --top-n 10
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import _common  # noqa: F401

from stock_watch.config import ensure_dirs
from stock_watch.daily_kline_db import DailyKlineDB
from stock_watch.indicators import compute_price_signals, top_signals
from stock_watch.signals import Signal

logger = logging.getLogger("stock_watch.advanced_screener")


# ── 高级策略评分权重 ──────────────────────────────────────────────────────────
# 趋势得分：MA多头排列、MACD位置
# 动量得分：RSI区间、KDJ状态
# 量能得分：成交量放大倍数
# 价格位置得分：距历史高点的距离
ADVANCED_WEIGHTS = {
    # 趋势维度 (满分 30)
    "trend_ma_alignment": 15.0,      # MA多头排列
    "trend_macd_position": 10.0,     # MACD在零轴上方
    "trend_macd_slope": 5.0,         # MACD斜率向上
    
    # 动量维度 (满分 20)
    "momentum_rsi_bullish": 10.0,    # RSI在50-70强势区
    "momentum_rsi_overbought": -10.0,# RSI超买
    "momentum_kdj_golden": 5.0,      # KDJ金叉
    "momentum_kdj_death": -5.0,      # KDJ死叉
    
    # 量能维度 (满分 15)
    "volume_expand": 10.0,           # 放量
    "volume_shrink": -5.0,           # 缩量
    
    # 价格位置维度 (满分 35)
    "price_near_high": 20.0,         # 接近200日高点
    "price_near_low": -15.0,         # 接近200日低点
    "price_in_middle": 5.0,          # 中间位置
}


def load_name_mapping(pool_type: str = "algo") -> Dict[str, str]:
    """从数据库加载股票名称映射（优先），候选池文件作为补充。"""
    name_map: Dict[str, str] = {}
    
    # 1. 先从数据库加载
    try:
        with DailyKlineDB() as db:
            # 检查是否有 name 列
            cursor = db.conn.execute('PRAGMA table_info(kline)')
            columns = [col[1] for col in cursor.fetchall()]
            
            if "name" in columns:
                # 从数据库加载所有有名称的股票
                cursor = db.conn.execute('SELECT DISTINCT symbol, name FROM kline WHERE name != ""')
                for symbol, name in cursor.fetchall():
                    name_map[symbol] = name
                logger.info("从数据库加载 %d 只股票名称", len(name_map))
    except Exception as e:
        logger.debug("从数据库加载名称失败: %s", e)
    
    # 2. 从候选池补充（数据库中可能没有的股票）
    try:
        pool_path = (
            Path(__file__).resolve().parent.parent / "data"
            / ("algo_pool.json" if pool_type == "algo" else "manual_pool.json")
        )
        if pool_path.exists():
            with pool_path.open("r", encoding="utf-8") as f:
                pool = json.load(f)
            for key, entry in pool.items():
                if isinstance(entry, dict):
                    code = re.sub(r"^(SH|SZ|BJ)", "", key)
                    name = entry.get("name", "")
                else:
                    code = re.sub(r"^(SH|SZ|BJ)", "", str(key))
                    name = getattr(entry, "name", "")
                if code and name and code not in name_map:
                    name_map[code] = name
            logger.info("从候选池补充 %d 只股票名称", len(name_map))
    except Exception as e:
        logger.debug("从候选池加载名称失败: %s", e)
    
    return name_map


def analyze_symbol_advanced(
    db: DailyKlineDB,
    symbol: str,
    *,
    lookback_days: int = 200,
    min_lookback_days: int = 100,
    name_map: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """使用高级策略分析单只标的。
    
    返回包含详细得分拆解的字典，数据不足时返回 None。
    """
    df = db.get_kline(symbol, days=lookback_days)
    if df is None or len(df) < min_lookback_days:
        return None
    
    close_col = "close" if "close" in df.columns else "收盘"
    volume_col = "volume" if "volume" in df.columns else "成交量"
    high_col = "high" if "high" in df.columns else "最高"
    low_col = "low" if "low" in df.columns else "最低"
    
    if close_col not in df.columns:
        return None
    
    close = df[close_col].astype(float)
    volume = df[volume_col].astype(float) if volume_col in df.columns else pd.Series([0.0] * len(df))
    high = df[high_col].astype(float) if high_col in df.columns else close
    low = df[low_col].astype(float) if low_col in df.columns else close
    
    # ── 趋势得分 ─────────────────────────────────────────────────────────
    trend_score = 0.0
    trend_signals: List[str] = []
    
    # MA多头排列检查
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    
    if len(ma5) >= 60:
        # 检查最近5日均线是否都在上升趋势
        ma5_up = ma5.iloc[-1] > ma5.iloc[-5]
        ma10_up = ma10.iloc[-1] > ma10.iloc[-5]
        ma20_up = ma20.iloc[-1] > ma20.iloc[-5]
        
        if ma5_up and ma10_up and ma20_up:
            # 完全多头排列
            trend_score += ADVANCED_WEIGHTS["trend_ma_alignment"]
            trend_signals.append("MA多头排列")
        elif ma5.iloc[-1] > ma20.iloc[-1]:
            # 部分多头
            trend_score += ADVANCED_WEIGHTS["trend_ma_alignment"] * 0.5
            trend_signals.append("MA部分多头")
    
    # MACD位置与斜率
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    
    if len(macd) >= 10:
        # MACD在零轴上方
        if dif.iloc[-1] > 0 and dea.iloc[-1] > 0:
            trend_score += ADVANCED_WEIGHTS["trend_macd_position"]
            trend_signals.append("MACD零轴上方")
        elif dif.iloc[-1] < 0:
            trend_score += ADVANCED_WEIGHTS["trend_macd_position"] * -0.5
        
        # MACD斜率
        macd_slope = macd.iloc[-1] - macd.iloc[-5]
        if macd_slope > 0:
            trend_score += ADVANCED_WEIGHTS["trend_macd_slope"]
            trend_signals.append("MACD上升")
    
    # ── 动量得分 ─────────────────────────────────────────────────────────
    momentum_score = 0.0
    momentum_signals: List[str] = []
    
    # RSI
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    
    if len(rsi) >= 14:
        rsi_val = rsi.iloc[-1]
        if 50 <= rsi_val <= 70:
            momentum_score += ADVANCED_WEIGHTS["momentum_rsi_bullish"]
            momentum_signals.append(f"RSI={rsi_val:.0f}(强势)")
        elif rsi_val > 70:
            momentum_score += ADVANCED_WEIGHTS["momentum_rsi_overbought"]
            momentum_signals.append(f"RSI={rsi_val:.0f}(超买)")
        elif rsi_val < 30:
            momentum_score += ADVANCED_WEIGHTS["momentum_rsi_bullish"] * 0.5
            momentum_signals.append(f"RSI={rsi_val:.0f}(超卖)")
    
    # KDJ
    low_n = low.rolling(9).min()
    high_n = high.rolling(9).max()
    rsv = (close - low_n) / (high_n - low_n).replace(0, 1e-10) * 100
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3 * k - 2 * d
    
    if len(k) >= 10:
        if k.iloc[-1] > d.iloc[-1] and k.iloc[-2] <= d.iloc[-2]:
            momentum_score += ADVANCED_WEIGHTS["momentum_kdj_golden"]
            momentum_signals.append("KDJ金叉")
        elif k.iloc[-1] < d.iloc[-1] and k.iloc[-2] >= d.iloc[-2]:
            momentum_score += ADVANCED_WEIGHTS["momentum_kdj_death"]
            momentum_signals.append("KDJ死叉")
    
    # ── 量能得分 ─────────────────────────────────────────────────────────
    volume_score = 0.0
    volume_signals: List[str] = []
    
    vol_ma20 = volume.rolling(20).mean()
    if len(vol_ma20) >= 20 and vol_ma20.iloc[-1] > 0:
        vol_ratio = volume.iloc[-1] / vol_ma20.iloc[-1]
        if vol_ratio >= 1.5:
            volume_score += ADVANCED_WEIGHTS["volume_expand"]
            volume_signals.append(f"放量{vol_ratio:.1f}倍")
        elif vol_ratio <= 0.5:
            volume_score += ADVANCED_WEIGHTS["volume_shrink"]
            volume_signals.append(f"缩量{vol_ratio:.1f}倍")
    
    # ── 价格位置得分 ─────────────────────────────────────────────────────
    price_score = 0.0
    price_signals: List[str] = []
    
    hist_high = high.rolling(200).max().iloc[-1] if len(high) >= 200 else high.max()
    hist_low = low.rolling(200).min().iloc[-1] if len(low) >= 200 else low.min()
    current_close = close.iloc[-1]
    
    if hist_high > hist_low:
        price_position = (current_close - hist_low) / (hist_high - hist_low)
        if price_position >= 0.8:
            price_score += ADVANCED_WEIGHTS["price_near_high"]
            price_signals.append(f"距高点{100-price_position*100:.0f}%")
        elif price_position <= 0.2:
            price_score += ADVANCED_WEIGHTS["price_near_low"]
            price_signals.append(f"距低点{price_position*100:.0f}%")
        else:
            price_score += ADVANCED_WEIGHTS["price_in_middle"]
            price_signals.append(f"位置{price_position*100:.0f}%")
    
    # ── 综合评分 ─────────────────────────────────────────────────────────
    total_score = trend_score + momentum_score + volume_score + price_score
    
    all_signals = (
        [f"[{s}]" for s in trend_signals] +
        [f"[{s}]" for s in momentum_signals] +
        [f"[{s}]" for s in volume_signals] +
        [f"[{s}]" for s in price_signals]
    )
    
    # 获取股票名称
    stock_name = ""
    if name_map and symbol in name_map:
        stock_name = name_map[symbol]
    
    return {
        "symbol": symbol,
        "name": stock_name,
        "total_score": round(total_score, 2),
        "trend_score": round(trend_score, 2),
        "momentum_score": round(momentum_score, 2),
        "volume_score": round(volume_score, 2),
        "price_score": round(price_score, 2),
        "all_signals": all_signals,
        "signal_count": len(all_signals),
        "latest_close": float(current_close),
        "latest_date": str(df["date"].iloc[-1]) if "date" in df.columns else "",
        "hist_high": float(hist_high),
        "hist_low": float(hist_low),
        "rows": len(df),
    }


def run_advanced_screener(
    symbols: Optional[List[str]] = None,
    *,
    pool_type: str = "algo",
    use_all_symbols: bool = False,
    top_n: int = 10,
    lookback_days: int = 200,
    min_lookback_days: int = 100,
    output_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """核心选股逻辑：使用高级策略分析给定标的列表。"""
    ensure_dirs()
    
    # 加载股票名称映射
    name_map = load_name_mapping(pool_type)
    logger.info("已加载 %d 只股票名称映射", len(name_map))
    
    with DailyKlineDB() as db:
        info = db.table_info()
        logger.info("数据库状态: %s 只标的, %s 行, 最新日期 %s",
                    info["symbol_count"], info["total_rows"], info["latest_date"])
        
        # 确定分析范围
        if symbols is None:
            if use_all_symbols:
                symbols = db.get_all_symbols()
                logger.info("从 kline.db 读取全量标的 %d 只", len(symbols))
            else:
                from stock_watch.candidate_pool import load_pool
                pool_path = (
                    Path(__file__).resolve().parent.parent / "data"
                    / ("algo_pool.json" if pool_type == "algo" else "manual_pool.json")
                )
                pool = load_pool(pool_path)
                raw_symbols = list(pool.keys())
                symbols = []
                for s in raw_symbols:
                    clean = re.sub(r"^(SH|SZ|BJ)", "", s)
                    symbols.append(clean)
                symbols = list(dict.fromkeys(symbols))
                logger.info("从 %s 候选池读取 %d 只标的", pool_type, len(symbols))
        
        # 批量分析
        results: List[Dict[str, Any]] = []
        skipped = 0
        for sym in symbols:
            r = analyze_symbol_advanced(
                db, sym,
                lookback_days=lookback_days,
                min_lookback_days=min_lookback_days,
                name_map=name_map,
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
            output_dir = Path(__file__).resolve().parent.parent / "reports" / "advanced_screener"
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
    
    lines: List[str] = []
    lines.append("# 高级多因子选股报告")
    lines.append("")
    lines.append(f"生成时间: {ts_label}")
    lines.append(f"分析标的: {total_with_data + skipped} 只 | 有足够数据: {total_with_data} 只 | 数据不足跳过: {skipped} 只")
    lines.append(f"Top N: {len(results)}")
    lines.append(f"策略: 趋势+动量+量能+价格位置 四维度综合评分")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    if not results:
        lines.append("> ⚠️ 无数据：请确认 kline.db 已积累足够历史 K 线数据。")
    else:
        # 排名表
        lines.append("## Top 候选股")
        lines.append("")
        lines.append("| 排名 | 代码 | 名称 | 综合评分 | 趋势分 | 动量分 | 量能分 | 位置分 | 收盘 | 信号 |")
        lines.append("|------|------|------|---------|-------|-------|-------|-------|------|------|")
        for i, r in enumerate(results, 1):
            name_str = r.get('name') or '-'
            close_str = f"{r['latest_close']:.2f}"
            sig_str = ", ".join(r["all_signals"][:3])
            lines.append(
                f"| {i} | {r['symbol']} | {name_str} | **{r['total_score']:.1f}** | "
                f"{r['trend_score']:.1f} | {r['momentum_score']:.1f} | "
                f"{r['volume_score']:.1f} | {r['price_score']:.1f} | {close_str} | {sig_str} |"
            )
        lines.append("")
        
        # 逐只详情
        lines.append("## 详细分析")
        for r in results:
            lines.append(f"### {r['symbol']} {r.get('name', '')}（评分: {r['total_score']:.1f}）")
            lines.append("")
            lines.append(f"- 综合评分: {r['total_score']:.1f}")
            lines.append(f"- 趋势得分: {r['trend_score']:.1f}")
            lines.append(f"- 动量得分: {r['momentum_score']:.1f}")
            lines.append(f"- 量能得分: {r['volume_score']:.1f}")
            lines.append(f"- 价格位置: {r['price_score']:.1f}")
            lines.append(f"- 最新收盘: {r['latest_close']:.2f}")
            lines.append(f"- 历史高点: {r['hist_high']:.2f}")
            lines.append(f"- 历史低点: {r['hist_low']:.2f}")
            lines.append(f"- 触发信号: {', '.join(r['all_signals'])}")
            lines.append("")
    
    report_path = output_dir / f"advanced_screener_{now_str}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Markdown 报告已保存: %s", report_path)
    
    json_path = output_dir / f"advanced_screener_{now_str}.json"
    json_path.write_text(
        json.dumps({
            "generated_at": ts_label,
            "total_analyzed": total_with_data,
            "skipped": skipped,
            "top_n": len(results),
            "strategy": "advanced_multi_factor",
            "results": results,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    logger.info("JSON 结果已保存: %s", json_path)
    
    return report_path


# ── 命令行入口 ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="高级多因子选股引擎（趋势+动量+量能+价格位置）",
    )
    parser.add_argument(
        "--codes",
        nargs="+",
        help="手动指定要分析的标的代码列表",
    )
    parser.add_argument(
        "--pool-type",
        choices=["algo", "manual"],
        default="algo",
        help="从哪个候选池读取标的（默认 algo）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="use_all_symbols",
        help="使用 kline.db 全量标的（5151 只）",
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
        default=200,
        help="用于计算指标的历史天数（默认 200）",
    )
    parser.add_argument(
        "--min-days",
        type=int,
        default=100,
        help="最低所需 K 线天数，不足则跳过（默认 100）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        help="报告输出目录（默认 reports/advanced_screener）",
    )
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir) if args.output_dir else None
    
    results = run_advanced_screener(
        symbols=args.codes,
        pool_type=args.pool_type,
        use_all_symbols=args.use_all_symbols,
        top_n=args.top_n,
        lookback_days=args.lookback_days,
        min_lookback_days=args.min_days,
        output_dir=output_dir,
    )
    
    if not results:
        logger.warning("未找到满足条件的候选股，请检查 kline.db 数据是否充足")
        return 1
    
    print(f"\n✅ 选股完成：共 {len(results)} 只候选股")
    print(f"策略: 趋势+动量+量能+价格位置 四维度综合评分\n")
    print(f"{'排名':<4} {'代码':<8} {'名称':<10} {'综合分':<8} {'趋势':<6} {'动量':<6} {'量能':<6} {'位置':<6} {'收盘':<8} {'信号'}")
    print("-" * 100)
    for i, r in enumerate(results, 1):
        name_str = r.get('name') or '-'
        sig_str = ", ".join(r["all_signals"][:2])
        print(f"{i:<4} {r['symbol']:<8} {name_str:<10} {r['total_score']:<8.1f} "
              f"{r['trend_score']:<6.1f} {r['momentum_score']:<6.1f} "
              f"{r['volume_score']:<6.1f} {r['price_score']:<6.1f} "
              f"{r['latest_close']:<8.2f} {sig_str}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(_common.run_entrypoint("advanced_screener", main))
