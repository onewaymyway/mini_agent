"""visual/director_llm.py — S4 分镜（模型版）：由模型为每段选择画面类型并写出短条目。

边界（与规则版一致的“不编造”原则）：
  * 模型只决定：画面类型、短条目（keypoint / illustration / formula 解释）、强调词、插图主题。
  * 结构内容一律取自原文：流程节点与回路、表格、公式、列表；金句取原文逐字句。
  * 模型给出的条目中，数字与英文词必须出现在本段口播中（否则要求重写）。
  * 输出先经 V4/V5 校验；不合格则带问题清单重试，最多 MAX_ATTEMPTS 次；仍不合格抛 PlanError，不写结果。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import cancel, joblog
from ..planning.errors import PlanError
from ..planning.jsonio import parse_json_object
from ..validators.visual import validate_storyboard
from .director import (MAX_DIAGRAM_NODES, MAX_KEYPOINT_ITEMS, MAX_LIST_ITEMS, MAX_TABLE_COLS,
                       MAX_TABLE_ROWS, TEXT_LIMIT, _emphasis, _first, _refs_blocks, _scene,
                       _text_of, _year_like, plain)
from .sharing import allocate_shared_images, count_groups
from .states import uniform_reveal

MAX_ATTEMPTS = 3
LLM_PROMPT_VERSION = "s4-llm-v1"
SYSTEM = ("你是讲解视频的视觉导演。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
          "只使用本段口播中已有的词，不得添加原文没有的数字、年份或英文词。")
_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9\-\.]*")
MAX_SUBJECT_CHARS = 24
LLM_ITEM_KINDS = ("keypoint", "illustration", "formula")


def beat_context(beat: Dict[str, Any], blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    refs_blocks = _refs_blocks(beat.get("source_refs") or [], blocks)
    chain = _first(refs_blocks, lambda b: b["kind"] == "code" and b.get("shape") == "chain")
    table = _first(refs_blocks, lambda b: b["kind"] == "table" and b.get("rows"))
    math = _first(refs_blocks, lambda b: b["kind"] == "math" and b.get("content"))
    lst = _first(refs_blocks, lambda b: b["kind"] == "list" and b.get("items"))
    allowed = ["keypoint", "illustration"]
    notes: List[str] = []
    if any(s.get("quote") for s in beat["sentences"]):
        allowed.append("quote")
    if chain and len(chain.get("nodes") or []) >= 2:
        allowed.append("diagram")
        arrow = " → ".join(str(n) for n in chain["nodes"][:MAX_DIAGRAM_NODES])
        notes.append(f"原文流程：{arrow}" + ("（首尾相接，为回路）" if chain.get("loop") else ""))
    if table:
        allowed.append("table")
        notes.append("原文表格：" + " | ".join(plain(h) for h in table.get("header") or []))
    if math:
        allowed.append("formula")
        notes.append("原文公式：" + str(math["content"]).replace("\n", " "))
    if lst:
        items = [_text_of(x) for x in lst["items"]][:MAX_LIST_ITEMS]
        allowed.append("timeline" if _year_like(items) else "list")
        notes.append("原文列表：" + "；".join(items))
    return {"allowed": allowed, "chain": chain, "table": table, "math": math, "lst": lst,
            "notes": notes, "refs": list(beat.get("source_refs") or [])}


def _structure_kinds(ctxs: Dict[str, Dict[str, Any]]) -> List[str]:
    out: List[str] = []
    for c in ctxs.values():
        if c["chain"] and "chain" not in out:
            out.append("chain")
        if c["table"] and "table" not in out:
            out.append("table")
        if c["math"] and "math" not in out:
            out.append("math")
        if c["lst"] and "list" not in out:
            out.append("list")
    return out


def build_prompt(part: Dict[str, Any], script: Dict[str, Any], ctxs: Dict[str, Dict[str, Any]],
                 *, allowance: int, shots: int, issues: List[str]) -> str:
    lines = [
        "【分镜任务】",
        f"PART_ID={script['part']}",
        f"章标题：{part['title']}",
        f"图片额度：最多生成 {allowance} 张图，每张图可用于 {shots} 个镜头（插图镜头总数不超过 {allowance * shots}）。",
        f"单条文字不超过 {TEXT_LIMIT} 字，每段条目最多 {MAX_KEYPOINT_ITEMS} 条。",
        "相邻三段不得使用同一画面类型；尽量让画面类型有变化。",
        "",
        "各段内容（按顺序）：",
    ]
    for beat in script["beats"]:
        c = ctxs[beat["id"]]
        lines.append(f"[{beat['id']}] 角色：{beat['role']}")
        for s in beat["sentences"]:
            tag = "（金句）" if s.get("quote") else ""
            lines.append(f"  · {s['text']}{tag}")
        for n in c["notes"]:
            lines.append(f"  {n}")
        lines.append(f"  可选画面类型：{'、'.join(c['allowed'])}")
    schema = {"scenes": [{"beat": "P01.b01", "type": "keypoint", "items": ["短条目"],
                          "emphasis": ["句中的词"], "image_subject": "插图的画面意象（不写文字）",
                          "formula_explanation": ["公式解释（仅 formula 类型）"]}]}
    lines += ["", "请为每一段输出一个场景，按段落顺序，输出如下结构的 JSON：",
              json.dumps(schema, ensure_ascii=False, indent=2),
              "要求：type 只能从该段的可选画面类型中选择；keypoint 与 illustration 的 items 用本段口播中的词组成短语；"
              "image_subject 只描述画面意象，不得要求出现文字、标题、字幕或标志。"]
    if issues:
        lines += ["", "【上次输出的问题，必须修正后重新输出完整 JSON】"] + [f"- {x}" for x in issues]
    return "\n".join(lines)


def _tokens_missing(text: str, source: str) -> List[str]:
    return [t for t in _TOKEN.findall(text) if t not in source]


def _build_scene(beat: Dict[str, Any], item: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    bid = beat["id"]
    errs: List[str] = []
    kind = str(item.get("type") or "")
    if kind not in ctx["allowed"]:
        return None, [f"{bid}：画面类型 {kind!r} 不适用于本段（可选：{'、'.join(ctx['allowed'])}）"]
    sents = beat["sentences"]
    source = "".join(s["text"] for s in sents)
    sc = _scene(beat, kind, ctx["refs"])
    if kind == "diagram":
        sc["items"] = [plain(n) for n in ctx["chain"]["nodes"][:MAX_DIAGRAM_NODES]]
        sc["loop"] = bool(ctx["chain"].get("loop"))
    elif kind == "table":
        t = ctx["table"]
        sc["header"] = [plain(h)[:30] for h in (t.get("header") or [])][:MAX_TABLE_COLS]
        sc["rows"] = [[plain(c)[:30] for c in (r or [])][:MAX_TABLE_COLS] for r in t["rows"]][:MAX_TABLE_ROWS]
        sc["items"] = ["、".join(r) for r in sc["rows"]]
    elif kind == "quote":
        q = next(i for i, s in enumerate(sents) if s.get("quote"))
        sc["items"] = [sents[q]["text"][:TEXT_LIMIT]]
        sc["reveal"] = [{"step": 0, "at_sentence": q}]
        return sc, []
    elif kind in ("list", "timeline"):
        items = [_text_of(x) for x in ctx["lst"]["items"]][:MAX_LIST_ITEMS]
        sc["items"] = [i[:TEXT_LIMIT] for i in items]
        sc["type"] = "timeline" if _year_like(items) else "list"
    else:  # keypoint / illustration / formula：条目由模型给出，但必须可追溯到本段
        if kind == "formula":
            sc["formula"] = re.sub(r"\$+", "", str(ctx["math"]["content"])).replace("\n", " ").strip()[:120]
            raw_items = item.get("formula_explanation") or []
        else:
            raw_items = item.get("items") or []
        items = [str(x).strip() for x in raw_items if str(x).strip()][:MAX_KEYPOINT_ITEMS]
        if not items and kind != "formula":
            errs.append(f"{bid}：{kind} 缺少条目")
        for it in items:
            if len(it) > TEXT_LIMIT:
                errs.append(f"{bid}：条目 {len(it)} 字，超过 {TEXT_LIMIT} 字")
            missing = _tokens_missing(it, source)
            if missing:
                errs.append(f"{bid}：条目「{it[:20]}」含原文没有的词「{missing[0]}」，请只用本段口播中的词")
        sc["items"] = items
        if kind == "illustration":
            subject = str(item.get("image_subject") or "").strip()[:MAX_SUBJECT_CHARS]
            emph = _emphasis(sents)
            sc["image"] = {"subject": subject or (emph[0] if emph else (items[0][:MAX_SUBJECT_CHARS] if items else bid)),
                           "focus_x": 0.5, "version": 0, "file": None}
    if kind != "quote":
        emph_in = [str(e) for e in item.get("emphasis") or [] if str(e) and str(e) in source]
        sc["emphasis"] = list(dict.fromkeys(emph_in))[:8] or _emphasis(sents)
        sc["reveal"] = uniform_reveal(sc, len(sents))
    return (sc if not errs else None), errs


def parse_scenes(obj: Dict[str, Any], script: Dict[str, Any], ctxs: Dict[str, Dict[str, Any]]) \
        -> Tuple[List[Dict[str, Any]], List[str]]:
    raw = obj.get("scenes") if isinstance(obj, dict) else None
    if not isinstance(raw, list):
        return [], ["缺少 scenes 数组"]
    by_beat: Dict[str, Dict[str, Any]] = {}
    errs: List[str] = []
    for item in raw:
        if not isinstance(item, dict):
            errs.append("scenes 中存在非对象条目")
            continue
        bid = str(item.get("beat") or "")
        if bid in by_beat:
            errs.append(f"{bid} 重复出现")
        by_beat[bid] = item
    ids = [b["id"] for b in script["beats"]]
    extra = sorted(set(by_beat) - set(ids))
    if extra:
        errs.append("出现了不存在的段落：" + "、".join(extra[:5]))
    scenes: List[Dict[str, Any]] = []
    for beat in script["beats"]:
        item = by_beat.get(beat["id"])
        if item is None:
            errs.append(f"缺少段落 {beat['id']} 的画面")
            continue
        sc, e = _build_scene(beat, item, ctxs[beat["id"]])
        errs += e
        if sc is not None:
            scenes.append(sc)
    return scenes, errs


def direct_part_llm(script: Dict[str, Any], blocks: List[Dict[str, Any]], *, part: Dict[str, Any],
                    chapter: Dict[str, str], allowance: int, shots_per_image: int, llm,
                    images_enabled: bool, style_anchor: str) -> Dict[str, Any]:
    ctxs = {b["id"]: beat_context(b, blocks) for b in script["beats"]}
    shots = max(1, int(shots_per_image))
    issues: List[str] = []
    raw = ""
    for _attempt in range(MAX_ATTEMPTS):
        cancel.check()
        prompt = build_prompt(part, script, ctxs, allowance=allowance, shots=shots, issues=issues)
        joblog.bound_log(f"{part['id']}：调用模型设计分镜（第 {_attempt + 1} 次）")
        raw = llm.ask(prompt, system=SYSTEM)
        cancel.check()
        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            issues = [f"JSON 解析失败：{exc}"]
            joblog.bound_log(f"{part['id']}：分镜输出不是有效 JSON，将重试", level="warn")
            continue
        scenes, errs = parse_scenes(obj, script, ctxs)
        if errs:
            issues = errs[:12]
            joblog.bound_log(f"{part['id']}：分镜未通过校验（{errs[0]}），将重试", level="warn")
            continue
        joblog.bound_log(f"{part['id']}：模型分镜通过校验", level="ok")
        allocate_shared_images(scenes, shots)
        sb = {
            "schema": 1, "part": script["part"], "title": script["title"], "chapter": chapter,
            "scenes": scenes, "source_structure": _structure_kinds(ctxs),
            "image_allowance": allowance, "shots_per_image": shots,
            "images_planned": count_groups(scenes), "warnings": [],
            "director": LLM_PROMPT_VERSION, "engine": "llm",
        }
        v = validate_storyboard(sb, script, allowance=allowance, style_anchor=style_anchor,
                                images_enabled=images_enabled, shots_per_image=shots)
        if v["errors"]:
            issues = v["errors"][:12]
            continue
        sb["warnings"] = v["warnings"]
        return sb
    raise PlanError(f"{script['part']} 分镜模型输出 {MAX_ATTEMPTS} 次仍不合格：" + "；".join(issues[:6]), raw=raw)
