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
- [x] 至少一种旧 Scheduler 已成功接入 `AgentRuntime`（Sprint 8-4
      已完成：`cron_job_runner` 的 message 任务，见下方"Sprint 8-4
      执行记录"）
- [x] 剩余 Scheduler 有明确的评估结论和排期，而不是被忽略（Sprint 8-3
      已完成，见下方"Sprint 8-3 执行记录"）
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

## Sprint 8-2 执行记录

**任务表第一项（持续运行）已完成**：新增
`runtime/event_loop.py::RuntimeEventLoop`——`run_forever(goal_spec_provider,
stop_event=None, max_iterations=None)` 实现任务表要求的 `while running:
run_once(...)` 外壳：

- `goal_spec_provider()` 返回 `None` 表示本轮没有可执行 Goal，计入
  `iterations_skipped`，睡眠 `poll_interval_seconds` 后重试（用
  `threading.Event.wait(timeout=...)` 而不是 `time.sleep()`，
  `stop_event.set()` 能立刻打断等待，不用傻等满一个 poll interval）。
- `run_once()` 内部异常只终止当前这一轮（计入 `iterations_failed` +
  `log_exception`），循环本身继续跑下一轮——常驻服务"单次任务失败不
  拖垮整个进程"的基本要求，与 `evolution/autonomous_loop.py`/
  `cli/daemon.py` 既有的"单个环节异常整体吞掉，只记日志"风格一致。
- 优雅退出两种方式：`stop_event.set()`（供另一个线程发起停止）与
  `KeyboardInterrupt`（本地调试 Ctrl-C），两者都让 `run_forever()`
  正常返回而不抛异常，返回值 `RuntimeEventLoopStats.stop_reason` 记录
  停止原因（`"stop_event"` / `"keyboard_interrupt"` / `"max_iterations"`）
  供调用方/测试判断。
- 新增测试 `tests/test_phase8_sprint8_2_event_loop.py`（5 用例）：真实
  provider 驱动 `run_once()` 被正确调用其次数 / provider 返回 None 时
  跳过且不误计入失败 / `run_once()` 抛异常时循环不中断且正确计数 /
  provider 自身抛异常同样不中断循环 / `stop_event` 能在远小于
  `poll_interval` 的时间内打断等待（用独立线程延迟 `set()` + 断言墙钟
  耗时验证，不是只验证调用次数），全部通过。回归测试
  （`test_goal_mode.py`/`test_goal_mode_phase2_events.py`/
  `test_goal_mode_characterization.py`/`test_phase4_state_manager.py`/
  `test_phase5_gap_detection.py`/`test_core_events.py`/
  `test_phase8_runtime.py` 共 160 用例，155 通过，5 个既有失败
  `test_build_from_history_*` 与本次改动无关，同此前多个 Phase 记录里
  提到的一组）；`pyflakes` 无告警；`scripts/dep_graph.py --module
  runtime.event_loop` 核对 inbound=1（仅 `runtime/__init__.py` 转导出），
  outbound=0，未触发止损阈值。

**任务表第二项（选一个旧 Scheduler 接入）本 Sprint 评估后暂缓**，触发
了本 Sprint 自己定义的止损条件（"某个 Scheduler 内部有大量特殊逻辑无法
简单转化为'提交一个 Goal'"），详见下方"变更记录"。

### 六个候选的依赖图扫描 + 评估结论

按任务表要求先扫描依赖图确认候选（用法见"文档写作规范"第 5 条，均按
具体子模块而非顶层包扫描）：

| 候选 | `--module` 参数 | inbound | 评估结论 |
|---|---|---|---|
| `AutonomousLoop` | `evolution.autonomous_loop` | 1 | inbound 最低，但内部 `_tick_autonomous()`/`_trigger_objective_candidate()` 深度耦合 `ObjectiveExecutor` 的公平调度/资源仲裁/暂停恢复语义，改动风险与"表面耦合度"不成正比，见下方"变更记录"第 1 条 |
| `Cron`（`run_mode="goal_cycle"`，经 `goal_cron_bridge.py`） | `evolution.cron_scheduler` | 22（超阈值） | 已超 Sprint 0 止损阈值；即使不改 `CronScheduler` 本身，其触发出口 `goal_cron_bridge._fire_goal_cycle()` 内部有约 20 个 `_append_*`/`_maybe_*` 辅助函数（执行阶段判定/产出目录约束/执行规范核对……），且落点是 `objective_executor.start()`（异步 fire-and-forget），见"变更记录"第 2 条 |
| `UnifiedTaskScheduler` | `evolution.unified_task_scheduler` | 4 | 本身只是"聚合展示 + 排序建议"层，`execute()` 均为委托转发，目前**未被任何既有 tick 路径调用**（模块自身文档已声明），不是真正在运行的触发入口，接入它不会让任何真实任务改道 |
| `ObjectiveExecutor` | `evolution.objective_executor` | 5 | 已在 Phase 5 Sprint 5.0.5 记录过："低耦合但高时序敏感"，唯一实例化点嵌在 `api/server.py::_build_autonomous_loop()` 的强时序依赖构造闭包里，Phase 6 现状盘点已判定"改动构造闭包"是风险点，"新增转发分支调用已构造好的实例"不是——本 Sprint 评估的是前者（把它整体换成 Runtime），仍属于前者范畴 |
| `ResourceArbiter` | `evolution.resource_arbiter` | 10（达阈值） | 本身是纯粹的资源检查/仲裁工具模块（`gating_state()`/`record_autonomous_token_usage()`），不持有"触发一个任务"的入口，不属于本 Sprint"把触发逻辑改为提交 Goal"的范畴——这正是止损条件例句里"它本身不是在做目标导向的任务，而是纯粹的后台维护任务"的情形，**判定暂不收编，保留独立运行** |
| `cron_job_runner`（`run_mode="message"` 普通 cron） | `evolution.cron_job_runner` | 2 | inbound 低，且执行本身已经在独立线程里（`submit()` 立即返回，不阻塞主循环），乍看是最合适的候选；但深入 `CronJobExecutor.run_job()` 后发现它自己就是一套完整的"单次任务执行框架"（`stuck detection`/超时/`circuit breaker`/`memory backfill`/watchdog 存活性回收），语义上与 `GoalRunner` 平行而非从属——直接切换成 `AgentRuntime.run_once()` 不是"转发"而是要么丢弃这些既有能力、要么整体重写，两者都违反"不做 Big Bang Rewrite"原则，见"变更记录"第 3 条 |
| `Daemon`（`cli/daemon.py`） | `cli.daemon` | 6 | 是进程管理/CLI 命令层（start/stop/status/restart），不持有任何"触发任务"的调度逻辑本身——它只是把 `AgentRunner` 常驻进程管理起来，真正的调度逻辑仍是上面几个模块，因此不构成独立的第七个候选，只是"运行以上调度逻辑的宿主进程"这一层 |

结论：六个候选里，`ResourceArbiter`/`Daemon` 本身不适用（不是"触发任务
入口"）；`UnifiedTaskScheduler` 是尚未被任何路径调用的只读层（接入它
不会让真实任务改道，不满足验收标准"被接入的旧 Scheduler **触发的任务**
现在走的是 `AgentRuntime.run_once`"）；其余三个（`AutonomousLoop` 的
Objective 触发路径、`goal_cycle`、`cron_job_runner` 的普通 message
任务）都因为下方"变更记录"列出的具体技术障碍而暂缓。**本 Sprint 不
强行拿一个"看起来简单但实际会丢功能/会破坏并发语义"的候选去凑"完成
一项"，而是把评估过程和结论如实记录**，留给 Sprint 8-3（本来就是
"剩余 Scheduler 逐个评估表"的范围）在此基础上排定后续接入顺序。

## 变更记录 2026-09-27

- 触发条件：Sprint 8-2 止损条件——"如果发现某个 Scheduler 内部有大量
  特殊逻辑无法简单转化为'提交一个 Goal'……应该在 phase-mapping 文档里
  明确标注'这个 Scheduler 暂不收编，保留独立运行'，而不是强行套用
  Runtime 抽象"。
- 原计划：Sprint 8-2 任务表第二项要求"从 Daemon/AutonomousLoop/Cron/
  UnifiedTaskScheduler/ObjectiveExecutor/ResourceArbiter 里选耦合最小
  的一个"，把它的触发逻辑改为向 `AgentRuntime` 提交 Goal，验收标准是
  "被接入的旧 Scheduler 触发的任务，现在走的是 `AgentRuntime.
  run_once`，且原有该 Scheduler 的测试全部通过"。
- 实际情况（逐条对应上面表格）：
  1. `AutonomousLoop._tick_autonomous()` 触发 Objective 的路径
     （`_trigger_objective_candidate()`）深度依赖
     `ObjectiveExecutor.fairness_paused_objective_ids()`/
     `user_paused_objective_ids()`/`running_count_for_goal()` 等公平
     调度状态，这些状态目前只存在于 `ObjectiveExecutor` 内部，
     `AgentRuntime.run_once()`（Sprint 8-1 设计为同步单次运行）没有
     对应的并发槽位/公平性概念，直接替换会丢失"多个 Goal 公平轮转"
     这个现有用户可感知的行为。
  2. `goal_cron_bridge._fire_goal_cycle()` 落点是
     `objective_executor.start(objective)`——**异步 fire-and-forget**
     （立即返回 `exec_id`，真正执行在别处异步推进，供
     `_goal_has_active_cycle()` 幂等检查用），而 `AgentRuntime.
     run_once()` 是**同步阻塞**直到整个 Goal 跑完才返回（Sprint 8-1
     范围声明原文："只支持单次运行"）。`_fire_goal_cycle()` 本身跑在
     `CronScheduler.tick()`（`AutonomousLoop._tick_passive()` 的一部分）
     调用链上，这条链最终又是 `AgentRunner` 主循环 dequeue 超时后的
     同步分支——如果换成同步阻塞的 `run_once()`，会让整个 daemon 主
     循环卡死到一次 Goal 跑完为止，这是一个具体的、可验证的架构不
     兼容，不是"嫌麻烦"。
  3. `cron_job_runner.py::_run_message_job()` 虽然已经跑在独立线程里
     （不存在第 2 条的阻塞问题），但它转发给的 `CronJobExecutor.
     run_job()` 自己就是一套完整执行框架（stuck detection、超时、
     circuit breaker、memory backfill、watchdog 存活性回收），跟
     `GoalRunner` 是"两套平行的单次任务执行框架"而不是"一个可以被
     另一个整体替代的简单触发点"——替换意味着要么丢弃这些能力，要么
     把它们整体搬进 `AgentRuntime`/`GoalRunner`，两者都是明显更大的
     改动，违反"不做 Big Bang Rewrite"的全局止损原则。
- 调整后方案：Sprint 8-2 完整完成"持续运行"（`runtime/event_loop.py`），
  "接入一种旧 Scheduler"改为**移交 Sprint 8-3**——正好与 Sprint 8-3
  "剩余 Scheduler 逐个评估表"的任务性质一致，上面的评估表直接作为
  Sprint 8-3 的起点，避免重复调研。`ResourceArbiter` 额外明确标注为
  "不属于本 Phase 收编范围"（不是"暂缓"，是"从一开始就不适用"），
  不占用 Sprint 8-3 的评估名额。
- 影响范围：Phase 8"完成标志"里"至少一种旧 Scheduler 已成功接入
  `AgentRuntime`"这一条继续保持未完成（`- [ ]`），移到 Sprint 8-3 里
  基于本次评估表选定具体候选后再推进；不影响"至少一种触发路径完全走
  `AgentRuntime`"（Sprint 8-1 已达成）和"所有涉及模块的原有测试全部
  通过"（本 Sprint 范围内的 `runtime/event_loop.py` 已验证）两条。

## Sprint 8-3 执行记录

任务表两项都是纯评估/排期产出，本 Sprint 不改动任何生产代码——
"逐个评估表"直接复用 Sprint 8-2 已经做过的六候选依赖图扫描 + 代码
阅读结论（`ResourceArbiter`/`Daemon` 不适用、`UnifiedTaskScheduler`
未被任何路径实际调用，已在上面"六个候选的依赖图扫描 + 评估结论"表格
中定论，不重复调研），本节只补齐"能否接入、大概需要多久、风险等级"
三项，并给出排期建议。

### 剩余三个真实候选的评估表（能否接入 / 预估工作量 / 风险等级）

| 候选 | 能否接入 `AgentRuntime` | 需要的前置改动 | 预估工作量 | 风险等级 |
|---|---|---|---|---|
| `cron_job_runner`（普通 `run_mode="message"` cron 任务） | 可以，但需要新增能力而非简单转发 | 1) 给 `AgentRuntime` 加一个非阻塞的 `run_once_async()`（沿用 `cron_job_runner.py` 已有的"独立线程执行"模式，返回句柄/Future，不阻塞调用方）；2) 新增 `cron.runtime_dispatch_enabled`（默认 `False`）配置开关，仅当开启时 `_run_message_job()` 走新路径，旧路径（`CronJobExecutor.run_job()`）完全不动；3) 把 `RunOutcome` 的关键字段（status/output/error/耗时）映射回 `CronJobWorkspace` 状态，让 watchdog/`reap_stale_jobs`/摘要展示等下游消费者无感 | 约 1 个 Sprint（0.5 加 `run_once_async` + 测试，0.5 接线 + 状态映射 + 回归） | **低-中**：新增而非替换，默认关闭不影响存量行为；风险集中在"状态映射是否完整覆盖 `CronJobExecutor` 原有语义"，需要专门的对照测试而不能只测"调用到了新路径" |
| `goal_cycle`（经 `goal_cron_bridge.py`，`objective_executor.start()`） | 可以，但前提是先有第一行的 `run_once_async()`（`goal_cycle` 本身就要求非阻塞 fire-and-forget，`run_once()` 同步阻塞在此处完全不可用，`run_once_async()` 是硬前提，不是可选优化） | 在 `run_once_async()` 基础上，还需要在 `AgentRuntime`/`RuntimeEventLoop` 层补一个"公平调度/并发槽位"的最小实现（对应 `ObjectiveExecutor` 现有的 `fairness_paused_objective_ids()`/`running_count_for_goal()` 语义），否则会退化成"每个 Goal 无限制并发触发"，是比 `cron_job_runner` 更大的一块改动 | 约 2 个 Sprint（第 1 个 Sprint 做 `run_once_async()`，第 2 个 Sprint 做最小公平调度 + `goal_cron_bridge` 接线 + 回归） | **中**：依赖第一行先完成；公平调度语义如果实现不完整，会在"多 Goal 并发"场景下产生行为回归，需要专门补一批"公平性"测试用例（可参考 `ObjectiveExecutor` 现有测试作为基准） |
| `AutonomousLoop._tick_autonomous()`（Objective 触发路径） | 可以，但依赖上面两行都做完 | 除了公平调度，还要处理 `_trigger_objective_candidate()` 里"暂停/恢复"（`fairness_paused`/`user_paused`）等状态机语义，是三者中状态最复杂的一个；且 `AutonomousLoop` 本身 1293 行、职责混杂（passive/maintenance/autonomous 三档 tick 都在同一个类里），改动前建议先做一次独立的"职责拆分"而不是直接在现状上打补丁 | 约 2-3 个 Sprint（前置 1-2 个 Sprint 做职责拆分，之后再接入） | **中-高**：改动面最大，且改动前需要先做拆分（拆分本身就是不小的工作量），是三者里最不适合"抢首个接入"的一个 |

### 排期建议

1. **优先接入 `cron_job_runner`**：工作量最小、风险最低、且不依赖任何
   其它前置改动，是"至少一种旧 Scheduler 已成功接入 `AgentRuntime`"
   这条完成标志最现实的落点，建议排为 **Sprint 8-4**。
2. **`goal_cycle` 排在其后**：依赖 `run_once_async()`（Sprint 8-4 的
   副产物可以直接复用），核心新增是最小公平调度，建议排为
   **Sprint 8-5**。
3. **`AutonomousLoop` 排最后**：既依赖前两者的基础设施，又需要一次
   独立的职责拆分作为前提，建议排为 **Sprint 8-6**，且拆分本身可以
   考虑单独立项而不占用 Phase 8 的 Sprint 编号（类似 Phase 6 对
   `objective_executor.py` 构造闭包的处理方式：先记录风险，不强行在
   本 Phase 内解决）。
4. `ResourceArbiter`/`Daemon`/`UnifiedTaskScheduler` 不进入排期——原因
   已在 Sprint 8-2 评估表中定论（不适用 / 未被实际调用），不需要
   "评估结论和排期"之外的进一步动作。

### 完成标志核对

- 六个候选均已有"能否接入 + 评估结论"（三个不适用/未使用，三个有
  具体工作量/风险等级评估），满足"剩余 Scheduler 有明确的评估结论和
  排期，而不是被忽略"——对应完成标志第二条改为 `[x]`。
- "至少一种旧 Scheduler 已成功接入 `AgentRuntime`"（完成标志第一条）
  仍为 `[ ]`：按上面排期，这是 Sprint 8-4 的产出，本 Sprint（8-3）
  按任务表定义"不要求本 Phase 内全部完成"，不在本次改动范围内，留给
  下一次对话按排期推进。
- 本 Sprint 未修改任何生产代码/测试，仅更新文档，因此没有新增测试
  运行记录；`pyflakes`/`dep_graph.py` 均不涉及（无代码改动）。

## 变更记录 2026-09-27（续）

- 触发条件：无——Sprint 8-3 任务表本身就是"评估表 + 排期"，不涉及
  代码改动，不存在需要触发止损条件的情形，记录仅为与
  `12-execution-and-doc-sync-norms.md` 的"每完成一个阶段都要更新文档"
  要求保持一致的常规文档同步。
- 结果：Sprint 8-2 遗留的两个未完成的"完成标志"中，"剩余 Scheduler
  有评估结论和排期"（第二条）已在本 Sprint 完成；"至少一种旧
  Scheduler 已成功接入"（第一条）按排期表移交 Sprint 8-4（`cron_job_runner`
  + 新增 `AgentRuntime.run_once_async()`）。

## Sprint 8-4 执行记录

按 Sprint 8-3 排期，接入 `cron_job_runner`（三个真实候选中工作量最小、
风险最低的一个）。

**改动内容：**

- `config/models.py::CronConfig` 新增 `runtime_dispatch_enabled: bool =
  False`。
- `evolution/cron_job_runner.py::_run_message_job()` 开头新增一个分流：
  `runtime_dispatch_enabled=True` 时调用新方法
  `_run_message_job_via_runtime()` 并直接返回，**共用同一个外层
  try/except**（该分支自身异常同样被下面既有的兜底 except 捕获并标记
  `needs_human_review`，没有写第二份异常处理）；默认 `False` 时这段
  代码原样保留，一字未改。
- 新增 `_run_message_job_via_runtime(job)`：复用既有的
  `build_cron_agent()` 构造 Agent（不重新实现"怎么为 cron job 配一个
  Agent"），用 `job.task_template` 直接构造
  `GoalSpec(goal_text=..., confirmed=True)`（cron job 没有"用户协商
  验收标准"这个概念，跳过 GoalSpecBuilder 协商阶段），调用
  `AgentRuntime.run_once()`；返回 `status=="done"` 时写
  `CronJobWorkspace` 状态为 `STATUS_IDLE`，否则为
  `STATUS_NEEDS_REVIEW` 并把状态原因写进 `last_error`；`run_once()`
  内部抛异常时同样标记 `STATUS_NEEDS_REVIEW`，异常信息记入
  `last_error`，不让线程崩溃；token 记账复用与旧路径相同的
  `ResourceArbiter.record_autonomous_token_usage(usage_type="cron")`；
  `on_finished` 回调收到的 `RunOutcome` 用 `AgentRuntimeResult.
  rounds_used` 填充 `steps_executed`。

**没有做、且原因需要如实记录的部分（已知限制）：**

- 没有实现"跨次恢复"——`CronJobExecutor.run_job()` 原本支持
  `CronJobWorkspace.render_prompt()` 读取上次的 `run_id`/
  `progress_summary`，让一个没跑完的长任务下次触发时接着做；
  `AgentRuntime.run_once()`（Sprint 8-1/8-2 范围）本身没有这个"跨次
  恢复"能力，因此走新路径的 cron job 每次触发都是全新的一轮
  `GoalRunner` 执行，不会接续上次进度。这不是本 Sprint 引入的新限制，
  是 `AgentRuntime` 当前范围本身决定的，开启 `runtime_dispatch_enabled`
  前用户需要知道这一点——因此默认值保持 `False`，只有明确需要"每次
  独立执行、不需要跨次续接"的 cron job 才适合开启（比如一次性检查类
  任务），需要"长任务续接"的 cron job 不应该开启。
- 没有做 stuck detection / 单步超时（`CronJobExecutor.run_job()`
  原有能力）——`GoalRunner` 本身有自己的轮次上限和 `run_goal_judge()`
  判定收敛的机制，但判定逻辑与 `CronJobExecutor` 的
  `StuckDetector`/`timeout_seconds` 不是同一套，行为观感会不同（不是
  "缺失"，是"换了一套不同的收敛判定"，同样需要用户知晓）。
- 未接入 `CircuitBreakerCore`（`_run_message_job()` 走
  `CronJobExecutor` 时会传入共享熔断实例）——`GoalRunner` 路径下暂不
  统计熔断，跨 job 系统性失败告警在这条路径下不会触发。

上述限制均已写入本节，供后续 Sprint（如果决定继续完善这条路径）或
用户决定是否开启这个开关时参考，不是被忽略的隐藏差异。

**测试与验证：**

- 新增 `tests/test_phase8_sprint8_4_cron_runtime_dispatch.py`（5
  用例）：flag 关闭时新路径完全不触发／flag 开启 + `run_once()`
  返回 `done` 时状态与 `on_finished` 回调正确／flag 开启 +
  非 `done` 状态时标记 `needs_human_review`／flag 开启 +
  `run_once()` 抛异常时不崩溃且正确标记／token 记账正确调用，全部
  通过。
- 回归测试：`test_cron_job_runner.py`（68 用例，含
  `test_cron_job_runner_resource_arbiter.py`/
  `test_cron_job_workspace_and_executor.py`）+
  `test_goal_cron_unified_scheduler_p0_p1_p2.py` +
  `test_phase8_runtime.py`/`test_phase8_sprint8_2_event_loop.py`/
  `test_goal_mode.py` 共 188 用例，183 通过，5 个既有失败
  `test_build_from_history_*` 与本次改动无关（同前几次 Sprint 记录里
  提到的同一组）。
- `pyflakes` 对本次改动的三个文件（`cron_job_runner.py`/
  `config/models.py`/新测试文件）均无新增告警（`models.py` 现有的两条
  `unused import` 告警是改动前就存在的，与本次改动无关）。
- `scripts/dep_graph.py --module evolution.cron_job_runner`：inbound=2，
  未触发止损阈值（改动前后耦合度不变，本次只是在模块内部新增一个
  opt-in 分支，没有新增/减少任何外部 import 关系）。

## 完成标志核对（更新）

- "至少一种旧 Scheduler 已成功接入 `AgentRuntime`"：**已完成**——
  `cron_job_runner` 的 `run_mode="message"` 任务在
  `cron.runtime_dispatch_enabled=True` 时，触发的任务现在走的是
  `AgentRuntime.run_once()`，原有该 Scheduler 的测试
  （`test_cron_job_runner.py` 等 68 用例）全部通过。完成标志第一条
  改为 `[x]`。
- Phase 8 四条完成标志全部达成（第一条 Sprint 8-1、第二条本 Sprint、
  第三条 Sprint 8-3、第四条 Sprint 8-1/8-2/8-4 各自范围内验证）——
  Phase 8 可视为主体完成；`goal_cycle`/`AutonomousLoop` 两个候选按
  Sprint 8-3 排期（Sprint 8-5/8-6）留待后续单独推进，不阻塞 Phase 8
  收尾。
