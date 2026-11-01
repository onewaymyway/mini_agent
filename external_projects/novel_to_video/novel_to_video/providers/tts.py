"""providers/tts.py — 配音 provider。

  * SilenceTTS（stub）：按字数估算时长写静音 wav，离线可用，时长估算用于流程验证；
  * EdgeTTS：edge-tts 在线语音（需 pip install edge-tts 且网络可达）。CosyVoice 本地模型尚未接入。
返回实际时长（秒）。
"""

from __future__ import annotations

import asyncio
import re
from abc import ABC, abstractmethod
from pathlib import Path

from ..media import ffmpeg as mf

CHARS_PER_SEC = 4.2  # 中文口播的粗略语速，仅用于 stub 与规划估算

VOICE_BY_GENDER = {
    ("narration", None): "zh-CN-YunjianNeural",
    ("male", None): "zh-CN-YunxiNeural",
    ("female", None): "zh-CN-XiaoxiaoNeural",
    ("unknown", None): "zh-CN-XiaoyiNeural",
}


def voice_for(role: str, gender: str | None) -> str:
    """role: narration | dialogue；gender 决定对白音色。"""
    if role == "narration":
        return VOICE_BY_GENDER[("narration", None)]
    return VOICE_BY_GENDER.get((gender or "unknown", None), VOICE_BY_GENDER[("unknown", None)])


def estimate_seconds(text: str) -> float:
    return max(0.6, len(re.sub(r"\s", "", text)) / CHARS_PER_SEC)


class TTSProvider(ABC):
    name = "base"

    @abstractmethod
    def synth(self, text: str, out_wav: Path, voice: str) -> float: ...


class SilenceTTS(TTSProvider):
    name = "stub"

    def synth(self, text: str, out_wav: Path, voice: str) -> float:
        return mf.write_silence_wav(out_wav, estimate_seconds(text))


class EdgeTTS(TTSProvider):
    name = "edge-tts"

    def __init__(self, timeout: int = 120):
        try:
            import edge_tts  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("未安装 edge-tts：pip install edge-tts") from exc
        self.timeout = timeout

    def synth(self, text: str, out_wav: Path, voice: str) -> float:
        import edge_tts
        mp3 = out_wav.with_suffix(".mp3")
        mp3.parent.mkdir(parents=True, exist_ok=True)

        async def _run():
            await asyncio.wait_for(edge_tts.Communicate(text, voice).save(str(mp3)), self.timeout)

        asyncio.run(_run())
        mf.to_wav(mp3, out_wav)
        mp3.unlink(missing_ok=True)
        return mf.probe_duration(out_wav)
