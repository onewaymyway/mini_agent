"""
perception/tool_cache.py — 工具调用结果缓存。

对 read_file、web_search 等幂等工具的结果做内存缓存，
相同输入直接复用，避免重复 token 消耗和 API 调用。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Optional


# 各工具的默认 TTL（秒）。None = 永不过期（session 内有效）
_DEFAULT_TTL: dict[str, Optional[float]] = {
    "read_file":  None,     # 由 FileWatcher 负责失效，这里不过期
    "web_search": 3600,     # 1 小时
    "bash":       None,     # 仅缓存明确幂等的命令
    "list_dir":   60,       # 1 分钟
}

# 只缓存这些工具
_CACHEABLE_TOOLS = {"read_file", "web_search", "list_dir"}


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


class ToolResultCache:
    """
    内存缓存，session 内有效。

    用法：
        cache = ToolResultCache()

        # 查询
        cached = cache.get("read_file", {"path": "app.py"})
        if cached is not None:
            return cached

        # 写入
        cache.put("read_file", {"path": "app.py"}, result)

        # 让某路径的 read_file 缓存失效（文件被修改时）
        cache.invalidate_file("app.py")
    """

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._stats = {"hits": 0, "misses": 0, "puts": 0}

    # ── 核心 API ──────────────────────────────────────────────────────────────

    def get(self, tool_name: str, input_dict: dict) -> Optional[str]:
        if tool_name not in _CACHEABLE_TOOLS:
            return None
        key = _make_key(tool_name, input_dict)
        entry = self._store.get(key)
        if entry is None or entry.expired:
            if entry and entry.expired:
                del self._store[key]
            self._stats["misses"] += 1
            return None
        entry.hits += 1
        self._stats["hits"] += 1
        return entry.result

    def put(self, tool_name: str, input_dict: dict, result: str) -> None:
        if tool_name not in _CACHEABLE_TOOLS:
            return
        ttl = _DEFAULT_TTL.get(tool_name)
        key = _make_key(tool_name, input_dict)
        self._store[key] = _CacheEntry(result=result, ttl=ttl)
        self._stats["puts"] += 1

    def invalidate_file(self, path: str) -> int:
        """
        使与某文件路径相关的所有 read_file / list_dir 缓存失效。
        返回失效的条目数。
        """
        prefix = _make_key("read_file", {"path": path})
        to_delete = [k for k in self._store if k == prefix]
        # 也清除同目录的 list_dir
        import os
        dir_path = str(os.path.dirname(path))
        dir_key = _make_key("list_dir", {"path": dir_path})
        if dir_key in self._store:
            to_delete.append(dir_key)
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
        rate = f"{self.hit_rate:.0%}"
        return f"tool cache: {h} hits / {m} misses ({rate}), {len(self._store)} entries"


def _make_key(tool_name: str, input_dict: dict) -> str:
    payload = json.dumps({"tool": tool_name, "input": input_dict}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()
