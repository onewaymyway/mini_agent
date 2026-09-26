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
- [ ] Sprint 0 尚未开始执行
