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
| ExplorationSandbox | 🔲 接口预留 | `perception/exploration_sandbox.py` |

---

## 关键设计决策

### Phase G → CronScheduler

原 `_tick_passive()` 直接调用 `should_run_phase_g() / run_phase_g()`。迁移后 Phase G 成为 cron job `sys:phase_g`（`interval:21600`）。好处：

- 用户可通过 `/cron disable sys:phase_g` 临时关闭，不需要改代码
- 用户可通过 `/cron run sys:phase_g` 手动触发，不需要重启 daemon
- 同一套机制同时服务系统维护任务和用户自定义任务
- Phase G 逻辑本身不变，只是触发方式变了

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

若 `CronScheduler` 注入失败（如 paths 不可用），`_tick_passive()` 回退到直接调用 `should_run_phase_g()`，保持向后兼容。`ObjectiveExecutor` 未注入时，`_tick_maintenance()` 回退到旧的单次 Task 提交逻辑。

---

## 数据文件清单

| 文件 | 位置 | 内容 |
|------|------|------|
| `goals.json` | `.agent/` | GoalBacklog（Goal + Objective 节点） |
| `cron_jobs.json` | `.agent/` | CronJob 列表（含内置系统 job 的 `last_run_at` 和 `next_run_at`） |
| `objective_executions.json` | `.agent/` | 活跃 + 最近完成的 ObjectiveExecution 状态 |
| `activity_digest.jsonl` | `.agent/` | 自主行为日志（cron_run、objective_started、soft_goal_created 等） |
| `phase_g_rhythm.json` | `.agent/` | Phase G 节奏治理 + `last_soft_goal_derive_at` |
| `soft_goal_rejected.json` | `.agent/` | 用户 reject 的软目标 dedupe_key + 时间戳（30 天有效） |

---

## 待实现（下一阶段）

### ExplorationSandbox 接入

`autonomous` 档位下，SoftGoalDeriver derive 出 Goal 后，可通过 `ExplorationSandbox` 在隔离 worktree 内做轻量验证实验，成功后通过 `skill_propose` 提案。`exploration_sandbox.py` 已有接口骨架，待 Phase 3 接入 SoftGoalDeriver 闭环。

### objective_progress SSE 前端渲染

`objective_progress` 事件已通过 `bridge.emit_objective_progress()` 推送，Web Demo 尚未实现对应的进度条组件。

### `/digest` 分组显示优化

`/digest` 目前输出原始 `activity_digest.jsonl` 记录。待实现按类型分组渲染，并在「新软目标」分组中直接嵌入 `/goals accept <id>` / `/goals reject <id>` 快捷操作提示。
