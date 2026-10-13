"""stock_watch/price_position.py — 基于历史价格位置的选股策略。

策略思路：
  - 统计最近 N 天（默认 200 天）的历史最高价、最低价
  - 计算当前价格相对于历史区间的位置
  - 预估风险收益比：上涨空间 - 2×下跌空间（风险厌恶型权重）
  - 筛选出"接近历史高点、远离历史低点"的强势股

适用场景：趋势跟踪、动量选股
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from stock_watch.daily_kline_db import DailyKlineDB
from stock_watch.signals import Signal

logger = logging.getLogger("stock_watch.price_position")


@dataclass
class PricePositionResult:
    """单只标的的价格位置分析结果。"""
    symbol: str
    name: str = ""  # 新增股票名称
    latest_close: float = 0.0
    hist_high: float = 0.0
    hist_low: float = 0.0
    days: int = 0
    
    # 核心指标
    upside_pct: float = 0.0      # 涨幅空间 = (最高价 - 当前价) / 当前价
    downside_pct: float = 0.0    # 跌幅空间 = (当前价 - 最低价) / 当前价
    price_percentile: float = 0.0  # 价格分位 = 当前价 / 最高价
    estimated_score: float = 0.0   # 预估分数 = 涨幅空间 - 2×跌幅空间


def analyze_price_position(
    db: DailyKlineDB,
    symbols: List[str],
    *,
    lookback_days: int = 200,
    min_lookback_days: int = 200,
    top_n: int = 100,
    name_map: Optional[Dict[str, str]] = None,
) -> List[PricePositionResult]:
    """批量分析标的的价格位置，返回按预估分数排序的 Top N 结果。
    
    Args:
        db: K线数据库连接
        symbols: 要分析的标的列表
        lookback_days: 统计天数（默认 200）
        min_lookback_days: 最低数据要求（默认等于 lookback_days）
        top_n: 输出数量
        name_map: 股票代码到名称的映射字典
    
    Returns:
        按 estimated_score 降序排列的 PricePositionResult 列表
    """
    results: List[PricePositionResult] = []
    skipped = 0
    
    for symbol in symbols:
        df = db.get_kline(symbol, days=lookback_days)
        
        if df is None or len(df) < min_lookback_days:
            skipped += 1
            logger.debug("%s 数据不足 %d 条（实际 %d），跳过", 
                        symbol, min_lookback_days, len(df) if df is not None else 0)
            continue
        
        # 使用最近 lookback_days 天的数据
        hist_high = float(df['high'].max())
        hist_low = float(df['low'].min())
        latest_close = float(df['close'].iloc[-1])
        
        # 边界检查：最高价/最低价不能为0或负
        if hist_high <= 0 or hist_low <= 0 or latest_close <= 0:
            skipped += 1
            continue
        
        # 核心指标计算
        upside_pct = (hist_high - latest_close) / latest_close
        downside_pct = (latest_close - hist_low) / latest_close
        price_percentile = latest_close / hist_high
        
        # 预估分数：上涨空间 - 2×下跌空间
        # 负权重表示风险厌恶，即使上涨空间大，下跌风险也要加倍考虑
        estimated_score = upside_pct - 2 * downside_pct
        
        # 获取股票名称
        stock_name = ""
        if name_map and symbol in name_map:
            stock_name = name_map[symbol]
        
        results.append(PricePositionResult(
            symbol=symbol,
            name=stock_name,
            latest_close=latest_close,
            hist_high=hist_high,
            hist_low=hist_low,
            days=len(df),
            upside_pct=upside_pct,
            downside_pct=downside_pct,
            price_percentile=price_percentile,
            estimated_score=estimated_score,
        ))
    
    # 按预估分数降序排序
    results.sort(key=lambda x: x.estimated_score, reverse=True)
    
    logger.info("价格位置分析完成: %d 只有足够数据, %d 只数据不足被跳过", 
                len(results), skipped)
    
    return results[:top_n]


def format_result(result: PricePositionResult) -> str:
    """格式化单只标的的分析结果为可读字符串。"""
    return (
        f"{result.symbol} | 评分={result.estimated_score:.3f} | "
        f"收盘={result.latest_close:.2f} | 区间=[{result.hist_low:.2f}, {result.hist_high:.2f}] | "
        f"分位={result.price_percentile:.1%} | "
        f"涨幅空间={result.upside_pct:.1%} | 跌幅空间={result.downside_pct:.1%}"
    )


def to_scorecard(result: PricePositionResult) -> Dict:
    """转换为 JSON 友好的字典格式，用于报告输出。"""
    return {
        "symbol": result.symbol,
        "name": result.name,  # 新增名称字段
        "rank": 0,  # 由调用方填充
        "estimated_score": round(result.estimated_score, 4),
        "latest_close": round(result.latest_close, 2),
        "hist_high": round(result.hist_high, 2),
        "hist_low": round(result.hist_low, 2),
        "price_percentile": round(result.price_percentile, 4),
        "upside_pct": round(result.upside_pct, 4),
        "downside_pct": round(result.downside_pct, 4),
        "days": result.days,
        "interpretation": _interpret_position(result),
    }


def _interpret_position(result: PricePositionResult) -> str:
    """根据价格位置给出简短解读。"""
    if result.price_percentile > 0.9:
        return "接近历史高点，强势股"
    elif result.price_percentile > 0.7:
        return "价格偏高，趋势向上"
    elif result.price_percentile < 0.1:
        return "接近历史低点，超跌反弹机会"
    elif result.price_percentile < 0.3:
        return "价格偏低，观望为主"
    else:
        return "区间中部，无明确方向"
