"""novel_writer/shelf.py — 书架：枚举所有小说工作目录。

来源有两处：默认目录 `data/novels/` 下的子目录扫描 + `data/shelf.json` 索引
（记录创建时指定在其它位置的工作目录）。书架本身只存"位置索引"，小说的
任何内容都在各自的工作目录里（方案：所有相关内容都放在工作目录下）。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer import config
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace


def _load_index(index_file: Path) -> List[str]:
    data = read_json(index_file, default={}) or {}
    items = data.get("workspaces") if isinstance(data, dict) else None
    return [str(p) for p in items] if isinstance(items, list) else []


def register(root: Path | str, *, index_file: Optional[Path] = None) -> None:
    """把工作目录登记到书架索引（默认目录下的小说不需要登记，扫描即可发现）。"""
    index_file = Path(index_file or config.SHELF_INDEX_FILE)
    resolved = str(Path(root).expanduser().resolve())
    items = _load_index(index_file)
    if resolved not in items:
        items.append(resolved)
        atomic_write_json(index_file, {"schema_version": 1, "workspaces": items})


def unregister(root: Path | str, *, index_file: Optional[Path] = None) -> bool:
    index_file = Path(index_file or config.SHELF_INDEX_FILE)
    resolved = str(Path(root).expanduser().resolve())
    items = _load_index(index_file)
    if resolved in items:
        items.remove(resolved)
        atomic_write_json(index_file, {"schema_version": 1, "workspaces": items})
        return True
    return False


def discover(novels_dir: Optional[Path] = None, index_file: Optional[Path] = None) -> List[Path]:
    """返回书架上所有存在且已初始化的工作目录（去重，按路径排序）。"""
    novels_dir = Path(novels_dir or config.NOVELS_DIR)
    index_file = Path(index_file or config.SHELF_INDEX_FILE)
    candidates: List[Path] = []
    if novels_dir.is_dir():
        candidates.extend(p for p in novels_dir.iterdir() if p.is_dir())
    candidates.extend(Path(p) for p in _load_index(index_file))
    seen: Dict[str, Path] = {}
    for p in candidates:
        ws = Workspace(p)
        if ws.is_initialized():
            seen[str(ws.root)] = ws.root
    return [seen[k] for k in sorted(seen)]


def summarize(root: Path) -> Dict[str, Any]:
    """书架卡片所需的摘要（只读文件，不调用 LLM）。"""
    ws = Workspace(root)
    rec = ws.recover()
    settings = ws.settings()
    manifest = ws.manifest()
    return {
        "workspace": str(ws.root),
        "title": rec["title"],
        "created_at": manifest.get("created_at", ""),
        "gear": settings["gear"],
        "quality": settings["quality"],
        "chapters_written": rec["chapters_written"],
        "total_chapters_planned": rec["total_chapters_planned"],
        "total_words": rec["total_words"],
        "progress_pct": rec["progress_pct"],
        "blueprint_exists": rec["blueprint_exists"],
    }


def list_novels(novels_dir: Optional[Path] = None, index_file: Optional[Path] = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for root in discover(novels_dir, index_file):
        try:
            rows.append(summarize(root))
        except Exception as exc:  # noqa: BLE001 - 单本损坏不应让整个书架不可用
            rows.append({"workspace": str(root), "title": root.name, "error": str(exc)})
    return rows
