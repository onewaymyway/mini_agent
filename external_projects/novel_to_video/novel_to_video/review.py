"""review.py — 全书级素材（角色/地点）的审核状态，存放于 global/review.json。

每个实体一条记录，键为 "character:<id>" / "location:<id>"：
  text_status    : missing | pending | approved   （视觉/声音设定）
  portrait_status: missing | pending | approved | stale | failed   （定妆图）
  portrait_version: int   （重生成时递增，避免界面缓存旧图）
  last_error     : str | None
  updated_at     : str
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .project_store import read_json, write_json_atomic

REVIEW_FILE = "global/review.json"


def entity_key(kind: str, entity_id: str) -> str:
    return f"{kind}:{entity_id}"


def load_review(project_dir: Path) -> Dict[str, Any]:
    path = project_dir / REVIEW_FILE
    if not path.is_file():
        return {"schema_version": 1, "items": {}}
    return read_json(path)


def _save(project_dir: Path, data: Dict[str, Any]) -> None:
    write_json_atomic(project_dir / REVIEW_FILE, data)


def get_item(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    items = data.setdefault("items", {})
    return items.setdefault(key, {
        "text_status": "missing", "portrait_status": "missing",
        "portrait_version": 0, "last_error": None, "updated_at": None,
    })


def update_item(project_dir: Path, key: str, **fields: Any) -> Dict[str, Any]:
    data = load_review(project_dir)
    item = get_item(data, key)
    item.update(fields)
    item["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save(project_dir, data)
    return item


def save_review(project_dir: Path, data: Dict[str, Any]) -> None:
    _save(project_dir, data)
