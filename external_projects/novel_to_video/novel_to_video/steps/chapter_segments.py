"""steps/chapter_segments.py — C-8b 动效片段（动态漫模式）。

把每个镜头已有的场景图与配音合成为一段带动效的片段，写入 clips/<镜头>.mp4。
C-9 章节合成读取的正是这个位置，因此合成步骤与视频模式共用。

- 时长：配音时长 + 0.4 秒余量，不设上下限、不裁剪；没有配音的镜头使用设置中的 silent_shot_sec。
- 动效：按镜头在章内的序号解析（auto_alternate 时推近与拉远交替）。
- 缓存键 segment_key = hash(场景图键, 配音键或 silent, 时长, 动效, 强度, 画幅格式, 版本)；
  场景图重新生成或配音变化后，片段自动重建。
- 调用前提：镜头已审核通过（由流水线保证）；本步骤本身只做合成，不改变审核状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from .. import cancel, joblog
from .. import render_mode as RM
from .. import video_format
from ..chapter_io import chapter_dir, load_shots, mark_stage, save_shots, sha12
from ..media import ffmpeg as mf
from ..media import motion as MO
from ..project_store import read_json

SEGMENT_KEY_VERSION = "segment-v1"
TAIL_MARGIN_SEC = 0.4      # 与视频模式的配音余量一致


def segment_seconds(project_dir: Path, no: int, sid: str, silent_sec: float) -> Tuple[float, bool]:
    """返回 (片段时长, 是否有配音)。"""
    wav = chapter_dir(project_dir, no) / "audio" / f"{sid}.wav"
    if wav.is_file():
        return round(mf.probe_duration(wav) + TAIL_MARGIN_SEC, 3), True
    return float(silent_sec), False


def run_segments(project_dir: Path, no: int) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    meta = read_json(project_dir / "project.json")
    cfg = RM.still_settings(meta)
    fmt = video_format.load(project_dir)
    mf.apply_format(fmt)
    data = load_shots(project_dir, no)
    shots = data["shots"]
    cdir = chapter_dir(project_dir, no)
    (cdir / "clips").mkdir(parents=True, exist_ok=True)

    generated, skipped, failed = [], [], []
    for index, sh in enumerate(shots):
        cancel.check()   # 用户请求停止：不再为后续镜头合成
        sid = sh["id"]
        still = (cdir / sh["still_file"]) if sh.get("still_file") else None
        if sh.get("error") or still is None or not sh.get("still_key") or not still.is_file():
            skipped.append(sid)           # 没有可用的场景图，不能合成
            continue
        seconds, has_audio = segment_seconds(project_dir, no, sid, cfg["silent_shot_sec"])
        motion = MO.resolve_motion(cfg["motion"], index)
        seg_key = sha12(sh["still_key"], (sh.get("audio_key") or "audio") if has_audio else "silent",
                        f"{seconds:.3f}", motion, cfg["motion_strength"], fmt.key, SEGMENT_KEY_VERSION)
        out = cdir / "clips" / f"{sid}.mp4"
        if out.is_file() and sh.get("segment_key") == seg_key:
            continue
        audio = (cdir / "audio" / f"{sid}.wav") if has_audio else None
        label = f"第 {no} 章 C-8b 镜头 {sid}"
        joblog.log(project_dir, "chapter",
                   f"{label}：合成动效片段（{motion}，{seconds:.1f} 秒，{'有' if has_audio else '无'}配音）")
        try:
            with joblog.task_label(label):
                MO.render_segment(still, audio, out, seconds=seconds, motion=motion,
                                  strength=cfg["motion_strength"], w=fmt.width, h=fmt.height, fps=fmt.fps)
        except mf.MediaError as exc:
            joblog.log(project_dir, "chapter", f"{label} 失败：{exc}", level="error")
            failed.append(sid)
            continue
        sh["segment_key"] = seg_key
        sh["segment_motion"] = motion
        sh["segment_seconds"] = seconds
        generated.append(sid)
        joblog.log(project_dir, "chapter", f"{label} 完成", level="ok")

    save_shots(project_dir, no, data)
    status = "failed" if failed else "done"
    mark_stage(project_dir, no, "segments", status, f"失败镜头：{', '.join(failed)}" if failed else None)
    return {"generated": generated, "skipped": skipped, "failed": failed}
