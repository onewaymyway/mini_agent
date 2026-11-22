"""deck/autofix.py — 幻灯片规划的确定性修复（R42）。

能用代码机械修好的问题不交给模型，模型只处理需要判断的内容。依次执行：
  1. 删除原文中找不到的条目（编造内容）；删除没有条目的列表/要点/引语元素；
  2. 把原文中有内容却未被任何页承载的块，挂到同一节的页上（没有同节页则挂到前一页）；
  3. 页尾的前言挪到下一页开头（前言不能留在页尾）；
  4. 只有前言或没有任何元素的页，并入相邻页（不留空页）；
  5. 超过条目上限或元素上限的页，按元素拆成续页（标题加“（续）”）；
  6. 重新编号（P06.S01…），并重算每页的字数与讲解时长。
修复只作用于校验结果中有问题的规划；修复后仍需通过 validate_deck 才算合格。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

from ..validators.fidelity import norm_key
from .plan import (MAX_ELEMENT_ITEMS, MAX_PAGE_CHARS, MAX_PAGE_ELEMENTS, PAGE_OVERHEAD_SEC,
                   SPEECH_CHARS_PER_SEC, _element_chars, element_count, elements_of)
from .validate import _source_text, content_block_ids

TEXT_KINDS = ("keypoint", "list", "quote")
CONT = "（续）"


def _refresh(s: Dict[str, Any]) -> Dict[str, Any]:
    s["chars"] = len(s["title"]) + sum(_element_chars(e) for e in s["elements"])
    s["est_sec"] = round(s["chars"] / SPEECH_CHARS_PER_SEC + PAGE_OVERHEAD_SEC, 1)
    return s


def _only_lead(s: Dict[str, Any]) -> bool:
    return not s["elements"] or all(e.get("lead_in") for e in s["elements"])


def _drop_fabricated(s: Dict[str, Any], by_id: Dict[str, Dict[str, Any]], block_src: Dict[str, str],
                     stats: Dict[str, int]) -> None:
    """条目必须能在原文中找到。本页来源块里找不到、但在整章原文中能找到的，补上对应的来源块（归属纠正）；
    整章原文中都找不到的，才视为编造并删除；删除因此变空的文字元素。"""
    src = _source_text(by_id, s.get("source_blocks") or [])
    kept = []
    for el in s["elements"]:
        if el.get("kind") in TEXT_KINDS and el.get("items"):
            ok = []
            for t in el["items"]:
                key = norm_key(str(t))
                if not key or key in src:
                    ok.append(t)
                    continue
                owner = next((bid for bid, text in block_src.items() if key in text), None)
                if owner is not None:
                    s["source_blocks"] = list(dict.fromkeys(list(s.get("source_blocks") or []) + [owner]))
                    src = src + block_src[owner]
                    ok.append(t)
                else:
                    stats["fabricated"] += 1
            if not ok:
                continue
            el = {**el, "items": ok}
        if el.get("kind") in TEXT_KINDS and not el.get("items"):
            continue                       # 没有条目的列表/要点：没有内容，删除
        kept.append(el)
    s["elements"] = kept


def _ensure_sources(slides: List[Dict[str, Any]], block_src: Dict[str, str],
                    by_id: Dict[str, Dict[str, Any]]) -> None:
    """页面的每个文字条目都必须出自本页的来源块。移动元素（前言挪页、合并页）后，补上来源块。"""
    for s in slides:
        src = _source_text(by_id, s.get("source_blocks") or [])
        for el in s["elements"]:
            if el.get("kind") not in TEXT_KINDS:
                continue
            for t in el.get("items") or []:
                key = norm_key(str(t))
                if key and key not in src:
                    owner = next((bid for bid, text in block_src.items() if key in text), None)
                    if owner is not None:
                        s["source_blocks"] = list(dict.fromkeys(list(s.get("source_blocks") or []) + [owner]))
                        src = src + block_src[owner]


def _attach_uncovered(slides: List[Dict[str, Any]], blocks: List[Dict[str, Any]],
                      element_cap: int, stats: Dict[str, int]) -> None:
    """把未被承载的内容块挂到同一节的页上；没有同节页则挂到前一个已承载的页。"""
    if not slides:
        return
    content = set(content_block_ids(blocks))
    pos: Dict[str, int] = {}
    for idx, s in enumerate(slides):
        for bid in s.get("source_blocks") or []:
            pos[bid] = idx
    last = 0
    for b in blocks:
        bid = b.get("id")
        if bid in pos:
            last = pos[bid]
            continue
        if bid not in content:
            continue
        sec = str(bid).split(".")[0]
        target = None
        for idx, s in enumerate(slides):
            if sec in (s.get("section_ids") or []):
                target = idx                # 取同一节的最后一页
        if target is None:
            target = last
        s = slides[target]
        s["elements"].extend(elements_of(b, cap=element_cap))
        s["source_blocks"] = list(s.get("source_blocks") or []) + [bid]
        pos[bid] = target
        last = target
        stats["attached"] += 1


def _move_trailing_leads(slides: List[Dict[str, Any]]) -> None:
    """页尾的前言挪到下一页开头；前言必须与它引出的内容在同一页。"""
    for i in range(len(slides) - 1):
        s, n = slides[i], slides[i + 1]
        tail = []
        while s["elements"] and s["elements"][-1].get("lead_in"):
            tail.insert(0, s["elements"].pop())
        if tail:
            n["elements"] = tail + n["elements"]


def _merge_thin(slides: List[Dict[str, Any]], stats: Dict[str, int]) -> List[Dict[str, Any]]:
    """只有前言或没有元素的页：并入后一页（没有后一页则并入前一页）。"""
    i = 0
    while i < len(slides):
        s = slides[i]
        if not _only_lead(s):
            i += 1
            continue
        if i + 1 < len(slides):
            n = slides[i + 1]
            n["elements"] = s["elements"] + n["elements"]
            target = n
        elif i > 0:                              # 没有后续内容：前言失去“引出”对象，改为普通条目
            target = slides[i - 1]
            moved = [{**e, "lead_in": False} for e in s["elements"]]
            target["elements"] = target["elements"] + moved
        else:
            i += 1
            continue
        target["source_blocks"] = list(dict.fromkeys((s.get("source_blocks") or []) + (target.get("source_blocks") or [])))
        target["section_ids"] = list(dict.fromkeys((s.get("section_ids") or []) + (target.get("section_ids") or [])))
        slides.pop(i)
        stats["merged"] += 1
    return slides


def _pieces(elements: List[Dict[str, Any]], element_cap: int) -> List[Dict[str, Any]]:
    """元素条目超过元素上限的，先拆成多个元素（保留类型，前言只在第一段）。"""
    out = []
    for el in elements:
        items = el.get("items") or []
        if el.get("kind") in TEXT_KINDS and len(items) > element_cap:
            for j in range(0, len(items), element_cap):
                out.append({**el, "items": items[j:j + element_cap], "lead_in": bool(el.get("lead_in")) and j == 0})
        else:
            out.append(el)
    return out


def _groups(elements: List[Dict[str, Any]], page_cap: int, element_cap: int) -> List[List[Dict[str, Any]]]:
    groups: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    n = chars = 0
    for el in _pieces(elements, element_cap):
        k, c = element_count(el), _element_chars(el)
        if cur and (n + k > page_cap or len(cur) + 1 > MAX_PAGE_ELEMENTS or chars + c > MAX_PAGE_CHARS):
            groups.append(cur)
            cur, n, chars = [], 0, 0
        cur.append(el)
        n += k
        chars += c
    if cur:
        groups.append(cur)
    for a, b in zip(groups, groups[1:]):          # 组尾不能是前言
        while a and a[-1].get("lead_in"):
            b.insert(0, a.pop())
    return [g for g in groups if g]


def _split_oversize(slides: List[Dict[str, Any]], page_cap: int, element_cap: int,
                    stats: Dict[str, int]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for s in slides:
        groups = _groups(s["elements"], page_cap, element_cap)
        if len(groups) <= 1:
            s["elements"] = groups[0] if groups else []
            out.append(s)
            continue
        for k, g in enumerate(groups):
            title = s["title"] if k == 0 else (s["title"] if s["title"].endswith(CONT) else s["title"] + CONT)
            out.append({**s, "title": title, "elements": g,
                        "source_blocks": list(s.get("source_blocks") or []),
                        "section_ids": list(s.get("section_ids") or [])})
        stats["split"] += len(groups) - 1
    return out


def autofix_deck(deck: Dict[str, Any], blocks: List[Dict[str, Any]], part_id: str, *,
                 page_cap: int, element_cap: int = MAX_ELEMENT_ITEMS) -> Tuple[Dict[str, Any], List[str]]:
    """对规划做确定性修复，返回 (新规划, 修复说明)。不修改传入的 deck。"""
    d = copy.deepcopy(deck)
    by_id = {b.get("id"): b for b in blocks}
    stats = {"fabricated": 0, "attached": 0, "merged": 0, "split": 0}
    slides = d.get("slides") or []
    block_src = {bid: norm_key(_source_text(by_id, [bid])) for bid in by_id if bid}
    for s in slides:
        _drop_fabricated(s, by_id, block_src, stats)
    _attach_uncovered(slides, blocks, element_cap, stats)
    _move_trailing_leads(slides)
    slides = _merge_thin(slides, stats)
    slides = _split_oversize(slides, page_cap, element_cap, stats)
    _ensure_sources(slides, block_src, by_id)
    for i, s in enumerate(slides, 1):
        s["id"] = f"{part_id}.S{i:02d}"
        _refresh(s)
    d["slides"] = slides
    d["page_cap"], d["element_cap"] = page_cap, element_cap
    d["total_est_sec"] = round(sum(s["est_sec"] for s in slides), 1)
    d["autofixed"] = True
    notes = []
    if stats["fabricated"]:
        notes.append(f"删除 {stats['fabricated']} 个原文中没有的条目")
    if stats["attached"]:
        notes.append(f"挂接 {stats['attached']} 个未被承载的原文块")
    if stats["merged"]:
        notes.append(f"并入 {stats['merged']} 个只有前言或为空的页")
    if stats["split"]:
        notes.append(f"拆出 {stats['split']} 个续页")
    return d, notes
