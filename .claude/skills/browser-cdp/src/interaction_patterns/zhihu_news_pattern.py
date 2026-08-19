"""
zhihu_news_pattern.py - 知乎新闻搜索模式

基于 NewsPattern 实现知乎热榜和话题搜索。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ._base import SearchResults
from .news_pattern import NewsPattern, ArticleData, ArticleResults

logger = logging.getLogger(__name__)


class ZhihuNewsPattern(NewsPattern):
    """知乎新闻/热榜搜索模式"""

    def __init__(self, session, domain: str = "zhihu.com", config: Optional[Dict] = None):
        zhihu_config = {
            "search_url": "https://www.zhihu.com/search?type=content&q={query}",
            "result_item": ".SearchResult-Item, .Item",
            "result_title": "h2, .Item-title",
            "result_url": "a[href]",
            "result_snippet": ".RichContent-inner, .ContentItem-content",
            "next_page": 'a.Next, [class*="Pagination"] a:last-child',
            # 知乎特有选择器
            "article_content": ".RichContent-inner, .Post-RichText",
            "article_title": "h1.Post-title, .Post-Title",
            "article_author": ".AuthorInfo-name, .Post-VoteInfo-authorName",
            "article_time": ".Post-itemTime, .ContentItem-time",
            "comment_section": ".ListContainer, .CommentBox",
            "comment_item": ".CommentItem, .Comment",
            "comment_text": ".Content, .RichContent-inner",
            "comment_author": ".AuthorInfo-name",
            "comment_time": ".Time",
        }
        if config:
            zhihu_config.update(config)
        super().__init__(session, domain, zhihu_config)
        self._site_name = "zhihu"

    async def execute(self, query: str, max_pages: int = 1, **kwargs) -> ArticleResults:
        """执行知乎搜索"""
        self._record_start()

        try:
            # 1. 导航到知乎搜索页
            search_url = self._config.get(
                "search_url", f"https://www.zhihu.com/search?type=content&q={query}"
            )
            await self._session.navigate(search_url)
            await self._wait.wait_for_selector(".SearchResult-Item", timeout=10.0)

            # 2. 等待网络空闲
            await self._wait.wait_for_network_idle(timeout=10.0)

            # 3. 解析结果
            results = await self._parse_results(query)

            # 4. 翻页
            if max_pages > 1:
                results = await self._paginate(results, max_pages)

            results.pattern_used = f"ZhihuNewsPattern({self._site_name})"
            return self._record_latency(results.to_dict())

        except Exception as e:
            logger.error(f"ZhihuNewsPattern failed: {e}")
            return ArticleResults(
                success=False,
                query=query,
                error_message=str(e),
                pattern_used="ZhihuNewsPattern",
            )

    async def _parse_results(self, query: str) -> ArticleResults:
        """解析知乎搜索结果"""
        items = await self._session.query_selector_all(".SearchResult-Item")
        results = []

        for item in items[:10]:  # 限制最多10条
            try:
                title_el = await item.query_selector("h2 a, .Item-title")
                title = (await title_el.get_text()).strip() if title_el else ""

                url = ""
                if title_el:
                    url = (await title_el.get_attribute("href")) or ""
                    if url and not url.startswith("http"):
                        url = "https://www.zhihu.com" + url

                snippet_el = await item.query_selector(".RichContent-inner")
                snippet = (await snippet_el.get_text()).strip() if snippet_el else ""

                # 提取作者
                author_el = await item.query_selector(".AuthorInfo-name")
                author = (await author_el.get_text()).strip() if author_el else ""

                results.append(ArticleData(
                    title=title[:200],
                    url=url,
                    snippet=snippet[:500],
                    author=author,
                    source_domain=self._domain,
                    metadata={"source": "zhihu", "category": "question"},
                ))
            except Exception as e:
                logger.warning(f"Parse zhihu result item failed: {e}")
                continue

        return ArticleResults(
            success=True, query=query, articles=results, total_count=len(results)
        )

    async def get_hot_list(self, top_n: int = 30) -> List[ArticleData]:
        """获取知乎热榜"""
        try:
            await self._session.navigate("https://www.zhihu.com/hot")
            await self._wait.wait_for_selector(".HotList-item", timeout=10.0)
            await self._wait.wait_for_network_idle(timeout=8.0)

            items = await self._session.query_selector_all(".HotList-item")
            hot_articles = []

            for item in items[:top_n]:
                try:
                    title_el = await item.query_selector("h2, .Content")
                    title = (await title_el.get_text()).strip() if title_el else ""

                    url_el = await item.query_selector("a")
                    url = (await url_el.get_attribute("href")) or "" if url_el else ""

                    hot_el = await item.query_selector(".HotIndex")
                    rank = (await hot_el.get_text()).strip() if hot_el else ""

                    hot_articles.append(ArticleData(
                        title=title[:200],
                        url=url,
                        source_domain=self._domain,
                        metadata={"source": "zhihu", "type": "hot", "rank": rank},
                    ))
                except Exception as e:
                    logger.warning(f"Parse hot item failed: {e}")
                    continue

            return hot_articles
        except Exception as e:
            logger.error(f"Get hot list failed: {e}")
            return []
