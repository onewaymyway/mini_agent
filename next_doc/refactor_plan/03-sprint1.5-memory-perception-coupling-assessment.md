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

---

## 八、perception/memory_store.py 死代码清理执行记录（Memory 迁移评估的前置步骤）

按"三、迁移优先级建议"第 3 条，`memory_store.py` 暂缓迁移，在
`history_manager.py` 的 facade 经验（"六"）积累之后先评估是否有同类
死代码噪音。用 `grep` + `pyflakes` 交叉验证复查"一、现状盘点"表格里
`memory_store.py` 的 33 个 inbound 文件，发现**确实存在同一类问题**，
但规模和结论与 `history_manager.py` 不同：

**关键发现**：
1. `history_manager.py` inbound 表格里那 11 个 `agent/*` 文件
   （`_helpers.py`/`compaction.py`/`core.py`/`lifecycle.py`/
   `llm_control.py`/`profile.py`/`reflection.py`/
   `reminders_correction.py`/`role_judge.py`/`snapshot.py`/
   `turn_loop.py`）同样都 import 了
   `from mini_agent.perception.memory_store import MemoryStore, MemoryEntry`
   ——这印证了"六"里的推测："agent/ 这批 mixin 文件大概率是从同一份
   公共导入模板复制出来的"。`pyflakes` 逐一确认：**`MemoryStore` 在
   全部 11 个文件里都是未使用的死代码**；`MemoryEntry` 在其中 8 个
   文件（`_helpers.py`/`compaction.py`/`core.py`/`lifecycle.py`/
   `llm_control.py`/`role_judge.py`/`snapshot.py`/`turn_loop.py`）
   也是死代码，但在另外 3 个文件（`profile.py`/`reflection.py`/
   `reminders_correction.py`）里被真实用来构造 `MemoryEntry(...)`，
   不能整体删除，只能删掉 `MemoryStore` 这一半。
2. 另外发现 3 处分散的、非 `agent/*` 集群的死代码局部 import：
   `perception/memory_factory.py::merge_search()`（`MemoryStore`
   未使用）、`evolution/memory_consolidation.py::_rule_based_merge()`
   （`MemoryEntry` 未使用，模块顶部第 29 行已有同名导入满足字符串
   前向引用注解）、`api/routes.py` 的 growth 诊断路由（`MemoryStore`
   未使用，注释显示是历史遗留——该处后来改用
   `memory_factory.build_default_memory_store()`，直接构造
   `MemoryStore` 的旧写法被替换后导入忘记删除）。

**处理方式**：与"六"一致，直接删除死代码 import，不引入 facade 层：
11 个 `agent/*` 文件里全部删掉 `MemoryStore`，其中 8 个连
`MemoryEntry` 一并删掉（保留 3 个真实使用的）；另外 3 处局部死代码
import 直接删除并加注释说明。

**验证**：
- `pyflakes` 复查确认改动的 14 个文件（11 个 `agent/*` + 3 个零散
  文件）均不再报 `MemoryStore`/`MemoryEntry` 相关的 unused-import
  警告，`py_compile` 全部通过。
- `scripts/dep_graph.py --module perception.memory_store` 复扫：
  inbound 深度依赖从 **33 降到 24**（11 个 `agent/*` 文件里的 8 个
  完全死代码文件被移出 inbound 列表，`api/routes.py` 的死代码点也
  被移出；`profile.py`/`reflection.py`/`reminders_correction.py`
  因为真实使用 `MemoryEntry` 继续留在 inbound 里）。
- 回归测试：与 memory 相关的关键字测试子集
  （`snapshot`/`compaction`/`turn_loop`/`reflection`/`reminders`/
  `role_judge`/`llm_control`/`profile`/`commit_guard`/`self_model`/
  `self_adapter`/`history_adapter`/`core_self`/`memory`，407 passed，
  6 failed）无新增回归——6 个失败里 5 个是
  `test_browser_core_session_manager.py` 里与本次改动完全无关的
  浏览器 profile 用例（同"七"记录的既有环境失败），第 6 个
  `test_evolution_cli.py::test_revert_memory_failure_does_not_raise`
  经核对**在未做任何本次改动的原始代码上跑同一个测试同样失败**
  （对照 `/tmp/orig` 下的原始 zip 解包结果复现），是与本次改动
  无关的既有失败，不是新增回归。
- `scripts/lint_no_new_toplevel_concepts.py` 通过。

**结论（对"三、迁移优先级建议"第 3 条的更新）**：清理死代码后，
`memory_store.py` 真实 inbound 深度依赖从 33 降到 24，**仍然远超
止损阈值 10+**，且真实调用方依旧跨越 `agent/*`（3 个真实使用）、
`perception/*` 内部、`evolution/*`、`api/routes.py`、
`context_builder.py`、`goal_mode/runner.py`、根目录 `profile.py`
至少 5 个子系统——"死代码清理"这一步能做的清理已经做完（不像
`history_manager.py` 那样清理后就跌破阈值），原判断"暂缓迁移，
需要分层加 facade 而非直接 Adapter 接入"依然成立，**没有被推翻**。
这与"六"里 `history_manager.py` 的结果不同：同一类死代码问题，
在不同模块上可能得出"清理后风险消除"或"清理后风险仍然存在，只是
量级降低"两种不同结论，不能假设同一手法一定能让所有模块跌破阈值。

**是否触发止损条件**：未触发（清理本身是死代码删除，无新增代码
风险；`pyflakes` + 回归测试双重验证，删除前后行为完全一致）。

**`MIGRATION_STATUS.md` 是否已同步更新**：是，
`perception/memory_store.py` 一行的耦合评估备注已更新为
"inbound=24（原 33 里 9 个是死代码，已清理），仍超止损阈值，
维持'暂缓迁移，需分层加 facade'的原判断"。

**遗留/下一步**：`perception/memory_store.py` 的分层 facade
设计（先 `evolution/` 内部收敛、再 `agent/` 内部收敛、
`api/routes.py` 等零散调用方逐个单独处理）**不在本次清理范围内**，
工作量和风险明显大于死代码清理本身，需要项目所有者单独确认排期
后再启动，参照"三、迁移优先级建议"第 3 条的分层顺序建议。

---

## 九、perception/memory_store.py 分层 facade · 第一层（evolution/）执行记录

按"八"末尾遗留事项与"三、迁移优先级建议"第 3 条的分层顺序（先
`evolution/` 内部收敛 → 再 `agent/` 内部收敛 → `api/routes.py`
等零散调用方逐个处理），启动第一层：`evolution/` 包内 6 个文件
（`consolidation.py`/`failure_pattern_store.py`/`memory_aging.py`/
`memory_backfill.py`/`memory_consolidation.py`/`outcome_tracker.py`）
的收敛。

**现状盘点**：复查这 6 个文件发现，它们**只用到 `MemoryEntry`
这一个类型**（构造一条记忆条目），完全不涉及 `MemoryStore` 本身
的读写方法——耦合面比"六"/"八"里 `agent/*` 的情况更单一，是"分层
facade"里风险最低的一层，适合先做，验证"引入门面模块收敛多处
直接 import"这个手法本身是否可行，再用到风险更高的 `agent/` 层。

**产出**：
- 新增 `evolution/memory_types.py`：只做 `MemoryEntry` 的重新导出
  （`from mini_agent.perception.memory_store import MemoryEntry`），
  不是新的领域概念，也不改变 `MemoryEntry` 的定义或行为。
- 6 个 `evolution/*` 文件里全部 10 处
  `from mini_agent.perception.memory_store import MemoryEntry`
  （含模块顶部与函数内局部导入）统一改成
  `from mini_agent.evolution.memory_types import MemoryEntry`。

**验证**：
- `py_compile` 全部通过；`pyflakes` 复查确认改动本身未引入新的
  unused-import/undefined-name 问题（`memory_backfill.py` 的
  3 处 `undefined name 'MemoryEntry'` 字符串注解警告，经与
  `/tmp/orig` 原始代码比对，是**改动前就存在**的既有问题，非本次
  引入）。
- `scripts/dep_graph.py --module perception.memory_store` 复扫：
  inbound 深度依赖从"八"评估时的 **24 降到 19**（6 个 `evolution/*`
  文件收敛为 `evolution/memory_types.py` 这 1 个新的直接调用方，
  净减少 5）。`scripts/dep_graph.py --module evolution.memory_types`
  确认新门面模块的 inbound 是预期的 6，未超止损阈值。
- 回归测试：`evolution`/`memory`/`consolidation`/`outcome_tracker`/
  `failure_pattern`/`backfill` 关键字测试子集 310 passed，2 failed
  ——均为 `test_evolution_cli.py` 里与本次改动无关的既有失败（
  `test_revert_memory_failure_does_not_raise`：与"七"记录的原因
  相同；`test_revert_writes_lesson_with_revert_record_source`：
  经核对在 `/tmp/orig` 未改动的原始代码上跑同一测试同样失败，
  是 `confidence` 期望值 0.9 与实际 0.85 不匹配的既有断言问题，
  与本次改动无关）。
- `scripts/lint_no_new_toplevel_concepts.py` 通过（新文件是
  `evolution/` 包内部子模块，不在顶层扫描范围内）。

**结论**：`perception/memory_store.py` 真实 inbound 从 19 仍然
**超过止损阈值 10+**，第一层收敛降低了耦合面但还不足以让这条
迁移链直接进入 Adapter 接入点设计；需要继续推进"三、迁移优先级
建议"第 3 条里的下一层（`agent/` 内部收敛：让持有真实 `MemoryEntry`
写入需求的 3 个文件——`profile.py`/`reflection.py`/
`reminders_correction.py`——改为通过 `agent/core.py` 的 Agent
对象统一访问，而不是各自直接 import）。

**是否触发止损条件**：未触发（本身是"重新导出"性质的收敛，
无新增代码风险；`pyflakes` + 回归测试双重验证，行为完全一致）。

**`MIGRATION_STATUS.md` 是否已同步更新**：是，
`perception/memory_store.py` 一行的耦合评估备注已更新为
"分层 facade 第一层（evolution/）已完成，inbound=19（原 24 里
5 个收敛进 evolution/memory_types.py），仍超止损阈值，继续推进
agent/ 层收敛"。

**遗留/下一步**：`agent/` 层收敛（`profile.py`/`reflection.py`/
`reminders_correction.py` 改为通过 Agent 对象统一访问）与
`api/routes.py` 等零散调用方的处理，**不在本次范围内**，待项目
所有者确认排期后再启动。

## 十、perception/memory_store.py 分层 facade · 第二层（agent/）执行记录

按"九"末尾"遗留/下一步"，启动第二层：`agent/` 包内 3 个文件
（`agent/profile.py`/`agent/reflection.py`/`agent/reminders_correction.py`）
的收敛。

**现状盘点**：复查这 3 个文件的真实用法（`grep memory_store\.` 确认
无任何 `MemoryStore` 实例方法调用），发现它们与"九"里 `evolution/`
的情况完全一致——**只用到 `MemoryEntry` 这一个类型**，不涉及
`MemoryStore` 本身的读写方法。这与"三、迁移优先级建议"第 3 条当初
"这三个文件可能持有真实 `MemoryStore` 写入需求，需改为通过 Agent
对象统一访问"的预判不同：预判基于未展开复查的粗粒度判断，实际展开
后耦合面比预想更窄，因此采用与 `evolution/memory_types.py` 相同、
更轻量的门面模式，而不是引入"通过 Agent 对象访问"这种更重的设计。

**产出**：
- 新增 `agent/memory_types.py`：只做 `MemoryEntry` 的重新导出
  （`from mini_agent.perception.memory_store import MemoryEntry`），
  不是新的领域概念，也不改变 `MemoryEntry` 的定义或行为。
- 3 个 `agent/*` 文件里原来的
  `from mini_agent.perception.memory_store import MemoryEntry`
  （原带 `[dead-code cleanup]` 注释，见"六"）统一改成
  `from mini_agent.agent.memory_types import MemoryEntry`。

**验证**：
- `py_compile` 全部通过；`pyflakes` 复查确认改动本身未引入新的
  unused-import/undefined-name 问题（三个文件里报出的其它
  unused-import 警告经核对是改动前就存在的既有问题——与"九"记录的
  `agent/*` mixin 文件公共导入模板复制现象一致，非本次引入）。
- `scripts/dep_graph.py --module perception.memory_store` 复扫：
  inbound 深度依赖从"九"评估时的 **19 降到 17**（3 个 `agent/*`
  文件收敛为 `agent/memory_types.py` 这 1 个新的直接调用方，净减少
  2；因原 19 里已算入 `agent/memory_types.py` 自身尚未存在，实际
  是 19 → 17，减少数与"评估口径"一致）。
  `scripts/dep_graph.py --module agent.memory_types` 确认新门面
  模块的 inbound 是预期的 3，未超止损阈值。
- 回归测试：`tests/test_profile.py`/`tests/test_session_end_reflection.py`/
  `tests/test_evolution_agent_profile.py` 47 passed（三个文件对应的
  直接测试全过）；`profile`/`reflection`/`memory` 关键字更大范围
  回归 235 passed, 11 failed——11 个失败逐一核对：`test_browser_core_session_manager.py`
  的 5 个是既有浏览器 profile 环境用例（与"七"记录原因相同）；
  `test_evolution_cli.py::test_revert_memory_failure_does_not_raise`
  与"九"记录的既有失败一致；`test_evolve_cli.py` 3 个与
  `test_subagent_inheritance.py` 2 个经在未改动的原始代码上单独
  复跑同一批测试确认同样失败（测试隔离/执行顺序相关的既有问题，
  非本次改动引入的回归）。
- `scripts/lint_no_new_toplevel_concepts.py` 通过（新文件是 `agent/`
  包内部子模块，不在顶层扫描范围内）。

**结论**：`perception/memory_store.py` 真实 inbound 从 17 仍然
**超过止损阈值 10+**，第二层收敛（`evolution/` + `agent/` 两层
合计从 24 降到 17）降低了耦合面但还不足以让这条迁移链直接进入
Adapter 接入点设计；剩余调用方分布在 `perception/` 包内部（约 10
个文件，属于同包内聚，非跨子系统耦合）、`context_builder.py`/
`goal_mode/runner.py`/根目录 `profile.py` 等零散调用方，需要继续
推进"三、迁移优先级建议"第 3 条里的最后一步（`api/routes.py` 等
零散调用方逐个处理，或评估"同包内部调用不计入跨子系统止损阈值"
的口径调整）。

**是否触发止损条件**：未触发（本身是"重新导出"性质的收敛，无新增
代码风险；`pyflakes` + 回归测试 + 依赖图三重验证，行为完全一致）。

**`MIGRATION_STATUS.md` 是否已同步更新**：是，
`perception/memory_store.py` 一行的耦合评估备注已更新为
"分层 facade 第二层（agent/）已完成，inbound=17（原 19 里 2 个净
收敛进 agent/memory_types.py），仍超止损阈值，剩余调用方多数集中
在 perception/ 包内部，需评估口径调整或继续处理零散调用方"，并
新增一行 `agent/memory_types.py（新增门面模块）` 记录。

**遗留/下一步**：`api/routes.py` 等零散调用方的处理，以及"同包
内部调用是否应计入跨子系统止损阈值"的口径评估，**不在本次范围
内**，待项目所有者确认排期后再启动。
