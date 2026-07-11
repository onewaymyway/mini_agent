# 守护进程多客户端架构指南 (Daemon Multi-Client Guide)

> 覆盖范围：`cli/daemon.py`、`api/bridge.py`、`api/session_pool.py`、`api/server.py` / `api/routes.py`。
> 这是 mini_agent 中"一个后台 daemon 进程 + 多个前台/远程客户端连接同一个 Agent 会话"能力的核心实现，
> 此前散落在多篇文档中提及，但没有集中的架构说明，故新增本文档。

## 1. 为什么需要 Daemon 模式

普通 CLI REPL 是"一个终端 = 一个 Agent 进程"。Daemon 模式把 Agent 进程与终端解耦：

- Agent 常驻后台运行（`cmd_daemon_start`），不因某个终端关闭而中断。
- 任意数量的 CLI 客户端（`DaemonClient`）、HTTP/SSE 客户端（网页、看板、手机 App）可以
  同时或先后"接入"同一个会话，查看实时输出、发送消息、批准工具权限。
- 适合长时间运行的自治任务（Autonomous Loop / Cron）与需要多端协作查看的场景。

## 2. 三层组件

```
终端 A (DaemonClient) ─┐
终端 B (DaemonClient) ─┼─ HTTP/SSE ─▶ api/server.py + routes.py
网页 / 看板 (SSE)      ─┘                     │
                                        SessionAgentPool（每 session 一个 Agent 实例）
                                               │
                                          AgentBridge（每个 session 一份）
                                        ┌──────┴──────┐
                                RingBuffer      InputQueue / PermissionGate
                                        │
                                 OutputBroadcaster ──▶ 所有已订阅 SSE 客户端
```

### 2.1 `api/bridge.py` — Agent 与 HTTP 层的解耦桥梁

Agent 核心（`agent.py`）完全不感知 HTTP/SSE 的存在，只通过 `AgentBridge` 读写：

- **RingBuffer**：线程安全环形缓冲区（默认 `maxlen=2000`），事件带自增 `id`，
  支持迟接入 / 断线重连客户端通过 `events_since(since_id)` 回放历史事件。
- **OutputBroadcaster**：写入 RingBuffer 的同时，把事件实时扇出给所有当前订阅的 SSE 连接。
- **InputQueue**：HTTP 端 `enqueue()` 写入用户消息 / 控制指令，`AgentRunner` 消费端逐条处理。
- **PermissionGate**：工具调用的权限审批网关，同时支持终端交互式批准与 HTTP 端批准两条路径。

设计原则：所有组件线程安全；每个 session 拥有**独立的一份** `AgentBridge`（而不是全局单例），
这是此前"Client B 显示所有历史排队消息"一类串会话 bug 的根源所在——历史版本中曾用全局 bridge，
现已改为按 session 隔离。

### 2.2 `api/session_pool.py` — `SessionAgentPool`

负责 session 生命周期管理：

- `get_or_create(session_id, user_ctx, ...)`：按需创建或复用 `SessionEntry`（内部持有 Agent 实例 + 专属 `AgentBridge`）。
- `_make_agent_factory` / `_build_session_cfg`：为每个 session 构造隔离的 `AppConfig`，
  确保多用户（`multi-user-guide.md`）场景下的数据、权限、工具白名单互不干扰。
- `find_by_turn` / `find_by_permission_req` / `find_by_interaction_req`：
  根据回合 ID / 权限请求 ID / 交互请求 ID 反查所属 session，用于把 HTTP 端的批准/回复路由回正确的 Agent。
- `_monitor_loop` + `_check_health` + `_gc_idle`：后台监控线程，定期清理空闲/崩溃的 session，
  并通过 `_on_crash` 回调做异常上报。
- `SelfMessageBus`：session 间自消息广播通道（`broadcast_to_sessions`），用于跨会话通知（如 Cron/自治任务完成提醒）。

### 2.3 `cli/daemon.py` — CLI 侧客户端与守护进程管理

- `cmd_daemon_start/stop/status`：管理守护进程本身的启停（PID 文件 + `_daemon_info_file`，
  存活性通过 `_is_process_alive` 判断）。
- `DaemonClient`：CLI 客户端与守护进程 HTTP/SSE 接口通信的封装类。
- `run_connected_repl`：**已连接模式**下的 REPL 主循环，是问题排查的核心入口：
  - `_pick_session`：连接时选择要接入的已有 session 或新建。
  - `_render_sse_event`：把服务端推来的 SSE 事件（`token` / `tool_call` / `permission_req` /
    `interaction_req` / `stream_end` 等）渲染进本地 `Terminal`。**Agent 名称前缀
    （如 `orzooo ❯`）的显示逻辑就在这里**——通过 `Terminal.print()` 消费一条携带前缀标记的
    渲染消息；若该消息未进入渲染线程的处理队列，前缀就不会出现，即使调用方日志显示函数已被正确调用。
    排查思路应聚焦于"消息是否真正到达并被 render 线程 `Terminal.print()` 消费"这一步，
    而不是调用点本身。
  - `_handle_connected_permission` / `_handle_connected_interaction`：把权限审批 / 用户交互请求
    以本地终端问答形式呈现，再把结果通过 `DaemonClient` 回传服务端的 `PermissionGate`。
  - `_handle_connected_cron` / `_handle_connected_goals` / `_handle_connected_digest`：
    连接态下的斜杠命令转发（cron / goals / digest 等），把命令结果通过 `Terminal.run_captured()`
    捕获后原样打印在本地终端，无需本地重新实现这些子命令的业务逻辑。
  - `_connected_status_bar_provider`：状态栏内容在已连接模式下的取数逻辑。

## 3. 一次消息的完整生命周期

1. 客户端（终端或网页）通过 HTTP 发送消息 → `routes.py` → `AgentBridge.InputQueue.enqueue()`。
2. `SessionAgentPool` 中对应 session 的 `AgentRunner` 从 `InputQueue` 取出消息并驱动 Agent 核心处理。
3. Agent 产生的 token / 工具调用 / 权限请求等事件通过 `OutputBroadcaster` 写入该 session 的
   `RingBuffer`，并实时推送给所有订阅的 SSE 客户端。
4. 每个客户端各自维护"已消费到的 `since_id`"，新请求时只拉取 `events_since(since_id)` 之后的增量，
   **而不是每次都取完整历史**——若客户端未正确维护/传递 `since_id`，就会出现"每次请求都重复显示
   全部历史消息"的问题（即已知问题中的 Client B 症状，见下）。
5. CLI 客户端收到 SSE 事件后交给 `_render_sse_event` 渲染到本地 `Terminal`。

## 4. 已知问题 / 当前排查焦点（截至本文档更新时）

以下问题正在积极调试中，记录以便后续查阅：

- **Agent 名称前缀不显示**：`orzooo ❯` 前缀在 daemon 自身前台终端中未出现，尽管诊断日志确认
  相关渲染函数被正确调用。当前定位方向：确认该前缀消息是否真正被 `Terminal.print()` 的渲染线程
  处理，而非函数调用点本身的问题。
- **`You ❯` 输入提示无颜色样式**：daemon 已连接模式下的本地输入提示缺少与非 daemon 模式一致的
  颜色渲染。
- **Client B 重复展示历史队列消息**：某 HTTP 客户端在每次新请求时会展示所有历史排队消息，
  而非仅展示新增消息 —— 需检查该客户端 `since_id` / 增量拉取逻辑是否正确落地（见第 3 节第 4 步）。
- 终端渲染伪影（terminal rendering artifacts）：与状态栏内容被中途注入响应流的历史 bug
  （现已修复，见 `terminal-display-internals.md`）类似的渲染时序问题仍需持续关注。

## 5. 相关文档

- `docs/http-api-guide.md` — HTTP/SSE 接口协议细节。
- `docs/multi-user-guide.md` — 多用户隔离、角色系统。
- `docs/terminal-display-internals.md` — `Terminal.print()` 渲染管线内部机制。
- `docs/kanban-dashboard-guide.md` — 基于同一套 SSE 事件流的 Streamlit 看板消费端示例。
- `next_doc/daemon-multiuser-architecture.md` / `daemon-multiuser-implementation-design.md` —
  该架构最初的设计稿（历史文档，本指南为其落地后的使用/结构说明，建议以本文档为准）。
