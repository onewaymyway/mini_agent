# mini_agent 架构收敛：可执行 Sprint 计划（第一阶段）

> 本文把原始方案（`00-original-architecture-proposal.md`）中偏概念性的
> "Phase 0 / Phase 1 / 第一条迁移链"（原文 §33-34、§51-54）重新组织成
> 4 个两周迭代（Sprint），每个迭代都有明确交付物、验收标准和止损条件。
>
> 目标范围：只覆盖原方案的 **Phase 0 + Phase 1 + Goal 迁移链**，
> 即"架构收敛"的第一阶段。后续 Memory / Self / World 等迁移，
> 待 Sprint 3 复盘后再单独排期。

---

## 总览

| Sprint | 主题 | 时长 | 对应原文 Phase |
|---|---|---|---|
| Sprint 0 | 地基与安全网 | 1 周 | Phase 0 |
| Sprint 1 | Domain Model + Goal 试验 | 2 周 | Phase 1 + 迁移链起点 |
| Sprint 2 | Experience 落地 + 可演示成果 | 2 周 | 迁移链延伸 |
| Sprint 3 | 复盘 + 推广决策 | 2 周 | 阶段收尾 |

---

## Sprint 0（1 周）：地基与安全网

**目的**：在动任何代码之前，先建立"改坏了能立刻知道"的能力。

| 任务 | 产出 | 验收标准 |
|---|---|---|
| 依赖关系扫描 | 一个脚本（如 `scripts/dep_graph.py`，用 `ast` 或 `grimp`）扫描 `src/mini_agent`，输出 `goal_mode` 对外/对内依赖清单 | 能明确列出"谁 import 了 `goal_mode`"、"`goal_mode` 又 import 了谁" |
| 冻结新增一级概念 | `CONTRIBUTING.md` 增加规则 + 一个 lint 脚本：禁止新建顶层含 `Manager/Scheduler/Advisor` 后缀的模块，除非在白名单里 | CI 能拦截违规 PR |
| 为 `goal_mode` 补特征测试 | 针对 `goal_mode/executor.py`、`runner.py` 现有行为录制"输入→输出"快照测试（不追求代码逻辑正确性，只保证"迁移前后行为一致"） | 覆盖主要分支即可，不要求 100% |
| 建立总纲文档 | `docs/architecture_v2/00-overview.md`：用一页纸重写核心结论（不是全文复制原方案） | 团队/未来的自己 5 分钟能看懂方向 |

**止损条件**：如果依赖扫描发现 `goal_mode` 和其它模块的耦合远超预期
（比如被 10+ 个模块直接 import 内部类），需要重新评估"Goal 是否还是
合适的第一条迁移链"，必要时改选更独立的子系统。

---

## Sprint 1（2 周）：Domain Model + Goal 试验

**原则**：不建"十几个设计文档 + 十几个空文件"，改为**边写最小代码边补文档**，
只建 Goal 链路真正用得上的部分。

| 任务 | 产出 |
|---|---|
| 建 `src/mini_agent/core/` | 只放 4 个文件：`types.py`（公共类型）、`events.py`、`goal.py`（`GoalState` dataclass）、`experience.py`（`Experience` dataclass）——不是原文档 §52 列的 11 个文件全建 |
| 定义 Adapter 契约 | `core/adapter.py`：定义 `Adapter[Old, New]` 最小协议——`to_new(old) -> New` / `to_old(new) -> Old`，作为后续所有 Adapter（Goal / Memory / Self）的统一接口 |
| `GoalAdapter` | 实现上述协议：`GoalAdapter.to_new(old_goal) -> GoalState`、`GoalAdapter.to_old(state) -> old_goal`，明确双向转换 |
| 接入点选择 | 只在 `goal_mode/runner.py` 的**一个**调用点接入 Adapter（执行前后各转换一次），不动 `executor.py` 内部逻辑 |
| 验证特征测试 | Sprint 0 录的特征测试迁移后仍全部通过 |

**验收标准**（比原文 §61 更具体）：

1. `goal_mode/runner.py` 里能看到一次完整的调用链：
   `Goal(old) → GoalAdapter → GoalState → 执行 → Outcome → Experience`，
   且有日志/trace 能证明这条链真的被执行过，不是"定义了但没人用"。
2. Sprint 0 录的特征测试全部通过。
3. 现有 398 个测试中，与 `goal_mode` 相关的部分（先筛出来）全部通过。

**止损条件**：如果 Adapter 双向转换在 Goal 这一个子系统上就出现明显的
"数据丢失"或"来回转换不一致"问题，暂停向 Memory 推广，先在 Sprint 1
内部把协议改稳。

---

## Sprint 2（2 周）：Experience 落地 + 可演示成果

**目的**：给团队/用户一个"重构真的有用"的阶段性演示，避免士气在长周期
重构中流失。

| 任务 | 产出 |
|---|---|
| `experience/store.py` 最小实现 | 用 JSON 或 SQLite 持久化 Sprint 1 产生的 `Experience` 对象 |
| 检索能力 | 一个 CLI 命令，如 `mini_agent experience search "<关键词>"`，能从存储里检索出上一次执行的 `Experience` |
| 可演示效果 | Agent 第二次遇到类似目标时，能看到自己上次做过什么（这是 Sprint 2 的 quick win，用于对内/对外汇报） |

**验收标准**：
1. 至少能演示一次"检索到历史 Experience 并影响本次决策"的完整流程。
2. Experience 的存储格式与 `core/experience.py` 中的 dataclass 定义一致。

---

## Sprint 3（2 周）：复盘 + 推广决策

**目的**：决定第一阶段的模式是否可以复制到 Memory / Self，而不是默认
"继续往下做"。

| 任务 | 产出 |
|---|---|
| 复盘会 | 记录 Sprint 1-2 遇到的耦合问题、Adapter 双向转换的坑 |
| 更新映射表 | 更新原文档 §30 的"当前模块 → 下一代归属"映射表，补充实际踩坑后发现的隐藏依赖 |
| 迁移完成度标注 | 建立 `MIGRATION_STATUS.md`，标注 `goal_mode` 当前有多少路径真正走了新 Adapter（避免"看起来迁移完成、实际只做了一半"的假象） |
| 正式决策 | 决定 Memory → Experience 这条链是否按 Sprint 1-2 的模式做，还是需要调整 Adapter 设计 |

**Phase 1（第一阶段）整体完成的判定标准**（细化自原文 §61）：

1. 新代码不再随意创建新的一级概念（由 Sprint 0 的 lint 规则保证）。
2. `Self / World / Experience / Goal / Capability / Action / Simulation /
   Runtime` 术语在 `core/` 中有对应的最小 dataclass 定义。
3. Goal 能完整走通 `Goal → Action → Outcome → Experience` 链路，且有
   trace 证据（不是"文档里画了图"）。
4. Experience 可以被下一次类似任务检索到（Sprint 2 已验证）。
5. 旧的 `goal_mode` 模块仍可正常运行，且原有测试全部通过。
6. 依赖图显示 `goal_mode` 与其它模块的耦合度没有因为迁移而上升。
7. `MIGRATION_STATUS.md` 如实反映了当前迁移完成度，不是"全有或全无"。

达到以上条件后，才进入原方案 §54 提到的 P1 阶段（Action Model / Self
Model），届时应参照本文档的格式，为 Memory / Self 单独产出一份
`03-memory-migration-sprint-plan.md`。

---

## 附：本目录文件说明

| 文件 | 内容 |
|---|---|
| `00-original-architecture-proposal.md` | 原始重构设计文档（用户上传） |
| `01-evaluation-and-gaps.md` | 对原方案的评估：合理性判断 + 结构性缺口 + 额外改进方向 |
| `02-executable-sprint-plan.md` | 本文档：把第一阶段拆成 4 个可执行 Sprint |

---

## Sprint 0 执行记录（复盘，按 `12-execution-and-doc-sync-norms.md` 第五节最低要求）

**验收标准逐条对照**（Sprint 0 表格里的四项任务）：

1. 依赖关系扫描脚本 `scripts/dep_graph.py`（AST 静态解析，未使用
   `grimp`——当前环境该三方包不可用，改用标准库 `ast` 实现同等效果）。
   已实测输出 `goal_mode` 的 inbound/outbound 清单，能明确列出"谁
   import 了 goal_mode"、"goal_mode 又 import 了谁"，达成验收标准。
2. 冻结新增一级概念：新增 `scripts/lint_no_new_toplevel_concepts.py` +
   `CONTRIBUTING.md` 规则说明。**与原验收标准有一处偏差**：原文写
   "CI 能拦截违规 PR"，但当前仓库没有任何 CI 配置文件（`.github/workflows`
   等均不存在），因此暂时只能保证"本地/PR 评审时手动运行能正确拦截"
   （已用单元测试 `tests/test_lint_no_new_toplevel_concepts.py` 验证脚本
   本身逻辑正确、退出码符合 CI 集成的要求），尚未真正接入 CI 自动拦截。
   见下方"影响范围"。
3. 特征测试：新增 `tests/test_goal_mode_characterization.py`，覆盖
   `CoarseStepExecutor` 全部分支 + `runner.py` 中 `render_replan_proposal`
   / `_compute_progress_score` / `_record_dead_end` / `_render_dead_ends_block`
   / `_extract_replan_proposal` / `_build_goal_aware_compact_hint` 六个
   纯函数/轻状态方法的输入输出快照；加上已有的 `tests/test_goal_mode.py`
   （92 个用例，覆盖 GoalRunner 主循环 DONE/CONTINUE/NEED_COMPACT/stuck/
   max_rounds 等主要分支），两者合计构成"迁移前后行为一致"的安全网。
   未追求 100% 覆盖（符合验收标准"覆盖主要分支即可"）。
4. 总纲文档：新增 `docs/architecture_v2/00-overview.md`。

**是否触发止损条件**：未触发。`scripts/dep_graph.py` 对 `goal_mode` 的
实测结果显示 inbound 深度依赖（import 具体符号/类）的文件数为 5，
远低于止损阈值（10+），Goal 仍是合适的第一条迁移链，可以按计划进入
Sprint 1。

**`MIGRATION_STATUS.md` 是否已同步更新**：Sprint 0 本身不涉及任何模块的
实际迁移（`MIGRATION_STATUS.md` 记录的是"迁移完成度"，Sprint 0 只是
建立安全网），因此该文件保持初始占位状态不变，符合预期，不需要更新。

**下一个 Sprint（Sprint 1）开始前需要注意**：
- Sprint 1 的"验证特征测试"任务依赖本次新增的两个测试文件持续通过，
  改动 `goal_mode/` 时先跑 `pytest tests/test_goal_mode.py
  tests/test_goal_mode_characterization.py`。
- 前置条件补充：执行 Sprint 0 时发现仓库现有测试里有 5 个既有失败用例
  （`tests/test_goal_mode.py` 的 `test_build_from_history_*` 系列，
  `GoalSpecBuilder._run_builder` 相关 lambda 签名与 `spec.py` 实际调用
  参数 `detection_text` 不匹配）与本次改动无关，是 Sprint 0 开始前就
  存在的问题；Sprint 1 统计"现有 398 个测试中与 goal_mode 相关的部分
  全部通过"时，应把这 5 个已知失败排除在外单独跟踪，不要误判为 Sprint 1
  引入的回归。

**影响范围**：上述"CI 未真正接入"的偏差不影响 Sprint 1-3 的前置条件
（Sprint 1 不依赖 CI 自动拦截才能开始），但建议在仓库后续接入 CI 时
（不属于本次架构重构范围，是否/何时接入 CI 由项目所有者决定）把
`python scripts/lint_no_new_toplevel_concepts.py` 和
`pytest tests/test_goal_mode.py tests/test_goal_mode_characterization.py`
加入流水线。
