"""audio/timeline.py — 时间轴（纯计算，不调用任何服务）。

每句：start/end 为该句在本章音轨中的位置（秒）；句间留白 gap（段末用 beat 的 pause_after_ms，
句内用 sentence_gap_sec）。章首、章尾留 lead_in / tail。
不变量：end = start + duration；下一句 start = 本句 end + gap；total = 末句 end + 末 gap + tail。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .config import rate_for


def build_timeline(script: Dict[str, Any], durations: Dict[str, float], cfg: Dict[str, Any],
                   voices: Dict[str, str], engines: Dict[str, str]) -> Dict[str, Any]:
    """script：已确认的口播稿；durations：句ID→秒；voices/engines：句ID→音色、实际引擎。"""
    quote_seen = 0
    items: List[Dict[str, Any]] = []
    warnings: List[str] = []
    cursor = float(cfg["lead_in_sec"])
    for b in script["beats"]:
        n = len(b["sentences"])
        for i, s in enumerate(b["sentences"]):
            sid = f"{b['id']}.s{i + 1:02d}"
            dur = float(durations[sid])
            is_last = i == n - 1
            gap = (b["pause_after_ms"] / 1000.0) if is_last else float(cfg["sentence_gap_sec"])
            start = cursor
            end = start + dur
            items.append({
                "id": sid,
                "beat": b["id"],
                "role": b["role"],
                "text": s["text"],
                "quote": bool(s.get("quote")),
                "voice": voices[sid],
                "rate": rate_for(b.get("pace", "normal"), b["role"]),
                "engine": engines[sid],
                "start": round(start, 3),
                "duration": round(dur, 3),
                "end": round(end, 3),
                "gap_after": round(gap, 3),
            })
            cursor = end + gap
    total = round(cursor + float(cfg["tail_sec"]), 3)
    return {
        "schema": 1,
        "part": script["part"],
        "lead_in_sec": cfg["lead_in_sec"],
        "tail_sec": cfg["tail_sec"],
        "total_sec": total,
        "sentences": items,
        "warnings": warnings,
    }


def assign_voices(script: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    """引文声线每章最多 max_quotes_per_part 句，超出的改用主讲音色（避免频繁换声）。"""
    voices: Dict[str, str] = {}
    warnings: List[str] = []
    quotes = 0
    for b in script["beats"]:
        for i, s in enumerate(b["sentences"]):
            sid = f"{b['id']}.s{i + 1:02d}"
            if s.get("quote"):
                quotes += 1
                if quotes <= cfg["max_quotes_per_part"]:
                    voices[sid] = cfg["quote_voice"]
                    continue
                warnings.append(f"{sid} 超出每章引文上限（{cfg['max_quotes_per_part']}），改用主讲音色")
            voices[sid] = cfg["narration_voice"]
    return voices, warnings
