"""
cls_news_pattern.py - 财联社新闻模式

基于 NewsPattern 实现财联社电报和新闻搜索。
财联社有公开 API，Pattern 优先使用 API 数据，浏览器作为回退。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from ._base import SearchResults
from .news_pattern import NewsPattern, ArticleData, ArticleResults

logger = logging.getLogger(__name__)

# 财联社基础 URL
CLS_BASE = "https://www.cls.cn"
CLS_API_TELEGRAPH = "https://www.cls.cn/nodeapi/updateTelegraph"
CLS_API_ROLL = "https://www.cls.cn/v3/roll/home/get/roll_data"
CLS_API_SEARCH = "https://www.cls.cn/searchpage/abc"

# 重要性评级
IMPORTANCE_MAP = {"0": "低", "1": "中", "2": "高", "3": "极高"}

# 分类映射
CATEGORY_MAP = {
    "telegraph": "telegraph",
    "finance": "finance",
    "tech": "tech",
    "stock": "stock",
    "crypto": "crypto",
    "macro": "macro",
    "world": "world",
}


class ClsNewsPattern(NewsPattern):
    """财联社新闻模式"""

    def __init__(self, session, domain: str = "cls.cn", config: Optional[Dict] = None):
        cls_config = {
            "search_url": f"{CLS_BASE}/searchpage/abc?keyword={{query}}",
            "result_item": ".search-result-item, .news-item, [class*=\"result\"][class*=\"item\"]",
            "result_title": ".title, h3, [class*=\"title\"]",
            "result_url": "a[href]",
            "result_snippet": ".summary, .excerpt, p",
            "next_page": 'a.next, a.pagination-next',
            # 财联社特有选择器（用于浏览器回退）
            "article_content": ".detail-content, .article-content, article",
            "article_title": "h1, .article-title, .detail-title",
            "article_author": ".author, .source",
            "article_time": ".time, .publish-time",
            "comment_section": ".comments, .comment-section",
            "comment_item": ".comment-item, .comment",
            "comment_text": ".comment-content, p",
            "comment_author": ".comment-author, .username",
            "comment_time": ".time",
            # API 配置
            "api_telegraph": CLS_API_TELEGRAPH,
            "api_roll": CLS_API_ROLL,
            "base_url": CLS_BASE,
        }
        if config:
            cls_config.update(config)
        super().__init__(session, domain, cls_config)
        self._site_name = "cls"

    async def execute(self, query: str = "", max_pages: int = 1, category: str = "telegraph", **kwargs) -> ArticleResults:
        """执行财联社搜索或分类浏览"""
        self._record_start()

        try:
            if query:
                # 有搜索关键词
                search_url = self._config.get(
                    "search_url", f"{CLS_BASE}/searchpage/abc?keyword={query}"
                ).format(query=query)
                await self._session.navigate(search_url)
                await self._wait.wait_for_selector(".search-result-item, .news-item", timeout=10.0)
            else:
                # 无搜索词：导航到分类页
                url = f"{CLS_BASE}/{category}"
                await self._session.navigate(url)
                await self._wait.wait_for_selector(".telegraph-item, .news-item, .roll-item", timeout=10.0)

            await self._wait.wait_for_network_idle(timeout=10.0)

            results = await self._parse_results(query or category)

            if max_pages > 1:
                results = await self._paginate(results, max_pages)

            results.pattern_used = f"ClsNewsPattern({self._site_name})"
            return self._record_latency(results.to_dict())

        except Exception as e:
            logger.error(f"ClsNewsPattern failed: {e}")
            return ArticleResults(
                success=False,
                query=query or category,
                error_message=str(e),
                pattern_used="ClsNewsPattern",
            )

    async def _parse_results(self, query: str) -> ArticleResults:
        """解析财联社搜索结果"""
        items = await self._session.query_selector_all(
            ".search-result-item, .news-item, [class*=\"result\"][class*=\"item\"]"
        )
        results = []

        for item in items[:20]:
            try:
                title_el = await item.query_selector(".title, h3")
                title = (await title_el.get_text()).strip() if title_el else ""

                url_el = await item.query_selector("a[href]")
                url = (await url_el.get_attribute("href")) or "" if url_el else ""
                if url and not url.startswith("http"):
                    url = CLS_BASE + url if url.startswith("/") else url

                time_el = await item.query_selector(".time")
                time_str = (await time_el.get_text()).strip() if time_el else ""

                results.append(ArticleData(
                    title=title[:200],
                    url=url,
                    publish_time=time_str,
                    source_domain=self._domain,
                    metadata={"source": "cls", "type": query},
                ))
            except Exception as e:
                logger.warning(f"Parse cls result item failed: {e}")
                continue

        return ArticleResults(
            success=True, query=query, articles=results, total_count=len(results)
        )

    async def get_telegraph(self, limit: int = 50) -> List[ArticleData]:
        """获取财联社电报（实时快讯）"""
        try:
            # 财联社电报有公开 API，直接使用
            import urllib.request
            import json
            api_url = f"{CLS_API_TELEGRAPH}?app=CailianpressWeb&os=web&sv=7.7.5&rn={limit}"

            req = urllib.request.Request(api_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            })

            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            articles = []
            roll_data = data.get("data", {}).get("roll_data", [])
            for item in roll_data[:limit]:
                articles.append(ArticleData(
                    title=item.get("title", "")[:200],
                    content=item.get("content", "")[:1000],
                    publish_time=item.get("update_time", ""),
                    source_domain=self._domain,
                    metadata={
                        "source": "cls",
                        "type": "telegraph",
                        "importance": IMPORTANCE_MAP.get(item.get("importance", "1"), "中"),
                        "id": item.get("id", ""),
                    },
                ))

            return articles
        except Exception as e:
            logger.error(f"Get cls telegraph failed: {e}")
            return []

    async def get_hot_list(self, top_n: int = 30, category: str = "telegraph") -> List[ArticleData]:
        """获取财联社热点"""
        return await self.get_telegraph(limit=top_n)
