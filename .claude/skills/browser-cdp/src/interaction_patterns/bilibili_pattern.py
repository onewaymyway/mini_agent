"""
bilibili_pattern.py - B站社交内容抓取模式

实现B站的搜索、热榜和内容抓取功能。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from .social_content_pattern import SocialContentPattern, SocialPost, SocialSearchResults

logger = logging.getLogger(__name__)


class BilibiliPattern(SocialContentPattern):
    """B站社交内容模式"""

    def __init__(self, session, domain: str = "bilibili.com", config: Optional[Dict] = None):
        config = config or {}
        bilibili_config = {
            "search_url": "https://search.bilibili.com/all?keyword={query}",
            "hot_list_url": "https://api.bilibili.com/x/web-interface/popular",
            "video_item": ".video-item, .bili-video-card",
            "video_title": ".title, .video-title",
            "video_author": ".author-name, .up-name",
            "video_views": ".view-count, .stat-view",
            "video_danmaku": ".danmaku-count, .stat-dm",
            "video_url": "a[href*='video/']",
            "search_input": "input[type='text'], .search-input",
            "search_button": "button.search-btn, .search-button",
        }
        bilibili_config.update(config)
        super().__init__(session, domain, bilibili_config)
        self._site_name = "bilibili"

    async def search(self, query: str, max_results: int = 20, **kwargs) -> SocialSearchResults:
        """搜索B站视频"""
        self._record_start()

        try:
            search_url = self._config.get("search_url", "https://search.bilibili.com/all?keyword={query}").format(query=query)
            await self._session.navigate(search_url)
            await self._wait_for_content(timeout=15.0)

            await self._wait.wait_for_selector(".video-item, .bili-video-card", timeout=15.0)

            posts = await self._parse_search_results(query, max_results)
            self._record_latency(posts)
            posts.pattern_used = f"BilibiliPattern({self._site_name})"
            return posts

        except Exception as e:
            logger.error(f"BilibiliPattern search failed: {e}")
            return SocialSearchResults(
                success=False,
                query=query,
                error_message=str(e),
                pattern_used="BilibiliPattern"
            )

    async def _parse_search_results(self, query: str, max_results: int = 20) -> SocialSearchResults:
        """解析搜索结果"""
        items = await self._session.query_selector_all(".video-item, .bili-video-card")
        posts = []

        for item in items[:max_results]:
            try:
                # 提取标题
                title_el = await item.query_selector(".title, .video-title")
                title = await title_el.get_text() if title_el else ""

                # 提取链接
                link_el = await item.query_selector("a[href*='video/']")
                url = await link_el.get_attribute("href") if link_el else ""
                if url and not url.startswith("http"):
                    url = "https://www.bilibili.com" + url

                # 提取作者
                author_el = await item.query_selector(".author-name, .up-name")
                author = await author_el.get_text() if author_el else ""

                # 提取播放量
                views_el = await item.query_selector(".view-count, .stat-view")
                views_text = await views_el.get_text() if views_el else ""
                view_count = self._parse_like_count(views_text)

                # 提取弹幕数
                danmaku_el = await item.query_selector(".danmaku-count, .stat-dm")
                danmaku_text = await danmaku_el.get_text() if danmaku_el else ""
                danmaku_count = self._parse_like_count(danmaku_text)

                posts.append(SocialPost(
                    post_id=f"bili_{len(posts)}",
                    title=title[:100],
                    url=url,
                    author=author[:50],
                    like_count=view_count,
                    comment_count=danmaku_count,
                    metadata={"source": "bilibili", "query": query, "type": "video"}
                ))
            except Exception as e:
                logger.warning(f"Parse video item failed: {e}")
                continue

        return SocialSearchResults(success=True, query=query, posts=posts)

    async def get_hot_list(self, limit: int = 20) -> List[Dict]:
        """获取B站排行榜"""
        try:
            await self._session.navigate(self._config.get("hot_list_url", "https://api.bilibili.com/x/web-interface/popular"))
            await self._wait_for_content(timeout=10.0)

            hot_items = await self._session.query_selector_all(".video-item, .rank-item")
            results = []

            for item in hot_items[:limit]:
                try:
                    title_el = await item.query_selector(".title, .video-title")
                    title = await title_el.get_text() if title_el else ""
                    rank_el = await item.query_selector(".rank, .index")
                    rank = await rank_el.get_text() if rank_el else ""
                    results.append({"title": title, "rank": rank})
                except Exception as e:
                    logger.warning(f"Parse hot item failed: {e}")

            return results
        except Exception as e:
            logger.error(f"BilibiliPattern get_hot_list failed: {e}")
            return []
