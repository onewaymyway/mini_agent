# Phase 6（统一 Action）现状盘点

> 对应 `next_doc/refactor_plan/07-phase6-action-model-sprint-plan.md`
> "现状盘点"任务产出。方法论同 Phase 5：实测 `scripts/dep_graph.py`
> inbound，不凭代码规模或直觉判断风险。

## 一、依赖规模实测

| 模块 | 位置 | inbound | 是否超止损阈值（10+） |
|---|---|---|---|
| `ToolExecutor` | `tool_executor.py`（顶层模块） | 11 | 是（但见下方"二"的分析） |
| `PermissionGuard` | `permissions.py`（顶层模块） | 26 | 是（但见下方"二"的分析） |
| `workflow/`（18 个模块） | `workflow/` | 5 | 否 |
| `orchestrator/` | `orchestrator/` | 37 | 是（但见下方"二"的分析） |
| `hybrid_exec/` | `hybrid_exec/` | 4 | 否 |
| `evolution/objective_executor.py::ObjectiveExecutor`（Phase 5 Sprint 5.0.5 移交） | `evolution/` | 5 | 否（Phase 5 已评估，风险点不在 inbound 数值，见下方"三"） |

## 二、关键发现：这几个"超阈值"跟 Phase 3/5 遇到的情况**性质不同**

Sprint 1.5（Memory/Perception）和 Sprint 5.0.5（GoalBacklog）里，止损
阈值之所以重要，是因为那两条迁移链要做的是 **Adapter 式迁移**——
逐步把一个被广泛依赖的模块**内部的读写路径**替换掉（`goal_backlog.py`
的持久化要改成走 `StateManager`，`memory_store.py` 的存储要素要迁移），
风险来自"改动一个所有人都在用的模块内部，可能牵连所有调用方"。

Phase 6 Sprint 6-1/6-2 的设计**从一开始就不是这种模式**：`ActionSpec
→ ActionExecutor → ActionResult` 是一层**新增的、纯转发的外层
wrapper**——`ActionExecutor.execute(spec)` 内部调用
`tool_executor.py::ToolExecutor.execute_all()`（已有的公开方法，
`agent/lifecycle.py` 第 254 行唯一实例化）、
`permissions.py::PermissionGuard`（已有的公开类）、`orchestrator/`
的现有子 Agent 调用入口——计划文档原文明确写的是"内部转发给现有
xxx.py"、"复用现有 permissions.py，而不是重新实现权限逻辑"，也就是
**完全不改动 `tool_executor.py`/`permissions.py`/`orchestrator/` 的
任何一行代码**，只是在它们之外新增一层调用方。

这种"只新增调用方，不改动被依赖模块内部"的改动，dep_graph 的 inbound
数字反映的是"这个模块本身有多重要、被多广泛依赖"，而不是"这次改动
的风险有多大"——因为这次改动根本不touch这些模块内部，原有的 11/26/37
个调用方不会受到任何影响。**止损条件的本意是防止"改动一个所有人都在
用的模块内部逻辑，波及所有调用方"，不是禁止"新增一个调用方去调用
一个被广泛使用的稳定公开接口"**——后者是几乎所有正常开发都会做的事，
如果套用止损条件会导致任何新代码都不能调用 `permissions.py`（毕竟它
被 26 个文件依赖），这明显不是止损条件设立的初衷。

因此本次盘点的结论是：**Sprint 6-1/6-2 原计划不需要因为 inbound 数值
而重新评估范围，可以按原计划直接推进**，与 Phase 3（Memory）/
Phase 5（GoalBacklog）需要暂停重新规划的情况不同。这是本次盘点特意
要澄清的一点，避免"看到超阈值就机械触发暂停流程"——止损条件要看的是
"这次改动会不会碰这个模块的内部"，不是单纯看 inbound 数字。

`ToolExecutor` 额外确认一点：11 个调用方**全部**在 `agent/` 包内
（`_helpers.py`/`compaction.py`/`core.py`/`lifecycle.py`/
`llm_control.py`/`profile.py`/`reflection.py`/
`reminders_correction.py`/`role_judge.py`/`snapshot.py`/
`turn_loop.py`），说明它本质是 Agent 对象拆分到多个文件后的一个内部
协作类，不是真正意义上的"跨 10 个独立子系统"，风险本来就比数字
暗示的低——即使按 Adapter 式迁移的口径重新统计（排除同包内部调用），
跨子系统 inbound 也是 0。

`PermissionGuard`/`orchestrator/` 没有这种"同包内部调用"模式（分布在
`agent/`/`api/`/`cli/`/`evolution/`/`history/`/`orchestrator/`/
`role_agents/`/`ui/`/`workflow/` 多个包），是真正广泛依赖的稳定基础
设施（`permissions.py`）和真正的独立子系统（`orchestrator/`），但如
上所述，这不影响本 Phase 的"纯转发"改动方式。

## 三、`ObjectiveExecutor`（Phase 5 Sprint 5.0.5 移交项）评估

Phase 5 现状盘点时的判断是"`ObjectiveExecutor` 的唯一实例化点嵌在
`api/server.py::HttpServer._build_autonomous_loop()` 内一段时序高度
敏感的构造闭包里，风险特征是低耦合但高时序敏感"。结合本次"二"里
澄清的原则重新评估：**这条风险结论描述的是"如果要在那个构造闭包里
插入新代码"的风险，而不是"`ActionExecutor` 能不能转发调用
`ObjectiveExecutor` 已经构造好的实例"的风险**——两者是不同的操作。
如果 Sprint 6-2/6-3 只是新增 `type="objective_step"` 分支，转发给
一个**已经在别处构造完毕**的 `ObjectiveExecutor` 实例（不改动
`api/server.py` 里那段构造闭包本身），风险应该和 Tool/Workflow/
SubAgent 一样低。

结论：`ObjectiveExecutor` 的接入不需要额外的止损处理，可以放在 Sprint
6-1/6-2 之后按同样的"纯转发"模式追加一个分支，不需要提前在本次盘点
阶段单独立项评估——具体排期留给 Sprint 6-2 或新增的 Sprint 6-3 决定，
不在本次盘点里下结论抢跑后续 Sprint 的任务划分。

## 四、结论

Phase 6 现状盘点未发现需要触发止损条件、暂停或重新规划的问题。
`07-phase6-action-model-sprint-plan.md` 原有的 Sprint 6-1/6-2 任务表
经核实无需调整，可以直接按原计划推进到 Sprint 6-1（下一次对话的
任务）。
