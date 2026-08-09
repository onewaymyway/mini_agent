"""
期货数据抓取模块

支持:
- 期货实时行情
- 期货历史K线
- 期货持仓数据
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import akshare as ak
except ImportError:
    ak = None


@dataclass
class FuturesSpot:
    """期货实时行情数据结构"""
    symbol: str
    name: str
    last_price: float
    change: float
    change_pct: float
    volume: int
    open_interest: int
    high: float
    low: float
    open: float
    prev_close: float
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "last_price": self.last_price,
            "change": self.change,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "high": self.high,
            "low": self.low,
            "open": self.open,
            "prev_close": self.prev_close,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class FuturesKline:
    """期货K线数据结构"""
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    open_interest: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "open_interest": self.open_interest,
        }


@dataclass
class FuturesPosition:
    """期货持仓数据结构"""
    symbol: str
    date: str
    long_position: int
    short_position: int
    change: int
    total_position: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "long_position": self.long_position,
            "short_position": self.short_position,
            "change": self.change,
            "total_position": self.total_position,
        }


def fetch_futures_spot(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取期货实时行情

    Args:
        symbol: 期货合约代码，如 'IF2406'，None 表示获取全部

    Returns:
        期货行情列表
    """
    if ak is None:
        logger.warning("akshare 未安装，无法获取期货数据")
        return []

    try:
        # 获取期货实时行情
        df = ak.futures_spot_price()
        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            spot = FuturesSpot(
                symbol=str(row.get("symbol", "")),
                name=str(row.get("name", "")),
                last_price=float(row.get("last_price", 0)),
                change=float(row.get("change", 0)),
                change_pct=float(row.get("change_pct", 0)),
                volume=int(row.get("volume", 0)),
                open_interest=int(row.get("open_interest", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                open=float(row.get("open", 0)),
                prev_close=float(row.get("prev_close", 0)),
                timestamp=datetime.now(),
            )
            result.append(spot.to_dict())

        return result
    except Exception as e:
        logger.error(f"获取期货行情失败: {e}")
        return []


def fetch_futures_kline(
    symbol: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    获取期货历史K线

    Args:
        symbol: 期货合约代码，如 'IF2406'
        start_date: 开始日期，格式 'YYYYMMDD'
        end_date: 结束日期，格式 'YYYYMMDD'

    Returns:
        K线数据列表
    """
    if ak is None:
        logger.warning("akshare 未安装，无法获取期货K线数据")
        return []

    try:
        # 获取期货历史数据
        df = ak.futures_main_sina(symbol=symbol)
        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            kline = FuturesKline(
                symbol=symbol,
                date=str(row.get("date", "")),
                open=float(row.get("open", 0)),
                high=float(row.get("high", 0)),
                low=float(row.get("low", 0)),
                close=float(row.get("close", 0)),
                volume=int(row.get("volume", 0)),
                open_interest=int(row.get("open_interest", 0)),
            )
            result.append(kline.to_dict())

        return result
    except Exception as e:
        logger.error(f"获取期货K线失败: {e}")
        return []


def fetch_futures_position(symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取期货持仓数据

    Args:
        symbol: 期货合约代码，如 'IF2406'，None 表示获取全部

    Returns:
        持仓数据列表
    """
    if ak is None:
        logger.warning("akshare 未安装，无法获取期货持仓数据")
        return []

    try:
        # 获取期货持仓数据
        df = ak.futures_position_detail()
        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            position = FuturesPosition(
                symbol=str(row.get("symbol", "")),
                date=str(row.get("date", "")),
                long_position=int(row.get("long_position", 0)),
                short_position=int(row.get("short_position", 0)),
                change=int(row.get("change", 0)),
                total_position=int(row.get("total_position", 0)),
            )
            result.append(position.to_dict())

        return result
    except Exception as e:
        logger.error(f"获取期货持仓失败: {e}")
        return []


class FuturesDataFetcher:
    """期货数据获取便捷类"""

    def __init__(self):
        self.symbol = None

    def get_futures_spot(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取期货实时行情"""
        return fetch_futures_spot(symbol)

    def get_futures_kline(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """获取期货历史K线"""
        return fetch_futures_kline(symbol, start_date, end_date)

    def get_futures_position(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取期货持仓数据"""
        return fetch_futures_position(symbol)

    def get_futures_summary(self) -> Dict[str, Any]:
        """获取期货数据摘要"""
        spot = fetch_futures_spot()
        position = fetch_futures_position()

        return {
            "spot_count": len(spot),
            "position_count": len(position),
            "spot_sample": spot[:5] if spot else [],
            "position_sample": position[:5] if position else [],
        }
