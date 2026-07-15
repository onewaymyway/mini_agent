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

__all__ = [
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
]