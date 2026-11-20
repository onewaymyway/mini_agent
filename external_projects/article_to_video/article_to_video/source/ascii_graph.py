"""source/ascii_graph.py — 代码块分类：把 ``A ↓ B ↓ C`` 形式的流程链解析为有序节点，识别回路与堆叠方向。

第一期（分镜改进·测试文章样本）起：
  * ``↑`` 只出现时表示“堆叠”（direction=up）：节点按书写顺序自上而下，最后一项为底层；
    分镜据此使用堆叠层图，不得画成流程箭头（否则方向反转）。
  * 其余箭头（``↓ → ⇒ -> =>``）为流程（direction=forward）。
  * 回路：节点归一化后重复；或节点含“再次/回到/循环/反复”；或末尾以省略号（……）表示未画完且节点不少于 3 个。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_ARROW_SPLIT = re.compile(r"\s*(?:↓|↑|→|⇒|->|=>)\s*")
_ARROW_GLYPHS = ("↓", "↑", "→", "⇒", "->", "=>")
_ART_CHARS = set("┌┐└┘│─├┤┬┴┼►◄▲▼")
_LOOP_WORDS = re.compile(r"再次|回到|循环|反复|往复")


def _norm(token: str) -> str:
    """回路判断用的归一化：去空白、去结尾省略号。"""
    return token.replace(" ", "").rstrip("….。.")


def classify_code(lines: List[str], lang: str = "") -> Dict[str, Any]:
    """返回 {shape, nodes, loop, node_count, direction}。

    shape:
      - empty      空代码块
      - chain      含箭头的有序链（nodes 为节点文字，按书写顺序）
      - ascii_art  框线字符绘制的图（不可解析为链，需人工或生图处理）
      - text       其它纯文字代码块
    direction（仅 chain）：up = 堆叠（只用 ↑）；forward = 流程。
    loop: 见模块文档。
    """
    nonblank = [ln.strip() for ln in lines if ln.strip()]
    if not nonblank:
        return {"shape": "empty", "nodes": [], "loop": False, "node_count": 0}

    if any(ch in _ART_CHARS for ln in nonblank for ch in ln):
        return {"shape": "ascii_art", "nodes": [], "loop": False, "node_count": 0}

    if any(_ARROW_SPLIT.search(ln) for ln in nonblank):
        nodes: List[str] = []
        glyphs = set()
        tail = False                       # 末尾或中间出现省略号：链未画完（回路的常见写法）
        for ln in nonblank:
            glyphs.update(g for g in _ARROW_GLYPHS if g in ln)
            for part in _ARROW_SPLIT.split(ln):
                p = part.strip()
                if not p:
                    continue
                if not _norm(p):          # 纯省略号
                    tail = True
                    continue
                nodes.append(p)
        if len(nodes) >= 2:
            normalized = [_norm(n) for n in nodes]
            loop = (len(set(normalized)) < len(normalized)
                    or any(_LOOP_WORDS.search(n) for n in nodes)
                    or (tail and len(nodes) >= 3))
            direction = "up" if glyphs == {"↑"} else "forward"
            return {"shape": "chain", "nodes": nodes, "loop": loop, "node_count": len(nodes),
                    "direction": direction}

    return {"shape": "text", "nodes": [], "loop": False, "node_count": 0}
