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

- [ ] 至少一种触发路径（用户主动 Goal）完全走 `AgentRuntime`
- [ ] 至少一种旧 Scheduler 已成功接入 `AgentRuntime`
- [ ] 剩余 Scheduler 有明确的评估结论和排期，而不是被忽略
- [ ] 所有涉及模块的原有测试全部通过
