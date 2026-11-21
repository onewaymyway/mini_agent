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

from .. import cancel, joblog
from ..planning.errors import PlanError
from ..planning.jsonio import parse_json_object
from ..project_store import now_iso, read_json, write_json_atomic
from .plan import PAGE_OVERHEAD_SEC, SPEECH_CHARS_PER_SEC
from .store import deck_root, load_deck

SCRIPT_VERSION = "deck-script-v1"
BATCH_PAGES = 8
MAX_ATTEMPTS = 3              # 人工挡位（G0/G1）每批的最多尝试次数
AUTO_ATTEMPTS = 30            # 全自动挡位（G2/G3）：持续修改直到通过（安全上限，可随时停止）
TOL_LOW, TOL_HIGH = 0.6, 1.4
SYSTEM = ("你是讲解视频的解说撰稿人。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
          "只使用给定页面的内容与原文中已有的词，不得添加原文没有的数字、年份或专有名词。")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def script_path(pdir: Path, part_id: str) -> Path:
    return deck_root(pdir, part_id) / "deck_script.json"


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


def _bounds(slide: Dict[str, Any]) -> Tuple[int, int, int]:
    tc = target_chars(slide)
    return tc, int(tc * TOL_LOW), int(tc * TOL_HIGH)


def slide_problems(text: str, slide: Dict[str, Any]) -> List[str]:
    """单页讲稿的问题（具体到字数与数字），空列表表示合格。"""
    tc, low, high = _bounds(slide)
    problems = []
    n = len(text)
    if not text:
        problems.append("缺少讲稿")
        return problems
    if n < low:
        problems.append(f"讲稿只有 {n} 字，太短（目标约 {tc} 字，需要 {low}–{high} 字）")
    elif n > high:
        problems.append(f"讲稿有 {n} 字，太长（目标约 {tc} 字，需要 {low}–{high} 字）")
    src = page_source(slide)
    bad = [x for x in _NUM.findall(text) if x not in src]
    if bad:
        problems.append("出现了页面与原文中都没有的数字：" + "、".join(bad[:4]))
    return problems


def _fix_hint(slide: Dict[str, Any], problems: List[str]) -> str:
    tc, low, high = _bounds(slide)
    hints = []
    for pr in problems:
        if "太短" in pr:
            hints.append(f"扩写：补充本页要点的解释与衔接，使总字数落在 {low}–{high} 字之间（目标约 {tc} 字）。")
        elif "太长" in pr:
            hints.append(f"压缩：删去重复与次要的表述，使总字数落在 {low}–{high} 字之间（目标约 {tc} 字）。")
        elif "数字" in pr:
            hints.append("删除那些数字，或只使用页面内容中出现过的数字。")
        elif "缺少" in pr:
            hints.append("请为这一页输出讲稿。")
    return " ".join(dict.fromkeys(hints))


def build_prompt(part_title: str, batch: List[Dict[str, Any]],
                 feedback: Optional[Dict[str, List[str]]] = None) -> str:
    lines = [f"章标题：{part_title}", "请为下列每一页写一段口播讲稿（像在讲一页 PPT，口吻清楚自然）。",
             "规则：",
             "1. 每页讲稿围绕本页的要点与流程展开，讲清楚这一页要传达的意思；不要只念标题；",
             "2. 字数必须落在“允许字数”区间内（目标字数只是参考）；",
             "3. 只使用本页内容与原文已有的数字、词语，不得新增数字、年份或人名；",
             "4. 不要提及“下一页”“这张幻灯片”之类的界面词。",
             "", "页面（按顺序）："]
    for s in batch:
        tc, low, high = _bounds(s)
        lines.append(f"[{s['id']}] 标题：{s.get('title', '')}；目标字数：{tc}；允许字数：{low}–{high}")
        for el in s.get("elements") or []:
            lines += ["  " + x for x in _element_lines(el)]
        if feedback and s["id"] in feedback:
            lines.append(f"  【上一次输出的问题，必须修正】{'；'.join(feedback[s['id']])}")
            hint = _fix_hint(s, feedback[s["id"]])
            if hint:
                lines.append(f"  【修改要求】{hint}")
    lines += ["", "输出 JSON（每一页都要输出，包括没有问题的页）：",
              json.dumps({"slides": [{"id": batch[0]["id"], "narration": "讲稿文字"}]}, ensure_ascii=False)]
    return "\n".join(lines)


def check_batch(obj: Any, batch: List[Dict[str, Any]]) -> Tuple[Dict[str, str], List[str]]:
    """逐页校验。返回 (合格的页 {id: 讲稿}, 全部问题的文字列表)。"""
    got = {}
    for item in (obj.get("slides") if isinstance(obj, dict) else None) or []:
        if isinstance(item, dict) and item.get("id"):
            got[str(item["id"])] = str(item.get("narration") or "").strip()
    ok: Dict[str, str] = {}
    errors: List[str] = []
    for s in batch:
        text = got.get(s["id"], "")
        problems = slide_problems(text, s)
        if problems:
            errors += [f"{s['id']}：{pr}" for pr in problems]
        else:
            ok[s["id"]] = text
    return ok, errors


def _sha(deck_sha: str, llm_key: str) -> str:
    raw = json.dumps({"deck": deck_sha, "version": SCRIPT_VERSION, "llm": llm_key}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def plan_script(pdir: Path, part_id: str, *, llm, part_title: str = "", force: bool = False,
                now: Optional[datetime] = None, max_attempts: int = AUTO_ATTEMPTS) -> Dict[str, Any]:
    """为本章每一页生成讲稿。需要幻灯片规划已确认。输入未变时复用。

    不合格的页会带着具体问题（字数、数字）反馈给模型，只重做这些页；合格的页保留。
    max_attempts 为每批的最多尝试次数（全自动挡位应足够大，持续修改直到通过）。
    """
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
        batch_no = i // BATCH_PAGES + 1
        pending = list(slides[i:i + BATCH_PAGES])
        feedback: Dict[str, List[str]] = {}
        attempt = 0
        while pending:
            attempt += 1
            if attempt > max_attempts:
                msgs = [f"{sid}：{m}" for sid, ms in feedback.items() for m in ms]
                raise PlanError(f"{part_id} 讲稿批次 {batch_no} 经 {max_attempts} 次仍不合格：" + "；".join(msgs[:6]))
            cancel.check()
            prompt = build_prompt(title, pending, feedback)
            raw = llm.ask(prompt, system=SYSTEM)
            try:
                obj = parse_json_object(raw)
            except ValueError as exc:
                joblog.bound_log(f"{part_id} 讲稿批次 {batch_no} 第 {attempt} 次：输出不是有效 JSON（{exc}），重做",
                                 level="warn")
                feedback = {s["id"]: ["输出不是有效的 JSON，请只输出 JSON 对象"] for s in pending}
                continue
            ok, errs = check_batch(obj, pending)
            done.update(ok)
            if not errs:
                joblog.bound_log(f"{part_id} 讲稿批次 {batch_no}：第 {attempt} 次通过（{len(ok)} 页）")
                break
            joblog.bound_log(f"{part_id} 讲稿批次 {batch_no} 第 {attempt} 次：{len(errs)} 处问题，只重做这些页", level="warn")
            for e in errs:
                joblog.bound_log(f"    · {e}", level="warn")
            feedback = {}
            for e in errs:
                sid, _, msg = e.partition("：")
                feedback.setdefault(sid, []).append(msg)
            pending = [s for s in pending if s["id"] in feedback]
    out_slides = []
    for s in slides:
        text = done[s["id"]]
        out_slides.append({"id": s["id"], "title": s.get("title", ""), "narration": text,
                           "target_chars": target_chars(s), "chars": len(text)})
    doc = {"schema": 1, "part": part_id, "version": SCRIPT_VERSION, "deck_sha": deck["input_sha"],
           "input_sha": sha, "slides": out_slides, "total_chars": sum(x["chars"] for x in out_slides),
           "confirmed": False, "confirmed_at": None, "generated_at": now_iso(now)}
    write_json_atomic(script_path(pdir, part_id), doc)
    joblog.bound_log(f"{part_id}：幻灯片讲稿已生成（{len(out_slides)} 页，{doc['total_chars']} 字）", level="ok")
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
