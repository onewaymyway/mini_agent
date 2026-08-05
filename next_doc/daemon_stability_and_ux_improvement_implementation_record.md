# Daemon 稳定性与用户体验改进：实施记录

> 对应方案：`next_doc/daemon_stability_and_ux_improvement_plan.md`（11 项方向，
> 按 P0/P1/P2/P3 排序）。本文档按实施顺序记录每一项的具体改动、涉及文件、
> 测试覆盖，逐项推进、逐项更新。

---

## P0-4：仲裁状态时间线的被动记录问题 ✅ 已实现

**对应方案第 4 项。**

### 问题回顾
`record_gating_transition()`（写入 `gating_history.jsonl`）此前只在
`GET /v1/autonomous/status` 被调用时顺带触发（`api/routes.py` 里
`ResourceArbiter(...).diagnose()` 调用之后）。如果长时间没有客户端轮询这个
接口（例如没人打开看板），期间发生的三态门控状态变化不会被记录，形成
"daemon 昨晚没跑任何任务，事后无法追溯当时是否真的被限流"的可观测性盲区。

### 改动
1. `src/mini_agent/evolution/resource_arbiter.py`
   - `ResourceArbiter.gating_state()`：在函数内部所有返回分支（预算耗尽的
     `blocked`、`resource_gating_degraded_enabled=False` 时的二元退化路径、
     三态路径下的 `blocked`/`degraded`/`full`）计算出最终结果后，统一调用
     新增的私有方法 `_record_transition(state, reason)`，在结果产生的
     那一刻主动落盘。
   - 新增 `ResourceArbiter._record_transition()`：内部调用已有的
     `record_gating_transition(paths, state, reason)`，用 try/except 包裹，
     记录失败不影响 `gating_state()` 本身的返回值。
   - `record_gating_transition()` 函数本体未改动，其"状态与上一条相同则
     不写入"的去重逻辑天然保证了这里重复调用（`gating_state()` 可能在一次
     tick 内被多处调用）是幂等的，不会把时间线刷成检查日志。
2. `src/mini_agent/api/routes.py`
   - `GET /v1/autonomous/status` 中不再重复调用
     `record_gating_transition()`（原来的写入点删除），改为依赖
     `ResourceArbiter(...).diagnose()` 内部对 `gating_state()` 的调用
     完成记录。避免同一次状态变化被两条路径分别判断、注释里显式说明
     记录点现在只有一处。

### 为什么"覆盖轮询空窗期"能成立
`gating_state()`（通过 `can_run_autonomous()`）在 `AutonomousLoop` 主循环
的每个 tick 都会被调用（`evolution/autonomous_loop.py` 的调度决策点），
与是否有 HTTP 客户端在轮询无关。把记录点下沉到 `gating_state()` 内部后，
只要 daemon 在跑（哪怕没有任何人打开看板），状态变化就会被记录下来。

### 测试
- 新增 `tests/test_gating_history_active_recording.py`（6 个用例）：
  - 只调用 `gating_state()`（不经过任何 HTTP 路径）验证历史文件被写入；
  - 多次状态切换（full → degraded → blocked）验证时间线完整记录；
  - 状态不变时重复调用不产生重复记录；
  - 预算耗尽分支、`resource_gating_degraded_enabled=False` 的二元退化
    分支分别验证记录生效。
- 回归：`tests/test_resource_arbiter_gating_track_j.py`、
  `tests/test_cron_job_runner_resource_arbiter.py`、
  `tests/test_resource_arbiter_behavior_gating.py`、
  `tests/test_gating_history.py`（原有的 `/v1/autonomous/gating_history`
  路由测试）全部通过，未观察到行为回归。
- 已知环境缺口（与本次改动无关，复现于未安装 `json_repair` 时的
  `tests/test_judge_verdict.py` 收集失败）：不影响本次改动涉及的模块。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 4 项标题与
  优先级表格新增"状态"列，标记为已实现。

---

## P0-8：主动告警通道，而非只有"打开看板才知道" ✅ 已实现

**对应方案第 8 项。**

### 问题回顾
`notification/dispatcher.py` + `channels/{kanban,email}.py` 的分级通知
基础设施已经存在，但只服务于 watchlist_report 场景。Objective failed、
circuit breaker tripped、卡死回收链路异常增长这几类执行异常事件此前只
落盘为日志/事件/看板标红，不会主动推送——用户必须主动打开看板或翻日志
才会发现"daemon 出问题了"。

### 改动
复用现有 `notification/dispatcher.py`（未新建通知机制），新增三类信号源
的主动推送点：

1. **Objective 被判定失败**（`src/mini_agent/evolution/objective_executor.py`）
   - `_on_objective_failed()`（Objective 收尾的唯一统一入口，此前已确认
     所有 `ex.status = "failed"` 路径最终都汇聚到这里）新增调用
     `_notify_objective_failed()`。
   - 覆盖范围是"任何原因判定失败"，包括 Guardian 判定（`progress_notes`
     带 `guardian:` 前缀）、watchdog 超时回收、多次无效结果判定失败等——
     因为它们都通过同一个收尾函数结束。
   - 通知 `source="objective_failed"`，`body` 取 `progress_notes` 前 200
     字符，`meta` 带 `execution_id`/`objective_id`。

2. **Workflow 熔断触发**（`src/mini_agent/workflow/watchdog.py`）
   - `WorkflowWatchdog.report_workflow_level_failure()` 判定熔断命中
     （`tripped=True`，已经在这里 `request_cancel()` 并写 `watchdog.jsonl`
     事件）时，新增调用 `_notify_circuit_breaker_tripped()`。
   - 通知 `source="workflow_circuit_breaker"`，正文包含 `error_type` 与
     命中的 distinct step id 列表。

3. **卡死回收链路短时间内异常增长**（`src/mini_agent/evolution/recovery_event_log.py`）
   - `record_recovery_event()` 新增可选 `paths: Optional[AgentPaths]`
     参数（默认 `None`，向后兼容——旧调用方不传时行为与改造前完全一致，
     只记录不检测不通知）。
   - 传入 `paths` 时，在追加事件后顺带统计"过去 `_BURST_WINDOW_SECONDS`
     （默认 600s=10 分钟）内同一 `kind` 的事件数"，达到
     `_BURST_THRESHOLD`（默认 3）时通过 dispatcher 推送一条
     `source="recovery_burst"` 的通知；用模块级
     `_last_burst_notified_at: dict[kind, ts]` 做每个 kind 独立的
     `_BURST_NOTIFY_COOLDOWN_SECONDS`（默认 1800s=30 分钟）冷却，避免
     阈值达到后每条新事件都重复推送。
   - 三条既有调用链路同步补上 `paths=self._paths`（或
     `AgentPaths(base_cfg.project_root)`，`ObjectiveIsolatedRunner` 场景
     没有现成的 `self._paths`，用 `AppConfig.project_root` 现场构造）：
     `cron_job_runner.py::reap_stale_jobs()`、
     `objective_executor.py::reap_stale_steps()`、
     `objective_agent_bridge.py::ObjectiveIsolatedRunner.check_health()`。

### 实现过程中发现并修复的一个 bug
最初 `_last_burst_notified_at.get(kind, 0.0)` 用 `0.0` 作为"从未通知过"
的哨兵值——如果调用方传入的 `now`（多见于测试，或系统时间被回拨/重置的
边界场景）本身接近 0，会被误判为"刚刚通知过"（`ts - 0.0 < cooldown`），
导致首次真实突发的通知被吞掉、永远不会真正发出。改为
`float("-inf")` 后修复，测试 `test_burst_within_window_triggers_notification_once`
覆盖了这个场景。

### 测试
- 新增 `tests/test_active_alerting_p0_8.py`（7 个用例）：
  - Objective 失败收尾直接触发一次 dispatch（`source=objective_failed`）；
  - 熔断达到阈值触发通知，未达阈值不触发；
  - 卡死回收突发在窗口内达到阈值触发一次通知、冷却期内不重复；
  - 未传 `paths` 时不检测不通知（向后兼容）；
  - 不同 `kind` 的事件数互不累加；
  - 超出窗口的旧事件不计入突发计数。
- 回归：`tests/test_recovery_event_log.py`（环形缓冲行为，未传 `paths` 的
  旧调用方式）、`tests/test_notification_dispatcher.py`、
  `tests/test_notification_routes_p7.py` 全部通过（除一个与本次改动无关
  的既有失败用例 `test_kanban_writes_alert_record`——该用例基于已废弃的
  `external_input_alerts` 存储路径断言，kanban 渠道早已改为写
  `notification_reports`，在完全不改动任何文件的原始代码上单独运行同样
  失败，与本次改动无关）。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 8 项标题与
  优先级表格状态列标记为已实现。

---

## 后续计划

按方案原有优先级表继续推进，下一项为 P0-3（调度心跳与持久 Worker 组合：
默认开启）。每完成一项，在本文档追加一节记录，并同步更新
`daemon_stability_and_ux_improvement_plan.md` 的状态列。
