"""core/capability.py — Phase 4 Sprint 4-2：CapabilityState 结构占位。

见 `next_doc/refactor_plan/05-phase4-unified-state-sprint-plan.md`
Sprint 4-2。原方案 §9（`00-original-architecture-proposal.md`
"九、Capability"）要求统一描述 `Tool`/`Skill`/`Workflow`/`SubAgent`/
`Strategy`/`Knowledge`/`Environment Access`/`Learned Procedure`，但这些
统一之前分别属于 Phase 6（统一 Action，收敛 Tool/Workflow/SubAgent）
的产出物，本文件不提前假设它们收敛后的字段形状。

# TODO: Phase 6（统一 Action）落地 Tool/Skill/Workflow/SubAgent 的统一
# 表达之后，再回填本文件的真实字段。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CapabilityState:
    """新领域模型中的 Capability 状态占位（Sprint 4-2：无字段，纯占位）。"""
