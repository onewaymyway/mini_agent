"""compose/config.py — 合成设置（保存在 project.json 的 compose 字段）。

fps 固定 25（与设计方案一致）；转场 cut / fade（黑场淡入淡出）/ dissolve（叠化）；
字幕 auto（卡片已完整呈现同一句时隐藏）/ always / off。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


TRANSITIONS = ("cut", "fade", "dissolve")
SUBTITLE_MODES = ("auto", "always", "off")
FPS = 25


def default_compose() -> Dict[str, Any]:
    return {"transition": "fade", "transition_sec": 0.6, "subtitles": "always",
            "progress_bar": True, "chapter_label": True}


def normalize_compose(raw: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[str]]:
    raw = dict(raw or {})
    base = default_compose()
    warnings: List[str] = []
    transition = raw.get("transition", base["transition"])
    if transition not in TRANSITIONS:
        warnings.append(f"转场 {transition!r} 不合法，已改用 {base['transition']}")
        transition = base["transition"]
    subs = raw.get("subtitles", base["subtitles"])
    if subs not in SUBTITLE_MODES:
        warnings.append(f"字幕模式 {subs!r} 不合法，已改用 {base['subtitles']}")
        subs = base["subtitles"]
    try:
        tsec = float(raw.get("transition_sec", base["transition_sec"]))
    except (TypeError, ValueError):
        tsec = base["transition_sec"]
        warnings.append("转场时长不是数字，已改用默认值")
    tsec = min(2.0, max(0.2, tsec))
    return {
        "fps": FPS,
        "transition": transition,
        "transition_sec": round(tsec, 2),
        "subtitles": subs,
        "progress_bar": bool(raw.get("progress_bar", base["progress_bar"])),
        "chapter_label": bool(raw.get("chapter_label", base["chapter_label"])),
    }, warnings


def load_compose_config(project_dir: Path) -> Dict[str, Any]:
    from ..app_settings import effective_group  # 延迟导入，避免循环依赖
    return effective_group(Path(project_dir), "compose")


def save_compose_config(project_dir: Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    from ..app_settings import effective_group, save_group  # 延迟导入
    project_dir = Path(project_dir)
    warnings = save_group(project_dir, "compose", raw)
    return {"config": effective_group(project_dir, "compose"), "warnings": warnings}
