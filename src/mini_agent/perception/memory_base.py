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

    def upsert(self, entry: "MemoryEntry") -> None:
        """
        按 session_id 写入/更新一条记忆条目。

        若已存在 session_id 相同的条目，先删除旧条目再 add()新条目；
        否则等价于 add()。用于"同一 session 内多次刷新摘要"场景，
        避免重复生成多条记忆。

        默认实现性能较低（O(n) 全量重写），子类可覆盖以提供更高效实现。
        """
        self.delete_by_session(entry.session_id)
        self.add(entry)

    def delete_by_session(self, session_id: str) -> None:
        """删除指定 session_id 的所有记忆条目。默认实现为 no-op，子类可覆盖。"""
        return None

    def reload(self) -> None:
        """
        重新从持久化存储加载，丢弃当前进程内的缓存状态。

        [Phase E / 3.3] 对应"SubAgent 触发的规则型 lesson 汇总写回主 agent
        memory"——SubAgent 在独立的 Agent 实例（及独立的 MemoryBackend 对象）
        里写入 lesson，物理上已经落到同一个 <project_root>/.agent/memory.jsonl
        文件（同进程内多次 open(..., "a") 追加写在 POSIX 上是安全的），但主
        agent 持有的 MemoryBackend 实例如果有本地内存缓存（如 MemoryStore 的
        self._entries 列表），该缓存不会自动感知到磁盘上新增的条目。
        SubAgent 完成后，TaskManager 应在主 agent 的 memory backend 上调用
        reload()，让后续 search() 能检索到 SubAgent 期间产生的新 lesson。

        默认实现为 no-op：无本地缓存的后端（例如直接查询远端数据库的
        Redis/Chroma 实现）不需要"重新加载"这个概念，数据本来就是实时的。
        """
        return None


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
