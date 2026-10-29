"""novel_writer/reading_state.py — 阅读与听书的持久化状态（M7）。

记录：上次阅读的章节、上次收听的位置（章节 + 章内段落）、听书语速与音色。
每本书的状态文件位于各自工作目录的 _studio/ 下，因此天然按书区分。
存于 `_studio/reading_state.json`，刷新浏览器或重启 Streamlit 后都能恢复。
文件损坏或缺失时返回默认值，不抛异常（状态只是便利信息，不影响正文）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from novel_writer.fsutil import atomic_write_json, read_json

STATE_NAME = "reading_state.json"
DEFAULTS: Dict[str, Any] = {
    "last_read_chapter": None,
    "last_listen_chapter": None,
    "listen_segment": None,   # 上次收听位置：该章内的段落下标（0 为标题）
    "listen_anchor": None,    # 该段落开头的一小段文字：章节被改写、段落下标漂移后，用它重新定位
    "listen_rate": 1.0,
    "listen_voice": "",
}


def _path(root: Path | str) -> Path:
    return Path(root) / "_studio" / STATE_NAME


def load(root: Path | str) -> Dict[str, Any]:
    data = read_json(_path(root), default=None)
    state = dict(DEFAULTS)
    if isinstance(data, dict):
        for key in DEFAULTS:
            if key in data and data[key] is not None:
                state[key] = data[key]
    return state


def update(root: Path | str, **patch: Any) -> Dict[str, Any]:
    """只更新给出的字段，其余保持不变；值相同则不写盘。"""
    state = load(root)
    changed = False
    for key, value in patch.items():
        if key not in DEFAULTS:
            raise KeyError(f"未知的阅读状态字段：{key}")
        if value is not None and state.get(key) != value:
            state[key] = value
            changed = True
    if changed:
        path = _path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, state)
    return state


def resume_segment(state: Dict[str, Any], start: int) -> Optional[int]:
    """从保存的收听位置续播：仅当保存的章节就是当前起始章节时，返回章内段落下标；否则 None（从该章开头）。"""
    seg = state.get("listen_segment")
    if state.get("last_listen_chapter") == start and isinstance(seg, int) and not isinstance(seg, bool) and seg >= 0:
        return seg
    return None


def resolve_listen_position(state: Dict[str, Any], available: list) -> Tuple[Optional[int], int, bool]:
    """听书的起始位置：(章节, 章内段落下标, 是否为回退)。**只依据已保存的位置**，与界面上选择框当前的值无关。

    - 保存的章节可听 → 原位续播；
    - 保存过位置但该章暂时不可听（正文正在被重写、文件瞬间缺失）→ 回退到不晚于它的最近一章的开头，
      回退标记为 True，调用方不得把这个回退位置写回保存的状态；
    - 从未保存过 → 第一个可听章节开头。
    永远不会因为「暂时读不到」而返回 None，更不会让播放器从全书第一段开始。"""
    if not available:
        return None, 0, False
    saved = chapter_or_none(state.get("last_listen_chapter"), available)
    seg = state.get("listen_segment")
    seg = seg if isinstance(seg, int) and not isinstance(seg, bool) and seg >= 0 else 0
    if saved is not None:
        return saved, seg, False
    if state.get("last_listen_chapter") is None:
        return available[0], 0, False
    return resolve_chapter(state.get("last_listen_chapter"), available), 0, True


def chapter_or_none(value: Optional[Any], available: list) -> Optional[int]:
    """保存的章节号若已不存在于章节列表中则返回 None，调用方回退到默认值。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n in available else None


def resolve_chapter(value: Optional[Any], available: list) -> Optional[int]:
    """把保存的章节解析为当前可听列表中的章节。
    章节存在则原样返回；不存在（如正文暂时被清空）则回退到不晚于它的最近一章；都没有才回退到第一章。
    避免因某一章暂时不可听就把进度整体重置到第一章。"""
    if not available:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return available[0]
    if n in available:
        return n
    earlier = [a for a in available if a < n]
    return max(earlier) if earlier else available[0]
