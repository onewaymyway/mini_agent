"""world_forge/repo.py — 世界数据的唯一读写入口。所有路径与文件格式集中在这里。

目录布局（每个世界一个目录）：
  world.json                      元信息：标题、状态、时钟、当前分支、设置
  canon/entities.json             设定实体
  canon/relations.json            实体关系
  cast/characters.json            角色卡
  cast/portraits/<cid>.png        角色立绘（漫画参考图）
  timeline/main.jsonl             主线事件（只追加）
  timeline/branches.json          分支登记
  timeline/branches/<bid>.jsonl   其它分支的事件（复制 fork 点之前的历史后追加）
  proposals/queue.json            提案队列（所有写入的记录）
  stories/<sid>/                  故事产物（meta.json / arc.json / story.md / storyboard.json / panels/ / comic.html）
  exports/<sid>/                  给 novel_writer 的输入包
  locks/world.lock                写锁
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from . import store
from .paths import data_root

MAIN = "main"


class WorldNotFound(LookupError):
    pass


def world_dir(wid: str) -> Path:
    return data_root() / wid


def lock_file(wid: str) -> Path:
    return world_dir(wid) / "locks" / "world.lock"


def _p(wid: str, *parts: str) -> Path:
    return world_dir(wid).joinpath(*parts)


def list_world_ids() -> List[str]:
    root = data_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "world.json").is_file())


# ---------- 世界元信息

def load_meta(wid: str) -> Dict[str, Any]:
    from .calendar import normalize_meta

    meta = store.read_json(_p(wid, "world.json"))
    if meta is None:
        raise WorldNotFound(wid)
    return normalize_meta(meta)


def save_meta(meta: Dict[str, Any]) -> None:
    meta["updated_at"] = store.now_iso()
    store.write_json(_p(meta["id"], "world.json"), meta)


# ---------- 设定与角色

def load_entities(wid: str) -> List[dict]:
    return store.read_json(_p(wid, "canon", "entities.json"), []) or []


def save_entities(wid: str, items: List[dict]) -> None:
    store.write_json(_p(wid, "canon", "entities.json"), items)


def load_relations(wid: str) -> List[dict]:
    return store.read_json(_p(wid, "canon", "relations.json"), []) or []


def save_relations(wid: str, items: List[dict]) -> None:
    store.write_json(_p(wid, "canon", "relations.json"), items)


def load_characters(wid: str) -> List[dict]:
    return store.read_json(_p(wid, "cast", "characters.json"), []) or []


def save_characters(wid: str, items: List[dict]) -> None:
    store.write_json(_p(wid, "cast", "characters.json"), items)


def portrait_path(wid: str, cid: str) -> Path:
    return _p(wid, "cast", "portraits", f"{cid}.png")


def sheet_path(wid: str, cid: str, key: str) -> Path:
    return _p(wid, "cast", "sheets", f"{cid}_{key}.png")


def resolve_sheet(wid: str, cid: str, key: str) -> Optional[Path]:
    p = sheet_path(wid, cid, key)
    return p if p.is_file() and p.stat().st_size > 0 else None


def scene_path(wid: str, eid: str) -> Path:
    return _p(wid, "scenes", f"{eid}.png")


def resolve_scene(wid: str, eid: str) -> Optional[Path]:
    p = scene_path(wid, eid)
    return p if p.is_file() and p.stat().st_size > 0 else None


def resolve_portrait(wid: str, ch: dict) -> Optional[Path]:
    """角色的人设图实际位置：固定路径、或记录在角色属性中的路径；空文件视为不存在。没有则 None。"""
    candidates = [portrait_path(wid, ch["id"])]
    rel = (ch.get("attrs") or {}).get("portrait")
    if rel:
        candidates.append(world_dir(wid) / rel)
    for p in candidates:
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


# ---------- 时间线与分支

def load_branches(wid: str) -> Dict[str, dict]:
    data = store.read_json(_p(wid, "timeline", "branches.json"))
    if data is None:
        data = {MAIN: {"name": "主线", "parent": None, "fork_event_id": None,
                       "created_at": store.now_iso()}}
    return data


def save_branches(wid: str, data: Dict[str, dict]) -> None:
    store.write_json(_p(wid, "timeline", "branches.json"), data)


def branch_file(wid: str, bid: str) -> Path:
    if bid == MAIN:
        return _p(wid, "timeline", "main.jsonl")
    return _p(wid, "timeline", "branches", f"{bid}.jsonl")


def load_events(wid: str, bid: str) -> List[dict]:
    return store.read_jsonl(branch_file(wid, bid))


def append_event(wid: str, bid: str, event: dict) -> None:
    store.append_jsonl(branch_file(wid, bid), event)


def write_events(wid: str, bid: str, events: List[dict]) -> None:
    store.write_jsonl(branch_file(wid, bid), events)


# ---------- 提案队列

def load_proposals(wid: str) -> List[dict]:
    return store.read_json(_p(wid, "proposals", "queue.json"), []) or []


def save_proposals(wid: str, items: List[dict]) -> None:
    store.write_json(_p(wid, "proposals", "queue.json"), items)


# ---------- 故事

def story_dir(wid: str, sid: str) -> Path:
    return _p(wid, "stories", sid)


def list_story_ids(wid: str) -> List[str]:
    root = _p(wid, "stories")
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "meta.json").is_file())


def load_story_meta(wid: str, sid: str) -> Dict[str, Any]:
    meta = store.read_json(story_dir(wid, sid) / "meta.json")
    if meta is None:
        raise WorldNotFound(f"故事不存在：{sid}")
    return meta


def save_story_meta(wid: str, sid: str, meta: Dict[str, Any]) -> None:
    store.write_json(story_dir(wid, sid) / "meta.json", meta)


def find_by_id(items: List[dict], item_id: str) -> Optional[dict]:
    for it in items:
        if it.get("id") == item_id:
            return it
    return None
