"""mini_agent.runtime — Phase 8（Autonomous Runtime 收敛）。

见 `next_doc/refactor_plan/09-phase8-runtime-convergence-sprint-plan.md`。

Sprint 8-1 产出 `runtime.py::AgentRuntime`（单次循环骨架，
`run_once()`）。Sprint 8-2 新增 `event_loop.py::RuntimeEventLoop`
（持续运行外壳）；旧 Scheduler 接入本 Sprint 评估后暂缓（见
`09-phase8-runtime-convergence-sprint-plan.md` "Sprint 8-2 执行记录"
与"变更记录"），留给后续专门评估，本包暂不新增对应模块。
"""

from .runtime import AgentRuntime, AgentRuntimeResult
from .event_loop import RuntimeEventLoop, RuntimeEventLoopStats

__all__ = [
    "AgentRuntime",
    "AgentRuntimeResult",
    "RuntimeEventLoop",
    "RuntimeEventLoopStats",
]
