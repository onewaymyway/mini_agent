# Mini Agent 看板（Streamlit 版）功能完整清单

> 基于 `apps/mini_agent_kanban/app.py`（8327 行）逐块梳理，作为
> `kanban_react_spa_replacement_plan.md` 分阶段实施计划的依据。
> 每一项功能都标注了对应的后端接口（`src/mini_agent/api/routes.py`，前缀 `/v1` 省略），
> 方便 React 版按同样的口径逐一迁移，不遗漏。

统计口径：登录门禁 1 项 + 侧边栏/全局顶栏 2 大块 + **18 个功能 Tab**，
后端一共约 **175 个 REST 端点** + 3 类 SSE/流式端点。

---

## 0. 登录门禁 / 连接配置（不属于 Tab，贯穿全局）

### 0.1 登录门禁（`--require-login` 开启时生效）
- 账户名+密码登录表单，失败次数限制（防爆破锁定，含每客户端计数）
- 登录成功后写 Cookie/Query，支持退出登录
- 对应：无独立后端端点，账户校验落在 `--users-file` 本地文件，Token 换取后仍走标准 `/v1/*` 鉴权

### 0.2 侧边栏 · 连接配置
- API Base URL 手动输入
- Token 手动输入 / `--auto-token` 自动读取（只读展示）并给出未找到时的排查路径提示
- 健康检查状态提示（已连接 / 无法连接）
- “手动刷新全部”按钮
- “自动刷新”开关（控制状态条与事件流两个局部刷新单元的轮询频率）
- 对应端点：`GET /health`

### 0.3 侧边栏 · 本页面对话 session 绑定
- 下拉选择将“当前浏览器标签页”绑定到某个 session_id（写入 URL query param，支持分享链接、支持多标签页各自绑定不同 session 并行对话）
- 对应端点：`GET /sessions`

### 0.4 顶部状态条（Topbar，跨所有 Tab 常驻）
- Agent 运行状态（idle/busy/…）、当前模型、活跃度展示，`GET /status`
- 排队请求提示（queue depth）
- daemon 正在执行的任务列表展开面板（含“查看并控制”跳转到对应 Tab）
- 调度暂停/恢复控制、调度 gating 详情展开
- 待审批权限请求 / 待回答交互请求 计数提醒（点击展开）
- 系统状态哨兵（Sentinel）异常项摘要，`GET /sentinel/summary`
- 全局待办中心（跨会话 inbox），`GET /inbox`
- 对应端点：`GET /status`、`GET /autonomous/status`、`POST /autonomous/scheduling/pause`、
  `POST /autonomous/scheduling/resume`、`GET /autonomous/gating_history`、
  `GET /permissions/pending`、`GET /interactions/pending`、`GET /sentinel/summary`、`GET /inbox`

---

## Tab 1｜💬 对话（Chat）

- 流式对话主界面：发送消息、增量渲染回复、中断当前回复
- 会话信息展示（模型、session 元信息）
- 最近工具活动展开面板（含待审批权限时自动展开）
- 权限请求 / 交互请求内联审批（允许一次/始终允许/拒绝，回答交互式问题）
- 事件流面板（独立于对话文本，展示原始 Agent 事件时间线，增量拉取+本地缓存上限）
- 对应端点：`POST /chat`、`POST /interrupt`、`GET /history`、`DELETE /history`、
  `GET /stream`、`GET /stream/{turn_id}`、`GET /events`、`GET /turns`、`GET /turns/{turn_id}`、
  `GET /permissions/pending`、`POST /permissions/{req_id}`、
  `GET /interactions/pending`、`POST /interactions/{req_id}`

**（已在 mini_agent_kanban_x 中完成：基础发送/流式增量/中断/历史；
待补：内联权限审批、事件流面板、最近工具活动。）**

---

## Tab 2｜🗂️ 会话管理（Sessions）

- 会话列表（含轮次数、更新时间、当前/绑定/置顶标记）
- 新建会话、恢复会话、删除会话
- 会话置顶（Pin），置顶会话“并排对比”视图（多会话同屏对照）
- 会话详情展开（历史摘要等）
- 会话变化提醒横幅（检测到后台新增/变更会话时提示）
- 认知锚点保存（save_anchor）
- 对应端点：`GET /sessions`、`GET /sessions/{id}`、`POST /sessions/new`、
  `POST /sessions/{id}/resume`、`DELETE /sessions/{id}`、`POST /sessions/{id}/save_anchor`

**（已完成：列表/新建/恢复/删除/详情。待补：置顶+并排对比、变化提醒横幅、认知锚点保存。）**

---

## Tab 3｜📌 目标看板（Goal Backlog / Kanban）

规模最大的 Tab，核心是“目标（Goal）”生命周期管理：

- 新建目标（标题/描述/优先级/是否周期性）
- 目标卡片：编辑标题/描述/优先级、设置/取消周期性（recur/unrecur）、跳过下一周期、
  轻量下一周期、对目标提意见反馈（feedback）
- 执行规范（Execution Spec）：查看、生成、修订（revise，含与上一版 diff 对比）、确认（confirm）、
  收尾检查（close_check）、从模板重新起草
- 执行阶段（Execution Phase）查看/推进/解锁
- 目标执行详情（Objective Execution）：查看步骤明细、取消/暂停/恢复/重试执行、
  编辑单步骤并续跑、重置单步骤、追加执行指导（guidance）、查看单步骤 trace
- 产出物清单（该目标关联的 output manifests）
- 周期诊断（cycle diagnostics）：单目标 + 总览
- 调优草案（tuning proposals）：查看、AI 建议生成、确认、应用、驳回，历史草案
- Objective 完成率趋势图、Cron 健康总览
- 每日融合日报 / 主动推荐 / 决策画像（含完整画像文档展开）
- 对应端点（节选，共 ~35 个）：`GET/POST /goals`、`PATCH /goals/{id}`、
  `POST /goals/{id}/recur|unrecur|skip_next_cycle|lightweight_next_cycle|feedback`、
  `GET /goal_execution_spec_templates`、`GET/POST /goals/{id}/execution_spec*`、
  `GET/POST /goals/{id}/execution_phase*`、
  `GET /goals/{id}/cycle_diagnostics`、`GET /goals/cycle_diagnostics_overview`、
  `POST/GET /goals/{id}/tuning_proposals*`、
  `POST /objectives/{id}/cancel|pause|resume|retry`、
  `POST /objectives/{id}/steps/{i}/edit|reset`、`POST /objectives/{id}/guidance`、
  `GET /objectives/{id}/steps/{i}/trace`、`GET /objectives/completion_trend`、
  `GET /artifacts`（目标关联产出）、`GET /next_actions`、`GET /decision_profile`、
  `GET /digest/daily`、`GET /digest/pending_startup`、`POST /digest/pending_startup/ack`

---

## Tab 4｜🔄 工作流（Workflow）

- 工作流定义查看（YAML）
- 运行面板：选择工作流、填输入参数（含从项目内选择文件路径的选择器）、运行选项（护栏）、发起执行
- 历史执行统计（每个工作流的成功率等）
- 执行详情：按步骤展示状态、可修改步骤定义并续跑、可编辑步骤输出并续跑
- 运行控制：暂停/取消/标记为中断/恢复/审批通过/驳回/提供输入/单步覆盖（override）
- 历史执行记录列表
- 对应端点：`GET /workflows`、`GET /workflows/{name}`、
  `POST /workflows/{name}/steps/{id}/patch`、`POST /workflows/{name}/preview`、
  `GET /workflows/{name}/stats`、`POST /workflows/{name}/run`、
  `GET /workflow_runs`、`GET /workflow_runs/{id}`、`GET /workflow_runs/{id}/events`、
  `POST /workflow_runs/{id}/pause|cancel|mark_interrupted|resume|approve|reject|input`、
  `POST /workflow_runs/{id}/steps/{step_id}/override`

---

## Tab 5｜📁 产出物浏览（Artifacts）

- 按目标/会话浏览产出物清单（manifest）
- 产出文件预览、下载
- 对应端点：`GET /artifacts`、`GET /artifacts/{manifest_id}`、`GET /artifacts/{manifest_id}/file`

## Tab 6｜🖼️ 产出预览（Artifacts Preview）

- 图片/文本类产出的直接内联预览（与 Tab5 的“浏览+下载”互补，偏“快速看内容”场景）
- 复用 Tab5 同一批端点，前端渲染方式不同（内联预览 vs 列表下载）

---

## Tab 7｜🧠 自我状态（Self / Embodied Intelligence）

- 目标卡住统计（goal stuck stats）
- LLM 调用池状态（多 key/多 provider 负载与健康）
- 公平性诊断（fairness diagnostics）
- LLM 调用统计（按天）
- 自我配置查看与在线编辑（config get/patch，按分类展示 + 关键字过滤）
- 自我运行状态总览（self/status）
- 🪞 自我画像 / 能力地图：identity/self_assessment/operating_state、当前
  项目实测能力地图、弱项数量走势、已发现技能目录（新增，见
  `next_doc/streamlit_self_cognition_dashboard_plan.md`）
- 对应端点：`GET /goal_mode/stuck_stats`、`GET /self/llm_pool_status`、
  `GET /self/fairness_diagnostics`、`GET /self/llm_call_stats`、
  `GET/PATCH /self/config`、`GET /self/status`、`GET /self/error_log_stats`、
  `GET /self/portrait`

---

## Tab 8｜🌱 成长顾问（Growth Advisor）

规模第二大的 Tab：

- 成长诊断信息（为什么候选是 0，附详细分布展开）
- 健康度趋势图
- 主动扫描（growth/scan）
- 候选列表：看板拖拽式管理（sortable 分栏）、按主题采纳/忽略排行、成长主题地图
- 单候选详情：调研报告查看/刷新、主题时间线（自绘 SVG timeline）
- 关键词管理：新增/确认/移除/恢复
- 回访提醒（followups）：待回访方向列表、标记回访结果
- 对齐视图（align）：有兴趣但未建目标的方向、一键全部采纳、确认匹配
- 追求中方向（pursuits）：正在自主推进列表、组合摘要、相关方向、饱和度走势、查看素材、
  素材生成
- 报告刷新候选列表
- 画像与关键词（growth profile & keywords）：技术栈/习惯特征展示，过期特征提示，隐藏内置主题
- 对应端点（节选，共 ~30 个）：`GET /growth/summary`、`POST /growth/first_touch_ack`、
  `POST /growth/scan`、`POST /growth/candidates/{id}/{action}`、
  `GET/POST /growth/followups*`、`POST /growth/keywords*`、
  `GET /growth/reports/refresh_candidates`、`GET /growth/reports/{id}`、
  `GET /growth/pursuits*`、`POST /growth/pursuits/{id}/view_material`、
  `GET /growth/align`、`POST /growth/align/adopt_all|confirm_match`、
  `GET /growth/candidates/{id}/timeline`、`POST /growth/candidates/{id}/report/refresh`、
  `POST /growth/candidates/{id}/adopt_goal`、`POST /growth/candidates/{id}/material/generate`、
  `GET /growth/materials/{id}`

---

## Tab 9｜🎓 能力学习 / 人设养成（Capability Learning）

- 新建能力/人设方向
- 方向列表：状态展示、详情展开（含预览效果 / 源码双 Tab 子视图）
- 待回答问题列表（含历史问答：已回答/已忽略/已过期）
- 大纲扩展建议
- 已发布角色一览
- 对应端点（前缀 `/capability`，共 15 个）：`GET/POST /capability/tracks`、
  `GET/PATCH/DELETE /capability/tracks/{id}`、`GET /capability/tracks/{id}/ledger`、
  `GET /capability/questions`、`POST /capability/questions/{id}/answer|dismiss`、
  `GET /capability/suggestions`、`POST /capability/suggestions/{id}/accept|dismiss`、
  `GET /capability/wiki_pages/{id}`、`GET /capability/personas`、
  `POST /capability/personas/{name}/wiki_scopes`、
  `POST /capability/tracks/{id}/persona/draft`、`GET /capability/tracks/{id}/persona/draft`、
  `POST /capability/tracks/{id}/persona/publish`

---

## Tab 10｜🧬 进化提案（Evolution Proposals）

- 提案列表、查看 diff（按文件分组展示，单文件时默认展开）
- 合并（merge）提案
- 对应端点：`GET /evolution/proposals`、`GET /evolution/proposals/{branch}/diff`、
  `POST /evolution/proposals/{branch}/merge`、`GET /evolution/feedback_loop_summary`

---

## Tab 11｜⏰ Cron 任务（Cron Jobs）

- 新建 cron job（表达式、prompt 等）
- 任务列表：上次遗留进度摘要、最近执行记录、编辑任务 Prompt、提意见反馈、
  调整优先级、删除任务
- 立即运行（run）、重置（reset）
- 查看任务专属工作区（workspace）
- 对应端点：`GET/POST /cron/jobs`、`PUT/DELETE /cron/jobs/{id}`、
  `POST /cron/jobs/{id}/run|feedback|reset`、`GET /cron/jobs/{id}/workspace`、
  `GET/PUT /cron/jobs/{id}/prompt`、`GET /cron/jobs/{id}/runs/{run_id}`

---

## Tab 12｜🗓️ 全局日程（Global Schedule）

- 未来 24 小时内到期的 cron job 列表
- 周期性 Goal 下次触发时间列表
- 仲裁状态变化时间线（调度仲裁历史）
- 调度公平性诊断（只读，含逐 objective 明细展开）
- 对应端点：`GET /cron/jobs`（过滤即将触发）、`GET /goals`（周期性过滤）、
  `GET /autonomous/gating_history`、`GET /self/fairness_diagnostics`

---

## Tab 13｜🔌 外部输入网关（External Input Gateway）

- 已注册来源列表
- 路由规则查看（policies.yaml）
- 待处理告警列表（含确认/ack）
- 新颖信号候选：查看、确认、忽略
- 外部知识反馈闭环 P1~P5 五个子面板：候选队列过期巡检、wiki 利用率、阈值自校准、
  外部趋势×能力薄弱点候选、生态定位扫描、月度战略回顾
- 来源健康趋势图
- 最近事件流水
- 归档查询
- 对应端点（~15 个）：`GET /external_input/sources`、`POST /external_input/sources/reload`、
  `GET /external_input/policies|events|alerts|health_history`、
  `GET /external_input/novelty_candidates`、
  `POST /external_input/novelty_candidates/{id}/confirm|dismiss`、
  `GET /archive/query`、`GET /evolution/feedback_loop_summary`

---

## Tab 14｜🔔 关注与通知（Watchlist & Notification）

- 关注对象列表（watchlist.yaml）
- 分级汇报规则（report_tiers.yaml）
- 待处理汇报列表（展开详情，支持分类筛选 + 批量标记已读）
- 通知发送记录
- 对应端点：`GET /notification/watchlist`、`GET /notification/report_tiers`、
  `GET /notifications/pending`（支持 `category` 参数）、
  `GET /notifications/pending/categories`、
  `POST /notifications/pending/{id}/ack`、
  `POST /notifications/pending/batch_ack`、`GET /notification/dispatch_log`

---

## Tab 15｜⚙️ 配置管理（Config）

- 按分类展示配置项，支持关键字过滤，展示“已自定义”徽标
- 按字段类型渲染对应控件（文本/数字/布尔/选择等）并保存
- 对应端点：`GET /self/config`、`PATCH /self/config`

---

## Tab 16｜🔧 诊断信息（Diagnostics）

- 系统级诊断信息展示（只读）
- 对应端点：`GET /diagnostics`

---

## Tab 17｜🧪 混合执行（Hybrid Exec：脚本 / LLM / Agent）

- 混合执行任务列表，按状态分组展开，展示任务详情
- 对应端点：`GET /hybrid_exec/summary`

---

## Tab 18｜📛 错误日志统计（Error Log Stats）

- 按异常类型（exc_type）分布统计
- 按发生位置（where）Top N 分布统计
- 原始统计 JSON 展开
- 对应端点：`GET /self/error_log_stats`

---

## 附：与本次 SPA 迁移无直接映射、但被多个 Tab 复用的通用能力

- 文件系统浏览/读写（`GET/POST /fs/*`）：目前主要在“工作流运行面板的文件选择器”“配置”等场景内嵌调用，
  没有独立 Tab，React 版规划为独立的 Files 页面（P3），同时被其它页面以组件形式复用
- 用户管理（`/users*`）：旧版没有独立 Tab，是通过命令行工具 `manage_users.py` 管理，
  **不在网页 UI 中**；React 版按用户提出的分阶段计划，仍规划一个可选的 Users 管理页（P7），
  作为对旧版能力的增强而非 1:1 迁移
- Diff 展示（`diff_view.py`）：被“进化提案”“目标执行规范修订”等多处复用，
  React 版对应 `components/DiffView.tsx`，用 `react-diff-viewer-continued` 实现

---

## 功能规模小结（用于评估迁移工作量）

| 维度 | 数量 |
|---|---|
| 顶层 Tab | 18 |
| 登录/侧边栏/顶栏 全局模块 | 4 大块 |
| 后端 REST 端点 | ~175 个（`/v1/*`） |
| SSE/流式端点 | 3 类（`/stream`、`/stream/{turn_id}`、事件轮询 `/events`） |
| 涉及“可编辑表单+审批流”的复杂交互场景 | 目标执行规范、调优草案、cron 任务、进化提案、权限/交互审批、配置管理 等 6 处 |
| 含自绘图表（非简单表格）的面板 | 目标完成率趋势、Cron 健康总览、成长健康度趋势、成长主题时间线（自绘 SVG）、饱和度走势、来源健康趋势、错误日志分布 等 7 处 |

这份清单是 `next_doc/kanban_react_spa_replacement_plan.md` 第 5 节分阶段计划的直接依据：
每个阶段对应清单里 2~4 个 Tab，确保"计划里的功能=旧看板的功能"，不会迁移一半漏掉大块能力。
