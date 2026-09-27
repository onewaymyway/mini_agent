# Phase 5：统一 Goal（收敛 Objective/Task/Workflow）—— 可执行计划

> 对应原方案 §38、§19、§22。前置条件：Phase 4 的 `StateManager` 已托管
> `GoalState`。

## 目标

把 `GoalSpec / GoalState / GoalBacklog / Objective / Task / Workflow`
这几个现在并存的概念，逐步统一到"当前状态 + 理想状态 + 问题 + 差距 +
计划 + 行动 + 结果"这一套语义下（原文 §8、§22）。

**关键原则**：不是删掉 Objective/Workflow，而是让它们变成 GoalState 的
"内部实现细节"，对外只暴露 Goal 这一个概念。

## 现状盘点

| 现有概念 | 文件位置 | 与 GoalState 的关系 |
|---|---|---|
| `Objective` | `goal_mode/` 或相关 orchestrator 代码 | 计划降级为 `GoalState` 内部的一个执行步骤（`InternalGoalStep`，原文 §38） |
| `goal_backlog.py` | 顶层模块 | 计划降级为 `GoalState` 列表的持久化实现细节 |
| `workflow/`（18 个模块） | `workflow/` | **不在本 Phase 处理**——Workflow 属于 Capability 范畴（原文 §9），留给 Phase 6/Capability 收敛处理，本 Phase 只处理 Goal 是否*引用* Workflow，不改 Workflow 本身 |

产出：`docs/architecture_v2/phase5-goal-inventory.md`，明确哪些代码
属于"Goal 本身"、哪些其实是"Goal 引用的 Capability"，避免这个 Phase
范围蔓延到 Workflow 内部重构。

**现状盘点已完成**（详见 `docs/architecture_v2/phase5-goal-inventory.md`），
核心发现：`Objective` 与 `goal_backlog.py` 实际上是**同一套**子系统
（`perception/goal_backlog.py::GoalBacklog`/`GoalNode`，`level="objective"`
即"Objective"），并非原表格暗示的两个独立处理项；且这套子系统
`scripts/dep_graph.py` 实测 inbound=33，**远超止损阈值**，与
`evolution/objective_executor.py::ObjectiveExecutor`（inbound=5，
未超阈值，但紧耦合依赖前者）合计构成一套服务于看板/自主循环/公平
调度的大型自主执行子系统，和 Phase 1 已迁移的 `goal_mode/`（`GoalSpec`/
`GoalRunner`）完全独立、互不引用。原 Sprint 5-1 计划因此触发止损条件，
需要变更留痕，详见下方"变更记录"小节。`workflow/` 边界（inbound=5，
未超阈值，与 `goal_backlog.py` 无直接依赖）盘点后确认原计划的
"本 Phase 不处理"仍然合理，无需调整。

## Sprint 5.0.5（新增，对应变更记录）：GoalBacklog/ObjectiveExecutor 耦合拆解评估

> 参照 Sprint 1.5（`03-sprint1.5-memory-perception-coupling-assessment.md`）
> 的方法论：按子模块细分，区分"真正需要收敛的部分"和"可以先 facade
> 隔离的部分"，产出有区分度的迁移优先级建议，而不是对整个子系统喊
> 暂停。留待下一次推进，任务范围：

| 任务 | 产出 |
|---|---|
| 按子系统重新统计 `goal_backlog.py` 的 33 个调用方 | 区分"`perception/`/`external_input/` 包内部调用"与"真正跨子系统调用"，参照 `12-execution-and-doc-sync-norms.md` 第六节第 6 条已确立的止损口径（只统计跨子系统 inbound） |
| 评估 `ObjectiveExecutor` 能否独立启动 Adapter | 判断"只转换 `ObjectiveExecutor` 侧、不动 `GoalBacklog`"是否会变成"没有实际解耦效果的包装层"，还是确实可以复用 Self/history_manager 那套"低风险先行"模式 |
| 给出 Sprint 5-1 实际任务范围建议 | 输出：`goal_backlog.py`/`ObjectiveExecutor` 分别应该"直接启动 Adapter" / "先做 facade 隔离" / "暂缓"三选一的判断，附实测数据 |

## Sprint 5-1（2 周）：Objective → InternalGoalStep 降级

| 任务 | 产出 |
|---|---|
| `core/goal.py` 扩充 | `GoalState` 增加 `current_state / ideal_state / problems / gap / constraints / resources / priority / evidence / deadline` 字段（原文 §8） |
| Objective Adapter | 把现有 `Objective` 对象转换为 `GoalState.gap` 下的一个条目，而不是独立对象；旧 `Objective` 的读写接口保留，内部转发到新结构 |
| GoalBacklog Adapter | 让 `goal_backlog.py` 的读写通过 `StateManager` 完成，内部数据结构逐步替换为多个 `GoalState` 实例的集合 |

**验收标准**：现有依赖 `Objective` / `GoalBacklog` 的旧测试全部通过
（Adapter 模式的核心验证方式——外部接口不变，内部实现改变）。

**止损条件**：如果 Objective 的语义（比如"多级子目标嵌套"）在
`GoalState.gap` 这个简单列表结构里表达不了，说明 `GoalState` 需要
支持递归结构，应先扩展数据模型，而不是强行压缩语义丢失信息。

## Sprint 5-2（1.5 周）：Gap 检测能力

| 任务 | 产出 |
|---|---|
| `goals/gap.py` | 实现"当前 State vs 理想 State → problems/gap"的检测逻辑，第一版可以是简单规则 + LLM 判断，不需要复杂算法 |
| 接入 StateManager | Gap 检测读取 Phase 4 的 `state_manager.get_state("world"/"self")` 作为输入（哪怕这些 State 目前还是占位，也要先把接口打通） |

**验收标准**：给定一个新 Goal 输入，系统能自动生成 `problems` 和 `gap`
字段，而不需要用户手动填写。

## 完成标志

- [ ] `Objective` 对外接口不变，内部已经是 `GoalState` 的适配层
- [ ] `GoalBacklog` 的持久化已经统一走 `StateManager`
- [ ] Gap 检测逻辑已实现最小版本，并在至少一个真实 Goal 场景下验证过
- [x] Workflow 相关代码未被本 Phase 触碰（留给 Capability 收敛处理）
      （现状盘点已核实：`workflow/` inbound=5，与 `goal_backlog.py`/
      `objective_executor.py` 无直接依赖，边界确认合理）

## 变更记录

### 变更记录 2026-09-27
- 触发条件：Sprint 5-1 止损条件——"如果 Objective 的语义...在
  `GoalState.gap` 这个简单列表结构里表达不了"；实测发现问题比止损
  条件描述的更早出现：还没到"语义表达不了"这一步，`goal_backlog.py`
  的**依赖规模**（inbound=33）就已经复现了 Sprint 3 对
  `perception/memory_store.py` 判定"远超阈值"的同款情况。
- 原计划：现状盘点表格把 `Objective`（"计划降级为 GoalState 内部的一个
  执行步骤"）和 `goal_backlog.py`（"计划降级为 GoalState 列表的持久化
  实现细节"）列为两个独立、可以分别评估的处理项，Sprint 5-1 据此安排
  "Objective Adapter"与"GoalBacklog Adapter"两项独立任务，均按 Goal
  迁移链"单一接入点 + Adapter 直接转换"的轻量模式设计。
- 实际情况：`docs/architecture_v2/phase5-goal-inventory.md` 现状盘点
  发现，`Objective` 其实就是 `goal_backlog.py::GoalNode` 里
  `level="objective"` 的节点，二者是同一套子系统，不是两个独立处理项；
  该子系统 `scripts/dep_graph.py --module perception.goal_backlog`
  实测 inbound=33，远超 10+ 止损阈值，且与
  `evolution/objective_executor.py`（自主执行引擎，反向回写
  `GoalNode.status`）紧耦合，服务于看板/自主循环/公平调度等 10+
  下游子系统，83 个测试文件涉及，规模和耦合度远超"轻量转换"的假设。
- 调整后方案：Sprint 5-1 暂缓直接执行原计划的两项任务，新增
  **Sprint 5.0.5（GoalBacklog/ObjectiveExecutor 耦合拆解评估）**，
  参照 Sprint 1.5 的方法论重新按子系统统计真实耦合面，给出有区分度的
  迁移优先级建议后，再确定 Sprint 5-1 的实际任务范围（可能是"先对
  `ObjectiveExecutor` 单独启动 Adapter，`goal_backlog.py` 暂缓"，
  也可能是别的结论，留给 Sprint 5.0.5 实测后判断，不在此提前下结论）。
- 影响范围：不影响 Phase 4 的前置条件（`StateManager` 已就绪，
  与本次发现无关）；影响本 Phase 自身 Sprint 5-1/5-2 的执行顺序——
  Sprint 5-2（Gap 检测）不依赖 `goal_backlog.py` 具体实现，可以在
  Sprint 5.0.5 结论产出前独立推进，不必等待。
