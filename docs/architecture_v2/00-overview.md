# mini_agent 架构收敛：一页纸总览

> 本文是 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 0
> 要求的"总纲文档"——用一页纸重写核心结论，不是原方案
> （`next_doc/refactor_plan/00-original-architecture-proposal.md`）的
> 全文复制。目标读者：5 分钟内看懂"现在在做什么、为什么、走到哪一步了"。
> 完整方案、评估、逐 Phase 计划都在 `next_doc/refactor_plan/` 目录，
> 本文只是入口索引 + 结论摘要。

## 一句话

mini_agent 从"每个新功能都新开一个顶层概念（XxxManager/Advisor/...）
的能力堆叠型 Agent"，收敛成"`Self / World / Experience / Goal /
Capability / Action / Simulation / Runtime` 八个核心概念、旧模块通过
Adapter 接入的 Self-centered Personal AI Runtime"。

## 为什么要做

现状：仓库已经积累了大量功能（daemon、goal_mode、workflow、evolution、
wiki、growth_advisor、goal_tree……），但这些功能之间没有统一的领域模型，
新功能倾向于"再开一个新概念"而不是"并入已有概念"，导致概念数量和相互
耦合持续增长。详见 `next_doc/refactor_plan/01-evaluation-and-gaps.md`
的评估。

## 怎么做（止损原则，贯穿全部 Phase）

- **不做 Big Bang Rewrite**：旧模块通过 Adapter（`to_new`/`to_old` 双向
  转换协议）接入新概念，不整体重写。
- **代码先行，文档紧跟**：`next_doc/refactor_plan/12-execution-and-doc-sync-norms.md`
  规定"改代码 → 同次提交内更新文档"的强制流程，不允许"文档写得很美，
  代码没真正走新链路"。
- **每步都可验证**：验收标准必须是可运行验证的（测试通过 / trace 存在），
  不是主观描述；`MIGRATION_STATUS.md` 如实记录每个模块的迁移完成度，
  不是"全有或全无"。
- **冻结新增一级概念**：重构完成前，`src/mini_agent/` 顶层禁止新增
  `Manager/Scheduler/Advisor` 后缀模块（见 `CONTRIBUTING.md`），逼着
  新功能先考虑能否并入已有概念。

## 当前进度

第一阶段（Phase 0 + Phase 1 + Goal 迁移链，见
`next_doc/refactor_plan/02-executable-sprint-plan.md`）：

- [x] **Sprint 0（地基与安全网）**：
  - 依赖关系扫描脚本 `scripts/dep_graph.py`（对 `goal_mode` 的扫描结果：
    inbound 5 个文件、均为浅层依赖，未触发"10+ 深度耦合"止损阈值——
    Goal 仍是合适的第一条迁移链）。
  - 冻结新增一级概念的 lint 脚本 `scripts/lint_no_new_toplevel_concepts.py`
    + `CONTRIBUTING.md` 规则说明（仓库目前无 CI 配置，暂为本地/PR 评审
    时手动运行，接入 CI 后再自动拦截）。
  - `goal_mode` 特征测试安全网：`tests/test_goal_mode.py`（已有 92 个
    GoalRunner 状态机测试）+ 新增 `tests/test_goal_mode_characterization.py`
    （`CoarseStepExecutor` 全覆盖 + `runner.py` 若干纯函数/辅助方法的
    输入输出快照）。
  - 本文档。
- [x] **Sprint 1（Domain Model + Goal 试验）**：
  - 新建 `src/mini_agent/core/`：只放 Goal 链路用得上的 5 个文件
    （`types.py`/`events.py`/`goal.py`/`experience.py`/`adapter.py`），
    未按原方案 §52 一次性建满 11 个文件。
  - `core/goal_adapter.py::GoalAdapter` 实现 `Adapter[Old, New]` 协议，
    完成 `GoalSpec ↔ GoalState(core)` 双向转换；`goal_run_result_to_experience`
    完成 `GoalRunResult → Experience(core)` 转换。
  - **唯一接入点**：`goal_mode/runner.py` 的 `run()`/`_finish()`，未改动
    `executor.py` 内部逻辑；两处均记录 DEBUG trace 日志作为"链路真的被
    执行过"的证据（`tests/test_core_goal_adapter.py` 已断言）。
  - 回归验证：`goal_mode` 特征测试安全网 126 passed（5 个 Sprint 0 已知
    历史失败照旧，与本次无关）；`dep_graph.py` 复查未触发止损阈值。
  - 详见 `next_doc/refactor_plan/02-executable-sprint-plan.md` 末尾
    "Sprint 1 执行记录" 与 `next_doc/refactor_plan/MIGRATION_STATUS.md`。
- [x] **Sprint 2（Experience 落地 + 可演示成果）**：
  - `core/experience_store.py::ExperienceStore`：Experience 的最小
    持久化实现（append-only JSONL，理由见文件头注释：与仓库已有
    `memory.jsonl` 落盘方式一致，量级不需要 SQLite）。新增
    `AgentPaths.workdir_experience_store`
    （`<project_root>/.agent/experience_store.jsonl`）。
  - `mini-agent experience search "<关键词>" [--limit N]` /
    `mini-agent experience list [--limit N]`：只读 CLI 检索命令
    （`cli/commands/experience_cmd.py`，与 `projects`/`workflow` 等
    既有短路子命令写法一致）。
  - `goal_mode/runner.py::_finish()` 在 Sprint 1 的转换之后新增一行
    实际持久化调用，仍只在同一个接入点，未新增第二处。
  - 可演示效果：写入一条 Experience 后，`mini-agent experience search`
    能检索命中（已实测，见"Sprint 2 执行记录"）；尚未接入主 Agent 的
    prompt 组装（即"Agent 决策时自动看到"），明确标注留给后续 Phase。
  - 详见 `next_doc/refactor_plan/02-executable-sprint-plan.md` 末尾
    "Sprint 2 执行记录"。
- [ ] Sprint 3（复盘 + 推广决策）：待开始。

已知问题（Sprint 0 执行期间发现，不在本次改动范围内，如实记录）：
`tests/test_goal_mode.py` 里 `test_build_from_history_*` 系列（5 个用例）
在当前仓库状态下会失败——测试里的 lambda 参数签名与
`goal_mode/spec.py` 实际调用参数（新增了 `detection_text` 关键字参数）
不匹配，看起来是代码演进后测试没有同步更新，与 Sprint 0 的改动无关。
另外，未安装可选依赖 `fastapi`/`uvicorn`/`sse-starlette`/`python-multipart`
（`pip install -e .[http]`）时，涉及 HTTP API 层的部分测试会因
`ModuleNotFoundError` 失败，这是环境依赖缺失，不是代码问题。

## 详细文档索引

- 原始方案：`next_doc/refactor_plan/00-original-architecture-proposal.md`
- 评估与缺口：`next_doc/refactor_plan/01-evaluation-and-gaps.md`
- Sprint 计划：`next_doc/refactor_plan/02-executable-sprint-plan.md`（Phase 0+1）
  到 `next_doc/refactor_plan/11-phase10-legacy-decommission-plan.md`（Phase 10）
- 执行规范：`next_doc/refactor_plan/12-execution-and-doc-sync-norms.md`
- 迁移完成度台账：`next_doc/refactor_plan/MIGRATION_STATUS.md`
- 目录总览与阅读顺序：`next_doc/refactor_plan/README.md`
