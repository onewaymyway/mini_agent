# Phase 10 S-A 分项方案（补齐前置条件）——待所有者确认后实施

> 状态：**方案，未实施，未改任何代码。** 承接 `13-phase10-post-decision-execution-plan.md` 第八节：
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
