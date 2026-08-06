# Goal / Cron 三条执行通道 统一调度层 改进计划 · 实施记录

对应计划文档：`next_doc/goal_cron_unified_scheduler_improvement_plan.md`
（P0、P1、P2、P3 已完成；P4 未开始；P5 为长期目标，本轮未启动）

## 处理状态

- **P0（cron 分级响应资源仲裁）**：已完成。
- **P1（cron 消耗统一记账）**：已完成。
- **P2（cron 跳过追踪与主动告警）**：已完成。
- **P3（tick() 执行看门狗）**：已完成（本轮）。
- **P4（统一调度可观测面板）**：未开始，留待后续独立实施。
- **P5（收敛到统一调度层）**：长期目标，本轮未启动，不在本次范围内。

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
