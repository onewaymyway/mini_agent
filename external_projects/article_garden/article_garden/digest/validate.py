"""validate.py — 摘要的代码校验（不依赖 LLM 自觉；方案 §5.1）。

比较采用“去空白 + 去 Markdown 标记”后的文本：LLM 摘录时常会改动空格、粗体符号或换行，
这些不应被判为“引文不存在”；但文字本身（包括中英文标点）必须一致。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

KINDS = ("概念讲解", "对比", "教程", "综述", "FAQ", "案例")
LEVELS = ("入门", "进阶", "专题")
LONG_ARTICLE_CHARS = 300

_MD_NOISE = re.compile(r"[\s*_#>`~|\-]+")


def squash(s: str) -> str:
    return _MD_NOISE.sub("", s or "")


def appears(needle: str, haystack: str) -> bool:
    n = squash(needle)
    return bool(n) and n in squash(haystack)


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
            issues.append(f"第 {i} 条断言的 quote 不是原文中的逐字摘录："
                          f"「{str(cl['quote'])[:40]}」")
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
