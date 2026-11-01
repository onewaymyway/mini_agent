"""steps/chapter_assets.py — C-5 素材补全与补建角色。

流程：
  1. 镜头中出现了角色库没有的说话人 → LLM 起草一条最小设定（auto_added），写入 characters.json；
  2. 等待人工通过文本设定（停止点：waiting_assets）；
  3. 对已通过文本的补建角色生成定妆图（G-3 同一套流程）；
  4. 等待人工通过定妆图（停止点）。
C-8 之前，镜头引用到的所有角色/地点都必须有定妆图文件，否则硬性拦截。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .. import joblog
from .. import llm_trace
from .. import prompts
from ..chapter_io import chapter_dir, load_entities, load_shots, mark_stage, save_shots, source_text
from ..review import entity_key, get_item, load_review, save_review
from ..providers.image import ImageProvider
from ..providers.text import TextLLM
from .global_visual import StepError, parse_json_object, portrait_pass, save_entities_ids  # noqa: F401

AGE_VALUES = {"child", "teen", "youth", "middle_aged", "elderly", "unknown"}
GENDER_VALUES = {"male", "female", "unknown"}


def _next_char_id(chars: List[Dict[str, Any]]) -> str:
    nums = [int(c["id"].split("_")[1]) for c in chars if c["id"].startswith("char_")
            and c["id"].split("_")[1].isdigit()]
    return f"char_{(max(nums) + 1 if nums else 1):03d}"


def _speaker_context(script: str, name: str) -> List[str]:
    out = []
    for line in script.splitlines():
        if line.startswith(f"{name}："):
            out.append(line[len(name) + 1:])
    return out


def _draft_entity(project_dir: Path, text_llm: TextLLM, name: str, context: List[str], chapter_no: int, eid: str) -> Dict[str, Any]:
    label = f"第 {chapter_no} 章 C-5 起草补建角色「{name}」设定"
    with joblog.timed(project_dir, "chapter", label):
        raw = llm_trace.ask_traced(project_dir, text_llm, prompts.entity_draft_prompt(name, context),
                                   system=prompts.SYSTEM_VISUAL, scope="chapter", label=label,
                                   step="C-5", chapter=chapter_no)
    obj = parse_json_object(raw)
    anchor = str(obj.get("visual_anchor_en") or "").strip()
    voice = str(obj.get("voice_profile") or "").strip()
    if not anchor or not voice:
        raise StepError(f"补建角色“{name}”的设定缺少 visual_anchor_en 或 voice_profile")
    gender = str(obj.get("gender") or "unknown").lower()
    age = str(obj.get("age_range") or "unknown").lower()
    return {
        "id": eid, "names": [name], "role": "auto_added", "age": None,
        "age_range": age if age in AGE_VALUES and age != "unknown" else None,
        "gender": gender if gender in GENDER_VALUES and gender != "unknown" else None,
        "description_zh": str(obj.get("description_zh") or "").strip(),
        "speech_pattern": None, "first_appear": chapter_no, "relations": [],
        "visual_anchor_en": anchor, "visual_anchor_source": text_llm.name,
        "voice_profile": voice, "voice_profile_source": text_llm.name,
        "asset_path": None, "appearance_variants": [], "persona_card": None,
        "character_file": None, "source": "auto_added", "auto_added_chapter": chapter_no,
    }


def run_assets(project_dir: Path, no: int, text_llm: TextLLM, image: ImageProvider) -> Dict[str, Any]:
    shots = load_shots(project_dir, no)
    if not shots or not shots.get("shots"):
        raise StepError("缺少镜头规划，请先执行 C-4")
    ents = load_entities(project_dir)
    chars_doc = ents["characters"]
    chars = chars_doc["characters"]
    script = (chapter_dir(project_dir, no) / "script.md").read_text(encoding="utf-8")

    # 1) 起草缺失的说话人
    by_name = {n: c["id"] for c in chars for n in c.get("names", [])}
    names_needed: List[str] = []
    for sh in shots["shots"]:
        for b in sh["blocks"]:
            if b["type"] == "dialogue" and not b.get("speaker_id") and b.get("speaker_name"):
                if b["speaker_name"] not in names_needed:
                    names_needed.append(b["speaker_name"])
    for n in shots.get("new_speakers") or []:
        if n not in names_needed and n not in by_name:
            names_needed.append(n)

    drafted: List[str] = []
    review = load_review(project_dir)
    for name in names_needed:
        if name in by_name:
            continue
        eid = _next_char_id(chars)
        ent = _draft_entity(project_dir, text_llm, name, _speaker_context(script, name), no, eid)
        chars.append(ent)
        by_name[name] = eid
        get_item(review, entity_key("character", eid)).update(text_status="pending", portrait_status="missing")
        drafted.append(eid)
    if drafted:
        save_entities_ids(project_dir, chars_doc)
        save_review(project_dir, review)

    # 2) 回填镜头中的说话人 id
    changed = False
    for sh in shots["shots"]:
        for b in sh["blocks"]:
            if b["type"] == "dialogue" and not b.get("speaker_id") and b.get("speaker_name") in by_name:
                b["speaker_id"] = by_name[b["speaker_name"]]
                b["speaker_name"] = None
                changed = True
        cast = [b["speaker_id"] for b in sh["blocks"] if b.get("speaker_id")]
        for cid in sh.get("character_ids") or []:
            if cid not in cast:
                cast.append(cid)
        if sh.get("character_ids") != cast:
            sh["character_ids"] = cast
            changed = True
    if changed:
        save_shots(project_dir, no, shots)

    # 3) 本章补建角色：等待文本设定通过 → 生成定妆图 → 等待定妆图通过
    review = load_review(project_dir)
    mine = [c for c in chars if c.get("auto_added_chapter") == no]
    waiting: List[str] = []
    need_portrait: List[str] = []
    for c in mine:
        item = get_item(review, entity_key("character", c["id"]))
        if item["text_status"] != "approved":
            waiting.append(f"{c['id']} {c['names'][0]}：等待确认文本设定")
        elif not c.get("asset_path"):
            need_portrait.append(c["id"])
    if need_portrait:
        summary = portrait_pass(project_dir, image, ids=need_portrait)
        if summary.get("stopped"):
            raise StepError(summary["stopped"])
        chars = load_entities(project_dir)["characters"]["characters"]
        review = load_review(project_dir)
        mine = [c for c in chars if c.get("auto_added_chapter") == no]
        for c in mine:
            if c["id"] in need_portrait and not c.get("asset_path"):
                waiting.append(f"{c['id']}：定妆图生成失败，请查看全书素材页")
    for c in mine:
        item = get_item(review, entity_key("character", c["id"]))
        if c.get("asset_path") and item["portrait_status"] != "approved":
            waiting.append(f"{c['id']} {c['names'][0]}：等待确认定妆图")

    mark_stage(project_dir, no, "assets", "waiting" if waiting else "done")
    return {"drafted": drafted, "waiting": waiting}


def missing_portraits(project_dir: Path, no: int) -> List[str]:
    """C-8 前检查：镜头引用的角色/地点是否都有定妆图文件。"""
    shots = load_shots(project_dir, no)
    ents = load_entities(project_dir)
    chars = {c["id"]: c for c in ents["characters"]["characters"]}
    locs = {l["id"]: l for l in ents["locations"]["locations"]}
    missing = []
    for sh in shots["shots"]:
        for cid in sh.get("character_ids") or []:
            ent = chars.get(cid)
            if not ent or not ent.get("asset_path") or not (project_dir / ent["asset_path"]).is_file():
                missing.append(f"角色 {cid}")
        ent = locs.get(sh.get("location_id"))
        if ent and (not ent.get("asset_path") or not (project_dir / ent["asset_path"]).is_file()):
            missing.append(f"地点 {ent['id']}")
    return sorted(set(missing))
