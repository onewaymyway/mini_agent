"""构思卡片：读写、校验、定版快照与回退（B0）。

卡片是构思包的内容本体。写入只经过 put_module（校验 + 原子写入）；AI 的提议在 B2 才引入，
且必须经过作者确认后才会调用 put_module。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Dict, List

from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace, now_iso
from novel_writer.fsutil import atomic_write_json, atomic_write_text, read_json
from novel_writer.intake import INTAKE_KEYS

# 模块名 → 文件名
MODULE_FILES: Dict[str, str] = {
    "ideas": "ideas.json",
    "brief": "brief.json",
    "premise": "premise.md",
    "world": "world.json",
    "cast": "cast.json",
    "plot": "plot.json",
    "open_questions": "open_questions.json",
}
IDEA_SOURCES = ("author", "ai")
IDEA_STATUSES = ("keep", "park", "drop", "used")
WORLD_LISTS = ("rules", "locations", "factions", "timeline", "taboos")


def default_module(name: str) -> Any:
    if name == "premise":
        return ""
    if name == "brief":
        return {k: "" for k in INTAKE_KEYS}
    if name == "world":
        return {k: [] for k in WORLD_LISTS}
    if name == "cast":
        return {}
    if name == "plot":
        return {"acts": [], "twists": [], "endings": []}
    return []                                   # ideas / open_questions


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise BrainstormError(msg)


def _nonempty_str(v: Any, where: str) -> None:
    _check(isinstance(v, str) and v.strip() != "", f"{where} 必须是非空文字")


def _str_list(v: Any, where: str) -> None:
    _check(isinstance(v, list) and all(isinstance(x, str) for x in v), f"{where} 必须是文字列表")


def validate_module(name: str, data: Any) -> Any:
    """校验并返回规范化后的数据。不合法则抛 BrainstormError。"""
    if name not in MODULE_FILES:
        raise BrainstormError(f"未知模块：{name}")
    if name == "premise":
        _check(isinstance(data, str), "核心命题必须是文字")
        return data.strip()
    if name == "brief":
        _check(isinstance(data, dict), "题材卡必须是对象")
        unknown = set(data) - set(INTAKE_KEYS)
        _check(not unknown, f"题材卡含未知字段：{', '.join(sorted(unknown))}")
        out = {k: "" for k in INTAKE_KEYS}
        for k, v in data.items():
            _check(isinstance(v, str), f"题材字段 {k} 必须是文字")
            out[k] = v.strip()
        return out
    if name == "ideas":
        _check(isinstance(data, list), "灵感池必须是列表")
        seen = set()
        out: List[Dict[str, Any]] = []
        for i, item in enumerate(data):
            _check(isinstance(item, dict), f"灵感 #{i + 1} 必须是对象")
            iid = item.get("id")
            _nonempty_str(iid, f"灵感 #{i + 1} 的 id")
            _check(iid not in seen, f"灵感 id 重复：{iid}")
            seen.add(iid)
            _nonempty_str(item.get("text"), f"灵感 {iid} 的内容")
            tags = item.get("tags", [])
            _str_list(tags, f"灵感 {iid} 的标签")
            source = item.get("source", "author")
            _check(source in IDEA_SOURCES, f"灵感 {iid} 的来源不合法：{source}")
            status = item.get("status", "keep")
            _check(status in IDEA_STATUSES, f"灵感 {iid} 的状态不合法：{status}")
            out.append({"id": iid, "text": item["text"].strip(), "tags": tags, "source": source, "status": status})
        return out
    if name == "world":
        _check(isinstance(data, dict), "世界观必须是对象")
        unknown = set(data) - set(WORLD_LISTS)
        _check(not unknown, f"世界观含未知字段：{', '.join(sorted(unknown))}")
        out = {k: [] for k in WORLD_LISTS}
        for key in WORLD_LISTS:
            items = data.get(key, [])
            _check(isinstance(items, list), f"世界观 {key} 必须是列表")
            for j, item in enumerate(items):
                if key == "timeline" or key == "taboos":
                    _nonempty_str(item if isinstance(item, str) else item.get("text") if isinstance(item, dict) else None,
                                  f"世界观 {key} 第 {j + 1} 条")
                    out[key].append(item)
                    continue
                _check(isinstance(item, dict), f"世界观 {key} 第 {j + 1} 条必须是对象")
                _nonempty_str(item.get("text"), f"世界观 {key} 第 {j + 1} 条的内容")
                out[key].append(item)
        return out
    if name == "cast":
        _check(isinstance(data, dict), "角色卡必须是对象（id → 角色）")
        out = {}
        for cid, card in data.items():
            _nonempty_str(cid, "角色 id")
            _check(isinstance(card, dict), f"角色 {cid} 必须是对象")
            _nonempty_str(card.get("name"), f"角色 {cid} 的名字")
            for field in ("role", "want", "need", "flaw", "lie", "arc"):
                _check(isinstance(card.get(field, ""), str), f"角色 {cid} 的 {field} 必须是文字")
            rel = card.get("relations", [])
            _str_list(rel, f"角色 {cid} 的关系")
            out[cid] = dict(card, relations=rel)
        return out
    if name == "plot":
        _check(isinstance(data, dict), "剧情骨架必须是对象")
        acts = data.get("acts", [])
        _check(isinstance(acts, list), "剧情的幕必须是列表")
        for a in acts:
            _check(isinstance(a, dict), "每一幕必须是对象")
            _nonempty_str(a.get("title"), "幕标题")
            _str_list(a.get("beats", []), "幕内转折点")
        _str_list(data.get("twists", []), "反转")
        _str_list(data.get("endings", []), "结局候选")
        return {"acts": acts, "twists": data.get("twists", []), "endings": data.get("endings", [])}
    if name == "open_questions":
        _check(isinstance(data, list), "开放问题必须是列表")
        out = []
        for i, q in enumerate(data):
            _check(isinstance(q, dict), f"开放问题 #{i + 1} 必须是对象")
            _nonempty_str(q.get("id"), "开放问题 id")
            _nonempty_str(q.get("text"), f"开放问题 {q['id']} 的内容")
            out.append({"id": q["id"], "text": q["text"].strip(), "closed": bool(q.get("closed", False))})
        return out
    raise BrainstormError(f"未知模块：{name}")


# ── 读写 ──────────────────────────────────────────────────────────

def get_module(ws: BrainstormWorkspace, name: str) -> Any:
    if name not in MODULE_FILES:
        raise BrainstormError(f"未知模块：{name}")
    path = ws.working / MODULE_FILES[name]
    if name == "premise":
        return path.read_text(encoding="utf-8").strip() if path.is_file() else default_module(name)
    data = read_json(path, default=None)
    return default_module(name) if data is None else data


def put_module(ws: BrainstormWorkspace, name: str, data: Any) -> Any:
    clean = validate_module(name, data)
    path = ws.working / MODULE_FILES[name]
    path.parent.mkdir(parents=True, exist_ok=True)
    if name == "premise":
        atomic_write_text(path, clean + "\n")
    else:
        atomic_write_json(path, clean)
    ws.touch()
    return clean


def all_modules(ws: BrainstormWorkspace) -> Dict[str, Any]:
    return {name: get_module(ws, name) for name in MODULE_FILES}


# ── 定版快照与回退 ─────────────────────────────────────────────────

def content_hash(ws: BrainstormWorkspace) -> str:
    h = hashlib.sha256()
    for name in sorted(MODULE_FILES):
        path = ws.working / MODULE_FILES[name]
        h.update(name.encode("utf-8"))
        h.update(path.read_bytes() if path.is_file() else b"<missing>")
    return h.hexdigest()


def snapshot(ws: BrainstormWorkspace, note: str = "") -> int:
    """把当前草稿定版为新版本 v<n>（不可变）。返回版本号。"""
    meta = ws.meta()
    n = int(meta.get("current_version", 0)) + 1
    target = ws.version_dir(n)
    if target.exists():
        raise BrainstormError(f"版本目录已存在：v{n}")
    target.mkdir(parents=True)
    for fname in MODULE_FILES.values():
        src = ws.working / fname
        if src.is_file():
            (target / fname).write_bytes(src.read_bytes())
    info = {"n": n, "created_at": now_iso(), "note": (note or "").strip(), "hash": content_hash(ws)}
    atomic_write_json(target / "version.json", info)
    meta["current_version"] = n
    ws.save_meta(meta)
    ws.touch()
    return n


def list_versions(ws: BrainstormWorkspace) -> List[Dict[str, Any]]:
    out = []
    if not ws.versions.is_dir():
        return out
    for d in sorted(ws.versions.iterdir(), key=lambda p: p.name):
        info = read_json(d / "version.json", default=None)
        if isinstance(info, dict):
            out.append(info)
    out.sort(key=lambda r: r["n"])
    return out


def restore(ws: BrainstormWorkspace, n: int) -> int:
    """把草稿恢复为版本 n，并记为一个新版本（历史不删除）。返回新版本号。"""
    src = ws.version_dir(n)
    if not (src / "version.json").is_file():
        raise BrainstormError(f"版本不存在：v{n}")
    for fname in MODULE_FILES.values():
        dst = ws.working / fname
        srcf = src / fname
        if srcf.is_file():
            dst.write_bytes(srcf.read_bytes())
        elif dst.is_file():
            dst.unlink()                        # 该版本里不存在的模块文件，恢复后也不存在
    return snapshot(ws, note=f"回退到 v{n}")
