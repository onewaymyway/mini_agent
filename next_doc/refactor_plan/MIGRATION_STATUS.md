# MIGRATION_STATUS.md（迁移完成度台账）

> 格式规范见 `12-execution-and-doc-sync-norms.md` 第三节。
> 每个被迁移的模块占一行，**每次相关 PR 合并后由负责人更新**，
> 不允许等到 Sprint 复盘时才批量补。

状态只允许四种取值：`未开始 / 部分迁移 / 完全迁移 / 已降级到 legacy`

| 模块 | 所属 Phase | 状态 | 走新链路的路径占比 | Adapter 是否双向 | 最后更新 | 负责人 |
|---|---|---|---|---|---|---|
| goal_mode/runner.py | Phase 1 | 部分迁移 | 未知，待补充（`run()`/`_finish()` 两处接入点已走新链路做转换+trace+持久化，主循环内部仍是旧逻辑，未按"路径条数"精确统计） | 是（`GoalAdapter.to_new`/`to_old` 均已实现并被特征测试覆盖） | 2026-09-26 | Sprint 2 执行者 |
| goal_mode/executor.py | Phase 1 | 未开始 | 0% | 否 | - | - |
| goal_backlog.py | Phase 5 | 未开始 | 0% | 否 | - | - |
| history_manager.py | Phase 3 | 部分迁移 | 未知，待补充（`agent/lifecycle.py::_init_components()` 唯一接入点已走新链路做转换+trace，`HistoryManager` 内部逻辑未改动，见 `03-sprint1.5-memory-perception-coupling-assessment.md` "七、history_manager.py Adapter 接入点执行记录"） | 否（`HistoryAdapter.to_old` 尚未实现，无实际调用方） | 2026-09-26 | history_manager Adapter 接入点执行者 |
| workflow/ | Phase 6 | 未开始 | 0% | 否 | - | - |
| tools/ + tool_executor.py | Phase 6 | 未开始 | 0% | 否 | - | - |
| orchestrator/ | Phase 6 | 未开始 | 0% | 否 | - | - |
| evolution/（67 个模块，逐步细分） | Phase 9 | 未开始 | 0% | 否 | - | - |
| perception/self_model.py | Phase 4 | 部分迁移 | 未知，待补充（`agent/lifecycle.py::_init_components()` 唯一接入点已走新链路做转换+trace，`AgentSelfModelBuilder`/`AgentSelfModel` 内部逻辑未改动） | 否（`SelfAdapter.to_old` 尚未实现，无实际调用方，见 `03-sprint1.5-memory-perception-coupling-assessment.md` "五、Self 迁移链执行记录"） | 2026-09-26 | Self 迁移链执行者 |
| perception/memory_store.py（原表遗漏，Sprint 1.5 补充） | Phase 3 | 部分迁移 | 未知，待补充（止损口径调整为只统计跨子系统 inbound=5，低于阈值 10+，`perception/` 包内部 12 个调用方按新口径不计入统计；Adapter 接入点已完成 `MemoryBackend → MemorySnapshot` 单向转换，唯一接入点 `agent/core.py::Agent.__init__()`，见 `03-sprint1.5-memory-perception-coupling-assessment.md` "十一、perception/memory_store.py 止损口径评估 + Adapter 接入点执行记录"） | 否（`MemoryAdapter.to_old` 尚未实现，无实际调用方） | 2026-09-26 | memory_store Adapter 接入点执行者 |
| evolution/memory_types.py（新增门面模块，Sprint 1.5 补充） | Phase 3 | 完全迁移 | 100%（`evolution/` 包内部对 `MemoryEntry` 的统一访问入口，本身只做重新导出，不涉及迁移语义） | 否 | 2026-09-26 | memory_store 分层 facade 第一层执行者 |
| agent/memory_types.py（新增门面模块，Sprint 1.5 补充） | Phase 3 | 完全迁移 | 100%（`agent/` 包内部对 `MemoryEntry` 的统一访问入口，本身只做重新导出，不涉及迁移语义） | 否 | 2026-09-26 | memory_store 分层 facade 第二层执行者 |
| core/memory.py + core/memory_adapter.py（新增，Sprint 1.5 补充） | Phase 3 | 部分迁移 | `MemoryBackend → MemorySnapshot` 单向转换已完成（`entry_count`/`backend_kind` 两字段），唯一接入点 `agent/core.py::Agent.__init__()` | 否（`to_old` 未实现） | 2026-09-26 | memory_store Adapter 接入点执行者 |
| Daemon / AutonomousLoop / Cron / UnifiedTaskScheduler / ObjectiveExecutor / ResourceArbiter | Phase 8 | 未开始 | 0% | 否 | - | - |
| core/events.py（Sprint 2-1 字段扩展） | Phase 2 | 部分迁移 | Goal 链路唯一接入点（`goal_mode/runner.py::run()`/`_finish()`）已用新字段（`id`/`actor`/`causation_id`/`correlation_id`）publish 4 类事件；其余旧模块（`history_manager.py`/`perception/behavior/`/`evolution/`/`orchestrator/`）的"事件雏形"尚未盘点、未接入 | 不适用（`Event` 是新领域概念本身，不是 Old↔New 转换 Adapter） | 2026-09-26 | Phase 2 Sprint 2-1 执行者 |
| core/event_bus.py（新增，Sprint 2-1） | Phase 2 | 完全迁移 | 100%（新增模块，实际被 `core/__init__.py` 一处导入再转导出给 `goal_mode/runner.py` 使用；已按 `scripts/dep_graph.py --module core.event_bus` 核对反向依赖，inbound=1、outbound=0，未触发止损阈值，见 `03-phase2-event-model-sprint-plan.md` "Sprint 2-1 收尾核对记录"） | 不适用 | 2026-09-26 | Phase 2 Sprint 2-1 收尾核对执行者 |
| docs/architecture_v2/phase2-event-inventory.md（新增，Sprint 2-1 收尾） | Phase 2 | 完全迁移 | 100%（纯盘点文档，盘点 `history_manager.py`/`perception/behavior/events.py`/`evolution/`/`orchestrator/plan.py` 四类现有事件雏形并给出映射建议，判定四者本次均不接入 `core/event_bus.py`） | 不适用 | 2026-09-26 | Phase 2 Sprint 2-1 收尾核对执行者 |
| core/event_log_store.py（新增，Sprint 2-2） | Phase 2 | 完全迁移 | 100%（新增模块：`EventLogStore` JSONL 落盘 + `trace(correlation_id)` 检索、`ensure_event_log_subscribed()` 幂等挂载订阅者；`goal_mode/runner.py::run()` 唯一接入点新增一行挂载调用，纯旁路，未改动 `run()`/`_finish()` 控制流或返回值） | 不适用 | 2026-09-26 | Phase 2 Sprint 2-2 执行者 |
| cli/commands/events_cmd.py（新增，Sprint 2-2） | Phase 2 | 完全迁移 | 100%（`mini-agent events trace\|list` 只读 CLI，与 `experience_cmd.py` 短路接入方式一致，见 `cli/app.py::main()`） | 不适用 | 2026-09-26 | Phase 2 Sprint 2-2 执行者 |

> 以上为初始占位行，对应 `02`-`11` 各 Phase 文档里"现状盘点"提到的
> 主要模块。执行过程中如发现遗漏模块，直接追加新行，不要删除已有行
> （即使某模块后来判定"暂不迁移"，也应保留记录并把状态维持在
> "未开始"，附注说明原因，而不是从表中移除）。
