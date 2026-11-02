"""steps/chapter_compose.py — C-9 章节合成：所有镜头都通过审核后，按顺序拼接为 chapter_NNN.mp4，并烧录字幕。

字幕（默认开启，项目选项 subtitles.enabled）：
  * 时间轴按台词块与镜头的真实时长生成，见 media/subtitles.py；
  * 对白前加说话人名，旁白原样；
  * 关闭字幕时直接输出拼接结果，与以前的行为一致。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict

from ..chapter_io import chapter_dir, load_entities, load_shots, mark_stage
from ..media import ffmpeg as mf
from ..media import subtitles as sub
from ..project_store import read_json
from ..shot_review import shot_states
from ..video_format import load as load_format


def subtitle_settings(project_dir: Path) -> Dict[str, Any]:
    """读取字幕设置。旧项目没有 subtitles 键时按默认值（开启）处理。"""
    try:
        cfg = read_json(Path(project_dir) / "project.json").get("subtitles") or {}
    except (OSError, ValueError):
        cfg = {}
    size = cfg.get("font_size")
    return {"enabled": bool(cfg.get("enabled", True)),
            "font_size": int(size) if size else None}


def speaker_names(project_dir: Path) -> Dict[str, str]:
    """角色库中 id → 显示名（字幕中对白的说话人）。"""
    ents = load_entities(project_dir)
    out: Dict[str, str] = {}
    for c in ents["characters"]["characters"]:
        names = c.get("names") or []
        if c.get("id") and names:
            out[c["id"]] = str(names[0])
    return out


def run_compose(project_dir: Path, no: int) -> Dict[str, Any]:
    shots = load_shots(project_dir, no)
    states = shot_states(project_dir, no)
    not_ready = [sid for sid, st in states.items() if st != "approved"]
    if not_ready:
        mark_stage(project_dir, no, "compose", "waiting", "等待审核：" + "、".join(not_ready))
        return {"waiting": not_ready}
    cdir = chapter_dir(project_dir, no)
    clips = [cdir / "clips" / f"{sh['id']}.mp4" for sh in shots["shots"]]
    missing = [str(p.name) for p in clips if not p.is_file()]
    if missing:
        raise RuntimeError(f"缺少小场景视频：{', '.join(missing)}")
    out = cdir / f"chapter_{no:03d}.mp4"
    settings = subtitle_settings(project_dir)
    if not settings["enabled"]:
        mf.concat(clips, out)
        info: Dict[str, Any] = {"enabled": False}
    else:
        raw = cdir / "chapter_nosub.mp4"
        mf.concat(clips, raw)
        durations = [mf.probe_duration(c) for c in clips]
        cues, total = sub.build_cues(shots["shots"], durations, speaker_names(project_dir))
        fmt = load_format(project_dir)
        info = {"enabled": True,
                **sub.burn(raw, out, cues, total, fmt.width, fmt.height, cdir / "subs", settings["font_size"])}
        raw.unlink(missing_ok=True)
        shutil.rmtree(cdir / "subs", ignore_errors=True)
    mark_stage(project_dir, no, "compose", "done")
    return {"waiting": [], "output": str(out), "duration": round(mf.probe_duration(out), 2),
            "subtitles": info}
