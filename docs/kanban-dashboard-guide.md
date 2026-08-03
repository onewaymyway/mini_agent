# Kanban 看板使用指南

> 基于 Streamlit 的一体化观测/交互面板 —— `apps/mini_agent_kanban/`

## 简介

`apps/mini_agent_kanban/app.py` 是在 `apps/mini_agent_webdemo`（纯聊天 Web Demo）基础上
扩展出的**多 Tab 综合面板**，一次性提供聊天、会话管理、目标/Cron 看板、产出物浏览、
产出预览（语义化产出物看板）、具身智能自省状态、诊断信息七大功能区，适合日常观测 daemon
运行状态，而不仅仅是聊天。

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
- **动作**：更细粒度的"agent 正在干什么"——空闲 / 等权限确认 / 调用模型中 /
  调用工具中（带工具名），比笼统的 running 更具体
- **模型**：当前实际在用的模型名（故障转移/手动切换模型后会跟着更新）
- **Session**：当前绑定的 session_id
- 当前 Turn
- 自主等级（Autonomous Loop 的当前 tier）
- 距下次 Tick 的时间、Tick 计数、订阅者数量
- **排队中**（新增）：`InputQueue.depth`（即 `StatusResponse.queue_depth`，之前后端一直有
  这个字段，看板此前从未展示过）——用户消息、cron 触发、自主任务提交都走同一条
  InputQueue，agent 正忙时后面的请求只能排队等待；非零时点击展开可看到具体排队列表
  （发起方 `user`/`cron`/`autonomous`、已等待秒数、输入内容预览，数据来自 `/v1/turns`
  里 `state=="queued"` 的条目）
- 待审批权限请求数 / 待回答交互请求数——点击展开后可逐条处理（这两项只统计**当前页面
  绑定的这一个 session**）
- **📥 全局待办中心**（`/v1/inbox`，看板与自主性改进方案 Track A）：跨**所有**活跃
  session 聚合待办——不仅包含权限/交互请求，还包含执行失败的 Objective。解决"后台
  自主任务在别的 session 里卡在权限审批上，但用户停留在当前页面完全看不到"的问题。
  非空时以可展开列表展示，每条待办若关联某个 session，可点击"跳转"按钮直接把当前
  页面切换绑定到那个 session。
- session 存储目录（`<project_root>/.agent/sessions/<session_id>/`），单独一行展示

## 多会话并行（每个看板页面绑定不同 session）

侧边栏"🗂️ 本页面对话 session"下拉框决定**这个浏览器标签页**跟哪个 session 对话：

- 选择某个已有 session，或在"🗂️ 会话管理"Tab 里对某条 session 点
  "📌 本页面绑定到此会话"，都会把 session_id 写入当前页面的 URL
  （`?session_id=xxx`），只影响这一个标签页。
- 用不同的 `?session_id=` 打开多个浏览器标签页/窗口，即可同时和多个 session
  对话，互不干扰——顶栏、对话历史、事件流、发送/中断都只作用于各自绑定的
  session。
- "🗂️ 会话管理"Tab 里的"▶️ 恢复此会话（全局）"按钮是旧行为：它改的是服务端
  全局默认 session，会影响所有**没有**单独绑定 session_id 的客户端（比如
  CLI）。日常在看板里切换对话，建议用"📌 本页面绑定到此会话"，只影响自己
  这个标签页，不会打扰其他正在用看板/CLI 的人。
- 不选任何 session（下拉框留在"(全局默认)"）时行为和旧版本完全一致，请求不带
  session_id，退回服务端全局共享的 bridge。
- 复制当前浏览器地址栏 URL（带着 `session_id=xxx`）发给别人，对方打开后会
  自动绑定到同一个 session——也是"产出预览"Tab 深链接机制的同一个查询参数，
  两者共享同一个 `session_id`，打开一条链接两处效果一起生效。

## 功能 Tab 一览

| Tab | 内容 |
|---|---|
| 💬 对话 | 聊天、历史消息、事件流、发送/中断 |
| 🗂️ 会话管理 | 会话列表、新建 / 恢复 / 删除会话 |
| 📌 目标看板 | Goal / Objective 看板（按状态分列）、新建目标、Cron Job 管理与手动触发、Objective 执行进度 |
| 🔄 工作流 | Workflow 运行面板、看板视图（按 Step 状态分列）、暂停/取消/续跑/审批、出错定位与单步修改重跑、历史执行列表 |
| 📁 产出物 | 浏览 `.agent/` 等目录下产出文件，预览与下载 |
| 🖼️ 产出预览 | 按任务/session 登记的产出物 manifest 语义化展示（图片内联、文档下载），支持深链接直达 |
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
- **周期性 Goal 可见性与操作**（`goal_cron_visibility_and_intervention_improvement_plan.md`
  Track A/B）：Goal 卡片标题下方展示 `🔁 周期性 · 已完成 N 轮` 徽标（未绑定则显示"未设为
  周期性"）；由 cron 触发的子 Objective 标注"⏰ 由 cron 周期触发"。卡片下方"⏰ 周期性设置"
  折叠区可直接绑定/解绑（对应 `/agent goals recur|unrecur`）、"⏭️ 跳过下一轮"（保持
  `recurring=True` 只跳过下一次触发，写入 `progress_notes` 留痕）——此前这三个操作只有
  CLI 入口，看板完全看不出一个 Goal 是不是在周期性运转。
- Cron Job 列表、新增、编辑，以及"立即执行一次"按钮。
- **🩺 自主调度诊断**（`/v1/autonomous/status` 新增字段）：直接回答"为什么加了目标/
  Objective，却没看到 agent 执行"——依次展示 `loop_active`（AutonomousLoop 是否真的
  挂在当前 daemon 上在 tick，最常见的"看起来配了 maintenance 却完全不执行"就是这里
  为 False：只是 autonomy_level 配置值，不代表 tick 真的在跑）、`has_actionable_work`
  （GoalBacklog 里有没有 status=active 的 Objective）、Objective 并发槽位占用、以及
  `ResourceArbiter.diagnose()` 逐条列出的预算/挫败感/用户在场三条门控规则通过情况。
- Objective 执行进度展示：每张 Objective 卡片下方直接展示 ObjectiveExecutor 拆出的
  分步计划与真实状态（而不是需要手动回写的 `progress_notes`）。
  - **状态单向同步**（Track B）：Objective 执行完成/失败/被终止后，看板列会自动挪到
    对应列（新增"✗ 执行失败"" 🚫 已终止"两列），不需要用户手动切换状态；反过来，
    用户在看板上点"🛑 终止"会驱动后台 execution 真正停止、释放并发槽位，不会出现
    "卡片显示已放弃，但后台还在跑"的脱节。
  - **路径互斥（退化版，Track C）**：两个 Objective 的当前 step 若被判定为会碰到
    同一批文件/目录，后提交的一方会显示"与其他 Objective 路径冲突，排队中"，而不是
    并行执行导致互相覆盖；占用方完成/失败/终止后自动重新提交，不需要人工干预。
  - **终止 / 重试 / 插话**（Track D）：每张有执行记录的 Objective 卡片下方提供三个
    按钮——"🛑 终止"（立即停止并释放槽位）、"🔁 重试当前步"（不等超时，随时手动
    触发重新提交）、"💬 插话"（记录一句补充说明，下次提交当前 step 时会附带在
    prompt 里）。
  - **失败重试携带原因**（Track F）：自动重试（超时/工具报错触发）时，下一次提交给
    agent 的 prompt 会附带上一次失败的具体原因，并提示"不要重复同样的做法"，而不是
    原样重发同一句任务描述。
- **🗞️ 每日融合日报 / 💡 主动推荐 / 🧭 决策画像**（`主动推荐与数字分身机制设计方案.md`）：
  三张并排只读卡片，分别展示 `sys:daily_digest`（行为+目标进展融合日报）、
  `sys:next_action_digest`（停滞目标/注意力错配排序推荐）、
  `sys:decision_profile_update`（决策价值模式归纳，默认关闭）三个 cron job 的
  最新产出。卡片本身不触发生成，避免刷新看板页面时意外产生额外 LLM 调用；
  要立即刷新内容，仍需在 CLI 侧执行 `/digest daily`、`/next refresh`、
  `/decision_profile update`，或用 Cron Job 列表的"立即执行一次"按钮触发对应 job。

详见 `docs/autonomous_daemon_design.md`、`docs/goal-mode-guide.md` 了解 Goal/Cron/
Objective 背后的调度机制；`docs/decision-profile-guide.md` 了解决策画像的归纳与
矛盾处理逻辑。

### 🔄 工作流 Tab

对接 `src/mini_agent/workflow/`（`workflow_mechanism_improvement_plan.md` P7 +
`workflow_mechanism_improvement_proposal.md` 最新一轮改进）的完整控制面：

- **运行面板**：下拉选择已保存工作流，自动扫描 YAML 里的 `{param}` 占位符生成
  输入表单；若 YAML 声明 `mode: autonomous`（全自动、不含需要人工介入的
  step），面板会有提示。「🔍 预览执行计划」做零成本 dry-run（并发分批 +
  占位符替换展示，不实际调用任何 Agent）；「⚙️ 运行选项」里可以打开
  **强制全部串行**（本次运行忽略并行分批，调试用，不改 YAML）和
  **要求输入一次性给全**（`require_all_inputs_upfront`，开启后凡是
  `human_input` 步骤没有对应输入，启动前直接报错，不会等运行中途才卡住）。
- **看板视图**：`StepStatus` 归并为 5 栏——未开始 / 进行中 / 已完成 /
  需要关注（`gate_failed` / `failed` / `needs_fix`）/ 等待审批。每张卡片展示
  耗时、评分（有的话）、输出预览；`needs_fix`（结构性/配置错误，重试无效）
  的卡片会额外标注"重跑无效"。
  - 出错的卡片（`failed` / `gate_failed` / `needs_fix`）会展示具体错误原因
    （`error_type` + `error`），并提供**「🛠️ 修改此步骤定义并续跑」**——直接
    在看板上改 `prompt`/`timeout` 等字段并保存（对应 `patch_workflow_step`），
    保存后自动从该 step 触发续跑（`force_rerun_from`），不用回到对话里手写
    JSON patch。
  - `awaiting_approval` 卡片提供「✅ 批准」/「❌ 拒绝」按钮。
  - `done` 卡片可展开「✏️ 编辑此步骤输出并续跑」——人工改写某一步的产出，
    下游 step 按新结果重新执行。
  - 顶部工具条：⏸️ 暂停 / 🛑 取消 / ▶️ 续跑；运行中每 2 秒局部刷新
    （`st.fragment`），跑完自动停止刷新。
- **历史执行列表**：折叠区展示所有历史执行记录，点击可切换到上方详情视图。

### 🖼️ 产出预览 Tab

与 📁 产出物 Tab（按目录遍历文件系统）不同，本 Tab 消费的是**产出物 Manifest**——
一份登记了"这次任务产出了什么"的 JSON 清单（`storage/artifacts.py`），语义更明确，
渲染方式也按文件类型区分（图片直接内联展示、文档给下载链接、代码/文本内联预览）。

- 顶部可按 `session_id` 过滤，也支持直接留空看全部产出（按时间倒序）。
- 每条产出可展开查看其下所有文件，并附带一段可复制的深链接参数
  `?manifest_id=xxx`，拼到看板 URL 后即可直接定位打开该次产出
  （也支持 `?session_id=xxx` 定位到某个 session 的产出列表）。
- Manifest 的产生有两种方式：
  1. **手动登记**：工具/Agent 代码里调用 `storage.artifacts.record_artifact(...)`。
  2. **自动侦测**（默认关闭）：`write_file` / `create_file` / `patch_file(_simple)` /
     `bash` 等工具成功执行后，自动扫描是否生成了文档/图片类产出并登记。
     需要在配置中显式打开 `artifact_auto_detect_enabled: true`（默认
     `false`，因为涉及对 bash 命令/输出做正则扫描 + 额外文件系统访问）。
     详见 `docs/artifacts-dashboard-guide.md`。

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
| `status(session_id=)` | `GET /v1/status` | Agent 运行状态（含 `model`/`session_dir`/`activity`/`activity_detail`） |
| `diagnostics()` | `GET /v1/diagnostics` | 诊断信息 |
| `chat(session_id=)` / `interrupt(session_id=)` | `POST /v1/chat`、`/v1/interrupt` | 发消息 / 中断 |
| `history(session_id=, limit=100, before_seq=)` / `clear_history(session_id=)` | `/v1/history` | 对话历史读取（默认只取最新一页，`before_seq` 翻页取更早的）与清空 |
| `events(session_id=)` | `GET /v1/events` | 拉取事件流 |
| `turns(session_id=)` | `GET /v1/turns` | Turn 列表 |
| `pending_permissions(session_id=)` / `respond_permission()` | `/v1/permissions/*` | 权限审批 |
| `sessions(limit=50, offset=0)` / `session_detail()` / `resume_session()` / `new_session()` / `delete_session()` | `/v1/sessions*` | 会话管理（`offset` 配合 `limit` 做分页） |
| `users()` | `/v1/users` | 多用户列表（多用户模式） |
| `self_status()` / `autonomous_status()` | `/self/status`、`/self/autonomous` | 自省与自主循环状态 |
| `goals()` / `add_goal()` / `update_goal()` | `/v1/goals*` | Goal 看板 |
| `recur_goal()` / `unrecur_goal()` / `skip_goal_next_cycle()` | `POST /v1/goals/{id}/recur\|unrecur\|skip_next_cycle` | 周期性 Goal 绑定 / 解绑 / 跳过下一轮（Track A/B） |
| `cancel_objective()` / `retry_objective()` / `inject_objective_guidance()` | `/v1/objectives/{execution_id}/*` | Objective 执行操作：终止 / 手动重试当前步 / 插话（Track D） |
| `inbox()` | `GET /v1/inbox` | 全局待办中心：跨 session 聚合权限/交互请求 + 失败 Objective（Track A） |
| `cron_jobs()` / `add_cron_job()` / `update_cron_job()` / `run_cron_job_now()` | `/v1/cron*` | Cron Job 管理 |
| `workflows()` / `workflow_yaml()` / `preview_workflow()` | `GET /v1/workflows*`、`POST .../preview` | 工作流列表 / YAML 定义 / dry-run 预览 |
| `run_workflow(force_serial=, require_all_inputs_upfront=)` | `POST /v1/workflows/{name}/run` | 启动执行，支持强制串行 / 要求输入一次性给全两个护栏开关 |
| `patch_workflow_step()` | `POST /v1/workflows/{name}/steps/{step_id}/patch` | 单步编辑工作流定义（不用重贴整份 YAML），落盘后对后续所有执行生效 |
| `workflow_runs()` / `workflow_run_detail()` / `workflow_run_events()` | `GET /v1/workflow_runs*` | 执行记录列表 / 单次详情 / 事件增量拉取 |
| `pause_workflow_run()` / `cancel_workflow_run()` / `resume_workflow_run(force_rerun_from=)` | `POST /v1/workflow_runs/{id}/{pause\|cancel\|resume}` | 暂停 / 取消 / 续跑（`force_rerun_from` 配合单步编辑做定点重跑） |
| `approve_workflow_step()` / `reject_workflow_step()` / `provide_workflow_input()` | `POST /v1/workflow_runs/{id}/{approve\|reject\|input}` | 审批门 / 人工输入 |
| `override_workflow_step_output()` | `POST /v1/workflow_runs/{id}/steps/{step_id}/override` | 人工改写已完成 step 的输出 |
| `fs_list()` / `fs_read()` / `fs_download_url()` | `/v1/fs/*` | 产出物浏览与下载 |
| `list_artifacts()` / `get_artifact()` / `artifact_file_url()` | `/v1/artifacts*` | 产出物 Manifest 列表、详情、文件预览/下载 |
| `daily_digest()` | `GET /v1/digest/daily` | 每日融合日报（只读，不触发生成） |
| `next_actions()` | `GET /v1/next_actions` | 主动推荐候选（只读，不触发重新计算） |
| `decision_profile()` | `GET /v1/decision_profile` | 决策画像 Markdown + 结构化模式列表（只读） |

上表标了 `session_id=` 的方法都新增了可选的 `session_id` 参数（默认 `None`，
不传时行为与旧版本完全一致）：传了就会作为 `?session_id=` 查询参数附加到请求上，
后端 `_bridge()` 在单 token 模式下会优先用它解析出对应 session 的
`AgentBridge`，从而实现"看板页面按 session 隔离"（见上面"多会话并行"一节）。
`chat()` 例外——它的 `session_id` 是放进 POST body（对应
`ChatRequest.session_id` 字段），不是查询参数。

## 使用场景

1. **日常巡检**：一次性查看多用户会话池、待处理目标、Cron 运行情况和最近产出物，
   无需分别登录 CLI 和 Web Demo。
2. **自主 daemon 观测**：`Stage 9` 自主循环运行期间，通过目标看板和自我状态 Tab
   观察它自己创建/推进的目标，而不打断其运行。
3. **远程排障**：结合诊断 Tab 的 `/diagnostics` 原始信息定位问题，无需 SSH 到服务器
   直接看日志文件。

## 大数据量下的分页显示

对话历史、事件流、session 列表三类数据在量特别大时都做了分页/增量处理，
避免"全量拉取再前端截断"带来的性能问题（设计与动机详见
`next_doc/看板大数据量分页显示改进计划.md`）：

- **对话历史**：`_render_chat_messages_body` 默认只拉最新一页
  （`limit=100`），历史更长时对话框顶部会出现"⬆️ 加载更早消息"按钮，
  点击后按 100 条为增量继续往前加载；后端 `GET /v1/history` 对应新增了
  `limit`/`before_seq` 分页参数，响应里新增 `total`/`has_more` 字段。
- **事件流**：右侧事件面板和"并排对比"面板都改为用 `since_id` 做增量拉取
  （`_fetch_events_incremental`），本地在 `st.session_state` 里维护一份
  滚动窗口缓存（默认最近 300/100 条），不再每次 2-3 秒的自动刷新都重新
  拉一遍"最近 N 条"、重复渲染已经看过的部分。清空历史、切换全局当前
  session 时会同步重置这份本地缓存。
- **Session 列表**：`render_sessions_tab` 改为标准 `offset`/`limit` 分页
  （每页 50 条），底部有"上一页 / 下一页"翻页控件和"第 X / Y 页"页码提示；
  后端 `GET /v1/sessions` 新增 `offset` 参数，`SessionManager` 新增
  `list_sessions_page()` 方法返回分页前的总数。

## 后续可扩展方向（未实现）

- SSE 真流式渲染（当前对话为轮询式刷新，简单可靠但非逐 token 流式）
- 页面内多路并行对话（当前"多会话"是"一个页面绑一个 session，开多个页面
  并行"；同一个页面内同时铺开多个对话面板还未做，需要把 `render_chat_tab`
  拆成可重复实例化的组件）
- Ensemble 多候选结果对比展示
- 进化流水线（Skill 提案 / git worktree diff）可视化
- 权限历史与安全网风险等级（T0-T3）统计图表
- 历史分页目前按消息条数切页，长会话下按"轮次"分页（配合 `/v1/turns`）会
  更符合用户心智模型，留待后续验证

## 相关文件

- `apps/mini_agent_kanban/app.py` — 看板主程序（8 个 Tab）
- `apps/mini_agent_kanban/client.py` — `AgentClient` HTTP 封装
- `apps/mini_agent_kanban/README.md` — 应用自带的简要说明
- `docs/http-api-guide.md` — HTTP API 完整参考
- `docs/artifacts-dashboard-guide.md` — 产出物 Manifest 设计与自动侦测开关详解
- `docs/web-demo-guide.md` — 姊妹应用（纯聊天 Web Demo）
- `docs/multi-user-guide.md`、`docs/autonomous_daemon_design.md`、
  `docs/goal-mode-guide.md`、`docs/embodied-agent-guide.md` — 看板中各功能区背后的机制
- `next_doc/看板大数据量分页显示改进计划.md` — 本次分页改造的设计文档

---

*最后更新：2026-07-24*
