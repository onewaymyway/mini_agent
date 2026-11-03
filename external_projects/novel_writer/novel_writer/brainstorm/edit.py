"""世界观与角色的直接编辑（B3）。作者手动修改，走 cards.put_module 的校验。

与提议（AI）不同：这里是作者本人的编辑，立即生效。
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace


def add_world_item(ws: BrainstormWorkspace, key: str, item: Any) -> Any:
    if key not in C.WORLD_LISTS:
        raise BrainstormError(f"未知的世界观分类：{key}")
    world = C.get_module(ws, "world")
    if isinstance(item, str):
        item = {"text": item} if key not in ("timeline", "taboos") else item
    world[key].append(item)
    C.put_module(ws, "world", world)
    return item


def remove_world_item(ws: BrainstormWorkspace, key: str, index: int) -> None:
    if key not in C.WORLD_LISTS:
        raise BrainstormError(f"未知的世界观分类：{key}")
    world = C.get_module(ws, "world")
    if not (0 <= index < len(world[key])):
        raise BrainstormError("条目不存在")
    del world[key][index]
    C.put_module(ws, "world", world)


def add_cast(ws: BrainstormWorkspace, card: Dict[str, Any], cid: Optional[str] = None) -> str:
    cast = C.get_module(ws, "cast")
    cid = cid or ("char_" + uuid.uuid4().hex[:6])
    if cid in cast:
        raise BrainstormError(f"角色 id 已存在：{cid}")
    cast[cid] = dict(card)
    C.put_module(ws, "cast", cast)
    return cid


def update_cast(ws: BrainstormWorkspace, cid: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    cast = C.get_module(ws, "cast")
    if cid not in cast:
        raise BrainstormError(f"找不到角色：{cid}")
    cast[cid].update(changes)
    C.put_module(ws, "cast", cast)
    return cast[cid]


def remove_cast(ws: BrainstormWorkspace, cid: str) -> None:
    cast = C.get_module(ws, "cast")
    if cid not in cast:
        raise BrainstormError(f"找不到角色：{cid}")
    del cast[cid]
    C.put_module(ws, "cast", cast)


# ── 剧情骨架（B4） ──

def _plot(ws: BrainstormWorkspace) -> Dict[str, Any]:
    return C.get_module(ws, "plot")


def add_act(ws: BrainstormWorkspace, title: str) -> int:
    title = (title or "").strip()
    if not title:
        raise BrainstormError("幕标题不能为空")
    plot = _plot(ws)
    plot["acts"].append({"title": title, "beats": []})
    C.put_module(ws, "plot", plot)
    return len(plot["acts"]) - 1


def remove_act(ws: BrainstormWorkspace, act: int) -> None:
    plot = _plot(ws)
    if not (0 <= act < len(plot["acts"])):
        raise BrainstormError("幕不存在")
    del plot["acts"][act]
    C.put_module(ws, "plot", plot)


def add_beat(ws: BrainstormWorkspace, act: int, text: str) -> None:
    text = (text or "").strip()
    if not text:
        raise BrainstormError("转折点不能为空")
    plot = _plot(ws)
    if not (0 <= act < len(plot["acts"])):
        raise BrainstormError("幕不存在")
    plot["acts"][act]["beats"].append(text)
    C.put_module(ws, "plot", plot)


def remove_beat(ws: BrainstormWorkspace, act: int, beat: int) -> None:
    plot = _plot(ws)
    if not (0 <= act < len(plot["acts"])) or not (0 <= beat < len(plot["acts"][act]["beats"])):
        raise BrainstormError("转折点不存在")
    del plot["acts"][act]["beats"][beat]
    C.put_module(ws, "plot", plot)


PLOT_LISTS = ("twists", "endings")


def add_plot_item(ws: BrainstormWorkspace, key: str, text: str) -> None:
    if key not in PLOT_LISTS:
        raise BrainstormError(f"未知的剧情分类：{key}")
    text = (text or "").strip()
    if not text:
        raise BrainstormError("内容不能为空")
    plot = _plot(ws)
    plot[key].append(text)
    C.put_module(ws, "plot", plot)


def remove_plot_item(ws: BrainstormWorkspace, key: str, index: int) -> None:
    if key not in PLOT_LISTS:
        raise BrainstormError(f"未知的剧情分类：{key}")
    plot = _plot(ws)
    if not (0 <= index < len(plot[key])):
        raise BrainstormError("条目不存在")
    del plot[key][index]
    C.put_module(ws, "plot", plot)
