"""
perception/memory_base.py — 记忆后端抽象接口

所有记忆后端必须实现 MemoryBackend。
Agent 和 ContextBuilder 只依赖此接口，与具体实现完全解耦。

接入新后端步骤：
  1. 继承 MemoryBackend，实现 add / search / search_by_tag
  2. 在 memory_factory.py 的 _REGISTRY 中注册

内置实现：
  MemoryStore（local）— JSONL + TF-IDF，无外部依赖（默认）

扩展示例（外部依赖，不内置）：
  ChromaMemoryBackend — 向量检索，需要 chromadb
  RedisMemoryBackend  — 跨进程共享，需要 redis-py
  SQLiteMemoryBackend — 关系型存储，适合多用户 Web 场景
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry


class MemoryBackend(ABC):
    """
    记忆后端统一接口。

    所有后端都必须满足：
    - add() 持久化一条记忆，立即可被后续 search() 查到
    - search() 返回与 query 最相关的 top-k 条，按相关性降序
    - search_by_tag() 精确标签匹配
    - count 属性返回当前记忆总数
    """

    @abstractmethod
    def add(self, entry: "MemoryEntry") -> None:
        """
        持久化一条记忆条目。
        若后端有容量上限，实现内部负责淘汰旧条目。
        """
        ...

    @abstractmethod
    def search(self, query: str, k: int = 3) -> list["MemoryEntry"]:
        """
        语义/关键词检索，返回 top-k 相关条目（按相关性降序）。
        score=0 的条目不应出现在结果中。
        """
        ...

    @abstractmethod
    def search_by_tag(self, tag: str) -> list["MemoryEntry"]:
        """精确标签匹配，返回所有含该标签的条目。"""
        ...

    @property
    @abstractmethod
    def count(self) -> int:
        """当前记忆条目总数。"""
        ...

    def all_entries(self) -> list["MemoryEntry"]:
        """
        返回所有条目（用于导出/调试）。
        默认实现：子类可覆盖以提供更高效的实现。
        """
        return self.search("", k=self.count) if self.count > 0 else []

    @property
    def backend_name(self) -> str:
        """后端的可读名称，用于日志和 UI 展示。"""
        return self.__class__.__name__
