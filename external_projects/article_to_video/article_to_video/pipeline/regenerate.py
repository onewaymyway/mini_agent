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

_STAGE_NAME = {"outline": "大纲", "script": "口播稿", "voice": "配音", "storyboard": "分镜",
               "render": "画面", "compose": "合成", "export": "导出"}


def regenerate_all(pdir: Path, *, include_images: bool = False, confirm: bool = False,
                   **run_kwargs: Any) -> Dict[str, Any]:
    """清空从大纲起的全部产出，然后重新运行流水线。返回 {"removed": [...], "status": 运行状态}。"""
    removed: List[str] = reset_project(pdir, from_stage="outline", include_images=include_images, confirm=confirm)
    status = run_pipeline(Path(pdir), **run_kwargs)
    return {"removed": removed, "status": status}


def outcome_of(status: Dict[str, Any]) -> Dict[str, str]:
    """流水线运行结果 → 后台任务结果 {state, message}。

    流水线停在等待确认、超出预算或失败时，后台任务不能显示“完成”。
    """
    state = (status or {}).get("state")
    if state == "done":
        outs = status.get("outputs") or []
        return {"state": "done", "message": "完成" + (f"：已导出 {len(outs)} 个视频" if outs else "")}
    if state == "waiting":
        w = status.get("waiting") or {}
        stage = _STAGE_NAME.get(w.get("stage"), w.get("stage") or "")
        return {"state": "waiting",
                "message": f"停在「{stage}」等待人工确认：{w.get('detail', '')}"}
    if state == "stopped":
        return {"state": "stopped", "message": "已停止（未完成的结果不会写入）"}
    text = {"budget_exceeded": "超出预算", "failed": "失败"}.get(state, state or "未知")
    return {"state": "error", "message": f"{text}：{status.get('error') or ''}"}
