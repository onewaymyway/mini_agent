"""compose/export.py — 成片导出（P5 + P6）。

流程（每步有指纹，未变化则复用）：
  1 片头卡 + 各章 + 片尾卡 → 转场拼接 joined.mp4
  2 主版本：下三分之一条、角标、BGM 混音 → master.mp4（无水印，即 *_clean.mp4 的内容）
  3 最后一步：水印叠加 → <标题>.mp4（改水印只重跑这一步）
输出（exports/）：<标题>.mp4（含水印）、<标题>_clean.mp4（无水印）、<标题>.srt、<标题>_cover.png、<标题>_report.json
中间文件保存在 exports/.work/（可复用）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..planning.runner import load_spine
from ..project_store import load_project, now_iso, read_json, write_json_atomic
from ..render.renderers import render_background
from ..render.themes import canvas_size
from ..validators.compose import check_video, check_watermark
from .branding import (END_SEC, LOWER_AFTER, LOWER_SEC, TITLE_SEC, render_corner_tag, render_credit_card,
                       render_lower_third, render_watermark, resolve_branding, resolve_bgm, watermark_xy)
from .config import FPS, load_compose_config
from .cover import build_cover, cover_subtitle, pick_cover_image
from .ffrun import ComposeError, probe
from .part import video_dir
from .post import copy_with_meta, fingerprint, join, make_still_clip, master_step, watermark_step
from .subtitles import build_srt

_THEME = "ink"


def safe_filename(title: str) -> str:
    import re
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", title or "").strip("._")
    return s[:80] or "video"


def part_offsets(durations: List[float], mode: str, d: float) -> Tuple[List[float], float]:
    """每段在全片中的起点与全片时长。dissolve 时后一段提前 d 秒开始。"""
    starts: List[float] = []
    cur = 0.0
    for k, dur in enumerate(durations):
        start = cur - d if (mode == "dissolve" and k > 0) else cur
        starts.append(start)
        cur = start + dur
    return starts, cur


def _meta(title: str, author: str, source_url: str, ai_text: str) -> List[str]:
    args = ["-metadata", f"title={title}"]
    if author:
        args += ["-metadata", f"artist={author}"]
    comments = []
    if source_url:
        comments.append(f"来源：{source_url}")
    if ai_text:
        args += ["-metadata", f"description={ai_text}"]
    if comments:
        args += ["-metadata", f"comment={'；'.join(comments)}"]
    return args


def _cached(state: Dict[str, Any], step: str, key: str, target: Path) -> bool:
    return state.get(step) == key and target.is_file()


def export_final(pdir: Path, *, part_ids: Optional[List[str]] = None, episode: Optional[Dict[str, Any]] = None,
                 label: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """导出成片。episode（可选）：{index, total, recap, preview}，用于多期的期号与上期回顾/下期预告。
    label（可选）：文件名后缀，如 「_第2期」「_全集」；同时决定中间文件目录，各期互不覆盖。"""
    pdir = Path(pdir)
    spine = load_spine(pdir)
    if spine is None:
        raise ComposeError("还没有大纲")
    ids = part_ids or [p["id"] for p in spine["parts"]]
    missing = [pid for pid in ids if not (video_dir(pdir, pid) / "part.mp4").is_file()]
    if missing:
        raise ComposeError("以下章还没有合成视频：" + "、".join(missing) + "（请先合成本章）")
    infos = {pid: read_json(video_dir(pdir, pid) / "info.json") for pid in ids}
    cfg = load_compose_config(pdir)
    from ..app_settings import load_effective_project  # 延迟导入
    project = load_effective_project(pdir)
    title = project.get("title") or spine.get("title") or "video"
    branding, warnings = resolve_branding(project)
    bgm, bgm_warn = resolve_bgm(pdir, project)
    warnings += bgm_warn
    credits = branding["credits"]
    wm = dict(branding["watermark"])
    # 设计 9.2：斜向平铺是水印的一种位置（diagonal_tile），模式只有 static / drift
    wm["mode"] = "diagonal_tile" if wm.get("position") == "diagonal_tile" else wm.get("mode", "static")
    ai = branding["ai_disclosure"]
    author = branding.get("author") or ""
    source_url = branding.get("source_url") or ""
    source_title = branding.get("source_title") or title
    ai_text = ai["text"] if ai.get("enabled") else ""
    video = project.get("settings", {}).get("video", {})
    orientation = video.get("orientation", "landscape")
    canvas = canvas_size(orientation, video.get("resolution", "1080P"))

    work = pdir / "exports" / ".work" / (safe_filename(label) if label else "main")
    work.mkdir(parents=True, exist_ok=True)
    state_path = work / "state.json"
    state: Dict[str, Any] = read_json(state_path) if state_path.is_file() else {}

    # ---- 1 序列：片头、各章、片尾 ----
    seq: List[Tuple[Path, float]] = []
    holds: List[float] = []   # 每段开头封面的秒数（不淡入，直接显示）
    ep = episode or {}
    ep_total = int(ep.get("total") or 1)
    if credits.get("title_card"):
        lines = [(title, "title")]
        if ep_total > 1:
            lines.append((f"第 {ep.get('index')} 期", "sub"))
        if author:
            lines.append((f"作者：{author}", "sub"))
        if ep.get("recap"):
            lines.append((str(ep["recap"]), "small"))
        png = work / "title_card.png"
        bg = render_background(canvas, _THEME).convert("RGBA")
        bg.alpha_composite(render_credit_card(canvas, orientation, _THEME, lines))
        bg.convert("RGB").save(png)
        clip = work / "intro.mp4"
        key = fingerprint({"png": png.stat().st_size, "sec": TITLE_SEC, "canvas": canvas, "lines": lines})
        if state.get("intro") != key or not clip.is_file():
            make_still_clip(png, TITLE_SEC, clip, canvas)
            state["intro"] = key
        seq.append((clip, TITLE_SEC))
        holds.append(TITLE_SEC)
    for pid in ids:
        seq.append((video_dir(pdir, pid) / "part.mp4", float(infos[pid]["duration"])))
        holds.append(_cover_hold(pdir, pid))
    if credits.get("end_card"):
        lines = [(title, "title")]
        if author:
            lines.append((f"作者：{author}", "sub"))
        if ep.get("preview"):
            lines.append((str(ep["preview"]), "small"))
        if source_url:
            lines.append((source_url, "small"))
        if ai_text:
            lines.append((ai_text, "muted"))
        png = work / "end_card.png"
        bg = render_background(canvas, _THEME).convert("RGBA")
        bg.alpha_composite(render_credit_card(canvas, orientation, _THEME, lines))
        bg.convert("RGB").save(png)
        clip = work / "outro.mp4"
        key = fingerprint({"png": png.stat().st_size, "sec": END_SEC, "canvas": canvas, "lines": lines})
        if state.get("outro") != key or not clip.is_file():
            make_still_clip(png, END_SEC, clip, canvas)
            state["outro"] = key
        seq.append((clip, END_SEC))
        holds.append(END_SEC)

    durations = [d for _, d in seq]
    mode = cfg["transition"] if len(seq) > 1 else "cut"
    d = float(cfg["transition_sec"]) if mode in ("fade", "dissolve") else 0.0
    if mode == "dissolve" and any(dur <= 2 * d for dur in durations):
        raise ComposeError("有段落短于两倍叠化时长，请减小转场时长或改用淡入淡出")
    starts, total = part_offsets(durations, mode, d)

    name = safe_filename(title) + (label or "")
    exports = pdir / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    meta = _meta(title, author, source_url, ai_text)

    # ---- 2 拼接（指纹：序列文件、时长、转场） ----
    joined = work / "joined.mp4"
    jkey = fingerprint({"seq": [(str(p), p.stat().st_size, p.stat().st_mtime_ns, dur) for p, dur in seq],
                        "mode": mode, "d": d, "holds": holds})
    if not _cached(state, "joined", jkey, joined):
        join(seq, mode, d, joined, work / "concat.txt", [], holds=holds)
        state["joined"] = jkey

    # ---- 3 主版本（下三分之一条、角标、BGM） ----
    lower = None
    lt_at = (TITLE_SEC if credits.get("title_card") else 0.0) + LOWER_AFTER
    body_end = total - (END_SEC if credits.get("end_card") else 0.0)  # 下三分之一条不得压到片尾卡
    if credits.get("lower_third") and lt_at + LOWER_SEC <= body_end:
        png = work / "lower_third.png"
        short = source_title if len(source_title) <= 22 else source_title[:21] + "…"
        render_lower_third(canvas, orientation, _THEME, f"原文｜{short}" + (f" · {author}" if author else "")).save(png)
        lower = (png, (lt_at, lt_at + LOWER_SEC))
    corner = None
    if credits.get("corner_tag") and author:
        png = work / "corner.png"
        render_corner_tag(canvas, orientation, _THEME, f"原文作者：{author}").save(png)
        corner = png
    bgm_arg = (Path(bgm["path"]), bgm["volume"]) if bgm["enabled"] else None
    master = work / "master.mp4"
    needs_master = bool(lower or corner or bgm_arg)
    if needs_master:
        mkey = fingerprint({"joined": jkey, "lower": lower and [str(lower[0]), lower[1]],
                            "corner": bool(corner), "bgm": bgm_arg and [bgm_arg[0].name, bgm_arg[1]],
                            "bgm_mtime": bgm_arg and bgm_arg[0].stat().st_mtime_ns, "total": total})
        if not _cached(state, "master", mkey, master):
            master_step(joined, master, total, lower=lower, corner=corner, bgm=bgm_arg, meta=meta)
            state["master"] = mkey
    else:
        master = joined
        state.pop("master", None)

    # ---- 4 无水印版本（_clean） ----
    clean = exports / f"{name}_clean.mp4"
    clean_key = fingerprint({"master": str(master), "size": master.stat().st_size, "meta": meta})
    if not _cached(state, "clean", clean_key, clean):
        copy_with_meta(master, clean, meta)
        state["clean"] = clean_key

    # ---- 5 最后一步：水印 ----
    final = exports / f"{name}.mp4"
    wm_box = None
    if wm.get("enabled"):
        wm_png = work / "watermark.png"
        tile = wm["mode"] == "diagonal_tile"
        mark, size = render_watermark(canvas, orientation, _THEME, wm, pdir, tile=tile)
        mark.save(wm_png)
        wm_size = canvas if tile else size
        if tile:  # 斜铺覆盖整幅画面，框即整个画布
            wm_box = (0, 0, canvas[0], canvas[1])
        else:
            wm_box = (*watermark_xy(canvas, wm_size, wm["position"], wm["margin"]), *wm_size)
        fkey = fingerprint({"master": str(master), "msize": master.stat().st_size, "wm": wm,
                            "wm_file": wm_png.stat().st_size, "canvas": canvas, "total": total})
        if not _cached(state, "final", fkey, final):
            watermark_step(master, final, total, wm_png, mode=wm["mode"], canvas=canvas, size=wm_size,
                           position=wm["position"], margin=wm["margin"], meta=meta)
            state["final"] = fkey
    else:
        fkey = fingerprint({"plain": str(master), "msize": master.stat().st_size, "meta": meta})
        if not _cached(state, "final", fkey, final):
            copy_with_meta(master, final, meta)
            state["final"] = fkey
    write_json_atomic(state_path, state)

    # ---- 字幕、封面、校验 ----
    entries = []
    for k, pid in enumerate(ids):
        off = starts[k + (1 if credits.get("title_card") else 0)]
        for s in infos[pid].get("subtitles", []):
            entries.append({"start": off + float(s["start"]), "end": off + float(s["end"]), "text": s["text"]})
    srt = exports / f"{name}.srt"
    srt.write_text(build_srt(entries), encoding="utf-8")

    # 封面（R16）：插图（若有）+ 标题 / 作者 / 副标题 / AI 声明 / 与成片一致的水印
    cover = exports / f"{name}_cover.png"
    cover_img = build_cover(canvas=canvas, orientation=orientation, title=title, author=author,
                            subtitle=cover_subtitle(spine), ai_text=ai_text, wm=wm, pdir=pdir,
                            source_image=pick_cover_image(pdir, ids))
    cover_img.save(cover)

    pr = probe(final)
    last_sub = max((e["end"] for e in entries), default=0.0)
    verdict = check_video(pr, canvas=canvas, fps=FPS, expected_sec=total, subtitle_end_sec=last_sub)
    wm_verdict = check_watermark(canvas, wm_box)
    verdict["errors"] += wm_verdict["errors"]
    report = {
        "schema": 1, "title": title, "author": author, "parts": ids, "transition": mode,
        "transition_sec": d, "duration": round(total, 3),
        "sequence": [{"kind": k, "start": round(starts[i], 3), "duration": round(durations[i], 3)}
                     for i, k in enumerate(_kinds(credits, ids))],
        "lower_third": None if not lower else [round(lower[1][0], 3), round(lower[1][1], 3)],
        "corner_tag": bool(corner), "bgm": bgm["enabled"],
        "watermark": {"enabled": bool(wm.get("enabled")), "mode": wm["mode"], "box": wm_box},
        "ai_disclosure": ai_text or None,
        "subtitles": len(entries), "warnings": warnings,
        "files": {"video": final.name, "clean": clean.name, "srt": srt.name, "cover": cover.name},
        "checks": verdict, "exported_at": now_iso(now),
    }
    write_json_atomic(exports / f"{name}_report.json", report)
    if verdict["errors"]:
        raise ComposeError("成片校验未通过：" + "；".join(verdict["errors"]))
    raw = load_project(pdir)           # 只写阶段标记：基于原始内容，不写回生效值
    raw["stage"] = "P6_exported"
    write_json_atomic(pdir / "project.json", raw)
    return report


def _cover_hold(pdir: Path, part_id: str) -> float:
    """章开头封面（章首画面）的秒数：来自时间轴 lead_in_sec；没有时间轴时为 0。"""
    from ..audio.synth import load_timeline  # 延迟导入
    tl = load_timeline(pdir, part_id)
    return float(tl.get("lead_in_sec", 0.0)) if tl else 0.0


def _kinds(credits: Dict[str, Any], ids: List[str]) -> List[str]:
    out = []
    if credits.get("title_card"):
        out.append("title_card")
    out += ["chapter"] * len(ids)
    if credits.get("end_card"):
        out.append("end_card")
    return out
