"""audio/config.py — 配音设置（音色、引擎顺序、代理）与语速映射。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

NARRATION_VOICES = ("zh-CN-YunjianNeural", "zh-CN-YunxiNeural", "zh-CN-XiaoyiNeural")
QUOTE_VOICES = ("zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural", "zh-CN-YunxiNeural")
ENGINES = ("cosyvoice", "edge-tts")
DEFAULT_TTS = {
    "narration_voice": "zh-CN-YunjianNeural",
    "quote_voice": "zh-CN-XiaoxiaoNeural",
    "engine_chain": ["cosyvoice", "edge-tts"],
    "edge_proxy": None,
    "use_system_proxy": False,   # 保守默认：不读取系统代理（R13）
    "max_quotes_per_part": 2,
    "lead_in_sec": 0.5,
    "tail_sec": 0.5,
    "sentence_gap_sec": 0.15,
}
PACE_RATE = {"slow": "-10%", "normal": "+0%", "fast": "+10%"}
HOOK_BOOST = "+5%"


def normalize_tts(raw: Dict[str, Any] | None) -> Tuple[Dict[str, Any], List[str]]:
    raw = dict(raw or {})
    warnings: List[str] = []
    out = {**DEFAULT_TTS, **{k: v for k, v in raw.items() if k in DEFAULT_TTS}}
    if out["narration_voice"] not in NARRATION_VOICES:
        warnings.append(f"主讲音色 {out['narration_voice']} 不在可选列表，已改用默认")
        out["narration_voice"] = DEFAULT_TTS["narration_voice"]
    if out["quote_voice"] not in QUOTE_VOICES:
        warnings.append(f"引文音色 {out['quote_voice']} 不在可选列表，已改用默认")
        out["quote_voice"] = DEFAULT_TTS["quote_voice"]
    chain = [e for e in out["engine_chain"] if e in ENGINES]
    if not chain:
        warnings.append("配音引擎顺序为空，已改用默认顺序")
        chain = list(DEFAULT_TTS["engine_chain"])
    out["engine_chain"] = chain
    out["max_quotes_per_part"] = max(0, min(10, int(out["max_quotes_per_part"])))
    out["lead_in_sec"] = max(0.0, min(3.0, float(out["lead_in_sec"])))
    out["tail_sec"] = max(0.0, min(3.0, float(out["tail_sec"])))
    out["sentence_gap_sec"] = max(0.0, min(2.0, float(out["sentence_gap_sec"])))
    out["edge_proxy"] = (out["edge_proxy"] or None)
    out["use_system_proxy"] = bool(out["use_system_proxy"])
    return out, warnings


def rate_for(pace: str, role: str) -> str:
    """句子语速：先按 beat 的 pace；开场（hook）略快。"""
    base = PACE_RATE.get(pace, "+0%")
    if role == "hook" and base == "+0%":
        return HOOK_BOOST
    return base
