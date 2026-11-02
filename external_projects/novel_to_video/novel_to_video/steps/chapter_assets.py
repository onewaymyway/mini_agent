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

from .. import autogate
from .. import joblog
from .. import llm_trace
from ..project_store import write_json_atomic
from .. import prompts
from ..chapter_io import chapter_dir, load_entities, load_shots, mark_stage, save_shots, source_text
from ..review import entity_key, get_item, load_review, save_review
from ..providers.image import ImageProvider
from ..providers.text import TextLLM
from . import character_looks
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
        get_item(review, entity_key("character", eid)).update(
            text_status="approved" if autogate.is_auto(project_dir, "asset_text") else "pending",
            portrait_status="missing")
        drafted.append(eid)
    if drafted:
        save_entities_ids(project_dir, chars_doc)
        save_review(project_dir, review)

    # 1b) 草拟剧本中出现、但全局地点库没有的地点（补充到全局库，文本与定妆图确认后参与镜头）
    drafted_locs = _draft_missing_locations(project_dir, text_llm, shots, no)

    # 1c) 草拟剧本中出现、但全局道具库没有的道具（阶段 3）
    drafted_props = _draft_missing_props(project_dir, text_llm, shots, no)

    # 1d) 草拟镜头里需要、但角色还没有的形象（阶段 4，项目选项开启时才会有 new_looks）
    drafted_looks = character_looks.draft_missing_looks(project_dir, text_llm, shots, no)

    # 2) 回填镜头中的说话人 id
    changed = False
    for sh in shots["shots"]:
        for b in sh["blocks"]:
            if b["type"] == "dialogue" and not b.get("speaker_id") and b.get("speaker_name") in by_name:
                b["speaker_id"] = by_name[b["speaker_name"]]
                b["speaker_name"] = None
                changed = True
        cast: List[str] = []
        for b in sh["blocks"]:                       # 同一说话人多句台词只记一次（原先会写成 [A, B, A]）
            if b.get("speaker_id") and b["speaker_id"] not in cast:
                cast.append(b["speaker_id"])
        for cid in sh.get("character_ids") or []:
            if cid not in cast:
                cast.append(cid)
        if sh.get("character_ids") != cast:
            sh["character_ids"] = cast
            changed = True
    pending = [n for n in shots.get("new_speakers") or [] if n not in by_name]
    if pending != (shots.get("new_speakers") or []):
        shots["new_speakers"] = pending
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

    # 4) 本章草拟的地点：文本确认 → 定妆图生成 → 定妆图确认（与补建角色相同的门槛）
    waiting += _locations_waiting(project_dir, no, image)
    waiting += _props_waiting(project_dir, no, image)
    waiting += character_looks.looks_waiting(project_dir, no, image)

    mark_stage(project_dir, no, "assets", "waiting" if waiting else "done")
    return {"drafted": drafted, "drafted_locations": drafted_locs,
            "drafted_props": drafted_props,
            "drafted_looks": drafted_looks, "waiting": waiting}


def _next_loc_id(locs: List[Dict[str, Any]]) -> str:
    nums = [int(l["id"].split("_")[1]) for l in locs
            if l["id"].startswith("loc_") and l["id"].split("_")[1].isdigit()]
    return f"loc_{(max(nums) + 1 if nums else 1):02d}"


def _location_context(script: str, name: str) -> List[str]:
    out = []
    for line in script.splitlines():
        if name in line and line.strip():
            out.append(line.strip())
    return out[:6]


def _draft_missing_locations(project_dir: Path, text_llm: TextLLM, shots: Dict[str, Any], no: int) -> List[str]:
    """为 new_locations 中的地点起草设定，写入全局地点库，并把镜头的地点回填为新 id。"""
    names = [n for n in (shots.get("new_locations") or []) if n]
    if not names:
        return []
    ents = load_entities(project_dir)
    locs_doc = ents["locations"]
    locs = locs_doc["locations"]
    by_name = {}
    for l in locs:
        for n in [l.get("name")] + list(l.get("aliases") or []):
            if n:
                by_name[n] = l["id"]
    script = (chapter_dir(project_dir, no) / "script.md").read_text(encoding="utf-8")
    review = load_review(project_dir)
    drafted: List[str] = []
    for name in names:
        if name in by_name:
            continue
        eid = _next_loc_id(locs)
        label = f"第 {no} 章 C-5 起草缺失地点「{name}」设定"
        ent = {"id": eid, "name": name, "aliases": [], "location_type": None, "era_setting": None,
               "first_appear": no, "description_zh": "剧本中出现的地点。上下文：" + "；".join(_location_context(script, name)),
               "visual_anchor_en": None, "visual_anchor_source": None, "asset_path": None,
               "appearance_variants": [], "source": "auto_added", "auto_added_chapter": no}
        with joblog.timed(project_dir, "chapter", label):
            raw = llm_trace.ask_traced(project_dir, text_llm, prompts.location_prompt(ent),
                                       system=prompts.SYSTEM_VISUAL, scope="chapter", label=label,
                                       step="C-5", chapter=no)
        obj = parse_json_object(raw)
        anchor = str(obj.get("visual_anchor_en") or "").strip()
        if not anchor:
            raise StepError(f"地点「{name}」的草稿缺少 visual_anchor_en")
        ent["visual_anchor_en"] = anchor
        ent["visual_anchor_source"] = text_llm.name
        locs.append(ent)
        by_name[name] = eid
        get_item(review, entity_key("location", eid)).update(
            text_status="approved" if autogate.is_auto(project_dir, "asset_text") else "pending",
            portrait_status="missing")
        drafted.append(eid)
    if drafted:
        write_json_atomic(project_dir / "global" / "locations.json", locs_doc)
        save_review(project_dir, review)
    # 回填镜头：地点名 → 新 id；仍未解决的留在 new_locations 中
    for sh in shots.get("shots") or []:
        if sh.get("location_id") is None and sh.get("location_name") in by_name:
            sh["location_id"] = by_name[sh["location_name"]]
            sh["location_name"] = None
    shots["new_locations"] = [n for n in names if n not in by_name]
    save_shots(project_dir, no, shots)
    return drafted


def _entities_waiting(project_dir: Path, no: int, image: ImageProvider, kind: str, label: str) -> List[str]:
    """本章草拟的地点/道具：文本确认 → 定妆图生成 → 定妆图确认（与补建角色相同的门槛）。"""
    waiting: List[str] = []
    ents = load_entities(project_dir)
    rows = ents["locations"]["locations"] if kind == "location" else ents["props"]["props"]
    mine = [e for e in rows if e.get("auto_added_chapter") == no]
    if not mine:
        return waiting
    review = load_review(project_dir)
    need = []
    for e in mine:
        item = get_item(review, entity_key(kind, e["id"]))
        if item["text_status"] != "approved":
            waiting.append(f"{e['id']} {e.get('name')}：等待确认{label}设定")
        elif not e.get("asset_path"):
            need.append(e["id"])
    if need:
        summary = portrait_pass(project_dir, image, ids=need)
        if summary.get("stopped"):
            raise StepError(summary["stopped"])
        ents = load_entities(project_dir)
        rows = ents["locations"]["locations"] if kind == "location" else ents["props"]["props"]
        review = load_review(project_dir)
        for e in rows:
            if e["id"] in need and not e.get("asset_path"):
                waiting.append(f"{e['id']}：{label}定妆图生成失败，请查看全书素材页")
    for e in rows:
        if e.get("auto_added_chapter") != no:
            continue
        item = get_item(review, entity_key(kind, e["id"]))
        if e.get("asset_path") and item["portrait_status"] != "approved":
            waiting.append(f"{e['id']} {e.get('name')}：等待确认{label}定妆图")
    return waiting


def _locations_waiting(project_dir: Path, no: int, image: ImageProvider) -> List[str]:
    return _entities_waiting(project_dir, no, image, "location", "地点")


def _props_waiting(project_dir: Path, no: int, image: ImageProvider) -> List[str]:
    return _entities_waiting(project_dir, no, image, "prop", "道具")


def _next_prop_id(props: List[Dict[str, Any]]) -> str:
    nums = [int(p["id"].split("_")[1]) for p in props
            if p["id"].startswith("prop_") and p["id"].split("_")[1].isdigit()]
    return f"prop_{(max(nums) + 1 if nums else 1):02d}"


def _draft_missing_props(project_dir: Path, text_llm: TextLLM, shots: Dict[str, Any], no: int) -> List[str]:
    """为 new_props 中的道具起草设定，写入全局道具库，并把镜头的 prop_names 回填为 prop_ids。"""
    names = [n for n in (shots.get("new_props") or []) if n]
    if not names:
        return []
    ents = load_entities(project_dir)
    props_doc = ents["props"]
    props = props_doc["props"]
    by_name: Dict[str, str] = {}
    for pr in props:
        for n in [pr.get("name")] + list(pr.get("aliases") or []):
            if n:
                by_name[n] = pr["id"]
    script = (chapter_dir(project_dir, no) / "script.md").read_text(encoding="utf-8")
    review = load_review(project_dir)
    drafted: List[str] = []
    for name in names:
        if name in by_name:
            continue
        eid = _next_prop_id(props)
        label = f"第 {no} 章 C-5 起草缺失道具「{name}」设定"
        ent = {"id": eid, "name": name, "aliases": [], "first_appear": no,
               "description_zh": "剧本中出现的道具。上下文：" + "；".join(_location_context(script, name)),
               "visual_anchor_en": None, "visual_anchor_source": None, "asset_path": None,
               "source": "auto_added", "auto_added_chapter": no}
        with joblog.timed(project_dir, "chapter", label):
            raw = llm_trace.ask_traced(project_dir, text_llm, prompts.prop_prompt(ent),
                                       system=prompts.SYSTEM_VISUAL, scope="chapter", label=label,
                                       step="C-5", chapter=no)
        obj = parse_json_object(raw)
        anchor = str(obj.get("visual_anchor_en") or "").strip()
        if not anchor:
            raise StepError(f"道具「{name}」的草稿缺少 visual_anchor_en")
        ent["visual_anchor_en"] = anchor
        ent["visual_anchor_source"] = text_llm.name
        props.append(ent)
        by_name[name] = eid
        get_item(review, entity_key("prop", eid)).update(
            text_status="approved" if autogate.is_auto(project_dir, "asset_text") else "pending",
            portrait_status="missing")
        drafted.append(eid)
    if drafted:
        write_json_atomic(project_dir / "global" / "props.json", props_doc)
        save_review(project_dir, review)
    # 回填镜头：道具名 → 新 id；仍未解决的留在 new_props 中
    for sh in shots.get("shots") or []:
        ids = list(sh.get("prop_ids") or [])
        left = []
        for n in sh.get("prop_names") or []:
            if n in by_name:
                if by_name[n] not in ids:
                    ids.append(by_name[n])
            else:
                left.append(n)
        sh["prop_ids"], sh["prop_names"] = ids, left
    shots["new_props"] = [n for n in names if n not in by_name]
    save_shots(project_dir, no, shots)
    return drafted


def missing_portraits(project_dir: Path, no: int) -> List[str]:
    """C-8 前检查：镜头引用的角色/地点是否都有定妆图文件。"""
    shots = load_shots(project_dir, no)
    ents = load_entities(project_dir)
    chars = {c["id"]: c for c in ents["characters"]["characters"]}
    locs = {l["id"]: l for l in ents["locations"]["locations"]}
    props = {p["id"]: p for p in ents["props"]["props"]}
    missing = []
    for sh in shots["shots"]:
        for cid in sh.get("character_ids") or []:
            ent = chars.get(cid)
            if not ent or not ent.get("asset_path") or not (project_dir / ent["asset_path"]).is_file():
                missing.append(f"角色 {cid}")
        ent = locs.get(sh.get("location_id"))
        if ent and (not ent.get("asset_path") or not (project_dir / ent["asset_path"]).is_file()):
            missing.append(f"地点 {ent['id']}")
        for pid in sh.get("prop_ids") or []:
            ent = props.get(pid)
            if ent and (not ent.get("asset_path") or not (project_dir / ent["asset_path"]).is_file()):
                missing.append(f"道具 {pid}")
    missing += character_looks.missing_look_portraits(project_dir, shots)
    return sorted(set(missing))
