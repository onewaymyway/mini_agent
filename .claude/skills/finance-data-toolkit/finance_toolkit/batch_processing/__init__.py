"""
批量处理模块
提供统一的批量数据获取接口
"""

from .batch_fetcher import (
    batch_fetch_stocks,
    batch_fetch_klines,
    fetch_single_stock,
    fetch_single_kline,
)

__all__ = [
    'batch_fetch_stocks',
    'batch_fetch_klines',
    'fetch_single_stock',
    'fetch_single_kline',
]