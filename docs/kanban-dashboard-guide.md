# Kanban 看板使用指南

> 基于 Streamlit 的一体化观测/交互面板 —— `apps/mini_agent_kanban/`

## 简介

`apps/mini_agent_kanban/app.py` 是在 `apps/mini_agent_webdemo`（纯聊天 Web Demo）基础上
扩展出的**多 Tab 综合面板**，一次性提供聊天、会话管理、目标/Cron 看板、产出物浏览、
具身智能自省状态、诊断信息六大功能区，适合日常观测 daemon 运行状态，而不仅仅是聊天。

与 `web-demo-guide.md` 中的 Web Demo 是两个独立的 Streamlit 应用，二者都通过 HTTP API
（`/v1/*`）连接同一个 daemon，可以按需选用：只想聊天用 Web Demo，想看目标/Cron/多用户
状态用本看板。

## 快速开始

### 1. 启动 daemon（HTTP API）

```bash
python -m mini_agent.cli.app daemon start
```

默认监听 `http://127.0.0.1:8765`，Token 通常写在 `agent_api.key` 文件里。

### 2. 安装依赖并启动看板

```bash
pip install -r apps/mini_agent_kanban/requirements.txt
cd apps/mini_agent_kanban
streamlit run app.py
```

在左侧栏填入 API Base URL（默认 `http://127.0.0.1:8765/v1`）与 Token 即可连接。

## 顶部状态条

顶部常驻展示：

- 运行状态（idle / running / waiting_permission / error）
- 当前 Turn
- 自主等级（Autonomous Loop 的当前 tier）
- 距下次 Tick 的时间、Tick 计数、订阅者数量
- 待审批权限请求数——点击展开后可逐条允许/拒绝

## 功能 Tab 一览

| Tab | 内容 |
|---|---|
| 💬 对话 | 聊天、历史消息、事件流、发送/中断 |
| 🗂️ 会话管理 | 会话列表、新建 / 恢复 / 删除会话 |
| 📌 目标看板 | Goal / Objective 看板（按状态分列）、新建目标、Cron Job 管理与手动触发、Objective 执行进度 |
| 📁 产出物 | 浏览 `.agent/` 等目录下产出文件，预览与下载 |
| 🧠 自我状态 | 具身智能自省信息（自主循环摘要、活跃目标数、最近活动、多用户会话池 SessionPool 概况） |
| 🔧 诊断 | `/diagnostics` 原始信息，便于排障 |

### 💬 对话 Tab

事件展示逐类型解析（`tool_call` / `tool_result` / `tool_error` / `permission_req` /
`permission_done` / `turn_start` / `turn_done` / `error` / `token`），与
Web Demo 的事件流面板类似，但集成在同一多 Tab 界面中。

### 📌 目标看板 Tab

对接 Stage 9 自主 daemon 的 `GoalBacklog` 与 `CronScheduler`：

- 按状态列出 Goal（如 `pending` / `active` / `done`），支持新建目标（标题、描述、优先级、
  来源）。
- Cron Job 列表、新增、编辑，以及"立即执行一次"按钮。
- Objective 执行进度展示。

详见 `docs/autonomous_daemon_design.md`、`docs/goal-mode-guide.md` 了解 Goal/Cron/
Objective 背后的调度机制。

### 🧠 自我状态 Tab

展示 `/self/status`、`/self/autonomous` 等接口返回的具身智能自省信息，包括多用户模式下的
`SessionPool` 概况（当前活跃 session 数、每 session 状态）。详见
`docs/multi-user-guide.md`、`docs/embodied-agent-guide.md`。

## `AgentClient` 封装的 API 端点

`apps/mini_agent_kanban/client.py` 中的 `AgentClient` 封装了看板所需的全部 HTTP 调用，
均基于 `docs/http-api-guide.md` 中描述的 `/v1/*` 接口：

| 方法 | 对应端点 | 用途 |
|---|---|---|
| `health()` | `GET /health` | 健康检查 |
| `status()` | `GET /v1/status` | Agent 运行状态 |
| `diagnostics()` | `GET /v1/diagnostics` | 诊断信息 |
| `chat()` / `interrupt()` | `POST /v1/chat`、`/v1/interrupt` | 发消息 / 中断 |
| `history()` / `clear_history()` | `/v1/history` | 对话历史读取与清空 |
| `events()` | `GET /v1/events` | 拉取事件流 |
| `turns()` | `GET /v1/turns` | Turn 列表 |
| `pending_permissions()` / `respond_permission()` | `/v1/permissions/*` | 权限审批 |
| `sessions()` / `session_detail()` / `resume_session()` / `new_session()` / `delete_session()` | `/v1/sessions*` | 会话管理 |
| `users()` | `/v1/users` | 多用户列表（多用户模式） |
| `self_status()` / `autonomous_status()` | `/self/status`、`/self/autonomous` | 自省与自主循环状态 |
| `goals()` / `add_goal()` / `update_goal()` | `/v1/goals*` | Goal 看板 |
| `cron_jobs()` / `add_cron_job()` / `update_cron_job()` / `run_cron_job_now()` | `/v1/cron*` | Cron Job 管理 |
| `fs_list()` / `fs_read()` / `fs_download_url()` | `/v1/fs/*` | 产出物浏览与下载 |

## 使用场景

1. **日常巡检**：一次性查看多用户会话池、待处理目标、Cron 运行情况和最近产出物，
   无需分别登录 CLI 和 Web Demo。
2. **自主 daemon 观测**：`Stage 9` 自主循环运行期间，通过目标看板和自我状态 Tab
   观察它自己创建/推进的目标，而不打断其运行。
3. **远程排障**：结合诊断 Tab 的 `/diagnostics` 原始信息定位问题，无需 SSH 到服务器
   直接看日志文件。

## 后续可扩展方向（未实现）

- SSE 真流式渲染（当前对话为轮询式刷新，简单可靠但非逐 token 流式）
- Ensemble 多候选结果对比展示
- 进化流水线（Skill 提案 / git worktree diff）可视化
- 权限历史与安全网风险等级（T0-T3）统计图表

## 相关文件

- `apps/mini_agent_kanban/app.py` — 看板主程序（6 个 Tab）
- `apps/mini_agent_kanban/client.py` — `AgentClient` HTTP 封装
- `apps/mini_agent_kanban/README.md` — 应用自带的简要说明
- `docs/http-api-guide.md` — HTTP API 完整参考
- `docs/web-demo-guide.md` — 姊妹应用（纯聊天 Web Demo）
- `docs/multi-user-guide.md`、`docs/autonomous_daemon_design.md`、
  `docs/goal-mode-guide.md`、`docs/embodied-agent-guide.md` — 看板中各功能区背后的机制

---

*最后更新：2026-07-04*
