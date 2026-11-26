# Windows 桌面常驻客户端指南（Desktop Client）

> 设计与分阶段计划见 [`next_doc/windows_desktop_resident_plan.md`](../next_doc/windows_desktop_resident_plan.md)。
> 当前已实现：**S1 系统托盘**、**S2 快速面板与全局热键**、**S3 Toast 与待处理事项**、**S4 自启动、token 加固与日志**。至此 `windows_desktop_resident_plan.md` 的 S0–S4 全部完成（待 Windows 手动验证）。
> 代码位置：`apps/mini_agent_desktop/`。

## 1. 它是什么

一个常驻系统托盘的**纯 HTTP/SSE 客户端**：只通过 daemon 的 `/v1/*` 接口与 mini_agent 通信，不 import mini_agent 核心包，也不改变 daemon 的任何行为。关闭它不影响后台 daemon。

托盘能告诉你：

- daemon 是否在线、是否正在执行任务；
- 有多少事项在等你处理（权限审批、交互请求、cron 异步提问、未读汇报、失败目标、崩溃告警）；
- daemon 崩溃后，即使 HTTP 已不可用也能提示（直接读崩溃告警文件）。

## 2. 安装与启动

```powershell
pip install -r apps/mini_agent_desktop/requirements.txt     # PySide6-Essentials + httpx
cd <mini_agent 项目根目录>
python -m pip install -e .                                   # 如果 daemon 还没装好
# 在 apps/mini_agent_desktop 目录下，或把它加入 PYTHONPATH：
cd apps\mini_agent_desktop
pythonw -m mini_agent_desktop     # 无控制台窗口；项目目录自动查找，也可加 --project-root D:\path\to\mini_agent 显式指定
python  -m mini_agent_desktop --project-root D:\path\to\mini_agent     # 调试时带控制台
```

前置条件：mini_agent 的 daemon 需要先在该项目根目录下运行（`mini-agent daemon start --detach`）。要让托盘能**即时**感知新增汇报/提问，还需在 `agent_config.json` 打开（默认关闭，改后重启 daemon），详见 [desktop-attention-events-guide.md](desktop-attention-events-guide.md)：

```json
{ "http": { "desktop_attention_events_enabled": true } }
```

不打开也能用：托盘退回按 `poll_interval_seconds`（默认 30 秒）兜底轮询摘要。

### 命令行参数

| 参数 | 说明 |
|---|---|
| `--project-root DIR` | 要连接的项目根目录；缺省读配置文件，再缺省用当前目录 |
| `--port N` | daemon 端口；缺省读 `<project_root>/.agent/daemon_info.json`，再缺省读配置（8765） |
| `--auto-start-daemon` | daemon 未运行时自动拉起（默认关闭） |
| `--check` | **无界面自检**后退出（不需要 PySide6），排障首选 |

`--check` 退出码：`0` 正常，`2` daemon 未运行，`3` 认证失败（token 无效/缺失），`4` 其它错误。

### 项目目录自动查找

`--project-root` 可以省略。桌面端按以下顺序确定 mini_agent 项目根目录，找到即停：

1. 命令行 `--project-root`（显式，原样采用）
2. `desktop.json` 的 `project_root`（显式，原样采用）
3. 环境变量 `MINI_AGENT_PROJECT_ROOT`（显式，原样采用）
4. 从当前工作目录向上查找
5. 从桌面端自身所在位置向上查找（在仓库内 `apps\mini_agent_desktop\` 下运行时总能找到项目）

自动查找（4、5）只认带有 `src\mini_agent\__init__.py` 的目录，避免误判。找不到时不会静默使用当前目录：`--check` 以退出码 4 给出提示，托盘版弹窗提示并退出。

`--check` 的输出会注明目录来源，例如 `（来源：桌面端安装位置向上查找）`，便于确认找的是哪个项目。

> 注意：`python -m mini_agent_desktop` 要求能导入该包，因此需要在 `apps\mini_agent_desktop` 目录下运行，或设置 `PYTHONPATH`。在其它目录运行会报 `No module named mini_agent_desktop`，这与项目目录查找无关。登录自启使用的 `launch_desktop.pyw` 不受此限制。

## 3. 配置文件（可选）

路径：`%LOCALAPPDATA%\mini_agent\desktop.json`（其它系统 `~/.config/mini_agent/desktop.json`；环境变量 `MINI_AGENT_DESKTOP_CONFIG` 可覆盖）。文件不存在或损坏时一律使用默认值。

```json
{
  "project_root": "D:\\path\\to\\mini_agent",
  "http_port": 8765,
  "console_url": "http://localhost:8501",
  "poll_interval_seconds": 30,
  "auto_start_daemon": false,
  "hotkey_enabled": false,
  "hotkey": "Ctrl+Alt+Space",
  "toast_enabled": false,
  "quiet_hours": "",
  "autostart": false
}
```

**默认值全部保守**：`auto_start_daemon` 默认 `false`——daemon 没运行时，托盘菜单会出现手动的"启动 Agent"，而不是自动拉起。

## 4. 托盘图标与菜单

| 图标颜色 | 含义 |
|---|---|
| 绿 | 在线、空闲 |
| 蓝（中心白点） | 正在执行任务 |
| 黄 | 有事项需要你处理（右下角红色数字角标 = 待处理总数，>9 显示 `9+`） |
| 红 | 有未确认的 daemon 崩溃告警（在线或离线均会显示） |
| 灰 | 连不上 daemon |

菜单：状态行 / 当前任务数 / 待处理事项数（均为信息行）、**打开管理控制台**（打开 `console_url`）、**启动 Agent**（仅在"daemon 未运行"时出现）、**重新连接**、**退出桌面端**。

- 鼠标悬停显示各类待处理数量明细。
- **退出桌面端不会停止 daemon**（菜单文案已注明）。
- 左键单击唤起**快速面板**（S2）；右键弹出菜单，菜单中也有"快速面板…"。
- 同一用户只允许运行一个桌面端实例（`QLockFile`），重复启动会提示并退出。

## 4.1 快速面板与全局热键（S2）

**快速面板**是一个无边框、置顶的小窗口：输入一句话，结果直接显示在窗口里，不用切回控制台。

- 打开方式：左键点托盘图标、菜单"快速面板…"，或（需开启）全局热键。
- 操作：`Enter` 发送，`Shift+Enter` 换行，`Esc` 关闭窗口。发送期间"发送"按钮禁用，"中断"按钮可用。
- 结果以 Markdown 渲染显示；窗口下方列出**最近任务**，双击某条可把原文填回输入框再改一改重发。
- 面板使用**专属 session**（`desktop-quick`）：daemon 为它单独建一个 Agent，所以主会话正在跑长任务时，快速提问**不会排在后面**。代价是多一个 Agent 实例；这个 session 会和其它 session 一样出现在 `/sessions` 列表中。
- 面板没有新增任何服务端接口：只组合已有的 `POST /v1/chat`、`GET /v1/turns/{id}`、`GET /v1/history`、`POST /v1/interrupt`，并都带上 `session_id` 参数。结果通过轮询 turn 状态获取（不走 SSE）。
- 超过 30 分钟仍未结束时面板会停止等待并提示，**不会**中断 daemon 上的任务；之后可在主控制台查看。

**全局热键**默认**关闭**（保守 opt-in，避免抢占其它程序的快捷键）。开启方式：在 `desktop.json` 中设置 `"hotkey_enabled": true`，重启桌面端。

- 格式：修饰键（`Ctrl`/`Alt`/`Shift`/`Win`）+ 且只能一个普通键（字母、数字、`F1`–`F24`、`Space`、`Enter`、`Tab`、`Esc`），例如 `Ctrl+Alt+Space`、`Win+Shift+K`。必须至少有一个修饰键。
- 配置写错（格式不对）时启动会弹窗提示并跳过注册，桌面端其它功能正常。
- 被其它程序占用时（`RegisterHotKey` 失败），托盘气泡提示"全局热键未注册"及原因，同样不影响其它功能。
- 仅 Windows 有效；其它系统上热键自动降级为不可用。

## 4.2 Toast 与待处理事项（S3）

**Toast** 用 Windows 原生气泡（经通知中心显示）提醒你需要处理的事情。**待处理事项面板**列出所有等你回答的事项，可直接在窗口里处理。

**前置条件**：Toast 依赖 daemon 推送 `attention` 事件，需要在 daemon 配置中开启（默认关闭，见 [桌面关注事件指南](desktop-attention-events-guide.md) §2）：

```json
{ "http": { "desktop_attention_events_enabled": true } }
```

不开启时桌面端仍可用，只是收不到 Toast，托盘与面板照常工作。崩溃提醒不依赖这个开关。

**会弹 Toast 的事件**（范围刻意收窄，遵循"主动性晚于可靠性"）：

| 事件 | Toast 标题 | 说明 |
|---|---|---|
| 工具审批等待你 | 需要审批 | 阻塞类：有任务停在那里等你 |
| 交互式提问（ask_user 系列） | 需要你回答 | 阻塞类 |
| cron 异步提问 | 后台任务有问题 | 阻塞类仅限 `urgency=blocking` 的问题 |
| 你自己发起的任务完成 / 失败 | 任务完成 / 任务失败 | cron、autonomous 发起的静默；快速面板的任务静默（结果已在面板里） |
| daemon 异常退出 | daemon 异常退出 | 仅当未确认崩溃告警**增加**时提醒；启动时已存在的旧告警不提醒 |

**静默的**：汇报（watchlist）、autonomous tick、digest、巩固循环。它们仍在托盘计数里，只是不弹气泡。

**去重**：同一事件只提醒一次。被抑制的事件依然留在待处理面板里，不会丢失。

**免打扰**：`quiet_hours` 设为如 `"23:00-08:00"` 时，时段内非阻塞类 Toast 被抑制；**阻塞类（审批、提问、崩溃）不受影响**，因为没有答案任务就停在那里。跨午夜写法支持；格式错误时启动会弹窗提示并忽略该设置。

**点击 Toast**或托盘菜单"待处理事项…"打开面板。面板每项的操作：

- 工具审批：**批准 / 拒绝**（仅本次，mode=once；"总是允许"请在看板里设置）；
- 交互提问：是否题给 **是 / 否**，选择题给选项按钮，其它给文字框；
- cron 提问：有选项给按钮，否则文字框；
- 点击后若返回 404，说明已被 CLI 或看板先处理，面板会提示并自动刷新。

## 4.3 自启动、token 加固、日志与退出选项（S4）

**开机自动启动**：托盘菜单"开机自动启动"（可勾选），或 `desktop.json` 中 `autostart: true`。原理是在 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 下写入一个名为 `mini_agent_desktop` 的值，启动命令为 `pythonw launch_desktop.pyw --project-root <路径>`。

- 只读写自己的值，不碰其它启动项；无需管理员权限。
- 每次启动时以 `autostart` 为准同步注册表项：路径变了会重写，设为关闭会删除自己的值。
- 启动器 `launch_desktop.pyw` 位于 `apps/mini_agent_desktop/`，用 pythonw 运行，没有控制台窗口。

**加固 token 文件权限**：菜单"加固 token 文件权限（仅当前用户）"会对 `.agent/agent_api.key` 与 `.agent/users/tokens/owner.key`（存在时）执行 `icacls <文件> /inheritance:r /grant:r <当前用户>:F`，即去掉继承的权限、只保留当前用户的完全控制。

- 只在你点击时执行，不做后台自动改动；结果以气泡与日志反馈。
- `/grant:r` 会替换文件上已有的显式授权。若 daemon 以其它账户运行，请先调整再加固。
- 仅 Windows；其它系统返回"不支持"。

**日志**：`%LOCALAPPDATA%\mini_agent\logs\desktop.log`（单文件 1 MB，保留 3 份）。记录启动、连接/离线切换与原因、全局热键注册结果、Toast 发出内容、开机自启与 ACL 加固的结果、关停请求。菜单"打开日志文件夹"直接打开目录。日志目录不可写时静默降级，不影响运行。

**退出选项**：菜单里有两个明确选择，互不混淆：

- **退出桌面端（Agent 继续在后台运行）**：默认的退出方式，只关托盘，daemon 不受影响；
- **退出并停止 Agent…**：先弹窗确认，然后请求 daemon 优雅关停（`POST /v1/shutdown`，与 `mini-agent daemon stop` 的第一步相同），正在执行的任务会被中断。daemon 本来已停止时也会正常退出桌面端。

## 5. 连接行为

1. 先 `health` 检查 → 取 `/v1/status` 与 `/v1/desktop/summary` **快照** → 再订阅 `/v1/stream`（不回放历史）。
2. 收到 `turn_start`/`turn_done` 等事件立即刷新状态；`permission_req`/`interaction_req`/`attention`/`cron_job_finished` 立即刷新待处理计数。
3. SSE 心跳（约 20 秒）顺带检查：距上次摘要 ≥ `poll_interval_seconds` 则兜底刷新（覆盖别的客户端已读汇报、事件开关未开的情况）。
4. 断线后指数退避重连（1/2/4/8/15/30 秒封顶），连上一次即重置；"重新连接"/"启动 Agent"会跳过等待立即重试。
5. 区分离线原因：`daemon 未运行`（可手动启动）、`认证失败，请检查 token`（拉起 daemon 没用，菜单不提供启动）、`连接中断`。
6. token 自动读取 `<project_root>/.agent/agent_api.key`，不存在则尝试多用户模式的 `.agent/users/tokens/owner.key`；只连接 `127.0.0.1`。

## 6. 排障

```powershell
python -m mini_agent_desktop --check
```

输出端点、token 是否找到、离线崩溃告警数、health/status/summary 结果。常见情形：

- `health: 失败` → daemon 没运行或端口不对（看 `.agent/daemon_info.json`）；
- `HTTP 401/403` → token 与 daemon 不一致（daemon 是否用了别的项目根目录/配置了 `api_token`）；
- 托盘图标不见 → Windows 可能把它折叠进了"隐藏的图标"区，拖出来即可。

## 7. Windows 手动验证清单（S1）

沙盒是 Linux，只能验证非 UI 逻辑与离屏 Qt。请在 Windows 上确认：

- [ ] `--check` 在 daemon 运行/未运行/token 错误三种情况下退出码为 0/2/3
- [ ] 启动后托盘出现图标，daemon 运行时为绿色
- [ ] 在 CLI 发起一个长任务，图标变蓝，结束后变回绿
- [ ] 触发一次权限审批（或 `ask_user_async`），图标变黄且角标数字正确
- [ ] `daemon stop` 后图标变灰，菜单出现"启动 Agent"，点击后能恢复
- [ ] 强杀 daemon 进程后，图标变红（有崩溃告警时）
- [ ] 退出桌面端后 daemon 仍在运行（`mini-agent daemon status`）
- [ ] 重复启动第二个实例会提示"已经在运行"

## 7.1 Windows 手动验证清单（S2）

- [ ] 左键单击托盘图标弹出快速面板，窗口置顶、出现在鼠标附近，输入框自动获得焦点
- [ ] 输入"你好"回车，状态依次显示"已提交"→"运行中…"→"完成"，结果区显示回复
- [ ] 主会话正在跑长任务时，快速面板提问仍能较快得到响应（确认走的是 `desktop-quick` 专属 Agent）
- [ ] 按 `Esc` 关闭；再次唤起时"最近任务"列表包含刚才的提问，双击可回填输入框
- [ ] 发送期间点"中断"，状态变为"已中断"或"完成"，且面板恢复可发送
- [ ] 把 `desktop.json` 的 `hotkey_enabled` 设为 `true`，`Ctrl+Alt+Space` 能在任意窗口唤起/隐藏面板
- [ ] 把 `hotkey` 改为已被占用的组合键，启动后托盘出现"全局热键未注册"气泡，其它功能正常
- [ ] 把 `hotkey` 改成 `Space`（无修饰键）启动，弹出配置错误提示，程序不崩溃
- [ ] 停止 daemon 后打开快速面板发送，显示"无法连接 daemon"而不是卡死

## 7.2 Windows 手动验证清单（S3）

先在 daemon 配置里开启 `desktop_attention_events_enabled`，并把桌面端 `toast_enabled` 设为 `true`，然后：

- [ ] 在 CLI 触发一次工具审批（需要确认的 bash），系统弹出"需要审批"气泡；点击后待处理面板出现该项
- [ ] 在面板点"批准"，agent 继续执行，托盘黄色角标消失
- [ ] 让 agent 调用 `ask_user_choice`，气泡标题为"需要你回答"，面板里能点选项回答
- [ ] 同一审批不会弹两次（去重）
- [ ] 主会话一条普通任务完成后弹"任务完成"；快速面板里的提问完成后**不**弹
- [ ] 设置 `quiet_hours` 为包含当前时刻的时段，普通任务完成不弹，但审批仍弹
- [ ] 在 CLI 先回答同一审批，再在面板点击，面板提示"已被处理"并刷新
- [ ] 强杀 daemon 后，托盘变红并弹"daemon 异常退出"；重启桌面端后不会为旧告警再弹
- [ ] `toast_enabled=false` 时全部气泡消失，但托盘角标与面板照常工作

## 7.3 Windows 手动验证清单（S4）

- [ ] 勾选"开机自动启动"，用 `regedit` 在 `HKCU\...\Run` 下看到 `mini_agent_desktop` 值，内容指向 `pythonw.exe` 与 `launch_desktop.pyw`
- [ ] 注销或重启 Windows，托盘图标自动出现且无控制台窗口
- [ ] 取消勾选后，注册表中的 `mini_agent_desktop` 值被删除，其它启动项保持不变
- [ ] 点"加固 token 文件权限"，`icacls .agent\agent_api.key` 只列出当前用户的 `(F)` 且没有继承项；加固后 CLI 与桌面端仍能正常连接 daemon
- [ ] 点"打开日志文件夹"打开的目录中有 `desktop.log`，内容包含启动、连接与离线记录
- [ ] "退出桌面端"后 `mini-agent daemon status` 仍显示运行中
- [ ] "退出并停止 Agent…"弹出确认，确认后 daemon 停止、桌面端退出；点取消则什么都不发生
- [ ] daemon 已停止时点"退出并停止 Agent…"，桌面端仍能正常退出

## 8. 代码位置

- `apps/mini_agent_desktop/mini_agent_desktop/config.py`：桌面端配置
- `discovery.py`：端点/token/崩溃告警发现、拉起 daemon（Windows 下 `CREATE_NO_WINDOW`）
- `api.py`：同步 httpx 客户端 + SSE 解析器
- `state.py`：托盘状态模型（图标优先级、菜单文案、事件→刷新集合）
- `controller.py`：后台线程（快照→订阅→重连退避），不依赖 Qt
- `tray.py`：PySide6 托盘（图标程序绘制，不带图片资源）
- `app.py`：入口、`--check`、单实例锁、热键注册与快速面板装配
- `hotkey.py`：组合键解析（纯函数）+ Windows `RegisterHotKey` 专用消息循环线程（S2）
- `quick.py`：快速面板的纯逻辑——专属 session 客户端、reply 提取、turn 轮询状态机（S2，不依赖 Qt）
- `autostart.py`：登录自启命令构造、HKCU\Run 读写（`winreg`，仅 Windows）与同步逻辑（S4）
- `security.py`：token 文件 ACL 加固（`icacls` 参数构造 + 可注入 runner，S4）
- `logsetup.py`：桌面端滚动日志（S4）
- `../launch_desktop.pyw`：登录自启的启动器（S4）
- `quick_panel.py`：PySide6 快速面板与热键→主线程桥（S2）；网络调用全部在工作线程
- `attention.py`：Toast 策略（范围/去重/免打扰，纯逻辑）与待处理事项的收集、回答体构造（S3，不依赖 Qt）
- `pending_dialog.py`：待处理事项面板与 attention 事件→主线程桥（S3）
- 测试：`tests/test_desktop_s1.py`（含起真实路由 + 真实 `AgentBridge` 的端到端集成测试与离屏 Qt 冒烟）、`tests/test_desktop_s2.py`（热键解析、quick 客户端请求形态、状态机、路由契约、离屏 Qt 发送冒烟）、`tests/test_desktop_s3.py`（后端 attention 发布、Toast 策略与免打扰、待处理收集与回答、崩溃提醒、离屏面板）、`tests/test_desktop_s4.py`（自启命令与同步、icacls 参数与结果分支、日志降级、shutdown 契约、托盘菜单）
