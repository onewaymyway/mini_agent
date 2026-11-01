"""steps/chapter_shots.py — C-3 大场景/C-4 小场景规划（合并为镜头规划）。

LLM 给出镜头与口播块；代码负责：
  * 对白必须能在剧本中找到（防改写）；
  * 地点/角色 id 校验，非法 id 回落到第一个地点 / 忽略；
  * 单个口播块超过 MAX_BLOCK_CHARS 时按标点切分；
  * 估算时长超过 SHOT_PLAN_SEC 时拆成多个镜头（保持场次不变）；
  * 说话人不在库中的对白记为 speaker_name，等待 C-5 补建。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List

from .. import prompts
from ..chapter_io import (MAX_BLOCK_CHARS, SHOT_PLAN_SEC, chapter_dir, load_entities, load_shots,
                          mark_stage, norm, save_shots, sha12)
from ..providers.text import TextLLM
from ..providers.tts import estimate_seconds
from .chapter_script import parse_script
from .global_visual import StepError, parse_json_object

_SPLIT = re.compile(r"(?<=[。！？；!?;，,])")


def split_text(text: str, limit: int = MAX_BLOCK_CHARS) -> List[str]:
    if len(re.sub(r"\s", "", text)) <= limit:
        return [text]
    pieces, buf = [], ""
    for part in _SPLIT.split(text):
        if not part:
            continue
        if len(re.sub(r"\s", "", buf + part)) > limit and buf:
            pieces.append(buf)
            buf = part
        else:
            buf += part
    if buf:
        pieces.append(buf)
    out: List[str] = []
    for p in pieces:  # 没有标点可切时硬切
        while len(re.sub(r"\s", "", p)) > limit:
            out.append(p[:limit])
            p = p[limit:]
        if p:
            out.append(p)
    return out


def _block_seconds(block: Dict[str, Any]) -> float:
    return estimate_seconds(block["text"]) + 0.2


def _group_into_shots(blocks: List[Dict[str, Any]], base: Dict[str, Any]) -> List[Dict[str, Any]]:
    shots: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []
    cur_sec = 0.0
    for b in blocks:
        sec = _block_seconds(b)
        if cur and cur_sec + sec > SHOT_PLAN_SEC:
            shots.append({**base, "blocks": cur})
            cur, cur_sec = [], 0.0
        cur.append(b)
        cur_sec += sec
    if cur:
        shots.append({**base, "blocks": cur})
    return shots


def validate_and_build(raw_shots: List[Dict[str, Any]], script: str, locations: List[Dict[str, Any]],
                       characters: List[Dict[str, Any]], chapter_no: int) -> Dict[str, Any]:
    if not raw_shots:
        raise StepError("LLM 没有返回任何镜头")
    loc_ids = {l["id"] for l in locations}
    char_by_id = {c["id"]: c for c in characters}
    char_by_name = {n: c["id"] for c in characters for n in c.get("names", [])}
    script_norm = norm(script)
    new_speakers: List[str] = []
    shots: List[Dict[str, Any]] = []
    scenes: Dict[int, Dict[str, Any]] = {}

    for rs in raw_shots:
        scene_no = int(rs.get("scene_no") or 1)
        loc = rs.get("location_id")
        if loc not in loc_ids:
            loc = locations[0]["id"] if locations else None
        blocks: List[Dict[str, Any]] = []
        for rb in rs.get("blocks") or []:
            btype = rb.get("type")
            text = str(rb.get("text") or "").strip()
            if btype not in ("narration", "dialogue") or not text:
                raise StepError(f"镜头口播块非法（type={btype}）")
            if btype == "dialogue":
                if norm(text) not in script_norm:
                    raise StepError(f"对白未在剧本中找到（疑似改写）：{text[:30]}")
                sid = rb.get("speaker_id")
                name = (rb.get("speaker_name") or "").strip() or None
                if sid not in char_by_id:
                    sid = char_by_name.get(name) if name else None
                if sid is None and not name:
                    raise StepError(f"对白缺少说话人：{text[:30]}")
                if sid is None:
                    if name not in new_speakers:
                        new_speakers.append(name)
                for piece in split_text(text):
                    blocks.append({"type": "dialogue", "text": piece, "speaker_id": sid,
                                   "speaker_name": None if sid else name})
            else:
                for piece in split_text(text):
                    blocks.append({"type": "narration", "text": piece, "speaker_id": None, "speaker_name": None})
        if not blocks:
            continue
        cast = []
        for b in blocks:
            if b["speaker_id"] and b["speaker_id"] not in cast:
                cast.append(b["speaker_id"])
        for cid in rs.get("character_ids") or []:
            if cid in char_by_id and cid not in cast:
                cast.append(cid)
        base = {"scene_no": scene_no, "location_id": loc, "character_ids": cast,
                "visual_hint_zh": str(rs.get("visual_hint_zh") or "").strip(),
                "visual_hint_en": str(rs.get("visual_hint_en") or "").strip() or "cinematic medium shot, no text"}
        scenes.setdefault(scene_no, {"scene_no": scene_no, "location_id": loc})
        shots.extend(_group_into_shots(blocks, base))

    for i, sh in enumerate(shots, start=1):
        sh["id"] = f"s{i:02d}"
        sh["est_seconds"] = round(sum(_block_seconds(b) for b in sh["blocks"]), 2)
        sh["duration_sec"] = None
        sh["audio_key"] = None
        sh["prompt_en"] = None
        sh["prompt_hash"] = None
        sh["clip_key"] = None
        sh["consistency"] = None
        for j, b in enumerate(sh["blocks"]):
            b["idx"] = j
    return {
        "chapter_no": chapter_no,
        "scenes": list(scenes.values()),
        "shots": shots,
        "new_speakers": new_speakers,
    }


def run_shots(project_dir: Path, no: int, text_llm: TextLLM, *, force: bool = False) -> Dict[str, Any]:
    cdir = chapter_dir(project_dir, no)
    script_path = cdir / "script.md"
    if not script_path.is_file():
        raise StepError("缺少剧本，请先执行 C-1/C-2")
    script = script_path.read_text(encoding="utf-8")
    script_hash = sha12(script)
    existing = load_shots(project_dir, no)
    if not force and existing and existing.get("script_hash") == script_hash and existing.get("shots"):
        mark_stage(project_dir, no, "shots", "done")
        return {"skipped": True, "shots": len(existing["shots"])}

    ents = load_entities(project_dir)
    characters = ents["characters"]["characters"]
    locations = ents["locations"]["locations"]
    raw = text_llm.ask(prompts.shots_prompt(script, characters, locations), system=prompts.SYSTEM_VISUAL)
    obj = parse_json_object(raw)
    built = validate_and_build(obj.get("shots") or [], script, locations, characters, no)
    built["script_hash"] = script_hash
    save_shots(project_dir, no, built)
    mark_stage(project_dir, no, "shots", "done")
    return {"skipped": False, "shots": len(built["shots"]), "new_speakers": built["new_speakers"]}
