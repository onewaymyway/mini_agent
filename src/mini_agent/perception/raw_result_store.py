"""
perception/raw_result_store.py — 原始工具结果留存仓库。

背景（[SYS-RAWSTORE]）：
  ToolExecutor._trim_result() 对超长工具结果做截断（规则截断）或摘要
  （[SYS-SMARTTRIM] LLM 摘要）后，原来的做法是直接丢弃原文，agent 之后
  再也看不到完整内容。RawResultStore 让"被截断/摘要过的原文"在 session
  内继续保留一份，配合 tools/builtin.py 里的 view_raw_result 工具，agent
  需要时可以按 result_id（+可选行号范围）取回完整内容。

设计：
  - session 内内存 LRU（风格参考 perception/tool_cache.py），不做跨进程持久化。
  - 双重容量限制：条目数上限（max_entries）+ 总字符数上限（max_total_chars），
    任一超限都会从最久未访问的条目开始淘汰，防止长 session 内存无界增长。
  - id 使用内容 md5 短哈希：同一段原文多次被截断也只存一份，天然去重。
  - 线程安全：用一把锁保护，开销可忽略（无高频并发场景）。

不做的事：
  - 不做磁盘落地 / 跨 session 持久化（原文本来就是"当前 session 内可回看"，
    session 结束后随进程释放是预期行为；如果未来需要跨 session 保留，
    可以在这里加一个可选的 spill-to-disk 分支，思路和 tool_cache 的
    mtime-keyed 缓存类似，暂不在本次范围内）。
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional


_DEFAULT_MAX_ENTRIES = 128
_DEFAULT_MAX_TOTAL_CHARS = 5_000_000


@dataclass
class _RawEntry:
    content: str
    tool_name: str = ""
    created_at: float = field(default_factory=time.time)


class RawResultStore:
    """
    原始工具结果的 session 内 LRU 仓库。

    用法：
        store = RawResultStore(max_entries=128, max_total_chars=5_000_000)

        result_id = store.put(tool_name="bash", content=full_output)
        # ... 截断后的文本里附带 result_id 提示 ...

        content = store.get(result_id)   # 需要时取回完整原文
    """

    def __init__(
        self,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        max_total_chars: int = _DEFAULT_MAX_TOTAL_CHARS,
    ) -> None:
        self._store: "OrderedDict[str, _RawEntry]" = OrderedDict()
        self._max_entries = max_entries
        self._max_total_chars = max_total_chars
        self._total_chars = 0
        self._lock = threading.Lock()
        self._stats = {"puts": 0, "gets": 0, "misses": 0, "evictions": 0}

    # ── 核心 API ──────────────────────────────────────────────────────────────

    def put(self, content: str, tool_name: str = "") -> str:
        """存入原文，返回 result_id（内容 md5 短哈希，天然去重）。"""
        result_id = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:12]
        with self._lock:
            existing = self._store.get(result_id)
            if existing is not None:
                # 已存在同内容条目，只需刷新 LRU 位置，不重复计入总字符数
                self._store.move_to_end(result_id)
                return result_id

            self._store[result_id] = _RawEntry(content=content, tool_name=tool_name)
            self._total_chars += len(content)
            self._stats["puts"] += 1
            self._evict_if_needed()
            return result_id

    def get(self, result_id: str) -> Optional[str]:
        with self._lock:
            entry = self._store.get(result_id)
            self._stats["gets"] += 1
            if entry is None:
                self._stats["misses"] += 1
                return None
            self._store.move_to_end(result_id)
            return entry.content

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._total_chars = 0

    # ── 内部：容量淘汰 ────────────────────────────────────────────────────────

    def _evict_if_needed(self) -> None:
        """按 LRU 淘汰最久未访问的条目，直到满足条目数 & 总字符数上限。调用方需已持锁。"""
        while self._store and (
            len(self._store) > self._max_entries
            or self._total_chars > self._max_total_chars
        ):
            _evicted_id, evicted_entry = self._store.popitem(last=False)
            self._total_chars -= len(evicted_entry.content)
            self._stats["evictions"] += 1

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def stats_summary(self) -> str:
        with self._lock:
            entries = len(self._store)
            chars = self._total_chars
            e = self._stats["evictions"]
        evict_str = f", {e} evictions" if e else ""
        return (
            f"raw result store: {entries}/{self._max_entries} entries, "
            f"{chars}/{self._max_total_chars} chars{evict_str}"
        )
