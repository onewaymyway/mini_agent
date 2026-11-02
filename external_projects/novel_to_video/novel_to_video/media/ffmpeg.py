"""media/ffmpeg.py — 所有视频/音频处理统一走这里。

统一输出参数：画面尺寸与帧率由项目的视频格式决定（默认横屏 1280x720、24fps），H.264 + AAC 单声道 24kHz。
每个小场景片段都带音轨（无对白时为静音），保证拼接时流结构一致。
"""

from __future__ import annotations

import subprocess
import os
import wave
from pathlib import Path
from typing import List, Optional, Sequence

from . import binaries
from .. import cancel

W, H, FPS = 1280, 720, 24          # 默认横屏 720P 24fps；实际值由 apply_format() 按项目设置改写


def apply_format(fmt) -> None:
    """把当前项目的视频格式（横屏/竖屏、分辨率、帧率）写入合成参数。进入任何媒体合成前调用。"""
    global W, H, FPS
    W, H, FPS = int(fmt.width), int(fmt.height), int(fmt.fps)


def use_project_format(project_dir) -> None:
    from ..video_format import load
    apply_format(load(project_dir))
AUDIO_RATE = 24000


class MediaError(RuntimeError):
    pass


def _run(args: Sequence[str]) -> str:
    # 经 cancel.run_process 运行：用户请求停止时立即终止 ffmpeg，而不是等它跑完
    returncode, stdout, stderr = cancel.run_process(list(args))
    if returncode != 0:
        tail = (stderr or "")[-400:]
        raise MediaError(f"{Path(args[0]).name} 失败：{tail.strip()}")
    return stdout


def ffmpeg() -> str:
    """ffmpeg 路径，统一由 media.binaries 查找（环境变量 → imageio_ffmpeg → conda → PATH）。"""
    try:
        return binaries.find_ffmpeg()
    except binaries.BinaryNotFound as exc:
        raise MediaError(str(exc)) from exc


def ffprobe() -> str:
    """ffprobe 路径，统一由 media.binaries 查找。"""
    try:
        return binaries.find_ffprobe()
    except binaries.BinaryNotFound as exc:
        raise MediaError(str(exc)) from exc


def probe_duration(path: Path) -> float:
    out = _run([ffprobe(), "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path)])
    try:
        return float(out.strip().splitlines()[0])
    except (ValueError, IndexError) as exc:
        raise MediaError(f"无法读取时长：{path.name}") from exc


def write_silence_wav(path: Path, seconds: float, rate: int = AUDIO_RATE) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(seconds * rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return frames / rate


def _scale_filter() -> str:
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},setsar=1,fps={FPS}")


def still_clip(image: Path, out: Path, seconds: float) -> Path:
    """静帧缓慢推近，生成带静音轨的片段（离线 stub 视频用）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-loop", "1", "-i", str(image),
          "-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=mono",
          "-t", f"{seconds:.3f}",
          "-vf", _scale_filter() + ",zoompan=z='min(zoom+0.0008,1.12)':d=1:"
                 f"s={W}x{H}:fps={FPS}",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    return out


def fit_clip(video: Path, audio: Path, out: Path) -> float:
    """把生成的画面缩放到配音的真实时长（同 novel-video-studio：setpts 快放或慢放），音频保持原样。
    返回画面的缩放系数：<1 为快放，>1 为慢放。时长差几秒可以接受，这里只做对齐，不做截断。"""
    v_dur = probe_duration(video)
    a_dur = probe_duration(audio)
    if v_dur <= 0 or a_dur <= 0:
        raise MediaError(f"无法对齐时长：画面 {v_dur:.2f}s，配音 {a_dur:.2f}s")
    scale = a_dur / v_dur
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-i", str(video), "-i", str(audio),
          "-map", "0:v:0", "-map", "1:a:0",
          "-vf", f"setpts={scale:.6f}*PTS," + _scale_filter(),
          "-af", "apad", "-t", f"{a_dur:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    return scale


def mux_clip(video: Path, audio: Optional[Path], out: Path, seconds: float) -> Path:
    """把视频统一到目标尺寸/帧率，替换为给定音频（无音频则静音），并裁到 seconds。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    if audio is None:
        audio_in = ["-f", "lavfi", "-i", f"anullsrc=r={AUDIO_RATE}:cl=mono"]
        audio_map = ["-map", "1:a:0"]
    else:
        audio_in = ["-i", str(audio)]
        audio_map = ["-map", "1:a:0"]
    _run([ffmpeg(), "-y", "-i", str(video), *audio_in,
          "-map", "0:v:0", *audio_map,
          "-vf", _scale_filter(),
          "-af", "apad", "-t", f"{seconds:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    return out


def concat(clips: List[Path], out: Path) -> Path:
    if not clips:
        raise MediaError("没有可拼接的片段")
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in clips), encoding="utf-8")
    _run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-ar", str(AUDIO_RATE), "-ac", "1", str(out)])
    listing.unlink(missing_ok=True)
    return out


def to_wav(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-i", str(src), "-ar", str(AUDIO_RATE), "-ac", "1", str(dst)])
    return dst
