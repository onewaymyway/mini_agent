"""pipeline/reset.py — 整章重置：删除某章从某一阶段起的全部产物（级联）。必须显式确认，不静默覆盖人工编辑。"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

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


# ---- 项目级清空（R18）：大纲、全部章节、成片导出；可选同时清空生图缓存 ----

PROJECT_ORDER = ("outline",) + ORDER + ("export",)   # 大纲 → 口播 → 配音 → 分镜 → 画面 → 合成 → 导出


def _part_ids(pdir: Path) -> List[str]:
    from ..planning.runner import load_spine  # 延迟导入，避免循环
    spine = load_spine(pdir)
    if spine:
        return [p["id"] for p in spine["parts"]]
    root = pdir / "parts"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def reset_project(pdir: Path, *, from_stage: str = "outline", part_ids: Optional[List[str]] = None,
                  include_images: bool = False, confirm: bool = False) -> List[str]:
    """清空从 from_stage 起的产出并准备重新生成（R18）。

    * 大纲（from_stage=outline）会重置全部章节，并删除大纲文件；
    * 导出（exports/）总是一并删除，因为它依赖所有上游产物；
    * include_images=True 同时删除生图缓存（会重新调用生图，产生费用）；默认保留。
    必须 confirm=True。返回已删除的条目。
    """
    if not confirm:
        raise PipelineError("清空会删除产出，请以 confirm=True 确认")
    if from_stage not in PROJECT_ORDER:
        raise PipelineError(f"未知阶段：{from_stage}（可选 {' / '.join(PROJECT_ORDER)}）")
    pdir = Path(pdir)
    removed: List[str] = []

    def _drop(target: Path, label: str) -> None:
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(label + "/")
        elif target.is_file():
            target.unlink()
            removed.append(label)

    if from_stage == "outline":
        _drop(pdir / "plan" / "spine.json", "plan/spine.json")
        part_ids = _part_ids(pdir)          # 大纲变化 → 全部章节的产出都过期
    else:
        part_ids = list(part_ids) if part_ids is not None else _part_ids(pdir)

    start = PROJECT_ORDER.index(from_stage)
    part_stages = [s for s in ORDER if PROJECT_ORDER.index(s) >= start]
    for pid in part_ids:
        cdir = pdir / "parts" / pid
        for stage in part_stages:
            for name in _PATHS[stage]:
                _drop(cdir / name, f"{pid}/{name}")
        if include_images and PROJECT_ORDER.index(from_stage) <= PROJECT_ORDER.index("render"):
            _drop(cdir / "images", f"{pid}/images")
    _drop(pdir / "exports", "exports")
    append_progress(pdir, f"项目级清空：从 {from_stage} 起，章 {len(part_ids)} 个，删除 {len(removed)} 项"
                          + ("（含生图缓存）" if include_images else ""))
    return removed
