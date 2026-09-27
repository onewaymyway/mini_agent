# mini_agent 架构收敛重构计划

本目录汇总 mini_agent 从"能力堆叠型 Agent"向"Self-centered Personal AI
Runtime"演进的重构相关文档。原始方案的 Phase 0-10 已全部拆解为可执行
Sprint 计划，每个 Phase 文档都包含：现状盘点、Sprint 划分、任务表、
验收标准、止损条件、完成标志。

## 目录结构

| 文件 | 内容 | 对应原方案章节 |
|---|---|---|
| `00-original-architecture-proposal.md` | 原始架构设计文档（用户提供），8 大核心概念 + 总体路线图 | 全文 |
| `01-evaluation-and-gaps.md` | 对原方案的评估：合理性判断、结构性缺口、额外改进方向 | — |
| `02-executable-sprint-plan.md` | Phase 0 + Phase 1（Domain Model + Goal 迁移链）：4 个 Sprint | §33-34, §51-54 |
| `03-phase2-event-model-sprint-plan.md` | Phase 2：统一 Event Model | §35 |
| `04-phase3-experience-layer-sprint-plan.md` | Phase 3：Experience Layer 落地 | §36 |
| `05-phase4-unified-state-sprint-plan.md` | Phase 4：统一 State（StateManager） | §37 |
| `06-phase5-goal-convergence-sprint-plan.md` | Phase 5：统一 Goal（Objective/Backlog 收敛） | §38 |
| `07-phase6-action-model-sprint-plan.md` | Phase 6：统一 Action（Tool/Workflow/SubAgent 收敛） | §39 |
| `08-phase7-decision-simulation-sprint-plan.md` | Phase 7：Decision + Simulation | §40 |
| `09-phase8-runtime-convergence-sprint-plan.md` | Phase 8：Autonomous Runtime 收敛 | §41 |
| `10-phase9-self-evolution-sprint-plan.md` | Phase 9：Self Evolution 接入统一 Experience | §42 |
| `11-phase10-legacy-decommission-plan.md` | Phase 10：旧系统降级（用户不可见，非删除） | §43 |
| `12-execution-and-doc-sync-norms.md` | **执行规范**：代码改动后如何同步更新文档、`MIGRATION_STATUS.md` 格式、计划变更留痕流程、复盘最低要求、文档写作规范 | — |
| `MIGRATION_STATUS.md` | 迁移完成度台账（初始占位模板，执行过程中持续更新） | — |

## 阅读与执行顺序

1. 先读 `00 → 01`，理解目标架构和原方案的不足。
2. **在开始任何一个 Phase 之前，先读 `12-execution-and-doc-sync-norms.md`**——
   这是贯穿所有 Phase 的执行规范（代码改动后如何同步文档、如何更新
   `MIGRATION_STATUS.md`、计划变更怎么留痕），不是读完就忘的说明书，
   而是每次提交代码都要遵守的检查清单。
3. 按 `02 → 11` 顺序**依次执行**，每个 Phase 文档开头都标注了前置条件，
   不建议跳跃执行（尤其 Phase 7/8/9 风险较高，强依赖前序 Phase 的产出）。
4. 每个 Phase 完成后，对照文档末尾的"完成标志"清单验收，再进入下一个，
   并按 `12` 的规范同步更新 `MIGRATION_STATUS.md`。

## 全局止损原则（贯穿所有 Phase）

- **不做 Big Bang Rewrite**：旧模块通过 Adapter 接入，不整体重写。
- **Adapter 契约统一**：所有 Adapter 遵循 Phase 1 定义的
  `to_new(old) -> New` / `to_old(new) -> Old` 协议。
- **每个 Sprint 都有止损条件**：如果验证过程中发现设计假设不成立，
  按对应文档的"止损条件"处理，不要硬着头皮继续推广到下一个模块。
- **测试优先于文档**：任何 Phase 的"完成"都以现有测试 + 新增特征测试
  全部通过为准，不以"文档写完/代码写完"为准。

## 当前状态

- [x] 全部 Phase（0-10）已产出可执行 Sprint 计划
- [x] 执行规范与文档同步流程已产出（`12-execution-and-doc-sync-norms.md`）
- [x] `MIGRATION_STATUS.md` 初始模板已建立
- [x] Sprint 0（地基与安全网）已完成，详见
      `02-executable-sprint-plan.md` 末尾的"Sprint 0 执行记录"
      与 `docs/architecture_v2/00-overview.md`
- [x] Sprint 1（Domain Model + Goal 试验）已完成：新建 `src/mini_agent/core/`
      （`types.py`/`events.py`/`goal.py`/`experience.py`/`adapter.py`/
      `goal_adapter.py`），在 `goal_mode/runner.py` 唯一接入点走通
      `Goal(old) → GoalAdapter → GoalState → 执行 → Outcome → Experience`
      链路并有 trace 日志证据，详见 `02-executable-sprint-plan.md` 末尾的
      "Sprint 1 执行记录"与 `MIGRATION_STATUS.md`
- [x] Sprint 2（Experience 落地）已完成：新增
      `src/mini_agent/core/experience_store.py`（JSONL 持久化 + 检索）、
      `AgentPaths.workdir_experience_store`、
      `mini-agent experience search|list` CLI 命令，`goal_mode/runner.py`
      在原有 Sprint 1 接入点上新增实际持久化调用，详见
      `02-executable-sprint-plan.md` 末尾的"Sprint 2 执行记录"
- [x] Sprint 3（复盘 + 推广决策）已完成：复盘发现 `history_manager.py`
      （inbound 12）、`perception/`（inbound 69）均已超过 Sprint 0
      定义的止损阈值（10+），**正式决策：Memory → Experience 迁移链
      暂缓直接照搬 Goal 的模式，需先补一个独立的"Sprint 1.5：
      Memory/Perception 耦合拆解评估"**。Phase 1（第一阶段）7 条完成
      判定标准逐条核对，全部达成或在计划范围内。详见
      `02-executable-sprint-plan.md` 末尾"Sprint 3 执行记录"与
      `00-original-architecture-proposal.md` 末尾的映射表补充。
- [x] Sprint 1.5（Memory/Perception 耦合拆解评估，Sprint 3 复盘新增）已
      完成：按子模块重新扫描（不再只扫整个 `perception/` 顶层包），发现
      `perception/self_model.py`（Self，inbound=5，未超阈值）、
      `history_manager.py`（Experience 一部分，inbound=12 但 11 个集中在
      `agent/*`）、`perception/memory_store.py`（Memory 核心，
      inbound=33，跨 4+ 子系统）三者风险程度实际差异很大，给出了有区分度
      的迁移优先级建议（Self 可直接启动 → history_manager 先做
      facade → memory_store 暂缓）。方法论教训（按子模块而非顶层包扫描）
      已回填进 `12-execution-and-doc-sync-norms.md` 第六节。详见
      `03-sprint1.5-memory-perception-coupling-assessment.md`。
- [x] Self（`perception/self_model.py`）迁移链已启动并完成第一步：新增
      `core/self.py::SelfState` + `core/self_adapter.py::SelfAdapter`
      （`AgentSelfModel → SelfState` 单向转换），唯一接入点
      `agent/lifecycle.py::_init_components()`，trace 证据 + 既有
      测试（32+21 passed）+ 依赖图核对均已验证，`to_old` 方向因无调用方
      暂未实现（显式标注，非静默空实现）。详见
      `03-sprint1.5-memory-perception-coupling-assessment.md` 末尾
      "五、Self 迁移链执行记录"。
- [x] `history_manager.py` 的 `agent/` 层 facade 整理已完成：发现原
      "inbound=12"里有 10 个是从未被引用的死代码 import（`pyflakes`
      交叉验证），删除后 inbound 降到 2（低于 Self 迁移链的 6），
      比预想的"建 facade"更简单——真正做的是"删掉死代码"，不是
      "新建抽象层"。回归测试（319+190 passed）+ lint 均确认无新增
      问题。Adapter 接入点尚未设计（本步骤只做了前置的耦合清理）。
      详见 `03-sprint1.5-memory-perception-coupling-assessment.md`
      末尾"六、history_manager.py facade 整理执行记录"。
- [x] `history_manager.py` 的 Adapter 接入点设计与实现已完成：新增
      `core/history.py::HistorySnapshot` + `core/history_adapter.py::HistoryAdapter`
      （`HistoryManager → HistorySnapshot` 单向转换，模式与 Self 迁移链
      一致），唯一接入点 `agent/lifecycle.py::_init_components()`，
      trace 证据 + 新增单测（3 passed）+ 既有相关测试（325 passed，
      5 个失败为环境相关的浏览器 profile 用例，与本次改动无关）+
      依赖图核对（inbound 2→3，未触发止损阈值）均已验证，`to_old`
      方向因无调用方暂未实现（显式标注，非静默空实现）。详见
      `03-sprint1.5-memory-perception-coupling-assessment.md` 末尾
      "七、history_manager.py Adapter 接入点执行记录"。
- [x] `perception/memory_store.py` 死代码清理已完成：复查同一批
      `agent/*` mixin 文件后发现与 `history_manager.py` 同款问题
      （公共导入模板复制导致的死代码），清理 14 处死代码 import 后
      inbound 从 33 降到 24，但**仍超止损阈值**，与
      `history_manager.py`（清理后跌破阈值）结论不同——真实调用方
      跨越至少 5 个子系统，原"暂缓迁移，需分层加 facade"的判断
      未被推翻。回归测试（407 passed，6 failed 均为既有环境/历史
      失败，非新增回归）+ lint 均确认无新增问题。详见
      `03-sprint1.5-memory-perception-coupling-assessment.md`
      末尾"八、perception/memory_store.py 死代码清理执行记录"。
- [x] `perception/memory_store.py` 分层 facade 第一层（`evolution/`
      内部收敛）已完成：新增 `evolution/memory_types.py` 门面模块，
      6 个 `evolution/*` 文件（只用到 `MemoryEntry`，不涉及
      `MemoryStore` 读写方法）统一改为从门面模块 import，
      inbound 从 24 降到 19，仍超止损阈值。回归测试（310 passed，
      2 failed 均为既有失败，非新增回归）+ 依赖图核对 + lint
      均确认无新增问题。详见
      `03-sprint1.5-memory-perception-coupling-assessment.md`
      末尾"九、perception/memory_store.py 分层 facade · 第一层
      （evolution/）执行记录"。
- [x] `perception/memory_store.py` 分层 facade 第二层（`agent/`
      内部收敛）已完成：复查 `profile.py`/`reflection.py`/
      `reminders_correction.py` 后发现与 `evolution/` 层同款情况——
      只用到 `MemoryEntry`，不涉及 `MemoryStore` 读写方法，因此沿用
      门面模式（新增 `agent/memory_types.py`）而非原计划更重的
      "通过 Agent 对象统一访问"设计。inbound 从 19 降到 17（两层
      合计 24→17），仍超止损阈值，剩余调用方多数集中在 `perception/`
      包内部。回归测试（235 passed，11 个既有失败均与本次改动无关）
      + 依赖图核对 + lint 均确认无新增问题。详见
      `03-sprint1.5-memory-perception-coupling-assessment.md`
      末尾"十、perception/memory_store.py 分层 facade · 第二层
      （agent/）执行记录"。
- [x] `perception/memory_store.py` 止损口径评估已完成，并按新口径
      启动 Adapter 接入点：止损阈值调整为只统计跨子系统 inbound
      （不含被迁移模块自己所属顶级包内部的调用方），口径变更走了
      `12-execution-and-doc-sync-norms.md` "四、计划变更流程"的
      留痕（新增该文档第六节第 6 条 + `03-sprint1.5-...md` 对应
      变更记录）；按新口径 `perception/memory_store.py` 的跨子系统
      inbound 只有 5（远低于阈值），直接参照 Self/history_manager
      模式启动 Adapter：新增 `core/memory.py::MemorySnapshot` +
      `core/memory_adapter.py::MemoryAdapter`（`MemoryBackend`
      接口 → `MemorySnapshot` 单向转换），唯一接入点
      `agent/core.py::Agent.__init__()`，trace 证据 + 实际构造
      Agent 验证 + 回归测试（124 passed，6 个既有环境失败均确认
      与本次改动无关）均已验证，`to_old` 方向因无调用方暂未实现
      （显式标注，非静默空实现）。详见
      `03-sprint1.5-memory-perception-coupling-assessment.md` 末尾
      "十一、perception/memory_store.py 止损口径评估 + Adapter
      接入点执行记录"。
- [ ] `MemoryAdapter.to_old` 方向、`self._global_memory` 是否需要
      单独接入、`perception/` 包内部 12 个调用方（若后续需要进一步
      收敛）：待项目所有者确认排期后启动。
- [x] Phase 1（Goal 迁移链）已达到验收标准，正式进入 **Phase 2（统一
      Event Model）**，按 `03-phase2-event-model-sprint-plan.md` 划分的
      Sprint 2-1（Event 数据结构 + 最小总线）已完成：扩展
      `core/events.py::Event` 字段集（新增 `id`/`actor`/`context`/
      `causation_id`/`correlation_id`，保留 `kind`/`payload`/`at`
      三个 Sprint 1 已用字段名不变，旧调用点不改一行仍可工作）、新增
      `core/event_bus.py`（进程内最小发布/订阅总线，订阅者异常不传播）、
      在 `goal_mode/runner.py` Sprint 1 唯一接入点上新增
      `GoalCreated → ActionStarted → ActionCompleted/ActionFailed →
      ExperienceCreated` 四类事件的 publish（共享同一
      `correlation_id`，`causation_id` 构成因果链）。验收标准第 1 条
      （一次 Goal 闭环至少 4 个 Event）与第 2 条（Phase 1 特征测试全部
      仍通过）均已用回归测试验证，详见
      `03-phase2-event-model-sprint-plan.md` 末尾"Sprint 2-1 执行记录"。
      事件雏形盘点表、依赖图核对两项完成标志尚未做，已在该文档标注为
      未完成，留待下一次推进（Sprint 2-2 或专门盘点任务）。
- [x] Sprint 2-1 完成标志清单剩余两项（事件雏形盘点表、依赖图核对）
      已补齐：新增 `docs/architecture_v2/phase2-event-inventory.md`，
      盘点 `history_manager.py`/`perception/behavior/events.py`/
      `evolution/`/`orchestrator/plan.py` 四类现有"事件雏形"，逐一
      给出对应新 Event type 建议并判定"本次均不接入"，其中
      `orchestrator/plan.py`（字段语义与 Phase 1 `ActionStarted/
      ActionCompleted/ActionFailed` 最接近）被列为优先级最高的后续
      接入候选；`python scripts/dep_graph.py --module core.event_bus`
      实际跑出 inbound=1（仅 `core/__init__.py`）、outbound=0，未触发
      止损阈值，确认总线不反向依赖发布者模块。至此 Sprint 2-1 对应的
      完成标志全部达成，`03-phase2-event-model-sprint-plan.md` 已同步
      勾选。Sprint 2-2 剩余任务（`mini_agent events trace` CLI 命令）
      留待下一次推进，详见该文档"Sprint 2-1 收尾核对记录"。
- [x] Sprint 2-2（causation_id/correlation_id 打通 + `events trace` CLI
      可视化）已完成，**Phase 2（统一 Event Model）三条完成标志全部
      达成**：新增 `core/event_log_store.py::EventLogStore`（JSONL 落盘
      + 按 `correlation_id` 检索）+ `ensure_event_log_subscribed()`
      （幂等挂载订阅者，只订阅 `EVENT_KINDS` 里的几种 kind）、
      `storage/paths.py::AgentPaths.workdir_event_log`
      （`.agent/events.jsonl`）；`goal_mode/runner.py::run()` 唯一接入点
      新增一行挂载调用，纯旁路，不改变控制流/返回值；新增 CLI
      `cli/commands/events_cmd.py::run_events_cli`（`mini-agent events
      trace <correlation_id>` / `events list`），接入方式与
      `experience`/`projects` 等既有短路子命令完全一致。验收标准（"能用
      `events trace` 命令完整重放...顺序一致"）已通过
      `tests/test_phase2_event_log_and_cli.py` 5 个新增用例验证（含
      跑一次真实 `GoalRunner.run()` 后通过 CLI 入口重放）；回归测试
      155 passed / 5 failed（失败用例与 Sprint 2-1 已确认的
      `test_build_from_history_*` 问题完全一致，非新增回归）；依赖图
      核对（`core.event_log_store` inbound=2/outbound=0，未触发止损
      阈值）+ pyflakes 均确认无新增问题。**关联链路**（causation_id/
      correlation_id 共享）部分实际已在 Sprint 2-1 提前完成，本次只
      补齐了"可视化"部分。详见
      `03-phase2-event-model-sprint-plan.md` 末尾"Sprint 2-2 执行
      记录"与 `MIGRATION_STATUS.md`。Phase 2 已达到验收标准，可进入
      **Phase 3（Experience Layer 落地）**，按
      `04-phase3-experience-layer-sprint-plan.md` 划分的 Sprint 继续
      推进（下一次对话的任务）。
- [x] Phase 2 达标后进入 **Phase 3（Experience Layer 落地）**，按
      `04-phase3-experience-layer-sprint-plan.md` 划分的"现状盘点"环节
      已完成：产出 `docs/architecture_v2/phase3-experience-inventory.md`，
      盘点 `history_manager.py`/`entry_type="lesson"` 的
      `MemoryEntry`（分布 4+ 写入点）/`wiki/experience_writer.py`/
      `evolution/failure_pattern_store.py`/`evolution/decision_recall.py`/
      `evolution/decision_profile_builder.py` 六类经验类结构，给出
      迁移优先级表，确认"lesson"（`MemoryEntry.entry_type="lesson"`）
      作为 Sprint 3-1 第一条 Adapter 接入链路、`failure_pattern_store.py`/
      `decision_recall.py` 分别作为 Sprint 3-2 Analyzer/Retriever 的
      参考实现。
- [x] Sprint 3-1（ExperienceRecorder + Store 扎实化）已完成：
      `core/experience.py::Experience` 扩展补齐原方案 §7 yaml 全部字段
      （新增 `id`/`context`/`state_before`/`action`/`reason`/
      `prediction`/`state_after`/`evidence`/`lesson`/
      `causal_hypothesis`/`confidence`，旧字段名不变）+
      `Experience.from_dict()` 宽容解析旧数据；`core/experience_store.py`
      从 JSONL 升级为 SQLite（对外 API 与构造签名不变，
      `AgentPaths.workdir_experience_store` 路径同步从 `.jsonl` 改名为
      `.db`）；新增 `core/experience_recorder.py::ExperienceRecorder`
      （订阅 `ExperienceCreated` 事件自动落库，接入模式与
      `core/event_log_store.py` 一致）；`goal_mode/runner.py::run()`
      挂载该订阅，`_finish()` 删除了原来手写的
      `ExperienceStore(...).append(...)` 调用；`core/goal_adapter.py::
      goal_run_result_to_experience()` 补充填充 `action`/`reason`/
      `evidence`/`lesson`（`state_before`/`state_after`/`prediction`/
      `causal_hypothesis`/`confidence` 因 `GoalRunResult` 拿不到对应
      中间状态，如实保留默认值）；新增一次性迁移脚本
      `scripts/migrate_experience_jsonl_to_sqlite.py`。验收标准（"一个
      Goal 执行结束后 Experience 自动落库，不需要手动调用 CLI，字段
      完整覆盖 §7 yaml 结构"）已用新增测试
      `tests/test_phase3_experience_recorder.py`（5 用例）+ 既有
      `tests/test_core_experience_store.py`（13 用例）验证，均通过；
      相关回归测试无新增失败。详见
      `04-phase3-experience-layer-sprint-plan.md` 末尾"Sprint 3-1
      执行记录"与 `MIGRATION_STATUS.md`。可进入 **Sprint 3-2
      （ExperienceRetriever + Analyzer，接入 Goal 规划阶段）**，
      留待下一次推进。
- [x] Sprint 3-2（ExperienceRetriever + Analyzer）已完成：新增
      `core/experience_retrieval.py::retrieve_similar_experiences()`
      （关键词重叠度检索"同类历史 Experience"）+
      `render_experiences_as_context()`（渲染成可读文本）；新增
      `core/experience_patterns.py::summarize_failures()`（Analyzer
      雏形，聚合同类失败的状态分布 + 高频教训，为 Phase 9 Self
      Evolution 打基础，只统计不决策）；`goal_mode/runner.py::run()`
      在挂载 `ExperienceRecorder` 订阅之后接入检索——新增配置项
      `cfg.goal_mode.experience_retrieval_enabled`（默认关闭，保守
      opt-in 默认值），开启时按当前 `GoalSpec.goal_text` 检索相似历史
      Experience，渲染后一次性注入 `agent._hist`（`_type=
      "goal_experience_context"`），检索失败纯旁路兜底不影响 Goal
      本身执行。三条 Sprint 3-2 验收标准（生成结构化 Experience /
      检索结果真实出现在传给 LLM 的 context 里 / 旧 history-lesson-
      decision 测试仍通过）均已用新增测试验证：
      `tests/test_phase3_experience_retrieval_and_patterns.py`（6 用例）
      + `tests/test_phase3_experience_retrieval_injection.py`（3 用例，
      直接跑真实 `GoalRunner.run()` 断言 `agent._hist` 里出现检索结果）；
      回归测试（`test_goal_mode.py`/`test_goal_mode_phase2_events.py`/
      `test_core_experience_store.py`/`test_phase2_event_log_and_cli.py`/
      `test_phase3_experience_recorder.py`/`test_core_events.py` 共 128
      用例，123 通过，5 个既有失败与本次改动无关）。详见
      `04-phase3-experience-layer-sprint-plan.md` 末尾"Sprint 3-2
      执行记录"与 `MIGRATION_STATUS.md`。Phase 3（Experience 分层）
      两个 Sprint 均已完成；`phase3-experience-inventory.md` 里"lesson
      通过 Adapter 接入新 Experience"这一条尚未实现，留在
      `MIGRATION_STATUS.md` 里作为待办。
- [x] Phase 3 达标后进入 **Phase 4（统一 State）**，按
      `05-phase4-unified-state-sprint-plan.md` 划分的 Sprint 4-1
      （StateManager 骨架）已完成：新增 `core/state_manager.py::StateManager`
      （`get_state`/`update_state`/`snapshot()` 三个核心方法），`goal_mode/
      runner.py::run()`/`_finish()` 唯一接入点把 `GoalAdapter.to_new(spec)`
      转出的 `GoalState` 交给 `StateManager` 托管，之后每轮 CONTINUE 推进 /
      终止时各 publish 一次此前定义了取值但从未真正使用过的
      `GoalUpdated` 事件，`StateManager` 订阅该事件自动同步内部
      `GoalState` 的 `round`/`status`，`runner.py` 不再自己另外持有一份
      可变的 `GoalState` 引用。验收标准第 1、3 条（"GoalState 读写全部
      经过 StateManager"、"`snapshot()` 可用"）已用新增测试
      `tests/test_phase4_state_manager.py`（7 用例，含端到端跑一次
      `GoalRunner.run()` 断言全局 `StateManager` 单例状态变为
      `"done"`）验证；因新增的 `GoalUpdated` publish 会被
      `EventLogStore` 落盘，`test_goal_mode_phase2_events.py`/
      `test_phase2_event_log_and_cli.py` 两处对事件序列的精确断言同步
      更新（有意的行为变化，非破坏）；回归测试（`test_goal_mode.py` 等
      共 238 用例，233 通过，5 个既有失败与本次改动无关）+ 依赖图核对
      （`core.state_manager` inbound=1/outbound=0，未触发止损阈值）均已
      验证，详见 `05-phase4-unified-state-sprint-plan.md` 末尾"Sprint
      4-1 执行记录"。验收标准第 2 条（`SelfState`/`WorldState`/
      `CapabilityState`/`RuntimeState` 占位）对应 Sprint 4-2，留待下一次
      推进。
- [x] Sprint 4-2（占位 State + 一致性快照）已完成，**Phase 4（统一
      State）两条完成标志全部达成**：新增 `core/world.py::WorldState`/
      `core/capability.py::CapabilityState`/`core/runtime.py::RuntimeState`
      三个无字段空 dataclass 占位（分别对应原方案 §6/§9/§12，标注
      `# TODO: Phase 5/6/8 填充`）；发现计划文档把 `SelfState` 也列为
      "待建占位"与实情不符——`SelfState` 在 Sprint 1.5 已是真实字段，
      本次据实处理，只补上它缺的一环：`agent/lifecycle.py` 唯一接入点
      新增 `get_state_manager().update_state("self", ...)`，使其真正被
      `StateManager` 托管而不只是转换一次即弃。`00-original-architecture-
      proposal.md` 末尾新增"补充（Sprint 4-2 新增）"小节，以表格形式
      标注 Self/World/Capability/Runtime 四者当前状态与后续填充所属
      Phase，对应完成标志里的"phase-mapping 文档"要求。验收标准（占位
      State 不报错 + `snapshot()` 形状与真实 GoalState 一致）已用新增
      测试 `tests/test_phase4_state_placeholders.py`（7 用例）验证；
      回归测试（`test_phase4_state_manager.py`/`test_core_self_adapter.py`/
      `test_self_model.py`/`test_goal_mode.py` 等共 177 用例，172 通过，
      5 个既有失败与本次改动无关）+ 依赖图核对（三个新模块均
      inbound=1/outbound=0，未触发止损阈值）+ pyflakes 均确认无新增
      问题。详见 `05-phase4-unified-state-sprint-plan.md` 末尾"Sprint
      4-2 执行记录"与 `MIGRATION_STATUS.md`。Phase 4 已达到验收标准，
      可进入 **Phase 5（统一 Goal）**，按
      `06-phase5-goal-convergence-sprint-plan.md` 划分的 Sprint 继续
      推进（下一次对话的任务）。
- [x] Phase 5（统一 Goal）"现状盘点"已完成：产出
      `docs/architecture_v2/phase5-goal-inventory.md`，核心发现——原
      计划把 `Objective` 与 `goal_backlog.py` 当成两个独立、可轻量
      转换的处理项，实测后发现二者是**同一套**子系统
      （`perception/goal_backlog.py::GoalBacklog`/`GoalNode`，
      `level="objective"` 即"Objective"），`scripts/dep_graph.py`
      实测 inbound=33，**远超**止损阈值，且与
      `evolution/objective_executor.py`（自主执行引擎，反向回写
      `GoalNode.status`）紧耦合，服务于看板/自主循环/公平调度等 10+
      下游子系统、83 个测试文件涉及，与 Phase 1 已迁移的 `goal_mode/`
      （`GoalSpec`/`GoalRunner`）完全独立、互不引用。这与 Sprint 3
      对 `perception/memory_store.py` 的判断是同一类问题，处理方式
      也一致：**正式决策：Sprint 5-1 暂缓直接执行"Objective/GoalBacklog
      Adapter"，新增 Sprint 5.0.5（GoalBacklog/ObjectiveExecutor
      耦合拆解评估）**，参照 Sprint 1.5 方法论重新按子系统统计真实
      耦合面后再确定 Sprint 5-1 范围；`workflow/` 边界（inbound=5，
      未超阈值）盘点后确认原计划"本 Phase 不处理"仍然合理。变更留痕
      详见 `06-phase5-goal-convergence-sprint-plan.md` 末尾"变更
      记录"。Sprint 5.0.5 留待下一次推进（Sprint 5-2 的 Gap 检测
      不依赖此结论，可并行）。
- [x] Sprint 5.0.5（GoalBacklog/ObjectiveExecutor 耦合拆解评估）已
      完成：按 `12-execution-and-doc-sync-norms.md` 第六节第 6 条口径
      重新统计 `goal_backlog.py` 跨子系统 inbound（剔除同属
      `perception/` 包的 11 个调用方后）仍为 **22**，与
      `memory_store.py`（33 → 剔除后降到 5，跌破阈值）的先例**结论
      不同**——`evolution/` 包单独就贡献了 15 个真实跨子系统调用方，
      确认这是"看起来复杂、实测确认真的复杂"，不是"口径问题"，正式
      决策：`goal_backlog.py` **暂缓**，不属于任何已排期 Phase。
      `ObjectiveExecutor` 自身 inbound=5 未超阈值，但实测其唯一
      实例化点（`api/server.py::HttpServer._build_autonomous_loop()`
      内部）嵌在一段孤儿执行恢复/公平调度回调接线/隔离 Runner 切换等
      强时序依赖的构造闭包里，风险特征是"低耦合但高时序敏感"，不满足
      Self/history_manager 那套"单点旁路 trace"模式的安全前提；且其
      `ExecutionStep`（多步执行+重试+超时）语义上更贴近 Action/
      Capability，因此正式决策：**移出 Phase 5，移交 Phase 6（统一
      Action）一并评估**，已在 `07-phase6-action-model-sprint-plan.md`
      现状盘点表格补充这条待评估项。原 Sprint 5-1 的两项任务
      （Objective Adapter/GoalBacklog Adapter）因此从 Phase 5 范围内
      正式移除，Sprint 5-1 文档保留原表格存档并标注调整状态；
      `core/goal.py` 的字段扩充任务并入 Sprint 5-2 一并推进。Phase 5
      当前可执行范围收窄为 Sprint 5-2（Gap 检测能力），留待下一次
      推进。本次改动只涉及文档判断，未修改生产代码，无需跑回归测试。
      详见 `06-phase5-goal-convergence-sprint-plan.md` 末尾"Sprint
      5.0.5 执行记录"与 `MIGRATION_STATUS.md`。
- [x] Sprint 5-2（Gap 检测能力）已完成，**Phase 5 在 Sprint 5.0.5
      调整后的范围内达到完成标志**：`core/goal.py::GoalState` 追加
      `current_state`/`ideal_state`/`problems`/`gap`/`constraints`/
      `resources`/`priority`/`evidence`/`deadline` 九个默认值字段
      （不影响现有构造方式）；新增 `goals/` 包
      （`goals/gap.py::detect_gap()`），第一版按规则推导
      problems/gap（`current_state`/`ideal_state` 缺失记 problem，
      相同视为已达成，不同则优先用 `acceptance_criteria` 生成
      gap），可选 `llm_judge` 接管 problems 生成；内部固定读取
      `state_manager.get_state("self"/"world")` 作为输入，占位/未
      托管时不报错、只记录"不可用"。新增测试
      `tests/test_phase5_gap_detection.py`（8 用例）全部通过；回归
      测试共 185 用例，180 通过，5 个既有失败与本次改动无关；
      pyflakes 无新增告警。详见 `06-phase5-goal-convergence-sprint-
      plan.md` 末尾"Sprint 5-2 执行记录"。可进入 **Phase 6（统一
      Action）**，注意需要一并评估 Sprint 5.0.5 移交的
      `evolution/objective_executor.py`（下一次对话的任务）。
- [x] Phase 6（统一 Action）"现状盘点"已完成：产出
      `docs/architecture_v2/phase6-action-inventory.md`。实测
      `ToolExecutor`（inbound=11）/`PermissionGuard`（inbound=26）/
      `orchestrator/`（inbound=37）均超 Sprint 0 止损阈值，但**判定
      不需要因此暂停或重新规划**——与 Phase 3/5 不同，本 Phase
      Sprint 6-1/6-2 的设计从一开始就是"新增一层纯转发 wrapper，
      不改动被依赖模块内部"（`ActionExecutor` 只调用
      `tool_executor.py`/`permissions.py`/`orchestrator/` 现有的
      公开接口），止损条件针对的是"改动一个被广泛依赖的模块内部逻辑"，
      不适用于"新增调用方调一个稳定公开接口"。`ToolExecutor` 额外
      确认 11 个调用方全部在 `agent/` 包内，本质是 Agent 拆分出的
      内部协作类，风险比数字暗示的更低。`evolution/objective_executor.py`
      （Sprint 5.0.5 移交项）评估结论：Phase 5 发现的风险点是"改动它
      的构造闭包"，不是"新增转发分支调用已构造好的实例"，因此不需要
      额外止损处理，接入排期留给 Sprint 6-2 之后决定。原
      `07-phase6-action-model-sprint-plan.md` 的 Sprint 6-1/6-2
      任务表核实后无需调整，可直接推进到 **Sprint 6-1（ActionSpec +
      ActionExecutor 骨架，先接 Tool）**（下一次对话的任务）。
- [x] Sprint 6-1（ActionSpec + ActionExecutor 骨架，先接 Tool）已完成：
      新增 `core/action.py::ActionSpec`/`ActionResult`（`type` 取值
      `"tool"/"workflow"/"subagent"`，Sprint 6-1 只实现 `"tool"`）；
      新增 `actions/executor.py::ActionExecutor.execute()`——先调用
      现有 `permissions.py::PermissionGuard.check()` 做权限检查（不
      重新实现越权逻辑），通过后转发给现有
      `tools/__init__.py::ToolRegistry.call()`，执行前后 publish
      `ActionStarted`/`ActionCompleted`/`ActionFailed` 三类事件（与
      `goal_mode/runner.py` 现有 Phase 2 接入点用同一套
      `Event`/`EventBus`，`correlation_id` 可由调用方透传以关联同一次
      Goal 闭环）；`type="workflow"`/`"subagent"` 显式
      `NotImplementedError`，不留静默空实现。两条验收标准（"Goal 从
      检测到 gap 到通过 ActionExecutor 调用 Tool 拿到结果全流程打通"
      "故意越权场景验证权限拦截生效"）均已用新增测试
      `tests/test_phase6_action_executor.py`（5 用例，含一条真实跑
      `goals/gap.py::detect_gap()` 产出 gap 后驱动 `ActionExecutor`
      的全链路测试）验证，全部通过；`pyflakes` 无告警。详见
      `07-phase6-action-model-sprint-plan.md` 末尾"Sprint 6-1 执行
      记录"与 `MIGRATION_STATUS.md`。可进入 **Sprint 6-2（接入
      Workflow 与 SubAgent）**。
- [x] Sprint 6-2（接入 Workflow 与 SubAgent）已完成：`actions/
      executor.py` 新增 `type="workflow"`（转发给 `WorkflowStore.
      load()` + `WorkflowRunner.run()`）与 `type="subagent"` 两个分支。
      `type="subagent"` **没有**按原计划转发给
      `orchestrator/task_manager.py`/`orchestrator/sub_agent.py`——
      耦合评估发现这一对是线程模型且强依赖主 Agent session 生命周期，
      与 Phase 5 对 `objective_executor.py` 的"低耦合但高时序敏感"
      判定同类，改为转发给语义等价、专为独立同步调用设计的
      `workflow/agent_spawn.py::build_minimal_agent()` +
      `Agent.run_turn()`，已按规范留痕（详见
      `07-phase6-action-model-sprint-plan.md` "变更记录 2026-09-27"）。
      权限接入相应调整为 workflow/subagent 各自复用自己模块内部的
      审批机制，不再套用 Tool 专属的 `PermissionGuard.check()` 签名。
      新增测试共 9 个（含 Sprint 6-1 原有用例调整后共 11 个）全部
      通过，其中 `test_all_three_action_types_produce_uniform_result_
      shape` 直接验证三种 type 产出的 `ActionResult` 字段形状一致
      （验收标准第二条）。回归测试 563 passed / 6 既有失败（与本次
      改动无关）。**Phase 6（统一 Action）三条完成标志全部达成**，
      详见 `07-phase6-action-model-sprint-plan.md` 末尾"Sprint 6-2
      执行记录"与更新后的 `MIGRATION_STATUS.md`。可进入 **Phase 7
      （Decision + Simulation）**。
- [x] Phase 7（Decision + Simulation）Sprint 7-1（Candidate Actions
      生成 + 最小 Simulation）与 Sprint 7-2（Decision Engine 接入）
      已完成：新增 `core/simulation.py::SimulationScenario`/
      `SimulationResult`（无任何数值分数字段，对应文档"禁止数值化
      打分系统"的边界）；新增 `simulation/engine.py::
      generate_candidate_actions()`/`simulate_candidates()`，均按
      `goals/gap.py::detect_gap(llm_judge=...)` 的既有风格把 LLM
      调用点做成调用方注入的 `Callable`，不内置默认网络实现；
      `simulate_candidates()` 对每个候选独立检索 Phase 3 相似
      Experience，`SimulationResult.experience_refs` 记录实际引用的
      `Experience.id`，作为"确实引用历史"这条验收标准的可核验证据。
      新增 `cognition/decision.py::DecisionEngine.select()`——比任务表
      签名多返回一个 `DecisionTrace`（承接验收标准"完整决策 trace"的
      要求），构造时二选一注入 `llm_select`/`human_confirm`，两者都给
      时 `human_confirm` 优先。新增测试
      `tests/test_phase7_simulation_decision.py`（8 用例，含一条
      `test_full_chain_gap_to_candidates_to_simulation_to_decision_
      to_action` 完整走一遍 Gap→Candidate→Simulation→Decision→
      Action→Experience 全链路）全部通过；`pyflakes` 无告警；回归
      测试 594 passed / 6 既有失败（与本次改动无关，同 Phase 6 记录
      的一组）。**范围决策**：未把决策链路自动接入
      `goal_mode/runner.py` 主循环——任务表"接入 Phase 6"字面要求的
      是"选中的 ActionSpec 直接交给 ActionExecutor 执行"，用测试验证
      转发路径成立即满足，扩大到自动接入主循环是明显更大的改动且与
      Phase 7"最容易过度设计"的警示冲突，留给后续专门评估。详见
      `08-phase7-decision-simulation-sprint-plan.md` 末尾"Sprint 7-1/
      7-2 执行记录"与"后续判断依据"一节。**Phase 7 三条完成标志全部
      达成**，可进入 **Phase 8（Runtime Convergence）**（下一次对话的
      任务，见 `next_doc/refactor_plan/09-phase8-runtime-convergence-
      sprint-plan.md`）。
- [x] Phase 8（Runtime Convergence）Sprint 8-1（AgentRuntime 单次循环
      骨架）已完成：新增 `runtime/` 包（`runtime/__init__.py` +
      `runtime/runtime.py::AgentRuntime`），`run_once(goal_spec)` 实现
      `observe → state update → gap detect → plan → simulate → decide →
      execute → record → learn` 九步骨架，**只支持单次运行**（Sprint
      8-2 才做常驻循环）；`execute`/`record` 两步**完全复用**
      `goal_mode/runner.py::GoalRunner`（不重新实现），`gap detect` 是
      `plan/simulate/decide` 三步里唯一默认真实执行的一步（调用 Phase 5
      `goals/gap.py::detect_gap()`），`plan`/`simulate`/`decide` 默认
      跳过——沿用 Phase 7 Sprint 7-2"不强行接入主循环"的范围决策，避免
      过度设计；`learn` 留空并显式标注 TODO（对应 Phase 9）。新增
      `RuntimeCycleStarted`/`RuntimeCycleCompleted` 两个 Event kind
      （已加入 `core/events.py::EVENT_KINDS`），包裹住内部 Goal 闭环
      已有的四类事件。接入点：`cli/commands/goal_mode_cmd.py::
      _run_goal()`（"用户主动发起一个新 Goal"这条路径）内部实现从
      直接调用 `GoalRunner` 改为调用 `AgentRuntime.run_once()`，旧入口
      签名/展示逻辑不变；`/goal resume` 场景本 Sprint 未改动（`run_once`
      已透传 `state_store`/`resume_state` 参数为后续预留接口）。验收
      标准（"通过 CLI 发起一个 Goal，能看到完整走了一遍 Phase 1-7 打通
      的链路，且旧的 CLI 相关测试仍然通过"）已用新增测试
      `tests/test_phase8_runtime.py`（4 用例：确认真的调用了
      `GoalRunner`、Event 顺序正确、gap detect 真实产出、`KeyboardInterrupt`
      时 `last_runner` 可用）验证，全部通过；回归测试（`test_goal_mode.py`/
      `test_goal_mode_phase2_events.py`/`test_goal_mode_characterization.py`/
      `test_phase4_state_manager.py`/`test_phase5_gap_detection.py`/
      `test_core_events.py` 共 292 用例，287 通过，5 个既有失败
      `test_build_from_history_*` 与本次改动无关，与此前多个 Phase 记录
      里提到的是同一组）；`pyflakes`/`scripts/dep_graph.py` 核对均无
      问题。详见 `09-phase8-runtime-convergence-sprint-plan.md` 末尾
      "Sprint 8-1 执行记录"与 `MIGRATION_STATUS.md`。可进入 **Sprint 8-2
      （接入持续运行 + 一种旧 Scheduler）**（下一次对话的任务）。
