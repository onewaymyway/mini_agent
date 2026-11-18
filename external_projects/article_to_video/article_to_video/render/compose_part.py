"""render/compose_part.py — 章级渲染：把已确认分镜渲染为图层文件，并写入 visuals/manifest.json。

manifest 是 P5 合成的唯一输入（每个场景的时间段、底图、逐状态图层与出现时刻）。
一图多用：每个图片组的主图先统一解析（生成或复用），组内其余镜头只取景，不再生图。
单场景缓存：场景内容、所在组主图主题、画幅、主题、风格锚都不变时复用旧图层。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .. import cancel, joblog
from ..audio.synth import load_timeline
from ..planning.errors import PlanError
from ..project_store import append_progress, load_project, now_iso, read_json, write_json_atomic
from ..script.writer import load_script
from ..visual.config import load_visual_config
from ..visual.images import ensure_illustration
from ..visual.sharing import leader_subjects
from ..visual.storyboard import load_storyboard, storyboard_status
from .fonts import FontError
from .renderers import (render_background, render_chapter_card, render_illustration_background,
                        render_scene_frames)
from .themes import canvas_size

RENDER_VERSION = "p4-render-v2"


def _sentence_starts(timeline: Dict[str, Any]) -> Dict[str, Tuple[float, float, float]]:
    """句ID → (start, end, gap_after)。"""
    return {s["id"]: (s["start"], s["end"], s["gap_after"]) for s in timeline["sentences"]}


def _beat_span(timeline_map, beat_id: str, sentences: int) -> Tuple[float, float]:
    ids = [f"{beat_id}.s{i + 1:02d}" for i in range(sentences)]
    present = [timeline_map[i] for i in ids if i in timeline_map]
    if not present:
        raise PlanError(f"时间轴中找不到段落 {beat_id}，请重新配音（P3）")
    return present[0][0], present[-1][1] + present[-1][2]


def _scene_sha(scene: Dict[str, Any], cfg: Dict[str, Any], canvas, orientation: str,
               group_subject: str) -> str:
    raw = json.dumps({"scene": scene, "theme": cfg["theme"], "anchor": cfg["bible"]["style_anchor"],
                      "canvas": list(canvas), "orientation": orientation, "group_subject": group_subject,
                      "v": RENDER_VERSION}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _frame_times(scene: Dict[str, Any], starts: Dict[str, Tuple[float, float, float]],
                 span_start: float) -> list:
    """每个状态的出现时刻：对齐到其句子的起点；同一句内的多个状态在该句时长内均分。"""
    reveal = scene.get("reveal") or []
    if not reveal:
        return [span_start]
    groups: Dict[int, list] = {}
    for k, r in enumerate(reveal):
        groups.setdefault(r["at_sentence"], []).append(k)
    times = [span_start] * len(reveal)
    for at, ks in groups.items():
        sid = f"{scene['beat']}.s{at + 1:02d}"
        if sid not in starts:
            continue
        start, end, _gap = starts[sid]
        dur = max(0.0, end - start)
        for j, k in enumerate(ks):
            times[k] = start + dur * j / len(ks)
    return times


def _check_files(pdir: Path, part_id: str, entry: Dict[str, Any]) -> bool:
    vdir = pdir / "parts" / part_id
    needed = [entry.get("background", "")] + [f["file"] for f in entry.get("frames", [])]
    return all((vdir / p).is_file() for p in needed if p)


def render_part(project_dir: Path, part_id: str, *, provider=None,
                now: Optional[datetime] = None) -> Dict[str, Any]:
    pdir = Path(project_dir)
    st = storyboard_status(pdir, part_id)
    if not st["exists"]:
        raise PlanError(f"{part_id} 还没有分镜（C2）")
    if st["stale"]:
        raise PlanError("分镜已过期，请先在「🎞️ 分镜」页重新生成并确认")
    if not st["confirmed"]:
        raise PlanError(f"请先确认 {part_id} 的分镜（C2）")
    if st["errors"]:
        raise PlanError("分镜不合格：" + "；".join(st["errors"][:5]))
    timeline = load_timeline(pdir, part_id)
    if timeline is None:
        raise PlanError(f"{part_id} 还没有时间轴，请先配音（P3）")
    script = load_script(pdir, part_id)
    sb = load_storyboard(pdir, part_id)
    cfg = load_visual_config(pdir)
    project = load_project(pdir)
    video = project.get("settings", {}).get("video", {})
    orientation = video.get("orientation", "landscape")
    canvas = canvas_size(orientation, video.get("resolution", "1080P"))
    size = cfg["images"]["size"]
    theme = cfg["theme"]

    vdir = pdir / "parts" / part_id / "visuals"
    vdir.mkdir(parents=True, exist_ok=True)
    manifest_path = vdir / "manifest.json"
    prev = read_json(manifest_path) if manifest_path.is_file() else {"scenes": []}
    prev_by_id = {s["id"]: s for s in prev.get("scenes", [])}

    try:
        render_background(canvas, theme).save(vdir / "bg.png")
        render_chapter_card(sb["chapter"], canvas, orientation, theme).save(vdir / "chapter.png")
    except FontError as exc:
        raise PlanError(str(exc)) from exc

    starts = _sentence_starts(timeline)
    beat_sent = {b["id"]: len(b["sentences"]) for b in script["beats"]}
    subjects = leader_subjects(sb["scenes"])
    counts = {"rendered": 0, "reused": 0, "illustration": 0, "fallback": 0, "groups": 0}
    warnings = list(sb.get("warnings", []))

    # 1) 解析每个图片组的主图（生成或复用）。组内其余镜头不生图。
    group_src: Dict[str, Dict[str, Any]] = {}
    reference: Optional[Path] = None
    for sc in sb["scenes"]:
        if sc["type"] != "illustration":
            continue
        img = sc.get("image") or {}
        gid = img.get("group") or sc["id"]
        if gid in group_src:
            continue
        cancel.check()
        out = ensure_illustration(pdir, part_id, sc, provider=provider,
                                  style_anchor=cfg["bible"]["style_anchor"],
                                  orientation=orientation, size=size, reference=reference)
        group_src[gid] = out
        counts["groups"] += 1
        if out["ok"] and reference is None:
            reference = Path(out["path"])

    # 2) 逐场景渲染图层（组内镜头取同一主图，按各自取景裁切）
    out_scenes = []
    for sc in sb["scenes"]:
        cancel.check()
        span_start, span_end = _beat_span(starts, sc["beat"], beat_sent[sc["beat"]])
        img = sc.get("image") or {}
        gid = img.get("group") or sc["id"]
        group_subject = subjects.get(gid, "") if sc["type"] == "illustration" else ""
        sha = _scene_sha(sc, cfg, canvas, orientation, group_subject)
        old = prev_by_id.get(sc["id"])
        if old and old.get("sha") == sha and _check_files(pdir, part_id, old):
            out_scenes.append(old)
            counts["reused"] += 1
            joblog.bound_log(f"镜头 {sc['id']}：未变化，复用已渲染画面")
            continue
        joblog.bound_log(f"镜头 {sc['id']}：渲染为 {sc['type']}")

        entry: Dict[str, Any] = {"id": sc["id"], "beat": sc["beat"], "type": sc["type"], "sha": sha,
                                 "start": round(span_start, 3), "end": round(span_end, 3),
                                 "rendered_as": sc["type"], "fallback_reason": None,
                                 "background": "visuals/bg.png", "frames": [], "image_group": None}
        as_type = ""
        if sc["type"] == "illustration":
            out = group_src.get(gid) or {"ok": False, "error": "图片组缺少主图", "path": None}
            if out["ok"]:
                bg_path = vdir / f"{sc['id']}_bg.png"
                render_illustration_background(Path(out["path"]), canvas, img.get("focus_x", 0.5),
                                               theme).save(bg_path)
                entry["background"] = f"visuals/{bg_path.name}"
                entry["background_src"] = str(out["path"])
                entry["image_group"] = gid
                entry["image_leader"] = bool(img.get("leader"))
                entry["image_reused"] = out.get("reused", False)
                counts["illustration"] += 1
                out_scenes.append(entry)
                counts["rendered"] += 1
                continue
            entry["rendered_as"] = "keypoint"
            entry["fallback_reason"] = out["error"]
            counts["fallback"] += 1
            as_type = "keypoint"
            warnings.append(f"{sc['id']}：插图未生成，已按卡片绘制（{out['error']}）")

        frames = render_scene_frames(sc, canvas, orientation, theme, as_type=as_type)
        times = _frame_times(sc, starts, span_start)
        for k, frame in enumerate(frames):
            fname = f"{sc['id']}_s{k:02d}.png"
            frame.save(vdir / fname)
            entry["frames"].append({"file": f"visuals/{fname}", "start": round(times[k], 3)})
        out_scenes.append(entry)
        counts["rendered"] += 1

    lead = float(timeline.get("lead_in_sec", 0.0))
    manifest = {
        "schema": 1,
        "part": part_id,
        "canvas": list(canvas),
        "orientation": orientation,
        "theme": theme,
        "chapter": {"file": "visuals/chapter.png", "background": "visuals/bg.png",
                    "start": 0.0, "end": round(lead, 3)},
        "total_sec": timeline["total_sec"],
        "scenes": out_scenes,
        "counts": counts,
        "warnings": warnings,
        "rendered_at": now_iso(now),
        "render_version": RENDER_VERSION,
        "storyboard_input_sha": sb.get("input_sha"),
    }
    write_json_atomic(manifest_path, manifest)
    append_progress(pdir, f"渲染画面 {part_id}：新绘 {counts['rendered']}、复用 {counts['reused']}、"
                          f"图片组 {counts['groups']}、降级 {counts['fallback']}", now)
    return manifest
