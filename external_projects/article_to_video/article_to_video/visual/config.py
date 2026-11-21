"""visual/config.py — 视觉配置：风格锚（visual_bible）、插图预算、生图开关、主题。

保存在 project.json 的 "visual" 字段；缺省值即本期默认。风格锚是所有生图提示词的固定前缀，
其中包含“禁止文字”约束（设计文档 6.4 / 7.3）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
        "images": {"enabled": True, "provider": "agnes", "max_per_minute": 2.5, "size": "1024x1024",
                   "shots_per_image": 2, "use_system_proxy": False},
        # 分镜改进三期：均为保守默认（关闭）。strict_coverage=结构块未被镜头承载则整章失败；
        # subject_from_source=插图主体必须出自原文（原文的具象类比）。
        "storyboard": {"strict_coverage": False, "subject_from_source": False},
        # 新方案排版（R30）：一页内容多、字会被缩得很小时，把元素分成左右两栏（可关闭）
        "deck_layout": {"columns": True},
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
    sb_raw = raw.get("storyboard") if isinstance(raw.get("storyboard"), dict) else {}
    try:
        rate = float(img_raw.get("max_per_minute", base["images"]["max_per_minute"]))
    except (TypeError, ValueError):
        rate = base["images"]["max_per_minute"]
        warnings.append("插图每分钟上限不是数字，已改用默认值")
    rate = min(MAX_PER_MINUTE_LIMIT, max(0.0, rate))
    try:
        shots = int(img_raw.get("shots_per_image", base["images"]["shots_per_image"]))
    except (TypeError, ValueError):
        shots = base["images"]["shots_per_image"]
        warnings.append("每图镜头数不是整数，已改用默认值")
    if not 1 <= shots <= 3:
        warnings.append(f"每图镜头数 {shots} 超出 1–3，已限制在范围内")
    shots = min(3, max(1, shots))
    out = {
        "theme": theme,
        "bible": {"style_anchor": anchor[:300]},
        "images": {
            "enabled": bool(img_raw.get("enabled", base["images"]["enabled"])),
            "provider": str(img_raw.get("provider", base["images"]["provider"])),
            "max_per_minute": round(rate, 2),
            "size": str(img_raw.get("size", base["images"]["size"])),
            "shots_per_image": shots,
            "use_system_proxy": bool(img_raw.get("use_system_proxy", False)),
        },
        "storyboard": {
            "strict_coverage": bool(sb_raw.get("strict_coverage", False)),
            "subject_from_source": bool(sb_raw.get("subject_from_source", False)),
        },
        "deck_layout": {"columns": bool((raw.get("deck_layout") if isinstance(raw.get("deck_layout"), dict) else {})
                                        .get("columns", True))},
    }
    return out, warnings


def load_visual_config(project_dir: Path) -> Dict[str, Any]:
    from ..app_settings import effective_group  # 延迟导入，避免循环依赖
    return effective_group(Path(project_dir), "visual")


def save_visual_config(project_dir: Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    from ..app_settings import effective_group, save_group  # 延迟导入
    project_dir = Path(project_dir)
    warnings = save_group(project_dir, "visual", raw)
    return {"config": effective_group(project_dir, "visual"), "warnings": warnings}


def image_allowance(part_minutes: float, max_per_minute: float, enabled: bool) -> int:
    """单章可用的插图数：floor(章时长 × 每分钟上限)；关闭生图时为 0。"""
    if not enabled:
        return 0
    return int(math.floor(float(part_minutes) * float(max_per_minute) + 1e-9))


def aspect_ratio(orientation: str) -> str:
    return "9:16" if orientation == "portrait" else "16:9"
