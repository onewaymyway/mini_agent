# Goal 与 Cron 绑定改进方案 · 实施记录

对应计划文档：`next_doc/goal_cron_binding_plan.md`（Track A–E 全部完成）

## 新增文件

| 文件 | 作用 |
|---|---|
| `src/mini_agent/evolution/goal_cron_bridge.py` | Goal ⇄ Cron 绑定桥接层：`register_goal_cycle_handler()`（把触发逻辑接进 CronScheduler）、`_fire_goal_cycle()`（Goal 状态门禁 + 幂等检查 + 派生并启动一轮子 Objective）、`make_goal_recurring()`/`stop_goal_recurrence()`（用户绑定/解绑入口）、`reap_finished_cycles()`（终态子节点计入 cycle_count/progress_notes） |
| `tests/test_goal_cron_bridge.py` | 11 项单测，覆盖绑定/解绑、Goal 非 active 时跳过、passive 档位时跳过、幂等（上一轮未完成不叠加）、启动失败时子节点标 failed、reap 去重计数 |

## 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/perception/goal_backlog.py` | `GoalNode` 新增 `recurring`/`recurrence_cron_job_id`/`cycle_count`/`reaped_cycle_child_ids` 字段（序列化/反序列化同步更新）；`add_objective()` 新增 `description` 参数；新增 `set_recurrence()`、`record_cycle_completed()` 两个方法 |
| `src/mini_agent/evolution/cron_scheduler.py` | `CronJob` 新增 `goal_id`/`run_mode` 字段（默认值保证向后兼容，`run_mode="message"` 时行为与改动前完全一致）；`add_job()` 新增同名参数；`CronScheduler` 新增 `set_goal_cycle_handler()`；`_fire()` 新增 `run_mode=="goal_cycle"` 分支，独立于既有 local_handler/job_runner/submit_fn 优先级链 |
| `src/mini_agent/evolution/autonomous_loop.py` | `_tick_maintenance()` 在 `_ensure_goal_objectives()` 之后调用 `goal_cron_bridge.reap_finished_cycles()`。**未**放进 `_tick_passive()`——保留该方法"不引用 GoalBacklog 任何方法"的既有边界（见下方"档位边界"一节） |
| `src/mini_agent/api/server.py` | `_build_autonomous_loop()` 在 `objective_executor.load()` 之后调用 `register_goal_cycle_handler(cron_scheduler, goal_backlog, objective_executor)`，接线失败静默降级 |
| `src/mini_agent/cli/commands/cron.py` | 新增 `/cron add-goal-cycle <goal_id> <schedule> [task_template]` 子命令 + `_cmd_add_goal_cycle()` 实现；模块文档字符串同步更新 |
| `src/mini_agent/cli/commands/goals.py` | 新增 `/agent goals recur <id> <schedule> [task]` / `/agent goals unrecur <id>` 两个子命令 + `_cmd_recur()`/`_cmd_unrecur()` 实现；模块文档字符串、`Unknown subcommand` 提示同步更新 |
| `src/mini_agent/cli/repl.py` | `/cron` 命令分发处的轻量 `_Ctx` 补上 `agent` 属性，供 `_cmd_add_goal_cycle()` 反查 `AgentPaths` |

## 关键设计决策

1. **不改 ObjectiveExecutor 的状态同步语义**：`_sync_goal_status()` 写的是 Objective（子节点）自己的 status，本来就不会向上传播到父 Goal。周期性场景直接复用这一点——父 Goal 靠 `recurring=True` 保持长期 active，每轮真正的执行状态记在子节点上，不需要改动 Track B 已有的双向同步逻辑。

2. **档位边界（重要）**：`AutonomousLoop._tick_passive()` 有明确的既有约定——"方法体内不引用 GoalBacklog 任何方法"，但 `CronScheduler.tick()` 恰好是在 passive 档位下被调用的（cron job 本身不分档位）。如果 goal_cycle 触发逻辑不做任何限制，会在 passive 档位下悄悄读写 goals.json，破坏这条边界。因此 `_fire_goal_cycle()` 显式检查 `autonomy_level`，为 `"passive"` 时直接跳过（不触碰 `goal_backlog`），只有 maintenance/autonomous 档位才会真正生效——见 `tests/test_goal_cron_bridge.py::TestFireGoalCycle::test_skips_when_passive_tier`。同理，`reap_finished_cycles()` 挂在 `_tick_maintenance()` 而不是 `_tick_passive()`。

3. **幂等而非并发保护**：`_fire_goal_cycle()` 的"上一轮是否还在跑"检查是尽力而为（读 GoalNode 子节点 + `objective_executor.is_running()`），不是加锁互斥——这与 CronScheduler 现有的"job_runner 忙时返回 False，下次 tick 再试"是同一种设计哲学，不引入新的并发原语。

4. **绑定关系是一对一**：`make_goal_recurring()` 检测到 Goal 已绑定过 job 时会复用旧 job（更新 schedule/task_template），不会为同一个 Goal 创建第二个 `goal_cycle` job——对应计划文档"非目标"里明确排除的"多 job 绑同一 Goal"。

5. **Goal 暂停/终止自动联动 cron**：`_fire_goal_cycle()` 在 Goal `status != "active"` 时直接跳过，不需要用户额外记得去 `/cron disable`——这是本方案要解决的原始问题之一（P3：cron 变僵尸任务）。反过来，`stop_goal_recurrence()` 会 disable 对应 job 但不删除，方便随时用 `make_goal_recurring()` 重新启用。

## 测试结果

```
tests/test_goal_cron_bridge.py ......... 11 passed
tests/test_cron_schedule_validation.py .......... 已有用例全绿（未受影响）
tests/test_cron_scheduler_local_handler.py ...... 已有用例全绿（未受影响）
tests/test_goal_backlog.py .............. 已有用例全绿（未受影响）
tests/test_autonomous_loop_decommission_hook.py .. 全绿
tests/test_objective_executor_kanban_tracks*.py ... 全绿（4 个文件）
tests/test_objective_executor_adaptive_concurrency.py .. 全绿
```
共验证 90+ 项既有 + 新增测试，全部通过。`tests/test_report_tiers.py` 有 2 项与本次改动无关的既有失败（`external_input/alerts.jsonl` 路径缺失，属于该测试自身的 fixture 问题，未触碰 `report_tiers.py`，不在本次改动范围内）。

## 用户文档更新

| 文件 | 改动 |
|---|---|
| `docs/goal-cron-binding-guide.md`（新增） | 面向用户的完整使用指南：绑定/解绑操作、三条触发规则、数据结构变化、已知限制 |
| `docs/self-evolution-stage9-guide.md` | §3.1 补充 `GoalNode` 新字段；§3.2 补充 `recur`/`unrecur` 命令；§5.4/§5.5 补充 `goal_cycle` 说明与命令列表 |
| `docs/commands-and-tools-reference.md` | Goal Backlog 表格补充 `recur`/`unrecur`；定时任务表格补充 `add-goal-cycle` |
| `docs/cron-jobs-reference.md` | §1 补充 `run_mode` 维度说明，与固定内置/按需补注册两种既有分类做区分 |

## 使用示例

对话中提到的场景——"持续关注最新的 Agent 领域和 AI 领域的最新技术，构建相关的技术 wiki，放在 `research/agent_and_ai` 目录下"——现在可以这样落地：

```
/agent goals add "持续关注 Agent 和 AI 领域最新技术，维护 research/agent_and_ai 下的技术 wiki"
/agent goals recur <goal_id> interval:86400 "搜索最新的 Agent/AI 技术进展，将结构化知识更新到 research/agent_and_ai/ 下的 wiki，接续上一轮 progress_notes 里记录的进度"
```

daemon 处于 maintenance/autonomous 档位时，每天会自动为这个 Goal 派生并启动一轮新的子 Objective；Goal 卡片上可以看到 `cycle_count` 逐轮递增、`progress_notes` 逐轮追加一行摘要；用户随时可以 `/agent goals pause <goal_id>` 暂停（cron 自动跟着停止触发）或 `/agent goals unrecur <goal_id>` 彻底停止周期性。

## 未完成 / 已知限制

- ~~看板（Streamlit）暂未展示 `recurring`/`cycle_count`/绑定的 cron job~~ ——已在
  `next_doc/goal_cron_visibility_and_intervention_improvement_plan.md`（Track A/B/C/D）
  中补上：看板可见性、绑定/解绑/跳过一轮的 UI 入口、失败通知、长期归档，详见该文档与
  `docs/goal-cron-binding-guide.md`。
- `reap_finished_cycles()` 是"轮询扫描"而不是事件订阅，最坏情况下有一个 tick 间隔（约 60s）的计数延迟，可接受。
- 一对一绑定假设未来若需要"多 Goal 共享一个 cron job"，需要重新设计（当前明确排除在本轮范围外）。
