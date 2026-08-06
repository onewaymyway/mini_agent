# Goal / Cron 三条执行通道 统一调度层 改进计划 · 实施记录

对应计划文档：`next_doc/goal_cron_unified_scheduler_improvement_plan.md`
（P0、P1、P2 已完成；P3、P4 未开始；P5 为长期目标，本轮未启动）

## 处理状态

- **P0（cron 分级响应资源仲裁）**：已完成。
- **P1（cron 消耗统一记账）**：已完成。
- **P2（cron 跳过追踪与主动告警）**：已完成。
- **P3（tick() 执行看门狗）**：未开始，留待后续独立实施。
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
