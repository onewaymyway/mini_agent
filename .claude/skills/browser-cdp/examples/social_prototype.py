#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
社交网站原型 - 演示社交核心功能

功能：
1. 用户主页（个人信息、粉丝、关注）
2. 动态流（发布、浏览、点赞、评论）
3. 互动功能（关注、私信）

基于 browser-cdp skill 架构规范实现
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

SKILL_ROOT = os.path.join(os.path.dirname(__file__), '..', '..', '..')
sys.path.insert(0, SKILL_ROOT)

logger = logging.getLogger(__name__)


# ============================================================================
# 枚举类型定义
# ============================================================================

class PostType(Enum):
    """动态类型"""
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    LINK = "link"
    POLL = "poll"


class RelationshipStatus(Enum):
    """关注状态"""
    NONE = "none"
    FOLLOWING = "following"
    FOLLOWER = "follower"
    MUTUAL = "mutual"


class PrivacyLevel(Enum):
    """隐私级别"""
    PUBLIC = "public"
    FRIENDS = "friends"
    PRIVATE = "private"


class MessageStatus(Enum):
    """消息状态"""
    SENT = "sent"
    READ = "read"
    DELIVERED = "delivered"
    FAILED = "failed"


# ============================================================================
# 数据模型定义
# ============================================================================

@dataclass
class UserProfile:
    """用户资料"""
    user_id: str = ""
    username: str = ""
    nickname: str = ""
    avatar: str = ""
    bio: str = ""
    gender: str = ""
    location: str = ""
    website: str = ""
    joined_at: str = ""
    followers_count: int = 0
    following_count: int = 0
    posts_count: int = 0
    is_verified: bool = False
    privacy_level: PrivacyLevel = PrivacyLevel.PUBLIC
    tags: List[str] = field(default_factory=list)
    scraped_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "nickname": self.nickname,
            "avatar": self.avatar,
            "bio": self.bio,
            "gender": self.gender,
            "location": self.location,
            "website": self.website,
            "joined_at": self.joined_at,
            "followers_count": self.followers_count,
            "following_count": self.following_count,
            "posts_count": self.posts_count,
            "is_verified": self.is_verified,
            "privacy_level": self.privacy_level.value,
            "tags": self.tags,
            "scraped_at": self.scraped_at,
        }


@dataclass
class Post:
    """动态/帖子"""
    post_id: str = ""
    author_id: str = ""
    author_name: str = ""
    content: str = ""
    post_type: PostType = PostType.TEXT
    images: List[str] = field(default_factory=list)
    video_url: str = ""
    link_url: str = ""
    link_title: str = ""
    link_desc: str = ""
    privacy_level: PrivacyLevel = PrivacyLevel.PUBLIC
    created_at: str = ""
    updated_at: str = ""
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    is_liked: bool = False
    is_pinned: bool = False
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "post_id": self.post_id,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "content": self.content,
            "post_type": self.post_type.value,
            "images": self.images,
            "video_url": self.video_url,
            "link_title": self.link_title,
            "privacy_level": self.privacy_level.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "is_liked": self.is_liked,
            "is_pinned": self.is_pinned,
            "tags": self.tags,
        }


@dataclass
class Comment:
    """评论"""
    comment_id: str = ""
    post_id: str = ""
    author_id: str = ""
    author_name: str = ""
    content: str = ""
    parent_comment_id: str = ""
    like_count: int = 0
    is_liked: bool = False
    created_at: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "comment_id": self.comment_id,
            "post_id": self.post_id,
            "author_id": self.author_id,
            "author_name": self.author_name,
            "content": self.content,
            "parent_comment_id": self.parent_comment_id,
            "like_count": self.like_count,
            "is_liked": self.is_liked,
            "created_at": self.created_at,
            "tags": self.tags,
        }


@dataclass
class Message:
    """私信"""
    message_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    receiver_id: str = ""
    receiver_name: str = ""
    content: str = ""
    message_type: str = "text"
    status: MessageStatus = MessageStatus.SENT
    created_at: str = ""
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "receiver_id": self.receiver_id,
            "receiver_name": self.receiver_name,
            "content": self.content,
            "message_type": self.message_type,
            "status": self.status.value,
            "created_at": self.created_at,
            "tags": self.tags,
        }


# ============================================================================
# 配置文件加载器
# ============================================================================

class ConfigLoader:
    """网站配置加载器"""

    def __init__(self, config_dir: str = None):
        self.config_dir = config_dir or os.path.join(os.path.dirname(__file__), '..', 'config', 'websites')
        self.config_dir = os.path.normpath(self.config_dir)
        self._cache: Dict[str, Dict] = {}

    def load(self, domain: str) -> Optional[Dict]:
        if domain in self._cache:
            return self._cache[domain]
        config_file = os.path.join(self.config_dir, f"{domain}.json")
        if not os.path.exists(config_file):
            logger.warning(f"Config not found: {config_file}")
            return None
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            self._cache[domain] = config
            return config
        except Exception as e:
            logger.error(f"Failed to load config {config_file}: {e}")
            return None

    def list_configs(self) -> List[str]:
        configs = []
        if not os.path.exists(self.config_dir):
            return configs
        for f in os.listdir(self.config_dir):
            if f.endswith('.json') and f not in ['template.json', 'example.com.json']:
                configs.append(f.replace('.json', ''))
        return sorted(configs)


# ============================================================================
# 动态解析器
# ============================================================================

class SocialParser:
    """社交网站动态解析器基类"""

    def __init__(self, domain: str, config: Dict):
        self.domain = domain
        self.config = config
        self.selectors = config.get('custom_config', {})

    def parse_user_profile(self, html: str) -> Optional[UserProfile]:
        """解析用户主页"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        profile = UserProfile()
        name_sel = self.selectors.get('username', '.username, .display-name')
        el = soup.select_one(name_sel)
        if el:
            profile.username = el.get_text(strip=True)
        bio_sel = self.selectors.get('bio', '.bio, .profile-bio')
        el = soup.select_one(bio_sel)
        if el:
            profile.bio = el.get_text(strip=True)[:500]
        follower_sel = self.selectors.get('followers', '.followers-count')
        el = soup.select_one(follower_sel)
        if el:
            text = el.get_text(strip=True)
            try:
                profile.followers_count = int(text.replace(',', ''))
            except ValueError:
                pass
        profile.scraped_at = datetime.now().isoformat()
        return profile

    def parse_timeline(self, html: str) -> List[Post]:
        """解析动态流"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        posts = []
        item_sel = self.selectors.get('post_item', '.timeline-item, .post, .feed-item')
        items = soup.select(item_sel)
        for item in items:
            post = Post()
            author_el = item.select_one('.author-name, .username, [class*=\"author\"]')
            if author_el:
                post.author_name = author_el.get_text(strip=True)
            content_el = item.select_one('.content, .text, [class*=\"content\"]')
            if content_el:
                post.content = content_el.get_text(strip=True)[:1000]
            for img in item.select('img')[:5]:
                src = img.get('src') or img.get('data-src')
                if src:
                    post.images.append(src)
            time_el = item.select_one('.time, .date')
            if time_el:
                post.created_at = time_el.get_text(strip=True)
            like_el = item.select_one('.like-count, [class*=\"like\"]')
            if like_el:
                try:
                    post.like_count = int(like_el.get_text(strip=True).replace(',', ''))
                except ValueError:
                    pass
            comment_el = item.select_one('.comment-count, [class*=\"comment\"]')
            if comment_el:
                try:
                    post.comment_count = int(comment_el.get_text(strip=True).replace(',', ''))
                except ValueError:
                    pass
            posts.append(post)
        return posts

    def parse_comments(self, html: str) -> List[Comment]:
        """解析评论内容"""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'lxml')
        comments = []
        item_sel = self.selectors.get('comment_item', '.comment-item, .comment')
        for item in soup.select(item_sel):
            comment = Comment()
            author_el = item.select_one('.comment-author, .username')
            if author_el:
                comment.author_name = author_el.get_text(strip=True)
            content_el = item.select_one('.comment-content, .text')
            if content_el:
                comment.content = content_el.get_text(strip=True)[:500]
            comments.append(comment)
        return comments


# ============================================================================
# 用户服务
# ============================================================================

class UserService:
    """用户管理服务"""

    def __init__(self):
        self._users: Dict[str, UserProfile] = {}
        self._follows: Dict[str, set] = {}  # user_id -> set of following user_ids

    def get_user(self, user_id: str) -> Optional[UserProfile]:
        return self._users.get(user_id)

    def add_user(self, user: UserProfile) -> bool:
        self._users[user.user_id] = user
        if user.user_id not in self._follows:
            self._follows[user.user_id] = set()
        return True

    def follow_user(self, follower_id: str, following_id: str) -> bool:
        if follower_id not in self._users or following_id not in self._users:
            return False
        if following_id not in self._follows[follower_id]:
            self._follows[follower_id].add(following_id)
            self._users[following_id].followers_count += 1
            self._users[follower_id].following_count += 1
            return True
        return False

    def unfollow_user(self, follower_id: str, following_id: str) -> bool:
        if follower_id not in self._users or following_id not in self._users:
            return False
        if following_id in self._follows[follower_id]:
            self._follows[follower_id].remove(following_id)
            self._users[following_id].followers_count -= 1
            self._users[follower_id].following_count -= 1
            return True
        return False

    def get_relationship(self, user1_id: str, user2_id: str) -> RelationshipStatus:
        if user1_id not in self._users or user2_id not in self._users:
            return RelationshipStatus.NONE
        u1_follows_u2 = user2_id in self._follows[user1_id]
        u2_follows_u1 = user1_id in self._follows.get(user2_id, set())
        if u1_follows_u2 and u2_follows_u1:
            return RelationshipStatus.MUTUAL
        elif u1_follows_u2:
            return RelationshipStatus.FOLLOWING
        elif u2_follows_u1:
            return RelationshipStatus.FOLLOWER
        return RelationshipStatus.NONE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "users": {uid: u.to_dict() for uid, u in self._users.items()},
            "follows": {uid: sorted(list(following)) for uid, following in self._follows.items()},
        }


# ============================================================================
# 动态服务
# ============================================================================

class PostService:
    """动态管理服务"""

    def __init__(self):
        self._posts: Dict[str, Post] = {}
        self._comments: Dict[str, List[Comment]] = {}
        self._post_likes: Dict[str, set] = {}  # post_id -> set of user_ids

    def add_post(self, post: Post) -> bool:
        self._posts[post.post_id] = post
        return True

    def like_post(self, user_id: str, post_id: str) -> bool:
        if post_id not in self._posts:
            return False
        post = self._posts[post_id]
        if post_id not in self._post_likes:
            self._post_likes[post_id] = set()
        if user_id not in self._post_likes[post_id]:
            self._post_likes[post_id].add(user_id)
            post.like_count += 1
            post.is_liked = True
            return True
        return False

    def unlike_post(self, user_id: str, post_id: str) -> bool:
        if post_id not in self._posts:
            return False
        post = self._posts[post_id]
        if post_id in self._post_likes and user_id in self._post_likes[post_id]:
            self._post_likes[post_id].remove(user_id)
            post.like_count = max(0, post.like_count - 1)
            post.is_liked = False
            return True
        return False

    def add_comment(self, comment: Comment) -> bool:
        if comment.post_id not in self._posts:
            return False
        if comment.post_id not in self._comments:
            self._comments[comment.post_id] = []
        self._comments[comment.post_id].append(comment)
        self._posts[comment.post_id].comment_count += 1
        return True

    def get_post(self, post_id: str) -> Optional[Post]:
        return self._posts.get(post_id)

    def get_comments(self, post_id: str) -> List[Comment]:
        return self._comments.get(post_id, [])

    def get_user_posts(self, user_id: str, limit: int = 20) -> List[Post]:
        return [p for p in self._posts.values() if p.author_id == user_id][:limit]

    def get_timeline(self, user_id: str, following: set, limit: int = 20) -> List[Post]:
        timeline = []
        for post in self._posts.values():
            if post.author_id == user_id or post.author_id in following:
                timeline.append(post)
        timeline.sort(key=lambda x: x.created_at, reverse=True)
        return timeline[:limit]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "posts": {pid: p.to_dict() for pid, p in self._posts.items()},
            "comments": {pid: [c.to_dict() for c in cs] for pid, cs in self._comments.items()},
            "post_likes": {pid: sorted(list(uids)) for pid, uids in self._post_likes.items()},
        }


# ============================================================================
# 私信服务
# ============================================================================

class MessageService:
    """私信管理服务"""

    def __init__(self):
        self._messages: Dict[str, Message] = {}
        self._conversations: Dict[str, List[str]] = {}

    def send_message(self, msg: Message) -> bool:
        self._messages[msg.message_id] = msg
        conv_id = self._get_conversation_id(msg.sender_id, msg.receiver_id)
        if conv_id not in self._conversations:
            self._conversations[conv_id] = []
        self._conversations[conv_id].append(msg.message_id)
        return True

    def _get_conversation_id(self, user1: str, user2: str) -> str:
        return '_'.join(sorted([user1, user2]))

    def get_conversation(self, user1: str, user2: str) -> List[Message]:
        conv_id = self._get_conversation_id(user1, user2)
        msg_ids = self._conversations.get(conv_id, [])
        return [self._messages[mid] for mid in msg_ids if mid in self._messages]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages": {mid: m.to_dict() for mid, m in self._messages.items()},
            "conversations": {
                cid: [self._messages[mid].to_dict() for mid in mids if mid in self._messages]
                for cid, mids in self._conversations.items()
            },
        }


# ============================================================================
# 主控制器
# ============================================================================

class SocialPrototype:
    """社交网站原型主控制器"""

    def __init__(self):
        self.config_loader = ConfigLoader()
        self.user_service = UserService()
        self.post_service = PostService()
        self.message_service = MessageService()
        self._next_post_id = 1
        self._next_msg_id = 1
        self._next_user_seq = 1  # 序列号计数器，避免时间戳精度问题

    def create_user(self, username: str, nickname: str = "", avatar: str = "") -> str:
        """创建用户，返回 user_id"""
        user_id = f"U{self._next_user_seq:08d}"
        self._next_user_seq += 1
        user = UserProfile(
            user_id=user_id,
            username=username,
            nickname=nickname or username,
            avatar=avatar,
            joined_at=datetime.now().isoformat(),
        )
        self.user_service.add_user(user)
        return user_id

    def get_user(self, user_id: str) -> Optional[UserProfile]:
        return self.user_service.get_user(user_id)

    def create_post(self, author_id: str, content: str,
                    post_type: PostType = PostType.TEXT,
                    images: List[str] = None) -> Post:
        post_id = f"P{self._next_post_id:08d}"
        self._next_post_id += 1
        author = self.get_user(author_id)
        post = Post(
            post_id=post_id,
            author_id=author_id,
            author_name=author.username if author else "Unknown",
            content=content,
            post_type=post_type,
            images=images or [],
            created_at=datetime.now().isoformat(),
        )
        self.post_service.add_post(post)
        return post

    def like_post(self, user_id: str, post_id: str) -> bool:
        return self.post_service.like_post(user_id, post_id)

    def comment_on_post(self, user_id: str, post_id: str, content: str) -> Optional[Comment]:
        author = self.get_user(user_id)
        if not author:
            return None
        comment = Comment(
            comment_id=f"C{int(time.time() * 1000000)}",
            post_id=post_id,
            author_id=user_id,
            author_name=author.username,
            content=content,
            created_at=datetime.now().isoformat(),
        )
        self.post_service.add_comment(comment)
        return comment

    def follow(self, follower_id: str, following_id: str) -> bool:
        return self.user_service.follow_user(follower_id, following_id)

    def send_message(self, sender_id: str, receiver_id: str, content: str) -> Message:
        sender = self.get_user(sender_id)
        receiver = self.get_user(receiver_id)
        msg = Message(
            message_id=f"M{self._next_msg_id:08d}",
            sender_id=sender_id,
            sender_name=sender.username if sender else "Unknown",
            receiver_id=receiver_id,
            receiver_name=receiver.username if receiver else "Unknown",
            content=content,
            created_at=datetime.now().isoformat(),
        )
        self._next_msg_id += 1
        self.message_service.send_message(msg)
        return msg

    # ------------------------------------------------------------------
    # 用户资料编辑接口（步骤5新增）
    # ------------------------------------------------------------------
    def update_username(self, user_id: str, new_username: str) -> Optional[UserProfile]:
        """修改用户名（需唯一，长度3-20字符）"""
        user = self.get_user(user_id)
        if not user:
            return None
        if not new_username or len(new_username) < 3 or len(new_username) > 20:
            raise ValueError("用户名长度必须在3-20个字符之间")
        # 检查唯一性
        for uid, u in self.user_service._users.items():
            if uid != user_id and u.username == new_username:
                raise ValueError(f"用户名 '{new_username}' 已存在")
        user.username = new_username
        user.nickname = new_username
        return user

    def update_bio(self, user_id: str, bio: str) -> Optional[UserProfile]:
        """修改个人签名（最多200字符）"""
        user = self.get_user(user_id)
        if not user:
            return None
        if len(bio) > 200:
            raise ValueError("签名长度不能超过200个字符")
        user.bio = bio[:200]
        return user

    def update_avatar(self, user_id: str, avatar_url: str) -> Optional[UserProfile]:
        """修改头像URL"""
        user = self.get_user(user_id)
        if not user:
            return None
        if not avatar_url or not (avatar_url.startswith('http://') or avatar_url.startswith('https://')):
            raise ValueError("头像URL必须以 http:// 或 https:// 开头")
        user.avatar = avatar_url
        return user

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        """获取完整用户资料"""
        user = self.get_user(user_id)
        if not user:
            return None
        profile = user.to_dict()
        # 附加关注关系状态
        profile['following_ids'] = sorted(list(self.user_service._follows.get(user_id, set())))
        return profile

    def run_demo(self):
        print("=" * 60)
        print("  browser-cdp 社交网站原型演示")
        print("=" * 60)

        # 1. 创建用户
        print("\n【创建用户】")
        uid1 = self.create_user("zhangsan", "张三", "https://example.com/avatar1.jpg")
        uid2 = self.create_user("lisi", "李四", "https://example.com/avatar2.jpg")
        uid3 = self.create_user("wangwu", "王五", "https://example.com/avatar3.jpg")
        u1 = self.get_user(uid1)
        u2 = self.get_user(uid2)
        u3 = self.get_user(uid3)
        print(f"  创建用户: {u1.username}, {u2.username}, {u3.username}")

        # 2. 关注关系
        print("\n【建立关注关系】")
        self.follow(uid1, uid2)
        self.follow(uid1, uid3)
        self.follow(uid2, uid1)
        rel = self.user_service.get_relationship(uid1, uid2)
        print(f"  {u1.username} 和 {u2.username}: {rel.value}")

        # 3. 发布动态
        print("\n【发布动态】")
        post1 = self.create_post(uid1, "今天天气真不错！阳光灿烂，适合出门散步。", PostType.TEXT, [])
        post2 = self.create_post(uid2, "分享一张美丽的风景照", PostType.IMAGE,
                                  ["https://example.com/img1.jpg", "https://example.com/img2.jpg"])
        post3 = self.create_post(uid3, "大家觉得这个项目怎么样？", PostType.TEXT, [])
        print(f"  发布动态: {post1.post_id} ({post1.author_name}), "
              f"{post2.post_id} ({post2.author_name}), "
              f"{post3.post_id} ({post3.author_name})")

        # 4. 点赞互动
        print("\n【点赞互动】")
        self.like_post(uid2, post1.post_id)
        self.like_post(uid3, post1.post_id)
        self.like_post(uid1, post2.post_id)
        p1 = self.post_service.get_post(post1.post_id)
        print(f"  动态 {post1.post_id}: {p1.like_count} 个赞")

        # 5. 发表评论
        print("\n【发表评论】")
        c1 = self.comment_on_post(uid2, post1.post_id, "说得对！一起出去走走吧")
        c2 = self.comment_on_post(uid3, post1.post_id, "羡慕了，我在加班 😭")
        if c1 and c2:
            p1 = self.post_service.get_post(post1.post_id)
            print(f"  动态 {post1.post_id}: {p1.comment_count} 条评论")

        # 6. 查看动态流
        print("\n【查看动态流】")
        following = self.user_service._follows.get(uid1, set())
        timeline = self.post_service.get_timeline(uid1, following, limit=10)
        print(f"  {u1.username} 的动态流: {len(timeline)} 条动态")
        for post in timeline[:3]:
            print(f"    [{post.post_id}] {post.author_name}: {post.content[:30]}...")

        # 7. 发送私信
        print("\n【发送私信】")
        msg = self.send_message(uid1, uid2, "你好，最近怎么样？")
        print(f"  发送私信: {msg.message_id} 给 {msg.receiver_name}")
        conv = self.message_service.get_conversation(uid1, uid2)
        print(f"  对话记录: {len(conv)} 条消息")

        # 8. 用户信息
        print("\n【用户信息】")
        for u in [u1, u2, u3]:
            print(f"  {u.username}: {u.followers_count}粉丝/{u.following_count}关注/{u.posts_count}动态")

        # 9. 用户资料编辑（步骤5新增）
        print("\n【用户资料编辑】")
        updated = self.update_username(uid1, "zhangsan_dev")
        print(f"  修改用户名: {updated.username}")
        self.update_bio(uid1, "全栈开发工程师 | Python爱好者")
        self.update_avatar(uid1, "https://example.com/avatar_zhangsan.jpg")
        profile = self.get_profile(uid1)
        print(f"  最新资料: 昵称={profile['nickname']}, 签名={profile['bio'][:20]}...")
        # 测试冲突检测
        try:
            self.update_username(uid1, "zhangsan_dev")  # 重复，应报错
        except ValueError as e:
            print(f"  冲突检测正常: {e}")

        # 10. 系统统计
        print("\n【系统统计】")
        print(f"  已注册用户: {len(self.user_service._users)} 人")
        print(f"  已发布动态: {len(self.post_service._posts)} 条")
        print(f"  已发送私信: {len(self.message_service._messages)} 条")
        print(f"  已配置网站: {len(self.config_loader.list_configs())} 个")

        print("\n" + "=" * 60)
        print("  演示完成!")
        print("=" * 60)


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    proto = SocialPrototype()
    proto.run_demo()
