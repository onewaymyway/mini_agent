# Goal 与 Cron 绑定改进方案

> 状态：已完成（Track A–E 全部落地），见
> `next_doc/goal_cron_binding_implementation_record.md`
> 关联代码：`src/mini_agent/perception/goal_backlog.py`、`src/mini_agent/evolution/cron_scheduler.py`、
> `src/mini_agent/evolution/objective_executor.py`（只读依赖，不改其收尾逻辑）
> 新增代码：`src/mini_agent/evolution/goal_cron_bridge.py`
> 前置背景：见对话记录中"goal 和 cron 的关系"一节的现状分析——两者目前是完全独立的两套体系，
> `GoalNode` 没有周期性字段，`CronJob` 触发只会裸投递一条消息，二者互不感知。

## 0. 目标与非目标

**目标**：让一个 Goal 可以声明"我需要被周期性推进"，由 CronScheduler 按 schedule 定时为它创建/启动
新一轮 Objective，同时保证：
- 幂等：上一轮还没跑完，不会叠加开第二轮。
- 状态联动：Goal 被暂停/终止时，对应的 cron job 自动停止触发；反之 cron job 被删除/禁用时，
  Goal 不再被标记为"周期性"。
- 进度可追溯：每一轮的完成/失败都要在 Goal 上留下痕迹（`cycle_count` + `progress_notes`），
  不是"静默地又跑了一次"。

**非目标（本轮不做）**：
- 不做"多个 Goal 共享同一个 cron job"（一对一绑定，简单模型先跑通）。
- 不改变 `ObjectiveExecutor._on_objective_completed()` 等既有收尾逻辑本身——Objective（子节点）
  完成后依然正常进入终态，"周期性"完全通过"父 Goal 保持 active、不断派生新 Objective 子节点"
  实现，不改动 Track B 已有的 Objective↔Goal 状态同步语义。
- 不做"任务本身耗时超过 interval 时自动缩短下一轮等待"之类的自适应调度——超时/重叠只做最基本的
  跳过，不做动态调频。

## 1. 现状回顾（问题清单）

| 编号 | 问题 |
|---|---|
| P1 | `GoalNode` 没有 recurring/cycle 相关字段，完成即终态，无法"再来一轮" |
| P2 | `CronJob` 触发时只会把 `task_template` 当一条裸消息塞进 InputQueue，不知道、也不关心是否
     对应某个 Goal |
| P3 | 用户暂停/终止 Goal 后，如果背后挂了一个 cron job，job 不会跟着停，容易产生"Goal 已经
     abandon 了，cron 还在无脑触发"的僵尸任务 |
| P4 | 没有面向用户的入口把"已有 Goal"和"定时调度"关联起来，只能各自用 `/agent goals` 和
     `/cron` 分别操作 |

## 2. 数据结构改动

### 2.1 `CronJob`（`evolution/cron_scheduler.py`）新增字段

```python
goal_id: Optional[str] = None     # 绑定的 GoalNode.id（level="goal"）
run_mode: str = "message"         # "message"（现状：裸投递） | "goal_cycle"（新增：驱动 Goal 周期）
```
向后兼容：两个字段都有默认值，现有 `cron_jobs.json` 反序列化时自动补上 `run_mode="message"`，
行为与改动前完全一致。

### 2.2 `GoalNode`（`perception/goal_backlog.py`）新增字段

```python
recurring: bool = False                       # 是否是"周期性 Goal"
recurrence_cron_job_id: Optional[str] = None   # 反向指针，指回绑定的 CronJob.id
cycle_count: int = 0                           # 已完成的周期数
```

语义澄清：**周期性 Goal 的 status 不会因为某一轮 Objective 完成就被拖动**——因为
`_sync_goal_status()` 写的是 Objective（子节点）自己的 status，不会往上传播到父 Goal，这是
`set_status()` 现有实现本来就有的行为（不做父子传播），周期性场景正好复用这一点，不需要额外改动
`ObjectiveExecutor`。真正的"这一轮完成了"记录，由新增的
`GoalBacklog.record_cycle_completed()` 写在 Goal 自己身上。

## 3. `evolution/goal_cron_bridge.py`（新增模块）

```python
def register_goal_cycle_handler(cron_scheduler, goal_backlog, objective_executor) -> None:
    """把 goal_cycle 触发逻辑挂到 cron_scheduler，daemon 启动时调用一次。"""

def make_goal_recurring(goal_backlog, cron_scheduler, goal_id, schedule,
                          task_template: Optional[str] = None) -> CronJob:
    """把一个已存在的 Goal 声明为周期性：创建/更新一个 run_mode="goal_cycle" 的 CronJob，
    写回 goal.recurring/recurrence_cron_job_id。"""

def stop_goal_recurrence(goal_backlog, cron_scheduler, goal_id) -> bool:
    """反向操作：disable 对应 cron job，goal.recurring 置回 False（不删 Goal 本身，
    也不删 cron job，只是不再自动续期，用户随时可以再次 make_goal_recurring）。"""
```

触发时的核心逻辑（`_fire_goal_cycle`）：

1. Goal 不存在或 `status != "active"` → 直接跳过（返回 False，不消耗本次触发计数，
   `CronScheduler.tick()` 会在下次 tick 再检查一次，等 Goal 恢复 active 后自然继续）。
   这一条同时解决了 P3：用户暂停/abandon Goal 之后，不需要用户再手动去 disable 对应 cron job。
2. 幂等检查：Goal 当前是否已有一个"活跃周期子 Objective"仍在跑
   （`GoalNode.children_ids` 中 status=="active" 且 `objective_executor.is_running(child.id)`
   为真）——有则跳过，不叠加开新一轮。
3. 否则：`goal_backlog.add_objective(title=f"{goal.title}（第 N 轮）", parent_id=goal.id,
   source="cron", description=job.task_template)`，再 `objective_executor.start(objective)`。
   - 启动失败（`start()` 返回 None）：把这个刚创建的 Objective 子节点标记为 `"failed"`，避免
     留下一个永远 active 但没有对应 execution 的"幽灵子节点"卡住下一次的幂等检查。
   - 启动成功：不在这里递增 `cycle_count`——那是"这一轮开始了"，不是"这一轮完成了"，
     真正的计数推迟到轮询发现子节点终态时才写（见下一节）。

## 4. 完成计数怎么补（不改 ObjectiveExecutor 本身）

`ObjectiveExecutor` 目前没有"某个 Objective 完成"的外部订阅接口（`on_progress_fn` 是给 SSE 用的，
语义是"每次推进都通知"，不是"完成时才通知"，复用它会让 goal_cron_bridge 承担过多判断成本）。
本方案选择更简单的路径：**在 `AutonomousLoop` 的被动 tick 里顺带扫一遍**——
`goal_cron_bridge.reap_finished_cycles(goal_backlog)` 遍历所有 `recurring=True` 的 Goal，
检查其 children 中是否有本轮新出现的终态子节点（status in completed/failed/cancelled 且
未被计过数，用子节点自己的 id 是否在 Goal 的一个内部"已计数 id 集合"里判断，避免重复计数），
命中则 `cycle_count += 1`，并把子节点结果摘要（`progress_notes` 前 80 字）追加到父 Goal 的
`progress_notes`。这个函数是纯读写 `goals.json`，不依赖 `ObjectiveExecutor` 内部状态，
线程安全性沿用 `GoalBacklog._locked()`。

## 5. CLI

`/cron`（`cli/commands/cron.py`）新增子命令：
```
/cron add-goal-cycle <goal_id> <schedule> [task_template...]
    等价于 make_goal_recurring(...)；task_template 省略时复用 goal.description/title。
```

`/agent goals`（`cli/commands/goals.py`）新增子命令：
```
/agent goals recur <goal_id> <schedule>     — 把已有 Goal 声明为周期性
/agent goals unrecur <goal_id>              — 停止周期性（不删 Goal/cron job）
```

两组命令内部都直接复用 `goal_cron_bridge` 里的便捷函数，不重复实现绑定逻辑。

## 6. 实施顺序（Track 拆法）

- **Track A**：`CronJob.goal_id/run_mode` + `GoalNode.recurring/recurrence_cron_job_id/cycle_count`
  字段与序列化；`GoalBacklog.add_objective()` 补 `description` 参数；`CronScheduler.add_job()`
  补 `goal_id/run_mode` 参数。纯数据层，不改变任何既有行为。
- **Track B**：`goal_cron_bridge.py`：`register_goal_cycle_handler` + `_fire_goal_cycle`
  （含幂等检查与 Goal 状态门禁）+ `make_goal_recurring`/`stop_goal_recurrence`。
- **Track C**：`reap_finished_cycles()` 完成计数与 progress_notes 追加，接入
  `AutonomousLoop` 被动 tick。
- **Track D**：`api/server.py` 里 daemon 启动时调用 `register_goal_cycle_handler`；
  CLI 命令（`/cron add-goal-cycle`、`/agent goals recur|unrecur`）。
- **Track E**：单元测试 + `test_cases/` 用例文档更新。

每个 Track 完成后更新本文档状态栏和
`next_doc/goal_cron_binding_implementation_record.md`，并跑一次全量回归。
