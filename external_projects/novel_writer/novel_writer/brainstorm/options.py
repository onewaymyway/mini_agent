"""备选方案（B4）：同一模块的另一种走向，可对比、合并为主线、放弃。

  options/<oid>/option.json   {oid, name, module, notes, status, created_at, merged_at?}
  options/<oid>/content.json  备选的内容（module 的数据）
  options/<oid>/replaced.json 合并时被替换下来的主线（用于撤销）

status: open（待定）/ merged（已合并为主线）/ discarded（已放弃）
合并不删除任何备选；合并后主线被替换，原主线保存在 replaced.json。
"""

from __future__ import annotations

import datetime as _dt
import difflib
import json
import uuid
from typing import Any, Dict, List

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace
from novel_writer.fsutil import atomic_write_json, read_json

OPTION_MODULES = ("premise", "plot")
OPTIONS_DIR = "options"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _dir(ws: BrainstormWorkspace, oid: str):
    return ws.root / OPTIONS_DIR / oid


def _meta(ws: BrainstormWorkspace, oid: str) -> Dict[str, Any]:
    data = read_json(_dir(ws, oid) / "option.json", default=None)
    if not isinstance(data, dict):
        raise BrainstormError(f"找不到备选方案：{oid}")
    return data


def _save_meta(ws: BrainstormWorkspace, oid: str, meta: Dict[str, Any]) -> None:
    atomic_write_json(_dir(ws, oid) / "option.json", meta)


def create(ws: BrainstormWorkspace, name: str, module: str, content: Any = None, notes: str = "") -> Dict[str, Any]:
    """新建备选。content 为空时复制当前主线作为起点（便于在它上面修改）。"""
    name = (name or "").strip()
    if not name:
        raise BrainstormError("备选名称不能为空")
    if module not in OPTION_MODULES:
        raise BrainstormError(f"备选只支持这些模块：{', '.join(OPTION_MODULES)}")
    data = C.get_module(ws, module) if content is None else C.validate_module(module, content)
    oid = "o" + uuid.uuid4().hex[:8]
    meta = {"oid": oid, "name": name, "module": module, "notes": (notes or "").strip(),
            "status": "open", "created_at": _now()}
    d = _dir(ws, oid)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / "content.json", {"data": data})
    _save_meta(ws, oid, meta)
    return meta


def list_options(ws: BrainstormWorkspace, module: str = None) -> List[Dict[str, Any]]:
    base = ws.root / OPTIONS_DIR
    if not base.is_dir():
        return []
    out = []
    for d in sorted(base.iterdir()):
        meta = read_json(d / "option.json", default=None)
        if isinstance(meta, dict) and (module is None or meta.get("module") == module):
            out.append(meta)
    return sorted(out, key=lambda m: m["created_at"])


def content(ws: BrainstormWorkspace, oid: str) -> Any:
    data = read_json(_dir(ws, oid) / "content.json", default=None)
    if not isinstance(data, dict) or "data" not in data:
        raise BrainstormError(f"备选内容缺失：{oid}")
    return data["data"]


def _lines(data: Any) -> List[str]:
    return json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True).splitlines()


def diff(ws: BrainstormWorkspace, oid: str) -> str:
    """备选与当前主线的差异（统一格式文本）。模块相同才可比较。"""
    meta = _meta(ws, oid)
    main = C.get_module(ws, meta["module"])
    alt = content(ws, oid)
    text = "\n".join(difflib.unified_diff(_lines(main), _lines(alt), "主线", meta["name"], lineterm=""))
    return text or "（与主线相同）"


def merge(ws: BrainstormWorkspace, oid: str) -> Dict[str, Any]:
    """把备选合并为主线。被替换的主线保存到 replaced.json；备选保留，状态改为 merged。"""
    meta = _meta(ws, oid)
    if meta["status"] != "open":
        raise BrainstormError("只有待定的备选可以合并")
    module = meta["module"]
    replaced = C.get_module(ws, module)
    atomic_write_json(_dir(ws, oid) / "replaced.json", {"data": replaced, "at": _now()})
    C.put_module(ws, module, content(ws, oid))
    meta.update(status="merged", merged_at=_now())
    _save_meta(ws, oid, meta)
    return meta


def discard(ws: BrainstormWorkspace, oid: str) -> Dict[str, Any]:
    meta = _meta(ws, oid)
    if meta["status"] != "open":
        raise BrainstormError("只有待定的备选可以放弃")
    meta.update(status="discarded", discarded_at=_now())
    _save_meta(ws, oid, meta)
    return meta


def undo_merge(ws: BrainstormWorkspace, oid: str) -> Dict[str, Any]:
    """撤销一次合并：用 replaced.json 恢复主线，备选回到待定。"""
    meta = _meta(ws, oid)
    if meta["status"] != "merged":
        raise BrainstormError("只有已合并的备选可以撤销")
    saved = read_json(_dir(ws, oid) / "replaced.json", default=None)
    if not isinstance(saved, dict):
        raise BrainstormError("找不到被替换的主线")
    C.put_module(ws, meta["module"], saved["data"])
    meta.update(status="open")
    meta.pop("merged_at", None)
    _save_meta(ws, oid, meta)
    return meta
