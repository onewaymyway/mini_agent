# Autonomous Daemon — 实现说明

> 此文档记录 daemon 自主运行能力的设计决策和实现状态。
> 功能使用方法见 [Stage 9 自主运行时指南](self-evolution-stage9-guide.md)。

---

## 实现状态

| 模块 | 状态 | 文件 |
|------|------|------|
| CronScheduler | ✅ 已实现 | `evolution/cron_scheduler.py` |
| ObjectiveExecutor | ✅ 已实现 | `evolution/objective_executor.py` |
| SoftGoalDeriver | ✅ 已实现 | `evolution/soft_goal_deriver.py` |
| AutonomousLoop 三档位 | ✅ 已实现 | `evolution/autonomous_loop.py` |
| `/v1/autonomous/status` | ✅ 已实现 | `api/routes.py` |
| `/v1/goals` CRUD | ✅ 已实现 | `api/routes.py` |
| `/v1/cron/jobs` CRUD | ✅ 已实现 | `api/routes.py` |
| `/cron` CLI 命令 | ✅ 已实现 | `cli/commands/cron.py` |
| SSE `objective_progress` | ✅ 已实现 | `api/bridge.py` + `api/models.py` |
| ExplorationSandbox | ✅ 已接入 | `perception/exploration_sandbox.py` + `autonomous_loop.py` |

---

## 关键设计决策

### 巩固循环 → CronScheduler

原 `_tick_passive()` 直接调用 `should_run_consolidation() / run_consolidation()`。迁移后 巩固循环 成为 cron job `sys:consolidation`（`interval:21600`）。好处：

- 用户可通过 `/cron disable sys:consolidation` 临时关闭，不需要改代码
- 用户可通过 `/cron run sys:consolidation` 手动触发，不需要重启 daemon
- 同一套机制同时服务系统维护任务和用户自定义任务
- 巩固循环 逻辑本身不变，只是触发方式变了

### ObjectiveExecutor 的 `_turn_to_exec` 索引

`turn_to_exec: dict[turn_id → (execution_id, step_idx)]` 是 O(1) 反查表。
AgentRunner 完成一个 turn 后，需要立即知道「这个 turn 是哪个 Objective 的哪步」才能推进。
重启恢复时，`status == "running"` 的步骤的 `turn_id` 会重新加入索引。

### on_turn_done 只在 initiator == "autonomous"/"cron" 时触发

用户消息（`initiator="user"`）不走 `ObjectiveExecutor`，否则会把用户发起的 turn 误判为自主步骤，触发错误的状态推进。

### SoftGoalDeriver 三路信号权重设计

| 来源 | priority | 理由 |
|------|---------|------|
| lesson（高频触发） | 30 | 有实证失败，最可信 |
| capability_map（低置信度） | 25 | 有量化数据，但样本可能太少 |
| WorkThread（积压建议） | 20 | agent 自己的猜测，最不确定 |

urgency 用于同 priority 档位内的排序（lesson 的 urgency 正比于触发次数）。

### record_rejected 自动触发时机

在 `PATCH /v1/goals/{id}` 中，当 `status` 被改为 `"abandoned"` 且 `source == "agent_derived"` 时自动调用 `SoftGoalDeriver.record_rejected()`。这样无论用户通过 CLI（`/goals abandon`）还是 API 操作，rejected 记录都能正确写入，30 天内不再 derive 相同主题。

### CronScheduler 降级兼容

若 `CronScheduler` 注入失败（如 paths 不可用），`_tick_passive()` 回退到直接调用 `should_run_consolidation()`，保持向后兼容。`ObjectiveExecutor` 未注入时，`_tick_maintenance()` 回退到旧的单次 Task 提交逻辑。

---

## 数据文件清单

| 文件 | 位置 | 内容 |
|------|------|------|
| `goals.json` | `.agent/` | GoalBacklog（Goal + Objective 节点） |
| `cron_jobs.json` | `.agent/` | CronJob 列表（含内置系统 job 的 `last_run_at` 和 `next_run_at`） |
| `objective_executions.json` | `.agent/` | 活跃 + 最近完成的 ObjectiveExecution 状态 |
| `activity_digest.jsonl` | `.agent/` | 自主行为日志（cron_run、objective_started、soft_goal_created 等） |
| `consolidation_rhythm.json` | `.agent/` | 巩固循环 节奏治理 + `last_soft_goal_derive_at` |
| `soft_goal_rejected.json` | `.agent/` | 用户 reject 的软目标 dedupe_key + 时间戳（30 天有效） |

---

## Phase 3 完成内容

### ExplorationSandbox 接入 `_tick_autonomous()`

`autonomous` 档位下，`SoftGoalDeriver.derive_candidates()` 将候选分为两类：

- **capability 类**（`source_tag="capability"`）：先经 `ExplorationSandbox.create()` 在隔离 git worktree 内做轻量验证实验，成功才写 Goal + 尝试 `skill_propose` 生成技能提案分支；失败静默丢弃（不骚扰用户）
- **其他类**（`workthread`/`lesson`）：直接调 `commit_goals()` 写 GoalBacklog

每次 tick 最多处理 1 个 capability 候选（通过 `ExplorationBudgetExhausted` 保护），与 `ResourceArbiter.can_run_exploration()` 预算门控配合。

`skill_propose` 触发条件：探索结果文本中包含 "skill"/"技能"/"封装"/"通用"/"可复用"/"pattern" 关键词。

### `/digest` 分组显示重写（`resource_arbiter.py`）

`build_digest_summary()` 从 3 个分组扩展为 6 个：

| 分组 | 记录类型 | 展示内容 |
|------|---------|---------|
| Objective 进展 | `objective_*` | 按 objective_id 折叠，显示完成/失败/运行状态 |
| Cron 执行记录 | `cron_run` | job 名 + 摘要 + 相对时间 |
| 探索实验结果 | `exploration_result` | 成功/失败 + finding 摘要 + skill_propose 分支 |
| Agent 建议目标 | `soft_goal_created` | 内嵌 `/goals accept <id>` / `/goals reject <id>` 快捷指令 |
| 进化提案 | `evolve_proposal` | 数量 + `/evolve review` 提示 |
| 其他活动 | 其余类型 | 原始 summary + 相对时间 |

### `/goals accept` 和 `/goals reject` 命令（`cli/commands/goals.py`）

| 命令 | 行为 |
|------|------|
| `/goals accept <id>` | 激活 Goal，若是 `agent_derived` 则 priority 提升到 50 |
| `/goals reject <id>` | 标记 abandoned，若是 `agent_derived` 则调用 `SoftGoalDeriver.record_rejected()`（30 天去重） |
| `/goals abandon <id>` | 同 reject（通用 abandon，调用同一底层函数） |

`SoftGoalDeriver` 新增 `derive_candidates()` + `commit_goals()` 两个方法，`derive()` 保持向后兼容。

---

## 完整数据流（Phase 1-3）

```
daemon tick（autonomous 档位）
  │
  ├─ CronScheduler.tick()
  │    └─ sys:consolidation 到期 → enqueue("执行 巩固循环 扫描", initiator="cron")
  │         └─ AgentRunner 执行 → on_turn_done() （不走 ObjectiveExecutor）
  │
  ├─ ObjectiveExecutor.resume()
  │    └─ 活跃 Objective step[2] → enqueue("步骤 3/4: ...", initiator="autonomous")
  │         └─ AgentRunner 执行 → on_turn_done() → step[3] 提交
  │              └─ 所有 step 完成 → objective.status = "completed"
  │                   └─ activity_digest: {type: "objective_completed", ...}
  │                        └─ SSE: {type: "objective_progress", status: "completed"}
  │
  └─ SoftGoalDeriver.derive_candidates()
       ├─ capability 候选（confidence=0.28）
       │    └─ ExplorationSandbox.create()
       │         └─ enqueue("[探索实验] ...", initiator="autonomous")
       │              └─ 成功 → commit_goals() + skill_propose()
       │                   └─ activity_digest: {type: "soft_goal_created", ...}
       │                   └─ activity_digest: {type: "exploration_result", proposed_skill_id}
       │
       └─ lesson 候选（触发 7 次）
            └─ commit_goals() 直接写 GoalBacklog
                 └─ activity_digest: {type: "soft_goal_created", ...}

用户 /digest:
  【Objective 进展】  ✅ 完善测试覆盖（4 步完成，用时 23m）[2h前]
  【Cron 执行记录】   ✓ 巩固循环 扫描 — 剪枝 2 技能，+3 能力条目 [6h前]
  【探索实验结果】    ✅ 改善 _call_llm 可靠性 → 已提案技能：improve_call_llm [1h前]
  【💡 Agent 建议】  💡 "系统性解决：连续工具调用失败" — 来自 lesson（触发 7 次）[30m前]
                        /goals accept goal_abc123  |  /goals reject goal_abc123
```
