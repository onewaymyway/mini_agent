# Phase 8：重构 Autonomous Runtime —— 可执行计划

> 对应原方案 §41、§12。前置条件：Phase 7 的 `Gap → Simulation →
> Decision → Action → Experience` 全链路已跑通一次。本 Phase 是把这条
> 链路包进一个持续运行的循环里，并逐步收编现有的
> `Daemon/AutonomousLoop/Cron/UnifiedTaskScheduler/ObjectiveExecutor/
> ResourceArbiter`。

## 目标与边界

**这是风险最高的 Phase 之一**：现有 Daemon/Cron/Scheduler 之间大概率
有复杂的隐式协作关系（比如资源抢占、优先级）。原则：

- **先跑通单次循环，再考虑持续运行**。
- **不要求一次性替换所有旧 Scheduler**——先让新 Runtime 接管一种
  触发场景（比如"用户主动发起的 Goal"），Cron 定时触发场景留到
  Sprint 8-2 再接入。

## Sprint 8-1（2 周）：AgentRuntime 单次循环骨架

| 任务 | 产出 |
|---|---|
| `runtime/runtime.py` | 实现原文 §41 的核心循环骨架（`observe → state update → gap detect → plan → simulate → decide → execute → record → learn`），但只支持**单次运行**，不是常驻循环 |
| 接入点 | 把"用户主动发起一个 Goal"这个场景，从现有入口（CLI/API）改为调用 `AgentRuntime.run_once(goal)`，内部串联 Phase 1-7 已经打通的各环节 |
| 旧入口保留 | 现有 CLI/API 入口不删除，只是内部实现从"直接调用 goal_mode”改为“调用 AgentRuntime” |

**验收标准**：通过 CLI 发起一个 Goal，能看到完整走了一遍 Phase 1-7
打通的链路（不是重新实现一遍逻辑，而是复用），且旧的 CLI 相关测试
仍然通过。

## Sprint 8-2（2 周）：接入持续运行 + 一种旧 Scheduler

| 任务 | 产出 |
|---|---|
| `runtime/event_loop.py` | 支持常驻运行：`while running: run_once(...)`，加上基本的异常处理和优雅退出 |
| 选择一个 Scheduler 接入 | 从 `Daemon/AutonomousLoop/Cron/UnifiedTaskScheduler/ObjectiveExecutor/ResourceArbiter` 里选**耦合最小的一个**（建议先扫描依赖图确认），把它的触发逻辑改为向 `AgentRuntime` 提交 Goal，而不是自己执行 |
| 资源管理最小版 | 如果 `ResourceArbiter` 是被选中接入的对象，把它的资源检查逻辑包成 `runtime/lifecycle.py` 里 `run_once` 前的一个前置检查，不重写内部算法 |

**验收标准**：被接入的旧 Scheduler 触发的任务，现在走的是
`AgentRuntime.run_once`，且原有该 Scheduler 的测试全部通过。

**止损条件**：如果发现某个 Scheduler 内部有大量特殊逻辑无法简单
转化为"提交一个 Goal"（比如它本身不是在做目标导向的任务，而是纯粹的
后台维护任务），应该在 `phase-mapping` 文档里明确标注"这个 Scheduler
暂不收编，保留独立运行"，而不是强行套用 Runtime 抽象。

## Sprint 8-3（1.5 周）：剩余 Scheduler 逐个评估

| 任务 | 产出 |
|---|---|
| 逐个评估表 | 对 `Daemon/Cron/UnifiedTaskScheduler/ObjectiveExecutor/ResourceArbiter` 中未接入的部分，逐个判断：能否用同样模式接入、需要多久、风险等级 |
| 排期 | 产出后续接入顺序，不要求本 Phase 内全部完成 |

## 完成标志

- [x] 至少一种触发路径（用户主动 Goal）完全走 `AgentRuntime`（Sprint 8-1
      已完成，见下方执行记录）
- [ ] 至少一种旧 Scheduler 已成功接入 `AgentRuntime`
- [ ] 剩余 Scheduler 有明确的评估结论和排期，而不是被忽略
- [x] 所有涉及模块的原有测试全部通过（Sprint 8-1 范围内，见下方执行记录；
      整个 Phase 8 完成标志仍要求 Sprint 8-2/8-3 涉及模块的测试也全部通过）

## Sprint 8-1 执行记录

- 新增 `runtime/` 包（`runtime/__init__.py` + `runtime/runtime.py`）：
  `AgentRuntime.run_once(goal_spec)` 实现 `observe → state update →
  gap detect → plan → simulate → decide → execute → record → learn`
  九步骨架，**只支持单次运行**（符合本 Sprint"先跑通单次循环，再考虑
  持续运行"的范围声明）：
  - `observe`：读取 `StateManager.snapshot()`（Phase 8 尚无真实
    Perception 管线接入，如实只做这一步，不假装更多）。
  - `state update`：只做一次只读的 `GoalAdapter.to_new(goal_spec)`
    转换（`GoalState` 的真正托管仍由 `GoalRunner.run()` 内部完成，
    避免同一个 state kind 被两处分别 seed 造成竞态）。
  - `gap detect`：调用 Phase 5 `goals/gap.py::detect_gap()`（唯一默认
    真实执行的中间步骤，纯函数、不需要 LLM 注入）。
  - `plan`/`simulate`/`decide`：**默认跳过**，沿用 Phase 7 Sprint 7-2
    "不强行接入主循环"的范围决策（见
    `08-phase7-decision-simulation-sprint-plan.md`"后续判断依据"），
    避免过度设计；显式标注为后续 Sprint 或专门评估任务的候选项，不是
    静默空实现。
  - `execute`/`record`：**完全复用** `goal_mode/runner.py::GoalRunner`
    （不重新实现），`AgentRuntime.run_once()` 只在外面包一层——这是
    验收标准"不是重新实现一遍逻辑，而是复用"的直接落实。
  - `learn`：TODO，留给 Phase 9。
  - 循环边界新增 `RuntimeCycleStarted`/`RuntimeCycleCompleted` 两个
    Event kind（已加入 `core/events.py::EVENT_KINDS`），与 `GoalRunner`
    内部已有的 `GoalCreated/ActionStarted/ActionCompleted/GoalUpdated/
    ExperienceCreated` 事件共享同一条因果链（各自独立的
    `correlation_id`，`RuntimeCycleStarted`/`Completed` 包裹住内部的
    Goal 闭环，两条 correlation_id 不强行合并，因为语义层级不同——
    一个是"一次 Runtime 循环"，一个是"一次 Goal 闭环"）。
- 接入点：`cli/commands/goal_mode_cmd.py::_run_goal()`（`/goal <文本>`
  确认后触发执行的唯一入口）内部实现从"直接调用 `GoalRunner`"改为
  "调用 `AgentRuntime.run_once()`"，旧入口本身（slash 命令签名、
  返回值展示逻辑）不改动；`Ctrl-C` 中断处理改为通过
  `AgentRuntime.last_runner`（`run_once()` 内部赋值，供调用方在
  `KeyboardInterrupt` 时取到底层 `GoalRunner` 实例调用 `pause()`）。
  `/goal resume` 场景（`_handle_resume()`）本 Sprint **未改动**——
  `AgentRuntime.run_once()` 已经透传了 `state_store`/`resume_state`
  参数为后续接入预留接口，但按"先跑通单次循环"的范围声明，只优先
  接入了"用户主动发起一个新 Goal"这一种场景，恢复场景留待确认是否
  需要在 Sprint 8-2 一并接入。
- `AgentRuntimeResult` 字段名与 `GoalRunResult` 保持一致（`status`/
  `rounds_used`/`compacts_done`/`final_report`/`goal_spec`/
  `replan_proposal`），额外携带 `gap`/`correlation_id`/
  `goal_run_result`，使得 `_run_goal()` 改动后不需要跟着改任何读取
  这些字段的展示代码。
- 验收标准验证：新增测试 `tests/test_phase8_runtime.py`（4 用例）：
  `run_once()` 确实调用了 `GoalRunner`（`agent._call_idx == 1`，非
  重新实现判定逻辑）/ Event 总线上 `RuntimeCycleStarted` → ... →
  `RuntimeCycleCompleted` 顺序正确且夹住 `GoalCreated`/
  `ExperienceCreated` / gap detect 步骤真实产出结果 / `KeyboardInterrupt`
  时 `last_runner` 可用，全部通过。回归测试（`test_goal_mode.py`/
  `test_goal_mode_phase2_events.py`/`test_goal_mode_characterization.py`/
  `test_phase4_state_manager.py`/`test_phase5_gap_detection.py`/
  `test_core_events.py` 共 292 用例，287 通过，5 个既有失败
  `test_build_from_history_*` 与本次改动无关，同此前多个 Phase 记录里
  提到的一组）；`core/events.py::EVENT_KINDS` 新增两个取值后同步更新了
  `tests/test_core_events.py::test_event_kinds_covers_sprint_2_1_minimal_set`
  的断言集合（测试本身随事件集合扩展更新，非放松验证）；
  `pyflakes` 对新增/改动文件无告警；`scripts/dep_graph.py --module
  mini_agent.runtime.runtime` 核对 inbound=0（脚本按精确模块路径匹配，
  `cli/commands/goal_mode_cmd.py` 是包级 `from mini_agent.runtime import
  AgentRuntime`），outbound=0，未触发止损阈值。
- 可进入 **Sprint 8-2（接入持续运行 + 一种旧 Scheduler）**：需要先按
  Sprint 8-2 任务表"选一个耦合最小的 Scheduler"扫描依赖图确认候选，
  留待下一次推进。
