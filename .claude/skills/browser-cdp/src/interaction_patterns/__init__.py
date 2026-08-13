"""
interaction_patterns/ - 交互模式抽象层

提供通用网站交互模式（搜索/电商/新闻/社交等）的统一接口。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.selector_manager import SelectorManager, Selector
from ..core.smart_wait_v2 import SmartWaitV2
from ..core.retry_handler import RetryHandler

from .ecommerce_pattern import (
    EcommercePattern,
    EcommerceResults,
    ProductResultItem,
)
from .taobao_search_pattern import TaobaoSearchPattern
from .jd_search_pattern import JDSearchPattern
from .news_pattern import (
    NewsPattern,
    ArticleData,
    ArticleResults,
)
from .zhihu_news_pattern import ZhihuNewsPattern
from .toutiao_news_pattern import ToutiaoNewsPattern

logger = logging.getLogger(__name__)


@dataclass
class SearchResultItem:
    """搜索结果项"""
    title: str
    url: str
    snippet: str = ""
    source_domain: str = ""
    publish_time: Optional[datetime] = None
    author: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResults:
    """搜索结果集"""
    success: bool
    query: str
    results: List[SearchResultItem] = field(default_factory=list)
    total_count: Optional[int] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0
    pattern_used: str = ""
    
    @property
    def is_empty(self) -> bool:
        return len(self.results) == 0
    
    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "query": self.query,
            "total_count": self.total_count,
            "latency_ms": self.latency_ms,
            "pattern": self.pattern_used,
            "results": [r.__dict__ for r in self.results],
        }


class InteractionPattern(ABC):
    """交互模式基类"""
    
    def __init__(self, session, domain: str, config: Optional[Dict] = None):
        self._session = session
        self._domain = domain
        self._config = config or {}
        self._selectors = SelectorManager.get_instance()
        self._wait = SmartWaitV2(session)
        self._retry = RetryHandler()
        self._start_time: float = 0.0
    
    @property
    def domain(self) -> str:
        return self._domain
    
    @property
    def selectors(self) -> SelectorManager:
        return self._selectors
    
    async def execute(self, **kwargs) -> Any:
        """执行交互流程（子类必须实现）"""
        raise NotImplementedError
    
    def validate_result(self, result: Any) -> bool:
        """验证结果有效性"""
        return result is not None
    
    def _get_selector(self, name: str) -> Optional[Selector]:
        """获取已注册的选择器"""
        return self._selectors.resolve(self._domain, name)
    
    def _record_start(self):
        self._start_time = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else __import__('time').time()
    
    def _record_latency(self, results_dict: Dict) -> Dict:
        import time
        elapsed = (time.time() - self._start_time) * 1000
        results_dict['latency_ms'] = round(elapsed, 2)
        return results_dict