"""
perception/catalog.py — 分类目录（图书馆"分类目录"入口）+ 知识编年目录

分类目录本身不存数据，只是"分类号 -> entry_id 列表"的指针索引，权威数据
仍是 memory.jsonl。索引文件可随时从 memory.jsonl 全量重建（rebuild），
日常运行走增量 add_entry，避免每次都重建整个索引。

知识编年目录（knowledge_timeline.jsonl）记录知识生命周期事件：一条记忆
何时生成、挂到了哪个分类/实体、何时被 Phase G 巩固合并、何时被更新的
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
) -> None:
    """
    追加一条知识生命周期事件。event_type 常见取值：
      created         — 记忆条目生成并完成归类/挂载
      merged          — 被 Phase G 巩固合并进另一条记忆
      superseded      — 被更晚近的证据推翻
      new_category    — 触发了新分类节点的诞生（entry_id 为空，category 为新分类号）
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "entry_id": entry_id,
        "event_type": event_type,
        "category": category,
        "entity_ids": entity_ids or [],
        "detail": detail,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


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
