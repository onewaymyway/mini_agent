"""render/themes.py — 主题（配色）与画幅尺寸。

画幅与 P5 的 video_format 约定一致：横屏 16:9、竖屏 9:16；720P / 1080P。
"""

from __future__ import annotations

from typing import Dict, Tuple

THEMES: Dict[str, Dict[str, object]] = {
    "ink": {
        "bg_top": (14, 22, 38),
        "bg_bottom": (30, 48, 74),
        "text": (240, 236, 226, 255),
        "muted": (165, 180, 200, 255),
        "accent": (217, 164, 65, 255),
        "card": (255, 255, 255, 22),
        "line": (120, 140, 170, 255),
    },
}
DEFAULT_THEME = "ink"

_LANDSCAPE = {"720P": (1280, 720), "1080P": (1920, 1080)}
_PORTRAIT = {"720P": (720, 1280), "1080P": (1080, 1920)}


def canvas_size(orientation: str, resolution: str) -> Tuple[int, int]:
    table = _PORTRAIT if orientation == "portrait" else _LANDSCAPE
    return table.get(resolution, table["1080P"])


def unit_for(canvas: Tuple[int, int], orientation: str) -> float:
    """字号与间距的基准单位：横屏以 1920 宽为 1.0，竖屏以 1080 宽为 1.0。"""
    w = canvas[0]
    return w / (1080.0 if orientation == "portrait" else 1920.0)


def get_theme(name: str) -> Dict[str, object]:
    return THEMES.get(name, THEMES[DEFAULT_THEME])
