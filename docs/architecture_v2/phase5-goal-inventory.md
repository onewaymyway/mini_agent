# Phase 5（统一 Goal）现状盘点

> 对应 `next_doc/refactor_plan/06-phase5-goal-convergence-sprint-plan.md`
> "现状盘点"任务产出。方法论沿用 Sprint 1.5 的教训（见
> `12-execution-and-doc-sync-norms.md` 第六节）：按具体模块扫描，
> 不只看包名/顶层目录；实测 `scripts/dep_graph.py` inbound，不凭
> 代码规模或直觉判断风险。

## 一、核心发现：mini_agent 里其实有三套"Goal"，不是一套

`06-phase5-goal-convergence-sprint-plan.md` 原计划把 `Objective` /
`goal_backlog.py` 都当作 `goal_mode/`（Phase 1 已迁移的 `GoalSpec` /
`GoalRunner`）的近亲概念，认为可以照搬"Objective → GoalState 内部
一个条目"这种轻量转换。实测代码后发现**三者是三套几乎互不引用的独立
子系统**，规模和耦合度差异巨大：

| 子系统 | 位置 | 角色 | 与 `goal_mode/` 的关系 |
|---|---|---|---|
| `GoalSpec` / `GoalRunner` | `goal_mode/` | 单次、交互式的"一个 Goal 从提出到完成"执行循环，**Phase 1 已迁移**到 `core/goal.py::GoalState` | 是 Phase 1 迁移链本身 |
| `GoalBacklog` / `GoalNode` | `perception/goal_backlog.py` | 跨 session 持久化的目标树（`.agent/goals.json`），两层：Goal / Objective，供看板、自主循环、公平调度等 30+ 个模块读写 | **完全独立**，`goal_mode/*.py` 里搜不到任何 `goal_backlog`/`GoalBacklog`/`GoalNode` 引用 |
| `ObjectiveExecutor` | `evolution/objective_executor.py` | 把 `GoalBacklog` 里 `level="objective"` 的节点拆解为多步 `ExecutionStep` 并调度执行的自主执行引擎，反向回写 `GoalNode.status` | 依赖 `GoalBacklog`，与 `goal_mode/` 无关 |

也就是说，**原计划文档"现状盘点"表格里把 `Objective` 和
`goal_backlog.py` 并列为两个待处理项，实际上它们是同一套子系统
（`GoalBacklog` 存 `GoalNode`，`level="objective"` 的节点就是
"Objective"），而且这套子系统跟 Phase 1 已迁移的 `goal_mode/` 是两回事，
不是"Objective 从 GoalState 里降级出来的近亲"**。

## 二、依赖规模实测（`scripts/dep_graph.py`）

| 模块 | inbound（深度依赖文件数） | 是否超止损阈值（10+） | 涉及测试文件数（grep 命中） |
|---|---|---|---|
| `perception.goal_backlog`（`GoalBacklog`/`GoalNode`） | **33** | **是，远超阈值** | 83 |
| `evolution.objective_executor`（`ObjectiveExecutor`） | 5 | 否 | 未单独统计（与上面重叠度高，因为大量测试同时 import 两者） |
| `workflow`（对照组，本 Phase 明确不处理） | 5 | 否 | — |
| `goal_mode.runner`（对照组，Phase 1 已迁移完成的参照基线） | 3 | 否 | — |

`perception/goal_backlog.py`（2563 行）+
`evolution/objective_executor.py`（2366 行）合计近 5000 行，且
`ObjectiveExecutor` 内部直接依赖 `GoalBacklog.get()` /
`active_objectives_fair_ranked()` 等方法做公平调度、状态回写——两者是
紧耦合的一套"自主目标执行"子系统，服务于看板（Kanban）、自主循环
（AutonomousLoop）、公平调度、cron 桥接等至少 10+ 个下游子系统。

这与 Sprint 3 对 `perception/memory_store.py`（inbound=33，同样
"远超阈值"）的判断结构完全一致，不是巧合——`goal_backlog.py` 属于
`perception/` 包内被多个跨子系统模块共享的"核心状态存储"类文件，
风险特征与 `memory_store.py` 同款。

## 三、`workflow/` 边界确认

按计划文档"现状盘点"表格的第三行，本 Phase 明确**不处理**
`workflow/`（18 个模块，inbound=5，未超阈值）——原计划的止损边界经
本次盘点验证仍然合理，`workflow/` 与 `goal_backlog.py`/
`objective_executor.py` 之间没有直接 import 关系（`workflow/` 不引用
`GoalBacklog`，`goal_backlog.py`/`objective_executor.py` 也不引用
`workflow/` 内部符号），两者可以独立处理，互不阻塞。

## 四、对 Sprint 5-1 计划的影响（触发止损条件，需要变更留痕）

`06-phase5-goal-convergence-sprint-plan.md` Sprint 5-1 原计划的两项
任务——"Objective Adapter：把现有 Objective 对象转换为
`GoalState.gap` 下的一个条目"、"GoalBacklog Adapter：让
`goal_backlog.py` 的读写通过 `StateManager` 完成"——建立在
"`Objective`/`GoalBacklog` 是轻量、低耦合概念"这一假设上，实测数据
（inbound=33，远超阈值，且与自主执行/公平调度/看板等系统深度耦合）
推翻了这一假设。

这与 Sprint 3 对 Memory/Perception 的判断是同一类问题，处理方式也
应该一致：**不能直接对 `goal_backlog.py` 套用 Goal 迁移链"单一接入点
+ Adapter 直接转换"的模式**，需要先补一个独立的"Sprint 5.0.5：
GoalBacklog/ObjectiveExecutor 耦合拆解评估"（对应 Sprint 1.5 的方法论：
按子模块细分，区分"真正需要收敛的部分"和"可以先 facade 隔离的部分"），
再决定 Sprint 5-1 的实际任务范围。变更记录见
`06-phase5-goal-convergence-sprint-plan.md` 末尾"变更记录"小节。

## 五、Sprint 5-1 / 5-2 范围建议（供后续 Sprint 5.0.5 参考，不是最终结论）

- `ObjectiveExecutor`（inbound=5，未超阈值）本身风险不高，可能可以
  按 Self/history_manager 的模式直接启动 Adapter；但它的输入
  （`GoalBacklog`/`GoalNode`）耦合度高，Adapter 转换目标结构如果只
  转换 `ObjectiveExecutor` 侧、不动 `GoalBacklog`，可能只是"新增一层
  没有实际解耦效果的包装"，需要在 Sprint 5.0.5 里具体评估。
- `goal_backlog.py` 的 33 个调用方分布在 `perception/`、
  `external_input/` 两个包，与 Sprint 1.5 对 `memory_store.py` 的
  "止损口径调整为只统计跨子系统 inbound"这一先例是否适用，需要
  Sprint 5.0.5 里重新按子系统分类统计（`perception/` 包内部互相调用
  是否应该计入，参照 `12-execution-and-doc-sync-norms.md` 第六节
  第 6 条已确立的口径）。
- Gap 检测（Sprint 5-2）不依赖 `goal_backlog.py`/`ObjectiveExecutor`
  的具体实现，可以在 Sprint 5.0.5 结论出来之前独立推进，不必等待。
