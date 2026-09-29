# Phase 10 S-A 分项方案（补齐前置条件）——待所有者确认后实施

> 状态：**S-A 全部完成（A2 第八节、A5 第九节、A3 第十节、A4 第十一节；A1 已由所有者在真实仓库核对通过，见
> `11-phase10-legacy-decommission-plan.md`"变更记录"）。Phase 10 后续（Sprint 10-2/10-3）已由所有者决定搁置（D5），
> D1 维持现状。本文件收尾。**
> 原状态：方案，未实施，未改任何代码。 承接 `13-phase10-post-decision-execution-plan.md` 第八节：
> 所有者已确认“不机械改名、重点理顺逻辑”“A5 只做只读投影 + 事件、不改 Objective 执行模型”。
> 本文把 S-A 的 A2–A5 逐项落到**真实代码现状**上（2026-09-28 静态核实），给出推荐做法与需你决定的问题。
> A1（Phase 9 完成标志第 3、4 条，需真实 git 仓库）只能由所有者执行，不在本文范围。

## 〇、更正上一版方案的一处事实错误

`13` 号文档第三节 A3 行写“`runtime.py` 有 `enable_decision_stage` 开关但默认 False”——**这是错的**。
`grep` 全仓只有一处出现：`runtime/runtime.py` 顶部 docstring 的一句话；代码里 `AgentRuntime.__init__` 只有
`enable_learn_stage`/`learn_auto_rollback` 两个开关，`plan/simulate/decide` 三步是被**直接跳过**的（正文注释写明沿用
Phase 7 Sprint 7-2 “不把候选生成→模拟→决策自动接入主循环”的范围决策）。因此 A3 不是“打开一个已有开关”，
而是要**新增**开关并写接入代码，工作量和风险都比我上次说的大。`13` 已同步更正。

## 一、核实到的现状

| 项 | 现状（已核实） |
|---|---|
| `WorldState`/`CapabilityState`/`RuntimeState` | 三个文件都是无字段空 dataclass。各自文件头写明：**不提前编造字段，字段来源以真实接入的模块为准**，并留了 TODO：Capability 待 Phase 6、Runtime 待 Phase 8、World 待 Phase 5 起视真实场景补充。Phase 5/6/8 现已完成，所以“等真实来源”的前置条件基本具备（World 除外，见 A2） |
| `StateManager` | 只有 `goal` 这个 kind 有真实托管：订阅 `GoalUpdated` 事件自动更新，`snapshot()` 可导出全部 |
| `AgentRuntime.run_once()` | 已发布 `RuntimeCycleStarted`/`RuntimeCycleCompleted` 事件（payload 含 `status`、`gap_item_count`，开启 learn 时含 learn 摘要）；`observe` 步只读 `StateManager.snapshot()` |
| `DecisionEngine`（`cognition/decision.py`） | `select(candidates: list[SimulationResult]) -> (ActionSpec, DecisionTrace)`；**必须由调用方注入** `llm_select` 或 `human_confirm`；`generate_candidate_actions`/`simulate_candidates` 在 `simulation/engine.py`，同样需要注入 LLM。生产代码无任何调用方 |
| `GoalRunner` 执行方式 | 自己驱动多轮，**不接受 `ActionSpec`**。所以即便 `decide` 选出了 ActionSpec，也没有地方可以“执行它”——这是 A3 的关键约束 |
| `goal_mode/executor.py` | 只有 `GoalStepExecutor`（ABC）+ `CoarseStepExecutor`，约 60 行；inbound 仅 3 个文件（`goal_mode/__init__`、`runner.py`、`runtime/runtime.py`），outbound 为 0，未触发止损。台账“未开始 0%”是 Phase 1 遗留，**从未评估过** |
| `ObjectiveExecutor`（`evolution/objective_executor.py`，2366 行）/ `GoalBacklog`（`perception/goal_backlog.py`，2563 行） | `ObjectiveExecution` 含 `ExecutionStep` 列表、`current_step()`、`progress_ratio()`；`GoalNode` 有 `id/level/title/status/progress_notes/parent_id/children_ids/…`。两者共享主 Agent + InputQueue 模型（Sprint 8-5 结论），构造闭包在 `api/server.py` 里时序敏感 |

## 二、A2：三个 State 的真实字段

**原则**：只投影“已有真实产出方”的数据，一个字段没有来源就不加（沿用 Sprint 4-2 的止损规则）。

| State | 推荐字段与来源 | 更新方式 | 推荐结论 |
|---|---|---|---|
| `RuntimeState` | `last_cycle_status`、`last_cycle_started_at`、`last_cycle_finished_at`、`cycles_started`、`cycles_completed`、`last_learn_summary`——全部来自已发布的 `RuntimeCycleStarted/Completed` 事件 payload | `StateManager` 订阅这两个事件（与 `GoalUpdated` 同一模式），不改 `runtime.py` | **做**。来源真实、零侵入 |
| `CapabilityState` | 只读快照：可用工具名列表、workflow 名列表、skill 名列表（来自 `ToolRegistry`/`WorkflowStore`/`SkillLoader`）。字段名先按这三类，**实施第一步先核实三个注册表各自的只读 API**，核实不到的类别就不放 | 由调用方在构造/刷新时 `update_state("capability", ...)`，不订阅事件（这些注册表没有事件） | **做**，但字段以核实结果为准，可能少于三类 |
| `WorldState` | 没有任何模块产出 Entity/Relation/Constraint 等原方案 §6 的结构。仅有的真实来源是配置与环境：`project_root`、平台、Python 版本。这些是“环境事实”，不是原方案里的 World 模型 | 启动时一次性写入 | **需你决定（Q-A2）** |

## 三、A5：Objective → Goal 的只读投影

**推荐：拉取式投影，不改 `objective_executor.py`。** 新增 `core/objective_adapter.py`：
`ObjectiveAdapter.to_new(ObjectiveExecution, GoalNode) -> GoalState`（单向，`to_old` 显式 `NotImplementedError`，与已有 Adapter 一致）。
映射：`goal_text` ← objective 的 `title`/`description`；`status` ← 执行状态映射到 `GoalStatus`；`round` ← 当前步序号；
`evidence` ← `{steps_total, steps_done, level:"objective", parent_goal_id}`。**只读、不写回、不订阅**。

你原话要求“投影 + 事件”。事件有两种做法，风险不同：

| 做法 | 是否改 `objective_executor.py` | 效果 | 风险 |
|---|---|---|---|
| **推荐：拉取式**——提供 `project_objective_executions(executor)`，由读取方（例如 B3 的 `/v1/goals/{id}/steps`）按需调用，读取时顺带 publish 一次投影事件 | 否 | 进入新领域模型，但事件只在被读取时产生 | 低 |
| 推送式——在 `start`/`on_turn_done`/`cancel` 三处加 publish（默认关、try/except 包裹） | 是（≤3 处新增行，但文件 2366 行、时序敏感，Sprint 8-5 评为高风险区域） | 事件实时 | 中 |

**推荐先做拉取式**：它同时给出 B3（Objective 改称 Goal 步骤）需要的数据来源，B3 的 HTTP 端点就是投影的第一个真实消费者，
避免“只建 Adapter 没人用”。推送式等你觉得需要实时事件时再单独立项（**Q-A5**）。

## 四、A3：DecisionEngine 接入

**推荐：新增默认关闭的旁路（advisory-only），不改变执行。** 开关 `goal_mode.runtime_decision_enabled`（默认 `False`，
与 `runtime_learn_enabled` 同风格）。开启后 `run_once()` 在 `execute` 之前：`generate_candidate_actions → simulate_candidates →
DecisionEngine.select`，LLM 注入使用 `agent.llm_helper`，把 `DecisionTrace.to_text()` 通过新事件 `DecisionMade` 发布并写入日志；
**不改变 `GoalRunner` 的执行**（它不接受 ActionSpec，见第一节）；整段 try/except，失败只记日志，不影响 Goal。

代价与限制（必须让你知道）：开启后每次 Goal 运行多约 2–3 次 LLM 调用；选出的动作**不会被执行**，只是“决策记录”。
要让决策真正驱动执行，需要改 `GoalRunner` 接受外部 ActionSpec，这属于更大的改动，本方案不做（**Q-A3**）。

“旧 Advisor 候选 → `ActionSpec` 的 Adapter”（`13` 里写的 A3 后半）：`next_action_advisor` 的候选结构我这次没有逐字段核实，
且当前没有消费者。**推荐本轮不做**，与 LessonAdapter 的 `to_old` 同理——避免为不存在的调用方设计；有真实消费者时再补。

## 五、A4：`goal_mode/executor.py`

它是 Goal 层“怎么执行一步”的策略接口（ABC + 一个粗粒度实现），不是一个需要“收敛”的旧概念；`ActionExecutor` 的
`type` 只有 tool/workflow/subagent，没有“agent 一轮对话”这一类。可选做法：

| 做法 | 说明 | 评价 |
|---|---|---|
| **推荐：评估后保留，台账改“已评估，不迁移”并写明理由** | 它已被 `runner.py` 与 `AgentRuntime` 正确引用，本身无耦合风险；硬迁移只会给 ActionSpec 加一个只服务它的 type | 诚实、零风险 |
| 加 `GoalStepResult → ActionResult` 转换函数 | 让一步的结果能用统一 Action 词汇表达 | 目前没有消费者，属推测性设计，不推荐 |
| 给 `ActionSpec` 新增 `type="agent_turn"` 并让 `CoarseStepExecutor` 走 `ActionExecutor` | 真正统一 | 改动 Phase 6 核心类型，影响面大，不推荐（**Q-A4**） |

## 六、推荐顺序与每步产出

| 顺序 | 内容 | 是否改现有核心模块 | 默认行为变化 |
|---|---|---|---|
| 1 | A2：`RuntimeState`（事件订阅）+ `CapabilityState`（只读快照）；World 视 Q-A2 | 仅 `state_manager.py` 增订阅、两个 State 文件加字段 | 无（新增只读状态） |
| 2 | A5：`ObjectiveAdapter` + `project_objective_executions`（拉取式） | 否 | 无 |
| 3 | B3：`/v1/goals/{id}/steps`（Objective 改称 Goal 步骤，旧 `/v1/objectives` 保留为隐藏别名）+ CLI/文档措辞 | 新增路由，旧路由加别名 | 无 |
| 4 | A3：默认关闭的 advisory 决策旁路 | `runtime/runtime.py` 增一段 opt-in 代码 | 默认无；开启后多 LLM 调用 |
| 5 | A4：评估结论写入台账 | 仅文档 | 无 |

每一步：先跑相关回归 → 更新文档（台账/计划/README）→ 打 diff zip，与之前一致。
A2 之前我会先核实三个注册表的只读 API（写进该步执行记录）。

## 七、需要你回答的问题

- **Q-A2（World）**：`WorldState` 没有真实的“世界模型”来源。选：(a) **保持空占位**并把“无真实来源、为何不填”写进台账（我倾向，符合 Sprint 4-2 止损规则）；
  (b) 只放 `project_root`/平台/Python 版本这类环境事实（能填，但它不是原方案 §6 的 World，可能让人误以为 World 已落地）。
- **Q-A5（事件）**：接受“拉取式投影 + 读取时发布事件”，还是要实时推送式（要改 `objective_executor.py`）？
- **Q-A3（决策）**：接受“默认关闭、advisory-only、不执行所选动作”吗？还是你要让决策真正驱动执行（需改 `GoalRunner`，另立方案）？
- **Q-A4（executor）**：接受“评估后保留、不迁移”吗？
- **Q-顺序**：接受第六节顺序，从 A2 的 Runtime/Capability 开始吗？

## 八、执行记录

### A2（2026-09-28）：RuntimeState / CapabilityState 填入真实字段——已完成

所有者对 Q-A5/Q-A3/Q-A4/Q-顺序 均答“接受”。**Q-A2（World）未获明确答复**，按推荐的 (a) 处理：`WorldState` 保持空占位
（无真实来源），由 `tests/test_phase4_state_placeholders.py::test_world_state_is_still_an_empty_placeholder` 和
`tests/test_phase10_sa_a2_runtime_capability_state.py::test_worldstate_stays_empty_placeholder` 守住；如需改为 (b) 随时可调。

| 文件 | 内容 |
|---|---|
| `core/runtime.py` | `RuntimeState`：`cycles_started/completed`、`last_cycle_status`、`last_cycle_started_at/finished_at`、`last_gap_item_count`、`last_learn_summary`——**每个字段都只来自 `RuntimeCycleStarted/Completed` 事件**，文件头有字段→来源表 |
| `core/capability.py` | `CapabilityState`：`tools`（`ToolRegistry.names()`）、`skills_available/active`（`SkillLoader.available/active`）、`workflows`（`WorkflowStore.list_all()` 的 `name`）、`refreshed_at`——只存名字，不是第二份真相来源 |
| `core/capability_projector.py`（新增） | `build_capability_state()`（纯读取）+ `refresh_capability_state()`（交给 `StateManager`）；三个来源均可选、单个来源失败互不影响、永不抛异常；**不会自己构造 `WorkflowStore`**（其 `__init__` 会 `mkdir`，有副作用，仅在调用方显式传入时才读） |
| `core/state_manager.py` | 订阅 `RuntimeCycleStarted/Completed`；`RuntimeState` 由事件从零累计（不需要外部 seed，不会出现“字段不完整”）；payload 类型不对时只跳过该字段 |
| `runtime/runtime.py` | ① `run_once()` 开头、发布 Started 事件**之前**幂等调用 `ensure_state_manager_subscribed()`（否则第一个周期的 Started 会丢，原订阅点在 `GoalRunner.run()` 内、晚于该事件）；② observe 步在 opt-in 时刷新 `CapabilityState` |
| `config/models.py` | 新增 `goal_mode.runtime_capability_snapshot_enabled`，**默认 False** |

**与 14 号文档第二节原方案的偏差（如实记录）**：原方案写“不改 `runtime.py`”。实际需要改两处小改动：订阅时序（①）和
opt-in 的快照刷新（②）。①无开关但幂等、纯内存、只读（`GoalRunner.run()` 本来就会做同样的订阅）；②默认关闭。
若不接线，`RuntimeState` 第一个周期会漏计、`CapabilityState` 则没有任何产出方。

**默认行为变化**：无。唯一可观察差异——`StateManager.snapshot()` 在第一次 `run_once()` 之后会多出 `runtime` 这个 key
（原先只有 `goal`）；`observe` 步只把 key 名写进 debug 日志，`detect_gap` 只读 `goal`，均不受影响。

**测试**
- 新增 `tests/test_phase10_sa_a2_runtime_capability_state.py` 15 用例：事件累计与并发计数差值、payload 类型不对不抛错、
  learn 摘要跨周期保留、三个来源可选且失败隔离、投影不产生磁盘副作用、真实 `ToolRegistry`/`WorkflowStore` 兼容、
  `run_once()` 端到端（**含第一个周期的 Started 被计到**）、快照默认关/开/刷新失败不影响周期、World 仍为空。
- **修改了既有测试**：`tests/test_phase4_state_placeholders.py` 里“占位必须是空 dict”的断言描述的是 Phase 4 事实，A2 有意改变了它——
  已改为：World 仍断言为空；Capability/Runtime 断言“已有字段，但托管/读回/snapshot 形状契约不变”。这是有意改断言，不是绕过失败。
- 定向回归（`test_core_*`/`test_phase*`/`test_capability*`/`test_persona*`/`test_goal_mode`/`test_cron*`/`test_slash*`/`test_api*`/`test_state*`）
  883 用例，877 通过；6 个失败均为既有（5 个 `test_build_from_history_*` + `test_draft_show_publish_full_flow`），无新增。
  （中途一度出现 5 个 Phase 4 占位断言失败，正是上一条“有意改断言”修好的。）`pyflakes` 无新告警；
  `lint_no_new_toplevel_concepts` 通过；`dep_graph.py --module core.state_manager` 深度 inbound=4，未触发止损。

**局限**：`CapabilityState` 只在开启开关后、每次 `run_once()` 的 observe 步刷新一次，不是实时；不含 workflows（observe 步不传
`workflow_store`，避免 `mkdir` 副作用），需要 workflows 时由调用方自己传入 `refresh_capability_state(..., workflow_store=...)`；
`RuntimeState` 只反映本进程内、经 `AgentRuntime.run_once()` 发起的周期（不含旧的 cron/AutonomousLoop 路径，那些已被评估为不接入）。

### 下一步

按确认的顺序：**A3（默认关闭 advisory）→ A4（评估后保留，仅文档）**（A5 见第九节；B3 已完成，见 `13-…` 第十节，它是 A5 投影的第一个生产调用方）。

## 九、执行记录（续）

### A5（2026-09-28）：Objective → GoalState 拉取式投影——已完成

按 Q-A5 的“接受”，采用**拉取式**，`evolution/objective_executor.py` 与 `perception/goal_backlog.py` **未改动一行**。

| 文件 | 内容 |
|---|---|
| `core/objective_adapter.py`（新增） | `ObjectiveAdapter.to_new(execution, node=None)`（单向；`to_old` 显式 `NotImplementedError`）+ `project_objective_executions(executor, backlog=None, publish_events=True)` |
| `core/events.py` | `EVENT_KINDS` 追加 `ObjectiveProjected`（因此会被 `EventLogStore` 落盘，可用 `events trace <execution_id>` 查看） |
| `core/__init__.py` | 导出 `ObjectiveAdapter`/`project_objective_executions`/`reset_objective_projection_cache` |

**只使用旧模块已有的只读公开方法**：`ObjectiveExecutor.get_status_summary()`、`get_execution()`、`GoalBacklog.get()`。

字段映射（每个字段都有真实来源，没有来源的不填）：

| GoalState 字段 | 来源 |
|---|---|
| `goal_text` / `round` | `objective_title` / `current_step_idx` |
| `status` | `pending/running/paused*` → `running`，`completed` → `done`，`failed`/`cancelled` 同名；**原始状态保留在 `evidence["objective_status"]`**（`GoalStatus` 没有 paused/pending，不丢信息） |
| `current_state` | 当前步骤的 `description`（截断 200 字） |
| `ideal_state` / `priority` / `evidence["parent_goal_id"]` | 仅在传入 `GoalNode` 时，取其 `description`/`priority`/`parent_id` |
| `problems` | 仅取 `failed`/`blocked` 步骤中非空的 `error_msg`（最多 5 条） |
| `evidence` | `execution_id`/`objective_id`/`level="objective"`/`steps_total`/`steps_done`/`current_step_idx`/`source` |
| `acceptance_criteria`、`gap`、`constraints`、`resources`、`deadline` | **不填**（Objective 没有对应概念） |

**与方案的偏差（如实记录）**
1. 方案写 `ObjectiveAdapter.to_new(ObjectiveExecution, GoalNode)`；实际 `node` 为**可选**第二参数，这样仍满足 `Adapter.to_new(old)` 协议签名。
2. 方案写 `current_step()`/`progress_ratio()` 是方法；核实代码后它们是 **property**，按 property 使用。
3. **事件做了去重**：方案只写“读取时发布”，但看板会轮询，每次读取都发会刷满 `events.jsonl`。实现为：仅当 `(objective_status, round, steps_done)` 相对上次发布发生变化时才发布（进程内缓存，重启后第一次读取会重发一次）。
4. **不写入 `StateManager` 的 `"goal"` kind**（那是 `GoalRunner` 当前 Goal 的托管位，写入会互相覆盖），有测试守住。

**默认行为变化**：无。全仓目前没有任何生产代码调用 `project_objective_executions()`——这是有意的：第一个真实消费者是 B3 的 `/v1/goals/{id}/steps`，届时再接。**在 B3 之前，这一步只是“能力就位”，不代表 HTTP/CLI 用户可见的行为有任何变化。**

**局限**
- `get_status_summary()` 会跳过“已终止且超过 1 小时”的记录，所以批量投影只覆盖活跃 + 最近 1 小时终止的；更早的记录需调用方自己 `get_execution(id)` 后传给 `to_new()`。
- 去重缓存仅在进程内，不跨进程/重启。
- 未实现推送式事件（方案已选拉取式）；`ObjectiveProjected` 不会在没人读取时产生。

**测试**
- 新增 `tests/test_phase10_sa_a5_objective_adapter.py` 25 用例：状态映射全取值 + 未知状态兜底、problems 只来自失败/阻塞步骤、空步骤不崩溃、`node` 补充与类型异常忽略、`to_old` 显式未实现、**真实 `ObjectiveExecutor`+`GoalBacklog` 投影**、完成一步后投影随之变化、**投影只读**（`objective_executions.json` 哈希不变、执行记录不变、`StateManager` 无 `goal`）、事件只发一次/变化后再发/关闭时不发、`EventLogStore` 真实落盘、执行器/单条记录/backlog 查询失败均不抛异常。
- 修改既有测试：`tests/test_core_events.py` 的 `EVENT_KINDS` 集合断言追加 `ObjectiveProjected`（有意的取值集合变化）。
- 定向回归（`test_core_*`/`test_phase*`/`test_objective*`/`test_goal_mode*`/`test_state*`/`test_cron*`/`test_autonomous*`）871 用例，866 通过；5 个失败均为既有的 `test_build_from_history_*`，无新增。**未跑全量测试**（其余需 streamlit/cdp_client 等本环境没有的依赖，另有 3 个测试文件因此无法收集，已排除）。
- `pyflakes` 无告警；`lint_no_new_toplevel_concepts` 通过；`dep_graph.py --module core.objective_adapter`：outbound=0，深度 inbound=1（仅 `core/__init__.py`），未触发止损；`check_frozen_evolution_modules.py --check-manifest` 6 个文件均未变。

## 十、执行记录（续）

### A3（2026-09-28）：默认关闭的 advisory 决策旁路——已完成

按 Q-A3 的“接受”实施：**默认关闭、advisory-only、不执行所选动作**。`goal_mode/runner.py`、`cognition/decision.py`、
`simulation/engine.py`、`core/simulation.py`、`core/action.py` 与 4 个 evolution 安全设施文件**均未改动**（只新增调用方）。

| 文件 | 内容 |
|---|---|
| `runtime/decision_stage.py`（新增） | `run_decision_stage()`：对**第一条 gap** 走 `generate_candidate_actions → simulate_candidates → DecisionEngine.select`，发布 `DecisionMade`，返回 `DecisionStageReport`。三个注入函数（生成/权衡/选择）用 `agent.llm_helper.ask()` 实现，LLM 输出只做 JSON 形状校验（非法 `type`/空 `capability`/越界或非整数 `index` 一律丢弃或判失败）。**永不抛异常** |
| `runtime/runtime.py` | 新增构造参数 `enable_decision_stage` 与开关读取；`plan/simulate/decide` 处的“显式跳过”改为“开关开启时调用旁路”；`AgentRuntimeResult.decision_report`；`RuntimeCycleCompleted` payload 仅在启用时多一个 `decision` 摘要键 |
| `config/models.py` | 新增 `goal_mode.runtime_decision_enabled`，**默认 False** |
| `core/events.py` | `EVENT_KINDS` 追加 `DecisionMade`（因此会被 `EventLogStore` 落盘）；payload 带 `advisory=True`、`executed=False` |
| `runtime/__init__.py` | 导出 `DecisionStageReport`/`run_decision_stage` |

**必须让使用者知道的边界**
1. **选出的动作不会被执行**——`GoalRunner` 不接受 `ActionSpec`。这是“决策记录”，不是“决策执行”。要让决策驱动执行需改 `GoalRunner`，另立方案（Q-A3 已明确不做）。
2. **LLM 调用次数比方案写的多**：第四节写“约 2–3 次”，**低估了**。实际为 `1（生成候选）+ N（每个候选各 1 次权衡描述）+ 1（选择）`，`N ≤ 3`，**上界 5 次**。测试 `test_max_candidates_bounds_llm_calls` 守住这个上界。
3. 一次运行只针对第一条 gap 决策一次，不遍历所有 gap（限制成本）。
4. 候选里的工具/工作流名称**不对照注册表校验**（它们只被记录，不会被执行）；日后若接入执行，必须在执行前校验。

**与方案的偏差（如实记录）**
- 方案写“整段 try/except，失败只记日志”。实现为两层：`run_decision_stage()` 自身永不抛异常（失败进 `report.error`，状态 `failed`），`run_once()` 再包一层兜底。
- 开启时 `run_once()` 会在旁路之前先挂载事件日志订阅（`ensure_event_log_subscribed`，幂等），否则 `DecisionMade` 发布时还没有订阅者、不会落盘。未开启时不做这件事，默认行为不变。
- 旁路使用 `AgentPaths(project_root=cfg.project_root)` 下的 Experience 库做历史检索（与 `GoalRunner` 用的是同一个库），不使用进程默认路径。
- “旧 Advisor 候选 → `ActionSpec` 的 Adapter”按方案推荐**本轮不做**（无消费者，且 `next_action_advisor` 候选结构未逐字段核实）。
- 顺手更正 `runtime.py` 顶部 docstring 里从未作为代码存在过的 `enable_decision_stage` 开关名（实际开关是 `runtime_decision_enabled`；构造参数沿用 `enable_decision_stage` 与 `enable_learn_stage` 同风格）。

**默认行为变化**：无。关闭时不发起任何 LLM 调用、不挂载额外订阅、payload 与返回值（`decision_report=None`）与此前一致；有测试守住。

**测试与验证**
- 新增 `tests/test_phase10_sa_a3_decision_stage.py` **27 用例**：JSON 解析（围栏、非法项丢弃、单元素数组、对象包装、垃圾输入）、无 gap/无 llm_helper 不调用 LLM、happy path 与 `DecisionMade` 事件字段、调用次数上界、**`ActionExecutor.execute` 不被调用**、三个阶段各自 LLM 失败被隔离且不发布事件、无有效候选、越界/非整数/bool/非 JSON 的选择输出、非 JSON 对象的权衡输出、payload 文本截断、默认关闭零变化、经配置/构造参数开启、覆盖优先级、`DecisionMade` 真实落盘且先于 `RuntimeCycleCompleted`、无 `llm_helper` 的 Agent 不影响 Goal、LLM 异常与旁路崩溃均不影响 Goal 结果。
- **测试发现并已修复一个实现 bug**：`_extract_json` 最初总是先尝试 `{…}`，LLM 只返回**一个**候选（`[{…}]`）时内层对象会抢先匹配，候选被误判为空。已改为按“最先出现的括号”解析，并补了回归用例。
- 修改既有测试：`tests/test_core_events.py` 的 `EVENT_KINDS` 集合断言追加 `DecisionMade`（有意的取值集合变化）。
- 定向回归（`test_core_*`/`test_phase*`/`test_goal_mode*`/`test_state*`/`test_cron*`/`test_runtime*`）：改动后 695 passed / 7 failed / 3 errors；**未修改的原始压缩包同批**：668 passed / 7 failed / 3 errors，**失败与错误集合逐条一致**（差值 27 即本次新增用例）。失败为既有的 5 个 `test_build_from_history_*` 与 2 个 `test_cron_async_user_feedback`；3 个 ERROR 是本环境缺 `uvicorn`，无法收集，与本次无关。**未跑全量测试**；前端与需 streamlit 的看板测试未运行。
- `pyflakes`：新增/改动的 `runtime/`、测试文件无告警（`config/models.py` 有 2 条既有的未使用 import，非本次引入）；`lint_no_new_toplevel_concepts` 通过；`dep_graph.py --module runtime.decision_stage`：outbound=0，深度 inbound=2（`runtime/__init__.py`、`runtime/runtime.py`），`runtime.runtime` inbound=3，均未触发止损；`check_frozen_evolution_modules.py --check-manifest` 6 个文件均未变。

**局限**
- 决策**从未在真实 LLM 上跑过**：测试用的是脚本化替身，只证明链路、解析、隔离与不执行的性质，不证明真实模型的输出质量，也不证明提示词效果。开启前建议在小范围内人工检查几条 `DecisionMade` 的 `trace_text`。
- `RuntimeCycleStarted` 在事件日志订阅之前发布，**首个周期的 Started 事件不会落盘**——这是 Phase 8 起的既有行为，本 Sprint 未改（改它会改变默认行为）；`DecisionMade` 与 `RuntimeCycleCompleted` 会落盘。
- 决策只看第一条 gap；`current_state` 目前多数为空（旧 `GoalSpec` 没有对应字段），权衡描述的上下文因此较薄。
- 没有面向用户的出口：`DecisionMade` 只出现在事件日志（可用 `events trace <correlation_id>` 查看）与 `AgentRuntimeResult.decision_report`，CLI/HTTP 不展示。

### 下一步

**A4**（`goal_mode/executor.py` “评估后保留、不迁移”，**仅文档**：台账改“已评估，不迁移”并写明理由）。之后 S-A 除 A1（需所有者在真实 git 仓库执行）外全部完成，可进入 S-B 其余批次或 S-C 的复核。

## 十一、执行记录（续）

### A4（2026-09-28）：`goal_mode/executor.py`——评估后保留、不迁移（仅文档，未改任何代码）

按 Q-A4 的“接受”执行推荐做法。**本步没有修改任何生产代码或测试**，只做核实与台账更新。

**核实结果（2026-09-28，直接读代码与跑脚本）**

| 项 | 结果 |
|---|---|
| 文件规模 | **71 行**（第一节写“约 60 行”，略低估；结论不受影响） |
| 内容 | `GoalStepResult`（dataclass）、`GoalStepExecutor`（ABC，唯一抽象方法 `execute(agent, prompt)`）、`CoarseStepExecutor`（调用一次 `agent.run_turn(prompt)`，读取 `agent.stats.turns/tool_calls` 前后差值与 `last_turn_hit_max_turns`） |
| 依赖 | `dep_graph.py --module goal_mode.executor`：outbound=0；深度 inbound=3（`goal_mode/__init__.py` 再导出、`goal_mode/runner.py`、`runtime/runtime.py`），**未触发止损**。其中 `runtime/runtime.py` 只在 `TYPE_CHECKING` 下引用类型、把 `executor` 参数透传给 `GoalRunner`，不是运行期依赖 |
| 使用方式 | `GoalRunner.__init__` 默认 `executor or CoarseStepExecutor()`；`AgentRuntime.run_once()` 透传；测试里有替换/注入用例（`test_goal_mode.py`、`test_goal_mode_phase2_events.py`、`test_goal_mode_characterization.py`） |
| 回归（本步开始前的事实核对） | `test_goal_mode_characterization.py` + `test_goal_mode.py`：121 passed / 5 failed，5 个失败即既有的 `test_build_from_history_*`，与此前一致 |

**结论：保留，不迁移。** 理由（逐条对应第五节）：
1. 它本身就是“可替换的策略接口”——Phase 1 起就为“细粒度版本”预留，已经是新旧架构都能接受的形态，不是需要被 Adapter 收编的旧概念。
2. `ActionSpec.type` 只有 `tool/workflow/subagent`。“驱动持有会话历史与 stats 的**主 Agent** 跑一轮”与 `type="subagent"`（转发给 `build_minimal_agent()` 新建的**独立**最小 Agent）语义不同，不能等同。
3. 给 `ActionSpec` 加 `agent_turn` 会改动 Phase 6 核心类型，而收益只是让这一个类改走 `ActionExecutor`；`GoalStepResult → ActionResult` 转换目前没有任何消费者，属推测性设计。均不做。
4. 重评触发条件（记录在此，避免以后凭印象翻案）：出现**真实消费者**需要用统一 Action 词汇表达“一步 Goal 迭代”，例如把 A3 的 `DecisionMade` 升级为“决策真正驱动执行”并需要执行所选 `ActionSpec` 时。

**与方案的偏差（如实记录）**：方案写“台账改‘已评估，不迁移’”。但 `12-execution-and-doc-sync-norms.md` 第三节规定状态只允许 `未开始/部分迁移/完全迁移/已降级到 legacy` 四种，台账页脚也明确写“判定暂不迁移的模块应把状态维持在‘未开始’并附注原因”。因此台账里状态**仍写“未开始”**，“已评估，决定不迁移”写在同一格括号与“走新链路占比”格里。（台账里已有的 `goal_cycle/AutonomousLoop` 行使用了“已评估，不建议接入”这一自造状态词，是 Sprint 8-5 遗留的不一致，本步**未改动**，仅在此提示。）

**对 Phase 10 前置条件的影响**：`11-phase10-legacy-decommission-plan.md` 前置条件核对里“`goal_mode/executor.py` 未开始”这一项现在有了明确结论（评估后不迁移，不再是“待做”）；但前置条件整体**仍未满足**（A1 待所有者、`objective_executor.py`/`orchestrator/*`/`goal_backlog.py` 未变），D1–D5 仍待所有者决定。已在 `11` 号文档追加带日期的更新说明。

### 下一步

S-A 的 A2–A5 均已完成。剩余需要**所有者**参与的事项：
- **A1**：在真实 git 仓库运行 `python scripts/check_frozen_evolution_modules.py --base <Phase 9 起点 commit>`，通过后勾选 Phase 9 完成标志第 3、4 条（本环境的压缩包没有 `.git`，无法代劳）。
- **S-B 其余批次 / S-C**：按 `13-…` 的分批计划，需要所有者确认起步批次后再继续；同时建议先决定 D1–D5 中仍开放的项。

