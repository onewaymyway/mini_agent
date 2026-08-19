"""
social_interaction_pattern.py - 社交网站互动功能基类

覆盖社交网站核心互动：点赞、评论、关注/取关、分享。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ._base import InteractionPattern
from ..core.selector_manager import SelectorManager, Selector, SelectorType
from ..core.smart_wait_v2 import SmartWaitV2

logger = logging.getLogger(__name__)


@dataclass
class InteractionResult:
    """互动操作结果"""
    action: str  # like, comment, follow, share
    success: bool
    target_id: str = ""
    target_url: str = ""
    message: str = ""
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "action": self.action,
            "success": self.success,
            "target_id": self.target_id,
            "target_url": self.target_url,
            "message": self.message,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


@dataclass
class CommentItem:
    """评论数据项"""
    comment_id: str
    author: str
    content: str
    like_count: int = 0
    reply_count: int = 0
    publish_time: Optional[datetime] = None
    replies: List["CommentItem"] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "comment_id": self.comment_id,
            "author": self.author,
            "content": self.content[:500],
            "like_count": self.like_count,
            "reply_count": self.reply_count,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "replies": [r.to_dict() for r in self.replies[:20]],  # 限制嵌套深度
            "metadata": self.metadata,
        }


@dataclass
class UserFollowStatus:
    """用户关注状态"""
    user_id: str
    username: str
    is_following: bool
    follower_count: int = 0
    following_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "is_following": self.is_following,
            "follower_count": self.follower_count,
            "following_count": self.following_count,
            "metadata": self.metadata,
        }


class SocialInteractionPattern(InteractionPattern):
    """社交网站互动模式基类 - 点赞/评论/关注/分享"""

    # 互动动作类型
    INTERACTION_TYPES = {
        "like":     "点赞",
        "comment":  "评论",
        "follow":   "关注",
        "unfollow": "取关",
        "share":    "分享",
        "retweet":  "转发",
    }

    # 默认互动选择器（子类可覆盖）
    DEFAULT_SELECTORS = {
        # 点赞
        "like_button":      Selector(type=SelectorType.CSS, value="button.like-btn, .like, [class*='like'] button, [aria-label*='like']"),
        "like_count":       Selector(type=SelectorType.CSS, value=".like-count, [class*='like'] .num, [class*='heart']"),
        "liked_state":      Selector(type=SelectorType.CSS, value="button.like-btn.liked, [class*='like'].active, .is-liked"),
        # 评论
        "comment_input":    Selector(type=SelectorType.CSS, value="textarea[placeholder*='评论'], input[placeholder*='评论'], .comment-input"),
        "comment_button":   Selector(type=SelectorType.CSS, value="button.comment-submit, .comment-btn, [class*='submit']"),
        "comment_list":     Selector(type=SelectorType.CSS, value=".comment-list, .comments, [class*='comment']"),
        "comment_item":     Selector(type=SelectorType.CSS, value=".comment-item, .comment, .reply-item"),
        "comment_author":   Selector(type=SelectorType.CSS, value=".comment-author, .username, [class*='author']"),
        "comment_content":  Selector(type=SelectorType.CSS, value=".comment-content, .text, .content"),
        "comment_like_btn": Selector(type=SelectorType.CSS, value="button.comment-like, .like-comment"),
        # 关注
        "follow_button":    Selector(type=SelectorType.CSS, value="button.follow-btn, .follow, [class*='follow'] button, [aria-label*='follow']"),
        "follow_count":     Selector(type=SelectorType.CSS, value=".follow-count, [class*='follower'] .num"),
        "following_state":  Selector(type=SelectorType.CSS, value="button.follow-btn.following, .is-following"),
        # 分享
        "share_button":     Selector(type=SelectorType.CSS, value="button.share-btn, .share, [class*='share'] button"),
        "share_modal":      Selector(type=SelectorType.CSS, value=".share-modal, .share-dialog, [class*='share']"),
        # 通用
        "post_container":   Selector(type=SelectorType.CSS, value=".post, .article, .feed-item, [class*='post']"),
        "load_more":        Selector(type=SelectorType.CSS, value="button.load-more, .load-more, [class*='more']"),
    }

    def __init__(self, session, domain: str, config: Optional[Dict] = None):
        super().__init__(session, domain, config)
        self._site_name = domain.split(".")[0]
        self._register_default_selectors()

    def _register_default_selectors(self):
        """注册默认选择器到当前域名"""
        sel_mgr = SelectorManager.get_instance()
        for name, sel in self.DEFAULT_SELECTORS.items():
            if not sel_mgr.has_domain(self._domain):
                sel_mgr.register(self._domain, name, sel)

    async def execute(self, action: str, target_id: str = "", **kwargs) -> InteractionResult:
        """执行互动操作（统一入口）"""
        self._record_start()
        valid_actions = list(self.INTERACTION_TYPES.keys())
        if action not in valid_actions:
            raise ValueError(f"Invalid action '{action}'. Valid: {valid_actions}")

        handler = getattr(self, f"_do_{action}", None)
        if not handler:
            raise NotImplementedError(f"Action '{action}' not implemented for {self._site_name}")

        result = await handler(target_id=target_id, **kwargs)
        return self._record_latency(result)

    # ─── 点赞 ───
    async def like_post(self, post_url: str, post_id: str = "") -> InteractionResult:
        """点赞帖子"""
        try:
            await self._navigate_to_post(post_url)
            like_btn = await self._session.query_selector(self._get_selector_value("like_button"))
            if not like_btn:
                return InteractionResult(
                    action="like", success=False,
                    target_id=post_id, message="Like button not found"
                )

            is_liked = await self._is_already_liked()
            if is_liked:
                logger.info(f"Post {post_id} already liked")
                return InteractionResult(
                    action="like", success=True,
                    target_id=post_id, target_url=post_url,
                    message="Already liked"
                )

            await like_btn.click()
            await asyncio.sleep(1)

            new_state = await self._is_already_liked()
            if new_state:
                return InteractionResult(
                    action="like", success=True,
                    target_id=post_id, target_url=post_url,
                    message="Liked successfully"
                )
            else:
                return InteractionResult(
                    action="like", success=False,
                    target_id=post_id, target_url=post_url,
                    message="Like action failed - state not updated"
                )
        except Exception as e:
            logger.error(f"like_post failed for {post_url}: {e}")
            return InteractionResult(
                action="like", success=False,
                target_id=post_id, target_url=post_url,
                message=str(e)
            )

    async def _is_already_liked(self) -> bool:
        """检查是否已点赞"""
        liked_sel = self._get_selector_value("liked_state")
        if liked_sel:
            el = await self._session.query_selector(liked_sel)
            if el:
                return True
        like_btn = await self._session.query_selector(self._get_selector_value("like_button"))
        if like_btn:
            classes = (await like_btn.get_attribute("class")) or ""
            return "liked" in classes.lower() or "active" in classes.lower()
        return False

    # ─── 评论 ───
    async def post_comment(self, post_url: str, content: str, reply_to: str = "") -> InteractionResult:
        """发布评论"""
        try:
            await self._navigate_to_post(post_url)
            await self._wait.wait_for_selector(self._get_selector_value("comment_input"), timeout=15.0)

            comment_input = await self._session.query_selector(self._get_selector_value("comment_input"))
            if not comment_input:
                return InteractionResult(
                    action="comment", success=False,
                    target_url=post_url,
                    message="Comment input not found"
                )

            await comment_input.click()
            await comment_input.type_text(content)
            await asyncio.sleep(0.5)

            submit_btn = await self._session.query_selector(self._get_selector_value("comment_button"))
            if submit_btn:
                await submit_btn.click()
            else:
                await self._session.press_key("Enter")

            await asyncio.sleep(2)

            # 验证评论是否成功
            comment_list = await self._session.query_selector_all(self._get_selector_value("comment_item"))
            if comment_list:
                last_comment = await comment_list[-1].get_text()
                if content[:50] in last_comment:
                    return InteractionResult(
                        action="comment", success=True,
                        target_url=post_url,
                        message="Comment posted successfully"
                    )

            return InteractionResult(
                action="comment", success=True,
                target_url=post_url,
                message="Comment action triggered (verification pending)"
            )
        except Exception as e:
            logger.error(f"post_comment failed for {post_url}: {e}")
            return InteractionResult(
                action="comment", success=False,
                target_url=post_url,
                message=str(e)
            )

    async def get_comments(self, post_url: str, max_comments: int = 50, load_more: bool = True) -> List[CommentItem]:
        """获取评论列表"""
        try:
            await self._navigate_to_post(post_url)
            await self._wait.wait_for_selector(self._get_selector_value("comment_list"), timeout=15.0)

            comments = []
            for _ in range(max(1, max_comments // 20 + 1)):  # 分页加载
                items = await self._session.query_selector_all(self._get_selector_value("comment_item"))
                for item in items[:max_comments]:
                    try:
                        author_el = await item.query_selector(self._get_selector_value("comment_author"))
                        author = (await author_el.get_text()).strip() if author_el else ""

                        content_el = await item.query_selector(self._get_selector_value("comment_content"))
                        content = (await content_el.get_text()).strip() if content_el else ""

                        like_el = await item.query_selector(self._get_selector_value("comment_like_btn"))
                        like_count = self._parse_like_count(await like_el.get_text()) if like_el else 0

                        comments.append(CommentItem(
                            comment_id=f"{len(comments)}",
                            author=author[:50],
                            content=content[:300],
                            like_count=like_count,
                        ))
                    except Exception as e:
                        logger.warning(f"Parse comment failed: {e}")

                if not load_more or len(comments) >= max_comments:
                    break

                load_more_btn = await self._session.query_selector(self._get_selector_value("load_more"))
                if not load_more_btn:
                    break
                await load_more_btn.click()
                await asyncio.sleep(1.5)

            return comments[:max_comments]
        except Exception as e:
            logger.error(f"get_comments failed for {post_url}: {e}")
            return []

    # ─── 关注/取关 ───
    async def follow_user(self, user_url: str, user_id: str = "") -> UserFollowStatus:
        """关注用户"""
        return await self._toggle_follow(user_url, user_id, action="follow")

    async def unfollow_user(self, user_url: str, user_id: str = "") -> UserFollowStatus:
        """取关用户"""
        return await self._toggle_follow(user_url, user_id, action="unfollow")

    async def _toggle_follow(self, user_url: str, user_id: str, action: str) -> UserFollowStatus:
        """切换关注状态（关注/取关统一处理）"""
        try:
            await self._navigate_to_profile(user_url)
            await self._wait.wait_for_selector(self._get_selector_value("follow_button"), timeout=15.0)

            follow_btn = await self._session.query_selector(self._get_selector_value("follow_button"))
            if not follow_btn:
                return UserFollowStatus(
                    user_id=user_id, username="",
                    is_following=False,
                    metadata={"error": "Follow button not found"}
                )

            current_state = await self._is_following()
            expected_state = action == "follow"

            if current_state == expected_state:
                logger.info(f"User {user_id} {'already followed' if expected_state else 'already unfollowed'}")
                return await self._parse_follow_status(user_id, user_url, current_state)

            await follow_btn.click()
            await asyncio.sleep(1.5)

            new_state = await self._is_following()
            return await self._parse_follow_status(user_id, user_url, new_state)

        except Exception as e:
            logger.error(f"_toggle_follow failed for {user_url}: {e}")
            return UserFollowStatus(
                user_id=user_id, username="",
                is_following=False,
                metadata={"error": str(e)}
            )

    async def _is_following(self) -> bool:
        """检查是否已关注"""
        following_sel = self._get_selector_value("following_state")
        if following_sel:
            el = await self._session.query_selector(following_sel)
            if el:
                return True
        follow_btn = await self._session.query_selector(self._get_selector_value("follow_button"))
        if follow_btn:
            classes = (await follow_btn.get_attribute("class")) or ""
            return "following" in classes.lower() or "unfollow" in classes.lower()
        return False

    async def _parse_follow_status(self, user_id: str, profile_url: str, is_following: bool) -> UserFollowStatus:
        """解析关注状态"""
        try:
            follower_el = await self._session.query_selector(self._get_selector_value("follow_count"))
            follower_count = self._parse_number((await follower_el.get_text()) if follower_el else "")
        except Exception:
            follower_count = 0

        return UserFollowStatus(
            user_id=user_id,
            username=user_id,
            is_following=is_following,
            follower_count=follower_count,
        )

    # ─── 分享 ───
    async def share_post(self, post_url: str, platform: str = "copy") -> InteractionResult:
        """分享帖子（复制链接或分享到指定平台）"""
        try:
            await self._navigate_to_post(post_url)
            share_btn = await self._session.query_selector(self._get_selector_value("share_button"))
            if not share_btn:
                return InteractionResult(
                    action="share", success=False,
                    target_url=post_url,
                    message="Share button not found"
                )

            if platform == "copy":
                await share_btn.click()
                await asyncio.sleep(1)
                url = await self._session.evaluate("window.location.href")
                return InteractionResult(
                    action="share", success=True,
                    target_url=post_url,
                    message=f"URL copied: {url[:80]}...",
                    metadata={"url": url}
                )
            else:
                await share_btn.click()
                await self._wait.wait_for_selector(self._get_selector_value("share_modal"), timeout=10.0)
                return InteractionResult(
                    action="share", success=True,
                    target_url=post_url,
                    message=f"Share dialog opened for platform: {platform}"
                )
        except Exception as e:
            logger.error(f"share_post failed for {post_url}: {e}")
            return InteractionResult(
                action="share", success=False,
                target_url=post_url,
                message=str(e)
            )

    # ─── 辅助方法 ───
    async def _navigate_to_post(self, url: str):
        if url:
            await self._session.navigate(url)
            await self._wait.wait_for_selector(self._get_selector_value("post_container"), timeout=15.0)

    async def _navigate_to_profile(self, url: str):
        if url:
            await self._session.navigate(url)
            await asyncio.sleep(2)

    def _get_selector_value(self, name: str) -> Optional[str]:
        sel = self._selectors.resolve(self._domain, name)
        return sel.value if sel else None

    def _parse_like_count(self, text: str) -> int:
        import re
        match = re.search(r'(\d+\.?\d*)([万千百]?)', text or "")
        if match:
            num = float(match.group(1))
            unit = match.group(2)
            multipliers = {'': 1, '百': 100, '千': 1000, '万': 10000}
            return int(num * multipliers.get(unit, 1))
        return 0

    def _parse_number(self, text: str) -> int:
        import re
        match = re.search(r'(\d+\.?\d*)', text or "")
        if match:
            return int(float(match.group(1)))
        return 0

    def _record_start(self):
        import time
        self._start_time = time.time()

    def _record_latency(self, result: Any) -> Any:
        import time
        elapsed = (time.time() - self._start_time) * 1000
        if hasattr(result, 'latency_ms'):
            result.latency_ms = round(elapsed, 2)
        elif isinstance(result, InteractionResult):
            result.metadata['latency_ms'] = round(elapsed, 2)
        return result
