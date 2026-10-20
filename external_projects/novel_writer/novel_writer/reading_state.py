"""novel_writer/reading_state.py — 阅读与听书的持久化状态（M7）。

记录：上次阅读的章节、上次收听的章节、听书语速与音色。
存于 `_studio/reading_state.json`，刷新浏览器或重启 Streamlit 后都能恢复。
文件损坏或缺失时返回默认值，不抛异常（状态只是便利信息，不影响正文）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from novel_writer.fsutil import atomic_write_json, read_json

STATE_NAME = "reading_state.json"
DEFAULTS: Dict[str, Any] = {
    "last_read_chapter": None,
    "last_listen_chapter": None,
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


def chapter_or_none(value: Optional[Any], available: list) -> Optional[int]:
    """保存的章节号若已不存在于章节列表中则返回 None，调用方回退到默认值。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n in available else None
