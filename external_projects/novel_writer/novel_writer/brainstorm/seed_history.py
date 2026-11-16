"""一句话起稿的输入记录：每次起稿 / 修订的用户输入（完整内容）与结果，便于作者回看。

存放：_studio/seed_draft/inputs.jsonl，只追加。每次提交写一行 submitted，结束时再写一行 outcome（同一 id）。
状态：submitted（已提交，运行中）/ done（成功，附版本号）/ failed（运行失败，附错误）/ rejected（输入未通过校验，未运行）。
记录的是用户原文，不截断；不影响起稿与修订本身，写入失败也不会中断操作。
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Dict, List

from novel_writer.brainstorm.workspace import BrainstormWorkspace, now_iso

FILE = "inputs.jsonl"
_LOCK = threading.Lock()


def _path(ws: BrainstormWorkspace):
    from novel_writer.brainstorm import seed_draft as SD
    return SD.draft_dir(ws) / FILE


def _append(ws: BrainstormWorkspace, row: Dict[str, Any]) -> None:
    path = _path(ws)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def submitted(ws: BrainstormWorkspace, kind: str, payload: Dict[str, Any]) -> str:
    """记录一次用户提交（kind: seed 起稿 / revise 修订）。返回记录编号。"""
    rid = uuid.uuid4().hex[:10]
    try:
        _append(ws, {"id": rid, "event": "submitted", "kind": kind, "at": now_iso(), "input": payload})
    except OSError:
        pass
    return rid


def outcome(ws: BrainstormWorkspace, rid: str, status: str, **fields: Any) -> None:
    try:
        _append(ws, {"id": rid, "event": "outcome", "status": status, "at": now_iso(), **fields})
    except OSError:
        pass


def history(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    """合并成每次提交一条，按时间从新到旧。"""
    path = _path(ws)
    if not path.is_file():
        return []
    items: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = row.get("id")
        if row.get("event") == "submitted":
            items[rid] = {"id": rid, "kind": row.get("kind"), "submitted_at": row.get("at"),
                          "input": row.get("input") or {}, "status": "submitted"}
            order.append(rid)
        elif rid in items:
            cur = items[rid]
            cur["status"] = row.get("status", cur["status"])
            cur["finished_at"] = row.get("at")
            for k in ("ver", "error"):
                if k in row:
                    cur[k] = row[k]
    return [items[r] for r in reversed(order)]
