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

## P0-3：调度心跳与持久 Worker 组合默认开启 ✅ 已实现（长稳验证未做）

**对应方案第 3 项。**

### 问题回顾
`objective_persistent_worker_enabled`（目标级持久 Worker）与
`scheduler_heartbeat_enabled`（调度心跳独立化）此前都默认 `False`，需要
用户手动改配置并重启 daemon 才能生效。两者共享调度锁的并发竞态问题已经
修复（回归见 `tests/test_objective_runner_sched_lock.py`），单独开启已
过验证。

### 改动
- `src/mini_agent/config/models.py`：
  - `AutonomyConfig.objective_persistent_worker_enabled` 默认值
    `False → True`。
  - `AutonomyConfig.scheduler_heartbeat_enabled` 默认值 `False → True`。
  - 两处都在字段上方补充注释说明改动依据和回退方式。
- 未改动任何加载/读取逻辑：配置加载对 dataclass 字段的处理方式是"文件里
  显式出现的字段覆盖默认值，未出现的字段沿用 dataclass 默认值"，因此这个
  改动天然满足方案要求的"新初始化项目默认双开；已有项目若显式写过
  `false`，尊重用户配置不覆盖"——不需要额外写迁移逻辑。
- `docs/daemon-execution-model-guide.md`：更新默认状态表格、两处字段说明
  表格行、两处"回退"提示文字（说明当前默认值已变为 `true`，`false` 是
  "原默认值"）、"两者可以同时开启吗"一节的验证现状说明。

### 验证与已知局限（如实记录，不夸大）
- **做了什么**：跑了一轮扩大范围的回归测试确认默认值改变后现有行为不
  受影响——`tests/test_objective_runner_sched_lock.py`（共享调度锁竞态）、
  `tests/test_objective_persistent_runner.py`、
  `tests/test_objective_persistent_worker_auto_compact.py`、
  `tests/test_execution_model_status_routes.py`、
  `tests/test_daemon_autonomous_state_recovery.py`、
  `tests/test_selective_compression.py`、
  `tests/test_objective_isolated_runner_health.py` 等，以及一次覆盖
  `objective`/`scheduler`/`heartbeat`/`execution_model`/`autonomous`
  关键词的过滤回归（179 个用例）全部通过；此外跑了一次全量测试套件确认
  失败集合（151 个失败/12 个 error）与本次改动无关——都是环境缺失依赖
  （`json_repair` 已补装、`rich`/`fastapi`/`uvicorn`/`httpx`/
  `python-multipart` 等）或与本次改动完全不相关的既有失败（如
  `test_workdir_knowledge_tools.py`、`test_skill_cli.py`），在完全未修改
  的原始代码上单独运行同样失败，与本次默认值调整无关。
- **没做什么**：方案原文建议的"24-72 小时、多 Goal 并发、混合正常/异常
  场景的长稳测试"没有做——这需要真实 daemon 长期运行环境，超出本次改动
  可执行的范围。这是一个已知局限，如实记录在
  `docs/daemon-execution-model-guide.md` 里，不假装已经完成过这轮验证。
  如果后续真实运行中观察到问题，两个开关都保留了独立的 `false` 配置项，
  可以单独或同时回退。

### 测试
- 未新增测试文件（这一项本身是默认值调整，没有新增行为分支需要覆盖；
  已有的 `test_objective_runner_sched_lock.py` 等已经覆盖了"两者组合开启
  时的并发正确性"这个核心风险点）。
- 回归结果见上一节。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 3 项标题与
  优先级表格状态列标记为已实现（并注明长稳验证的局限）。
- `docs/daemon-execution-model-guide.md`：见上方改动说明。

---

## P0-6：Guardian 与结果健全性校验：默认开启 ✅ 已实现

**对应方案第 6 项。**

### 问题回顾
`objective_isolated_context_enabled`（自主任务独立上下文，避免污染 Self
主 session）与 `guardian_mode_enabled`（跨 step "原地打转"检测 + 有限
恢复 + 兜底失败）此前都默认 `False`，需要用户读文档、手动开启才能获得
这两项对无人值守场景直接有价值的能力。

### 改动
- `src/mini_agent/config/models.py`：
  - `AutonomyConfig.objective_isolated_context_enabled` 默认值
    `False → True`，字段上方补充改动依据、回退方式，以及与
    `objective_persistent_worker_enabled` 互斥关系的说明。
  - `AutonomyConfig.guardian_mode_enabled` 默认值 `False → True`，字段
    上方同样补充说明。
  - 未改动任何加载逻辑：与 P0-3 相同，config loader 只在字段显式出现在
    `agent_config.json` 里时才覆盖 dataclass 默认值，因此这个改动天然
    满足"新初始化项目默认开启；已有项目若显式写过 `false`，尊重用户
    配置不覆盖"，不需要额外写迁移逻辑。
- `docs/daemon-autonomous-state-recovery-guide.md`：更新阶段三/阶段四的
  默认状态表格、字段说明表两处、两处"回退"提示文字（说明当前默认值已
  变为 `true`，`false` 是"原默认值"），以及开篇"默认配置下行为与升级前
  完全一致"的表述（不再完全成立，改为如实记录每一项的默认值变更历史）。

### 一个值得记录的发现：与 P0-3 的路由优先级交互
`api/server.py::_build_autonomous_loop()` 里，`objective_persistent_worker_enabled`
与 `objective_isolated_context_enabled` 是 `if`/`elif` 互斥关系，前者
优先。自 P0-3 起 `objective_persistent_worker_enabled` 已默认 `True`，
这意味着对**新初始化的项目**而言，`objective_isolated_context_enabled`
默认改为 `True` 之后，实际路由早已优先被 P0-3 的持久 Worker 接管
（持久 Worker 同样跑在独立线程、不广播到 Self 主 bridge，只是额外保留
了跨 step 会话连续性）——本项默认开启，实际主要覆盖"用户显式关闭了
持久 Worker、但仍希望自主任务不污染主 session"这一组合场景，而不是
新项目的默认路径本身。这个交互关系已经在 `config/models.py` 字段注释
和 `docs/daemon-autonomous-state-recovery-guide.md` 对应字段说明里
明确写出，避免用户误以为默认开启后两条路径都在生效。

### 实现过程中发现并修复的一处测试假设
`tests/test_daemon_autonomous_state_recovery.py::test_guardian_disabled_by_default_no_effect`
原本依赖 `guardian_mode_enabled` 默认 `False` 来验证"关闭时无影响"这一
分支。默认值改为 `True` 后该测试会因为 Guardian 提前介入而失败（连续
提交完全相同结果会被判定为"卡住"）。改为显式传入
`guardian_mode_enabled=False` 并改名为
`test_guardian_disabled_no_effect`，测试意图不变（验证"关闭 Guardian
时行为与升级前一致"这一分支仍然成立），只是不再依赖默认值。

### 测试
- 修正后的 `tests/test_daemon_autonomous_state_recovery.py` 全部 25 个
  用例通过。
- 回归：过滤关键词
  `objective|guardian|isolated|persistent|execution_model|autonomous`
  的 158 个用例全部通过；额外跑了一轮覆盖
  `objective_executor|cron_agent_bridge|objective_agent_bridge|
  autonomous_loop|workflow_watchdog|execution_model|kanban|
  resource_arbiter|notification` 的 141 个用例，除
  `test_notification_dispatcher.py::test_kanban_writes_alert_record`
  （P0-8 记录里已确认的既有失败，与 kanban 渠道存储路径迁移有关，和
  本次改动无关）全部通过。
- 未做方案原文之外的额外验证：本次改动是纯默认值调整，风险面已通过
  上述回归覆盖，未执行长稳测试（与 P0-3 保持一致的验证深度）。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 6 项标题与
  优先级表格状态列标记为已实现。
- `docs/daemon-autonomous-state-recovery-guide.md`：见上方改动说明。

---

## P1-5：Goal 侧引入"暂停"，而非只有"终止/重来" ✅ 已实现

**对应方案第 5 项。**

### 问题回顾
Workflow 执行已有暂停（⏸️）/取消（🛑）/续跑（▶️）的完整控制面，但
Goal/Objective 侧此前的干预选项只有终止（彻底结束）、重试当前步、
插话——没有"暂停后原样恢复"这一档。用户想临时叫停一个长期任务时，只能
"终止重来"或者"放着不管让它继续跑"，中间态缺失。

`ObjectiveExecutor` 内部此前已经有一个 `status == "paused"`，但那是
`pause_all()`/`resume()` 服务的资源仲裁全局暂停（`ResourceArbiter` 判定
预算耗尽时对**所有**运行中 Objective 生效），以及调度层面自动触发的
`paused_for_fairness`（跑满时间片、有其它 Goal 排队时自动让出槽位）——
都不是"用户对单个 Objective 主动叫停，之后原样恢复"这个语义。

### 改动
1. `src/mini_agent/evolution/objective_executor.py`：
   - `ObjectiveExecution` 新增 `pause_requested: bool = False` 字段（含
     `to_dict`/`from_dict` 持久化），并更新 `status` 字段上方的枚举注释，
     补充 `paused_by_user` 与另外两种暂停态的区分说明。
   - 新增 `request_pause(execution_id) -> bool`：
     - 当前 step 正在跑（`turn_id` 已存在，通常场景）时，无法立即打断，
       只记 `pause_requested = True`，等这一步真正完成（`on_turn_done`）
       时才落定，这一步的结果不受影响、正常写入；
     - 当前没有正在跑的 turn 时（比如已经是 `paused_for_fairness`，或者
       刚被资源仲裁全局暂停、还没重新提交下一步），立即落定为
       `paused_by_user`，不需要等待；
     - 已经是终止态（`completed`/`failed`/`cancelled`）或已经是
       `paused_by_user` 本身时返回 `False`，不做任何改动。
   - 新增 `resume_user_pause(execution_id) -> bool`：只对 `paused_by_user`
     状态生效，从 `current_step_idx`（断点）重新提交，不重新拆解、不
     丢失已完成 step 的进度，写法与已有的 `resume_fairness()` 一致。
   - 新增 `user_paused_objective_ids()`：供调度器识别当前处于
     `paused_by_user` 状态的 objective_id 集合。
   - `on_turn_done()`：在"检查是否全部完成"分支之后、公平性让出检查
     之前新增 `elif ex.pause_requested` 分支——优先级顺序是"完成 >
     用户暂停请求 > 公平性让出"：即使这一步执行期间用户请求了暂停，如果
     这一步恰好是最后一步，仍然正常走完成收尾，不会被暂停请求打断成
     `paused_by_user`（不合理地阻断一个已经做完的 Objective）；用户的
     暂停请求优先于公平性让出检查，避免被 fairness 分支抢先命中、误报成
     "因公平性暂停"。
2. `src/mini_agent/evolution/objective_executor.py::get_status_summary()`：
   状态摘要新增 `pause_requested` 字段，供看板显示"暂停请求已发送，等待
   当前步骤完成"这类过渡态提示（暂停请求已发出但还没真正落定时为
   `True`）。
3. `src/mini_agent/evolution/autonomous_loop.py`：
   `_tick_maintenance()` 里新起 Objective 的候选筛选新增
   `user_paused_ids` 检查——与 `fairness_paused_ids` 不同，这里**不**
   自动恢复（用户没有明确表示要继续），只是跳过本轮候选，防止调度器把
   一个用户主动暂停的 Objective 当作"已结束/可以重新 `start()`"，
   避免产生重复的 execution。
4. `src/mini_agent/api/routes.py`：新增
   `POST /v1/objectives/{execution_id}/pause`（调用 `request_pause()`）
   与 `POST /v1/objectives/{execution_id}/resume`（调用
   `resume_user_pause()`），写法与已有的 `/cancel`、`/retry`、
   `/guidance` 路由风格一致，同样走 `_objective_executor_or_404()` 鉴权。
5. `apps/mini_agent_kanban/client.py`：新增 `pause_objective()`/
   `resume_objective()` 两个封装方法。
6. `apps/mini_agent_kanban/app.py`：
   - `_render_objective_execution_detail()` 的状态文案 map 拆分出
     `paused_for_fairness`（"⏸️ 已暂停（公平调度让出）"）与
     `paused_by_user`（"📌 已暂停（用户）"），原来的 `paused` 改注明
     "（资源受限）"，避免三种暂停在看板上显示成同一个文案让用户误解；
   - 底部操作按钮从三个（终止/重试/插话）扩到四个，新增"⏸️ 暂停"/
     "▶️ 恢复"：`running`/`paused_for_fairness` 状态下显示"暂停"按钮，
     `paused_by_user` 状态下显示"恢复"按钮，与已有的终止/重试/插话按钮
     并列；
   - 暂停请求已发出但还没落定（`pause_requested=True`）时，按钮上方
     显示一行"⏸️ 暂停请求已发送，将在当前步骤完成后生效……"的过渡态
     提示，避免用户看不到反馈重复点击。

### 与已有资源仲裁全局暂停（`status == "paused"`）的关系
两者独立、不冲突：`request_pause()` 只检查当前有没有正在跑的 turn，不
关心 `ex.status` 具体是 `"running"` 还是 `"paused"`（全局暂停也可能仍有
一个已提交但还没返回结果的 turn）。落定时机统一走 `on_turn_done()` 里
基于 `turn_id` 的回调路径，与外层 `ex.status` 当时是什么无关——这意味着
即使一个 Objective 正处于资源仲裁的全局 `"paused"`，用户依然可以对它
调用 `request_pause()`，等它的当前 turn 真正完成后会落定为
`paused_by_user`（不会被 `resume()`——只恢复 `status=="paused"` 的既有
逻辑——自动恢复到 `"running"`），行为符合"用户暂停优先于自动恢复"的
直觉。

### 测试
- 新增 `tests/test_objective_user_pause.py`（8 个用例）：
  - 当前 step 正在跑时请求暂停，延迟到 `on_turn_done` 才落定，这一步
    结果不受影响；
  - `resume_user_pause()` 从断点续跑，步骤数不变、已完成 step 保留；
  - `paused_for_fairness` 状态下请求暂停立即生效；
  - 终止态（`completed`/`failed`/`cancelled`）及 `paused_by_user` 本身
    拒绝暂停请求；
  - `resume_user_pause()` 只对 `paused_by_user` 生效，其它状态（如
    `running`）返回 `False`；
  - 暂停请求发出后如果这一步恰好是最后一步且完成，正常走完成收尾，
    不被打断；
  - `user_paused_objective_ids()` 正确反映当前状态；
  - 暂停请求发出后用户改主意直接 `cancel()`，仍然正常生效。
- 回归：过滤关键词
  `objective|fairness|autonomous_loop|kanban_tracks|execution_model` 的
  157 个用例全部通过，未观察到行为回归。
- 未新增 API 路由层的独立测试：检查发现已有的 `/cancel`、`/retry`、
  `/guidance` 路由本身也没有独立的路由级测试（只在
  `ObjectiveExecutor` 层面测试对应方法），本次新增的 `/pause`、
  `/resume` 路由延续同一模式，路由本身是对已测试方法的薄封装
  （鉴权 + 参数解析 + 404 处理），风险面已被上面的单元测试覆盖。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 5 项标题与
  优先级表格状态列标记为已实现。

---

## 后续计划

按方案原有优先级表继续推进，下一项为 P1-11（干预操作的一致反馈）。
每完成一项，在本文档追加一节记录，并同步更新
`daemon_stability_and_ux_improvement_plan.md` 的状态列。
