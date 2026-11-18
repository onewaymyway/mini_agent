"""planning/rules.py — 离线规则版：不调用模型，生成与 LLM 版同结构的摘要与大纲。

用途：无模型可用时的兜底，也是 LLM 版失败时可人工切换的方案。结果 engine=rules，
章标题取自原文一级标题，论点取自段落首句，不做任何改写。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .spine_math import partition, renumber, recompute_budgets

_SENT = re.compile(r"[^。！？!?]+[。！？!?]?")
_MIN_CLAIM = 12


def _claim_sentences(body: str) -> List[str]:
    """论点句：去掉过短的句子与以冒号结尾的残句（如“答案很可能是：”）。"""
    out: List[str] = []
    for sent in _SENT.findall(body):
        t = sent.strip()
        if len(t) >= _MIN_CLAIM and not t.endswith(("：", ":")):
            out.append(t)
    return out
_NUM = re.compile(r"\d+(?:\.\d+)?(?:%|倍|万亿|亿|万|年|美元|元)")


def _short(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


def rules_digest(group: Dict[str, Any], blocks_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    bs = [blocks_by_id[i] for i in group["block_ids"]]
    paras = [b.get("plain", "") for b in bs if b["kind"] == "paragraph"]
    body = " ".join(p for p in paras if p)
    sentences = _claim_sentences(body)

    numbers: List[str] = []
    for n in _NUM.findall(group["text"]):
        if n not in numbers:
            numbers.append(n)

    quotable: List[str] = []
    for b in bs:
        if b["kind"] == "quote":
            t = b.get("plain", "").replace("\n", " ").strip()
            if 0 < len(t) <= 80 and t not in quotable:
                quotable.append(t)

    summary = _short(body, 120) if body else "（本组没有正文段落）"
    return {
        "group_id": group["id"],
        "section_ids": group["section_ids"],
        "summary": summary,
        "core_claims": sentences[:3] or [summary],
        "key_numbers": numbers[:5],
        "terms": [],
        "analogies": [],
        "quotable": quotable[:3],
        "relation_to_prev": "",
        "engine": "rules",
    }


def _unique(items: List[str]) -> List[str]:
    out: List[str] = []
    for x in items:
        if x and x not in out:
            out.append(x)
    return out


def section_claims(doc: Dict[str, Any]) -> Dict[str, str]:
    """每个小节自己的首个论点句（用于章的一句话，避免同组各章重复）。"""
    texts: Dict[str, List[str]] = {}
    for b in doc["blocks"]:
        if b["kind"] == "paragraph":
            texts.setdefault(b["section"], []).append(b.get("plain", ""))
    out: Dict[str, str] = {}
    for sid, parts in texts.items():
        claims = _claim_sentences(" ".join(p for p in parts if p))
        out[sid] = claims[0] if claims else ""
    return out


def rules_spine(catalog: List[Dict[str, Any]], digests: List[Dict[str, Any]], meta: Dict[str, Any],
                claims: Dict[str, str] = None) -> Dict[str, Any]:
    claims = claims or {}
    n = len(catalog)
    k = n if n <= 4 else min(7, max(5, round(n / 9)))
    weights = [max(1, c["chars"]) for c in catalog]
    groups = partition(weights, k)

    digest_by_sec: Dict[str, Dict[str, Any]] = {}
    for d in digests:
        for sid in d["section_ids"]:
            digest_by_sec.setdefault(sid, d)

    parts: List[Dict[str, Any]] = []
    for idx, members in enumerate(groups):
        ids = [catalog[i]["id"] for i in members]
        role = "intro" if idx == 0 else ("conclusion" if idx == len(groups) - 1 else "argument")
        parts.append({
            "id": "",
            "title": _short(catalog[members[0]]["title"], 24),
            "role": role,
            "one_line": _short(next((claims[x] for x in ids if claims.get(x)), ""), 60),
            "section_ids": ids,
            "weight": float(sum(weights[i] for i in members)),
            "budget_minutes": 0.0,
        })

    quotes = _unique([q for d in digests for q in d.get("quotable", [])])
    thesis = (digests[0].get("core_claims") or [meta["title"]])[0] if digests else meta["title"]
    hooks = [_short(q, 40) for q in quotes[:3]] or [_short(thesis, 40)]

    spine = {
        "schema": 1,
        "engine": "rules",
        "confirmed": False,
        "target_minutes": meta["target_minutes"],
        "mode": meta["mode"],
        "thesis": thesis,
        "audience": "未指定（规则生成）",
        "tone": "口语讲解",
        "arc": f"规则生成：按原文一级标题顺序分为 {len(parts)} 章，未做改写。",
        "hook_candidates": hooks,
        "selected_hook": 0,
        "parts": parts,
        "glossary": [],
        "golden_quotes": quotes[:3],
        "cuts": [],
    }
    renumber(spine)
    recompute_budgets(spine)
    return spine
