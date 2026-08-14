# -*- coding: utf-8 -*-
"""
统一接口规范包

提供:
1. FinanceData 统一数据契约 (from core)
2. BaseFetcher / MultiSourceFetcher 抽象层
3. 便捷调用函数
4. 错误类型定义
5. 数据源配置
"""

# 核心数据契约
from ..core import FinanceData

# 统一接口实现
from .unified import (
    BaseFetcher,
    MultiSourceFetcher,
    CircuitBreaker,
    FallbackError,
    SourceUnavailableError,
    create_default_router,
    get_router,
    fetch_by_type,
    fetch_crypto_quote,
    fetch_bond_yield,
    fetch_fund_nav,
    fetch_forex_quote,
    fetch_futures_quote,
    get_source_status,
    _global_router,
)

# 数据源配置
from ..data_source_config import (
    DataType,
    DataSourceType,
    DataSourceMeta,
    DataSourceRegistry,
    FinancialDataTypeConfig,
)

# SOURCE_PRIORITY 直接从 data_source_config 导入，避免循环依赖
from ..data_source_config import SOURCE_PRIORITY


def _get_source_priority():
    return SOURCE_PRIORITY


# 符号格式转换 (延迟导入避免循环依赖)
def __getattr__(name):
    if name in ('to_standard_symbol', 'to_akshare_symbol', 'to_sina_symbol', 'to_eastmoney_symbol'):
        from ..interface import (
            to_standard_symbol,
            to_akshare_symbol,
            to_sina_symbol,
            to_eastmoney_symbol,
        )
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # 数据契约
    'FinanceData',
    # 抽象层
    'BaseFetcher',
    'MultiSourceFetcher',
    'CircuitBreaker',
    # 错误类型
    'FallbackError',
    'SourceUnavailableError',
    # 路由
    'create_default_router',
    'get_router',
    '_global_router',
    # 便捷函数
    'fetch_by_type',
    'fetch_crypto_quote',
    'fetch_bond_yield',
    'fetch_fund_nav',
    'fetch_forex_quote',
    'fetch_futures_quote',
    'get_source_status',
    # 配置
    'DataType',
    'DataSourceType',
    'DataSourceMeta',
    'DataSourceRegistry',
    'FinancialDataTypeConfig',
    # 符号转换
    'to_standard_symbol',
    'to_akshare_symbol',
    'to_sina_symbol',
    'to_eastmoney_symbol',
    # SOURCE_PRIORITY
    'SOURCE_PRIORITY',
    '_get_source_priority',
]
