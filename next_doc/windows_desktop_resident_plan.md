# Windows 常驻桌面交互系统改造方案（已确认版）

> 来源：《mini_agent Windows 常驻交互系统开发方案》（改进计划.md）。
> 本文是对该计划**结合现有代码核实后**的落地方案：保留其产品目标，砍掉与现有实现重复的部分。
> 状态：方案已确认，按 S0→S4 一次一阶段实施；每阶段完成后更新文档并打包改动。

## 1. 结论：计划与现状的差距

原计划把 mini_agent 当作"只有 CLI"。实际上后台运行层已有六七成，真正缺的是 **Windows 桌面外壳** 与少量后端补丁。

| 计划模块 | 现有实现 | 处理 |
|---|---|---|
| M1 AgentService 抽象 | `AgentBridge` + `SessionAgentPool` + `AgentRunner` + `POST /v1/chat`；CLI 的 `DaemonClient` 本就是它的客户端 | **不做** |
| M2 后台 Runtime | `cli/daemon.py` + supervisor、PID/info 文件、崩溃自动重启、卡死探测、`/v1/health`、`/v1/status`；Windows 已用 `DETACHED_PROCESS` | **已有** |
| 本地鉴权 | Bearer token（`.agent/agent_api.key`）、默认仅 127.0.0.1 白名单 | **已有**；Windows ACL 加固放 S4 |
| M3 事件/SSE | 环形缓冲 + 自增 id + `since_id`/`Last-Event-ID` 回放、`/v1/events`、`/v1/stream` | **已有**；缺"关注事件"，S0 补 |
| 审批 | `PermissionGate`/`InteractionGate`、`/v1/permissions/{id}`、`/v1/interactions/{id}`、`/v1/inbox` | **已有**，桌面端直接接 |
| 通知 | `NotificationDispatcher` 渠道、`reports_store`、崩溃告警独立通道、`cron_questions` | **已有**，S0 补即时唤醒 |
| 托盘/热键/浮窗/Toast/自启动 | 无 | **S1～S4 新做** |

## 2. 明确不做的部分（及理由）

1. **不建 SQLite `runtime.db`**：现有状态分散在 goals/objective_executions/cron_jobs/reports 等 JSON(L) 中，统一用 `atomic_write`；再加一套 SQLite 会产生双写一致性问题。"UI 断线丢通知"已由 `reports_store` 持久化 + ack 解决。
2. **不引入统一 9 状态 Task 状态机**：turn / Objective / cron / workflow_run 各有状态；需要"任务列表"时做只读投影，不新建状态机。
3. **暂不做 PyInstaller 打包**：项目靠文件路径发现 skills、`myplugins`、`external_projects`，并有自我演化 worktree，冻结会冲突；开发方式也是 diff zip 覆盖。第一阶段用 venv + `pythonw` + 启动项快捷方式。
4. **不做 Windows Service**：需要访问用户目录与桌面应用，普通登录进程更合适（与原计划一致）。
5. **上下文感知（当前窗口/浏览器/VS Code 插件）暂不做**：与 `docs/mini-agent-philosophy-and-roadmap.md` 中"暂不推进新数据源接入"一致。

## 3. 已确认的关键决策

- **范围**：按 S0～S4 推进，不做上面第 2 节的部分。
- **快速面板使用独立 session**（如 `desktop-quick`）：带 `session_id` 的 `/v1/chat` 会路由到 `SessionAgentPool` 里独立的 Agent，不会排在主会话长任务后面。代价是多一个 Agent 实例与一条 runner 线程。
- **UI 技术**：PySide6（托盘/浮窗）；Toast 用 `winotify`/`win11toast` 一类库，点击回调回到面板。
- **默认值全部保守 opt-in**：自启动、全局热键注册、Toast 默认关闭，首次运行由用户逐项开启。
- **Toast 范围收窄**（理念：主动性晚于可靠性）：仅权限审批、`ask_user_async` 问题、用户自己发起的任务完成/失败、daemon 崩溃。autonomous tick、digest、巩固循环一律静默。

## 4. 分阶段计划

| 阶段 | 内容 | 估时 | 状态 |
|---|---|---|---|
| S0 后端补丁 | `attention` SSE 事件、`/v1/desktop/summary`、单测、文档 | 1～1.5 天 | ✅ 已完成 |
| S1 托盘 | PySide6 骨架、daemon 发现与拉起、托盘状态图标/菜单/角标、SSE 断线重连 | 1.5 天 | ⬜ |
| S2 热键与快速面板 | `RegisterHotKey`、输入面板、专属 session、最近任务、中断 | 2 天 | ⬜ |
| S3 Toast 与审批 | 审批和异步提问可在 Toast/面板回答，点击跳转，去重与免打扰 | 2 天 | ⬜ |
| S4 自启动与加固 | 登录自启（默认关）、token ACL（`icacls`）、日志、退出时是否停 daemon 的明确选项 | 1 天 | ⬜ |

新增桌面代码放 `apps/mini_agent_desktop/`（与 Kanban 同级，纯 HTTP/SSE 客户端），不改 `src/mini_agent` 核心。

## 5. S0 实施记录

### 5.1 相对原方案的一处调整

原方案设想新增一个 `desktop` 通知渠道。实现时改为**进程内发布点 `notification/attention_hub.py`**：

- `NotificationDispatcher` 渠道只覆盖 watchlist 汇报；而 cron 异步提问（`ask_user_async`）直接写 `questions_store`，不经过 dispatcher。若只加渠道，最关心的"等你回答"场景拿不到推送。
- 发布点放在两个 store 的 `append_*` 里（落盘成功后），覆盖面更完整；没有监听者时是空操作。

### 5.2 改动清单

| 文件 | 改动 |
|---|---|
| `src/mini_agent/notification/attention_hub.py` | 新增：监听注册/发布，异常不外溢，无监听者空操作 |
| `src/mini_agent/notification/reports_store.py` | `append_report` 落盘成功后发布 `report` 事件 |
| `src/mini_agent/notification/questions_store.py` | `append_question` 落盘成功后发布 `cron_question` 事件，`urgency=blocking` 映射 `blocking` 级别 |
| `src/mini_agent/api/models.py` | `EventType.ATTENTION = "attention"` |
| `src/mini_agent/api/bridge.py` | `AgentBridge.emit_attention()`（不带 session_id，跨 session 系统级事件） |
| `src/mini_agent/api/server.py` | `HttpServer` 新参数 `desktop_attention_events_enabled`；`start()` 注册监听、`stop()` 注销 |
| `src/mini_agent/cli/app.py` | 把配置传给 `HttpServer` |
| `src/mini_agent/config/models.py` | `HttpConfig.desktop_attention_events_enabled: bool = False` |
| `src/mini_agent/api/routes.py` | `GET /v1/desktop/summary`：聚合权限/交互/失败 Objective/汇报/cron 提问/崩溃告警计数 |
| `tests/test_desktop_attention_s0.py` | 13 个用例 |
| `docs/desktop-attention-events-guide.md` | 使用说明 |

### 5.3 设计要点与已知限制

- **崩溃告警不走 attention 事件**：它由 supervisor 进程写入，被它杀掉的 daemon 进程里的监听者早已不存在。客户端应在 **SSE 断线后**查询 `/v1/desktop/summary.counts.crash_alerts_unacked` 来发现崩溃。
- **事件只带摘要**（≤200 字标题 + `kind` + `ref_id`），不带正文，避免长文本进入 2000 条的内存环形缓冲；详情按 `kind`+`ref_id` 回查对应 REST 端点。
- **默认关闭**：关闭时不注册监听，现有行为完全不变；开启后老客户端会收到未知事件类型 `attention`，按 SSE 惯例应忽略。
- **环形缓冲不持久**：daemon 重启后 `attention` 事件丢失，所以客户端重连后必须先拉 `/v1/desktop/summary` 做快照，再订阅新事件（与原计划"先快照再订阅"一致）。
- 已知遗留（非本次引入）：`tests/test_notification_dispatcher.py::test_kanban_writes_alert_record` 在原始代码包上即失败（汇报已改写 `reports.jsonl`，测试仍断言 `alerts.jsonl`）。
