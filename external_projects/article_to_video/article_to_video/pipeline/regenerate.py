"""pipeline/regenerate.py — 项目级「清空所有，全部重新生成」（R24）。

先从大纲起清空全部产出（大纲、口播、配音、分镜、画面、合成、导出），再按给定的挡位与预算重新运行整条流水线。
  * confirm=True 必须显式确认，否则不删除任何文件；
  * include_images 默认 False：生图缓存保留，重新生图会产生费用；
  * 清空与运行都使用同一套参数（挡位、预算、引擎、供应商），与驾驶舱的「运行流水线」一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .reset import reset_project
from .run import run_pipeline


def regenerate_all(pdir: Path, *, include_images: bool = False, confirm: bool = False,
                   **run_kwargs: Any) -> Dict[str, Any]:
    """清空从大纲起的全部产出，然后重新运行流水线。返回 {"removed": [...], "status": 运行状态}。"""
    removed: List[str] = reset_project(pdir, from_stage="outline", include_images=include_images, confirm=confirm)
    status = run_pipeline(Path(pdir), **run_kwargs)
    return {"removed": removed, "status": status}
