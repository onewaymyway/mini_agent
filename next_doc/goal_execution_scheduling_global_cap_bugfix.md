# Goal / Cron 执行调度总并行数缺失修复
（goal_execution_scheduling_global_cap_bugfix）

## 问题现象

用户反馈：看板顶栏显示"daemon 正在执行 3 项任务（点击查看）"，但按理说
不应该有 3 个 Goal 一起执行——直觉上系统应该有"一个地方控制总并行数"。

## 根因

`Goal → Objective` 派生的执行（`ObjectiveExecutor`）与普通 `cron job`
执行（`CronJobRunner`）是**两条完全独立**的并发控制通道：

- `ObjectiveExecutor.effective_max_concurrent()` 起点是
  `autonomy.max_concurrent_objectives_cap`，默认 **2**。
- `CronJobRunner.effective_max_concurrent()` 起点是
  `cron.max_concurrent_jobs`，默认 **2**。

两者只有在 `ResourceArbiter` 判定为 `degraded`、且
`scheduler.unified_arbitration_enabled=True`（默认 **False**，"默认行为
变化需要可灰度控制"）时，才会通过 `unified_task_scheduler.
allocate_weighted_slots()` 按权重统一裁决出一个共享的槽位总数。

也就是说，在正常（非 degraded）状态下——绝大多数时间——两条通道各自最多
能跑到自己的 cap，加总起来系统里同时最多可能有 2（Objective）+
2（cron）= 4 个任务在跑，完全没有任何"总并行数"控制。看板顶栏"正在执行
N 项任务"里 N=3（或更多）正是这个设计现状的直接体现：不是某处的 bug
导致数字算错了，而是压根就没有一个跨通道生效的总并行度上限。

`goal_cron_unified_scheduler_improvement_plan.md` 里已经预见到了这个
问题并做了 P5 步骤，但明确把"是否/何时切换到统一入口"列为"P5 第 5
步"、留在了改进计划范围之外——即当前代码库这部分本来就是"设计上先例，
后续再补"的状态。

## 修复内容

### 1. 新增配置 `scheduler.max_total_concurrent_tasks`
（`src/mini_agent/config/models.py::SchedulerConfig`）

`Optional[int] = None`（默认不生效，保证未配置时两条通道行为与改造前
完全一致）。设置为正整数后，代表 Goal Objective 通道 + 普通 cron 通道
**加起来**同时最多能跑多少个任务——与 `unified_arbitration_enabled`
（只在 degraded 状态下生效）互补，本字段在**任意状态**（degraded 与否）
下都生效，是一个更朴素、更贴近用户直觉的"总闸门"。

`scheduler` 这个配置 block 早已通过 `NestedBlockSpec("scheduler",
_m.SchedulerConfig)` 注册进了 `param_registry.py`（见
`goal_cron_unified_scheduler_improvement_plan.md` 遗留缺陷修复记录），
新增的 dataclass 字段自动被通用加载机制覆盖，无需在 `param_registry.py`
/`loader.py` 里额外手写解析代码。

### 2. `ObjectiveExecutor`/`CronJobRunner` 互相感知对方运行数

- `ObjectiveExecutor` 新增 `_other_channel_running_fn`（构造时为
  `None`）+ `set_other_channel_running_fn(fn)` setter；
  `effective_max_concurrent()` 在原有 cap（含 Track J degraded 收紧、
  Track K 自适应收紧）算完之后，如果 `scheduler.
  max_total_concurrent_tasks` 已配置且回调已接线，再叠加一层 clamp：
  `cap = min(cap, max(0, max_total_concurrent_tasks - cron 通道当前运行数))`。
- `CronJobRunner` 对称新增同名的 `_other_channel_running_fn`/
  `set_other_channel_running_fn(fn)`，`effective_max_concurrent()` 重构
  为"先算 degraded 分支（不变），再无条件叠加一层全局 clamp"——原本
  `if not self._gating_degraded: return cap` 的提前返回改成了先记录
  `cap`、degraded 时在原地收紧，最后统一走一次全局 clamp 再返回，让
  全局上限在 degraded/非 degraded 两种状态下都生效。

两处叠加逻辑都遵循项目一贯的"只降不升"原则：任何异常都 `log_exception`
后直接跳过这一层 clamp，不会因为回调本身出错导致并发被错误地砍到 0
或抛出到上层影响正常调度。

### 3. `src/mini_agent/api/server.py` 接线

`objective_executor.load()` 之后、两个组件都已构造完毕的位置，双向注入
回调：

```python
cron_job_runner.set_other_channel_running_fn(lambda: objective_executor.running_count())
objective_executor.set_other_channel_running_fn(lambda: cron_job_runner.running_count)
```

`scheduler.max_total_concurrent_tasks` 未配置时，这两行调用完全是
no-op（对应的 clamp 分支不会被触发），不影响任何现有部署。

## 涉及文件

- `src/mini_agent/config/models.py`（`SchedulerConfig.
  max_total_concurrent_tasks`）
- `src/mini_agent/evolution/objective_executor.py`
  （`_other_channel_running_fn`/`set_other_channel_running_fn()`/
  `effective_max_concurrent()`）
- `src/mini_agent/evolution/cron_job_runner.py`（同上，对称实现）
- `src/mini_agent/api/server.py`（双向接线回调）
- `docs/cron-dedicated-execution-guide.md`（新增 §7.1）
- 本文档

## 验证

- `python3 -m py_compile` 通过：`config/models.py`、
  `evolution/objective_executor.py`、`evolution/cron_job_runner.py`、
  `api/server.py`。
- 手工走查：`max_total_concurrent_tasks=None`（默认）时两个
  `effective_max_concurrent()` 的返回值与改造前完全一致（clamp 分支
  被 `total_cap is not None` 短路跳过）；设置为一个正整数后，两条通道
  在任意时刻的运行数之和不会超过该值。

## 使用建议

如果只是想让看板顶栏"正在执行 N 项任务"不再出现 N > 2 这种情况，配置：

```json
{ "scheduler": { "max_total_concurrent_tasks": 2 } }
```

如果想要"总并发 2，但 cron 类任务优先级更高、必须至少保证 1 个槽位"这种
更精细的按权重分配，仍然应该走已有的 `unified_arbitration_enabled` +
`channel_weights` + `cron.reserved_min_concurrent` 组合（目前只在
degraded 状态下生效）——两套机制可以同时打开，`max_total_concurrent_tasks`
管"正常状态下的总闸门"，`unified_arbitration_enabled` 管"资源紧张时按
权重精细分配"，互不冲突。

## 后续建议（未在本次改动范围内）

- 如果希望"正常状态下也按权重分配"，而不只是一个不区分通道的硬总数，
  可以考虑把 `max_total_concurrent_tasks` 生效时也顺带调用
  `allocate_weighted_slots()`（而不是简单的"总数减对方运行数"）——但这
  会让"正常状态"和"degraded 状态"的裁决逻辑趋同，属于更大的重构，建议
  单独评估，不在本次 bugfix 里顺手做。
- `workflow` 执行通道（看板顶栏第三类"来源：工作流"）目前完全没有接入
  本次的跨通道感知，如果之后也想把 workflow 并发计入同一个总闸门，需要
  单独设计（workflow 的并发控制机制与 Goal/cron 两者结构不同，不能直接
  复用同一套 `_other_channel_running_fn` 模式）。
