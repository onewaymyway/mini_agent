"""
crawler.py - 内容网站爬虫

提供针对内容网站的智能抓取能力，支持分页、滚动、认证等功能。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .database import ContentDatabase
from .models import Article, ArticleSearchResults, ContentSiteProfile
from .parsers import BaseContentParser, create_parser_for_site

logger = logging.getLogger(__name__)


class ContentCrawler:
    """内容网站爬虫 - 负责实际抓取操作"""

    def __init__(
        self,
        session,
        domain: str,
        db: Optional[ContentDatabase] = None,
        max_pages: int = 10,
        delay_between_requests: float = 1.0,
        save_to_db: bool = True,
    ):
        self._session = session
        self._domain = domain
        self._db = db or ContentDatabase()
        self._max_pages = max_pages
        self._delay = delay_between_requests
        self._save_to_db = save_to_db
        self._parser: Optional[BaseContentParser] = None
        self._stats: Dict[str, Any] = {
            'pages_crawled': 0,
            'articles_found': 0,
            'articles_saved': 0,
            'errors': 0,
            'start_time': 0,
            'end_time': 0,
        }

    async def initialize(self) -> None:
        """初始化爬虫"""
        from ..content.parsers import create_parser_for_site
        self._parser = create_parser_for_site(self._domain)
        logger.info(f"ContentCrawler initialized for {self._domain}")

    async def crawl_article(self, url: str, save: bool = True) -> Optional[Article]:
        """抓取单篇文章"""
        try:
            # 导航到文章页面
            await self._navigate(url)
            
            # 等待页面加载
            await self._wait_for_content()
            
            # 获取HTML
            html = await self._get_page_html()
            
            if not self._parser:
                from ..content.parsers import create_parser_for_site
                self._parser = create_parser_for_site(self._domain)
            
            # 解析文章
            article = await self._parser.parse_article(html, url)
            article.source_domain = self._domain
            article.scrape_source_url = url
            
            self._stats['articles_found'] += 1
            
            # 保存到数据库
            if save and self._save_to_db:
                if self._db.save_article(article):
                    self._stats['articles_saved'] += 1
            
            return article
            
        except Exception as e:
            logger.error(f"Failed to crawl article {url}: {e}")
            self._stats['errors'] += 1
            return None

    async def crawl_list(
        self,
        url: str,
        max_results: int = 50,
        save: bool = True,
    ) -> ArticleSearchResults:
        """抓取文章列表"""
        try:
            await self._navigate(url)
            await self._wait_for_content()
            html = await self._get_page_html()
            
            if not self._parser:
                from ..content.parsers import create_parser_for_site
                self._parser = create_parser_for_site(self._domain)
            
            results = await self._parser.parse_list(html, url)
            results.site_domain = self._domain
            
            # 保存找到的文章
            saved_count = 0
            for article in results.articles[:max_results]:
                article.source_domain = self._domain
                if save and self._save_to_db:
                    if self._db.save_article(article):
                        saved_count += 1
            
            self._stats['articles_found'] += len(results.articles)
            self._stats['articles_saved'] += saved_count
            
            return results
            
        except Exception as e:
            logger.error(f"Failed to crawl list {url}: {e}")
            self._stats['errors'] += 1
            return ArticleSearchResults(error_message=str(e), site_domain=self._domain)

    async def crawl_sequential(
        self,
        base_url: str,
        paginate_pattern: Optional[str] = None,
        max_pages: Optional[int] = None,
        save: bool = True,
        on_article: Optional[Callable[[Article], None]] = None,
    ) -> List[Article]:
        """顺序抓取多页内容"""
        all_articles = []
        max_pages = max_pages or self._max_pages
        
        self._stats['start_time'] = time.time()
        
        for page in range(1, max_pages + 1):
            # 构建分页URL
            if paginate_pattern:
                page_url = paginate_pattern.replace('{page}', str(page))
            else:
                page_url = f"{base_url}?page={page}"
            
            # 检查是否还有更多内容
            if not await self._has_next_page(page_url):
                logger.info(f"No more pages at {page_url}")
                break
            
            # 抓取当前页
            results = await self.crawl_list(page_url, save=save)
            all_articles.extend(results.articles)
            
            self._stats['pages_crawled'] += 1
            
            # 回调通知
            if on_article and results.articles:
                for article in results.articles:
                    on_article(article)
            
            # 延迟
            if page < max_pages:
                await asyncio.sleep(self._delay)
            
            # 检查是否达到目标数量
            if len(all_articles) >= max_pages * 20:
                break
        
        self._stats['end_time'] = time.time()
        
        logger.info(
            f"Sequential crawl completed: {len(all_articles)} articles in {self._stats['pages_crawled']} pages"
        )
        
        return all_articles

    async def crawl_by_date_range(
        self,
        start_url: str,
        start_date: str,
        end_date: str,
        max_articles: int = 100,
        save: bool = True,
    ) -> List[Article]:
        """按日期范围抓取"""
        articles = []
        current_url = start_url
        
        self._stats['start_time'] = time.time()
        
        while len(articles) < max_articles:
            # 抓取当前页
            results = await self.crawl_list(current_url, save=False)
            
            if not results.articles:
                break
            
            # 筛选符合日期范围的文章
            for article in results.articles:
                if article.published_at:
                    pub_str = article.published_at.isoformat()[:10]
                    if start_date <= pub_str <= end_date:
                        article.source_domain = self._domain
                        if save and self._save_to_db:
                            self._db.save_article(article)
                        articles.append(article)
                        
                        if len(articles) >= max_articles:
                            break
            
            # 检查下一页
            next_url = await self._get_next_page_url()
            if not next_url or next_url == current_url:
                break
            current_url = next_url
            
            await asyncio.sleep(self._delay)
        
        self._stats['end_time'] = time.time()
        return articles

    async def crawl_with_filters(
        self,
        url: str,
        filters: Dict[str, Any],
        max_results: int = 50,
        save: bool = True,
    ) -> ArticleSearchResults:
        """按条件筛选抓取"""
        results = await self.crawl_list(url, max_results=max_results, save=False)
        
        # 应用筛选
        filtered_articles = []
        for article in results.articles:
            if self._matches_filters(article, filters):
                article.source_domain = self._domain
                filtered_articles.append(article)
        
        # 保存
        if save and self._save_to_db:
            for article in filtered_articles:
                self._db.save_article(article)
        
        results.articles = filtered_articles
        results.success = True
        
        return results

    # ─── 辅助方法 ───

    async def _navigate(self, url: str) -> None:
        """导航到URL"""
        await self._session.navigate(url)
        await asyncio.sleep(0.5)

    async def _wait_for_content(self, timeout: float = 10.0) -> bool:
        """等待页面内容加载"""
        start = time.time()
        while time.time() - start < timeout:
            # 检查是否有文章内容
            has_content = await self._session.evaluate('''
                document.querySelector('.article-content, .entry-content, article, .content') !== null
            ''')
            if has_content:
                return True
            await asyncio.sleep(0.5)
        return False

    async def _get_page_html(self) -> str:
        """获取页面HTML"""
        return await self._session.evaluate('document.documentElement.outerHTML')

    async def _has_next_page(self, url: str) -> bool:
        """检查是否存在下一页"""
        try:
            result = await self._session.evaluate('''
                document.querySelector('.next, .pagination-next, a[href*="page"]') !== null
            ''')
            return bool(result)
        except Exception:
            return False

    async def _get_next_page_url(self) -> Optional[str]:
        """获取下一页URL"""
        try:
            url = await self._session.evaluate('''
                (() => {
                    const next = document.querySelector('.next, .pagination-next, a[href*="page"]');
                    return next ? next.href : null;
                })()
            ''')
            return url if url and url.startswith('http') else None
        except Exception:
            return None

    def _matches_filters(self, article: Article, filters: Dict[str, Any]) -> bool:
        """检查文章是否符合筛选条件"""
        for key, value in filters.items():
            if key == 'min_quality' and article.quality_score < value:
                return False
            if key == 'min_word_count' and article.word_count < value:
                return False
            if key == 'content_type' and article.content_type.value != value:
                return False
            if key == 'has_images' and value and len(article.images) == 0:
                return False
            if key == 'tags' and value:
                tag_names = [t.name for t in article.tags]
                if not any(t in tag_names for t in value):
                    return False
        return True

    def get_stats(self) -> Dict[str, Any]:
        """获取爬取统计"""
        elapsed = self._stats.get('end_time', 0) - self._stats.get('start_time', 0) or (time.time() - self._stats['start_time'])
        
        return {
            **self._stats,
            'elapsed_seconds': round(elapsed, 2),
            'articles_per_second': round(self._stats['articles_found'] / max(elapsed, 0.1), 2),
            'success_rate': round(
                self._stats['articles_saved'] / max(self._stats['articles_found'], 1) * 100, 2
            ),
        }

    def reset_stats(self) -> None:
        """重置统计"""
        self._stats = {
            'pages_crawled': 0,
            'articles_found': 0,
            'articles_saved': 0,
            'errors': 0,
            'start_time': 0,
            'end_time': 0,
        }


class ContentCrawlerFactory:
    """内容爬虫工厂"""

    _instances: Dict[str, ContentCrawler] = {}

    @classmethod
    def get_or_create(
        cls,
        session,
        domain: str,
        db: Optional[ContentDatabase] = None,
        **kwargs,
    ) -> ContentCrawler:
        """获取或创建爬虫实例"""
        key = f"{domain}:{id(session)}"
        
        if key not in cls._instances:
            cls._instances[key] = ContentCrawler(
                session=session,
                domain=domain,
                db=db or ContentDatabase(),
                **kwargs,
            )
        
        return cls._instances[key]

    @classmethod
    def remove(cls, domain: str, session_id: Optional[int] = None) -> None:
        """移除爬虫实例"""
        if session_id:
            key = f"{domain}:{session_id}"
            cls._instances.pop(key, None)
        else:
            keys_to_remove = [k for k in cls._instances if k.startswith(f"{domain}:")]
            for key in keys_to_remove:
                cls._instances.pop(key, None)

    @classmethod
    def clear_all(cls) -> None:
        """清除所有实例"""
        cls._instances.clear()
