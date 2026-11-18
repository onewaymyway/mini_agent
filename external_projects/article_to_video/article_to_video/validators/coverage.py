"""validators/coverage.py — V1 覆盖与结构校验（大纲）。

错误（errors）阻止确认；警告（warnings）只提示。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .fidelity import norm_key

MIN_PARTS, MAX_PARTS = 5, 7


def validate_spine(
    spine: Dict[str, Any],
    section_ids: List[str],
    digests: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    known = set(section_ids)
    seen: Dict[str, str] = {}

    parts = spine.get("parts") or []
    cuts = spine.get("cuts") or []
    if not parts:
        errors.append("没有任何章")

    for p in parts:
        if not str(p.get("title", "")).strip():
            errors.append(f"{p.get('id', '?')} 章标题为空")
        ids = p.get("section_ids") or []
        if not ids:
            errors.append(f"{p.get('id', '?')} 没有包含任何小节")
        if float(p.get("weight", 0) or 0) <= 0:
            errors.append(f"{p.get('id', '?')} 权重必须大于 0")
        for sid in ids:
            if sid not in known:
                errors.append(f"未知小节 {sid}")
            elif sid in seen:
                errors.append(f"小节 {sid} 重复出现（{seen[sid]} 与 {p.get('id', '?')}）")
            else:
                seen[sid] = p.get("id", "?")

    for c in cuts:
        for sid in c.get("section_ids") or []:
            if sid not in known:
                errors.append(f"取舍报告中未知小节 {sid}")
            elif sid in seen:
                errors.append(f"小节 {sid} 同时出现在章与取舍报告中（{seen[sid]}）")
            else:
                seen[sid] = "cuts"
        if not str(c.get("reason", "")).strip():
            warnings.append(f"取舍 {c.get('section_ids')} 缺少理由")

    missing = [sid for sid in section_ids if sid not in seen]
    if missing:
        errors.append(f"以下小节未被任何章或取舍报告覆盖：{', '.join(missing)}")

    hooks = spine.get("hook_candidates") or []
    if not hooks:
        errors.append("没有开场钩子候选")
    elif not 0 <= int(spine.get("selected_hook", 0) or 0) < len(hooks):
        errors.append("所选开场钩子越界")

    if digests is not None:
        quotable = {norm_key(q) for d in digests for q in d.get("quotable") or []}
        for q in spine.get("golden_quotes") or []:
            if norm_key(q) not in quotable:
                errors.append(f"金句不是摘要中登记的原文逐字片段：{q[:30]}")

    if len(section_ids) >= MIN_PARTS and not (MIN_PARTS <= len(parts) <= MAX_PARTS):
        warnings.append(f"章数为 {len(parts)}，设计期望 {MIN_PARTS}～{MAX_PARTS} 章")
    return {"errors": errors, "warnings": warnings}
