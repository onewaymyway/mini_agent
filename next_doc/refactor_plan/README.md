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
