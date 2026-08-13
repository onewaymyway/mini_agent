# Goal 执行公平性调度：配置说明

对应设计文档：`next_doc/goal_execution_fairness_improvement_plan.md`（P1-P5
均已实现，v1.3）。本文档只说明配置项含义和取值建议，设计动机、证据、验收
标准见上述设计文档。

所有配置项均位于 `AutonomyConfig`（即配置文件里的 `autonomy` 段）。

> **配置加载机制**：`autonomy.*` 段现在通过统一的嵌套 block 加载机制
> （`config/param_registry.py::NESTED_CONFIG_BLOCKS` + `load_all_nested_blocks()`）
> 从 `agent_config.json` 读取，与 `tech_radar`/`goal_mode`/`cron` 等其它
> 嵌套配置块用同一套通用代码，不再是单独手写的特例。历史上（改造前）
> 这里曾经出现过"改了配置没生效"的 bug（`autonomy`/`observability` 两个
> block 一度没有真正接入 `file_cfg`），现已通过这次统一化重构从根本上
> 修复——同样的 bug 不会再单独出现在某一个 block 上，因为所有嵌套 block
> 现在共享同一条加载路径。新增 `autonomy.*` 字段的方法、以及整个参数
> 加载机制的设计说明，见 `docs/param-system-guide.md`。

## P1：同 Goal 并发上限

```yaml
autonomy:
  max_concurrent_objectives_per_goal: 1   # 默认值
```

- 含义：同一个 Goal（按 `GoalNode.parent_id` 分组）同时最多能占用几个执行
  并发槽位。
- 默认 `1`：一个 Goal 一次只能有一个 Objective 在跑，避免它自己吃满全局
  并发上限（`MAX_CONCURRENT_OBJECTIVES`，默认 2）。
- 设为 `0` 或负数：不限制，等价于关闭本项，回退到改造前"谁排前面谁跑"的
  行为。
- 影响面：只影响"同时在跑"的数量，不影响 Goal 能拆出几个 Objective
  （见 `auto_objective_max_per_goal`）——超出上限的 Objective 仍会排队，
  排队顺序由下面的 `goal_scheduling_strategy` 决定。

## P2：调度策略

```yaml
autonomy:
  goal_scheduling_strategy: "fair_round_robin"   # 默认值，可选 "priority"
```

- `"fair_round_robin"`（新默认）：Objective 按所属 Goal 分组，组间按
  "该 Goal 上次被调度的时间"（`GoalNode.last_scheduled_at`）升序排列——
  越久没被调度过的 Goal 越优先；同一时间桶内按（含 P3 老化加成的）
  `priority` 降序。组内（同一 Goal 下的多个 Objective）按 `priority`
  降序，只有排第一的会被当作本轮"代表候选"参与组间排序。
- `"priority"`：改造前的行为，纯按 `priority` 降序排序（Python 稳定排序，
  同优先级时先创建的永远排前面）。用于灰度回退或问题排查时对比。
- 排序是自我修正的：一个 Goal 被调度后 `last_scheduled_at` 更新，自然排到
  后面；没被选中的 Goal 该字段不变，自然排到前面。不需要额外配置"补偿"。

## P3：停滞 Goal 老化加成

```yaml
autonomy:
  fairness_aging_boost_per_day: 1.0    # 默认值
  fairness_aging_boost_max_days: 14.0  # 默认值
next_action_stale_days: 7.0            # 已有配置项，P3 直接复用
```

- 只在 `goal_scheduling_strategy="fair_round_robin"` 下生效（`"priority"`
  策略不叠加老化加成）。
- 判定"是否已停滞"复用现有的 `next_action_stale_days`（默认 7 天，与晨报/
  `next_action_advisor.py` 的停滞检测口径一致，不需要单独配置一套阈值）。
- 停滞超过 `next_action_stale_days` 后，每多停滞一天，调度侧使用的
  `effective_priority = priority + aging_boost` 就临时 +
  `fairness_aging_boost_per_day`；累计加成不超过
  `fairness_aging_boost_max_days` 天对应的量（默认最多 +14）。
- 只影响调度侧计算出的 `effective_priority`，**不会覆盖、不会持久化**
  `GoalNode.priority` 本身——用户在看板上看到的 priority 数值不会因为
  老化加成而改变。
- Goal 一旦重新有实质进展（`last_touched_at` 更新，通常伴随一次执行推进），
  `days_since_touched` 归零，加成自动回到 0，不需要手动清零。

## P4：执行时间片化（抢占式让出槽位）

```yaml
autonomy:
  fairness_time_slicing_enabled: false     # 默认值：关闭
  fairness_yield_after_steps: 3            # 默认值
  fairness_yield_after_seconds: 900.0      # 默认值（15 分钟）
```

- 默认关闭：这是比 P1-P3 更激进的行为变化（会中途打断一个本来能连续跑完的
  Objective），先默认关闭，按需灰度开启，不强制所有部署一起切换。
- 开启后，`ObjectiveExecutor.on_turn_done()` 每完成一步会检查：当前"执行
  片段"（从 `start()` 或上次 `resume_fairness()` 算起）已完成的 step 数
  达到 `fairness_yield_after_steps`，**或**已运行时长达到
  `fairness_yield_after_seconds`，且按 P2 的公平排序确实存在另一个"未在
  运行"的 Goal 排在自己前面时，才会主动让出槽位（execution 状态置为
  `paused_for_fairness`，不计入 `running_count()`，`current_step_idx`
  停在断点）。
- 只有一个 active Goal（没有其它 Goal 排队）时，即使跑满阈值也不会让出——
  让出没有实际意义，避免无谓的暂停/恢复开销。
- 下次轮到该 Goal 时（`AutonomousLoop._tick_maintenance()` 的调度循环
  识别到候选处于 `paused_for_fairness`），走 `ObjectiveExecutor.
  resume_fairness()` 从断点续跑——不重新拆解 Objective、不丢失已完成的
  step 进度，同时重置本次"执行片段"的计时起点。
- 两个阈值任一满足即触发检查（不要求同时满足）；检查触发不等于一定让出，
  还要看是否真的有其它 Goal 在排队。

## 灰度回退

如果新策略导致意外行为，可以按下面方式逐项回退到改造前：

```yaml
autonomy:
  max_concurrent_objectives_per_goal: 0     # 关闭 P1
  goal_scheduling_strategy: "priority"      # 关闭 P2（同时使 P3 失效）
  fairness_time_slicing_enabled: false      # 关闭 P4（默认值，本来就是关闭）
```

各项相互独立，可以只关其中一个（例如保留公平轮询但取消并发上限）。

## P5：看板可视化

看板"🧠 自我状态"tab 新增了"⚖️ 执行公平性"折叠区块，展示每个 active Goal
的 priority / 老化加成 / effective_priority / 上次调度时间 / 上次进展时间，
按上次调度时间升序排列（与实际调度顺序一致）。数据来自新增的只读端点
`GET /v1/self/goal_fairness`，纯展示，不提供手动调整调度顺序的交互——人工
干预仍通过看板已有的"改 priority/改 status"等通用手段进行。

## P4 补充：调度公平性参数自诊断（goal_fairness_scheduling_diagnostics_plan.md）

P5 的"⚖️ 执行公平性"区块是**按 Goal 聚合**的视角，展示的是老化加成/
effective_priority；但 P4 时间片抢占本身是否开启、当前有没有 execution
正因为抢占被暂停、抢占的触发阈值现在是什么值——这些完全没有暴露过。
新增只读端点 `GET /v1/self/fairness_diagnostics`（"🗓️ 全局日程"tab 下的
"⚖️ 调度公平性诊断"折叠区块）作为补充：

- P4 时间片抢占当前是否开启（默认关闭）、`yield_after_steps`/
  `yield_after_seconds` 两个阈值当前的值；
- 当前因为抢占正被暂停的 execution 列表（`paused_for_fairness_*`）；
- 按 **Objective 粒度**（而不是 Goal 粒度）展示 priority/aging_boost/
  effective_priority/是否在跑/是否被抢占暂停——同一个 Goal 下多个
  Objective 之间谁排在前面，`/self/goal_fairness` 看不出来。

两个端点数据来源相同（`compute_aging_boost`/`active_objectives_fair_
ranked`），互为补充而非替代：先看 `/self/goal_fairness` 了解"哪个 Goal
最近被冷落"，再看 `/self/fairness_diagnostics` 了解"P4 抢占有没有在
生效、具体是哪个 Objective 被让出去了"。

