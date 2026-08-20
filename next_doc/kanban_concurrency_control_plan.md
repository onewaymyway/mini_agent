# 看板并发上限控制功能

- **版本**: v1.0
- **状态**: 已实现
- **变更记录**:
  - v1.0：初版设计 + 完整实现。

## 0. 背景与问题

daemon 内部通过 `orchestrator/concurrency.py` 的两个信号量控制并发：

1. `TaskSemaphore`（`max_tasks`）—— 同时运行的 SubAgent/任务数量上限；
2. `LLMSemaphore`（`max_llm_calls`）—— 同时进行中的 LLM 请求数量上限。

这两个值在**运行时可以热改**（`set_max_tasks()` / `set_max_llm_calls()`
直接改信号量的 `limit` 属性，不涉及重建/重启，线程安全），且已经有一个
可用的入口：daemon 本机终端的 `/concurrency tasks <n>` / `/concurrency
llm <n>` slash 命令（`cli/commands/concurrency.py`）。

问题是：这个入口只能在启动 daemon 的那个终端里用。看板（`apps/
mini_agent_kanban/app.py`）是通过 HTTP `/v1/...` 跟 daemon 通信的独立
streamlit 进程，此前完全看不到、也改不了并发上限——顶栏 `⚙️ daemon 正在
执行 N 项任务` 只展示"正在跑什么"，不展示"上限是多少、要不要调"。当用户
想临时收紧并发（比如担心 LLM 限流）或临时放宽（比如想让多个 Goal 并行
推进快一点）时，只能去 SSH 到 daemon 所在机器手敲命令，对纯看板用户不
友好。

本计划：给 daemon 加一对轻量 HTTP 路由，直接复用现成的
`concurrency_snapshot()` / `set_max_tasks()` / `set_max_llm_calls()`，
再在看板顶栏加一个可折叠的控件展示 + 编辑。

## 1. 设计边界

- **不做持久化**。这两个值是运行时状态（信号量 limit），本功能只做"热改
  当前生效值"，**不**写回 `agent_config.json`。daemon 重启后会掉回配置
  文件里的默认值（`max_tasks` 默认 4，`max_llm_calls` 默认 8）。如果用户
  想要"重启后依然生效"，需要手动改配置文件对应字段（不在本功能范围内，
  语义上跟 `kanban_config_management_plan.md` 的"配置管理"是两套东西，
  不复用它的 `/self/config` 机制）。
- **调低上限不打断当前任务**。信号量的语义是"新的 acquire 请求要排队"，
  正在持有信号量、已经在跑的任务不受影响，只有后续新任务会排队等待。
- **鉴权跟其他 `/self/*` 路由一致**，走 `_require_owner(request)`，需要
  owner token。

## 2. API

### `GET /v1/self/concurrency`

只读返回并发状态快照，字段跟 CLI `/concurrency` 展示的是同一份数据：

```json
{
  "tasks": {"active": 1, "limit": 4, "waiting": 0, "waiters": []},
  "llm":   {"active": 2, "limit": 8, "waiting": 0, "waiters": []}
}
```

### `POST /v1/self/concurrency`

Body: `{"max_tasks": int}` 和/或 `{"max_llm_calls": int}`，至少提供一个；
两者都 `>= 1` 校验。成功后返回更新后的快照（同 GET 的返回结构）。

内部实现：

```python
from mini_agent.orchestrator.concurrency import set_max_tasks, set_max_llm_calls
set_max_tasks(max_tasks)       # 同时同步 TaskManager.max_workers（跟 CLI 命令行为一致）
set_max_llm_calls(max_llm_calls)
```

## 3. 看板改动

- `apps/mini_agent_kanban/client.py`：新增 `concurrency_status()` /
  `set_concurrency(max_tasks=None, max_llm_calls=None)`。
- `apps/mini_agent_kanban/app.py`：新增 `_render_concurrency_control()`，
  放在顶栏 `_render_daemon_current_tasks()` 之后；折叠面板标题常驻展示
  `任务 active/limit` 和 `LLM active/limit`，展开后左右两栏分别是"任务
  并发"和"LLM 调用并发"的 `st.metric` + 数字输入框 + 应用按钮，另外展示
  当前排队中的任务标签（若有）。

## 4. 后续可能的扩展（不在本次范围）

- 是否需要"应用并持久化到配置文件"的第二个按钮（写回
  `agent_config.json` 对应字段，重启后依然生效）；
- LLM 并发是否需要按 provider 细分（目前是全局一个信号量，跟
  `_base_mixin.py` 里所有 provider 共用同一个 `get_llm_sem()`一致）。
