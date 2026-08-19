"""
models.py - 内容网站数据模型

定义文章、作者、分类、标签等核心数据模型。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class ContentType(Enum):
    """内容类型"""
    BLOG = "blog"           # 博客文章
    NEWS = "news"           # 新闻资讯
    TUTORIAL = "tutorial"   # 教程文档
    RESEARCH = "research"   # 研究论文
    PRODUCT = "product"     # 产品文档
    REVIEW = "review"       # 评测文章
    OPINION = "opinion"     # 观点评论


class ContentSource(Enum):
    """来源类型"""
    ORIGINAL = "original"           # 原创
    REPOST = "repost"               # 转载
    TRANSLATED = "translated"       # 翻译
    AGGREGATED = "aggregated"       # 聚合


@dataclass
class Author:
    """文章作者"""
    author_id: str = ""
    name: str = ""
    username: str = ""
    avatar_url: str = ""
    bio: str = ""
    follower_count: int = 0
    following_count: int = 0
    article_count: int = 0
    website: str = ""
    social_links: Dict[str, str] = field(default_factory=dict)
    is_verified: bool = False
    scraped_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "author_id": self.author_id,
            "name": self.name,
            "username": self.username,
            "avatar_url": self.avatar_url,
            "bio": self.bio[:500],
            "follower_count": self.follower_count,
            "following_count": self.following_count,
            "article_count": self.article_count,
            "website": self.website,
            "social_links": self.social_links,
            "is_verified": self.is_verified,
            "scraped_at": self.scraped_at,
        }


@dataclass
class Tag:
    """内容标签"""
    tag_id: str = ""
    name: str = ""
    slug: str = ""
    article_count: int = 0
    description: str = ""
    parent_tag_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag_id": self.tag_id,
            "name": self.name,
            "slug": self.slug,
            "article_count": self.article_count,
            "description": self.description[:200],
            "parent_tag_id": self.parent_tag_id,
        }


@dataclass
class Category:
    """内容分类"""
    category_id: str = ""
    name: str = ""
    slug: str = ""
    description: str = ""
    parent_category_id: str = ""
    article_count: int = 0
    sort_order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category_id": self.category_id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description[:300],
            "parent_category_id": self.parent_category_id,
            "article_count": self.article_count,
            "sort_order": self.sort_order,
        }


@dataclass
class Article:
    """文章内容"""
    article_id: str = ""
    title: str = ""
    url: str = ""
    content: str = ""
    excerpt: str = ""
    content_type: ContentType = ContentType.BLOG
    source_type: ContentSource = ContentSource.ORIGINAL
    author: Optional[Author] = None
    categories: List[Category] = field(default_factory=list)
    tags: List[Tag] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    videos: List[str] = field(default_factory=list)
    
    # 元数据
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_at: str = ""
    word_count: int = 0
    read_time_minutes: float = 0.0
    
    # 互动数据（爬取时记录）
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    view_count: int = 0
    
    # SEO 相关
    meta_title: str = ""
    meta_description: str = ""
    canonical_url: str = ""
    
    # 来源追踪
    source_domain: str = ""
    scrape_source_url: str = ""
    scraped_at: str = ""
    raw_html_snippet: str = ""
    
    # 质量指标
    quality_score: float = 0.0
    is_spam: bool = False
    
    # 扩展字段
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "article_id": self.article_id,
            "title": self.title[:200],
            "url": self.url,
            "content_type": self.content_type.value,
            "source_type": self.source_type.value,
            "author": self.author.to_dict() if self.author else None,
            "categories": [c.to_dict() for c in self.categories[:10]],
            "tags": [t.to_dict() for t in self.tags[:20]],
            "excerpt": self.excerpt[:300],
            "word_count": self.word_count,
            "read_time_minutes": self.read_time_minutes,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "view_count": self.view_count,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "scraped_at": self.scraped_at,
            "source_domain": self.source_domain,
            "quality_score": self.quality_score,
            "is_spam": self.is_spam,
            "image_count": len(self.images),
            "metadata": self.metadata,
        }


@dataclass
class ArticleSearchResults:
    """文章搜索/列表结果集"""
    success: bool = False
    query: str = ""
    articles: List[Article] = field(default_factory=list)
    total_count: Optional[int] = None
    page: int = 1
    page_size: int = 20
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    parser_used: str = ""
    site_domain: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.articles) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "query": self.query,
            "total_count": self.total_count,
            "page": self.page,
            "page_size": self.page_size,
            "latency_ms": self.latency_ms,
            "parser_used": self.parser_used,
            "site_domain": self.site_domain,
            "articles": [a.to_dict() for a in self.articles[:self.page_size]],
            "error_message": self.error_message,
        }


@dataclass
class ContentSiteProfile:
    """内容网站档案"""
    domain: str = ""
    name: str = ""
    site_type: str = ""          # blog/news/tutorial/research/product
    language: str = "zh"
    description: str = ""
    article_count: int = 0
    active_since: str = ""
    last_crawled: str = ""
    health_score: float = 1.0
    anti_crawl_level: int = 1    # 1-5
    requires_login: bool = False
    rate_limit_per_minute: int = 10
    
    # 选择器配置（由网站配置文件填充）
    selectors: Dict[str, str] = field(default_factory=dict)
    
    # 解析结果统计
    crawl_stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "name": self.name,
            "site_type": self.site_type,
            "language": self.language,
            "description": self.description[:200],
            "article_count": self.article_count,
            "health_score": self.health_score,
            "anti_crawl_level": self.anti_crawl_level,
            "requires_login": self.requires_login,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "last_crawled": self.last_crawled,
            "selectors_preview": {k: v[:80] for k, v in list(self.selectors.items())[:5]},
        }
