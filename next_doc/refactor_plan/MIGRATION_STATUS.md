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
| perception/memory_store.py（原表遗漏，Sprint 1.5 补充） | Phase 3 | 未开始 | 0%（Sprint 1.5 耦合评估：inbound=33，其中 21 个跨 `agent/`/`evolution/`/`api/`/`goal_mode/` 等至少 4 个子系统，耦合面最广，建议暂缓，等 history_manager.py 的 facade 经验积累后再评估，见 `03-sprint1.5-memory-perception-coupling-assessment.md`） | 否 | 2026-09-26 | Sprint 1.5 执行者 |
| Daemon / AutonomousLoop / Cron / UnifiedTaskScheduler / ObjectiveExecutor / ResourceArbiter | Phase 8 | 未开始 | 0% | 否 | - | - |

> 以上为初始占位行，对应 `02`-`11` 各 Phase 文档里"现状盘点"提到的
> 主要模块。执行过程中如发现遗漏模块，直接追加新行，不要删除已有行
> （即使某模块后来判定"暂不迁移"，也应保留记录并把状态维持在
> "未开始"，附注说明原因，而不是从表中移除）。
