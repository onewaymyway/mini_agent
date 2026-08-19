"""
weibo_pattern.py - 微博社交互动模式

实现微博平台的搜索、点赞、评论、关注、分享等核心互动功能。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from .social_content_pattern import SocialContentPattern, SocialPost, SocialSearchResults
from .social_interaction_pattern import (
    SocialInteractionPattern,
    InteractionResult,
    CommentItem,
    UserFollowStatus,
)

logger = logging.getLogger(__name__)


class WeiboPattern(SocialContentPattern, SocialInteractionPattern):
    """微博社交互动模式"""

    WEIBO_SELECTORS = {
        "post_item":     "span[class='from_s'] + div, .WB_text",
        "post_content":  ".WB_text span.W_texta, .content-2Z6wY6",
        "like_btn":      "a[action-type='flike']",
        "comment_btn":   "a[action-type='comment']",
        "forward_btn":   "a[action-type='forward']",
        "follow_btn":    "a[action-type='follow']",
        "share_btn":     "a[action-type='share']",
        "comment_input": "textarea[action-type='flcomment']",
        "reply_list":    "div[class='reply-list'], ul[class='comment']",
    }

    def __init__(self, session, domain: str = "weibo.com", config: Optional[Dict] = None):
        config = config or {}
        wb_config = {
            "search_url": "https://s.weibo.com/weibo?q={query}",
            "home_url": "https://weibo.com",
            "profile_url": "https://weibo.com/u/{user_id}",
            "weibo_url": "https://weibo.com/{user_id}/{weibo_id}",
            **self.WEIBO_SELECTORS,
        }
        wb_config.update(config)
        SocialContentPattern.__init__(self, session, domain, wb_config)
        SocialInteractionPattern.__init__(self, session, domain, wb_config)
        self._site_name = "weibo"

    # ─── 搜索 ───
    async def search(self, query: str, max_results: int = 20, **kwargs) -> SocialSearchResults:
        self._record_start()
        try:
            search_url = self._config["search_url"].format(query=query)
            await self._session.navigate(search_url)
            await self._wait.wait_for_selector(".WB_text, .card-wrap", timeout=15.0)
            posts = await self._parse_weibo_search_results(query, max_results)
            return self._record_latency(posts)
        except Exception as e:
            logger.error(f"WeiboPattern search failed: {e}")
            return SocialSearchResults(success=False, query=query, error_message=str(e))

    async def _parse_weibo_search_results(self, query: str, max_results: int) -> SocialSearchResults:
        items = await self._session.query_selector_all(".WB_text, .card-wrap")
        posts = []
        for item in items[:max_results]:
            try:
                content_el = await item.query_selector(".W_texta, .content-2Z6wY6")
                content = (await content_el.get_text()).strip() if content_el else ""
                link_el = await item.query_selector("a[href*='/weibo']")
                url = await link_el.get_attribute("href") if link_el else ""
                if url and not url.startswith("http"):
                    url = "https://weibo.com" + url
                posts.append(SocialPost(
                    post_id=f"wb_{len(posts)}",
                    title=content[:100] if content else f"微博{len(posts)+1}",
                    url=url,
                    content_snippet=content[:200],
                    metadata={"source": "weibo", "query": query}
                ))
            except Exception as e:
                logger.warning(f"Parse weibo item failed: {e}")
        return SocialSearchResults(success=True, query=query, posts=posts)

    # ─── 互动操作（重写基类方法以适配微博） ───
    async def like_post(self, post_url: str, post_id: str = "") -> InteractionResult:
        try:
            if post_url:
                await self._session.navigate(post_url)
                await asyncio.sleep(2)
            like_btn = await self._session.query_selector(self._get_wb_selector("like_btn"))
            if not like_btn:
                return InteractionResult(action="like", success=False, target_id=post_id, message="Like button not found")
            btn_text = (await like_btn.get_text()).strip()
            if "点赞" in btn_text and "已赞" not in btn_text:
                await like_btn.click()
                await asyncio.sleep(1)
                return InteractionResult(action="like", success=True, target_id=post_id, target_url=post_url, message="Liked")
            return InteractionResult(action="like", success=True, target_id=post_id, target_url=post_url, message="Already liked")
        except Exception as e:
            logger.error(f"like_post failed: {e}")
            return InteractionResult(action="like", success=False, target_id=post_id, message=str(e))

    async def post_comment(self, post_url: str, content: str) -> InteractionResult:
        try:
            if post_url:
                await self._session.navigate(post_url)
                await asyncio.sleep(2)
            comment_input = await self._session.query_selector(self._get_wb_selector("comment_input"))
            if not comment_input:
                return InteractionResult(action="comment", success=False, target_url=post_url, message="Comment input not found")
            await comment_input.click()
            await comment_input.type_text(content)
            await asyncio.sleep(0.5)
            submit_btn = await self._session.query_selector(self._get_wb_selector("comment_btn"))
            if submit_btn:
                await submit_btn.click()
            await asyncio.sleep(2)
            return InteractionResult(action="comment", success=True, target_url=post_url, message="Comment posted")
        except Exception as e:
            logger.error(f"post_comment failed: {e}")
            return InteractionResult(action="comment", success=False, target_url=post_url, message=str(e))

    async def get_comments(self, post_url: str, max_comments: int = 30) -> List[CommentItem]:
        try:
            if post_url:
                await self._session.navigate(post_url)
                await asyncio.sleep(2)
            reply_items = await self._session.query_selector_all(self._get_wb_selector("reply_list"))
            comments = []
            for item in reply_items[:max_comments]:
                try:
                    text_el = await item.query_selector(".text, .WB_text")
                    text = (await text_el.get_text()).strip() if text_el else ""
                    author_el = await item.query_selector("a[name='name'], .name")
                    author = (await author_el.get_text()).strip() if author_el else "未知用户"
                    comments.append(CommentItem(
                        comment_id=f"c_{len(comments)}",
                        author=author[:50],
                        content=text[:300],
                    ))
                except Exception as e:
                    logger.warning(f"Parse comment failed: {e}")
            return comments
        except Exception as e:
            logger.error(f"get_comments failed: {e}")
            return []

    async def follow_user(self, user_url: str, user_id: str = "") -> UserFollowStatus:
        return await self._toggle_follow(user_url, user_id, action="follow")

    async def unfollow_user(self, user_url: str, user_id: str = "") -> UserFollowStatus:
        return await self._toggle_follow(user_url, user_id, action="unfollow")

    async def _toggle_follow(self, user_url: str, user_id: str, action: str) -> UserFollowStatus:
        try:
            if user_url:
                await self._session.navigate(user_url)
                await asyncio.sleep(2)
            follow_btn = await self._session.query_selector(self._get_wb_selector("follow_btn"))
            if not follow_btn:
                return UserFollowStatus(user_id=user_id or "unknown", username="", is_following=False)
            btn_text = (await follow_btn.get_text()).strip()
            want_follow = action == "follow"
            is_following = "已关注" in btn_text or "正在关注" in btn_text
            if is_following == want_follow:
                return UserFollowStatus(user_id=user_id or "unknown", username=user_id, is_following=is_following)
            await follow_btn.click()
            await asyncio.sleep(1.5)
            new_state = "已关注" in (await follow_btn.get_text()) if follow_btn else is_following
            return UserFollowStatus(user_id=user_id or "unknown", username=user_id, is_following=new_state)
        except Exception as e:
            logger.error(f"_toggle_follow failed: {e}")
            return UserFollowStatus(user_id=user_id or "unknown", username="", is_following=False, metadata={"error": str(e)})

    async def share_post(self, post_url: str) -> InteractionResult:
        try:
            if post_url:
                await self._session.navigate(post_url)
                await asyncio.sleep(2)
            share_btn = await self._session.query_selector(self._get_wb_selector("share_btn"))
            if not share_btn:
                return InteractionResult(action="share", success=False, target_url=post_url, message="Share button not found")
            await share_btn.click()
            await asyncio.sleep(1)
            current_url = await self._session.evaluate("window.location.href")
            return InteractionResult(action="share", success=True, target_url=post_url, message=f"Share dialog opened")
        except Exception as e:
            logger.error(f"share_post failed: {e}")
            return InteractionResult(action="share", success=False, target_url=post_url, message=str(e))

    # ─── 辅助方法 ───
    def _get_wb_selector(self, name: str) -> Optional[str]:
        sel = self._selectors.resolve(self._domain, name)
        if sel:
            return sel.value
        return self._config.get(name)

    async def get_hot_weibo(self, limit: int = 20) -> List[Dict]:
        try:
            await self._session.navigate(self._config.get("home_url", "https://weibo.com"))
            await asyncio.sleep(3)
            hot_items = await self._session.query_selector_all(".hot-item, .rank-item, .feed_title")
            results = []
            for item in hot_items[:limit]:
                try:
                    title_el = await item.query_selector(".title, .rank-title, span")
                    title = (await title_el.get_text()).strip() if title_el else ""
                    if title:
                        results.append({"title": title, "rank": len(results) + 1})
                except Exception:
                    continue
            return results
        except Exception as e:
            logger.error(f"get_hot_weibo failed: {e}")
            return []

    async def get_message_notifications(self, unread_only: bool = True) -> List[Dict]:
        try:
            await self._session.navigate("https://weibo.com/message")
            await asyncio.sleep(2)
            msg_items = await self._session.query_selector_all(".msg-item, .notification, .feed-item")
            notifications = []
            for item in msg_items[:20]:
                try:
                    content_el = await item.query_selector(".content, .text, .msg-text")
                    content = (await content_el.get_text()).strip() if content_el else ""
                    time_el = await item.query_selector(".time, .date")
                    time_str = (await time_el.get_text()).strip() if time_el else ""
                    unread_el = await item.query_selector(".unread, .new-badge")
                    is_unread = bool(unread_el)
                    if not unread_only or is_unread:
                        notifications.append({"content": content[:200], "time": time_str, "is_unread": is_unread})
                except Exception:
                    continue
            return notifications
        except Exception as e:
            logger.error(f"get_message_notifications failed: {e}")
            return []
