# 桌面端功能扩展改进计划（E 系列）

> 前置：`windows_desktop_resident_plan.md` S0–S4 与修订 A/B 已完成（待 Windows 实机验证）。
> 本计划在其基础上扩展桌面端功能。按"一个阶段一个阶段执行"：每阶段完成后更新相关文档、补测试，并打包本阶段的修改与新增文件（保持项目内相对路径，便于覆盖）。

## 1. 范围与原则

- 桌面端仍是纯 HTTP/SSE 客户端，**不改 `src/mini_agent` 核心**，只复用已有接口（`/v1/stream/{turn_id}`、`/v1/goals`、`/v1/cron/jobs`、`PUT /v1/cron/jobs/{id}`）。
- 凡是"会主动做事"或"读取用户环境"的能力，默认关闭（保守 opt-in）。
- 纯逻辑（解析、判定、几何、文件暂存）不依赖 Qt，可在 Linux 沙盒单测；Qt 层只做展示，并有离屏冒烟测试。
- 上下文感知与项目既有文档的"暂不做"存在张力，因此只做**显式、可关闭、只读窗口标题与程序名、不落盘、不读取页面内容**的最小版本，并在文档中说明边界。

## 2. 阶段表

| 阶段 | 内容 | 依赖 | 默认状态 |
|---|---|---|---|
| E1 热键设置 | 设置窗口：启用开关、组合键输入与即时校验、保存后**无需重启**重新注册热键；注册失败给出原因 | config、hotkey | 热键仍默认关闭 |
| E2 托盘健康面板 | 窗口集中显示：连接状态与时长、重连次数、最近离线原因、崩溃告警数、弹窗/热键/推送开关、事件接收计数；可刷新 | controller、config | 只读展示 |
| E3 流式输出 | 快速面板订阅 `/v1/stream/{turn_id}`，边生成边显示 `token` 事件；流断开或不可用时回退为轮询整段结果 | api、quick | 自动启用，无开关 |
| E4 拖文件到面板 | 拖入文件/文件夹 → 复制到临时目录 `%TEMP%\mini_agent_drop\<批次>\` → 发送时把目录路径告诉 agent；限制大小与数量；过期目录自动清理 | quick_panel、新模块 | 自动启用 |
| E5 目标与定时任务视图 | 托盘打开窗口：进行中的目标（objective）与 cron 任务、下次运行时间、执行状态；可暂停/恢复 cron | api、新窗口 | 只读 + 启停 |
| E6 上下文感知（最小版） | 快速面板可附带"当前前台窗口的标题与程序名"（浏览器/编辑器/终端分类）；需在设置中总开关开启；发送时才读取，不落盘、不进日志 | config、quick_panel、新模块 | 默认关闭 |

## 3. 各阶段要点

### E1 热键设置
- 设置窗口字段：`hotkey_enabled`（复选框）、`hotkey`（输入框，失焦/输入时校验）、保存按钮。
- 校验复用 `hotkey.parse_hotkey`，并新增纯函数 `validate_hotkey_text()` 返回可读错误信息。
- 保存流程：写 `desktop.json` → 停止旧热键 → 按新配置启动 → 托盘气泡报告结果（成功/被占用/格式错误）。
- 托盘菜单新增"设置…"入口。

### E2 托盘健康面板
- 纯函数 `build_health_rows(snapshot)` 生成 `(标签, 值, 级别)` 列表，级别用于着色（正常/注意/错误）。
- controller 新增只读统计：最近一次连接成功时间、连接次数、最近离线原因与时间、收到的 SSE 事件数。
- 面板不发额外请求（崩溃告警数用已有的离线读取逻辑，daemon 摘要用控制器缓存）。

### E3 流式输出
- `api.stream_turn(turn_id, stop)`：`GET /v1/stream/{turn_id}?replay=true`，复用 `parse_sse`。
- 纯类 `TokenAccumulator`：只接收 `token` 事件的 `data.text`，忽略其它类型，保留已到达的文本。
- 快速面板：提交后并行开一个流线程，把 token 经 Qt 信号追加到回复区（纯文本）；turn 结束后用 `setMarkdown` 替换为最终渲染结果。流异常只记录，轮询仍然保证结果出现。
- 注意：`/v1/stream/{turn_id}` 通过 turn 找到所属 session，不需要 `session_id` 参数。

### E4 拖文件到面板
- 新模块 `drop_stage.py`（纯逻辑 + 文件复制，无 Qt）：
  - `stage_paths(paths, base_dir, max_files=20, max_total_bytes=200 MB)` → 返回 `StagedBatch(dir, files, skipped)`；文件夹递归复制受同一限额约束；超限与无法读取的文件进入 `skipped` 并附原因；
  - `build_message(user_text, batch)`：在用户原文前附上临时目录路径与文件清单，提示 agent 从该目录读取；
  - `cleanup_old(base_dir, max_age_hours=24)`：仅删除超龄的批次目录，只动 `mini_agent_drop` 下的子目录。
- 临时目录：Windows `%TEMP%\mini_agent_drop`，其它系统 `tempfile.gettempdir()/mini_agent_drop`。
- 快速面板接受拖放（`dragEnterEvent` / `dropEvent`）：拖入即复制，面板显示"已附加 N 个文件"，发送时携带目录。发送后保留目录直到过期清理。
- 安全边界：拷贝而非引用原文件；不跟随符号链接；不自动执行任何内容，仅告诉 agent 路径。

### E5 目标与定时任务视图
- `api.py` 新增：`goals()`（GET `/v1/goals`）、`cron_jobs()`（GET `/v1/cron/jobs`）、`set_cron_enabled(job_id, enabled)`（PUT `/v1/cron/jobs/{id}`，body `{"enabled": bool}`）。
- 纯函数 `workboard.py`：把 objectives 归一为 `(标题, 状态, 进展摘要)`，把 cron 归一为 `(名称, 启用, 下次运行, 执行阶段)`，并给出排序（运行中优先，其次下次运行时间）。
- 窗口两个页签；cron 行可切换启停，操作后重新拉取。目标在本阶段只读。

### E6 上下文感知（最小版）
- 配置 `context_awareness_enabled`（默认 `false`）。关闭时快速面板不显示相关控件。
- 读取：仅 Windows，`GetForegroundWindow` → 窗口标题 + 进程可执行文件名。纯函数 `classify_app(exe, title)` 归为 browser / editor / terminal / other。**不读取 URL、页面内容、剪贴板或击键。**
- 时机：**面板打开前**读取（面板自己抢焦点之后就读不到原窗口了）；快照只保存在面板内存中，发送时附带一行上下文，用户可在发送前取消勾选。
- 隐私：窗口标题可能含敏感信息，文档中明确说明；不写入日志与 `desktop.json`。

## 4. 测试与验证

- 每阶段新增 `tests/test_desktop_e{N}_*.py`；纯逻辑全部不依赖 Qt，Qt 部分用离屏冒烟。
- 全部桌面端测试回归：`QT_QPA_PLATFORM=offscreen PYTHONPATH=src:apps/mini_agent_desktop pytest tests/test_desktop_*.py`。
- 需 Windows 实机验证：热键热切换、拖放（资源管理器拖入）、流式显示的流畅度、前台窗口读取（各类应用）、临时目录权限。

## 5. 不做的部分（本轮）

- 读取浏览器 URL、页面内容、编辑器文件内容（需要扩展或 UI 自动化，隐私风险高，另起计划）。
- 语音输入（依赖较重，排在本计划之后）。
- 目标的新建、编辑与拖拽排序（本轮只读；写操作需要先确认后端的并发与校验规则）。
- 文件拖入后的自动解析执行（只告诉 agent 路径，由 agent 按现有权限处理）。

## 6. 实施记录

（每阶段完成后在此追加，格式同 `windows_desktop_resident_plan.md`。）

### E1 热键设置（已完成）

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/hotkey.py` | 新增 `validate_hotkey_text()`（纯函数，返回空串或可读原因） |
| `apps/mini_agent_desktop/mini_agent_desktop/settings_dialog.py` | 新增设置窗口：启用复选框、组合键输入、实时校验、保存/取消；保存回调返回 `(ok, 说明)` |
| `apps/mini_agent_desktop/mini_agent_desktop/app.py` | 热键启停抽成 `apply_hotkey()`，启动与设置保存共用；`save_hotkey()` 持久化并气泡告知；热键句柄改放 `hotkey_state`，可替换 |
| `apps/mini_agent_desktop/mini_agent_desktop/tray.py` | 菜单新增“设置…” |
| `tests/test_desktop_e1_hotkey_settings.py` | 13 个用例：校验合法/非法、保存禁用状态、关闭时跳过校验、成功/失败回调、托盘菜单回调 |
| `docs/desktop-client-guide.md` | §4.1 热键说明改为通过设置窗口开启，并说明热切换 |

设计取舍：注册失败时仍保存用户选择（`hotkey_enabled` 与组合键），但窗口与气泡都明确说明热键未生效，避免用户误以为已生效。

未验证（需 Windows 实机）：热键热切换时旧注册是否被及时释放；同一组合键由另一程序占用时的报错文字。

### E2 托盘健康面板（已完成）

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/health.py` | 新增（纯逻辑）：`HealthStats` 计数模型；`build_health_rows()` 生成 (标签, 值, 级别) |
| `apps/mini_agent_desktop/mini_agent_desktop/health_window.py` | 新增：只读窗口，可见时每 2 秒刷新，不发网络请求 |
| `apps/mini_agent_desktop/mini_agent_desktop/controller.py` | 新增 `health` 字段与 `wall` 时钟参数；在连接成功、离线、收到非心跳事件、摘要刷新处埋点 |
| `apps/mini_agent_desktop/mini_agent_desktop/app.py` | `health_snapshot()` 汇总快照；`open_health()` 懒创建窗口 |
| `apps/mini_agent_desktop/mini_agent_desktop/tray.py` | 菜单新增“健康状态…” |
| `tests/test_desktop_e2_health.py` | 6 个用例：统计转换、行生成各分支、控制器埋点、窗口填充 |
| `docs/desktop-client-guide.md` | 新增 §4.2.1 健康状态面板说明 |

设计取舍：面板不主动请求 daemon，避免界面线程阻塞；摘要里的推送开关来自控制器已有的摘要刷新，因此未取到时显示“未知”而不是猜测。

### E3 流式输出（已完成）

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/api.py` | 新增 `stream_turn(turn_id, stop)`：订阅 `/v1/stream/{turn_id}?replay=true` |
| `apps/mini_agent_desktop/mini_agent_desktop/quick.py` | 新增 `TokenAccumulator`（只收 `token` 事件文本）与 `STREAM_END_EVENTS`；`run_task` 新增 `on_turn` 回调，提交后立即通知 turn_id |
| `apps/mini_agent_desktop/mini_agent_desktop/quick_panel.py` | 提交后开流线程，token 经信号增量追加；`_streaming` 标志在结束时关闭，迟到的 token 被忽略；结束后仍以 Markdown 覆盖 |
| `tests/test_desktop_e3_streaming.py` | 7 个用例：累积规则、端点路径与 replay 参数、停止标志、turn 回调、面板最终结果、收尾后忽略迟到 token |
| `docs/desktop-client-guide.md` | §4.1 增加流式输出说明 |

设计取舍：流线程是独立 session 客户端，不与轮询共用连接；流结束由 `turn_done` 或 `run_task` 结束的停止标志共同决定。流中途断开只记录并忽略，轮询保证最终结果。

未验证（需 Windows 实机）：长回复时的渲染流畅度；流断开后的回退是否无感。

### E4 拖文件到面板（已完成）

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/drop_stage.py` | 新增（纯逻辑）：`stage_paths()` 复制文件/文件夹到批次目录，限额、同名去重、跳过符号链接与不存在项；`build_message()` 生成带目录路径的消息；`cleanup_old()` 清理超时批次 |
| `apps/mini_agent_desktop/mini_agent_desktop/quick_panel.py` | 面板接收拖放（子控件不接收，避免文件 URL 被当文字插入）；异步复制；附件标签与“清除附件”；发送时拼接消息并清空附件 |
| `apps/mini_agent_desktop/mini_agent_desktop/app.py` | 启动时清理超过 24 小时的副本 |
| `tests/test_desktop_e4_drop.py` | 12 个用例：复制不改原文件、目录层级、同名不覆盖、数量与大小限额、不存在项、符号链接、消息内容、清理范围、面板发送带附件 |
| `docs/desktop-client-guide.md` | §4.1 增加拖文件说明 |

设计取舍：拷贝而非引用，agent 读到的是副本，不会因为原文件被改动而读到不一致内容；清理只删除本工具创建的批次目录。

未验证（需 Windows 实机）：资源管理器拖入时的 URL 格式（本地路径）；`%TEMP%` 在企业环境下的权限；大文件复制时面板的响应。

### E5 目标与定时任务视图（已完成）

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/api.py` | 新增 `goals()`、`cron_jobs()`、`set_cron_enabled()`（PUT，只带 `enabled`） |
| `apps/mini_agent_desktop/mini_agent_desktop/workboard.py` | 新增（纯逻辑）：目标按状态与标题排序、缺字段兜底；定时任务按“运行中 → 已启用（按下次运行）→ 已停用”排序 |
| `apps/mini_agent_desktop/mini_agent_desktop/workboard_window.py` | 新增：两个页签；加载与启停在线程中完成，结果经信号回主线程 |
| `apps/mini_agent_desktop/mini_agent_desktop/app.py` | `workboard_fetch` / `workboard_toggle` / `open_workboard` |
| `apps/mini_agent_desktop/mini_agent_desktop/tray.py` | 菜单新增“目标与定时任务…” |
| `tests/test_desktop_e5_workboard.py` | 6 个用例：排序与兜底、API 请求形态、窗口渲染与启停 |
| `docs/desktop-client-guide.md` | 新增 §4.2.2 |

设计取舍：目标页本轮只读。目标的状态迁移有后端的校验与并发规则，桌面端直接写入风险较高，留待后续单独评估。

未验证（需 Windows 实机）：真实 daemon 中 objective 的字段内容（进展字段在不同目标上是否齐全）。

### E6 上下文感知（最小版）（已完成）

| 文件 | 改动 |
|---|---|
| `apps/mini_agent_desktop/mini_agent_desktop/context.py` | 新增：`classify_app()`、`make_window()`（截断 120 字、压缩空白）、`format_context()` 纯函数；`read_foreground_window()` 仅 Windows（`GetForegroundWindow` + 进程映像名），其它系统返回 None |
| `apps/mini_agent_desktop/mini_agent_desktop/config.py` | 新增 `context_awareness_enabled`（默认 `false`） |
| `apps/mini_agent_desktop/mini_agent_desktop/quick_panel.py` | `set_context_reader()`；`open_panel()` 在显示面板**之前**读取；复选框默认勾选；发送时附加一行上下文并清空快照；读取异常不阻止打开 |
| `apps/mini_agent_desktop/mini_agent_desktop/app.py` | `toggle_context()` 写回配置并即时切换读取器 |
| `apps/mini_agent_desktop/mini_agent_desktop/tray.py` | 菜单新增可勾选的“快速面板附带当前窗口（隐私）” |
| `tests/test_desktop_e6_context.py` | 19 个用例：分类、标题不影响分类、截断、格式与边界说明、默认关闭、非 Windows 降级、读取发生在显示之前、发送后快照清空、取消勾选不附带、无法读取时禁用、读取异常不影响面板 |
| `docs/desktop-client-guide.md` | 新增 §4.2.3，明确隐私边界 |

设计取舍：读取时机选在“打开面板之前”，因为面板自身获得焦点后前台窗口就变成了它；只读标题与程序名，是能给 agent 提供有用语境、同时又不触碰内容的最小集合。

未验证（需 Windows 实机）：`GetWindowTextW` 对 UWP 与管理员权限窗口的返回；`QueryFullProcessImageNameW` 对提权进程的权限拒绝时的降级（当前会得到空程序名，分类为“应用”）。

### 修复：app.py 启动即 NameError（E 系列集成问题）

- **问题**：E1–E6 集成时，`open_settings` / `open_health` / `open_workboard` 及其依赖的 `apply_hotkey` / `save_hotkey` 定义在 `TrayUi(...)` 构造之后，托盘菜单引用时会 NameError，程序无法启动。单测覆盖的是各模块，未覆盖 `main()` 的装配段，因此未被发现。
- **修复**：把这段定义整体移到 `TrayUi(...)` 之前；清理 pyflakes 报告的 `Resolved` 未导入与 `PySide6` 未使用导入（探测改为 `importlib.util.find_spec`）。
- **防回归**：新增 `tests/test_desktop_app_assembly.py`：(1) pyflakes 检查 app.py 不含未定义名；(2) 离屏真正调用一次 `main()`（仅替换阻塞的 `app.exec()`），热键、弹窗、上下文等配置全部开启，确认装配段执行完成。
- **结果**：桌面端全部测试 242 项通过。
