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
| S3 Toast 与审批 | 审批和异步提问可在 Toast/面板回答，点击跳转，去重与免打扰 | 2 天 | ✅ 已完成（待 Windows 手动验证） |
| S4 自启动与加固 | 登录自启（默认关）、token ACL（`icacls`）、日志、退出时是否停 daemon 的明确选项 | 1 天 | ✅ 已完成（待 Windows 手动验证） |
| A 弹窗可见性（后续修订） | 托盘可直接开关桌面弹窗、测试弹窗、判定原因写入日志、`--check` 显示两道开关 | 0.5 天 | ✅ 已完成（待 Windows 手动验证） |
| B 快速面板完整显示（后续修订） | 面板按鼠标所在屏幕可用区域夹紧尺寸与位置，整窗可见；右下角缩放把手 | 0.5 天 | ✅ 已完成（待 Windows 手动验证） |

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
| `tray.py`（改） | 左键唤起快速面板；菜单新增"快速面板…"；`notify_text()` 托盘气泡 | 是 |
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

真实热键触发与被占用时的报错、面板在全屏/多显示器下的定位、长任务期间快速提问的实际响应速度（验证"专属 session 不排队"这一设计假设）、托盘左键与右键的分工。清单见 [docs/desktop-client-guide.md](../docs/desktop-client-guide.md) 第 7.1 节。

### 7.5 已知限制

- 快速面板的专属 session 会出现在 `/sessions` 列表中，且与其它 session 一样占用历史存储；需要时可在看板中清理。
- 最近任务列表只显示专属 session 的 turn，不包含主会话；主会话的任务请看管理控制台。
- 面板不做流式输出，回复在 turn 结束后一次性显示。
- 热键只支持单个组合，不支持多组合或按键序列。

### 7.6 下一步

S3（Toast 与审批）：权限审批和 `ask_user_async` 问题可在 Toast / 面板中直接回答，点击 Toast 跳转到面板；按设计文档的 Toast 范围收窄原则，autonomous tick、digest、巩固循环一律静默。

## 8. S3 实施记录

### 8.1 相对原方案的两处调整

1. **Toast 用 `QSystemTrayIcon.showMessage` 而非 winotify / win11toast**。原方案想用第三方库做点击回调。实际上托盘气泡在 Windows 上经通知中心显示，且 Qt 自带 `messageClicked` 信号可直接回调，无需新增依赖。代价是样式不能自定义，这在当前范围内可以接受。
2. **后端补了两个发布点**。原方案假定 `permission` / `interaction` 已经会发 attention，实际上只有汇报与 cron 提问发。S3 在 `api/bridge.py` 的权限与交互登记处补发布，并为用户发起的 turn 完成/失败补发布（cron、autonomous 发起的不发）。

### 8.2 后端改动（`src/mini_agent`）

| 文件 | 改动 |
|---|---|
| `notification/attention_hub.py` | 新增 kind：`permission` / `interaction` / `turn_done` / `turn_failed` |
| `api/bridge.py` | `HttpPermissionGate.request()` 与 `register_pending()`、`HttpInteractionGate.register_pending()` 登记后发布（`level=blocking`）；`AgentBridge.emit_turn_done()` / `emit_error()` 调用 `_publish_user_turn_outcome()`，仅 `initiator=user` 发布，`session_id` 放进 `extra` 供客户端过滤 |

所有发布都经 `_publish_attention_safely()`，异常吞掉；无监听者时是空操作。`desktop_attention_events_enabled` 默认关闭，因此未开启的部署行为完全不变。

### 8.3 桌面端改动（`apps/mini_agent_desktop`）

| 模块 | 职责 | 依赖 Qt |
|---|---|---|
| `attention.py` | `QuietHours`（跨午夜、格式校验）；`ToastPolicy`（范围、去重、免打扰，阻塞类不受免打扰影响）；`collect_pending()` 汇总三类待回答项（单个接口失败不影响其它）；`answer_item()` 按类型构造回答体 | 否 |
| `pending_dialog.py` | `PendingDialog` 待处理面板；`AttentionBridge` 控制器线程→主线程桥 | 是 |
| `controller.py`（改） | 启动时记录崩溃告警基线；离线时若未确认告警数增长则发出 `daemon_crash` 事件（只提醒增量） | 否 |
| `tray.py`（改） | `toast()` 气泡；`messageClicked` 打开待处理面板；菜单新增"待处理事项…" | 是 |
| `api.py`（改） | 新增 `permissions_pending` / `respond_permission` / `interactions_pending` / `respond_interaction` / `cron_questions_pending` / `answer_cron_question` | 否 |
| `config.py`（改） | 新增 `toast_enabled`（默认 `false`）与 `quiet_hours`（默认空） | 否 |
| `app.py`（改） | attention 事件经 `AttentionBridge` 切回主线程，再过 `ToastPolicy` 决定是否弹气泡 | — |

### 8.4 Toast 范围与依据

| 事件 | 弹出 | 依据 |
|---|---|---|
| 审批 / 交互提问 / 阻塞 cron 提问 | 是（阻塞） | 任务停在那里等回答 |
| 普通 cron 提问 | 是（非阻塞，受免打扰） | 可以等 |
| 用户任务完成 / 失败 | 是（非阻塞，受免打扰） | 用户可能已离开 |
| 快速面板的任务完成 | 否 | 结果已在面板里显示 |
| cron / autonomous 发起的 turn | 否 | 主动性晚于可靠性 |
| 汇报 / autonomous tick / digest / 巩固 | 否 | 同上，仍在托盘计数中 |
| daemon 异常退出（增量） | 是（阻塞） | 启动时的旧告警不提醒 |

### 8.5 测试

`tests/test_desktop_s3.py`（36 个用例）：

- 后端：权限登记发布阻塞类事件、交互登记以问题文本为标题、无监听者是空操作、监听器异常不影响登记、用户 turn 完成/失败发布、cron/autonomous 静默、未知 turn 静默；
- 免打扰：跨午夜与同日时段、格式错误、阻塞类不受影响、被抑制的事件不会延后重复弹出；
- 策略：默认关闭、范围、去重、快速面板 session 静默、垃圾事件容错；
- 待处理：三类收集、单接口失败隔离、各类回答体（批准/拒绝、是/否、选项索引、文字、cron 文字）、空回答不发请求、404 向上抛出；
- 控制器：启动时已有告警不提醒，增长后提醒一次，再次离线不重复；
- Qt 离屏：面板渲染各类按钮、空列表提示。

桌面端全部测试（S0–S3）及与 bridge / permission / notification 相关的后端回归测试应一并运行。

### 8.6 未验证项（需 Windows 手动）

气泡在通知中心中的实际样式与点击行为、Windows 专注助手（勿扰模式）对气泡的拦截、`messageClicked` 在 Windows 11 上是否可靠触发、多显示器下面板定位。清单见 [docs/desktop-client-guide.md](../docs/desktop-client-guide.md) 第 7.2 节。

### 8.7 已知限制

- Toast 依赖 daemon 开启 `desktop_attention_events_enabled`；关闭时只有崩溃提醒可用（它由控制器本地发出）。
- 去重记忆只在桌面端进程内，重启后同一未处理事项可能再弹一次。这是有意的：重启后仍未处理的阻塞事项值得再次提醒。
- 面板里的审批只提供"仅本次"，"总是允许"仍需在看板中设置，避免误点造成长期授权。
- 交互提问的选择题若选项过多（超过约 10 个），按钮会换行显示，体验一般；可考虑改为下拉框。

### 8.9 回归验证与修复记录

对与 bridge / permission / notification 相关的后端测试做了整体回归（2533 通过，43 失败），并把全部失败项拿到**未修改的原始代码**上对比：

- 39 个失败在原始代码上同样失败，属于既有问题（例如 MCP 连接、goal execution spec 路由、`tests/test_system_tool_call_and_debug.py` 中两条 XML 工具结果格式用例等），与本次改动无关，未在本阶段处理；
- 4 个 `TestLLMDebugLogger` 用例只在整体运行时失败、单独运行通过，定位为**既有隐患**：`llm/debug_logger.py::_build_file_logger()` 用 `id(path)` 命名 logger，对象回收后 id 会被复用，新 logger 会拿到指向已删除临时文件的旧 handler，日志静默丢失。已改为用路径字符串命名。修复后相关组合只剩上述两条既有 XML 失败；
- `tests/test_desktop_s1.py` 在原始代码上也会污染后续用例（同一根因），修复后一并消失。

修复后桌面端测试（S0–S3）合计 115 个用例一次性全部通过。

### 8.8 下一步

S4（自启动与加固）：登录自启（默认关闭）、token 文件的 Windows ACL 收紧（`icacls`）、桌面端日志、退出时是否停止 daemon 的明确选项。

## 9. S4 实施记录

### 9.1 结构

| 模块 | 职责 | 依赖 Qt |
|---|---|---|
| `autostart.py` | 启动命令构造（pythonw 替换、引号、可选 `--project-root`）；`WinRegRunStore`（`winreg` 读写 HKCU\Run，仅 Windows）；`sync()` 幂等同步 | 否 |
| `security.py` | `icacls_argv()`；`harden_file()` 返回 hardened / missing / failed / unsupported；runner 可注入 | 否 |
| `logsetup.py` | `RotatingFileHandler`（1 MB × 3）；幂等；目录不可写时退化为 NullHandler | 否 |
| `launch_desktop.pyw`（新增） | 登录自启的启动器：把自身目录加入 sys.path 后调用 `app.main()` | — |
| `api.py`（改） | `shutdown()` → `POST /v1/shutdown` | 否 |
| `config.py`（改） | `autostart`（默认 `false`） | 否 |
| `controller.py`（改） | 连接成功与离线切换写入日志 | 否 |
| `tray.py`（改） | 新增"开机自动启动"（可勾选）、"加固 token 文件权限"、"打开日志文件夹"、"退出并停止 Agent…" | 是 |
| `app.py`（改） | 启动时同步自启项；各菜单动作的实现；退出并停止前弹窗确认；热键与 Toast 结果写日志 | — |

### 9.2 设计要点

- **启动器而非 `-m`**：Run 项的工作目录不可控，`python -m mini_agent_desktop` 在别的目录下找不到包。`launch_desktop.pyw` 把自身目录加入 sys.path，然后调用 `main()`，参数原样透传。
- **只动自己的值**：`HKCU\...\Run` 下只读写 `mini_agent_desktop`；关闭自启时只删这一个值。
- **启动时同步**：以 `desktop.json` 为准写入或删除。路径变化（例如项目搬家）会自动更新命令。
- **ACL 加固只在点击时执行**：`icacls /inheritance:r /grant:r <用户>:F`。不做后台自动改动，因为错误的权限可能让 daemon 读不到 token。
- **两种退出语义分开**："退出桌面端"保留 daemon；"退出并停止 Agent"需要确认，并经 `POST /v1/shutdown` 走与 `daemon stop` 相同的优雅关停通道。
- **日志只用自己的 logger**：名为 `mini_agent_desktop`，`propagate=False`，不影响宿主进程的其它日志。

### 9.3 测试

`tests/test_desktop_s4.py`（26 个用例）：启动器文件存在且可编译、pythonw 替换、引号与可选参数；sync 的写入/幂等/重写/删除/不支持；icacls 参数（仅当前用户 F、无继承）；非 Windows、文件缺失、成功、失败（含 stderr）、icacls 不存在五种分支；两个 token 位置的加固；日志写文件、幂等、目录不可写降级；`/v1/shutdown` 在真实路由中存在且客户端请求形态正确；autostart 配置默认关闭；托盘 S4 菜单项与回调（离屏 Qt）。

全部桌面端测试 S0–S4 合计 141 个用例通过。

### 9.4 未验证项（需 Windows 手动）

注册表项的实际写入与注销后的状态、pythonw 无窗口启动、`icacls` 的真实输出与加固后 CLI/桌面端连接是否正常、Windows 启动时托盘出现的时机、"退出并停止 Agent"在 daemon 忙碌时的关停耗时。清单见 [docs/desktop-client-guide.md](../docs/desktop-client-guide.md) 第 7.3 节。

### 9.5 已知限制

- 自启动依赖当前用户登录；不适用于无人登录的服务器场景（设计上明确不做 Windows Service）。
- ACL 加固只授予当前用户；daemon 若以其它账户运行会读不到 token，需要先手动调整。
- 日志只记录桌面端自身行为，daemon 的日志见 `docs/logging-mechanisms-guide.md`。
- 打包（PyInstaller）仍未做，与计划 §2 一致：第一阶段依赖 venv + pythonw。

### 9.6 全部阶段结束

S0–S4 已全部按计划实施。后续若需要真正的安装包、自动更新或 Windows Service，应另起计划评估，不属于本计划范围。

## 10. 阶段A实施记录：弹窗为什么不出现（后续修订）

### 10.1 现象与根因

用户反馈桌面端一直没有弹窗。逐层排查后，链路本身是通的（daemon 发布 → SSE `attention` → `ToastPolicy` → 托盘气泡），问题在于**两道开关的可见性不足**：

1. **桌面端 `toast_enabled` 默认关闭，且托盘菜单里没有开关**。用户只能手动编辑 `desktop.json`，很容易以为功能不存在。
2. **没有任何反馈**：不弹窗的原因（未开启、静默类型、快速面板、去重、免打扰）都不落日志，`--check` 也不显示，无法判断是哪一环。
3. daemon 侧 `desktop_attention_events_enabled` 在项目 `agent_config.json` 中已是 `true`，不是原因；但这一点此前不直观，`--check` 现在会直接显示。

### 10.2 改动

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/attention.py` | `ToastPolicy.decide()` 拆出 `_decide()`，每次判定写入 `last_reason`（`toast_disabled` / `silent_kind` / `quick_session` / `no_ref` / `duplicate` / `quiet_hours` / `ok`）；判定结果不变 |
| `apps/mini_agent_desktop/mini_agent_desktop/tray.py` | 新增可勾选的“桌面弹窗提醒”（回调 `on_toggle_toast`，初始状态 `toast_checked`）与“发送测试弹窗”（绕过规则，直接发气泡） |
| `apps/mini_agent_desktop/mini_agent_desktop/app.py` | 每条 attention 事件打日志 `attention kind=… ref=… → 弹窗/原因`；勾选即时生效并写回 `desktop.json`；`--check` 增加“桌面弹窗”与“daemon 推送”两行状态 |
| `tests/test_desktop_popup_stage_a.py` | 新增 7 个用例：各原因分支、运行时开关、托盘勾选回调、`--check` 状态行 |
| `docs/desktop-client-guide.md` | §4.2 增加“两道开关”与“什么时候会弹（判定顺序表）”；§6 排障增加“没有弹窗”条目；§7.2 清单更新 |
| `docs/desktop-attention-events-guide.md` | §2 后增加说明：daemon 开关与桌面端开关的关系 |

### 10.3 什么情况会弹窗（结论）

弹窗需同时满足：桌面端“桌面弹窗提醒”已勾选；daemon 推送已开启；事件属于下列类型之一，且不是重复事件：

- 工具审批、交互提问（阻塞，免打扰不影响）；
- cron 异步提问（非阻塞受免打扰影响；阻塞类不受影响）；
- 用户自己发起的任务完成/失败（快速面板与 cron/autonomous 发起的不弹）；
- daemon 异常退出（仅当未确认告警增加时，启动前的旧告警不弹）。

汇报、autonomous tick、digest、巩固循环始终静默。

### 10.4 测试

`tests/test_desktop_popup_stage_a.py` 7 个用例通过；桌面端全部测试（含阶段 S0–S4 与本阶段）合计 165 个用例通过。

### 10.5 未验证项（需 Windows 手动）

Windows 通知中心对 Python 应用的通知开关、勿扰模式/专注助手对气泡的拦截、“发送测试弹窗”是否可见。清单见 `docs/desktop-client-guide.md` §7.2。

## 11. 阶段B实施记录：快速面板完整显示（后续修订）

### 11.1 现象与根因

快速面板每次打开只做 `move(鼠标位置)`，既不设置尺寸，也不检查屏幕边界。靠近屏幕底部或右侧时窗口伸出屏幕外；窗口尺寸依赖布局 sizeHint，回复区可能只剩很小一块，因此只能看到一部分。

### 11.2 改动

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/panel_geometry.py` | 新增（纯逻辑，不依赖 Qt）：`fit_panel(cursor, avail, want_w, want_h)`，尺寸不超过可用区域，位置以鼠标为锚点并夹紧到所在屏幕可用区域（扣任务栏，留 8px） |
| `apps/mini_agent_desktop/mini_agent_desktop/quick_panel.py` | `open_panel()` 改为 `_place_on_screen()`：取鼠标所在屏幕的 `availableGeometry()`，调用 `fit_panel` 后 `setGeometry`；屏幕信息缺失时退回默认尺寸；最小高度 360；回复区最小高度 120、最近任务列表最小高度 60；右下角增加 `QSizeGrip` 缩放把手 |
| `tests/test_desktop_quick_panel_geometry.py` | 新增 12 个用例：居中、四角与中部、小屏缩小、负坐标副屏、参数化边角、Qt 离屏下 `open_panel` 后窗口确实被夹紧且回复区有高度 |
| `docs/desktop-client-guide.md` | §4.1 增加“尺寸与位置”说明；§7.1 清单增加 4 条阶段B 手动验证项 |

### 11.3 设计要点

- **几何计算独立成纯函数**：沙盒是 Linux，无法看到真实屏幕；把夹紧逻辑抽出后可以在任意屏幕尺寸与光标位置下单测，不依赖 Qt。
- **夹紧而非缩放位置**：优先保持目标尺寸；只有可用区域确实放不下时才缩小尺寸，避免“整窗超出屏幕”。
- **每次打开都重新放置**：光标位置变化后，旧位置不再有意义；尺寸也按当前屏幕重算，手动拉大的尺寸在下次打开时会被重新夹紧。

### 11.4 测试

`tests/test_desktop_quick_panel_geometry.py` 12 个用例与既有 `test_desktop_s2.py` 36 个用例通过。桌面端全部测试合计 165 个用例通过（含阶段 A）。

### 11.5 未验证项（需 Windows 手动）

真实多显示器与缩放比例（DPI 125%/150%）下 `availableGeometry()` 与鼠标坐标的一致性；`QSizeGrip` 在无边框窗口上的实际缩放手感。清单见 `docs/desktop-client-guide.md` §7.1。

### 11.6 下一步

两个阶段均已完成离屏验证，等待 Windows 实机确认。若实机仍不弹窗，优先看桌面端日志中的 `attention … → 原因` 行，它会直接指出卡在哪一环。
