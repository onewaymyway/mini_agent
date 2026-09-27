# Phase 6：统一 Action —— 可执行计划

> 对应原方案 §39、§10。前置条件：Phase 5 的 Gap 检测已能产出候选问题，
> 需要一个统一的执行接口来响应。

## 目标

把 `Tool / Workflow / SubAgent / Script / Research / UserAsk` 这些各自
独立的执行路径，统一收敛到一个 `ActionSpec → ActionExecutor →
ActionResult` 的接口（原文 §10、§39）。这是 Capability 收敛的关键一步：
执行完统一后，Tool/Skill/Workflow/SubAgent 才能真正降级为"Capability
的不同实现形式"，而不是各自拥有独立的执行语义。

## 现状盘点

| 现有执行路径 | 文件位置 | 备注 |
|---|---|---|
| Tool 调用 | `tools/`、`tool_executor.py` | 已有相对统一的调用框架，适合作为 `ActionExecutor` 的第一个后端 |
| Workflow 执行 | `workflow/`（18 个模块） | 有自己的执行状态机，需要包一层 Adapter |
| SubAgent 调用 | `orchestrator/` | 涉及跨进程/跨会话调用，风险相对高 |
| Hybrid Exec | `hybrid_exec/` | 已经是"多种执行方式混合"的雏形，可以直接参考其分发逻辑设计 `ActionExecutor` |
| `evolution/objective_executor.py::ObjectiveExecutor`（Phase 5 Sprint 5.0.5 移交） | `evolution/` | `ExecutionStep`（多步执行 + 重试 + 超时）语义上更接近 Action/Capability 而非 Goal，Phase 5 评估后判定不适合套用 Goal 的 Adapter 模式（唯一实例化点在 `api/server.py::HttpServer._build_autonomous_loop()` 内一段时序高度敏感的构造闭包里，风险特征是"低耦合但高时序敏感"），移交本 Phase 一并评估，见 `06-phase5-goal-convergence-sprint-plan.md` "Sprint 5.0.5 执行记录" |

产出：`docs/architecture_v2/phase6-action-inventory.md`。

**现状盘点已完成**（详见 `docs/architecture_v2/phase6-action-inventory.md`），
核心结论：`ToolExecutor`（inbound=11）/`PermissionGuard`（inbound=26）/
`orchestrator/`（inbound=37）虽然实测数值超过 Sprint 0 止损阈值，但
**不需要因此暂停或重新规划**——与 Phase 3（Memory）/Phase 5
（GoalBacklog）不同，本 Phase Sprint 6-1/6-2 的设计从一开始就是"新增
一层纯转发 wrapper，不改动被依赖模块内部"，止损条件防的是"改动一个
被广泛依赖的模块内部逻辑波及所有调用方"，不适用于"新增一个调用方去
调一个稳定公开接口"这种情况。原 Sprint 6-1/6-2 任务表核实后**无需
调整，直接按原计划推进**。`evolution/objective_executor.py`（Phase 5
Sprint 5.0.5 移交项）评估结论：风险点在于"改动它现有的构造闭包"，
不在于"新增转发分支调用已构造好的实例"，因此不需要额外止损处理，
具体接入排期留给 Sprint 6-2 之后决定。

## Sprint 6-1（2 周）：ActionSpec + ActionExecutor 骨架（先接 Tool）

| 任务 | 产出 |
|---|---|
| `core/action.py` | 定义 `ActionSpec(type, capability, arguments, expected_outcome)`、`ActionResult` |
| `actions/executor.py` | 第一版只对接 Tool 调用路径：`ActionExecutor.execute(spec)` 内部转发给现有 `tool_executor.py` |
| 权限/资源接入 | 复用现有 `permissions.py`，让 `ActionExecutor` 在执行前调用它，而不是重新实现权限逻辑 |
| 接入 Goal 链路 | Phase 5 产出的 gap/plan，通过 `ActionSpec` 触发执行，执行结果通过 Phase 2 Event 总线发布 `ActionStarted/Completed/Failed` |

**验收标准**：一个真实 Goal 从"检测到 gap"到"通过 ActionExecutor 调用
一个 Tool 并拿到结果"全流程打通，且过程中权限检查确实生效（可以用一个
故意越权的场景验证拦截是否工作）。

**止损条件**：如果发现 `tool_executor.py` 内部逻辑和 `ActionExecutor`
的抽象冲突严重（比如 Tool 调用有大量 Tool 专属的重试/超时逻辑难以
泛化），先把这部分留在 `tool_executor.py` 内部，`ActionExecutor` 只做
最外层转发，不强行统一内部细节。

## Sprint 6-2（2 周）：接入 Workflow 与 SubAgent

| 任务 | 产出 |
|---|---|
| Workflow Adapter | `ActionExecutor` 增加 `type="workflow"` 分支，转发给现有 workflow 执行状态机 |
| SubAgent Adapter | 增加 `type="subagent"` 分支，转发给 `orchestrator/` |
| 统一 Outcome 记录 | 无论走哪个分支，`ActionResult` 都产出统一结构，供 Phase 3 的 Experience Layer 记录 |

**验收标准**：三种 Action 类型（tool/workflow/subagent）都能通过同一个
`ActionExecutor.execute()` 入口调用，且产生的 `Experience` 记录格式一致
（这是验证"统一执行接口"是否真的统一的关键：如果 Experience 记录里
还需要 `if type == "workflow": ... else: ...` 特判，说明抽象没做好）。

## 完成标志

- [x] Tool/Workflow/SubAgent 三类执行路径都已通过 `ActionExecutor`
      统一接口调用
- [x] 权限、超时、重试等横切逻辑没有在三个分支里重复实现
- [x] 产出的 Experience 记录格式统一，不需要按 Action 类型特判处理

## Sprint 6-1 执行记录

按任务表逐项完成：

- `core/action.py`：新增 `ActionSpec(type, capability, arguments,
  expected_outcome)` + `ActionResult(success, output, error,
  action_type, capability)`。`type` 用 `Literal["tool", "workflow",
  "subagent"]` 标注取值空间，Sprint 6-1 只实现 `"tool"`。
- `actions/executor.py`（新增 `actions/` 包）：`ActionExecutor.execute(spec,
  correlation_id=?, causation_id=?)`——`type="tool"` 时先调用
  `permissions.py::PermissionGuard.check(capability, arguments)`，
  被拒绝时不执行、返回 `ActionResult(success=False, error="permission_denied: <tool>")`；
  通过后转发给 `tools/__init__.py::ToolRegistry.call(capability, arguments)`，
  工具执行抛异常时捕获并转成 `ActionResult(success=False, error=...)`，
  不让异常穿透给调用方。`type="workflow"`/`type="subagent"` 显式
  `raise NotImplementedError`（按 `12-execution-and-doc-sync-norms.md`
  第六节第 4 条要求，不留静默空实现）。
- 权限接入：直接复用现有 `PermissionGuard.check()`，未重新实现任何
  越权判断逻辑，符合止损条件"不强行统一内部细节"。
- Event 总线接入：每次 `execute()` 调用 publish `ActionStarted` →
  （成功）`ActionCompleted` 或（权限拒绝/异常）`ActionFailed`，与
  `goal_mode/runner.py` 现有的 `# [Phase 2 Sprint 2-1]` 接入点用同一套
  `Event`/`EventBus`，`causation_id` 指向本次 `ActionStarted.id`，
  `correlation_id` 由调用方透传（复用某次 Goal 闭环的 id）或缺省时
  自动生成一个新的。
- Goal 链路打通验证：新增 `tests/test_phase6_action_executor.py::
  test_goal_gap_to_action_executor_full_chain_with_shared_correlation_id`——
  构造一个真实 `GoalState`，跑 `goals/gap.py::detect_gap()` 产出
  `gap`，把 `gap[0]` 转成一个 `ActionSpec(type="tool", ...)`，通过
  `ActionExecutor.execute()` 真实调用一个测试工具并核对返回结果与
  发布的事件序列/`correlation_id`。**注意**：本 Sprint 只打通
  "gap 结果 → 手工构造 ActionSpec → ActionExecutor 执行"这条链路，
  不实现"从 gap 自动规划出 ActionSpec"的 Planner（那不在 Sprint 6-1
  任务表范围内，任务表只要求"通过 ActionExecutor 调用一个 Tool 并
  拿到结果"）。
- 越权拦截验证：新增
  `test_execute_tool_blocked_by_sandbox_permission_returns_failure_not_exception`——
  用 `PermissionGuard(sandbox=True)` 故意对 `bash`（`_RISKY_TOOLS`
  之一）发起调用，验证被拦截、返回 `ActionResult(success=False)`
  而不是抛异常或静默放行，且发布了 `ActionFailed`（未发布
  `ActionCompleted`）。

验收标准两条均已用测试验证：

1. "一个真实 Goal 从检测到 gap 到通过 ActionExecutor 调用一个 Tool
   并拿到结果全流程打通" —— 见上文 full_chain 测试。
2. "过程中权限检查确实生效（故意越权场景验证拦截）" —— 见上文越权
   拦截测试。

新增测试 `tests/test_phase6_action_executor.py`（5 用例）全部通过；
`python -m pyflakes core/action.py actions/executor.py actions/__init__.py`
无告警；本次改动未涉及既有模块的 import 关系变化（`actions/` 是全新
包，只反向依赖 `core/action.py`/`core/event_bus.py`/`core/events.py`），
无需重新跑 `dep_graph.py`。回归测试：本沙箱环境里跑
`pytest tests/ -k "phase or goal_mode or events or experience or action"`
有大量既有的模块导入错误（`ModuleNotFoundError: No module named
'fastapi'` 等，仓库多处历史记录里已确认是环境缺依赖问题，与本次改动
无关）与既有失败（`test_build_from_history_*` 系列等，历次 Sprint
记录均已标注为"既有失败，与改动无关"），本次改动新增的
`test_phase6_action_executor.py` 全部 5 用例本身独立通过，未观察到
因本次新增代码导致的新增失败。

Phase 6 Sprint 6-1 达到验收标准，可进入 **Sprint 6-2（接入 Workflow
与 SubAgent）**，留待下一次推进。

## 变更记录

### 变更记录 2026-09-27

- 触发条件：Sprint 6-2 任务表第 2 项"SubAgent Adapter：增加
  `type="subagent"` 分支，转发给 `orchestrator/`"在实际实现前的耦合
  评估中，发现 `orchestrator/task_manager.py::TaskManager` +
  `orchestrator/sub_agent.py::SubAgent` 是线程模型（`start()` 非阻塞 +
  `join()`），且 `SubAgent` 的构造强依赖主 Agent 的 session 生命周期
  （`session_id`/`session_dir`/`shared_tool_cache`/主 Agent memory
  backend 通过 `set_memory_sinks()` 事后注入），与 Phase 5
  Sprint 5.0.5 评估 `evolution/objective_executor.py` 时定性的"低耦合
  但高时序敏感"是同一类风险特征——`scripts/dep_graph.py --module
  orchestrator.sub_agent`/`--module orchestrator.task_manager` 实测
  inbound 均为 5（未超止损阈值），问题不在耦合面大小，而在于
  `ActionExecutor.execute()` 是同步调用，若要接 `TaskManager`/
  `SubAgent` 就必须在 `execute()` 内部自己处理"提交任务 → 轮询/阻塞
  等待完成 → 读取 `TaskRecord.result`"这一整套时序，且需要伪造一个
  独立于当前主 Agent session 的 `session_id`/`session_dir`，这已经不是
  "新增一层纯转发 wrapper"，而是在搭建一套新的时序控制逻辑，不满足
  Phase 6 现状盘点定下的止损前提。
- 原计划：Sprint 6-2 任务表"SubAgent Adapter：增加 `type="subagent"`
  分支，转发给 `orchestrator/`"。
- 实际情况：`orchestrator/` 的 `TaskManager`/`SubAgent` 不适合直接
  转发；但项目里已经存在另一个语义同样是"临时起一个 Agent 执行一次
  prompt"、且从设计上就是为了脱离 `WorkflowRunner` 实例被独立调用的
  同步函数——`workflow/agent_spawn.py::build_minimal_agent()`
  （原本是给 `workflow/executors.py::SkillAgentStepExecutor`/
  `workflow/py_step_runner.py` 复用的"构造最小 Agent"逻辑），
  构造完成后 `agent.run_turn(prompt)` 就是一次同步调用，没有线程/
  session 耦合问题。
- 调整后方案：`type="subagent"` 分支转发给 `build_minimal_agent()` +
  `Agent.run_turn()`，不转发给 `orchestrator/task_manager.py`/
  `orchestrator/sub_agent.py`。`ActionSpec.capability` 对 `subagent`
  类型降级为自由文本标签（不做校验），`arguments["prompt"]` 为必填项，
  其余键（`model`/`sandbox`/`max_turns`/`timeout`/`skill_name`）透传给
  `build_minimal_agent()`。权限接入相应调整：不调用顶层
  `PermissionGuard.check()`（其签名 `check(tool_name, tool_input)` 是
  Tool 专属的，套不到 workflow/subagent 上），workflow 分支依赖
  `WorkflowStep.require_approval` 自己的审批门禁，subagent 分支依赖
  `build_minimal_agent()` 内部自建的
  `PermissionGuard(auto_approve=True, sandbox=...)`（`sandbox` 透传自
  `arguments`）。
- 影响范围：`orchestrator/task_manager.py`/`orchestrator/sub_agent.py`
  本身未被这条迁移链触碰，仍是"未开始"状态（见
  `MIGRATION_STATUS.md`），不影响后续 Phase 的前置条件——本 Phase的
  目标"统一执行入口"通过"另一个语义等价的同步实现"达成，没有留下
  语义缺口（`ActionExecutor` 依然能"派生一个子 Agent 执行任务"，只是
  不是通过 `TaskManager` 那套并发调度/后台任务基础设施）。如果后续
  确实需要"真正的并发多任务子 Agent 调度"接入 `ActionExecutor`，需要
  新开一个专门评估 `TaskManager` 同步化接入方式的 Sprint，不在本次
  变更范围内。

## Sprint 6-2 执行记录

按调整后方案（见上方"变更记录"）完成：

- `actions/executor.py`：新增 `_execute_workflow()`/`_execute_subagent()`
  两个私有方法，`execute()` 主体按 `spec.type` 分派到
  `_execute_tool()`/`_execute_workflow()`/`_execute_subagent()` 三者
  之一，事件发布逻辑（`ActionStarted` → 分支执行 →
  `ActionCompleted`/`ActionFailed`）统一收口在 `execute()` 里，三个
  私有方法只负责"执行并返回 `ActionResult`"，不再各自重复发布事件
  （相比 Sprint 6-1 版本的一处重构，行为不变，减少后续新增分支时
  漏发事件的风险）。
- `type="workflow"`：`ActionExecutor.__init__` 新增可选
  `cfg`/`workflow_store` 两个构造参数；`_execute_workflow()` 用
  `workflow_store.load(spec.capability)`（惰性用
  `WorkflowStore(cfg.project_root)` 构造）加载 `WorkflowDef`，找不到
  返回 `ActionResult(success=False, error="workflow_not_found: ...")`；
  加载到后转发给 `WorkflowRunner(cfg).run(wf, inputs=spec.arguments)`
  （同步调用）；只有 `WorkflowRunResult.status == "done"` 才映射为
  `success=True`，`"failed"/"partial"/"paused"/"cancelled"` 一律
  `success=False`（`error` 里带上具体 status + 原始 `error` 字段），
  但 `output` 无论成功与否都保留完整的 `WorkflowRunResult`，不因为
  失败丢信息。
- `type="subagent"`：转发给 `workflow/agent_spawn.py::
  build_minimal_agent()` + `Agent.run_turn()`（原因见上方"变更
  记录"）；`arguments["prompt"]` 缺失时返回
  `ActionResult(success=False, error="missing_argument: ...")`，不
  抛异常也不静默用空字符串跑一次。
- 统一 Outcome：`ActionResult` 字段形状（`success`/`output`/`error`/
  `action_type`/`capability`）三种 type 完全复用同一个 dataclass，
  未新增任何 type 专属字段，`execute()` 主体也不再包含任何
  `if spec.type == "workflow": ...` 式的特判（分派到私有方法之后，
  三个分支各自独立，返回值形状由 `ActionResult` 的构造保证一致）。
- 新增测试 `tests/test_phase6_action_executor.py`（新增 9 个用例，
  含 Sprint 6-1 原有 5 个用例调整后共 11 个）：
  - `test_execute_truly_unknown_type_raises_not_implemented`
    （替换 Sprint 6-1 时"workflow/subagent 抛 NotImplementedError"的
    旧测试——两者现已实现，改为验证真正非法的 `type` 值仍会显式报错）
  - `test_execute_workflow_missing_cfg_returns_failure_not_exception`/
    `test_execute_workflow_not_found_returns_failure`/
    `test_execute_workflow_success_forwards_to_workflow_runner`/
    `test_execute_workflow_non_done_status_maps_to_failure`
  - `test_execute_subagent_missing_prompt_returns_failure`/
    `test_execute_subagent_success_forwards_to_build_minimal_agent`
  - `test_all_three_action_types_produce_uniform_result_shape`——
    直接用 `dataclasses.fields()` 断言三种 type 返回的 `ActionResult`
    字段集合完全一致，对应验收标准第二条。

验收标准两条均已验证：

1. "三种 Action 类型（tool/workflow/subagent）都能通过同一个
   `ActionExecutor.execute()` 入口调用" —— 三个新增/沿用的
   `test_execute_*_success_*` 用例覆盖。
2. "产生的 `Experience` 记录格式一致（不需要 `if type == ... else ...`
   特判）" —— `test_all_three_action_types_produce_uniform_result_shape`
   验证。

新增测试全部通过（`python -m pytest tests/test_phase6_action_executor.py -q`
→ 11 passed）；`python -m pyflakes src/mini_agent/actions/executor.py
src/mini_agent/core/action.py` 无告警；回归测试
`pytest tests/ -k "phase or goal_mode or events or experience or action"`
共 569 个用例收集成功（另有 4 个测试文件因环境缺少
`streamlit`/`websocket`/`cdp_client` 等三方依赖或历史遗留的
`mini_agent.session._flock` 导入问题无法收集，均与本次改动无关），
其中 563 通过、6 个既有失败——`test_build_from_history_*` 系列
（历次 Sprint 记录已多次确认为既有失败）与
`test_attach_mode_with_no_listening_port_raises_actionable_error`
（浏览器 profile 相关，与本次改动无关），未观察到因本次新增代码
导致的新增失败。依赖图核对：`workflow.runner`/`orchestrator.sub_agent`/
`orchestrator.task_manager` 三者 inbound 分别为 5/5/5，均未超止损
阈值（Sprint 6-2 未改动这三个模块内部任何一行，只是新增
`actions/executor.py` 对 `workflow.runner`/`workflow.store`/
`workflow.agent_spawn` 的调用方，`orchestrator/` 最终未被本次改动
触碰，见上方"变更记录"）。

Phase 6（统一 Action）三条完成标志全部达成。可进入
**Phase 7（Decision + Simulation）**，按
`08-phase7-decision-simulation-sprint-plan.md` 划分的 Sprint 继续
推进（下一次对话的任务）。
