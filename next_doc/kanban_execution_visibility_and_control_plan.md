# 看板执行可观测性 + 管控能力增强方案

> 状态：**阶段 A / B / C 均已完成**。
> 背景：完成 `daemon_task_hang_recovery_and_watchdog_hardening_plan.md`
> 三阶段（cron/心跳/reap_stale_steps 可观测性）之后，用户要求"聚焦
> 卡死回收相关能力，改进看板对这些的可见性——正在执行/出过问题/等待
> 执行分别是哪些，以及看板对这些的管理能力（中断执行、中途干预等）"。
> 本方案覆盖：(1) 实际配置里 `objective_isolated_context_enabled=true`，
> 需要补做 `ObjectiveIsolatedRunner` 的对称自愈能力（已并入上述文档
> §7，阶段四）；(2) 看板统一执行总览 + 管控入口（本文档阶段 B/C）。
> 关联代码：`src/mini_agent/evolution/cron_job_runner.py`、
> `src/mini_agent/evolution/objective_executor.py`、
> `src/mini_agent/evolution/objective_agent_bridge.py`、
> `src/mini_agent/api/routes.py`、
> `apps/mini_agent_kanban/app.py`、
> `apps/mini_agent_kanban/client.py`

## 0. 现状盘点

看板目前的相关能力分散在三处，且有明显的可观测性盲区：

| 区块 | 已有能力 | 盲区 |
|---|---|---|
| `⚙️ 执行模型` | 只读展示 persistent/isolated/heartbeat 的累计计数（reaped_job_count 等） | 只有数字，看不出"具体是哪个任务被回收的、什么时候" |
| `🎯 Objective 执行进度` | `st.json` 摆原始数据 | 无状态分组/高亮，卡死 vs 正常运行 vs 排队肉眼分不出来 |
| `⏰ Cron Jobs` | 立即运行 / 启停按钮 | `is_running()` 把"排队中"和"真正在跑"混为一谈（原方案 1.1 节已指出） |
| Objective 管控 | `cancel()`/`retry_current_step()`/`inject_guidance()` 后端已有 | 看板 UI 上是否已接出这三个操作需逐一确认，本方案会补齐缺失的入口 |

## 1.【阶段 B】后端：补齐可观测性数据源

### 1.1 cron job 排队状态区分

`CronJobRunner._running_job_ids` 目前在 `submit()` 里线程**启动前**就
加入，导致"正在排队等 semaphore"和"已经真正开始执行"在 `is_running()`
层面无法区分。新增：

- `self._sem_acquired: set[str]`（`_lock` 保护）：`_run_job_thread()`
  里 `self._sem.acquire()` **返回之后**才把 `job.id` 加入这个集合，
  线程收尾时（真正合法执行者才）移除。
- 新增查询方法 `execution_phase(job_id) -> str`：返回
  `"not_running"` / `"queued"`（在 `_running_job_ids` 但不在
  `_sem_acquired`）/ `"running"`（两者都在）。
- `CronScheduler` 新增委托方法 `execution_phase(job_id)`（未注入
  job_runner 时返回 `"not_running"`，与既有降级风格一致）。

### 1.2 卡死回收事件环形缓冲（`recent_recoveries`）

三条回收链路（cron/`reap_stale_jobs`、Objective step/`reap_stale_steps`、
isolated pool/`check_health`）目前只有累计计数，看不出"具体是谁、什么
时候"。新增一个进程内、有上限（最近 50 条）的环形缓冲，每次判定卡死时
除计数 +1 外，额外 append 一条记录：

```python
{"time": float, "kind": "cron_job" | "objective_step" | "isolated_pool",
 "id": str,       # job_id / execution_id-step_index / "pool" (isolated 是整体事件，无单独 id)
 "detail": str}   # 简短说明，比如 "超过 1500s 未收到执行结果"
```

- `CronJobRunner._reap_one_if_stale()`、
  `ObjectiveExecutor.reap_stale_steps()`、
  `ObjectiveIsolatedRunner.check_health()` 三处在各自判定卡死/触发
  重建的分支里，调用一个共享的小工具函数
  `evolution/recovery_event_log.py::record_recovery_event(kind, id, detail)`
  （新增模块，纯内存 `collections.deque(maxlen=50)` + 线程锁，无持久化，
  daemon 重启后清空——这是"最近发生过什么"的运维观测辅助，不是审计
  日志，不需要持久化）。
- `execution_model_status` 新增顶层字段 `recent_recoveries: list[dict]`
  （按时间倒序，最多 50 条）。

### 1.3 手动"立即回收"接口

新增 REST 端点 `POST /v1/self/execution_model/force_reap`，
body 可选 `{"target": "cron" | "objective_step" | "isolated_pool" | "all"}`
（默认 `"all"`），分别调用：

- `cron_scheduler.reap_stale_jobs()`（本身已按各自阈值判定，不强制降低
  阈值——"立即回收"的语义是"现在就跑一次回收扫描"，不是"无视阈值瞎回收
  正在正常运行的任务"）。
- `objective_executor.reap_stale_steps()`（同上，用当前配置的阈值）。
- `isolated_runner.check_health(force=True)`（isolated 池子是整体性的，
  `force=True` 才有意义——按当前 in-flight 数量直接判断是否需要重建，
  跳过超时等待，因为看板管理员点这个按钮就是想立刻处理"疑似卡死"）。

返回本次各链路实际回收的数量，供看板提示"已回收 N 个卡死任务"。

`_require_owner(request)` 权限校验与其它写操作端点一致。

### 1.4 处理状态：**已完成**

新增/修改：
- `src/mini_agent/evolution/recovery_event_log.py`（新增模块）：进程内
  环形缓冲（`deque(maxlen=50)`），`record_recovery_event()`/
  `recent_recovery_events()`。
- `CronJobRunner`：新增 `_sem_acquired` 集合 + `execution_phase(job_id)`；
  `reap_stale_jobs()` 判定卡死时调用 `record_recovery_event("cron_job", ...)`。
- `CronScheduler.execution_phase(job_id)`：委托给 job_runner，未注入时
  返回 `"not_running"`（与 `is_job_running()` 同一降级风格）。
- `ObjectiveExecutor.reap_stale_steps()`：判定卡死时调用
  `record_recovery_event("objective_step", "{execution_id}:{step_idx}", ...)`。
- `ObjectiveIsolatedRunner.check_health()`：触发整体重建时调用
  `record_recovery_event("isolated_pool", "", ...)`。
- `GET /v1/self/execution_model_status`：新增顶层字段
  `recent_recoveries: list[dict]`。
- `GET /v1/cron/jobs`：每个 job 新增 `execution_phase` 字段
  （`"not_running"`/`"queued"`/`"running"`）。
- `POST /v1/self/execution_model/force_reap`（新增端点）：body 可选
  `{"target": "cron" | "objective_step" | "isolated_pool" | "all"}`，
  立即对指定链路跑一次回收扫描（`isolated_pool` 用 `force=True`），
  `_require_owner` 权限校验与其它写操作端点一致。

新增测试：`tests/test_recovery_event_log.py`（环形缓冲容量/顺序）、
`tests/test_cron_job_runner.py::TestCronJobRunnerExecutionPhase`
（not_running/queued/running 三态转换）、
`tests/test_cron_scheduler_reap_stale_jobs.py`（新增 `execution_phase`
委托用例）、`tests/test_execution_model_status_routes.py`（新增
`recent_recoveries` 字段用例 + `force_reap` 端点的 all/target 过滤用例）。

回归：`cron`/`objective`/`execution_model`/`heartbeat`/`recovery_event`
相关全部 233 项测试通过，未改变任何既有端点/字段的既有语义（均为新增
字段/新增端点）。

## 2.【阶段 C】看板 UI：统一执行总览 + 管控入口

### 2.1 统一执行总览区块

在 `⚙️ 执行模型` 区块顶部新增"📋 执行总览"，四栏展示（不是摆 JSON）：

- 🟢 正在执行：`objective_executions(status=running)` 中
  `current_step` 非空的 + cron `execution_phase()=="running"` 的，
  显示标题/已运行时长/执行模式角标（persistent/isolated/shared）。
- 🟡 排队等待：cron `execution_phase()=="queued"` 的 + Objective 因
  资源仲裁/并发上限暂缓推进的（复用现有"🩺 为什么没有执行？"诊断数据）。
- 🔴 异常/已回收：直接渲染 `recent_recoveries`（阶段 B 新增字段），
  每条展示时间/链路/对象/原因；同时列出 `status=needs_review`/
  `status=failed` 的 cron job 和 Objective execution。
- ⚪ 最近完成：`status=completed`（近 30 分钟内 `finished_at`）。

每栏内容为空时展示"当前没有 XX"的提示，不留空白区块造成误解。

### 2.2 管控入口

1. **Objective**：确认 `_render_goal_card`（`app.py` 里渲染 Objective
   卡片的函数）是否已经接出 `cancel()`/`retry_current_step()`/
   `inject_guidance()` 三个按钮；若缺失，逐一补上（后端能力已存在，
   只是接线）。
2. **"🚨 立即回收"按钮**：放在"📋 执行总览"顶部，调用阶段 B 新增的
   `/v1/self/execution_model/force_reap`，回收后 `st.rerun()` 刷新，
   并用 `st.success`/`st.info` 展示"本次回收了 N 个卡死任务"。
3. **异常趋势高亮**：`⚙️ 执行模型` 区块里 `reaped_job_count`/
   `stale_step_reap_count`/`discarded_worker_count`/`pool_rebuild_count`
   四个累计数字，任一数字比看板会话内第一次读到的基线值增长时，用
   🔴 标红 + 提示"最近发生过卡死回收，建议查看下方 📋 执行总览"，把
   "要不要关注"这个判断从用户手动做减法改成看板主动提示。

### 2.3 处理状态：**已完成**

新增/修改：
- `apps/mini_agent_kanban/client.py`：新增 `force_reap(target="all")`
  客户端方法。
- `apps/mini_agent_kanban/app.py`：
  - 新增 `_render_execution_overview()`：渲染"📋 执行总览"四栏
    （🟢正在执行/🟡排队等待/🔴异常已回收/⚪最近完成），数据来自
    `autonomous_status().objective_executions` + `cron_jobs()`（含
    阶段 B 新增的 `execution_phase`）+ `execution_model_status().
    recent_recoveries`。
  - "🚨 立即回收卡死任务"按钮，调用 `force_reap("all")`，展示本次实际
    回收数量。
  - `_render_execution_model_status()` 顶部接入总览面板；末尾新增
    "🩹 卡死回收累计计数"四个 `st.metric`（cron/Objective step/持久
    Worker discard/隔离池重建），用 `st.session_state` 记录本次看板
    会话打开时的基线值，任一计数比基线增长时标红 + 提示去看总览面板，
    把"要不要关注"从用户手动比对数字改成看板主动提示。
  - Objective 的终止/重试/插话三个管控按钮此前已经接好（`cancel_objective`/
    `retry_objective`/`inject_objective_guidance`），本次盘点确认无需
    补充。

未做（见 §4"明确不做的事"）：不提供强制中断线程/热切换执行模式开关的
按钮。

回归：`python -m py_compile` 通过；`cron`/`objective`/`execution_model`/
`heartbeat`/`recovery_event`/`isolated` 相关全部 237 项后端测试通过（
看板本身是 Streamlit 脚本，不在这套 pytest 回归范围内，改动均为新增
渲染函数 + 新增按钮，未修改任何既有函数签名/既有渲染路径的行为）。

## 3. 验收标准

- 阶段 B：新增/扩展测试覆盖 `execution_phase()` 三态转换、
  `recent_recoveries` 环形缓冲的容量上限与内容正确性、
  `force_reap` 端点对三条链路的委托行为（含未注入对应 runner 时的
  降级）。
- 阶段 C：人工验证 + （如 streamlit 组件可测试）关键渲染函数的单元
  测试；不引入新的第三方依赖。
- 两阶段均不改变任何既有 API 的响应结构（只新增字段/新增端点），
  不影响现有回归测试。

## 4. 明确不做的事

- 不尝试真正"强制中断"一条已经卡死的同步调用/线程——这在 CPython 里
  没有安全的官方 API，与 `daemon_task_hang_recovery_and_watchdog_
  hardening_plan.md` 的结论一致。看板上的"立即回收"是"提前触发一次
  回收判定并代为清理记账"，不是"物理打断"。
- 不做"一键切换执行模式开关"之类的运行时热切换——`objective_persistent_
  worker_enabled`/`objective_isolated_context_enabled`/
  `scheduler_heartbeat_enabled` 仍然只能改配置文件 + 重启生效，原因见
  `⚙️ 执行模型` 区块现有注释：这不是运行时可以热切换的开关，看板上放
  一个按钮反而会让人误以为点一下就能生效。
