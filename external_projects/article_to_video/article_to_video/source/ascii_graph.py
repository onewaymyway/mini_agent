"""source/ascii_graph.py — 代码块分类：把 ``A ↓ B ↓ C`` 形式的流程链解析为有序节点，并识别回路。

参考文章 35 个代码块几乎全是这种链；它们不能朗读，应转为图。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_ARROW_SPLIT = re.compile(r"\s*(?:↓|↑|→|⇒|->|=>)\s*")
_ART_CHARS = set("┌┐└┘│─├┤┬┴┼►◄▲▼")


def _norm(token: str) -> str:
    """回路判断用的归一化：去空白、去结尾省略号。"""
    return token.replace(" ", "").rstrip("….。.")


def classify_code(lines: List[str], lang: str = "") -> Dict[str, Any]:
    """返回 {shape, nodes, loop, node_count}。

    shape:
      - empty      空代码块
      - chain      含箭头的有序链（nodes 为节点文字，按出现顺序）
      - ascii_art  框线字符绘制的图（不可解析为链，需人工或生图处理）
      - text       其它纯文字代码块
    loop: 节点归一化后出现重复，视为回路（如 “超级AI … → 超级AI”）。
    """
    nonblank = [ln.strip() for ln in lines if ln.strip()]
    if not nonblank:
        return {"shape": "empty", "nodes": [], "loop": False, "node_count": 0}

    if any(ch in _ART_CHARS for ln in nonblank for ch in ln):
        return {"shape": "ascii_art", "nodes": [], "loop": False, "node_count": 0}

    if any(_ARROW_SPLIT.search(ln) for ln in nonblank):
        nodes: List[str] = []
        for ln in nonblank:
            nodes.extend(part.strip() for part in _ARROW_SPLIT.split(ln) if part.strip())
        # 省略号单独成节点（如 “……”）表示链未画完，不是节点，剔除后再判断
        nodes = [n for n in nodes if _norm(n)]
        if len(nodes) >= 2:
            normalized = [_norm(n) for n in nodes]
            loop = len(set(normalized)) < len(normalized)
            return {"shape": "chain", "nodes": nodes, "loop": loop, "node_count": len(nodes)}

    return {"shape": "text", "nodes": [], "loop": False, "node_count": 0}
