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
- [ ] Workflow 相关代码未被本 Phase 触碰（留给 Capability 收敛处理）
