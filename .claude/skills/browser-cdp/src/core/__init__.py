"""
Browser-CDP Enhanced - 核心模块
"""
from .auth_module import AuthManager, WebsiteManager
from .content_service import ContentDetailService
from .web_interface import app as api_app

# 爬虫基础框架
from .url_dedup import UrlNormalizer, BloomFilter, UrlDedupManager
from .request_client import (
    RequestConfig,
    HttpResponse,
    RateLimiter,
    UaRotator,
    SyncRequestClient,
    AsyncRequestClient,
    create_sync_client,
    create_async_client,
)
from .parser_base import BaseParser, ParseResult, JsonParser, SearchResultsParser
from .crawl_scheduler import (
    CrawlScheduler,
    CrawlTask,
    TaskStatus,
    CrawlCallback,
    PriorityQueue,
)

__all__ = [
    # 原有模块
    'AuthManager', 'WebsiteManager', 'ContentDetailService', 'api_app',
    # URL去重
    'UrlNormalizer', 'BloomFilter', 'UrlDedupManager',
    # 请求客户端
    'RequestConfig', 'HttpResponse', 'RateLimiter', 'UaRotator',
    'SyncRequestClient', 'AsyncRequestClient',
    'create_sync_client', 'create_async_client',
    # 解析框架
    'BaseParser', 'ParseResult', 'JsonParser', 'SearchResultsParser',
    # 调度器
    'CrawlScheduler', 'CrawlTask', 'TaskStatus', 'CrawlCallback', 'PriorityQueue',
]
