"""planning/llm.py — LLM 版 S1 分节摘要（map）与 S2 大纲（reduce）。

输出经 validators 校验；不合格带问题清单重试一次，仍不合格则抛 PlanError（不写任何结果）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .. import joblog
from ..validators.coverage import validate_spine
from ..validators.fidelity import validate_digest
from .errors import PlanError
from .jsonio import parse_json_object
from .prompts import DIGEST_SYSTEM, SPINE_SYSTEM, digest_prompt, spine_prompt
from .spine_math import recompute_budgets, renumber

MAX_ATTEMPTS = 2


def llm_digest(group: Dict[str, Any], llm, article_title: str) -> Tuple[Dict[str, Any], str]:
    issues: List[str] = []
    raw = ""
    for attempt in range(MAX_ATTEMPTS):
        joblog.bound_log(f"节组 {group['id']}：调用模型生成摘要（第 {attempt + 1} 次）")
        raw = llm.ask(digest_prompt(group, article_title, issues), system=DIGEST_SYSTEM)
        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            issues = [f"JSON 解析失败：{exc}"]
            joblog.bound_log(f"节组 {group['id']}：输出不是有效 JSON，将重试", level="warn")
            continue
        obj["group_id"] = group["id"]
        obj["section_ids"] = group["section_ids"]
        obj.setdefault("key_numbers", [])
        obj.setdefault("quotable", [])
        obj.setdefault("terms", [])
        obj.setdefault("analogies", [])
        obj.setdefault("relation_to_prev", "")
        obj["engine"] = "llm"
        issues = validate_digest(obj, group["text"])
        if not issues:
            joblog.bound_log(f"节组 {group['id']}：摘要通过校验", level="ok")
            return obj, raw
        joblog.bound_log(f"节组 {group['id']}：摘要未通过校验（{issues[0]}），将重试", level="warn")
    raise PlanError(f"分节摘要 {group['id']} 两次输出仍不合格：{'；'.join(issues)}", raw=raw)


def _normalize_spine(obj: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    parts = []
    for p in obj.get("parts") or []:
        if not isinstance(p, dict):
            continue
        parts.append({
            "id": "",
            "title": str(p.get("title", "")).strip(),
            "role": str(p.get("role") or "argument"),
            "one_line": str(p.get("one_line", "")).strip(),
            "section_ids": [str(x) for x in p.get("section_ids") or []],
            "weight": float(p.get("weight") or 1.0),
            "budget_minutes": 0.0,
        })
    spine = {
        "schema": 1,
        "engine": "llm",
        "confirmed": False,
        "target_minutes": meta["target_minutes"],
        "mode": meta["mode"],
        "thesis": str(obj.get("thesis", "")).strip(),
        "audience": str(obj.get("audience", "")).strip(),
        "tone": str(obj.get("tone", "")).strip(),
        "arc": str(obj.get("arc", "")).strip(),
        "hook_candidates": [str(h).strip() for h in obj.get("hook_candidates") or [] if str(h).strip()],
        "selected_hook": int(obj.get("selected_hook") or 0),
        "parts": parts,
        "glossary": [g for g in obj.get("glossary") or [] if isinstance(g, dict)],
        "golden_quotes": [str(q) for q in obj.get("golden_quotes") or []],
        "cuts": [{"section_ids": [str(x) for x in c.get("section_ids") or []],
                  "reason": str(c.get("reason", "")).strip()}
                 for c in obj.get("cuts") or [] if isinstance(c, dict)],
    }
    renumber(spine)
    recompute_budgets(spine)
    return spine


def llm_spine(digests: List[Dict[str, Any]], catalog: List[Dict[str, Any]],
              meta: Dict[str, Any], llm) -> Tuple[Dict[str, Any], str]:
    section_ids = [c["id"] for c in catalog]
    issues: List[str] = []
    raw = ""
    for attempt in range(MAX_ATTEMPTS):
        joblog.bound_log(f"调用模型生成大纲（第 {attempt + 1} 次）")
        raw = llm.ask(spine_prompt(digests, catalog, meta, issues), system=SPINE_SYSTEM)
        try:
            spine = _normalize_spine(parse_json_object(raw), meta)
        except (ValueError, TypeError, AttributeError) as exc:
            issues = [f"JSON 结构不正确：{exc}"]
            joblog.bound_log(f"大纲输出结构不正确，将重试：{exc}", level="warn")
            continue
        result = validate_spine(spine, section_ids, digests)
        if not result["errors"]:
            joblog.bound_log("大纲通过校验", level="ok")
            return spine, raw
        issues = result["errors"]
        joblog.bound_log(f"大纲未通过校验（{issues[0]}），将重试", level="warn")
    raise PlanError(f"大纲两次输出仍不合格：{'；'.join(issues)}", raw=raw)
