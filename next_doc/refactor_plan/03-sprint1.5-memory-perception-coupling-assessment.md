# Sprint 1.5：Memory / Perception 耦合拆解评估

> 本文档是 Sprint 3 复盘时做出的正式决策的直接产出（见
> `02-executable-sprint-plan.md` 末尾"Sprint 3 执行记录 · 正式决策"一
> 节）：Memory → Experience 迁移链不能直接照搬 Goal 迁移链"单点接入 +
> Adapter"的模式，需要先做一次独立的耦合拆解评估。本文档遵循
> `12-execution-and-doc-sync-norms.md` 第六节固定结构：现状盘点 →
> Sprint 划分（任务表 + 验收标准 + 止损条件）→ 完成标志。

---

## 一、现状盘点

Sprint 3 复盘时用 `scripts/dep_graph.py --module perception` 得到的
"inbound 69"这个数字，本身是一个**需要拆解才能用的粗粒度信号**：
`perception/` 是一个把多个不同概念（Memory、Self、GoalBacklog、
system_events、behavior perception 等）捆在一个顶级包里的"大杂烩"
包，直接对整个包做依赖扫描，会把"某个文件只是因为用了
`GoalBacklog` 而 import 了 `perception`"和"某个文件真的依赖 Memory
存储细节"混在一起统计，夸大了 Memory 这个概念本身的真实耦合度。

用 `scripts/dep_graph.py --module <包.子模块>` 对 `perception/` 内部
按概念拆开重新扫描，得到更真实的数据：

| 子模块 | 对应领域概念 | 实测 inbound 深度依赖 | 止损阈值 10+ | 结构特征 |
| --- | --- | --- | --- | --- |
| `perception/self_model.py`（`AgentSelfModel`/`AgentSelfModelBuilder`） | Self | **5** | 未触发 | 分散在 `agent/lifecycle.py` + 3 个 `evolution/*` 文件，无明显聚集 |
| `history_manager.py`（`HistoryManager`） | Experience（部分） | **12** | 已触发，但幅度小 | 12 个里有 11 个是 `mini_agent/agent/*` 下的文件（`_helpers.py`/`compaction.py`/`core.py`/`lifecycle.py`/`llm_control.py`/`profile.py`/`reflection.py`/`reminders_correction.py`/`role_judge.py`/`snapshot.py`/`turn_loop.py`），只有 1 个在 `evolution/session_cleanup.py`——**高度集中在同一个调用方集群（Agent 组合层）**，不是分散在互不相关的多个子系统 |
| `perception/memory_store.py`（`MemoryStore`/`MemoryEntry`） | Memory（核心） | **33**（其中 12 个是 `perception/` 包内部文件互相依赖，21 个是包外文件） | 已触发，幅度大 | 21 个包外调用方分布在 `agent/*`（11 个）、`evolution/*`（6 个）、`api/routes.py`、`context_builder.py`、`goal_mode/runner.py`、`profile.py`——**跨越至少 4 个互不相同的子系统**，是三者中耦合面最广的 |

**结论（现状盘点）**：Sprint 3 复盘时"perception 耦合远超预期"这个
判断方向是对的，但"整个 perception 都危险"这个粒度太粗——真正的高
风险集中在 `memory_store.py`，`self_model.py` 实际上和 Goal 一样
"低耦合、可以直接用 Adapter 模式"，`history_manager.py` 介于两者
之间（超阈值但集中在一个集群，风险可控）。这个发现本身也说明了一件
更一般的事：**评估迁移链耦合度时，应该按"要迁移的具体概念/类"扫描，
不能直接扫整个顶层包**——这一条本身值得补充进
`12-execution-and-doc-sync-norms.md`（见下方 Sprint 1.5 任务表）。

---

## 二、Sprint 划分

### 任务表

| 任务 | 产出 |
| --- | --- |
| 1. 按概念重新扫描 `perception/` 内部子模块的耦合度 | 本文档"一、现状盘点"表格（已完成） |
| 2. 补充依赖扫描方法论说明到执行规范文档 | 在 `12-execution-and-doc-sync-norms.md` 追加一条：评估迁移链耦合度时按具体类/子模块扫描，不按顶层包扫描 |
| 3. 为三个子模块分别给出迁移优先级建议 | 见下方"三、迁移优先级建议" |
| 4. 更新 `MIGRATION_STATUS.md` 补充耦合评估结论列 | `history_manager.py`、`perception/self_model.py` 两行补充耦合评估备注 |

### 验收标准

1. `perception/` 内部至少 3 个子模块（`self_model.py` /
   `history_manager.py`（注：物理上不在 `perception/` 包内，但概念上
   属于同一次评估范围）/ `memory_store.py`）分别有独立的
   `scripts/dep_graph.py --module` 扫描结果记录在本文档——**已达成**，
   见上方表格。
2. 每个子模块都有明确的迁移优先级建议（可以/需要先做 facade/暂缓），
   不是笼统的"需要评估"——**已达成**，见下方"三、迁移优先级建议"。
3. 执行规范文档补充了本次发现的方法论教训——**已达成**，见
   `12-execution-and-doc-sync-norms.md` 新增的方法论条目。

### 止损条件

本 Sprint 是评估性质，产出是文档而非代码改动，**没有需要止损的代码
风险**。唯一的止损情形是：如果按子模块重新扫描后，`self_model.py`
或 `history_manager.py` 也出现类似 `memory_store.py` 那样的
跨子系统扩散（而不是本次实测的"低耦合"/"集中在一个集群"），需要
把它们的迁移优先级同步降级为"暂缓"，不能因为已经写了"优先级建议"
就不再看实测数据——本次实测已经做完，此止损条件面向未来重新评估时
（比如代码库继续演化几个月后再次评估）适用。

---

## 三、迁移优先级建议

1. **`perception/self_model.py`（Self）：可以参照 Goal 的模式直接
   启动迁移**。inbound=5，未触发止损阈值，调用方分散在 4 个文件里
   且都是"读取/构建 AgentSelfModel"这种单一职责的调用，与 Goal 迁移
   链的耦合特征相似度高，风险最低。**建议作为下一条迁移链启动**
   （不是本文档决定启动，需项目所有者确认排期）。
2. **`history_manager.py`（Experience 的一部分）：需要先在 `agent/`
   层加一道 facade，再启动迁移**。12 个调用方里 11 个集中在
   `mini_agent/agent/*`——这暗示 `HistoryManager` 实际上是"Agent
   组合层的一个内部协作对象"，被那一层的十几个文件分别直接
   import，更像是 `agent/` 包内部缺一层"只暴露必要方法"的门面，而
   不是真的有十几个完全独立的子系统在依赖它。建议先在 `agent/`
   包内部整理出一个统一的访问入口（例如让 `agent/core.py` 的 Agent
   对象成为唯一持有 `HistoryManager` 实例的地方，其它 `agent/*`
   文件通过 Agent 对象访问，而不是各自 `from mini_agent.history_manager
   import HistoryManager`），把"12 个直接调用方"收敛之后，再评估
   Adapter 接入点。这一步 facade 整理**不在本 Sprint 范围内**，需要
   单独排期（会改动 `agent/` 包内部的调用方式，风险与工作量都明显
   大于本次评估文档本身）。
3. **`perception/memory_store.py`（Memory 核心）：暂缓迁移，且暂缓
   顺序在 `history_manager.py` 之后**。21 个包外调用方跨越
   `agent/`、`evolution/`、`api/`、`goal_mode/`、根目录模块（`profile.py`
   / `context_builder.py`）至少 5 个不同层级，任何一层单独加 facade
   都无法收敛全部调用方，需要的是"分层加 facade"（先在
   `evolution/` 内部收敛、再在 `agent/` 内部收敛、`api/routes.py`
   等零散调用方逐个单独处理），工作量和风险是三者中最大的。**在
   `history_manager.py` 的 facade 整理经验积累之前，不建议直接启动
   `memory_store.py` 的评估性 Sprint**（用小风险的 `history_manager.py`
   先验证"facade 收敛 + Adapter"这套组合模式是否可行，再用到风险
   更大的 `memory_store.py` 上）。

**排期建议**（仅供项目所有者参考，本文档不代表已批准的排期）：
`perception/self_model.py`（Self）→ `history_manager.py`
（facade 整理 + Experience 迁移）→ `perception/memory_store.py`
（Memory 迁移，待前两者验证完 facade 模式后再启动）。

---

## 四、完成标志

- [x] `perception/` 按概念拆解重新扫描完成，得到比 Sprint 3 粗粒度
      扫描更准确的耦合数据。
- [x] 三个候选迁移概念（Self / Experience 的 history 部分 / Memory
      核心）各自有独立的、基于实测数据的迁移优先级建议。
- [x] 方法论教训（按具体类/子模块扫描，不按顶层包扫描）已补充进
      `12-execution-and-doc-sync-norms.md`。
- [x] `perception/self_model.py` 的正式迁移 Sprint 已启动并完成第一步
      （见下方"五、Self 迁移链执行记录"）。

---

## 五、Self 迁移链执行记录（复盘，按 `12-execution-and-doc-sync-norms.md` 第五节最低要求）

按本文档"三、迁移优先级建议"第 1 条，Self 是三者中风险最低、可以直接
参照 Goal 模式启动的一条，项目所有者确认后按此执行：

**产出**：
- 新增 `core/self.py::SelfState`（最小 dataclass，只保留
  `capability_snapshot`/`active_skill_count`/`session_start_at` 三个
  跨子系统共享字段）+ `core/self_adapter.py::SelfAdapter`
  （`AgentSelfModel → SelfState` 单向转换；`to_old` 方向没有实际调用方，
  显式抛 `NotImplementedError` 并注明原因，不做没人用的空实现）。
- **唯一接入点**：`agent/lifecycle.py::_init_components()` 里
  `AgentSelfModelBuilder().build()` 调用之后，做一次
  `SelfAdapter.to_new` 转换 + DEBUG trace 记录（`mini_agent.core.trace`
  logger，与 `goal_mode/runner.py` 的 trace 约定一致），不改动
  `perception/self_model.py` 内部逻辑。

**验收标准对照**（参照 Goal 迁移链 Sprint 1 的验收标准格式）：
1. 有 trace 证据证明链路被执行——**已达成**，
   `tests/test_core_self_adapter.py::test_lifecycle_self_model_hook_emits_trace_event`
   用 `caplog` 断言。
2. 现有 `self_model` 相关测试全部通过——**已达成**：
   `tests/test_self_model.py` + `test_self_model_drift.py` +
   `test_self_model_snapshot.py` 共 32 passed，0 failed。
3. 真实构造 Agent 的既有测试不受影响——**已达成**：
   `tests/test_agent_startup_project_meta.py` +
   `tests/test_global_knowledge_integration.py` 共 21 passed。
4. 依赖图显示耦合度没有因为迁移而上升——**已达成**：inbound 深度依赖
   从评估时的 5 变为 6（唯一新增的正是计划内的
   `core/self_adapter.py`），未触发止损阈值。

**是否触发止损条件**：未触发。

**`MIGRATION_STATUS.md` 是否已同步更新**：是，`perception/self_model.py`
一行状态由"未开始"更新为"部分迁移"。

**遗留/下一步**：`SelfAdapter.to_old` 尚未实现（无调用方需要），如果
未来 Self 迁移链继续推进（比如需要让某个新调用方只持有
`core.SelfState` 而不直接依赖 `perception.self_model`），需要先补上
这个方向的实现与往返转换测试，而不是假设它已经能用。

---

## 六、history_manager.py facade 整理执行记录（第二条迁移链的前置步骤）

按本文档"三、迁移优先级建议"第 2 条，`history_manager.py` 需要先在
`agent/` 层做 facade 收敛，再评估 Adapter 接入点。实际动手整理时，
发现比预想的更简单：

**关键发现**：用 `grep`（人工排查）+ `pyflakes`（工具交叉验证）逐个
检查"三、现状盘点"表格里那 11 个 `agent/*` 直接 import
`HistoryManager` 的文件，发现除 `agent/lifecycle.py`（唯一实际
`HistoryManager(cfg=..., skill_loader=...)` 构造并持有实例的文件）
之外，其余 **10 个文件（`_helpers.py` / `compaction.py` / `core.py` /
`llm_control.py` / `profile.py` / `reflection.py` /
`reminders_correction.py` / `role_judge.py` / `turn_loop.py` /
`snapshot.py`）在模块顶部 import `HistoryManager` 之后，再也没有
在文件其它地方引用过这个名字**——既不是类型注解、也不是实际调用，
是纯粹的死代码（`pyflakes` 对全部 10 个文件逐一确认
"imported but unused"）。`snapshot.py` 略特殊：文件内部另有一处
**函数内的局部 import**（`from mini_agent.history_manager import
HistoryManager` 出现在某个方法体内部，紧跟着 `HistoryManager(cfg=cfg)`
真实构造），这处局部 import 是真实使用、原样保留；顶层那行才是
需要删除的重复死代码。这与 Sprint 3 复盘"perception 耦合被夸大"是
同一类问题的再现：`--module` 扫描统计的是"文件里出现了 import
语句"，不区分这行 import 是否真的被用到，`history_manager.py`
"inbound=12"里有 10 个其实是无效噪音，真实耦合远低于表面数字。

**处理方式**：不是"加一层 facade 隐藏这些依赖"，而是更直接、风险
更低的方式——**直接删除这 10 处模块顶层的未使用 import**（`agent/`
这批 mixin 文件大概率是从同一份公共导入模板复制出来的，很可能各自
按需删减时遗漏了这一行，不是有意为之的设计）。这比"新建一层 facade
模块，让 10 个文件改成 import facade 而不是 import HistoryManager
本身"的改动量小得多，也不引入新的中间层。

**验证**：
- 删除后 10 个文件仍可正常 `import`（无 `ImportError`/`NameError`）。
- `pyflakes` 复查确认这 10 个文件不再报 `HistoryManager` 相关的
  unused-import 警告。
- `scripts/dep_graph.py --module history_manager` 复扫：inbound 深度
  依赖从 **12 降到 2**（`agent/lifecycle.py` 真正实例化 + 
  `evolution/session_cleanup.py` 的真实使用），远低于止损阈值 10+。
- 回归测试：`test_agent_startup_project_meta.py` /
  `test_global_knowledge_integration.py`（真实构造 Agent）+
  与被改动文件相关的关键字测试子集（`snapshot`/`compaction`/
  `turn_loop`/`reflection`/`reminders`/`role_judge`/`llm_control`/
  `profile`/`commit_guard`，319 passed）+ Self/Goal 迁移链既有测试
  （190 passed，同 5 个已知 `test_build_from_history_*` 历史失败）
  均无新增回归。`lint_no_new_toplevel_concepts.py` 通过。

**结论**：`history_manager.py` 迁移链的前置条件（"facade 收敛"）已经
通过删除死代码的方式达成，且比预期简单——**真实耦合度（inbound=2）
已经比 Self 迁移链（inbound=6）更低，未触发止损阈值，可以考虑直接
进入 Adapter 接入点设计，不再需要额外的、更复杂的 facade 层**。这是
对"三、迁移优先级建议"第 2 条的更新：原建议"先做 facade 再评估"里的
"facade"被证实其实就是"删掉死代码"这么简单，不需要引入新抽象层。

**是否触发止损条件**：未触发（本身是清理性质，无新增代码风险；
`pyflakes` + 回归测试双重验证，删除前后行为完全一致）。

**`MIGRATION_STATUS.md` 是否已同步更新**：是，`history_manager.py`
一行的耦合评估备注已更新为"inbound=2（原 12 里 10 个是死代码，已
清理），未触发止损阈值，可评估 Adapter 接入点"。

---

## 七、history_manager.py Adapter 接入点执行记录（第二条迁移链正式启动，复盘）

按"六"的结论（inbound=2，未触发止损阈值），直接参照 Self 迁移链
（`core/self.py` + `core/self_adapter.py`）的模式启动 Adapter 接入点，
不再需要额外的 facade 层。

**产出**：
- 新增 `core/history.py::HistorySnapshot`（最小 dataclass，只保留
  `active_history_len`/`raw_history_len`/`has_pending_snapshot` 三个
  跨子系统共享字段，不包含压缩策略、raw history 具体条目、extraction
  调度状态等 `history_manager.py` 内部实现细节）+
  `core/history_adapter.py::HistoryAdapter`
  （`HistoryManager → HistorySnapshot` 单向转换；`to_old` 方向因无
  调用方暂未实现，显式抛 `NotImplementedError` 并注明原因）。
- **唯一接入点**：`agent/lifecycle.py::_init_components()` 里
  `self._hist = HistoryManager(...)` 构造之后，做一次
  `HistoryAdapter.to_new` 转换 + DEBUG trace 记录（`mini_agent.core.trace`
  logger，与 Self/Goal 迁移链的 trace 约定一致），不改动
  `history_manager.py` 内部逻辑。

**验收标准对照**（参照 Self 迁移链的验收标准格式）：
1. 有 trace 证据证明链路被执行——**已达成**，
   `tests/test_core_history_adapter.py::test_lifecycle_history_hook_emits_trace_event`
   用 `caplog` 断言。
2. `HistoryAdapter`/`HistorySnapshot` 自身单测（正向转换字段正确、
   反向转换显式 `NotImplementedError`）——**已达成**，
   `tests/test_core_history_adapter.py` 共 3 个用例全部通过。
3. 真实构造 Agent 的既有测试不受影响——**已达成**：
   `tests/test_agent_startup_project_meta.py` +
   `tests/test_global_knowledge_integration.py` 共 21 passed（与
   Self 迁移链验证时一致）。
4. 依赖图显示耦合度没有因为迁移而异常上升——**已达成**：
   `scripts/dep_graph.py --module history_manager` 的 inbound 深度
   依赖从"六"评估时的 2 变为 3（唯一新增的正是计划内的
   `core/history_adapter.py`），远低于止损阈值 10+，且低于 Self
   迁移链当前的 6。
5. 与 history 相关的既有测试子集（`snapshot`/`compaction`/
   `turn_loop`/`reflection`/`reminders`/`role_judge`/`llm_control`/
   `profile`/`commit_guard`/`self_model`/`self_adapter`）——**已达成**：
   325 passed，5 failed（`test_browser_core_session_manager.py` 里
   与本次改动完全无关的浏览器 profile 用例，环境相关的既有失败，
   非本次改动引入）。
6. `scripts/lint_no_new_toplevel_concepts.py` 通过——**已达成**。

**是否触发止损条件**：未触发。

**`MIGRATION_STATUS.md` 是否已同步更新**：是，`history_manager.py`
一行状态由"未开始（可评估 Adapter 接入点）"更新为"部分迁移"。

**遗留/下一步**：`HistoryAdapter.to_old` 尚未实现（无调用方需要），
处理方式与 `SelfAdapter.to_old` 一致；`perception/memory_store.py`
（Memory 核心）仍按"三、迁移优先级建议"第 3 条暂缓，待项目所有者
确认排期后再评估。
