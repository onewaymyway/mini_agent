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
  4. 全局挡位 `gear` 的预设表；
  5. 挡位表之后的两条「最低审批」规则（M5，方案 6.2 表格中的注释行）：
     - 前三章的章节写作（W8）在 G1 起逐章审批：挡位表若给出 auto，改为 review；
     - 幕末深度审稿（act_review）与全书终审（final_review）在 G3 下仍审批：挡位表若给出 auto，改为 review。
     这两条只提高闸门严格度，且不覆盖显式的单步覆盖（覆盖在第 2 级已返回）。
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
    gate = table[gear]
    source = "stage_gear" if stage_hit else "gear"

    # 最低审批规则（M5）：只能把 auto 提升为 review，不会放松任何闸门
    if gate == GATE_AUTO and _opening_write(step):
        return GateDecision(GATE_REVIEW, "opening")
    if gate == GATE_AUTO and _act_review(step):
        return GateDecision(GATE_REVIEW, "act_review")
    return GateDecision(gate, source)


def _opening_write(step: S.StepDef) -> bool:
    """前三章的章节写作（方案 6.2：前三章 G1–G3 均为逐章 review）。"""
    return (step.category == S.CAT_CHAPTER_WRITE
            and 1 <= step.chapter <= S.OPENING_CHAPTERS)


def _act_review(step: S.StepDef) -> bool:
    """幕末深度审稿（方案 6.2：G3 下仍保留 review，可在覆盖里显式改为 auto）。"""
    return step.id.startswith("act_review:") or step.id == "final_review"


# 各挡位的运行范围（方案 6.2 最后一行「连续运行范围」）。仅用于界面说明与批量入口的文档化，
# 实际停止点由闸门 + 预算共同决定（worker 在遇到 review / manual / 预算时返回）。
RUN_SCOPE: Dict[str, Dict[str, str]] = {
    "G0": {"scope": "single", "label": "单步：每次只跑一步"},
    "G1": {"scope": "checkpoint", "label": "到下个检查点：遇到审批即停"},
    "G2": {"scope": "batch", "label": "每批 N 章：达到批大小即暂停，可续跑"},
    "G3": {"scope": "budget", "label": "到预算上限或全书完成：遇到审批即停"},
}


def gear_table_rows() -> list:
    """给界面/文档用：类别 × 挡位 的预设矩阵。"""
    rows = []
    for cat, cols in GEAR_GATES.items():
        rows.append({"category": cat, **cols})
    return rows
