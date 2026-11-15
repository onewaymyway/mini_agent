"""steps/motion_preview.py — 「预览动效」：用一张已有的场景图，按所选动效与转场生成约 3 秒的预览。

预览由两段各 1.5 秒的片段组成（动效按序号 0、1 解析，auto_alternate 因此会先推近后拉远），
之间使用所选的镜间转场拼接。预览只用于挑选设置，不写入章节、不影响审核状态。
输出：exports/previews/motion_preview_<镜头>.mp4（同一镜头重复预览会覆盖）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .. import render_mode as RM
from .. import video_format
from ..chapter_io import chapter_dir, load_shots
from ..media import ffmpeg as mf
from ..media import motion as MO

HALF_SEC = 1.5


def build_preview(project_dir: Path, no: int, sid: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """cfg 为完整的动态漫设置（motion、motion_strength、transition、transition_sec）。"""
    project_dir = Path(project_dir)
    RM.validate_transition(cfg)
    if cfg["motion_strength"] not in MO.STRENGTHS:
        raise ValueError(f"未知的动效强度：{cfg['motion_strength']!r}")
    shots = load_shots(project_dir, no).get("shots") or []
    index, sh = next(((i, s) for i, s in enumerate(shots) if s["id"] == sid), (None, None))
    if sh is None:
        raise KeyError(f"镜头不存在：{sid}")
    cdir = chapter_dir(project_dir, no)
    if not sh.get("still_file") or not (cdir / sh["still_file"]).is_file():
        raise ValueError("该镜头还没有场景图，先生成场景图再预览")
    still = cdir / sh["still_file"]

    fmt = video_format.load(project_dir)
    mf.apply_format(fmt)
    out_dir = project_dir / "exports" / "previews"
    tmp = out_dir / f"_half_{sid}"
    tmp.mkdir(parents=True, exist_ok=True)
    halves = []
    for k in (0, 1):
        motion = MO.resolve_motion(cfg["motion"], k)
        p = tmp / f"half_{k}.mp4"
        MO.render_segment(still, None, p, seconds=HALF_SEC, motion=motion, strength=cfg["motion_strength"],
                          w=fmt.width, h=fmt.height, fps=fmt.fps)
        halves.append(p)
    out = out_dir / f"motion_preview_{sid}.mp4"
    durations = [mf.probe_duration(p) for p in halves]
    kind = cfg["transition"]
    if kind == "fade":
        mf.concat_fade(halves, durations, out, float(cfg["transition_sec"]))
    elif kind == "dissolve":
        mf.concat_dissolve(halves, durations, out, float(cfg["transition_sec"]))
    else:
        mf.concat(halves, out)
    for p in halves:
        p.unlink(missing_ok=True)
    try:
        tmp.rmdir()
    except OSError:
        pass
    return {"path": str(out.relative_to(project_dir)), "seconds": round(mf.probe_duration(out), 2)}
