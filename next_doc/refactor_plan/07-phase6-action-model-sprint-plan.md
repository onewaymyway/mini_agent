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

- [ ] Tool/Workflow/SubAgent 三类执行路径都已通过 `ActionExecutor`
      统一接口调用
- [ ] 权限、超时、重试等横切逻辑没有在三个分支里重复实现
- [ ] 产出的 Experience 记录格式统一，不需要按 Action 类型特判处理
