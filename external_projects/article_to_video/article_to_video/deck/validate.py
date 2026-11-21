"""幻灯片规划的校验（第一阶段）：结构、条目上限、内容须出自原文、原文内容块须全部被覆盖。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..validators.fidelity import norm_key
from .plan import MAX_ELEMENT_ITEMS, MAX_SLIDE_ITEMS, _text, element_count

PAGE_WARN_SEC = 60.0   # 单页讲解超过这个秒数给出警告（建议拆页）
ELEMENT_KINDS = ("keypoint", "list", "quote", "diagram", "formula", "table")


def _source_text(blocks_by_id: Dict[str, Dict[str, Any]], ids: List[str]) -> str:
    parts = []
    for bid in ids:
        b = blocks_by_id.get(bid)
        if not b:
            continue
        parts.append(_text(b))
        parts.extend(str(x) for x in b.get("items") or [])
        parts.extend(str(x) for x in b.get("nodes") or [])
        parts.append(str(b.get("content", "")))
        if b.get("kind") == "table":
            parts.extend(str(c) for c in b.get("header") or [])
            parts.extend(str(c) for r in b.get("rows") or [] for c in r)
    return norm_key(" ".join(parts))


def validate_deck(deck: Dict[str, Any], blocks: List[Dict[str, Any]], *, page_cap: int = None,
                  element_cap: int = None) -> Dict[str, List[str]]:
    """校验规划。page_cap / element_cap 默认取规划结果中记录的分页上限。"""
    page_cap = page_cap or deck.get("page_cap") or MAX_SLIDE_ITEMS
    element_cap = element_cap or deck.get("element_cap") or MAX_ELEMENT_ITEMS
    errors: List[str] = []
    warnings: List[str] = []
    by_id = {b.get("id"): b for b in blocks}
    slides = deck.get("slides") or []
    if not slides:
        errors.append("没有任何幻灯片")
    ids = [s.get("id") for s in slides]
    if len(set(ids)) != len(ids):
        errors.append("幻灯片编号重复")
    used: set = set()
    for s in slides:
        sid = s.get("id", "?")
        if not str(s.get("title", "")).strip():
            errors.append(f"{sid}：缺少标题")
        els = s.get("elements") or []
        if not els:
            errors.append(f"{sid}：没有任何元素")
        # 分页标准：页面不能只有标题或前言；前言不能留在页尾（关键内容必须与前言在同一页）
        if els and all(e.get("lead_in") for e in els):
            errors.append(f"{sid}：只有标题或前言，没有实际内容")
        elif els and els[-1].get("lead_in"):
            errors.append(f"{sid}：前言留在页尾，关键内容被分到了下一页")
        n_items = 0
        for el in els:
            k = el.get("kind")
            if k not in ELEMENT_KINDS:
                errors.append(f"{sid}：未知元素类型 {k}")
                continue
            if k == "formula":
                if not str(el.get("text", "")).strip():
                    errors.append(f"{sid}：公式为空")
                n_items += element_count(el)
                continue
            if k == "table":
                if not el.get("header") or not el.get("rows"):
                    errors.append(f"{sid}：表格缺少表头或行")
                n_items += element_count(el)
                continue
            items = el.get("items") or []
            if not items:
                errors.append(f"{sid}：{k} 元素没有条目")
            if len(items) > element_cap:
                errors.append(f"{sid}：{k} 元素 {len(items)} 条，超过 {element_cap}")
            n_items += len(items)
        if n_items > page_cap:
            errors.append(f"{sid}：条目 {n_items} 个，超过一页上限 {page_cap}")
        for bid in s.get("source_blocks") or []:
            used.add(bid)
        # 内容须出自原文：文字条目必须能在本页引用的原文块中找到
        src = _source_text(by_id, s.get("source_blocks") or [])
        for el in els:
            texts = [el.get("text", "")] if el.get("kind") == "formula" else list(el.get("items") or [])
            for t in texts:
                if t and norm_key(str(t)) and norm_key(str(t)) not in src:
                    errors.append(f"{sid}：「{str(t)[:16]}」不在原文中")
        if float(s.get("est_sec", 0)) > PAGE_WARN_SEC:
            warnings.append(f"{sid}：讲解约 {s['est_sec']:.0f} 秒，超过 {PAGE_WARN_SEC:.0f} 秒，建议拆页")
    # 覆盖：原文中有内容的块都应被某一页承载
    content_ids = [b.get("id") for b in blocks if b.get("kind") not in ("heading", "rule")
                   and (_text(b) or b.get("items") or b.get("rows") or b.get("nodes") or b.get("content"))]
    missing = [bid for bid in content_ids if bid not in used]
    if missing:
        errors.append(f"原文内容未被任何幻灯片承载：{', '.join(missing[:8])}" + (" 等" if len(missing) > 8 else ""))
    return {"errors": errors, "warnings": warnings}
