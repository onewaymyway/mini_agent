"""
social_content_pattern.py - SocialContentPattern 基类

提供小红书/B站等社交媒体平台的搜索与内容抓取通用框架。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SocialPost:
    """社交帖子"""
    post_id: str
    title: str
    url: str
    author: Optional[str] = None
    content_snippet: str = ""
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    publish_time: Optional[datetime] = None
    tags: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "post_id": self.post_id,
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "content_snippet": self.content_snippet[:300],
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
            "tags": self.tags[:10],
            "image_count": len(self.images),
            "metadata": self.metadata,
        }


@dataclass
class SocialSearchResults:
    """社交搜索结集"""
    success: bool
    query: str
    posts: List[SocialPost] = field(default_factory=list)
    total_count: Optional[int] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    pattern_used: str = ""

    @property
    def is_empty(self) -> bool:
        return len(self.posts) == 0

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "query": self.query,
            "total_count": self.total_count,
            "latency_ms": self.latency_ms,
            "pattern_used": self.pattern_used,
            "posts": [p.to_dict() for p in self.posts],
            "error_message": self.error_message,
        }


class SocialContentPattern(ABC):
    """社交内容模式基类"""

    def __init__(self, session, domain: str, config: Optional[Dict] = None):
        self._session = session
        self._domain = domain
        self._config = config or {}
        from ..core.selector_manager import SelectorManager
        from ..core.smart_wait_v2 import SmartWaitV2
        self._selectors = SelectorManager.get_instance()
        self._wait = SmartWaitV2(session)
        self._start_time: float = 0.0
        self._site_name = domain.split(".")[0]

    @property
    def domain(self) -> str:
        return self._domain

    @abstractmethod
    async def search(self, query: str, max_results: int = 20, **kwargs) -> SocialSearchResults:
        """执行社交搜索"""
        raise NotImplementedError

    async def get_post_detail(self, post_url: str) -> Optional[SocialPost]:
        """获取单篇帖子详情（子类可覆盖）"""
        logger.warning(f"get_post_detail not implemented for {self._site_name}")
        return None

    async def get_hot_list(self, category: str = "", limit: int = 20) -> List[Dict]:
        """获取热榜（子类可覆盖）"""
        logger.warning(f"get_hot_list not implemented for {self._site_name}")
        return []

    def _record_start(self):
        self._start_time = __import__('time').time()

    def _record_latency(self, results):
        import time
        elapsed = (time.time() - self._start_time) * 1000
        if hasattr(results, 'latency_ms'):
            results.latency_ms = round(elapsed, 2)
        return results

    def _extract_common_tags(self, text: str) -> List[str]:
        """从文本中提取标签（以#xxx#格式）"""
        import re
        tags = re.findall(r'#([^\s#]+)#', text)
        return list(dict.fromkeys(tags[:10]))  # 去重并限制数量

    def _parse_like_count(self, text: str) -> int:
        """解析点赞数文本"""
        import re
        # 匹配数字+单位（万、千等）
        match = re.search(r'(\d+\.?\d*)([万千百]?)', text or "")
        if match:
            num = float(match.group(1))
            unit = match.group(2)
            if unit == '万':
                return int(num * 10000)
            elif unit == '千':
                return int(num * 1000)
            elif unit == '百':
                return int(num * 100)
            return int(num)
        return 0

    async def _wait_for_content(self, timeout: float = 15.0):
        """等待动态内容加载"""
        await self._wait.wait_for_network_idle(timeout=timeout)
        await asyncio.sleep(1)  # 额外缓冲
