# Cron 任务专属执行机制 使用指南

- **设计文档**：`next_doc/cron_dedicated_execution_improvement_plan.md`
- **实施记录**：`next_doc/cron_dedicated_execution_implementation_record.md`
- **前置依赖**：daemon 模式的基础 cron 调度（`evolution/cron_scheduler.py`，
  见 [具身智能改进指南](autonomous_daemon_design.md) 里 CronScheduler 的
  说明）——本文只讲"job 到期之后，具体怎么被执行"这一段，job 的增删改查、
  schedule 语法本身不重复展开。
- **当前状态**：Track A-M 全部完成，核心执行链路、REST API、看板 tab、
  正式配置字段、单元测试（58 项）均已落地。

---

## 1. 这套机制解决什么问题

daemon 里的 cron job（比如"每天 22:00 生成日报""每 6 小时跑一次巩固循环
扫描"）原本和用户消息共用同一条 `InputQueue` + 单线程 `AgentRunner`：

- 一个 cron job 跑得久，会卡住其它 job 和用户消息——daemon 表现为"卡住
  不回复"
- 没有单任务的超时/步数上限，极端情况下一个任务可能无限跑下去
- 每次触发都是全新的一条消息，没有"上次做到哪了"的状态，需要多次触发
  才能做完的任务每次都从头开始
- 没有"是否卡住"的判断，输出重复雷同、原地打转的任务会一直被重复调度、
  浪费预算
- 看板看不到执行细节，排查"这次到底跑了什么"只能翻 daemon 日志

Cron 任务专属执行机制把 cron job 的**实际执行**搬到独立后台线程，加上
超时/步数/卡死检测三重兜底、每 job 专属的进度存档文件夹，解决以上问题。
**cron 任务本身如何配置（schedule、task_template）不变**，改变的只是
"到期之后怎么跑"。

## 2. 整体执行链路

```
CronScheduler.tick() 到期
  → _fire(job)
  → CronJobRunner.submit(job)          # 立即返回，不阻塞主线程
       └─ 独立后台线程：
            build_cron_agent(job)      # 全量继承主 Agent 工具集的专用 Agent
              → CronJobExecutor.run_job(job, submit_step_fn)
                   while 未超时 且 未超步数:
                       result = submit_step_fn(prompt)   # 一次 run_turn()
                       判断完成 / 继续 / 卡死
                   写回 state.json / runs/<run_id>.jsonl
```

- **不阻塞主线程**：`AgentRunner`（daemon 的单线程主循环）只负责
  `submit()` 一下就返回，真正执行发生在独立线程里，用户消息和其它到期
  job 不会被挤占。
- **未注入 `job_runner` 时自动回退旧路径**（直接 `submit_fn` 塞回
  `InputQueue`），向后兼容不升级配置的部署——这条回退路径完全是内部
  兼容性考虑，普通使用不需要关心。

## 3. 并发控制

`CronJobRunner` 用 `threading.Semaphore` 限制同时执行的 cron job 数量
（默认 2，见 §6 全局配置），超出上限的 job **在线程内部排队等待**，
不会丢失触发——只是延后开始。

同一个 job 如果上一次执行还没跑完，本次触发会被直接拒绝（避免调度
间隔比单次执行还短的极端配置导致同一 job 并发跑两份）。

## 4. 执行循环：超时 + 步数 + 卡死检测

`CronJobExecutor.run_job()` 是一个同步循环，每一"步"调用一次
`submit_step_fn`（底层是一次完整的 `agent.run_turn()`）：

```
while True:
    if now >= deadline:            → 超时收尾（status = timed_out）
    if step_index >= max_steps:    → 超步数收尾（status = timed_out）
    result = submit_step_fn(...)   → 单步异常不让整个 job 崩溃
    if result.error:               → status = needs_human_review，收尾
    if result.done:                → 正常完成（status = idle），收尾
    卡死检测（见下）
```

**"是否完成"的判断**优先级：

1. 输出末尾出现 `[CRON_DONE]` 标记 → 明确完成
2. 输出末尾出现 `[CRON_CONTINUE]` 标记 → 明确未完成，继续
3. 都没出现时，用"本次 `run_turn()` 是否自然结束"兜底：没撞到内层
   `max_turns` 预算就认为完成，被预算打断就认为未完成

这两个标记由 cron 专用 Agent 的 system prompt 里的约定注入（见 §5），
你不需要在自己的 `prompt.md` 里手写这句话，但如果任务确实是"分批处理"
类型，写清楚判断"是否已经全部处理完"的标准会让模型更准确地打对标记。

**卡死检测**复用已有的 `StuckDetector`：连续输出相似度过高 → 先尝试
`RECOVER`（继续但换个角度），恢复次数耗尽后判定 `GIVE_UP`，标记
`needs_human_review` 并停止调度，直到你在看板上手动重置。

这是"内层限步数（`max_turns`）、外层限墙钟时间+步数
（`timeout_seconds`/`max_steps`）"的双重兜底：单次 `run_turn()`
调用本身不会无限跑，即使某一步异常复杂也会先撞到内层预算，把控制权
交还给外层循环。

## 5. cron 专用 Agent

每次 job 触发都重新构建一个全新的 Agent（`cron_agent_bridge.
build_cron_agent()`），不跨触发复用同一个 Agent/history：

- **全量继承主 Agent 的工具集**（不做工具白名单裁剪，按用户明确要求）
- `auto_approve=True`：无人值守场景，工具调用自动批准
- system prompt 会自动追加一段说明当前是 daemon 后台定时任务身份、
  无法等待人类澄清、要求在最后一行输出 `[CRON_DONE]` 或
  `[CRON_CONTINUE]` 的约定
- 单次 `run_turn()` 内部的 `max_turns` 预算默认 15（可通过全局
  `cron.inner_max_turns` 配置调整，见 §6）

"上次做到哪了"的连续性**不是**靠保留 Agent 对象或完整对话历史实现的，
而是靠 §7 的 `progress_summary` 文本摘要拼进下一次触发的 prompt——这样
可以避免 cron 任务的历史无限增长，也避免和用户会话的 session 存储混
在一起。如果某类任务确实需要精细的多轮上下文，目前只能靠这段摘要
本身写得足够详细；更结构化的 checkpoint 机制尚未实现（见实施记录的
「剩余工作」）。

## 6. 每个 job 的专属文件夹

```
.agent/cron_jobs/<job_id>/          # job_id 里的 ':' 替换成 '_' 做目录名
├── prompt.md      用户可编辑，下次触发立即生效，无需重启 daemon
├── config.json    单 job 的超时/步数/卡死检测阈值覆盖
├── state.json     跨次启动持久化：status/progress_summary/
│                  consecutive_failures/last_error/last_run_id
└── runs/
    └── <run_id>.jsonl   单次执行的逐步事件流
```

首次触发时自动创建，已存在的文件不会被覆盖（你手动编辑过的 prompt
不会被"重置"）。

### 6.1 `prompt.md` 支持的占位符

```
{{task_description}}         cron_jobs.json 里配置的 task_template
{{progress}}                 上次执行遗留的进度摘要（首次为空字符串）
{{#progress}}...{{/progress}}  条件块：progress 为空时整段连同标记
                                一起去掉，避免每次都印出一段空的
                                "上次进度"标题
```

默认模板：

```
{{task_description}}

{{#progress}}
--- 上次执行遗留的进度 ---
{{progress}}
请从上述进度继续，不要从头重新开始。
{{/progress}}
```

正常完成后 `progress_summary` 会被清空；超时/卡死时保留最后一步输出
（截断到 2000 字）供下次续接或人工查看。

### 6.2 `config.json`：单 job 覆盖

```json
{
  "timeout_seconds": 1200,
  "max_steps": 60,
  "stuck_similarity_threshold": 0.92,
  "stuck_consecutive_limit": 3,
  "stuck_max_recoveries": 2
}
```

字段缺省时回退全局默认值（见 §7）——**这个回退是每次读取都会重新
计算的**，也就是说改一次全局配置，所有没有在自己 `config.json` 里
显式写这个字段的 job，下次触发立即跟着变化，不需要逐个 job 手动改
文件，也不需要跑迁移脚本。已经在 `config.json` 里显式写过的字段不受
全局配置变化影响（这是你主动覆盖的值，理应保留）。

### 6.3 `state.json`：执行状态机

| 状态 | 含义 |
|---|---|
| `idle` | 从未运行过 / 上次正常结束 |
| `running` | 当前正在执行（用于检测"上次异常退出、state 还留在 running"的僵尸状态） |
| `needs_human_review` | `StuckDetector` 判定 `GIVE_UP`，或单步执行异常，或 Agent 构造失败 |
| `timed_out` | 上次因触达硬超时/步数上限被收尾（不算失败，下次会带着进度继续） |

## 7. 全局默认配置

`agent_config.json` 里可选的 `"cron": {...}` 块：

```json
{
  "cron": {
    "max_concurrent_jobs": 3,
    "default_timeout_seconds": 1200,
    "default_max_steps": 60,
    "inner_max_turns": 15
  }
}
```

| 字段 | 默认值 | 作用 |
|---|---|---|
| `max_concurrent_jobs` | 2 | `CronJobRunner` 的并发上限（见 §3） |
| `default_timeout_seconds` | 1200（20 分钟） | 新建 job **首次生成** `config.json` 时写入的默认值，也是已存在 job 缺省该字段时的回退来源（见 §6.2） |
| `default_max_steps` | 60 | 同上 |
| `inner_max_turns` | 15 | cron 专用 Agent 单次 `run_turn()` 内部的 `max_turns` 预算（见 §5） |

不配置这一块时，所有字段使用上表的硬编码默认值。

### 7.1 跨通道总并发上限（`scheduler.max_total_concurrent_tasks`）

`max_concurrent_jobs`（本节）与 Goal Objective 通道的
`autonomy.max_concurrent_objectives_cap`（默认 2）是两条**完全独立**的
并发上限——正常（非 degraded）状态下互不感知，默认配置下系统里最多可能
同时有 2（Objective）+ 2（cron）= 4 个任务在跑，看板顶栏"daemon 正在
执行 N 项任务"里 N 超过单条通道上限（比如同时看到 3 个）就是这么来的，
不是 bug，是"两条通道各自独立限流、彼此不感知对方"这个设计现状的直接
体现。

如果需要一个真正跨通道的**总**并发天花板，在 `agent_config.json` 里配置
`scheduler.max_total_concurrent_tasks`（默认 `null`，不生效）：

```json
{
  "scheduler": {
    "max_total_concurrent_tasks": 2
  }
}
```

设置后，`ObjectiveExecutor`/`CronJobRunner` 的 `effective_max_concurrent()`
都会在各自原有上限（`max_concurrent_objectives_cap`/`max_concurrent_jobs`，
以及 degraded 状态下更低的收紧值）基础上，再 clamp 到
`max(0, max_total_concurrent_tasks - 对方通道当前运行数)`——任意时刻
Objective + cron job 的运行总数不会超过这个值。始终"只降不升"：不配置
时两条通道继续各走各的独立上限，与改造前完全一致。详见
`next_doc/goal_execution_scheduling_global_cap_bugfix.md`。

## 8. REST API

```
GET   /v1/cron/jobs/{id}/workspace   state + config + 最近执行列表
GET   /v1/cron/jobs/{id}/prompt      读 prompt.md
PUT   /v1/cron/jobs/{id}/prompt      改 prompt.md（Body: {"prompt": "..."}）
GET   /v1/cron/jobs/{id}/runs/{run_id}  某次执行的完整事件流
POST  /v1/cron/jobs/{id}/reset       needs_human_review → idle（正在执行中的
                                      job 拒绝重置，返回 409）
```

job 基础的增删改查（`GET/POST /v1/cron/jobs`、`PUT /v1/cron/jobs/{id}`、
`POST /v1/cron/jobs/{id}/run`）不是本机制新增的，见
[HTTP API 指南](http-api-guide.md#v1cronjobs--cron-job-rest-api)。

`GET /v1/cron/jobs/{id}/workspace` 响应示例：

```json
{
  "job_id": "user:ab12cd34",
  "state": {
    "status": "idle",
    "progress_summary": "",
    "last_step_index": 3,
    "consecutive_failures": 0,
    "last_run_started_at": 1720000000.0,
    "last_run_finished_at": 1720000180.0,
    "last_run_id": "2026-07-20T09-00-00",
    "last_error": ""
  },
  "config": {
    "timeout_seconds": 1200,
    "max_steps": 60,
    "stuck_similarity_threshold": 0.92,
    "stuck_consecutive_limit": 3,
    "stuck_max_recoveries": 2
  },
  "is_running": false,
  "recent_runs": ["2026-07-20T09-00-00", "2026-07-19T09-00-00"]
}
```

后台线程执行完一次后通过 `AgentBridge.emit_cron_job_finished()` 推
`CRON_JOB_FINISHED` SSE 事件，实时打开着看板的用户不需要手动刷新。

## 9. 看板：⏰ Cron 任务 Tab

`apps/mini_agent_kanban/app.py` 的 "⏰ Cron 任务" tab 提供：

- 状态徽标（空闲 / 执行中 / 需人工介入 / 上次超时）
- 连续失败次数
- 进度摘要展开查看
- 最近执行记录回放（对应 `runs/<run_id>.jsonl` 的逐步事件）
- `prompt.md` 在线编辑保存
- `needs_human_review` 状态下的一键重置按钮
- "➕ 新建 cron job" 表单：提交前会对 `schedule` 字段做格式前置校验
  （`interval:<秒数>` 或 `cron:<5 字段表达式>`），格式明显不对时直接
  在表单内提示，不会发起后端请求

看板的通用说明见 [看板指南](kanban-dashboard-guide.md)。

## 10. 排查指南

| 现象 | 排查方向 |
|---|---|
| job 到期了但一直没执行 | 检查 `max_concurrent_jobs` 是否被占满（看板 `is_running` 列表）；检查该 job 上一次是否还在跑（同 job 去重会拒绝并发触发） |
| 状态卡在 `needs_human_review` | 打开该 job 的最近一次 `runs/<run_id>.jsonl`，看最后几条 `step`/`stuck_recover`/`stuck_give_up`/`step_error` 事件；确认原因后在看板点"重置"或调用 `POST /v1/cron/jobs/{id}/reset` |
| 状态卡在 `running` 但看板显示未在执行 | daemon 异常退出导致的僵尸状态，不影响下次触发（下次执行会记一次
`consecutive_failures` 但仍会正常继续执行），也可以手动 `reset` 清掉 |
| 任务每次都从头开始，没有接续上次进度 | 检查 `prompt.md` 是否还保留 `{{#progress}}...{{/progress}}` 块（被用户误删就不会拼进度了）；检查上次是不是 `idle` 正常完成（正常完成会清空 `progress_summary`，这是预期行为，不是 bug） |
| 想让所有 job 的超时时间统一改长一点 | 改 `agent_config.json` 的 `cron.default_timeout_seconds` 即可，对未在自己 `config.json` 里显式覆盖过该字段的 job 立即生效（见 §6.2/§7），不需要逐个改文件 |
| 新建 job 提示 schedule 格式不合法 | 按提示修正为 `interval:<秒数>`（如 `interval:3600`）或 `cron:<分> <时> <日> <月> <周>`（如 `cron:0 22 * * *`），字段支持 `*`/`*/n`/`n`/`n,m`/`n-m` |

## 11. 已知局限

- cron 任务的 Agent 每次触发都重新构建，不跨触发保留完整对话历史——
  连续性完全依赖 `progress_summary` 这一段文本摘要，复杂的多轮上下文
  可能会在摘要压缩中丢细节。更结构化的 checkpoint 机制（`state.json`
  里存自由格式的 `checkpoint_data`）尚未实现，属于按需再做的可选项。
- 全量继承主 Agent 工具意味着 cron 任务和主 Agent、SubAgent 共用同一份
  全局 `ToolRegistry` 单例；这是本代码库里已经被 SubAgent 并发验证过的
  既有模式（各自的 thread-local 状态按"构造 Agent 的线程"隔离），
  `CronJobRunner` 保证 Agent 在专属的 cron 执行线程内构造并运行、不
  跨线程，所以是安全的。
- `[CRON_DONE]`/`[CRON_CONTINUE]` 标记依赖模型遵循 system prompt 里的
  约定，如果模型没有输出任何标记，退化到"是否自然结束"兜底判断，存在
  极少数"模型自然说完话但其实任务没做完"被误判为完成的可能——这属于
  "用文本约定代替结构化协议"的固有权衡，可接受。
