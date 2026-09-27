"""core/world.py — Phase 4 Sprint 4-2：WorldState 结构占位。

见 `next_doc/refactor_plan/05-phase4-unified-state-sprint-plan.md`
Sprint 4-2：只建空 dataclass 占位，不实现内部更新逻辑，也不提前编造
字段——原方案 §6（`00-original-architecture-proposal.md` "六、World"）
列出的 `Entity`/`State`/`Event`/`Relation`/`Constraint`/`Cause`/
`External Signal`/`Environment` 目前在 mini_agent 里没有任何一个模块
产出对应的真实数据结构，强行现在定义字段属于"猜测字段"，按 Sprint 4-2
止损条件明确禁止。

# TODO: Phase 5（统一 Goal，需要 World 判断"目标是否已达成"）起
# 视真实场景补充字段，字段来源以那时接入的具体模块为准，不在本文件
# 提前假设。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WorldState:
    """新领域模型中的 World 状态占位（Sprint 4-2：无字段，纯占位）。

    与 `core.state_manager.StateManager` 的约定一致：即使没有任何字段，
    也应该能被 `update_state("world", WorldState())` 托管，并且
    `snapshot()` 能正常导出（空 dict），不应该因为"占位、没数据"而报错。
    """
