"""
perception/tool_cache.py — 工具调用结果缓存。

对 read_file、web_search 等幂等工具的结果做内存缓存，
相同输入直接复用，避免重复 token 消耗和 API 调用。

修复（v2）：
  1. 路径规范化：put/get/invalidate_file 统一使用 Path.resolve().as_posix()，
     消除相对路径 vs 绝对路径 key 不一致导致 invalidate 失效的 bug。
  2. LRU 容量上限：超过 max_entries 时淘汰最久未访问的条目，
     防止长时运行内存无界增长。
  3. invalidate_file 同时清除同目录下所有 list_dir 缓存（按规范化路径匹配）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 各工具的默认 TTL（秒）。None = 永不过期（session 内有效，靠 FileWatcher 失效）
_DEFAULT_TTL: dict[str, Optional[float]] = {
    "read_file":  None,   # 由 FileWatcher 负责失效
    "web_search": 3600,   # 1 小时
    "bash":       None,   # 仅缓存明确幂等的命令
    "list_dir":   60,     # 1 分钟
}

# 只缓存这些工具
_CACHEABLE_TOOLS = {"read_file", "web_search", "list_dir"}

# 默认最大缓存条目数（防止内存无界增长）
_DEFAULT_MAX_ENTRIES = 256


@dataclass
class _CacheEntry:
    result: str
    created_at: float = field(default_factory=time.time)
    ttl: Optional[float] = None
    hits: int = 0

    @property
    def expired(self) -> bool:
        if self.ttl is None:
            return False
        return (time.time() - self.created_at) > self.ttl


def _normalize_path(path: str) -> str:
    """
    规范化路径为绝对 POSIX 字符串。
    所有 put/get/invalidate 操作都经过此函数，确保 key 一致。
    """
    try:
        return Path(path).resolve().as_posix()
    except Exception:
        return path


def _normalize_input(tool_name: str, input_dict: dict) -> dict:
    """对含路径参数的工具，规范化 path 字段后返回新 dict。"""
    if tool_name in ("read_file", "list_dir") and "path" in input_dict:
        normalized = dict(input_dict)
        normalized["path"] = _normalize_path(input_dict["path"])
        return normalized
    return input_dict


class ToolResultCache:
    """
    内存 LRU 缓存，session 内有效。

    用法：
        cache = ToolResultCache(max_entries=256)

        cached = cache.get("read_file", {"path": "app.py"})
        if cached is not None:
            return cached

        cache.put("read_file", {"path": "app.py"}, result)

        # 文件被修改时，同时清除 read_file 缓存和同目录 list_dir 缓存
        cache.invalidate_file("app.py")
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        # OrderedDict 用于 LRU：最近访问的移到末尾，淘汰头部
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._max_entries = max_entries
        self._stats = {"hits": 0, "misses": 0, "puts": 0, "evictions": 0}

    # ── 核心 API ──────────────────────────────────────────────────────────────

    def get(self, tool_name: str, input_dict: dict) -> Optional[str]:
        if tool_name not in _CACHEABLE_TOOLS:
            return None
        key = _make_key(tool_name, _normalize_input(tool_name, input_dict))
        entry = self._store.get(key)
        if entry is None or entry.expired:
            if entry and entry.expired:
                del self._store[key]
            self._stats["misses"] += 1
            return None
        # LRU：命中后移到末尾
        self._store.move_to_end(key)
        entry.hits += 1
        self._stats["hits"] += 1
        return entry.result

    def put(self, tool_name: str, input_dict: dict, result: str) -> None:
        if tool_name not in _CACHEABLE_TOOLS:
            return
        key = _make_key(tool_name, _normalize_input(tool_name, input_dict))
        ttl = _DEFAULT_TTL.get(tool_name)
        self._store[key] = _CacheEntry(result=result, ttl=ttl)
        self._store.move_to_end(key)
        self._stats["puts"] += 1
        # LRU 淘汰：超出容量时删除最久未访问的条目
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)
            self._stats["evictions"] += 1

    def invalidate_file(self, path: str) -> int:
        """
        使与某文件路径相关的所有缓存失效（read_file + 同目录 list_dir）。
        使用规范化绝对路径匹配，修复原来相对/绝对路径不一致导致失效无效的 bug。
        返回失效的条目数。
        """
        norm_path = _normalize_path(path)
        norm_dir = str(Path(norm_path).parent.as_posix())

        read_key = _make_key("read_file", {"path": norm_path})
        dir_key  = _make_key("list_dir",  {"path": norm_dir})

        to_delete = [k for k in (read_key, dir_key) if k in self._store]
        for k in to_delete:
            del self._store[k]
        return len(to_delete)

    def clear(self) -> None:
        self._store.clear()

    # ── 统计 ──────────────────────────────────────────────────────────────────

    @property
    def hit_rate(self) -> float:
        total = self._stats["hits"] + self._stats["misses"]
        return self._stats["hits"] / total if total else 0.0

    def stats_summary(self) -> str:
        h, m = self._stats["hits"], self._stats["misses"]
        e = self._stats["evictions"]
        rate = f"{self.hit_rate:.0%}"
        evict_str = f", {e} evictions" if e else ""
        return (
            f"tool cache: {h} hits / {m} misses ({rate}), "
            f"{len(self._store)}/{self._max_entries} entries{evict_str}"
        )


def _make_key(tool_name: str, input_dict: dict) -> str:
    payload = json.dumps({"tool": tool_name, "input": input_dict}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()
