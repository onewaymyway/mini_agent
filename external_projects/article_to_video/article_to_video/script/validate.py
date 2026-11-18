"""script/validate.py — 口播稿校验。

V2 忠实度（R9 起忽略标点与大小写）：句中的数字必须以完整记号出现在本章原文中（3.5 与 35 不相同，
            3,000 与 3000 相同）；英文词按归一化后的子串比较；标记为金句的句子必须能对齐到原文（见 canonicalize_quotes）；
            引用的小节必须属于本章。
V3 可读性：不得含 Markdown 符号与裸公式；单句不超过 60 字（超过 40 字给警告）。
节奏：      condensed 模式下章的字数应在预算 ±15% 内（超出作为“节奏问题”，由写稿重试处理）。

返回 errors（阻止保存/确认）、warnings（提示）、pacing（节奏问题，单独返回）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..validators.fidelity import locate, norm_key, numbers_supported
from .budget import count_chars, pacing_issue

_NUM = re.compile(r"\d+(?:\.\d+)?%?")
_LATIN = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_MD = re.compile(r"[#*`|>]|\*\*")
_FORMULA = re.compile(r"[\\{}^_$]")
MAX_SENTENCE = 60
WARN_SENTENCE = 40


def validate_script(
    script: Dict[str, Any],
    part_source_text: str,
    part_section_ids: List[str],
    *,
    check_pacing: bool = True,
    manual_quotes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """manual_quotes：大纲中人工加入、指定到本章的金句（R10）。它们不要求出现在原文中，
    标记为金句的句子只要与其中之一内容相同即可。"""
    manual_keys = {norm_key(q) for q in (manual_quotes or []) if norm_key(q)}
    errors: List[str] = []
    warnings: List[str] = []
    flags: List[Dict[str, Any]] = []      # R11：句子级问题的位置，供局部修正使用
    src_key = norm_key(part_source_text)
    beats = script.get("beats") or []
    if not beats:
        errors.append("没有任何段落（beat）")

    def flag(bid, si, kind, msg):
        errors.append(msg)
        flags.append({"beat": bid, "index": si, "kind": kind, "problem": msg})

    covered = set()
    total = 0
    for b in beats:
        bid = b.get("id", "?")
        refs = b.get("source_refs") or []
        if not refs:
            errors.append(f"{bid} 缺少 source_refs（原文来源）")      # 段落级：整章重写
        for r in refs:
            if r not in part_section_ids:
                errors.append(f"{bid} 引用了不属于本章的小节 {r}")    # 段落级：整章重写
            else:
                covered.add(r)
        sentences = b.get("sentences") or []
        if not sentences:
            errors.append(f"{bid} 没有句子")                          # 段落级：整章重写
        for si, s in enumerate(sentences):
            t = str(s.get("text", "")).strip()
            if not t:
                flag(bid, si, "empty", f"{bid} 含空句")
                continue
            n = count_chars(t)
            total += n
            if _MD.search(t):
                flag(bid, si, "markdown", f"{bid} 含 Markdown 符号：{t[:24]}")
            if _FORMULA.search(t):
                flag(bid, si, "formula", f"{bid} 含裸公式符号（应改为口述）：{t[:24]}")
            if n > MAX_SENTENCE:
                flag(bid, si, "too_long", f"{bid} 单句过长（{n} 字）：{t[:24]}")
            elif n > WARN_SENTENCE:
                warnings.append(f"{bid} 单句偏长（{n} 字）")
            for num in _NUM.findall(t):
                if not numbers_supported(num, part_source_text):
                    flag(bid, si, "number", f"{bid} 数字“{num}”在本章原文中找不到")
            for w in _LATIN.findall(t):
                if norm_key(w) not in src_key:
                    flag(bid, si, "latin", f"{bid} 英文词“{w}”在本章原文中找不到")
            if s.get("quote") and locate(t, part_source_text) is None and norm_key(t) not in manual_keys:
                flag(bid, si, "quote", f"{bid} 标记为原文金句，但不是原文逐字片段：{t[:24]}")

    uncovered = [sid for sid in part_section_ids if sid not in covered]
    if uncovered:
        warnings.append(f"以下小节未在口播中被引用：{', '.join(uncovered)}")

    pacing: Optional[str] = None
    if check_pacing:
        pacing = pacing_issue(total, int(script.get("budget_chars") or 0))
    hard = len(flags) < len(errors)         # 存在不属于单句的错误（段落或结构问题）
    return {"errors": errors, "warnings": warnings, "pacing": pacing, "total_chars": total,
            "flags": flags, "hard": hard}


def canonicalize_quotes(script: Dict[str, Any], part_source_text: str) -> int:
    """R9：标记为原文金句的句子，若能对齐原文，改为原文中的规范写法（引号、换行与引用符去掉）。
    对不上原文的句子保持不变，由 validate_script 报错。返回改写的句子数。原地修改 script。"""
    changed = 0
    for beat in script.get("beats") or []:
        for s_ in beat.get("sentences") or []:
            if not s_.get("quote"):
                continue
            found = locate(str(s_.get("text", "")), part_source_text)
            if found is not None and found != s_.get("text"):
                s_["text"] = found
                changed += 1
    return changed
