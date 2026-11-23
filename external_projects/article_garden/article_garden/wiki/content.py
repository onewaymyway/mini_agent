"""wiki/content.py — 概念定义、关系边、冲突的提示词与校验（方案 §5.2）。

每个 LLM 步骤都只接受“可以被代码验证”的输出：
  * 定义：必须点名概念，长度在范围内，且 based_on 只能引用提供给它的断言 id。
  * 关系：类型必须在允许列表内，依据摘录必须逐字出现在该文章原文中，否则整条丢弃（不修复）。
  * 冲突：两条断言 id 必须存在、来自不同文章，且给出理由。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import validate_util as vu

DEF_MIN, DEF_MAX = 60, 400          # 首版字数范围（方案原为 100–300，放宽以适应短概念）
EDGE_TYPES = ("is_a", "part_of", "prerequisite", "related", "contrasts", "causes", "example_of")
UNDIRECTED = {"related", "contrasts"}
MAX_CLAIMS_PER_CONCEPT = 15
MAX_BODY_FOR_EDGES = 12000


# ------------------------------------------------------------------ 定义

def definition_prompt(canon: str, claims: List[Dict[str, Any]]) -> str:
    lines = [f"{c['id']}：{c['text']}（原文：{c['quote'][:120]}）" for c in claims[:MAX_CLAIMS_PER_CONCEPT]]
    return ("【阶段:定义】请为概念「" + canon + "」写一段定义。只依据下面列出的断言，不得引入其它知识。"
            f"定义需点名「{canon}」，长度约 {DEF_MIN}–{DEF_MAX} 字。只输出 YAML：\n"
            "definition: 定义正文\n"
            "based_on: [用到的断言 id…]\n\n断言：\n" + "\n".join(lines))


def definition_validator(canon: str, allowed: List[str]):
    def _v(obj: Dict[str, Any]) -> List[str]:
        issues = []
        d = str(obj.get("definition") or "").strip()
        if not d:
            issues.append("definition 不能为空")
        else:
            if not vu.appears(canon, d):
                issues.append(f"定义中应点名「{canon}」")
            if not DEF_MIN <= len(d) <= DEF_MAX:
                issues.append(f"定义长度为 {len(d)} 字，应在 {DEF_MIN}–{DEF_MAX} 字之间")
        based = obj.get("based_on")
        if not isinstance(based, list) or not based:
            issues.append("based_on 应为非空列表，列出所依据的断言 id")
        else:
            bad = [str(b) for b in based if str(b) not in allowed]
            if bad:
                issues.append(f"based_on 含有未提供的断言 id：{', '.join(bad[:5])}")
        return issues
    return _v


# ------------------------------------------------------------------ 关系

def edge_prompt(title: str, body: str, pairs: List[Tuple[str, str]]) -> str:
    listed = "\n".join(f"- {a} ｜ {b}" for a, b in pairs)
    return ("【阶段:关系】阅读下面的文章，判断列出的概念对之间是否存在明确的关系。"
            "只有文章中明确表述的关系才可以输出；没有依据的一律不要输出。只输出 YAML：\n"
            "edges:\n"
            "  - {src: 概念名, dst: 概念名, type: is_a|part_of|prerequisite|related|contrasts|causes|example_of,\n"
            "     quote: 文章中逐字摘录的依据, rationale: 一句话说明理由}\n"
            "关系方向：is_a（src 是 dst 的一种）、part_of（src 是 dst 的组成部分）、prerequisite（读懂 src 需要先懂 dst）、"
            "causes（src 导致 dst）、example_of（src 是 dst 的例子）；related 与 contrasts 不分方向。\n"
            "概念名只能使用下面列出的写法。没有关系时输出 edges: []。\n\n"
            f"概念对：\n{listed}\n\n文章《{title}》：\n<article>\n{body}\n</article>")


def parse_edges(obj: Dict[str, Any], pairs: List[Tuple[str, str]], body: str):
    """返回 (接受的边, 丢弃的原因列表)。每条独立判断，不整体失败。"""
    accepted, dropped = [], []
    names = {n for p in pairs for n in p}
    valid_pairs = {frozenset(p) for p in pairs}
    items = obj.get("edges") or []
    if not isinstance(items, list):
        return [], ["edges 不是列表"]
    for it in items:
        if not isinstance(it, dict):
            dropped.append("条目不是键值结构")
            continue
        src, dst = str(it.get("src") or "").strip(), str(it.get("dst") or "").strip()
        typ = str(it.get("type") or "").strip()
        quote = str(it.get("quote") or "").strip()
        why = str(it.get("rationale") or "").strip()
        if typ not in EDGE_TYPES:
            dropped.append(f"{src}→{dst}：类型「{typ}」不在允许列表内")
        elif src not in names or dst not in names or src == dst or frozenset((src, dst)) not in valid_pairs:
            dropped.append(f"{src}→{dst}：概念名不在本次给出的概念对中")
        elif not quote or not vu.appears(quote, body):
            dropped.append(f"{src}→{dst}：依据摘录不在原文中（无依据，丢弃）")
        elif not why:
            dropped.append(f"{src}→{dst}：缺少理由")
        else:
            accepted.append({"src": src, "dst": dst, "type": typ, "quote": quote, "rationale": why})
    return accepted, dropped


# ------------------------------------------------------------------ 冲突

def conflict_prompt(canon: str, claims: List[Dict[str, Any]]) -> str:
    lines = [f"{c['id']}（来自 {c['article_id']}）：{c['text']}" for c in claims]
    return ("【阶段:冲突】下面是关于概念「" + canon + "」的若干断言，来自不同文章。"
            "只找出**实质上互相矛盾**的两条（不是措辞不同或侧重不同）。没有则输出空列表。只输出 YAML：\n"
            "conflicts:\n  - {a: 断言id, b: 断言id, reason: 矛盾的简要说明}\n\n" + "\n".join(lines))


def conflict_validator(claims: List[Dict[str, Any]]):
    by_id = {c["id"]: c for c in claims}

    def _v(obj: Dict[str, Any]) -> List[str]:
        items = obj.get("conflicts")
        if items is None:
            return ["conflicts 字段缺失（没有冲突请写 conflicts: []）"]
        if not isinstance(items, list):
            return ["conflicts 应为列表"]
        issues = []
        for it in items:
            if not isinstance(it, dict):
                issues.append("冲突条目应为键值结构")
                continue
            a, b = str(it.get("a")), str(it.get("b"))
            if a not in by_id or b not in by_id:
                issues.append(f"断言 id 不存在：{a} / {b}")
            elif by_id[a]["article_id"] == by_id[b]["article_id"]:
                issues.append(f"{a} 与 {b} 来自同一篇文章，不算跨文章冲突")
            elif not str(it.get("reason") or "").strip():
                issues.append("每条冲突需给出 reason")
        return issues
    return _v
