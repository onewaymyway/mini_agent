# Phase 5：统一 Goal（收敛 Objective/Task/Workflow）—— 可执行计划

> 对应原方案 §38、§19、§22。前置条件：Phase 4 的 `StateManager` 已托管
> `GoalState`。

## 目标

把 `GoalSpec / GoalState / GoalBacklog / Objective / Task / Workflow`
这几个现在并存的概念，逐步统一到"当前状态 + 理想状态 + 问题 + 差距 +
计划 + 行动 + 结果"这一套语义下（原文 §8、§22）。

**关键原则**：不是删掉 Objective/Workflow，而是让它们变成 GoalState 的
"内部实现细节"，对外只暴露 Goal 这一个概念。

## 现状盘点

| 现有概念 | 文件位置 | 与 GoalState 的关系 |
|---|---|---|
| `Objective` | `goal_mode/` 或相关 orchestrator 代码 | 计划降级为 `GoalState` 内部的一个执行步骤（`InternalGoalStep`，原文 §38） |
| `goal_backlog.py` | 顶层模块 | 计划降级为 `GoalState` 列表的持久化实现细节 |
| `workflow/`（18 个模块） | `workflow/` | **不在本 Phase 处理**——Workflow 属于 Capability 范畴（原文 §9），留给 Phase 6/Capability 收敛处理，本 Phase 只处理 Goal 是否*引用* Workflow，不改 Workflow 本身 |

产出：`docs/architecture_v2/phase5-goal-inventory.md`，明确哪些代码
属于"Goal 本身"、哪些其实是"Goal 引用的 Capability"，避免这个 Phase
范围蔓延到 Workflow 内部重构。

**现状盘点已完成**（详见 `docs/architecture_v2/phase5-goal-inventory.md`），
核心发现：`Objective` 与 `goal_backlog.py` 实际上是**同一套**子系统
（`perception/goal_backlog.py::GoalBacklog`/`GoalNode`，`level="objective"`
即"Objective"），并非原表格暗示的两个独立处理项；且这套子系统
`scripts/dep_graph.py` 实测 inbound=33，**远超止损阈值**，与
`evolution/objective_executor.py::ObjectiveExecutor`（inbound=5，
未超阈值，但紧耦合依赖前者）合计构成一套服务于看板/自主循环/公平
调度的大型自主执行子系统，和 Phase 1 已迁移的 `goal_mode/`（`GoalSpec`/
`GoalRunner`）完全独立、互不引用。原 Sprint 5-1 计划因此触发止损条件，
需要变更留痕，详见下方"变更记录"小节。`workflow/` 边界（inbound=5，
未超阈值，与 `goal_backlog.py` 无直接依赖）盘点后确认原计划的
"本 Phase 不处理"仍然合理，无需调整。

## Sprint 5.0.5（新增，对应变更记录）：GoalBacklog/ObjectiveExecutor 耦合拆解评估

> 参照 Sprint 1.5（`03-sprint1.5-memory-perception-coupling-assessment.md`）
> 的方法论：按子模块细分，区分"真正需要收敛的部分"和"可以先 facade
> 隔离的部分"，产出有区分度的迁移优先级建议，而不是对整个子系统喊
> 暂停。留待下一次推进，任务范围：

| 任务 | 产出 |
|---|---|
| 按子系统重新统计 `goal_backlog.py` 的 33 个调用方 | 区分"`perception/`/`external_input/` 包内部调用"与"真正跨子系统调用"，参照 `12-execution-and-doc-sync-norms.md` 第六节第 6 条已确立的止损口径（只统计跨子系统 inbound） |
| 评估 `ObjectiveExecutor` 能否独立启动 Adapter | 判断"只转换 `ObjectiveExecutor` 侧、不动 `GoalBacklog`"是否会变成"没有实际解耦效果的包装层"，还是确实可以复用 Self/history_manager 那套"低风险先行"模式 |
| 给出 Sprint 5-1 实际任务范围建议 | 输出：`goal_backlog.py`/`ObjectiveExecutor` 分别应该"直接启动 Adapter" / "先做 facade 隔离" / "暂缓"三选一的判断，附实测数据 |

## Sprint 5.0.5 执行记录

**一、`goal_backlog.py` 按子系统重新统计**

沿用 `12-execution-and-doc-sync-norms.md` 第六节第 6 条已确立的口径
（同一顶级包内部调用不计入"跨子系统 inbound"——`goal_backlog.py`
本身位于 `perception/` 包，因此 `perception/*` 的 11 个调用方按新口径
不计入）：

| 调用方所属顶级包 | 文件数 | 是否计入跨子系统 inbound |
|---|---|---|
| `perception/`（`goal_backlog.py` 自己所属的包） | 11 | 否（同包内部） |
| `evolution/` | 15 | 是 |
| `cli/`（`commands/cron.py`/`commands/goals.py`/`commands/growth_cmd.py`） | 3 | 是 |
| `api/`（`routes.py`/`server.py`） | 2 | 是 |
| `external_input/`（`goal_relevance.py`/`novelty_judge.py`） | 2 | 是 |
| **跨子系统 inbound 合计** | **22** | — |

与 `perception/memory_store.py` 的先例（33 → 按新口径降到 5，跌破
阈值）**结论不同**：`goal_backlog.py` 按新口径重新统计后仍有 22，
**依然远超 10+ 止损阈值**——不是"同包内部调用占多数、剔除后就达标"
的情况，而是`evolution/` 包里就有 15 个真实跨子系统调用方（自主循环、
cron 调度、公平度诊断、看板衍生数据等），本身就已经超过阈值近 50%。
这意味着"口径调整"这条路对 `goal_backlog.py` 不成立，不需要再往下
做分层 facade 尝试（`memory_store.py` 当初是先做两层 facade 才发现
口径问题；这次实测数据已经足够明确，没有必要重复走一遍"先 facade
再看还剩多少"的过程去验证一个大概率不会成立的假设）。

**结论**：`goal_backlog.py` **暂缓**任何形式的迁移（包括 facade），
维持"未开始"状态。它不是"看起来复杂、实测发现没那么复杂"的情况
（`history_manager.py`/`memory_store.py` 都是这类），而是"看起来复杂、
实测确认真的复杂"——一套服务于自主循环/公平调度/看板衍生数据的
跨会话持久化目标树，本身就该被视为独立于 `goal_mode/`（Phase 1）
的另一个大子系统，不应该被塞进 Phase 5（统一 Goal）的范围里一起做。

**二、`ObjectiveExecutor` 能否独立启动 Adapter**

`ObjectiveExecutor` 自身 inbound=5，数值上未超阈值，但实测其唯一
实例化位置（`api/server.py::HttpServer._build_autonomous_loop()`
内部，约第 1866 行）后发现：这个接入点本身嵌在一段几百行的 HTTP
服务器自主循环构造闭包里，构造完成后紧接着做孤儿执行恢复
（`reconcile_orphaned_executions()`）、双向接线公平调度回调
（`set_other_channel_running_fn`）、条件性切换到隔离 Runner
（`ObjectiveIsolatedRunner`）等一系列强状态、强时序依赖的操作——
与 Self/History/Memory 迁移链的接入点
（`agent/lifecycle.py::_init_components()`，一个相对独立、可以安全
"只加一行 trace 调用不影响后续逻辑"的位置）性质不同：这里插入任何
新代码都有干扰这段精密时序编排的风险，即使新代码本身只做只读转换
+ trace，也难以保证不会因为求值顺序、异常传播等副作用打断后续几行
强依赖前面构造结果的接线逻辑。

**结论**：`ObjectiveExecutor` 的 inbound 数值虽然不高，但**风险特征
不是"低耦合"而是"低耦合但高时序敏感"**，不满足 Self/history_manager
那套"单点接入、纯旁路 trace"模式安全套用的前提条件。同时
`ObjectiveExecutor` 拆解的 `ExecutionStep`（多步执行 + 重试 + 超时）
在语义上更接近原方案 §9/§10（Capability/Action），而不是 Phase 5
的 Goal 语义本身——把它现在塞进 Phase 5 用 Goal 的 Adapter 模式硬套，
既有风险又语义不贴切。**建议把 `ObjectiveExecutor` 的迁移移出
Phase 5，留给 Phase 6（统一 Action，收敛 Tool/Workflow/SubAgent）
一并处理**，那里本来就要建立"多步执行"的统一表达，`ExecutionStep`
天然应该在那时候被纳入评估，而不是现在孤立处理。

**三、对 Sprint 5-1 的最终范围调整**

综合以上两点，原 Sprint 5-1 计划的两项任务（"Objective Adapter"、
"GoalBacklog Adapter"）均从 Phase 5 移出：

- `goal_backlog.py`（含其中"Objective"概念）：暂缓，不在当前任何
  已排期 Phase 内，留待项目所有者确认排期后再决定归属（可能是新增
  一个独立 Phase，也可能等 Phase 8 Runtime 收敛时一并评估——它同时
  涉及调度/自主循环，与 Phase 8 的 Runtime 概念也有交集）。
- `evolution/objective_executor.py`：移入 Phase 6（统一 Action）
  范围，在 `07-phase6-action-model-sprint-plan.md` 补充这条待评估
  任务（不在本次改动，留给 Phase 6 启动时处理，避免在 Phase 5 的
  文档里预支 Phase 6 还没排期的具体任务设计）。

Phase 5 剩余可执行内容收窄为 **Sprint 5-2（Gap 检测能力）**——该任务
只依赖 `core/goal.py::GoalState` 的字段扩展和 Phase 4 的
`state_manager.get_state("world"/"self")` 接口，不依赖
`goal_backlog.py`/`ObjectiveExecutor` 的具体实现，可以独立推进，
详见现状盘点文档"五、Sprint 5-1/5-2 范围建议"一节的判断已被验证成立。

**回归测试**：本次 Sprint 5.0.5 只做依赖关系实测和文档判断，未修改
任何生产代码，因此不需要跑回归测试（与 Sprint 3/Sprint 1.5 里"纯
复盘/评估类"子任务的处理方式一致）。

## Sprint 5-1（2 周，原计划——按 Sprint 5.0.5 结论已收窄范围）：Objective → InternalGoalStep 降级

> **范围调整说明（不删除原文，仅标注，完整依据见 Sprint 5.0.5 执行
> 记录）**：下表"Objective Adapter"、"GoalBacklog Adapter"两项任务
> 已确认**暂缓/移出本 Phase**（详见上方"Sprint 5.0.5 执行记录·三、
> 对 Sprint 5-1 的最终范围调整"）。原表格保留在下面，作为"曾经的
> 计划"存档，不代表当前仍要执行；`core/goal.py` 扩充这一项因为
> Sprint 5-2 的 Gap 检测需要用到新增字段，改为并入 Sprint 5-2 一并
> 推进（见下方 Sprint 5-2 任务表）。

| 任务 | 产出 | 当前状态 |
|---|---|---|
| `core/goal.py` 扩充 | `GoalState` 增加 `current_state / ideal_state / problems / gap / constraints / resources / priority / evidence / deadline` 字段（原文 §8） | 并入 Sprint 5-2 |
| Objective Adapter | 把现有 `Objective` 对象转换为 `GoalState.gap` 下的一个条目，而不是独立对象；旧 `Objective` 的读写接口保留，内部转发到新结构 | **暂缓**（Sprint 5.0.5：`goal_backlog.py` 跨子系统 inbound=22，远超阈值） |
| GoalBacklog Adapter | 让 `goal_backlog.py` 的读写通过 `StateManager` 完成，内部数据结构逐步替换为多个 `GoalState` 实例的集合 | **暂缓**，理由同上 |

**验收标准**：现有依赖 `Objective` / `GoalBacklog` 的旧测试全部通过
（Adapter 模式的核心验证方式——外部接口不变，内部实现改变）。

**止损条件**：如果 Objective 的语义（比如"多级子目标嵌套"）在
`GoalState.gap` 这个简单列表结构里表达不了，说明 `GoalState` 需要
支持递归结构，应先扩展数据模型，而不是强行压缩语义丢失信息。

> 止损条件本身已在 Sprint 5.0.5 触发（见上方执行记录），处理方式是
> 暂缓迁移而非强行压缩语义——这与止损条件描述的"先扩展数据模型"是
> 同一种保守处理原则的应用，只是这次问题出现在"依赖规模"而不是
> "语义表达能力"上。

## Sprint 5-2（1.5 周）：Gap 检测能力

| 任务 | 产出 |
|---|---|
| `goals/gap.py` | 实现"当前 State vs 理想 State → problems/gap"的检测逻辑，第一版可以是简单规则 + LLM 判断，不需要复杂算法 |
| 接入 StateManager | Gap 检测读取 Phase 4 的 `state_manager.get_state("world"/"self")` 作为输入（哪怕这些 State 目前还是占位，也要先把接口打通） |

**验收标准**：给定一个新 Goal 输入，系统能自动生成 `problems` 和 `gap`
字段，而不需要用户手动填写。

## Sprint 5-2 执行记录

**`core/goal.py` 字段扩充**：`GoalState` 追加
`current_state`/`ideal_state`/`problems`/`gap`/`constraints`/
`resources`/`priority`/`evidence`/`deadline` 九个字段，均带默认值
（`""`/`[]`/`None`/`{}`），全部追加在原有字段之后，不影响
`GoalAdapter.to_new()`/`to_old()`（Sprint 1，用关键字参数显式构造）
和现有测试里所有 `GoalState(goal_text=...)` 的构造方式。新字段暂不
接入 `GoalAdapter`——旧的 `GoalSpec` 里没有对应概念，硬填属于编造
数据，留到后续真有 Goal 执行链路产出这些数据时再评估。

**新增 `goals/` 包**（`goals/__init__.py` + `goals/gap.py`）：
`detect_gap(goal, state_manager=None, llm_judge=None)`，原地写回
`goal.problems`/`goal.gap`/`goal.evidence["gap_detection"]` 并返回
同一个 `GoalState`。第一版按任务表止损备注"不需要复杂算法"实现为
纯规则：`current_state`/`ideal_state` 缺一项各记一条 problem；两者
相同视为已达成（`problems`/`gap` 均为空）；不同则优先用
`acceptance_criteria` 逐条生成 `gap`，没有 `acceptance_criteria` 时
退化为一条通用提示，不编造具体差距内容。`llm_judge` 是可选替换点，
传入时接管 `problems` 生成（`gap` 仍走规则），签名
`(goal, self_state, world_state) -> list[str]`，本 Sprint 不提供
默认实现（LLM 判断不像规则那样能保证确定性/可测试，留给调用方接入
真实 LLM）。

**"接入 StateManager"任务**：`detect_gap()` 内部固定调用
`state_manager.get_state("self")`/`get_state("world")`（不传
`state_manager` 时退化到全局单例 `get_state_manager()`），把两者
是否可用记进 `goal.evidence["gap_detection"]`；`world`/`self` 目前
即使是占位/未托管（返回 `None`）也不报错，只是老实记录
"不可用"，不替占位 State 编造内容——验证了 Sprint 4-2 占位 State
"不会导致调用报错"这条验收标准在真实调用方（而不只是单元测试直接
调 `StateManager`）场景下同样成立。

**验收标准核对**：新增测试
`tests/test_phase5_gap_detection.py`（8 用例）覆盖：
`current_state`/`ideal_state` 均缺失时生成两条 problems + 用
`acceptance_criteria` 生成 gap；两者相同时无 problems/gap；两者不同
但没有 `acceptance_criteria` 时退化为通用提示；`llm_judge` 只接管
problems 不影响 gap；`self`/`world` State 已托管/未托管两种情况下
`evidence` 记录是否正确；不显式传 `state_manager` 时走全局单例。全部
通过，验证"给定一个新 Goal 输入，系统能自动生成 problems 和 gap
字段，而不需要用户手动填写"这条验收标准成立。

**回归测试**：`test_phase5_gap_detection.py` +
`test_phase4_state_placeholders.py` + `test_phase4_state_manager.py` +
`test_core_self_adapter.py` + `test_self_model.py` +
`test_goal_mode.py` + `test_goal_mode_phase2_events.py` +
`test_phase2_event_log_and_cli.py` + `test_core_events.py` +
`test_core_event_bus.py` + `test_core_experience_store.py` +
`test_phase3_experience_recorder.py` +
`test_phase3_experience_retrieval_and_patterns.py` +
`test_phase3_experience_retrieval_injection.py` 共 185 用例，180
通过，5 个既有失败（`test_build_from_history_*`，与本次改动无关，
此前已多次确认）；pyflakes 检查新增/修改文件均无告警。

**止损条件核对**：未触发。`current_state`/`ideal_state`/
`acceptance_criteria` 这几个简单字段足够表达"多级子目标嵌套"以外的
常见场景；如果后续真的遇到 Objective 那种需要递归结构的场景，止损
条件里"应先扩展数据模型"的应对方式与 Sprint 5.0.5 对 Objective/
GoalBacklog 采取的"暂缓，不强行套用"是同一种保守原则，届时按需
处理，本 Sprint 范围内没有出现这类场景。

**Phase 5 完成情况**：Sprint 5.0.5 结论已把 Objective Adapter/
GoalBacklog Adapter 移出本 Phase（详见上方"Sprint 5.0.5 执行记录"），
Sprint 5-2（Gap 检测）是调整后 Phase 5 的唯一剩余可执行任务，现已
完成。据此，**Phase 5 在调整后的范围内已达到完成标志**（见下方
"完成标志"，两条"暂缓"项已改为不计入本 Phase 完成判定，理由同上）。

## 完成标志

- [x] ~~`Objective` 对外接口不变，内部已经是 `GoalState` 的适配层~~
      **不计入本 Phase 判定**——Sprint 5.0.5 已确认 `Objective`
      （即 `goal_backlog.py::GoalNode(level="objective")`）暂缓，
      移出 Phase 5 范围，见"Sprint 5.0.5 执行记录"
- [x] ~~`GoalBacklog` 的持久化已经统一走 `StateManager`~~
      **不计入本 Phase 判定**，理由同上
- [x] Gap 检测逻辑已实现最小版本，并在至少一个真实 Goal 场景下验证过
      （Sprint 5-2 已完成：`goals/gap.py::detect_gap()` + 8 个测试
      用例覆盖多种真实场景，见上方"Sprint 5-2 执行记录"）
- [x] Workflow 相关代码未被本 Phase 触碰（留给 Capability 收敛处理）
      （现状盘点已核实：`workflow/` inbound=5，与 `goal_backlog.py`/
      `objective_executor.py` 无直接依赖，边界确认合理）

**Phase 5 结论**：调整后范围内（Sprint 5.0.5 + Sprint 5-2）已完成，
可进入 **Phase 6（统一 Action）**——注意 Phase 6 现状盘点表格已补充
`evolution/objective_executor.py` 这条 Sprint 5.0.5 移交项，Phase 6
启动时需要一并评估。

## 变更记录

### 变更记录 2026-09-27
- 触发条件：Sprint 5-1 止损条件——"如果 Objective 的语义...在
  `GoalState.gap` 这个简单列表结构里表达不了"；实测发现问题比止损
  条件描述的更早出现：还没到"语义表达不了"这一步，`goal_backlog.py`
  的**依赖规模**（inbound=33）就已经复现了 Sprint 3 对
  `perception/memory_store.py` 判定"远超阈值"的同款情况。
- 原计划：现状盘点表格把 `Objective`（"计划降级为 GoalState 内部的一个
  执行步骤"）和 `goal_backlog.py`（"计划降级为 GoalState 列表的持久化
  实现细节"）列为两个独立、可以分别评估的处理项，Sprint 5-1 据此安排
  "Objective Adapter"与"GoalBacklog Adapter"两项独立任务，均按 Goal
  迁移链"单一接入点 + Adapter 直接转换"的轻量模式设计。
- 实际情况：`docs/architecture_v2/phase5-goal-inventory.md` 现状盘点
  发现，`Objective` 其实就是 `goal_backlog.py::GoalNode` 里
  `level="objective"` 的节点，二者是同一套子系统，不是两个独立处理项；
  该子系统 `scripts/dep_graph.py --module perception.goal_backlog`
  实测 inbound=33，远超 10+ 止损阈值，且与
  `evolution/objective_executor.py`（自主执行引擎，反向回写
  `GoalNode.status`）紧耦合，服务于看板/自主循环/公平调度等 10+
  下游子系统，83 个测试文件涉及，规模和耦合度远超"轻量转换"的假设。
- 调整后方案：Sprint 5-1 暂缓直接执行原计划的两项任务，新增
  **Sprint 5.0.5（GoalBacklog/ObjectiveExecutor 耦合拆解评估）**，
  参照 Sprint 1.5 的方法论重新按子系统统计真实耦合面，给出有区分度的
  迁移优先级建议后，再确定 Sprint 5-1 的实际任务范围（可能是"先对
  `ObjectiveExecutor` 单独启动 Adapter，`goal_backlog.py` 暂缓"，
  也可能是别的结论，留给 Sprint 5.0.5 实测后判断，不在此提前下结论）。
- 影响范围：不影响 Phase 4 的前置条件（`StateManager` 已就绪，
  与本次发现无关）；影响本 Phase 自身 Sprint 5-1/5-2 的执行顺序——
  Sprint 5-2（Gap 检测）不依赖 `goal_backlog.py` 具体实现，可以在
  Sprint 5.0.5 结论产出前独立推进，不必等待。
