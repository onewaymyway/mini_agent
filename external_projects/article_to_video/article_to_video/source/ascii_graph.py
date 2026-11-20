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
_BOX_ONLY = set("┌┐└┘│─├┤┬┴┼▲▼↓↑ ")
_KV_LINE = re.compile(r"^\s*\S.*?\s*(=|≠|≈)\s*\S")
_STAGE_HEAD = re.compile(r"^(早期|初期|中期|后期|高级|初级|进阶|未来)\s*\S")
_SENTENCE_PUNCT = re.compile(r"[。，；;！？!?]")


def _parse_boxes(lines: List[str]) -> Dict[str, Any]:
    """框线图（A4）：每个方框的首段文字为一个节点（按出现顺序）；方框外的文字行也是节点；
    “→ 某节点”的连线表示回到已有节点（回路）。解析不出两个以上节点时返回空。"""
    nodes: List[str] = []
    loop_target = None
    cur: List[str] = []
    closed = True
    in_box = False
    for raw in lines:
        line = raw.rstrip()
        if "┌" in line:
            in_box, cur, closed = True, [], False
            continue
        if in_box and "└" in line:
            in_box = False
            if cur:
                nodes.append(" ".join(cur))
            cur = []
            continue
        if in_box:
            inner = line.strip().strip("│").strip()
            if not closed:
                if inner and not set(inner) & set("↓↑│┌┐└┘┼─") and len(inner) <= 40:
                    cur.append(inner)                  # 方框的标题行
                elif cur or inner:
                    closed = True                      # 空行或图形行结束标题
            continue
        if "→" in line:
            loop_target = line.split("→")[-1].strip()
            continue
        for part in re.split(r"\s{2,}|│", line):          # 方框外多列并排的文字各自成为节点
            t = part.strip().strip("↓↑").strip()
            if not t or set(t) <= _BOX_ONLY:
                continue
            if len(t) > 40:                               # 不像节点的长文字：放弃框线解析，保留为图形
                return {"nodes": [], "loop": False}
            nodes.append(t)
    loop = loop_target is not None and loop_target in nodes
    return {"nodes": nodes, "loop": loop}


def _text_hint(nonblank: List[str]) -> Dict[str, Any]:
    """文本代码块的自动分类（A3）：公式式表达、键值对比、阶段、标签列表、数据列表；都不是则为 None。"""
    joined = " ".join(nonblank)
    n_plus = joined.count("+")
    if len(nonblank) <= 40 and ("≈" in joined or "×" in joined or n_plus >= 2) and not _SENTENCE_PUNCT.search(joined):
        terms = [t for t in re.split(r"\s*(?:≈|×|\+|=)\s*", joined) if t]
        if len(terms) >= 3 and all(len(t) <= 24 for t in terms):
            return {"hint": "formula", "hint_text": joined, "items": terms}
    kv = [ln for ln in nonblank if _KV_LINE.match(ln)]
    triple = len(nonblank) == 3 and nonblank[1] in ("=", "≠", "≈")      # 形如 “X / = / Y” 的三行写法
    if triple or (len(kv) >= 2 and len(kv) * 2 >= len(nonblank)):
        return {"hint": "kv", "hint_text": "；".join(nonblank), "items": nonblank}
    if len(nonblank) >= 2 and _STAGE_HEAD.match(nonblank[0]):
        return {"hint": "stages", "hint_text": nonblank[0], "items": nonblank[1:]}
    if len(nonblank) >= 3 and sum(1 for ln in nonblank if re.search(r"\d", ln)) >= 3:
        return {"hint": "facts", "hint_text": "；".join(nonblank), "items": nonblank}
    if len(nonblank) >= 3 and all(len(ln) <= 24 and not _SENTENCE_PUNCT.search(ln) for ln in nonblank):
        return {"hint": "labels", "hint_text": "、".join(nonblank), "items": nonblank}
    return {}


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
        boxed = _parse_boxes(list(lines))
        if len(boxed["nodes"]) >= 3:
            return {"shape": "chain", "nodes": boxed["nodes"], "loop": boxed["loop"],
                    "node_count": len(boxed["nodes"]), "direction": "forward", "source": "ascii"}
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
        if len(nodes) >= 2 and max(len(n) for n in nodes) <= 40:   # 节点过长说明不是流程链（如并排的图形文字）
            normalized = [_norm(n) for n in nodes]
            loop = (len(set(normalized)) < len(normalized)
                    or any(_LOOP_WORDS.search(n) for n in nodes)
                    or (tail and len(nodes) >= 3))
            direction = "up" if glyphs == {"↑"} else "forward"
            return {"shape": "chain", "nodes": nodes, "loop": loop, "node_count": len(nodes),
                    "direction": direction}

    hint = _text_hint(nonblank)
    return {"shape": "text", "nodes": [], "loop": False, "node_count": 0, **hint}
