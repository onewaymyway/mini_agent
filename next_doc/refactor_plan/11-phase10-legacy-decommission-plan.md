# Phase 10：旧系统降级 —— 可执行计划

> 对应原方案 §43。前置条件：Phase 1-9 全部完成，新架构的八大核心概念
> 已在真实场景下跑通完整闭环。本 Phase 不是"删除旧代码"，而是让
> **用户/新开发者看不到旧系统**，旧实现可以继续在内部运行很长时间。

## 目标与边界

原文明确：*"用户看不到旧系统，但旧实现可以在内部继续运行很长时间。"*
这意味着本 Phase 的交付物主要是**接口层面的收敛**，不是大规模删除代码。

## Sprint 10-1（1.5 周）：对外 API 收敛

| 任务 | 产出 |
|---|---|
| 梳理对外入口 | 列出 CLI / HTTP API / 微信 / Android 等所有对外暴露的接口，标注每个接口当前是直接调用旧模块，还是已经过 `AgentRuntime` |
| 统一入口 | 未收敛的接口，改为统一调用 `AgentRuntime`（Phase 8 的产出），旧模块降级为其内部实现细节 |
| 更新映射表 | 把原文 §43 的映射关系（`旧 Goal→Adapter`、`旧 Memory→Adapter`、
`旧 Workflow→Capability`、`旧 Scheduler→Runtime adapter`、
`旧 Advisor→Decision policy`、`旧 Objective→Goal internal step`）
逐条核对是否已经落地，写入 `MIGRATION_STATUS.md` 最终版 |

**验收标准**：所有对外接口的文档/帮助信息里，只出现 Self/World/
Experience/Goal/Capability/Action/Simulation/Runtime 这套术语，
不再直接暴露 `GoalManager/ObjectiveExecutor/CronScheduler/...` 等
旧类名给最终用户。

## Sprint 10-2（1 周）：代码可见性收敛（非删除）

| 任务 | 产出 |
|---|---|
| 目录调整 | 参照原方案 §29 的目标目录结构，把已经完全走 Adapter 的旧模块移动到 `legacy/` 或类似命名空间下（物理上仍存在，但目录名清晰标注"这是被适配的旧实现"） |
| import 检查 | 确认新代码（`core/`、`runtime/`、`goals/` 等）不再直接 import 旧模块的内部实现细节，只通过 Adapter 交互 |

**验收标准**：依赖图脚本（Sprint 0 建立）显示，`legacy/` 目录下的模块
只被对应的 Adapter 引用，没有被新架构代码跨层直接调用。

**止损条件**：如果某个旧模块被发现有多处新代码在绕过 Adapter 直接调用，
说明 Adapter 覆盖不完整，应先补齐 Adapter，再执行目录移动，不要在
Adapter 不完整的情况下强行移动目录（会导致 import 报错）。

## Sprint 10-3（1 周）：文档与验收总结

| 任务 | 产出 |
|---|---|
| `docs/architecture_v2/11-migration-plan.md` 定稿 | 汇总 Phase 0-10 全部完成情况 |
| 架构收敛验收报告 | 对照最初原方案 §61 的判定标准，逐条核对是否达成，产出最终报告 |

## 完成标志（对应原方案的"最终理想状态"，§56）

- [ ] 开发者/用户首先看到的是 `AgentRuntime`，而不是一堆 Manager/
      Scheduler/Advisor
- [ ] 旧模块全部通过 Adapter 接入，且已移动到清晰标注的 `legacy/`
      命名空间下
- [ ] `MIGRATION_STATUS.md` 显示所有关键路径均已完成迁移标注
- [ ] 全量测试（398+ 个测试文件）通过率与重构前持平或更好
