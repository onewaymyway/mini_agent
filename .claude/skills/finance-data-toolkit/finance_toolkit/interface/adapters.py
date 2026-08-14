# -*- coding: utf-8 -*-
"""
数据源适配器 - 将现有 Fetcher 适配到统一 BaseFetcher 接口
"""

import logging
from typing import List, Dict, Any, Optional

from .unified import BaseFetcher

logger = logging.getLogger(__name__)


class CryptoFetcherAdapter(BaseFetcher):
    """加密货币数据源适配器"""
    
    SOURCE_NAME = 'akshare'
    SUPPORTED_TYPES = ['crypto_quote', 'crypto_kline', 'crypto_rank', 'crypto_trending']
    
    def __init__(self):
        self._inner = None
    
    def _ensure_inner(self):
        if self._inner is None:
            from ..data_fetching.crypto_fetchers import CryptoFetcher
            self._inner = CryptoFetcher()
    
    def get_source_name(self) -> str:
        return self.SOURCE_NAME
    
    def get_supported_types(self) -> List[str]:
        return self.SUPPORTED_TYPES
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        self._ensure_inner()
        if data_type == 'crypto_quote':
            return self._inner.get_quote(symbols, **kwargs)
        elif data_type == 'crypto_kline':
            sym = symbols[0] if symbols else 'BTC'
            return self._inner.get_kline(sym, **kwargs)
        elif data_type == 'crypto_rank':
            return self._inner.get_rank(**kwargs)
        elif data_type == 'crypto_trending':
            return self._inner.get_trending(**kwargs)
        return []
    
    def health_check(self) -> bool:
        try:
            self._ensure_inner()
            result = self._inner.get_quote(['BTC'])
            return bool(result)
        except Exception:
            return False


class BondFetcherAdapter(BaseFetcher):
    """债券数据源适配器"""
    
    SOURCE_NAME = 'akshare'
    SUPPORTED_TYPES = ['bond_yield', 'bond_convertible', 'bond_corporate']
    
    def __init__(self):
        self._inner = None
    
    def _ensure_inner(self):
        if self._inner is None:
            from ..data_fetching.bond_fetcher import BondDataFetcher
            self._inner = BondDataFetcher()
    
    def get_source_name(self) -> str:
        return self.SOURCE_NAME
    
    def get_supported_types(self) -> List[str]:
        return self.SUPPORTED_TYPES
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        self._ensure_inner()
        if data_type == 'bond_yield':
            return self._inner.get_bond_yield()
        elif data_type == 'bond_convertible':
            return self._inner.get_convertible_bond_spot()
        elif data_type == 'bond_corporate':
            return self._inner.get_corporate_bond_spot()
        return []
    
    def health_check(self) -> bool:
        try:
            self._ensure_inner()
            result = self._inner.get_bond_yield()
            return bool(result)
        except Exception:
            return False


class FundFetcherAdapter(BaseFetcher):
    """基金数据源适配器"""
    
    SOURCE_NAME = 'akshare'
    SUPPORTED_TYPES = ['fund_etf_quote', 'fund_etf_kline', 'fund_lof_quote', 
                       'fund_open_nav', 'fund_holdings', 'fund_rank', 'fund_list']
    
    def __init__(self):
        self._inner = None
    
    def _ensure_inner(self):
        if self._inner is None:
            from ..data_fetching.fund_fetcher import FundDataFetcher
            self._inner = FundDataFetcher()
    
    def get_source_name(self) -> str:
        return self.SOURCE_NAME
    
    def get_supported_types(self) -> List[str]:
        return self.SUPPORTED_TYPES
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        self._ensure_inner()
        if data_type == 'fund_etf_quote':
            return self._inner.get_etf_quote(symbols)
        elif data_type == 'fund_etf_kline':
            sym = symbols[0] if symbols else '510300'
            return self._inner.get_etf_kline(sym)
        elif data_type == 'fund_lof_quote':
            return self._inner.get_lof_quote(symbols)
        elif data_type == 'fund_open_nav':
            code = symbols[0] if symbols else '000001'
            return self._inner.get_fund_nav(code)
        elif data_type == 'fund_holdings':
            code = symbols[0] if symbols else '000001'
            return self._inner.get_fund_holdings(code)
        elif data_type == 'fund_rank':
            return self._inner.get_fund_rank()
        elif data_type == 'fund_list':
            return self._inner.get_fund_list()
        return []
    
    def health_check(self) -> bool:
        try:
            self._ensure_inner()
            result = self._inner.get_fund_list()
            return bool(result)
        except Exception:
            return False


class FuturesFetcherAdapter(BaseFetcher):
    """期货数据源适配器"""
    
    SOURCE_NAME = 'akshare'
    SUPPORTED_TYPES = ['future_quote', 'future_kline']
    
    def __init__(self):
        self._inner = None
    
    def _ensure_inner(self):
        if self._inner is None:
            from ..data_fetching.futures_fetcher import FuturesDataFetcher
            self._inner = FuturesDataFetcher()
    
    def get_source_name(self) -> str:
        return self.SOURCE_NAME
    
    def get_supported_types(self) -> List[str]:
        return self.SUPPORTED_TYPES
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        self._ensure_inner()
        if data_type == 'future_quote':
            sym = symbols[0] if symbols else None
            return self._inner.get_futures_spot(sym)
        elif data_type == 'future_kline':
            sym = symbols[0] if symbols else 'IF2406'
            return self._inner.get_futures_kline(sym)
        return []
    
    def health_check(self) -> bool:
        try:
            self._ensure_inner()
            result = self._inner.get_futures_spot()
            return bool(result)
        except Exception:
            return False


class ForexFetcherAdapter(BaseFetcher):
    """外汇数据源适配器"""
    
    SOURCE_NAME = 'akshare'
    SUPPORTED_TYPES = ['forex_quote', 'forex_cny']
    
    def __init__(self):
        self._inner = None
    
    def _ensure_inner(self):
        if self._inner is None:
            from ..data_fetching.forex_fetcher import ForexFetcher
            self._inner = ForexFetcher()
    
    def get_source_name(self) -> str:
        return self.SOURCE_NAME
    
    def get_supported_types(self) -> List[str]:
        return self.SUPPORTED_TYPES
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        self._ensure_inner()
        if data_type == 'forex_quote':
            return self._inner.fetch_quote(symbols)
        elif data_type == 'forex_cny':
            return self._inner.fetch_cny_rate()
        return []
    
    def health_check(self) -> bool:
        try:
            self._ensure_inner()
            result = self._inner.health_check()
            return bool(result)
        except Exception:
            return False


class ExtendedFetcherAdapter(BaseFetcher):
    """扩展数据源适配器（外汇、加密货币、ETF）"""
    
    SOURCE_NAME = 'extended'
    SUPPORTED_TYPES = ['forex_quote', 'forex_cny', 'crypto_quote', 'crypto_rank', 'etf_quote', 'etf_kline']
    
    def __init__(self):
        self._inner = None
    
    def _ensure_inner(self):
        if self._inner is None:
            from ..data_fetching.extended_fetchers import ExtendedDataFetcher
            self._inner = ExtendedDataFetcher()
    
    def get_source_name(self) -> str:
        return self.SOURCE_NAME
    
    def get_supported_types(self) -> List[str]:
        return self.SUPPORTED_TYPES
    
    def fetch(self, data_type: str, symbols: List[str], **kwargs) -> List[Any]:
        self._ensure_inner()
        if data_type == 'forex_quote':
            return self._inner.get_forex_quote(symbols)
        elif data_type == 'forex_cny':
            return self._inner.get_cny_rates()
        elif data_type == 'crypto_quote':
            return self._inner.get_crypto_quote(symbols)
        elif data_type == 'crypto_rank':
            return self._inner.get_crypto_rank()
        elif data_type == 'etf_quote':
            return self._inner.get_etf_quote(symbols)
        elif data_type == 'etf_kline':
            sym = symbols[0] if symbols else '510300'
            return self._inner.get_etf_kline(sym)
        return []
    
    def health_check(self) -> bool:
        try:
            self._ensure_inner()
            result = self._inner.get_crypto_quote(['BTC'])
            return bool(result)
        except Exception:
            return False


# 适配器注册表
ADAPTERS = {
    'crypto': CryptoFetcherAdapter,
    'bond': BondFetcherAdapter,
    'fund': FundFetcherAdapter,
    'futures': FuturesFetcherAdapter,
    'forex': ForexFetcherAdapter,
    'extended': ExtendedFetcherAdapter,
}


def get_adapter(name: str) -> Optional[BaseFetcher]:
    """获取指定名称的适配器"""
    cls = ADAPTERS.get(name)
    if cls:
        return cls()
    return None


def register_all_adapters(router) -> None:
    """注册所有适配器到路由"""
    for name, cls in ADAPTERS.items():
        try:
            adapter = cls()
            router.register(adapter)
            logger.info(f"已注册适配器: {name}")
        except Exception as e:
            logger.warning(f"注册适配器 {name} 失败: {e}")
