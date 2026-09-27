"""mini_agent.runtime — Phase 8（Autonomous Runtime 收敛）。

见 `next_doc/refactor_plan/09-phase8-runtime-convergence-sprint-plan.md`。

Sprint 8-1 只产出 `runtime.py::AgentRuntime`（单次循环骨架，
`run_once()`）。持续运行（`event_loop.py`）与旧 Scheduler 接入留给
Sprint 8-2/8-3，本包届时会新增对应模块，不提前占位。
"""

from .runtime import AgentRuntime, AgentRuntimeResult

__all__ = ["AgentRuntime", "AgentRuntimeResult"]
