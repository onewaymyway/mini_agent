"""AI 提议（B2 / B6-3）：对卡片的修改建议。提议只是数据，只有作者采纳后才写入卡片。

提议结构：{pid, module, op, target, value, reason, status, created_at, error, ...}
  status: pending（待确认）/ accepted（已采纳）/ rejected（已拒绝）

允许的操作（module → ops，及 target 的写法）：
  brief           set    value: 部分题材字段
  premise         set    value: 文字
  ideas           add    value: {text, tags?}
                  set    target: 灵感 id,   value: {text?, tags?, status?}
                  delete target: 灵感 id
  open_questions  add    value: {text}
                  set    target: 问题 id,   value: {text?, closed?}
                  delete target: 问题 id
  world           add    target: 列表名 rules|locations|factions|timeline|taboos, value: 条目
                  set    target: 列表名:序号, value: 条目（文字或对象）
                  delete target: 列表名:序号
  cast            add    value: 角色卡(需 name)，target 可选指定 id
                  set    target: 角色 id,   value: 部分字段
                  delete target: 角色 id
  plot            set    无 target: value 为 {acts?, twists?, endings?}（替换给出的键，B2 旧行为）
                  set    target: act:i 的字段 {title?, beats?}；act:i:beat:j 文字；twists:k / endings:k 文字
                  add    target: act:i 追加转折点；twists / endings 追加文字
                  delete target: act:i；act:i:beat:j；twists:k；endings:k
  options         add    value: {name, module: premise|plot, content?, notes?}（只新建备选）
                  delete target: 备选 id（放弃该备选，不合并）
序号从 0 开始。
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
    "brief": {"set"}, "premise": {"set"},
    "ideas": {"add", "set", "delete"}, "open_questions": {"add", "set", "delete"},
    "world": {"add", "set", "delete"}, "cast": {"add", "set", "delete"},
    "plot": {"set", "add", "delete"}, "options": {"add", "delete"},
}
_INDEXED_OPS = {"set", "delete"}       # 这些操作需要明确的目标
_NEEDS_TARGET = {
    "ideas": _INDEXED_OPS, "open_questions": _INDEXED_OPS, "cast": _INDEXED_OPS, "options": {"delete"},
}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _load(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    data = read_json(ws.root / PROPOSALS_FILE, default=[])
    return data if isinstance(data, list) else []


def _save(ws: BrainstormWorkspace, items: List[Dict[str, Any]]) -> None:
    atomic_write_json(ws.root / PROPOSALS_FILE, items)


def _parse_index(target: str, where: str) -> int:
    try:
        n = int(str(target).rsplit(":", 1)[-1])
    except ValueError as exc:
        raise BrainstormError(f"{where}的目标序号不合法：{target}") from exc
    if n < 0:
        raise BrainstormError(f"{where}的目标序号不能为负：{target}")
    return n


def _check_target(module: str, op: str, target: Optional[str]) -> None:
    if module == "world":
        base = (target or "").split(":")[0]
        if base not in C.WORLD_LISTS:
            raise BrainstormError("世界观的提议需要指明列表名（rules/locations/factions/timeline/taboos）")
        if op in _INDEXED_OPS and ":" not in (target or ""):
            raise BrainstormError("世界观条目的修改或删除需要 列表名:序号 形式的目标")
        if op in _INDEXED_OPS:
            _parse_index(target, "世界观")
        return
    if module == "plot":
        if op == "set" and not target:
            return                                          # 整体替换（B2 行为）
        if not target:
            raise BrainstormError("剧情的追加或删除需要指明目标（act:i、act:i:beat:j、twists:k、endings:k）")
        head = target.split(":")[0]
        if head not in ("act", "twists", "endings"):
            raise BrainstormError(f"剧情的目标不合法：{target}")
        return
    if op in _NEEDS_TARGET.get(module, set()) and not target:
        raise BrainstormError(f"{module} 的{op}操作需要指明目标 id")


def create(ws: BrainstormWorkspace, module: str, op: str, value: Any, *, target: Optional[str] = None,
           reason: str = "", batch: Optional[str] = None, source_turns: Optional[List[int]] = None,
           conflict: str = "") -> Dict[str, Any]:
    """登记一条待确认的提议。这里只做结构检查，真正的校验在采纳时进行。"""
    if module not in ALLOWED_OPS:
        raise BrainstormError(f"提议的模块不合法：{module}")
    if op not in ALLOWED_OPS[module]:
        raise BrainstormError(f"模块 {module} 不支持操作 {op}")
    if op != "delete" and value is None:
        raise BrainstormError("该提议需要 value")
    if module == "options" and op == "add":
        if not isinstance(value, dict) or not str(value.get("name", "")).strip():
            raise BrainstormError("备选提议需要 name")
        if value.get("module") not in ("premise", "plot"):
            raise BrainstormError("备选提议的 module 只能是 premise 或 plot")
    _check_target(module, op, target)
    items = _load(ws)
    item = {"pid": "p" + uuid.uuid4().hex[:8], "module": module, "op": op, "target": target,
            "value": value, "reason": (reason or "").strip(), "status": "pending", "created_at": _now(), "error": "",
            "batch": batch, "source_turns": list(source_turns or []), "conflict": conflict}
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


# ── 世界观条目 ──

def _world_item(list_name: str, value: Any) -> Any:
    if isinstance(value, str):
        return value if list_name in ("timeline", "taboos") else {"text": value}
    if isinstance(value, dict):
        if list_name in ("timeline", "taboos"):
            return str(value.get("text", ""))
        return dict(value)
    raise BrainstormError("世界观条目需要对象或文字")


def _apply_world(module_data: Dict[str, Any], op: str, target: str, value: Any) -> Dict[str, Any]:
    world = {k: list(v) for k, v in module_data.items()}
    list_name = target.split(":")[0]
    if op == "add":
        if ":" in target:
            raise BrainstormError("新增世界观条目时目标只写列表名")
        world[list_name].append(_world_item(list_name, value))
        return world
    idx = _parse_index(target, "世界观")
    if idx >= len(world[list_name]):
        raise BrainstormError(f"世界观 {list_name} 没有第 {idx} 条")
    if op == "set":
        world[list_name][idx] = _world_item(list_name, value)
    else:
        del world[list_name][idx]
    return world


# ── 剧情骨架 ──

def _apply_plot(module_data: Dict[str, Any], op: str, target: Optional[str], value: Any) -> Dict[str, Any]:
    plot = {"acts": [dict(a, beats=list(a.get("beats", []))) for a in module_data["acts"]],
            "twists": list(module_data["twists"]), "endings": list(module_data["endings"])}
    if op == "set" and not target:
        if not isinstance(value, dict):
            raise BrainstormError("剧情提议需要对象")
        for key in ("acts", "twists", "endings"):
            if key in value:
                plot[key] = value[key]
        return plot
    parts = target.split(":")
    head = parts[0]
    if head in ("twists", "endings"):
        if op == "add":
            plot[head].append(_text(value))
            return plot
        idx = _parse_index(target, "剧情")
        if idx >= len(plot[head]):
            raise BrainstormError(f"剧情 {head} 没有第 {idx} 条")
        if op == "set":
            plot[head][idx] = _text(value)
        else:
            del plot[head][idx]
        return plot
    # act:i 或 act:i:beat:j
    ai = _act_index(parts[1], plot)
    if len(parts) == 2:
        if op == "set":
            if not isinstance(value, dict):
                raise BrainstormError("修改幕需要对象（title 或 beats）")
            act = plot["acts"][ai]
            if "title" in value:
                act["title"] = _text(value["title"])
            if "beats" in value:
                act["beats"] = [_text(b) for b in value["beats"]]
        elif op == "add":
            plot["acts"][ai]["beats"].append(_text(value))
        else:
            del plot["acts"][ai]
        return plot
    if len(parts) == 4 and parts[2] == "beat":
        beats = plot["acts"][ai]["beats"]
        bi = _parse_index(target, "剧情")
        if bi >= len(beats):
            raise BrainstormError(f"第 {ai} 幕没有第 {bi} 个转折点")
        if op == "set":
            beats[bi] = _text(value)
        else:
            del beats[bi]
        return plot
    raise BrainstormError(f"剧情的目标不合法：{target}")


def _act_index(text: str, plot: Dict[str, Any]) -> int:
    try:
        ai = int(text)
    except ValueError as exc:
        raise BrainstormError(f"幕序号不合法：{text}") from exc
    if not 0 <= ai < len(plot["acts"]):
        raise BrainstormError(f"没有第 {ai} 幕")
    return ai


def _text(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict) and str(value.get("text", "")).strip():
        return str(value["text"]).strip()
    raise BrainstormError("这里需要文字")


# ── 应用到卡片副本 ──

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
    if module in ("ideas", "open_questions"):
        items = [dict(i) for i in module_data]
        if op == "add":
            if not isinstance(value, dict) or not str(value.get("text", "")).strip():
                raise BrainstormError("提议需要 text")
            if module == "ideas":
                items.append({"id": "i" + uuid.uuid4().hex[:8], "text": value["text"], "tags": value.get("tags", []),
                              "source": "ai", "status": value.get("status", "keep")})
            else:
                items.append({"id": "q" + uuid.uuid4().hex[:8], "text": value["text"],
                              "closed": bool(value.get("closed", False))})
            return items
        keys = ("text", "tags", "status") if module == "ideas" else ("text", "closed")
        for i, item in enumerate(items):
            if item["id"] == target:
                if op == "delete":
                    del items[i]
                else:
                    item.update({k: v for k, v in value.items() if k in keys})
                return items
        raise BrainstormError(f"找不到{'灵感' if module == 'ideas' else '开放问题'}：{target}")
    if module == "world":
        return _apply_world(module_data, op, target or "", value)
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
        if op == "delete":
            del cast[target]
            return cast
        cast[target].update(value)
        return cast
    if module == "plot":
        return _apply_plot(module_data, op, target, value)
    raise BrainstormError(f"未知模块：{module}")


def accept(ws: BrainstormWorkspace, pid: str, edited_value: Any = None) -> Dict[str, Any]:
    """采纳提议：应用到卡片并落盘。任何校验失败都保持 pending，不写入卡片。"""
    items, item = _find(ws, pid)
    if item["status"] != "pending":
        raise BrainstormError(f"该提议已{ {'accepted': '采纳', 'rejected': '拒绝'}[item['status']] }")
    value = item["value"] if edited_value is None else edited_value
    if item["module"] == "options":
        from novel_writer.brainstorm import options as O
        try:
            if item["op"] == "delete":
                O.discard(ws, item["target"])
            else:
                O.create(ws, value["name"], value["module"], value.get("content"), value.get("notes", ""))
        except (BrainstormError, KeyError, TypeError) as exc:
            item["error"] = str(exc)
            _save(ws, items)
            raise BrainstormError(str(exc)) from exc
        item.update(status="accepted", error="", value=value, decided_at=_now())
        _save(ws, items)
        return item
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
