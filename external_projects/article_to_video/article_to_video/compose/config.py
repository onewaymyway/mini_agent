"""compose/config.py — 合成设置（保存在 project.json 的 compose 字段）。

fps 固定 25（与设计方案一致）；转场 cut / fade（黑场淡入淡出）/ dissolve（叠化）；
字幕 auto（卡片已完整呈现同一句时隐藏）/ always / off。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..project_store import load_project, write_json_atomic

TRANSITIONS = ("cut", "fade", "dissolve")
SUBTITLE_MODES = ("auto", "always", "off")
FPS = 25


def default_compose() -> Dict[str, Any]:
    return {"transition": "fade", "transition_sec": 0.6, "subtitles": "auto",
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
    cfg, _ = normalize_compose(load_project(Path(project_dir)).get("compose"))
    return cfg


def save_compose_config(project_dir: Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    project_dir = Path(project_dir)
    project = load_project(project_dir)
    cfg, warnings = normalize_compose(raw)
    project["compose"] = cfg
    write_json_atomic(project_dir / "project.json", project)
    return {"config": cfg, "warnings": warnings}
