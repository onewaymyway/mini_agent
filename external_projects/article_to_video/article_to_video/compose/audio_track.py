"""compose/audio_track.py — 章音轨：按时间轴把每句配音放到准确的时刻，中间留静音（纯 Python，无需 ffmpeg）。"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any, Dict

from ..media.ffmpeg import AUDIO_RATE
from .ffrun import ComposeError


def sentence_wav(part_dir: Path, sid: str) -> Path:
    return part_dir / "audio" / f"{sid}.wav"


def build_part_audio(part_dir: Path, timeline: Dict[str, Any], out_path: Path) -> float:
    """写出整章音轨（24kHz 单声道 16bit）。返回时长（秒）。"""
    rate = AUDIO_RATE
    total = float(timeline["total_sec"])
    n_total = int(round(total * rate))
    buf = bytearray(n_total * 2)
    for item in timeline["sentences"]:
        path = sentence_wav(part_dir, item["id"])
        if not path.is_file():
            raise ComposeError(f"缺少配音文件：{path.name}（请先完成 P3 配音）")
        with wave.open(str(path)) as w:
            if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, rate):
                raise ComposeError(f"配音文件格式不符（需要 24kHz 单声道 16bit）：{path.name}")
            frames = w.readframes(w.getnframes())
        offset = int(round(float(item["start"]) * rate)) * 2
        end = min(len(buf), offset + len(frames))
        buf[offset:end] = frames[: end - offset]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(buf))
    return n_total / rate
