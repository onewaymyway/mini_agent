"""source/author_meta.py — 文章元数据：front matter 与“作者：”行。

署名优先级：界面/命令行输入 > front matter（author: / 作者:）> 文首“作者：”行 > 留空。
不让模型猜作者：参考文章没有作者名，猜错会造成错误署名。
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_FM_LINE = re.compile(r"^\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$")
_BODY_AUTHOR = re.compile(
    r"^\s*(?:>\s*)?(?:\*\*)?\s*(?:作者|author)\s*(?:\*\*)?\s*[:：]\s*(?:\*\*)?\s*(.+?)\s*(?:\*\*)?\s*$",
    re.IGNORECASE,
)


def parse_front_matter(lines: List[str]) -> Tuple[Dict[str, str], int]:
    """解析开头的 ``---`` 包裹的 front matter。返回 (键值, 正文起始行下标)；无则 ({}, 0)。"""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for j in range(1, min(len(lines), 80)):
        if lines[j].strip() in ("---", "..."):
            meta: Dict[str, str] = {}
            for ln in lines[1:j]:
                m = _FM_LINE.match(ln)
                if m:
                    meta[m.group(1).strip().lower()] = m.group(2).strip().strip("\"'")
            if not meta:
                return {}, 0
            return meta, j + 1
    return {}, 0


def resolve_author(
    explicit: Optional[str],
    front_matter: Dict[str, str],
    body_head_lines: List[str],
) -> Tuple[str, str]:
    """返回 (作者名, 来源)；来源 ∈ input / front_matter / body_line / none。"""
    name = (explicit or "").strip()
    if name:
        return name, "input"
    for key in ("author", "作者"):
        value = (front_matter or {}).get(key, "").strip()
        if value:
            return value, "front_matter"
    for ln in body_head_lines or []:
        m = _BODY_AUTHOR.match(ln)
        if m and m.group(1).strip():
            return m.group(1).strip(), "body_line"
    return "", "none"
