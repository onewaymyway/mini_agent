"""media/ffmpeg.py — ffmpeg/ffprobe 调用与音频时长（WAV 直接读取，其它格式用 ffprobe）。"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

AUDIO_RATE = 24000


class MediaError(RuntimeError):
    pass


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise MediaError("未找到 ffmpeg：请安装 ffmpeg 并加入 PATH")
    return exe


def find_ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise MediaError("未找到 ffprobe：请安装 ffmpeg（通常包含 ffprobe）并加入 PATH")
    return exe


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def probe_duration(path: Path) -> float:
    if str(path).lower().endswith(".wav"):
        return wav_duration(path)
    out = subprocess.run(
        [find_ffprobe(), "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or not out.stdout.strip():
        raise MediaError(f"无法读取时长：{path}：{out.stderr.strip()[:200]}")
    return float(out.stdout.strip())


def to_wav(src: Path, dst: Path) -> None:
    """转为 24kHz 单声道 16bit WAV。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        [find_ffmpeg(), "-y", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(AUDIO_RATE),
         "-sample_fmt", "s16", str(dst)],
        capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise MediaError(f"ffmpeg 转换失败：{out.stderr.strip()[:300]}")


def write_silence_wav(path: Path, seconds: float, rate: int = AUDIO_RATE) -> float:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(seconds * rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return frames / float(rate)
