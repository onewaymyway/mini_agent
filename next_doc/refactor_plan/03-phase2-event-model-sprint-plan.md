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
- [x] 事件雏形盘点表完成，明确了后续哪些旧模块的"通知机制"要逐步
      替换为 Event（而不是立刻全部替换）：产出
      `docs/architecture_v2/phase2-event-inventory.md`，盘点了
      `history_manager.py` 历史条目结构（`append_*` 方法）、
      `perception/behavior/events.py::ActivityEvent`（已有独立统一
      结构）、`evolution/`（以 `candidate_queue_triage.py` 的 `status`
      字段流转为代表样本）、`orchestrator/plan.py::PlanTask` 状态流转
      四类现有"事件雏形"，逐一给出对应新 Event type 建议，并明确
      **四者均暂不接入** `core/event_bus.py`（本次只做盘点，接入是
      独立的后续任务），其中 `orchestrator/plan.py` 因字段语义与
      Phase 1 `ActionStarted/ActionCompleted/ActionFailed` 最接近、
      接入点容易收敛，被列为优先级最高的后续接入候选。
- [x] 依赖图（Sprint 0 建立的脚本）显示引入 Event 总线没有增加模块间
      的硬编码依赖（订阅关系应该是单向的：旧模块 → 发布 Event，
      不应该反向依赖 Event 总线的内部实现）：已用
      `python scripts/dep_graph.py --module core.event_bus` 实际跑过，
      结果为 inbound 1（仅 `mini_agent/core/__init__.py` 导入
      `EventBus`/`get_event_bus`/`reset_event_bus` 三个符号）、
      outbound 0（`core/event_bus.py` 自身不依赖任何包外目标），
      未触发 Sprint 0 止损阈值，且 outbound=0 说明总线本身不反向
      依赖任何发布者模块，结构上是单向的（旧模块 → import 总线，
      总线不 import 旧模块）。详见下方"Sprint 2-1 收尾核对记录"。

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

## Sprint 2-1 收尾核对记录（2026-09-26，补做完成标志剩余两项）

> 本次只补做 Sprint 2-1 完成标志清单里遗留的"事件雏形盘点表"和
> "依赖图核对"两项，不涉及代码改动，也不属于 Sprint 2-2 的任务范围
> （Sprint 2-2 是 `causation_id`/`correlation_id` 打通 + `events trace`
> CLI，这两项已在 Sprint 2-1 里随字段扩展一并实现，见上方执行记录，
> Sprint 2-2 剩余任务是补 CLI 命令）。

- **事件雏形盘点表**：产出 `docs/architecture_v2/phase2-event-inventory.md`
  （详见文档内容），核心结论：
  1. `history_manager.py`（历史条目）→ 建议新增 `MessageAppended` /
     `HistoryAnnotationAdded`，或复用已有的 `ActionCompleted`，
     暂不接入。
  2. `perception/behavior/events.py::ActivityEvent`（行为感知）→
     已有独立统一结构和落盘存储，建议新增
     `PerceptionActivityObserved`，暂不接入，需先评估总线承载高频
     事件的性能。
  3. `evolution/`（状态变更，以 `candidate_queue_triage.py` 为样本）→
     实际代码库中未找到原计划文档假设的 `EvolutionProposed`/
     `EvolutionApplied` 固定类型，改为按真实 `status` 字段流转命名
     `EvolutionCandidateStatusChanged`，接入前需要先对 `evolution/`
     做子模块级耦合扫描（属于 Phase 9 前置工作）。
  4. `orchestrator/plan.py::PlanTask`（SubAgent 任务状态）→ 字段语义
     与 Phase 1 `ActionStarted/ActionCompleted/ActionFailed` 最接近，
     建议直接复用 + 新增 `ActionSkipped`，列为**优先级最高**的后续
     接入候选。
  - 四类现有事件雏形均判定为"本次不接入"，理由和优先级排序已写入
    盘点文档，供 Sprint 2-2 之后决定下一条接入链路时参考。
- **依赖图核对**：执行
  `python scripts/dep_graph.py --module core.event_bus`，输出：
  - 对内依赖（inbound）：1 个文件（`mini_agent/core/__init__.py`，
    导入 `EventBus`/`get_event_bus`/`reset_event_bus`）。
  - 对外依赖（outbound）：0（`core/event_bus.py` 不 import 任何
    包外目标）。
  - 止损条件核对：深度 inbound 计数 1，远低于 Sprint 0 定义的 10+
    阈值，未触发止损。
  - 结论：`core/event_bus.py` 只被 `core/__init__.py` 重新导出后
    间接被 `goal_mode/runner.py` 使用，自身不依赖任何发布者模块，
    订阅/发布关系是单向的（发布者 → 总线，总线不反向依赖发布者），
    符合完成标志里"不应该反向依赖 Event 总线的内部实现"的预期，
    也没有观察到"总线反向依赖 Event 总线内部实现"之外的其它异常
    依赖形态。
- 两项均已在上方"完成标志"清单勾选为 `[x]`，Sprint 2-1 对应的完成
  标志现已全部达成；Sprint 2-2 遗留任务（`mini_agent events trace`
  CLI 命令）不受影响，留待专门的 Sprint 2-2 收尾任务处理。
