"""pipeline/gates.py — 门控挡位（设计文档 14.3）。

检查点：C0 大纲（outline）、C1 口播（script）、C2 分镜（storyboard）、终审（export）。
  G0（默认）：四个检查点都需要人工确认；
  G1：只有 C0 与终审需要人工，C1、C2 通过校验后自动确认；
  G2：全自动，必须设置预算上限（图像调用数、总耗时），超限即停止。
"""

from __future__ import annotations

from typing import Set

GEARS = ("G0", "G1", "G2")
STAGES = ("outline", "script", "voice", "storyboard", "render", "compose", "export")
_MANUAL = {
    "G0": {"outline", "script", "storyboard", "export"},
    "G1": {"outline", "export"},
    "G2": set(),
}


class PipelineError(RuntimeError):
    pass


def manual_checkpoints(gear: str) -> Set[str]:
    if gear not in _MANUAL:
        raise PipelineError(f"未知挡位：{gear}（可选 G0 / G1 / G2）")
    return set(_MANUAL[gear])


def check_budget_requirement(gear: str, max_images, max_minutes) -> None:
    """G2 必须设置预算上限，否则拒绝启动（设计 14.3）。"""
    if gear == "G2" and max_images is None and max_minutes is None:
        raise PipelineError("G2（全自动）必须设置预算上限：图像调用数或总耗时（分钟）至少一项")
