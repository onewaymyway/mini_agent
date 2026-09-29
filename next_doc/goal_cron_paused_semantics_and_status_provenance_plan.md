# 周期性 Goal 的暂停语义、跳过分类与状态变更来源追溯方案

> 状态：已实施完成。新增/修改文件清单见文末"实施记录"。
> 前置背景：`next_doc/goal_cron_status_integrity_and_self_healing_plan.md`（状态
> 完整性护栏）、`next_doc/goal_cron_unified_scheduler_improvement_plan.md`（P2
> 连续跳过告警）。本方案修正其中两处遗留问题，并补上"状态是谁改的"的追溯能力。

## 1. 问题

线上出现告警：`[执行失败] cron job「goal-cycle:持续关注最新的Agent领域和AI领域的
最新技术」长期未能触发`——连续跳过 11195 次，原因 `goal_cycle_goal_paused`，上次
成功触发在 8 天多以前。

### 1.1 根因：`False` 一个返回值承载了三种含义

`_fire_goal_cycle()` 未触发时返回 `False`，而 `CronScheduler` 对 `False` 只有一种
解释："这次没触发成功"——`consecutive_skip_count += 1`、不推进 `next_run_at`、
每 5 次告警。于是：

| 未触发的真实情况 | 例子 | 应有处理 | 旧行为 |
|---|---|---|---|
| 暂时性，稍后重试 | 上一轮还在跑、资源仲裁 blocked | 重试，计数告警合理 | 一致 |
| 用户意图，本就不该跑 | paused、abandoned、用户"跳过本轮" | 静默 | **被当故障，每分钟重试并告警** |
| 配置已失效 | Goal 已被删、没有 goal_id | 自动停用 job | **永远重试** |

代码注释写着 paused"不触发、不报错"，实现与注释不一致；11195 次跳过 ≈ 8 天每分钟
一次，与"上次成功 8 天 14 小时前"吻合。

### 1.2 同根因的另外两个缺陷

1. **"跳过下一轮"只跳过约 1 分钟**：`skip_next_cycle` 被消耗后返回 `False`，
   `next_run_at` 不推进，下一次 tick 立刻正常触发，用户想跳过的整个周期没被跳过。
2. **启动持续失败时每分钟新建一个 failed 子 Objective**：`add_objective()` 之后
   `start()` 返回 `None`，子节点标 failed 并返回 `False`，下一分钟重来，`goals.json`
   持续膨胀。

### 1.3 追溯缺口：查不到"是谁把它暂停的"

`GoalNode.status_history` 只有 `{status, at}`，无操作者；并且经
`update_fields(status=…)`（看板 PATCH、CLI accept、SoftGoalDeriver 自动复核）改
状态**完全不写历史**。所以事后既不知道是用户、cron 还是别的自动机制改的，
甚至可能没有记录。cron job 的启停（`enabled`）同样没有任何历史。

## 2. 产品决策

- **周期性 Goal 不可暂停**（默认，`heal`）。理由：这类 Goal 的设计不变量是"永不
  结束"，`paused` 对它是矛盾状态。保留 `respect`（旧语义）作为可配置退路。
- "停一下"的三种替代手段：跳过一轮（`/agent goals skip`）、停用绑定的 cron job、
  彻底停止（`/agent goals unrecur`）。
- 暂停/放弃期间不推进 `next_run_at`（方案 A）：恢复后立即补跑**一轮**，不会连发。
- 不做"暂停自动 disable job"：Goal 状态与 job.enabled 是两份状态，联动容易漂移，
  以 Goal 状态为唯一事实来源（`_fire_goal_cycle` 每次本来就读它）。

## 3. 设计

### 3.1 状态变更来源追溯（`perception/status_provenance.py`）

来源字符串 `<类别>:<入口>`，类别 `user / cron / system / agent / unknown`：

| 来源 | 写入点 |
|---|---|
| `user:cli` | `/agent goals done|pause|accept|abandon|reject|unrecur|recur|skip`、`/cron enable|disable` |
| `user:api` | `PATCH /v1/goals/{id}`、`POST …/unrecur`、`PUT /v1/cron/jobs/{id}`（看板） |
| `cron:goal_cycle` | `_fire_goal_cycle` 的自愈拉回 active、启动失败标记 |
| `cron:scheduler` | 调度器对 invalid 类 job 的自动停用 |
| `system:objective_executor` | 执行终态回写 |
| `system:goal_node_retry` | 失败节点自动重试 |
| `system:goal_relevance` | 外部信号相关时 `try_advance_goal` 重新激活 |
| `system:soft_goal_deriver` | 自动复核不通过置 paused |
| `system:goal_recurrence` | `stop_goal_recurrence` 未指定 actor 时 |
| `unknown` | 调用方未声明——**自动附带 `caller`（`module.function:line`）** |

要点：
- `status_history` 每项：`{status, at, from, by, reason[, caller]}`；旧记录无后三个
  字段，读取按缺失处理，显示"未记录来源（旧数据）"，**无需迁移**。
- 未声明来源不报错（既有调用点行为不变），但会记 `unknown + caller`，任何我们没
  预料到的入口改状态也能被追到。
- 现在 `update_fields(status=…)` 也写历史（`status_actor`/`status_reason` 参数）。
- 每个节点/job 历史最多 200 条，超长丢最旧（`HISTORY_MAX_ENTRIES`）。
- `CronJob.state_history`：`{enabled, at, from, by, reason[, caller]}`，仅在
  `enabled` 真正变化时追加。
- 追溯记录失败一律吞掉，不影响状态写入本身。

### 3.2 暂停语义

`cron.recurring_goal_paused_policy`：

- `heal`（默认）：CLI/REST 拒绝把周期性 Goal 写成 `paused`（REST 返回 409，信息里
  给出替代做法）；遗留的 `paused` 在下一次 tick 走自愈分支拉回 `active` 并触发，
  `progress_notes` 记录"该状态的写入记录：`[时间] active → paused　by 用户操作
  （user:api）`"，即直接回答"谁暂停的"；拉回动作本身记为 `cron:goal_cycle`。
- `respect`：旧语义，允许暂停；暂停期间静默（`intentional` 类）。
- 非法值按 `heal`。`register_goal_cycle_handler(paused_policy_provider=…)` 每次触发
  读取，配置热更新无需重注册。

### 3.3 跳过原因分类（`cron_skip_reasons.py`）

| 类别 | 原因码 | 计数 | 告警 | `next_run_at` | 其它 |
|---|---|---|---|---|---|
| `retry`（默认，含未知码） | `already_running`、`arbiter_blocked`、`goal_cycle_prev_cycle_running` 等 | +1 | 每 N 次 | 不推进 | 与旧行为一致 |
| `retry_backoff` | `goal_cycle_objective_start_failed` | +1 | 每 N 次 | **指数退避** 60s×2^(n-1)，上限为 job 自身一个调度周期 | 可用 `start_failure_backoff_enabled=false` 关闭 |
| `intentional` | `goal_cycle_goal_paused`、`…_abandoned`、`…_user_skip` | **清零** | 无 | 仅 `user_skip` 推进一个完整周期 | 原因码仍写入，看板可见 |
| `invalid` | `goal_cycle_goal_missing`、`goal_cycle_no_goal_id` | 清零 | 一次性"已自动停用"通知 | — | job.enabled=False，记入 state_history |

调度器 tick 里"是否需要落盘"改为对比 `(skip_count, next_run_at, enabled)` 三元组，
避免推进 `next_run_at`/自动停用后未持久化。

**存量数据**：现有 job 的 `last_skip_reason` 在升级后第一次 tick 被重写；已 paused
的 Goal 在 `heal` 下直接恢复，在 `respect` 下计数归零，均无需迁移。

### 3.4 告警降噪（opt-in）

`cron.skip_alert_backoff_enabled`（默认 `false`）：开启后只在第 N×2^k 次（默认
5、10、20、40…）告警。默认保持"每 N 次一条"的既有节奏。

### 3.5 新增 CLI

- `/agent goals history <id>`：打印 Goal 状态变更历史（含来源）；周期性 Goal 同时
  打印绑定 cron job 的启停历史。
- `/agent goals skip <id>`：跳过周期性 Goal 的下一轮（同看板"跳过下一轮"）。

## 4. 配置汇总

| 配置 | 默认 | 说明 |
|---|---|---|
| `cron.recurring_goal_paused_policy` | `heal` | `heal`：周期性 Goal 不可暂停并自愈；`respect`：允许暂停，暂停期间静默 |
| `cron.start_failure_backoff_enabled` | `true` | 子任务启动失败时 `next_run_at` 指数退避 |
| `cron.skip_alert_backoff_enabled` | `false` | 连续跳过告警改为 5、10、20、40… 次 |

## 5. 影响范围与兼容性

- `GoalBacklog.set_status()` 新增可选关键字参数 `actor`/`reason`；
  `update_fields()` 新增 `status_actor`/`status_reason`；
  `CronScheduler.enable()/disable()`、`stop_goal_recurrence()`、
  `make_goal_recurring()` 新增可选 `actor`。全部可选，旧调用点不变。
- **行为变化（需知悉）**：`heal` 为默认，周期性 Goal 不能再通过 CLI/REST 暂停。
  若要保留旧行为，配置 `cron.recurring_goal_paused_policy = "respect"`。
- **鸭子类型替身注意**：调用 `set_status(id, status, actor=…)` 的内部路径
  （执行器、失败重试）要求替身接受 `**kwargs`；仓库内两处测试替身已对齐。
- 看板前端未改动：`GoalNode.to_dict()` 已带 `by/reason/from`，前端展示需另接。
- 遗留局限：本方案上线**之前**写入的 `status_history` 没有来源，无法回溯
  （例如这次 `goal_d31cfa19` 是谁暂停的）。

## 6. 排查手册：怎么查"谁改了状态"

1. `/agent goals history <goal_id>`：看每次变更的 `by`/`reason`/`caller`。
2. `by=unknown` 时看 `caller`，它是改状态的代码位置。
3. 周期性 Goal 不触发时，同时看输出末尾绑定 job 的启停历史。
4. `heal` 自愈发生过时，Goal 的 `progress_notes` 里有"该状态的写入记录"。

## 7. 实施记录

新增文件：
- `next_doc/goal_cron_paused_semantics_and_status_provenance_plan.md`（本文档）
- `src/mini_agent/perception/status_provenance.py`
- `tests/test_goal_cron_paused_semantics_and_provenance.py`（46 个用例）

修改文件：
- `src/mini_agent/perception/goal_backlog.py`：`set_status`/`update_fields` 写来源；
  `try_advance_goal` 写历史；`validate_status_write_for_recurring_goal` 加
  `paused_policy`；`normalize_paused_policy`。
- `src/mini_agent/evolution/cron_skip_reasons.py`：类别与 `ADVANCE_ON_INTENTIONAL`。
- `src/mini_agent/evolution/cron_scheduler.py`：`CronJob.state_history`；
  `enable/disable` 记来源；`_record_skip` 分类记账；退避；自动停用；告警退避。
- `src/mini_agent/evolution/goal_cron_bridge.py`：`paused_policy`；自愈带来源；
  `stop_goal_recurrence`/`make_goal_recurring` 接 actor。
- `src/mini_agent/config/models.py`：三个 `CronConfig` 字段。
- `src/mini_agent/api/server.py`：注册 handler 时传 `paused_policy_provider`。
- `src/mini_agent/api/routes.py`：PATCH goals、unrecur、PUT cron jobs 传 `user:api`；
  校验带策略。
- `src/mini_agent/cli/commands/goals.py`、`cli/commands/cron.py`：传 `user:cli`；
  新增 `history`、`skip`。
- `src/mini_agent/evolution/soft_goal_deriver.py`、`objective_executor.py`、
  `goal_node_retry.py`：传各自 actor。
- 测试：`tests/test_goal_cron_bridge.py`、`tests/test_cron_skip_alert_detail.py`
  （paused 用例显式 `respect`）；`tests/test_objective_executor_kanban_tracks_r2.py`、
  `tests/test_objective_executor_orphan_reconcile.py`（替身接受 provenance 参数）。
- 文档：`next_doc/goal_cron_status_integrity_and_self_healing_plan.md`、
  `docs/cron-dedicated-execution-guide.md`、`docs/commands-and-tools-reference.md`。

验证：相关 784 个既有测试中，除 15 个在原始仓库里同样失败的既有用例（缺依赖/环境
相关：`test_goal_mode`、`test_goal_execution_spec_kanban_routes`、
`test_goal_cron_feedback_and_output_policy` 的部分用例）外全部通过；本次新增
46 个用例全部通过。
