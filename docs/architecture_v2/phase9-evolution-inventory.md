# Phase 9 现状盘点：evolution/ 67 个模块分类

> 对应 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
> "现状盘点"任务。目的：在动手做 Sprint 9-1 之前，先明确哪些模块是
> "安全设施"（绝对不能动），哪些是"决策逻辑"（本 Phase 要重新接入
> 统一 Experience 的对象），哪些跟本 Phase 无关（属于其它 Phase 或
> 独立子系统，不在本次改动范围内）。

## 一、分类方法

逐一读取 `src/mini_agent/evolution/` 下全部 67 个文件的模块级 docstring
（不逐行读实现），按职责归入以下三类之一：

- **安全设施**：原方案 §28/§43 明确点名、构成"沙盒/验证/回滚"闭环
  本身的模块。**本 Phase 及后续 Sprint 严禁修改其内部实现**，只允许
  新增调用方对接。
- **决策逻辑**：识别问题模式 / 生成假设与提案 / 评估建议效果的模块，
  是 Sprint 9-1～9-3 要重新接到统一 Experience 上的对象。
- **不相关（本 Phase 不处理）**：属于调度（Phase 8 Runtime 范围）、
  Memory/Perception（Phase 1.5 范围）、Wiki/报表展示、日常运维等
  独立子系统，与"Experience → Pattern → Proposal → Sandbox → Deploy"
  闭环无直接关系，本 Phase 不修改、不接入。

## 二、安全设施（绝对不能动，4 个）

| 模块 | 角色 |
|---|---|
| `state_repo.py` | `StateRepo`：自我修改的唯一写入入口（git 仓库化版本管理） |
| `workspace.py` | `EvolutionWorkspace`：进程级隔离（git worktree 沙盒） |
| `validators.py` | 验证流水线（Tier 分级校验，T0~T3） |
| `eval_runner.py` | eval 反馈环（回归集对比评估） |

这 4 个模块对应原方案 §28/§43 点名的安全网核心，Sprint 9-2/9-3 只新增
"用新的 `EvolutionProposal` 结构调用它们现有公开接口"这一层，**不修改
以上文件内部任何一行**。是否被误改可用 `git diff` 在每个 Sprint 结束
时核对（对应完成标志第 4 条）。

## 三、决策逻辑（本 Phase 迁移对象，15 个）

| 模块 | 对应闭环环节 | 说明 |
|---|---|---|
| `failure_pattern_store.py` | Pattern | 统一失败模式库，Sprint 9-1 `patterns.py` 的直接参考实现/对接对象 |
| `decision_recall.py` | Pattern 检索 | 提案前召回历史决策，检索方式可参考用于 Experience 检索 |
| `decision_profile_builder.py` | Pattern | 对多条决策做周期性归纳 |
| `evidence_pattern.py` | Pattern | 通用"证据 → LLM 归纳 → 矛盾不覆盖只降权"合并算法，多个 Pattern 类模块的公共底座 |
| `self_model_drift.py` | Problem | 自我模型漂移检测，产出的"漂移"即一种 Problem |
| `relevance_threshold_calibration.py` | Hypothesis | 阈值自校准，是"提出改进假设"的一个具体实例 |
| `proposal_risk.py` | Evaluation | 进化提案风险分级，Sprint 9-2 `EvolutionProposal` 评估环节的直接对接对象 |
| `capability_learning.py` | Hypothesis/Experiment | 能力缺口扫描 + 学习闭环 |
| `persona_candidates.py` | Hypothesis | 候选人设/能力自动检测，`capability_learning.py` 的平行子系统 |
| `soft_goal_deriver.py` | Problem→Hypothesis | 从能力置信度低/待办信号 derive 软目标 |
| `next_action_advisor.py` | Hypothesis 排序 | 对已存在候选做优先级排序 |
| `growth_advisor.py` | Hypothesis/Proposal | 成长方向规划与持续收集 |
| `outcome_tracker.py` | Observe/Promote | 用户真实反馈闭环指标 |
| `objective_outcome_tracker.py` | Observe | 效果回填到目标推导优先级 |
| `suggestion_feedback_ledger.py` / `suggestion_outcome_review.py` | Observe/Promote | 建议采纳率账本与回看，Promote/Rollback 决策的输入 |

以上模块是 Sprint 9-1～9-3 优先考虑"做 Adapter 对接、不重写"的对象；
具体先接哪几个、后接哪几个，留到 Sprint 9-1 任务表内按"哪个已有真实
数据可跑通闭环"决定，本文档只做定性分类，不预先排定顺序（避免重复
Phase 5/8 "计划写太细、一跑就变"的教训）。

## 四、不相关（本 Phase 不处理，48 个）

按子领域分组，仅说明"为什么不属于本 Phase"，不代表这些模块不重要：

### 1. 调度/自主循环（属于 Phase 8 Runtime 范围，10 个）

`autonomous_loop.py`、`objective_executor.py`、`objective_agent_bridge.py`、
`cron_scheduler.py`、`cron_job_runner.py`、`cron_job_executor.py`、
`cron_job_workspace.py`、`cron_agent_bridge.py`、`cron_context.py`、
`scheduler_heartbeat.py`、`resource_arbiter.py`、`unified_task_scheduler.py`、
`guardian.py`、`cycle_patrol.py`、`goal_cron_bridge.py`、`goal_node_retry.py`

> 说明：数量超过小标题里的"10 个"，因为调度类实际有 16 个；这些都已在
> Phase 8 Sprint 8-3/8-4/8-5 逐一评估过（`cron_job_runner` 已接入，
> `goal_cycle`/`AutonomousLoop` 判定风险高、移出 Phase 8），Phase 9 不
> 重复处理，也不因为"看起来像决策触发"就纳入本 Phase。

### 2. Memory/Perception 治理（属于 Phase 1.5 范围，4 个）

`memory_aging.py`、`memory_backfill.py`、`memory_consolidation.py`、
`memory_types.py`

> 说明：`memory_types.py` 是 Sprint 1.5 已建立的 facade，另外三个是
> Memory 衰减/回填/巩固逻辑，与 `perception/memory_store.py` 迁移链
> 是同一条线，不属于 Self Evolution 闭环。

### 3. Wiki/报表/知识链接展示类（8 个）

`goal_wiki.py`、`daily_digest.py`、`monthly_trend_retrospective.py`、
`lineage_view.py`、`wiki_utility_audit.py`、`external_trend_capability_link.py`、
`focus_research_trigger.py`、`research_service.py`

> 说明：这些是把已有数据渲染/聚合成人可读报告或触发调研，不产出
> `Problem`/`Proposal` 这类会进入 evolution 闭环的结构。

### 4. 日常运维/工具类（不构成决策闭环，14 个）

`session_cleanup.py`、`protected_files_backup.py`、`output_path_policy.py`、
`output_projects_root.py`、`output_workspace.py`、`recovery_event_log.py`、
`circuit_breaker_core.py`、`candidate_queue_triage.py`、
`improvement_backlog_merge.py`、`lesson_to_reminder.py`、
`self_maintenance.py`、`step_runner.py`、`consolidation.py`、
`objective_trend.py`

### 5. 自我叙事/身份类（与 Experience 闭环平行，独立主题，5 个）

`self_narrative.py`、`self_model_snapshot.py`、`sub_agent_experience.py`、
`agent_value_profile_builder.py`、`user_signal_profile_builder.py`

### 6. 其它（7 个）

`__init__.py`（公共 API 出口，跟随其它文件改动自然更新，不单独分类）

## 五、结论

- 安全设施 4 个：**冻结**，Sprint 9-2/9-3 只新增调用，不改内部。
- 决策逻辑 15 个：本 Phase 的迁移/对接对象，具体接入顺序留给
  Sprint 9-1 任务表执行时决定。
- 不相关 48 个：本 Phase 不修改、不接入，各自归属的 Phase/子系统
  已有或将有独立的评估流程。

本次盘点未修改任何生产代码，仅新增本文档，无需跑回归测试。
