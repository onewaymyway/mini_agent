"""core/action.py — Phase 6 Sprint 6-1：ActionSpec / ActionResult。

对应 `next_doc/refactor_plan/07-phase6-action-model-sprint-plan.md`
Sprint 6-1 任务表第一项：定义 `ActionSpec(type, capability, arguments,
expected_outcome)` 与 `ActionResult`，用作 `actions/executor.py::
ActionExecutor` 的输入/输出协议。

Sprint 6-1 范围内 `type` 只实际支持 `"tool"`（转发给现有
`tool_executor.py` 依赖的 `tools/__init__.py::ToolRegistry.call()`）。
Sprint 6-2 补齐了 `"workflow"`（转发给 `workflow/runner.py::
WorkflowRunner`）与 `"subagent"`（转发给 `workflow/agent_spawn.py::
build_minimal_agent()` + `Agent.run_turn()`）——本文件当初按
`ActionType` 提前把取值空间定出来的设计验证有效：Sprint 6-2 落地时
`ActionSpec`/`ActionResult` 字段形状没有改动，只在
`actions/executor.py::ActionExecutor.execute()` 里新增了两个分支。

刻意保持极简（不像 `core/events.py::Event` 那样带 `id`/`causation_id`
等追踪字段）：`ActionSpec`/`ActionResult` 是"一次调用的输入输出"，追踪
职责交给调用方在 publish `ActionStarted`/`ActionCompleted`/
`ActionFailed` 事件时自己生成 `id`/`correlation_id`（与
`goal_mode/runner.py` 现有做法一致，见该文件 `# [Phase 2 Sprint 2-1]`
标注的位置），不在这里重复设计一遍。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

# Sprint 6-1 只实现 "tool"；"workflow"/"subagent" 是 Sprint 6-2 的任务，
# 这里先把取值空间定出来（类型标注 + 下面 ActionExecutor 里的显式
# NotImplementedError），避免 Sprint 6-2 需要改 ActionSpec 的字段形状。
ActionType = Literal["tool", "workflow", "subagent"]


@dataclass
class ActionSpec:
    """一次 Action 调用的输入协议（原文 §10/§39）。

    字段：
      type              — Action 类型，Sprint 6-1 只支持 "tool"。
      capability        — 具体能力标识：type="tool" 时是工具名
                           （对应 `ToolRegistry.call(name, ...)` 的
                           `name` 参数）。
      arguments         — 传给该能力的参数字典（type="tool" 时对应
                           `ToolRegistry.call()` 的 `tool_input`）。
      expected_outcome  — 可选，调用方对这次 Action 预期结果的描述
                           （自由文本，供 Phase 3 Experience 记录里的
                           `prediction` 字段使用，Sprint 6-1 本身不
                           校验实际结果是否符合预期）。
    """

    type: ActionType
    capability: str
    arguments: dict = field(default_factory=dict)
    expected_outcome: Optional[str] = None


@dataclass
class ActionResult:
    """一次 Action 调用的输出协议，三种 type 共用同一形状（Sprint 6-2
    验收标准要求："产出的 Experience 记录格式一致，不需要按 Action
    类型特判处理"——Sprint 6-1 先把这个形状定稳）。

    字段：
      success    — 是否成功执行（权限被拒绝也算 success=False，
                   `error` 里注明原因，不是抛异常，方便调用方统一处理）。
      output     — 成功时的返回值（type="tool" 时是 `ToolRegistry.call()`
                   的原始返回值，可能是 str 或 dict，不在这里强制转换，
                   避免丢信息）。
      error      — 失败时的原因描述；权限拒绝时是固定前缀
                   `"permission_denied: "` + 工具名，便于调用方/测试
                   区分"权限拒绝"与"工具执行本身抛异常"两种失败。
      action_type / capability — 回填自 `ActionSpec`，方便调用方在不
                   持有原始 `ActionSpec` 的情况下也能知道这次结果对应
                   哪个 Action。
    """

    success: bool
    output: object = None
    error: Optional[str] = None
    action_type: ActionType = "tool"
    capability: str = ""
