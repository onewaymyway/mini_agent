# Goal / Cron 三条执行通道 统一调度层 改进计划 · 实施记录

对应计划文档：`next_doc/goal_cron_unified_scheduler_improvement_plan.md`
（P0、P1、P2、P3、P4 已完成；P5 第 1-4 步已完成/部分完成，第 5 步已启动，
本轮完成灰度开关子集）

## 处理状态

- **P0（cron 分级响应资源仲裁）**：已完成。
- **P1（cron 消耗统一记账）**：已完成。
- **P2（cron 跳过追踪与主动告警）**：已完成。
- **P3（tick() 执行看门狗）**：已完成。
- **P4（统一调度可观测面板）**：已完成（后端只读端点 + 看板 UI 展示区块，
  分两轮完成，见下方 P4 小节及"P4 追加（看板 UI）"小节）。
- **P5（收敛到统一调度层）**：第 1-2 步（定义统一接口 + 三条通道只读
  适配 + 只读聚合排序建议）已完成；第 3 步（接管仲裁裁决 · degraded 并发
  分配子集）已完成；第 4 步（接管实际派发）部分完成——cron/goal_cycle
  两通道 `execute()` 已实现真正委托派发，goal 通道 `execute()` 未实现；
  第 5 步（收敛到统一入口）**本轮完成一个子集**——新增
  `dispatch_due_cron_jobs()` + `scheduler.unified_dispatch_enabled`
  灰度开关，`AutonomousLoop._tick_passive()` 可选切换到经统一调度层
  派发 cron/goal_cycle job，默认关闭；`AutonomousLoop._tick_maintenance()`
  的 Goal 通道派发路径未涉及，仍是未启动部分，见下方"P5 第 5 步"小节。

## 新增文件

| 文件 | 作用 |
|---|---|
| `tests/test_goal_cron_unified_scheduler_p0_p1_p2.py` | P0/P1/P2 共 11 项单测：degraded 并发收紧/full 恢复/两端安全阀、degraded 下 cron job 仍能被触发并跑完、cron token 计入 `used_today_cron`、`gating_state()` 的 blocked 原因带分项数字、`consecutive_skip_count` 递增/清零、跨越阈值告警且不重复刷屏、新增字段不影响既有 `priority` 序列化 |
| `next_doc/goal_cron_unified_scheduler_implementation_record.md` | 本文件 |

## 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/config/models.py` | `CronConfig` 新增 `degraded_max_concurrent: int = 1`（P0）、`skip_alert_threshold: int = 5`（P2） |
| `src/mini_agent/evolution/cron_job_runner.py` | P0：把固定容量的 `threading.Semaphore(max_concurrent)` 换成基于 `threading.Condition` 的可变容量槽位实现（`_acquire_slot`/`_release_slot`），新增 `effective_max_concurrent()`（degraded 时收紧到 `cron.degraded_max_concurrent`，`autonomy.resource_gating_degraded_enabled=False` 时保持 full 容量，"只降不升"）与 `set_gating_degraded(bool)`（纯内存标志位，语义对齐 `ObjectiveExecutor.set_gating_degraded()`）；`_run_job_thread()`/`reap_stale_jobs()` 相应改用新的槽位释放路径。P1：`_run_job_thread()` 里 `executor.run_job()` 返回后，读取本次 job 独占 Agent 的 `stats.input_tokens + output_tokens`（cron 每次触发都重新构造 Agent，累计值即为本次总消耗），非零时调用 `ResourceArbiter.record_autonomous_token_usage(usage_type="cron")`，失败静默降级 |
| `src/mini_agent/evolution/cron_scheduler.py` | P0：新增 `CronScheduler.set_gating_degraded(degraded)`，委托给 `job_runner.set_gating_degraded()`（`job_runner` 未注入时静默跳过）。P2：`CronJob` 新增 `consecutive_skip_count: int = 0` 字段（`to_dict`/`from_dict` 同步更新，缺省 0 保证旧数据兼容）；`tick()` 里 `_fire()` 成功时清零、失败时 `+1` 并调用新增的 `_maybe_alert_consecutive_skip()`；`save()` 触发条件从"仅 triggered 非空"扩展为"triggered 或 skip 计数发生变化"，避免连续跳过时的计数丢失在下次重启后 |
| `src/mini_agent/evolution/resource_arbiter.py` | P1：`record_autonomous_token_usage()` 新增 `usage_type="cron"` 分支（写 `used_today_cron`），三种 usage_type 统一走同一段 `setattr` 逻辑，不再是 `if/else` 各写一遍；新增 `_usage_breakdown_str()`，在 `gating_state()` 判定为预算 `blocked` 时拼进 `reason` 文案，展示 goals/cron/exploration 三类消耗的分项数字 |
| `src/mini_agent/evolution/autonomous_loop.py` | `_tick_maintenance()` 在给 `ObjectiveExecutor.set_gating_degraded()` 喂同一次 `gating_state()` 结果之后，紧接着也喂给 `CronScheduler.set_gating_degraded()`（`cron_scheduler` 未注入时跳过；异常独立 try/except，不影响本次 tick 其余逻辑）——两条通道共用同一次仲裁结果，不重复计算 |
| `src/mini_agent/perception/global_knowledge.py` | **附带修复的既有缺陷**：`ResourceBudget.used_today_goals`/`used_today_exploration` 此前只是 `ResourceArbiter.record_autonomous_token_usage()` 通过 `rb.__dict__[...] = ...` 动态挂上去的属性，从未出现在 `to_dict()`/`from_dict()` 里——每次 `load_self_profile()` 重新反序列化后这两个分项就会丢失，只有汇总的 `used_today` 是真正持久化的。本轮把三个分项（含新增的 `used_today_cron`）提升为正式 dataclass 字段并纳入序列化，缺省值 0 保证旧 `self_profile.json` 反序列化后行为不变；`update_self_profile_on_session_end()` 的跨自然日重置同步清零三个分项，避免"汇总已清零、分项还是昨天的数字"这种展示不一致 |

## 关键设计决策

1. **P0 不改变 semaphore 的"排队而非丢弃"语义，只改变容量本身**：原方案文字是"临时收紧 max_concurrent"，如果只是简单地在 `submit()` 里加一层"degraded 时超过 1 个在跑就拒绝"的判断，会退化成新的一种"整体跳过"，与"不再是'blocked 就一刀切跳过'"的设计初衷矛盾。改为用 `Condition` 实现真正可变容量的槽位：job 到期后总能 `submit()` 成功、线程总会被创建，只是在真正开始执行前排队等待槽位——排队时长随 degraded/full 状态实时变化（每次被唤醒都重新读取当前的 `effective_max_concurrent()`），不会因为提交时刚好是 degraded 状态就永远卡在收紧后的容量上。

2. **P0 的 degraded 开关粒度对齐 Objective 通道，但配置项独立**：`AutonomousLoop._tick_maintenance()` 用同一次 `gating_state()` 结果同时喂给 `ObjectiveExecutor.set_gating_degraded()` 和 `CronScheduler.set_gating_degraded()`，避免两次调用 `ResourceArbiter.gating_state()`（该方法内部会做一次 `record_gating_transition()` 落盘尝试，重复调用没有正确性问题但没必要）。但收紧到多少（`cron.degraded_max_concurrent` vs `autonomy.resource_gating_degraded_max_concurrent`）是两个独立配置项——cron 对时间确定性的要求本身就和 Objective 不同，方案原文也明确建议"cron 的收紧幅度可以比 Objective 更激进"，不强绑定同一个数值。

3. **P1 选择在 job 级别记账，不做 step 级别**：`ObjectiveExecutor`/`exploration_sandbox` 现有的记账粒度分别是"整个 Objective 执行完"和"整个探索沙盒结束"，不是每个 step/每次 LLM 调用单独记一次。cron 沿用同一粒度——`CronJobExecutor.run_job()` 内部可能有多个 step，但只在整个 job 跑完（无论成功/超时/needs_review）后统一读一次 `agent.stats` 的累计值记账一次，理由：1) 与既有两条通道的记账粒度保持一致，不引入新的"记账频率"概念；2) `agent.stats` 本身就是跨 step 累计的，不需要在每个 step 之间做差值计算，实现更简单也更不容易因为中途异常漏记。

4. **P1 顺带修的 `ResourceBudget` 序列化缺陷是必须修的，不是无关的顺手清理**：如果不修，`used_today_cron` 记账和 `used_today_goals`/`used_today_exploration` 一样，每次 `load_self_profile()` 都会打回 0——`gating_state()` 的分项 reason 文案会长期显示错误的数字（除非记账和读取发生在同一次进程内存里、中间没有任何一次重新加载），P1 的验收标准"cron 单独跑满预算也能触发 blocked"在跨进程/跨 tick 的真实场景下也无法成立。这不是新增功能，是让 P1 本身能正确工作的前提。

5. **P2 的告警判定用 `==` 而不是 `>=`**：`consecutive_skip_count == threshold` 保证每一"轮"连续跳过只在恰好跨越阈值那一刻触发一次通知，后续继续跳过不会重复刷屏；某次成功触发清零后，如果又开始连续跳过，会在重新累积到阈值时再次告警——与 `record_gating_transition()`"状态变化才写入"是同一节流思路，行文里也是同一处引用。

6. **P2 的告警读取配置走 `job_runner._base_cfg`，不是给 `CronScheduler` 新增 `AppConfig` 构造参数**：读码确认 `CronScheduler.__init__()` 现有构造参数只有 `paths`/`submit_fn`/`digest_advisor_cfg`/`job_runner`，没有 `AppConfig`。新增一个全局配置构造参数会牵动所有既有调用点（CLI/API/测试里直接 `CronScheduler(paths, ...)` 的写法），改动面明显超出 P2 本身的范围。`job_runner`（`CronJobRunner` 实例）本身已经持有 `_base_cfg`，且 P2 的告警阈值 `cron.skip_alert_threshold` 和 P0 的 `cron.degraded_max_concurrent` 语义上都是"cron 通道全局配置"，复用同一份已经存在的引用是成本最低的路径；`job_runner` 未注入（旧路径，没有独立执行通道）时退回默认阈值 5，不阻塞旧路径的正常使用。

## 测试结果

```
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py ...........   11 passed
```

回归测试（cron/仲裁/goal_cron/autonomous_loop/objective_executor/resource_budget/
self_profile/global_knowledge/exploration 相关全部既有测试文件）：

```
317 passed, 0 failed
```

无回归。（`tests/test_session.py`、`tests/test_system_connectivity_routes.py` 等
少数文件在本次改动之前就存在与本计划无关的环境依赖问题——如
`ImportError: cannot import name '_flock' from 'mini_agent.session'`、
`ModuleNotFoundError: No module named 'fastapi'`——已确认与本次改动无关，
未在本轮范围内修复。）

## 待确认项回应（对照原方案 §4）

1. `cron.degraded_max_concurrent`（默认 1）、`cron.skip_alert_threshold`
   （默认 5）：本轮先按方案原文给出的默认值上线，暂不做按 `sys:`/用户自定义
   分别设置不同阈值——两个都是全局配置项，`sys:` 前缀 job 本身不经过
   `submit()` 的仲裁检查（沿用既有设计），也不参与 `consecutive_skip_count`
   语义上的特殊化，行为已经是合理的默认状态，观察实际使用数据后再评估是否
   需要拆分。
2. cron 预算是否需要单独设上限（类似探索预算的 `exploration_budget_ratio`）：
   本轮未实现，`used_today_cron` 目前只是"记账 + 展示分项"，仍然共用同一份
   `daily_token_budget` 总预算，没有单独的 cron 预算上限。列为观察项：如果
   后续发现"cron 反过来把 Goal 也连坐限流"的新不对称，再补一个类似
   `exploration_budget_ratio` 的独立比例配置。
3. P4 统一调度总览的信息架构：P4 本身未开始，暂不适用。
4. P5 的 `channel_weights` 是否自适应：P5 未启动，暂不适用。
5. `UnifiedTaskScheduler` 是否需要感知现有灰度开关组合：P5 未启动，暂不适用。

## 新增文件（P3）

| 文件 | 作用 |
|---|---|
| `tests/test_goal_cron_unified_scheduler_p3.py` | P3 共 4 项单测：正常节奏 tick 不误报 `suspected_stuck`、模拟一次超长 tick（`threading.Event` 打桩阻塞）能在预期时间窗口内检测到卡死且只告警一次、`paths` 未注入时 `suspected_stuck` 仍正确置位但静默降级不发通知、`set_tick_interval_seconds()` 能实时更新看门狗判定阈值 |

## 修改文件（P3）

| 文件 | 改动 |
|---|---|
| `src/mini_agent/evolution/scheduler_heartbeat.py` | 新增独立看门狗线程：`start()` 重写为额外拉起一条 `_watchdog_run()` 线程（`stop()` 同步置位新增的 `_watchdog_stop_evt`），按不超过 2 秒的轮询间隔调用 `_check_stuck()`。判定条件：`last_tick_started_at > last_tick_finished_at`（当前确实卡在某次未返回的 tick 里，排除"从未 tick 过"/"tick 过但已正常结束"两种不该误报的情况）且 `time.time() - last_tick_started_at > tick_interval_seconds * stuck_threshold_multiplier`（默认 2 倍）。命中时置位 `suspected_stuck`（新增只读 property）并通过 `NotificationDispatcher` 告警一次（`_alert_stuck()`，`paths` 未注入时静默跳过，失败走 `log_exception` 降级）；`finally` 分支里 `last_tick_finished_at` 被刷新后自动复位 `suspected_stuck`/`_stuck_alert_sent`，允许下一次卡住重新告警一次。新增 `set_tick_interval_seconds()` 允许运行期间刷新判定用的基准值。构造参数新增 `tick_interval_seconds`（默认 60.0）、`paths`（默认 None）、`stuck_threshold_multiplier`（默认 2.0） |
| `src/mini_agent/api/server.py` | 构造 `SchedulerHeartbeat` 时新增传入 `tick_interval_seconds`（读 `autonomous_loop.get_digest_status()` 里的值，失败退回 60.0）和 `paths`（现场 `AgentPaths(agent.cfg.project_root)` 构造，与本文件其它位置"paths 从不缓存"的一贯风格一致，构造失败时保持 `None`，看门狗静默降级） |
| `src/mini_agent/api/routes.py` | `GET /v1/self/execution_model_status` 的 `scheduler_heartbeat` 字段新增 `suspected_stuck: bool`；端点内顺带调用 `heartbeat.set_tick_interval_seconds(tick_interval_seconds)` 刷新看门狗阈值（该端点已有的 `tick_interval_seconds` 计算逻辑复用，不重复计算；失败不影响其它字段返回） |

## 关键设计决策（P3）

7. **看门狗必须是独立线程，不能放进原有的 tick 循环线程里"顺带检查"**：
   如果只是在 `run()` 主循环里"tick 完之后调用 `_check_stuck()`"，一旦
   真的卡在某次 `tick()` 里，主循环本身会阻塞在那一行代码上出不来，
   `_check_stuck()` 永远没有机会被执行——这正是本阶段要解决的故障场景
   本身，用一个会被同样卡住的东西去检测"是否卡住"是自相矛盾的。因此
   拆成两条线程：原有的 `run()` 只负责 tick 触发；新增 `_watchdog_run()`
   完全独立运行，只依赖 `_stats_lock` 保护的几个时间戳字段，不依赖
   `self._lock`（与 AgentRunner 共享的业务锁）也不依赖 tick 循环本身
   是否还在正常运转。本轮实现之初曾把检查逻辑放在 `run()` 循环内，
   写单测模拟"tick() 内部用 `threading.Event.wait()` 卡住"时立即复现了
   "watchdog 永远检测不到"的问题，验证了拆分是必须的，不是过度设计。

8. **判定阈值用"当前 tick 已经跑了多久"（`now - last_tick_started_at`），
   不是文档原文字面的"`now - last_tick_finished_at`"**：如果直接照抄
   `now - last_tick_finished_at > 2 * tick_interval_seconds`，在"进程
   刚启动、第一次 tick 就卡住、`last_tick_finished_at` 还停留在初始值
   `0.0`"这种场景下，`now - 0.0` 会立刻算出一个"距 Unix 纪元"的巨大差值，
   还没真正卡住多久就会被误判为卡死。改为衡量"本次 tick 已经持续了
   多久"，语义与文档描述的"当前正卡在一次未返回的 tick() 里"完全一致，
   且不受 `last_tick_finished_at` 初始值的影响；`last_tick_started_at >
   last_tick_finished_at` 这个前提条件仍然保留，用来排除"根本没有正在
   进行的 tick"的情况。

9. **告警节流沿用 P2 的"跨越阈值那一刻只告警一次"思路，但状态机更简单**：
   P2 是计数器"恰好等于阈值"时触发；P3 没有一个天然递增的计数器，改用
   一个布尔标志 `_stuck_alert_sent`——检测到卡死且尚未告警过（本次"卡住
   事件"内）时告警并置位，之后同一次卡住事件内不再重复；`finally` 分支
   刷新 `last_tick_finished_at` 后下一次 `_check_stuck()` 会发现"已经不
   再处于卡住状态"，同步复位 `suspected_stuck`/`_stuck_alert_sent`，为
   下一次可能的卡住事件重新做好告警准备。

10. **看门狗自身的轮询间隔独立于 `interval_seconds`（tick 触发轮询间隔），
    取 `min(interval_seconds, 2.0)`**：如果用户把 `scheduler_heartbeat_
    poll_interval_seconds` 配置得很长（比如 30 秒），看门狗的反应速度不
    应该跟着变慢到"最多 30 秒才检查一次"——卡死检测本身是独立的可观测性
    功能，反应速度应该有一个不依赖用户配置的合理上限；测试环境里传入
    很小的 `interval_seconds`（如 0.05）时看门狗也会相应更频繁，不受这个
    2 秒上限影响（`min` 取两者中较小值）。

## 测试结果（P3）

```
tests/test_goal_cron_unified_scheduler_p3.py ....   4 passed
```

与既有 `tests/test_scheduler_heartbeat.py`（阶段二观测字段测试）联合运行：

```
tests/test_goal_cron_unified_scheduler_p3.py tests/test_scheduler_heartbeat.py
12 passed
```

与 P0/P1/P2 的既有测试文件联合运行，确认无回归：

```
tests/test_goal_cron_unified_scheduler_p3.py
tests/test_scheduler_heartbeat.py
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py
23 passed
```

范围回归（cron/仲裁/goal_cron/autonomous_loop/objective_executor/
resource_budget/self_profile/global_knowledge/exploration/scheduler
相关测试文件，补装本地环境缺失的 `pydantic`/`rich`/`fastapi`/`httpx`
后运行）：282 passed；另有少数失败/报错文件（`test_global_knowledge_
integration.py`、`test_objective_executor_kanban_tracks_r4.py` 等）经
排查确认是本地沙箱环境缺少 `uvicorn` 等依赖导致的既有收集错误，与本轮
改动无关，不在本次范围内修复（与 P0-P2 实施记录里"少数文件在本次改动
之前就存在环境依赖问题"的结论一致）。

## 待确认项回应（对照原方案 §4，追加 P3 相关项）

6.（新增，对应原文档 §4 未明确编号但与 P3 相关的隐含问题）看门狗告警的
   `stuck_threshold_multiplier`（默认 2.0）是否需要做成独立配置项：本轮
   先以构造参数形式存在（`SchedulerHeartbeat.__init__` 的
   `stuck_threshold_multiplier`），未接入 `AppConfig`/`autonomy.*`
   配置体系——`api/server.py` 构造时未显式传入，使用默认值 2.0，与方案
   原文"`now - last_tick_finished_at > 2 * tick_interval_seconds`"的建议
   完全一致。若后续需要用户可调，可以比照 `cron.degraded_max_concurrent`
   /`cron.skip_alert_threshold` 的模式补一个 `autonomy.scheduler_
   heartbeat_stuck_threshold_multiplier` 配置项，本轮判断"先用固定默认值
   观察实际数据"是更稳妥的路径，不引入还没有使用数据支撑的可调参数。

## 新增文件（P4）

| 文件 | 作用 |
|---|---|
| `tests/test_scheduling_overview_route.py` | P4 共 5 项单测：空状态返回合理默认值、`gating`/`usage_breakdown` 在 paths 可解析时正确返回、Goal 通道正确报告 `objective_slots` 与公平队首 Goal（`last_scheduled_at` 更早的排最前）、cron 通道正确列出 `consecutive_skip_count` 达到阈值的 job、goal_cycle 通道与普通 cron 通道正确分流（不互相污染 running/queued 计数） |

## 修改文件（P4）

| 文件 | 改动 |
|---|---|
| `src/mini_agent/api/routes.py` | 新增只读端点 `GET /v1/self/scheduling_overview`：聚合返回 `gating`（`ResourceArbiter.gating_state()` 当前值）、`usage_breakdown`（P1 产出的三类消耗分项 + `daily_token_budget`，直接读 `ensure_self_profile(paths).resource_budget`，不再依赖只出现在 `reason` 文案里的字符串）、`goal_channel`（`objective_slots` 复用 `autonomous_status` 端点已有的 running/max/static_cap 计算逻辑；`queue_head_goal` 取 `GoalBacklog.active_goals()` 中 `last_scheduled_at` 最小的一个，即公平轮询下一个最应该被调度的 Goal）、`cron_channel`（遍历 `CronScheduler.list_jobs()`，按 `run_mode != "goal_cycle"` 过滤后用既有的 `execution_phase()` 分类 running/queued，`jobs_over_skip_threshold` 复用 P2 已经在维护的 `consecutive_skip_count` 字段和 `cron.skip_alert_threshold` 配置，达到阈值即视为需要关注）、`goal_cycle_channel`（同一份 `list_jobs()` 结果按 `run_mode == "goal_cycle"` 过滤，`pending_due_count` 统计 `enabled and next_run_at <= now` 的数量，`recent` 按 `last_run_at` 倒序取最近 5 条，`consecutive_skip_count` 复用同一字段间接反映"最近一次触发是否成功"）。任一子系统缺失（`autonomous_loop`/`_cron_scheduler`/`_objective_executor` 为 None）时对应字段保持初始占位值，不影响其它字段正常返回；顺带修复了本文件里一处既有的 `_objective_executor` 兜底逻辑 bug（`if oe is None and al is not None` 在 `al is None` 时会跳过对 `http_server.bridge._objective_executor` 的兜底查找，导致明明挂在 bridge 上的 executor 永远读不到；改为与文件内其它 7 处同类兜底一致的 `if oe is None:` 无条件兜底） |

## 关键设计决策（P4）

11. **`usage_breakdown` 不解析 `gating_state()["reason"]` 里的分项文案，
    直接读 `ResourceBudget` 字段**：P1 把三类消耗数字拼进了 `reason`
    字符串（供人读的告警/日志场景），但 P4 是一个结构化 API，让前端去
    解析一段中文拼接字符串取数字是脆弱的（文案后续任何措辞调整都会
    静默破坏解析）。`ResourceBudget` 从 P1 起本身就是持久化的正式
    dataclass 字段（P1 顺带修复了序列化缺陷），直接读取是更稳的路径，
    与 `reason` 文案是两条独立但数值一致的展示通道。

12. **`goal_cycle_channel` 的"最近一次触发结果"不新增状态字段，复用
    `consecutive_skip_count`**：方案原文只写"最近一次触发结果"，没有
    规定具体形式。`CronJob` 目前没有一个"上次是否成功"的布尔字段（P2
    只在跳过时递增计数，成功时清零），新增一个专门字段属于改变
    `CronJob` 数据结构、需要考虑 `to_dict`/`from_dict` 兼容性的改动，
    超出 P4"只读聚合展示"的范围。`consecutive_skip_count == 0` 已经
    隐含"上次触发成功"，`> 0` 隐含"最近处于连续失败中"，配合
    `last_run_at`/`run_count` 已经能让看板判断"这个 goal_cycle 最近
    健康与否"，不需要额外持久化状态。

13. **`cron_channel`/`goal_cycle_channel` 共用同一次 `list_jobs()` 调用，
    只遍历一次**：而不是分别为两个通道各查一次 job 列表——`run_mode`
    字段已经足够区分两类 job，没有必要发起两次等价的 IO（`list_jobs()`
    内部会读一次 `cron_jobs.json`）。这与 P0 决策 2"避免重复调用
    `gating_state()`"是同一节流思路的延伸。

14. **顺带修复的 `_objective_executor` 兜底 bug 必须修，不是无关的
    顺手清理**：写 P4 端点时复用了 `autonomous_status` 端点里
    `oe = getattr(al, "_objective_executor", None) if al is not None
    else None` 这一行的既有写法，但紧接着的兜底判断在原文件里错误地
    多加了一个 `and al is not None` 条件——当 `autonomous_loop` 未注入
    （`al is None`，例如某些精简部署或测试场景）但 `ObjectiveExecutor`
    仍然正常挂在 `http_server.bridge._objective_executor` 上时，这个多
    余的条件会导致兜底查找被跳过，`objective_slots` 永远返回 `None`。
    对照文件内其它 7 处同类兜底代码（均为无条件 `if oe is None:`），
    确认这是原 `autonomous_status` 端点的既有缺陷，本轮顺带修复，不
    改变其余字段的既有行为。

## 测试结果（P4）

```
tests/test_scheduling_overview_route.py .....   5 passed
```

与 P0-P3 的既有测试文件联合运行，确认无回归：

```
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py
tests/test_goal_cron_unified_scheduler_p3.py
tests/test_scheduler_heartbeat.py
tests/test_scheduling_overview_route.py
28 passed
```

扩大范围回归（cron/仲裁/goal_cron/autonomous_loop/objective_executor/
resource_budget/self_profile/global_knowledge/exploration/scheduler/
goal_fairness/execution_model/scheduling_overview 相关测试文件，本轮
补装了本地沙箱环境缺失的 `pydantic`/`rich`/`fastapi`/`httpx`/`uvicorn`/
`python-multipart` 依赖后运行，此前 P0-P3 记录里因为这些依赖缺失而报
"环境问题、与改动无关"的用例本轮已经验证全部正常通过，不再是未确认
状态）：

```
320 passed, 0 failed
```

无回归。仍有 2 个文件在收集阶段报错（`test_judge_verdict.py` 缺
`json_repair`、`test_session.py` 里 `ImportError: cannot import name
'_flock' from 'mini_agent.session'`），确认与本轮改动无关（`_flock` 是
`mini_agent/session.py` 在非 POSIX 或缺少 `fcntl` 环境下的既有平台
兼容问题，`json_repair` 是另一个未安装的第三方依赖），未在本次范围内
修复。

## 待确认项回应（对照原方案 §4，追加 P4 相关项）

7.（对应原方案 §4 第 3 条，v1.4 已解决）P4 的"统一调度总览"信息架构：
   见下方"P4 追加（看板 UI）"小节，本轮已完成并入"🧠 自我状态" tab 的
   展示。

其余待确认项（1-6）沿用 P0-P3 实施记录的既有回应，状态不变。

## P4 追加（看板 UI）—— 补齐"🕹️ 统一调度总览"展示区块

**处理状态：已完成。** 上一轮 P4 只交付了后端聚合端点，本轮补上看板侧
可视化，使 P4 前后端均完整落地。

### 新增/修改文件（P4 追加）

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_kanban/client.py` | 新增 `AgentClient.scheduling_overview()`，包装 `GET /v1/self/scheduling_overview`，写法与既有 `self_diagnosis_feedback()`/`execution_model_status()` 等只读聚合接口的包装方式保持一致 |
| `apps/mini_agent_kanban/app.py` | 新增 `_render_scheduling_overview(client)`：依次展示当前仲裁状态（`full`/`degraded`/`blocked` 三态高亮）+ 三类消耗分项进度条、Goal 通道并发槽位与公平队首、普通 cron 通道运行/排队计数与连续跳过超阈值 job 列表、goal_cycle 通道待触发数与最近触发记录（用 `consecutive_skip_count` 是否为 0 间接展示"最近是否健康"，与后端 P4 决策 12 的字段复用思路一致）。`render_self_tab()` 里在既有 `_render_execution_model_status(client)` 之后追加调用，与"🩺 自诊断信号闭环""⚖️ 执行公平性""🔗 系统关联性""⚙️ 执行模型"等区块并列，同属"🧠 自我状态" tab 内的折叠/分区展示，不单独开新 tab |

### 关键设计决策（P4 追加）

15. **并入"🧠 自我状态" tab，不单独开新 tab**：方案原文 §4 第 3 条把
    "独立新 tab 还是并入'🩺 自诊断信号闭环'"作为待讨论问题留白。复核
    看板既有信息架构后确认：`render_self_tab()` 本身已经是"自我状态"
    这一主题下若干只读观测区块的聚合容器（自主循环摘要、活跃目标、
    自诊断信号闭环、执行公平性、系统关联性、执行模型），统一调度总览
    在语义上与这些区块同级（都是"当前系统内部状态的只读快照"），且
    与"⚙️ 执行模型"区块存在明显的数据关联（两者都读取
    `ResourceArbiter`/`gating_state()` 相关信息），紧跟其后展示便于
    对照阅读；不并入"🩺 自诊断信号闭环"区块本身，是因为后者关注的是
    "改进信号闭环是否见效"这一更长周期的问题，与"此刻三条通道各自在
    干什么"的实时调度快照主题不同，混在同一区块标题下会让区块职责
    模糊。因此选择"新增区块，挂在既有 tab 下，不新增顶层 tab"这一
    最小改动路径。

16. **仲裁状态展示为文字高亮而非独立图表**：三态（`full`/`degraded`/
    `blocked`）用 emoji + 文字标签展示（复用与"⚙️ 执行模型"区块一致的
    视觉语言），预算消耗用 `st.progress` 单条进度条 + 三个分项
    `st.metric`，不引入新的图表库依赖或自定义可视化组件——统一调度
    总览首要目标是"信息聚合、少翻页"，不是"新增炫酷图表"，与看板
    其它只读观测区块的一贯呈现风格保持一致，降低维护成本。

17. **`jobs_over_skip_threshold`/`recent` 列表不做分页或截断交互**：
    后端本身已经把 `recent` 限制在最近 5 条（P4 后端决策），
    `jobs_over_skip_threshold` 命中的场景本身应该是少数（否则更应该
    优先处理背后的资源仲裁问题，而不是在看板上翻页浏览），因此看板侧
    直接展示完整列表，不额外做分页组件，避免为一个理论上应该保持
    "很短"的列表引入不必要的交互复杂度。

### 验证

```
python3 -m py_compile apps/mini_agent_kanban/app.py apps/mini_agent_kanban/client.py   # 通过
python3 -m pytest tests/test_scheduling_overview_route.py -q   # 5 passed（后端端点无回归）
```

看板前端本身沿用项目一贯做法不做自动化 UI 测试（Streamlit 渲染逻辑无
既有测试基础设施覆盖，与其它 `_render_*` 区块的验证方式一致，人工过一遍
`streamlit run apps/mini_agent_kanban/app.py` 确认渲染即可）。

## P5 第 1-2 步 —— 定义统一接口 + 三条通道只读适配 + 只读聚合排序建议

**处理状态：已完成。** 对应改进计划 P5 分步迁移路径的第 1、2 步。**本轮
不接管任何实际执行决策**——三条通道现有的触发路径
（`AutonomousLoop._tick_maintenance()` 直接调 `ObjectiveExecutor`/
`CronScheduler.tick()`）完全不变，新增内容全部是"从外部只读观察"这一层，
调用多少次都不会改变任何通道的实际运行结果。第 3-5 步（接管仲裁裁决、
接管实际派发）仍是未启动的长期目标。

### 新增文件（P5 第 1-2 步）

| 文件 | 作用 |
|---|---|
| `src/mini_agent/evolution/unified_task_scheduler.py` | 新模块：`SchedulableTask` dataclass（`source`/`task_id`/`title`/`priority`/`due_at`/`resource_estimate`/`extra`）+ `TaskChannel` `Protocol`（`poll_due()`/`execute()`）+ 三个只读适配器 `ObjectiveChannelAdapter`/`CronChannelAdapter`/`GoalCycleChannelAdapter` + 聚合层 `UnifiedTaskScheduler`（`register_channel()`/`poll_all()`/`suggest_order()`）+ 便捷构造函数 `build_default_scheduler()` |
| `tests/test_unified_task_scheduler.py` | 15 项单测：`SchedulableTask` 默认字段、三个适配器的 `poll_due()` 正确性（含"只读、不修改底层状态"的显式验证）与 `execute()` 均按设计 raise `NotImplementedError`、`UnifiedTaskScheduler.poll_all()` 按通道分组/单通道异常降级为空列表不影响其它通道、`suggest_order()` 按加权优先级降序+`due_at`升序排序、`channel_weights` 生效验证、`build_default_scheduler()` 全依赖为 `None` 时三通道均正常降级 |
| `tests/test_unified_scheduler_preview_route.py` | 4 项单测：新增只读端点 `GET /v1/self/unified_scheduler_preview` 的空状态、Goal 通道数据正确性、cron/goal_cycle 两通道正确分流、`suggested_order` 跨通道合并且按优先级排序正确（cron job 显式给高 priority 验证确实排到 Goal 前面） |

### 修改文件（P5 第 1-2 步）

| 文件 | 改动 |
|---|---|
| `src/mini_agent/api/routes.py` | 新增只读端点 `GET /v1/self/unified_scheduler_preview`：构造 `UnifiedTaskScheduler`（复用与 `scheduling_overview` 端点相同的 `paths`/`goal_backlog`/`cron_scheduler` 解析逻辑，`goal_backlog` 改用既有的 `load_goal_backlog(paths)` 辅助函数，与文件内其它 goal_backlog 相关端点写法一致），返回 `channels`（三条通道各自 `poll_due()` 原始快照）+ `suggested_order`（跨通道合并排序结果）。任一环节失败均走 `log_exception` 静默降级，返回空占位结构，不影响其它字段/不 500。同步在文件头部的路由列表注释里补充了这条新端点的说明 |

### 关键设计决策（P5 第 1-2 步）

18. **`execute()` 本轮统一 `raise NotImplementedError`，不做"看起来能跑但
    从未被调用"的占位实现**：方案原文 `TaskChannel` 协议要求
    `execute(task) -> concurrent, non-blocking`，但第 1-2 步的验收标准
    明确是"不接管真正的执行决策"。如果现在就把 `execute()` 接到
    `ObjectiveExecutor.start()`/`CronScheduler` 的真实触发逻辑上，即使
    `UnifiedTaskScheduler` 本身不调用它，也会造成两个问题：一是这段代码
    路径没有真实调用方，长期处于"未经验证但看起来可用"的状态，属于
    测试盲区；二是一旦后续有人（哪怕是误用）调用了它，就会绕过三条通道
    各自现有的去重/并发/公平性检查，造成重复执行。显式 `raise
    NotImplementedError` 并在异常信息里写清楚原因，是更安全的"占位"
    方式，等到第 3 步真正需要接管派发时再实现，且实现时会天然获得一次
    强制的设计复核机会（决定要不要更细粒度的 execute 语义）。

19. **`suggest_order()` 的默认排序键选择"`weight * priority` 降序，
    `due_at` 升序 tie-break"，不是更复杂的加权综合评分**：cron 的
    `priority`（`CronJob.priority`，用户可配置整数）与 Goal 的
    `effective_priority`（`priority + aging_boost`，量纲不同但都是"数值
    越大越该优先"）在缺乏实际调度数据支撑的情况下，任何"综合评分公式"
    都是没有依据的猜测。选择最朴素的排序规则（先比 `weight * priority`，
    平手再比 `due_at`）是为了让"排序建议"本身足够透明、容易解释——这正是
    第 2 步"先上线观察排序结果是否符合预期"的前提：如果排序逻辑本身
    复杂到难以解释，观察阶段就失去了意义。`channel_weights` 参数预留了
    未来调整相对权重的空间，但默认全 1.0，不引入任何隐含偏向。

20. **`ObjectiveChannelAdapter` 复用 `GoalBacklog.active_objectives_
    fair_ranked()`，没有使用改进计划原文字面提到的"
    `active_objectives_fair_round_robin`" 方法名**：读码确认
    `GoalBacklog` 现有的公平轮询方法实际命名为
    `active_objectives_fair_ranked()`（返回 `list[GoalNode]`，不是
    `(node, priority)` 元组），改进计划原文的方法名是背景性描述、不是
    强制要求实现的确切签名。适配器内部用同一份 `compute_aging_boost()`
    重新计算 `effective_priority` 填进 `SchedulableTask.priority`，排序
    口径与 `active_objectives_fair_ranked()` 内部使用的完全一致，只是
    多暴露了这个数值供跨通道比较——这与改进计划 P5 验收标准第 3 条"现有
    公平轮询/老化补偿逻辑作为 `UnifiedTaskScheduler` 内部候选排序算法
    保留，不重新发明"的精神一致，只是绑定到了代码里真实存在的方法名。

21. **新增端点与既有 `scheduling_overview`（P4）刻意保持数据源解析逻辑
    一致，但不合并成一个端点**：两者都需要 `paths`/`cron_scheduler`，
    P5 端点额外需要 `goal_backlog`（P4 端点不需要，它读的是
    `ObjectiveExecutor`/`CronScheduler` 的运行时计数，不直接读
    `GoalBacklog`）。没有把两个端点合并，是因为语义不同：P4 是"聚合
    计数"（运行/排队/跳过），P5 预览端点是"如果现在要排序，建议是什么"
    ——后续 P5 第 3 步真正接管调度后，`unified_scheduler_preview` 的
    `suggested_order` 字段语义会发生实质变化（从"建议"变成"实际生效的
    分配依据"），而 `scheduling_overview` 的字段语义不会变，混在一个
    端点里会让未来的版本演进更难解释清楚哪部分是纯观测、哪部分会随 P5
    推进而改变含义。

### 测试结果（P5 第 1-2 步）

```
tests/test_unified_task_scheduler.py               15 passed
tests/test_unified_scheduler_preview_route.py        4 passed
```

与 P0-P4 既有测试文件联合运行，确认无回归：

```
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py
tests/test_goal_cron_unified_scheduler_p3.py
tests/test_scheduling_overview_route.py
tests/test_unified_task_scheduler.py
tests/test_unified_scheduler_preview_route.py
39 passed, 0 failed
```

### 待确认项回应（对照原方案 §4，追加 P5 第 1-2 步相关项）

8.（对应原方案 §4 第 4 条）`channel_weights` 是否自适应：本轮
   `UnifiedTaskScheduler.suggest_order()` 已预留同名参数，默认全 1.0
   （不自适应，也不偏向任何通道）。该参数目前只影响"排序建议"这一只读
   预览的展示结果，不产生任何实际调度后果，是否需要自适应机制留到 P5
   第 3 步真正"接管仲裁裁决"、有实际调度数据积累后再评估，与原方案
   建议的路径一致。
9.（对应原方案 §4 第 5 条）`UnifiedTaskScheduler` 是否需要感知
   `objective_isolated_context_enabled`/`heartbeat_owns_tick` 等现有
   灰度开关组合：本轮的三个只读适配器都只读取"当前有哪些任务到期/可跑"
   这类与执行模式无关的数据（`GoalBacklog`/`CronScheduler.list_jobs()`），
   不涉及 Objective 具体如何执行（持久 Worker/隔离 Runner/共享队列），
   因此第 1-2 步不需要感知这些开关。第 3 步"接管仲裁裁决"如果需要
   `objective_slots`/`effective_max_concurrent()` 这类会受执行模式影响
   的数据，才需要重新评估——留到那时再判断，不在本轮提前引入。

其余待确认项（1-7）沿用 P0-P4 实施记录的既有回应，状态不变。

## P5 第 3 步 —— 接管仲裁裁决

**处理状态：已完成。** 对应改进计划 P5 分步迁移路径的第 3 步，范围是
"degraded 状态下 goal 通道与 cron 通道的并发上限，改由统一裁决计算"——
`blocked`/`full` 两态判定逻辑（`ResourceArbiter.gating_state()` 本身）
不变，goal_cycle 通道仍复用 goal 通道的执行池（不单独参与槽位分配），
第 4-5 步（接管实际派发）仍未启动。

### 新增/修改文件（P5 第 3 步）

| 文件 | 改动 |
|---|---|
| `src/mini_agent/evolution/unified_task_scheduler.py` | 新增纯函数 `allocate_weighted_slots(total_slots, weights, reserved_min=None) -> dict[str, int]`：先满足 `reserved_min` 声明的保底槽位，剩余槽位按权重用最大余数法（largest remainder）比例分配，保证 `sum(allocation.values()) == total_slots`；`total_slots<=0`/`weights` 为空/权重全为 0 均有明确降级行为 |
| `src/mini_agent/config/models.py` | 新增 `SchedulerConfig` dataclass（`unified_arbitration_enabled: bool = False`、`degraded_total_slots: int = 2`、`channel_weights: dict` 默认 `{goal:1.0, cron:1.0, goal_cycle:1.0}`），挂载到 `AppConfig.scheduler`；`CronConfig` 新增 `reserved_min_concurrent: int = 1` |
| `src/mini_agent/evolution/objective_executor.py` | `effective_max_concurrent()` 的 Track J 分支：`scheduler.unified_arbitration_enabled=True` 时，`degraded_cap` 改由 `allocate_weighted_slots(degraded_total_slots, {"goal": w_goal, "cron": w_cron}, reserved_min={"cron": reserved_min_cron})` 计算的 `allocation["goal"]` 覆盖，失败/未启用均回退到原有的 `resource_gating_degraded_max_concurrent` 固定值 |
| `src/mini_agent/evolution/cron_job_runner.py` | `effective_max_concurrent()` 对称改动：同一次 `allocate_weighted_slots()` 调用取 `allocation["cron"]`（与 `ObjectiveExecutor` 各自独立调用，但传入相同的配置源，计算结果天然一致——两处不共享调用是因为两个类之间本来就没有直接引用关系，重复一次纯函数调用的成本可忽略） |
| `src/mini_agent/api/routes.py` | `GET /v1/self/unified_scheduler_preview` 新增 `slot_allocation` 字段：展示"如果当前是 degraded 状态，会分配到的槽位"，读取当前配置直接计算，与 `unified_arbitration_enabled` 是否真正打开无关（开关关闭时也能看到"如果打开会怎样"），失败时静默降级为占位结构 |
| `tests/test_unified_task_scheduler.py` | 新增 `TestAllocateWeightedSlots`：7 项用例覆盖默认配置复现改造前行为、保底优先、保底总和超出总槽位的降级、零/负槽位、按权重的最大余数分配、缺失权重仍保底、空输入 |
| `tests/test_unified_arbitration_p5_step3.py` | 新增：6 项用例覆盖开关默认关闭时两条通道行为不变、开关开启且默认权重时复现改造前的 1:1 分配、goal 权重更高时分到更多槽位但 cron 仍保底、`full` 态不受影响、构造并发上限仍是"只降不升"的最终安全阀（统一裁决算出的份额不能突破它） |

### 关键设计决策（P5 第 3 步）

22. **本轮只接管"degraded 状态的并发上限"这一个决策点，不是"仲裁的
    全部结果"**：改进计划原文第 3 步描述比较宽泛（"决定这次调度周期给
    每条通道分配几个执行槽位"），但 `blocked`/`full` 两态在改造前就是
    "全体一致响应"（`blocked` 两条通道都停/跳过，`full` 两条通道都不
    收紧），本来就没有"各通道独立判断、互相不感知"的不一致问题——P0-P1
    已经解决了这部分（cron 对 `blocked`/`full` 的响应方式已经与 goal
    通道对齐）。真正存在"两条通道各自硬编码一个固定数字、互相不感知
    对方权重"这个不对称问题的，只有 `degraded` 这一态的并发上限。把
    范围收紧到这一个具体决策点，是"渐进式适配、不重写"（改进计划设计
    边界第 1 条）在本步骤的体现，也让改动可以做到"默认关闭，开启后行为
    可预测、可回退"，而不是一次性改变三条通道全部的仲裁响应路径。

23. **`channel_weights` 采用固定值，不做"预算消耗越多、权重越低"式的
    自适应**：与 P5 第 1-2 步 `suggest_order()` 的选择一致（见既有决策
    19），也是改进计划待讨论问题 4 明确建议的路径——"先上线固定权重
    版本，积累实际调度数据后再评估是否需要自适应机制，避免一开始就
    引入难以调试的动态反馈系统"。本轮固定权重版本已经解决了"cron 保底"
    这个最迫切的不对称问题（`reserved_min_concurrent`），自适应权重
    留给以后视观察到的调度数据决定是否需要。

24. **`ObjectiveExecutor`/`CronJobRunner` 各自独立调用
    `allocate_weighted_slots()`，不引入一个共享的 `UnifiedTaskScheduler`
    单例来"广播"分配结果**：改进计划原文写"改为向它'申请槽位'"，字面
    上暗示一个共享的调度器实例。但复核代码后发现 `ObjectiveExecutor` 与
    `CronJobRunner` 之间目前没有任何直接引用关系（都是被
    `AutonomousLoop`/`HttpServer` 分别持有，互相不知道对方的存在），
    引入一个共享单例意味着要么在两者之外新建一个必须被同时注入两处的
    生命周期对象（改变两个类的构造签名和现有的依赖注入路径），要么退化
    成一个全局单例（违反项目里"避免隐式全局状态"的一贯风格）。
    `allocate_weighted_slots()` 是纯函数、给定相同输入必然给出相同
    输出——两个类各自读同一份 `cfg.scheduler`/`cfg.cron` 配置、各自调用
    这个纯函数，效果与"向一个共享调度器申请槽位"完全等价（两次调用
    在同一个 tick 周期内看到的配置是同一份，计算结果天然一致），但不
    需要改变任何现有类的构造方式或引入新的运行时依赖对象，风险显著
    更低，且完全符合"改进计划第 3 步是这一具体决策点收归统一，不是
    收归到某一个具体的运行时对象"这一目标本身。等到第 4 步真正需要
    "统一入口调用 `tick()`"时，再评估是否需要这样一个共享实例。

25. **`goal_cycle` 通道不单独参与槽位分配，`allocate_weighted_slots()`
    调用时只传 `{"goal": w_goal, "cron": w_cron}` 两个权重**：与 P5
    第 1-2 步既有决策一致——`goal_cycle` 触发后转发进 `ObjectiveExecutor`
    （改进计划背景第 3 条），复用的就是 goal 通道的并发池，不是一个
    独立的执行槽位消费者。`channel_weights` 配置里仍保留 `goal_cycle`
    这个键（默认 1.0），是为了与 `suggest_order()` 的排序预览保持字段
    形状一致，但第 3 步的槽位分配计算不读取它，避免"配置了却没有实际
    效果"的字段被误解为已经生效。

26. **构造并发上限（`max_concurrent`/`MAX_CONCURRENT_OBJECTIVES` 等既有
    天花板）仍在统一裁决结果之上再收紧一次，不是被统一裁决取代**：
    `effective_max_concurrent()` 的既有结构是"层层取更严格值"（模块级
    绝对天花板 → 配置 cap → degraded 收紧 → 自适应收紧，见 Track K
    文档字符串"只降不升，安全阀在两端"），统一裁决只是替换了"degraded
    收紧"这一层用什么数字，不改变这个"层层收紧、最终取最严格值"的既有
    结构。这意味着理论上存在"`allocate_weighted_slots()` 算出的份额超过
    goal 通道自身天花板、多出的槽位没有被任何通道实际使用"的情况（见
    `test_allocation_beyond_goal_ceiling_is_clamped_by_module_cap`）——
    这是已知的、留给未来视是否有实际影响再评估的开放问题（不是本轮的
    实现缺陷：任何两个独立收紧机制叠加都会有这种"名义分配用不完"的
    可能，第 4 步"接管实际派发"如果需要更精确的槽位利用率，届时会有
    完整上下文重新设计）。

### 测试结果（P5 第 3 步）

```
tests/test_unified_task_scheduler.py            29 passed  （含新增 7 项）
tests/test_unified_arbitration_p5_step3.py        6 passed  （新增）
```

与 P0-P5 第 1-2 步既有测试文件联合运行，确认无回归：

```
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py
tests/test_goal_cron_unified_scheduler_p3.py
tests/test_scheduler_heartbeat.py
tests/test_scheduling_overview_route.py
tests/test_unified_task_scheduler.py
tests/test_unified_scheduler_preview_route.py
tests/test_unified_arbitration_p5_step3.py
tests/test_objective_executor_adaptive_concurrency.py
tests/test_resource_arbiter_gating_track_j.py
80 passed, 0 failed
```

`tests/test_unified_scheduler_preview_route.py` 单独复核（确认新增
`slot_allocation` 字段未破坏既有 4 项用例）：4 passed。

全量回归本轮未完整跑完（本地沙箱执行较慢，单次 `pytest tests/` 超出
执行时间限制被中断），已确认无回归的范围覆盖了本次改动直接涉及的全部
模块（`unified_task_scheduler`/`objective_executor`/`cron_job_runner`/
`config.models`/`api.routes` 的 unified_scheduler_preview 端点）；仍有
2 个文件在收集阶段报错（`test_judge_verdict.py` 缺 `json_repair`、
`test_session.py` 的 `_flock` 平台兼容问题），与 P4 实施记录里的结论
一致，确认与本轮改动无关，未在本次范围内修复。

### 待确认项回应（对照原方案 §4，追加 P5 第 3 步相关项）

10.（对应原方案 §4 第 2 条）cron 自己耗尽预算是否需要单独设上限：本轮
    第 3 步解决的是"degraded 状态下的并发分配"，与"预算是否耗尽"是两个
    独立的判定维度（`blocked` 仍是二元硬限制，本轮未改变），该问题仍然
    开放，留给后续评估。
11.（对应原方案 §4 第 4 条，追加）`channel_weights` 本轮确认采用固定值
    版本（回应同待确认项 8），第 3 步已经用这份固定权重支撑了"cron 保底
    并发数"的实际落地（`reserved_min_concurrent`），验证了固定权重版本
    本身已经能解决当前最迫切的不对称问题，进一步印证了待确认项 4/8
    "先上线固定权重、积累数据后再评估自适应"这一路径的合理性。

其余待确认项（1-9）沿用 P0-P5 第 1-2 步实施记录的既有回应，状态不变。

## P5 第 4 步 —— 接管实际派发（部分完成）

**处理状态：cron/goal_cycle 两通道的 `execute()` 已实现真正委托派发；
goal 通道未实现；`AutonomousLoop` 尚未切换到经由 `UnifiedTaskScheduler`
派发。** 对应改进计划 P5 分步迁移路径的第 4 步，本轮是一个明确的**子集**
完成，不是整步。

### 新增/修改文件（P5 第 4 步）

| 文件 | 改动 |
|---|---|
| `src/mini_agent/evolution/cron_scheduler.py` | `tick()` 循环体里"触发单个 job + 记账"（调用 `_fire()`、更新 `last_run_at`/`run_count`/`next_run_at`/`consecutive_skip_count`、失败时告警）抽成新方法 `_trigger_and_record(job, now) -> bool`；`tick()` 改为调用它，行为完全保留（`skip_state_changed` 判定逻辑同步调整为"调用前后 `consecutive_skip_count` 是否变化"，语义与改造前等价）。新增公开方法 `trigger_job_now(job_id: str) -> bool`：查 job、调用同一个 `_trigger_and_record()`、`save()`——供外部（当前是统一调度层的两个 cron 侧适配器）直接触发某个 job，且与 `tick()` 共用同一份记账，不会造成"触发了但记账没更新，下次 tick 又重复触发"的错位 |
| `src/mini_agent/evolution/unified_task_scheduler.py` | `CronChannelAdapter`/`GoalCycleChannelAdapter.execute()` 从 `raise NotImplementedError` 改为 `return bool(self._cron_scheduler.trigger_job_now(task.task_id))`，`cron_scheduler` 为 `None` 或触发过程异常均返回 `False`（不抛出）；`ObjectiveChannelAdapter.execute()` 保持 `raise NotImplementedError`，文档字符串更新为说明"为什么这次没有实现"（Goal 通道派发逻辑深度耦合 `AutonomousLoop` 运行时状态，缺一个安全的公开入口）；模块头部文档同步更新范围边界说明 |
| `tests/test_unified_dispatch_p5_step4.py` | 新增：10 项用例，覆盖 `trigger_job_now()` 成功/失败的记账正确性（含"失败后再成功，`consecutive_skip_count` 正确清零"）、未知 `job_id` 返回 `False`、`tick()` 重构前后行为一致性（一次到期触发的记账结果验证）、`CronChannelAdapter`/`GoalCycleChannelAdapter.execute()` 的委托正确性（含 `None` scheduler 场景）、`ObjectiveChannelAdapter.execute()` 仍然 raise |
| `tests/test_unified_task_scheduler.py` | 原 `test_execute_raises_not_implemented` 改名为 `test_execute_with_none_scheduler_returns_false_not_raises`，断言从"raise"改为"返回 `False`"，反映 cron/goal_cycle 两适配器 `execute()` 的新行为；模块头部文档字符串同步更新 |

### 关键设计决策（P5 第 4 步）

27. **只完成"cron/goal_cycle 两通道的 `execute()`"这一个子集，明确不
    在本轮切换 `AutonomousLoop` 的实际调用路径**：改进计划原文第 4 步
    的完整目标是"三条通道的 tick() 触发点最终都收敛成
    `UnifiedTaskScheduler.tick()` 一个入口"，但 `AutonomousLoop.
    _tick_passive()`/`_tick_maintenance()` 是 daemon 的核心生产路径，
    直接切换调用入口意味着：(a) 需要新建/注入一个
    `UnifiedTaskScheduler` 运行时实例到 `AutonomousLoop`（改变现有构造
    签名和依赖关系）；(b) 一旦切换出错，影响的是所有到期 cron/
    goal_cycle job 是否还能正常触发这一核心功能，且本地沙箱环境无法
    完整跑一次真实 daemon 生命周期来验证。改进计划设计边界第 5 条本身
    也允许"允许长期与旧路径并存直到确认稳定"——本轮选择先把
    "execute() 委托本身是否记账正确"这一半独立、可以脱离
    `AutonomousLoop` 完整验证的部分做完、测试覆盖，是把一个高风险大
    改动拆成"先证明子部件可靠"与"再决定何时切换调用方"两个独立、可
    分别评估的阶段，切换调用方本身留给后续（且很可能需要专门的灰度
    开关和更完整的集成测试环境，不适合在当前验证条件下一次性做完）。

28. **`trigger_job_now()` 与 `tick()` 共用 `_trigger_and_record()`
    是本轮最关键的安全前提，不是可选的代码整洁性改进**：P5 第 1-2 步
    实施记录的既有决策 18 已经明确指出过风险——"如果现在就把 execute()
    接到 CronScheduler 的真实触发逻辑上……绕过三条通道各自现有的去重/
    并发/公平性检查，造成重复执行"。具体到 cron 通道，风险点是
    "记账不同步"：如果 `execute()` 只是简单调用一个不更新 `next_run_at`
    的触发函数，`tick()` 下一次运行时仍会认为这个 job 到期，造成重复
    触发。本轮通过让 `tick()` 自己也改成调用 `_trigger_and_record()`
    （而不是给 `trigger_job_now()` 单独拼一份"看起来等价"的记账代码），
    从根本上排除了两条路径记账不一致的可能——两者物理上就是同一段
    代码，不存在"改一处忘了改另一处"的维护风险。这是本轮愿意实现
    cron/goal_cycle 两个 `execute()`（而不是继续保持 P5 第 1-2 步"提前
    实现是不安全的"结论）的前提条件；`ObjectiveChannelAdapter.execute()`
    之所以本轮仍不实现，正是因为 Goal 通道没有一个类似的、已经被
    验证"记账口径与现有触发路径完全一致"的公开入口可以复用（见决策 29）。

29. **`ObjectiveChannelAdapter.execute()` 本轮不实现，且明确不采用
    "简化版重新实现"这条路径**：读码确认 `AutonomousLoop.
    _tick_maintenance()` 里 Goal 通道的实际派发（约 90 行代码，见
    `autonomous_loop.py` 行 384-472）涉及：资源仲裁 pause/resume、
    fairness_paused_objective_ids/user_paused_objective_ids 两种暂停
    状态的排除、按 Goal 分组的 per-goal 并发上限检查
    （`running_count_for_goal`/`max_concurrent_objectives_per_goal`）、
    公平轮询候选的调度顺序、`mark_scheduled()`/`resume_fairness()` 等
    多个相互关联的状态更新。这些状态目前只存在于 `AutonomousLoop`
    实例内部（不是 `ObjectiveExecutor` 自己管理的），没有一个类似
    `CronScheduler.trigger_job_now()` 那样"给定一个具体 Goal/Objective，
    保证按现有全部规则正确触发一次"的独立公开方法。要实现
    `ObjectiveChannelAdapter.execute()`，要么（a）在适配器里重新拼一套
    简化版判断逻辑——这会导致两份平行的、大概率逐渐漂移的判断逻辑，
    正是改进计划本身想解决的"三条通道各自实现一套局部判断"问题在
    Goal 通道内部的翻版；要么（b）先重构 `AutonomousLoop`，把这部分
    逻辑抽成一个独立的公开方法（类似本轮对 `CronScheduler` 做的
    `trigger_job_now()`）。(b) 是正确路径，但 `AutonomousLoop` 这部分
    代码的改动面明显大于本轮对 `CronScheduler` 的改动（`CronScheduler.
    tick()` 循环体只有～20 行且已经是一个独立方法，`AutonomousLoop`
    这部分逻辑跨越多个状态字段、与同一 tick 内的其它步骤有隐含的
    先后依赖），贸然在本轮一并做掉超出了"能在当前验证条件下充分测试"
    的范围，留给后续单独评估、单独实施。

### 测试结果（P5 第 4 步）

```
tests/test_unified_dispatch_p5_step4.py              10 passed （新增）
```

`CronScheduler.tick()` 内部重构（`_trigger_and_record()` 抽取）的行为
保留性验证——与既有 cron 相关测试文件联合运行，确认无回归：

```
tests/test_cron_scheduler_local_handler.py
tests/test_cron_job_runner.py
tests/test_cron_scheduler_priority.py
tests/test_goal_cron_feedback_and_output_policy.py
tests/test_goal_cron_bridge.py
tests/test_cron_schedule_validation.py
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py
tests/test_cron_scheduler_reap_stale_jobs.py
84 passed, 0 failed
```

与 P0-P5 第 1-3 步全部既有测试文件联合运行，确认无回归：

```
tests/test_unified_task_scheduler.py
tests/test_unified_dispatch_p5_step4.py
tests/test_unified_arbitration_p5_step3.py
tests/test_unified_scheduler_preview_route.py
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py
tests/test_goal_cron_unified_scheduler_p3.py
tests/test_scheduling_overview_route.py
tests/test_scheduler_heartbeat.py
tests/test_cron_scheduler_local_handler.py
tests/test_cron_job_runner.py
tests/test_cron_scheduler_priority.py
tests/test_goal_cron_feedback_and_output_policy.py
tests/test_goal_cron_bridge.py
tests/test_cron_schedule_validation.py
tests/test_cron_scheduler_reap_stale_jobs.py
tests/test_objective_executor_adaptive_concurrency.py
tests/test_resource_arbiter_gating_track_j.py
163 passed, 0 failed
```

全量回归本轮仍未完整跑完（本地沙箱执行较慢，`pytest tests/` 单次超出
执行时间限制被中断），已确认无回归的范围覆盖了本次改动直接涉及/间接
关联的全部模块；`test_judge_verdict.py`（缺 `json_repair`）、
`test_session.py`（`_flock` 平台兼容问题）两个既有的收集期报错文件与
本轮改动无关，结论与 P4/P5 第 3 步实施记录一致。

### 待确认项回应（对照原方案 §4，追加 P5 第 4 步相关项）

12. `AutonomousLoop` 何时切换到经由 `UnifiedTaskScheduler` 派发 cron/
    goal_cycle job（而不是直接调 `cron_scheduler.tick()`）：本轮未做
    这个切换，留待后续单独评估——需要先设计一个灰度开关（类似
    `scheduler.unified_arbitration_enabled` 的思路）+ 更完整的集成
    验证，不适合与"证明 execute() 委托本身可靠"这一步骤合并。
13. `ObjectiveChannelAdapter.execute()` 需要的"Goal 通道安全公开入口"
    该以什么形式抽出（是否需要连带重构 `AutonomousLoop` 现有的暂停/
    公平性状态管理方式）：本轮未评估具体方案，只确认了"直接在适配器里
    重新实现一遍简化逻辑"这条路径不可取（见决策 29），具体重构方案
    留给后续单独设计。

其余待确认项（1-11）沿用 P0-P5 第 1-3 步实施记录的既有回应，状态不变。



## P5 第 5 步 —— 灰度接入统一入口（子集完成）

**处理状态：cron/goal_cycle 两通道已可经统一调度层派发，由配置开关
控制默认关闭；Goal 通道派发路径未涉及，`AutonomousLoop` 内部调用点
本身仍保留（切换逻辑内置在同一方法里，不是替换调用点，见下方决策
30）。** 对应改进计划 P5 分步迁移路径第 4-5 步交界处：第 4 步遗留的
"两个 `execute()` 已经可以安全调用，但还没有人在调用"这一状态，本轮
让`AutonomousLoop`在可控的灰度开关下开始调用它们。

### 新增/修改文件（P5 第 5 步）

| 文件 | 改动 |
|---|---|
| `src/mini_agent/evolution/unified_task_scheduler.py` | 新增 `dispatch_due_cron_jobs(cron_scheduler)`：合并 `CronChannelAdapter`/`GoalCycleChannelAdapter` 两条通道的 `poll_due()` 结果、按 `priority` 降序排序后依次调用各自的 `execute()`，返回触发成功的 `job_id` 列表，返回值语义与 `CronScheduler.tick()` 完全一致；`cron_scheduler=None` 或 `poll_due()` 异常时返回空列表，不抛出。**附带修复**：`_poll_cron_jobs()` 原先 `next_run_at is None or next_run_at > now` 的判断遗漏了 `next_run_at <= 0`（`CronScheduler.tick()` 用来标记"尚未初始化"的哨兵值）这一情况——`0 > now` 恒为假，会被误判为"早已到期"。P5 第 1-2 步该字段只供只读预览使用，这个误差不影响任何实际执行；但本轮 `dispatch_due_cron_jobs()` 要真正拿它去触发，必须与 `tick()` 的到期判断口径完全一致，因此补上了这个排除条件 |
| `src/mini_agent/config/models.py` | `SchedulerConfig` 新增 `unified_dispatch_enabled: bool = False`——控制 `AutonomousLoop._tick_passive()` 是否改用 `dispatch_due_cron_jobs()` 派发普通 cron/goal_cycle job，默认关闭，未升级配置行为不变 |
| `src/mini_agent/evolution/autonomous_loop.py` | `_tick_passive()` 里原来直接调用 `self._cron_scheduler.tick()` 的地方，改为先读取 `getattr(self._cfg, "scheduler", None)` 上的 `unified_dispatch_enabled`（缺失时按 `False` 处理，兼容没有 `scheduler` 属性的老 `cfg` 对象），`True` 时改调用 `unified_task_scheduler.dispatch_due_cron_jobs(self._cron_scheduler)`，`False`（默认）或读取失败时保持原有 `tick()` 调用；两条路径返回值语义一致，后续的 `_record_digest()` 记录逻辑无需区分 |
| `tests/test_unified_dispatch_p5_step5.py` | 新增：8 项用例，覆盖 `dispatch_due_cron_jobs()` 的 `None` 输入、cron+goal_cycle 混合触发且按 priority 排序、`next_run_at<=0` 边界修复、触发失败时 `consecutive_skip_count` 正确递增但不进入触发列表、goal_cycle job 经 `GoalCycleChannelAdapter` 正确派发；`AutonomousLoop` 灰度开关的开/关两条路径（含真实 `CronScheduler` 端到端验证）、`cfg` 上完全缺失 `scheduler` 属性时静默退化为 `tick()` 路径不抛异常 |

### 关键设计决策（P5 第 5 步）

30. **只在 `_tick_passive()` 方法内部做"二选一"分支，不新增/替换
    `AutonomousLoop` 的调用点或构造签名**：改进计划原文 P5 第 5 步的
    完整目标是"三条通道的 tick() 触发点最终都收敛成
    `UnifiedTaskScheduler.tick()` 一个入口"，但本轮沿用 P5 第 3 步既有
    决策 24 的思路——不引入一个需要被同时注入 `AutonomousLoop` 的共享
    `UnifiedTaskScheduler` 运行时实例（会改变现有构造签名和依赖注入
    路径），而是让 `dispatch_due_cron_jobs()` 作为一个无状态纯函数，
    每次调用时用传入的 `cron_scheduler` 现场构造两个轻量适配器。这样
    `AutonomousLoop.__init__()` 的构造签名完全不变，`_tick_passive()`
    内部只是多了一个 `if` 分支，改动面被压缩到最小，符合改进计划设计
    边界第 5 条"允许长期分阶段推进"的要求——是否/何时把这个分支替换成
    真正的"统一入口调用"，留给后续视灰度观察结果决定。

31. **默认关闭而不是默认开启，且不提供"部分通道单独开关"的更细粒度
    控制**：`dispatch_due_cron_jobs()` 与 `tick()` 理论上应该产生完全
    等价的触发结果（到期判断、触发、记账三个环节都复用同一份底层
    实现），但"理论等价"和"经过充分灰度验证的等价"是两回事——`tick()`
    是 daemon 至今唯一被验证过的生产路径，`dispatch_due_cron_jobs()`
    多引入了一层"先聚合排序、再逐个触发"的组装逻辑（尽管排序键与
    `tick()` 内部完全一致），在没有实际运行数据支撑之前默认开启是不
    必要的风险。开关粒度上选择"cron+goal_cycle 整体开关"而不是"cron
    单独开、goal_cycle 单独开"，是因为两者内部走的是同一个
    `CronScheduler.trigger_job_now()`，拆分开关不会带来额外的风险
    隔离效果，反而增加配置面和测试组合数，与改进计划待讨论问题 5
    "避免开关组合爆炸导致的测试覆盖盲区"的顾虑一致。

32. **`dispatch_due_cron_jobs()` 的"多次 `save()`"这一已知行为差异
    本轮不做进一步优化**：`tick()` 一次调用只在触发列表非空或有状态
    变化时整体 `save()` 一次；`dispatch_due_cron_jobs()` 通过
    `trigger_job_now()` 逐个派发，每个成功/失败的 job 各自触发一次
    `save()`。曾考虑过是否要在本函数内部批量收集变更、最后统一落盘
    一次，但那样需要在 `dispatch_due_cron_jobs()` 里重新拼一份"只
    save 一次"的批量逻辑，或者要求 `CronScheduler` 暴露一个"记账但不
    落盘"的变体接口——两者都会引入与 `_trigger_and_record()`/
    `trigger_job_now()` 平行的第二份记账相关代码路径，与 P5 第 4 步
    决策 28"两条路径物理上就是同一段代码，不存在'改一处忘了改另一处'
    的维护风险"这一关键安全前提相冲突。多次落盘的 IO 成本在到期 job
    数量正常范围内（单个 tick 周期内到期 job 通常是个位数）可以忽略，
    换来的正确性保证更重要，因此本轮明确选择不优化，留作已知的、
    可接受的行为差异记录在案。

33. **未涉及 `_tick_maintenance()` 里的 Goal 通道派发路径，`autonomy.
    level=maintenance/autonomous` 档位下 `_tick_maintenance()`
    仍然独立调用 `cron_scheduler.tick()`（而非 `_tick_passive()` 里
    新增的分支）**：复核代码确认 `_tick_maintenance()`/`_tick_
    autonomous()` 目前的 cron 触发路径与 `_tick_passive()` 是各自
    独立的调用点（都直接调 `self._cron_scheduler.tick()`），本轮为了
    把改动面控制在"先验证一条路径"的最小范围，只改了 `_tick_passive()`
    这一个方法体；`maintenance`/`autonomous` 档位下 cron 到期触发的
    行为本轮完全不变。这是一个**明确的遗留范围**（不是遗漏）——把
    同样的分支逻辑复制到 `_tick_maintenance()`/`_tick_autonomous()`
    技术上很直接，但既然灰度开关本身就是为了"先观察一段时间数据"，
    没有必要在观察结果出来之前就把改动面扩大到全部三个档位；下一轮
    如果灰度观察无异常，会一并把这两个档位的调用点也切过去，并同时
    评估是否值得把这部分"读开关 + 二选一"的样板代码抽成一个共享的
    私有方法（当前只有一处调用，重复一次之前抽取暂无必要）。

### 测试结果（P5 第 5 步）

```
tests/test_unified_dispatch_p5_step5.py             8 passed （新增）
```

与 P0-P5 第 1-4 步既有测试文件联合运行，确认无回归：

```
tests/test_unified_dispatch_p5_step4.py
tests/test_unified_dispatch_p5_step5.py
tests/test_unified_task_scheduler.py
tests/test_unified_arbitration_p5_step3.py
tests/test_goal_cron_unified_scheduler_p0_p1_p2.py
tests/test_goal_cron_unified_scheduler_p3.py
tests/test_scheduler_heartbeat.py
tests/test_cron_scheduler_local_handler.py
tests/test_cron_job_runner.py
tests/test_cron_scheduler_priority.py
tests/test_goal_cron_feedback_and_output_policy.py
tests/test_goal_cron_bridge.py
tests/test_cron_schedule_validation.py
tests/test_cron_scheduler_reap_stale_jobs.py
tests/test_objective_executor_adaptive_concurrency.py
tests/test_resource_arbiter_gating_track_j.py
162 passed, 0 failed
```

`tests/test_unified_scheduler_preview_route.py`/`tests/test_scheduling_
overview_route.py`（P4/P5 第 1-2 步既有端点测试）本轮沙箱环境缺
`httpx2` 包，`fastapi.testclient` 导入阶段报错，无法采集运行——与本轮
改动的模块（`unified_task_scheduler.py`/`autonomous_loop.py`/
`config/models.py`）没有依赖关系，确认是环境限制而非本轮引入的回归
（这两个端点测试文件本身未被本轮修改）。

全量回归本轮仍未完整跑完（本地沙箱执行较慢），已确认无回归的范围
覆盖了本次改动直接涉及的全部模块。

### 待确认项回应（对照原方案 §4，追加 P5 第 5 步相关项）

14.（对应 P5 第 4 步待确认项 12）`AutonomousLoop` 何时切换到经由
    `UnifiedTaskScheduler` 派发 cron/goal_cycle job：本轮给出的答案是
    "先在 `_tick_passive()` 一个档位下加一个默认关闭的灰度开关"——不是
    "立刻切换"，也不是"继续无限期搁置"，是两者之间的折中：功能已经
    可以被打开验证，但默认行为完全不变，且刻意没有扩大到
    `_tick_maintenance()`/`_tick_autonomous()`（见决策 33），把"何时
    默认开启"、"何时把其余两个档位也接进来"都留给积累了实际灰度数据
    之后再决定。
15. `ObjectiveChannelAdapter.execute()` 仍未实现，P5 第 4 步待确认项
    13 提出的"Goal 通道安全公开入口该以什么形式抽出"本轮未涉及，状态
    不变——本轮的灰度开关范围明确限定在 cron/goal_cycle 两条通道。

其余待确认项（1-13）沿用 P0-P5 第 1-4 步实施记录的既有回应，状态不变。
