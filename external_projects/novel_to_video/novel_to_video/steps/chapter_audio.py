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

from .. import cancel, joblog
from ..chapter_io import chapter_dir, requested_video_seconds, load_entities, load_shots, mark_stage, save_shots, sha12
from ..providers.tts import TTSProvider, sanitize_tts_text, voice_for

GAP_SEC = 0.2


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


MAX_TEXT_REPAIR_ROUNDS = 2   # 单个口播块合成失败后，让 LLM 改写文本并重试的最多轮数

_REPAIR_SYSTEM = "你是中文配音文本修复助手。只输出修复后的口播文本本身，不要解释、不要引号、不要 Markdown。"


def _repair_prompt(text: str, voice: str, err: str) -> str:
    return ("下面这段口播文本在调用 TTS（语音合成）时失败了，请根据错误信息与请求参数修复文本。\n"
            "要求：保留原意与语气；只用汉字、字母、数字和常见中文标点；去掉表情、特殊符号、控制/零宽字符；"
            "如果原文只有标点或符号，改写成一句简短、可朗读的自然口播；长度不要超过原文的 1.2 倍（最多 40 字）。\n"
            f"音色：{voice}\n原文：{text!r}\n错误信息（含请求参数）：\n{err}\n\n只输出修复后的文本。")


def _synth_with_repair(project_dir: Path, no: int, sid: str, b: Dict[str, Any], path: Path,
                       voice: str, tts: TTSProvider, text_llm) -> float:
    """合成一个口播块；失败时把错误和请求参数交给 LLM 改写文本后再试（最多 MAX_TEXT_REPAIR_ROUNDS 轮），
    改写成功会写回块文本（原文保留在 text_original）。仍失败则抛出最后一次的完整错误。"""
    last: Exception | None = None
    for rnd in range(MAX_TEXT_REPAIR_ROUNDS + 1):
        try:
            return tts.synth(b["text"], path, voice)
        except Exception as exc:  # noqa: BLE001 - 任何配音失败都进入修复流程
            last = exc
            joblog.log(project_dir, "chapter",
                       f"第 {no} 章 C-6 配音 {sid}/{b['idx']:02d} 失败（第 {rnd + 1} 次）：{exc}", level="warn")
            if rnd >= MAX_TEXT_REPAIR_ROUNDS:
                break
            new = ""
            if text_llm is not None:
                try:
                    new = sanitize_tts_text(text_llm.ask(_repair_prompt(b["text"], voice, str(exc)),
                                                         system=_REPAIR_SYSTEM).strip().strip("\"'“”"))
                except Exception as llm_exc:  # noqa: BLE001
                    joblog.log(project_dir, "chapter", f"第 {no} 章 C-6 LLM 修复文本失败：{llm_exc}", level="warn")
            if not new:
                new = sanitize_tts_text(b["text"])      # 没有 LLM 或 LLM 无输出：至少做规则清洗
            if not new or new == b["text"]:
                break                                   # 改不动，继续试也是同样的结果
            joblog.log(project_dir, "chapter",
                       f"第 {no} 章 C-6 配音 {sid}/{b['idx']:02d} 文本已修复：{b['text']!r} → {new!r}")
            b.setdefault("text_original", b["text"])
            b["text"] = new
    raise last  # type: ignore[misc]


def run_audio(project_dir: Path, no: int, tts: TTSProvider, text_llm=None) -> Dict[str, Any]:
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
        cancel.check()   # 用户请求停止：不再合成后续配音段
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
                try:
                    b["duration"] = _synth_with_repair(project_dir, no, sh["id"], b, path, voice, tts, text_llm)
                except Exception:
                    save_shots(project_dir, no, shots)   # 已修复的文本先落盘，下次重试不必重复修复
                    raise
                key = sha12(b["text"], voice, tts.name)   # 文本可能已被修复
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
        # 请求视频的时长只需大致对齐口播（取整并限制在接口范围内）；画面合成时再按配音真实时长缩放
        sh["duration_sec"] = requested_video_seconds(speech)
        sh["error"] = None
    save_shots(project_dir, no, shots)
    errors = [sh["id"] for sh in shots["shots"] if sh.get("error")]
    mark_stage(project_dir, no, "audio", "failed" if errors else "done",
               f"配音失败：{', '.join(errors)}" if errors else None)
    summary = "、".join(f"{k} {v} 段" for k, v in engine_usage.items()) or "无新合成"
    joblog.log(project_dir, "chapter", f"第 {no} 章 C-6 配音引擎使用：{summary}")
    return {"synthesized_blocks": synthesized, "errors": errors, "engine_usage": engine_usage}
