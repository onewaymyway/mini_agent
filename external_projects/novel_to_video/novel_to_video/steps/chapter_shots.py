"""steps/chapter_shots.py — C-3 大场景/C-4 小场景规划（合并为镜头规划）。

LLM 给出镜头与口播块；代码负责：
  * 对白必须能在剧本中找到（防改写）；
  * 地点/角色 id 校验，非法 id 回落到第一个地点 / 忽略；
  * 单个口播块超过 MAX_BLOCK_CHARS 时按标点切分；
  * 估算时长超过 SHOT_PLAN_SEC 时拆成多个镜头（保持场次不变）；
  * 说话人不在库中的对白记为 speaker_name，等待 C-5 补建。
"""

from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import location_spots as LS
from .character_looks import DEFAULT_LABELS as DEFAULT_LOOK_LABELS, find_look, looks_enabled

from .. import joblog
from .. import llm_trace
from .. import prompts
from ..chapter_io import (MAX_BLOCK_CHARS, SHOT_MAX_SEC, SHOT_MIN_SEC, SHOT_PLAN_SEC, chapter_dir, load_entities, load_shots,
                          mark_stage, norm, save_shots, sha12)
from ..providers.text import TextLLM
from ..providers.tts import CHARS_PER_SEC, estimate_seconds
from ..project_store import auto_fix_retries, write_yaml_atomic
from .chapter_script import parse_script, spoken_chars
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


def _shot_seconds(blocks: List[Dict[str, Any]]) -> float:
    return sum(_block_seconds({"text": str(b.get("text") or "")}) for b in blocks if str(b.get("text") or "").strip())


def validate_and_build(raw_shots: List[Dict[str, Any]], script: str, locations: List[Dict[str, Any]],
                       characters: List[Dict[str, Any]], chapter_no: int,
                       block_limit: int = MAX_BLOCK_CHARS,
                       props: Optional[List[Dict[str, Any]]] = None,
                       looks_enabled: bool = False,
                       spots_enabled: bool = False) -> Dict[str, Any]:
    if not raw_shots:
        raise StepError("LLM 没有返回任何镜头")
    loc_ids = {l["id"] for l in locations}
    loc_by_name = {}
    for l in locations:
        for n in [l.get("name")] + list(l.get("aliases") or []):
            if n:
                loc_by_name[str(n).strip()] = l["id"]
    new_locations: List[str] = []
    new_props: List[str] = []
    new_looks: List[Dict[str, str]] = []
    prop_by_name: Dict[str, str] = {}
    for pr in props or []:
        for n in [pr.get("name")] + list(pr.get("aliases") or []):
            if n:
                prop_by_name[str(n).strip()] = pr["id"]
    prop_ids_all = {pr["id"] for pr in props or []}
    char_by_id = {c["id"]: c for c in characters}
    char_by_name = {n: c["id"] for c in characters for n in c.get("names", [])}
    script_norm = norm(script)
    new_speakers: List[str] = []
    shots: List[Dict[str, Any]] = []
    scenes: Dict[int, Dict[str, Any]] = {}

    plan_warnings: List[Dict[str, Any]] = []  # 保留字段；代码不再拆镜头，不再产生拆分警告
    for raw_idx, rs in enumerate(raw_shots, start=1):
        scene_no = int(rs.get("scene_no") or 1)
        loc = rs.get("location_id")
        loc_name = str(rs.get("location_name") or "").strip() or None
        if loc not in loc_ids:
            # 模型可能给的是名字而不是 id：先按库中名称/别名匹配
            name_hit = loc_by_name.get(str(loc or "").strip()) or (loc_by_name.get(loc_name) if loc_name else None)
            if name_hit:
                loc = name_hit
            else:
                # 库中没有这个地点：记为缺失素材（不再悄悄挂到第一个地点上）
                missing = loc_name or (str(loc).strip() if loc else None)
                if missing and missing not in new_locations:
                    new_locations.append(missing)
                loc = None
                loc_name = missing
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
                for piece in split_text(text, block_limit):
                    blocks.append({"type": "dialogue", "text": piece, "speaker_id": sid,
                                   "speaker_name": None if sid else name})
            else:
                for piece in split_text(text, block_limit):
                    blocks.append({"type": "narration", "text": piece, "speaker_id": None, "speaker_name": None})
        if not blocks:
            continue
        # 道具：按库中名称/别名匹配；库里没有的记为缺失素材（每镜头最多 3 个）
        shot_prop_ids: List[str] = []
        shot_prop_names: List[str] = []
        for raw_name in (rs.get("prop_names") or [])[:3]:
            pname = str(raw_name or "").strip()
            if not pname:
                continue
            pid = pname if pname in prop_ids_all else prop_by_name.get(pname)
            if pid:
                if pid not in shot_prop_ids:
                    shot_prop_ids.append(pid)
            else:
                if pname not in shot_prop_names:
                    shot_prop_names.append(pname)
                if pname not in new_props:
                    new_props.append(pname)
        # 角色形象（阶段 4，默认关闭）：只在模型指出外观与基础外观不同时记录
        shot_look_ids: Dict[str, str] = {}
        shot_look_labels: Dict[str, str] = {}
        if looks_enabled:
            for rl in rs.get("character_looks") or []:
                if not isinstance(rl, dict):
                    continue
                cid = rl.get("character_id")
                cid = cid if cid in char_by_id else char_by_name.get(str(cid or "").strip())
                lab = str(rl.get("look") or "").strip()
                if cid is None or lab.lower() in DEFAULT_LOOK_LABELS:
                    continue
                hit = find_look(char_by_id[cid], lab)
                if hit:
                    shot_look_ids[cid] = hit["id"]
                else:
                    shot_look_labels[cid] = lab
                    if not any(w["character_id"] == cid and w["label"] == lab for w in new_looks):
                        new_looks.append({"character_id": cid, "label": lab})
        cast = []
        for b in blocks:
            if b["speaker_id"] and b["speaker_id"] not in cast:
                cast.append(b["speaker_id"])
        for cid in rs.get("character_ids") or []:
            if cid in char_by_id and cid not in cast:
                cast.append(cid)
        sheet_id = None                      # 场景设定图（L5）：只接受所选地点确实有的 sheet_id
        loc_obj = next((l for l in locations if l.get("id") == loc), None) if loc else None
        want_sheet = str(rs.get("sheet_id") or "").strip()
        if loc_obj and want_sheet and any(x.get("id") == want_sheet for x in loc_obj.get("sheets") or []):
            sheet_id = want_sheet
        base = {"scene_no": scene_no, "location_id": loc, "location_name": loc_name if loc is None else None,
                "character_ids": cast, "prop_ids": shot_prop_ids, "prop_names": shot_prop_names,
                "visual_hint_zh": str(rs.get("visual_hint_zh") or "").strip(),
                "visual_hint_en": str(rs.get("visual_hint_en") or "").strip() or "cinematic medium shot, no text"}
        for cid in list(shot_look_ids) + list(shot_look_labels):    # 指定了形象的角色一定出镜
            if cid not in cast:
                base["character_ids"].append(cid)
        if sheet_id:
            base["sheet_id"] = sheet_id
        if shot_look_ids:
            base["look_ids"] = shot_look_ids
        if shot_look_labels:
            base["look_labels"] = shot_look_labels
        scenes.setdefault(scene_no, {"scene_no": scene_no, "location_id": loc})
        # 一条模型镜头 = 一个镜头。时长与画面的修正由模型完成（见 collect_shot_issues），代码不机械拆分。
        shots.append({**copy.deepcopy(base), "blocks": blocks})

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
    out = {
        "chapter_no": chapter_no,
        "scenes": list(scenes.values()),
        "shots": shots,
        "new_speakers": new_speakers,
        "new_locations": new_locations,
        "new_props": new_props,
        "new_looks": new_looks,
        "plan_warnings": plan_warnings,
    }
    if spots_enabled:    # 新场景点规格（仅开启时写入；关闭时输出与以前逐字节一致）
        out["new_location_specs"] = LS.build_specs(raw_shots, new_locations, script, locations)
        if len(new_locations) > LS.MAX_NEW_SPOTS_PER_CHAPTER:
            plan_warnings.append({"kind": "too_many_spots",
                                  "message": f"本章规划了 {len(new_locations)} 个新场景点（建议不超过 "
                                             f"{LS.MAX_NEW_SPOTS_PER_CHAPTER} 个），可能拆得过细，请检查"})
    return out


def strategy_block_limit(project_dir: Path, no: int) -> int:
    """G4 策略切换写入的口播块上限；没有策略文件时用默认值。"""
    from ..project_store import read_json
    p = chapter_dir(project_dir, no) / "strategy.json"
    if p.is_file():
        return int(read_json(p).get("max_block_chars") or MAX_BLOCK_CHARS)
    return MAX_BLOCK_CHARS


def _closest_script_line_obj(text: str, script_lines: List[Dict[str, str]]) -> Dict[str, str] | None:
    """剧本对白行里与 text 最接近的一行（dict: speaker/text），找不到像样的返回 None。"""
    from difflib import SequenceMatcher
    nt = norm(text)
    best, best_score = None, 0.0
    for ln in script_lines:
        nl = norm(ln["text"])
        if not nl or not nt:
            continue
        m = SequenceMatcher(None, nt, nl, autojunk=False)
        score = sum(b.size for b in m.get_matching_blocks()) / len(nt)
        if score > best_score:
            best, best_score = ln, score
    if best is None or best_score < 0.4:
        return None
    return best


def _closest_script_line(text: str, script_lines: List[Dict[str, str]]) -> str:
    """剧本对白行里与 text 最接近的一句，用于告诉模型“应该照抄哪里”。找不到像样的返回空串。"""
    best = _closest_script_line_obj(text, script_lines)
    return f"{best['speaker']}：{best['text']}" if best else ""


def _speaker_hint(script_speaker: str, characters: List[Dict[str, Any]]) -> str:
    """把剧本里的说话人名字翻译成模型能直接照做的修正指令（给出角色库 id 或 speaker_name）。"""
    if not script_speaker:
        return "请在 speaker_id 中从角色库选择说话人，或把 speaker_id 置 null 并填 speaker_name"
    for c in characters:
        names = c.get("names") or [c.get("name")]
        if script_speaker in names:
            return f"剧本里的说话人是“{script_speaker}”，角色库中对应的 id 为 {c['id']}，请设置 speaker_id=\"{c['id']}\""
    known = "、".join(f"{c['id']}（{c.get('name', '')}）" for c in characters[:30]) or "（角色库为空）"
    return (f"剧本里的说话人是“{script_speaker}”，角色库中没有这个名字。若它其实是角色库中的某个人，"
            f"请从下列 id 中选择并设置 speaker_id；否则把 speaker_id 置 null，并设置 speaker_name=\"{script_speaker}\"。"
            f"角色库：{known}")


def collect_shot_issues(raw_shots: List[Dict[str, Any]], script: str,
                        characters: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """一次性找出镜头规划里的全部问题（而不是遇到第一个就停），用于把完整问题清单反馈给模型自动修正。
    检查项与 validate_and_build 一致：口播块合法、对白是剧本中的连续原文、对白有说话人。
    每条 message 都写明：位置、出错的原文、剧本里对应的原句与说话人、以及具体怎么改。"""
    issues: List[Dict[str, str]] = []
    if not raw_shots:
        # 空结果也要指出缺哪些场次，否则修正时模型只知道“什么都没有”，不知道该补哪一场
        return [{"message": "没有返回任何镜头（shots 为空）"}] + _scene_coverage_issues([], script)
    script_norm = norm(script)
    try:
        script_lines = [{"speaker": ln["speaker"], "text": ln["text"]}
                        for sc in parse_script(script) for ln in sc["lines"] if ln["type"] == "dialogue"]
    except Exception:
        script_lines = []
    char_by_id = {c["id"] for c in characters}
    for i, rs in enumerate(raw_shots, start=1):
        for j, rb in enumerate(rs.get("blocks") or [], start=1):
            where = f"第 {i} 个镜头的第 {j} 个口播块"
            btype = rb.get("type")
            text = str(rb.get("text") or "").strip()
            if btype not in ("narration", "dialogue") or not text:
                issues.append({"message": f"{where}非法：type 只能是 narration 或 dialogue，text 不能为空（当前 type={btype}）。"
                                          f"请改为 narration 或 dialogue，并填入非空的 text。"})
                continue
            if btype != "dialogue":
                continue
            if norm(text) not in script_norm:
                near = _closest_script_line_obj(text, script_lines)
                if near:
                    hint = (f"剧本里最接近的原句是“{near['speaker']}：{near['text']}”。"
                            f"请从这一行中截取一段连续原文作为 text，不要删字、改字或拼接不相邻的句子；"
                            f"原句太长时拆成多个镜头，各取一段。")
                else:
                    hint = "剧本中找不到相近的对白行。请改为 narration，或只使用剧本对白行中的原文。"
                issues.append({"message": f"{where}的对白“{text[:60]}”不是剧本中的连续原文。{hint}"})
                continue
            if rb.get("speaker_id") in char_by_id or str(rb.get("speaker_name") or "").strip():
                continue
            near = _closest_script_line_obj(text, script_lines)
            script_speaker = near["speaker"] if near else ""
            issues.append({"message": f"{where}的对白“{text[:30]}”缺少说话人（speaker_id 不在角色库中且没有 speaker_name）。"
                                      + _speaker_hint(script_speaker, characters)})
    issues.extend(_scene_coverage_issues(raw_shots, script))
    issues.extend(_shot_level_issues(raw_shots))
    return issues


def _scene_coverage_issues(raw_shots: List[Dict[str, Any]], script: str) -> List[Dict[str, str]]:
    """每个剧本场次都必须至少有一个镜头；镜头引用的场次号必须存在。缺场次会导致大场景没有镜头。"""
    from . import chapter_scenes as CS
    scenes = CS.parse_script_scenes(script)
    if not scenes:
        return []
    titles = {s["scene_no"]: s["header"] for s in scenes}
    got = set()
    for rs in raw_shots:
        try:
            got.add(int(rs.get("scene_no")))
        except (TypeError, ValueError):
            pass
    issues: List[Dict[str, str]] = []
    for n in sorted(set(titles) - got):
        issues.append({"message": f"剧本第 {n} 场「{titles[n]}」没有任何镜头。每一场都必须至少有一个镜头，"
                                  f"请为第 {n} 场补充镜头（画面与口播都取自该场的剧本内容，scene_no 填 {n}）；"
                                  f"不要把该场内容并入其它场次的镜头而丢掉它。"})
    for n in sorted(got - set(titles)):
        issues.append({"message": f"有镜头的 scene_no 为 {n}，但剧本中没有第 {n} 场。scene_no 只能是剧本中存在的场次号。"})
    return issues


def _shot_level_issues(raw_shots: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """镜头级检查：只检查相邻镜头的画面描述是否相同。
    口播时长不做硬性限制（差几秒可以接受，合成时会按配音时长缩放画面）。"""
    issues: List[Dict[str, str]] = []
    for i in range(2, len(raw_shots) + 1):
        prev, cur = raw_shots[i - 2], raw_shots[i - 1]
        if prev.get("scene_no") != cur.get("scene_no"):
            continue
        same_zh = _norm_hint(prev.get("visual_hint_zh")) and _norm_hint(prev.get("visual_hint_zh")) == _norm_hint(cur.get("visual_hint_zh"))
        same_en = _norm_hint(prev.get("visual_hint_en")) and _norm_hint(prev.get("visual_hint_en")) == _norm_hint(cur.get("visual_hint_en"))
        if same_zh or same_en:
            issues.append({"message": (
                f"第 {i} 个镜头与第 {i - 1} 个镜头画面描述相同。请根据第 {i} 个镜头自己的口播内容，重新写它的画面："
                f"至少改变景别（远景/中景/近景/特写）、机位或主体动作中的一项，且不要复述整场戏的画面。")})
    return issues


def _norm_hint(text: Any) -> str:
    return re.sub(r"\s", "", str(text or "")).lower()


def _segment_script(script: str, lo: int, hi: int) -> str:
    """只保留场次 lo..hi 的剧本文本（含各场的标题行）。"""
    from .chapter_scenes import _HEADER
    out, keep = [], False
    for line in script.splitlines():
        m = _HEADER.match(line.strip())
        if m:
            keep = lo <= int(m.group(1)) <= hi
        if keep:
            out.append(line)
    return "\n".join(out) + "\n"


def _segments(script: str, plan: Optional[Dict[str, Any]]):
    """分段方案：[(段落名, 段剧本, 段内大场景列表, 段预计镜头数)]。没有大场景时整章一段。"""
    if not plan or not plan.get("big_scenes"):
        return [("整章", script, None, max(1, math.ceil(spoken_chars(script) / int(SHOT_PLAN_SEC * CHARS_PER_SEC))))]
    segs = []
    for g in plan["big_scenes"]:
        seg_script = _segment_script(script, g["from_scene"], g["to_scene"])
        label = f"大场景 {g['big_no']}「{g['title_zh']}」（场次 {g['from_scene']}–{g['to_scene']}）"
        expected = max(1, math.ceil(spoken_chars(seg_script) / int(SHOT_PLAN_SEC * CHARS_PER_SEC)))
        segs.append((label, seg_script, [g], expected))
    return segs


def _merge_parts(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """合并各段的规划结果：镜头重新编号（s01…），拆分记录的 id 引用同步改写；其它列表去重合并。"""
    if len(parts) == 1:
        return parts[0]
    out = copy.deepcopy(parts[0])
    shots: List[Dict[str, Any]] = []
    for part in parts:
        id_map = {}
        for sh in part.get("shots") or []:
            id_map[sh["id"]] = f"s{len(shots) + 1:02d}"
            shots.append(sh)
        for sh in part.get("shots") or []:
            sh["id"] = id_map[sh["id"]]
            if sh.get("split_from") in id_map:
                sh["split_from"] = id_map[sh["split_from"]]
    out["shots"] = shots
    for key in ("scenes", "new_speakers", "new_locations", "new_props", "new_looks", "plan_warnings",
                "new_location_specs"):
        seen, merged = set(), []
        for part in parts:
            for item in part.get(key) or []:
                k = json.dumps(item, ensure_ascii=False, sort_keys=True) if not isinstance(item, str) else item
                if k not in seen:
                    seen.add(k)
                    merged.append(item)
        if key in parts[0] or merged:
            out[key] = merged
    return out


def _plan_segment(project_dir: Path, no: int, text_llm: TextLLM, script: str, prompt: str, *,
                   seg_label: str, characters, locations, props, looks_on: bool, spots_on: bool,
                   limit: int, retries: int, per_shot: int, min_per_shot: int, expected: int,
                   review_path: Path):
    """规划一段剧本（一个大场景，或整章）的镜头：调用模型，校验，失败则把问题反馈修正。
    返回 (built, attempts)；修正次数用尽仍不通过时抛出 StepError（消息带上大场景名）。"""
    attempt = 0
    while True:
        label = (f"第 {no} 章 C-3 {seg_label}：调用文本模型规划镜头"
                 + (f"（第 {attempt} 次修正）" if attempt else f"（剧本 {len(script)} 字）"))
        with joblog.timed(project_dir, "chapter", label):
            raw = llm_trace.ask_traced(project_dir, text_llm, prompt, system=prompts.SYSTEM_VISUAL,
                                       scope="chapter", label=label, step="C-3", chapter=no)
        built, obj, issues = None, None, []
        try:
            obj = parse_json_object(raw)
            issues = collect_shot_issues(obj.get("shots") or [], script, characters)
            if not issues:
                built = validate_and_build(obj.get("shots") or [], script, locations, characters, no, block_limit=limit,
                                           props=props, looks_enabled=looks_on, spots_enabled=spots_on)
                if spots_on:
                    # 新场景识别：标题写到具体场所、镜头却挂在对不上的已有地点上 → 先反馈给模型修正；
                    # 自动修正次数用尽则只记为警告，不让整章失败
                    spot_issues = LS.granularity_issues(built["shots"], script, locations)
                    if spot_issues and attempt < retries:
                        issues, built = spot_issues, None
                    elif spot_issues:
                        built["plan_warnings"].extend({"kind": "location_not_specific", "message": i["message"]}
                                                      for i in spot_issues)
        except StepError as exc:
            issues = issues or [{"message": str(exc)}]
        if built is not None:
            break
        write_yaml_atomic(review_path, {"status": "fail", "attempts": attempt + 1, "issues": issues})
        if attempt >= retries:
            more = f"（共 {len(issues)} 个问题）" if len(issues) > 1 else ""
            raise StepError(f"{seg_label}：镜头规划校验未通过{more}"
                            + (f"，已自动修正 {attempt} 次仍未解决" if attempt else "")
                            + "：" + issues[0]["message"])
        attempt += 1
        joblog.log(project_dir, "chapter",
                   f"第 {no} 章 C-3 校验未通过（{len(issues)} 个问题），自动把问题反馈给模型修正（第 {attempt}/{retries} 次）：\n"
                   + "\n".join(f"- {i['message']}" for i in issues), level="warn")
        prompt = prompts.shots_fix_prompt(script, characters, locations,
                                          json.dumps(obj, ensure_ascii=False) if obj is not None else (raw or "")[:6000],
                                          issues, props=props, looks_enabled=looks_on, max_chars_per_shot=per_shot, min_chars_per_shot=min_per_shot,
                                          expected_shots=expected, spots_enabled=spots_on)
    return built, attempt + 1


def run_shots(project_dir: Path, no: int, text_llm: TextLLM, *, force: bool = False) -> Dict[str, Any]:
    """C-3/C-4。校验失败时把全部问题反馈给模型自动修正，最多 shots_max_retries 次（默认 2，写 0 则直接失败）。"""
    cdir = chapter_dir(project_dir, no)
    script_path = cdir / "script.md"
    if not script_path.is_file():
        raise StepError("缺少剧本，请先执行 C-1/C-2")
    script = script_path.read_text(encoding="utf-8")
    limit = strategy_block_limit(project_dir, no)
    # C-3a 大场景划分（先于镜头规划；缓存于 big_scenes.yaml）。剧本没有场次标题时无法划分，记警告后按整章规划。
    from . import chapter_scenes as CS
    try:
        plan = CS.plan_big_scenes(project_dir, no, script, text_llm, force=force)
    except ValueError as exc:
        plan = None
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-3a 无法划分大场景：{exc}", level="warn")
    big_key = json.dumps([(g["from_scene"], g["to_scene"]) for g in (plan or {}).get("big_scenes") or []])
    # 缓存键包含口播块上限与大场景划分：策略或划分变化后必然重新规划
    script_hash = sha12(script, str(limit), big_key)
    existing = load_shots(project_dir, no)
    if not force and existing and existing.get("script_hash") == script_hash and existing.get("shots"):
        mark_stage(project_dir, no, "shots", "done")
        return {"skipped": True, "shots": len(existing["shots"])}

    ents = load_entities(project_dir)
    characters = ents["characters"]["characters"]
    locations = ents["locations"]["locations"]
    props = ents["props"]["props"]
    looks_on = looks_enabled(project_dir)
    spots_on = LS.spots_enabled(project_dir)
    per_shot = int(SHOT_MAX_SEC * CHARS_PER_SEC)                 # 单镜头口播字数上限（约 12 秒）
    min_per_shot = int(SHOT_MIN_SEC * CHARS_PER_SEC)             # 单镜头口播字数下限（约 4 秒）
    expected = max(1, math.ceil(spoken_chars(script) / int(SHOT_PLAN_SEC * CHARS_PER_SEC)))
    retries = auto_fix_retries(project_dir, "shots_max_retries")
    review_path = cdir / "shots_review.yaml"
    # C-3b：按大场景分段规划（每段只含该大场景的剧本，覆盖检查与修正都针对这一段）。没有大场景时整章一段。
    segments = _segments(script, plan)
    parts = []
    total_attempts = 0
    for seg_label, seg_script, seg_groups, seg_expected in segments:
        seg_prompt = prompts.shots_prompt(seg_script, characters, locations, props=props, looks_enabled=looks_on,
                                          max_chars_per_shot=per_shot, min_chars_per_shot=min_per_shot,
                                          expected_shots=seg_expected, spots_enabled=spots_on,
                                          big_scenes=seg_groups or None)
        built_seg, attempts = _plan_segment(
            project_dir, no, text_llm, seg_script, seg_prompt, seg_label=seg_label, characters=characters,
            locations=locations, props=props, looks_on=looks_on, spots_on=spots_on, limit=limit, retries=retries,
            per_shot=per_shot, min_per_shot=min_per_shot, expected=seg_expected, review_path=review_path)
        parts.append(built_seg)
        total_attempts += attempts
    built = _merge_parts(parts)
    attempt = total_attempts - 1
    if plan:
        CS.attach(built, plan, locations, characters)      # 大场景 → 镜头；共用素材汇总到大场景
    built["script_hash"] = script_hash
    built["block_limit"] = limit
    built["attempts"] = attempt + 1
    save_shots(project_dir, no, built)
    write_yaml_atomic(review_path, {"status": "pass", "attempts": attempt + 1, "issues": []})
    mark_stage(project_dir, no, "shots", "done")
    return {"skipped": False, "shots": len(built["shots"]), "new_speakers": built["new_speakers"],
            "new_locations": built.get("new_locations", []),
            "new_location_specs": built.get("new_location_specs", []),
            "new_props": built.get("new_props", []), "new_looks": built.get("new_looks", []), "attempts": attempt + 1}
