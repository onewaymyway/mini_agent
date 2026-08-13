# Goal / Cron 统一调度层指南

对应设计与实施记录：`next_doc/goal_cron_unified_scheduler_improvement_plan.md`
（P0-P5）、`next_doc/goal_cron_unified_scheduler_implementation_record.md`。

## 1. 背景：三条通道，一份共享资源

daemon 内部实际有三条相互独立、但共享同一份底层 LLM 资源的执行通道：

1. **Goal → Objective**：`ObjectiveExecutor` 按公平轮询/老化补偿从
   `GoalBacklog` 挑选待执行的 Objective。
2. **普通 cron job**（`run_mode="message"`）：`CronScheduler.tick()` 到期后
   交给 `CronJobRunner` 独立执行。
3. **goal_cycle cron**（Goal 绑定的周期性执行，见
   [Goal/Cron 绑定指南](goal-cron-binding-guide.md)）：同样经
   `CronScheduler.tick()` 触发，但最终转发进 `ObjectiveExecutor`，复用
   通道 1 的并发。

三条通道过去各自实现了一部分"资源仲裁响应"逻辑，本指南描述的
`UnifiedTaskScheduler` 就是为了逐步把这些分散的判断收敛到一处，采用
**分步迁移**而不是一次性重写——每一步都可以独立观察效果，允许长期与
旧路径并存。

## 2. 现状总览（各阶段完成度）

| 阶段 | 内容 | 状态 |
|---|---|---|
| P0 | cron 对 `degraded`/`blocked` 分级响应（不再一刀切跳过） | 已完成 |
| P1 | cron 消耗统一记账（`used_today_cron`） | 已完成 |
| P2 | cron 连续跳过追踪与告警 | 已完成 |
| P3 | `tick()` 执行看门狗 | 已完成 |
| P4 | 统一调度可观测面板（后端 + 看板 UI） | 已完成 |
| P5 第 1-2 步 | 定义统一接口 + 三通道只读适配 + 排序预览 | 已完成 |
| P5 第 3 步 | 接管仲裁裁决（degraded 并发分配） | 已完成 |
| P5 第 4 步 | 接管实际派发 | cron/goal_cycle 已完成；**Goal 通道未实现** |
| P5 第 5 步 | 收敛到统一入口 | 已启动（子集：`_tick_passive()` 灰度开关） |

以下各节按功能而不是完成阶段组织。

## 3. cron 的分级响应与记账（P0/P1）

`ResourceArbiter.gating_state()` 给出 `full`/`degraded`/`blocked` 三态。
改造前普通 cron 通道对 `degraded` 的响应是"整体跳过"，与 Goal 通道
"收紧并发但不停摆"的响应方式不一致；现在两者对齐：

- `degraded`：`CronJobRunner` 把并发上限收紧到 `cron.degraded_max_concurrent`
  （默认 `1`），但仍会触发到期的 job，不整体跳过。
- `blocked`：行为不变，到期 job 跳过、下次 tick 重试。

同时 cron 自己的消耗现在会计入 `used_today_cron`（不再只有 Goal 消耗才
会把日预算打满），`gating_state()` 的 `reason` 文案里会区分是
`used_today_goals`/`used_today_cron`/`used_today_exploration` 哪一项
占比触发的限流。

## 4. cron 连续跳过追踪与告警（P2）

每个 `CronJob` 有 `consecutive_skip_count` 字段：到期未能成功触发时
+1，成功触发一次清零。超过 `cron.skip_alert_threshold`（默认 `5`）时，
通过通知系统发一条告警（"cron job X 已连续 N 次到点未能触发"），且只在
**跨越阈值那一刻**发一次，不重复刷屏；之后再次连续跳过会重新从零累积。

看板 Cron 面板会展示每个 job 的 `consecutive_skip_count`，非零时高亮。

## 5. tick() 执行看门狗（P3）

`tick()` 内部约定"只做决策 + 提交，不做耗时调用"，过去这只是代码注释里
的君子协定。现在有一个独立看门狗检测：若当前正卡在一次未返回的
`tick()` 里且已超过 `2 * tick_interval_seconds` 仍未结束，会触发一次告警
并在 `execution_model_status` 里标记 `heartbeat_suspected_stuck: true`，
供看板醒目提示，不必再点开面板细看时间戳才能发现。

## 6. 只读观测端点（P4/P5 第 1-2 步）

### `GET /v1/self/scheduling_overview`（P4）

聚合展示"这一刻三条通道各自的运行/排队/跳过状态"，取代此前需要在
`autonomous_status`/`execution_model_status`/`goal_fairness`/cron 面板
之间来回切换才能拼出的全貌。任一子系统数据缺失时对应字段返回占位默认值，
不影响其它字段。返回结构（节选）：

```
{
  "gating": {"state": "full"|"degraded"|"blocked", "reason": str},
  "scheduling_mode": {
    "unified_arbitration_enabled": bool,
    "adaptive_concurrency_enabled": bool,
    "resource_gating_degraded_enabled": bool,
    "channel_weights": {"goal": float, "cron": float} | null,
    "degraded_allocation": {"goal": int, "cron": int} | null
  },
  "usage_breakdown": {
    "daily_token_budget": int, "used_today": int,
    "used_today_goals": int, "used_today_cron": int,
    "used_today_exploration": int
  },
  "goal_channel": {
    "objective_slots": {"running": int, "max": int, "static_cap": int} | null,
    "queue_head_goal": {"goal_id": str, "title": str, "last_scheduled_at": float} | null
  },
  "cron_channel": {
    "running": int, "queued": int,
    "max_concurrent": int | null, "static_max_concurrent": int | null,
    "arbiter_skipped_count": int,
    "jobs_over_skip_threshold": [{"job_id": str, "name": str, "consecutive_skip_count": int}]
  },
  "goal_cycle_channel": {
    "total_count": int, "pending_due_count": int,
    "recent": [{"job_id": str, "goal_title": str, "last_run_at": float,
                "run_count": int, "consecutive_skip_count": int}]
  }
}
```

`scheduling_mode` 一次性回答"到底是哪种调度机制在生效"（此前只能翻配置
文件才知道）。

### `GET /v1/self/unified_scheduler_preview`（P5 第 1-2 步 + 第 3 步预览）

三条通道各自 `poll_due()` 的原始快照 + 一份"建议执行顺序"
（跨通道按 `priority`/`channel_weights` 合并排序）。与
`scheduling_overview` 的区别：后者是聚合计数，本端点是"如果现在要决定
谁先执行，统一调度层会给出什么建议"。**本端点不触发、不影响任何实际
执行，纯读取。**

```
{
  "channels": {
    "goal": [{"source", "task_id", "title", "priority", "due_at",
               "resource_estimate", "extra"}, ...],
    "cron": [...],
    "goal_cycle": [...]
  },
  "suggested_order": [ 同上字段的任务列表，跨通道合并排序 ],
  "slot_allocation": {
    "unified_arbitration_enabled": bool,
    "degraded_total_slots": int,
    "channel_weights": {"goal": float, "cron": float},
    "reserved_min_cron": int,
    "allocation": {"goal": int, "cron": int}
  }
}
```

`goal` 通道每个任务的 `resource_estimate` 会反映该 Goal 当前的执行阶段
（explore/converge/stable/tidy，见
[Goal 执行阶段指南](goal-execution-phase-guide.md#调度联动阶段感知的资源估算只读预览)）——
这仍然是纯诊断展示，未接入下面 §7 的实际槽位分配计算。

`slot_allocation` 展示的是"如果当前处于 `degraded` 状态，
`allocate_weighted_slots()` 会给两条通道分配多少并发槽位"——按当前配置
计算，**与 `unified_arbitration_enabled` 是否真正开启无关**（开关关闭
时这里仍展示"如果开启会怎样"，方便在正式打开开关前先观察计算结果是否
符合预期）。

### 看板 UI

上述两个端点在看板"🧠 自我状态" Tab 内以"🕹️ 统一调度总览"折叠区块呈现，
紧跟在"⚙️ 执行模型"区块之后（详见
[Kanban 看板使用指南](kanban-dashboard-guide.md#-自我状态-tab)）。

## 7. 接管仲裁裁决：`degraded` 状态下的槽位分配（P5 第 3 步）

默认情况下（`scheduler.unified_arbitration_enabled=False`），Goal 通道
与 cron 通道在 `degraded` 状态下各自独立收紧并发上限（分别读
`autonomy.resource_gating_degraded_max_concurrent`/
`cron.degraded_max_concurrent`），互相不感知对方的分配。

打开 `scheduler.unified_arbitration_enabled=True` 后，`degraded` 状态下
两条通道的并发上限改由纯函数 `allocate_weighted_slots()` 按以下配置
统一计算：

| 配置项 | 位置 | 默认值 | 含义 |
|---|---|---|---|
| `scheduler.degraded_total_slots` | `SchedulerConfig` | `2` | `degraded` 时两条通道总共可用的并发槽位数（等于改造前两条通道各自默认上限之和，是"接入但不改变现状"的安全默认值） |
| `scheduler.channel_weights` | `SchedulerConfig` | `{"goal": 1.0, "cron": 1.0, "goal_cycle": 1.0}` | 各通道的相对权重，用于比例分配（`goal_cycle` 复用 goal 通道执行池，目前只影响只读排序预览，不参与本节的实际裁决） |
| `cron.reserved_min_concurrent` | `CronConfig` | `1` | cron 通道保底并发槽位，无论权重算出多少都不会低于这个值 |

`GET /v1/self/scheduling_overview` 的 `scheduling_mode.degraded_allocation`
字段展示当前实际生效的分配结果（仅 `unified_arbitration_enabled=True`
且当前处于 `degraded` 时有值）；`GET /v1/self/unified_scheduler_preview`
的 `slot_allocation` 字段则始终展示"如果按当前配置计算会分配多少"，
与开关是否打开无关，方便提前观察。

**范围边界**：本步骤只接管"`degraded` 状态下两条通道的并发上限"这一
具体决策点，`blocked`/`full` 两态的判定逻辑（`ResourceArbiter.
gating_state()` 本身）不变。

## 8. 接管实际派发（P5 第 4-5 步）

- **cron / goal_cycle 通道**：`CronScheduler` 新增公开入口
  `trigger_job_now(job_id)`（与 `tick()` 共用同一份记账逻辑），
  `CronChannelAdapter`/`GoalCycleChannelAdapter.execute()` 已委托它实现
  真正派发。
- **合并派发入口**：新增纯函数 `dispatch_due_cron_jobs()`，合并 cron/
  goal_cycle 两条通道到期任务后按 priority 统一触发，内部复用上面同一份
  委托链路。配置开关 `scheduler.unified_dispatch_enabled`（默认
  `False`）控制 `AutonomousLoop._tick_passive()` 是否改用这条统一路径
  而不是直接调用 `cron_scheduler.tick()`。由于 `_tick_maintenance()`/
  `_tick_autonomous()` 方法体都以调用 `_tick_passive()` 开头，这个开关
  对 **passive/maintenance/autonomous 三个执行档位同时生效**，不需要
  分别配置。两条路径的到期判断/触发/记账口径完全一致，开关只影响"谁来
  组织触发顺序"。
- **Goal 通道未接入**：`ObjectiveChannelAdapter.execute()` 仍
  `raise NotImplementedError`——Goal 通道的实际派发逻辑（公平排序/
  per-Goal 并发上限/pause 状态检查等）深度耦合 `AutonomousLoop` 自身
  持有的运行时状态，还没有一个类似 `trigger_job_now()` 那样的安全公开
  入口。Goal 通道的实际调度路径完全不受本节任何开关影响。

## 9. 配置速查

```jsonc
{
  "scheduler": {
    "unified_arbitration_enabled": false,   // §7：degraded 槽位是否统一裁决
    "unified_dispatch_enabled": false,      // §8：cron/goal_cycle 是否走统一派发入口
    "degraded_total_slots": 2,
    "channel_weights": {"goal": 1.0, "cron": 1.0, "goal_cycle": 1.0},
    "max_total_concurrent_tasks": null      // 跨通道总并发天花板，默认不生效
  },
  "cron": {
    "degraded_max_concurrent": 1,
    "skip_alert_threshold": 5,
    "reserved_min_concurrent": 1
  }
}
```

所有新增配置默认值都保证"未升级配置的用户行为基本不变或只变得更宽松
（cron 更容易跑）"，符合项目对灰度开关的一贯要求。

## 10. 明确不做的事

- 不改变 `ObjectiveExecutor`/`CronJobRunner` 各自的并发实现细节（信号量
  vs 显式计数、线程 vs 异步）。
- 不改变用户可见的 cron/Goal 配置语义（`priority`、`schedule` 表达式
  等），只调整调度器如何解释和使用这些配置。
- P5 不追求"一次性完成"，允许长期分阶段推进，也允许在观察到收益不明显
  时中止后续步骤——P0-P4 本身都是独立可交付、有正向价值的改进，不依赖
  P5 是否最终完成。

## 相关文档

- [Goal/Cron 绑定指南](goal-cron-binding-guide.md)
- [Goal 执行阶段指南](goal-execution-phase-guide.md)
- [Cron 独立执行链路指南](cron-dedicated-execution-guide.md)
- [Kanban 看板使用指南](kanban-dashboard-guide.md)
- `next_doc/goal_cron_unified_scheduler_improvement_plan.md`（设计与分阶段计划）
- `next_doc/goal_cron_unified_scheduler_implementation_record.md`（实施记录）
