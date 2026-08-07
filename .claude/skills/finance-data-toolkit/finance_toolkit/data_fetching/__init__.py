"""
数据抓取模块
提供统一的数据获取接口
"""

from .fetchers import (
    fetch_realtime_quote,
    fetch_kline,
    fetch_financial,
    fetch_dividend,
    fetch_lhb,
    fetch_northbound,
    fetch_stock_basic,
    DataFetcher,
    default_fetcher,
)

# 异步抓取器
try:
    from .async_fetchers import (
        async_fetch_realtime_quote,
        async_fetch_kline,
        async_fetch_multiple_stocks,
        fetch_with_fallback,
        AsyncHTTPClient,
        AsyncFinanceData,
    )
except ImportError:
    pass

from .eastmoney_fetcher import fetch_stock_data as fetch_eastmoney_quote
from .sina_kline_fetcher import analyze_stock as fetch_sina_kline
from .guba_scraper import (
    GubaPost,
    GubaComment,
    GubaUserProfile,
    EastmoneyGubaAPI,
    GubaCDPScraper,
    fetch_guba_posts,
    fetch_guba_hot_posts,
    fetch_guba_post_detail,
    fetch_guba_comments,
    fetch_guba_user_profile,
    sync_fetch_guba_posts,
    sync_fetch_guba_hot_posts,
)

# 扩展数据获取
from .extended_fetchers import (
    fetch_forex_quote,
    fetch_cny_rates,
    fetch_crypto_quote,
    fetch_crypto_rank,
    fetch_etf_quote,
    fetch_etf_kline,
    ExtendedDataFetcher,
    extended_fetcher,
)

__all__ = [
    # 同步函数
    'fetch_realtime_quote',
    'fetch_kline',
    'fetch_financial',
    'fetch_dividend',
    'fetch_lhb',
    'fetch_northbound',
    'fetch_stock_basic',
    'DataFetcher',
    'default_fetcher',
    'fetch_eastmoney_quote',
    'fetch_sina_kline',
    
    # 异步函数
    'async_fetch_realtime_quote',
    'async_fetch_kline',
    'async_fetch_multiple_stocks',
    'fetch_with_fallback',
    'AsyncHTTPClient',
    'AsyncFinanceData',
    
    # 股吧
    'GubaPost',
    'GubaComment',
    'GubaUserProfile',
    'EastmoneyGubaAPI',
    'GubaCDPScraper',
    'fetch_guba_posts',
    'fetch_guba_hot_posts',
    'fetch_guba_post_detail',
    'fetch_guba_comments',
    'fetch_guba_user_profile',
    'sync_fetch_guba_posts',
    'sync_fetch_guba_hot_posts',
    
    # 扩展数据获取
    'fetch_forex_quote',
    'fetch_cny_rates',
    'fetch_crypto_quote',
    'fetch_crypto_rank',
    'fetch_etf_quote',
    'fetch_etf_kline',
    'ExtendedDataFetcher',
    'extended_fetcher',
]