# Daemon 执行模型与调度心跳指南

> 设计与实现记录见
> [`next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md`](../next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md)；
> 本文档是面向使用者的说明（是什么、怎么开、怎么观测、怎么回退），不重复
> 设计推理过程。

## 1. 解决的问题

daemon 的公平调度算法（[Goal 执行公平性调度配置](goal-execution-fairness-config.md)）
决定"该轮到哪个 Goal/Objective"，但这只是"排序"——默认情况下，所有
Objective 的 step 最终都提交进和用户交互对话共用的同一个单线程队列，
任意时刻只有一个 turn 真正在执行；同时，`AutonomousLoop.tick()`（真正
触发调度决策的地方）依赖主循环"处理完当前消息、发现没有新消息时顺带
检查"这种协作式触发方式，如果主循环正忙于处理一次很长的 turn，调度决策
会被顺延同样长的时间。

本方案是两个独立的执行模型层面的改动，都是默认关闭的灰度开关：

| 阶段 | 内容 | 默认状态 |
|---|---|---|
| 阶段一 | 目标级持久 Worker（`ObjectivePersistentRunner`） | ⬜ 默认关闭 |
| 阶段二 | 调度心跳独立化（`SchedulerHeartbeat`） | ⬜ 默认关闭 |

两者相互独立，可以只开其中一个，也可以都开；都关闭时（默认状态）行为与
升级前完全一致。

## 2. 阶段一：目标级持久 Worker

**解决什么**：默认路径下"并发"只是排队顺序，不是真并行；已有的
`objective_isolated_context_enabled`（见
[Daemon 自主任务错误状态识别与恢复指南](daemon-autonomous-state-recovery-guide.md#4-阶段三自主任务独立上下文p1)）
虽然做到了真并行，但代价是每个 step 都在一次性的全新 Agent 实例上跑，
不保留任何跨 step 的会话/工具调用状态。

**做什么**：`ObjectivePersistentRunner`（`evolution/objective_agent_bridge.py`）
给每个 Objective execution 分配一条专属的单线程 executor，第一个 step
提交时惰性构建一个 Agent 实例并缓存，同一 execution 后续所有 step 都复用
这个 Agent 实例、在同一条专属线程上执行：

- 不同 execution 之间各自独立，真正并行（谁也不用等谁）；
- 同一 execution 内部的多个 step 保留连续的会话/工具调用状态，不再是
  "失忆的一次性 agent"；
- Objective 到达终止状态（完成/失败/取消）时，专属线程和 Agent 实例会
  被立即释放（通过 `ObjectiveExecutor(release_worker_fn=...)` 回调），
  额外有一个 idle TTL 兜底清理（默认 30 分钟），防止极端情况下的线程/
  Agent 泄漏。

与 `objective_isolated_context_enabled` 二选一、互斥，**本项优先**——
两个开关都打开时，以目标级持久 Worker 为准。

> **边界**：这份"跨 step 记得自己做过什么"的连续性是**纯内存态**的。如果
> daemon 在某个 Objective 执行到一半时重启，恢复执行时会重新构建一个全新
> Agent——之前积累的会话历史在重启这一刻就没了，退化回和隔离 runner 完全
> 一样的效果（每步都是失忆的新 agent）。持久 Worker 换来的是"进程存活期
> 内"的连续性，不是"跨进程重启"的连续性；`_submit_step()` 拼接的结构化
> 摘要（前序步骤结果/产出文件）仍然是跨重启也不丢的信息来源，不要只依赖
> Agent 自己的会话记忆。

**配置开关**（`AutonomyConfig`，见
[配置指南](config-guide.md#autonomyconfig好奇心评分--自主探索排序权重)）：

```json
{
  "autonomy": {
    "objective_persistent_worker_enabled": true,
    "objective_persistent_worker_idle_ttl_seconds": 1800.0
  }
}
```

| 字段 | 说明 |
|------|------|
| `objective_persistent_worker_enabled` | 默认 `False`。开启后 Self 同样不能在 REPL 里直接看到自主任务执行过程的中间对话（Agent 实例跑在独立线程上，不广播到主 bridge），这一点与 `objective_isolated_context_enabled` 相同 |
| `objective_persistent_worker_idle_ttl_seconds` | 某个 execution 的专属线程/Agent 超过这个时长（秒）未收到新 step 提交，视为孤儿并回收。正常情况下应由 Objective 终止时的回调及时释放，这里只是兜底 |

**回退**：设 `objective_persistent_worker_enabled=false`（默认值），
`_submit_fn` 恢复为改造前的路径（共享队列，或如果同时开了
`objective_isolated_context_enabled` 则退回隔离 runner）。daemon 关闭时
会调用 `ObjectivePersistentRunner.shutdown(wait=False)`，不强行打断正在
跑的线程。

## 3. 阶段二：调度心跳独立化

**解决什么**：`AutonomousLoop.tick()` 默认靠 `AgentRunner` 主循环
"`dequeue(timeout=0.5)` 超时后顺带检查"触发——如果主循环正卡在处理一次
耗时很长的 turn，tick 会被顺延同样长的时间，且延迟量不可预测。

**做什么**：`SchedulerHeartbeat`（`evolution/scheduler_heartbeat.py`）是
一条独立的后台线程，按自己的轮询间隔（默认 5 秒）检查
`autonomous_loop.should_tick()`，到期则触发 `tick()`。为了不和主循环产生
数据竞争，`tick()` 期间会持有一把共享锁，`AgentRunner` 处理完一个 turn、
回调 `on_turn_done()`/`on_turn_failed()` 那一小段状态更新代码上也会持有
同一把锁——两者只在这个短暂窗口互斥，真正耗时的 `run_turn()` 完全不受
影响，因此心跳线程不会因为一次长 turn 而被整体拖住，最多只需要等待"状态
更新"这一小段代码执行完。

开启后，`AgentRunner` 主循环原有的"顺带触发 tick"逻辑会自动关闭，避免
同一个 `tick_interval` 周期内被触发两次。

**配置开关**：

```json
{
  "autonomy": {
    "scheduler_heartbeat_enabled": true,
    "scheduler_heartbeat_poll_interval_seconds": 5.0
  }
}
```

| 字段 | 说明 |
|------|------|
| `scheduler_heartbeat_enabled` | 默认 `False`。这是比公平调度算法本身更底层的执行模型变化 |
| `scheduler_heartbeat_poll_interval_seconds` | 心跳线程自己"多久检查一次是否该 tick"的轮询间隔，应明显小于 `tick_interval_seconds`（`AutonomousLoop` 构造参数，默认 60 秒）才有意义 |

**回退**：设 `scheduler_heartbeat_enabled=false`（默认值），`AgentRunner`
恢复为原有的"dequeue 超时后顺带 tick"路径。daemon 关闭时会调用
`SchedulerHeartbeat.stop()`（非阻塞，不 `join()`）。

## 4. 在看板上观测

打开看板"🧠 自我状态"Tab，下滑到"⚙️ 执行模型"区块（对应
`GET /v1/self/execution_model_status`，见
[HTTP API 指南](http-api-guide.md#v1selfexecution_model_status--执行模型状态owner-only)）：

- **Objective 执行模式**：`persistent`（🟢 目标级持久 Worker）/
  `isolated`（🟡 隔离 Runner）/ `shared_queue`（⚪ 默认，共享队列）；
- 开启持久 Worker 时，展示当前活跃的 execution 数量和 `execution_id`
  列表——这个数字就是"这一刻真正并行执行的 Objective 数量"；
- 开启心跳解耦时，展示心跳线程是否存活（🔴 已启用但未存活是异常状态，
  需要检查 daemon 日志）、轮询间隔与 `AutonomousLoop` 自身的 tick 周期。

纯只读展示，看板上不提供切换这两个开关的按钮——两者都需要改
`agent_config.json` 并重启 daemon 才会生效，不是运行时可以热切换的开关。

## 5. 两者可以同时开启吗

可以。两者相互独立：目标级持久 Worker 解决"并发是否真实"，调度心跳
独立化解决"调度决策是否及时触发"。

> **历史提醒**：事后复查曾发现一处正确性问题（方案文档 §7.1）——
> `ObjectivePersistentRunner`/`ObjectiveIsolatedRunner` 在专属线程里回调
> `on_turn_done()`/`on_turn_failed()` 时，最初没有接入 `HttpServer` 构造的
> 共享调度锁，导致两个开关同时打开时，心跳线程和这两个 runner 的回调线程
> 可能并发读写 `ObjectiveExecutor` 内部状态字典。这个缺口已经修复：
> `HttpServer.__init__` 现在会在构建 `AutonomousLoop`（进而构建这两个
> runner）**之前**先创建好共享锁，并把它一并传给两个 runner 的构造函数；
> 两个 runner 在 `_run_step()` 里回调 `on_done`/`on_failed` 时会持有这把锁，
> 与 `SchedulerHeartbeat` 线程持锁调用 `tick()` 互斥。回归测试见
> `tests/test_objective_runner_sched_lock.py`。

建议仍然先分别灰度观察一段时间，确认各自稳定后再考虑同时开启——这个组合
目前还没有在真实 daemon 长期运行中验证过（见方案文档"后续可以观察的点"
一节），但底层的锁覆盖缺口已经不存在了，不再是"同时开启"的阻塞项。

## 相关文档

- [`next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md`](../next_doc/daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md) —— 设计推理过程与实施记录
- [Daemon 自主任务错误状态识别与恢复指南](daemon-autonomous-state-recovery-guide.md) —— `objective_isolated_context_enabled`（与本方案阶段一互斥的旧路径）
- [Goal 执行公平性调度配置](goal-execution-fairness-config.md) —— "该轮到谁"的排序算法（与本方案的关系：本方案解决排序结果是否被真正并行执行、是否被及时触发，不改变排序算法本身）
- [看板使用指南](kanban-dashboard-guide.md) —— "⚙️ 执行模型"面板
- [HTTP API 指南](http-api-guide.md) —— `GET /v1/self/execution_model_status`
