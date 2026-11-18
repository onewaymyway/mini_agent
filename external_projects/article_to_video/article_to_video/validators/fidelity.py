"""validators/fidelity.py — V2 忠实度（P1 部分）：摘要中的原话与数字必须能在源文找到。

比较前去掉空白与行内 Markdown 标记，避免格式差异造成误判。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_SQUASH = re.compile(r"[\s*`]+")


def squash(s: str) -> str:
    return _SQUASH.sub("", s or "")


def validate_digest(obj: Dict[str, Any], source_text: str) -> List[str]:
    issues: List[str] = []
    if not str(obj.get("summary", "")).strip():
        issues.append("summary 为空")
    claims = obj.get("core_claims")
    if not isinstance(claims, list) or not any(str(c).strip() for c in claims):
        issues.append("core_claims 为空")
    src = squash(source_text)
    for q in obj.get("quotable") or []:
        if squash(str(q)) and squash(str(q)) not in src:
            issues.append(f"quotable 不是原文逐字片段：{str(q)[:30]}")
    for n in obj.get("key_numbers") or []:
        if squash(str(n)) and squash(str(n)) not in src:
            issues.append(f"key_numbers 在原文中找不到：{str(n)[:20]}")
    return issues
