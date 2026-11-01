"""media/ffmpeg.py — 所有视频/音频处理统一走这里。

统一输出参数：竖屏 720x1280、24fps、H.264 + AAC 单声道 24kHz。
每个小场景片段都带音轨（无对白时为静音），保证拼接时流结构一致。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path
from typing import List, Optional, Sequence

W, H, FPS = 720, 1280, 24
AUDIO_RATE = 24000


class MediaError(RuntimeError):
    pass


def _bin(name: str, env: str) -> str:
    path = os.environ.get(env) or shutil.which(name)
    if not path:
        raise MediaError(f"找不到 {name}，请安装 ffmpeg 或设置环境变量 {env}")
    return path


def _run(args: Sequence[str]) -> str:
    proc = subprocess.run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-400:]
        raise MediaError(f"{Path(args[0]).name} 失败：{tail.strip()}")
    return proc.stdout


def ffmpeg() -> str:
    return _bin("ffmpeg", "FFMPEG_BIN")


def ffprobe() -> str:
    return _bin("ffprobe", "FFPROBE_BIN")


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
