#!/usr/bin/env python
"""
base.py - 搜索器抽象基类

定义统一的搜索器接口，所有搜索器实现此基类。
集成可靠性保障层：统一重试、错误分类、智能等待。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from datetime import datetime
import json
from pathlib import Path

# 集成可靠性保障层
from src.reliability import (
    SearcherConfig as ReliabilitySearcherConfig,
    SearcherMixin,
    SearcherErrorProcessor,
    run_cmd_with_retry,
    run_cmd_with_retry_sync,
    is_retryable,
    categorize_error,
)
from src.reliability.error import (
    ErrorCategory,
    ReliabilityError,
    CDPConnectionLostError,
    ElementNotFoundError,
    NavigationTimeoutError,
)


@dataclass
class SearcherConfig:
    """搜索器通用配置"""
    # 浏览器配置
    port: int = 9333
    tab_id: Optional[str] = None
    session_name: Optional[str] = None
    
    # 搜索配置
    query: str = ""
    max_results: int = 10
    page_size: int = 20
    
    # 等待配置
    wait_timeout: int = 30
    wait_strategy: str = "networkidle"  # networkidle/route/stable/selector
    
    # 反爬配置
    stealth: bool = True
    handle_captcha: bool = False
    random_delay_range: tuple = (2, 5)  # 随机延迟范围（秒）
    
    # 输出配置
    output_dir: Optional[str] = None
    output_format: str = "json"  # json/csv/markdown
    save_detail: bool = False
    
    # 去重配置
    dedup_by: str = "url"  # url/title/simhash
    dedup_threshold: float = 0.9
    
    def to_dict(self) -> Dict:
        return {
            "port": self.port,
            "tab_id": self.tab_id,
            "session_name": self.session_name,
            "query": self.query,
            "max_results": self.max_results,
            "page_size": self.page_size,
            "wait_timeout": self.wait_timeout,
            "wait_strategy": self.wait_strategy,
            "stealth": self.stealth,
            "handle_captcha": self.handle_captcha,
            "random_delay_range": list(self.random_delay_range),
            "output_dir": self.output_dir,
            "output_format": self.output_format,
            "save_detail": self.save_detail,
            "dedup_by": self.dedup_by,
            "dedup_threshold": self.dedup_threshold,
        }


@dataclass
class SearchResult:
    """搜索结果统一格式（单条记录）"""
    source: str = ""               # 数据源标识
    title: str = ""                # 标题
    url: str = ""                  # 原始链接
    snippet: str = ""              # 摘要/片段
    published_time: Optional[str] = None
    author: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    scraped_at: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "published_time": self.published_time,
            "author": self.author,
            "metadata": self.metadata,
            "scraped_at": self.scraped_at or datetime.now().isoformat(),
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass
class SearchResults:
    """批量搜索结果容器"""
    source: str                    # 数据源标识
    query: str                     # 搜索关键词
    total_results: int = 0         # 总结果数
    results: List[SearchResult] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {
            "source": self.source,
            "query": self.query,
            "total_results": self.total_results,
            "results": [r.to_dict() for r in self.results],
            "metadata": self.metadata,
            "error": self.error,
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    @classmethod
    def from_json(cls, json_str: str) -> "SearchResults":
        data = json.loads(json_str)
        items = [SearchResult(**r) for r in data.get("results", [])]
        return cls(
            source=data.get("source", ""),
            query=data.get("query", ""),
            total_results=data.get("total_results", 0),
            results=items,
        )

    def deduplicate(self, by: str = "url", threshold: float = 0.9) -> int:
        """去重，返回移除的数量"""
        from src.searchers.utils import dedup_results
        original_count = len(self.results)
        if original_count <= 1:
            return 0
        # 使用原始对象进行去重，保留子类字段
        dicts = [r.to_dict() for r in self.results]
        unique_dicts = dedup_results(dicts, by=by, threshold=threshold)
        # 重建原始对象（保留子类字段）
        self.results = [type(r)(**d) for r, d in zip(self.results, unique_dicts)]
        return original_count - len(self.results)


class BaseSearcher(ABC, SearcherMixin):
    """搜索器抽象基类（集成可靠性保障）"""
    
    def __init__(self, config: Optional[SearcherConfig] = None):
        # 先初始化可靠性层
        SearcherMixin.__init__(self)
        self._config = config or SearcherConfig()
        self._error_processor = SearcherErrorProcessor(self.__class__.__name__)
    
    @property
    def config(self) -> SearcherConfig:
        return self._config
    
    @property
    def error_processor(self) -> SearcherErrorProcessor:
        return self._error_processor
    
    def process_error(self, error: Exception, context: Optional[Dict] = None) -> Dict[str, Any]:
        """处理错误并记录"""
        return self._error_processor.process_error(error, context)
    
    def should_retry(self, error: Exception) -> bool:
        """判断是否应该重试"""
        return is_retryable(error)
    
    def get_error_summary(self) -> Dict[str, Any]:
        """获取错误统计"""
        return self._error_processor.get_error_summary()
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称"""
        ...
    
    @property
    @abstractmethod
    def supported_types(self) -> List[str]:
        """支持的数据类型"""
        ...
    
    @abstractmethod
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        """执行搜索"""
        ...
    
    @abstractmethod
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        """获取详情页内容"""
        ...
    
    async def health_check(self) -> bool:
        """健康检查"""
        return True
    
    async def close(self):
        """关闭资源"""
        pass
    
    @property
    def default_config(self) -> SearcherConfig:
        return SearcherConfig()
    
    def validate_config(self, config: SearcherConfig) -> bool:
        """验证配置合法性"""
        if config.max_results < 1:
            raise ValueError("max_results 必须 >= 1")
        if config.wait_timeout < 5:
            raise ValueError("wait_timeout 必须 >= 5")
        if config.wait_strategy not in ["networkidle", "route", "stable", "ajax", "selector"]:
            raise ValueError(f"不支持的 wait_strategy: {config.wait_strategy}")
        return True
    
    def format_results(self, results: List[SearchResult], fmt: str = "json") -> str:
        """格式化输出结果"""
        if fmt == "json":
            return json.dumps(
                [r.to_dict() for r in results],
                ensure_ascii=False,
                indent=2
            )
        elif fmt == "markdown":
            lines = [f"# {self.source_name} 搜索结果", f"共找到 {len(results)} 条结果\n"]
            for i, r in enumerate(results, 1):
                lines.append(f"## {i}. {r.title}")
                lines.append(f"- 来源: {r.source}")
                lines.append(f"- 链接: {r.url}")
                if r.snippet:
                    lines.append(f"- 摘要: {r.snippet[:100]}...")
                lines.append("")
            return "\n".join(lines)
        else:
            return self.format_results(results, "json")


class AsyncBaseSearcher(BaseSearcher):
    """异步搜索器基类（用于支持 async/await 的实现）"""
    
    async def search(self, query: str, config: Optional[SearcherConfig] = None) -> List[SearchResult]:
        raise NotImplementedError("子类必须实现 search 方法")
    
    async def get_detail(self, url: str, config: Optional[SearcherConfig] = None) -> Dict:
        raise NotImplementedError("子类必须实现 get_detail 方法")


# 导出公共接口
__all__ = [
    "SearcherConfig",
    "SearchResult",
    "SearchResults",
    "BaseSearcher",
    "AsyncBaseSearcher",
]
