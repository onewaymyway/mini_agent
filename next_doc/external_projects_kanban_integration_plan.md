# 外部项目管理接入 Streamlit 看板（第一期：只读监控 + backlog + review 预览）

> **这篇文档管什么**：把目前只能通过 `mini-agent projects ...` 命令行
> 操作的外部项目管理能力（`external_projects_workspace_plan.md`/
> `stock_watch_continuous_improvement_plan.md` 已经落地的注册表、
> 执行账本、改进积压账本、review session 任务模板），接入
> `apps/mini_agent_kanban/app.py` 看板，做成新的一个 tab。
>
> **不管什么**：不改动任何底层机制本身（注册表格式、backlog 账本
> schema、review 任务模板拼装逻辑一律复用现成实现，本文档只是给它们
> 接一层 HTTP 路由 + Streamlit UI）；不实现 propose_fix/land 的
> 看板化（见第4节"分期"）。

## 1. 起因

前两份文档（`external_projects_workspace_plan.md`、
`stock_watch_continuous_improvement_plan.md`）把外部项目管理机制从
"设计"做到了"能跑"，但入口一直停留在命令行（`mini-agent projects
list/status/run/backlog/review`）和 agent 对话里的工具调用
（`list_projects`/`inspect_project`/`trigger_run`/`list_backlog`/
`append_backlog_item`/`propose_fix`）。看板（`apps/mini_agent_kanban/
app.py`）已经是日常管理 Goal/Cron/进化提案的主要入口，外部项目管理
留在命令行侧，导致：

- 日常查看 stock_watch 是否健康、backlog 里堆了什么问题，得单独开一个
  终端跑命令，而不是像其它子系统一样在看板里一眼看到。
- 已经有一个只读聚合状态接口 `GET /v1/self/external_projects`
  （`api/routes.py`，`external_projects_workspace_plan.md` 阶段4），
  但看板前端从未消费它——**这是本次要补的最基础的一块**。
- backlog/review 这两个"阶段5/6"新增的机制，目前只有 CLI
  （`mini-agent projects backlog/review`）能访问，看板完全看不到。

## 2. 复用与新增的边界

严格复用现成后端逻辑，本次只新增"接线"，不重新实现任何判断：

| 能力 | 现成实现（不改动） | 本次新增 |
|---|---|---|
| 项目列表 + 健康聚合 | `external_projects/status.py::aggregate_status()`，已有路由 `GET /v1/self/external_projects` | 无需新路由，看板直接消费现成接口 |
| 注册新项目 | `ExternalProjectRegistry.register()` | 新路由 `POST /v1/external_projects/register` |
| 手动触发 entrypoint | `scheduler.py::trigger_run()` | 新路由 `POST /v1/external_projects/{name}/trigger_run` |
| 执行账本 | `ledger.py::read_ledger()` | 新路由 `GET /v1/external_projects/{name}/ledger` |
| 改进积压账本 | `backlog.py::read_backlog()`/`append_item()` | 新路由 `GET`/`POST /v1/external_projects/{name}/backlog` |
| review 任务模板预览 | `review.py::build_review_task_template_for()` | 新路由 `GET /v1/external_projects/{name}/review` |

路由风格对齐仓库已有约定（参照 `/evolution/proposals*`/`/cron/jobs*`
这一批既有端点）：owner-only（`_require_owner()`）、失败时返回结构化
错误而不是让整个请求 500、路径参数用项目 `name` 而不是内部 id。

## 3. 页面设计

新增 tab `🗂️ 外部项目`（`render_external_projects_tab()`，代码位置
与其它 tab 一致，紧邻 `render_evolution_proposals_tab` 之后），内部
再分两个子区域（不用嵌套 `st.tabs`，用 `st.expander`/分割线，因为
看板顶层 tab 已经很多，参照 `render_growth_tab` 内部用 expander 组织
子模块的既有写法）：

1. **项目总览**：卡片列表，每张卡片一个已注册项目——名称、启用状态、
   健康状态徽标（🟢健康/🔴不健康/⚪未知，与 `_PROPOSAL_RISK_LABEL`
   同样的徽标风格）、最近一次执行摘要。卡片内可展开看最近5条执行
   记录（复用 `aggregate_status()` 已返回的 `recent_runs`）。
2. **每个项目卡片内**再嵌 3 个小按钮/expander：
   - 「▶️ 手动触发」：下拉选 entrypoint + 按钮，调
     `trigger_run`，成功/失败直接在卡片内提示（不做二次确认——手动
     跑一次 entrypoint 本身没有破坏性，和命令行 `projects run` 一样
     的风险等级）。
   - 「📋 改进积压」expander：列出该项目的 backlog（`status` 筛选下拉：
     open/proposed/landed/dismissed/全部），一个文本框 + 按钮可以新增
     一条 `source="user_feedback"` 的待办——这是本次最直接补上"用户
     在看板里随口反馈一句，也能被记下来"这个诉求的地方。
   - 「🔍 Review 预览」expander：按钮"生成本周 review 任务模板"，
     调用 `review` 路由，把返回的任务模板文本用 `st.code` 展示，并给
     一个"复制到对话框"的按钮（写入 `st.session_state` 里对话输入框
     绑定的 key，与看板其它"发送到对话"入口的既有模式一致，具体参照
     `render_topbar` 里"🔍 查看并控制"按钮跳转+回填输入框的写法）——
     **这一步只是把模板文本送进对话输入框，不自动发送**，真正发起
     review session 仍然是用户自己点发送，agent 仍然要走一遍正常的
     工具调用+权限确认流程，不因为多了这个按钮就绕过任何既有的审批
     机制。
3. **注册新项目**：总览区顶部一个小表单（路径 + 可选名称 + 「注册」
   按钮），调用新增的 `register` 路由。

## 4. 分期

**第一期（本文档实现范围）**：项目总览、手动触发、backlog 查看/新增、
review 模板预览、注册新项目。全部是"只读展示 + 低风险写操作"（触发
执行、写一条待办、注册项目——都不涉及改代码或合并分支）。

**第二期（明确不在本次范围）**：`propose_fix` 生成 enhancement/fix
提案分支 + diff 查看 + `land_maintenance_fix` 落地按钮的看板化。这块
延后不是技术难度问题（`render_evolution_proposals_tab` 已经有现成的
"diff 展示 + 风险分级 + 二次确认合并"UI 模式，抄一份接上
`propose_fix`/`land_maintenance_fix` 的后端并不难），而是刻意的风险
控制：`stock_watch_continuous_improvement_plan.md` 第5节反复强调
"enhancement 类改动的最终落地永远保留给人工"，第一期先把"发现问题→
记录→触发 review 对话"这条低风险链路接上看板，落地按钮这种"一键就
可能合并代码"的操作留到确认了"团队真的会在看板上而不是命令行上做这类
判断"之后再做，避免为了"看起来功能完整"而在还没想清楚二次确认交互
之前就把高风险按钮摆到界面上。

## 5. 具体改造计划

> 约定：每完成一项，回来把对应复选框打勾，并在文末"变更记录"补一行。

### 阶段 1：后端 — 新增 HTTP 路由（`api/routes.py`）✅已完成
- [x] `POST /v1/external_projects/register`：body `{path, name?,
      validate?}`，包装 `ExternalProjectRegistry.register()`。
- [x] `POST /v1/external_projects/{name}/trigger_run`：body
      `{entrypoint}`，包装 `scheduler.trigger_run()`。
- [x] `GET /v1/external_projects/{name}/ledger`：query `limit`，包装
      `ledger.read_ledger()`。
- [x] `GET /v1/external_projects/{name}/backlog`：query `status?`，
      包装 `backlog.read_backlog()`。
- [x] `POST /v1/external_projects/{name}/backlog`：body
      `{source, summary, evidence_ref?}`，包装 `backlog.append_item()`
      （`source` 固定只允许 `user_feedback`——看板手填的这条路径语义
      上就是"人工反馈"，`outcome_review`/`health_trend` 应该继续由
      entrypoint 自动写入，不应该在 UI 上开放让人手填成看起来像是
      系统自动发现的）。
- [x] `GET /v1/external_projects/{name}/review`：包装
      `review.build_review_task_template_for()`，`review.enabled=
      false` 时不报错，返回模板文本 + `enabled: false` 字段，UI 侧
      据此提示"未开启定期 review，但仍可手动预览"。
- [x] 全部 owner-only（`_require_owner()`），全部遵循"目标项目不存在/
      manifest 解析失败"时返回结构化错误而不是 500 的既有约定（用
      `_external_project_error_status()` 这个小启发式，把
      `ExternalProjectRegistryError` 的信息里含"未注册"的映射成 404，
      其余映射成 400）。

### 阶段 2：`AgentClient` 新增对应方法（`apps/mini_agent_kanban/
client.py`）✅已完成
- [x] `external_projects_status()` / `register_external_project()` /
      `trigger_external_project_run()` / `external_project_ledger()` /
      `external_project_backlog()` / `append_external_project_backlog()`
      / `external_project_review()`，风格对齐既有的
      `evolution_proposals()`/`merge_evolution_proposal()` 一组方法。

### 阶段 3：看板新 tab（`apps/mini_agent_kanban/app.py`）✅已完成
- [x] `render_external_projects_tab()`：总览卡片列表 + 健康徽标。
- [x] 卡片内「手动触发」「改进积压」「Review 预览」三个子区域。
- [x] 顶部「注册新项目」表单。
- [x] 在 `st.tabs([...])` 列表与对应 `with tabs[i]:` 分支里插入这个
      新 tab（放在"🧬 进化提案"之后、"⏰ Cron 任务"之前，与后端管理类
      tab 归在一组）。附带实现："Review 预览"里「📋 复制到对话框」
      按钮通过新增的 `chat_prefill_text` 会话状态 + 既有的
      `_pending_tab_switch`/`_inject_tab_switch_script` 跳转机制，把
      模板文本写进对话输入框并切到"💬 对话" tab——只是预填文本，不
      自动发送，真正发起 review session 仍由用户自己点发送。

### 阶段 4：端到端验证
- [x] 后端路由单元测试（`tests/test_api_external_projects_routes.py`），
      覆盖：注册（成功/缺路径/重复注册/manifest 不合法）、触发执行
      （成功/项目未注册/缺 entrypoint）、账本（空/项目未注册/触发后
      能读到）、backlog（读空/缺 summary/写入后 source 被强制改写为
      user_feedback+按状态过滤）、review 预览（成功返回模板+enabled
      标记/项目未注册）共 15 个用例，全部通过；额外跑了原有
      `test_external_projects*.py`（64 个用例）确认无回归。
- [x] 静态验证：`app.py`/`client.py`/`routes.py` 三个文件的 `ast.parse`
      + `py_compile` 编译检查通过；对 `st.tabs([...])` 标签列表与
      `with tabs[i]:` 分支索引做了脚本化交叉核对，19 个 tab 标签与
      18 个 `render_*_tab(client)` 调用（索引 1-18，索引 0 是对话
      tab，单独处理）一一对应，无错位。
- [ ] 手动过一遍看板 UI（需要真实拉起 daemon + streamlit，当前环境
      无法交互式验证）：把 stock_watch 注册进去、看健康状态卡片、
      手动触发一次 entrypoint、加一条 backlog、预览 review 模板、
      点"复制到对话框"确认文本正确写入输入框——**这一项留给使用者
      在自己的开发环境里跑一遍确认**，本文档后续如发现 UI 层的问题
      会在"变更记录"里补充修复记录。

## 6. 变更记录

- 2026-08-26：文档创建，设计确认（第1-4节）。阶段1-4待开始。
- 2026-08-26：完成阶段1（后端 6 个新路由）、阶段2（AgentClient 7 个
  新方法）、阶段3（看板新 tab「🗂️ 外部项目」+ 对话框预填联动机制）、
  阶段4的自动化部分（15 个新单元测试 + 64 个既有测试回归通过 + 静态
  语法/结构校验）。第一期（只读监控 + backlog + review 预览 + 手动
  触发 + 注册项目）后端与前端代码均已完成，仅剩人工过一遍真实 UI
  这一项验收留给使用者自行确认。第二期（`propose_fix`/
  `land_maintenance_fix` 看板化）仍按第4节约定暂不实施。
- 2026-08-26：附带修复一个与本文档功能无关、但同样发生在 `app.py`
  里的既有 bug：使用者实测时触发了「🌱 成长顾问」tab「正在自主推进」
  列表（`_render_growth_pursuits`）的 `StreamlitDuplicateElementKey`
  崩溃——后端 `growth_pursuits()` 返回了重复 `goal_id` 的记录，而该
  区块的按钮 `key` 只用 `goal_id` 拼出，没有区分同一 `goal_id` 出现
  第几次。给 `active`/`paused` 两个循环的按钮 key 各加一个序号后缀
  （`_{idx}`）作为最小兜底，让 UI 不再因为这类重复数据崩溃；没有去
  排查 `growth_pursuits()` 为什么会返回重复项——那是数据层问题，不
  属于本文档范围，如需彻底修复应另开一份文档追踪。
- 2026-08-27：完成阶段6（entrypoint 参数化触发）——使用者反馈"有些
  entrypoint 需要传参数，看板没地方传"。`project.yaml` 的 entrypoint
  新增可选 `params` 声明，manifest/scheduler/status/路由/client/看板
  UI 全链路接线，`stock_analysis` 补上 `code`/`name` 参数声明作为落地
  案例；新增 16 个测试用例，六个外部项目相关测试文件共 110 个用例
  全部通过。
- 2026-08-27：使用者实测反馈"填参数的时候看板会卡一下"——根因是裸
  `st.text_input` 每次失焦/回车触发整页重跑，连带重新请求
  `GET /v1/self/external_projects`。修复：参数输入框 + 触发按钮包进
  `st.form`，改用 `st.form_submit_button` 提交，输入过程中不再重跑，
  只有点「▶️ 触发」才发请求。纯前端交互修复，未涉及后端/测试改动。
- 2026-08-27：`next_doc/stock_watch_pool_state_tracking_and_kanban_
  plan.md` 阶段4在本文档「🗂️ 外部项目」tab 基础上追加了一块
  stock_watch 专属的「📊 候选池状态跟踪」折叠面板（状态列视图/变更
  状态表单/信号溯源/回溯统计），新增路由 `GET /v1/external_projects/
  {name}/pool_tracking`；只对产出了 `data/pool_tracking_latest.json`
  的项目显示，不影响其它外部项目的现有行为，详细设计见该文档第4节。
- 2026-08-27：上一条记录的"候选池状态跟踪"面板被识别为一次性、
  stock_watch 专属的实现，不满足"任何外部项目都能声明接入"的通用性
  诉求。已在
  `next_doc/external_projects_generic_kanban_view_refactor_plan.md`
  里设计了通用替代方案（`project.yaml` 新增 `dashboard.kanban_view`
  声明 + 通用路由 `kanban_data` + 通用渲染函数），本文档描述的
  `pool_tracking` 专属路由/面板将在该文档实施完成后被替换，具体
  进度以该文档为准。本文档记录的其它通用机制（项目卡片/手动触发/
  backlog/review）不受影响。

### 阶段 5：手动触发改为按钮列表（使用者反馈驱动）

- [x] 「▶️ 手动触发」原来要求用户手填 entrypoint key（容易记错/写错，
      也没法知道这个项目到底有哪些 entrypoint），改成直接把
      `project.yaml` 里声明的 entrypoints 全部列出来，一个 entrypoint
      一个按钮，点了就是那个 key，不再有手填环节。
- [x] 后端：`external_projects/status.py` 的 `aggregate_status()`
      新增 `entrypoints` 字段（`[{key, cmd, schedule}]`），复用已加载
      的 `manifest.entrypoints`，manifest 解析失败时该字段为空列表
      （`manifest_error` 已经说明原因，不重复报错）。`GET /v1/self/
      external_projects` 不用改代码，直接透传新字段。
- [x] 前端：`app.py` 的「▶️ 手动触发」expander 改成遍历
      `proj["entrypoints"]`，每条渲染一行——`key`（含 schedule，如
      有）+ 命令预览 + 「▶️ 触发」按钮（key 按 `name_entrypointkey`
      拼，不会跟别的项目撞），点击直接调用既有的
      `trigger_external_project_run(name, ep_key)`，没有 entrypoints
      时给出提示文案而不是空白一片。
- [x] 测试：`test_external_projects_ledger_and_status.py` 新增 2 个
      用例（`entrypoints` 字段内容正确性 + manifest 损坏时为空列表），
      `test_api_external_projects_routes.py` 新增 1 个用例（HTTP 层
      能拿到 entrypoints）。三个相关测试文件共 81 个用例全部通过，
      无回归。
- [x] 文档：`docs/kanban-dashboard-guide.md`「🗂️ 外部项目 Tab」一节
      同步更新触发方式的描述。

### 阶段 6：entrypoint 参数化触发（使用者反馈驱动）

- [x] 起因：`stock_analysis` 等 entrypoint 依赖位置参数（`sys.argv[1:]`，
      如股票代码），阶段5把"手动触发"做成按钮列表后反而更没法传参了——
      看板上没有任何地方能填这些值，之前只能回退到命令行
      `mini-agent projects run stock_watch stock_analysis 600519`。
- [x] `manifest.py`：`project.yaml` 的 `entrypoints.<key>` 新增可选
      `params` 列表，每项 `{name, required?, default?, help?}`；新增
      `ParamSpec`、`EntrypointParamError`、
      `build_cmd_with_params(entrypoint, values)`——按声明顺序把值拼成
      位置参数（`shlex.quote()` 转义）追加在 `cmd` 后面，缺必填/传了
      未声明的参数名都在这一步直接报错，不执行任何子进程。未声明
      `params` 的 entrypoint 完全不受影响（忽略传入的 values，原样
      执行 `cmd`，向后兼容阶段1-5的既有项目）。
- [x] `scheduler.py`：`_run_entrypoint()`/`trigger_run()` 新增
      `params: dict | None` 参数，内部改用 `build_cmd_with_params()`
      算出最终命令行再 `subprocess.run()`。
- [x] `status.py`：`aggregate_status()` 的 `entrypoints` 列表里每一项
      新增 `params` 字段（完整 schema：name/required/default/help），
      供看板据此渲染输入框，不用用户去猜 cmd 后面该传什么。
- [x] 后端路由：`POST /v1/external_projects/{name}/trigger_run` 的
      body 新增可选 `params: {name: value}` 字段；`EntrypointParamError`
      映射成 400（而不是 500 或让命令带空参数跑起来）。
- [x] `AgentClient.trigger_external_project_run()` 新增 `params` 可选
      参数，透传给后端。
- [x] 看板 `app.py`「▶️ 手动触发」：每个 entrypoint 按声明的 `params`
      逐个渲染文本输入框（必填/可选标注 + help 文案），点「▶️ 触发」
      前先在前端做一次"必填项是否为空"的粗校验（避免明知会失败还发
      请求），真正的参数合法性判断仍然全部在后端
      `build_cmd_with_params()`，前端不重复实现判断逻辑。参数输入框
      + 触发按钮包在一个 `st.form` 里，不是裸的 `st.text_input` +
      `st.button`——见下方"追加修复"，这是使用者实测后反馈才补上的。
- [x] `external_projects/stock_watch/project.yaml`：给 `stock_analysis`
      补上 `params`（`code` 必填 + `name` 可选）作为落地验证案例——这
      也是本次改动能直接生效的第一个真实受益 entrypoint。
- [x] 测试：`tests/test_external_projects.py` 新增 `params` 解析/
      `build_cmd_with_params()`（顺序拼接+转义/无声明时忽略传参/缺
      必填/未声明参数名）/`trigger_run()` 端到端共 9 个用例；
      `tests/test_external_projects_ledger_and_status.py` 新增 2 个
      （`aggregate_status()` 的 `params` 字段内容 + 未声明时为空列表）；
      `tests/test_api_external_projects_routes.py` 新增 5 个（HTTP 层
      params schema 透传/带参触发成功/缺必填 400/未声明参数名 400/
      params 非对象类型 400）。六个外部项目相关测试文件共 110 个用例
      全部通过，无回归。
- [x] 文档：`docs/kanban-dashboard-guide.md`「🗂️ 外部项目 Tab」一节
      补充参数输入框的说明。
- [x] **追加修复（使用者实测反馈）**：原实现是裸的 `st.text_input` +
      `st.button`，而 Streamlit 的 `st.text_input` 每次失焦/回车都会
      触发整个脚本重跑——包括 `render_external_projects_tab()` 顶部的
      `client.external_projects_status()` 网络请求，导致"填参数的时候
      看板卡一下"。修复：把每个 entrypoint 的参数输入框 + 「▶️ 触发」
      按钮一起包进 `st.form(key=f"ext_trigger_form_{name}_{ep_key}")`，
      表单内控件变化不再触发重跑，只有点击
      `st.form_submit_button("▶️ 触发")` 提交表单时才真正发一次
      `trigger_run` 请求、才需要刷新——符合"只有真正点触发才需要提交
      请求刷新"的预期。没有声明 `params` 的 entrypoint 也套上同一个
      `st.form`（此时表单里只有一个提交按钮），保持所有 entrypoint
      行为一致，不用按"有没有参数"分两套代码路径。

- 2026-08-27：本计划里"🗂️ 外部项目"tab 的通用框架（项目卡片、健康
  徽标、执行账本、手动触发+参数表单、backlog、review 预览）保持不变。
  另有一块由 `stock_watch_pool_state_tracking_and_kanban_plan.md`
  阶段4引入、原本 stock_watch 专属的"状态跟踪"面板，已被
  `next_doc/external_projects_generic_kanban_view_refactor_plan.md`
  重构为通用的「📊 状态看板」机制（阶段 A-D 全部完成）：任何外部
  项目通过 `project.yaml` 的 `dashboard.kanban_view` 声明即可接入，
  不再是 stock_watch 专属代码。详见该文档。

- 2026-08-27：「▶️ 手动触发」的响应结构新增 `detail` 字段（失败时子
  进程 stdout/stderr 尾部），账本时间戳改为本地时间。属于
  `external_projects_workspace_plan.md` 阶段 4 账本 schema 的增强，
  不涉及本文档已完成阶段的返工，详见该文档"变更记录"2026-08-27 条目。

- 2026-08-27：stock_watch 新增 `tools/fetch_iwencai_cookie.py`（交互式
  获取问财登录令牌，见 `PROJECT.md`"问财登录令牌"一节）后，同时新增
  `entrypoints/fetch_iwencai_cookie.py` 把它接进看板——是阶段 6
  `params` 机制继 `stock_analysis`（`code`/`name`）之后第二个真实落地
  案例，且暴露了一种新情况：`tools/` 下的脚本用 argparse 定义**选项型**
  参数（`--port`/`--spawn`/`--timeout`），跟 `params` 机制"按声明顺序拼
  成**位置参数**"的既有约定对不上，所以没有直接把 `tools/` 脚本注册进
  `project.yaml`，而是在 `entrypoints/` 下加一层薄包装做参数翻译 +
  接入 `run_entrypoint()` 账本。这个 entrypoint 还依赖能弹出真实浏览器
  窗口的桌面环境，是目前唯一"看板手动触发在无 GUI 服务器上必然失败"的
  entrypoint（已在 `PROJECT.md`"已知限制"一节记录）；不涉及本文档
  `params` 机制本身的改动，仅作为使用案例补充记录。

- 2026-08-27：**使用者实测反馈的 bug**——上面 `fetch_iwencai_cookie`
  接进看板后，使用者反馈“`spawn` 填了 `true`，实际执行还是提示未检测到
  调试端口，像是没生效”。根因定位在 `build_cmd_with_params()` 既有的
  位置参数语义本身：三个参数只标了 `required: false`、没配
  `default`，使用者把排在前面的 `port` 留空时，函数在遇到“没填值也没有
  `default`”的可选参数处直接 `break`，**不再追加它后面的任何参数**——
  `spawn` 虽然填了值，但因为排在被跳过的 `port` 后面，整个被丢弃，
  实际执行的命令里根本没有 `--spawn`。这不是新代码的 bug，是 `params`
  机制阶段 6 就有的既有行为（本文档阶段 6 一节已经写明“跳过中间一个会
  让后面的参数错位……应该在 `project.yaml` 里把该参数放在参数列表
  最后，或者改用 `default` 兜底”），只是 `fetch_iwencai_cookie` 声明时
  没有照着这条建议给三个参数都配 `default`。修复：`project.yaml` 里给
  `port`/`spawn`/`timeout` 都补上 `default`（`"9222"`/`"0"`/`"120"`），
  保证不管使用者填了哪几个、留空了哪几个，都会用 `default` 补齐、按
  声明顺序完整拼出三个位置参数，不会再被“前面留空”连累丢弃。同时给
  `entrypoints/fetch_iwencai_cookie.py` 加了一行 stderr 输出，把“看板
  实际传来的位置参数”和“翻译后真正调用 `tools` 脚本的完整命令”都打
  出来，方便下次类似问题直接对照账本 `detail` 字段排查，不用靠猜。
  **给这套机制补的经验**：任何 entrypoint 声明多个 `required: false`
  的 `params` 时，只要它们之间没有强制“必须一起留空”的依赖关系，都应该
  照 §阶段6 的建议给每个都配 `default`，否则“部分留空”会有静默丢参的
  风险，且现象具有误导性（看起来像是传参格式错了，实际是位置参数被
  整体跳过）。

- 2026-08-27：**第二个使用者实测反馈的 bug（Chrome 侧，非看板/params
  机制问题）**——上面两条 `fetch_iwencai_cookie` 记录修好参数传递之后，
  实测又在建立 CDP WebSocket 会话这一步报错：
  `websocket._exceptions.WebSocketBadStatusException: Handshake status
  403 Forbidden ... Rejected an incoming WebSocket connection ...
  Use the command line flag --remote-allow-origins=... to allow
  connections`。根因：较新版本 Chrome（约 111+）出于安全加固，默认会
  校验 CDP WebSocket 握手的 Origin 头，拒绝不在白名单内的连接来源
  （防止恶意网页直接连本机调试端口），这跟 `mini_agent` 仓库的
  `.claude/skills/browser-cdp` 机制面对的是同一个 Chrome 版本变化，
  与本文档"看板→trigger_run→entrypoint"这条链路本身无关。修复落在
  `tools/fetch_iwencai_cookie.py`：`--spawn` 路径下 `_spawn_chrome()`
  启动 Chrome 时自动加上 `--remote-allow-origins=*`；手动启动 Chrome
  的默认路径（不加 `--spawn`）在模块 docstring 和运行期错误提示里都
  补充了这个 flag 的说明，缺了它会在建立 WebSocket 会话时稳定失败。
  同时给 `CDPSession(...)` 建立失败这一步加了针对 403 的对症中文提示
  （之前是裸 traceback），看板「▶️ 手动触发」失败时账本 `detail` 字段
  会直接带上这段更友好的诊断信息，不用再对照 Python 异常堆栈猜原因。

- 2026-08-27：**第三个使用者实测反馈的 bug（判断逻辑本身，非环境/参数
  问题）**——上面两条修好之后，使用者反馈"『fetch_iwencai_cookie』执行
  完成，returncode=0，但实际有问题，打开调试浏览器之后，根本没有等待
  用户登录，就关闭了，然后获取了没用的 cookie"。根因定位在轮询判断
  逻辑本身：`stock_watch/data_sources.py`"问财 hexin-v 令牌"一节说明过
  `v` cookie 是页面加载时那段混淆 JS **无条件**算出来的，跟有没有
  登录/过验证无关；旧实现把"cookie 存在"当成"用户已完成登录/验证"的
  信号，逐秒轮询、第一个 tick（≤1 秒，用户还没来得及做任何操作）就
  命中退出，写进配置文件的自然是登录前那个服务端不认的临时值——这不是
  某次网络抖动或环境差异，是判断逻辑从设计上就是错的，只要真实网络
  连通就必现。修复落在 `tools/fetch_iwencai_cookie.py`：改成两条路径
  （详见该文件模块 docstring"2026-08-27 再次追加"一节）——交互式终端
  下先记一次登录前基线值，然后用 `input()` **真正阻塞**等用户在浏览器
  里完成登录/验证后回终端按回车确认，确认后才读取 cookie，且如果读到
  的值跟基线一样会打印警告提示大概率还没登录成功；非交互式场景（看板
  「▶️ 手动触发」的子进程 stdin 通常不是真正终端，`input()` 用不了）
  退化成轮询等待令牌值**相对基线发生变化**，而不是"存在就算数"。
  `entrypoints/fetch_iwencai_cookie.py` 和 `PROJECT.md`"问财登录令牌"/
  "已知限制"两节同步更新，说明了看板触发走的是非交互式（较不可靠）
  路径，如果总是超时或拿到没用的令牌，建议改成在有桌面环境的终端里
  直接跑 `tools/fetch_iwencai_cookie.py`，用回车确认那条更可靠的路径。
  **给这套机制补的经验**：给"人工完成某个前置动作后，脚本再继续"设计
  自动化时，优先找"动作完成后才会出现/才会变化"的信号（这里是"令牌
  值变化"，更严格的话应该是"用户主动确认"），不要用"某个值存在"这种
  从页面一加载就恒真的条件当完成信号——尤其是当那个值本身就是无条件
  生成的（如本例的混淆 JS）时，"存在"和"有效"是两回事。

- 2026-08-28：**使用者实测反馈两个性能/稳定性问题——「切到外部项目 tab
  总是卡很久才刷新」+「看板手动触发有导致 daemon 卡死的风险」**。排查
  定位到同一类根因：`GET /v1/self/external_projects`
  （`external_projects/status.py::aggregate_status()`）和
  `POST /external_projects/{name}/trigger_run`
  （`external_projects/scheduler.py::trigger_run()`）内部分别是同步
  阻塞的 `subprocess.run(shell=True, timeout=...)`——前者是每个声明了
  `health_check` 的项目探测一次（stock_watch 的 `health.py` 要 `import
  akshare/pandas`，单次冷启动就有明显耗时），后者是真正执行 entrypoint
  （`project.yaml` 里 `timeout_sec` 最长声明到 900s，如 `kline_batch`/
  `signal_scan`）。两处都是在 FastAPI 的 `async def` 路由里直接同步
  调用，会独占 uvicorn 单事件循环——`aggregate_status()` 侧表现为"切
  tab 卡几秒"，`trigger_run()` 侧更严重："手动触发一次长 entrypoint，
  daemon 对所有其它请求（其它 session 对话、SSE 推送、其它看板 tab）
  900 秒内都没有响应"，跟使用者的两个反馈完全对应。

  修复（不改变对外契约，纯内部实现）：
  1. `status.py::probe_health()` 新增 60s TTL 进程内缓存（按
     `(项目名, health_check 命令)` 为 key），命中缓存不再 fork 子
     进程——直接把"切 tab 反复触发探测"这一大头开销砍掉。
     `aggregate_status()`/`project_status_snapshot()` 透传
     `use_cache` 参数，CLI 场景可传 `False` 强制拿到当下最新结果。
  2. `api/routes.py` 的 `get_self_external_projects()`/
     `post_external_projects_trigger_run()` 改用仓库里已有的
     `utils/blocking_guard.py::run_blocking()`（跟 LLM 调用同一套
     线程池 + 超时 + 熔断防护，不是新发明一套裸
     `asyncio.to_thread`）把这两处阻塞调用丢进线程池执行，事件循环
     本身不再被占用——这是解决"daemon 卡死"的关键一步。
  3. `scheduler.py::trigger_run()` 新增 `EntrypointAlreadyRunningError`：
     同一个 `(project, entrypoint)` 有一次执行正在进行时直接拒绝
     （409），防止用户在等待响应期间误以为没反应而连续点击「触发」，
     堆出多个并发子进程进一步放大资源占用——这是独立于"阻塞事件循环"
     之外的第二个风险点，一并修了。
  4. **实现过程中顺手发现并修复的真实 bug**：`run_blocking()` 把
     `fn` 抛出的任何异常都计入熔断失败计数，但 `EntrypointParamError`/
     `ExternalProjectRegistryError`/`EntrypointAlreadyRunningError`
     是"用户填错参数/项目名/重复点击"这类正常业务错误，不代表触发
     操作本身不健康——如果不处理，连续填错几次参数就会把整个触发
     功能熔断 120 秒，殃及其它项目的正常触发请求。加了一层"业务异常
     不 raise、作为返回值带出线程池"的包装（`_trigger_run_catching_
     business_errors()`），熔断计数只统计线程池调用本身的意外失败。
  5. `apps/mini_agent_kanban/client.py::trigger_external_project_run()`
     的 HTTP 客户端超时从 120s 放宽到 960s（略高于目前已知最长的
     entrypoint timeout_sec=900）——之前 120s 比部分 entrypoint 的
     声明超时短，长任务场景下客户端会先于服务端超时，看到一个"触发
     失败"的假报错，实际后台还在继续跑；现在服务端已经不会阻塞
     daemon，可以放心让客户端多等一会儿。

  验证：`tests/test_api_external_projects_routes.py` +
  `test_external_projects*.py` 共 128 个用例全部通过（含新增覆盖
  `EntrypointAlreadyRunningError` 409 路径的用例）。

  **刻意不做的事**（记录决策，避免以后重复纠结）：没有把
  `trigger_run` 改成"立即返回 + 后台异步执行，前端轮询状态"这种更
  彻底的架构——当前"阻塞线程池 + 放宽客户端超时"已经解决了"daemon
  卡死"这个更严重的问题，改成异步轮询需要新增任务 ID/状态存储/前端
  轮询 UI，属于超出本次"发现卡顿→定位→修复"范围的架构变更，如果未来
  长 entrypoint 场景变多、用户对"触发后立刻拿到反馈"的需求变强，可以
  再单独立项做。
