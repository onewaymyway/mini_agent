"""
_base.py - InteractionPattern 基类与数据模型（无循环导入）

避免 __init__.py 循环导入：将基类和基础数据类放在独立模块中，
各 Pattern 模块从此直接导入，__init__.py 仅做 re-export。
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
        from ..core.selector_manager import SelectorManager
        from ..core.smart_wait_v2 import SmartWaitV2
        from ..core.retry_handler import RetryHandler
        self._selectors = SelectorManager.get_instance()
        self._wait = SmartWaitV2(session)
        self._retry = RetryHandler()
        self._start_time: float = 0.0

    @property
    def domain(self) -> str:
        return self._domain

    @property
    def selectors(self):
        return self._selectors

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        raise NotImplementedError

    def validate_result(self, result: Any) -> bool:
        return result is not None

    def _get_selector(self, name: str):
        from ..core.selector_manager import SelectorManager
        sel_mgr = SelectorManager.get_instance()
        return sel_mgr.resolve(self._domain, name)

    def _record_start(self):
        self._start_time = __import__('time').time()

    def _record_latency(self, results_dict: Dict) -> Dict:
        import time
        elapsed = (time.time() - self._start_time) * 1000
        results_dict['latency_ms'] = round(elapsed, 2)
        return results_dict
