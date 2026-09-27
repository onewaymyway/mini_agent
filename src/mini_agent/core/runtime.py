"""core/runtime.py — Phase 4 Sprint 4-2：RuntimeState 结构占位。

见 `next_doc/refactor_plan/05-phase4-unified-state-sprint-plan.md`
Sprint 4-2。原方案 §12（`00-original-architecture-proposal.md`
"十二、Runtime"）要求统一 `Event Loop`/`Scheduling`/`Persistence`/
`Recovery`/`Context`/`Resource Management`/`Permission`/`Channels`/
`Background Execution`/`Lifecycle`，对应旧模块
`Daemon`/`Cron`/`AutonomousLoop`/`UnifiedTaskScheduler`/
`ObjectiveExecutor`/`ResourceArbiter` 目前分散在各处、尚未收敛，
这是 Phase 8（重构 Autonomous Runtime）的范围，本文件不提前假设。

# TODO: Phase 8（Autonomous Runtime 收敛）落地后再回填本文件的真实字段。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RuntimeState:
    """新领域模型中的 Runtime 状态占位（Sprint 4-2：无字段，纯占位）。"""
