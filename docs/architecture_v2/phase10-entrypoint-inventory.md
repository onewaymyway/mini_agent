# Phase 10 Sprint 10-1：对外入口盘点

> 对应 `next_doc/refactor_plan/11-phase10-legacy-decommission-plan.md`
> Sprint 10-1 三项任务里的“梳理对外入口”与“更新映射表（核对）”，以及对
> “统一入口”一项的可行性评估。**本文件由两部分组成**：第一至六节是人工
> 分析，附录 A/B 及汇总表由 `scripts/entrypoint_inventory.py` 生成（勿手改，
> 重新生成即可）。
>
> **结论先行**：Sprint 10-1 **未完成**——盘点与核对已完成，但“统一入口”
> 触发止损、验收标准（HTTP 文档术语）未达成，且 Phase 10 的前置条件
> （“Phase 1-9 全部完成”）本身尚未满足。详见第五、六节与
> `11-phase10-legacy-decommission-plan.md` 的“变更记录”。

## 一、方法与局限

对每个入口（REPL 斜杠命令分支 / HTTP 路由处理函数），静态收集其调用链上出现的
名字（最多展开 3 跳），与三类标记比对：§43 六条映射对应的**真实**旧类/
旧模块（代码里没有 `GoalManager`，README 里那是举例名，此处只用真实存在的）、
`AgentRuntime`、`mini_agent.core|actions`。

**这是下限估计，不是证明。**
- “命中旧类”是确凿的（名字确实出现在调用链上）；
- “未命中”（kind=`none`）**不等于**已收敛：经 `self.agent.xxx` / `http_server.xxx`
  这类实例属性间接到达旧模块的路径、超过 3 跳的路径都看不到。
  例如 `ObjectiveExecutor` 在台账里明确是被多处使用的（唯一实例化点在
  `api/server.py`），但静态扫描到的入口直接触达数为 0 / 0
  ——正是因为路由处理函数通过属性访问它，而不是引用类名。
- 脚本自身有测试（`tests/test_phase10_sprint10_1_entrypoint_inventory.py`），
  包括用**独立的逐行正则**核对 HTTP 路由数（避免 AST 抽取悄悄漏项）。

## 二、核心发现

1. **走 `AgentRuntime` 的入口极少**：CLI 斜杠命令 59 条中仅 1 条
   （`/goal`，且只是“发起新 Goal”这一条路径，`/goal resume` 不在内，见台账）；
   HTTP 路由 308 条中 **0 条**。该 `/goal` 的分类是 `mixed`：
   `AgentRuntime` 内部仍是同一个旧 `GoalRunner`，并非重写。
   （cron 的 `message` 模式经 `AgentRuntime` 分发是 daemon 内部路径、默认关闭，
   不是对外入口，未计入。）
2. **直接触达旧类的入口**：CLI 8 条、HTTP 86 条
   （占 HTTP 总数 28%）。分布见汇总表；按 §43 映射：
   Goal 66 条 HTTP、Advisor 35 条 HTTP 最多。
3. **这些入口大多不是“执行循环”**：触达旧类的 86 条 HTTP 路由里
   52 条是非 GET，但逐条看几乎全是对 Goal 待办库（`GoalBacklog`）与
   growth 配置的增删改（`/v1/goals/*`、`/v1/growth/*`、`/v1/directions/*`），
   **没有一条是“跑一轮 agent 循环”**。其余为只读聚合视图。这直接决定了第五节的结论。
4. **绕过 HTTP 的入口存在**：`weixin_bot.py` 在进程内直接构造 `Agent`（不经 HTTP、
   不经 `AgentRuntime`）；`apps/weixin_plugin`、`apps/mini_agent_kanban` 同时存在
   HTTP 调用与对 `mini_agent` 的进程内导入。这些入口不受 API 层收敛的覆盖。

## 三、§43 六条映射逐条核对（Sprint 10-1 第三项任务）

“落地”的判据：目标概念在真实调用路径上被使用，而不只是模块存在。

| 映射 | 状态 | 证据 | 入口直接触达（CLI / HTTP） |
|---|---|---|---|
| 旧 Goal → Adapter | **部分落地** | `GoalAdapter` 与 `GoalState` 已用于 `goal_mode/runner.py` 的 `run()`/`_finish()`（台账：部分迁移）；`goal_mode/executor.py` 未开始；`goal_backlog.py` 暂缓（跨子系统 inbound=22，远超止损阈值） | 6 / 66 |
| 旧 Memory → Adapter | **部分落地** | `MemoryBackend → MemorySnapshot` 仅单向且只转换 `entry_count`/`backend_kind` 两个字段；`HistoryManager` 内部逻辑未改（台账）（2026-09-29 更新：现为三个字段，新增 `scope`，并覆盖全局记忆后端；仍仅单向，见 03 号文档"十二"） | 1 / 2 |
| 旧 Workflow → Capability | **部分落地，且目标概念本身未实装** | 实际落地的是 `ActionExecutor` 的 `type="workflow"` 旁路（Action，不是 Capability）；`core/capability.py::CapabilityState` 是无字段空占位，**除 `core/__init__.py` 的再导出与占位测试外，没有任何生产代码引用**（已 grep 核实） | 1 / 0 |
| 旧 Scheduler → Runtime adapter | **部分落地（opt-in，默认关）** | `cron_job_runner` 的 `message` 模式在 `cron.runtime_dispatch_enabled=True` 时经 `AgentRuntime`，默认 `False`；`goal_cycle`/`AutonomousLoop` 经 Sprint 8-5 评估为高风险、移出 Phase 8 | 1 / 6 |
| 旧 Advisor → Decision policy | **未落地** | `DecisionEngine`（`cognition/decision.py`，Phase 7）是**新增**模块，**没有任何生产代码调用它**：`runtime/runtime.py` 里只有注释说明 `plan/simulate/decide` 三步被显式跳过，`core/simulation.py` 里只是文档字符串提及（已 grep 核实）；旧 `next_action_advisor`/`growth_advisor` 也**完全不引用 `cognition/`**，台账里没有这两个模块的行 | 5 / 35 |
| 旧 Objective → Goal internal step | **未落地** | `evolution/objective_executor.py` 台账状态“未开始”（Sprint 5.0.5 评估移交 Phase 6 后仍未开始）；Phase 5 盘点结论是它与 `GoalBacklog` 是同一套子系统 | 静态扫描 0 / 0（**低估**，见第一节） |

**观察**：六条里没有任何一条是“完全落地”。其中“Advisor → Decision policy”是最
被高估的一条——Phase 7 交付了新的决策引擎，但它目前没有任何生产调用方，旧 Advisor
也没有接过去：这条映射现在只有“目标端的新模块”，还没有发生任何“迁移”。

> **更新（2026-09-28，Phase 10 A3；上表为 Sprint 10-1 时点快照，未改写）**：“没有任何生产代码调用 `DecisionEngine`”自 A3 起不再成立——`runtime/decision_stage.py` 在 `goal_mode.runtime_decision_enabled=True`（默认关闭）时调用它，但只记录决策、不执行；旧 `next_action_advisor`/`growth_advisor` 仍未接入，这条映射本身仍是“未落地”。见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第十节。

## 四、验收标准基线：用户可见文本的术语

验收标准原文：*所有对外接口的文档/帮助信息里，只出现 Self/World/Experience/Goal/
Capability/Action/Simulation/Runtime 这套术语，不再直接暴露 `GoalManager/
ObjectiveExecutor/CronScheduler/...` 等旧类名给最终用户*。可度量的部分是**旧类名**。

| 表面 | 字符串数 | 命中旧类名 | 判定 |
|---|---|---|---|
| 斜杠命令菜单（`ui/terminal.py::_COMMANDS`） | 47 | 0 | 达标 |
| `--help` / `/help`（`cli/parser.py`） | 75 | 0 | 达标 |
| HTTP OpenAPI 文档（路由 docstring/summary） | 267 | **32**（分布在 26 条路由说明里） | **未达标** |

`/docs`、`/redoc`、`/openapi.json` 在 `api/server.py` 里是显式开启的，所以这
26 条路由说明对 API 使用者可见。命中的旧类名及位置见下方生成表。
这些说明大多是开发期备忘（含 `[xxx_plan.md P4]` 这样的内部计划文档引用），并非
面向使用者写的。

**另有两点不计入判定、但需要项目所有者知晓**：
- **产品概念词**（不是类名）在 HTTP 文档里大量出现：`Cron`×96、
  `Objective`×62、`Workflow`×31、
  `Scheduler`×4；且斜杠命令名本身就是 `/workflow`、`/cron`、
  `/goals`、`/growth`、`/next`。若验收口径包含这些词，意味着**用户可见命令改名**，
  是破坏性的产品决策，不是文档润色。
- **`capability` 重名**：用户可见的 `/capability`、`/v1/capability/*` 指的是
  “人设能力自主学习（CapabilityTrack）”这个**已有产品功能**
  （见 `cli/commands/capability_cmd.py`、`api/capability_routes.py`），与架构里的
  Capability 领域对象是两回事。验收标准要求对外只出现 “Capability” 这套术语，
  但这个词在对外接口里已经被另一个含义占用了。

## 五、对“统一入口”任务的评估（止损触发）

任务原文：*未收敛的接口，改为统一调用 `AgentRuntime`，旧模块降级为其内部实现细节*。

**不建议按字面执行**，理由：

1. **对多数入口是范畴错误。** `AgentRuntime.run_once()` 是“独占一个 Agent、同步
   驱动多轮”的**执行循环**。入口按性质分三类：
   - **发起执行类**（聊天、`/goal`、cron 触发）：`/goal` 新目标路径已接入；聊天与
     Objective/goal_cycle 走的是“共享主 Agent + InputQueue”模型，Phase 8 Sprint 8-5
     已评估为高风险（需要新增“为 Goal cycle 构造独占 Agent”的产品层决策，会把
     Objective 从“与用户共享上下文”变成“隔离执行”）；
   - **状态管理类**（第二节第 3 点：对 `GoalBacklog`/growth 配置的增删改）：收敛
     目标应是 Goal 领域对象 / `StateManager`，而不是 `AgentRuntime`；而
     `goal_backlog.py` 本身已因耦合过高被**暂缓**；
   - **只读视图类**（GET 聚合状态）：不该走 `run_once()`。
2. **会制造“表面收敛”。** 在底层未迁移前，把文档措辞或命令名改成新术语，会让文档
   声称一个尚不存在的事实（例如把 `AutonomousLoop` 的说明改写成“Runtime”，但它
   仍是旧类）。这违反执行规范“文档滞后不可接受，文档先行也不可接受”。
3. **与既有止损评估一致，不是新发现的孤立问题**：Sprint 8-5、Sprint 5.0.5 已分别
   对 Objective/goal_cycle 与 GoalBacklog 给出“暂缓 / 不建议接入”。

按 `12-execution-and-doc-sync-norms.md` 第四节，已在 Phase 10 文档写入“变更记录”，
**未修改任务表**（规范要求留痕后才能改，且需项目所有者确认）。

## 六、需要项目所有者决定的事项

- **D1 — 重新界定“统一入口”的范围。** 建议：“发起执行类”只做 Phase 8 已评估可接的
  （已完成）；“状态管理类”推迟到 `goal_backlog` 立项之后；“只读视图类”不动。
- **D2 — 术语验收口径。** 仅指旧类名（当前 HTTP 文档 26 条未达标，可在不
  改任何行为的前提下逐条改写 docstring），还是也包括概念词与命令名（破坏性改名）？
- **D3 — `capability` 重名**：架构 Capability 与“人设能力学习”谁改名？
- **D4 — Phase 10 前置条件。** 计划写明前置为“Phase 1-9 全部完成，新架构八大概念已
  在真实场景跑通完整闭环”。现状：Phase 9 完成标志第 3、4 条待在真实仓库核对；
  `AgentRuntime` 的 `learn` 步骤仍空、`DeployRecord` 未持久化；台账里 `goal_mode/
  executor.py`、`objective_executor.py`、`orchestrator/*` 未开始，`goal_backlog.py`
  暂缓。建议先补齐或明确豁免，再启动 Sprint 10-2。
- **D5 — Sprint 10-2 的止损条件已实际成立**：该计划写明“Adapter 覆盖不完整时不要强行
  移动目录”。依据是台账中上述“未开始/暂缓”项，以及本次盘点中 86
  条 HTTP 路由与 8 条 CLI 命令直接触达旧类。**本 Sprint 未开始 10-2，
  也不建议在 D1/D4 有结论前开始。**

---

> **B3 更新（2026-09-28）**：下文附录是 Sprint 10-1 时的快照，未重新生成。其中 `/v1/objectives/*` 现已改名为 `/v1/goal_steps/*`（旧名为隐藏别名），并新增 `GET /v1/goals/{goal_id}/steps`；重新运行脚本 HTTP 路由数为 319（含 9 个隐藏别名）。详见 `next_doc/refactor_plan/13-…` 第十节。

## 附录（由 `scripts/entrypoint_inventory.py` 生成）

<!-- 由 scripts/entrypoint_inventory.py 生成，勿手改；重新生成见该脚本 docstring -->

静态分析深度 `MAX_DEPTH=3`，结果为**下限估计**（见脚本 docstring）。

### 汇总

| 入口类别 | 总数 | runtime | mixed | legacy | none |
|---|---|---|---|---|---|
| CLI 斜杠命令 | 61 | 0 | 1 | 9 | 51 |
| HTTP 路由 | 309 | 0 | 0 | 87 | 222 |

### §43 六条映射：入口直接触达旧类的情况

| 映射 | 触达的 CLI 命令数 | 触达的 HTTP 路由数 |
|---|---|---|
| 旧 Goal → Adapter | 8 | 67 |
| 旧 Memory → Adapter | 1 | 2 |
| 旧 Workflow → Capability | 1 | 0 |
| 旧 Scheduler → Runtime adapter | 1 | 6 |
| 旧 Advisor → Decision policy | 7 | 35 |
| 旧 Objective → Goal internal step | 0 | 0 |

### 其它对外入口

| 入口 | 接入方式 | 依据 |
|---|---|---|
| `weixin_bot.py` | 进程内直接构造 Agent（不经 HTTP、不经 AgentRuntime） | import: mini_agent.agent, mini_agent.config, mini_agent.permissions, mini_agent.skills |
| `apps/android_companion_app` | HTTP 客户端（经 `/v1/*` 接口） | /v1/perception/report |
| `apps/browser_extension_example` | HTTP 客户端（经 `/v1/*` 接口） | /v1/perception/report |
| `apps/weixin_plugin` | 同时存在 HTTP 调用（`/v1/*`）与进程内导入 `mini_agent` | /v1/chat, /v1/history, /v1/permissions/pending, /v1/turns/{turn_id}, /v1/users; import: mini_agent_client |
| `apps/mini_agent_kanban` | 同时存在 HTTP 调用（`/v1/*`）与进程内导入 `mini_agent` | /v1/artifacts, /v1/autonomous/gating_history, /v1/autonomous/status, /v1/chat, /v1/cron/jobs/{job_id}, /v1/evolution/proposals …; import: mini_agent.evolution.cron_scheduler, mini_agent.evolution.cycle_patrol, mini_agent.perception.cycle_tuning |
| `apps/mini_agent_kanban_x` | HTTP 客户端（经 `/v1/*` 接口） | /v1/diagnostics, /v1/events, /v1/stream, /v1/stream/{turn_id}, /v1/users |

### 用户可见字符串的术语扫描

| 表面 | 字符串数 | 命中旧类名（验收判定项） | 产品概念词计数（仅供参考） |
|---|---|---|---|
| cli_menu | 47 | 0 | Workflow×1 |
| cli_args | 75 | 0 | - |
| http_docs | 267 | 32 | Cron×96, Objective×62, Scheduler×4, Workflow×31 |

命中旧类名的用户可见字符串：

| 表面 | 位置 | 旧类名 | 文本节选 |
|---|---|---|---|
| http_docs | `src/mini_agent/api/routes.py:3846` | `AutonomousLoop` | GET /v1/self/execution_model_status — [daemon_execution_model_and_scheduler_heartbeat_improvement_pl |
| http_docs | `src/mini_agent/api/routes.py:3846` | `ObjectiveExecutor` | GET /v1/self/execution_model_status — [daemon_execution_model_and_scheduler_heartbeat_improvement_pl |
| http_docs | `src/mini_agent/api/routes.py:3846` | `ObjectivePersistentRunner` | GET /v1/self/execution_model_status — [daemon_execution_model_and_scheduler_heartbeat_improvement_pl |
| http_docs | `src/mini_agent/api/routes.py:4052` | `UnifiedTaskScheduler` | GET /v1/self/scheduling_overview — [goal_cron_unified_scheduler_improvement_plan.md P4] 只读聚合视图：Goal  |
| http_docs | `src/mini_agent/api/routes.py:4337` | `UnifiedTaskScheduler` | GET /v1/self/unified_scheduler_preview — [goal_cron_unified_scheduler_improvement_plan.md P5 第 1-2 步 |
| http_docs | `src/mini_agent/api/routes.py:4793` | `ObjectiveExecutor` | POST /v1/self/task_concurrency — [看板"🎛️ 并发上限"面板] 运行时热改 Objective/Goal 通道、Cron 通道各自能同时执行几个任务，效果就是顶栏 " |
| http_docs | `src/mini_agent/api/routes.py:4871` | `GoalBacklog` | Self（主自我）的状态总览：GoalBacklog、自主活动摘要（含最近的 session_crashed 通知）、SessionAgentPool 概况。  注意：必须从 request.app. |
| http_docs | `src/mini_agent/api/routes.py:5271` | `AutonomousLoop` | GET /v1/autonomous/status  返回 daemon 自主执行的实时状态：   - autonomy_level：当前档位（passive/maintenance/autonomo |
| http_docs | `src/mini_agent/api/routes.py:5271` | `GoalBacklog` | GET /v1/autonomous/status  返回 daemon 自主执行的实时状态：   - autonomy_level：当前档位（passive/maintenance/autonomo |
| http_docs | `src/mini_agent/api/routes.py:5271` | `ObjectiveExecutor` | GET /v1/autonomous/status  返回 daemon 自主执行的实时状态：   - autonomy_level：当前档位（passive/maintenance/autonomo |
| http_docs | `src/mini_agent/api/routes.py:5439` | `AutonomousLoop` | POST /v1/autonomous/scheduling/pause body（可选）：{"reason": "..."}  [看板"停止调度"功能] 全局暂停自动调度：AutonomousLoo |
| http_docs | `src/mini_agent/api/routes.py:5484` | `AutonomousLoop` | POST /v1/autonomous/scheduling/resume  [看板"停止调度"功能] 撤销暂停，AutonomousLoop.tick() 从下一次调用起 恢复正常按 autonom |
| http_docs | `src/mini_agent/api/routes.py:5554` | `GoalBacklog` | GET /v1/goals — 返回完整的 GoalBacklog 视图。 |
| http_docs | `src/mini_agent/api/routes.py:5640` | `GoalBacklog` | POST /v1/goals/nodes — [goal_tree_system_plan.md §4.5] 通用节点创建 入口，对应 `GoalBacklog.add_node()`，支持任意层级（ |
| http_docs | `src/mini_agent/api/routes.py:5724` | `GoalBacklog` | POST /v1/goals/{node_id}/candidates/{candidate_id}/reject — [goal_tree_system_plan.md §4.4/§4.5] 忽略一 |
| http_docs | `src/mini_agent/api/routes.py:5724` | `GoalTreeDecomposer` | POST /v1/goals/{node_id}/candidates/{candidate_id}/reject — [goal_tree_system_plan.md §4.4/§4.5] 忽略一 |
| http_docs | `src/mini_agent/api/routes.py:5764` | `GoalBacklog` | POST /v1/goals/{node_id}/reparent — [goal_tree_system_plan.md §4.4 阶段四遗留项] 把节点重新挂载到另一个父节点下，对应看板"🌳 目标 |
| http_docs | `src/mini_agent/api/routes.py:5896` | `next_action_advisor` | GET /v1/goals/next_steps?node_id=... — [goal_tree_research_and_action_recommendation_plan.md §4.6 阶段 |
| http_docs | `src/mini_agent/api/routes.py:6383` | `GoalBacklog` | DELETE /v1/goals/{goal_id} — [看板目标看板删除功能] 彻底删除一个 Goal（及其全部子 Objective），并级联清理该 Goal 关联的一切外部数据：    1.  |
| http_docs | `src/mini_agent/api/routes.py:6443` | `GoalBacklog` | DELETE /v1/goals — [看板"一键删除所有目标"功能] 遍历当前 GoalBacklog 里全部 `level == "goal"` 的节点，逐个走 `_cascade_delete_ |
| http_docs | `src/mini_agent/api/routes.py:7439` | `ObjectiveExecutor` | POST /v1/objectives/{execution_id}/cancel — 终止一个正在运行的 Objective 执行：立即释放并发槽位，不再重试；对应 GoalNode.status  |
| http_docs | `src/mini_agent/api/routes.py:7708` | `ObjectiveExecutor` | GET /v1/objectives/{execution_id}/steps/{step_index}/trace  [看板与自主性改进方案 Track E] 返回某个 step 实际执行过程中的完 |
| http_docs | `src/mini_agent/api/routes.py:7919` | `ObjectiveExecutor` | GET /v1/inbox — 跨所有 session 聚合"待办列表"：   - pending 权限审批请求（不再局限于"当前最近活跃 session"，而是     遍历 SessionAgen |
| http_docs | `src/mini_agent/api/routes.py:9048` | `CronScheduler` | DELETE /v1/cron/jobs/{job_id} — 彻底删除一个 cron job。  系统内置 job（id 以 "sys:" 开头）不可删除，只能禁用——与 CronScheduler |
| http_docs | `src/mini_agent/api/routes.py:10685` | `growth_advisor` | POST /v1/growth/candidates/{id}/accept\|dismiss — 看板上对单个候选的 显式反馈动作，写入 GrowthFeedbackLedger（供后续置信度调优参考 |
| http_docs | `src/mini_agent/api/routes.py:11025` | `CronScheduler` | GET /v1/growth/pursuits — [growth_advisor_autonomy_deepening_plan.md 方向 D1] 聚合"哪些方向正在被自主持续调研"：已采纳且关联 |
| http_docs | `src/mini_agent/api/routes.py:11025` | `GoalBacklog` | GET /v1/growth/pursuits — [growth_advisor_autonomy_deepening_plan.md 方向 D1] 聚合"哪些方向正在被自主持续调研"：已采纳且关联 |
| http_docs | `src/mini_agent/api/routes.py:11156` | `GoalBacklog` | POST /v1/growth/pursuits/{goal_id}/view_material — [growth_advisor_ideal_advisor_gap_and_roadmap_pla |
| http_docs | `src/mini_agent/api/routes.py:11224` | `growth_advisor` | POST /v1/growth/align/adopt_all — [growth_advisor_autonomy_ deepening_plan.md 方向 A3] 批量落地对齐分析里"有兴趣但没 |
| http_docs | `src/mini_agent/api/routes.py:11454` | `GoalBacklog` | POST /v1/growth/candidates/{id}/adopt_goal — 把一个候选"落地"成 GoalBacklog 里的一个 Goal 节点，交给 Goal/Cron 体系持续推进 |
| http_docs | `src/mini_agent/api/routes.py:11496` | `GoalBacklog` | POST /v1/persona_learning/tracks/{track_id}/topics/{topic_id}/adopt_goal —— [next_doc/initiative_sys |
| http_docs | `src/mini_agent/api/persona_candidate_routes.py:146` | `growth_advisor` | 忽略一条候选，`reason` 复用 growth_advisor 的 DISMISS_REASON_* 常量语义（不强制传值，默认记为 unspecified）。 |

### 附录 A：CLI 斜杠命令逐条

| 入口 | 分类 | 触达的旧类 | 触达 core/actions | 位置 |
|---|---|---|---|---|
| `/agent` | legacy | GoalBacklog, GoalTreeDecomposer, next_action_advisor | - | `src/mini_agent/cli/repl.py:593` |
| `/agent_value_profile` | none | - | - | `src/mini_agent/cli/repl.py:557` |
| `/agents` | none | - | - | `src/mini_agent/cli/repl.py:505` |
| `/behavior` | none | - | - | `src/mini_agent/cli/repl.py:317` |
| `/capability` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/cli/repl.py:579` |
| `/cc` | none | - | - | `src/mini_agent/cli/repl.py:495` |
| `/clear` | none | - | - | `src/mini_agent/cli/repl.py:301` |
| `/commit-guard` | none | - | - | `src/mini_agent/cli/repl.py:524` |
| `/compact` | none | - | - | `src/mini_agent/cli/repl.py:367` |
| `/compact_continue` | none | - | - | `src/mini_agent/cli/repl.py:370` |
| `/concurrency` | none | - | - | `src/mini_agent/cli/repl.py:495` |
| `/cron` | legacy | GoalBacklog | - | `src/mini_agent/cli/repl.py:606` |
| `/debug` | none | - | - | `src/mini_agent/cli/repl.py:587` |
| `/decision_profile` | none | - | - | `src/mini_agent/cli/repl.py:548` |
| `/digest` | none | - | - | `src/mini_agent/cli/repl.py:536` |
| `/ensemble` | none | - | - | `src/mini_agent/cli/repl.py:498` |
| `/evolution` | none | - | - | `src/mini_agent/cli/repl.py:527` |
| `/evolve` | none | - | - | `src/mini_agent/cli/repl.py:530` |
| `/goal` | mixed | GoalRunner, GoalStateStore | - | `src/mini_agent/cli/repl.py:373` |
| `/goals` | legacy | CronScheduler, GoalBacklog, GoalTreeDecomposer, next_action_advisor | - | `src/mini_agent/cli/repl.py:597` |
| `/growth` | legacy | GoalBacklog, MemoryStore, growth_advisor | - | `src/mini_agent/cli/repl.py:574` |
| `/help` | none | - | - | `src/mini_agent/cli/repl.py:298` |
| `/hooks` | none | - | - | `src/mini_agent/cli/repl.py:515` |
| `/memory` | none | - | - | `src/mini_agent/cli/repl.py:379` |
| `/model` | none | - | - | `src/mini_agent/cli/repl.py:361` |
| `/next` | legacy | growth_advisor, next_action_advisor | - | `src/mini_agent/cli/repl.py:544` |
| `/notepad` | none | - | - | `src/mini_agent/cli/repl.py:489` |
| `/persona-learning` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/cli/repl.py:579` |
| `/persona_learning` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/cli/repl.py:579` |
| `/plan` | none | - | - | `src/mini_agent/cli/repl.py:486` |
| `/platform` | none | - | - | `src/mini_agent/cli/repl.py:518` |
| `/profile` | none | - | - | `src/mini_agent/cli/repl.py:383` |
| `/prompts` | none | - | - | `src/mini_agent/cli/repl.py:477` |
| `/provider` | none | - | - | `src/mini_agent/cli/repl.py:502` |
| `/proxy` | none | - | - | `src/mini_agent/cli/repl.py:511` |
| `/quarantine` | none | - | - | `src/mini_agent/cli/repl.py:521` |
| `/raw-output` | none | - | - | `src/mini_agent/cli/repl.py:345` |
| `/raw_output` | none | - | - | `src/mini_agent/cli/repl.py:345` |
| `/rawoutput` | none | - | - | `src/mini_agent/cli/repl.py:345` |
| `/reasoning` | none | - | - | `src/mini_agent/cli/repl.py:353` |
| `/recall` | none | - | - | `src/mini_agent/cli/repl.py:492` |
| `/reload` | none | - | - | `src/mini_agent/cli/repl.py:320` |
| `/retry` | none | - | - | `src/mini_agent/cli/repl.py:305` |
| `/role` | none | - | - | `src/mini_agent/cli/repl.py:508` |
| `/rollback` | none | - | - | `src/mini_agent/cli/repl.py:308` |
| `/self_narrative` | none | - | - | `src/mini_agent/cli/repl.py:569` |
| `/session` | none | - | - | `src/mini_agent/cli/repl.py:480` |
| `/sessions` | none | - | - | `src/mini_agent/cli/repl.py:480` |
| `/show-reasoning` | none | - | - | `src/mini_agent/cli/repl.py:353` |
| `/show_reasoning` | none | - | - | `src/mini_agent/cli/repl.py:353` |
| `/skill` | none | - | - | `src/mini_agent/cli/repl.py:314` |
| `/skills` | none | - | - | `src/mini_agent/cli/repl.py:311` |
| `/stats` | none | - | - | `src/mini_agent/cli/repl.py:337` |
| `/tasks` | none | - | - | `src/mini_agent/cli/repl.py:483` |
| `/turn-judge` | none | - | - | `src/mini_agent/cli/repl.py:358` |
| `/turn_judge` | none | - | - | `src/mini_agent/cli/repl.py:358` |
| `/turnjudge` | none | - | - | `src/mini_agent/cli/repl.py:358` |
| `/user_signal_profile` | none | - | - | `src/mini_agent/cli/repl.py:563` |
| `/verbose` | none | - | - | `src/mini_agent/cli/repl.py:340` |
| `/wiki` | none | - | - | `src/mini_agent/cli/repl.py:533` |
| `/workflow` | legacy | WorkflowGenerator, WorkflowRunner, WorkflowStore | - | `src/mini_agent/cli/repl.py:376` |

### 附录 B：HTTP 路由逐条

| 入口 | 分类 | 触达的旧类 | 触达 core/actions | 位置 |
|---|---|---|---|---|
| `GET /` | none | - | - | `src/mini_agent/api/server.py:992` |
| `GET /personas` | none | - | - | `src/mini_agent/api/capability_routes.py:330` |
| `POST /personas/{persona_name}/wiki_scopes` | none | - | - | `src/mini_agent/api/capability_routes.py:348` |
| `GET /questions` | none | - | - | `src/mini_agent/api/capability_routes.py:259` |
| `POST /questions/{question_id}/answer` | none | - | - | `src/mini_agent/api/capability_routes.py:265` |
| `POST /questions/{question_id}/dismiss` | none | - | - | `src/mini_agent/api/capability_routes.py:276` |
| `GET /suggestions` | none | - | - | `src/mini_agent/api/capability_routes.py:293` |
| `POST /suggestions/{suggestion_id}/accept` | none | - | - | `src/mini_agent/api/capability_routes.py:299` |
| `POST /suggestions/{suggestion_id}/dismiss` | none | - | - | `src/mini_agent/api/capability_routes.py:311` |
| `GET /tracks` | none | - | - | `src/mini_agent/api/capability_routes.py:126` |
| `POST /tracks` | none | - | - | `src/mini_agent/api/capability_routes.py:132` |
| `POST /tracks/refresh_all` | none | - | - | `src/mini_agent/api/capability_routes.py:176` |
| `DELETE /tracks/{track_id}` | none | - | - | `src/mini_agent/api/capability_routes.py:166` |
| `GET /tracks/{track_id}` | none | - | - | `src/mini_agent/api/capability_routes.py:147` |
| `PATCH /tracks/{track_id}` | none | - | - | `src/mini_agent/api/capability_routes.py:156` |
| `GET /tracks/{track_id}/ledger` | none | - | - | `src/mini_agent/api/capability_routes.py:186` |
| `POST /tracks/{track_id}/outline/apply_revision` | none | - | - | `src/mini_agent/api/capability_routes.py:212` |
| `POST /tracks/{track_id}/outline/revise` | none | - | - | `src/mini_agent/api/capability_routes.py:198` |
| `POST /tracks/{track_id}/outline/topics` | none | - | - | `src/mini_agent/api/capability_routes.py:222` |
| `DELETE /tracks/{track_id}/outline/topics/{topic_id}` | none | - | - | `src/mini_agent/api/capability_routes.py:247` |
| `PATCH /tracks/{track_id}/outline/topics/{topic_id}` | none | - | - | `src/mini_agent/api/capability_routes.py:234` |
| `GET /tracks/{track_id}/persona/draft` | none | - | - | `src/mini_agent/api/capability_routes.py:447` |
| `POST /tracks/{track_id}/persona/draft` | none | - | - | `src/mini_agent/api/capability_routes.py:394` |
| `POST /tracks/{track_id}/persona/publish` | none | - | - | `src/mini_agent/api/capability_routes.py:476` |
| `GET /v1/archive/query` | none | - | - | `src/mini_agent/api/routes.py:8676` |
| `GET /v1/artifacts` | none | - | - | `src/mini_agent/api/routes.py:5196` |
| `GET /v1/artifacts/{manifest_id}` | none | - | - | `src/mini_agent/api/routes.py:5212` |
| `GET /v1/artifacts/{manifest_id}/file` | none | - | - | `src/mini_agent/api/routes.py:5229` |
| `GET /v1/async_jobs/latest/by_key` | none | - | - | `src/mini_agent/api/routes.py:555` |
| `GET /v1/async_jobs/{job_id}` | none | - | - | `src/mini_agent/api/routes.py:537` |
| `GET /v1/autonomous/gating_history` | none | - | - | `src/mini_agent/api/routes.py:5507` |
| `POST /v1/autonomous/scheduling/pause` | none | - | - | `src/mini_agent/api/routes.py:5439` |
| `POST /v1/autonomous/scheduling/resume` | none | - | - | `src/mini_agent/api/routes.py:5484` |
| `GET /v1/autonomous/status` | none | - | - | `src/mini_agent/api/routes.py:5271` |
| `POST /v1/capability/tracks/{track_id}/topics/{topic_id}/adopt_goal` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:11496` |
| `POST /v1/chat` | none | - | - | `src/mini_agent/api/routes.py:2187` |
| `GET /v1/cron/jobs` | none | - | - | `src/mini_agent/api/routes.py:8935` |
| `POST /v1/cron/jobs` | none | - | - | `src/mini_agent/api/routes.py:8972` |
| `DELETE /v1/cron/jobs/{job_id}` | none | - | - | `src/mini_agent/api/routes.py:9048` |
| `PUT /v1/cron/jobs/{job_id}` | none | - | - | `src/mini_agent/api/routes.py:9011` |
| `PUT /v1/cron/jobs/{job_id}/config` | none | - | - | `src/mini_agent/api/routes.py:9245` |
| `POST /v1/cron/jobs/{job_id}/feedback` | none | - | - | `src/mini_agent/api/routes.py:9109` |
| `GET /v1/cron/jobs/{job_id}/prompt` | none | - | - | `src/mini_agent/api/routes.py:9310` |
| `PUT /v1/cron/jobs/{job_id}/prompt` | none | - | - | `src/mini_agent/api/routes.py:9327` |
| `POST /v1/cron/jobs/{job_id}/reset` | none | - | - | `src/mini_agent/api/routes.py:9371` |
| `POST /v1/cron/jobs/{job_id}/run` | none | - | - | `src/mini_agent/api/routes.py:9088` |
| `GET /v1/cron/jobs/{job_id}/runs/{run_id}` | none | - | - | `src/mini_agent/api/routes.py:9357` |
| `GET /v1/cron/jobs/{job_id}/workspace` | none | - | - | `src/mini_agent/api/routes.py:9193` |
| `GET /v1/cron_questions/counts` | none | - | - | `src/mini_agent/api/routes.py:8327` |
| `GET /v1/cron_questions/dismissed` | none | - | - | `src/mini_agent/api/routes.py:8293` |
| `GET /v1/cron_questions/history` | none | - | - | `src/mini_agent/api/routes.py:8267` |
| `GET /v1/cron_questions/pending` | none | - | - | `src/mini_agent/api/routes.py:8238` |
| `POST /v1/cron_questions/{question_id}/answer` | none | - | - | `src/mini_agent/api/routes.py:8355` |
| `POST /v1/cron_questions/{question_id}/dismiss` | none | - | - | `src/mini_agent/api/routes.py:8384` |
| `GET /v1/daemon/crash_alerts` | none | - | - | `src/mini_agent/api/routes.py:8156` |
| `GET /v1/daemon/crash_alerts/history` | none | - | - | `src/mini_agent/api/routes.py:8176` |
| `GET /v1/daemon/crash_alerts/pending_count` | none | - | - | `src/mini_agent/api/routes.py:10199` |
| `POST /v1/daemon/crash_alerts/{alert_id}/ack` | none | - | - | `src/mini_agent/api/routes.py:8195` |
| `GET /v1/decision_profile` | none | - | - | `src/mini_agent/api/routes.py:10323` |
| `GET /v1/diagnostics` | none | - | - | `src/mini_agent/api/routes.py:2004` |
| `GET /v1/digest/daily` | none | - | - | `src/mini_agent/api/routes.py:10273` |
| `GET /v1/digest/pending_startup` | none | - | - | `src/mini_agent/api/routes.py:10221` |
| `POST /v1/digest/pending_startup/ack` | none | - | - | `src/mini_agent/api/routes.py:10249` |
| `GET /v1/directions` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5920` |
| `POST /v1/directions` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5944` |
| `DELETE /v1/directions/{direction_id}` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6013` |
| `PATCH /v1/directions/{direction_id}` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5977` |
| `GET /v1/events` | none | - | - | `src/mini_agent/api/routes.py:2426` |
| `GET /v1/evolution/feedback_loop_summary` | none | - | - | `src/mini_agent/api/routes.py:8745` |
| `GET /v1/evolution/proposals` | none | - | - | `src/mini_agent/api/routes.py:7834` |
| `GET /v1/evolution/proposals/{branch:path}/diff` | none | - | - | `src/mini_agent/api/routes.py:7850` |
| `POST /v1/evolution/proposals/{branch:path}/merge` | none | - | - | `src/mini_agent/api/routes.py:7867` |
| `GET /v1/external_input/alerts` | none | - | - | `src/mini_agent/api/routes.py:8615` |
| `GET /v1/external_input/events` | none | - | - | `src/mini_agent/api/routes.py:8553` |
| `GET /v1/external_input/health_history` | none | - | - | `src/mini_agent/api/routes.py:8644` |
| `GET /v1/external_input/novelty_candidates` | none | - | - | `src/mini_agent/api/routes.py:8718` |
| `POST /v1/external_input/novelty_candidates/{candidate_id}/confirm` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:8892` |
| `POST /v1/external_input/novelty_candidates/{candidate_id}/dismiss` | none | - | - | `src/mini_agent/api/routes.py:8913` |
| `GET /v1/external_input/policies` | none | - | - | `src/mini_agent/api/routes.py:8525` |
| `GET /v1/external_input/sources` | none | - | - | `src/mini_agent/api/routes.py:8445` |
| `POST /v1/external_input/sources/reload` | none | - | - | `src/mini_agent/api/routes.py:8492` |
| `POST /v1/external_projects/register` | none | - | - | `src/mini_agent/api/routes.py:920` |
| `DELETE /v1/external_projects/{name}` | none | - | - | `src/mini_agent/api/routes.py:1010` |
| `GET /v1/external_projects/{name}/backlog` | none | - | - | `src/mini_agent/api/routes.py:1198` |
| `POST /v1/external_projects/{name}/backlog` | none | - | - | `src/mini_agent/api/routes.py:1225` |
| `PATCH /v1/external_projects/{name}/enabled` | none | - | - | `src/mini_agent/api/routes.py:954` |
| `GET /v1/external_projects/{name}/kanban_data` | none | - | - | `src/mini_agent/api/routes.py:1297` |
| `GET /v1/external_projects/{name}/ledger` | none | - | - | `src/mini_agent/api/routes.py:1171` |
| `GET /v1/external_projects/{name}/review` | none | - | - | `src/mini_agent/api/routes.py:1270` |
| `POST /v1/external_projects/{name}/trigger_run` | none | - | - | `src/mini_agent/api/routes.py:1061` |
| `DELETE /v1/fs/delete` | none | - | - | `src/mini_agent/api/routes.py:3473` |
| `GET /v1/fs/download` | none | - | - | `src/mini_agent/api/routes.py:3422` |
| `GET /v1/fs/list` | none | - | - | `src/mini_agent/api/routes.py:3398` |
| `POST /v1/fs/mkdir` | none | - | - | `src/mini_agent/api/routes.py:3463` |
| `GET /v1/fs/read` | none | - | - | `src/mini_agent/api/routes.py:3406` |
| `POST /v1/fs/rename` | none | - | - | `src/mini_agent/api/routes.py:3483` |
| `GET /v1/fs/search` | none | - | - | `src/mini_agent/api/routes.py:3438` |
| `GET /v1/fs/stat` | none | - | - | `src/mini_agent/api/routes.py:3414` |
| `POST /v1/fs/upload` | none | - | - | `src/mini_agent/api/routes.py:3493` |
| `POST /v1/fs/write` | none | - | - | `src/mini_agent/api/routes.py:3452` |
| `GET /v1/goal_execution_spec_templates` | none | - | - | `src/mini_agent/api/routes.py:6689` |
| `GET /v1/goal_mode/stuck_stats` | none | - | - | `src/mini_agent/api/routes.py:1939` |
| `DELETE /v1/goals` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6443` |
| `GET /v1/goals` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5554` |
| `POST /v1/goals` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6112` |
| `GET /v1/goals/cycle_diagnostics_overview` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:7209` |
| `GET /v1/goals/next_steps` | legacy | next_action_advisor | - | `src/mini_agent/api/routes.py:5896` |
| `POST /v1/goals/nodes` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5640` |
| `GET /v1/goals/research_summary` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:5828` |
| `GET /v1/goals/scheduling_diagnostics` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:1411` |
| `GET /v1/goals/similar_confirmed_specs` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6073` |
| `GET /v1/goals/tree` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5625` |
| `GET /v1/goals/tree_report` | legacy | CronScheduler, GoalBacklog | - | `src/mini_agent/api/routes.py:7157` |
| `POST /v1/goals/wiki/build` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:7182` |
| `DELETE /v1/goals/{goal_id}` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6383` |
| `PATCH /v1/goals/{goal_id}` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6184` |
| `GET /v1/goals/{goal_id}/cycle_diagnostics` | legacy | CronScheduler, GoalBacklog | - | `src/mini_agent/api/routes.py:7088` |
| `POST /v1/goals/{goal_id}/direction` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6040` |
| `GET /v1/goals/{goal_id}/execution_phase` | none | - | - | `src/mini_agent/api/routes.py:6974` |
| `POST /v1/goals/{goal_id}/execution_phase` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6987` |
| `POST /v1/goals/{goal_id}/execution_phase/unlock` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:7019` |
| `GET /v1/goals/{goal_id}/execution_spec` | none | - | - | `src/mini_agent/api/routes.py:6704` |
| `PUT /v1/goals/{goal_id}/execution_spec` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6715` |
| `POST /v1/goals/{goal_id}/execution_spec/close_check` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6922` |
| `POST /v1/goals/{goal_id}/execution_spec/confirm` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6890` |
| `POST /v1/goals/{goal_id}/execution_spec/generate` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6768` |
| `POST /v1/goals/{goal_id}/execution_spec/revise` | none | - | - | `src/mini_agent/api/routes.py:6836` |
| `POST /v1/goals/{goal_id}/feedback` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6642` |
| `POST /v1/goals/{goal_id}/lightweight_next_cycle` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6603` |
| `POST /v1/goals/{goal_id}/migrate_legacy` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6620` |
| `GET /v1/goals/{goal_id}/page` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:7131` |
| `POST /v1/goals/{goal_id}/recur` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6550` |
| `POST /v1/goals/{goal_id}/skip_next_cycle` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6587` |
| `GET /v1/goals/{goal_id}/tuning_proposals` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:7330` |
| `POST /v1/goals/{goal_id}/tuning_proposals` | legacy | CronScheduler, GoalBacklog | - | `src/mini_agent/api/routes.py:7254` |
| `POST /v1/goals/{goal_id}/tuning_proposals/suggest` | legacy | CronScheduler, GoalBacklog | - | `src/mini_agent/api/routes.py:7310` |
| `POST /v1/goals/{goal_id}/tuning_proposals/{proposal_id}/apply` | legacy | CronScheduler, GoalBacklog | - | `src/mini_agent/api/routes.py:7361` |
| `POST /v1/goals/{goal_id}/tuning_proposals/{proposal_id}/confirm` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:7343` |
| `POST /v1/goals/{goal_id}/tuning_proposals/{proposal_id}/reject` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:7396` |
| `POST /v1/goals/{goal_id}/unrecur` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:6573` |
| `POST /v1/goals/{node_id}/candidates/{candidate_id}/accept` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5705` |
| `POST /v1/goals/{node_id}/candidates/{candidate_id}/reject` | legacy | GoalBacklog, GoalTreeDecomposer | - | `src/mini_agent/api/routes.py:5724` |
| `POST /v1/goals/{node_id}/decompose` | legacy | GoalBacklog, GoalTreeDecomposer | - | `src/mini_agent/api/routes.py:5672` |
| `POST /v1/goals/{node_id}/focus_pin` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5742` |
| `POST /v1/goals/{node_id}/reparent` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:5764` |
| `GET /v1/goals/{node_id}/research` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:5792` |
| `GET /v1/growth/align` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11183` |
| `POST /v1/growth/align/adopt_all` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11224` |
| `POST /v1/growth/align/confirm_match` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11271` |
| `POST /v1/growth/candidates/{candidate_id}/adopt_goal` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11454` |
| `POST /v1/growth/candidates/{candidate_id}/material/generate` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:11560` |
| `POST /v1/growth/candidates/{candidate_id}/report/refresh` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:11408` |
| `GET /v1/growth/candidates/{candidate_id}/timeline` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11351` |
| `POST /v1/growth/candidates/{candidate_id}/{action}` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:10685` |
| `POST /v1/growth/first_touch_ack` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10575` |
| `GET /v1/growth/followups` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:10777` |
| `POST /v1/growth/followups/{candidate_id}/{outcome}` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10820` |
| `GET /v1/growth/health_trend` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:11299` |
| `POST /v1/growth/keywords` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10841` |
| `POST /v1/growth/keywords/confirm` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10887` |
| `POST /v1/growth/keywords/remove` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10910` |
| `POST /v1/growth/keywords/restore` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10932` |
| `POST /v1/growth/keywords/{topic}/confirm` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10957` |
| `POST /v1/growth/keywords/{topic}/remove` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10964` |
| `POST /v1/growth/keywords/{topic}/restore` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:10971` |
| `GET /v1/growth/materials/{material_id}` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:11602` |
| `GET /v1/growth/pursuits` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11025` |
| `GET /v1/growth/pursuits/portfolio_summary` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11097` |
| `GET /v1/growth/pursuits/related_directions` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11130` |
| `GET /v1/growth/pursuits/{goal_id}/saturation_trend` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:11324` |
| `POST /v1/growth/pursuits/{goal_id}/view_material` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:11156` |
| `GET /v1/growth/reports/refresh_candidates` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:10978` |
| `GET /v1/growth/reports/{report_id}` | legacy | growth_advisor | - | `src/mini_agent/api/routes.py:11537` |
| `POST /v1/growth/scan` | legacy | MemoryStore, growth_advisor | - | `src/mini_agent/api/routes.py:10593` |
| `GET /v1/growth/summary` | legacy | GoalBacklog, MemoryStore, growth_advisor | - | `src/mini_agent/api/routes.py:10446` |
| `GET /v1/health` | none | - | - | `src/mini_agent/api/routes.py:567` |
| `DELETE /v1/history` | none | - | - | `src/mini_agent/api/routes.py:2244` |
| `GET /v1/history` | none | - | - | `src/mini_agent/api/routes.py:2213` |
| `GET /v1/hybrid_exec/summary` | none | - | - | `src/mini_agent/api/routes.py:8873` |
| `GET /v1/inbox` | none | - | - | `src/mini_agent/api/routes.py:7919` |
| `POST /v1/inbox/external_alerts/{alert_id}/ack` | none | - | - | `src/mini_agent/api/routes.py:8011` |
| `GET /v1/interactions/pending` | none | - | - | `src/mini_agent/api/routes.py:3275` |
| `POST /v1/interactions/{req_id}` | none | - | - | `src/mini_agent/api/routes.py:3281` |
| `POST /v1/interrupt` | none | - | - | `src/mini_agent/api/routes.py:2207` |
| `GET /v1/models` | none | - | - | `src/mini_agent/api/routes.py:740` |
| `GET /v1/next_actions` | legacy | next_action_advisor | - | `src/mini_agent/api/routes.py:10303` |
| `GET /v1/notification/dispatch_log` | none | - | - | `src/mini_agent/api/routes.py:10172` |
| `GET /v1/notification/report_tiers` | none | - | - | `src/mini_agent/api/routes.py:10137` |
| `GET /v1/notification/watchlist` | none | - | - | `src/mini_agent/api/routes.py:10120` |
| `GET /v1/notifications/pending` | none | - | - | `src/mini_agent/api/routes.py:8041` |
| `POST /v1/notifications/pending/batch_ack` | none | - | - | `src/mini_agent/api/routes.py:8110` |
| `GET /v1/notifications/pending/categories` | none | - | - | `src/mini_agent/api/routes.py:8069` |
| `POST /v1/notifications/pending/{report_id}/ack` | none | - | - | `src/mini_agent/api/routes.py:8089` |
| `GET /v1/objectives/completion_trend` | none | - | - | `src/mini_agent/api/routes.py:11389` |
| `POST /v1/objectives/{execution_id}/cancel` | none | - | - | `src/mini_agent/api/routes.py:7439` |
| `POST /v1/objectives/{execution_id}/guidance` | none | - | - | `src/mini_agent/api/routes.py:7563` |
| `POST /v1/objectives/{execution_id}/pause` | none | - | - | `src/mini_agent/api/routes.py:7454` |
| `POST /v1/objectives/{execution_id}/resume` | none | - | - | `src/mini_agent/api/routes.py:7472` |
| `POST /v1/objectives/{execution_id}/retry` | none | - | - | `src/mini_agent/api/routes.py:7487` |
| `POST /v1/objectives/{execution_id}/steps/{step_index}/edit` | none | - | - | `src/mini_agent/api/routes.py:7502` |
| `POST /v1/objectives/{execution_id}/steps/{step_index}/reset` | none | - | - | `src/mini_agent/api/routes.py:7538` |
| `GET /v1/objectives/{execution_id}/steps/{step_index}/trace` | none | - | - | `src/mini_agent/api/routes.py:7708` |
| `GET /v1/output_projects` | none | - | - | `src/mini_agent/api/routes.py:865` |
| `POST /v1/perception/browser/start` | none | - | - | `src/mini_agent/api/routes.py:10052` |
| `GET /v1/perception/browser/status` | none | - | - | `src/mini_agent/api/routes.py:10078` |
| `POST /v1/perception/browser/stop` | none | - | - | `src/mini_agent/api/routes.py:10064` |
| `DELETE /v1/perception/events` | none | - | - | `src/mini_agent/api/routes.py:10044` |
| `GET /v1/perception/events` | none | - | - | `src/mini_agent/api/routes.py:10032` |
| `POST /v1/perception/git/install-hooks` | none | - | - | `src/mini_agent/api/routes.py:10009` |
| `POST /v1/perception/report` | none | - | - | `src/mini_agent/api/routes.py:9980` |
| `GET /v1/perception/status` | none | - | - | `src/mini_agent/api/routes.py:9953` |
| `GET /v1/perception/summary` | none | - | - | `src/mini_agent/api/routes.py:10084` |
| `POST /v1/perception/toggle` | none | - | - | `src/mini_agent/api/routes.py:9959` |
| `GET /v1/permissions/pending` | none | - | - | `src/mini_agent/api/routes.py:3203` |
| `POST /v1/permissions/{req_id}` | none | - | - | `src/mini_agent/api/routes.py:3215` |
| `GET /v1/persona_learning/persona_candidates` | none | - | - | `src/mini_agent/api/persona_candidate_routes.py:92` |
| `POST /v1/persona_learning/persona_candidates/scan` | legacy | growth_advisor | - | `src/mini_agent/api/persona_candidate_routes.py:101` |
| `POST /v1/persona_learning/persona_candidates/{candidate_id}/accept` | none | - | - | `src/mini_agent/api/persona_candidate_routes.py:135` |
| `POST /v1/persona_learning/persona_candidates/{candidate_id}/dismiss` | none | - | - | `src/mini_agent/api/persona_candidate_routes.py:146` |
| `POST /v1/persona_learning/tracks/{track_id}/topics/{topic_id}/adopt_goal` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:11496` |
| `POST /v1/protected-files/backup` | none | - | - | `src/mini_agent/api/routes.py:3107` |
| `DELETE /v1/protected-files/entries` | none | - | - | `src/mini_agent/api/routes.py:3082` |
| `POST /v1/protected-files/entries` | none | - | - | `src/mini_agent/api/routes.py:3061` |
| `POST /v1/protected-files/restore` | none | - | - | `src/mini_agent/api/routes.py:3164` |
| `GET /v1/protected-files/snapshots` | none | - | - | `src/mini_agent/api/routes.py:3129` |
| `GET /v1/protected-files/snapshots/{generation_id}` | none | - | - | `src/mini_agent/api/routes.py:3146` |
| `GET /v1/protected-files/status` | none | - | - | `src/mini_agent/api/routes.py:3034` |
| `GET /v1/self/concurrency` | none | - | - | `src/mini_agent/api/routes.py:4647` |
| `POST /v1/self/concurrency` | none | - | - | `src/mini_agent/api/routes.py:4663` |
| `GET /v1/self/config` | none | - | - | `src/mini_agent/api/routes.py:4540` |
| `PATCH /v1/self/config` | none | - | - | `src/mini_agent/api/routes.py:4578` |
| `GET /v1/self/diagnosis_feedback` | none | - | - | `src/mini_agent/api/routes.py:3620` |
| `GET /v1/self/error_log_stats` | none | - | - | `src/mini_agent/api/routes.py:5136` |
| `POST /v1/self/execution_model/force_reap` | none | - | - | `src/mini_agent/api/routes.py:4473` |
| `GET /v1/self/execution_model_status` | none | - | - | `src/mini_agent/api/routes.py:3846` |
| `GET /v1/self/external_projects` | none | - | - | `src/mini_agent/api/routes.py:800` |
| `GET /v1/self/fairness_diagnostics` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:1366` |
| `GET /v1/self/goal_fairness` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:3691` |
| `GET /v1/self/http_access_log/slow` | none | - | - | `src/mini_agent/api/routes.py:5160` |
| `GET /v1/self/initiative_inbox` | legacy | GoalBacklog, growth_advisor | - | `src/mini_agent/api/routes.py:1456` |
| `GET /v1/self/llm_call_stats` | none | - | - | `src/mini_agent/api/routes.py:1570` |
| `GET /v1/self/llm_pool_status` | none | - | - | `src/mini_agent/api/routes.py:768` |
| `GET /v1/self/personal_state` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:1497` |
| `GET /v1/self/portrait` | none | - | - | `src/mini_agent/api/routes.py:4951` |
| `GET /v1/self/priority_briefing` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:1530` |
| `GET /v1/self/scheduling_overview` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:4052` |
| `GET /v1/self/status` | legacy | GoalBacklog | - | `src/mini_agent/api/routes.py:4871` |
| `GET /v1/self/system_connectivity` | none | - | - | `src/mini_agent/api/routes.py:3773` |
| `GET /v1/self/task_concurrency` | none | - | - | `src/mini_agent/api/routes.py:4720` |
| `POST /v1/self/task_concurrency` | none | - | - | `src/mini_agent/api/routes.py:4793` |
| `GET /v1/self/unified_scheduler_preview` | legacy | GoalBacklog, UnifiedTaskScheduler | - | `src/mini_agent/api/routes.py:4337` |
| `GET /v1/sentinel/summary` | none | - | - | `src/mini_agent/api/routes.py:1889` |
| `GET /v1/sessions` | none | - | - | `src/mini_agent/api/routes.py:2558` |
| `POST /v1/sessions/cleanup` | none | - | - | `src/mini_agent/api/routes.py:2936` |
| `POST /v1/sessions/new` | none | - | - | `src/mini_agent/api/routes.py:2789` |
| `DELETE /v1/sessions/{session_id}` | none | - | - | `src/mini_agent/api/routes.py:2860` |
| `GET /v1/sessions/{session_id}` | none | - | - | `src/mini_agent/api/routes.py:2670` |
| `POST /v1/sessions/{session_id}/pin` | none | - | - | `src/mini_agent/api/routes.py:2897` |
| `POST /v1/sessions/{session_id}/resume` | none | - | - | `src/mini_agent/api/routes.py:2741` |
| `POST /v1/sessions/{session_id}/save_anchor` | none | - | - | `src/mini_agent/api/routes.py:2829` |
| `POST /v1/sessions/{session_id}/unpin` | none | - | - | `src/mini_agent/api/routes.py:2910` |
| `POST /v1/shutdown` | none | - | - | `src/mini_agent/api/routes.py:572` |
| `GET /v1/status` | none | - | - | `src/mini_agent/api/routes.py:600` |
| `GET /v1/stream` | none | - | - | `src/mini_agent/api/routes.py:2345` |
| `GET /v1/stream/{turn_id}` | none | - | - | `src/mini_agent/api/routes.py:2383` |
| `GET /v1/turns` | none | - | - | `src/mini_agent/api/routes.py:2469` |
| `GET /v1/turns/{turn_id}` | none | - | - | `src/mini_agent/api/routes.py:2475` |
| `GET /v1/user_profile/preferences` | none | - | - | `src/mini_agent/api/routes.py:10348` |
| `POST /v1/user_profile/preferences` | none | - | - | `src/mini_agent/api/routes.py:10385` |
| `POST /v1/user_profile/preferences/delete` | none | - | - | `src/mini_agent/api/routes.py:10412` |
| `GET /v1/users` | none | - | - | `src/mini_agent/api/routes.py:3536` |
| `POST /v1/users` | none | - | - | `src/mini_agent/api/routes.py:3545` |
| `DELETE /v1/users/{user_id}` | none | - | - | `src/mini_agent/api/routes.py:3569` |
| `PATCH /v1/users/{user_id}` | none | - | - | `src/mini_agent/api/routes.py:3584` |
| `POST /v1/users/{user_id}/token` | none | - | - | `src/mini_agent/api/routes.py:3604` |
| `GET /v1/whoami` | none | - | - | `src/mini_agent/api/routes.py:1972` |
| `POST /v1/wiki/promotion` | none | - | - | `src/mini_agent/api/routes.py:1676` |
| `GET /v1/wiki/quarantine` | none | - | - | `src/mini_agent/api/routes.py:1721` |
| `POST /v1/wiki/quarantine/purge` | none | - | - | `src/mini_agent/api/routes.py:1848` |
| `POST /v1/wiki/quarantine/repair` | none | - | - | `src/mini_agent/api/routes.py:1768` |
| `POST /v1/wiki/quarantine/retry` | none | - | - | `src/mini_agent/api/routes.py:1800` |
| `GET /v1/wiki/quarantine_status` | none | - | - | `src/mini_agent/api/routes.py:1603` |
| `POST /v1/wiki/stats` | none | - | - | `src/mini_agent/api/routes.py:1639` |
| `GET /v1/workflow_editor/meta` | none | - | - | `src/mini_agent/api/routes.py:9509` |
| `GET /v1/workflow_runs` | none | - | - | `src/mini_agent/api/routes.py:9752` |
| `GET /v1/workflow_runs/{run_id}` | none | - | - | `src/mini_agent/api/routes.py:9761` |
| `POST /v1/workflow_runs/{run_id}/approve` | none | - | - | `src/mini_agent/api/routes.py:9872` |
| `POST /v1/workflow_runs/{run_id}/cancel` | none | - | - | `src/mini_agent/api/routes.py:9797` |
| `GET /v1/workflow_runs/{run_id}/events` | none | - | - | `src/mini_agent/api/routes.py:9773` |
| `POST /v1/workflow_runs/{run_id}/input` | none | - | - | `src/mini_agent/api/routes.py:9900` |
| `POST /v1/workflow_runs/{run_id}/mark_interrupted` | none | - | - | `src/mini_agent/api/routes.py:9810` |
| `POST /v1/workflow_runs/{run_id}/pause` | none | - | - | `src/mini_agent/api/routes.py:9784` |
| `POST /v1/workflow_runs/{run_id}/reject` | none | - | - | `src/mini_agent/api/routes.py:9885` |
| `POST /v1/workflow_runs/{run_id}/resume` | none | - | - | `src/mini_agent/api/routes.py:9827` |
| `POST /v1/workflow_runs/{run_id}/steps/{step_id}/override` | none | - | - | `src/mini_agent/api/routes.py:9915` |
| `GET /v1/workflows` | none | - | - | `src/mini_agent/api/routes.py:9430` |
| `POST /v1/workflows` | none | - | - | `src/mini_agent/api/routes.py:9577` |
| `GET /v1/workflows/{name}` | none | - | - | `src/mini_agent/api/routes.py:9439` |
| `GET /v1/workflows/{name}/backups` | none | - | - | `src/mini_agent/api/routes.py:9641` |
| `POST /v1/workflows/{name}/backups/{backup_id}/restore` | none | - | - | `src/mini_agent/api/routes.py:9654` |
| `GET /v1/workflows/{name}/editor` | none | - | - | `src/mini_agent/api/routes.py:9519` |
| `PUT /v1/workflows/{name}/editor` | none | - | - | `src/mini_agent/api/routes.py:9550` |
| `POST /v1/workflows/{name}/editor/validate` | none | - | - | `src/mini_agent/api/routes.py:9531` |
| `POST /v1/workflows/{name}/preview` | none | - | - | `src/mini_agent/api/routes.py:9673` |
| `POST /v1/workflows/{name}/run` | none | - | - | `src/mini_agent/api/routes.py:9703` |
| `GET /v1/workflows/{name}/stats` | none | - | - | `src/mini_agent/api/routes.py:9690` |
| `POST /v1/workflows/{name}/steps/{step_id}/patch` | none | - | - | `src/mini_agent/api/routes.py:9452` |
| `POST /v1/workflows/{name}/steps/{step_id}/test` | none | - | - | `src/mini_agent/api/routes.py:9598` |
| `GET /wiki_pages/{page_id}` | none | - | - | `src/mini_agent/api/capability_routes.py:498` |

