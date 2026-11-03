"""灵感池（B1）：作者与 AI 的灵感条目。状态：keep 保留 / park 搁置 / drop 丢弃 / used 已采用。

丢弃用状态表示，不删除条目，便于回看为什么放弃。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace


def _new_id() -> str:
    return "i" + uuid.uuid4().hex[:8]


def _clean_tags(tags: Iterable[str]) -> List[str]:
    out: List[str] = []
    for t in tags or []:
        t = str(t).strip()
        if t and t not in out:
            out.append(t)
    return out


def list_ideas(ws: BrainstormWorkspace, status: Optional[str] = None) -> List[Dict[str, Any]]:
    items = C.get_module(ws, "ideas")
    if status:
        items = [i for i in items if i.get("status") == status]
    return items


def add_idea(ws: BrainstormWorkspace, text: str, *, tags: Iterable[str] = (), source: str = "author",
             status: str = "keep") -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise BrainstormError("灵感内容不能为空")
    items = C.get_module(ws, "ideas")
    item = {"id": _new_id(), "text": text, "tags": _clean_tags(tags), "source": source, "status": status}
    C.put_module(ws, "ideas", items + [item])
    return item


def update_idea(ws: BrainstormWorkspace, idea_id: str, *, text: Optional[str] = None,
                tags: Optional[Iterable[str]] = None, status: Optional[str] = None) -> Dict[str, Any]:
    items = C.get_module(ws, "ideas")
    for item in items:
        if item["id"] == idea_id:
            if text is not None:
                if not text.strip():
                    raise BrainstormError("灵感内容不能为空")
                item["text"] = text.strip()
            if tags is not None:
                item["tags"] = _clean_tags(tags)
            if status is not None:
                item["status"] = status
            C.put_module(ws, "ideas", items)
            return item
    raise BrainstormError(f"找不到灵感：{idea_id}")
