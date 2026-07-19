"""
perception/tool_cache.py — 工具调用结果缓存。

对 read_file、grep、web_search 等幂等工具的结果做内存缓存，
相同输入直接复用，避免重复 token 消耗和 API 调用。

设计原则（v3）：
  缓存策略按工具的状态依赖性分三类：

  ① 永不缓存（状态依赖型）：
       bash      — 副作用不可预测，每次执行结果都可能不同
       list_dir  — 目录内容随文件增删实时变化，60 s TTL 远不足以保证正确性
       glob      — 同上，文件系统遍历结果实时变化

  ② 内容寻址缓存（mtime-keyed）：
       read_file — cache key = (path, start_line, end_line, mtime)
                   文件 mtime 变化后自动落 miss，无需等 FileWatcher 下一轮
       grep      — cache key = (pattern, path, file_pattern, case_sensitive, dir_mtime)
                   以搜索根目录的 mtime 作为 key 组件，目录有写入即失效

  ③ 时间 TTL 缓存（纯幂等）：
       web_search — 1 小时，查询结果与本地文件系统无关

历史修复（v2）：
  1. 路径规范化：put/get/invalidate_file 统一使用 Path.resolve().as_posix()。
  2. LRU 容量上限：超过 max_entries 时淘汰最久未访问的条目。
  3. invalidate_file 同时清除同目录下所有 list_dir 缓存（已废弃，list_dir 不再缓存）。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# 各工具的默认 TTL（秒）。None = 永不过期（依赖 mtime 或显式 invalidate 失效）
_DEFAULT_TTL: dict[str, Optional[float]] = {
    "read_file":  None,   # mtime-keyed，文件变动即 miss
    "grep":       None,   # mtime-keyed，目录写入即 miss
    "web_search": 3600,   # 1 小时
}

# 只缓存这些工具
# 注意：bash / list_dir / glob 是状态依赖型工具，故意不在此列表中。
_CACHEABLE_TOOLS = {"read_file", "grep", "web_search"}

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


def _get_mtime(path: str) -> Optional[float]:
    """获取文件或目录的 mtime；不存在时返回 None。"""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def _normalize_path(path: str) -> str:
    """
    规范化路径为绝对 POSIX 字符串。
    所有 put/get/invalidate 操作都经过此函数，确保 key 一致。
    """
    try:
        return Path(path).resolve().as_posix()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.perception.tool_cache._normalize_path')
        return path


def _normalize_input(tool_name: str, input_dict: dict) -> dict:
    """
    对含路径参数的工具，规范化路径并注入 mtime，使 cache key 与文件内容绑定。

    read_file：key 包含 (path, start_line, end_line, mtime)
               — 文件任何写入都会改变 mtime，自动触发 miss，无需等 FileWatcher
    grep：      key 包含 (pattern, path, file_pattern, case_sensitive, _dir_mtime)
               — 以搜索根目录的 mtime 作为 key 组件，目录有新增/删除文件即失效
               — 注意：只检测根目录的 mtime，深层子目录的写入不会反映在此，
                 对于跨目录的深度搜索，FileWatcher invalidate 是补充保障
    """
    if tool_name == "read_file" and "path" in input_dict:
        normalized = dict(input_dict)
        norm_path = _normalize_path(input_dict["path"])
        normalized["path"] = norm_path
        normalized["_mtime"] = _get_mtime(norm_path)
        return normalized

    if tool_name == "grep" and "path" in input_dict:
        normalized = dict(input_dict)
        norm_path = _normalize_path(input_dict["path"])
        normalized["path"] = norm_path
        normalized["_dir_mtime"] = _get_mtime(norm_path)
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

    线程安全（Phase E，3.3，对应设计文档第 5 节"SubAgent 信息继承"）：
    默认情况下每个 Agent 实例持有自己的 ToolResultCache（session 内私有，
    无并发访问）。但 TaskManager 可选择持有一个跨 SubAgent 共享的全局实例
    （见 orchestrator/task_manager.py），此时多个 SubAgent 线程会并发调用
    get/put/invalidate_file——内部用一把 threading.Lock 保护所有读写 self._store
    （含 OrderedDict 的 move_to_end/popitem 等非原子操作）和 self._stats 的代码段，
    保证共享场景下不会出现 dict 结构损坏或计数错乱。私有场景下加锁的开销可忽略
    （无竞争锁），不单独区分"共享/私有"两套实现以保持代码简单。
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        # OrderedDict 用于 LRU：最近访问的移到末尾，淘汰头部
        self._store: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._max_entries = max_entries
        self._stats = {"hits": 0, "misses": 0, "puts": 0, "evictions": 0}
        self._lock = threading.Lock()

    # ── 核心 API ──────────────────────────────────────────────────────────────

    def get(self, tool_name: str, input_dict: dict) -> Optional[str]:
        if tool_name not in _CACHEABLE_TOOLS:
            return None
        key = _make_key_with_index(tool_name, _normalize_input(tool_name, input_dict))
        with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.expired:
                if entry and entry.expired:
                    del self._store[key]
                    _key_registry.pop(key, None)
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
        key = _make_key_with_index(tool_name, _normalize_input(tool_name, input_dict))
        ttl = _DEFAULT_TTL.get(tool_name)
        with self._lock:
            self._store[key] = _CacheEntry(result=result, ttl=ttl)
            self._store.move_to_end(key)
            self._stats["puts"] += 1
            # LRU 淘汰：超出容量时删除最久未访问的条目，同步清理 registry
            while len(self._store) > self._max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                _key_registry.pop(evicted_key, None)
                self._stats["evictions"] += 1

    def invalidate_file(self, path: str) -> int:
        """
        使与某文件路径相关的所有缓存失效。

        由于 read_file 和 grep 的 cache key 已内嵌 mtime，文件修改后
        下次调用会自动 miss——invalidate_file 的作用是在同一毫秒内发生
        写操作后立即清除旧 key（避免 mtime 未变的极端情况），以及
        为旧版 key（无 mtime 字段）提供向后兼容清除。

        清除范围：
          - read_file({path})                       — 精确匹配
          - grep({path=任意值}) where key contains norm_path  — 模式匹配
        返回失效的条目数。
        """
        norm_path = _normalize_path(path)

        with self._lock:
            # 精确清除 read_file key（含/不含 mtime 后缀的旧 key 都要清）
            to_delete: list[str] = []
            for key in list(self._store.keys()):
                entry_key_data = _key_to_tool_path_hint(key)
                if entry_key_data is None:
                    continue
                t, p = entry_key_data
                if t == "read_file" and p == norm_path:
                    to_delete.append(key)
                elif t == "grep" and (p == norm_path or p.startswith(norm_path)):
                    # grep 以该文件或其父目录为根时一并失效
                    to_delete.append(key)

            for k in to_delete:
                del self._store[k]
            return len(to_delete)

    def clear(self) -> None:
        with self._lock:
            _key_registry.clear()
            self._store.clear()

    # ── 统计 ──────────────────────────────────────────────────────────────────

    @property
    def hit_rate(self) -> float:
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            return self._stats["hits"] / total if total else 0.0

    def stats_summary(self) -> str:
        with self._lock:
            h, m = self._stats["hits"], self._stats["misses"]
            e = self._stats["evictions"]
            entries = len(self._store)
        rate_total = h + m
        rate = f"{(h / rate_total if rate_total else 0.0):.0%}"
        evict_str = f", {e} evictions" if e else ""
        return (
            f"tool cache: {h} hits / {m} misses ({rate}), "
            f"{entries}/{self._max_entries} entries{evict_str}"
        )


def _make_key(tool_name: str, input_dict: dict) -> str:
    payload = json.dumps({"tool": tool_name, "input": input_dict}, sort_keys=True)
    return hashlib.md5(payload.encode()).hexdigest()


# _key_registry: 记录 md5-key → (tool_name, norm_path)，供 invalidate_file 反查。
# 这是一个模块级弱索引（不持有值，只持有路由信息），避免扫描 store 全量条目。
_key_registry: dict[str, tuple[str, str]] = {}


def _make_key_with_index(tool_name: str, input_dict: dict) -> str:
    """
    生成 cache key 并在 _key_registry 中登记 (tool_name, norm_path)，
    使 invalidate_file 能够 O(1) 反查而无需扫描全部 store。
    内部使用，由 put/get 调用。
    """
    payload = json.dumps({"tool": tool_name, "input": input_dict}, sort_keys=True)
    key = hashlib.md5(payload.encode()).hexdigest()
    norm_path = input_dict.get("path") or input_dict.get("_path", "")
    if norm_path:
        _key_registry[key] = (tool_name, str(norm_path))
    return key


def _key_to_tool_path_hint(key: str) -> Optional[tuple[str, str]]:
    """从 _key_registry 查询 (tool_name, norm_path)；未登记则返回 None。"""
    return _key_registry.get(key)