# Phase 2：统一 Event Model —— 可执行计划

> 对应原方案 §35。前置条件：Phase 1（Goal 迁移链，见
> `02-executable-sprint-plan.md`）已达到验收标准，即 `core/` 已有
> `GoalState`、`Experience` 等最小 dataclass，且 Goal 链路已跑通一次
> 真实闭环。

## 目标

把当前散落在各模块里的"状态变化"统一成一种 `Event` 结构，为后续
`State → Experience → Learning` 的链路提供统一入口，不再各模块各自
定义自己的变更通知方式。

## 现状盘点（先做，再设计）

在写 `Event` 之前，先扫描现有代码里有多少种"事件"雏形，避免重新发明：

- `history_manager.py` 里记录的历史条目结构
- `perception/behavior/` 下的行为感知事件
- `evolution/` 下 `EvolutionProposed` / `EvolutionApplied` 一类的状态变更
- `orchestrator/` 里 SubAgent 调用的开始/结束通知

产出：`docs/architecture_v2/phase2-event-inventory.md`，列出"现有事件雏形
→ 对应到新 Event type"的映射表，而不是凭空定义 15 种 event type。

## Sprint 2-1（1.5 周）：Event 数据结构 + 最小总线

| 任务 | 产出 |
|---|---|
| `core/events.py` | 定义 `Event` dataclass：`id / type / timestamp / actor / context / payload / causation_id / correlation_id`（原文 §35 结构） |
| Event type 枚举 | 先只落地 Phase 1 已经用到的几种：`GoalCreated / GoalUpdated / ActionStarted / ActionCompleted / ActionFailed / ExperienceCreated`，其余（`ToolCalled/WorldChanged/SelfChanged/...`）留到对应 Phase 再加，**不要一次性把 15 种全定义空壳** |
| 最小事件总线 | `core/event_bus.py`：一个进程内的 `publish(event)` / `subscribe(type, handler)`，不引入外部消息队列 |
| 接入点 | 在 Phase 1 已经打通的 `goal_mode/runner.py` 调用链上，把原本隐式的状态变化替换成显式 `publish(Event(...))` |

**验收标准**：
1. Goal 执行一次完整闭环时，能在日志里看到至少 4 个 Event 被 publish
   （Created → ActionStarted → ActionCompleted/Failed → ExperienceCreated）。
2. Phase 1 的特征测试全部仍然通过（Event 只是"旁路记录"，不应改变原有
   业务逻辑的返回值）。

**止损条件**：如果引入 Event 后发现某个现有测试因为"多了一次 publish
调用的副作用"而失败，说明 Event 目前还在侵入业务逻辑，应回退为
纯旁路（observer），不修改主流程返回值。

## Sprint 2-2（1 周）：causation_id / correlation_id 打通

| 任务 | 产出 |
|---|---|
| 关联链路 | 让一次 Goal 执行产生的所有 Event 共享同一个 `correlation_id`，`causation_id` 指向触发它的上一个 Event |
| 可视化 | 一个简单 CLI：`mini_agent events trace <correlation_id>`，按时间顺序打印一次 Goal 执行的完整事件链 |

**验收标准**：能用 `events trace` 命令完整重放 Sprint 2-1 里那次 Goal
执行的事件序列，事件顺序与实际执行顺序一致。

## 完成标志（对应原文 Phase 2 目标）

- [ ] `core/events.py` 中 Event 结构稳定，且已在 Goal 链路上验证过
- [ ] 事件雏形盘点表完成，明确了后续哪些旧模块的"通知机制"要逐步
      替换为 Event（而不是立刻全部替换）
- [ ] 依赖图（Sprint 0 建立的脚本）显示引入 Event 总线没有增加模块间
      的硬编码依赖（订阅关系应该是单向的：旧模块 → 发布 Event，
      不应该反向依赖 Event 总线的内部实现）
