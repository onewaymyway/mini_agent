# Goal / Cron 三条执行通道 统一调度层 改进计划 · 实施记录

对应计划文档：`next_doc/goal_cron_unified_scheduler_improvement_plan.md`
（P0、P1、P2、P3、P4 已完成；P5 为长期目标，本轮未启动）

## 处理状态

- **P0（cron 分级响应资源仲裁）**：已完成。
- **P1（cron 消耗统一记账）**：已完成。
- **P2（cron 跳过追踪与主动告警）**：已完成。
- **P3（tick() 执行看门狗）**：已完成。
- **P4（统一调度可观测面板）**：已完成（后端只读端点 + 看板 UI 展示区块，
  分两轮完成，见下方 P4 小节及"P4 追加（看板 UI）"小节）。
- **P5（收敛到统一调度层）**：第 1-2 步（定义统一接口 + 三条通道只读
  适配 + 只读聚合排序建议）本轮已完成；第 3-5 步（接管仲裁裁决/接管实际
  派发）仍是未启动的长期目标，见下方"P5 第 1-2 步"小节。

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
