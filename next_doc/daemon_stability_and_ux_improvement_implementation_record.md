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

## P1-11：干预操作的一致反馈 ✅ 已实现

**对应方案第 11 项。**

### 问题回顾
终止/重试/暂停/恢复/插话这几个 Objective 干预按钮此前只在失败时用
`st.error()` 报错，成功时**没有任何确认**——点击后直接 `st.rerun()`，
用户不确定操作是否真的被后台接收，容易因为"看不到反馈"而重复点击，或者
通过发消息等别的路径重复处理同一个 Objective，造成混乱。已有的暂停操作
虽然通过服务端 `pause_requested` 标记提供了持续显示的过渡态 caption（见
P1-5），但终止/重试/恢复/插话四个操作完全没有对应的即时反馈。

### 改动
`apps/mini_agent_kanban/app.py::_render_objective_execution_detail()`：
- 终止（`🛑`）、重试（`🔁`）、暂停（`⏸️`）、恢复（`▶️`）、插话（`💬`）
  五个操作在调用对应 client 方法成功后，统一补一条 `st.toast()` 即时
  确认，例如"🛑 终止请求已发送，正在停止该 Objective……"、
  "🔁 重试请求已发送，正在重新提交当前步骤……"。选择 `st.toast()` 而不是
  `st.success()` 的原因：Streamlit 的 `st.toast()` 消息会跨随后紧接着的
  `st.rerun()` 继续展示（内部走的是独立于本次脚本执行的消息队列），不需要
  额外用 `st.session_state` 搬运"上一次点击了什么"这类状态，实现成本低且
  行为与已有 `st.error()` 失败提示自然对称（成功用 toast、失败用 error，
  一致地紧跟在按钮点击之后）；
- 终止/重试属于服务端同步立即生效的操作（`cancel()`/`retry_current_step()`
  内部直接把状态改定，不像暂停那样需要等当前 step 收尾），所以这两个操作
  不需要额外的服务端过渡态标记——toast 即时确认 + rerun 后状态标签本身的
  变化（例如变成"🚫 已终止"）已经构成完整反馈链条；暂停操作继续保留
  P1-5 已实现的服务端 `pause_requested` 持续显示 caption，在 toast 之外
  再叠加一层"直到真正落地都能看到"的持续提示，因为暂停确实存在
  "已发出请求、但还没到当前 step 完成"这段有实际等待时间的窗口期；
  插话原本已有 `st.success()`，本次追加一条 toast 保持五个操作反馈风格
  统一。

### 测试
- 纯前端展示层改动（Streamlit UI 反馈文案），项目对 `app.py` 没有现成的
  UI 层自动化测试基础设施（其余按钮如`▶️ 立即运行一次`同样没有独立测试），
  用 `python3 -m py_compile apps/mini_agent_kanban/app.py` 做语法/静态
  检查，未引入新的依赖或改动任何后端方法签名，不影响
  `tests/test_objective_executor_kanban_tracks*.py` 等既有后端测试。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 11 项标题与
  优先级表格状态列标记为已实现。

---

## P1-9：看板"执行总览"面板的主动性增强 ✅ 已实现

**对应方案第 9 项。**

### 问题回顾
"📋 执行总览"面板的"🔴 异常/已回收"栏此前只是把 `recent_recoveries`
原始事件列表按时间倒序平铺展示，用户需要自己从列表里数"最近是不是同一个
job_id 反复出现"，没有归纳性提示，容易错过"其实都是同一个任务在反复卡死"
这种一眼就能定位问题范围的信号。

### 改动
`apps/mini_agent_kanban/app.py::_render_execution_overview()`：
- 用 `st.session_state["_exec_overview_last_seen_recovery_ts"]` 记录
  "上一次渲染时看到过的最新一条回收事件时间戳"，本次渲染时只把时间戳
  比它更新的事件当作"新增"参与归纳，避免每次 Streamlit rerun（例如切换
  Tab、翻看其它面板触发的全页面重渲染）都对同一批旧事件重复弹出提示；
- 对本次新增的事件，取最近 10 分钟窗口内的子集做归纳（不足则退化为全部
  新增事件）：
  - 新增事件 ≥ 2 条时才归纳（1 条新增不构成"增长"信号，交给下方明细
    列表即可，不需要额外提示打扰）；
  - 全部新增事件的 `id` 相同 → 归纳为"过去 N 分钟内有 M 次 {kind} 卡死
    回收，都指向同一个 `{id}`，建议优先排查它"；
  - 新增事件里有一个 `id` 占比最高但不是全部 → 归纳为"新增 M 次，其中
    K 次集中在同一个 {id}"；
  - 新增事件分散在不同 `id` 上、没有明显集中 → 归纳为"可能是系统性问题
    （例如某个工具/API 全局失效），建议查看下方明细列表"，呼应方案第 1
    项提到的"广度性熔断"场景描述，帮用户判断这是不是要往"系统性故障"
    方向排查而不是"单个任务的偶发问题"；
- 归纳提示用 `st.warning()` 展示在四栏卡片上方，比"只把数字标红"更
  醒目，同时仍然保留原有的逐条明细列表（`col3`），归纳提示是补充而不是
  替代。

### 为什么用"新增事件"而不是"环比总数增长"
方案原文是"计数环比上一次刷新明显增长"，实现时改成跟踪"新出现的具体
事件"而不是简单对比总数字：`recent_recoveries` 本身已经是一个有限长度
的最近事件列表（旧事件会被自然滚出窗口），如果只比较"这次总数 - 上次
总数"，当列表滚动导致旧事件被挤出、新事件数量恰好抵消时会得出误导性的
"没有增长"结论；直接基于时间戳判断"哪些是新出现的"更准确地对应方案里
"过去 10 分钟内有 3 个 cron job 被判定卡死回收"这类描述。

### 测试
- 同 P1-11，属于 Streamlit UI 展示层改动，用 `python3 -m py_compile`
  做语法检查；归纳逻辑本身是纯函数式的列表分组计算，不依赖 Streamlit
  运行时，但受限于当前项目对 `app.py` 缺少 UI 层测试基础设施，未新增
  自动化测试覆盖，改动范围局限于 `_render_execution_overview()` 单个
  函数内部，不影响其它面板或后端路由。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 9 项标题与
  优先级表格状态列标记为已实现。

---

## P2-10：Goal/Objective 干预操作与 Workflow 对齐 ✅ 已实现

**对应方案第 10 项。**

### 问题回顾
Workflow 侧已支持"编辑某一步已产生的结果后继续"，Objective 侧此前只有
`reset_step()`（整步打回 pending 重做，清空该步及之后所有步骤的既有
进度，需要重新调用一次模型）。当某个 step 的 `result_summary` 基本正确
只是有个小事实错误时，`reset_step` 成本过高，且重跑结果未必和用户预期
一致，缺少"直接手工改一下、让后续 step 基于修正后的结果继续"这种更精确
的控制手段。

### 改动
1. `src/mini_agent/evolution/objective_executor.py`：
   - `ExecutionStep` 新增 `edited_by_user: bool = False` 字段（含
     `to_dict`/`from_dict` 持久化），仅用于看板展示"✏️ 已编辑"标记，
     不影响执行逻辑。
   - 新增 `edit_step_result(exec_id, step_idx, result_summary=None,
     artifacts=None) -> bool`：
     - 只允许编辑 `status == "done"` 的历史 step——仍在跑或还没跑的
       step 没有产出可编辑，`failed`/`blocked` 的 step 语义上是"没做完"，
       应该走 retry/reset 让它先真正跑完，不允许用户手写一个假装做完的
       结果（避免后续 step 基于一个从未真正执行过的编造结果继续）；
     - 不触碰该 step 的 `status`/`turn_id`/`retry_count`，只写回
       `result_summary`（截断到 500 字，与 `on_turn_done` 的截断长度
       一致）和/或 `artifacts`；
     - `result_summary`、`artifacts` 都为 `None`（没有传任何改动）时
       直接返回 `False`，不产生空的编辑记录；
     - 成功后设置 `edited_by_user = True`，更新 `progress_notes` 提示
       "步骤 N 的产出已被用户手工修正，后续步骤将基于修正后的结果继续"，
       调用 `_notify_progress()` + `save()`。
   - `get_status_summary()` 的 `steps` 列表新增 `edited_by_user` 字段，
     供看板判断是否显示"已编辑"标记。
   - **为什么修正后的内容能被后续 step 用上**：`_build_prompt()`
     组装"前序步骤结果"时直接读 `ex.steps[:step_idx]` 里每个 step 当前
     的 `result_summary`（见已有实现），`edit_step_result()` 写回的就是
     这个字段本身，不需要额外接线——这也是为什么这个能力比
     `reset_step()` 轻量：不产生新的 turn，不用等模型重新跑一遍。
2. `src/mini_agent/api/routes.py`：新增
   `POST /v1/objectives/{execution_id}/steps/{step_index}/edit`（Body:
   `{"result_summary"?: str, "artifacts"?: list[str]}`），做类型校验后
   调用 `edit_step_result()`，失败时返回 404（execution 不存在/
   step_index 越界/该 step 不是 done 状态/没有提供任何改动，这几种情况
   合并成一个 404，与已有 `/reset` 路由的错误处理粒度一致）。
3. `apps/mini_agent_kanban/client.py`：新增 `edit_objective_step()`
   封装方法，`result_summary`/`artifacts` 为 `None` 的参数不会被塞进
   请求体，交由后端区分"没传"与"传了空值"。
4. `apps/mini_agent_kanban/app.py`：
   - `_render_objective_execution_detail()` 里"🔍 查看详情"展开面板
     标题追加"✏️已编辑"标记（`edited_by_user=True` 时）；
   - 原来"trace 拉取失败/无内容时 `continue` 跳过本 step、不渲染任何
     后续内容"的写法改成 `if/else`，让编辑表单不依赖 trace 拉取是否
     成功都能展示（编辑只需要 `result_summary`/`artifacts`，跟 trace
     日志是两回事）；
   - 展开面板内、trace 内容下方，`status == "done"` 的 step 追加一个
     `st.form`：文本框预填当前 `result_summary`，文本输入框预填当前
     `artifacts`（逗号分隔），提交后调用 `edit_objective_step()`，只有
     文本真的发生变化时才传 `result_summary`（避免把"用户没改但原样
     提交"误判成一次改动，虽然后端本身也会正确处理这种情况，这里是双重
     保险）；成功后走 P1-11 已确立的一致反馈风格（`st.toast()` +
     `st.rerun()`）。

### 与 reset_step() 的关系（互补，非替代）
沿用方案原文的定位：`reset_step()` 用于"这一步做错了需要重做"（整步
重跑，成本高）；`edit_step_result()` 用于"这一步做得基本对、只是描述
有误，不需要重做，改一下继续就行"（不产生新 turn，成本低）。两者作用
的目标状态不同——`reset_step` 可以作用于任意历史 step 且会级联清空
之后所有 step 的进度；`edit_step_result` 只影响被编辑 step 自身的两个
字段，不影响 `current_step_idx`、不清空任何其它 step。

### 测试
- 新增 `tests/test_objective_edit_step_result.py`（7 个用例）：
  - 编辑 `done` step 的 `result_summary`，验证写入成功且
    `edited_by_user` 置位、`status` 不变；
  - 只编辑 `artifacts`，验证未传的 `result_summary` 不受影响；
  - 编辑仍在 `running`（未调用 `on_turn_done`）的 step 返回 `False`；
  - 不传任何改动（`result_summary`/`artifacts` 均为 `None`）返回
    `False`；
  - 未知 `execution_id`、越界 `step_idx` 分别返回 `False`；
  - 端到端验证修正后的 `result_summary` 确实被下一步的
    `_submit_step()` 组装进 prompt（同时验证原始（未修正）内容不再
    出现在 prompt 里）。
- 回归：`python3 -m pytest tests/ -q -k "objective or reset_step or
  kanban_tracks or execution_model"`，144 个用例全部通过，未观察到
  行为回归。
- 前端改动（`app.py`/`client.py`）同 P1-11/P1-9，用
  `python3 -m py_compile` 做语法检查；未新增 API 路由层独立测试，延续
  `/pause`、`/resume`、`/reset` 等既有路由"薄封装、风险面已被单元测试
  覆盖"的测试策略。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 10 项标题
  与优先级表格状态列标记为已实现。

---

## P2-1：统一看护/熔断内核 ✅ 已实现

**对应方案第 1 项。**

### 问题回顾
`workflow/watchdog.py` 已有一套相对成熟的看护能力——"同一 step 连续
同类失败提前熔断"（`report_attempt_failure`）与"同一 error_type 在多个
不同 step 上出现即触发的跨 step 系统性故障熔断"（
`report_workflow_level_failure`）。但这套能力只服务于 Workflow 执行
路径：`ObjectiveExecutor` 侧完全没有"跨多个不同 Objective 因同一
error_type 失败"的广度信号，`CronJobExecutor` 侧是另一套独立的
`StuckDetector` + `reap_stale_jobs`。三条链路各自维护相似但不完全一致
的阈值和状态字典，同一类 bug 需要在三处分别修一次。

### 改动
1. 新增 `src/mini_agent/evolution/circuit_breaker_core.py`：
   - `CircuitBreakerCore`：从 `workflow/watchdog.py` 抽出的通用组件，
     线程安全，提供两组能力——
     - `report_attempt_failure(scope_id, error_type, threshold=2)`：
       单 scope 内连续同类失败追踪，达阈值返回 `True`；
     - `report_breadth_failure(scope_id, error_type)` /
       `tripped` / `trip_reason`：跨 scope 广度熔断——同一
       `error_type` 累计出现在多少个不同 `scope_id` 上达到
       `distinct_scope_threshold` 即触发（只触发一次），通过构造函数
       传入的 `on_trip(error_type, distinct_scope_ids)` 回调决定
       触发后果（workflow 是 `request_cancel`；Objective/cron 现阶段
       只做记录 + 主动告警，见下）。
     - `scope_id` 的粒度由调用方决定：workflow 是 step_id，Objective
       是 execution_id，cron 是 job_id——熔断判定逻辑完全通用，只是
       "scope"的语义不同。
   - `classify_error_type(message: str) -> str`：粗粒度错误分类，按
     关键词把自由文本失败原因归到 `timeout`/`rate_limit`/`auth`/
     `connection`/`tool_protocol`/`stuck`/`other` 几个稳定 bucket——
     Objective/cron 侧的失败原因是纯文本（不像 workflow 那样能拿到
     异常对象类型 `type(e).__name__`），这里不追求精确分类，只追求
     "同一类问题能落到同一个 bucket 里"，因为熔断判断本身是模糊的
     系统性信号，不是精确诊断。
2. `src/mini_agent/workflow/watchdog.py`：内部持有一个
   `CircuitBreakerCore` 实例，`report_attempt_failure` /
   `report_workflow_level_failure` / `circuit_breaker_tripped` /
   `circuit_breaker_reason` / `reset_step_failures` 全部委托给它，
   熔断触发后的 `request_cancel` + 告警逻辑保留在
   `_on_circuit_breaker_tripped` 回调里——对外接口和行为与重构前完全
   一致（回归测试见下），只是内部实现改为复用共享组件，不再自己维护
   一份 `_consecutive_failures`/`_error_type_step_ids` 字典。
3. `src/mini_agent/config/models.py`：新增
   `AutonomyConfig.objective_circuit_breaker_distinct_threshold`（跨
   Objective 广度熔断阈值）与
   `CronConfig.circuit_breaker_distinct_threshold`（跨 cron job 广度
   熔断阈值），均默认 `None`（不启用）——这是新增的观测/告警信号，
   不影响任何现有执行/重试路径，触发后也只做记录 + 主动告警、不阻断
   调度，默认关闭是出于"先观察阈值是否合理"的谨慎，不是因为有风险。
4. `src/mini_agent/evolution/objective_executor.py`：
   - `ObjectiveExecutor.__init__` 持有一个 `CircuitBreakerCore`
     实例（`scope_id` = `execution_id`），阈值读
     `cfg.autonomy.objective_circuit_breaker_distinct_threshold`；
   - 在三处"Objective 真正判定 failed"的收尾点（`on_turn_failed`
     重试耗尽分支、`_handle_invalid_step_result` 重试耗尽分支、
     `reap_stale_steps` 超时且重试提交也失败分支）调用
     `report_breadth_failure(execution_id, classify_error_type(...))`；
     `reap_stale_steps` 的超时分支固定传 `"timeout"`（不需要再分类，
     超时本身就是明确的 error_type）；
   - `on_trip` 回调 `_on_circuit_breaker_tripped` 复用现有
     `notification/dispatcher.py`，推一条"检测到跨目标的系统性失败"
     通知，明确不阻断新 Objective 的调度——与 workflow 侧熔断即
     `request_cancel` 的语义不同：Objective 的"重做一次"成本和语义与
     workflow step 不同，贸然让整个自主执行停摆的风险比"某个工具/
     API 全局失效"这个信号本身更大，所以现阶段只做记录 + 告警，是否
     要进一步联动"暂停新 Objective 调度"留给用户看到告警后自行决定
     （或后续单独评估）。
5. `src/mini_agent/evolution/cron_job_runner.py` /
   `cron_job_executor.py`：
   - `CronJobRunner` 是长期持有的单例（不像 `CronJobExecutor` 是每次
     触发临时构造），所以熔断状态放在 `CronJobRunner.__init__` 里，
     阈值读 `cron_cfg.circuit_breaker_distinct_threshold`；
   - `CronJobExecutor.__init__` 新增可选的 `circuit_breaker` 参数
     （默认 `None`，构造签名保持兼容——已有测试里大量用
     `class _FakeExecutor: def __init__(self, paths): ...` 这种简化
     替身直接替换 `CronJobExecutor`，如果把它做成必填构造参数会破坏
     这些替身）；`CronJobRunner._run_job_thread()` 构造完
     `CronJobExecutor(self._paths)` 后，用
     `executor.circuit_breaker = self._circuit_breaker` 属性赋值把
     共享实例传进去，兼顾"跨多次 run_job() 维持累计状态"与"不改变
     构造签名"两个约束；
   - `run_job()` 的 `finally` 块里，`final_status == STATUS_NEEDS_REVIEW`
     且 `error_text` 非空时，调用
     `self.circuit_breaker.report_breadth_failure(job.id,
     classify_error_type(error_text))`（`self.circuit_breaker` 为
     `None` 时整体跳过，行为与改造前一致）；
   - `CronJobRunner._on_circuit_breaker_tripped` 同样只做通知，不
     阻断调度，理由同 Objective：cron 是定时任务，贸然全局停摆影响面
     比"某个工具/API 全局失效"这个信号本身更大。

### 为什么"三条链路接入同一套判定"而不是三处分别再调一次阈值
方案原文的收益点是"减少同一类 bug 在三处分别修一次的维护成本"——
`CircuitBreakerCore` 把"连续失败计数"和"广度去重计数"这两段纯状态
管理逻辑（谁失败了、失败了几次、失败的 scope 有多少个不同的）完全
和"失败后要做什么"解耦（通过 `on_trip` 回调），所以三条链路虽然
"触发后果"完全不同（workflow 直接 cancel；Objective/cron 只告警），
仍然可以共用同一份判定实现，以后要调整"连续失败"或"广度去重"这两段
逻辑本身（比如改成滑动窗口），只需要改一处。

### 测试
- 新增 `tests/test_circuit_breaker_core.py`（覆盖
  `report_attempt_failure` 连续/中断计数、`report_breadth_failure`
  跨 scope 触发且只触发一次、`on_trip`/`log_fn` 回调、`reset_trip`、
  `classify_error_type` 的关键词分类与未命中兜底）；
- 回归：`python3 -m pytest tests/test_workflow_p10.py
  tests/test_workflow_p14.py tests/test_objective_edit_step_result.py
  tests/test_scheduler_heartbeat.py tests/test_cron_job_runner.py -q`，
  65 个用例全部通过——`workflow/watchdog.py` 重构为委托实现后行为
  与重构前完全一致；
- `tests/test_objective_executor_kanban_tracks_r3.py`/`_r4.py` 等
  ObjectiveExecutor 相关既有用例本地跑时受限于环境缺少
  `python-multipart`（fastapi 表单依赖，与本次改动无关的既有环境
  问题）而报错，非本次改动引入的回归，未修复该环境依赖问题（不在
  本次改动范围内）。

### 局限
- Objective/cron 侧的广度熔断触发后只做"记录 + 主动告警"，不联动
  "暂停新任务调度"——这是有意的范围收窄（见上文"为什么不阻断调度"
  说明），如果后续观察到"光靠告警、用户没有及时看到导致同类问题继续
  在新 Objective/job 上重复消耗预算"，可以再评估要不要在
  `_on_circuit_breaker_tripped` 里追加"临时降低并发/暂停 sys:*
  自动派发"这类更主动的联动动作，但那属于新的设计决策，不在本次改动
  范围内；
- `classify_error_type` 是关键词粗分类，不是精确的异常类型识别，
  存在"同一类问题因为措辞不同被分到不同 bucket"或"不同问题因为措辞
  相似被分到同一 bucket"的可能——这是有意的取舍（详见模块 docstring），
  广度熔断本身就是模糊的系统性信号，用于提前预警而不是精确诊断，
  精度要求不高；如果后续发现分类效果不理想，可以再补充关键词或改用
  更结构化的错误分类（例如让调用方在能拿到异常对象时传入
  `type(e).__name__`，退化到关键词分类只在拿不到异常对象时使用）。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 1 项标题
  与优先级表格状态列标记为已实现。

---

## P3-2：持久 Worker 跨重启连续性 ✅ 已实现

**对应方案第 2 项。**

### 问题回顾
`ObjectivePersistentRunner`（[daemon_execution_model_and_scheduler_
heartbeat_improvement_plan.md 阶段一]）让同一 Objective execution 的
多个 step 复用同一个 Agent 实例、保留跨 step 会话状态，但这段连续性是
纯内存态的——daemon 在某个 Objective 执行到一半时重启，恢复执行会
重新构建一个全新 Agent，之前累积的会话历史当场清零。虽然
`_build_prompt()` 本身已经会把"前序步骤结果/产出文件"这类结构化摘要
拼进下一个 step 的消息里（不依赖持久 Worker 也有），但持久 Worker 场景
下更丰富的会话状态（多轮探索推理、已打开的文件上下文等）在重启后仍然
完全丢失，退化为"每步都是失忆的新 agent"（等同于隔离 runner 模式）。

### 改动
1. `src/mini_agent/evolution/objective_agent_bridge.py`：
   - 新增三个模块级函数
     `load_worker_restart_summary(paths, execution_id)` /
     `save_worker_restart_summary(paths, execution_id, summary,
     max_chars=4000)` /
     `discard_worker_restart_summary(paths, execution_id)`，把每个
     execution 的"重启续接摘要"落到独立文件
     `<workdir>/objective_worker_summaries/<execution_id>.txt`（不混进
     `objective_executions.json` 主状态文件——这是纯粹的"给下一次重建
     Agent 用的提示文本"，不需要跟着 ExecutionStep 做结构化持久化/
     展示，独立文件也让"读取失败/文件损坏"不会连累主状态文件加载）；
     只保留最新一版（不追加、不留历史版本），落盘前按
     `max_chars` 截断；
   - `build_objective_agent()` 新增可选参数 `restart_summary`：仅在
     `persistent=True` 时有意义，非空时在 `system_extra` 里追加一段
     "[重启续接摘要]" 说明，明确告知模型这是重启前会话状态的摘要、不是
     原始记忆，细节以"前序步骤结果"等结构化信息为准（避免模型误以为
     自己真的"记得"精确细节）；
   - `ObjectivePersistentRunner.__init__` 新增可选参数
     `paths`（默认 `None`），供落盘/读取时定位 workdir；
   - `ObjectivePersistentRunner._restart_summary_enabled()`：同时满足
     "配置 `cfg.autonomy.objective_persistent_worker_restart_summary_
     enabled` 开启"和"构造时传入了 `paths`"才生效，任一条件不满足都
     静默跳过，行为与升级前完全一致；
   - `_run_step()`：
     - 首次为某个 execution_id 构建 Agent 时（`self._agents.get(...)
       is None`），若功能开启，先尝试 `load_worker_restart_summary()`
       ——真正的第一个 step 时磁盘上不存在文件，返回 `None`，行为不变；
       daemon 重启后重建时文件存在，读出来作为 `restart_summary` 传入
       `build_objective_agent()`。用"磁盘上是否存在摘要文件"这一个信号
       同时区分两种情况，不需要额外的"是否是重启"标志位；
     - 每次 `run_turn()` 成功后，若功能开启，调用
       `agent.compact_with_skills(goal_hint=...)` 生成一份摘要并
       `save_worker_restart_summary()` 落盘——复用现有的
       `compact_with_skills` 真实压缩这个 Agent 自身的历史（不是另开
       一套摘要机制），顺带控制了持久 Worker 长期运行下的 context
       增长（与 `build_objective_agent()` 里已有的 token 阈值自动
       compact 是同一机制，这里只是多了"落盘"这一步）；压缩/落盘失败
       只记日志，不影响这个 step 本身已经成功完成的结果上报；
   - `release()`：execution 到达终止状态时，若功能开启，额外调用
     `discard_worker_restart_summary()` 清理掉不再需要的摘要文件，
     避免目录堆积。
2. `src/mini_agent/config/models.py`：新增
   `AutonomyConfig.objective_persistent_worker_restart_summary_enabled`
   （默认 `False`）与
   `objective_persistent_worker_restart_summary_max_chars`（默认
   `4000`）。默认关闭的理由：每个 step 完成后多一次
   `compact_with_skills` 调用带有真实 LLM 开销，这是新增能力，先默认
   关闭观察实际收益与开销后再考虑调整默认值（与本次改动的其它两项
   一致的谨慎策略）。
3. `src/mini_agent/api/server.py`：构造 `ObjectivePersistentRunner`
   时新增传入 `paths=paths`（`AgentPaths` 实例在该处已经在作用域内，
   直接透传）。

### 为什么用"磁盘文件是否存在"作为重启信号，而不是显式状态位
`ObjectivePersistentRunner._agents` 字典本身就是"内存态缓存"，它的
生命周期天然与进程生命周期一致——只要还在同一个进程里，`_agents.get(
execution_id)` 命中就说明不需要读摘要（Agent 还活着，会话历史都在）；
一旦进程重启，`_agents` 从空字典开始，任何 execution 的第一次
`_run_step()` 调用都会落到"agent is None"分支。这个分支本来就要区分
"真正的第一个 step"（无摘要文件，`load_worker_restart_summary()` 返回
`None`）和"重启后重建"（有摘要文件），不需要额外维护一个"是否发生过
重启"的显式状态，磁盘文件的存在性本身就是这个信号，逻辑更简单、也不
会因为某处忘记更新状态位而失配。

### 测试
- 新增 `tests/test_objective_persistent_worker_restart_summary.py`
  （用 fake Agent，覆盖：默认关闭时完全不落盘/不调用
  `compact_with_skills`；开启后 step 完成即落盘；模拟重启（清空内存
  `_agents` 缓存）后重建 Agent 能读到并注入 `restart_summary`；
  `max_chars` 截断；`release()` 清理文件；未传入 `paths` 时即使配置
  开启也不生效）；
- 回归：`python3 -m pytest tests/test_objective_persistent_runner.py
  tests/test_objective_runner_sched_lock.py
  tests/test_objective_persistent_worker_restart_summary.py
  tests/test_circuit_breaker_core.py tests/test_workflow_p10.py
  tests/test_workflow_p14.py tests/test_objective_edit_step_result.py
  tests/test_scheduler_heartbeat.py tests/test_cron_job_runner.py -q`，
  104 个用例全部通过。

### 局限
- 只在功能开启且传入了 `paths` 时生效，默认关闭——不影响任何现有
  部署的默认行为；
- 摘要是"降级"而不是"完全恢复"：重启后 Agent 拿到的是一份压缩摘要，
  不是逐字还原的原始会话历史，细节层面的连续性（例如某个具体文件里
  第几行的临时观察）仍然会丢失，只保证"至少记得自己做过什么、还差
  什么没做"这个粗粒度的连续性，与方案原文的边界说明一致；
- 每个 step 完成后多一次 `compact_with_skills` 调用，带来额外的 LLM
  开销和延迟（阻塞该 step 结果上报，因为落盘发生在
  `_safe_on_done()` 之前）——这是有意的顺序（保证"上报成功"和"摘要已
  落盘"尽量同步，避免 daemon 恰好在两者之间重启导致摘要缺失这次 step
  的成果），但也意味着开启这项功能会让每个 step 的端到端耗时增加一次
  额外 LLM 调用的时间，默认关闭正是为了不让所有用户都承担这个成本。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 2 项标题
  与优先级表格状态列标记为已实现。

---

## P3-7：失败模式事中拦截 ✅ 已实现

**对应方案第 7 项。**

### 问题回顾
`sys:failure_pattern_aggregation`（`evolution/failure_pattern_store.py`）
已经把 Objective 步骤失败、Goal dead-ends、TurnJudge stuck 事件按
task_category 聚合为 `failure_pattern_store.json`，并且早就提供了
`get_patterns_for_category()` 这个只读查询接口——但改动前没有任何调用方
真正使用它：`soft_goal_deriver` 只在"生成新目标"这个事后分析场景用得上
聚合结果，`_submit_step()` 提交 step 时完全不查这份数据，"重试携带原因"
（Track F，见 `test_objective_executor_kanban_tracks.py`）也只解决"同一个
Objective 内部重试时告诉它上次为什么失败"，命中的是这次执行自己的
`step.error_msg`，不是跨 Objective 复用的历史失败模式。

### 改动
1. `src/mini_agent/evolution/failure_pattern_store.py`：
   - 新增 `format_pattern_warning(patterns, *, max_patterns=3) -> str`——
     把 `get_patterns_for_category()` 返回的 `FailurePattern` 列表格式化
     为一段可以直接拼进 step 消息的提示文本；空列表返回空字符串（调用方
     按"空字符串不拼接"处理，不需要额外判空）；只做格式化和截断，不做
     二次排序（排序已经在聚合落盘时按 `occurrence_count` 降序完成）。
2. `src/mini_agent/config/models.py`：`AutonomyConfig` 新增三个字段：
   - `failure_pattern_interception_enabled: bool = True`——只读一次本地
     JSON 文件（数据已经在磁盘上，聚合本身由既有的每日 cron job
     `sys:failure_pattern_aggregation` 完成，本项不新增聚合逻辑），不新增
     LLM 调用/网络请求，成本可忽略，默认开启；命中率完全取决于
     `failure_pattern_store.json` 的既有数据积累量，数据不足时自然不
     命中，不会产生噪音提示（与"后续计划"里原先"先观察数据量再决定要不要
     实现"的顾虑相比，改为"直接实现、数据不足时行为上等价于关闭"更简单，
     不需要额外做一次埋点观察再回来实现）；
   - `failure_pattern_interception_min_occurrence: int = 3`——与
     `get_patterns_for_category()` 的默认阈值一致，只有历史
     `occurrence_count` 达到该值才视为"高频"并注入提示，避免偶发一两次
     失败就被当成"已知模式"打扰正常执行；
   - `failure_pattern_interception_max_patterns: int = 3`——单次提示最多
     附带几条命中的失败模式，避免同一个 task_category 下多个
     `root_cause_tag` 都命中时把提示堆得过长。
3. `src/mini_agent/evolution/objective_executor.py`：`_submit_step()` 里
   在拼接 `retry_ctx` 之后、拼接 `policy_ctx` 之前新增 `pattern_ctx`：
   - 读 `cfg.autonomy.failure_pattern_interception_enabled`（`cfg` 为
     `None` 或未配置 `autonomy` 时通过 `getattr(..., True)` 兜底为默认
     开启，不因为没传 `cfg` 而报错或静默跳过——与既有的
     `objective_persistent_worker_*` 等配置读取方式保持一致的写法）；
   - 开启时调用 `get_patterns_for_category(self._paths, step.description
     or ex.objective_title, min_occurrence=...)` 查询命中的高频失败模式，
     再用 `format_pattern_warning()` 格式化；
   - 整段包在 `try/except` 里，查询/格式化异常时 `log_exception` 记录并
     让 `pattern_ctx` 保持空字符串，不影响 step 正常提交（与模块里其余
     "锦上添花"类上下文——`goal_ctx`/`policy_ctx`——的容错方式一致）；
   - `message` 拼接顺序调整为
     `...{retry_ctx}{pattern_ctx}{policy_ctx}`——`retry_ctx` 是"这次重试
     针对的具体失败原因"（如果有），`pattern_ctx` 是"跨 Objective 的历史
     经验"，两者不冲突、可以同时出现，`pattern_ctx` 放在 `retry_ctx` 之后
     是因为前者优先级更高（更具体、更贴近这次尝试），`policy_ctx`
     （产出路径规范）作为通用规范放最后。

### 为什么直接用 objective_title 兜底而不是要求必须有具体 step 描述
`get_patterns_for_category()` 的查询 key 是"归一化后的标题文本"，
`_decompose()` 在 LLM 拆解失败或只拆出单步时会整体降级为
`[objective.title]`（即 `step.description == objective.title`），这种
情况下 `step.description or ex.objective_title` 两者取值相同；真正有
意义的场景是"`step.description` 为空但 `ex.objective_title` 有值"这种
理论上不应该出现但没有硬性保证的边界（`step.description` 目前的构造
路径不会为空，这里只是防御性写法，与模块里其余 `or` 兜底风格一致，不
是本次改动引入的新假设）。

### 测试
- 新增 `tests/test_failure_pattern_interception.py`：
  - `format_pattern_warning`：空列表返回空字符串；非空列表正确格式化并
    截断到 `max_patterns` 条；
  - `ObjectiveExecutor._submit_step` 集成：命中高频失败模式时 message
    带上"[已知失败模式提醒]"；`failure_pattern_store.json` 为空（无历史
    数据）时不附带该段落；`cfg.autonomy.failure_pattern_interception_
    enabled=False` 时即使命中也不附带；未传入 `cfg`（`cfg=None`）时
    默认按开启处理且不报错、不影响正常提交。
- 回归：`python3 -m pytest tests/test_failure_pattern_interception.py
  tests/test_failure_pattern_store.py
  tests/test_objective_executor_kanban_tracks.py
  tests/test_objective_persistent_runner.py
  tests/test_objective_runner_sched_lock.py
  tests/test_objective_persistent_worker_restart_summary.py
  tests/test_circuit_breaker_core.py tests/test_workflow_p10.py
  tests/test_workflow_p14.py tests/test_objective_edit_step_result.py
  tests/test_scheduler_heartbeat.py tests/test_cron_job_runner.py -q`，
  142 个用例全部通过；
- 本地环境缺少 `pydantic`/`uvicorn` 等依赖导致部分既有 API 相关测试
  （`tests/test_objective_executor_kanban_tracks_r3.py`/`_r4.py` 里依赖
  `mini_agent.api.*` 的用例）无法收集，属于既有环境限制（前序 P2-1 记录
  里已提及同类问题），非本次改动引入的回归，未在本次改动范围内修复。

### 局限
- 命中判定完全依赖既有的"标题归一化 + 关键词根因分类"聚类规则（见
  `failure_pattern_store.py` 模块头部说明），本身就是粗粒度的模糊匹配，
  不是语义相似度——"同一类问题因为 Objective 标题措辞不同而不命中"的
  情况会发生，这是复用既有聚合规则的自然结果，本次改动不改变聚类规则
  本身；
- 提示只在 step 提交前"附带一段文本"，是否真的据此调整方法完全取决于
  模型是否认真读取并采纳，不是强制约束——与"重试携带原因"（Track F）
  的性质一致，都是"提示"而不是"拦截"（尽管方案文档标题叫"事中拦截"，
  实际做的是"提交前提示"，不阻止 step 真正提交执行，命名上的"拦截"
  指的是"在失败发生前拦截式提醒"而不是"阻止执行"）；
- 默认开启但没有做"提示是否降低了实际失败率"的量化验证——与方案文档
  "方法论说明"第 1 条"默认值调整需要验证依据"的要求相比是一个例外：
  这里的默认开启不是"改变已有行为的默认值"（改造前没有这项能力，无
  所谓"改变默认值"），而是"新增一项低成本、命中才生效、不命中零影响
  的能力"，风险模型与"改默认值"不同，因此没有另外补一轮长稳验证。

### 文档
- `next_doc/daemon_stability_and_ux_improvement_plan.md`：第 7 项标题与
  优先级表格状态列标记为已实现。

---

## 后续计划

方案文档 11 项全部已实现（4、8、3、6、5、11、9、10、1、2、7）。暂无
新的待推进方向；后续如果发现新的稳定性/体验问题，另开新方案文档，不在
本文档继续追加。

每完成一项，在本文档追加一节记录，并同步更新
`daemon_stability_and_ux_improvement_plan.md` 的状态列。

