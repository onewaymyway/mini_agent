"""visual/config.py — 视觉配置：风格锚（visual_bible）、插图预算、生图开关、主题。

保存在 project.json 的 "visual" 字段；缺省值即本期默认。风格锚是所有生图提示词的固定前缀，
其中包含“禁止文字”约束（设计文档 6.4 / 7.3）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..project_store import load_project, write_json_atomic
from ..render.themes import DEFAULT_THEME, THEMES

DEFAULT_STYLE_ANCHOR = (
    "扁平矢量插画风格，柔和渐变，干净留白，色调统一为深蓝与暖金，"
    "画面中不出现任何文字、字母、数字、标志或水印"
)
FORBIDDEN_WORDS = ("写上", "标语", "文案", "字幕", "logo", "水印", "二维码")
MAX_PER_MINUTE_LIMIT = 6.0


def default_visual() -> Dict[str, Any]:
    return {
        "theme": DEFAULT_THEME,
        "bible": {"style_anchor": DEFAULT_STYLE_ANCHOR},
        "images": {"enabled": True, "provider": "agnes", "max_per_minute": 2.5, "size": "1024x1024"},
    }


def normalize_visual(raw: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    raw = dict(raw or {})
    warnings: List[str] = []
    base = default_visual()
    theme = raw.get("theme", base["theme"])
    if theme not in THEMES:
        warnings.append(f"主题 {theme!r} 不存在，已改用 {base['theme']}")
        theme = base["theme"]
    bible_raw = raw.get("bible") if isinstance(raw.get("bible"), dict) else {}
    anchor = str(bible_raw.get("style_anchor", base["bible"]["style_anchor"])).strip()
    if not anchor:
        warnings.append("风格锚为空，已恢复默认（插图禁文字约束不能缺失）")
        anchor = DEFAULT_STYLE_ANCHOR
    if "文字" not in anchor and "字母" not in anchor:
        anchor = anchor + "，画面中不出现任何文字"
    img_raw = raw.get("images") if isinstance(raw.get("images"), dict) else {}
    try:
        rate = float(img_raw.get("max_per_minute", base["images"]["max_per_minute"]))
    except (TypeError, ValueError):
        rate = base["images"]["max_per_minute"]
        warnings.append("插图每分钟上限不是数字，已改用默认值")
    rate = min(MAX_PER_MINUTE_LIMIT, max(0.0, rate))
    out = {
        "theme": theme,
        "bible": {"style_anchor": anchor[:300]},
        "images": {
            "enabled": bool(img_raw.get("enabled", base["images"]["enabled"])),
            "provider": str(img_raw.get("provider", base["images"]["provider"])),
            "max_per_minute": round(rate, 2),
            "size": str(img_raw.get("size", base["images"]["size"])),
        },
    }
    return out, warnings


def load_visual_config(project_dir: Path) -> Dict[str, Any]:
    project = load_project(Path(project_dir))
    cfg, _ = normalize_visual(project.get("visual"))
    return cfg


def save_visual_config(project_dir: Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    project = load_project(project_dir)
    cfg, warnings = normalize_visual(raw)
    project["visual"] = cfg
    write_json_atomic(project_dir / "project.json", project)
    return {"config": cfg, "warnings": warnings}


def image_allowance(part_minutes: float, max_per_minute: float, enabled: bool) -> int:
    """单章可用的插图数：floor(章时长 × 每分钟上限)；关闭生图时为 0。"""
    if not enabled:
        return 0
    return int(math.floor(float(part_minutes) * float(max_per_minute) + 1e-9))


def aspect_ratio(orientation: str) -> str:
    return "9:16" if orientation == "portrait" else "16:9"
