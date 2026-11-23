"""validate.py — 摘要的代码校验（不依赖 LLM 自觉；方案 §5.1）。

比较采用“去空白 + 去 Markdown 标记”后的文本：LLM 摘录时常会改动空格、粗体符号或换行，
这些不应被判为“引文不存在”；但文字本身（包括中英文标点）必须一致。
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, List, Tuple

KINDS = ("概念讲解", "对比", "教程", "综述", "FAQ", "案例")
LEVELS = ("入门", "进阶", "专题")
LONG_ARTICLE_CHARS = 300

_MD_NOISE = re.compile(r"[\s*_#>`~|\-]+")


def squash(s: str) -> str:
    return _MD_NOISE.sub("", s or "")


def norm(s: str) -> str:
    """比较用的规范形式：统一全角/半角，去掉所有标点、空白、Markdown 标记，忽略大小写。
    只改变书写形式，不改变用词；因此“意思与用词不变、只是标点或引号不同”的文字会视为相同。"""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", s or "")).lower()


def appears(needle: str, haystack: str) -> bool:
    n = norm(needle)
    return bool(n) and n in norm(haystack)


_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")


def sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if squash(s).strip()]


def _candidates(text: str) -> List[str]:
    sents = sentences(text)
    out = list(sents)
    out += [f"{a}{b}" for a, b in zip(sents, sents[1:])]    # 引文可能跨两句
    return out


def nearest_sentence(quote: str, text: str) -> Tuple[str, float]:
    """原文中与 quote 最接近的句子（或相邻两句）及相似度，比较时使用规范形式。"""
    q = norm(quote)
    best: Tuple[str, float] = ("", 0.0)
    if not q:
        return best
    for s in _candidates(text):
        r = difflib.SequenceMatcher(None, q, norm(s), autojunk=False).ratio()
        if r > best[1]:
            best = (s, r)
    return best


COVER_MIN = 0.8     # 引文中有 80% 以上能在原文中找到连续的对应片段（容许截断、改写少量字词）
SIM_MIN = 0.85      # 或整体相似度 ≥ 0.85


def grounded_sentence(quote: str, text: str) -> str:
    """如果 quote 能在原文中找到对应（标点、引号、标记、少量用词不同均可），返回原文中的那一句；否则返回空串。"""
    q = norm(quote)
    if not q:
        return ""
    best = ("", 0.0)
    for s in _candidates(text):
        ns = norm(s)
        if q in ns:
            return s
        m = difflib.SequenceMatcher(None, q, ns, autojunk=False)
        match = m.find_longest_match(0, len(q), 0, len(ns)).size
        if match >= COVER_MIN * len(q):
            return s
        r = m.ratio()
        if r > best[1]:
            best = (s, r)
    return best[0] if best[1] >= SIM_MIN else ""


def clean_edges(sentence: str) -> str:
    """去掉句首的引用/加粗标记与句末的加粗标记。只删除两端，所以结果仍是原文的子串。"""
    s = sentence.strip()
    s = re.sub(r"^(?:>\s*)+", "", s)
    s = re.sub(r"^\*\*", "", s)
    s = re.sub(r"\*\*$", "", s)
    return s.strip()


def _verbatim(quote: str, text: str) -> bool:
    """引文是否就是原文中的一段（只忽略空白）。存储的引文必须是逐字原文。"""
    strip = lambda x: re.sub(r"\s+", "", x or "")
    return bool(strip(quote)) and strip(quote) in strip(text)


def autofix_quotes(obj: Any, text: str) -> List[str]:
    """代码校正：引文与原文某处对应（只差标点、引号、标记或个别用词）时，直接改为原文中的逐字句子，不再请模型修复。
    原地修改 obj；返回校正说明（供日志）。"""
    notes: List[str] = []
    claims = obj.get("claims") if isinstance(obj, dict) else None
    if not isinstance(claims, list):
        return notes
    for i, cl in enumerate(claims, 1):
        if not isinstance(cl, dict):
            continue
        q = str(cl.get("quote") or "")
        if not q.strip() or _verbatim(q, text):
            continue
        s = grounded_sentence(q, text)
        if s:
            cl["quote"] = clean_edges(s)
            notes.append(f"第 {i} 条引文与原文对应（允许标点、引号或个别用词不同），已改为原文中的逐字句子")
    return notes


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_concepts_and_claims(obj: Dict[str, Any], text: str) -> List[str]:
    issues: List[str] = []
    concepts = obj.get("concepts")
    if not isinstance(concepts, list) or not all(isinstance(c, str) and c.strip() for c in concepts):
        issues.append("concepts 应为非空字符串的列表")
    else:
        for c in concepts:
            if not appears(c, text):
                issues.append(f"概念「{c}」在原文中找不到，请只使用原文中出现的用词")
    claims = obj.get("claims")
    if not isinstance(claims, list):
        issues.append("claims 应为列表，每项含 text 与 quote")
        return issues
    for i, cl in enumerate(claims, 1):
        if not isinstance(cl, dict) or not str(cl.get("text") or "").strip() or not str(cl.get("quote") or "").strip():
            issues.append(f"第 {i} 条断言缺少 text 或 quote")
            continue
        if not appears(str(cl["quote"]), text):
            s, r = nearest_sentence(str(cl["quote"]), text)
            # 即使相似度不高，也给出一句真实原文：让模型“照抄”比“重新改写”更容易成功
            s = s.replace("*", "").replace(">", "").strip()
            hint = (f"；原文中最接近的句子是「{s[:80]}」，请把 quote 改为该句的逐字复制"
                    if s else "；请从原文中逐字复制一句作为 quote")
            issues.append(f"第 {i} 条断言的 quote 不是原文中的逐字摘录："
                          f"「{str(cl['quote'])[:40]}」{hint}")
    return issues


def validate_extract(obj: Dict[str, Any], text: str) -> List[str]:
    """分块摘录（长文的 map 阶段）只检查概念与断言。"""
    return check_concepts_and_claims(obj, text)


def validate_full(obj: Dict[str, Any], text: str) -> List[str]:
    """整篇摘要（短文一次完成）的完整校验。"""
    issues = check_concepts_and_claims(obj, text)
    for key in ("title", "thesis"):
        if not str(obj.get(key) or "").strip():
            issues.append(f"缺少字段 {key}")
    if obj.get("kind") not in KINDS:
        issues.append(f"kind 必须是 {' / '.join(KINDS)} 之一")
    if obj.get("level") not in LEVELS:
        issues.append(f"level 必须是 {' / '.join(LEVELS)} 之一")
    score = obj.get("self_score")
    if not _is_int(score) or not 1 <= score <= 5:
        issues.append("self_score 应为 1 到 5 的整数")
    dangling = obj.get("dangling", [])
    if not isinstance(dangling, list):
        issues.append("dangling 应为列表（没有则写 []）")
    else:
        for d in dangling:
            if not isinstance(d, str) or not appears(d, text):
                issues.append(f"悬空概念「{d}」在原文中找不到")
    if len(squash(text)) > LONG_ARTICLE_CHARS and not obj.get("claims"):
        issues.append("正文较长但 claims 为空，请至少给出一条事实性断言")
    return issues


def validate_meta(obj: Dict[str, Any], text: str) -> List[str]:
    """长文的元信息步骤：只检查元字段；dangling 必须能在全文找到。"""
    issues: List[str] = []
    for key in ("title", "thesis"):
        if not str(obj.get(key) or "").strip():
            issues.append(f"缺少字段 {key}")
    if obj.get("kind") not in KINDS:
        issues.append(f"kind 必须是 {' / '.join(KINDS)} 之一")
    if obj.get("level") not in LEVELS:
        issues.append(f"level 必须是 {' / '.join(LEVELS)} 之一")
    score = obj.get("self_score")
    if not _is_int(score) or not 1 <= score <= 5:
        issues.append("self_score 应为 1 到 5 的整数")
    dangling = obj.get("dangling", [])
    if not isinstance(dangling, list):
        issues.append("dangling 应为列表（没有则写 []）")
    else:
        for d in dangling:
            if not isinstance(d, str) or not appears(d, text):
                issues.append(f"悬空概念「{d}」在原文中找不到")
    return issues
