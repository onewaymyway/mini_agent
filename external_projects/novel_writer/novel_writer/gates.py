"""novel_writer/gates.py — 挡位 / 覆盖 / 强制检查点 → 每个步骤的闸门（方案第 6 节）。

闸门取值（settings.GATES）：
  manual  不自动运行，等用户点 ▶
  review  运行后停在 waiting_review，等用户批准
  auto    运行并自动继续
  skip    跳过

优先级（高 → 低）：
  1. 强制检查点（方案 6.3）：不能被挡位或覆盖降级成 auto/skip；
     仅 `checkpoints.waive_blueprint_confirm` 可显式豁免蓝图确认；
  2. 单步覆盖 `step_overrides[step_id]`；
  3. 阶段挡位覆盖 `stage_gear_overrides[stage]`；
  4. 全局挡位 `gear` 的预设表。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from novel_writer import steps as S

GATE_MANUAL = "manual"
GATE_REVIEW = "review"
GATE_AUTO = "auto"
GATE_SKIP = "skip"

# 挡位预设表：类别 -> {挡位: 闸门}（逐格对应方案 6.2 表格）
GEAR_GATES: Dict[str, Dict[str, str]] = {
    S.CAT_INFRA:          {"G0": GATE_AUTO,   "G1": GATE_AUTO,   "G2": GATE_AUTO,   "G3": GATE_AUTO},
    S.CAT_DESIGN:         {"G0": GATE_MANUAL, "G1": GATE_REVIEW, "G2": GATE_REVIEW, "G3": GATE_REVIEW},
    S.CAT_BUILD:          {"G0": GATE_MANUAL, "G1": GATE_REVIEW, "G2": GATE_AUTO,   "G3": GATE_AUTO},
    S.CAT_CHECKPOINT:     {"G0": GATE_REVIEW, "G1": GATE_REVIEW, "G2": GATE_REVIEW, "G3": GATE_REVIEW},
    S.CAT_CHAPTER_PREP:   {"G0": GATE_MANUAL, "G1": GATE_AUTO,   "G2": GATE_AUTO,   "G3": GATE_AUTO},
    S.CAT_CHAPTER_WRITE:  {"G0": GATE_MANUAL, "G1": GATE_REVIEW, "G2": GATE_AUTO,   "G3": GATE_AUTO},
    S.CAT_CHAPTER_REVIEW: {"G0": GATE_MANUAL, "G1": GATE_AUTO,   "G2": GATE_AUTO,   "G3": GATE_AUTO},
    S.CAT_ACT:            {"G0": GATE_MANUAL, "G1": GATE_REVIEW, "G2": GATE_REVIEW, "G3": GATE_AUTO},
    S.CAT_REVISE:         {"G0": GATE_MANUAL, "G1": GATE_MANUAL, "G2": GATE_MANUAL, "G3": GATE_MANUAL},
}

# 蓝图确认步骤的 id（可被 waive_blueprint_confirm 豁免）
BLUEPRINT_STEP_ID = "plan_blueprint"


@dataclass(frozen=True)
class GateDecision:
    gate: str
    source: str     # forced / waived / override / stage_gear / gear

    def to_dict(self) -> Dict[str, str]:
        return {"gate": self.gate, "source": self.source}


def resolve_gate(step: S.StepDef, settings: Dict[str, Any]) -> GateDecision:
    override = (settings.get("step_overrides") or {}).get(step.id)
    stage_gears = settings.get("stage_gear_overrides") or {}
    gear = stage_gears.get(step.stage) or settings.get("gear", "G0")
    stage_hit = step.stage in stage_gears

    if step.category == S.CAT_CHECKPOINT:
        waived = step.id == BLUEPRINT_STEP_ID and bool(
            (settings.get("checkpoints") or {}).get("waive_blueprint_confirm")
        )
        if waived:
            return GateDecision(GATE_AUTO, "waived")
        # 覆盖只能让检查点更严格，不能放松
        if override in (GATE_MANUAL, GATE_REVIEW):
            return GateDecision(override, "override")
        return GateDecision(GATE_REVIEW, "forced")

    if override in (GATE_MANUAL, GATE_REVIEW, GATE_AUTO, GATE_SKIP):
        return GateDecision(override, "override")

    table = GEAR_GATES.get(step.category)
    if table is None:
        raise KeyError(f"未知闸门类别 {step.category!r}（步骤 {step.id}）")
    return GateDecision(table[gear], "stage_gear" if stage_hit else "gear")


def gear_table_rows() -> list:
    """给界面/文档用：类别 × 挡位 的预设矩阵。"""
    rows = []
    for cat, cols in GEAR_GATES.items():
        rows.append({"category": cat, **cols})
    return rows
