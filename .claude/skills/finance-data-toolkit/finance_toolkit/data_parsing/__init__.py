# -*- coding: utf-8 -*-
"""
数据解析模块
提供统一的数据解析和转换功能
"""

from .parser import (
    DataParser,
    parse_tencent_quote,
    parse_sina_quote,
    parse_eastmoney_quote,
    parse_kline_data,
    parse_news_data,
    parse_sector_data,
    parser,
    parse_data,
)

__all__ = [
    'DataParser',
    'parse_tencent_quote',
    'parse_sina_quote',
    'parse_eastmoney_quote',
    'parse_kline_data',
    'parse_news_data',
    'parse_sector_data',
    'parser',
    'parse_data',
]
