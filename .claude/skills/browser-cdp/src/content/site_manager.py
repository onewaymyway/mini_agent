"""
site_manager.py - 内容网站管理器

管理多内容网站的解析配置、缓存和统计信息。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .models import (
    Article,
    ArticleSearchResults,
    ContentSiteProfile,
)
from .parsers import BaseContentParser, BlogParser, NewsParser, create_parser_for_site

logger = logging.getLogger(__name__)


class ContentSiteManager:
    """内容网站管理器 - 统一管理多个内容网站的解析"""

    def __init__(self, config_dir: Optional[str] = None):
        self._config_dir = config_dir or os.path.join(
            os.path.dirname(__file__), '..', '..', 'config', 'websites'
        )
        self._config_dir = os.path.normpath(self._config_dir)
        self._parsers: Dict[str, BaseContentParser] = {}
        self._profiles: Dict[str, ContentSiteProfile] = {}
        self._stats: Dict[str, Dict[str, Any]] = {}
        self._cache: Dict[str, Article] = {}
        self._max_cache_size = 1000

    # ─── 配置管理 ───

    def load_config(self, domain: str) -> Optional[Dict]:
        """加载网站配置文件"""
        config_file = os.path.join(self._config_dir, f"{domain}.json")
        if not os.path.exists(config_file):
            logger.warning(f"Config not found: {config_file}")
            return None
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config
        except Exception as e:
            logger.error(f"Failed to load config {config_file}: {e}")
            return None

    def get_parser(self, domain: str, site_type: Optional[str] = None) -> Optional[BaseContentParser]:
        """获取或创建解析器"""
        if domain in self._parsers:
            return self._parsers[domain]
        config = self.load_config(domain)
        if not config:
            return None
        parser = create_parser_for_site(domain, site_type=site_type, config=config)
        self._parsers[domain] = parser
        return parser

    def get_profile(self, domain: str) -> Optional[ContentSiteProfile]:
        """获取网站档案"""
        if domain in self._profiles:
            return self._profiles[domain]
        parser = self.get_parser(domain)
        if not parser:
            return None
        # 从配置文件构建档案
        config = self.load_config(domain) or {}
        profile = ContentSiteProfile(
            domain=domain,
            name=config.get("name", domain),
            site_type=config.get("category", "blog"),
            anti_crawl_level=config.get("anti_crawl_level", 1),
            requires_login=config.get("login_required", False),
            selectors=config.get("custom_config", {}).get("interaction_selectors", {}),
        )
        self._profiles[domain] = profile
        return profile

    def list_supported_sites(self) -> List[str]:
        """列出所有支持的内容网站"""
        if not os.path.exists(self._config_dir):
            return []
        sites = []
        for f in os.listdir(self._config_dir):
            if f.endswith('.json') and f not in ['template.json', 'example.com.json']:
                sites.append(f.replace('.json', ''))
        return sorted(sites)

    # ─── 解析操作 ───

    async def parse_article(
        self,
        domain: str,
        html: str,
        url: str,
        site_type: Optional[str] = None,
        use_cache: bool = True,
    ) -> Optional[Article]:
        """解析文章内容"""
        cache_key = f"{domain}:{url}"
        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        parser = self.get_parser(domain, site_type=site_type)
        if not parser:
            logger.error(f"No parser for domain: {domain}")
            return None

        try:
            article = await parser.parse_article(html, url)
            article.source_domain = domain
            self._update_stats(domain, "article_parsed")
            # 缓存（限制大小）
            if use_cache and len(self._cache) < self._max_cache_size:
                self._cache[cache_key] = article
            return article
        except Exception as e:
            logger.error(f"parse_article failed for {url}: {e}")
            self._update_stats(domain, "parse_error")
            return None

    async def parse_list(
        self,
        domain: str,
        html: str,
        url: str,
        page: int = 1,
        site_type: Optional[str] = None,
    ) -> Optional[ArticleSearchResults]:
        """解析文章列表"""
        parser = self.get_parser(domain, site_type=site_type)
        if not parser:
            logger.error(f"No parser for domain: {domain}")
            return None

        try:
            results = await parser.parse_list(html, url, page=page)
            results.site_domain = domain
            self._update_stats(domain, "list_parsed")
            return results
        except Exception as e:
            logger.error(f"parse_list failed for {url}: {e}")
            self._update_stats(domain, "parse_error")
            return None

    async def crawl_article(
        self,
        session,
        domain: str,
        url: str,
        site_type: Optional[str] = None,
    ) -> Optional[Article]:
        """通过浏览器会话抓取并解析单篇文章"""
        from ..core.browser_api import BrowserAPI
        browser = BrowserAPI.get_instance() if hasattr(BrowserAPI, 'get_instance') else None
        
        # 简化实现：直接通过session获取内容
        try:
            html = await session.evaluate("document.documentElement.outerHTML")
            return await self.parse_article(domain, html, url, site_type=site_type)
        except Exception as e:
            logger.error(f"crawl_article failed for {url}: {e}")
            return None

    # ─── 统计管理 ───

    def _update_stats(self, domain: str, event: str):
        """更新统计信息"""
        if domain not in self._stats:
            self._stats[domain] = {
                "last_updated": time.time(),
                "events": {},
                "articles_parsed": 0,
                "errors": 0,
            }
        stats = self._stats[domain]
        stats["last_updated"] = time.time()
        stats["events"][event] = stats["events"].get(event, 0) + 1
        if event == "article_parsed":
            stats["articles_parsed"] += 1
        elif event == "parse_error":
            stats["errors"] += 1

    def get_stats(self, domain: Optional[str] = None) -> Dict[str, Any]:
        """获取统计信息"""
        if domain:
            return self._stats.get(domain, {})
        return {
            domain: self._stats.get(domain, {}) 
            for domain in self.list_supported_sites()
            if domain in self._stats
        }

    def reset_stats(self, domain: Optional[str] = None):
        """重置统计"""
        if domain:
            self._stats.pop(domain, None)
        else:
            self._stats.clear()

    # ─── 缓存管理 ───

    def clear_cache(self, domain: Optional[str] = None):
        """清理缓存"""
        if domain:
            self._cache = {k: v for k, v in self._cache.items() if not k.startswith(domain)}
        else:
            self._cache.clear()

    def get_cache_size(self, domain: Optional[str] = None) -> int:
        """获取缓存大小"""
        if domain:
            return sum(1 for k in self._cache if k.startswith(domain))
        return len(self._cache)

    # ─── 便捷方法 ───

    def get_supported_content_sites(self) -> List[str]:
        """获取支持内容抓取的站点列表"""
        all_sites = self.list_supported_sites()
        content_types = {"blog", "news", "knowledge_base", "tutorial", "docs"}
        supported = []
        for domain in all_sites:
            config = self.load_config(domain)
            if config and config.get("category", "") in content_types:
                supported.append(domain)
        return sorted(supported)

    def get_site_info(self, domain: str) -> Dict[str, Any]:
        """获取网站综合信息"""
        profile = self.get_profile(domain)
        stats = self.get_stats(domain)
        cache_size = self.get_cache_size(domain)
        return {
            "domain": domain,
            "profile": profile.to_dict() if profile else None,
            "stats": stats,
            "cache_size": cache_size,
            "parser_available": domain in self._parsers,
        }
