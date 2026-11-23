"""catalog.py — 文章索引 library/index.json（文章 id 分配、哈希查重、列表）。

事实源是 library/ 下的文章文件本身；index.json 是它们的目录。index.json 缺失但文章文件存在
（例如 P0 时手动放入的 Markdown）时，会从文件重建目录，保留 front matter 里已有的 id。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import store
from ..domains import DomainError
from . import front_matter, hashing

_LOCK = threading.RLock()
_ID_RE = re.compile(r"^a-(\d{4,})$")


def _empty() -> Dict[str, Any]:
    return {"version": 1, "next_seq": 1, "articles": []}


def _index_path(ctx) -> Path:
    return ctx.path("library", "index.json")


def load(ctx) -> Dict[str, Any]:
    with _LOCK:
        p = _index_path(ctx)
        if p.is_file():
            try:
                data = store.read_json(p, default=None)
            except ValueError as exc:
                raise DomainError(f"文章索引无法解析：{p}（{str(exc)[:80]}）。请手动修复，文章文件未受影响") from exc
            if isinstance(data, dict) and isinstance(data.get("articles"), list):
                data.setdefault("next_seq", 1)
                return data
        return _rebuild(ctx)


def save(ctx, data: Dict[str, Any]) -> None:
    with _LOCK:
        store.write_json_atomic(_index_path(ctx), data)


def _rebuild(ctx) -> Dict[str, Any]:
    """从文章文件重建目录（不写盘，由下一次 save 持久化）。"""
    data = _empty()
    for origin, d in (("imported", ctx.imported_dir), ("generated", ctx.generated_dir)):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            meta, body = front_matter.split(p.read_text(encoding="utf-8-sig"))
            aid = str(meta.get("id") or "")
            if not _ID_RE.match(aid):
                aid = allocate_id(data)
            else:
                data["next_seq"] = max(data["next_seq"], int(_ID_RE.match(aid).group(1)) + 1)
            data["articles"].append({
                "id": aid,
                "title": str(meta.get("title") or _first_heading(body) or p.stem),
                "origin": origin,
                "status": str(meta.get("status") or ("published" if origin == "imported" else "draft")),
                "content_hash": hashing.content_hash(body),
                "path": p.relative_to(ctx.root).as_posix(),
                "source_name": str(meta.get("source_name") or p.name),
                "chars": len(hashing.normalize(body)),
                "imported_at": str(meta.get("imported_at") or ""),
            })
    return data


def _first_heading(body: str) -> Optional[str]:
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or None
    return None


def allocate_id(data: Dict[str, Any]) -> str:
    seq = int(data.get("next_seq", 1))
    data["next_seq"] = seq + 1
    return f"a-{seq:04d}"


def list_articles(ctx) -> List[Dict[str, Any]]:
    return list(load(ctx)["articles"])


def get(ctx, article_id: str) -> Dict[str, Any]:
    for row in load(ctx)["articles"]:
        if row.get("id") == article_id:
            return row
    raise DomainError(f"文章不存在：{article_id}")


def read_body(ctx, row: Dict[str, Any]) -> str:
    """读取文章正文（去掉 front matter）。"""
    _, body = front_matter.split(ctx.path(row["path"]).read_text(encoding="utf-8"))
    return body
