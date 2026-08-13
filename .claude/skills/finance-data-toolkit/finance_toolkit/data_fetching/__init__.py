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
    fetch_sector_quote,
    fetch_sector_flow,
    fetch_margin_data,
    fetch_capital_flow,
    fetch_ipo_data,
    fetch_convertible_bond,
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

# 板块数据抓取
from .sector_fetcher import (
    fetch_sector_quote,
    fetch_sector_list,
    fetch_sector_flow,
    SectorFetcher,
    sector_fetcher,
)

# 大宗交易数据抓取
from .block_trade_fetcher import (
    fetch_block_trade_detail,
    fetch_block_trade_sector,
    fetch_block_trade_seat,
    BlockTradeFetcher,
    block_trade_fetcher,
)

# 股东数据抓取
from .shareholder_fetcher import (
    fetch_shareholder_count,
    fetch_institution_hold,
    fetch_top10_shareholder,
    ShareholderFetcher,
    shareholder_fetcher,
)

# 新闻数据抓取
from .news_fetcher import (
    fetch_fenghuang_news,
    fetch_fenghuang_hot_news,
    fetch_sina_news,
    fetch_eastmoney_news,
    fetch_stock_news,
    NewsFetcher,
    news_fetcher,
)

# 新增数据源
from .fenghuang_fetcher import (
    fetch_fenghuang_quote,
    fetch_fenghuang_news,
    fetch_fenghuang_hot_news,
    FenghuangFetcher,
    fenghuang_fetcher,
)
from .sina_news_fetcher import (
    fetch_sina_news,
    fetch_sina_stock_news,
    fetch_sina_hot_news,
    SinaNewsFetcher,
    sina_news_fetcher,
)
from .macro_fetcher import (
    fetch_gdp_data,
    fetch_cpi_data,
    fetch_pmi_data,
    fetch_interest_rate_data,
    fetch_money_supply_data,
    fetch_exchange_rate_data,
    fetch_unemployment_data,
    fetch_trade_data,
    fetch_all_macro_data,
    MacroFetcher,
    macro_fetcher,
)
from .crypto_fetchers import (
    fetch_crypto_quote,
    fetch_crypto_kline,
    fetch_crypto_rank,
    fetch_crypto_trending,
    CryptoFetcher,
    crypto_fetcher,
)
from .commodity_fetchers import (
    fetch_gold_quote,
    fetch_gold_kline,
    fetch_crude_oil_quote,
    fetch_nymex_wti_quote,
    fetch_dxy_quote,
    fetch_lme_metal_quote,
    CommodityFetcher,
    commodity_fetcher,
)

# 股票数据抓取
from ..scrapers.stock_scraper import (
    StockScraper,
    fetch_stock_quote,
    fetch_stock_kline,
    fetch_stock_financial,
    fetch_stock_dividend,
    fetch_stock_lhb,
    fetch_stock_northbound,
    fetch_stock_basic,
    create_scraper as create_stock_scraper,
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
    
    # 新增数据获取
    'fetch_sector_quote',
    'fetch_sector_list',
    'fetch_sector_flow',
    'fetch_margin_data',
    'fetch_capital_flow',
    'fetch_ipo_data',
    'fetch_convertible_bond',
    'SectorFetcher',
    'sector_fetcher',
    'BlockTradeFetcher',
    'block_trade_fetcher',
    'ShareholderFetcher',
    'shareholder_fetcher',
    'NewsFetcher',
    'news_fetcher',

    # 新增数据源
    'fetch_fenghuang_quote',
    'fetch_fenghuang_news',
    'fetch_fenghuang_hot_news',
    'FenghuangFetcher',
    'fenghuang_fetcher',
    'fetch_sina_news',
    'fetch_sina_stock_news',
    'fetch_sina_hot_news',
    'SinaNewsFetcher',
    'sina_news_fetcher',
    'fetch_gdp_data',
    'fetch_cpi_data',
    'fetch_pmi_data',
    'fetch_interest_rate_data',
    'fetch_money_supply_data',
    'fetch_exchange_rate_data',
    'fetch_unemployment_data',
    'fetch_trade_data',
    'fetch_all_macro_data',
    'MacroFetcher',
    'macro_fetcher',

    # 加密货币数据抓取
    'fetch_crypto_quote',
    'fetch_crypto_kline',
    'fetch_crypto_rank',
    'fetch_crypto_trending',
    'CryptoFetcher',
    'crypto_fetcher',

    # 大宗商品数据抓取
    'fetch_gold_quote',
    'fetch_gold_kline',
    'fetch_crude_oil_quote',
    'fetch_nymex_wti_quote',
    'fetch_dxy_quote',
    'fetch_lme_metal_quote',
    'CommodityFetcher',
    'commodity_fetcher',

    # 股票数据抓取
    'StockScraper',
    'fetch_stock_quote',
    'fetch_stock_kline',
    'fetch_stock_financial',
    'fetch_stock_dividend',
    'fetch_stock_lhb',
    'fetch_stock_northbound',
    'fetch_stock_basic',
    'create_stock_scraper',
]