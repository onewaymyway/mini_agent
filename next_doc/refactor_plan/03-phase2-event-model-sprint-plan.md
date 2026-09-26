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

- [x] `core/events.py` 中 Event 结构稳定，且已在 Goal 链路上验证过
      （Sprint 2-1：扩展 `id/actor/context/causation_id/correlation_id`
      字段，保留 `kind/payload/at` 三个 Sprint 1 已用字段名不变；
      `goal_mode/runner.py` 唯一接入点新增
      `GoalCreated → ActionStarted → ActionCompleted/ActionFailed →
      ExperienceCreated` 四类 publish，`tests/test_goal_mode_phase2_events.py`
      验证了一次 DONE 闭环恰好产生 `[GoalCreated, ActionStarted,
      ActionCompleted, ExperienceCreated]` 四个事件、
      correlation_id 一致、causation_id 构成因果链，且执行器抛异常时
      publish `ActionFailed` 后异常照常向上抛出）
- [ ] 事件雏形盘点表完成，明确了后续哪些旧模块的"通知机制"要逐步
      替换为 Event（而不是立刻全部替换）——**尚未开始**：Sprint 2-1
      范围内只在 Goal 链路唯一接入点新增了埋点，`docs/architecture_v2/
      phase2-event-inventory.md`（现状盘点里要求的"现有事件雏形 →
      对应到新 Event type"映射表）尚未产出，`history_manager.py`
      历史条目结构 / `perception/behavior/` 行为感知事件 /
      `evolution/` 状态变更通知 / `orchestrator/` SubAgent 开始结束
      通知这四类现有"事件雏形"尚未盘点，留给下一次推进（不在本次
      Sprint 2-1 范围内，避免"顺手"把 Sprint 划分之外的工作也做掉）。
- [ ] 依赖图（Sprint 0 建立的脚本）显示引入 Event 总线没有增加模块间
      的硬编码依赖（订阅关系应该是单向的：旧模块 → 发布 Event，
      不应该反向依赖 Event 总线的内部实现）——**尚未核对**：
      `core/event_bus.py` 只被 `goal_mode/runner.py` 一处导入
      （`from mini_agent.core import ... get_event_bus ...`），从代码
      结构上看不存在反向依赖，但尚未按 `scripts/dep_graph.py` 实际跑一次
      并把结果记录进本文档，留给 Sprint 2-2 收尾时一并核对。

## Sprint 2-1 执行记录（2026-09-26）

- 已完成：`core/events.py`（`Event` 字段扩展 + `EVENT_KINDS` 常量）、
  `core/event_bus.py`（`EventBus`/`get_event_bus`/`reset_event_bus`，
  订阅者异常不传播）、`goal_mode/runner.py` 唯一接入点新增 4 类
  publish（`GoalCreated`/`ActionStarted`/`ActionCompleted`/
  `ActionFailed`/`ExperienceCreated`）。
- 验收标准第 1 条（至少 4 个 Event）：已通过
  `tests/test_goal_mode_phase2_events.py::
  test_goal_runner_done_on_first_round_publishes_closed_loop_events`
  验证。
- 验收标准第 2 条（Phase 1 特征测试全部仍然通过，Event 只是旁路记录）：
  已核对 `tests/test_goal_mode.py`、
  `tests/test_goal_mode_characterization.py`、
  `tests/test_compact_autopilot_improvements.py`、
  `tests/test_judge_verdict.py`，`GoalRunner.run()`/`_finish()`
  相关用例无新增失败（`test_goal_mode.py` 中 5 个
  `test_build_from_history_*` 用例失败，但失败原因是
  `GoalSpecBuilder._run_builder()` 的 `detection_text` 参数问题，
  与本次改动的代码路径（`run()`/`_finish()`/`_executor.execute()`
  调用点）无关，不属于本次改动引入的回归）。
- 止损条件未触发：未出现"因多了一次 publish 调用的副作用导致现有测试
  失败"的情况，`EventBus.publish()` 保持纯旁路（观察者），未修改
  `GoalRunner` 任何方法的返回值或控制流（执行器异常照常向上抛出，
  只是抛出前多 publish 一次 `ActionFailed`）。
- 未完成项（见上方完成标志清单）：事件雏形盘点表、依赖图核对，
  留待下一次推进（Sprint 2-2 或专门的盘点任务）时补上，不在本次
  Sprint 2-1 范围内一并做掉。
- `MIGRATION_STATUS.md` 已同步新增 `core/events.py`（字段扩展）、
  `core/event_bus.py`（新增）两行。
