"""
perception/catalog.py — 分类目录（图书馆"分类目录"入口）+ 知识编年目录

分类目录本身不存数据，只是"分类号 -> entry_id 列表"的指针索引，权威数据
仍是 memory.jsonl。索引文件可随时从 memory.jsonl 全量重建（rebuild），
日常运行走增量 add_entry，避免每次都重建整个索引。

知识编年目录（knowledge_timeline.jsonl）记录知识生命周期事件：一条记忆
何时生成、挂到了哪个分类/实体、何时被 巩固循环 巩固合并、何时被更新的
证据推翻。与 workdir_knowledge.py 里 W2 的 session 活动时间线是不同维度
（那个记的是"session 做了什么"，这个记的是"某条知识经历了什么"）。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Optional
from mini_agent.time_utils import ts_to_str


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=2))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.replace(tmp, path)


def _read_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class CategoryCatalog:
    """分类号 -> entry_id 列表 的指针索引，持久化为一个 JSON 文件。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, list[str]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._data = _read_json(self._path, {}) or {}

    def add_entry(self, category: str, entry_id: str) -> None:
        self._ensure_loaded()
        ids = self._data.setdefault(category, [])
        if entry_id not in ids:
            ids.append(entry_id)
            _atomic_write_json(self._path, self._data)

    def entry_ids_for(self, category: str) -> list[str]:
        self._ensure_loaded()
        return list(self._data.get(category, []))

    def redirect(self, old_code: str, new_code: str) -> None:
        """改进2：分类节点合并后，把旧分类号下的 entry_id 列表并入新规范分类号，
        旧分类号本身保留一个空列表（不删除 key，避免历史 knowledge_timeline
        事件里记录的旧分类号查目录时突然 KeyError）。"""
        self._ensure_loaded()
        old_ids = self._data.get(old_code, [])
        if not old_ids:
            return
        new_ids = self._data.setdefault(new_code, [])
        for eid in old_ids:
            if eid not in new_ids:
                new_ids.append(eid)
        self._data[old_code] = []
        _atomic_write_json(self._path, self._data)

    def rebuild(self, entries: list) -> None:
        """从权威数据（MemoryEntry 列表）全量重建索引，用于修复/迁移场景。"""
        data: dict[str, list[str]] = {}
        for e in entries:
            category = getattr(e, "category", "") or "000"
            data.setdefault(category, []).append(e.entry_id)
        self._data = data
        _atomic_write_json(self._path, data)


def append_knowledge_event(
    path: Path,
    *,
    entry_id: str,
    event_type: str,
    category: str = "",
    entity_ids: Optional[list[str]] = None,
    detail: str = "",
    index_path: Optional[Path] = None,
) -> None:
    """
    追加一条知识生命周期事件。event_type 常见取值：
      created         — 记忆条目生成并完成归类/挂载
      merged          — 被 巩固循环 巩固合并进另一条记忆
      superseded      — 被更晚近的证据推翻
      new_category    — 触发了新分类节点的诞生（entry_id 为空，category 为新分类号）
      category_merged — 分类节点被合并掉（改进2）

    index_path 给出时（推荐），同步维护一份"实体/分类 -> 行号列表"的轻量
    侧车索引（改进6），支持 load_timeline_for() 按实体/分类过滤读取而不必
    每次全文件扫描——这是本模块里唯一称得上"索引"的部分，其余的分类目录/
    实体目录都只是指针表，这里是真正按行号定位的索引。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    entity_ids = entity_ids or []
    record = {
        "ts": time.time(),
        "ts_str": ts_to_str(time.time()),
        "entry_id": entry_id,
        "event_type": event_type,
        "category": category,
        "entity_ids": entity_ids,
        "detail": detail,
    }

    index = None
    if index_path is not None:
        index = _read_json(index_path, {}) or {}
    # 行号来源优先用索引里缓存的计数器（O(1)），没有索引时才退化为扫描计数
    # （只会发生在 index_path=None 的旧调用路径上，此时行号本来也用不到）。
    if index is not None and "_line_count" in index:
        line_no = index["_line_count"]
    elif path.exists():
        with path.open("r", encoding="utf-8") as f:
            line_no = sum(1 for _ in f)
    else:
        line_no = 0

    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    if index is not None:
        keys = []
        if category:
            keys.append(f"category:{category}")
        for eid in entity_ids:
            keys.append(f"entity:{eid}")
        for key in keys:
            rows = index.setdefault(key, [])
            rows.append(line_no)
        index["_line_count"] = line_no + 1
        _atomic_write_json(index_path, index)


def load_timeline_for(
    path: Path,
    index_path: Path,
    *,
    entity_id: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """
    改进6：按实体或分类号过滤读取知识编年事件，走侧车索引直接定位行号，
    不需要扫描整个 knowledge_timeline.jsonl（大项目跑久了这个文件不小）。
    entity_id 和 category 至少给一个；都给时取交集。
    """
    if not entity_id and not category:
        return load_recent_knowledge_events(path, limit=limit)
    index = _read_json(index_path, {}) or {}
    line_sets = []
    if category:
        line_sets.append(set(index.get(f"category:{category}", [])))
    if entity_id:
        line_sets.append(set(index.get(f"entity:{entity_id}", [])))
    target_lines = line_sets[0]
    for s in line_sets[1:]:
        target_lines &= s
    if not target_lines:
        return []
    if not path.exists():
        return []
    results = []
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i in target_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except Exception:
                    continue
    results.sort(key=lambda r: r.get("ts", 0))
    return results[-limit:]


def load_recent_knowledge_events(path: Path, limit: int = 20) -> list[dict]:
    if not path.exists():
        return []
    records = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return records[-limit:]
