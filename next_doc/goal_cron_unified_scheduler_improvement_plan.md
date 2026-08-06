# Goal / Cron 三条执行通道 统一调度层 改进计划

- **版本**: v1.9
- **实施记录**: `next_doc/goal_cron_unified_scheduler_implementation_record.md`
  （P0/P1/P2/P3/P4 已完成；P5 第 1-3 步已完成，第 4 步部分完成
  [cron/goal_cycle 两通道 execute() 已实现委托派发；goal 通道 execute()
  未实现]，第 5 步已启动 [cron/goal_cycle 两通道新增
  `scheduler.unified_dispatch_enabled` 灰度开关，经 `_tick_passive()`
  对 passive/maintenance/autonomous 三个档位同时生效，默认关闭；Goal
  通道派发路径未涉及]）
- **变更记录**：
  - v1.9：**订正 v1.8 的一处不准确表述**——v1.8 曾写"`_tick_maintenance()`/
    `_tick_autonomous()` 两个档位仍直接调用 `cron_scheduler.tick()`，未
    接入该开关"，复核代码后发现这一表述不准确：`_tick_maintenance()`
    方法体第一行就是 `self._tick_passive()`，`_tick_autonomous()` 方法
    体又以 `self._tick_maintenance()` 开头——三个档位在 cron 触发这件事
    上共用同一个物理调用点（`AutonomousLoop._tick_passive()` 内部那一
    处 `if self._cron_scheduler is not None:` 分支），`scheduler.
    unified_dispatch_enabled` 开关打开后对 **passive/maintenance/
    autonomous 三个档位同时生效**，不存在"仅 passive 档位生效、其余两个
    档位需要额外接线"这回事。本轮新增测试
    `test_maintenance_level_inherits_gate_via_tick_passive_delegation`
    验证该行为，并同步修正了 `autonomous_loop.py` 里 `_tick_passive()`
    对应代码段的注释、本文档 P5 第 4 步分步迁移路径描述、待讨论问题 6
    的措辞。**不涉及任何代码行为变化**——纯粹是文档准确性订正，代码
    本身在 v1.8 就已经是这个行为，只是记录写错了。
  - v1.8：P5 第 5 步（收敛到统一入口）启动并完成一个子集——新增纯函数
    `dispatch_due_cron_jobs()`，合并 cron/goal_cycle 两条通道到期任务后
    按 priority 统一触发，内部完全复用 P5 第 4 步已有的 `execute()` →
    `trigger_job_now()` → `_trigger_and_record()` 委托链路；新增配置开关
    `scheduler.unified_dispatch_enabled`（默认 `False`），仅在
    `AutonomousLoop._tick_passive()` 一个方法体内生效——`True` 时改用
    `dispatch_due_cron_jobs()`，`False`（默认）时保持原有
    `cron_scheduler.tick()` 调用，两条路径的到期判断/触发/记账口径完全
    一致，只是"谁来组织触发顺序"不同。**明确未做**：`_tick_maintenance()`/
    `_tick_autonomous()` 两个档位下的 cron 触发路径未接入这个开关（各自
    独立调用 `cron_scheduler.tick()`，行为不变）；`ObjectiveChannelAdapter.
    execute()` 仍未实现，Goal 通道派发路径完全不受本轮影响。附带修复了
    `_poll_cron_jobs()` 里 `next_run_at <= 0`（未初始化哨兵值）被误判为
    "已到期"的边界条件——P5 第 1-2 步该字段只用于只读预览时这个误差不
    影响任何实际执行，但本轮 `dispatch_due_cron_jobs()` 要真正拿它去
    触发，必须与 `tick()` 到期判断口径完全一致。
  - v1.7：P5 第 4 步（接管实际派发）部分完成——`CronScheduler.tick()`
    内部"触发单个 job + 记账"逻辑抽成 `_trigger_and_record()`，并新增
    公开入口 `trigger_job_now(job_id)` 复用同一份记账逻辑（行为保留式
    重构，既有 cron 相关测试全部通过，无回归）；`CronChannelAdapter`/
    `GoalCycleChannelAdapter.execute()` 改为真正委托 `trigger_job_now()`，
    不再 `raise NotImplementedError`。**`execute()` 目前仍未被
    `UnifiedTaskScheduler` 自身或 `AutonomousLoop` 的任何既有 tick 路径
    调用**——`CronScheduler.tick()` 依然是唯一的实际触发入口，这是"已经
    可以安全调用，但还没有人在调用"的中间状态，为将来切换统一入口做
    准备但本身不改变任何现有运行时行为。`ObjectiveChannelAdapter.
    execute()` 因 Goal 通道派发逻辑深度耦合 `AutonomousLoop` 运行时状态、
    缺少类似 `trigger_job_now()` 的安全公开入口，本轮未实现，留待后续。
  - v1.6：P5 第 3 步（接管仲裁裁决）已实现——新增纯函数
    `allocate_weighted_slots()`，`ObjectiveExecutor`/`CronJobRunner` 在
    `scheduler.unified_arbitration_enabled=True`（默认 `False`）时，
    degraded 状态的并发上限改由该函数按 `channel_weights`/
    `degraded_total_slots`/`cron.reserved_min_concurrent` 统一裁决，两条
    通道从此互相感知对方的权重分配；开关默认关闭，未升级配置的用户行为
    完全不变。`GET /v1/self/unified_scheduler_preview` 新增
    `slot_allocation` 字段展示当前配置下的裁决结果（与开关是否打开无关，
    可提前观察）。第 4-5 步（接管实际派发）仍未启动，详见实施记录。
  - v1.5：P5 第 1-2 步（定义统一接口 + 三条通道只读适配 + 只读聚合排序
    建议）已实现，新增 `UnifiedTaskScheduler`/`TaskChannel` 及配套只读
    预览端点 `GET /v1/self/unified_scheduler_preview`。**不接管任何实际
    执行决策**，三条通道现有触发路径完全不受影响。第 3-5 步（接管仲裁
    裁决/接管实际派发）仍未启动，详见实施记录。
  - v1.4：P4 补齐看板 UI 展示区块（"🕹️ 统一调度总览"，并入"🧠 自我状态"
    tab），至此 P4 前后端均已完成。详见实施记录。P5 状态不变，仍未实现。
  - v1.3：P4（统一调度可观测面板）已实现（后端只读端点部分；看板 UI
    展示区块本轮未做，见实施记录说明），详见实施记录。P5 状态不变，
    仍未实现。
  - v1.2：P3（tick() 执行看门狗）已实现，详见实施记录。P4/P5 状态不变，
    仍未实现。
  - v1.1：P0（cron 分级响应资源仲裁）、P1（cron 消耗统一记账）、P2（cron
    跳过追踪与主动告警）已实现，详见实施记录。P3/P4/P5 状态不变，仍未实现。
  - v1.0：初版。规划 P0-P5，均未实现。P0/P1 是低风险的过渡修复（消解当前
    "Goal 挤占 Cron"的直接症状），P2-P4 是可观测性与保护机制补齐，P5 是
    本文档的最终目标——把三条通道收敛到一个统一调度层，属于架构级重构，
    分步骤迁移，不做推倒重来。
- **背景**：daemon 里实际存在三条相互独立、但共享同一份底层 LLM 资源的执行通道：
  1. **Goal → Objective**：`ObjectiveExecutor` 从 `GoalBacklog` 按
     `fair_round_robin`（老化补偿）或 `priority` 策略挑选，受全局并发槽位 +
     per-Goal 并发上限约束（`goal_execution_fairness_improvement_plan.md`
     已完成 P1-P5）。
  2. **普通 cron job**（`run_mode="message"`）：`CronScheduler.tick()` 到期后
     交给 `CronJobRunner` 起独立线程执行，自己的
     `threading.Semaphore(max_concurrent=2)`
     （`cron_dedicated_execution_improvement_plan.md`）。
  3. **goal_cycle cron**（Goal 绑定周期性执行）：同样经 `CronScheduler.tick()`
     触发，但走 `_fire_goal_cycle` 独立分支，实际上又转发进
     `ObjectiveExecutor`，复用通道 1 的并发/公平性
     （`goal_cron_binding_plan.md`）。
  三条通道各自实现了一部分"并发控制 + 资源仲裁"，仲裁的最终裁判是
  `ResourceArbiter.gating_state()`（`full`/`degraded`/`blocked` 三态），但
  各通道对同一个仲裁结果的响应方式并不一致，也没有互相感知对方此刻占用了
  多少资源。复核代码（见
  `daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md`
  的既有讨论 + 本次线上问题排查）发现的具体不对称：
  - 通道 1 消耗预算会写入 `rb.used_today_goals`；通道 2（普通 cron）跑掉的
    token **不计入任何预算计数器**；通道 3 因为借道 `ObjectiveExecutor`，
    实际计入的是 goals 的账，语义上也说不通（明明是 cron 触发的）。
  - `ResourceArbiter` 进入 `blocked` 后：通道 1 是 `pause_all()`（暂停，
    资源恢复后自动续跑，不丢失进度）；通道 2 是 `submit()` 直接返回
    `False`（本次触发**整体跳过**，不计入 `last_run_at`，下次 tick 重试）；
    两种响应方式对"到点应该执行"这件事的语义完全不同，但共用同一个
    `blocked` 信号。
  - `blocked` 是二元的、没有"最低保障"概念——Goal 只要持续把预算耗到底，
    普通 cron 理论上可以被连续跳过，直到预算重置或 frustration 状态恢复
    为止，期间没有任何补偿机制。
  - tick() 内部"决策 + 提交，不做耗时调用"这条约束目前只是代码注释里的
    君子协定，没有强制手段——上一轮复核就发现 `_run_capability_exploration()`
    违反了这条约束，阻塞 tick() 最长 5 分钟，直接表现为 cron 长期不触发
    （已在另一轮修复中改为后台线程，见改动记录，但这类问题理论上还会在
    未来新增代码里重现，因为约束本身不是强制的）。
- **设计边界（明确写出以防后续误解）**：
  1. 本计划**不改变**三条通道各自已经实现好的"通道内部"调度逻辑
     （`ObjectiveExecutor` 的公平轮询/老化补偿、`CronJobRunner` 的
     去重+watchdog 回收），P0-P4 只调整"通道之间如何共享资源/仲裁结果如何
     解释"这一层；P5 才涉及把三者收敛到统一入口，且是渐进式适配，不是重写。
  2. 不改变 `ResourceArbiter` 三条既有仲裁规则本身的判定逻辑（用户优先 /
     资源锁 / 预算硬限制），只改变"仲裁结果如何分发给不同通道、通道如何
     响应"。
  3. 不引入新的 LLM 调用——本计划所有新增判断都是规则化计算（计数器、
     阈值比较、状态机），零 LLM 成本，符合项目对调度类改动的一贯要求。
  4. 默认行为变化需要可灰度控制——新增配置项默认值需保证"未升级配置的
     用户行为基本不变或只变得更宽松（cron 更容易跑）"，不能引入默认收紧。
  5. P5 的统一调度层是**长期目标**，本文档只规划到"定义统一接口 + 让现有
     三条通道逐个接入"这一步，不承诺一次性完成迁移，允许分批上线、允许
     长期与旧路径并存直到确认稳定。
- **关联文档**：
  - `goal_execution_fairness_improvement_plan.md`（通道 1 内部公平性，已完成）
  - `cron_dedicated_execution_improvement_plan.md` /
    `cron_dedicated_execution_implementation_record.md`（通道 2 的独立执行
    通道设计）
  - `goal_cron_binding_plan.md`（通道 3 的绑定/触发/回收机制）
  - `daemon_task_hang_recovery_and_watchdog_hardening_plan.md`（`reap_stale_*`
    系列卡死回收，与 P3 的看门狗强化是同一思路的延伸）
  - `daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md`
    （`SchedulerHeartbeat`、`tick()` 持锁时间短的既有设计原则）
  - `system_connectivity_gaps_and_missing_capabilities_plan.md`（`arbiter_
    skipped_count` 等既有可观测性埋点，P4 会复用/扩展这些数据源）

## 1. 现状复盘：不合理之处小结

用一句话概括：**三条通道对同一份资源各自实现了一套局部判断，裁判（`ResourceArbiter`）
是共享的，但"怎么响应裁判结果"、"要不要给裁判反馈自己的消耗"都不统一**，
导致的具体后果：

1. Goal 消耗预算，cron 被连坐限流，但 cron 自己的消耗从不计入预算——责任
   不对等。
2. cron 到点被跳过时只是"下次重试"，没有"已经连续跳过 N 次"这种记账，
   用户很难第一时间发现"某个 cron 长期没跑成功"。
3. `blocked` 是全有或全无的二元开关，没有"至少保留最低限度资源给 cron"
   的中间态，而 cron 恰恰是三条通道里对"时间确定性"要求最高的一个，错位。
4. tick() 不能长时间阻塞这条规则只是注释约定，没有强制检测，容易被未来
   新代码破坏而不被发现（本轮问题排查的直接诱因）。
5. 没有一个统一视图能看到"这一刻三条通道各自的运行/排队/跳过状态"，
   问题定位依赖翻多个接口/文件拼图。

## 2. 分阶段实施计划

### P0 —— cron 分级响应资源仲裁（不再一刀切"blocked 就跳过"）【已完成】

**处理状态：已完成。** 详见
`next_doc/goal_cron_unified_scheduler_implementation_record.md`。

- **目标**：让普通 cron 通道对 `degraded`/`blocked` 的响应方式向 Goal 通道
  看齐——`degraded` 时收紧、不停摆；只有真正 `blocked`（预算硬耗尽 /
  frustration 硬停摆阈值）才跳过。
- **改动**：
  - `CronJobRunner` 增加 `set_gating_degraded(bool)`，语义对齐
    `ObjectiveExecutor.set_gating_degraded()`：`degraded` 时把内部
    `max_concurrent` 临时收紧（例如降到 1），而不是直接拒绝提交。
  - `AutonomousLoop._tick_maintenance()` 里已经在算 `state = arbiter.
    gating_state()["state"]` 并喂给 `ObjectiveExecutor`，顺带也喂给
    `CronJobRunner`（同一个 tick 内两个通道用同一次仲裁结果，不重复计算）。
  - `CronJobRunner.submit()` 里原来的 `if state == "blocked": return False`
    保留（真正硬预算耗尽时 cron 也应该让步），但 `degraded` 分支不再存在
    "整体跳过"这一说，只是并发收紧。
- **验收标准**：
  - `degraded` 状态下，到期的普通 cron job 仍能被触发（并发收紧到 1，不是
    完全不跑）。
  - `blocked` 状态下行为与改造前一致（跳过、下次重试）。
  - 新增测试覆盖：`degraded` 时 cron 正常触发、`blocked` 时仍跳过、状态
    切换时并发上限正确升降档。
- **风险**：`degraded` 更容易触发（比如用户短暂活跃），如果 cron 此时仍
  持续跑，理论上会比改造前更抢用户交互的资源。缓解方式：`degraded` 状态
  下 cron 的收紧幅度可以比 Objective 更激进（比如直接降到 1 而不是跟随
  Objective 的收紧曲线），具体收紧策略留一个独立配置项
  `cron.degraded_max_concurrent`（默认 1）。

### P1 —— cron 消耗统一记账【已完成】

**处理状态：已完成。** 详见
`next_doc/goal_cron_unified_scheduler_implementation_record.md`。

- **目标**：让 cron（含 goal_cycle）跑掉的 token 也计入同一份预算，Goal 和
  cron 对预算负同等责任，`blocked` 状态的产生原因才是可解释、可审计的。
- **改动**：
  - `cron_agent_bridge.py` / `cron_job_executor.py` 每步执行完后，调用
    `ResourceArbiter` 现有的记账入口（参考 `used_today_goals`/
    `used_today_exploration` 的写法），新增 `used_today_cron` 计数器，同时
    仍累加进总的 `rb.used_today`。
  - `gating_state()` 的 `reason` 文案里明确写出这次 `blocked`/`degraded`
    是被哪部分消耗触发的（`used_today_goals` / `used_today_cron` /
    `used_today_exploration` 各自的占比），方便看板展示"是 Goal 太多还是
    cron 太多"。
- **验收标准**：
  - cron 执行后 `rb.used_today_cron` 正确递增，`rb.used_today` 同步递增。
  - `gating_state()` 返回的 reason 里能看到三类消耗的分项数字。
  - 新增测试：cron 单独跑满预算也能触发 `blocked`（验证记账生效，不再是
    只有 Goal 才能把 arbiter 打满）。

### P2 —— cron 跳过追踪与主动告警【已完成】

**处理状态：已完成。** 详见
`next_doc/goal_cron_unified_scheduler_implementation_record.md`。

- **目标**：cron 到点被跳过（无论是 P0 改造前的整体跳过，还是并发满载/
  job 已在跑）不能只是静默重试，需要有"连续跳过次数"的记账和阈值告警。
- **改动**：
  - `CronJob` 新增字段 `consecutive_skip_count`（每次 `_fire()` 返回
    `False` 时 +1，成功触发一次清零）。
  - `CronScheduler.tick()` 里 `consecutive_skip_count` 超过可配置阈值
    （`cron.skip_alert_threshold`，默认 5）时，通过
    `NotificationDispatcher` 发一条告警（"cron job X 已连续 N 次到点未能
    触发，最近一次原因：xxx"），且只在跨越阈值那一刻发一次，不重复刷屏
    （与 `record_gating_transition()` 的"状态变化才写入"是同一节流思路）。
- **验收标准**：
  - 连续跳过达到阈值时准确触发一次通知，之后成功执行一次后计数器清零，
    再次连续跳过会重新从零累积、重新在下次跨越阈值时告警。
  - 看板 cron 面板展示每个 job 的 `consecutive_skip_count`（非零时高亮）。

### P3 —— tick() 执行看门狗（从"暴露观测字段"升级为"主动检测告警"）【已完成】

**处理状态：已完成。** 详见
`next_doc/goal_cron_unified_scheduler_implementation_record.md`。

- **目标**：把"tick() 不能长时间阻塞"从君子协定升级为有主动检测的硬约束，
  避免未来新代码再次悄悄破坏这条设计原则而无人察觉。
- **改动**：
  - `SchedulerHeartbeat` 已有 `last_tick_started_at`/`last_tick_finished_at`
    观测字段，新增一个独立的轻量看门狗线程（或复用心跳线程本身在下一次
    唤醒时顺带检查），判定条件：`now - last_tick_finished_at > 2 *
    tick_interval_seconds` 且 `last_tick_started_at > last_tick_finished_at`
    （即当前正卡在一次未返回的 tick() 里）。
  - 命中时通过 `NotificationDispatcher` 告警，并在 `execution_model_status`
    里标记 `heartbeat_suspected_stuck: true`，供看板展示醒目提示（而不是
    现在这样，只有点开面板细看时间戳才能发现）。
  - 命中告警的同时记录一次结构化日志，包含"当前 tick 已持续多少秒"，
    便于事后定位是哪一类调用违反了不阻塞约束。
- **验收标准**：
  - 模拟一次超长 tick()（测试里用 `time.sleep` 打桩），看门狗能在预期
    时间窗口内检测到并触发一次告警，且不重复刷屏。
  - 正常节奏的 tick() 不会误报。

### P4 —— 统一调度可观测面板【已完成】

**处理状态：后端只读端点 + 看板 UI 展示区块均已完成。** 详见
`next_doc/goal_cron_unified_scheduler_implementation_record.md`。

- **目标**：一个视图能看到三条通道当前的运行/排队/跳过状态，不需要在
  `autonomous_status`、`arbiter_skipped_count`、`gating_history`、cron
  面板之间来回切换拼图。
- **改动**：
  - 新增只读端点 `GET /v1/self/scheduling_overview`，聚合返回：
    - Goal 通道：`objective_slots`（running/max）、当前公平排序队首
      Goal（复用 `goal_fairness` 已有数据）。
    - 普通 cron 通道：`running`/`queued`/`arbiter_skipped_count`/
      `consecutive_skip_count` 超阈值的 job 列表（P2 产出）。
    - goal_cycle 通道：待触发的 Goal 数、最近一次触发结果。
    - 三者共享的 `gating_state()` 当前值 + P1 产出的分项消耗占比。
  - 看板新增一个"🕹️ 统一调度总览"折叠区块，并入现有"🧠 自我状态" tab
    （紧跟在"⚙️ 执行模型"区块之后），不单独开新 tab（信息架构见实施记录
    §关键设计决策）。
- **验收标准**：
  - 端点返回结构包含上述四类信息，任一子系统数据缺失时该字段返回空/占位，
    不影响其它字段正常返回（沿用项目一贯的"非核心信息降级不影响主链路"
    风格）。
  - 看板能正确渲染，新增测试覆盖端点空态/正常态。

### P5（长期目标）—— 收敛到统一调度层【第 1-3 步已完成，第 4 步部分完成，第 5 步已启动（子集完成）】

**处理状态：第 1-3 步已完成；第 4 步"接管实际派发"部分完成**（cron/
goal_cycle 两通道的 `execute()` 已实现真正委托派发，goal 通道
`execute()` 未实现）；**第 5 步"收敛到统一入口"已启动并完成一个子集**
——`AutonomousLoop._tick_passive()` 新增灰度开关
`scheduler.unified_dispatch_enabled`（默认 `False`），开启后 cron/
goal_cycle 两通道改由 `unified_task_scheduler.dispatch_due_cron_jobs()`
统一派发；由于 `_tick_maintenance()`/`_tick_autonomous()` 方法体都以
调用 `_tick_passive()`（`_tick_autonomous()` 是间接经 `_tick_maintenance()`）
开头，这个开关对 **passive/maintenance/autonomous 三个档位同时生效**
（v1.9 订正，见变更记录）；Goal 通道派发路径未涉及。详见
`next_doc/goal_cron_unified_scheduler_implementation_record.md`。

- **目标**：三条通道最终都通过一个统一的 `UnifiedTaskScheduler` 提交任务、
  领取执行槽位，由它统一做：并发分配、优先级/权重排序、资源仲裁响应、
  预算记账。三条通道各自保留"如何产生任务"（`GoalBacklog` 拆 Objective、
  `CronScheduler` 算到期）和"如何真正执行一步"（`ObjectiveExecutor` 的
  step 逻辑、`CronJobExecutor` 的续接逻辑）的领域知识，但"能不能现在跑、
  该给谁优先"这件事收归统一调度层。
- **分步迁移路径（不是一次性重写）**：
  1. **定义统一接口**：`SchedulableTask`（最小字段：`source`
     ∈ {goal/cron/goal_cycle}、`task_id`、`priority`、`due_at`（cron 有，
     goal 可以为 None）、`resource_estimate`）+ `TaskChannel` 协议
     （`poll_due() -> list[SchedulableTask]`、
     `execute(task) -> concurrent, non-blocking`）。【已完成，见实施记录】
  2. **先适配只读部分**：让 `ObjectiveExecutor`/`CronJobRunner` 各自实现
     `TaskChannel.poll_due()`，`UnifiedTaskScheduler` 先只做"聚合展示 +
     统一排序建议"，不接管真正的执行决策（等价于 P4 的数据源升级版，
     风险为零，可以先上线观察排序结果是否符合预期）。【已完成，见实施
     记录——新增只读端点 `GET /v1/self/unified_scheduler_preview`
     供观察排序结果】
  3. **接管仲裁裁决**【已完成，见实施记录】：`UnifiedTaskScheduler` 内部持有权重配置
     （`scheduler.channel_weights = {goal: x, cron: y, goal_cycle: z}` 或
     "cron 保底并发数"这类更直观的配置），根据 P1 统一记账的预算数据 +
     权重，决定"这次调度周期给每条通道分配几个执行槽位"，`ObjectiveExecutor`/
     `CronJobRunner` 改为向它"申请槽位"而不是各自问 `ResourceArbiter`。
     ——**范围说明**：本轮落地的是"degraded 状态下两条通道的并发上限"这
     一具体决策点（新增纯函数 `allocate_weighted_slots()`，`channel_weights`
     用固定值而非 P1 提到的"根据预算数据自适应"，理由见实施记录待确认项
     回应），`blocked`/`full` 两态的判定逻辑（`ResourceArbiter.
     gating_state()` 本身）未改变——仍是"only the degraded concurrency
     split moved into the unified layer"，不是"仲裁的全部决策都收归统一
     层"，后者留给未来视情况评估是否需要。
  4. **接管实际派发**【第 4 步部分完成 + 第 5 步子集完成，见实施记录】：
     三条通道的 `tick()` 触发点最终都收敛成 `UnifiedTaskScheduler.
     tick()` 一个入口，`AutonomousLoop` 里现在调用 `cron_scheduler.
     tick()`/`objective_executor` 相关方法的代码逐步收敛进去。——**第
     4 步完成的子集**：`CronScheduler` 新增公开入口
     `trigger_job_now(job_id)`（与 `tick()` 共用同一份记账逻辑），
     `CronChannelAdapter`/`GoalCycleChannelAdapter.execute()` 已委托它
     实现真正派发；**第 5 步完成的子集**：新增
     `dispatch_due_cron_jobs()` 合并两条通道到期任务、按 priority 统一
     触发，`AutonomousLoop._tick_passive()` 新增
     `scheduler.unified_dispatch_enabled` 灰度开关（默认关闭）可选切换
     到这条路径——因为 `_tick_maintenance()`/`_tick_autonomous()` 都以
     调用 `_tick_passive()` 开头，三个档位共用同一个物理调用点，开关对
     三者同时生效（v1.9 订正了 v1.8 "仅 passive 生效"的错误表述）；
     **尚未完成**：`ObjectiveChannelAdapter.execute()` 未实现（Goal 通道
     派发逻辑深度耦合 `AutonomousLoop` 运行时状态，缺一个安全的公开
     入口），Goal 通道不受本轮任何改动影响。
     再移除旧路径，不强求单个版本内完成全部迁移。
- **验收标准（分步骤各自验收，此处只列总目标）**：
  - 迁移完成后，`gating_state()`、并发分配、优先级排序都只有一份实现，
    不再有三处平行判断可能互相不一致的情况。
  - 新增/修改一条通道的调度策略只需要改 `UnifiedTaskScheduler` 一处，不
    需要同时改三个文件。
  - 现有 `goal_execution_fairness_improvement_plan.md` 的公平轮询/老化
    补偿逻辑作为 `UnifiedTaskScheduler` 内部"Goal 通道的候选排序算法"
    保留，不重新发明。
- **风险与开放问题**：见 §3。

## 3. 明确不做的事（写清楚边界，避免后续误解）

1. 不在 P0-P4 阶段改变 `ObjectiveExecutor`/`CronJobRunner` 各自的并发实现
   细节（信号量 vs 显式计数、线程 vs 异步），这些是"通道内部"的实现选择，
   P5 迁移时才会讨论是否需要统一底层并发原语。
2. 不改变用户可见的 cron/Goal 配置语义（`priority`、`schedule` 表达式等），
   本计划只调整"调度器如何解释和使用这些配置"，不改配置格式本身。
3. P5 不追求"一个 PR 完成"，允许长期分阶段推进，且允许在观察到收益不明显
   时中止后续步骤、只保留已完成部分的收益（P0-P4 本身都是独立可交付、
   有正向价值的改进，不依赖 P5 是否最终完成）。

## 4. 待讨论问题（留空，实施前需要确认）

1. `cron.degraded_max_concurrent`（P0）、`cron.skip_alert_threshold`（P2）
   的默认值是否合理，是否需要按 `sys:`/用户自定义分别设置不同阈值。
2. P1 统一记账后，如果 cron 自己就能把预算耗尽进而触发 `blocked`，是否
   需要给 cron 预算单独设一个上限（类似探索预算的 `exploration_budget_
   ratio`），避免"cron 反过来把 Goal 也连坐限流"这种新的不对称。
3.（已解决，v1.4）P4 的"统一调度总览"信息架构：并入现有"🧠 自我状态"
   tab，作为独立折叠区块（与"🩺 自诊断信号闭环""⚙️ 执行模型"等并列），
   不单独开新 tab——理由见实施记录。
4. `P5` 第 3 步的 `channel_weights` 配置交给用户手工设定，还是像 Goal 公平性
   那样引入"老化补偿"式的自动调整（比如 cron 连续被挤占越久，自动临时
   提高其权重）——建议先上线固定权重版本，积累实际调度数据后再评估是否
   需要自适应机制，避免一开始就引入难以调试的动态反馈系统。（第 1-2 步
   已在 `UnifiedTaskScheduler.suggest_order()` 里预留了同名 `channel_weights`
   参数，但目前只影响"排序建议"这一只读预览，不产生任何实际调度后果，
   第 3 步真正"接管仲裁裁决"时才需要决定这份配置最终落在哪个配置层级。）
5. `UnifiedTaskScheduler` 是否需要感知 `objective_isolated_context_
   enabled`/`heartbeat_owns_tick` 这类现有的灰度开关组合，还是要求先把
   这些开关收敛/固化之后再引入新的统一层，避免开关组合爆炸导致的测试
   覆盖盲区。
6. （P5 第 5 步新增，v1.9 订正）`scheduler.unified_dispatch_enabled`
   开关对 passive/maintenance/autonomous 三个档位同时生效（见 v1.9
   变更记录——此前 v1.8 误以为需要分别接入），因此真正待讨论的问题是
   "何时从默认关闭推进到默认开启"，而不是"何时扩大到其余档位"：建议先
   观察实际运行数据（尤其是 `dispatch_due_cron_jobs()` 多次 `save()`
   带来的 IO 开销、以及排序结果是否与 `tick()` 原有触发顺序有可感知
   差异）积累到一定程度后再评估默认值是否翻转，避免在没有真实数据
   支撑时就贸然改变默认行为。
