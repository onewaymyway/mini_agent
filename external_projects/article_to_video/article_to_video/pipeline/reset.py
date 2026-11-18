"""pipeline/reset.py — 整章重置：删除某章从某一阶段起的全部产物（级联）。必须显式确认，不静默覆盖人工编辑。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from ..project_store import append_progress
from .gates import PipelineError

ORDER = ("script", "voice", "storyboard", "render", "compose")
_PATHS = {
    "script": ["script.json", "raw_last.txt"],
    "voice": ["audio", "timeline.json"],
    "storyboard": ["storyboard.json"],
    "render": ["visuals"],
    "compose": ["video"],
}


def reset_part(pdir: Path, part_id: str, *, from_stage: str = "script", confirm: bool = False) -> List[str]:
    """删除本章 from_stage 及之后阶段的产物。images/ 生图缓存保留（重新生图代价高，且按指纹复用）。"""
    if not confirm:
        raise PipelineError("整章重置会删除该章的口播、配音、分镜、画面与视频，请以 confirm=True 确认")
    if from_stage not in ORDER:
        raise PipelineError(f"未知阶段：{from_stage}（可选 {' / '.join(ORDER)}）")
    pdir = Path(pdir)
    cdir = pdir / "parts" / part_id
    removed: List[str] = []
    for stage in ORDER[ORDER.index(from_stage):]:
        for name in _PATHS[stage]:
            target = cdir / name
            if target.is_dir():
                shutil.rmtree(target)
                removed.append(f"{part_id}/{name}/")
            elif target.is_file():
                target.unlink()
                removed.append(f"{part_id}/{name}")
    append_progress(pdir, f"整章重置 {part_id}：从 {from_stage} 起，删除 {len(removed)} 项")
    return removed
