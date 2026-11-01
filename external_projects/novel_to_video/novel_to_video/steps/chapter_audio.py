"""steps/chapter_audio.py — C-6 配音。

每个口播块一个 wav（audio/<sid>_b<idx>.wav），块的 audio_key = hash(文本, 音色, TTS 引擎)，
文本或音色变化才会重新合成。镜头音频按块拼接（块间 0.2 秒停顿），并据此确定镜头时长：
  duration = clamp(ceil(口播总时长 + 0.4), 4, 12)
口播总时长超过 11.6 秒的镜头标记为 error，需要人工拆分。
"""

from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Any, Dict, List

from .. import joblog
from ..chapter_io import SHOT_MAX_SEC, SHOT_MIN_SEC, chapter_dir, load_entities, load_shots, mark_stage, save_shots, sha12
from ..providers.tts import TTSProvider, voice_for

GAP_SEC = 0.2
MAX_SPEECH_SEC = SHOT_MAX_SEC - 0.4   # 留出 0.4 秒余量，保证 ceil(speech+0.4) 不超过镜头上限


def _concat_wavs(parts: List[Path], out: Path, gap: float) -> float:
    out.parent.mkdir(parents=True, exist_ok=True)
    params = None
    frames_out = b""
    total = 0.0
    for i, p in enumerate(parts):
        with wave.open(str(p), "rb") as w:
            if params is None:
                params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            frames_out += w.readframes(w.getnframes())
            total += w.getnframes() / w.getframerate()
        if i < len(parts) - 1 and gap > 0:
            frames_out += b"\x00\x00" * int(gap * params[2])
            total += gap
    with wave.open(str(out), "wb") as w:
        w.setnchannels(params[0])
        w.setsampwidth(params[1])
        w.setframerate(params[2])
        w.writeframes(frames_out)
    return total


def run_audio(project_dir: Path, no: int, tts: TTSProvider) -> Dict[str, Any]:
    shots = load_shots(project_dir, no)
    if not shots or not shots.get("shots"):
        raise RuntimeError("缺少镜头规划")
    ents = load_entities(project_dir)
    gender_of = {c["id"]: c.get("gender") for c in ents["characters"]["characters"]}
    adir = chapter_dir(project_dir, no) / "audio"
    synthesized = 0
    engine_usage: Dict[str, int] = {}   # 实际产出每段配音的引擎计数（与 novel-video-studio 的 engine_usage 对应）
    fallback_warned = False
    for sh in shots["shots"]:
        block_keys: List[str] = []
        parts: List[Path] = []
        durations: List[float] = []
        for b in sh["blocks"]:
            role = "narration" if b["type"] == "narration" else "dialogue"
            gender = gender_of.get(b.get("speaker_id")) if role == "dialogue" else None
            voice = voice_for(role, gender)
            key = sha12(b["text"], voice, tts.name)
            path = adir / f"{sh['id']}_b{b['idx']:02d}.wav"
            if not path.is_file() or b.get("audio_key") != key:
                b["duration"] = tts.synth(b["text"], path, voice)
                engine = getattr(tts, "last_engine", None) or tts.name
                engine_usage[engine] = engine_usage.get(engine, 0) + 1
                b["engine"] = engine
                errs = getattr(tts, "last_errors", None) or {}
                if errs and not fallback_warned:
                    fallback_warned = True
                    joblog.log(project_dir, "chapter",
                               f"第 {no} 章 C-6 配音降级：" + "；".join(f"{k}：{v}" for k, v in errs.items())
                               + f"；本段改用 {engine}", level="warn")
                joblog.log(project_dir, "chapter", f"第 {no} 章 C-6 配音 {sh['id']}/{b['idx']:02d}：{voice}，"
                           f"{len(b['text'])} 字 → {float(b['duration']):.1f} 秒（{engine}）")
                b["audio_key"] = key
                synthesized += 1
            block_keys.append(key)
            parts.append(path)
            durations.append(float(b["duration"]))
        speech = sum(durations) + GAP_SEC * max(0, len(durations) - 1)
        sh_audio = adir / f"{sh['id']}.wav"
        _concat_wavs(parts, sh_audio, GAP_SEC)
        sh["audio_key"] = sha12(*block_keys)
        sh["speech_sec"] = round(speech, 2)
        sh["duration_sec"] = int(min(SHOT_MAX_SEC, max(SHOT_MIN_SEC, math.ceil(speech + 0.4))))
        if speech > MAX_SPEECH_SEC:
            sh["error"] = f"口播总时长 {speech:.1f} 秒超过 {MAX_SPEECH_SEC} 秒，请拆分该镜头"
        else:
            sh["error"] = None
    save_shots(project_dir, no, shots)
    errors = [sh["id"] for sh in shots["shots"] if sh.get("error")]
    mark_stage(project_dir, no, "audio", "failed" if errors else "done",
               f"镜头过长：{', '.join(errors)}" if errors else None)
    summary = "、".join(f"{k} {v} 段" for k, v in engine_usage.items()) or "无新合成"
    joblog.log(project_dir, "chapter", f"第 {no} 章 C-6 配音引擎使用：{summary}")
    return {"synthesized_blocks": synthesized, "errors": errors, "engine_usage": engine_usage}
