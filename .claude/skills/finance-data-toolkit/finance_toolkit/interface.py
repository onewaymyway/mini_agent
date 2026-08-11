# -*- coding: utf-8 -*-
"""
统一数据抓取接口

提供统一的 DataFetcher 基类和便捷函数，封装多源数据抓取逻辑。

使用示例：
    from finance_toolkit.interface import DataFetcher, fetch_all
    
    # 使用统一接口
    fetcher = DataFetcher()
    quotes = fetcher.fetch_quote(['600000.SH', '000001.SZ'])
    
    # 使用便捷函数
    from finance_toolkit.interface import fetch_realtime_quote, fetch_kline
    quotes = fetch_realtime_quote(['600000.SH'])
"""

import logging
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass, asdict
from datetime import datetime

from .exceptions import (
    FinanceError,
    SourceUnavailableError,
    SourceRateLimitedError,
    DataNotFoundError,
    DataQualityError,
    CircuitBreakerError,
    FallbackError,
)
from .resilience import CircuitBreaker, FallbackManager, retry_with_backoff
from .validation import validate_quote_data, validate_kline_data

logger = logging.getLogger(__name__)


# ============== 统一数据契约 ==============

@dataclass
class FinanceData:
    """统一金融数据契约"""
    source: str                    # 数据源标识
    data_type: str                 # 数据类型
    symbol: str                    # 标的代码
    timestamp: str                 # 数据时间戳 (ISO 8601)
    payload: Dict[str, Any]        # 数据载荷
    raw: Optional[Dict] = None     # 原始响应数据
    meta: Optional[Dict] = None    # 元数据
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)
    
    def get(self, key: str, default: Any = None) -> Any:
        """便捷获取 payload 中的字段"""
        return self.payload.get(key, default)


# ============== 符号格式转换 ==============

def to_standard_symbol(code: str) -> str:
    """转换为标准格式: 600000 -> 600000.SH"""
    code = code.strip()
    if '.' in code:
        return code.upper()
    if code.startswith(('60', '68', '90')):
        return f'{code}.SH'
    else:
        return f'{code}.SZ'


def to_akshare_symbol(code: str) -> str:
    """转换为AKShare格式: 600000.SH -> 600000"""
    return code.split('.')[0]


def to_sina_symbol(code: str) -> str:
    """转换为新浪格式: 600000.SH -> sh600000"""
    code = code.split('.')[0]
    if code.startswith(('60', '68', '90')):
        return f'sh{code}'
    else:
        return f'sz{code}'


def to_eastmoney_symbol(code: str) -> str:
    """转换为东方财富格式: 600000.SH -> sh600000"""
    return to_sina_symbol(code)


# ============== 数据源优先级配置 ==============

SOURCE_PRIORITY = {
    'quote': ['akshare', 'tencent', 'sina', 'eastmoney', 'netease'],
    'kline': ['akshare', 'sina'],
    'financial': ['akshare', 'tushare'],
    'fund': ['akshare', 'eastmoney'],
    'bond': ['akshare', 'eastmoney'],
    'futures': ['akshare', 'eastmoney'],
    'index': ['akshare', 'eastmoney'],
    'macro': ['akshare', 'eastmoney'],
    'news': ['eastmoney', 'sina'],
    'sentiment': ['guba', 'eastmoney'],
}


# ============== 统一数据抓取器 ==============

class DataFetcher:
    """
    统一数据获取器
    
    支持多数据源自动切换、熔断保护、降级策略。
    
    使用示例：
        fetcher = DataFetcher(default_source='akshare')
        quotes = fetcher.fetch_quote(['600000.SH', '000001.SZ'])
        klines = fetcher.fetch_kline('600000.SH', period='daily')
    """
    
    def __init__(self, default_source: str = 'akshare'):
        self.default_source = default_source
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._initialize_circuit_breakers()
    
    def _initialize_circuit_breakers(self):
        """初始化默认熔断器"""
        for source in SOURCE_PRIORITY.get('quote', []):
            self.circuit_breakers[source] = CircuitBreaker(
                source=source,
                failure_threshold=5,
                reset_timeout=60
            )
    
    def fetch_quote(
        self,
        symbols: List[str],
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """
        获取实时行情
        
        Args:
            symbols: 股票代码列表
            source: 数据源，默认使用配置
        
        Returns:
            List[FinanceData]
        """
        from .data_fetching.fetchers import fetch_realtime_quote
        return fetch_realtime_quote(symbols, source or self.default_source)
    
    def fetch_kline(
        self,
        symbol: str,
        period: str = 'daily',
        start: str = '20240101',
        end: Optional[str] = None,
        adjust: str = 'qfq',
        source: Optional[str] = None
    ) -> List[Dict]:
        """
        获取K线数据
        
        Args:
            symbol: 股票代码
            period: 周期 daily/weekly/monthly/1m/5m/15m/30m/60m
            start: 开始日期 YYYYMMDD
            end: 结束日期 YYYYMMDD
            adjust: 复权方式 qfq/hfq/不复权
            source: 数据源
        
        Returns:
            List[Dict]
        """
        from .data_fetching.fetchers import fetch_kline
        return fetch_kline(symbol, period, start, end, adjust, source or self.default_source)
    
    def fetch_financial(
        self,
        symbol: str,
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取财务报表数据"""
        from .data_fetching.fetchers import fetch_financial
        return fetch_financial(symbol, source or self.default_source)
    
    def fetch_dividend(
        self,
        symbol: str,
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取分红数据"""
        from .data_fetching.fetchers import fetch_dividend
        return fetch_dividend(symbol, source or self.default_source)
    
    def fetch_lhb(
        self,
        symbol: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取龙虎榜数据"""
        from .data_fetching.fetchers import fetch_lhb
        return fetch_lhb(symbol, start_date, end_date, source or self.default_source)
    
    def fetch_northbound(
        self,
        symbol: Optional[str] = None,
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取北向资金数据"""
        from .data_fetching.fetchers import fetch_northbound
        return fetch_northbound(symbol, source or self.default_source)
    
    def fetch_stock_basic(
        self,
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取股票基础信息"""
        from .data_fetching.fetchers import fetch_stock_basic
        return fetch_stock_basic(source or self.default_source)
    
    def fetch_fund(
        self,
        symbol: str,
        data_type: str = 'nav',
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取基金数据"""
        from .data_fetching.fetchers import fetch_fund
        return fetch_fund(symbol, data_type, source or self.default_source)
    
    def fetch_bond(
        self,
        symbol: str,
        data_type: str = 'yield',
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取债券数据"""
        from .data_fetching.fetchers import fetch_bond
        return fetch_bond(symbol, data_type, source or self.default_source)
    
    def fetch_futures(
        self,
        symbol: str,
        data_type: str = 'quote',
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取期货数据"""
        from .data_fetching.fetchers import fetch_futures
        return fetch_futures(symbol, data_type, source or self.default_source)
    
    def fetch_index(
        self,
        symbol: str,
        data_type: str = 'quote',
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取指数数据"""
        from .data_fetching.fetchers import fetch_index
        return fetch_index(symbol, data_type, source or self.default_source)
    
    def fetch_macro(
        self,
        data_type: str = 'gdp',
        source: Optional[str] = None
    ) -> List[FinanceData]:
        """获取宏观经济数据"""
        from .data_fetching.fetchers import fetch_macro
        return fetch_macro(data_type, source or self.default_source)
    
    def reset_circuit_breaker(self, source: str):
        """手动重置指定数据源的熔断器"""
        if source in self.circuit_breakers:
            self.circuit_breakers[source].reset()
            logger.info(f"熔断器 [{source}] 已手动重置")
    
    def get_circuit_breaker_status(self) -> Dict[str, str]:
        """获取所有熔断器状态"""
        return {
            source: cb.state
            for source, cb in self.circuit_breakers.items()
        }


# ============== 便捷函数 ==============

# 创建默认实例
default_fetcher = DataFetcher()


# ============== 批量抓取 ==============

def fetch_all(
    symbols: List[str],
    data_types: List[str] = None,
    source: Optional[str] = None
) -> Dict[str, List[FinanceData]]:
    """
    批量抓取多种数据类型
    
    Args:
        symbols: 股票代码列表
        data_types: 数据类型列表 ['quote', 'kline', 'financial', ...]
        source: 数据源
    
    Returns:
        Dict[data_type, List[FinanceData]]
    """
    data_types = data_types or ['quote', 'kline']
    results = {}
    
    fetcher = DataFetcher(source or 'akshare')
    
    if 'quote' in data_types:
        results['quote'] = fetcher.fetch_quote(symbols)
    
    if 'kline' in data_types:
        results['kline'] = {}
        for sym in symbols:
            results['kline'][sym] = fetcher.fetch_kline(sym)
    
    return results


if __name__ == '__main__':
    # 测试
    logger.info("测试统一接口...")
    
    fetcher = DataFetcher()
    
    # 测试实时行情
    logger.info("测试实时行情...")
    quotes = fetcher.fetch_quote(['600000.SH', '000001.SZ'])
    for q in quotes:
        logger.info(f"{q.symbol}: {q.get('close')} ({q.get('change_pct')}%)")
    
    # 测试K线
    logger.info("\n测试K线...")
    klines = fetcher.fetch_kline('600000.SH', period='daily', start='20240101')
    logger.info(f"获取 {len(klines)} 条K线")
    if klines:
        logger.info(f"最新: {klines[-1]}")
    
    # 测试批量抓取
    logger.info("\n测试批量抓取...")
    all_data = fetch_all(['600000.SH'], data_types=['quote', 'kline'])
    logger.info(f"quote: {len(all_data.get('quote', []))} 条")
    logger.info(f"kline: {len(all_data.get('kline', {}).get('600000.SH', []))} 条")
