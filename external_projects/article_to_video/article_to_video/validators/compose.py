"""validators/compose.py — V6 成片校验（设计文档第 11 节）：时长、分辨率、帧率、音轨、字幕不越界。"""

from __future__ import annotations

from typing import Any, Dict, List

DURATION_TOL = 0.25  # 秒：编码对齐到整帧带来的误差上限


def _fps(stream: Dict[str, Any]) -> float:
    # 用平均帧率：流复制拼接后 r_frame_rate 可能因时间戳不规则而失真（如误报 200）
    num, _, den = str(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1").partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def check_video(probe: Dict[str, Any], *, canvas, fps: int, expected_sec: float,
                subtitle_end_sec: float = None) -> Dict[str, List[str]]:
    errors: List[str] = []
    streams = probe.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        errors.append("成片没有视频流")
    else:
        if (int(video.get("width", 0)), int(video.get("height", 0))) != tuple(canvas):
            errors.append(f"分辨率 {video.get('width')}x{video.get('height')} 与设定 {canvas[0]}x{canvas[1]} 不符")
        if abs(_fps(video) - fps) > 0.05:
            errors.append(f"帧率 {_fps(video):.2f} 与设定 {fps} 不符")
    if audio is None:
        errors.append("成片没有音轨")
    try:
        dur = float((probe.get("format") or {}).get("duration", 0))
    except (TypeError, ValueError):
        dur = 0.0
    if abs(dur - expected_sec) > DURATION_TOL:
        errors.append(f"时长 {dur:.2f} 秒与预期 {expected_sec:.2f} 秒相差超过 {DURATION_TOL} 秒")
    if subtitle_end_sec is not None and subtitle_end_sec > dur + DURATION_TOL:
        errors.append(f"字幕结束于 {subtitle_end_sec:.2f} 秒，超出成片时长")
    return {"errors": errors, "warnings": []}


def check_watermark(canvas, box) -> Dict[str, List[str]]:
    """水印位置在安全区内：完整位于画面中（box = (x, y, w, h)，None 表示未启用水印）。"""
    from ..compose.branding import inside_canvas
    errors: List[str] = []
    if box is None:
        return {"errors": errors, "warnings": []}
    if not inside_canvas(canvas, tuple(int(v) for v in box)):
        errors.append("水印超出画面安全区，请调整位置或缩放")
    return {"errors": errors, "warnings": []}
