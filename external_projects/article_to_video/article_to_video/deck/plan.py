"""确定性的幻灯片规划（第一阶段）：把一章的文章块组织成幻灯片。

规则：
  * 标题块开始新的一页；章节开头没有标题时，页标题取章名；
  * 列表 → 列表元素；流程 → 流程元素；公式 → 公式元素；表格 → 表格元素；
    单行引语 → 金句元素；多行引语与段落 → 要点元素（段落按句子切分）；
  * 一页的条目数超过 MAX_SLIDE_ITEMS 时续页（标题加“（续）”）；单个元素超过 MAX_ELEMENT_ITEMS 时拆分；
  * 每页的预计讲解秒数按字数估算（SPEECH_CHARS_PER_SEC），用于控制页面时长。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

DECK_VERSION = "deck-rules-v1"
MAX_SLIDE_ITEMS = 10          # 一页的条目上限（超过则续页）
MAX_ELEMENT_ITEMS = MAX_SLIDE_ITEMS   # 一个元素的条目上限（与一页上限一致，超过则拆分到多个元素）
SPEECH_CHARS_PER_SEC = 4.2    # 讲解语速估算（字/秒）
PAGE_OVERHEAD_SEC = 2.0       # 每页的固定停顿
MAX_PAGE_SEC = 60.0           # 单页讲解时长上限（超过则续页）
MAX_PAGE_CHARS = int((MAX_PAGE_SEC - PAGE_OVERHEAD_SEC) * SPEECH_CHARS_PER_SEC)
_SENT = re.compile(r"[^。！？；!?;]+[。！？；!?;]?")


def _text(b: Dict[str, Any]) -> str:
    return re.sub(r"[*`]+", "", str(b.get("plain") or b.get("text") or "")).strip()


def _sentences(text: str) -> List[str]:
    out = [m.group(0).strip() for m in _SENT.finditer(text)]
    return [x for x in out if x]


def _chunks(seq: List[str], n: int) -> List[List[str]]:
    return [seq[i:i + n] for i in range(0, len(seq), n)] or []


def elements_of(b: Dict[str, Any]) -> List[Dict[str, Any]]:
    """一个文章块转换成若干页面元素（标题与无内容的块返回空列表）。"""
    kind = b.get("kind")
    bid = b.get("id")
    if kind in ("heading", "rule"):
        return []
    if kind == "list":
        items = [str(x).strip() for x in b.get("items") or [] if str(x).strip()]
        return [{"kind": "list", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(items, MAX_ELEMENT_ITEMS)]
    if kind == "table":
        header = [str(h) for h in b.get("header") or []]
        rows = [[str(c) for c in r] for r in b.get("rows") or []]
        if not rows:
            return []
        return [{"kind": "table", "header": header, "rows": c, "items": [], "emphasis": [], "source": bid}
                for c in _chunks(rows, MAX_SLIDE_ITEMS)]           # 表格超过一页时按行拆分，每段保留表头
    if kind == "code":
        shape = b.get("shape")
        nodes = [str(x).strip() for x in b.get("nodes") or [] if str(x).strip()]
        if shape == "chain" and nodes:
            return [{"kind": "diagram", "items": nodes, "loop": bool(b.get("loop")), "emphasis": [], "source": bid}]
        if b.get("hint") == "formula":
            text = str((b.get("items") or [b.get("content", "")])[0]).strip()
            return [{"kind": "formula", "text": text, "items": [], "emphasis": [], "source": bid}] if text else []
        items = [str(x).strip() for x in (b.get("items") or nodes) if str(x).strip()]
        if not items:
            items = [ln.strip() for ln in str(b.get("content", "")).splitlines() if ln.strip()]
        return [{"kind": "keypoint", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(items, MAX_ELEMENT_ITEMS)]
    if kind == "quote":
        lines = [ln.strip().strip("“”\"「」").strip() for ln in _text(b).split("\n") if ln.strip()]
        if len(lines) == 1:
            return [{"kind": "quote", "items": lines, "emphasis": [], "source": bid}]
        return [{"kind": "keypoint", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(lines, MAX_ELEMENT_ITEMS)] if lines else []
    if kind == "paragraph":
        sents = _sentences(_text(b))
        return [{"kind": "keypoint", "items": c, "emphasis": [], "source": bid}
                for c in _chunks(sents, MAX_ELEMENT_ITEMS)] if sents else []
    return []


def element_count(el: Dict[str, Any]) -> int:
    """元素在页面上占用的条目数（表格按行数，公式记为 1）。"""
    if el["kind"] == "table":
        return len(el.get("rows") or [])
    if el["kind"] == "formula":
        return 1
    return len(el.get("items", []))


def _fallback(b: Dict[str, Any]) -> List[Dict[str, Any]]:
    """块没能转换成任何元素，但原文中有内容：回退为要点，保证原文内容不会因此丢失。"""
    if b.get("kind") in ("heading", "rule"):
        return []
    items = [str(x).strip() for x in (b.get("items") or b.get("nodes") or []) if str(x).strip()]
    if not items:
        items = _sentences(_text(b))
    if not items:
        items = [ln.strip() for ln in str(b.get("content", "")).splitlines() if ln.strip()]
    return [{"kind": "keypoint", "items": c, "emphasis": [], "source": b.get("id")}
            for c in _chunks(items, MAX_ELEMENT_ITEMS)]


def _element_chars(el: Dict[str, Any]) -> int:
    if el["kind"] == "table":
        return sum(len(c) for c in el.get("header", [])) + sum(len(c) for r in el.get("rows", []) for c in r)
    if el["kind"] == "formula":
        return len(el.get("text", ""))
    return sum(len(x) for x in el.get("items", []))


def _slide(sid: str, title: str, sections: List[str]) -> Dict[str, Any]:
    return {"id": sid, "title": title, "section_ids": [], "source_blocks": [], "elements": [], "chars": 0,
            "est_sec": 0.0, "_sections": sections}


def _finish(s: Dict[str, Any]) -> Dict[str, Any]:
    s["section_ids"] = list(dict.fromkeys(s.pop("_sections")))
    s["chars"] = len(s["title"]) + sum(_element_chars(e) for e in s["elements"])
    s["est_sec"] = round(s["chars"] / SPEECH_CHARS_PER_SEC + PAGE_OVERHEAD_SEC, 1)
    return s


def plan_slides(part: Dict[str, Any], blocks: List[Dict[str, Any]], part_id: str) -> Dict[str, Any]:
    """按章节规划幻灯片。blocks 为本章的文章块（已按原文顺序）。"""
    slides: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    cur_items = 0
    cur_chars = 0
    page_title = part.get("title", "")
    base_title = page_title

    def new_slide(title: str, sec: str) -> Dict[str, Any]:
        sid = f"{part_id}.S{len(slides) + 1:02d}"
        s = _slide(sid, title, [sec] if sec else [])
        slides.append(s)
        return s

    for b in blocks:
        sec = b.get("section")
        if b.get("kind") == "heading":
            title = _text(b) or base_title
            if b.get("level", 2) <= 2 or cur is None:
                base_title = title
            cur = new_slide(title, sec)
            cur_items = 0
            cur_chars = 0
            cur["source_blocks"].append(b.get("id"))
            continue
        els = elements_of(b) or _fallback(b)
        if not els:
            continue
        if cur is None:
            cur = new_slide(base_title, sec)
            cur_items = 0
            cur_chars = 0
        for el in els:
            n = element_count(el)
            c = _element_chars(el)
            too_many = cur_items + n > MAX_SLIDE_ITEMS
            too_long = cur_chars + c > MAX_PAGE_CHARS
            if cur["elements"] and (too_many or too_long):
                cur = new_slide(f"{base_title}（续）", sec)
                cur_items = 0
                cur_chars = 0
            cur["elements"].append(el)
            cur["source_blocks"].append(b.get("id"))
            cur["_sections"].append(sec)
            cur_items += n
            cur_chars += c
    finished = [_finish(s) for s in slides if s["elements"]]
    for i, s in enumerate(finished, 1):
        s["id"] = f"{part_id}.S{i:02d}"
    total_sec = round(sum(s["est_sec"] for s in finished), 1)
    return {"schema": 1, "part": part_id, "title": part.get("title", ""), "mode": "deck",
            "slides": finished, "total_est_sec": total_sec, "version": DECK_VERSION}
