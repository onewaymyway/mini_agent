# -*- coding: utf-8 -*-
"""
数据解析模块 v2 (Step 6)

提供 ABC 抽象基类 + 各数据类型专用解析器
"""

from .abstract import (
    DataParser,
    ParserRegistry,
    register_parser,
    get_registry,
    parse_raw_data,
    parse_data,
    # 通用工具
    _parse_float,
    _parse_int,
    _parse_date,
    _now_iso,
    _extract_jsonp,
    _to_records,
    _safe_iterrows,
)

# 注册表全局单例
from .abstract import registry

# 各类型解析器模块（按顺序导入以触发自动注册）
from . import quote_parser
from . import fund_nav_parser
from . import kline_parser
from . import sector_parser
from . import lhb_parser
from . import northbound_parser
from . import forex_parser
from . import crypto_parser
from . import commodity_parser
from . import macro_parser
from . import guba_parser
from . import news_parser
from . import etf_parser
from . import bond_parser

# 向后兼容导出
from .base_parsers import (
    DataParser as LegacyDataParser,
    parse_tencent_quote,
    parse_sina_quote,
    parse_eastmoney_quote,
    parse_kline_data,
    parse_news_data,
    parse_sector_data,
)

__all__ = [
    # ABC 抽象层
    'DataParser',
    'ParserRegistry',
    'registry',
    'register_parser',
    'get_registry',
    'parse_raw_data',
    'parse_data',
    # 通用工具
    '_parse_float',
    '_parse_int',
    '_parse_date',
    '_now_iso',
    '_extract_jsonp',
    '_to_records',
    '_safe_iterrows',
    # 已注册的解析器模块
    'quote_parser',
    'fund_nav_parser',
    'kline_parser',
    'sector_parser',
    'lhb_parser',
    'northbound_parser',
    'forex_parser',
    'crypto_parser',
    'commodity_parser',
    'macro_parser',
    'guba_parser',
    'news_parser',
    'etf_parser',
    'bond_parser',
    # 向后兼容
    'LegacyDataParser',
    'parse_tencent_quote',
    'parse_sina_quote',
    'parse_eastmoney_quote',
    'parse_kline_data',
    'parse_news_data',
    'parse_sector_data',
]
