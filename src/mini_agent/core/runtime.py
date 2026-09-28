"""core/runtime.py — RuntimeState（Phase 10 S-A A2：从占位填为真实字段）。

见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第二节。原占位文件（Phase 4
Sprint 4-2）的约束是“不提前编造字段，字段来源以真实接入的模块为准”，并把回填时机
留给 Phase 8。Phase 8 已落地 `AgentRuntime.run_once()`，它会发布
`RuntimeCycleStarted` / `RuntimeCycleCompleted` 事件——本文件的每个字段都只来自这两个
事件的 payload / 时间戳，**没有任何字段来自猜测**：

| 字段 | 来源 |
|---|---|
| cycles_started / cycles_completed | 两类事件各自的到达次数 |
| last_cycle_started_at / last_cycle_finished_at | 事件的 `at` |
| last_cycle_status | `RuntimeCycleCompleted.payload["status"]` |
| last_gap_item_count | `RuntimeCycleCompleted.payload["gap_item_count"]` |
| last_learn_summary | `RuntimeCycleCompleted.payload["learn"]`（仅开启 learn 步骤时才有） |

`cycles_started - cycles_completed` 是“正在进行或异常中断”的周期数：`run_once()`
抛异常时不会发布 Completed 事件，所以这个差值大于 0 且没有周期在跑时，说明有周期没走完。

并发：多个周期同时跑时 `last_*` 字段是“最后到达的事件”的值（后写覆盖），计数不受影响。

原方案 §12 里的 Event Loop / Scheduling / Persistence / Recovery / Resource
Management / Permission / Channels 等其余 Runtime 概念**目前没有对应的真实产出方**，
不在此定义（Phase 8 Sprint 8-3/8-5 已评估旧 Scheduler 不适用/暂缓）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class RuntimeState:
    """Autonomous Runtime 的只读运行状态快照（全部由事件驱动更新）。"""

    cycles_started: int = 0
    cycles_completed: int = 0
    last_cycle_status: str = ""
    last_cycle_started_at: Optional[float] = None
    last_cycle_finished_at: Optional[float] = None
    last_gap_item_count: Optional[int] = None
    last_learn_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
