"""新方案第二阶段：每一页的讲稿。

* 每页一段讲稿，字数按该页的预计讲解时长推算（允许 ±40%）；
* 讲稿只能使用本页的内容（标题、要点、流程、公式、表格）与原文中已有的数字，不得编造数字；
* 按批（每批若干页）调用文本模型，校验不合格则只重做该批，最多 MAX_ATTEMPTS 次；
* 结果保存为 parts/<章>/deck_script.json，依赖幻灯片规划（deck.json）的指纹，规划变化后自动过期。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import joblog
from ..planning.errors import PlanError
from ..planning.jsonio import parse_json_object
from ..project_store import now_iso, read_json, write_json_atomic
from .plan import PAGE_OVERHEAD_SEC, SPEECH_CHARS_PER_SEC
from .store import load_deck

SCRIPT_VERSION = "deck-script-v1"
BATCH_PAGES = 8
MAX_ATTEMPTS = 3
TOL_LOW, TOL_HIGH = 0.6, 1.4
SYSTEM = ("你是讲解视频的解说撰稿人。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
          "只使用给定页面的内容与原文中已有的词，不得添加原文没有的数字、年份或专有名词。")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def script_path(pdir: Path, part_id: str) -> Path:
    return Path(pdir) / "parts" / part_id / "deck_script.json"


def load_script(pdir: Path, part_id: str) -> Optional[Dict[str, Any]]:
    p = script_path(pdir, part_id)
    return read_json(p) if p.is_file() else None


def target_chars(slide: Dict[str, Any]) -> int:
    sec = max(4.0, float(slide.get("est_sec", 0)) - PAGE_OVERHEAD_SEC)
    return max(12, int(round(sec * SPEECH_CHARS_PER_SEC)))


def _element_lines(el: Dict[str, Any]) -> List[str]:
    k = el.get("kind")
    if k == "table":
        head = "、".join(el.get("header") or [])
        rows = ["、".join(r) for r in el.get("rows") or []]
        return [f"表格（{head}）："] + [f"· {r}" for r in rows]
    if k == "formula":
        return [f"公式：{el.get('text', '')}"]
    if k == "diagram":
        return ["流程：" + " → ".join(el.get("items") or [])]
    return [f"· {x}" for x in el.get("items") or []]


def page_source(slide: Dict[str, Any]) -> str:
    parts = [slide.get("title", "")]
    for el in slide.get("elements") or []:
        if el.get("kind") == "table":
            parts += list(el.get("header") or []) + [c for r in el.get("rows") or [] for c in r]
        elif el.get("kind") == "formula":
            parts.append(el.get("text", ""))
        else:
            parts += list(el.get("items") or [])
    return " ".join(str(x) for x in parts)


def build_prompt(part_title: str, batch: List[Dict[str, Any]]) -> str:
    lines = [f"章标题：{part_title}", "请为下列每一页写一段口播讲稿（像在讲一页 PPT，口吻清楚自然）。",
             "规则：",
             "1. 每页讲稿围绕本页的要点与流程展开，讲清楚这一页要传达的意思；不要只念标题；",
             "2. 字数接近“目标字数”（允许上下约 40%）；",
             "3. 只使用本页内容与原文已有的数字、词语，不得新增数字、年份或人名；",
             "4. 不要提及“下一页”“这张幻灯片”之类的界面词。",
             "", "页面（按顺序）："]
    for s in batch:
        lines.append(f"[{s['id']}] 标题：{s.get('title', '')}；目标字数：{target_chars(s)}")
        for el in s.get("elements") or []:
            lines += ["  " + x for x in _element_lines(el)]
    lines += ["", "输出 JSON：", json.dumps({"slides": [{"id": batch[0]["id"], "narration": "讲稿文字"}]},
                                              ensure_ascii=False)]
    return "\n".join(lines)


def check_batch(obj: Any, batch: List[Dict[str, Any]]) -> Tuple[Dict[str, str], List[str]]:
    errors: List[str] = []
    out: Dict[str, str] = {}
    got = {}
    for item in (obj.get("slides") if isinstance(obj, dict) else None) or []:
        if isinstance(item, dict) and item.get("id"):
            got[str(item["id"])] = str(item.get("narration") or "").strip()
    for s in batch:
        sid = s["id"]
        text = got.get(sid, "")
        if not text:
            errors.append(f"{sid}：缺少讲稿")
            continue
        tc = target_chars(s)
        n = len(text)
        if not (tc * TOL_LOW <= n <= tc * TOL_HIGH):
            errors.append(f"{sid}：讲稿 {n} 字，目标 {tc} 字（允许 {int(tc * TOL_LOW)}–{int(tc * TOL_HIGH)}）")
        src = page_source(s)
        bad = [x for x in _NUM.findall(text) if x not in src]
        if bad:
            errors.append(f"{sid}：讲稿出现页面与原文中没有的数字：{'、'.join(bad[:4])}")
        out[sid] = text
    return out, errors


def _sha(deck_sha: str, llm_key: str) -> str:
    raw = json.dumps({"deck": deck_sha, "version": SCRIPT_VERSION, "llm": llm_key}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def plan_script(pdir: Path, part_id: str, *, llm, part_title: str = "", force: bool = False,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    """为本章每一页生成讲稿。需要幻灯片规划已确认。输入未变时复用。"""
    pdir = Path(pdir)
    deck = load_deck(pdir, part_id)
    if deck is None:
        raise PlanError(f"{part_id} 还没有幻灯片规划")
    if not deck.get("confirmed"):
        raise PlanError(f"请先确认 {part_id} 的幻灯片规划，再生成讲稿")
    if llm is None:
        raise PlanError("生成讲稿需要文本模型（mini_agent 配置）")
    llm_key = getattr(llm, "model", None) or type(llm).__name__
    sha = _sha(deck["input_sha"], str(llm_key))
    existing = load_script(pdir, part_id)
    if existing and existing.get("input_sha") == sha and not force:
        joblog.bound_log(f"{part_id}：幻灯片讲稿输入未变，复用已有结果")
        return existing
    title = part_title or deck.get("title", part_id)
    slides = deck["slides"]
    done: Dict[str, str] = {}
    for i in range(0, len(slides), BATCH_PAGES):
        batch = slides[i:i + BATCH_PAGES]
        issues: List[str] = []
        ok = False
        for attempt in range(1, MAX_ATTEMPTS + 1):
            prompt = build_prompt(title, batch)
            if issues:
                prompt += "\n上一次输出的问题（请修正，只输出修正后的完整 JSON）：\n" + "\n".join(issues[:12])
            raw = llm.ask(prompt, system=SYSTEM)
            try:
                obj = parse_json_object(raw)
            except ValueError as exc:
                issues = [f"JSON 解析失败：{exc}"]
                joblog.bound_log(f"{part_id} 讲稿批次 {i // BATCH_PAGES + 1}：输出不是有效 JSON，重做", level="warn")
                continue
            got, errs = check_batch(obj, batch)
            if not errs:
                done.update(got)
                ok = True
                break
            issues = errs
            joblog.bound_log(f"{part_id} 讲稿批次 {i // BATCH_PAGES + 1}：校验发现 {len(errs)} 处问题，第 {attempt} 次重做",
                             level="warn")
        if not ok:
            raise PlanError(f"{part_id} 讲稿批次 {i // BATCH_PAGES + 1} 经 {MAX_ATTEMPTS} 次仍不合格：" + "；".join(issues[:4]))
    out_slides = []
    for s in slides:
        text = done[s["id"]]
        out_slides.append({"id": s["id"], "title": s.get("title", ""), "narration": text,
                           "target_chars": target_chars(s), "chars": len(text)})
    doc = {"schema": 1, "part": part_id, "version": SCRIPT_VERSION, "deck_sha": deck["input_sha"],
           "input_sha": sha, "slides": out_slides, "total_chars": sum(x["chars"] for x in out_slides),
           "confirmed": False, "confirmed_at": None, "generated_at": now_iso(now)}
    write_json_atomic(script_path(pdir, part_id), doc)
    joblog.bound_log(f"{part_id}：幻灯片讲稿已生成（{len(out_slides)} 页，{doc['total_chars']} 字）")
    return doc


def script_status(pdir: Path, part_id: str) -> Dict[str, Any]:
    doc = load_script(Path(pdir), part_id)
    deck = load_deck(Path(pdir), part_id)
    if doc is None:
        return {"exists": False, "stale": False, "confirmed": False, "pages": 0}
    stale = deck is None or deck.get("input_sha") != doc.get("deck_sha")
    return {"exists": True, "stale": stale, "confirmed": bool(doc.get("confirmed")),
            "pages": len(doc.get("slides") or []), "total_chars": doc.get("total_chars", 0)}


def confirm_script(pdir: Path, part_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    doc = load_script(Path(pdir), part_id)
    if doc is None:
        raise PlanError(f"{part_id} 还没有幻灯片讲稿")
    if script_status(pdir, part_id)["stale"]:
        raise PlanError(f"{part_id} 的讲稿已过期（幻灯片规划已变化），请先重新生成")
    doc["confirmed"] = True
    doc["confirmed_at"] = now_iso(now)
    write_json_atomic(script_path(Path(pdir), part_id), doc)
    return doc
