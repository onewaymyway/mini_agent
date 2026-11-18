"""compose/export.py — 成片导出：章视频按转场拼接、元数据、全片 SRT、封面，并做 V6 成片校验。

转场：
  cut      直接拼接（流复制，不重新编码）
  fade     每章首尾黑场淡入淡出（重新编码每章，再拼接）
  dissolve 相邻章叠化（xfade / acrossfade），章的起点提前 transition_sec
输出（exports/）：<标题>.mp4、<标题>.srt、<标题>_cover.png、<标题>_report.json
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..planning.runner import load_spine
from ..project_store import append_progress, load_project, now_iso, read_json, write_json_atomic
from ..render.renderers import render_background, render_chapter_card
from ..render.themes import canvas_size
from ..validators.compose import check_video
from .config import FPS, load_compose_config
from .ffrun import ComposeError, ffmpeg, probe
from .part import video_dir
from .subtitles import build_srt

ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k"]


def safe_filename(title: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", title or "").strip("._")
    return s[:80] or "video"


def part_offsets(durations: List[float], mode: str, d: float) -> Tuple[List[float], float]:
    """返回每章在全片中的起点与全片时长。dissolve 时后一章提前 d 秒开始。"""
    starts: List[float] = []
    cur = 0.0
    for k, dur in enumerate(durations):
        start = cur - d if (mode == "dissolve" and k > 0) else cur
        starts.append(start)
        cur = start + dur
    return starts, cur


def _meta(title: str, author: str, source_url: str) -> List[str]:
    args = ["-metadata", f"title={title}"]
    if author:
        args += ["-metadata", f"artist={author}"]
    if source_url:
        args += ["-metadata", f"comment=来源：{source_url}"]
    return args


def _concat(paths: List[Path], lst: Path, out: Path, meta: List[str]) -> None:
    lst.write_text("\n".join(f"file '{str(p.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
                             for p in paths) + "\n", encoding="utf-8")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy"] + meta + [str(out)])


def export_final(pdir: Path, *, part_ids: Optional[List[str]] = None,
                 now: Optional[datetime] = None) -> Dict[str, Any]:
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
    project = load_project(pdir)
    title = project.get("title") or spine.get("title") or "video"
    branding = project.get("branding") or {}
    author = branding.get("author") or ""
    source_url = branding.get("source_url") or ""
    video = project.get("settings", {}).get("video", {})
    orientation = video.get("orientation", "landscape")
    canvas = canvas_size(orientation, video.get("resolution", "1080P"))

    durations = [float(infos[pid]["duration"]) for pid in ids]
    mode = cfg["transition"] if len(ids) > 1 else "cut"
    d = float(cfg["transition_sec"]) if mode in ("fade", "dissolve") else 0.0
    if mode == "dissolve" and any(dur <= 2 * d for dur in durations):
        raise ComposeError("有章短于两倍叠化时长，请减小转场时长或改用淡入淡出")
    starts, total = part_offsets(durations, mode, d)

    exports = pdir / "exports"
    build = exports / ".build"
    shutil.rmtree(build, ignore_errors=True)
    build.mkdir(parents=True, exist_ok=True)
    name = safe_filename(title)
    out = exports / f"{name}.mp4"
    meta = _meta(title, author, source_url)
    inputs = [video_dir(pdir, pid) / "part.mp4" for pid in ids]

    if mode == "cut" or len(ids) == 1:
        _concat(inputs, build / "concat.txt", out, meta)
    elif mode == "fade":
        faded = []
        for i, (src, dur) in enumerate(zip(inputs, durations)):
            dst = build / f"fade_{i:02d}.mp4"
            ffmpeg(["-i", str(src),
                    "-vf", f"fade=t=in:st=0:d={d:.3f},fade=t=out:st={dur - d:.3f}:d={d:.3f}",
                    "-af", f"afade=t=in:st=0:d={d:.3f},afade=t=out:st={dur - d:.3f}:d={d:.3f}"]
                   + ENC + [str(dst)])
            faded.append(dst)
        _concat(faded, build / "concat.txt", out, meta)
    else:  # dissolve
        args: List[str] = []
        for p in inputs:
            args += ["-i", str(p)]
        chain: List[str] = []
        vprev, aprev = "[0:v]", "[0:a]"
        cur = durations[0]
        for k in range(1, len(inputs)):
            off = cur - d
            chain.append(f"{vprev}[{k}:v]xfade=transition=fade:duration={d:.3f}:offset={off:.3f}[v{k}]")
            chain.append(f"{aprev}[{k}:a]acrossfade=d={d:.3f}[a{k}]")
            vprev, aprev = f"[v{k}]", f"[a{k}]"
            cur = off + durations[k]
        ffmpeg(args + ["-filter_complex", ";".join(chain), "-map", vprev, "-map", aprev]
               + ENC + meta + [str(out)])

    # 全片字幕
    entries = []
    for k, pid in enumerate(ids):
        for s in infos[pid].get("subtitles", []):
            entries.append({"start": starts[k] + float(s["start"]), "end": starts[k] + float(s["end"]),
                            "text": s["text"]})
    srt = exports / f"{name}.srt"
    srt.write_text(build_srt(entries), encoding="utf-8")

    # 封面：程序绘制（标题与作者），不依赖任何外部图片
    bg = render_background(canvas, "ink").convert("RGBA")
    card = render_chapter_card({"number": "", "title": title, "one_line": author}, canvas, orientation, "ink")
    bg.alpha_composite(card)
    cover = exports / f"{name}_cover.png"
    bg.convert("RGB").save(cover)

    # V6 成片校验
    pr = probe(out)
    last_sub = max((e["end"] for e in entries), default=0.0)
    verdict = check_video(pr, canvas=canvas, fps=FPS, expected_sec=total, subtitle_end_sec=last_sub)
    report = {
        "schema": 1, "title": title, "author": author, "parts": ids, "transition": mode,
        "transition_sec": d, "duration": round(total, 3), "part_starts": [round(x, 3) for x in starts],
        "subtitles": len(entries), "files": {"video": out.name, "srt": srt.name, "cover": cover.name},
        "checks": verdict, "exported_at": now_iso(now),
    }
    write_json_atomic(exports / f"{name}_report.json", report)
    if verdict["errors"]:
        raise ComposeError("成片校验未通过：" + "；".join(verdict["errors"]))
    project["stage"] = "P5_exported"
    write_json_atomic(pdir / "project.json", project)
    shutil.rmtree(build, ignore_errors=True)
    append_progress(pdir, f"导出成片：{out.name}，{total:.1f} 秒，转场 {mode}", now)
    return report
