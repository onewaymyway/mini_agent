# 看板任务执行并发上限控制功能

- **版本**: v2.0
- **状态**: 已实现
- **变更记录**:
  - v1.0：初版设计，误把"任务并发"理解成 SubAgent/LLM 请求这两个底层
    信号量（`orchestrator/concurrency.py`），跟看板顶栏"⚙️ daemon 正在
    执行 N 项任务"实际展示的东西（Objective/Cron 执行数量）不是同一层级。
  - v2.0：改为控制 Objective/Goal 通道、Cron 通道各自的并发执行数——即
    顶栏那个任务列表"同时最多几条"，这才是用户实际想控制的"当前 daemon
    允许的同时进行的任务数量"。v1.0 加的 SubAgent/LLM 并发控件保留，
    折叠在本面板内部单独作为"高级"区域，避免两个概念混淆。

## 0. 背景与问题

看板顶栏"⚙️ daemon 正在执行 N 项任务"聚合展示三类来源：Objective 执行
（🎯 目标(Goal)）、Cron job（⏰ Cron 定时任务）、workflow（🔄 工作流）。
用户想要的"控制同时进行的任务数量"，指的正是这个列表能同时有几条——也
就是 daemon 内部两条独立的调度通道各自的并发上限：

1. **Objective/Goal 通道**（`evolution/objective_executor.py`）——由
   `ObjectiveExecutor.effective_max_concurrent()` 决定，起点是
   `min(MAX_CONCURRENT_OBJECTIVES, cfg.autonomy.
   max_concurrent_objectives_cap)`，`MAX_CONCURRENT_OBJECTIVES` 是模块级
   常量硬天花板（当前为 `2`），`max_concurrent_objectives_cap` 是可配置
   项，但永远不能突破硬天花板（"只降不升"的安全阀设计，见函数注释）。
2. **Cron 通道**（`evolution/cron_job_runner.py`）——`CronJobRunner`
   实例的 `_max_concurrent`（构造时从 `cron.max_concurrent_jobs` 配置读
   入，默认 `2`），`effective_max_concurrent()` 直接返回它（degraded 状态
   下会临时收紧到 `cron.degraded_max_concurrent`，跟本功能无关）。

这两个值都不是靠信号量控制的，而是各自的 `_acquire`/等待循环每次都调用
`effective_max_concurrent()` 重新读取当前值来判断"能不能再启动一个"——
这意味着：

- **Objective 通道**：只要能拿到活着的 `ObjectiveExecutor` 实例，直接把
  `oe._cfg.autonomy.max_concurrent_objectives_cap` 改掉，下一次
  `effective_max_concurrent()` 调用就会读到新值，**热改无需重启**。
- **Cron 通道**：只要能拿到活着的 `CronJobRunner` 实例（`_job_runner`），
  直接把 `runner._max_concurrent` 改掉，同样**热改无需重启**；额外唤醒一
  下等待槽位的调度循环（`_slot_cond.notify_all()`），让排队中的等待者
  立即用新值重新判断，不用等到下一次轮询周期。

跟 v1.0 加的 `/v1/self/concurrency`（`orchestrator/concurrency.py` 的
`TaskSemaphore`/`LLMSemaphore`）是完全不同的两个层级：一次 Objective 执行
内部可能再派生多个 SubAgent、发起多次 LLM 调用，那是"任务内部"的并发；
本功能控制的是"daemon 同时能跑几个任务（Objective 执行/cron job）"，
是"任务之间"的并发，对应顶栏列表数量。两者都保留，UI 上明确分层展示。

## 1. 设计边界

- **不做持久化**。跟 v1.0 一致，只热改内存里的当前生效值，**不**写回
  `agent_config.json`。daemon 重启后会掉回配置文件里的默认值。
- **Objective 通道有绝对硬天花板**（`MAX_CONCURRENT_OBJECTIVES = 2`，写
  在 `objective_executor.py` 模块级常量），无法通过本功能突破，这是代码
  里"只降不升"的设计（详见函数注释），不是本功能的限制。UI 上把数字输入
  框的 `max_value` 直接设为这个天花板，超出直接在前端拦掉，避免用户误以
  为调大了却没生效。
- **Cron 通道依赖 `CronJobRunner` 是否启用**。cron 有"新路径"
  （`CronScheduler._job_runner` 注入了 `CronJobRunner`）和"旧路径"（未注入，
  走 `submit_fn` 直接进 `InputQueue`，没有独立的并发槽位机制）两种；旧路径
  下本功能对 cron 无效，API 返回 `current_cap: null`，看板据此隐藏 cron
  那一栏的编辑控件、只展示"未启用独立并发通道"提示。
- **调低上限不打断当前任务**。语义与 v1.0 一致：只影响后续新任务排队，
  正在跑的 Objective/cron job 不受影响。
- **鉴权**跟其他 `/self/*` 路由一致，走 `_require_owner(request)`。

## 2. API

### `GET /v1/self/task_concurrency`

```json
{
  "objectives": {"current_cap": 2, "hard_ceiling": 2, "running": 1},
  "cron":       {"current_cap": 2, "running": 0}
}
```

`cron` 字段在 `CronJobRunner` 未启用时为
`{"current_cap": null, "running": null}`。

### `POST /v1/self/task_concurrency`

Body: `{"max_objectives": int}` 和/或 `{"max_cron_jobs": int}`，至少提供
一个。`max_objectives` 会被服务端 clamp 到 `[1, hard_ceiling]`；
`max_cron_jobs` 要求 `>= 1`，`CronJobRunner` 未启用时返回 400。成功后
返回更新后的快照（同 GET）。

内部实现（简化）：

```python
oe._cfg.autonomy.max_concurrent_objectives_cap = min(max_objectives, MAX_CONCURRENT_OBJECTIVES)

runner._max_concurrent = max(1, max_cron_jobs)
with runner._slot_cond:
    runner._slot_cond.notify_all()
```

## 3. 看板改动

- `apps/mini_agent_kanban/client.py`：新增 `task_concurrency_status()` /
  `set_task_concurrency(max_objectives=None, max_cron_jobs=None)`；v1.0
  的 `concurrency_status()` / `set_concurrency()` 保留不变。
- `apps/mini_agent_kanban/app.py`：`_render_concurrency_control()` 改为
  主体展示"目标(Goal)执行并发" / "Cron 执行并发"两栏（各自 `st.metric` +
  数字输入框 + 应用按钮，Objective 栏的输入框 `max_value` 直接设为硬
  天花板），面板标题常驻展示 `目标 running/cap　Cron running/cap`；内部
  新增 `_render_subagent_llm_concurrency_control()` 承接 v1.0 的
  SubAgent/LLM 并发控件，作为"高级"子区域折叠在同一个 expander 里，用
  `st.divider()` 分隔并加说明文字区分两个层级。

## 4. 后续可能的扩展（不在本次范围）

- 是否需要"应用并持久化到配置文件"的第二个按钮；
- workflow 通道目前没有并发上限机制（代码里没找到对应的信号量/槽位控制），
  如果之后要控制 workflow 并发，需要先在 `workflow/runner.py` 补上这层
  机制，不是本功能能覆盖的。
