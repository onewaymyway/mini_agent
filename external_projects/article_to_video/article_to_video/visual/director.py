"""visual/director.py — S4 分镜（规则版）：把已确认的口播稿转成场景列表。

设计说明：本期 S4 先实现规则版（不调用模型），由原文结构决定画面形式：
  * 原文代码块中的链式流程/回路 → diagram（逐节点出现，回路画下方回线）
  * 表格 → table（逐行出现）；公式 → formula（先显示公式，再逐条解释）
  * 原文列表 → list（年份开头的列表 → timeline）
  * beat 中的金句 → quote；开场与其余 beat → keypoint（按 60 字拆成逐条出现）
  * 少量 beat 按插图预算改为 illustration（AI 生图，失败时降级为卡片）
同一章相邻三个 beat 不得同一类型（_break_runs 修正）。LLM 版分镜留待后续期。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .sharing import allocate_shared_images, count_groups
from .states import uniform_reveal

TEXT_LIMIT = 60          # 单张卡文字上限（字）
MAX_KEYPOINT_ITEMS = 4
MAX_LIST_ITEMS = 5
MAX_DIAGRAM_NODES = 6
MAX_TABLE_ROWS = 6
MAX_TABLE_COLS = 4
DIRECTOR_VERSION = "p4-rules-v1"

_CLAUSE = re.compile(r"[^，,；;。！？!?：:]+[，,；;。！？!?：:]?")
_YEAR = re.compile(r"^\s*(19|20)\d{2}")


def split_chunks(text: str, limit: int = TEXT_LIMIT) -> List[str]:
    """按子句贪心打包，每块不超过 limit 字；超长子句硬切。去掉块末标点。"""
    clauses = [c.strip() for c in _CLAUSE.findall(text or "") if c.strip()]
    chunks: List[str] = []
    cur = ""
    for c in clauses:
        while len(c) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(c[:limit])
            c = c[limit:]
        if not cur or len(cur) + len(c) <= limit:
            cur += c
        else:
            chunks.append(cur)
            cur = c
    if cur:
        chunks.append(cur)
    return [c.rstrip("，,；;。！？!?：:") for c in chunks if c.strip("，,；;。！？!?：: ")]


def _text_of(x: Any) -> str:
    if isinstance(x, dict):
        return str(x.get("text") or x.get("plain") or "")
    return str(x)


def _refs_blocks(refs: Sequence[str], blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    wanted = set(refs)
    return [b for b in blocks if b.get("id") in wanted or b.get("section") in wanted]


def _first(blocks: List[Dict[str, Any]], pred) -> Optional[Dict[str, Any]]:
    for b in blocks:
        if pred(b):
            return b
    return None


def _keypoint_items(sentences: List[Dict[str, Any]], limit: int = MAX_KEYPOINT_ITEMS) -> Tuple[List[str], List[int]]:
    items: List[str] = []
    sent_idx: List[int] = []
    for i, s in enumerate(sentences):
        if s.get("quote"):
            continue
        for c in split_chunks(s.get("text", "")):
            items.append(c)
            sent_idx.append(i)
            if len(items) >= limit:
                return items, sent_idx
    return items, sent_idx


def _emphasis(sentences: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for s in sentences:
        for e in s.get("emphasis") or []:
            if e and e not in seen and e in s.get("text", ""):
                seen.append(e)
    return seen[:8]


def _scene(beat: Dict[str, Any], kind: str, refs: List[str]) -> Dict[str, Any]:
    return {
        "id": beat["id"], "beat": beat["id"], "type": kind, "refs": refs,
        "items": [], "emphasis": _emphasis(beat["sentences"]), "header": [], "rows": [],
        "loop": False, "formula": "", "image": None, "reveal": [],
    }


def _year_like(items: List[str]) -> bool:
    return len(items) >= 3 and all(_YEAR.match(x) for x in items)


def _build_beat(beat: Dict[str, Any], idx: int, refs_blocks: List[Dict[str, Any]], *,
                illustrate: bool) -> Dict[str, Any]:
    sents = beat["sentences"]
    refs = list(beat.get("source_refs") or [])
    chain = _first(refs_blocks, lambda b: b["kind"] == "code" and b.get("shape") == "chain")
    table = _first(refs_blocks, lambda b: b["kind"] == "table")
    math = _first(refs_blocks, lambda b: b["kind"] == "math")
    lst = _first(refs_blocks, lambda b: b["kind"] == "list")
    has_quote = any(s.get("quote") for s in sents)
    has_plain = any(not s.get("quote") for s in sents)

    if chain and len(chain.get("nodes") or []) >= 2:
        sc = _scene(beat, "diagram", refs)
        nodes = [str(n) for n in chain["nodes"]][:MAX_DIAGRAM_NODES]
        sc["items"] = nodes
        sc["loop"] = bool(chain.get("loop"))
    elif table and table.get("rows"):
        sc = _scene(beat, "table", refs)
        header = [str(h) for h in table.get("header") or []][:MAX_TABLE_COLS]
        rows = [[str(c)[:30] for c in (r or [])][:MAX_TABLE_COLS] for r in table["rows"]][:MAX_TABLE_ROWS]
        sc["header"] = header
        sc["rows"] = rows
        sc["items"] = ["、".join(r) for r in rows]
    elif math and math.get("content"):
        sc = _scene(beat, "formula", refs)
        formula = re.sub(r"\$+", "", str(math["content"])).replace("\n", " ").strip()
        sc["formula"] = formula[:120]
        sc["items"], _ = _keypoint_items(sents, limit=3)
    elif lst and lst.get("items"):
        items = [_text_of(x) for x in lst["items"]][:MAX_LIST_ITEMS]
        sc = _scene(beat, "timeline" if _year_like(items) else "list", refs)
        sc["items"] = [i[:TEXT_LIMIT] for i in items]
    elif has_quote and not has_plain:
        sc = _scene(beat, "quote", refs)
        q = next(i for i, s in enumerate(sents) if s.get("quote"))
        sc["items"] = [sents[q]["text"][:TEXT_LIMIT]]
        sc["reveal"] = [{"step": 0, "at_sentence": q}]
        return sc
    else:
        kind = "illustration" if (illustrate and idx >= 1 and beat.get("role") not in ("hook", "summary")) \
            else "keypoint"
        sc = _scene(beat, kind, refs)
        items, sent_idx = _keypoint_items(sents)
        sc["items"] = items
        sc["reveal"] = [{"step": k, "at_sentence": s} for k, s in enumerate(sent_idx)]
        if kind == "illustration":
            subject = sc["emphasis"][0] if sc["emphasis"] else (items[0][:24] if items else beat["id"])
            sc["image"] = {"subject": subject, "focus_x": 0.5, "version": 0, "file": None}
        return sc

    if not sc.get("reveal"):
        sc["reveal"] = uniform_reveal(sc, len(sents))
    return sc


def _as_keypoint(sc: Dict[str, Any], sentences: List[Dict[str, Any]]) -> None:
    if sc["type"] == "table":
        items = ["、".join(r) for r in sc["rows"]]
    else:
        items = list(sc["items"])
    sc["type"] = "keypoint"
    sc["items"] = items[:MAX_KEYPOINT_ITEMS] or [sc["id"]]
    sc["header"], sc["rows"], sc["formula"], sc["loop"] = [], [], "", False
    sc["image"] = None
    sc["reveal"] = uniform_reveal(sc, len(sentences))


def _break_runs(scenes: List[Dict[str, Any]], beats: Dict[str, Dict[str, Any]]) -> List[str]:
    """相邻三个同类型时修正第三个：卡片类→列表或卡片，结构类→卡片。返回说明。"""
    notes: List[str] = []
    for i in range(2, len(scenes)):
        t = scenes[i]["type"]
        if t == scenes[i - 1]["type"] == scenes[i - 2]["type"] and t != "quote":
            sc = scenes[i]
            sentences = beats[sc["beat"]]["sentences"]
            if t in ("keypoint", "illustration") and len(sc["items"]) >= 2:
                sc["type"] = "list"
                sc["image"] = None
                sc["reveal"] = uniform_reveal(sc, len(sentences))
                notes.append(f"{sc['id']}：连续同类画面，改为列表")
            else:
                _as_keypoint(sc, sentences)
                notes.append(f"{sc['id']}：连续同类画面，改为卡片")
    return notes


def direct_part(script: Dict[str, Any], blocks: List[Dict[str, Any]], *, chapter: Dict[str, str],
                allowance: int, shots_per_image: int = 1) -> Dict[str, Any]:
    """为一章生成分镜（纯函数）。allowance 为本章可生成的图片数；每张图可用于 shots_per_image 个镜头。"""
    beats = {b["id"]: b for b in script["beats"]}
    candidates: List[int] = []
    for i, b in enumerate(script["beats"]):
        refs_blocks = _refs_blocks(b.get("source_refs") or [], blocks)
        structured = any(x["kind"] in ("table", "math", "list") or
                         (x["kind"] == "code" and x.get("shape") == "chain") for x in refs_blocks)
        if i >= 1 and i % 3 == 2 and not structured and b.get("role") not in ("hook", "summary") \
                and any(not s.get("quote") for s in b["sentences"]):
            candidates.append(i)
    chosen: List[int]
    cap = max(0, allowance) * max(1, shots_per_image)  # 一图多用：镜头数上限 = 图片额度 × 每图镜头数
    if cap <= 0 or not candidates:
        chosen = []
    elif len(candidates) <= cap:
        chosen = candidates
    else:
        step = len(candidates) / cap
        chosen = [candidates[int(k * step)] for k in range(cap)]

    scenes: List[Dict[str, Any]] = []
    structure: List[str] = []
    for i, b in enumerate(script["beats"]):
        refs_blocks = _refs_blocks(b.get("source_refs") or [], blocks)
        for x in refs_blocks:
            kind = "chain" if x["kind"] == "code" and x.get("shape") == "chain" else x["kind"]
            if kind in ("chain", "table", "math", "list") and kind not in structure:
                structure.append(kind)
        scenes.append(_build_beat(b, i, refs_blocks, illustrate=i in chosen))

    notes = _break_runs(scenes, beats)
    allocate_shared_images(scenes, shots_per_image)
    used_images = count_groups(scenes)
    return {
        "schema": 1,
        "part": script["part"],
        "title": script["title"],
        "chapter": chapter,
        "scenes": scenes,
        "source_structure": structure,
        "image_allowance": allowance,
        "shots_per_image": max(1, shots_per_image),
        "images_planned": used_images,
        "engine": "rules",
        "warnings": notes,
        "director": DIRECTOR_VERSION,
    }
