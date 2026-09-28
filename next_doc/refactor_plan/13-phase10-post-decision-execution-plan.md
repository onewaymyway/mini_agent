# Phase 10 决策落地方案（D2/D3/D4）——待所有者确认后实施

> 状态：**方案已获所有者确认（Q1–Q4，见第八节，范围已大幅收窄）；S-B0 已实施（第九节）；其余阶段未实施。**
> （第一至七节是确认前的原方案，保留作决策依据；与第八节冲突处以第八节为准。）
>
> 原状态：方案，未实施，未改任何代码。本文件承接 `11-phase10-legacy-decommission-plan.md`
> “变更记录 2026-09-28”里的 D1–D5。所有者已给出 D2/D3/D4 的决定（见下），本文把决定
> 转成可执行的分阶段计划，并列出**仍需所有者确认的事项（第七节）**，确认后才动代码。
> 影响面数字均为 2026-09-28 对上传仓库的静态统计（grep/AST），是下限估计。

## 一、所有者已做的决定

| 项 | 决定 | 含义 |
|---|---|---|
| D2 | 术语验收**连概念词和命令名一起改** | 用户可见的命令名、HTTP 路径、文档术语都要收敛到 Self/World/Experience/Goal/Capability/Action/Simulation/Runtime；这是**破坏性改名** |
| D3 | 由 Claude 决定 `capability` 谁改名 | 见第二节，结论：**人设能力学习一侧改名**，架构 Capability 保留 |
| D4 | **补齐**前置条件（不豁免） | Phase 10 之前先把“未落地”的部分做实，见第三节 |

## 二、D3 结论：人设能力学习改名，架构 Capability 保留

理由：

1. 验收标准与 §43 都要求对外术语是 “Capability”，且 D2 的 `/workflow` 改名目标（Workflow → Capability）
   正好要占用这个词——两边同时用会直接命名冲突。
2. 架构 Capability 目前**没有任何生产引用**（`core/capability.py` 空占位），改名成本为 0；
   人设学习一侧是已上线功能，但它的用户可见面很窄：CLI `/capability`（不在 `_COMMANDS` 菜单里）、
   HTTP `/v1/capability/*`（`capability_routes.py`、`persona_candidate_routes.py`，另有 `routes.py:11494` 一条）。
3. 人设学习的语义本就是“给人设（persona）学知识”，`docs/persona-guide.md`、`/v1/personas` 已在用 persona 一词，改名后更自洽。

**新名字（建议）**：用户可见处统一叫“人设学习 / Persona Learning”——CLI `/persona-learning`，
HTTP `/v1/persona_learning/*`（含 `/persona_candidates`）。

**只改用户可见面，不改内部标识**（避免数据迁移）：`CapabilityTrack*` 类名、`capability_learning.py` 模块名、
磁盘上的 track 文件格式、cron 任务 id `sys:capability_learning_cycle` 都**保持不变**。
兼容：旧 `/capability …` 与 `/v1/capability/*` 保留为**别名**（HTTP 用 `include_in_schema=False` 从 OpenAPI 隐藏，
CLI 从菜单/`--help` 隐藏并在使用时提示新名）。别名必须保留的一个具体原因：
`evolution/cron_agent_bridge.py:96` 的提示词会让 agent 执行 `/capability cycle`，且可能已写入用户已有的 cron 任务里。
涉及约 78 个文件含相关标识（src/apps/docs/tests），其中真正需要改的是用户可见字符串与调用方，内部标识不动。

## 三、D4 落地：什么叫“补齐前置条件”

Phase 10 前置条件原文：Phase 1–9 全部完成，八大概念在真实场景跑通完整闭环。逐项核实后需要补的有：

| 编号 | 缺口 | 现状（已核实） | 补齐做法 | 风险 |
|---|---|---|---|---|
| A1 | Phase 9 完成标志第 3、4 条 | 需真实仓库 `git diff`；压缩包无 `.git` | **只能由你执行**：`python scripts/check_frozen_evolution_modules.py --base <Phase 9 起点 commit>`，把结果发我，我勾选并记录 | 无 |
| A2 | `WorldState`/`CapabilityState`/`RuntimeState` 三个空占位 | 全是无字段 dataclass，仅 `state_manager` 与 `core/__init__` 引用 | 各填最小真实字段并接**一个**真实产出方：Capability ← 现有 tool/skill/workflow 注册表的只读快照；Runtime ← `AgentRuntime` 周期计数/上次运行状态；World ← 项目/工作区只读事实。全部只读投影，不改旧模块 | 低 |
| A3 | `DecisionEngine` 无生产调用方；旧 Advisor 未接 | `runtime.py` 有 `enable_decision_stage` 开关但默认 False | 让 `AgentRuntime` 的 plan/simulate/decide 在开关打开时真的调用 `DecisionEngine`；给 `next_action_advisor` 的候选做一个 `→ ActionSpec` 的 Adapter。**默认仍关闭** | 中 |
| A4 | `goal_mode/executor.py` 台账“未开始” | Phase 1 遗留 | 先按 Sprint 1.5 方法论重新按跨子系统口径统计 inbound，再决定 Adapter 还是评估后豁免 | 低–中 |
| A5 | `ObjectiveExecutor`/`GoalBacklog`（“旧 Objective → Goal internal step”“旧 Goal”） | inbound=22（跨子系统）；共享 Agent + InputQueue 模型，Sprint 8-5 评估为**高风险** | **不改执行模型**。只做“只读投影 + 事件”：把 `ObjectiveExecution` 投影成 `GoalState` 并 publish 事件，使其进入 Event/Experience 链路。是否进一步让 Objective 改用独占 Agent 属产品决策，**见第七节 Q2** | 中（投影）/ 高（改模型） |

关键约束（沿用 `12` 第一节）：**只有在对应底层真的落地后，才能对该概念改名**。所以第四节的改名按概念分批，
每批的前置是上表对应项完成。这样避免出现此前盘点里警告过的“文档声称尚不存在的事实”。

## 四、D2 落地：改名清单与兼容策略

**规模**（静态统计）：CLI 菜单 45 条命令；HTTP 308 条路由，前缀分布——`/goals`44、`/growth`29、`/self`24、
`/workflows`13、`/cron`12、`/workflow_runs`11、`/objectives`9、`/cron_questions`6 等；调用方 `apps/mini_agent_kanban`、
`apps/weixin_plugin` 有直接依赖；`docs` 里提到这些路径的文件：`/v1/goals`42、`/v1/cron`22、`/next` 命令 41、`/cron` 命令 36。
HTTP 文档里概念词计数：Cron×96、Objective×62、Workflow×31、Scheduler×4。

**兼容策略（保守，符合你的 opt-in 偏好）**：新名成为唯一对外名字，旧名保留为**隐藏别名**（不出现在菜单/`--help`/OpenAPI/文档），
使用时打印一行迁移提示。**本方案不删除任何旧名**；删除是之后单独的决定。原因：kanban/weixin/android 三个调用方、
用户已有 cron 任务里的提示词、`run_slash_command` 都会用到旧名。

**命名映射（建议，需你确认——第七节 Q1）**：

| 现有用户可见名 | 建议新名 | 依据 / 前置 | 批次 |
|---|---|---|---|
| `/capability`、`/v1/capability/*`（人设学习） | `/persona-learning`、`/v1/persona_learning/*` | D3 | B0（最先，为下一行腾出名字） |
| `/workflow`、`/v1/workflows`、`/v1/workflow_runs` | `/capability`、`/v1/capabilities`、`/v1/capability_runs` | §43 “Workflow → Capability”；前置 A2（Capability 要先有真实内容） | B1 |
| `/cron`、`/v1/cron*`、`/v1/cron_questions` | `/runtime schedule`、`/v1/runtime/schedules*` | §43 “Scheduler → Runtime”；前置 A2（RuntimeState） | B2 |
| Objective（`/v1/objectives`、文档词） | Goal 的“步骤”：`/v1/goals/{id}/steps` 一类 | §43 “Objective → Goal internal step”；前置 A5 | B3 |
| `/next`、`/digest`、`/growth`、`/v1/growth/*`、`/v1/directions/*`（Advisor 类） | `/decision …`（`/next`→`/decision next`）等 | §43 “Advisor → Decision policy”；前置 A3 | B4 |
| `/goal`、`/goals`、`/v1/goals` | **不改**（Goal 本就是八大词之一） | — | — |

> `/growth`、`/directions` 是否应归入 Decision、还是 Experience/Self，我没有足够依据，属于要你拍板的部分。

**每个批次的固定动作**：1) 新旧名并存（别名）；2) 改菜单/`--help`/OpenAPI summary/docstring；3) 改 `apps/*` 调用方到新名；
4) 改 `docs/` 与 `next_doc` 里的用户文档（不改历史计划文档正文，只加状态头，沿用此前做法）；5) 更新测试并新增“旧名仍可用”的兼容测试；
6) 扩展 `scripts/entrypoint_inventory.py`，新增“用户可见面出现旧概念词即失败”的检查，作为该批次的机器验收。

## 五、阶段顺序与验收

| 阶段 | 内容 | 验收（均以实际运行为准） |
|---|---|---|
| S-A | A2 → A4 → A3 → A5（投影版），A1 由你执行 | 各项台账状态更新；对应新增测试通过；`dep_graph.py` 未触发止损；默认行为不变（新能力均 opt-in、默认关） |
| S-B0 | D3 人设学习改名 + 别名 | 旧路径仍 200 / 旧命令仍可用；新路径可用；OpenAPI 与菜单不再出现旧名；cron 提示词里的 `/capability cycle` 仍能执行 |
| S-B1…B4 | 按第四节批次逐批改名 | 每批：兼容测试 + 用户可见面旧概念词命中为 0 + 调用方（kanban/weixin）冒烟 |
| S-C | Sprint 10-2（`legacy/` 目录移动）、10-3（验收报告） | 依赖图显示 `legacy/` 仅被 Adapter 引用；**A5 若只做了投影版，则依赖图大概率仍不满足，此时按计划的止损条件不移动这部分目录，并如实写进验收报告** |

每个阶段结束仍按你的惯例：更新文档 + 打 diff zip（保持目录结构）。

## 六、风险与如实说明

1. **改名与“底层未迁移”的张力**：D2 让用户可见名先统一，但 A5 只做投影时，`ObjectiveExecutor` 等仍是旧实现。
   本方案用“别名 + 按概念在底层落地后才改名”来规避，但 B3 的“Objective→Goal step”只是术语与投影层面的收敛，
   **不等于执行模型迁移**，验收报告里会如实区分。
2. **破坏面**：即便保留别名，`apps/*` 与用户已保存的 cron 提示词若使用旧名，仍依赖别名长期存在；别名的移除时间点需另行决定。
3. **测试覆盖**：全量测试（428 个测试文件）在单核环境预计数小时，历次只跑了定向回归；每批仍只做定向回归 + 兼容测试，
   全量回归建议由你在本机执行一次。
4. **我无法执行 A1**（无 `.git`）。

## 七、需要你确认的事项（确认前不动代码）

- **Q1 命名映射**：第四节表格里的新名是否认可？尤其是 `/workflow→/capability`、`/cron→/runtime schedule`，
  以及 `/growth`、`/directions`、`/digest` 归到哪个词（Decision / Experience / Self）。
- **Q2 A5 的范围**：接受“只读投影 + 事件、不改 Objective 执行模型”（推荐，风险中），
  还是做 Sprint 8-5 说的产品层决策、让 Objective 改用独占 Agent（风险高，会改变 Objective 与用户共享上下文的行为）？
- **Q3 别名策略**：接受“旧名永久隐藏别名、本方案不删除”吗？
- **Q4 起步阶段**：建议先做 **S-B0（D3 改名）**，它独立、可回退、且是后续所有改名的前置；或者你更想先做 S-A 的某一项？

## 八、所有者确认结果与范围调整（2026-09-28）

| 问题 | 所有者答复 | 对方案的影响 |
|---|---|---|
| Q1 命名映射 | Objective 可以改；`/workflow`、`/cron` 不改；其他也没有修改必要。**“没必要机械的改名，重要的是把整体的逻辑理顺”** | **取消 B1（workflow→capability）、B2（cron→runtime schedule）、B4（next/digest/growth/directions→decision）**；只保留 B3（Objective → Goal 步骤），且需等 A5 完成；不再要求“用户可见面旧概念词命中为 0”的机器验收 |
| Q2 A5 范围 | 只做只读投影 + 事件，不改 Objective 执行模型 | A5 按“投影版”实施，不做独占 Agent |
| Q3 别名策略 | 接受旧名永久隐藏别名 | 生效，S-B0 已按此实施 |
| Q4 起步阶段 | 先做 S-B0 | 已实施，见第九节 |

**修订后的阶段**：S-B0（已完成）→ S-A（A1 由所有者执行；A2/A3/A4/A5 由我实施，重点是“理顺逻辑”而非改名）→ B3（Objective 改称，仅在 A5 之后）→ S-C（10-2/10-3）。

**需如实说明的一点**：S-B0 的原始动机之一是给 `/workflow → /capability` 腾名字；Q1 取消该改名后，这个冲突不再存在。
S-B0 仍按所有者指示完成，且它独立、可回退、旧名保留，所以没有回滚；它现在的价值只是消除
“人设学习”与架构 Capability 这个领域词在用户可见面的歧义。若所有者认为价值不足，回退只需把
`capability_routes.py`/`persona_candidate_routes.py` 的前缀和 `repl.py`/`parser.py` 的主名改回即可（别名机制可保留）。

## 九、S-B0 执行记录（2026-09-28）

**改动（用户可见名 `capability` → `persona-learning` / `persona_learning`）**

| 面 | 新名（主） | 旧名 |
|---|---|---|
| CLI | `/persona-learning`（另接受 `/persona_learning`） | `/capability`，隐藏别名，使用时打印一行迁移提示；`--help` 只列新名 |
| HTTP（`capability_routes.py`、`persona_candidate_routes.py`） | `/v1/persona_learning/*`、`/v1/persona_learning/persona_candidates/*` | `/v1/capability/*`，隐藏别名（`include_in_schema=False`，由 `make_legacy_alias_router` 为每条路由生成） |
| HTTP（`routes.py` 的 adopt_goal） | `/v1/persona_learning/tracks/{id}/topics/{id}/adopt_goal` | 旧路径叠加第二个装饰器，`include_in_schema=False` |
| 挂载 | `api/server.py` 改为调用 `mount_persona_learning_routers(app)` 一处完成新路径 + 旧别名 | — |
| 调用方 | Streamlit 看板 `client.py` 28 处 API 路径、`app.py` 提示语；React 看板 `endpoints.ts`（API 前缀）与导航文案“人设学习” | — |
| 定时任务提示词 | `cron_agent_bridge.py` 改为让 agent 执行 `/persona-learning cycle` | 用户已保存的旧任务提示词里的 `/capability cycle` 仍可用 |
| 文档 | `docs/persona-guide.md`、`docs/kanban-dashboard-guide.md`；`next_doc/persona_capability_learning_design.md` 加命名更新状态头（正文保持历史原貌）；`phase10-entrypoint-inventory.md` 附录重新生成 | — |

**刻意不改**：`CapabilityTrack*` 类名、`capability_router` 变量名、`evolution/capability_learning.py`、磁盘上的 track 文件格式、
cron 任务 id `sys:capability_learning_cycle`/`sys:capability_question_sweep`（改这些需要数据迁移，且无用户可见收益）。
React 看板的前端路由路径 `/capability` 与文件名 `useCapability.ts`/`CapabilityLearning` 也未改（内部导航，非 API）。
Streamlit 看板 `app.py` 里除提示语外的界面文案（如“能力学习”标签）未改，避免机械改名。

**验证（均已实际运行）**

- 新增 `tests/test_phase10_sb0_persona_learning_rename.py` 8 用例：新旧路径行为一致且共享同一份数据；OpenAPI 只暴露新路径；
  别名覆盖主路由的每一条（方法 + 路径）；CLI 新/旧名都能分发且只有旧名出现提示；cron 提示词与 `--help` 用新名。
- 既有测试 `test_capability_routes_mount.py`、`test_capability_persona_wiki_scopes_binding.py` 改为通过 `mount_persona_learning_routers` 挂载，
  其中大量用例仍访问旧路径 `/v1/capability/*`——它们通过即证明旧路径别名可用。
- 定向回归（`test_core_*`/`test_phase*`/`test_capability*`/`test_persona*`/`test_goal_mode`/`test_cron*`/`test_slash*`/`test_api*`）
  831 用例，825 通过；6 个失败均为既有：5 个 `test_build_from_history_*`（Sprint 0 已记录）+
  `test_capability_routes_mount.py::TestPersonaDraftRoutes::test_draft_show_publish_full_flow`（缺 `app.state.async_jobs`，
  已在**未修改的原始压缩包**上复现，与本次无关）。
- `pyflakes` 无告警；`entrypoint_inventory` 的 13 个测试通过（路由计数与独立正则一致）。

**未验证 / 局限**

- `tests/test_kanban_*` 需要 `streamlit`，本环境未安装，未运行；React 看板（`mini_agent_kanban_x`）未构建，只做了字符串级修改。
  建议你在本机跑一次看板相关测试与前端构建。
- 未拉起完整 `HttpServer` 做端到端冒烟；挂载逻辑通过 `mount_persona_learning_routers`（`server.py` 实际调用的同一函数）在测试里覆盖。
- 全量测试未跑（历次均为定向回归）。
