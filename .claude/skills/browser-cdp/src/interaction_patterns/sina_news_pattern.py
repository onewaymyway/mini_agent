"""
sina_news_pattern.py - 新浪财经新闻模式

基于 NewsPattern 实现新浪财经热榜和文章搜索。
支持 RSS 抓取和浏览器回退两种方式。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ._base import SearchResults
from .news_pattern import NewsPattern, ArticleData, ArticleResults

logger = logging.getLogger(__name__)

# 新浪财经分类 URL 映射
SINA_CATEGORY_URLS = {
    "stock": "https://finance.sina.com.cn/stock/",
    "macro": "https://finance.sina.com.cn/china/",
    "industry": "https://finance.sina.com.cn/chanjing/",
    "forex": "https://forex.sina.com.cn/",
    "futures": "https://futures.sina.com.cn/",
}

# 新浪财经 RSS  feeds
SINA_RSS_FEEDS = {
    "stock": "https://feed.finance.sina.com.cn/rss/stock.xml",
    "macro": "https://feed.finance.sina.com.cn/rss/macro.xml",
    "industry": "https://feed.finance.sina.com.cn/rss/industry.xml",
    "forex": "https://feed.finance.sina.com.cn/rss/forex.xml",
    "futures": "https://feed.finance.sina.com.cn/rss/futures.xml",
}


class SinaNewsPattern(NewsPattern):
    """新浪财经新闻模式"""

    def __init__(self, session, domain: str = "finance.sina.com.cn", config: Optional[Dict] = None):
        sina_config = {
            "search_url": "https://finance.sina.com.cn/search/#keywords={query}",
            "result_item": ".list-item, .news-item, article, .news-list li",
            "result_title": "a, .title, h3, h2",
            "result_url": "a[href]",
            "result_snippet": ".summary, .desc, p",
            "next_page": 'a.next, a.pagination-next, [rel="next"]',
            # 新浪特有选择器
            "article_content": "#artibody, article, .article-content, .content",
            "article_title": "h1, .article-title, .main-title",
            "article_author": ".author, .source, .editor",
            "article_time": ".time, .date, .publish-time",
            "comment_section": ".comments, .comment-section, #comment-area",
            "comment_item": ".comment-item, .comment, .reply-item",
            "comment_text": ".comment-text, .reply-content, p",
            "comment_author": ".comment-author, .username, .reply-user",
            "comment_time": ".time, .date",
            # 分类配置
            "category_urls": SINA_CATEGORY_URLS,
            "rss_feeds": SINA_RSS_FEEDS,
        }
        if config:
            sina_config.update(config)
        super().__init__(session, domain, sina_config)
        self._site_name = "sina"

    async def execute(self, query: str = "", max_pages: int = 1, category: str = "stock", **kwargs) -> ArticleResults:
        """执行新浪财经搜索或分类浏览"""
        self._record_start()

        try:
            if query:
                # 有搜索关键词：导航到搜索页
                search_url = self._config.get(
                    "search_url", f"https://finance.sina.com.cn/search/#keywords={query}"
                ).format(query=query)
                await self._session.navigate(search_url)
                await self._wait.wait_for_selector(".list-item, .news-item", timeout=10.0)
            else:
                # 无搜索词：导航到分类页
                url = SINA_CATEGORY_URLS.get(category, SINA_CATEGORY_URLS["stock"])
                await self._session.navigate(url)
                await self._wait.wait_for_selector(".list-item, .news-item, article", timeout=10.0)

            await self._wait.wait_for_network_idle(timeout=10.0)

            results = await self._parse_results(query or category)

            if max_pages > 1:
                results = await self._paginate(results, max_pages)

            results.pattern_used = f"SinaNewsPattern({self._site_name})"
            return self._record_latency(results.to_dict())

        except Exception as e:
            logger.error(f"SinaNewsPattern failed: {e}")
            return ArticleResults(
                success=False,
                query=query or category,
                error_message=str(e),
                pattern_used="SinaNewsPattern",
            )

    async def _parse_results(self, query: str) -> ArticleResults:
        """解析新浪财经搜索结果"""
        items = await self._session.query_selector_all(".list-item, .news-item, article")
        results = []

        for item in items[:20]:
            try:
                title_el = await item.query_selector("a, .title, h3")
                title = (await title_el.get_text()).strip() if title_el else ""

                url = ""
                if title_el:
                    url = (await title_el.get_attribute("href")) or ""
                    if url and not url.startswith("http"):
                        url = "https:" + url if url.startswith("//") else "https://finance.sina.com.cn" + url

                time_el = await item.query_selector(".time, .date")
                time_str = (await time_el.get_text()).strip() if time_el else ""

                results.append(ArticleData(
                    title=title[:200],
                    url=url,
                    publish_time=time_str,
                    source_domain=self._domain,
                    metadata={"source": "sina", "category": "finance"},
                ))
            except Exception as e:
                logger.warning(f"Parse sina result item failed: {e}")
                continue

        return ArticleResults(
            success=True, query=query, articles=results, total_count=len(results)
        )

    async def get_hot_list(self, top_n: int = 20, category: str = "stock") -> List[ArticleData]:
        """获取新浪财经热点"""
        try:
            url = SINA_CATEGORY_URLS.get(category, SINA_CATEGORY_URLS["stock"])
            await self._session.navigate(url)
            await self._wait.wait_for_selector(".list-item, .news-item", timeout=10.0)
            await self._wait.wait_for_network_idle(timeout=8.0)

            items = await self._session.query_selector_all(".list-item, .news-item")
            hot_articles = []

            for item in items[:top_n]:
                try:
                    title_el = await item.query_selector("a, h3, h2")
                    title = (await title_el.get_text()).strip() if title_el else ""

                    url_el = await item.query_selector("a[href]")
                    url = (await url_el.get_attribute("href")) or "" if url_el else ""
                    if url and not url.startswith("http"):
                        url = "https:" + url if url.startswith("//") else url

                    hot_articles.append(ArticleData(
                        title=title[:200],
                        url=url,
                        source_domain=self._domain,
                        metadata={"source": "sina", "type": "hot", "category": category},
                    ))
                except Exception as e:
                    logger.warning(f"Parse sina hot item failed: {e}")
                    continue

            return hot_articles
        except Exception as e:
            logger.error(f"Get sina hot list failed: {e}")
            return []
