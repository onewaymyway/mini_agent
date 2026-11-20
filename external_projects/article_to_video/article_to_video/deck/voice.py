"""新方案第三阶段：每页讲稿的配音与时间线。

* 每页讲稿按句切分，形成与口播稿同形的“口播结构”，交给现有的句级配音（缓存、续跑、引擎记录完全相同）；
* 页与页之间的停顿由页末停顿决定；页的停留时长 = 该页讲稿的真实配音时长 + 页末停顿；
* 页内元素的出现时刻：按元素的关键词在讲稿中的位置定位到句子，再按句内位置换算成真实时刻；
  找不到关键词的元素按字数占比分配；时刻单调不减。
* 结果保存为 parts/<章>/deck_timeline.json（与现有方案的 timeline.json 分开）。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..audio.synth import synthesize_script
from ..planning.errors import PlanError
from ..project_store import now_iso, read_json, write_json_atomic
from .plan import element_count
from .script import load_script, script_status

PAGE_PAUSE_MS = 500
_SPLIT = re.compile(r"[^。！？!?；;]+[。！？!?；;]?")


def deck_root(pdir: Path, part_id: str) -> Path:
    """新方案的产物目录：parts/<章>/deck/（配音文件也在其中，与现有方案的 audio/ 分开）。"""
    return Path(pdir) / "parts" / part_id / "deck"


def timeline_path(pdir: Path, part_id: str) -> Path:
    return Path(pdir) / "parts" / part_id / "deck_timeline.json"


def load_deck_timeline(pdir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = timeline_path(pdir, part_id)
    return read_json(p) if p.is_file() else None


def split_sentences(text: str) -> List[str]:
    return [m.group(0) for m in _SPLIT.finditer(text) if m.group(0).strip()]


def pages_as_script(part_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """把每页讲稿转换为与口播稿同形的结构（beats = 页，sentences = 页内句子）。"""
    beats = []
    for page in doc["slides"]:
        sents = split_sentences(page["narration"])
        beats.append({"id": page["id"], "role": "claim", "source_refs": [], "pace": "normal",
                      "pause_after_ms": PAGE_PAUSE_MS,
                      "sentences": [{"text": t, "tts": None, "emphasis": [], "quote": False} for t in sents]})
    return {"schema": 1, "part": part_id, "title": doc.get("title", part_id), "beats": beats, "confirmed": True}


def _anchor(el: Dict[str, Any]) -> str:
    if el.get("kind") == "formula":
        return str(el.get("text", ""))
    if el.get("kind") == "table":
        return str((el.get("header") or [""])[0])
    items = el.get("items") or []
    return str(items[0]) if items else ""


def _elements_char_share(elements: List[Dict[str, Any]]) -> List[int]:
    return [max(1, sum(len(str(x)) for x in (e.get("items") or [])) or len(str(e.get("text", "")))) for e in elements]


def page_reveals(page: Dict[str, Any], sentence_times: List[Tuple[float, float]], sentence_texts: List[str]) -> List[Dict[str, Any]]:
    """页内各元素的出现时刻（秒，绝对时间）。sentence_times 与 sentence_texts 一一对应，且按顺序拼接成讲稿。"""
    narration = "".join(sentence_texts)
    offsets, acc = [], 0
    for t in sentence_texts:
        offsets.append(acc)
        acc += len(t)
    total_chars = max(1, len(narration))
    shares = _elements_char_share(page.get("elements") or [])
    total_share = max(1, sum(shares))
    before = 0
    out: List[Dict[str, Any]] = []
    prev_t = sentence_times[0][0] if sentence_times else 0.0

    def time_at(pos: int) -> float:
        if not sentence_texts:
            return prev_t
        j = 0
        for k in range(len(offsets)):
            if offsets[k] <= pos:
                j = k
        s, e = sentence_times[j]
        length = max(1, len(sentence_texts[j]))
        frac = min(1.0, max(0.0, (pos - offsets[j]) / length))
        return s + (e - s) * frac

    for idx, el in enumerate(page.get("elements") or []):
        anchor = _anchor(el)
        pos = narration.find(anchor) if anchor else -1
        if pos < 0:
            pos = int(total_chars * before / total_share)
        at = max(prev_t, time_at(min(pos, total_chars - 1)))
        out.append({"element": idx, "at_sec": round(at, 3)})
        prev_t = at
        before += shares[idx]
    return out


def synthesize_deck(pdir: Path, part_id: str, tts, *, force: bool = False,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """为本章每页讲稿配音，生成 deck_timeline.json。需要讲稿已确认且未过期。"""
    pdir = Path(pdir)
    doc = load_script(pdir, part_id)
    if doc is None:
        raise PlanError(f"{part_id} 还没有幻灯片讲稿")
    st = script_status(pdir, part_id)
    if not doc.get("confirmed"):
        raise PlanError(f"请先确认 {part_id} 的幻灯片讲稿，再配音")
    if st["stale"]:
        raise PlanError(f"{part_id} 的讲稿已过期，请先重新生成并确认")
    pseudo = pages_as_script(part_id, doc)
    tl = synthesize_script(pdir, part_id, pseudo, tts, force=force, audio_root=deck_root(pdir, part_id))
    by_page: Dict[str, List[Dict[str, Any]]] = {}
    for it in tl["sentences"]:
        by_page.setdefault(it["beat"], []).append(it)

    pages_out = []
    for page in doc["slides"]:
        items = by_page.get(page["id"], [])
        if not items:
            continue
        sents = [it["text"] for it in items]
        times = [(it["start"], it["end"]) for it in items]
        sentence_texts = split_sentences(page["narration"])
        # 讲稿切分与配音切分一致（同一函数），此处做一次保护性核对
        if [x for x in sentence_texts] != sents:
            raise PlanError(f"{page['id']}：讲稿切句与配音不一致，请重新生成讲稿")
        start = items[0]["start"]
        last = items[-1]
        end = last["end"] + last["gap_after"]
        pages_out.append({
            "id": page["id"], "title": page.get("title", ""), "start": round(start, 3),
            "end": round(end, 3), "dur": round(end - start, 3),
            "sentences": [{"id": it["id"], "text": it["text"], "start": it["start"], "end": it["end"]}
                          for it in items],
            "reveals": page_reveals(page, times, sents),
        })
    doc_out = {"schema": 1, "part": part_id, "lead_in_sec": tl["lead_in_sec"], "tail_sec": tl["tail_sec"],
               "total_sec": tl["total_sec"], "pages": pages_out, "script_sha": doc.get("input_sha"),
               "warnings": tl.get("warnings", []), "generated_at": now_iso(now)}
    write_json_atomic(timeline_path(pdir, part_id), doc_out)
    return doc_out


def voice_status(pdir: Path, part_id: str) -> Dict[str, Any]:
    tl = load_deck_timeline(Path(pdir), part_id)
    doc = load_script(Path(pdir), part_id)
    if tl is None:
        return {"exists": False, "stale": False, "total_sec": 0.0}
    stale = doc is None or tl.get("script_sha") != doc.get("input_sha")
    return {"exists": True, "stale": stale, "total_sec": tl.get("total_sec", 0.0), "pages": len(tl.get("pages", []))}
