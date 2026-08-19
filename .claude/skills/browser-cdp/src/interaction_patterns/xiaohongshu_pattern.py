"""
xiaohongshu_pattern.py - 小红书社交内容抓取模式

实现小红书的搜索、热榜和内容抓取功能。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from ._base import SearchResultItem, SearchResults
from .social_content_pattern import SocialContentPattern, SocialPost, SocialSearchResults

logger = logging.getLogger(__name__)


class XiaohongshuPattern(SocialContentPattern):
    """小红书社交内容模式"""

    def __init__(self, session, domain: str = "xiaohongshu.com", config: Optional[Dict] = None):
        config = config or {}
        xhs_config = {
            "search_url": "https://www.xiaohongshu.com/search_result?keyword={query}",
            "hot_list_url": "https://www.xiaohongshu.com/explore",
            "note_item": ".note-item, .note-card",
            "note_title": ".note-item-title, .title",
            "note_author": ".author-name, .user-name",
            "note_likes": ".like-count, .count",
            "note_images": ".cover-img, .note-image",
            "note_url": "a[href*='explore']",
            "search_input": "input[type='text'], .search-input",
            "search_button": "button.search-btn, .search-button",
        }
        xhs_config.update(config)
        super().__init__(session, domain, xhs_config)
        self._site_name = "xiaohongshu"

    async def search(self, query: str, max_results: int = 20, **kwargs) -> SocialSearchResults:
        """搜索小红书笔记"""
        from datetime import datetime
        self._record_start()

        try:
            search_url = self._config.get("search_url", "https://www.xiaohongshu.com/search_result?keyword={query}").format(query=query)
            await self._session.navigate(search_url)
            await self._wait_for_content(timeout=15.0)

            # 等待搜索结果加载
            await self._wait.wait_for_selector(".note-item, .note-card", timeout=15.0)

            # 解析结果
            posts = await self._parse_search_results(query, max_results)
            self._record_latency(posts)
            posts.pattern_used = f"XiaohongshuPattern({self._site_name})"
            return posts

        except Exception as e:
            logger.error(f"XiaohongshuPattern search failed: {e}")
            return SocialSearchResults(
                success=False,
                query=query,
                error_message=str(e),
                pattern_used="XiaohongshuPattern"
            )

    async def _parse_search_results(self, query: str, max_results: int = 20) -> SocialSearchResults:
        """解析搜索结果"""
        items = await self._session.query_selector_all(".note-item, .note-card")
        posts = []

        for item in items[:max_results]:
            try:
                # 提取标题
                title_el = await item.query_selector(".note-item-title, .title")
                title = await title_el.get_text() if title_el else ""

                # 提取链接
                link_el = await item.query_selector("a[href*='explore']")
                url = await link_el.get_attribute("href") if link_el else ""
                if url and not url.startswith("http"):
                    url = "https://www.xiaohongshu.com" + url

                # 提取作者
                author_el = await item.query_selector(".author-name, .user-name")
                author = await author_el.get_text() if author_el else ""

                # 提取点赞数
                likes_el = await item.query_selector(".like-count, .count")
                like_text = await likes_el.get_text() if likes_el else ""
                like_count = self._parse_like_count(like_text)

                # 提取标签
                tags = self._extract_common_tags(title + " " + like_text)

                posts.append(SocialPost(
                    post_id=f"xhs_{len(posts)}",
                    title=title[:100],
                    url=url,
                    author=author[:50],
                    like_count=like_count,
                    tags=tags,
                    metadata={"source": "xiaohongshu", "query": query}
                ))
            except Exception as e:
                logger.warning(f"Parse note item failed: {e}")
                continue

        return SocialSearchResults(success=True, query=query, posts=posts)

    async def follow_user(self, username: str, user_id: Optional[str] = None) -> bool:
        """关注指定用户（步骤5核心功能）"""
        try:
            user_url = f"https://www.xiaohongshu.com/user/profile/{user_id or username}"
            await self._session.navigate(user_url)
            await self._wait.wait_for_selector(".follow-btn, button[class*=follow]", timeout=10.0)
            follow_btn = await self._session.query_selector(".follow-btn, button[class*=follow]")
            if follow_btn:
                btn_text = (await follow_btn.get_text()).strip()
                if btn_text not in ("已关注", "Following"):
                    await follow_btn.click()
                    await asyncio.sleep(1)
                return True
            return False
        except Exception as e:
            logger.error(f"XiaohongshuPattern follow_user failed: {e}")
            return False

    async def unfollow_user(self, username: str, user_id: Optional[str] = None) -> bool:
        """取消关注指定用户"""
        try:
            user_url = f"https://www.xiaohongshu.com/user/profile/{user_id or username}"
            await self._session.navigate(user_url)
            await self._wait.wait_for_selector(".follow-btn, button[class*=follow]", timeout=10.0)
            follow_btn = await self._session.query_selector(".follow-btn, button[class*=follow]")
            if follow_btn:
                btn_text = (await follow_btn.get_text()).strip()
                if btn_text in ("已关注", "Following"):
                    await follow_btn.click()
                    await asyncio.sleep(1)
                return True
            return False
        except Exception as e:
            logger.error(f"XiaohongshuPattern unfollow_user failed: {e}")
            return False

    async def get_message_notifications(self, unread_only: bool = True) -> List[Dict]:
        """获取消息推送通知（步骤5核心功能）"""
        try:
            msg_url = "https://www.xiaohongshu.com/message"
            await self._session.navigate(msg_url)
            await self._wait.wait_for_selector(".message-item, .notification-item", timeout=10.0)

            msg_items = await self._session.query_selector_all(".message-item, .notification-item")
            notifications = []
            for item in msg_items[:20]:
                try:
                    type_el = await item.query_selector(".msg-type, .notification-type")
                    msg_type = (await type_el.get_text()).strip() if type_el else "unknown"
                    content_el = await item.query_selector(".msg-content, .notification-content")
                    content = (await content_el.get_text()).strip() if content_el else ""
                    time_el = await item.query_selector(".msg-time, .notification-time")
                    time_str = (await time_el.get_text()).strip() if time_el else ""
                    unread_el = await item.query_selector(".unread-badge, .unread")
                    is_unread = bool(unread_el)
                    if not unread_only or is_unread:
                        notifications.append({
                            "type": msg_type,
                            "content": content[:300],
                            "time": time_str,
                            "is_unread": is_unread,
                        })
                except Exception:
                    continue
            return notifications
        except Exception as e:
            logger.error(f"XiaohongshuPattern get_message_notifications failed: {e}")
            return []

    async def infinite_scroll(self, max_pages: int = 5, scroll_delay: float = 1.5) -> int:
        """无限滚动加载更多帖子（步骤5核心功能）"""
        total_loaded = 0
        try:
            for page in range(max_pages):
                await self._session.evaluate(f"window.scrollTo(0, document.body.scrollHeight);")
                await asyncio.sleep(scroll_delay)
                new_items = await self._session.query_selector_all(".note-item, .note-card")
                new_count = len(new_items) - total_loaded
                total_loaded = len(new_items)
                if new_count <= 0:
                    logger.info(f"No more content loaded after page {page+1}")
                    break
            return total_loaded
        except Exception as e:
            logger.error(f"XiaohongshuPattern infinite_scroll failed: {e}")
            return total_loaded

    async def get_hot_list(self, category: str = "", limit: int = 20) -> List[Dict]:
        """获取小红书热榜"""
        try:
            await self._session.navigate(self._config.get("hot_list_url", "https://www.xiaohongshu.com/explore"))
            await self._wait_for_content(timeout=10.0)

            hot_items = await self._session.query_selector_all(".note-item, .hot-item")
            results = []

            for item in hot_items[:limit]:
                try:
                    title_el = await item.query_selector(".note-item-title, .title")
                    title = await title_el.get_text() if title_el else ""
                    rank_el = await item.query_selector(".rank, .index")
                    rank = await rank_el.get_text() if rank_el else ""
                    results.append({"title": title, "rank": rank})
                except Exception as e:
                    logger.warning(f"Parse hot item failed: {e}")

            return results
        except Exception as e:
            logger.error(f"XiaohongshuPattern get_hot_list failed: {e}")
            return []
