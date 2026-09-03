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
- **⏸️ 暂停全部调度 / ▶️ 恢复调度**（看板"停止调度"功能，见
  [Stage 9 自主运行时指南](self-evolution-stage9-guide.md#421-全局暂停调度看板停止调度功能)）：
  顶栏常驻按钮，与"⚙️ daemon 正在执行 N 项任务"区块相邻。点击暂停后
  `AutonomousLoop.tick()` 直接短路，cron job 到期触发 / Objective 推进 /
  软目标 derive 全部不再自动发生，同时展示暂停时间与原因（若填写）；
  暂停期间仍可在"⏰ Cron 任务"tab 手动"立即触发"某个 job、在"📌 目标看板"
  tab 手动增删改 Goal/Objective 来调试配置，也不会打断当前正在跑的任务。
  点击"▶️ 恢复调度"撤销暂停。状态持久化在 `self_profile.json`，
  daemon 重启后仍保持暂停，不会自动恢复。
- **⚙️ daemon 正在执行 N 项任务**（[daemon_stability_and_ux_improvement_plan.md
  补充] 顶栏跳转增强）：聚合展示 daemon 后台此刻正在跑的 Objective 执行
  （`/v1/autonomous/status` 的 `objective_executions`，`status=="running"`）、
  正在执行的 cron job（`/v1/cron/jobs` 的 `execution_phase=="running"`）、
  正在运行的 workflow（`/workflow_runs`，`status=="running"`）三类，每条都
  标出明确来源（🎯 目标(Goal) / ⏰ Cron 定时任务 / 🔄 工作流(Workflow)），
  避免只看标题猜不出这项任务是谁触发的。每条后面带一个「🔍 查看并控制」
  按钮，点击后会跳转到对应的 tab（📌 目标看板 / ⏰ Cron 任务 / 🔄 工作流）
  并高亮/置顶显示这一项，可以在那里直接暂停/终止/编辑等操作，不需要自己
  去对应 tab 里翻找。跳转的实现见下方"Tab 导航与按需渲染"一节——现在是
  直接把 `st.session_state["_active_tab"]` 设成目标 tab 并 `st.rerun()`。
  跳转到「📌 目标看板」时如果该 Objective 因为状态筛选被隐藏，会在筛选
  框之上单独高亮渲染一遍，并提供"❌ 清除定位"退出高亮态；跳转到「⏰ Cron
  任务」时目标 job 会被排到列表最前面并给出提示；跳转到「🔄 工作流」复用
  已有的 `wf_active_run_id` 机制直接展开该次运行详情。
- **🎛️ 并发上限**（`next_doc/kanban_concurrency_control_plan.md`）：紧跟在
  "⚙️ daemon 正在执行 N 项任务"之后的可折叠面板，标题常驻展示"目标
  running/cap"和"Cron running/cap"。这里控制的是**顶栏那个任务列表能
  同时有几条**——即 Objective/Goal 通道、Cron 通道各自的并发执行数，跟
  daemon 内部更底层的 SubAgent/LLM 请求并发是两个不同层级（后者折叠在
  同一面板内部"高级：SubAgent / LLM 并发"子区域单独展示，避免混淆）。
  展开后左右两栏分别是"目标(Goal)执行并发"和"Cron 执行并发"，可查看当前
  运行数/上限并编辑，点击"应用"立刻生效，跟 daemon 本机终端调整对应配置
  是同一套底层机制。目标(Goal)通道的并发上限（`autonomy.
  max_concurrent_objectives_cap`，默认 2）**没有硬天花板**，数字输入框
  只要求 >= 1；Cron 通道若当前走的是未启用独立并发槽位的旧路径，该栏会
  显示"未启用独立并发通道"提示而非可编辑控件。**注意这些都是运行时状态
  的热改，不会写回 `agent_config.json`**——`agent_config.json` 里配置的
  值才是持久化的默认值，daemon 重启后会掉回配置文件里的值（没配置就是
  硬编码默认值 2）；调低上限只影响后续新任务排队，不会打断当前正在跑的
  任务。
- **⚠️ 系统状态哨兵**（`GET /v1/sentinel/summary`，
  `next_doc/kanban_perception_gaps_improvement_plan.md` 方向 A）：跟上面
  "📥 全局待办中心"是姊妹关系但语义不同——待办中心的每一条都有明确的
  下一步操作（批准/拒绝/查看），哨兵面板聚合的是"系统状态可能不太对劲，
  用户大概率没注意到"的信号，很多条目本身不需要用户立即做什么，只是
  提醒留意。两者刻意不合并，各自独立可折叠。当前聚合五类信号：
  - cron job 连续失败次数达到阈值（默认 ≥2 次，含"已启用但一直在失败"
    这种最容易被忽视的组合），点击"跳转"直接定位到「⏰ Cron 任务」Tab；
  - Objective 执行步骤重试次数接近上限（快要判定失败前的最后一次机会）；
  - wiki 隔离区积压条数（详见下方"🧠 自我状态"Tab 一节）；
  - LLM 是否已切换到备用配置（详见下方 LLM 故障转移状态一节）；
  - 过去 7 天资源仲裁处于 `degraded`/`blocked` 的时间占比。
  五类中任意一类非空即视为"有内容"，区块默认展开；全部为空时区块本身
  不渲染（不占位置提示"一切正常"）。不引入"已读/已忽略"状态机——某一类
  信号持续存在但用户判断"不需要处理"时，让它持续显示是符合预期的，不是
  bug。

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
| 🗂️ 会话管理 | 会话列表、新建 / 恢复 / 删除会话、清理保护置顶、批量清理旧会话（见下方专节） |
| 📌 目标看板 | Goal / Objective 看板（按状态分列）、新建目标、删除单个/一键删除全部目标（级联清理关联 cron job 与产出数据）、Cron Job 管理与手动触发、Objective 执行进度、按长期方向聚合视图（见下方专节） |
| 🔄 工作流 | Workflow 运行面板、看板视图（按 Step 状态分列）、暂停/取消/续跑/审批、出错定位与单步修改重跑、历史执行列表 |
| 📁 产出物 | 浏览 `.agent/` 等目录下产出文件，预览与下载 |
| 🖼️ 产出预览 | 按任务/session 登记的产出物 manifest 语义化展示（图片内联、文档下载），支持深链接直达 |
| 🧠 自我状态 | 具身智能自省信息（自主循环摘要、活跃目标数、最近活动、多用户会话池 SessionPool 概况） |
| 🎓 能力学习 | 能力学习 / 人设养成 Track 管理（新建/暂停/恢复/删除）、大纲覆盖状态与学习台账、异步问答队列（提交/忽略回答、历史问答），见下方专节 |
| 🧬 进化提案 | Skill 提案列表、git worktree diff、批准/拒绝 |
| 🗂️ 外部项目 | 外部项目（`mini-agent projects`）管理接入看板：注册项目、健康状态总览、手动触发 entrypoint、改进积压 backlog 查看/新增、周期性 review 任务模板预览（见下方专节） |
| ⏰ Cron 任务 | Cron Job 列表（含 priority）、启用/禁用、手动触发、新建、删除（非 sys: job） |
| 🗓️ 全局日程 | cron job 到期时间线 / 周期性 Goal 下次触发 / 仲裁状态变化时间线，三类合并展示（见下方专节） |
| 🔌 外部输入 | 外部输入网关配置与最近事件 |
| 🔔 关注与通知 | Watchlist 关注项与通知历史 |
| 🛡️ 受保护文件 | `protected_files.txt` 清单查看/增删声明、手动备份快照、按快照恢复（见下方专节） |
| ⚙️ 配置 | 运行时配置字段编辑 |
| 🔧 诊断 | `/diagnostics` 原始信息，便于排障 |
| 🧪 混合执行 | 混合执行模式相关面板 |

### Tab 导航与按需渲染（`tab_lazy_render_plan.md`）

看板不再用 `st.tabs()` 做 tab 切换，改成一排 `st.button` 模拟的"假
tab"（`render_tab_nav()`），点击某个按钮会把 `st.session_state["_active_tab"]`
设成对应 key 并 `st.rerun()`；这次 rerun 之后**只有当前选中的那一个
`render_*_tab()` 会被调用**，其余 19 个 tab 的函数完全不会执行。

背景：`st.tabs()` 只是把内容分组到不同的 CSS 容器做视觉切换，Streamlit
脚本本身还是从头到尾整体重跑一遍——旧实现下，不管触发 rerun 的交互
发生在哪个 tab 里（哪怕只是"💬 对话"里发了一条消息），20 个
`render_*_tab()` 都会被无条件调用一遍，各自内部该发的 HTTP 请求也都会
发出去；里面挂着 `@st.fragment(run_every=...)` 的板块（"💬 对话"和
"🗂️ 会话管理"下的几个）更是从看板打开那一刻起就在服务端常驻轮询，跟
用户实际停在哪个 tab 完全无关。改成按需渲染后：

- 没被选中的 tab，其 `render_*_tab()` 不会被调用，自然也不会发出它内部
  的 HTTP 请求；
- 没被选中的 tab 里挂着的 `@st.fragment(run_every=...)` 也不会被创建，
  切走之后原来挂载的 fragment 自然停止轮询。

**唯一的 tab 清单来源是 `TAB_DEFS`**（`app.py` 里紧挨着 `main()` 定义）：
一个 `(key, label, render_fn)` 三元组列表，新增/删除/重排 tab 只改这一
处。默认停在 `"chat"`（💬 对话）。

**顶栏跳转**（"🔍 查看并控制"之类的按钮）现在只是一次普通的
`st.session_state["_active_tab"] = "<目标 tab 的 key>"` 赋值加
`st.rerun()`，不再需要在 DOM 里找按钮模拟点击的 JS 注入手法。

**深链接兼容**：`?manifest_id=`/`?session_id=` 打开时，
`apply_deep_link_query_params()` 会顺带把 `_active_tab` 设成
`"artifacts_preview"`，做到"打开链接直接停在产出预览 tab"，不需要用户
自己再点一下。

**已知取舍**：tab 切换从"纯前端瞬切"变成"点击 → 服务端 rerun → 重新
渲染"，会有一次网络往返（通常几十到一两百毫秒），不再是零延迟切换——
这是用瞬时切换体验换取减少无效后台请求的本质代价。

**排版**（`tab_lazy_render_plan.md` 阶段2 修正）：按钮宽度跟着各自文字
内容走（固定宽度），不是"20 个等分可用宽度"——早期版本给每个按钮传了
`use_container_width=True`，20 等宽列在窄屏下会被压得越来越窄，挤成
一条基本看不清文字的横条，而不是换行。现在 `render_tab_nav()` 用
`st.container(key="tab_nav_row")` 包一层，配合 scope 到
`.st-key-tab_nav_row` 的 CSS（`flex-wrap: wrap` + 每列 `flex: 0 0 auto`），
宽度不够时整行自动换到下一行，每个按钮保持自身固定宽度不被压缩。这个
scope 方式依赖 Streamlit 给带 `key` 的容器自动加 `st.key-<key>` CSS
class 的特性，`apps/mini_agent_kanban/requirements.txt` 里 Streamlit 最低
版本已相应提到 `1.39`（更早版本理论上仍可运行，只是这条换行 CSS 可能
不生效，退化成"按钮不换行、超宽时挤压"，不影响功能本身）。

### 💬 对话 Tab

事件展示逐类型解析（`tool_call` / `tool_result` / `tool_error` / `permission_req` /
`permission_done` / `turn_start` / `turn_done` / `error` / `token`），与
Web Demo 的事件流面板类似，但集成在同一多 Tab 界面中。

### 🗂️ 会话管理 Tab

会话列表支持分页（见下方"大数据量下的分页显示"），每个 session 卡片新增
两个跟"清理"相关的操作：

- **🔒 保护（防清理）/ 🔓 取消保护**：对应 CLI 的 `/session pin` /
  `/session unpin`，置顶保护后批量清理永远不会删除这个 session，卡片
  标题上会显示"🔒已保护"徽标。

  > 注意跟旁边"📎 加入/移出并排对比"按钮的区别：那个"📎固定"是看板本地
  > 概念（把 session 加进下方并排对比区，方便同时看多个 session 状态），
  > 只影响这个浏览器标签页的显示，不会写回后端；这里的"🔒保护"才是真正
  > 影响后端批量清理判定的开关，两者刻意用了不同的图标和措辞，避免混淆。

- **🧹 批量清理旧会话**：分页控件下方的折叠面板，对应 CLI 的
  `/session cleanup`。填好"保留最近 N 天"/"保留最近 N 个"两道安全网参数
  （默认 30 天 / 20 个），可选"先补跑知识抽取"（对候选删除但还没抽取过
  知识的 session，删除前先跑一次离线抽取，会调用 LLM、耗时更长）。点
  **🔍 预览** 先 dry-run 一次，列出"将删除 / 待抽取跳过 / 失败"三张表；
  确认列表无误、勾选"我已确认以上列表"后，才能点亮 **⚠️ 确认执行清理**
  真正执行。

  永远不会被清理的 session：当前活跃 session、🔒已保护、挂着未终结
  Goal（running/stuck）的 session，以及命中"最近 N 天"或"最近 N 个"
  安全网的 session。判定规则、代码实现均与 CLI 共用同一个
  `evolution/session_cleanup.py`，行为完全一致，详见
  `next_doc/session_cleanup_design.md` §9。该操作需要 owner 权限（单
  token 模式下自动放行，多用户模式下非 owner 调用会被拒绝）。

  - **含孤儿目录**：会话管理分页统计数（走 `meta.json` 才能识别的
    "正常 session"）经常和 `.agent/sessions/` 目录下实际的目录数对不
    上——差值通常是一批"有目录、没 `meta.json`"的孤儿目录，一轮对话
    没跑完就中断（daemon 重启/进程被杀/cron 子 agent 提前失败）时会
    留下这种残留，普通的会话列表和批量清理默认都扫描不到。勾选这个
    选项后，预览/执行会额外把这批孤儿目录一起纳入，独立展示"孤儿目录
    汇总 + 将删除/失败"表格，跟正常 session 的清理结果分开列，互不
    影响。旁边的"孤儿目录最小年龄（小时）"（默认 6 小时）是安全网——
    目录创建时间早于 `meta.json` 写入，太新的目录很可能只是正在进行
    中的第一轮，达不到这个年龄不会被当孤儿删除。详见
    `next_doc/session_cleanup_design.md` §10。


### 📌 目标看板 Tab

对接 Stage 9 自主 daemon 的 `GoalBacklog` 与 `CronScheduler`：

- **🌳 目标树子页**（`next_doc/goal_tree_system_plan.md` 阶段四）：Tab
  顶部有一个"📋 列表/看板视图" / "🌳 目标树"视图切换单选框（默认列表/看板
  视图，不影响原有使用习惯）。切到"🌳 目标树"后：
  - 从全局唯一根节点（`level=ultimate`）开始，递归展示完整的
    `ultimate → domain → stage → goal → objective` 层级，每个节点标题前
    带 level 图标（🌍/🧭/📅/🎯/📌）+ 状态标签，尚未创建根节点时提供
    创建表单。
  - 每个节点下方"⚙️ 管理"折叠区：编辑标题/描述/优先级、新建子节点
    （选层级+标题+描述）；`ultimate`/`domain`/`stage` 三层非叶子节点
    额外提供"🪄 帮我拆解此节点"按钮（手动触发 `GoalTreeDecomposer`，
    走 `POST /v1/goals/{id}/decompose` 异步任务轮询，跟执行规范生成同
    一套 `async_job_ui.start_async_job`/`run_async_job` 模式，不会
    卡住页面）、以及对直接子节点的"📌 pin/取消 pin"现阶段焦点按钮。
  - 待确认的分解候选（`decompose_candidates`）以虚线卡片形式挂在对应
    父节点下方，提供"✅ 采纳"/"✖️ 忽略"/"✏️ 编辑后采纳"三个操作。
  - 当前处于父节点 `current_focus_ids` 里的子节点，渲染前会有一行
    "⭐ 以下为当前焦点"提示。
  - **改父节点**（`POST /v1/goals/{id}/reparent`）：每个非根节点的
    "⚙️ 管理"折叠区里有一个"🔀 改父节点"表单，下拉框列出全树里除自己和
    自己的全部子孙之外的其它节点，选中后提交即生效；后端会拒绝会形成
    环、层级顺序倒挂（如把 `domain` 挂到 `goal` 下面）的选择，前端下拉
    框已经预先排除了"挂到自己子孙下"这一类选项（减少无效尝试，真正的
    环检测仍在后端兜底）。根节点（`level=ultimate`）不允许改父节点，
    不会展示这个表单。
  - 对应新增 REST 端点：`GET /v1/goals/tree`、`POST /v1/goals/nodes`、
    `POST /v1/goals/{id}/decompose`、
    `POST /v1/goals/{id}/candidates/{cid}/accept|reject`、
    `POST /v1/goals/{id}/focus_pin`、`POST /v1/goals/{id}/reparent`，
    详见 [HTTP API 指南](http-api-guide.md)。
  - **📄 相关调研 / 💡 建议**（`next_doc/goal_tree_research_and_action_
    recommendation_plan.md` §4.5/阶段四）：每个节点的\"⚙️ 管理\"折叠区
    底部新增\"📄 相关调研\"子区块——展示该节点当前待处理的调研候选（标题+
    生成理由）、最近一次触发调研的时间，以及一个\"🔍 立即调研\"按钮
    （对应 `POST /v1/goals/{id}/research/trigger`，`force=true`，手动
    跳过节奏治理）。产出的候选走成长顾问既有的 `GrowthBacklog` 队列，
    要采纳/忽略请到\"🌱 成长顾问\"tab 或用 `/agent growth accept|dismiss`
    处理，本区块只做展示和手动触发，不重复一套候选处理 UI。
    节点标题旁如果出现\"💡\"图标，表示该节点有未处理的\"焦点行动建议\"
    （`focus_next_step` 候选，来自 `next_action_advisor`，需要先在
    `agent_config.json` 里显式开启 `digest_advisor.next_action_focus_
    next_step_enabled` 并触发过一轮 `/next` 生成才会有数据）；建议内容
    本身走已有的\"💡 建议\"展示入口（晨报/`/next`），树形视图里只做\"有没有
    待处理建议\"的提示，不重复展示具体建议文案。
  - **自动巡检**（默认关闭）：`agent_config.json` 里
    `growth_advisor.goal_tree_focus_research_auto_trigger_enabled` 设为
    `true` 后，每小时一次的 `sys:goal_tree_focus_recompute` 巡检会在
    重算 `current_focus_ids` 之后，顺带对\"新进入焦点\"的节点自动触发一次
    `FocusResearchTrigger`（跟上面\"🔍 立即调研\"按钮走同一套函数，仍受
    节奏治理约束），每轮最多处理
    `growth_advisor.goal_tree_focus_research_auto_trigger_max_nodes`
    （默认 5）个节点，避免焦点集合一次性大幅变化时瞬间铺开一堆调研候选。
    默认关闭，先手动用\"🔍 立即调研\"/CLI `research` 命令观察候选质量，
    确认合适后再考虑开启。
  - 对应新增 REST 端点：`GET /v1/goals/{id}/research`、
    `POST /v1/goals/{id}/research/trigger`、`GET /v1/goals/next_steps`，
    详见 [HTTP API 指南](http-api-guide.md)。
- **📈 完成率趋势**（`GET /v1/objectives/completion_trend`，
  `next_doc/kanban_perception_gaps_improvement_plan.md` 方向 D.1）：折叠
  区块，展开才拉取。展示每日完成/失败 Objective 数的折线图，以及最近
  一次快照的完成数/失败数/平均重试次数三个指标——回答"这周完成的
  Objective 比上周多还是少""平均一个 Objective 要重试几次才能完成"这
  类此前答不出来的趋势问题。跟"🌱 成长顾问"tab 的"📈 健康度趋势"是同一套
  展示模式（折叠区块 + 折线图 + 最新指标），但数据源完全独立：本区块
  的快照挂在 `POST /v1/growth/scan`（cron `sys:growth_advisor_daily` 每日
  调用）上顺带记录，不是成长顾问自己的数据，只是复用了同一个每日调用
  时机，避免新增线程/独立 cron。首次使用需要等 cron 至少跑过一轮才会
  有数据，之前显示"暂无历史快照"提示。
- 按状态列出 Goal（如 `pending` / `active` / `done`），支持新建目标（标题、描述、优先级、
  来源）。创建成功后用 `st.toast()` 弹出确认提示，表单同时以
  `clear_on_submit=True` 清空输入（`kanban_goal_creation_feedback_bugfix.md`），
  避免用户点了创建之后看着输入框里残留的旧内容、不确定是否提交成功。
- **待完善状态 / 创建后是否立即执行**（`goal_draft_flow_plan.md`）：新建表单里
  除了标题/描述/优先级，还有一个单选——"🚀 立即可执行"（默认，等同以前的
  行为）或"📝 待完善"。选"待完善"时新 Goal 会以 `status="draft"` 创建，
  在看板上单独占一列（`📝 待完善`），**不会**被调度器或周期性 cron 拾取
  （两者都只认 `status=="active"`），但可以正常在 Goal 详情里设置周期性、
  生成/确认执行规范草案、改描述等——这些操作本身不检查 status。适用于
  "这个 Goal 一开始就打算做成周期性的，想先把周期设好再让它开始跑"的
  场景，避免创建后立刻被调度执行了一轮"一次性"的老路径。待完善的 Goal
  卡片顶部会有醒目的"🚀 激活并开始执行"按钮（等价于把状态改成
  `active`，也可以直接用卡片下方的通用状态下拉框切换），点击后才真正
  进入调度。
- **删除目标**（`kanban_goal_delete_and_bulk_delete_plan.md`）：
  - 单个删除——每张 Goal 卡片（非 Objective 子卡片）下方有独立的
    "🗑️ 删除目标"折叠区，点击"🗑️ 删除"进入二次确认态，再点"⚠️ 确认删除"
    才真正调用 `DELETE /v1/goals/{goal_id}`；确认文案会列出连带清理的
    内容（绑定的 cron 定时任务、`daemon_run_outputs/goals/<id>/` 过程
    数据、执行规范、执行阶段状态、调优草案），如果该 Goal 设置过产出
    目录（`user_output_dir`）会额外提示"该目录不会被删除"。删除成功后
    展示实际清理了几个关联 cron job，若部分关联文件清理失败会单独提示。
  - 一键删除全部——Tab 顶部（新建目标表单上方）有一个默认收起的
    "🗑️ 一键删除所有目标"折叠区，用于一次性清空当前全部 Goal（及各自的
    子 Objective）。确认门槛比单个删除更高：需要在文本框里输入固定短语
    「删除全部」才能点亮"⚠️ 确认删除全部"按钮，避免误触清空整个看板。
    对应 `DELETE /v1/goals`，内部对每个 Goal 走与单个删除完全相同的
    级联清理逻辑，单个 Goal 清理失败不影响其它 Goal 继续处理。
  - 两个删除接口都只接受 `level == "goal"` 的节点——Objective（子任务）
    没有单独的硬删除入口，需要终止/取消的话用卡片上已有的"🛑 终止"或
    状态下拉框。
  - 若某个 Goal 通过 `user_output_dir` 显式指定过产出目录，删除时**只清
    理 Goal 自己的内部记账目录，不会碰用户的自定义产出目录**，详见
    `docs/http-api-guide.md` `/v1/goals` 一节。
- **周期性 Goal 可见性与操作**（`goal_cron_visibility_and_intervention_improvement_plan.md`
  Track A/B）：Goal 卡片标题下方展示 `🔁 周期性 · 已完成 N 轮` 徽标（未绑定则显示"未设为
  周期性"）；由 cron 触发的子 Objective 标注"⏰ 由 cron 周期触发"。卡片下方"⏰ 周期性设置"
  折叠区可直接绑定/解绑（对应 `/agent goals recur|unrecur`）、"⏭️ 跳过下一轮"（保持
  `recurring=True` 只跳过下一次触发，写入 `progress_notes` 留痕）——此前这三个操作只有
  CLI 入口，看板完全看不出一个 Goal 是不是在周期性运转。
  - **修改已生效的周期**（`goal_recurring_schedule_editable_after_bind_plan.md`）：已绑定
    周期性的 Goal，"⏰ 周期性设置"折叠区里多了一个"✏️ 修改周期"子折叠区，可以直接改
    schedule（间隔或 Cron 表达式）和每轮任务内容，表单默认预填当前实际生效的配置。
    之前只能先"🛑 取消周期性"、再走"设为周期性"表单重新绑定，会经历一次"暂时不是
    周期性"的中间状态；后端 `make_goal_recurring()` 本身早就是幂等的（已绑定时直接
    复用同一个 cron job 更新 schedule/task_template，不会重复建 job），这次只是把这个
    能力从看板暴露出来，接口不变（还是 `POST /v1/goals/{id}/recur`）。
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
- **🗞️ 每日融合日报 / 💡 主动推荐 / 🧭 决策画像**（`proactive-recommendation-and-digital-persona-design.md`）：
  三张并排只读卡片，分别展示 `sys:daily_digest`（行为+目标进展融合日报）、
  `sys:next_action_digest`（停滞目标/注意力错配排序推荐）、
  `sys:decision_profile_update`（决策价值模式归纳，默认关闭）三个 cron job 的
  最新产出。卡片本身不触发生成，避免刷新看板页面时意外产生额外 LLM 调用；
  要立即刷新内容，仍需在 CLI 侧执行 `/digest daily`、`/next refresh`、
  `/decision_profile update`，或用 Cron Job 列表的"立即执行一次"按钮触发对应 job。

详见 `docs/autonomous-daemon-design.md`、`docs/goal-mode-guide.md` 了解 Goal/Cron/
Objective 背后的调度机制；`docs/decision-profile-guide.md` 了解决策画像的归纳与
矛盾处理逻辑。

#### Goal 执行规范草稿（generate → 反馈迭代 → 确认）

每张 Goal 卡片下方有一个执行规范面板（`_render_goal_execution_spec_
widget()`），对接 `perception/goal_execution_spec.py`，与 CLI `/agent goals spec ...`
是同一套后端能力（详见 [命令与工具参考](commands-and-tools-reference.md)、
[HTTP API 指南](http-api-guide.md)）：

- **生成草稿**：未生成过时展示"起草方式"下拉框（不用模板从零生成，或选一个
  模板库骨架作为 few-shot 参考——关键词规则命中 Goal 描述时默认预选）、
  "从最近一轮执行记录反推草稿内容"勾选框（该 Goal 已跑过至少一轮时才
  展示）、"生成路径"下拉框——跟随配置默认（回退配置文件 `builder_mode`）/
  自动判断（关键词规则粗筛，命中项目相关诉求或 LLM 自报拿不准才起 Agent）/
  纯 LLM（不读项目内容，最快，但涉及项目细节容易编造）/ 只读探索 Agent
  （先用只读工具看一眼项目再生成，更贴合实际但更慢），单次覆盖
  `builder_mode`，不改配置文件。生成成功后展示"🧭 上次生成走的路径"，
  告知这份草稿有没有实际读取过项目内容。各选项的详细含义与对应的 REST
  参数值见 [Goal 执行规范指南 §7.1](goal-execution-spec-guide.md#71-看板周期性设置)。
  CLI 侧对应 `/agent goals spec generate`。
- **反馈迭代**：有未确认草稿时展示摘要 + 每个 section 的 🔒 锁定复选框
  （产出物/跨轮传递/子目录/每轮标准/特殊约束）+ 补充意见文本框——提交
  后未锁定的 section 据反馈重新生成，已锁定的原样保留。重新生成后自动
  展开"🔍 与上一版的差异"区块（➕ 新增 / ➖ 删除 / ✏️ 改写 三类标注，
  纯前端对比，不需要额外 LLM 调用），点"知道了"收起。
- **确认/放弃**：「✅ 确认使用此规范」冻结当前草稿（下次触发即生效）；
  「❌ 放弃草稿」清空重新开始；「📄 从模板重新起草」独立按钮，不用先
  放弃当前草稿就能整段换模板重新生成（同样会触发差异高亮）。
- **整体关闭判定**（仅一次性 Goal，`overall_completion_criteria` 非空时
  有意义）：「🔁 手动重判整体是否可以关闭」按钮旁有"整体关闭判定路径"
  下拉框——跟随配置默认（回退 `overall_completion_use_agent`）/ 只读探索
  Agent（打开该 Goal 实际产出目录核查文件内容后判定，更可靠但更慢）/
  纯 LLM（只依据 manifest 摘要文本判定，更快），单次覆盖
  `overall_completion_use_agent`，按钮上方常驻展示上一次判定结果
  （时间 + 走的路径 + 结论：已关闭/暂不关闭），不再只是一次性 toast
  提示。详见 [Goal 执行规范指南 §7.2](goal-execution-spec-guide.md#72-看板手动重判整体是否可以关闭)。
  CLI 侧对应 `/agent goals spec close-check`。

- **🧭 按长期方向聚合**（`next_doc/personal_researcher_and_coach_
  capability_gap_plan.md` C1）：Goal 列表上方的折叠区块，展示一个可选
  的"长期方向"分组视图——多个独立的 Goal（如"工作项目""投资学习"
  "内容创作"）可能共同服务于同一个更高层、不需要验收标准、不会真正
  "完成"的方向，这里把它们聚合展示出来。纯展示聚合，不参与
  GoalJudge 判定、不影响任何 Goal 的执行/调度：
  - 新建方向（标题即可，可选备注）
  - 每个方向卡片显示关联 Goal 数、Goal 列表（标题 + 状态）
  - 🗑️ 删除方向：只清空关联 Goal 的分组（`direction_id` 置空），不会
    删除 Goal 本身
  - 未分组的 Goal 可以在下方选择"目标 + 方向"手动关联
  - 对应 API：`GET/POST /v1/directions`、`PATCH/DELETE
    /v1/directions/{id}`、`POST /v1/goals/{goal_id}/direction`，
    `GET /v1/goals` 的返回值里也带上了 `directions` 字段

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

同一 Tab 内还包含几个只读观测区块：⚖️ 执行公平性（Goal 调度顺序快照）、
🔗 系统关联性（决策消费率/失败模式/建议反馈/纠正事件）、以及
**⚙️ 执行模型**——展示"目标级持久 Worker"与"调度心跳独立化"这两个默认
关闭的灰度开关（见 [Daemon 执行模型与调度心跳指南](daemon-execution-model-guide.md)）
当前的生效状态：Objective 执行模式（`shared_queue`/`isolated`/`persistent`）、
持久 Worker 活跃 execution 数、心跳线程是否存活。开关切换仍需改
`agent_config.json` 并重启 daemon，本区块本身不提供热切换按钮。

紧接着的 **🕹️ 统一调度总览** 区块（`GET /v1/self/scheduling_overview` +
`GET /v1/self/unified_scheduler_preview`，详见
[Goal/Cron 统一调度层指南](unified-scheduler-guide.md)）：展示 Goal/
普通 cron/goal_cycle 三条执行通道当前的运行/排队/跳过状态、共享的
`ResourceArbiter` 仲裁结果、哪些调度灰度开关（`unified_arbitration_
enabled`/`unified_dispatch_enabled` 等）当前生效，以及"如果现在要决定
谁先执行"的建议排序预览。纯只读展示，不提供任何触发/暂停操作按钮。

`⚙️ 执行模型` 区块顶部另有一个 **📋 执行总览** 面板
（`next_doc/kanban_execution_visibility_and_control_plan.md`）：
四栏分别展示 🟢 正在执行 / 🟡 排队等待 / 🔴 异常已回收 / ⚪ 最近完成，
汇总了 cron job（区分"正在跑"和"卡在排队"）、Objective execution
（`ObjectiveExecutor.get_status_summary()`）、以及三条卡死回收链路
（cron/`ObjectiveExecutor.reap_stale_steps()`/`ObjectiveIsolatedRunner.
check_health()`）最近发生过的具体事件——不再只是几个孤立的累计数字。
面板顶部有一个"🚨 立即回收卡死任务"按钮，不必等 watchdog 下一次 tick
就能立刻对三条链路各跑一次回收扫描（`POST /v1/self/execution_model/
force_reap`）。区块下方还有"🩹 卡死回收累计计数"四个指标（cron/
Objective step/持久 Worker discard/隔离线程池重建），本次看板会话内
任一数字增长会标红提示。

Tab 末尾还有一个 **🔀 LLM 故障转移状态** 区块（`GET /v1/self/
llm_pool_status`，`next_doc/kanban_perception_gaps_improvement_plan.md`
方向 B.1）：展示 `LLMClientPool.snapshot()` 的当前状态——是否已经切离
首选 provider/model（`current` entry 下标非 0 时高亮"⚠️ 当前已切换到
备用配置"），以及每个 configured key 的可用性（🟢/🔴）、累计失败次数、
冷却剩余时间。这是"接上一根已经焊好的线"：`LLMClientPool.snapshot()`
早就实现好了，此前只在内部用于取当前模型名（`/models`、`/status`），
key 级的 `fail_count`/`cooldown_remaining` 从未被任何端点返回过，daemon
因为限流不断切 key/切配置时用户完全没有渠道知道。未配置
`llm_fallback_chain`（只用单一配置）时本区块显示"未配置故障转移链"，
不是错误。

紧接着的"📊 LLM 调用统计"区块（`GET /v1/self/llm_call_stats`，方向
B.2）展示近 7 天按天聚合的调用次数/失败数柱状图，以及当日调用数/失败数/
输入输出 token 数四个指标——默认开启、只统计数字，不含请求/响应正文，
跟需要手动开启的 `LLM_DEBUG=1` 完整调试日志是两套独立的东西，详见
`docs/llm-failover-guide.md`。

再往下是"📈 健康度趋势"折叠区块——跟"🌱 成长顾问"tab 里的同名区块是
**同一个组件、同一份数据**（`next_doc/kanban_perception_gaps_improvement_
plan.md` 方向 D.2）：`growth_health_trend.jsonl` 已经覆盖了"记忆总条数
走势"这类需求，这里只是换一个展示位置方便"🧠 自我状态"tab 一站式查看，
没有另起一份采集逻辑。

再往下是**🧊 Goal Stuck 历史统计**区块（`GET /v1/goal_mode/stuck_stats`，
`next_doc/goal_stuck_stats_and_llm_progress_judge_plan.md` §1）：纯只读
聚合，不提供任何操作按钮。展示历史 goal_mode 会话总数、被判定 `stuck`
（GoalJudge/StuckDetector 多次恢复无效后的终态）的次数与占比、近 30 天
内的 stuck 数量，以及一个可展开的"反复卡住的目标"列表（按 `goal_text`
归并，同一个目标反复被判 stuck 往往说明目标描述或验收标准本身有问题，
比孤立的一次更值得关注）。这块数据不新增存储，直接复用
`goal_mode/state.py` 已有的会话目录扫描，主要用途是为"要不要上更高成本
的机制（比如并行多路径择优）"之类的立项决策提供真实触发频率参考，而不是
凭感觉决定。

Tab 最末是**🪞 自我画像 / 能力地图**区块（`GET /v1/self/portrait`，
`next_doc/streamlit_self_cognition_dashboard_plan.md`）：把此前散落在
`self_profile.json`（`identity`/`self_assessment`/`operating_state`）、
当前 workdir 实测能力地图（`consolidation.build_capability_map()`）、
`self_model_history.jsonl` 弱项数量走势、以及已发现技能目录
（`SkillLoader.get_catalog()`）一次性拉平展示：

- 顶部引用块展示 `identity.purpose`（若已设置）；
- 四个指标：累计运行 session 数、涉足项目数、自主等级、当前活跃项目；
- **历史强项 / 历史待加强领域**——`SelfAssessment.strengths`/`weak_areas`，
  跨 session、跨项目的慢变量汇总；
- 可展开的**全局领域置信度**——`confidence_by_domain`（global scope）；
- 可展开的**当前项目能力地图**（实测，workdir scope）——按置信度排序，
  每条显示 🟢/🟡/🔴（≥70%/50%~70%/<50%）+ 成功/失败次数，与上面的全局
  置信度是两份不同粒度的数据（一个是当前项目实测，一个是跨项目历史
  汇总），不应混淆；
- **弱项数量走势**折线图——`self_model_snapshot.py` 日频落盘的历史快照，
  至少有 2 个数据点时才展示；
- 可展开的**已发现技能目录**——所有已被 `SkillLoader` 发现的技能
  （不止当前激活的），🟢/⚪ 标记是否激活，用于回答"我现在具备哪些能力
  （声明层面），当前用上了哪些（激活层面）"。

`GET /v1/self/portrait` 后续又新增了 `agent_value_profile`/`body_inventory`/
`self_narrative`/`drift_signals`/`lineage` 五个只读字段（Agent 自身价值观、
身体清单、自我叙事、自我模型漂移信号、谱系视图），详见
[self-awareness-identity-guide.md](self-awareness-identity-guide.md)。
**这几个新字段目前只在 API 层暴露，本区块尚未渲染**——如需在看板展示，
按上面同样的"只读聚合、无操作按钮"模式扩展即可。

纯只读展示，不提供任何编辑/触发入口——`self_profile.json` 的写入是巩固
循环（Stage 8）的职责，不是看板要接管的操作。

### 🎓 能力学习 Tab

对应 `next_doc/persona_capability_learning_design.md` §7 三个区域，数据来自
`/v1/capability/*`：

- **人设管理区**：顶部"➕ 新建能力 / 人设方向"表单（标题 + 方向描述 +
  类型 knowledge/persona + 可选 wiki 命名空间 + 可选"用 LLM 起草初始
  大纲"），下方按 Track 列出，每个 Track 可折叠展开，含暂停/恢复、
  二次确认删除（删除只下线 Track 本身，不级联删已产出的 wiki 页面，
  见设计文档 §7.1 的克制原则）。
- **进度展示区**：展开某个 Track 后，按 `uncovered`/`partial`/`covered`
  三态图标展示大纲子主题覆盖状态，以及最近 10 条学习台账（检索沉淀/
  生成问题/消费回答/跳过/记录未命中/复用其它 Track 页面，见 §3.2、
  §13.1-c）。
- **大纲编辑区（§14.7，本轮新增）**：进度展示区上方，「🤖 生成/
  刷新大纲建议」按钮调用 LLM，在**当前**大纲基础上生成新增/改名/
  移除三类修订建议，以带复选框的清单展示（新增默认勾选，改名/移除
  默认不勾选，更保守），点「应用勾选的修订」才会真正写回——不是一键
  整体替换，改名/移除也不会影响子主题已有的覆盖状态和关联 wiki 页面。
  下方还有手动新增子主题的表单，以及每个子主题旁的 ✏️（内联改名）/
  🗑️（二次确认删除）。空大纲的 Track（表现为看板显示"覆盖 0/?"、
  cron 记录一直"成功"但检索/问题数全是 0）可以直接点「生成/刷新大纲
  建议」补齐，等价于生成一整批新增建议。
- **人设草稿区（仅 persona 型 Track，见 §10.3）**：「生成/刷新草稿」
  按钮会把目前已回答的问题合成一份 `.agent/personas/*.md` 格式的草稿
  并落盘（不会自动发布），旁边展示完成度（几个维度已经有信息、还缺
  哪些）；草稿正文可以在折叠区里预览；「发布」按钮需要二次确认，
  确认后才会真正写入 `.agent/personas/`（项目级目录），供 `/role use`
  立即激活。生成草稿和发布是两个独立的、都需要用户显式点击的动作，
  不会因为点了"生成"就顺带发布。
- **知识范围绑定区（仅 knowledge 型、已生成 wiki_tag 的 Track，见
  §11.4）**：展示当前哪些角色的 `wiki_scopes` 绑定了这个 Track 的
  wiki 命名空间，可在弹出面板里勾选/取消绑定各个已定义角色。
- **待回答问题区**：所有 `status=pending` 的异步问题，逐条展示问题文本+
  提示+所属 Track，配文本框"提交"/"忽略"按钮；提交后立即返回、不等待
  也不触发学习循环（异步语义，见设计文档 §3.3/§9 第 6 条）——下一轮
  `/capability cycle` 才会去消费已回答的问题。已回答/已忽略/
  已过期的问题收进一个折叠的"历史问答"区。

`sys:capability_learning_cycle`/`sys:capability_question_sweep` 两个
cron job 已注册但默认 `enabled=False`（opt-in，见设计文档「实施
状态」），因此这个 Tab 目前没有"距离下次学习还有多久"之类的倒计时
展示，也没有"立即学习"按钮——避免在用户显式打开 cron 之前，让 UI
暗示这是个已经在自动运行的功能。当前唯一能推进学习循环的方式是 CLI
的 `/capability cycle` 命令，或在 ⏰ Cron 任务 Tab 里手动打开对应
cron job 后等待其按 cadence 自动触发。

### 🗂️ 外部项目 Tab

对应 `next_doc/external_projects_kanban_integration_plan.md` 第一期，把
此前只能通过 `mini-agent projects ...` 命令行操作的外部项目管理能力
（详见 [docs/external-projects-guide.md](./external-projects-guide.md)）
接入看板。**只读展示 + 低风险写操作**（触发执行、写一条待办、注册
项目），不涉及改代码或合并分支——`propose_fix`/`land_maintenance_fix`
的看板化明确不在本次范围（风险控制考量见该计划文档第4节）。

- **顶部「➕ 注册新项目」**：填项目根目录路径（需包含 `project.yaml`）+
  可选项目名称，调用 `POST /v1/external_projects/register`。
- **项目总览卡片**：每个已注册项目一张卡片——名称、健康徽标
  （🟢健康/🔴不健康/⚪未知，数据来自既有的 `GET /v1/self/
  external_projects` 聚合端点）、启用状态、最近一次执行摘要，可展开
  查看最近 5 条执行记录。
  - **「自动调度」开关**：勾选后 daemon 会按 `project.yaml` 里每个
    entrypoint 的 `schedule` 自动触发，取消勾选会把该项目名下的
    `ext:*` cron job 真正删除（不是仅 disable），语义详见
    `ExternalProjectRegistry.register()` 文档字符串。
  - **[2026-08-31 修复的 bug]**：曾经存在"关掉自动调度、daemon 重启
    后又自动打开"的问题——根因在
    `RegisteredProject.from_dict()`（`src/mini_agent/external_projects/
    registry.py`）反序列化时，`enabled` 字段缺失时的兜底默认值错误地
    给了 `True`，跟 dataclass 字段默认值、`register()` 默认值（两处都
    是 `False`）不一致。`enabled` 字段是后来才加进这个 dataclass 的，
    在这之前注册的项目，磁盘上 `~/.agent/external_projects.json`
    里那条记录本来就没有 `"enabled"` 这个 key；daemon 每次启动都会对
    所有已注册项目跑一遍 `ensure_external_project_cron_jobs()`，命中
    这个错误默认值的记录会被误判成"已启用"，从而重新生成对应的
    `ext:*` cron job。已修正为默认 `False`（与其余两处保持一致，
    "未显式开启就是关闭"），并补了对应的回归测试
    （`tests/test_external_projects.py::
    test_registry_from_dict_defaults_enabled_false_when_key_missing`）。
    如果你在这次修复之前就注册过外部项目、且怀疑受过这个问题影响，
    可以直接打开 `~/.agent/external_projects.json` 检查对应项目
    的条目是否有 `"enabled"` 字段；没有的话，在看板上重新点一次
    「自动调度」开关（哪怕先开再关）就会把这个 key 正确落盘，之后不
    会再复现。
- **卡片内「▶️ 手动触发」**：把该项目 `project.yaml` 声明的 entrypoints
  全部列出来，一个 entrypoint 对应一行（key + schedule[如有] + 命令
  预览 + 「▶️ 触发」按钮），点按钮直接调用
  `POST /v1/external_projects/{name}/trigger_run`——**不需要手填
  entrypoint key**，与 `mini-agent projects run` 走同一条执行路径，
  成功/失败直接在该行下方提示，不做二次确认（触发一次 entrypoint
  本身没有破坏性）。manifest 解析失败或没有声明 entrypoints 时该区块
  给出对应提示文案，不是空白一片。
  - **需要传参数的 entrypoint**（`external_projects_kanban_integration_
    plan.md` 阶段6，如 `stock_watch` 的 `stock_analysis` 需要股票
    代码）：`project.yaml` 里在该 entrypoint 下声明 `params` 列表
    （`name`/`required`/`default`/`help`），看板会在「▶️ 触发」按钮
    上方按声明逐个渲染文本输入框（必填/可选标注 + help 说明文字）。
    这组输入框和触发按钮包在一个 `st.form` 里——填参数时只是在表单
    内部改值，不会触发整页重跑（也就不会重新请求
    `GET /v1/self/external_projects` 造成"打字就卡一下"），只有点
    「▶️ 触发」（`st.form_submit_button`）提交表单时才真正发一次请求、
    才需要刷新。提交后前端先做一次"必填项是否为空"的粗校验；真正的
    参数合法性判断（缺必填/传了未声明的参数名）全部在后端
    `manifest.py::build_cmd_with_params()` 完成——按声明顺序把输入框
    的值拼成位置参数（自动做 shell 转义）追加在 `cmd` 后面，与
    entrypoint 脚本读 `sys.argv[1:]` 的既有写法直接对齐，不需要改
    entrypoint 脚本本身。没有声明 `params` 的 entrypoint 不受影响，
    按钮下方不会多出任何输入框。
- **卡片内「📋 改进积压」**：按状态（open/proposed/landed/dismissed/
  全部）筛选查看该项目的改进积压账本（`GET /v1/external_projects/
  {name}/backlog`），下方文本框可新增一条待办
  （`POST /v1/external_projects/{name}/backlog`）——**`source` 后端
  固定写死为 `user_feedback`**，不接受前端传入，因为看板手填的这条
  路径语义上就是"人工反馈"，`outcome_review`/`health_trend` 应该继续
  只由 entrypoint/review session 自动写入。
- **卡片内「🔍 Review 预览」**：按钮生成本次 review 任务模板文本
  （`GET /v1/external_projects/{name}/review`，`review.enabled=false`
  时不报错，附带提示"未开启定期 review，但仍可手动预览"），旁边
  「📋 复制到对话框」按钮会把模板文本写进"💬 对话"Tab 的输入框并跳转
  过去——**只是预填文本，不自动发送**，真正发起 review session 仍需
  用户自己点发送，agent 仍要走一遍正常的工具调用+权限确认流程。
- **卡片内「📊 状态看板」**（`external_projects_generic_kanban_view_
  refactor_plan.md`，通用机制，任何声明了 `dashboard.kanban_view` 的
  外部项目都会显示这块，不是 stock_watch 专属）：读
  `GET /v1/external_projects/{name}/kanban_data`（`project.yaml` 里
  `dashboard.kanban_view.data_file` 声明的文件，由项目自己的某个
  entrypoint 产出），项目未声明该配置，或声明了但数据文件尚未产出，
  整块面板都不渲染（或只给一行"暂无数据"提示），不在没有这项功能的
  外部项目卡片上留一个空白 expander。内容按 `kanban_view` 声明动态
  生成：
  - **状态列视图**：按 `states` 声明的顺序分栏（`collapsed: true`
    的状态放进单独折叠区，不占列位），每列列出该状态下的记录标题、
    `metric_fields` 声明的正文字段。
  - **变更状态**（可选，取决于是否声明 `change_state`）：逐条记录
    展开后有一个下拉选新状态 + 可选备注输入框的表单，底层调用的是
    `change_state.entrypoint` 声明的 entrypoint（跟「▶️ 手动触发」
    区块走同一条后端路径，这里只是更顺手的入口，不是另一套逻辑）。
  - **详情列表**（可选，取决于是否声明 `detail_list_field`）：展开
    记录后展示该字段（字符串列表），比如 stock_watch 用它展示信号
    溯源 `reasons`。
  - stock_watch 是第一个接入方，通过 `project.yaml` 里的
    `dashboard.kanban_view` 声明接入六态候选池看板；任何其它外部
    项目只要在自己的 `project.yaml` 里声明同样的 schema、产出对应
    格式的 JSON 文件，不用改一行看板代码就能获得同样的看板。
    schema 完整说明见
    `external_projects_generic_kanban_view_refactor_plan.md` 第3节。

对应的 `AgentClient` 方法见下方"`AgentClient` 封装的 API 端点"一节。

### ⏰ Cron 任务 Tab

Cron Job 列表、启用/禁用、手动触发、新建，`priority` 字段的展示与编辑
（`update_cron_job(job_id, priority=...)`）。详见
`docs/cron-jobs-reference.md`、`next_doc/scheduling_unification_and_kanban_
visibility_improvement_plan.md`（P2）。

**删除**（`kanban_cron_delete_consistency_bugfix.md` 新增）：非 `sys:`
前缀的自定义 job 现在可以直接在本 Tab 删除，不用再切到"📌 目标看板"
Tab。流程与目标看板一致：点击"🗑️ 删除"进入二次确认态，"⚠️ 确认删除"
后才真正调用 `DELETE /v1/cron/jobs/{job_id}`；`sys:` 前缀的内置 job 不
展示删除按钮，只能通过启用/禁用开关控制。

### 🗓️ 全局日程 Tab

`next_doc/scheduling_unification_and_kanban_visibility_improvement_plan.md`
（P5）新增，把此前分散在不同 Tab 的三类时间信息合并成一条时间线，用来
回答"为什么现在没有任务在跑"这类问题：

- **未来 24 小时内到期的 cron job**（含 `priority`、已运行次数），来自
  `/v1/autonomous/status` 里的 `cron_jobs` 列表，按 `next_run_in` 排序。
- **周期性 Goal 下次触发**：展示绑定了 `recurring` 的 Goal 各自的下次
  触发时间，数据源和"📌 目标看板"Tab 里 Goal 卡片"下次触发"完全一致
  （都是对应 cron job 的 `next_run_str`），不是第二套计算逻辑。
- **仲裁状态变化时间线**：`ResourceArbiter` 三态门控
  （`full`/`degraded`/`blocked`）的历史变化记录，来自新增的
  `GET /v1/autonomous/gating_history`。只有状态发生变化时才会产生一条
  记录（例如从 `full` 变成 `degraded` 又恢复到 `full`），不是"每次轮询
  记一条"的日志流。记录的写入时机挂在 `/v1/autonomous/status` 被轮询上
  （看板顶栏会周期性调用），因此如果长时间没有任何客户端轮询过这个接口，
  期间发生的状态变化不会被记录下来。时间线上方新增一行**聚合占比摘要**
  （`next_doc/kanban_perception_gaps_improvement_plan.md` 方向 C，响应体
  新增的 `ratio_summary` 字段）：过去 7 天 `full`/`degraded`/`blocked`
  各自的累计时长占比，回答"这周有百分之多少时间处于降级/阻塞"这类逐条
  时间线难以心算的问题。历史记录条数达到 `_GATING_HISTORY_MAX_ENTRIES`
  （200 条）裁剪上限、且窗口内的最早一条记录仍晚于窗口起点时，摘要会
  附带"数据不完整，可能因为期间状态变化过于频繁"的提示，而不是静默
  给出一个不准确的比例。

Tab 末尾还有一个**⚖️ 调度公平性诊断**折叠区块（`GET /v1/self/
fairness_diagnostics`，`next_doc/goal_fairness_scheduling_diagnostics_
plan.md`）：纯只读快照，回答"P2 公平轮询/P3 老化加成/P4 时间片抢占这几个
默认值拍脑袋定的参数，现在实际状态是什么样"。展示 P4 时间片抢占当前是否
开启（默认关闭）、当前生效的老化加成/停滞判定/抢占触发阈值这几个参数值、
当前 active objectives 总数/老化加成生效数/因抢占被暂停数三个指标，以及
可展开的逐个 objective 明细（priority/aging_boost/effective_priority/
是否在跑/是否被抢占暂停）。跟"🧠 自我状态"tab 已有的"⚖️ 执行公平性"面板
（`/v1/self/goal_fairness`，Goal 粒度）是互补关系，不是重复：这个新面板
是 Objective 粒度，且额外覆盖了 P4 抢占状态（P5 面板完全不涉及）。不新增
任何历史事件持久化，只是"现在这一刻"的快照。

### 🛡️ 受保护文件 Tab

[受保护文件清单与删除防护机制](protected-files-guide.md) 阶段 5 新增，
把此前只能通过 CLI（`/agent protected ...`）或直接编辑
`protected_files.txt` 使用的判定/备份/恢复能力包装成看板 UI，全部调用
`/v1/protected-files/*` 端点，不重新实现判定/打包/恢复逻辑：

- **当前生效清单**：表格展示路径 + 来源清单文件，顶部附快照数量/最新
  快照 ID。
- **添加受保护路径**：路径输入 + 是否目录 + 写入顶层 `protected_files.txt`
  还是 `.agent/protected_files.txt`，清单为空时默认展开。
- **删除受保护路径声明**：下拉选择 + 删除按钮，只列出用户声明的路径
  （不含清单文件自身，该保护是自动规则，看板上没有删除入口）。
- **手动备份快照**：保留份数输入 + 立即备份按钮；命中"缺失核对"
  （某个曾经受保护的路径相对上一份快照已经消失）时直接在页面上给出
  警告，不做任何自动恢复。
- **快照与恢复**：快照选择 + 详情表格 + 恢复范围（全部/勾选部分）+
  两段式确认流程（先"预览恢复影响"不写盘，展示将覆盖的路径列表，勾选
  确认后才出现"确认执行恢复"按钮），与 Session 清理面板的预览/确认
  交互模式保持一致，避免误触覆盖。

`client.py` 对应新增 `protected_files_status` / `add_entry` /
`remove_entry` / `backup_now` / `list_snapshots` / `snapshot_detail` /
`restore` 7 个方法；`_delete()` 新增 `json_body` 参数支持（DELETE
请求带 body，删除声明需要在 body 里传具体路径）。

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
| `pin_session()` / `unpin_session()` | `POST /v1/sessions/{id}/pin\|unpin` | 清理保护置顶 / 取消置顶 |
| `cleanup_sessions(dry_run=True, keep_recent_days=30, keep_recent_count=20, extract_first=False, include_orphans=False, orphan_min_age_hours=6.0)` | `POST /v1/sessions/cleanup` | 批量清理旧会话（预览/执行同一接口，`dry_run` 区分），owner only；`include_orphans` 一并清理无 `meta.json` 的孤儿目录 |
| `users()` | `/v1/users` | 多用户列表（多用户模式） |
| `self_status()` / `autonomous_status()` | `/self/status`、`/self/autonomous` | 自省与自主循环状态 |
| `pause_scheduling(reason=)` / `resume_scheduling()` | `POST /v1/autonomous/scheduling/pause\|resume` | 看板"停止调度"功能：全局暂停/恢复自动调度，见顶部状态条一节 |
| `gating_history(limit=50)` | `GET /v1/autonomous/gating_history` | 仲裁状态（`full`/`degraded`/`blocked`）变化时间线，供"🗓️ 全局日程"Tab 使用（只读） |
| `goals()` / `add_goal()` / `update_goal()` / `delete_goal()` / `delete_all_goals()` | `/v1/goals*` | Goal 看板：查看 / 新建 / 更新 / 删除单个（级联清理关联 cron job 与产出数据）/ 一键删除全部；`goals()` 返回值内嵌 `directions` 字段 |
| `directions()` / `add_direction()` / `update_direction()` / `delete_direction()` / `assign_goal_direction()` | `/v1/directions*`、`POST /v1/goals/{id}/direction` | 长期方向分组：查看 / 新建 / 重命名改备注 / 删除（关联 Goal 自动清空分组）/ 把某个 Goal 关联或取消关联到某个方向（`personal_researcher_and_coach_capability_gap_plan.md` C1，纯展示聚合，不影响执行） |
| `recur_goal()` / `unrecur_goal()` / `skip_goal_next_cycle()` | `POST /v1/goals/{id}/recur\|unrecur\|skip_next_cycle` | 周期性 Goal 绑定 / 解绑 / 跳过下一轮（Track A/B） |
| `execution_spec_templates()` / `get_execution_spec()` | `GET /v1/goal_execution_spec_templates`、`GET /v1/goals/{id}/execution_spec` | Goal 执行规范：模板库摘要（带关键词匹配预选） / 查看当前草稿或已确认版本 |
| `generate_execution_spec(mode=)` / `revise_execution_spec(locked_fields=, mode=)` | `POST .../execution_spec/generate\|revise` | 生成第 1 版草稿 / 基于反馈+字段级锁定重新生成，`mode` 单次覆盖 `builder_mode`，响应体带 `effective_path` |
| `confirm_execution_spec()` | `POST .../execution_spec/confirm` | 确认并冻结当前草稿，下次触发即生效 |
| `close_check_execution_spec(use_agent=)` | `POST .../execution_spec/close_check` | 手动（重新）触发"整体是否可以关闭"判定，`use_agent` 单次覆盖是否走受限 Agent 路径核实产出文件 |
| `cancel_objective()` / `retry_objective()` / `inject_objective_guidance()` | `/v1/objectives/{execution_id}/*` | Objective 执行操作：终止 / 手动重试当前步 / 插话（Track D） |
| `inbox()` | `GET /v1/inbox` | 全局待办中心：跨 session 聚合权限/交互请求 + 失败 Objective（Track A） |
| `cron_jobs()` / `add_cron_job()` / `update_cron_job()` / `run_cron_job_now()` | `/v1/cron*` | Cron Job 管理，每个 job 附带 `execution_phase`（`not_running`/`queued`/`running`） |
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
| `execution_model_status()` | `GET /v1/self/execution_model_status` | 目标级持久 Worker / 调度心跳独立化两个灰度开关的生效状态，含 `recent_recoveries` 最近卡死回收事件（只读） |
| `force_reap(target=)` | `POST /v1/self/execution_model/force_reap` | 立即对指定链路（`cron`/`objective_step`/`isolated_pool`/`all`）跑一次卡死回收扫描 |
| `llm_pool_status()` | `GET /v1/self/llm_pool_status` | LLMClientPool 当前故障转移状态：是否已切离首选配置、各 key 的 fail_count/冷却剩余时间（只读，方向 B.1） |
| `llm_call_stats(days=7)` | `GET /v1/self/llm_call_stats` | 按天聚合的 LLM 调用计数：调用次数/成功失败数/切换次数/token 用量/平均耗时（只读，方向 B.2） |
| `objective_completion_trend(limit=30)` | `GET /v1/objectives/completion_trend` | Objective 完成率每日快照序列：完成/失败数、平均重试次数、活跃数（只读，方向 D.1） |
| `wiki_quarantine_status()` | `GET /v1/wiki/quarantine_status` | wiki 隔离区当前积压情况，不含已修复记录（只读，方向 E） |
| `external_projects_status()` | `GET /v1/self/external_projects` | 已注册外部项目的聚合状态（健康 + 最近5条执行记录），"🗂️ 外部项目"Tab 总览卡片数据来源 |
| `register_external_project(path, name=, validate=True)` | `POST /v1/external_projects/register` | 注册一个新的外部项目 |
| `trigger_external_project_run(name, entrypoint, params=None)` | `POST /v1/external_projects/{name}/trigger_run` | 立即触发某个已注册项目的某个 entrypoint 一次；`params` 是该 entrypoint 在 `project.yaml` 里声明了 `params` 时按参数名传的值（阶段6） |
| `external_project_ledger(name, limit=20)` | `GET /v1/external_projects/{name}/ledger` | 该项目的执行账本，最近 `limit` 条 |
| `external_project_backlog(name, status=)` | `GET /v1/external_projects/{name}/backlog` | 该项目的改进积压账本；`status` 留空表示不过滤 |
| `append_external_project_backlog(name, summary, evidence_ref=)` | `POST /v1/external_projects/{name}/backlog` | 新增一条待办，`source` 由后端固定写死为 `user_feedback` |
| `external_project_review(name)` | `GET /v1/external_projects/{name}/review` | 生成该项目的 review 任务模板预览（不实际发起 review） |
| `external_project_kanban_data(name)` | `GET /v1/external_projects/{name}/kanban_data` | 通用看板视图的结构化数据；未声明 `dashboard.kanban_view` 或数据文件尚未产出时返回 `available: false` |
| `sentinel_summary(cron_failure_threshold=2)` | `GET /v1/sentinel/summary` | 哨兵聚合面板：cron 连续失败 + Objective 重试热点 + wiki 隔离区积压 + LLM 故障转移状态 + 近 7 天仲裁降级/阻塞占比一次性拉取（只读，方向 A） |
| `concurrency_status()` | `GET /v1/self/concurrency` | SubAgent/LLM 请求这两个底层信号量的并发状态快照（只读，高级用法） |
| `set_concurrency(max_tasks=, max_llm_calls=)` | `POST /v1/self/concurrency` | 运行时热改最大并发 SubAgent 数 / 最大并发 LLM 调用数，立即生效、不写回配置文件 |
| `task_concurrency_status()` | `GET /v1/self/task_concurrency` | 顶栏"⚙️ daemon 正在执行 N 项任务"对应的任务执行并发状态：Objective/Goal 通道、Cron 通道各自的 running/current_cap（只读） |
| `set_task_concurrency(max_objectives=, max_cron_jobs=)` | `POST /v1/self/task_concurrency` | 运行时热改 Objective/Goal 通道、Cron 通道各自的最大并发执行数，立即生效、不写回配置文件；`max_objectives` 没有上限，只要求 >= 1 |

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
`next_doc/kanban-large-data-pagination-improvement-plan.md`）：

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

  **[阻塞防护 + 缓存改进]** `list_sessions_page()` 底层是同步磁盘扫描
  （`iterdir()` + 逐个读取解析 `meta.json`），session 数量越多、daemon
  运行越久，这个函数本身耗时会越长。曾经出现过 daemon 运行一段时间后
  `GET /v1/sessions` 单次请求耗时超过 4 分钟，把 FastAPI 的 asyncio
  事件循环整个卡死，导致看板连 `/v1/health` 心跳都拿不到响应、误判
  "无法连接到 Agent 服务"（详见 `next_doc/session_list_blocking_and_cache_fix.md`）。
  现已从两个层面修复：
  - **线程池隔离**：路由层用项目已有的 `run_blocking()` 助手
    （`mini_agent/utils/blocking_guard.py`）把 `list_sessions_page()` /
    `mgr.load()` 这类同步 I/O 丢进线程池执行，带硬超时（默认 45s）和
    连续失败熔断，即使个别请求很慢也不会拖住事件循环、影响其它并发请求
    （包括心跳检查）。
  - **进程内缓存**：`SessionManager` 新增按 `session_dir` 路径 keyed 的
    全量 metas 缓存（TTL 5 秒），看板高频轮询命中缓存时完全不碰磁盘；
    `save()` / `delete()` / `set_pinned()` / `mark_knowledge_extracted()` /
    `mark_summary_backfilled()` 等写操作成功后会主动使对应 session_dir
    的缓存失效，保证不会因为缓存而看到过期数据。

## 故障排查：daemon 进程还在跑，但看板提示"无法连接到 Agent 服务"

如果命令行能确认 daemon 进程仍在运行，但看板报"无法连接到 Agent 服务，
请检查地址/Token"，且此时 `http_access.jsonl` 里能看到某个请求（典型是
`GET /v1/sessions`）的 `duration_ms` 异常大（几十秒到几分钟，`slow: true`），
基本可以判定是**事件循环被某个同步阻塞调用卡住**，而不是真的网络不通或
token 错误。日志里同时出现的 `ConnectionResetError [WinError 10054]`
只是连接超时后的连锁反应，不是根因。

排查步骤：
1. 搜索 `http_access.jsonl` 里同一时间段内 `duration_ms` 明显偏大的
   请求，定位是哪个端点卡住了。
2. 确认该端点内部是否有未加 `run_blocking()`/线程池保护的同步磁盘或
   CPU 密集操作——`session.py::list_sessions_page()` 已在本次修复中处理，
   如果是其它新增端点出现同样问题，参考同样的模式加固。
3. session 数量特别多（几千条以上）时，即便有缓存 + 线程池，缓存过期
   瞬间的那次全量扫描本身开销也会变大，可考虑清理/归档旧 session
   （见 `next_doc/session_cleanup_design.md`），或后续把
   `_list_session_entries()` 换成增量索引（sqlite/独立索引文件），
   避免随 session 数量线性增长。

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

## 开发规范：新增/修改板块务必用 `@st.fragment` 做局部刷新

Streamlit 的默认行为是：任何一个 widget（`st.button`/`st.checkbox`/
`st.text_input`/`st.form_submit_button` 等）被触发，**只要它所在的函数不是
`@st.fragment`，就会重跑整个脚本**——对看板来说就是重跑整个
`render_xxx_tab()`。如果某个 Tab 里同时挂着好几个各自独立请求后端的板块
（比如成长顾问 Tab 里的诊断信息 / 健康度趋势 / 关键词管理 / 回访提醒 /
对齐视图 / 自主推进 / 报告刷新提示 / 候选列表），漏掉 `@st.fragment` 的
那个板块只要有一次 widget 交互，就会连带把其它跟它完全无关的板块一起
重新请求一遍接口、重新渲染一遍——用户体感就是"点一下毫不起眼的复选框，
整个页面卡一下"，但从这次交互本身看完全没必要触发任何刷新。

（配合上面"Tab 导航与按需渲染"一节：这里说的"重跑整个脚本"在按需渲染
改造后，实际效果是"重新渲染当前选中的这一个 tab"——其它 19 个 tab 依然
不受影响。也就是说 `@st.fragment` 现在解决的是"同一个 tab 内部板块之间
互相牵连"的问题，"不同 tab 之间互相牵连"的问题已经被按需渲染从根上
解决了，两者分工不同、都还需要。）

**这是一个实际踩过的坑**：`_render_growth_profile_and_keywords`（成长顾问
Tab 的"关键词管理"板块）最初没有包 `@st.fragment`，导致勾选/取消勾选一个
关键词的"选中"复选框，会把 `growth_summary`、`health_trend`、
`followups`、`alignment`、`pursuits`、`report_refresh_candidates` 等一整
批互不相关的接口全部重新请求一遍。修复方式是给该函数加上
`@st.fragment`（详见 `_render_growth_profile_and_keywords` 函数上方注释）。

因此，**今后新增或修改看板里任何"自成一块、内部有 widget 交互"的板块函数
（渲染 Tab 内某个子区域的 `_render_xxx()` 函数），只要满足以下任一条件，
一律加 `@st.fragment`**：

- 板块内部有按钮/复选框/输入框/表单等会触发交互的 widget；
- 板块自己独立调用后端接口（不依赖外层已经拉取好的数据）；
- 板块跟同一个 Tab 里的其它板块在数据/交互上没有强关联（不需要"点这个
  按钮之后，另一个板块也必须跟着刷新"）。

已经按这个模式做的板块可参考：`_render_growth_health_trend`、
`_render_growth_followups`、`_render_growth_alignment`、
`_render_growth_pursuits`、`_render_growth_report_refresh_candidates`、
`_render_growth_profile_and_keywords`。

写的时候注意两点：

1. Fragment 内部的 `st.rerun()`（Streamlit ≥1.37）默认只重跑该 fragment
   本身，不会变成全页刷新，这正是我们想要的效果——板块内点完按钮直接
   `st.rerun()` 局部刷新即可，不用担心波及其它板块。
2. Fragment 的入参如果来自外层一次性拉取的数据快照（比如
   `diagnostics: dict`），fragment 内部重跑并不会重新拉取这份快照——
   板块自己发起的写操作（比如关键词的增删改）之后数据是新的，但快照里
   跟本板块无关的其它字段会保持外层上次渲染时的旧值，直到外层整体重跑
   （切换 Tab、点顶部刷新按钮等）才会更新。这是可接受的小滞后，写板块
   时心里有数即可，不需要为此额外做同步。

如果某个板块内部逻辑复杂、抛异常风险较高，同时按现有的 `_safe_growth_section`
（或对应 Tab 下的同类兜底函数）包一层，做到"单个板块出错/卡顿只影响它
自己，不拖垮整个 Tab"。

## 相关文件

- `apps/mini_agent_kanban/app.py` — 看板主程序（15 个 Tab）
- `apps/mini_agent_kanban/client.py` — `AgentClient` HTTP 封装
- `apps/mini_agent_kanban/README.md` — 应用自带的简要说明
- `docs/http-api-guide.md` — HTTP API 完整参考
- `docs/artifacts-dashboard-guide.md` — 产出物 Manifest 设计与自动侦测开关详解
- `docs/web-demo-guide.md` — 姊妹应用（纯聊天 Web Demo）
- `docs/multi-user-guide.md`、`docs/autonomous-daemon-design.md`、
  `docs/goal-mode-guide.md`、`docs/embodied-agent-guide.md` — 看板中各功能区背后的机制
- `next_doc/kanban-large-data-pagination-improvement-plan.md` — 本次分页改造的设计文档
- `next_doc/scheduling_unification_and_kanban_visibility_improvement_plan.md`
  — cron 仲裁接入 / priority 排序 / 仲裁状态可见 / recurring 语义合并 /
  "🗓️ 全局日程"Tab 的设计与实现记录
- `next_doc/kanban_perception_gaps_improvement_plan.md` /
  `next_doc/kanban_perception_gaps_implementation_record.md`
  — "⚠️ 系统状态哨兵"面板、LLM 故障转移状态暴露、wiki 隔离区暴露、
  仲裁状态聚合占比的设计与实现记录
- `next_doc/goal_execution_spec_generation_plan.md` /
  `next_doc/goal_execution_spec_generation_implementation_record.md`
  — Goal 执行规范自动生成 + 用户确认机制（模板库/字段级锁定反馈迭代/
  差异高亮/只读探索 Agent 路径/整体关闭判定）的设计与逐阶段实施记录
- `next_doc/kanban_concurrency_control_plan.md` — "🎛️ 并发上限"面板：
  顶栏运行时热改最大并发任务数 / 最大并发 LLM 调用数的设计文档
- `next_doc/kanban_goal_delete_and_bulk_delete_plan.md` — 目标看板"删除
  单个 Goal" / "一键删除所有 Goal"功能的设计文档：级联清理关联 cron job、
  `daemon_run_outputs`/执行规范/执行阶段/调优草案四类外部数据，以及
  `user_output_dir` 用户自定义产出目录的显式保护逻辑
- `src/mini_agent/perception/sentinel.py` — 哨兵聚合面板后端：cron 连续
  失败 / Objective 重试热点 / wiki 隔离区积压 / LLM 故障转移状态 四类
  扫描函数 + `sentinel_summary()`
- `src/mini_agent/perception/daily_snapshot.py` /
  `src/mini_agent/evolution/objective_trend.py` — 方向 D.1 Objective
  完成率每日趋势：通用"每日快照 + 降采样"存储小工具 + 具体的快照计算/
  记录/查询函数

---

*最后更新：2026-08-20*
