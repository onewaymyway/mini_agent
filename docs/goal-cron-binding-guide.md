# Goal 与 Cron 绑定指南

> 对应设计与实施记录：`next_doc/goal_cron_binding_plan.md` /
> `next_doc/goal_cron_binding_implementation_record.md`
> 前置阅读：[Stage 9 自主运行时指南 · 3. Goal Backlog](self-evolution-stage9-guide.md#3-goal-backlogperceptiongoal_backlogpy)、
> [Stage 9 自主运行时指南 · 5. 定时任务](self-evolution-stage9-guide.md#5-定时任务evolutioncron_schedulerpy)

## 1. 要解决的问题

`GoalBacklog`（Goal/Objective 层级）和 `CronScheduler`（定时任务）此前是完全独立的两套体系：

- 一个 Goal 一旦被标记完成，就真的结束了——没有"每天/每周自动重跑同一个 Goal"这种机制。
- Cron job 触发时只会把 `task_template` 当一条裸消息塞进 `InputQueue`，不知道、也不关心
  这条消息是否对应某个具体的 Goal。
- 用户暂停/放弃一个背后挂着定时任务的 Goal 后，如果没人记得手动 `/cron disable`，
  cron 会继续无脑触发，变成僵尸任务。

本方案让一个 Goal 可以声明"我需要被周期性推进"：绑定后，Cron 到期时会自动为该 Goal
派生并启动新一轮子 Objective，而不是发一条孤立的消息。

## 2. 使用方式

### 2.1 把已有 Goal 声明为周期性

```bash
/agent goals add "持续关注 Agent 和 AI 领域最新技术，维护 research/agent_and_ai 下的技术 wiki"
# 假设新建的 Goal id 是 goal_1a2b3c4d

/agent goals recur goal_1a2b3c4d interval:86400 "搜索最新的 Agent/AI 技术进展，将结构化知识更新到 research/agent_and_ai/ 下的 wiki，接续上一轮 progress_notes 里记录的进度"
```

也可以反过来，从 cron 一侧发起绑定（等价效果）：

```bash
/cron add-goal-cycle goal_1a2b3c4d interval:86400 "搜索最新的 Agent/AI 技术进展..."
```

`task_template` 参数省略时，会复用 Goal 自身的 `description`/`title` 作为每轮任务描述。

### 2.2 停止周期性

```bash
/agent goals unrecur goal_1a2b3c4d
```

只是 disable 绑定的 cron job、把 `Goal.recurring` 置回 `False`，**不会删除 Goal，也不会
删除 cron job**——随时可以再次 `recur` 复用同一个绑定。

### 2.3 观察进度

```bash
/agent goals            # 列表里能看到 Goal 的 cycle_count（已跑过几轮）
/agent goals progress goal_1a2b3c4d "..."   # 也可以手动追加进展备注
/cron list --all        # 能看到绑定的 goal_cycle job 及下次触发时间
```

每一轮子 Objective 进入终态（完成/失败/取消）后，`Goal.progress_notes` 会自动追加一行
形如 `[2026-08-02 10:00] 第 3 轮：<该轮结果摘要前 80 字>` 的记录，方便回看历史轮次。

## 3. 触发规则（重要，决定了"为什么这次没自动跑"）

一个 `run_mode="goal_cycle"` 的 cron job 到期时，按以下顺序检查，**任一条件不满足就
静默跳过，不报错、不消耗触发计数（下次 tick 会再检查一次）**：

1. **Goal 必须是 `active` 状态**。用户 `pause`/`abandon`/`done` 掉 Goal 之后，
   即便绑定的 cron job 还是 enabled，也不会再触发——这是刻意设计，避免你需要额外记得
   去 `/cron disable`。
2. **autonomy_level 不能是 `passive`**。周期性 Goal 的自动续期本质上是一种自主行为，
   只有守护进程处于 `maintenance` 或 `autonomous` 档位时才会生效
   （见 [Stage 9 自主运行时指南 · 4.1 三档位](self-evolution-stage9-guide.md#41-三档位完整行为)）。
   `passive` 档位下 cron 的其余（`sys:` 前缀的）维护任务仍会正常运行，只是 goal_cycle
   不会。
3. **上一轮不能还在跑**。如果这一轮的子 Objective 还处于执行中，不会叠加开第二轮，
   要等上一轮进入终态才会开始下一轮——即便到了下一次 schedule 触发点也会先跳过。

## 4. 数据结构变化（供二次开发参考）

`GoalNode`（`perception/goal_backlog.py`）新增字段：

| 字段 | 说明 |
|---|---|
| `recurring` | 是否已绑定一个 `run_mode="goal_cycle"` 的 CronJob |
| `recurrence_cron_job_id` | 反向指针，指回绑定的 `CronJob.id` |
| `cycle_count` | 已完成（含失败）的周期数 |
| `reaped_cycle_child_ids` | 已被计过数的子 Objective id 集合，避免重复计数 |

`CronJob`（`evolution/cron_scheduler.py`）新增字段：

| 字段 | 说明 |
|---|---|
| `goal_id` | 绑定的 GoalNode.id |
| `run_mode` | `"message"`（默认，裸消息投递） \| `"goal_cycle"`（驱动 Goal 周期） |

核心逻辑在新增模块 `evolution/goal_cron_bridge.py`：

- `register_goal_cycle_handler()` — daemon 启动时接线一次（`api/server.py`）
- `_fire_goal_cycle()` — 上述三条触发规则的实现
- `make_goal_recurring()` / `stop_goal_recurrence()` — CLI 命令背后调用的绑定/解绑函数
- `reap_finished_cycles()` — 由 `AutonomousLoop._tick_maintenance()` 周期调用，
  回收终态子节点计入 `cycle_count`/`progress_notes`

## 5. 已知限制

- 一对一绑定：一个 Goal 只能绑定一个 goal_cycle job，暂不支持"多个 job 共享同一个 Goal"。
- 看板（Streamlit）目前还没有展示 `recurring`/`cycle_count`/绑定 job 的专门 UI，
  只能通过 CLI（`/agent goals`、`/cron`）操作和查看。
- `reap_finished_cycles()` 是轮询式回收，最坏情况下有一个 tick 间隔（约 60s）的计数延迟。
