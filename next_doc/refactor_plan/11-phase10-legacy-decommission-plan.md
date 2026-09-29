# Phase 10：旧系统降级 —— 可执行计划

> 对应原方案 §43。前置条件：Phase 1-9 全部完成，新架构的八大核心概念
> 已在真实场景下跑通完整闭环。本 Phase 不是"删除旧代码"，而是让
> **用户/新开发者看不到旧系统**，旧实现可以继续在内部运行很长时间。

> **前置条件核对（Sprint 10-1，2026-09-28）**：上述前置条件目前**未满足**——
> Phase 9 完成标志第 3、4 条待在真实仓库核对，`AgentRuntime` 的 `learn` 步骤
> 仍空；台账里 `goal_mode/executor.py`、`objective_executor.py`、`orchestrator/*`
> 未开始，`goal_backlog.py` 暂缓。详见文末“变更记录 2026-09-28”与
> `docs/architecture_v2/phase10-entrypoint-inventory.md`。
>
> **更新（Phase 9 Sprint 9-4，2026-09-28）**：上述两项中的 `learn` 步骤与
> `DeployRecord` 持久化已由 Sprint 9-4 接入（默认关闭）；Phase 9 完成标志第 3、4
> 条仍待在真实仓库核对（现有 `scripts/check_frozen_evolution_modules.py --base
> <commit>` 一条命令即可）。`goal_mode/executor.py`/`objective_executor.py`/
> `orchestrator/*`/`goal_backlog.py` 的状态**未变**，因此前置条件整体**仍未满足**，
> D1–D5 仍待项目所有者决定。详见 `10-phase9-self-evolution-sprint-plan.md` 文末。
>
> **更新（Phase 10 A4，2026-09-28）**：`goal_mode/executor.py` 已评估——它是可替换的策略接口而非待收敛的旧概念，**决定不迁移**（台账状态按规范维持“未开始”并附注原因），不再是前置条件里的“待做”项；前置条件整体仍未满足（A1 待所有者、`objective_executor.py`/`orchestrator/*`/`goal_backlog.py` 未变）。详见 `14-phase10-sa-item-plan.md` 第十一节。
>
> **更新（Sprint 10-2 复核，2026-09-28）**：S-A（A2–A5、A4）与 S-B0、B3 均已完成（见 `13-…`/`14-…`）。据此实际跑了一次
> `dep_graph.py`，核实 Sprint 10-2 的止损条件**目前仍然成立**，尚不能开始目录移动：
>
> - `orchestrator/`：深度 inbound = **37**（远超阈值 10），涵盖 `agent/core.py`、`agent/lifecycle.py`、`cli/app.py`、
>   `api/routes.py` 等——这是被大量新旧代码共同直接使用的通用基础设施（人设/并发/计划会话），不是“已被 Adapter 完全代理”的
>   单一旧实现，本身就不适合整体移入 `legacy/`。
> - `evolution/objective_executor.py`：深度 inbound = 6（数量上未过阈值），但其中 `api/server.py`、`api/routes.py`、
>   `evolution/goal_cron_bridge.py` **绕过** `core/objective_adapter.py` 直接 import `ObjectiveExecutor`/
>   `MAX_CONCURRENT_OBJECTIVES` 等符号——这正是止损条件描述的“被多处新代码绕过 Adapter 直接调用”，Adapter（A5 的
>   `project_objective_executions()`）覆盖的只是只读投影，写路径与生命周期管理仍被直接调用。
> - `goal_mode/objective_executor.py`、`goal_cycle.objective_executor` 两个路径均**不存在**（`dep_graph.py` 返回 0/0）——
>   11 号文档与部分早期文档里对该模块路径的表述需要以 `evolution/objective_executor.py` 为准，本次未去改历史文档正文，只在此注明。
> - `perception/goal_backlog.py` 立项状态未变（仍暂缓），`orchestrator/` 的量级也说明它并非当初设想的“待降级单点”，需要先拆分
>   评估，而非直接判定去留。
>
> **结论：Sprint 10-2 继续不开始**（沿用既有止损条件，本次只是用最新代码重新核实，结论未变）。Phase 10 四条完成标志继续
> 全部不勾选。D1（状态管理类入口是否统一）与 D5（何时启动 Sprint 10-2）仍待所有者决定；即便决定启动，也需要先针对
> `orchestrator/` 单独评估拆分方案，而不能整包移动。Sprint 10-3 的验收报告因此也**不产出**——它的前提是 10-2 的目录移动
> 结果，现在没有可汇总的收敛结果，写一份“形式上完整但结论空洞”的报告不符合本文档一贯的如实原则。

> **更新（`orchestrator/` 拆分评估，2026-09-29）**：上文“需要先针对 `orchestrator/` 单独评估拆分方案”这一前提已由
> `15-phase10-orchestrator-split-assessment.md` 完成（仅评估，未改代码）。要点：`orchestrator/` 的 10 个文件其实是五类基本互不相关的
> 东西（子 Agent 执行 / 并发限流 / 执行计划 / 角色人设配置 / 终端 UI）；深度 inbound=37 里约三分之二（25 个）来自**配置加载类**，真正与子 Agent
> 调度相关的调用方只有 6 个；**Sprint 10-2 的止损条件对整包继续成立**（子 Agent 执行类没有任何 Adapter，且有 3 处生产代码直接使用），
> 结论是“分类归位而非整体降级”。任务表与验收标准未修订。

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

**状态（2026-09-28）：所有者已决定不追求这三条的完全达成**（D1 维持现状、D5 搁置
Sprint 10-2），三条均保持未勾选，且不再是"待办"，而是主动放弃的范围。

- [ ] 开发者/用户首先看到的是 `AgentRuntime`，而不是一堆 Manager/
      Scheduler/Advisor（D1：状态管理类入口维持现状，不追求）
- [ ] 旧模块全部通过 Adapter 接入，且已移动到清晰标注的 `legacy/`
      命名空间下（D5：`orchestrator/` 等目录级收敛已搁置）
- [ ] `MIGRATION_STATUS.md` 显示所有关键路径均已完成迁移标注（同上，
      台账将保持记录当前真实状态，不追求"全部完成迁移"字样）
- [ ] 全量测试（398+ 个测试文件）通过率与重构前持平或更好

## Sprint 10-1 执行记录（部分完成，验收未达成）

产出：`docs/architecture_v2/phase10-entrypoint-inventory.md`（人工分析 + 脚本生成
的逐条附录）、`scripts/entrypoint_inventory.py`（可重复运行的静态盘点脚本，与
Sprint 0 的 `dep_graph.py` 同一风格）、`tests/test_phase10_sprint10_1_entrypoint_
inventory.py`（13 用例）。

| 任务 | 状态 | 说明 |
|---|---|---|
| 梳理对外入口 | **完成** | CLI 斜杠命令 59 条、HTTP 路由 308 条（与独立逐行正则计数一致）、weixin_bot 与 5 个 apps；每条标注是否经 `AgentRuntime`、是否直接触达旧类 |
| 统一入口 | **未执行（止损触发，待决策）** | 见下方“变更记录” |
| 更新映射表（核对 §43 六条） | **核对完成，结论：六条均未“完全落地”** | 见盘点文档第三节；台账已追加快照小节。**不是**“最终版”——现状不允许写成最终版 |

**验收标准未达成**：CLI 菜单与 `--help` 里旧类名命中为 0（达标）；HTTP OpenAPI
文档（`/docs` 显式开启）有 32 处旧类名命中，分布在 26 条路由说明里
（未达标）。本 Sprint 刻意**没有**改写这些文档：在底层未迁移前改写措辞会让文档
声称尚不存在的事实。

**过程中发现并修复的脚本缺陷（首次在真实仓库运行时暴露，均已加回归测试）**：
1. 未跟随包的再导出（`cli/commands/__init__.py` 只做 `from .x import handle_x`），
   导致 `/goal` 被误判为“未走 `AgentRuntime`”，而 Phase 8 明确它走了；
2. `_COMMANDS` 是带类型标注的赋值（`ast.AnnAssign`），首版只认 `Assign`，菜单字符串数为 0；
3. 独立正则核对路由数时发现差 1 条——是**测试**的正则太严（漏了
   `@router.get("")` 这种空路径路由），脚本本身是对的。

**已知局限**：静态、浅层（3 跳）、只看名字与函数调用，是**下限估计**：
“命中旧类”确凿，“未命中”不代表已收敛（例如 `ObjectiveExecutor` 经实例属性访问，
静态扫描看不到）。报告里已逐处说明，不要把 `kind=none` 当作“已收敛”的证据。

可进入的下一步：**不是 Sprint 10-2**——需先由项目所有者就“变更记录”里的 D1–D5
给出决定。

## 变更记录

### 变更记录 2026-09-28
- 触发条件：本 Phase 文档 Sprint 10-2 的止损条件（“Adapter 覆盖不完整时不要强行
  移动目录”）已实际成立；同时 Sprint 10-1 “统一入口”任务在盘点后被证明与既有的
  Phase 8 Sprint 8-5 / Phase 5 Sprint 5.0.5 结论冲突。
- 原计划：Sprint 10-1 第二项——“未收敛的接口，改为统一调用 `AgentRuntime`，旧模块
  降级为其内部实现细节”；验收——对外文档里不再出现旧类名。
- 实际情况：59 条 CLI 命令中仅 1 条（`/goal` 新目标路径）经过
  `AgentRuntime`，308 条 HTTP 路由中 0 条；触达旧类的 HTTP 路由几乎
  全是对 `GoalBacklog`/growth 的状态增删改与只读视图，**不是“执行循环”**，
  “统一调用 `AgentRuntime`”对它们是范畴错误；真正的执行类入口（聊天、Objective、
  goal_cycle）走“共享 Agent + InputQueue”模型，已被 Sprint 8-5 评估为高风险。
  另：`DecisionEngine` 无任何生产调用方，旧 Advisor 未接入；`core/capability.py`
  是无引用的空占位；用户可见的 `capability` 一词已被“人设能力学习”功能占用。
- 调整后方案（**提议，待项目所有者确认；未修改任务表**）：
  D1 “统一入口”只覆盖 Phase 8 已评估可接的执行类入口（已完成），状态管理类推迟到
  `goal_backlog` 立项之后，只读视图不动；
  D2 明确术语验收口径——仅旧类名（可在不改行为的前提下逐条改写 26 条 docstring）
  还是包含概念词与命令名（破坏性改名）；
  D3 决定 `capability` 重名由谁改名；
  D4 补齐或明确豁免 Phase 10 的前置条件；
  D5 在 D1/D4 有结论前不启动 Sprint 10-2。
- 影响范围：影响 Sprint 10-2（目录移动）与 10-3（验收报告）的前置条件；Phase 10
  四条“完成标志”全部保持未勾选。

### 变更记录 2026-09-28（所有者决定 D2/D3/D4）
- 触发条件：项目所有者对上一条变更记录里的 D2/D3/D4 给出决定。
- 决定：**D2**——术语验收包含概念词与命令名（破坏性改名）；**D3**——由 Claude 判断，结论为人设能力学习一侧改名、
  架构 Capability 保留；**D4**——补齐前置条件，不豁免。D1/D5 待 D2–D4 落地方案确认后随之确定。
- 影响：Sprint 10-1 “统一入口/术语验收”与 Sprint 10-2 的前置条件被重新定义为分阶段的
  S-A（补齐）→ S-B（改名）→ S-C（10-2/10-3）。**本次只产出方案，未修改任务表、未改任何代码**；
  任务表与验收标准的修订待所有者确认方案（含第七节 Q1–Q4）后再走本规范第四节流程落地。
- 详见 `13-phase10-post-decision-execution-plan.md`。

### 变更记录 2026-09-28（方案确认 + S-B0 完成）
- 所有者确认 Q1–Q4：Objective 可改名，`/workflow`、`/cron` 及其他命令**不改**（“不机械改名，重点是理顺逻辑”）；A5 只做只读投影 + 事件；
  旧名永久隐藏别名；先做 S-B0。
- 因此**取消**上一条记录里的 B1/B2/B4 改名批次与“用户可见面旧概念词为 0”的验收；D2 的实际执行范围收窄为
  “人设学习改名（D3）+ Objective 改称（待 A5 后）”。
- S-B0（人设学习 `/capability` → `/persona-learning`，HTTP `/v1/persona_learning/*`，旧名隐藏别名）已完成，详见
  `13-phase10-post-decision-execution-plan.md` 第八、九节。Sprint 10-1/10-2/10-3 的任务表与验收标准**仍未修订**，
  待 S-A 结束后按本规范第四节流程统一修订。

### 变更记录 2026-09-28（S-A 分项方案）
- 产出 `14-phase10-sa-item-plan.md`（仅方案）。核实发现 `13` 号文档关于“`runtime.py` 已有 `enable_decision_stage` 开关”的表述有误，已在 `13` 内更正，并在 `14` 第〇节说明。任务表/验收标准仍未修订。

### 变更记录 2026-09-28（S-A：A2 完成）
- 所有者对 Q-A5/Q-A3/Q-A4/顺序均“接受”（Q-A2 未答复，按推荐保持 World 空占位）。A2（`RuntimeState`/`CapabilityState` 填真实字段）已完成，详见 `14-phase10-sa-item-plan.md` 第八节。任务表/验收标准仍未修订，待 S-A 结束后统一修订。

### 变更记录 2026-09-29（`orchestrator/` 拆分评估）
- 触发条件：所有者选择推进 README 中“未来重启目录收敛的前提”——对 `orchestrator/` 单独评估拆分方案。
- 产出：`15-phase10-orchestrator-split-assessment.md`（仅评估，未改任何生产代码/测试/配置）。
- 结论：**Sprint 10-2 对 `orchestrator/` 整包的止损条件继续成立**；建议的可执行步骤只有两步，且均为提议、待所有者确认——Step 1 把
  `orchestrator/__init__.py` 改为惰性再导出（不移动文件、不改调用方；已在临时副本验证旧写法全部可用、LLM 层导入不再连带加载
  `agent`/`tools`/`skills`、测试失败集合不变）；Step 2（可选）把 `concurrency.py` 归位到轻量中性位置。
- 影响范围：**未修改**本文档的任务表、验收标准与四条完成标志（按 `12-execution-and-doc-sync-norms.md` 第四节，修订需所有者确认）；
  D1/D5 的决定不受影响，本评估不启动 Sprint 10-2。
