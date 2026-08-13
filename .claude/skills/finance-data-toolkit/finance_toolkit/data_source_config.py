# -*- coding: utf-8 -*-
"""
统一数据源配置管理

定义所有支持的金融数据类型及其数据源、接口、优先级等元信息
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Set
from pathlib import Path
import json

logger = logging.getLogger(__name__)


class DataType(Enum):
    """金融数据类型枚举"""
    # 股票
    STOCK_QUOTE = "stock_quote"
    STOCK_KLINE = "stock_kline"
    STOCK_FINANCIAL = "stock_financial"
    STOCK_DIVIDEND = "stock_dividend"
    STOCK_LHB = "stock_lhb"
    STOCK_NORTHBOUND = "stock_northbound"
    STOCK_BASIC = "stock_basic"
    STOCK_SECTOR = "stock_sector"
    STOCK_CAPITAL_FLOW = "stock_capital_flow"
    
    # 基金
    FUND_ETF_QUOTE = "fund_etf_quote"
    FUND_ETF_KLINE = "fund_etf_kline"
    FUND_LOF_QUOTE = "fund_lof_quote"
    FUND_OPEN_NAV = "fund_open_nav"
    FUND_HOLDINGS = "fund_holdings"
    FUND_RANK = "fund_rank"
    FUND_LIST = "fund_list"
    
    # 债券
    BOND_YIELD = "bond_yield"
    BOND_CONVERTIBLE = "bond_convertible"
    BOND_CORPORATE = "bond_corporate"
    BOND_GOVERNMENT = "bond_government"
    
    # 加密货币
    CRYPTO_QUOTE = "crypto_quote"
    CRYPTO_KLINE = "crypto_kline"
    CRYPTO_RANK = "crypto_rank"
    CRYPTO_TRENDING = "crypto_trending"
    CRYPTO_ORDERBOOK = "crypto_orderbook"
    
    # 外汇
    FOREX_QUOTE = "forex_quote"
    FOREX_CNY = "forex_cny"
    FOREX_HISTORICAL = "forex_historical"
    
    # 宏观经济
    MACRO_GDP = "macro_gdp"
    MACRO_CPI = "macro_cpi"
    MACRO_PMI = "macro_pmi"
    MACRO_INTEREST_RATE = "macro_interest_rate"
    MACRO_MONEY_SUPPLY = "macro_money_supply"
    MACRO_EXCHANGE_RATE = "macro_exchange_rate"
    MACRO_UNEMPLOYMENT = "macro_unemployment"
    MACRO_TRADE = "macro_trade"
    MACRO_FRED_GDP = "macro_fred_gdp"
    MACRO_FRED_CPI = "macro_fred_cpi"
    
    # 期货期权
    FUTURE_QUOTE = "future_quote"
    FUTURE_KLINE = "future_kline"
    OPTION_QUOTE = "option_quote"
    OPTION_GREEKS = "option_greeks"
    
    # 指数
    INDEX_QUOTE = "index_quote"
    INDEX_KLINE = "index_kline"
    
    # 商品
    COMMODITY_QUOTE = "commodity_quote"
    COMMODITY_FUTURES = "commodity_futures"
    COMMODITY_GOLD = "commodity_gold"
    COMMODITY_CRUDE = "commodity_crude"
    COMMODITY_DXY = "commodity_dxy"


class DataSourceType(Enum):
    """数据源类型枚举"""
    AKSHARE = "akshare"
    EASTMONEY = "eastmoney"
    SINA = "sina"
    TENCENT = "tencent"
    NETEASE = "netease"
    COINGECKO = "coingecko"
    BINANCE = "binance"
    YAHOO = "yahoo"
    NORTHBOUND = "northbound"
    FRED = "fred"


@dataclass
class DataSourceMeta:
    """数据源元数据"""
    name: str
    display_name: str
    type: DataSourceType
    base_url: str
    is_free: bool = True
    requires_auth: bool = False
    rate_limit: int = 10  # 每秒请求次数限制
    timeout: float = 30.0
    priority: int = 0  # 优先级，越小越高
    health_check_url: Optional[str] = None
    
    # 支持的DataType列表
    supported_types: List[DataType] = field(default_factory=list)
    
    # 配置项
    api_key: Optional[str] = None
    proxy: Optional[str] = None
    custom_headers: Dict[str, str] = field(default_factory=dict)


class DataSourceRegistry:
    """数据源注册表"""
    
    _sources: Dict[str, DataSourceMeta] = {}
    
    @classmethod
    def register(cls, meta: DataSourceMeta):
        """注册数据源"""
        cls._sources[meta.name] = meta
        logger.debug(f"数据源已注册: {meta.name} -> {[dt.value for dt in meta.supported_types]}")
    
    @classmethod
    def get(cls, name: str) -> Optional[DataSourceMeta]:
        """获取数据源"""
        return cls._sources.get(name)
    
    @classmethod
    def list_all(cls) -> Dict[str, DataSourceMeta]:
        """列出所有数据源"""
        return cls._sources.copy()
    
    @classmethod
    def get_supported_by_type(cls, data_type: DataType) -> List[DataSourceMeta]:
        """获取支持某类型的所有数据源（按优先级排序）"""
        sources = [
            src for src in cls._sources.values()
            if data_type in src.supported_types
        ]
        return sorted(sources, key=lambda x: x.priority)
    
    @classmethod
    def get_enabled(cls) -> List[DataSourceMeta]:
        """获取所有已注册的数据源"""
        return list(cls._sources.values())


class FinancialDataTypeConfig:
    """金融数据类型配置"""
    
    _config: Dict[DataType, Dict[str, Any]] = {}
    
    @classmethod
    def define(cls, data_type: DataType, config: Dict[str, Any]):
        """定义数据类型配置"""
        cls._config[data_type] = config
    
    @classmethod
    def get(cls, data_type: DataType) -> Optional[Dict[str, Any]]:
        """获取数据类型配置"""
        return cls._config.get(data_type)
    
    @classmethod
    def list_all(cls) -> Dict[DataType, Dict[str, Any]]:
        """列出所有数据类型配置"""
        return cls._config.copy()


# ============== 内置数据源定义 ==============

def _register_builtin_sources():
    """注册内置数据源"""
    
    # AKShare - 免费开源Python财经数据接口
    akshare = DataSourceMeta(
        name="akshare",
        display_name="AKShare",
        type=DataSourceType.AKSHARE,
        base_url="https://akshare.akfamily.xyz",
        is_free=True,
        requires_auth=False,
        rate_limit=5,
        timeout=30.0,
        priority=1,
        supported_types=[
            DataType.STOCK_QUOTE, DataType.STOCK_KLINE, DataType.STOCK_FINANCIAL,
            DataType.STOCK_DIVIDEND, DataType.STOCK_LHB, DataType.STOCK_NORTHBOUND,
            DataType.STOCK_BASIC, DataType.STOCK_SECTOR, DataType.STOCK_CAPITAL_FLOW,
            DataType.FUND_ETF_QUOTE, DataType.FUND_ETF_KLINE, DataType.FUND_LOF_QUOTE,
            DataType.FUND_OPEN_NAV, DataType.FUND_HOLDINGS, DataType.FUND_RANK,
            DataType.BOND_YIELD, DataType.BOND_CONVERTIBLE, DataType.BOND_CORPORATE,
            DataType.CRYPTO_QUOTE, DataType.CRYPTO_KLINE, DataType.CRYPTO_RANK,
            DataType.COMMODITY_GOLD, DataType.COMMODITY_CRUDE,
            DataType.FOREX_QUOTE, DataType.FOREX_CNY,
            DataType.MACRO_GDP, DataType.MACRO_CPI, DataType.MACRO_PMI,
            DataType.MACRO_INTEREST_RATE, DataType.MACRO_MONEY_SUPPLY,
            DataType.MACRO_EXCHANGE_RATE, DataType.MACRO_UNEMPLOYMENT,
            DataType.MACRO_TRADE, DataType.INDEX_QUOTE, DataType.INDEX_KLINE,
        ],
        custom_headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )
    DataSourceRegistry.register(akshare)
    
    # 东方财富
    eastmoney = DataSourceMeta(
        name="eastmoney",
        display_name="东方财富",
        type=DataSourceType.EASTMONEY,
        base_url="https://push2.eastmoney.com",
        is_free=True,
        requires_auth=False,
        rate_limit=10,
        timeout=15.0,
        priority=2,
        supported_types=[
            DataType.STOCK_QUOTE, DataType.STOCK_KLINE, DataType.STOCK_FINANCIAL,
            DataType.STOCK_DIVIDEND, DataType.STOCK_LHB,
            DataType.FUND_ETF_QUOTE, DataType.FUND_ETF_KLINE,
            DataType.BOND_CONVERTIBLE, DataType.INDEX_QUOTE,
        ],
        custom_headers={
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )
    DataSourceRegistry.register(eastmoney)
    
    # 新浪财经
    sina = DataSourceMeta(
        name="sina",
        display_name="新浪财经",
        type=DataSourceType.SINA,
        base_url="https://hq.sinajs.cn",
        is_free=True,
        requires_auth=False,
        rate_limit=10,
        timeout=10.0,
        priority=3,
        supported_types=[
            DataType.STOCK_QUOTE, DataType.STOCK_KLINE,
            DataType.FOREX_QUOTE, DataType.CRYPTO_QUOTE,
            DataType.INDEX_QUOTE,
        ],
        custom_headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )
    DataSourceRegistry.register(sina)
    
    # 腾讯财经
    tencent = DataSourceMeta(
        name="tencent",
        display_name="腾讯财经",
        type=DataSourceType.TENCENT,
        base_url="https://qt.gtimg.cn",
        is_free=True,
        requires_auth=False,
        rate_limit=10,
        timeout=10.0,
        priority=4,
        supported_types=[
            DataType.STOCK_QUOTE, DataType.FUND_ETF_QUOTE,
            DataType.INDEX_QUOTE,
        ],
        custom_headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )
    DataSourceRegistry.register(tencent)
    
    # CoinGecko
    coingecko = DataSourceMeta(
        name="coingecko",
        display_name="CoinGecko",
        type=DataSourceType.COINGECKO,
        base_url="https://api.coingecko.com/api/v3",
        is_free=True,
        requires_auth=False,
        rate_limit=2,  # 免费API限流
        timeout=30.0,
        priority=5,
        supported_types=[
            DataType.CRYPTO_QUOTE, DataType.CRYPTO_KLINE,
            DataType.CRYPTO_RANK, DataType.CRYPTO_TRENDING,
        ],
    )
    DataSourceRegistry.register(coingecko)
    
    # Binance
    binance = DataSourceMeta(
        name="binance",
        display_name="Binance",
        type=DataSourceType.BINANCE,
        base_url="https://api.binance.com/api/v3",
        is_free=True,
        requires_auth=False,
        rate_limit=5,
        timeout=15.0,
        priority=6,
        supported_types=[
            DataType.CRYPTO_QUOTE, DataType.CRYPTO_KLINE,
            DataType.CRYPTO_ORDERBOOK,
        ],
    )
    DataSourceRegistry.register(binance)
    
    # Yahoo Finance
    yahoo = DataSourceMeta(
        name="yahoo",
        display_name="Yahoo Finance",
        type=DataSourceType.YAHOO,
        base_url="https://query1.finance.yahoo.com/v8",
        is_free=True,
        requires_auth=False,
        rate_limit=3,
        timeout=30.0,
        priority=7,
        supported_types=[
            DataType.STOCK_QUOTE, DataType.STOCK_KLINE,
            DataType.STOCK_FINANCIAL,
            DataType.CRYPTO_QUOTE, DataType.FOREX_QUOTE,
            DataType.INDEX_QUOTE,
            DataType.COMMODITY_QUOTE,
        ],
    )
    DataSourceRegistry.register(yahoo)

    # FRED - 美联储经济数据（宏观数据补充）
    fred = DataSourceMeta(
        name="fred",
        display_name="FRED (Federal Reserve Economic Data)",
        type=DataSourceType.FRED,
        base_url="https://api.stlouisfed.org/fred",
        is_free=True,
        requires_auth=True,  # 需要 API Key
        rate_limit=2,
        timeout=30.0,
        priority=8,
        supported_types=[
            DataType.MACRO_FRED_GDP,
            DataType.MACRO_FRED_CPI,
            DataType.MACRO_UNEMPLOYMENT,
            DataType.MACRO_TRADE,
        ],
    )
    DataSourceRegistry.register(fred)

    # 上海黄金交易所
    shfe = DataSourceMeta(
        name="shfe",
        display_name="上海黄金交易所",
        type=DataSourceType.EASTMONEY,
        base_url="https://www.sge.com.cn",
        is_free=True,
        requires_auth=False,
        rate_limit=5,
        timeout=15.0,
        priority=9,
        supported_types=[
            DataType.COMMODITY_GOLD,
        ],
    )
    DataSourceRegistry.register(shfe)


def _define_data_type_configs():
    """定义各数据类型配置"""
    configs = {
        DataType.STOCK_QUOTE: {
            "description": "A股实时行情",
            "required_fields": ["code", "name", "price", "change_pct", "volume", "amount"],
            "optional_fields": ["open", "high", "low", "pre_close", "turnover", "pe", "pb", "total_mv", "circ_mv"],
            "update_frequency": "实时",
            "cache_ttl": 5,  # 秒
        },
        DataType.STOCK_KLINE: {
            "description": "A股历史K线",
            "required_fields": ["date", "open", "high", "low", "close", "volume", "amount"],
            "optional_fields": ["change_pct", "amplitude", "turnover"],
            "update_frequency": "日/周/月",
            "cache_ttl": 3600,
        },
        DataType.FUND_ETF_QUOTE: {
            "description": "ETF实时行情",
            "required_fields": ["code", "name", "price", "change_pct"],
            "optional_fields": ["nav", "premium", "volume", "amount"],
            "update_frequency": "实时",
            "cache_ttl": 5,
        },
        DataType.BOND_YIELD: {
            "description": "国债收益率曲线",
            "required_fields": ["date", "yield_1y", "yield_5y", "yield_10y"],
            "optional_fields": ["yield_2y", "yield_3y", "yield_7y"],
            "update_frequency": "日",
            "cache_ttl": 86400,
        },
        DataType.CRYPTO_QUOTE: {
            "description": "加密货币实时行情",
            "required_fields": ["symbol", "price", "market_cap", "volume_24h"],
            "optional_fields": ["price_change_24h", "high_24h", "low_24h", "circulating_supply"],
            "update_frequency": "实时",
            "cache_ttl": 10,
        },
        DataType.MACRO_GDP: {
            "description": "中国GDP数据",
            "required_fields": ["quarter", "gdp", "yoy"],
            "optional_fields": ["first_industry", "second_industry", "third_industry"],
            "update_frequency": "季度",
            "cache_ttl": 86400 * 7,
        },
        DataType.MACRO_CPI: {
            "description": "CPI数据",
            "required_fields": ["date", "cpi", "yoy"],
            "optional_fields": ["mom", "food", "core_cpi"],
            "update_frequency": "月",
            "cache_ttl": 86400 * 3,
        },
        DataType.MACRO_UNEMPLOYMENT: {
            "description": "失业率数据",
            "required_fields": ["date", "urban_unemployment"],
            "optional_fields": ["urban_sampled"],
            "update_frequency": "月",
            "cache_ttl": 86400 * 3,
        },
        DataType.MACRO_TRADE: {
            "description": "贸易收支数据",
            "required_fields": ["date", "export", "import", "balance"],
            "optional_fields": ["export_yoy", "import_yoy", "balance_yoy"],
            "update_frequency": "月",
            "cache_ttl": 86400 * 3,
        },
        DataType.CRYPTO_RANK: {
            "description": "加密货币市值排行",
            "required_fields": ["rank", "symbol", "price", "market_cap"],
            "optional_fields": ["volume_24h", "price_change_24h", "circulating_supply"],
            "update_frequency": "实时",
            "cache_ttl": 30,
        },
        DataType.COMMODITY_GOLD: {
            "description": "黄金现货行情",
            "required_fields": ["symbol", "price", "buy_price", "time"],
            "optional_fields": ["high", "low", "change_pct"],
            "update_frequency": "实时",
            "cache_ttl": 10,
        },
        DataType.COMMODITY_CRUDE: {
            "description": "原油期货行情",
            "required_fields": ["symbol", "price", "change_pct"],
            "optional_fields": ["high", "low", "volume", "open_interest"],
            "update_frequency": "实时",
            "cache_ttl": 10,
        },
        DataType.COMMODITY_DXY: {
            "description": "美元指数行情",
            "required_fields": ["symbol", "price", "change_pct"],
            "optional_fields": ["high", "low", "open"],
            "update_frequency": "实时",
            "cache_ttl": 10,
        },
    }
    
    for data_type, config in configs.items():
        FinancialDataTypeConfig.define(data_type, config)


# 初始化
_register_builtin_sources()
_define_data_type_configs()


# ============== 便捷接口 ==============

def get_source_for_type(data_type: DataType) -> List[DataSourceMeta]:
    """获取支持某类型的数据源列表（按优先级排序）"""
    return DataSourceRegistry.get_supported_by_type(data_type)


def get_source_by_name(name: str) -> Optional[DataSourceMeta]:
    """根据名称获取数据源"""
    return DataSourceRegistry.get(name)


def list_all_data_types() -> List[DataType]:
    """列出所有支持的数据类型"""
    return list(FinancialDataTypeConfig.list_all().keys())


def get_all_sources() -> Dict[str, DataSourceMeta]:
    """获取所有已注册的数据源"""
    return DataSourceRegistry.list_all()


if __name__ == "__main__":
    print("=== 数据源注册表 ===")
    for name, src in get_all_sources().items():
        print(f"{name}: {src.display_name} (priority={src.priority}, free={src.is_free})")
        print(f"  支持: {', '.join([dt.value for dt in src.supported_types[:5]])}{'...' if len(src.supported_types) > 5 else ''}")
    
    print("\n=== 数据类型列表 ===")
    for dt in list_all_data_types():
        config = FinancialDataTypeConfig.get(dt)
        if config:
            sources = get_source_for_type(dt)
            print(f"{dt.value}: {config['description']} (支持源: {[s.name for s in sources]})")
