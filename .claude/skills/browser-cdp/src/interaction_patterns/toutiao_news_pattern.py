"""
toutiao_news_pattern.py - 今日头条新闻搜索模式

基于 NewsPattern 实现今日头条热榜和文章搜索。
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from ._base import SearchResults
from .news_pattern import NewsPattern, ArticleData, ArticleResults

logger = logging.getLogger(__name__)


class ToutiaoNewsPattern(NewsPattern):
    """今日头条新闻/热榜模式"""

    def __init__(self, session, domain: str = "toutiao.com", config: Optional[Dict] = None):
        toutiao_config = {
            "search_url": "https://www.toutiao.com/search/?keyword={query}",
            "result_item": ".article-item, .widget-box",
            "result_title": "h2, .title, a.title",
            "result_url": "a[href*='toutiao.com']",
            "result_snippet": ".abstract, .desc, p",
            "next_page": 'a.next, a.pagination-next',
            # 头条特有选择器
            "article_content": ".article-content, .content-area, article",
            "article_title": "h1, .title-area, h1.article-title",
            "article_author": ".source, .author, .media-name",
            "article_time": ".time, .publish-time",
            "comment_section": ".comment-list, .comment-section",
            "comment_item": ".comment-item, .comment",
            "comment_text": ".comment-content, .text",
            "comment_author": ".author, .username",
            "comment_time": ".time",
        }
        if config:
            toutiao_config.update(config)
        super().__init__(session, domain, toutiao_config)
        self._site_name = "toutiao"

    async def execute(self, query: str, max_pages: int = 1, **kwargs) -> ArticleResults:
        """执行今日头条搜索"""
        self._record_start()

        try:
            # 1. 导航到头条搜索页
            search_url = self._config.get(
                "search_url", f"https://www.toutiao.com/search/?keyword={query}"
            )
            await self._session.navigate(search_url)
            await self._wait.wait_for_selector(".article-item, .widget-box", timeout=10.0)

            # 2. 等待网络空闲
            await self._wait.wait_for_network_idle(timeout=10.0)

            # 3. 解析结果
            results = await self._parse_results(query)

            # 4. 翻页
            if max_pages > 1:
                results = await self._paginate(results, max_pages)

            results.pattern_used = f"ToutiaoNewsPattern({self._site_name})"
            return self._record_latency(results.to_dict())

        except Exception as e:
            logger.error(f"ToutiaoNewsPattern failed: {e}")
            return ArticleResults(
                success=False,
                query=query,
                error_message=str(e),
                pattern_used="ToutiaoNewsPattern",
            )

    async def _parse_results(self, query: str) -> ArticleResults:
        """解析今日头条搜索结果"""
        items = await self._session.query_selector_all(".article-item, .widget-box")
        results = []

        for item in items[:15]:  # 限制最多15条
            try:
                title_el = await item.query_selector("h2 a, .title a")
                title = (await title_el.get_text()).strip() if title_el else ""

                url = ""
                if title_el:
                    url = (await title_el.get_attribute("href")) or ""
                    if url and not url.startswith("http"):
                        url = "https://www.toutiao.com" + url

                snippet_el = await item.query_selector(".abstract, .desc")
                snippet = (await snippet_el.get_text()).strip() if snippet_el else ""

                # 提取作者
                author_el = await item.query_selector(".source, .author")
                author = (await author_el.get_text()).strip() if author_el else ""

                results.append(ArticleData(
                    title=title[:200],
                    url=url,
                    snippet=snippet[:500],
                    author=author,
                    source_domain=self._domain,
                    metadata={"source": "toutiao", "category": "news"},
                ))
            except Exception as e:
                logger.warning(f"Parse toutiao result item failed: {e}")
                continue

        return ArticleResults(
            success=True, query=query, articles=results, total_count=len(results)
        )

    async def get_hot_list(self, top_n: int = 20) -> List[ArticleData]:
        """获取头条热榜"""
        try:
            await self._session.navigate("https://www.toutiao.com/hot-event")
            await self._wait.wait_for_selector(".hot-item, .list-item", timeout=10.0)
            await self._wait.wait_for_network_idle(timeout=8.0)

            items = await self._session.query_selector_all(".hot-item, .list-item")
            hot_articles = []

            for item in items[:top_n]:
                try:
                    title_el = await item.query_selector("h3, .title, a")
                    title = (await title_el.get_text()).strip() if title_el else ""

                    url_el = await item.query_selector("a")
                    url = (await url_el.get_attribute("href")) or "" if url_el else ""

                    hot_articles.append(ArticleData(
                        title=title[:200],
                        url=url,
                        source_domain=self._domain,
                        metadata={"source": "toutiao", "type": "hot"},
                    ))
                except Exception as e:
                    logger.warning(f"Parse hot item failed: {e}")
                    continue

            return hot_articles
        except Exception as e:
            logger.error(f"Get hot list failed: {e}")
            return []