# Cron 任务专属执行机制 使用指南

- **设计文档**：`next_doc/cron_dedicated_execution_improvement_plan.md`
- **实施记录**：`next_doc/cron_dedicated_execution_implementation_record.md`
- **前置依赖**：daemon 模式的基础 cron 调度（`evolution/cron_scheduler.py`，
  见 [自主 Daemon 设计](autonomous-daemon-design.md) 里 CronScheduler 的
  说明）——本文只讲"job 到期之后，具体怎么被执行"这一段，job 的增删改查、
  schedule 语法本身不重复展开。
- **当前状态**：Track A-M 全部完成，核心执行链路、REST API、看板 tab、
  正式配置字段、单元测试（58 项）均已落地。之后 §3.1-§3.4/§7.2 描述的
  资源仲裁接入、优先级排序、卡死回收 watchdog、跨 job 熔断、degraded
  加权分配等机制，是后续
  `next_doc/scheduling_unification_and_kanban_visibility_improvement_plan.md`、
  `next_doc/daemon_task_hang_recovery_and_watchdog_hardening_plan.md`、
  `next_doc/goal_cron_unified_scheduler_improvement_plan.md` 等方案在这套
  执行链路上层叠加的，本文档一并收录，不单独维护变更历史。

---

## 1. 这套机制解决什么问题

daemon 里的 cron job（比如"每天 22:00 生成日报""每 6 小时跑一次巩固循环
扫描"）原本和用户消息共用同一条 `InputQueue` + 单线程 `AgentRunner`：

- 一个 cron job 跑得久，会卡住其它 job 和用户消息——daemon 表现为"卡住
  不回复"
- 没有单任务的超时/步数上限，极端情况下一个任务可能无限跑下去
- 每次触发都是全新的一条消息，没有"上次做到哪了"的状态，需要多次触发
  才能做完的任务每次都从头开始
- 没有"是否卡住"的判断，输出重复雷同、原地打转的任务会一直被重复调度、
  浪费预算
- 看板看不到执行细节，排查"这次到底跑了什么"只能翻 daemon 日志

Cron 任务专属执行机制把 cron job 的**实际执行**搬到独立后台线程，加上
超时/步数/卡死检测三重兜底、每 job 专属的进度存档文件夹，解决以上问题。
**cron 任务本身如何配置（schedule、task_template）不变**，改变的只是
"到期之后怎么跑"。

## 2. 整体执行链路

```
CronScheduler.tick() 到期
  → _fire(job)
  → CronJobRunner.submit(job)          # 立即返回，不阻塞主线程
       └─ 独立后台线程：
            build_cron_agent(job)      # 全量继承主 Agent 工具集的专用 Agent
              → CronJobExecutor.run_job(job, submit_step_fn)
                   while 未超时 且 未超步数:
                       result = submit_step_fn(prompt)   # 一次 run_turn()
                       判断完成 / 继续 / 卡死
                   写回 state.json / runs/<run_id>.jsonl
```

- **不阻塞主线程**：`AgentRunner`（daemon 的单线程主循环）只负责
  `submit()` 一下就返回，真正执行发生在独立线程里，用户消息和其它到期
  job 不会被挤占。
- **未注入 `job_runner` 时自动回退旧路径**（直接 `submit_fn` 塞回
  `InputQueue`），向后兼容不升级配置的部署——这条回退路径完全是内部
  兼容性考虑，普通使用不需要关心。
- **不是所有 job 都会走这条链路**：`CronScheduler._fire()` 在
  `CronJobRunner.submit(job)` 之前还有两个更高优先级的分支——
  `run_mode="goal_cycle"` 的 job（绑定了 Goal，走 Objective 执行通道）
  和注册过 `register_local_handler()` 的 job（"本地回调"，比如
  `sys:watchlist_report_<tier_id>` 这类零 LLM 成本、确定性逻辑的内置
  job）——命中这两类会直接在原进程/回调里同步执行，完全不经过本文档
  描述的独立线程 + `CronJobWorkspace` 记录链路。`CronJob.run_count`
  这个计数字段在三条路径下都会正常累加（只要触发成功），但只有走完整
  这条链路的 job 才会在 `CronJobWorkspace` 里留下 `recent_runs`/
  `recent_runs_summary`——这是"看板某个 job 累计运行次数不是 0，但
  执行记录列表却是空的"最常见的原因，详见 §8 的 `execution_channel`
  字段和 §10 排查指南。

## 3. 并发控制

`CronJobRunner` 用一个容量可变的槽位实现（不是固定容量的
`threading.Semaphore`）限制同时执行的 cron job 数量，默认 2（见 §7 全局
配置）。超出当前生效上限的 job **在线程内部排队等待**，不会丢失触发——
只是延后开始。

同一个 job 如果上一次执行还没跑完，本次触发会被直接拒绝（避免调度
间隔比单次执行还短的极端配置导致同一 job 并发跑两份）。

看板/API 区分"排队中"和"执行中"两种状态（`CronJobRunner.
execution_phase()`）：已提交但还没真正拿到槽位是 `queued`，拿到槽位、
Agent 已经在跑是 `running`——此前两者会被合并展示为笼统的"正在执行"。

### 3.1 并发上限不是恒定的：degraded 时会自动收紧

当 `ResourceArbiter.gating_state()` 判定为 `degraded`（预算紧张/用户
明显活跃切换等），`CronJobRunner.effective_max_concurrent()` 会把并发
上限临时收紧到 `cron.degraded_max_concurrent`（默认 1），状态恢复 `full`
后自动回升，不需要人工干预。这个收紧和 Goal Objective 通道的
`autonomy.resource_gating_degraded_max_concurrent` 是两个独立配置项，
可以分别调整幅度。

如果开启了 `scheduler.unified_arbitration_enabled`（见 §7.1），degraded
时的实际上限改由 `UnifiedTaskScheduler` 按 `channel_weights` 统一裁决，
但保证 cron 通道至少分到 `cron.reserved_min_concurrent`（默认 1）个槽位，
不会被 Goal 通道完全挤占。

### 3.2 资源仲裁：用户自定义 job 会被暂停，`sys:` 系统维护 job 不受影响

`submit()` 在真正占用槽位之前，会对非 `sys:` 前缀的用户自定义 job（含
`goal_cron_bridge` 绑定但走 `run_mode="message"` 的普通 job；绑定为
`run_mode="goal_cycle"` 的 job 走另一条分支，见 §5 的相关说明）额外检查
一次 `ResourceArbiter.gating_state()`：

- `blocked`（预算耗尽/挫败感达到阈值）→ 本次不触发，累加进程内计数器
  `CronJobRunner.arbiter_skipped_count`，下次 tick 会重试，不会丢失
  触发记录；`CronJob.consecutive_skip_count` 同步 +1（由
  `CronScheduler.tick()` 维护）。
- `degraded` → 不阻断触发，只影响 §3.1 的并发上限收紧。
- 仲裁模块本身异常 → 保守放行，不能因为仲裁检查失败导致所有 cron job
  停摆。

`sys:` 前缀的内置维护任务（`sys:digest_trim`、`sys:session_cleanup` 等）
不受这条检查影响，设计上就应该在用户在场时也能正常跑（本身低频、轻量、
以只读扫描为主）。

`CronJob.consecutive_skip_count` 达到 `cron.skip_alert_threshold`（默认
5）时会通过 `NotificationDispatcher` 发一次告警，避免一个 job 长期
"到点但从来没成功触发过"而没人注意到。

同一次 `tick()` 内多个 job 同时到期时，按 `CronJob.priority`（默认 0，
数值越大越优先）降序排序后依次提交——只影响"谁先拿到排队位置"，不做
抢占（正在执行的 job 不会被打断）。可以在看板 cron job 卡片上直接调整
优先级（见 §9），或通过 `PUT /v1/cron/jobs/{id}` 的 `priority` 字段。

### 3.3 卡死回收（watchdog）

如果 `CronJobExecutor.run_job()` 内部真正卡死不返回（网络请求挂起/
工具调用阻塞在某个系统调用上），仅靠线程正常收尾的记账方式会让这个
job 之后所有触发都被 `submit()` 静默拒绝（`_running_job_ids` 里的记录
永远不会被清理），攒够 `max_concurrent_jobs` 个卡死 job 后 cron 功能
会整体瘫痪。

`CronJobRunner.reap_stale_jobs()` 提供外部存活性回收：daemon 每次
`_tick_maintenance()` 都会调用一次，扫描所有正在跑的 job，若某个 job
运行时长超过"该 job 自己的 `timeout_seconds`（或全局
`cron.default_timeout_seconds`）+ `cron.stale_job_watchdog_grace_seconds`
（默认 5 分钟宽限期）"，判定为卡死：强制清空记账、释放它占用的槽位、
把 workspace 状态标记为 `needs_human_review`。真正卡死的旧线程本身
无法被强制杀死，会作为孤儿线程继续在后台跑，但它收尾时会发现自己的
执行 token 已经不是当前合法 token，从而跳过重复清理，不会和 watchdog
的回收互相踩踏。

被回收次数记在进程内计数器 `CronJobRunner.reaped_job_count`（不持久化，
daemon 重启后清零），透出在 `GET /v1/self/execution_model_status` 的
`cron.reaped_job_count` 字段。

### 3.4 跨 job 广度熔断

`CronJobRunner` 内部持有一个共享的 `CircuitBreakerCore` 实例（scope 用
`job_id`）：如果同一粗分类的 `error_type` 在超过
`cron.circuit_breaker_distinct_threshold`（默认 `None`，不启用）个
**不同** job 上都失败过，判定为系统性问题（比如某个第三方 API 全局
失效），通过 `NotificationDispatcher` 主动告警——只记录 + 告警，
**不阻断**新 job 的调度。

## 4. 执行循环：超时 + 步数 + 卡死检测

`CronJobExecutor.run_job()` 是一个同步循环，每一"步"调用一次
`submit_step_fn`（底层是一次完整的 `agent.run_turn()`）：

```
while True:
    if now >= deadline:            → 超时收尾（status = timed_out）
    if step_index >= max_steps:    → 超步数收尾（status = timed_out）
    result = submit_step_fn(...)   → 单步异常不让整个 job 崩溃
    if result.error:               → status = needs_human_review，收尾
    if result.done:                → 正常完成（status = idle），收尾
    卡死检测（见下）
```

**"是否完成"的判断**优先级：

1. 输出末尾出现 `[CRON_DONE]` 标记 → 明确完成
2. 输出末尾出现 `[CRON_CONTINUE]` 标记 → 明确未完成，继续
3. 都没出现时，用"本次 `run_turn()` 是否自然结束"兜底：没撞到内层
   `max_turns` 预算就认为完成，被预算打断就认为未完成

这两个标记由 cron 专用 Agent 的 system prompt 里的约定注入（见 §5），
你不需要在自己的 `prompt.md` 里手写这句话，但如果任务确实是"分批处理"
类型，写清楚判断"是否已经全部处理完"的标准会让模型更准确地打对标记。

**卡死检测**复用已有的 `StuckDetector`：连续输出相似度过高 → 先尝试
`RECOVER`（继续但换个角度），恢复次数耗尽后判定 `GIVE_UP`，标记
`needs_human_review` 并停止调度，直到你在看板上手动重置。

这是"内层限步数（`max_turns`）、外层限墙钟时间+步数
（`timeout_seconds`/`max_steps`）"的双重兜底：单次 `run_turn()`
调用本身不会无限跑，即使某一步异常复杂也会先撞到内层预算，把控制权
交还给外层循环。

## 5. cron 专用 Agent

每次 job 触发都重新构建一个全新的 Agent（`cron_agent_bridge.
build_cron_agent()`），不跨触发复用同一个 Agent/history：

- **全量继承主 Agent 的工具集**（不做工具白名单裁剪，按用户明确要求）
- `auto_approve=True`：无人值守场景，工具调用自动批准
- system prompt 会自动追加一段说明当前是 daemon 后台定时任务身份、
  无法等待人类澄清、要求在最后一行输出 `[CRON_DONE]` 或
  `[CRON_CONTINUE]` 的约定
- 单次 `run_turn()` 内部的 `max_turns` 预算默认 15（可通过全局
  `cron.inner_max_turns` 配置调整，见 §7）

"上次做到哪了"的连续性**不是**靠保留 Agent 对象或完整对话历史实现的，
而是靠 §7 的 `progress_summary` 文本摘要拼进下一次触发的 prompt——这样
可以避免 cron 任务的历史无限增长，也避免和用户会话的 session 存储混
在一起。如果某类任务确实需要精细的多轮上下文，目前只能靠这段摘要
本身写得足够详细；更结构化的 checkpoint 机制尚未实现（见实施记录的
「剩余工作」）。

## 6. 每个 job 的专属文件夹

```
.agent/cron_jobs/<job_id>/          # job_id 里的 ':' 替换成 '_' 做目录名
├── prompt.md      用户可编辑，下次触发立即生效，无需重启 daemon
├── config.json    单 job 的超时/步数/卡死检测阈值覆盖
├── state.json     跨次启动持久化：status/progress_summary/
│                  consecutive_failures/last_error/last_run_id
└── runs/
    └── <run_id>.jsonl   单次执行的逐步事件流
```

首次触发时自动创建，已存在的文件不会被覆盖（你手动编辑过的 prompt
不会被"重置"）。

### 6.1 `prompt.md` 支持的占位符

```
{{task_description}}         cron_jobs.json 里配置的 task_template
{{progress}}                 上次执行遗留的进度摘要（首次为空字符串）
{{#progress}}...{{/progress}}  条件块：progress 为空时整段连同标记
                                一起去掉，避免每次都印出一段空的
                                "上次进度"标题
{{pending_answers}}          用户已回答但还没喂给过 agent 的问答对
                                （由 ask_user_async 提出），见
                                cron-async-user-feedback-guide.md §5
{{unanswered_questions}}     该 job 下仍未回答的问题列表，提醒 agent
                                不要重复提问同一个问题
```

`{{pending_answers}}`/`{{unanswered_questions}}` 是 cron 任务异步用户
反馈机制新增的两个占位符，同样复用条件块机制（为空时整段隐藏），详见
[cron-async-user-feedback-guide.md](cron-async-user-feedback-guide.md)。

默认模板：

```
{{task_description}}

{{#progress}}
--- 上次执行遗留的进度 ---
{{progress}}
请从上述进度继续，不要从头重新开始。
{{/progress}}
```

正常完成后 `progress_summary` 会被清空；超时/卡死时保留最后一步输出
（截断到 2000 字）供下次续接或人工查看。

### 6.2 `config.json`：单 job 覆盖

首次触发时自动创建，内容是**空 JSON 对象 `{}`**——不预写任何全局默认值
快照。只有你（通过看板的"⚙️ 调整执行配置"面板，或直接调用
`PUT /v1/cron/jobs/{id}/config`）显式设置过的字段才会出现在这个文件里：

```json
{
  "timeout_seconds": 1800,
  "max_steps": 40
}
```

（上面这个例子里只有 `timeout_seconds`/`max_steps` 被用户显式覆盖过，
`stuck_similarity_threshold`/`stuck_consecutive_limit`/`stuck_max_recoveries`
仍然跟随全局默认。）

字段缺省时回退全局默认值（见 §7）——**这个回退是每次读取都会重新
计算的**，也就是说改一次全局配置，所有没有在自己 `config.json` 里
显式写这个字段的 job，下次触发立即跟着变化，不需要逐个 job 手动改
文件，也不需要跑迁移脚本。已经在 `config.json` 里显式写过的字段不受
全局配置变化影响（这是你主动覆盖的值，理应保留）。

> **早期版本的已知问题（已修复）**：在这次修复之前，`ensure()` 会在
> job **首次创建** `config.json` 时把调用方按当时全局配置拼出来的值
> （比如刚好等于 `cron.default_timeout_seconds`）整份写死进文件，
> 这份"快照"从此被当成了"用户显式覆盖"，导致之后再调整全局
> `default_timeout_seconds` 对已经存在的 job 完全不生效，必须手动改
> 每个 job 自己的 `config.json` 才能生效——这跟本节开头描述的"回退是
> 每次读取都会重新计算的"设计意图是矛盾的。现在 `ensure()` 不再写入
> 任何快照，`config.json` 首次创建时是空对象，只有用户主动通过
> `write_config_overrides()`（对应 §9.1 的 API/UI）设置过的字段才会
> 持久化，从根本上避免这个问题复现。

#### 6.2.1 手动改文件 vs 通过 API/看板改

直接编辑 `config.json` 文件仍然可以（比如批量脚本场景），效果和调用
`PUT /v1/cron/jobs/{id}/config` 完全等价——两者读写的是同一份文件，
没有哪个是"更权威"的入口。看板/API 只是提供了带校验、带"恢复为全局
默认"一键清除覆盖能力的更友好操作方式，具体见 §9.1。

### 6.3 `state.json`：执行状态机

| 状态 | 含义 |
|---|---|
| `idle` | 从未运行过 / 上次正常结束 |
| `running` | 当前正在执行（用于检测"上次异常退出、state 还留在 running"的僵尸状态） |
| `needs_human_review` | `StuckDetector` 判定 `GIVE_UP`，或单步执行异常，或 Agent 构造失败 |
| `timed_out` | 上次因触达硬超时/步数上限被收尾（不算失败，下次会带着进度继续） |
| `waiting_feedback` | 本次本来正常完成，但通过 `ask_user_async` 提出的问题仍有未回答的——**不计入** `consecutive_failures`，语义是"等一个具体问题的答案"而非"卡死放弃"，详见 [cron-async-user-feedback-guide.md §4](cron-async-user-feedback-guide.md#4-任务状态如何体现等反馈中不算失败) |

## 7. 全局默认配置

> 本节讲的是 cron 通道自身的分级响应/记账/仲裁配置；三条执行通道
> （Goal/普通 cron/goal_cycle）如何共享同一份资源仲裁结果的完整设计与
> 分阶段状态，见 [Goal/Cron 统一调度层指南](unified-scheduler-guide.md)。

`agent_config.json` 里可选的 `"cron": {...}` 块：

```json
{
  "cron": {
    "max_concurrent_jobs": 2,
    "default_timeout_seconds": 1200,
    "default_max_steps": 60,
    "inner_max_turns": 15,
    "stale_job_watchdog_grace_seconds": 300,
    "degraded_max_concurrent": 1,
    "skip_alert_threshold": 5,
    "reserved_min_concurrent": 1,
    "circuit_breaker_distinct_threshold": null
  }
}
```

| 字段 | 默认值 | 作用 |
|---|---|---|
| `max_concurrent_jobs` | 2 | `CronJobRunner` 的并发上限（见 §3） |
| `default_timeout_seconds` | 1200（20 分钟） | 单个 job 自己的 `config.json` 里没有显式覆盖 `timeout_seconds` 时的回退来源（见 §6.2，实时生效，不是"新建时写死一次"）；同时也是 §3.3 watchdog 计算"有效超时阈值"的基准之一 |
| `default_max_steps` | 60 | 同上 |
| `inner_max_turns` | 15 | cron 专用 Agent 单次 `run_turn()` 内部的 `max_turns` 预算（见 §5） |
| `stale_job_watchdog_grace_seconds` | 300（5 分钟） | §3.3 watchdog 判定"job 已卡死"时，在 job 自己的 `timeout_seconds` 之上再加的宽限期 |
| `degraded_max_concurrent` | 1 | §3.1 resource arbiter 判定为 `degraded` 时，cron 通道临时收紧到的并发上限（`scheduler.unified_arbitration_enabled=True` 时改由 §7.1 的加权分配接管，本字段退化为兜底值） |
| `skip_alert_threshold` | 5 | §3.2 `consecutive_skip_count` 达到该阈值时触发一次告警 |
| `reserved_min_concurrent` | 1 | 仅 `scheduler.unified_arbitration_enabled=True` 时生效：degraded 状态下 cron 通道保证能分到的最少槽位数（见 §7.1） |
| `circuit_breaker_distinct_threshold` | `null`（不启用） | §3.4 跨 job 广度熔断的判定阈值 |

不配置这一块时，所有字段使用上表的硬编码默认值。

### 7.1 跨通道总并发上限（`scheduler.max_total_concurrent_tasks`）

`max_concurrent_jobs`（本节）与 Goal Objective 通道的
`autonomy.max_concurrent_objectives_cap`（默认 2，可通过 agent_config.json
配置或看板热改，没有硬天花板）是两条**完全独立**的
并发上限——正常（非 degraded）状态下互不感知，默认配置下系统里最多可能
同时有 2（Objective）+ 2（cron）= 4 个任务在跑，看板顶栏"daemon 正在
执行 N 项任务"里 N 超过单条通道上限（比如同时看到 3 个）就是这么来的，
不是 bug，是"两条通道各自独立限流、彼此不感知对方"这个设计现状的直接
体现。

如果需要一个真正跨通道的**总**并发天花板，在 `agent_config.json` 里配置
`scheduler.max_total_concurrent_tasks`（默认 `null`，不生效）：

```json
{
  "scheduler": {
    "max_total_concurrent_tasks": 2
  }
}
```

设置后，`ObjectiveExecutor`/`CronJobRunner` 的 `effective_max_concurrent()`
都会在各自原有上限（`max_concurrent_objectives_cap`/`max_concurrent_jobs`，
以及 degraded 状态下更低的收紧值）基础上，再 clamp 到
`max(0, max_total_concurrent_tasks - 对方通道当前运行数)`——任意时刻
Objective + cron job 的运行总数不会超过这个值。始终"只降不升"：不配置
时两条通道继续各走各的独立上限，与改造前完全一致。详见
`next_doc/goal_execution_scheduling_global_cap_bugfix.md`。

### 7.2 degraded 时的加权槽位分配（`scheduler.unified_arbitration_enabled`）

§7.1 的 `max_total_concurrent_tasks` 解决的是"任意时刻总数不超过 N"，
但没有解决"degraded 时这 N 个槽位应该怎么在两条通道之间分配"——默认
是两条通道各自独立收紧到自己的 `resource_gating_degraded_max_concurrent`/
`cron.degraded_max_concurrent`，互不感知对方的权重。

如果需要 degraded 时按权重统一分配（比如"Goal 通道更重要，degraded 时
优先保 Goal，但 cron 至少留一个槽位不能完全饿死"），开启
`scheduler.unified_arbitration_enabled`：

```json
{
  "scheduler": {
    "unified_arbitration_enabled": true,
    "degraded_total_slots": 2,
    "channel_weights": {"goal": 1.0, "cron": 1.0}
  },
  "cron": {
    "reserved_min_concurrent": 1
  }
}
```

开启后，degraded 状态下 `ObjectiveExecutor`/`CronJobRunner` 的
`effective_max_concurrent()` 都改由 `UnifiedTaskScheduler.
allocate_weighted_slots()` 按 `channel_weights` 对 `degraded_total_slots`
做加权分配，同时保证 cron 通道不低于 `cron.reserved_min_concurrent`。
默认关闭（`false`），关闭或计算异常时都退回 §3.1/§7.1 描述的独立裁决，
不影响现有行为。看板"🕹️ 统一调度总览"区块（见 §9）会展示当前是否
开启、权重配置、以及（degraded 时）实际算出的槽位分配，不需要翻配置
文件确认生效与否。只读预览可另见
`GET /v1/self/unified_scheduler_preview`。

## 8. REST API

```
GET   /v1/cron/jobs/{id}/workspace   state + config + 最近执行列表
GET   /v1/cron/jobs/{id}/prompt      读 prompt.md
PUT   /v1/cron/jobs/{id}/prompt      改 prompt.md（Body: {"prompt": "..."}）
PUT   /v1/cron/jobs/{id}/config      改这个 job 专属的超时/步数/卡死检测覆盖
                                      （见 §6.2/§8.1，Body 里字段传 null 表示
                                      清除覆盖、恢复跟随全局默认）
GET   /v1/cron/jobs/{id}/runs/{run_id}  某次执行的完整事件流
POST  /v1/cron/jobs/{id}/reset       needs_human_review → idle（正在执行中的
                                      job 拒绝重置，返回 409）
```

job 基础的增删改查（`GET/POST /v1/cron/jobs`、`PUT /v1/cron/jobs/{id}`、
`POST /v1/cron/jobs/{id}/run`）不是本机制新增的，见
[HTTP API 指南](http-api-guide.md#v1cronjobs--cron-job-rest-api)；`priority`
（见 §3.2）作为 `CronJob` 的普通字段，通过同一组增删改查接口读写
（`GET /v1/cron/jobs` 响应里每个 job 自带 `priority`/`consecutive_skip_count`/
`is_system`/`execution_phase`（`not_running`/`queued`/`running`，见 §3），
`PUT /v1/cron/jobs/{id}` 支持传 `priority` 字段更新），没有单独的
`/priority` 子路由。

本机制的执行状态也汇总在两个只读聚合端点里，不需要逐个 job 翻
`/workspace`：

```
GET /v1/self/execution_model_status     cron.reaped_job_count / cron.arbiter_skipped_count
                                         等跨 job 的运行时计数（见 §3.2/§3.3）
GET /v1/self/scheduling_overview        cron_channel.running/queued/max_concurrent/
                                         arbiter_skipped_count/jobs_over_skip_threshold，
                                         以及 goal_cycle_channel 与三条通道共享的
                                         ResourceArbiter 仲裁结果（见 §9）
GET /v1/self/unified_scheduler_preview  §7.2 加权分配的只读预览（是否开启、
                                         权重、当前会算出的槽位分配）；
                                         goal 通道每个任务的 resource_estimate
                                         另外反映该 Goal 的执行阶段（见
                                         [Goal 执行阶段指南](goal-execution-phase-guide.md#调度联动阶段感知的资源估算只读预览)），
                                         仍是纯诊断展示，未接入 §7.2 的实际
                                         槽位分配计算
```

`GET /v1/cron/jobs/{id}/workspace` 响应示例：

```json
{
  "job_id": "user:ab12cd34",
  "state": {
    "status": "idle",
    "progress_summary": "",
    "last_step_index": 3,
    "consecutive_failures": 0,
    "last_run_started_at": 1720000000.0,
    "last_run_finished_at": 1720000180.0,
    "last_run_id": "2026-07-20T09-00-00",
    "last_error": ""
  },
  "config": {
    "timeout_seconds": 1200,
    "max_steps": 60,
    "stuck_similarity_threshold": 0.92,
    "stuck_consecutive_limit": 3,
    "stuck_max_recoveries": 2
  },
  "config_overrides": {},
  "config_global_default": {
    "timeout_seconds": 1200,
    "max_steps": 60,
    "stuck_similarity_threshold": 0.92,
    "stuck_consecutive_limit": 3,
    "stuck_max_recoveries": 2
  },
  "is_running": false,
  "recent_runs": ["2026-07-20T09-00-00", "2026-07-19T09-00-00"],
  "recent_runs_summary": [
    {
      "run_id": "2026-07-20T09-00-00",
      "started_at": 1720000000.0,
      "finished_at": 1720000180.0,
      "status": "success",
      "raw_status": "idle",
      "success": true,
      "error": "",
      "steps_executed": 3,
      "duration_seconds": 180.0
    },
    {
      "run_id": "2026-07-19T09-00-00",
      "started_at": 1719913600.0,
      "finished_at": 1719913605.0,
      "status": "failed",
      "raw_status": "needs_human_review",
      "success": false,
      "error": "LLM API 超时",
      "steps_executed": 1,
      "duration_seconds": 5.0
    }
  ],
  "execution_channel": "dedicated_workspace"
}
```

`config` 是与全局默认合并后的**实际生效值**；`config_overrides` 是这个
job 自己 `config.json` 里显式设置过的原始字段（未合并，空对象表示这个
job 至今没有任何自定义覆盖，`config` 里展示的每个值都直接来自
`config_global_default`）；`config_global_default` 是按当前
`AppConfig.cron` 实时算出来的全局默认值，三者对照即可判断"这个数字
是用户自己设的，还是跟着全局配置走的"，看板 §9.1 的"⚙️ 调整执行配置"
面板就是靠这三个字段渲染每个输入框旁边的"已自定义"/"跟随全局默认"
标注。

`recent_runs` 只是 run_id 列表（旧字段，保留向后兼容）；`recent_runs_summary`
（新增）逐条给出是否成功（`status`/`success`）与失败原因（`error`），由
`CronJobWorkspace.recent_runs_summary()` 从对应 `runs/<run_id>.jsonl`
事件流里提取——`status` 取值：`success`（正常完成）/ `timed_out`（触达
超时或 max_steps 上限，不算成功）/ `failed`（`needs_human_review` 或
其它异常状态）/ `crashed_or_running`（没找到 `run_finished` 事件，进程
可能异常退出或仍在跑）。看板"⏰ Cron 任务" Tab 的"最近执行记录"直接展示
这份摘要（时间 + 状态角标，失败时额外展示 `error` 文本），不用逐条点开
事件详情才知道哪次跑失败了。

`execution_channel`（新增，`CronScheduler.execution_channel()` 计算，
只读、不影响任何调度行为）标注这个 job 到期时**实际会走哪条执行路径**，
只有一种取值会产生上面这两个执行记录字段：

| 取值 | 含义 | 会产生 `recent_runs`/`recent_runs_summary` 吗 |
|---|---|---|
| `dedicated_workspace` | 走本文档描述的独立线程 + `CronJobWorkspace` 记录链路（`job_runner` 已注入，且不属于下面两类） | ✅ 会 |
| `local_handler` | 注册过 `register_local_handler()` 的"本地回调"job（零 LLM 成本、确定性逻辑，比如 `sys:watchlist_report_<tier_id>`、`sys:wiki_quarantine_repair` 等），触发时在原进程内直接同步执行 | ❌ 不会——但 `CronJob.run_count` 照常累加 |
| `goal_cycle` | `run_mode="goal_cycle"` 的 job，走 Objective 执行通道，执行记录去对应 Goal 详情里看 | ❌ 不会——但 `CronJob.run_count` 照常累加 |
| `message_queue` | `job_runner` 未注入时的旧回退路径（消息直接进 `InputQueue`，当普通 turn 处理） | ❌ 不会——但 `CronJob.run_count` 照常累加 |

看板在 `recent_runs_summary`/`recent_runs` 都为空时，会按 `execution_channel`
的取值给出针对性说明（见 §9.1），而不是笼统地提示"触发过至少一次后
这里会显示"——`local_handler`/`goal_cycle`/`message_queue` 这三种通道
无论触发多少次都不会在这里出现记录，这是设计使然，不是 bug。

### 8.1 `PUT /v1/cron/jobs/{id}/config`：修改单 job 专属限制覆盖

Body 支持以下字段的任意子集（对应 §6.2 `config.json` 里的可覆盖字段）：

| 字段 | 校验规则 |
|---|---|
| `timeout_seconds` | 数值，> 0 |
| `max_steps` | 整数，> 0 |
| `stuck_similarity_threshold` | 数值，0 < x ≤ 1 |
| `stuck_consecutive_limit` | 整数，≥ 1 |
| `stuck_max_recoveries` | 整数，≥ 0 |

某个字段传具体数值 = 显式设置这个覆盖值（持久化进 `config.json`，从此
不再跟随全局默认变化，直到你再改它或清除覆盖）；传 `null` = 清除这个
字段的覆盖，恢复跟随全局 `cron.default_timeout_seconds` 等默认值；不
出现在 body 里的字段维持原样不动。下次该 job 触发时立即生效，不需要
重启 daemon。

```bash
# 只把超时时间改成 30 分钟，其它字段不动
curl -X PUT $BASE/v1/cron/jobs/user:ab12cd34/config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"timeout_seconds": 1800}'

# 恢复超时时间跟随全局默认
curl -X PUT $BASE/v1/cron/jobs/user:ab12cd34/config \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"timeout_seconds": null}'
```

响应：`{"job_id": ..., "config_overrides": {...}, "config": {...}}`
（同样是"原始覆盖"+"合并后生效值"的组合，语义与上面 workspace 响应里
的两个字段一致）。

后台线程执行完一次后通过 `AgentBridge.emit_cron_job_finished()` 推
`CRON_JOB_FINISHED` SSE 事件，实时打开着看板的用户不需要手动刷新。

## 9. 看板：相关 Tab 一览

本机制的执行细节分散在看板的几个不同 tab/区块里，按"看单个 job 的
执行细节"还是"看整体调度状况"分开：

### 9.1 "⏰ Cron 任务" Tab（单 job 视角）

`apps/mini_agent_kanban/app.py` 的 "⏰ Cron 任务" tab 提供：

- 状态徽标（空闲 / 排队中 / 执行中 / 需人工介入 / 上次超时），排队中
  和执行中分开展示（§3 `execution_phase`），不再混为一谈
- 连续失败次数、`consecutive_skip_count`（连续因仲裁被跳过的次数，
  达到 `cron.skip_alert_threshold` 时有专门标注，见 §3.2）
- 优先级展示 + "🔢 调整优先级" 展开面板（对应 §3.2 的 `priority` 字段，
  影响同一 tick 内多个到期 job 的提交顺序）
- "⚙️ 调整执行配置（超时 / 步数 / 卡死检测）" 展开面板（对应 §6.2/§8.1
  的 `config.json` 覆盖）：每个输入框旁标注"已自定义"还是"跟随全局
  默认"（依据 §8 workspace 响应里的 `config_overrides`），预填的是
  当前实际生效值；"💾 保存为自定义配置"把当前表单值整体固化为这个 job
  自己的显式覆盖，"↩️ 恢复为全局默认"一键清除全部覆盖字段，之后这个
  job 会重新跟随全局 `cron.default_timeout_seconds` 等配置实时变化
- 进度摘要展开查看
- 最近执行记录：每条直接展示时间 + 成功/超时/失败/未知状态角标，失败
  时额外展示失败原因文本，不用点开才知道；仍可展开对应
  `runs/<run_id>.jsonl` 的逐步事件详情
- 空记录时的针对性说明：没有任何执行记录时，不再是笼统的"触发过至少
  一次后这里会显示"——按 `execution_channel`（见 §8）区分文案：
  `local_handler` 类会说明"这是本地回调任务，不产生执行记录，但累计
  运行次数正常累加"；`goal_cycle` 类会提示去对应 Goal 详情里看；
  `message_queue` 类会提示当前部署未启用独立执行通道
- `prompt.md` 在线编辑保存
- `needs_human_review` 状态下的一键重置按钮
- "➕ 新建 cron job" 表单：提交前会对 `schedule` 字段做格式前置校验
  （`interval:<秒数>` 或 `cron:<5 字段表达式>`），格式明显不对时直接
  在表单内提示，不会发起后端请求；可选填写初始 `priority`

### 9.2 "🕹️ 统一调度总览" 区块（跨通道视角）

聚合 Goal Objective / 普通 cron / goal_cycle 三条执行通道当前的运行、
排队、跳过状态，以及三者共享的 `ResourceArbiter` 仲裁结果，一次性
展示，不需要在多个面板之间来回切换拼图：

- 当前仲裁状态（full/degraded/blocked）+ 原因
- **当前调度模式**：统一仲裁（§7.2）/自适应并发/degraded 收紧并发
  三个开关各自是否开启，开启统一仲裁时展示 `channel_weights` 和
  （degraded 时）实际算出的槽位分配
- 今日预算消耗分项（Goal/cron/探索沙盒三类各自消耗多少，cron 的消耗
  见 §3.2 提到的 token 记账）
- Goal 通道：并发槽位（运行中/当前上限）+ 静态上限；公平排序队首
- cron 通道：运行中/排队中计数、**当前并发上限**（§3.1 收紧后的实际
  值，与静态上限不同时会额外标注）、仲裁累计跳过次数、连续跳过超阈值
  的 job 列表
- goal_cycle 通道：待触发数/总数、最近触发记录

### 9.3 "🗓️ 全局日程" Tab

未来 24 小时内到期的 cron job（含 priority）+ recurring Goal 的下次
触发时间 + 仲裁状态变化时间线，按时间顺序合并展示成一条时间线。

看板的通用说明见 [看板指南](kanban-dashboard-guide.md)。

## 10. 排查指南

| 现象 | 排查方向 |
|---|---|
| job 到期了但一直没执行（用户自定义 job） | 先看"🕹️ 统一调度总览"的仲裁状态是不是 `blocked`——非 `sys:` job 在 `blocked` 时会被直接跳过触发（§3.2），不是排队问题；再检查 `max_concurrent_jobs`/`effective_max_concurrent()` 是否被占满（看板 `execution_phase=queued` 列表）；检查该 job 上一次是否还在跑（同 job 去重会拒绝并发触发） |
| `sys:` 系统维护 job 到期了但一直没执行 | 不受仲裁影响（§3.2），只可能是并发槽位被占满或该 job 自己上次还没跑完，同上一行后半部分排查 |
| 并发上限忽然从 2 变成 1，job 排队变久 | 仲裁状态处于 `degraded`（§3.1），查"🕹️ 统一调度总览"的原因；开了 `scheduler.unified_arbitration_enabled` 时实际上限由 §7.2 的加权分配决定，不一定是固定的 `cron.degraded_max_concurrent` |
| `consecutive_skip_count` 持续增长、收到"连续跳过超阈值"告警 | 该 job 长期处于 `blocked` 仲裁状态下到期——检查预算是否持续耗尽、或 frustration 阈值配置是否过低；这是 §3.2 的 `skip_alert_threshold` 机制在正常工作，不是 bug |
| 状态卡在 `needs_human_review` | 打开该 job 的最近一次 `runs/<run_id>.jsonl`，看最后几条 `step`/`stuck_recover`/`stuck_give_up`/`step_error` 事件；确认原因后在看板点"重置"或调用 `POST /v1/cron/jobs/{id}/reset`；如果 `last_error` 里提到"判定为卡死…已被 watchdog 强制回收"，说明是 §3.3 的存活性回收触发的，可以按需调大该 job 的 `timeout_seconds` 或全局 `stale_job_watchdog_grace_seconds` |
| 状态卡在 `running` 但看板显示未在执行 | daemon 异常退出导致的僵尸状态，不影响下次触发（下次执行会记一次 `consecutive_failures` 但仍会正常继续执行），也可以手动 `reset` 清掉；如果 daemon 一直在跑但某个 job 长时间卡在 `running`，正常情况下 §3.3 的 watchdog 会在超时+宽限期后自动回收，不需要手动介入 |
| 任务每次都从头开始，没有接续上次进度 | 检查 `prompt.md` 是否还保留 `{{#progress}}...{{/progress}}` 块（被用户误删就不会拼进度了）；检查上次是不是 `idle` 正常完成（正常完成会清空 `progress_summary`，这是预期行为，不是 bug） |
| 想让所有 job 的超时时间统一改长一点 | 改 `agent_config.json` 的 `cron.default_timeout_seconds` 即可，对没有在自己 `config.json` 里显式覆盖过该字段的 job 立即生效（见 §6.2/§7），不需要逐个改文件；如果某个 job 之前手动/通过看板设置过自己的 `timeout_seconds` 覆盖，需要在看板"⚙️ 调整执行配置"面板里点"恢复为全局默认"（或把 `config.json` 里的对应字段删掉）才会重新跟随全局值，注意同时会影响 §3.3 watchdog 的有效超时阈值 |
| 只想给某一个 job 单独调超时/步数，不想影响其它 job | 看板该 job 卡片里展开"⚙️ 调整执行配置"，改好数值点"保存为自定义配置"，或直接调 `PUT /v1/cron/jobs/{id}/config`（见 §8.1）——不会影响全局默认，也不会影响其它 job |
| 新建 job 提示 schedule 格式不合法 | 按提示修正为 `interval:<秒数>`（如 `interval:3600`）或 `cron:<分> <时> <日> <月> <周>`（如 `cron:0 22 * * *`），字段支持 `*`/`*/n`/`n`/`n,m`/`n-m` |
| 多个 job 同时到期，想让某个 job 优先跑 | 在看板给该 job 调高 `priority`（§3.2），数值越大越优先；只影响同一 tick 内的提交顺序，不会抢占正在执行的其它 job |
| 某个第三方 API 挂了，多个不相关的 job 陆续失败 | 查是否收到"检测到跨 cron job 的系统性失败"告警（§3.4 广度熔断），需要先在 `agent_config.json` 显式配置 `cron.circuit_breaker_distinct_threshold`（默认不启用）才会触发 |
| 某个 job 的"累计运行次数"不是 0，但"执行记录"列表却是空的 | 不是 bug——查该 job 详情里的空记录提示文案，或直接看 `GET .../workspace` 响应的 `execution_channel` 字段（见 §8）：`local_handler`（本地回调，比如 `sys:watchlist_report_<tier_id>`）和 `goal_cycle`（走 Objective 通道，去对应 Goal 详情查）这两类 job 设计上就不产生这里的执行记录，但触发成功时 `run_count` 照常累加，两者不冲突，也不会"等下就有了" |

## 11. 已知局限

- cron 任务的 Agent 每次触发都重新构建，不跨触发保留完整对话历史——
  连续性完全依赖 `progress_summary` 这一段文本摘要，复杂的多轮上下文
  可能会在摘要压缩中丢细节。更结构化的 checkpoint 机制（`state.json`
  里存自由格式的 `checkpoint_data`）尚未实现，属于按需再做的可选项。
- 全量继承主 Agent 工具意味着 cron 任务和主 Agent、SubAgent 共用同一份
  全局 `ToolRegistry` 单例；这是本代码库里已经被 SubAgent 并发验证过的
  既有模式（各自的 thread-local 状态按"构造 Agent 的线程"隔离），
  `CronJobRunner` 保证 Agent 在专属的 cron 执行线程内构造并运行、不
  跨线程，所以是安全的。
- `[CRON_DONE]`/`[CRON_CONTINUE]` 标记依赖模型遵循 system prompt 里的
  约定，如果模型没有输出任何标记，退化到"是否自然结束"兜底判断，存在
  极少数"模型自然说完话但其实任务没做完"被误判为完成的可能——这属于
  "用文本约定代替结构化协议"的固有权衡，可接受。
- §3.3 的 watchdog 只能强制回收记账状态（槽位、`_running_job_ids`），
  无法真正杀死已经卡死的 Python 线程——那条孤儿线程会继续占用内存/
  连接直到自然结束或进程重启，watchdog 解决的是"卡死不影响其它 job
  调度"，不是"立即释放被卡死线程占用的系统资源"。
- §3.4 的跨 job 广度熔断默认关闭（`circuit_breaker_distinct_threshold=
  None`），且只做告警、不阻断调度——需要用户自己配置阈值并根据告警
  手动介入排查，不会自动暂停出问题的 job。
