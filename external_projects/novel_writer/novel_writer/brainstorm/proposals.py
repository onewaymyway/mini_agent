"""AI 提议（B2）：对卡片的修改建议。提议只是数据，只有作者采纳后才写入卡片。

提议结构：{pid, module, op, target, value, reason, status, created_at, error}
  status: pending（待确认）/ accepted（已采纳）/ rejected（已拒绝）

允许的操作（module → ops）：
  brief           set    value: 部分题材字段
  premise         set    value: 文字
  ideas           add    value: {text, tags?}      set  target: 灵感 id, value: {text?, tags?, status?}
  open_questions  add    value: {text}             set  target: 问题 id, value: {text?, closed?}
  world           add    target: 列表名, value: 条目
  cast            add    value: 角色卡(需 name)     set  target: 角色 id, value: 部分字段
  plot            set    value: {acts?, twists?, endings?}（替换给出的键）
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any, Dict, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, read_json

PROPOSALS_FILE = "_studio/proposals.json"
ALLOWED_OPS = {
    "brief": {"set"}, "premise": {"set"}, "ideas": {"add", "set"}, "open_questions": {"add", "set"},
    "world": {"add"}, "cast": {"add", "set"}, "plot": {"set"},
}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _load(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    data = read_json(ws.root / PROPOSALS_FILE, default=[])
    return data if isinstance(data, list) else []


def _save(ws: BrainstormWorkspace, items: List[Dict[str, Any]]) -> None:
    atomic_write_json(ws.root / PROPOSALS_FILE, items)


def create(ws: BrainstormWorkspace, module: str, op: str, value: Any, *, target: Optional[str] = None,
           reason: str = "") -> Dict[str, Any]:
    """登记一条待确认的提议。这里只做结构检查，真正的校验在采纳时进行。"""
    if module not in ALLOWED_OPS:
        raise BrainstormError(f"提议的模块不合法：{module}")
    if op not in ALLOWED_OPS[module]:
        raise BrainstormError(f"模块 {module} 不支持操作 {op}")
    if op == "set" and module in ("ideas", "open_questions", "cast") and not target:
        raise BrainstormError(f"{module} 的修改需要指明目标 id")
    if module == "world" and target not in C.WORLD_LISTS:
        raise BrainstormError("世界观的提议需要指明列表名（rules/locations/factions/timeline/taboos）")
    items = _load(ws)
    item = {"pid": "p" + uuid.uuid4().hex[:8], "module": module, "op": op, "target": target,
            "value": value, "reason": (reason or "").strip(), "status": "pending", "created_at": _now(), "error": ""}
    items.append(item)
    _save(ws, items)
    return item


def list_items(ws: BrainstormWorkspace, status: Optional[str] = None) -> List[Dict[str, Any]]:
    items = _load(ws)
    return [i for i in items if status is None or i["status"] == status]


def _find(ws: BrainstormWorkspace, pid: str) -> tuple:
    items = _load(ws)
    for item in items:
        if item["pid"] == pid:
            return items, item
    raise BrainstormError(f"找不到提议：{pid}")


def apply_to(module_data: Any, module: str, op: str, target: Optional[str], value: Any) -> Any:
    """在模块数据的副本上执行一条提议，返回新数据（不落盘）。结果会再经过 cards.validate_module。"""
    if module == "premise":
        if not isinstance(value, str):
            raise BrainstormError("核心命题的提议需要文字")
        return value
    if module == "brief":
        if not isinstance(value, dict):
            raise BrainstormError("题材提议需要字段对象")
        merged = dict(module_data)
        merged.update(value)
        return merged
    if module == "ideas":
        items = [dict(i) for i in module_data]
        if op == "add":
            if not isinstance(value, dict) or not str(value.get("text", "")).strip():
                raise BrainstormError("灵感提议需要 text")
            items.append({"id": "i" + uuid.uuid4().hex[:8], "text": value["text"], "tags": value.get("tags", []),
                          "source": "ai", "status": value.get("status", "keep")})
            return items
        for item in items:
            if item["id"] == target:
                item.update({k: v for k, v in value.items() if k in ("text", "tags", "status")})
                return items
        raise BrainstormError(f"找不到灵感：{target}")
    if module == "open_questions":
        items = [dict(q) for q in module_data]
        if op == "add":
            if not isinstance(value, dict) or not str(value.get("text", "")).strip():
                raise BrainstormError("开放问题提议需要 text")
            items.append({"id": "q" + uuid.uuid4().hex[:8], "text": value["text"], "closed": bool(value.get("closed", False))})
            return items
        for q in items:
            if q["id"] == target:
                q.update({k: v for k, v in value.items() if k in ("text", "closed")})
                return items
        raise BrainstormError(f"找不到开放问题：{target}")
    if module == "world":
        if not isinstance(value, (dict, str)):
            raise BrainstormError("世界观条目需要对象或文字")
        world = {k: list(v) for k, v in module_data.items()}
        item = {"text": value} if isinstance(value, str) else dict(value)
        if target in ("timeline", "taboos") and isinstance(value, dict):
            item = value.get("text", "")
        world[target].append(item)
        return world
    if module == "cast":
        cast = {k: dict(v) for k, v in module_data.items()}
        if op == "add":
            if not isinstance(value, dict) or not str(value.get("name", "")).strip():
                raise BrainstormError("角色提议需要 name")
            cid = target or ("char_" + uuid.uuid4().hex[:6])
            if cid in cast:
                raise BrainstormError(f"角色 id 已存在：{cid}")
            cast[cid] = dict(value)
            return cast
        if target not in cast:
            raise BrainstormError(f"找不到角色：{target}")
        cast[target].update(value)
        return cast
    if module == "plot":
        if not isinstance(value, dict):
            raise BrainstormError("剧情提议需要对象")
        plot = dict(module_data)
        for key in ("acts", "twists", "endings"):
            if key in value:
                plot[key] = value[key]
        return plot
    raise BrainstormError(f"未知模块：{module}")


def accept(ws: BrainstormWorkspace, pid: str, edited_value: Any = None) -> Dict[str, Any]:
    """采纳提议：应用到卡片并落盘。任何校验失败都保持 pending，不写入卡片。"""
    items, item = _find(ws, pid)
    if item["status"] != "pending":
        raise BrainstormError(f"该提议已{ {'accepted': '采纳', 'rejected': '拒绝'}[item['status']] }")
    value = item["value"] if edited_value is None else edited_value
    try:
        current = C.get_module(ws, item["module"])
        new = apply_to(current, item["module"], item["op"], item["target"], value)
        C.put_module(ws, item["module"], new)
    except BrainstormError as exc:
        item["error"] = str(exc)
        _save(ws, items)
        raise
    item.update(status="accepted", error="", value=value, decided_at=_now())
    _save(ws, items)
    return item


def reject(ws: BrainstormWorkspace, pid: str) -> Dict[str, Any]:
    items, item = _find(ws, pid)
    if item["status"] != "pending":
        raise BrainstormError("该提议已处理")
    item.update(status="rejected", decided_at=_now())
    _save(ws, items)
    return item
