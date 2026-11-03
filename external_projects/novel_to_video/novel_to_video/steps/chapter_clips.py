"""steps/chapter_clips.py — C-7 提示词与一致性检查，C-8 小场景视频。

C-7：prompt_en 由代码确定性拼装：画风 + 镜头画面 + 出镜角色外观锚点 + 地点锚点。
     一致性（机械）：每个出镜角色的 visual_anchor_en 与地点 visual_anchor_en 必须原样出现在 prompt 中。
     语义层面的核查（画面是否符合情节）留给后续阶段的 Agent 核查，本期只做机械核查。
C-8：参考图为出镜角色定妆图（最多 2 个）+ 地点定妆图（1 个），缺失则硬性拦截。
     clip_key = hash(prompt, 音频, 时长, 参考图)；任一变化都会重新生成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import cancel, joblog
from ..chapter_io import chapter_dir, load_entities, load_shots, mark_stage, save_shots, sha12
from ..media import ffmpeg as mf
from .. import video_format
from ..project_store import read_json
from ..providers.video import VideoProvider
from . import character_looks
from .chapter_assets import missing_portraits
from . import location_sheets
from .global_visual import DEFAULT_ART_STYLE


def build_prompt(art_style: str, shot: Dict[str, Any], chars: Dict[str, Dict], locs: Dict[str, Dict],
                 frame_prompt: str = "vertical 9:16 frame",
                 props: Optional[Dict[str, Dict]] = None) -> Dict[str, Any]:
    char_anchors = []
    for cid in shot.get("character_ids") or []:
        c = chars.get(cid)
        if c:
            anchor, _ = character_looks.resolve(c, (shot.get("look_ids") or {}).get(cid))
            if anchor:
                char_anchors.append(anchor)
    loc = locs.get(shot.get("location_id")) or {}
    sheet = location_sheets.sheet_for_shot(loc, shot) if loc else None      # 场景设定图（L5）：镜头选用的视角
    loc_anchor = (sheet or {}).get("visual_anchor_en") or loc.get("visual_anchor_en") or ""
    marks = location_sheets.markers_for(loc, sheet) if sheet else []
    if marks:
        loc_anchor = loc_anchor + ". Key objects: " + "; ".join(m.get("visual_anchor_en") or m["name"] for m in marks)
    prop_anchors = []
    for pid in shot.get("prop_ids") or []:
        pr = (props or {}).get(pid)
        if pr and pr.get("visual_anchor_en"):
            prop_anchors.append(pr["visual_anchor_en"])
    parts = [art_style, shot.get("visual_hint_en") or ""]
    if char_anchors:
        parts.append("Characters: " + " | ".join(char_anchors))
    if loc_anchor:
        parent = locs.get(loc.get("parent_id")) if loc.get("level") == "spot" else None
        where = f" (inside {parent['name']})" if parent and parent.get("name") else ""   # 场景点：补一句所属区域
        parts.append("Setting: " + loc_anchor + where)
    if prop_anchors:
        parts.append("Props: " + " | ".join(prop_anchors))
    parts.append(f"{frame_prompt}, consistent character design, no text, no subtitles")
    prompt = ". ".join(p.strip().rstrip(".") for p in parts if p and p.strip()) + "."
    missing = [a for a in char_anchors + ([loc_anchor] if loc_anchor else []) + prop_anchors if a not in prompt]
    return {"prompt": prompt, "consistency": "pass" if not missing else "fail",
            "missing": missing, "anchors": len(char_anchors) + (1 if loc_anchor else 0) + len(prop_anchors)}


def run_prompts(project_dir: Path, no: int, art_style: str | None = None) -> Dict[str, Any]:
    shots = load_shots(project_dir, no)
    ents = load_entities(project_dir)
    chars = {c["id"]: c for c in ents["characters"]["characters"]}
    locs = {l["id"]: l for l in ents["locations"]["locations"]}
    props = {p["id"]: p for p in ents["props"]["props"]}
    style = art_style or read_json(project_dir / "project.json").get("art_style") or DEFAULT_ART_STYLE
    fmt = video_format.load(project_dir)
    failed = []
    for sh in shots["shots"]:
        built = build_prompt(style, sh, chars, locs, frame_prompt=fmt.frame_prompt, props=props)
        sh["prompt_en"] = built["prompt"]
        sh["prompt_hash"] = sha12(built["prompt"])
        sh["consistency"] = built["consistency"]
        if built["consistency"] == "fail":
            failed.append(sh["id"])
    save_shots(project_dir, no, shots)
    mark_stage(project_dir, no, "prompts", "failed" if failed else "done",
               f"一致性检查未通过：{', '.join(failed)}" if failed else None)
    return {"failed": failed}


def _reference_images(project_dir: Path, sh: Dict[str, Any], chars: Dict[str, Dict],
                      locs: Dict[str, Dict], props: Optional[Dict[str, Dict]] = None) -> List[Path]:
    refs: List[Path] = []
    for cid in (sh.get("character_ids") or [])[:2]:
        c = chars.get(cid)
        asset = character_looks.resolve(c, (sh.get("look_ids") or {}).get(cid))[1] if c else None
        if asset:
            refs.append(project_dir / asset)
    loc = locs.get(sh.get("location_id"))
    if loc:
        sheet = location_sheets.sheet_for_shot(loc, sh)
        asset = (sheet or {}).get("asset_path") or loc.get("asset_path")
        if asset:
            refs.append(project_dir / asset)
    # 视频接口最多 3 张参考图：角色与地点优先，有余量时才带道具（道具外观始终写在提示词里）
    for pid in sh.get("prop_ids") or []:
        pr = (props or {}).get(pid)
        if len(refs) < 3 and pr and pr.get("asset_path"):
            refs.append(project_dir / pr["asset_path"])
    return refs[:3]


def run_clips(project_dir: Path, no: int, video: VideoProvider) -> Dict[str, Any]:
    blocked = missing_portraits(project_dir, no)
    if blocked:
        mark_stage(project_dir, no, "clips", "waiting", "缺少定妆图：" + "、".join(blocked))
        return {"blocked": blocked, "generated": [], "failed": []}

    shots = load_shots(project_dir, no)
    ents = load_entities(project_dir)
    chars = {c["id"]: c for c in ents["characters"]["characters"]}
    locs = {l["id"]: l for l in ents["locations"]["locations"]}
    props = {p["id"]: p for p in ents["props"]["props"]}
    cdir = chapter_dir(project_dir, no) / "clips"
    fmt = video_format.load(project_dir)
    mf.apply_format(fmt)
    generated, failed = [], []
    for sh in shots["shots"]:
        cancel.check()   # 用户请求停止：不再为后续镜头提交视频
        if sh.get("error") or not sh.get("prompt_en") or sh.get("consistency") != "pass":
            failed.append(sh["id"])
            continue
        refs = _reference_images(project_dir, sh, chars, locs, props)
        ref_key = sha12(*[f"{p.name}:{p.stat().st_size}" for p in refs])
        clip_key = sha12(sh["prompt_hash"], sh["audio_key"], str(sh["duration_sec"]), ref_key, fmt.key)
        clip = cdir / f"{sh['id']}.mp4"
        if clip.is_file() and sh.get("clip_key") == clip_key:
            continue
        raw = cdir / "raw" / f"{sh['id']}.mp4"
        joblog.log(project_dir, "chapter", f"第 {no} 章 C-8 镜头 {sh['id']}：提交视频生成"
                   f"（{int(sh['duration_sec'])} 秒，参考图 {len(refs)} 张）")
        res = video.generate(sh["prompt_en"], raw, seconds=int(sh["duration_sec"]), reference_images=refs,
                             aspect_ratio=fmt.aspect_ratio, size=fmt.resolution)
        if not res.ok:
            joblog.log(project_dir, "chapter", f"第 {no} 章 C-8 镜头 {sh['id']} 失败：{res.error}", level="error")
            sh["clip_error"] = res.error
            failed.append(sh["id"])
            if res.non_retryable:
                save_shots(project_dir, no, shots)
                mark_stage(project_dir, no, "clips", "failed", res.error)
                return {"blocked": [], "generated": generated, "failed": failed, "stopped": res.error}
            continue
        audio_wav = cdir.parent / "audio" / f"{sh['id']}.wav"
        if audio_wav.is_file():
            scale = mf.fit_clip(raw, audio_wav, clip)
            factor = scale if scale > 1 else 1 / scale      # 慢放倍数 / 快放倍数
            speed = "慢放" if scale > 1 else "快放"
            note = f"画面按配音时长{speed} x{factor:.2f}" if abs(scale - 1) > 0.02 else "画面与配音时长一致"
            lvl = "warn" if (scale < 0.5 or scale > 2) else "ok"
            joblog.log(project_dir, "chapter", f"第 {no} 章 C-8 镜头 {sh['id']} 完成（{note}）", level=lvl)
        else:
            mf.mux_clip(raw, None, clip, float(sh["duration_sec"]))
            joblog.log(project_dir, "chapter", f"第 {no} 章 C-8 镜头 {sh['id']} 完成（无配音）", level="ok")
        sh["clip_key"] = clip_key
        sh["clip_error"] = None
        generated.append(sh["id"])
    save_shots(project_dir, no, shots)
    status = "failed" if failed else "done"
    mark_stage(project_dir, no, "clips", status, f"失败镜头：{', '.join(failed)}" if failed else None)
    return {"blocked": [], "generated": generated, "failed": failed}
