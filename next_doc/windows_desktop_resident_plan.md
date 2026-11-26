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
| S1 托盘 | PySide6 骨架、daemon 发现与拉起、托盘状态图标/菜单/角标、SSE 断线重连 | 1.5 天 | ✅ 已完成（待 Windows 手动验证） |
| S2 热键与快速面板 | `RegisterHotKey`、输入面板、专属 session、最近任务、中断 | 2 天 | ✅ 已完成（待 Windows 手动验证） |
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

## 6. S1 实施记录

### 6.1 结构

`apps/mini_agent_desktop/`（纯 HTTP/SSE 客户端，不 import mini_agent 核心包，**未改动 `src/` 下任何文件**）：

| 模块 | 职责 | 依赖 Qt |
|---|---|---|
| `state.py` | `TrayModel`：图标优先级、菜单文案、事件→刷新集合 | 否 |
| `controller.py` | 后台线程：health → 快照 → 订阅 SSE → 断线退避重连；心跳兜底刷新；离线时读崩溃告警文件 | 否 |
| `api.py` | httpx 客户端 + SSE 解析器 | 否 |
| `discovery.py` | 端点（`daemon_info.json`）、token、离线崩溃告警统计、拉起 daemon | 否 |
| `config.py` | `%LOCALAPPDATA%\mini_agent\desktop.json`，字段类型容错 | 否 |
| `tray.py` | `QSystemTrayIcon`：程序绘制图标、菜单、跨线程信号刷新 | 是 |
| `app.py` | 入口、`--check` 无界面自检、单实例锁 | 延迟导入 |

### 6.2 设计要点

- **分层的原因**：沙盒是 Linux，无法验证真实 Windows 托盘。把状态/连接/发现全部做成不依赖 Qt 的纯逻辑并单测；Qt 层只做展示，另有离屏冒烟测试。
- **图标优先级**：未连接（有离线崩溃告警→红，否则灰）> 崩溃告警（红）> 有待处理事项（黄）> 工作中（蓝）> 空闲（绿）。"需要处理"优先于"工作中"。
- **离线崩溃发现**：崩溃告警由 supervisor 写入且不走 SSE，daemon 已死时 HTTP 不可用，所以离线时直接读 `.agent/notification/daemon_crash_alerts.jsonl`（S0 已约定的做法）。
- **认证失败与 daemon 未运行区分对待**：前者菜单不提供"启动 Agent"（拉起 daemon 解决不了 token 问题）。
- **保守 opt-in**：`auto_start_daemon` 默认关闭，且即使开启也只在每次离线周期尝试一次，避免反复拉起。
- **退出桌面端不停 daemon**，菜单文案明确注明。
- **不回放历史订阅**（`replay=false`）：先快照再订阅，重连后重新快照，不依赖环形缓冲。
- `objective_progress` 事件不触发摘要刷新（可能很频繁），失败目标靠心跳兜底轮询发现。

### 6.3 测试

`tests/test_desktop_s1.py`（31 个用例）：配置容错、发现逻辑、SSE 解析、状态模型、控制器（快照后订阅、事件刷新、退避增长与重置、认证失败、自动拉起一次、工厂异常存活），以及：

- **集成**：用真实 `routes.router` + 真实 `AgentBridge` 起 uvicorn，验证客户端与服务端的真实接口契约——`new report → attention_hub → SSE attention → 客户端 → 托盘角标/黄色图标`；
- **Qt 离屏冒烟**：图标绘制、菜单文案、"启动 Agent"显隐、跨线程信号。

### 6.4 未验证项（需 Windows 手动）

真实系统托盘显示、`pythonw` 无窗口启动、`CREATE_NO_WINDOW` 拉起 daemon、单实例锁行为。清单见 [docs/desktop-client-guide.md](../docs/desktop-client-guide.md) 第 7 节。

## 7. S2 实施记录

### 7.1 结构

在 `apps/mini_agent_desktop/mini_agent_desktop/` 下新增三个模块，修改 `api.py` / `config.py` / `tray.py` / `app.py`；仍然**未改动 `src/` 下任何文件**，也没有新增服务端接口。

| 模块 | 职责 | 依赖 Qt |
|---|---|---|
| `quick.py` | 专属 session 客户端 `QuickSession`、`extract_reply()`、`run_task()` 轮询状态机（排队→运行→终态/超时） | 否 |
| `hotkey.py` | `parse_hotkey()` 组合键解析；`GlobalHotkey` 在专用线程里 `RegisterHotKey` + `GetMessageW` 循环 | 否 |
| `quick_panel.py` | `QuickPanel` 无边框置顶输入窗口；`QuickHotkeyBridge` 把热键线程的回调切回主线程 | 是 |
| `api.py`（改） | 新增 `chat` / `interrupt` / `turns` / `turn` / `history`，均支持 `session_id` 参数 | 否 |
| `config.py`（改） | 新增 `hotkey_enabled`（默认 `false`）与 `hotkey`（默认 `Ctrl+Alt+Space`） | 否 |
| `tray.py`（改） | 左键唤起快速面板；菜单新增\"快速面板…\"；`notify_text()` 托盘气泡 | 是 |
| `app.py`（改） | 装配面板与热键；热键注册失败只气泡提示，不影响其它功能 | — |

### 7.2 设计要点

- **专属 session 而非复用主会话**：`/v1/chat` 带 `session_id="desktop-quick"` 时，`SessionAgentPool` 会为它单独建 Agent，主会话的长任务不会挡住快速提问。代价是多一个 Agent 实例与一条 runner 线程（与设计文档 §3 的取舍一致）。
- **不新增接口**：只组合已有 `POST /v1/chat`、`GET /v1/turns/{id}`、`GET /v1/history`、`POST /v1/interrupt`。中断与历史读取都通过 query 参数 `session_id` 路由到专属 Agent（`routes.py::_bridge()` 已支持该路径）。
- **结果获取用轮询而非 SSE**：一次提问维持长连接不划算，且 SSE 事件是全局的，要按 turn 过滤。轮询间隔 1.5 秒，超时 30 分钟，超时只停止等待、不中断 daemon 上的任务。
- **线程模型**：面板所有网络调用都在 `threading.Thread` 里做，结果通过 Qt 信号回主线程；热键回调只 `emit` 一个信号，不直接碰界面。
- **热键保守 opt-in**：默认关闭；格式错误在构造时抛 `ValueError` 并弹窗跳过；`RegisterHotKey` 失败（被占用）只气泡提示。非 Windows 下 `start()` 返回 `(False, 原因)`，不抛异常。
- **退出时注销热键**：`quit_app()` 与 `app.exec()` 返回后都会 `stop()`，通过 `PostThreadMessageW(WM_QUIT)` 让消息循环退出并 `UnregisterHotKey`。

### 7.3 测试

`tests/test_desktop_s2.py`（35 个用例）：

- 热键：合法组合（大小写、`win` 别名、F 键、数字键）、非法组合（无修饰键、两个普通键、空记号、不支持的键、超出 F24）、非 Windows 降级；
- 配置：默认关闭、往返保存、错误类型回落默认值；
- reply 提取：字符串内容、文本块列表、跳过纯工具调用消息；
- `QuickSession` 请求形态（`httpx.MockTransport`）：`session_id` 路由、空内容不发请求、中断走 query、404 视为排队中、最近任务截断与顺序、401 向上抛出；
- `run_task` 状态机（注入假时钟）：排队→运行→完成取回复、出错不取回复、中断为终态、超时不中断；
- **契约**：客户端用到的 5 个路径/方法必须存在于真实 `routes.router`；
- Qt 离屏冒烟：面板发送、结果回填为 Markdown、最近任务列表与双击所需的 `UserRole` 数据。

全部桌面测试（S0 + S1 + S2）共 79 个用例通过：

```
PYTHONPATH=src python3 -m pytest tests/test_desktop_s1.py tests/test_desktop_attention_s0.py tests/test_desktop_s2.py -q
```

### 7.4 未验证项（需 Windows 手动）

真实热键触发与被占用时的报错、面板在全屏/多显示器下的定位、长任务期间快速提问的实际响应速度（验证\"专属 session 不排队\"这一设计假设）、托盘左键与右键的分工。清单见 [docs/desktop-client-guide.md](../docs/desktop-client-guide.md) 第 7.1 节。

### 7.5 已知限制

- 快速面板的专属 session 会出现在 `/sessions` 列表中，且与其它 session 一样占用历史存储；需要时可在看板中清理。
- 最近任务列表只显示专属 session 的 turn，不包含主会话；主会话的任务请看管理控制台。
- 面板不做流式输出，回复在 turn 结束后一次性显示。
- 热键只支持单个组合，不支持多组合或按键序列。

### 7.6 下一步

S3（Toast 与审批）：权限审批和 `ask_user_async` 问题可在 Toast / 面板中直接回答，点击 Toast 跳转到面板；按设计文档的 Toast 范围收窄原则，autonomous tick、digest、巩固循环一律静默。
