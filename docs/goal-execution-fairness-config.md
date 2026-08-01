# Goal 执行公平性调度：配置说明

对应设计文档：`next_doc/goal_execution_fairness_improvement_plan.md`（P1-P3，
v1.1 已实现）。本文档只说明配置项含义和取值建议，设计动机、证据、验收标准见
上述设计文档。

所有配置项均位于 `AutonomyConfig`（即配置文件里的 `autonomy` 段）。

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

## 灰度回退

如果新策略导致意外行为，可以按下面方式逐项回退到改造前：

```yaml
autonomy:
  max_concurrent_objectives_per_goal: 0     # 关闭 P1
  goal_scheduling_strategy: "priority"      # 关闭 P2（同时使 P3 失效）
```

两项独立配置，可以只关其中一个（例如保留公平轮询但取消并发上限）。

## 尚未实现的部分

- **P4（执行时间片化）**：设计文档中的草案，暂未实现，是否推进留待观察
  P1-P3 上线效果后再决定，见设计文档 §4 待讨论问题 1。
- **P5 看板可视化**：本文档覆盖的是 P5 的"配置文档"部分；"执行公平性"
  看板区块（展示各 Goal 的 `last_scheduled_at`/近期调度次数/
  `effective_priority`）尚未实现。
