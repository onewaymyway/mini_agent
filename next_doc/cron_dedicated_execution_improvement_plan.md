# Cron 任务专属执行机制改进方案

> 状态：已落地（第一、二、三轮）· 见
> `next_doc/cron_dedicated_execution_implementation_record.md`
> 已完成：独立后台线程执行通道、超时/步数双重兜底、StuckDetector 卡死检测、
> 跨次触发进度恢复、每 job 专属文件夹、REST API + 看板 "⏰ Cron 任务" tab、
> `CronConfig` 正式配置字段、单元测试（23 项）、`config.json` 缺省字段
> 实时回退全局配置、看板新建 job 的 schedule 格式前置校验、单元测试
> （新增 19 项，累计 42 项）。
> 关联代码：`src/mini_agent/evolution/cron_job_{workspace,executor,runner,
> agent_bridge}.py`、`src/mini_agent/evolution/cron_scheduler.py`、
> `src/mini_agent/api/{server,bridge,models,routes}.py`、
> `apps/mini_agent_kanban/{client,app}.py`、`src/mini_agent/config/
> {models,loader}.py`
> 顺带修复：`skill_activate`/`skill_deactivate` 等工具执行后 turn 级
> system prompt 缓存未失效的问题（`agent/turn_loop.py`），与本方案同批次
> 提交但属于独立 bug，不计入本方案的 Track 划分。

## 0. 背景与问题

daemon 的 cron 任务（`evolution/cron_scheduler.py`）原来的执行路径是：

```
CronScheduler.tick() 到期
  → _fire(job)
  → submit_fn(job.task_template, ...)   # 包装 InputQueue.enqueue
  → AgentRunner 主线程 dequeue 后当成一次普通 turn 处理（run_turn）
```

`AgentRunner` 是单线程循环：dequeue InputQueue → 处理 → 空闲时才跑
`AutonomousLoop.tick()`（cron 调度就挂在这条链路上）。这意味着：

| 问题编号 | 问题 | 根因 | 影响 |
|---|---|---|---|
| P1 | 一个 cron job 执行很久，会卡住其它 job 和用户消息 | cron job 的 turn 和用户消息共用同一条 InputQueue + 单线程 AgentRunner，没有任何超时机制 | daemon 对用户"卡住不回复"；到期的其它 cron job 触发不了 |
| P2 | 没有单任务时间/步数上限 | `run_turn()` 只有 `max_turns`（步数），没有墙钟超时 | 极端情况下一个任务可以无限跑下去 |
| P3 | 没有跨次触发的进度保存机制 | cron job 每次触发都是一条新消息，没有"上次做到哪了"的状态 | 需要多次触发才能完成的任务，每次都从头开始，或者需要用户在 task_template 里手工塞进度 |
| P4 | 没有"是否卡住"的判断机制 | 普通对话场景默认相信模型会自己意识到卡住，但无人值守场景没有人来打断 | 输出重复雷同、原地打转的 cron 任务会一直被重复调度、浪费预算，也没人知道 |
| P5 | 看板看不到 cron 任务的执行细节 | cron job 只有 `cron_jobs.json` 里的调度元数据（schedule/next_run_at/run_count），没有执行过程的落盘 | 排查"这次到底跑了什么、跑到哪、为什么没完成"很难，只能翻 daemon 日志 |

## 1. 目标与非目标

**目标**：
- cron 任务的执行不能阻塞 `AgentRunner` 主线程（用户消息优先级不能被 cron 挤占）
- 单次执行有墙钟超时 + 步数上限双重兜底，避免失控
- 跨次触发能够"接着上次的进度继续"，而不是每次从零开始
- 能自动判断"是否卡住"，卡住后停止无意义的重复调度，等人工确认
- 每个 job 有独立的文件夹存放 prompt/进度/执行记录，看板能直接读取展示
- cron 任务全量继承主 Agent 的工具集（用户明确要求，不做工具白名单裁剪）

**非目标（本轮不做）**：
- 不做"跨触发保留完整对话历史"——连续性用一段 `progress_summary` 文本摘要
  传递，不是完整 session 回放，避免历史无限增长、避免和用户 session 存储
  混在一起。如果某类任务确实需要精细的多轮上下文，后续可以再扩展
  `state.json` 存结构化 checkpoint（不在本轮范围内）。
- 不做"cron 任务专属的工具白名单/权限模型"——按用户要求全量继承主 Agent
  工具，不引入额外的裁剪逻辑。
- 不改 `cron_jobs.json` 的索引结构（schedule/enabled/next_run_at 等仍在
  原文件里），新增信息全部下沉到每个 job 自己的文件夹，两者是索引 vs
  详情的关系，不重复存储。

## 2. 方案设计

### 2.1 执行通道：从"排队给主线程"改为"独立后台线程"

新增 `CronJobRunner`（`evolution/cron_job_runner.py`），`CronScheduler._fire()`
注入后优先走这条通道：

```
_fire(job) → CronJobRunner.submit(job)   # 立即返回，不阻塞
                 └─ 独立线程: build_cron_agent() → CronJobExecutor.run_job()
```

- 用 `threading.Semaphore` 控制全局并发上限（`max_concurrent_jobs`，默认 2），
  超出上限的 job 在线程内部排队等待，而不是丢弃触发
- 同一个 job 如果还有一次执行没跑完，`submit()` 直接拒绝本次触发（避免
  调度间隔比单次执行还短的极端配置导致同一 job 并发跑两份）
- 未注入 `job_runner` 时完全回退旧路径（`submit_fn`），向后兼容不升级的部署

### 2.2 执行循环：超时 + 步数 + 卡死检测三重兜底

`CronJobExecutor.run_job()`（`evolution/cron_job_executor.py`）是一个同步
循环，每一"步"调用一次 `submit_step_fn`（底层是一次完整的
`agent.run_turn()`）：

```
while True:
    if now >= deadline:             → status = timed_out，收尾
    if step_index >= max_steps:     → status = timed_out，收尾
    result = submit_step_fn(...)    → 单步异常也不让整个 job 崩溃
    if result.error:                → status = needs_human_review，收尾
    if result.done:                 → status = idle（正常完成），收尾
    signal = StuckDetector.observe(result.text)
    if signal == GIVE_UP:           → status = needs_human_review，收尾
    # 否则继续下一步（continue）
```

"是否完成"的判断（`cron_agent_bridge.make_submit_step_fn`）：
1. 输出末尾出现 `[CRON_DONE]` 标记 → 明确完成
2. 输出末尾出现 `[CRON_CONTINUE]` 标记 → 明确未完成，继续
3. 都没出现时，用 `agent._last_turn_hit_max_turns` 兜底：本次
   `run_turn()` 是自然结束（没撞到内层 `max_turns` 预算）就认为完成，
   被预算打断就认为未完成

这是"内层限步数（`max_turns`）、外层限墙钟时间+步数（`timeout_seconds`/
`max_steps`）"的双重兜底：单次 `run_turn()` 调用本身不会无限跑，即使某
一步异常复杂也会先撞到内层预算，把控制权交还给外层循环。

卡死检测复用已有的 `StuckDetector`（`role_agents/stuck_detector.py`）：
连续输出相似度过高 → 先尝试 `RECOVER`（继续但换个角度），恢复次数耗尽后
判定 `GIVE_UP`。

### 2.3 每 job 专属文件夹

```
.agent/cron_jobs/<job_id>/          # job_id 里的 ':' 替换成 '_' 做目录名
├── prompt.md      用户可编辑，支持 {{task_description}}/{{progress}} 占位符
│                  以及 {{#progress}}...{{/progress}} 条件块
├── config.json    单 job 的超时/步数覆盖（缺省回退全局 CronConfig 默认值）
├── state.json     跨次启动持久化：status/progress_summary/
│                  consecutive_failures/last_error/last_run_id 等
└── runs/
    └── <run_id>.jsonl   单次执行的逐步事件流（run_started/step/
                         stuck_recover/stuck_give_up/timed_out/
                         step_error/run_finished）
```

`CronJobWorkspace.render_prompt()` 负责把 `progress_summary` 拼进下次触发
的 prompt——这就是"跨次触发进度恢复"的落地方式：正常完成后清空
`progress_summary`，超时/卡死时保留最后一步输出（截断到 2000 字）供下次
续接或人工查看。

### 2.4 REST + 看板集成

不新增专门的"cron 详情"数据流转层，直接在 `api/routes.py` 加 5 个端点，
薄薄地包一层 `CronJobWorkspace` 的读写方法：

```
GET   /v1/cron/jobs/{id}/workspace   state + config + 最近执行列表
GET   /v1/cron/jobs/{id}/prompt      读 prompt.md
PUT   /v1/cron/jobs/{id}/prompt      改 prompt.md
GET   /v1/cron/jobs/{id}/runs/{run_id}  某次执行的完整事件流
POST  /v1/cron/jobs/{id}/reset       needs_human_review → idle
```

看板 `apps/mini_agent_kanban/app.py` 新增 "⏰ Cron 任务" tab：状态徽标
（空闲/执行中/需人工介入/上次超时）、连续失败次数、进度摘要展开、最近
执行记录回放、prompt 在线编辑、卡死后的一键重置按钮。

后台线程执行完一次后通过 `AgentBridge.emit_cron_job_finished()` 推
`CRON_JOB_FINISHED` SSE 事件，实时打开着看板的用户不需要手动刷新。

### 2.5 全局默认配置

新增 `config/models.py::CronConfig`，`agent_config.json` 里可选配置
`"cron": {...}` 块覆盖（见实施记录文档的示例），影响：
- `CronJobRunner` 的并发上限
- 新建 job **首次生成** `config.json` 时写入的默认超时/步数
- 已存在的 job：`config.json` 里没写的字段每次读取都会回退到这里的全局
  值（`CronJobWorkspace.read_config(default=...)` 做的是"缺省字段合并"，
  不是"整份覆盖或整份不覆盖"）——改一次全局配置，所有 job 下次触发时
  立即生效，不需要额外的批量迁移脚本
- cron 专用 Agent 的内层 `max_turns` 预算

## 3. Track 划分（供实施记录引用）

| Track | 内容 | 状态 |
|---|---|---|
| A | `CronJobWorkspace` 文件夹结构 + prompt 渲染 | 已完成 |
| B | `CronJobExecutor` 执行循环（超时/步数/StuckDetector） | 已完成 |
| C | `CronJobRunner` 独立后台线程 + 并发控制 | 已完成 |
| D | `cron_agent_bridge`：全量工具继承的 Agent 构造 + 完成判定 | 已完成 |
| E | `CronScheduler._fire()` 接入新通道（向后兼容旧路径） | 已完成 |
| F | REST API（5 端点） | 已完成 |
| G | 看板 "⏰ Cron 任务" tab | 已完成 |
| H | SSE 事件推送（`CRON_JOB_FINISHED`） | 已完成 |
| I | `CronConfig` 正式配置字段 + `agent_config.json` 覆盖 | 已完成 |
| J | 单元测试 | 已完成（23 项，见实施记录） |
| K | `config.json` 缺省字段实时回退全局 `CronConfig`（不止首次创建生效） | 已完成 |
| L | 看板"新建 cron job"表单 schedule 格式前置校验 | 已完成 |

## 4. 风险与已知局限

- cron 任务的 Agent 每次触发都重新构建，不跨触发保留完整对话历史——
  连续性完全依赖 `progress_summary` 这一段文本摘要，复杂的多轮上下文
  可能会在摘要压缩中丢细节。
- 全量继承主 Agent 工具意味着 cron 任务和主 Agent、SubAgent 共用同一份
  全局 `ToolRegistry` 单例；这是本代码库里已经被 SubAgent 并发验证过的
  既有模式（各自的 thread-local 状态按"构造 Agent 的线程"隔离），但如果
  未来某个工具引入了非 thread-local 的全局可变状态，需要重新评估。
- `[CRON_DONE]`/`[CRON_CONTINUE]` 标记依赖模型遵循 system prompt 里的
  约定，如果模型没有输出任何标记，退化到 `_last_turn_hit_max_turns` 兜底
  判断，存在极少数"模型自然说完话但其实任务没做完"被误判为完成的可能——
  这属于"用文本约定代替结构化协议"的固有权衡，可接受。
