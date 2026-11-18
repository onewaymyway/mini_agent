"""validators/fidelity.py — V2 忠实度：摘要中的原话与数字必须能在源文找到。

R5 起的比较规则（忽略标点、大小写、空白与 Markdown 标记）：
  * norm_key(s)：NFKC 归一化、转小写，去掉所有标点（P*）、符号（S*）、空白与控制字符（Z*、C*）。
    中文引号 “” 与英文引号 ""、全角与半角逗号、换行与 “>” 引用符都不再造成误判。
  * locate(quote, source)：在源文中找到归一化后相同的片段，返回**源文中的原始文本**（去掉引用符与换行后的版本）。
    模型输出的引文只要内容对得上，就会被替换为源文中的规范写法，下游拿到的始终是逐字来源。
  * repair_digest(obj, source)：能对齐的引文/数字替换为规范写法，对不上的剔除并返回剔除清单（不再让整组失败）。
  * digest_structure_issues(obj)：只检查结构（摘要、核心论点），这是唯一需要整组重试的情况。

squash() 保留给其它模块（口播校验、测试）使用，行为不变。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

_SQUASH = re.compile(r"[\s*`]+")
_MD_MARK = re.compile(r"[>*`#|]+")
_DROP_CATS = ("P", "S", "Z", "C")
MIN_QUOTE_KEY = 4        # 归一化后少于 4 个字符的引文不可靠，视为无法对齐
MAX_QUOTE_CHARS = 120    # 规范化后的引文上限（字符），超出的截断为无法使用


def squash(s: str) -> str:
    """旧的宽松比较（只去空白与 * `）。保留给不需要忽略标点的调用方。"""
    return _SQUASH.sub("", s or "")


def _keep(ch: str) -> bool:
    return unicodedata.category(ch)[0] not in _DROP_CATS


def norm_key(s: str) -> str:
    """忽略标点、大小写、空白与 Markdown 标记后的比较键。"""
    text = unicodedata.normalize("NFKC", s or "").casefold()
    return "".join(ch for ch in text if _keep(ch))


def _norm_with_map(text: str) -> Tuple[str, List[int]]:
    """归一化文本，并记录每个归一化字符对应的原文位置（用于把匹配区间映射回原文）。"""
    out: List[str] = []
    idx: List[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if _keep(c):
                out.append(c)
                idx.append(i)
    return "".join(out), idx


def clean_span(span: str) -> str:
    """把源文片段整理为可读的规范写法：换行并为空格，去掉引用符、加粗与代码标记，压缩空白。"""
    text = re.sub(r"\s*\n\s*", " ", span)
    text = _MD_MARK.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def locate(quote: str, source: str, min_key: int = MIN_QUOTE_KEY) -> Optional[str]:
    """在源文中找到与 quote 归一化相同的片段，返回规范写法；找不到返回 None。"""
    key = norm_key(quote)
    if len(key) < min_key:
        return None
    src_norm, idx = _norm_with_map(source)
    pos = src_norm.find(key)
    if pos < 0:
        return None
    start, end = idx[pos], idx[pos + len(key) - 1] + 1
    return clean_span(source[start:end])


def contains(source: str, text: str) -> bool:
    """归一化后 text 是否出现在 source 中（用于数字、英文词等短片段）。"""
    key = norm_key(text)
    return bool(key) and key in norm_key(source)


def repair_digest(obj: Dict[str, Any], source_text: str) -> Tuple[Dict[str, Any], List[str]]:
    """对齐模型输出的引文与数字。返回 (修复后的对象, 被剔除的条目说明)。

    * quotable：能对齐的替换为源文规范写法（去重）；对不上的剔除。
    * key_numbers：能对齐的替换为源文规范写法；对不上的剔除。
    结构性问题（摘要、核心论点为空）不在这里处理，见 digest_structure_issues。
    """
    dropped: List[str] = []
    quotes: List[str] = []
    for q in obj.get("quotable") or []:
        text = str(q)
        found = locate(text, source_text)
        if found is None:
            dropped.append(f"金句未在原文中找到，已剔除：{text[:30]}")
        elif found not in quotes:
            quotes.append(found[:MAX_QUOTE_CHARS])
    numbers: List[str] = []
    for n in obj.get("key_numbers") or []:
        text = str(n).strip()
        if not text:
            continue
        found = locate(text, source_text, min_key=1)
        if found is None:
            dropped.append(f"数字未在原文中找到，已剔除：{text[:20]}")
        elif found not in numbers:
            numbers.append(found)
    repaired = dict(obj)
    repaired["quotable"] = quotes
    repaired["key_numbers"] = numbers
    return repaired, dropped


def dropped_entries(obj: Dict[str, Any], source_text: str) -> List[Dict[str, str]]:
    """模型输出中无法对齐原文的金句与数字（结构化，供界面展示与手动恢复）。应在 repair_digest 之前调用。"""
    out: List[Dict[str, str]] = []
    for q in obj.get("quotable") or []:
        text = str(q).strip()
        if text and locate(text, source_text) is None:
            out.append({"kind": "quote", "text": text})
    for n in obj.get("key_numbers") or []:
        text = str(n).strip()
        if text and locate(text, source_text, min_key=1) is None:
            out.append({"kind": "number", "text": text})
    return out


def digest_structure_issues(obj: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if not str(obj.get("summary", "")).strip():
        issues.append("summary 为空")
    claims = obj.get("core_claims")
    if not isinstance(claims, list) or not any(str(c).strip() for c in claims):
        issues.append("core_claims 为空")
    return issues


def validate_digest(obj: Dict[str, Any], source_text: str) -> List[str]:
    """兼容旧接口：返回全部问题（结构 + 无法对齐的引文/数字）。不修改输入。"""
    issues = digest_structure_issues(obj)
    for q in obj.get("quotable") or []:
        if norm_key(str(q)) and locate(str(q), source_text) is None:
            issues.append(f"quotable 不是原文逐字片段：{str(q)[:30]}")
    for n in obj.get("key_numbers") or []:
        if norm_key(str(n)) and not contains(source_text, str(n)):
            issues.append(f"key_numbers 在原文中找不到：{str(n)[:20]}")
    return issues
