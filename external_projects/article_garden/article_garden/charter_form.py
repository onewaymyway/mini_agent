"""charter_form.py — 章程与表单文本之间的互转（纯函数，不依赖 streamlit，便于测试）。

设置页的章程编辑器用多行文本框（每行一项）与表格（术语表）编辑；这里负责转换。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_SPLIT = re.compile(r"[,，、;；]")


def lines_to_list(text: str) -> List[str]:
    out: List[str] = []
    for line in (text or "").splitlines():
        item = line.strip()
        if item and item not in out:
            out.append(item)
    return out


def list_to_lines(items: List[str]) -> str:
    return "\n".join(items or [])


def aliases_to_list(text: str) -> List[str]:
    out: List[str] = []
    for part in _SPLIT.split(text or ""):
        item = part.strip()
        if item and item not in out:
            out.append(item)
    return out


def terms_to_rows(terms: Dict[str, List[str]]) -> List[Dict[str, str]]:
    return [{"规范术语": k, "别名（逗号分隔）": "，".join(v)} for k, v in (terms or {}).items()]


def rows_to_terms(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    terms: Dict[str, List[str]] = {}
    for row in rows or []:
        name = str(row.get("规范术语") or "").strip()
        if not name:
            continue
        aliases = [a for a in aliases_to_list(str(row.get("别名（逗号分隔）") or "")) if a != name]
        merged = terms.setdefault(name, [])
        for a in aliases:
            if a not in merged:
                merged.append(a)
    return terms


def form_to_charter(*, domain: str, scope_in: str, scope_out: str, primary: str, levels: str, tone: str,
                    length_min: int, length_max: int, must_have: str, ai_disclosure: bool,
                    term_rows: List[Dict[str, Any]], prefer: str, avoid: str, min_credibility: str,
                    disclaimer: str, forbidden: str) -> Dict[str, Any]:
    """表单值 → 章程字典（再交给 charter.save_charter 校验）。"""
    return {
        "domain": domain,
        "scope": {"in": lines_to_list(scope_in), "out": lines_to_list(scope_out)},
        "audience": {"primary": primary, "levels": lines_to_list(levels)},
        "style": {"tone": tone, "length": [int(length_min), int(length_max)],
                  "must_have": lines_to_list(must_have), "ai_disclosure": bool(ai_disclosure)},
        "terms": rows_to_terms(term_rows),
        "sources": {"prefer": lines_to_list(prefer), "avoid": lines_to_list(avoid),
                    "min_credibility": min_credibility},
        "risk": {"disclaimer": disclaimer, "forbidden_claims": lines_to_list(forbidden)},
    }
