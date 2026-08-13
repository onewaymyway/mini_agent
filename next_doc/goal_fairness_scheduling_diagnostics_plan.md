# 调度公平性参数自诊断（只读快照）

延续此前讨论的第 5 类改进方向"调度参数自诊断"：
`goal_execution_fairness_improvement_plan.md` 里 P2 公平轮询 / P3 老化加成 /
P4 时间片抢占的权重、阈值目前都是默认值拍脑袋定的，没有一个基于真实运行
状态反馈参数是否合理的观测面。本方案先做最小可行的一步——不新增事件
持久化（历史触发频率统计成本更高、需要新的日志基础设施，留到有真实
需求再做），只做一个**当前快照级别**的只读诊断：把"公平排序算出来的
effective_priority、当前谁的老化加成生效了、当前有没有 execution 因为
时间片抢占被暂停"这些已经在内存里算出来但从未对外暴露的信息接上一个
端点。

## 与既有 P5 看板面板（`GET /v1/self/goal_fairness`）的关系

`goal_execution_fairness_improvement_plan.md` P5 已经有一个"⚖️ 执行公平性"
面板，按 **Goal 粒度**展示 priority/老化加成/effective_priority/调度时间。
本方案不是重复造轮子：P5 完全没有涉及 P4（时间片抢占）当前是否开启、
有没有 execution 正被抢占暂停、抢占阈值是什么；也是 Goal 粒度看不出
"同一个 Goal 下多个 Objective 谁排在前面"。这两个端点数据来源相同
（`compute_aging_boost`/`active_objectives_fair_ranked`），互为补充，
分别放在"🧠 自我状态"（P5，已有）和"🗓️ 全局日程"（本方案，新增）两个
不同的 tab。

## 设计

新增 `perception/fairness_diagnostics.py::fairness_diagnostics_snapshot(
goal_backlog, objective_executor, cfg)`：

- `time_slicing_enabled`：`autonomy.fairness_time_slicing_enabled` 当前是否
  开启（P4 默认关闭，这个字段本身就能回答"这个功能到底有没有人在用"）。
- `config`：当前生效的 `aging_boost_per_day`/`aging_boost_max_days`/
  `stale_days`/`yield_after_steps`/`yield_after_seconds` 五个参数快照，
  方便对照"现在到底是什么值"而不用去翻 config 文件。
- `paused_for_fairness_count`/`paused_for_fairness_objective_ids`：当前
  因为时间片抢占被暂停的 execution，复用已有的
  `ObjectiveExecutor.fairness_paused_objective_ids()`。
- `active_objectives_count`/`goals_with_active_aging_boost`/`objectives`
  （截断前 20 条）：复用 `GoalBacklog.active_objectives_fair_ranked()` +
  `compute_aging_boost()`，对每个当前 active 的 objective 给出
  priority/aging_boost/effective_priority/是否在跑/是否被暂停——直接
  回答"老化加成现在对谁生效、生效了多少"这个此前只能靠读代码猜的问题。

任何异常返回全零/空结构，不抛异常，与既有只读聚合（`sentinel_summary`/
`stuck_stats_summary`）风格一致。

## 暴露方式

- REST：`GET /v1/self/fairness_diagnostics`（只读）。
- 看板：`AgentClient.fairness_diagnostics()` + "🗓️ 全局日程"tab（公平调度
  相关信息已经在这个 tab 出现过 gating 时间线）新增一个折叠区块。

## 明确不做的部分（本轮范围之外）

- 不新增"历史触发频率"的持久化事件日志——这是当前快照做不到的（比如
  "过去 7 天老化加成一共生效了多少次""时间片抢占历史触发次数"），需要在
  `_should_yield_for_fairness()`/`active_objectives_fair_ranked()` 调用点
  插桩写日志，属于更大改动，等这版快照用起来之后再看要不要做。
- 不改任何调度决策逻辑本身，纯只读展示。

## 实施记录

已实现：`perception/fairness_diagnostics.py` + `GET /v1/self/
fairness_diagnostics` + `AgentClient.fairness_diagnostics()` + "🗓️ 全局
日程"tab 新增"⚖️ 调度公平性诊断"折叠区块。`tests/
test_fairness_diagnostics.py` 覆盖空 backlog/异常兜底/老化加成计算/暂停
列表透传/开关状态。
