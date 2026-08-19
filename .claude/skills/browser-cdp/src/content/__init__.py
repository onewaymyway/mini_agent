"""
content/ - 内容网站框架模块

覆盖博客、新闻、知识分享等内容型网站的内容抓取与分析能力。

包含：
- models: 数据模型（Article, Author, Category, Tag等）
- parsers: 解析器（BlogParser, NewsParser, KnowledgeBaseParser等）
- database: 数据库层（SQLite存储、搜索、索引）
- crawler: 爬虫引擎（单页、列表、分页、筛选抓取）
- site_manager: 网站管理器（配置加载、统计、缓存）
"""
from .models import (
    Article,
    Author,
    Category,
    Tag,
    ContentSiteProfile,
    ArticleSearchResults,
    ContentType,
    ContentSource,
)
from .parsers import (
    BaseContentParser,
    BlogParser,
    NewsParser,
    KnowledgeBaseParser,
    create_parser_for_site,
)
from .database import ContentDatabase
from .crawler import ContentCrawler, ContentCrawlerFactory
from .site_manager import ContentSiteManager

__all__ = [
    # 模型
    "Article",
    "Author",
    "Category",
    "Tag",
    "ContentSiteProfile",
    "ArticleSearchResults",
    "ContentType",
    "ContentSource",
    # 解析器
    "BaseContentParser",
    "BlogParser",
    "NewsParser",
    "KnowledgeBaseParser",
    "create_parser_for_site",
    # 数据库
    "ContentDatabase",
    # 爬虫
    "ContentCrawler",
    "ContentCrawlerFactory",
    # 管理器
    "ContentSiteManager",
]
