"""
新闻抓取模块
提供多源财经新闻抓取、聚合、去重、实时流式处理功能
"""

from .models import (
    NewsSource,
    NewsCategory,
    FinanceNews,
)
from .scrapers import (
    SinaNewsScraper,
    CLSNewsScraper,
    WallstreetcnScraper,
    XueqiuScraper,
    WechatScraper,
    ArxivScraper,
    RegulatorScraper,
)
from .aggregator import (
    NewsAggregator,
    NewsAggregatorBuilder,
)
from .stream import (
    NewsStreamProcessor,
    SentimentAnalyzer,
    NewsAlert,
    AlertLevel,
    run_news_monitor,
)

__all__ = [
    # 模型
    'NewsSource',
    'NewsCategory',
    'FinanceNews',
    # 抓取器
    'SinaNewsScraper',
    'CLSNewsScraper',
    'WallstreetcnScraper',
    'XueqiuScraper',
    'WechatScraper',
    'ArxivScraper',
    'RegulatorScraper',
    # 聚合器
    'NewsAggregator',
    'NewsAggregatorBuilder',
    # 流式处理
    'NewsStreamProcessor',
    'SentimentAnalyzer',
    'NewsAlert',
    'AlertLevel',
    'run_news_monitor',
]

__version__ = '1.0.0'