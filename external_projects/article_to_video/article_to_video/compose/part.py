"""compose/part.py — 章级合成：音轨 → 分段图层 → ffmpeg 编码 → ffprobe 校验（V6）→ parts/<章>/video/。

输出：parts/<章>/video/part.mp4、info.json（时长、字幕时间窗，供成片导出与 SRT 使用）。
前置条件：画面已渲染（P4）且与当前分镜一致；时间轴存在（P3）。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .. import cancel
from ..audio.synth import load_timeline
from ..project_store import append_progress, load_project, now_iso, read_json, write_json_atomic
from ..script.writer import load_script
from ..validators.compose import check_video
from ..visual.storyboard import load_storyboard
from .audio_track import build_part_audio
from .config import FPS, load_compose_config
from .ffrun import ComposeError, ffmpeg, probe
from .segments import build_segments
from .subtitles import subtitle_plan
from ..render.themes import canvas_size


def _concat_list(segs, path: Path) -> None:
    lines = []
    for p, dur in segs:
        q = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{q}'\nduration {dur:.6f}")
    last = str(segs[-1][0].resolve()).replace("'", "'\\''")
    lines.append(f"file '{last}'")  # concat 规则：最后一张需重复列出（不带时长）
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def video_dir(pdir: Path, part_id: str) -> Path:
    return Path(pdir) / "parts" / part_id / "video"


def compose_part(pdir: Path, part_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    pdir = Path(pdir)
    part_dir = pdir / "parts" / part_id
    man_path = part_dir / "visuals" / "manifest.json"
    if not man_path.is_file():
        raise ComposeError(f"{part_id} 还没有画面，请先在「🎞️ 分镜」页渲染")
    manifest = read_json(man_path)
    storyboard = load_storyboard(pdir, part_id)
    if storyboard is None or manifest.get("storyboard_input_sha") != storyboard.get("input_sha"):
        raise ComposeError(f"{part_id} 的画面与当前分镜不一致，请重新渲染")
    timeline = load_timeline(pdir, part_id)
    if timeline is None:
        raise ComposeError(f"{part_id} 还没有时间轴（P3）")
    script = load_script(pdir, part_id)
    project = load_project(pdir)
    video = project.get("settings", {}).get("video", {})
    orientation = video.get("orientation", "landscape")
    canvas = canvas_size(orientation, video.get("resolution", "1080P"))
    cfg = load_compose_config(pdir)

    vdir = video_dir(pdir, part_id)
    vdir.mkdir(parents=True, exist_ok=True)
    wav = vdir / "audio.wav"
    total = build_part_audio(part_dir, timeline, wav)
    subs = subtitle_plan(timeline, script, storyboard, cfg["subtitles"])
    segs = build_segments(part_dir, timeline=timeline, manifest=manifest, subs=subs,
                          storyboard=storyboard, cfg=cfg, canvas=canvas, orientation=orientation,
                          seg_dir=vdir / "segments")
    cancel.check()
    lst = vdir / "concat.txt"
    _concat_list(segs, lst)
    out = vdir / "part.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(wav),
            "-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)])
    pr = probe(out)
    verdict = check_video(pr, canvas=canvas, fps=FPS, expected_sec=total)
    if verdict["errors"]:
        raise ComposeError(f"{part_id} 成片校验未通过：" + "；".join(verdict["errors"]))
    info = {
        "schema": 1, "part": part_id, "title": storyboard["title"], "canvas": list(canvas), "fps": FPS,
        "duration": round(total, 3), "segments": len(segs), "rendered_at": now_iso(now),
        "subtitles": [{"id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"]}
                      for s in subs if s["visible"]],
        "subtitle_mode": cfg["subtitles"],
        "warnings": manifest.get("warnings", []),
    }
    write_json_atomic(vdir / "info.json", info)
    append_progress(pdir, f"合成章 {part_id}：{total:.1f} 秒，{len(segs)} 段，字幕 {len(info['subtitles'])} 条", now)
    return info


def preview_part(pdir: Path, part_id: str, seconds: float = 30.0) -> Path:
    """前 N 秒预览（快速编码，不改变章视频）。"""
    src = video_dir(Path(pdir), part_id) / "part.mp4"
    if not src.is_file():
        raise ComposeError(f"{part_id} 还没有合成视频")
    out = video_dir(Path(pdir), part_id) / f"preview_{int(seconds)}s.mp4"
    ffmpeg(["-i", str(src), "-t", f"{seconds:.2f}", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-c:a", "aac", "-b:a", "96k", str(out)])
    return out
