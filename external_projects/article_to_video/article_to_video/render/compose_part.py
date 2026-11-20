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
from ..project_store import append_progress, now_iso, read_json, write_json_atomic
from ..script.writer import load_script
from ..visual.config import load_visual_config
from ..visual.images import ensure_illustration
from ..visual.sharing import leader_subjects
from ..visual.shots import shot_range
from ..visual.storyboard import load_storyboard, storyboard_status
from .fonts import FontError
from .renderers import (render_background, render_chapter_card, render_illustration_background,
                        render_scene_frames)
from .themes import canvas_size

RENDER_VERSION = "p4-render-v3"


def _sentence_starts(timeline: Dict[str, Any]) -> Dict[str, Tuple[float, float, float]]:
    """句ID → (start, end, gap_after)。"""
    return {s["id"]: (s["start"], s["end"], s["gap_after"]) for s in timeline["sentences"]}


def _scene_span(timeline_map, sc: Dict[str, Any], sentences: int) -> Tuple[float, float]:
    """镜头在时间轴上的起止：首句起点到末句终点（含句间停顿），按镜头的句子范围；旧场景覆盖整段。"""
    a, b = shot_range(sc, sentences)
    beat_id = sc["beat"]
    first = f"{beat_id}.s{a + 1:02d}"
    last = f"{beat_id}.s{b + 1:02d}"
    if first not in timeline_map or last not in timeline_map:
        raise PlanError(f"时间轴中找不到镜头 {sc.get('id')}（段落 {beat_id}），请重新配音（P3）")
    return timeline_map[first][0], timeline_map[last][1] + timeline_map[last][2]


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
    times = [span_start] * len(reveal)
    # 有 at_frac 的状态（LLM 分镜按要点在句中的位置定时）：直接按句内位置换算
    framed = {k for k, r in enumerate(reveal) if isinstance(r.get("at_frac"), (int, float))}
    for k in framed:
        r = reveal[k]
        sid = f"{scene['beat']}.s{r['at_sentence'] + 1:02d}"
        if sid not in starts:
            continue
        start, end, _gap = starts[sid]
        times[k] = start + max(0.0, end - start) * min(1.0, max(0.0, float(r["at_frac"])))
    # 其余状态：同一句内均分（旧分镜兼容）
    groups: Dict[int, list] = {}
    for k, r in enumerate(reveal):
        if k not in framed:
            groups.setdefault(r["at_sentence"], []).append(k)
    for at, ks in groups.items():
        sid = f"{scene['beat']}.s{at + 1:02d}"
        if sid not in starts:
            continue
        start, end, _gap = starts[sid]
        dur = max(0.0, end - start)
        for j, k in enumerate(ks):
            times[k] = start + dur * j / len(ks)
    # 出现时刻不得倒退
    for k in range(1, len(times)):
        times[k] = max(times[k], times[k - 1])
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
    from ..app_settings import load_effective_project  # 延迟导入
    video = load_effective_project(pdir)["settings"].get("video", {})
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
        span_start, span_end = _scene_span(starts, sc, beat_sent[sc["beat"]])
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


def render_ready_reason(pdir: Path, part_id: str) -> Optional[str]:
    """本章能否渲染：返回 None 表示可以；否则返回跳过的原因（R20）。"""
    st = storyboard_status(pdir, part_id)
    if not st["exists"]:
        return "还没有分镜"
    if st["stale"]:
        return "分镜已过期"
    if not st["confirmed"]:
        return "分镜未确认"
    if st["errors"]:
        return "分镜不合格"
    if load_timeline(pdir, part_id) is None:
        return "还没有时间轴"
    return None


def render_all_parts(project_dir: Path, part_ids, *, provider=None,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    """逐章渲染画面（R20，后台任务内调用）。

    * 未就绪的章（分镜未确认、已过期、不合格、无时间轴）跳过，并写明原因；
    * 每章独立：单章失败只记录，继续下一章；全部完成后若有失败，以 PlanError 结束并列出失败章节；
    * 图片复用由 render_part 负责（已生成的图片不会重新生图），所以重复执行不会产生额外费用；
    * 停止请求（cancel.Cancelled）直接向上抛出。
    """
    pdir = Path(project_dir)
    part_ids = list(part_ids)
    results: list = []
    joblog.bound_log(f"批量渲染：共 {len(part_ids)} 章（已生成的图片直接复用）")
    for pid in part_ids:
        cancel.check()
        reason = render_ready_reason(pdir, pid)
        if reason:
            joblog.bound_log(f"{pid}：{reason}，跳过渲染", level="warn")
            results.append({"part": pid, "action": "skipped", "reason": reason})
            continue
        joblog.bound_log(f"{pid}：开始渲染画面")
        try:
            out = render_part(pdir, pid, provider=provider, now=now)
        except cancel.Cancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — 单章失败不影响其它章
            joblog.bound_log(f"{pid}：渲染失败：{exc}", level="error")
            results.append({"part": pid, "action": "failed", "reason": str(exc)})
            continue
        counts = out.get("counts", {})
        joblog.bound_log(f"{pid}：渲染完成（镜头 {counts.get('rendered', 0)}，复用 {counts.get('reused', 0)}，"
                         f"插图 {counts.get('illustration', 0)}，降级 {counts.get('fallback', 0)}）", level="ok")
        results.append({"part": pid, "action": "rendered", "counts": counts})
    done = [r for r in results if r["action"] == "rendered"]
    skip = [r for r in results if r["action"] == "skipped"]
    bad = [r for r in results if r["action"] == "failed"]
    joblog.bound_log(f"批量渲染结束：渲染 {len(done)} 章，跳过 {len(skip)} 章，失败 {len(bad)} 章",
                     level="warn" if bad else "ok")
    append_progress(pdir, f"批量渲染画面：渲染 {len(done)}，跳过 {len(skip)}，失败 {len(bad)}", now)
    if bad:
        raise PlanError("以下章节渲染失败（已完成的章节不受影响）：" +
                        "；".join(f"{r['part']}：{r['reason'][:80]}" for r in bad))
    return {"results": results, "rendered": len(done), "skipped": len(skip), "failed": len(bad)}
