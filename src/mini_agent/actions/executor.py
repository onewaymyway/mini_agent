"""actions/executor.py — Phase 6 Sprint 6-1：ActionExecutor（先接 Tool）。

对应 `next_doc/refactor_plan/07-phase6-action-model-sprint-plan.md`
Sprint 6-1 任务表：

  - 只对接 Tool 调用路径：`ActionExecutor.execute(spec)` 内部转发给
    现有 `tools/__init__.py::ToolRegistry.call()`。
  - 权限接入：复用现有 `permissions.py::PermissionGuard.check()`，
    在执行前调用它，不重新实现权限逻辑。
  - 执行结果通过 Phase 2 Event 总线发布 `ActionStarted`/
    `ActionCompleted`/`ActionFailed`，模式与
    `goal_mode/runner.py` 里 `# [Phase 2 Sprint 2-1]` 标注的既有接入点
    一致（共享调用方传入的 `correlation_id`，`causation_id` 指向
    本次 `ActionStarted` 事件的 `id`）。

止损条件落地方式（见 Phase 6 文档"止损条件"）：`ActionExecutor` 只做
最外层转发——不重新实现 `ToolRegistry.call()`/`PermissionGuard.check()`
内部的任何重试/超时/审批逻辑，Tool 调用本身的专属逻辑（比如权限交互式
审批）完全留在 `permissions.py`/`tools/__init__.py` 内部不动。

Sprint 6-1 范围内 `type="workflow"`/`type="subagent"` 两个分支显式抛出
`NotImplementedError`（而不是静默忽略或伪造一个假结果），留给 Sprint
6-2 实现，符合 `12-execution-and-doc-sync-norms.md` 第六节第 4 条
"占位/暂不实现的设计必须显式标注"的要求。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from mini_agent.core.action import ActionResult, ActionSpec
from mini_agent.core.event_bus import EventBus, get_event_bus
from mini_agent.core.events import Event

if TYPE_CHECKING:
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import ToolRegistry


class ActionExecutor:
    """把 `ActionSpec` 转发到对应执行路径，统一产出 `ActionResult`。

    Sprint 6-1 只实现 `type="tool"` 分支：

        result = executor.execute(ActionSpec(
            type="tool", capability="read_file",
            arguments={"path": "README.md"},
        ))

    构造参数：
      registry    — 现有 `tools/__init__.py::ToolRegistry` 实例，
                    `type="tool"` 时用它的 `.call(name, tool_input)`
                    真正执行工具。
      guard       — 现有 `permissions.py::PermissionGuard` 实例，
                    执行前调用 `.check(tool_name, tool_input)`；
                    返回 False 时不执行工具，`ActionResult.success=False`
                    且 `error` 以 `"permission_denied: "` 开头。
      event_bus   — 可选，默认使用 `core/event_bus.py` 的进程内单例。
                    传入自定义实例主要供测试隔离用。
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        guard: "PermissionGuard",
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._event_bus = event_bus if event_bus is not None else get_event_bus()

    def execute(
        self,
        spec: ActionSpec,
        *,
        correlation_id: Optional[str] = None,
        causation_id: Optional[str] = None,
    ) -> ActionResult:
        """执行一个 `ActionSpec`，返回统一的 `ActionResult`。

        `correlation_id`/`causation_id` 透传给 publish 的三类事件——
        调用方（例如把 Phase 5 `detect_gap()` 产出的 gap 转成
        `ActionSpec` 的上层代码）通常应该复用同一次 Goal 闭环的
        `correlation_id`，让 `mini-agent events trace <correlation_id>`
        能把 Goal/Action/Experience 串成一条链（模式与
        `goal_mode/runner.py` 一致）。不传时各自生成一个新的
        `correlation_id`，事件之间仍然自洽（不会出错），只是不会和某个
        既有 Goal 闭环关联在一起。
        """
        if spec.type != "tool":
            # Sprint 6-1 止损条件：不强行提前实现 Sprint 6-2 的分支，
            # 也不返回一个假的"成功"结果掩盖未实现的事实。
            raise NotImplementedError(
                f"ActionExecutor: type={spec.type!r} 留给 Sprint 6-2 实现"
                "（见 07-phase6-action-model-sprint-plan.md），"
                "Sprint 6-1 只支持 type=\"tool\""
            )

        effective_correlation_id = correlation_id or uuid.uuid4().hex

        started = Event(
            kind="ActionStarted",
            payload={"action_type": spec.type, "capability": spec.capability,
                     "arguments": spec.arguments},
            causation_id=causation_id,
            correlation_id=effective_correlation_id,
        )
        self._event_bus.publish(started)

        # 权限检查：复用现有 PermissionGuard，不重新实现越权判断逻辑。
        allowed = self._guard.check(spec.capability, dict(spec.arguments))
        if not allowed:
            result = ActionResult(
                success=False,
                error=f"permission_denied: {spec.capability}",
                action_type=spec.type,
                capability=spec.capability,
            )
            self._event_bus.publish(Event(
                kind="ActionFailed",
                payload={"action_type": spec.type, "capability": spec.capability,
                         "error": result.error},
                causation_id=started.id,
                correlation_id=effective_correlation_id,
            ))
            return result

        try:
            output = self._registry.call(spec.capability, dict(spec.arguments))
        except Exception as exc:  # noqa: BLE001 — 转成 ActionResult，不让异常穿透
            result = ActionResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                action_type=spec.type,
                capability=spec.capability,
            )
            self._event_bus.publish(Event(
                kind="ActionFailed",
                payload={"action_type": spec.type, "capability": spec.capability,
                         "error": result.error},
                causation_id=started.id,
                correlation_id=effective_correlation_id,
            ))
            return result

        result = ActionResult(
            success=True,
            output=output,
            action_type=spec.type,
            capability=spec.capability,
        )
        self._event_bus.publish(Event(
            kind="ActionCompleted",
            payload={"action_type": spec.type, "capability": spec.capability},
            causation_id=started.id,
            correlation_id=effective_correlation_id,
        ))
        return result
