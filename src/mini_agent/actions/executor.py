"""actions/executor.py — Phase 6：ActionExecutor（Sprint 6-1 Tool + Sprint 6-2 Workflow/SubAgent）。

对应 `next_doc/refactor_plan/07-phase6-action-model-sprint-plan.md`。

Sprint 6-1（`type="tool"`）：
  - 只对接 Tool 调用路径：`ActionExecutor.execute(spec)` 内部转发给
    现有 `tools/__init__.py::ToolRegistry.call()`。
  - 权限接入：复用现有 `permissions.py::PermissionGuard.check()`，
    在执行前调用它，不重新实现权限逻辑。

Sprint 6-2（`type="workflow"`/`type="subagent"`，本次新增）：
  - `type="workflow"`：转发给现有 `workflow/store.py::WorkflowStore.load()`
    + `workflow/runner.py::WorkflowRunner.run()`。`capability` 是工作流
    名称，`arguments` 就是传给 `run(inputs=...)` 的动态参数字典
    （与 Tool 分支"`arguments` 对应 `ToolRegistry.call()` 的
    `tool_input`"是同一种映射方式）。`WorkflowRunner.run()` 本身就是
    同步阻塞调用（见该文件顶部文档字符串"后台执行"一节），不需要额外
    处理异步/后台执行语义。
  - `type="subagent"`：**没有**转发给 `orchestrator/task_manager.py::
    TaskManager` + `orchestrator/sub_agent.py::SubAgent`——这一对是
    线程模型（`start()` 非阻塞 + `join()`），且构造强依赖主 Agent 的
    session 生命周期（`session_id`/`session_dir`/`shared_tool_cache`/
    主 Agent memory backend 注入），与 Phase 5 Sprint 5.0.5 评估
    `evolution/objective_executor.py` 时定性的"低耦合但高时序敏感"
    是同一类风险特征，不满足"新增一层纯转发 wrapper、不碰内部时序"的
    止损前提。改为转发给语义上同样是"临时起一个 Agent 跑一次
    prompt"、且本来就是为脱离 `WorkflowRunner` 实例独立调用设计的
    `workflow/agent_spawn.py::build_minimal_agent()` + `Agent.run_turn()`
    （同步调用，无线程/session 耦合），这是本 Sprint 的一次范围调整，
    详见 `07-phase6-action-model-sprint-plan.md` 末尾"Sprint 6-2 执行
    记录"。

止损条件落地方式（见 Phase 6 文档"止损条件"）：`ActionExecutor` 只做
最外层转发——不重新实现 `ToolRegistry.call()`/`PermissionGuard.check()`/
`WorkflowRunner.run()`/`build_minimal_agent()` 内部的任何重试/超时/
审批逻辑，各自的专属逻辑完全留在原模块内部不动。

权限接入范围说明：`permissions.py::PermissionGuard.check(tool_name,
tool_input)` 的签名是 Tool 专属的（按工具名 + 参数判断越权），无法
直接套用到 workflow/subagent 上，因此 Sprint 6-2 新增的两个分支
**不**调用 `PermissionGuard.check()`——而是各自复用自己模块内部已有的
权限收口：workflow 分支的越权防护是 `WorkflowStep.require_approval`/
`step_requires_approval()`（工作流步骤自己的审批门禁）；subagent 分支
是 `build_minimal_agent()` 内部自建的
`PermissionGuard(auto_approve=True, sandbox=..., ...)`（`sandbox` 参数
透传自 `ActionSpec.arguments`）。这是显式的设计决策，不是遗漏，见
`07-phase6-action-model-sprint-plan.md` "Sprint 6-2 执行记录"。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Optional

from mini_agent.core.action import ActionResult, ActionSpec
from mini_agent.core.event_bus import EventBus, get_event_bus
from mini_agent.core.events import Event

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.permissions import PermissionGuard
    from mini_agent.tools import ToolRegistry
    from mini_agent.workflow.store import WorkflowStore


class ActionExecutor:
    """把 `ActionSpec` 转发到对应执行路径，统一产出 `ActionResult`。

    `type="tool"`（Sprint 6-1）：

        result = executor.execute(ActionSpec(
            type="tool", capability="read_file",
            arguments={"path": "README.md"},
        ))

    `type="workflow"`（Sprint 6-2，新增）：

        result = executor.execute(ActionSpec(
            type="workflow", capability="my_workflow_name",
            arguments={"code": "..."},   # 对应 WorkflowRunner.run(inputs=...)
        ))

    `type="subagent"`（Sprint 6-2，新增）：

        result = executor.execute(ActionSpec(
            type="subagent", capability="research_subtask",  # 自由文本标签
            arguments={"prompt": "总结一下 README 的内容"},
        ))

    构造参数：
      registry    — 现有 `tools/__init__.py::ToolRegistry` 实例，
                    `type="tool"` 时用它的 `.call(name, tool_input)`
                    真正执行工具。
      guard       — 现有 `permissions.py::PermissionGuard` 实例，
                    `type="tool"` 执行前调用 `.check(tool_name, tool_input)`；
                    返回 False 时不执行工具，`ActionResult.success=False`
                    且 `error` 以 `"permission_denied: "` 开头。
      event_bus   — 可选，默认使用 `core/event_bus.py` 的进程内单例。
                    传入自定义实例主要供测试隔离用。
      cfg         — 可选，`config.py::AppConfig` 实例。`type="workflow"`/
                    `type="subagent"` 需要它来构造 `WorkflowRunner`/
                    `build_minimal_agent()`；不传且用到这两个分支时会
                    返回 `ActionResult(success=False)`（不是静默跳过，
                    也不抛异常穿透给调用方）。`type="tool"` 不需要它。
      workflow_store — 可选，`workflow/store.py::WorkflowStore` 实例，
                    供测试注入；不传且 `cfg` 已提供时，`type="workflow"`
                    首次用到时惰性用 `WorkflowStore(cfg.project_root)` 构造。
    """

    def __init__(
        self,
        registry: "ToolRegistry",
        guard: "PermissionGuard",
        event_bus: Optional[EventBus] = None,
        cfg: Optional["AppConfig"] = None,
        workflow_store: Optional["WorkflowStore"] = None,
    ) -> None:
        self._registry = registry
        self._guard = guard
        self._event_bus = event_bus if event_bus is not None else get_event_bus()
        self._cfg = cfg
        self._workflow_store = workflow_store

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

        三种 `type` 产出的 `ActionResult` 字段形状完全一致
        （`success`/`output`/`error`/`action_type`/`capability`），
        调用方（含 Phase 3 Experience 记录）不需要按 `type` 特判。
        """
        if spec.type not in ("tool", "workflow", "subagent"):
            raise NotImplementedError(
                f"ActionExecutor: type={spec.type!r} 不是已知取值"
                "（tool/workflow/subagent），见 core/action.py::ActionType"
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

        if spec.type == "tool":
            result = self._execute_tool(spec)
        elif spec.type == "workflow":
            result = self._execute_workflow(spec)
        else:
            result = self._execute_subagent(spec)

        if result.success:
            self._event_bus.publish(Event(
                kind="ActionCompleted",
                payload={"action_type": spec.type, "capability": spec.capability},
                causation_id=started.id,
                correlation_id=effective_correlation_id,
            ))
        else:
            self._event_bus.publish(Event(
                kind="ActionFailed",
                payload={"action_type": spec.type, "capability": spec.capability,
                         "error": result.error},
                causation_id=started.id,
                correlation_id=effective_correlation_id,
            ))
        return result

    # ── type="tool"（Sprint 6-1） ────────────────────────────────────────

    def _execute_tool(self, spec: ActionSpec) -> ActionResult:
        # 权限检查：复用现有 PermissionGuard，不重新实现越权判断逻辑。
        allowed = self._guard.check(spec.capability, dict(spec.arguments))
        if not allowed:
            return ActionResult(
                success=False,
                error=f"permission_denied: {spec.capability}",
                action_type=spec.type,
                capability=spec.capability,
            )

        try:
            output = self._registry.call(spec.capability, dict(spec.arguments))
        except Exception as exc:  # noqa: BLE001 — 转成 ActionResult，不让异常穿透
            return ActionResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                action_type=spec.type,
                capability=spec.capability,
            )

        return ActionResult(
            success=True,
            output=output,
            action_type=spec.type,
            capability=spec.capability,
        )

    # ── type="workflow"（Sprint 6-2） ────────────────────────────────────

    def _get_workflow_store(self) -> "WorkflowStore":
        if self._workflow_store is not None:
            return self._workflow_store
        from mini_agent.workflow.store import WorkflowStore

        self._workflow_store = WorkflowStore(self._cfg.project_root)
        return self._workflow_store

    def _execute_workflow(self, spec: ActionSpec) -> ActionResult:
        if self._cfg is None:
            return ActionResult(
                success=False,
                error="missing_cfg: ActionExecutor 未配置 cfg，无法执行 type=\"workflow\"",
                action_type=spec.type,
                capability=spec.capability,
            )

        store = self._get_workflow_store()
        wf = store.load(spec.capability)
        if wf is None:
            return ActionResult(
                success=False,
                error=f"workflow_not_found: {spec.capability}",
                action_type=spec.type,
                capability=spec.capability,
            )

        from mini_agent.workflow.runner import WorkflowRunner

        try:
            run_result = WorkflowRunner(self._cfg).run(wf, inputs=dict(spec.arguments))
        except Exception as exc:  # noqa: BLE001 — 转成 ActionResult，不让异常穿透
            return ActionResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                action_type=spec.type,
                capability=spec.capability,
            )

        # 只有 "done" 视为 Action 成功；"failed"/"partial"/"paused"/
        # "cancelled" 一律映射为 success=False，具体状态保留在 error 里，
        # 调用方仍可从 output（完整 WorkflowRunResult）里取 step_results
        # 等细节，不因为 success=False 丢失信息。
        if run_result.status == "done":
            return ActionResult(
                success=True,
                output=run_result,
                action_type=spec.type,
                capability=spec.capability,
            )
        return ActionResult(
            success=False,
            output=run_result,
            error=f"workflow_status_not_done: {run_result.status}"
            + (f" ({run_result.error})" if run_result.error else ""),
            action_type=spec.type,
            capability=spec.capability,
        )

    # ── type="subagent"（Sprint 6-2） ────────────────────────────────────

    def _execute_subagent(self, spec: ActionSpec) -> ActionResult:
        if self._cfg is None:
            return ActionResult(
                success=False,
                error="missing_cfg: ActionExecutor 未配置 cfg，无法执行 type=\"subagent\"",
                action_type=spec.type,
                capability=spec.capability,
            )

        arguments: "dict[str, Any]" = dict(spec.arguments)
        prompt = arguments.get("prompt")
        if not prompt:
            return ActionResult(
                success=False,
                error="missing_argument: arguments['prompt'] 是必填项",
                action_type=spec.type,
                capability=spec.capability,
            )

        from mini_agent.workflow.agent_spawn import build_minimal_agent

        cfg = self._cfg
        try:
            agent = build_minimal_agent(
                project_root=cfg.project_root,
                verbose=False,
                sandbox=bool(arguments.get("sandbox", False)),
                model=arguments.get("model"),
                llm_provider=cfg.llm_provider,
                llm_base_url=cfg.llm_base_url,
                api_key=cfg.api_key,
                max_turns=int(arguments.get("max_turns", 10)),
                timeout=arguments.get("timeout"),
                skill_name=arguments.get("skill_name"),
            )
            output = agent.run_turn(prompt)
        except Exception as exc:  # noqa: BLE001 — 转成 ActionResult，不让异常穿透
            return ActionResult(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
                action_type=spec.type,
                capability=spec.capability,
            )

        return ActionResult(
            success=True,
            output=output,
            action_type=spec.type,
            capability=spec.capability,
        )
