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

## 后续计划

按方案原有优先级表继续推进，下一项为 P0-8（主动告警通道）。每完成一项，
在本文档追加一节记录，并同步更新 `daemon_stability_and_ux_improvement_plan.md`
的状态列。
