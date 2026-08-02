# Daemon 自主任务错误状态识别与恢复指南

> 设计与实现记录见 [`next_doc/daemon_autonomous_state_recovery_plan.md`](../next_doc/daemon_autonomous_state_recovery_plan.md)；
> 本文档是面向使用者的说明（是什么、怎么开、怎么用），不重复设计推理过程。

## 1. 解决的问题

daemon 自主任务（`autonomous` Objective 执行 / `cron` 周期任务）偶尔会把
"没解析成功的 `<tool_use>` 原始协议残留文本"当成一次正常的步骤结果，然后
把这段脏内容当作"前序步骤已完成的事实"继续喂给后续步骤——状态会越跑越歪，
且升级前没有自动/半自动的重置手段。同时，`autonomous` 任务此前和真人交互
共用同一个 Agent Session，容易互相污染上下文。

本计划分四个阶段解决这个问题，四项改进相互独立，各自有配置开关，
**默认配置下行为与升级前完全一致**（除阶段一的结果健全性校验默认开启，
因为它只是"拦截明显畸形的结果"，不改变正常场景下的行为）。

| 阶段 | 内容 | 默认状态 |
|---|---|---|
| 阶段一 P0-A | 结果健全性校验 | ✅ 默认开启 |
| 阶段二 P0-B | Step 级重置能力（自动 + 手动） | ✅ 始终可用（手动命令不需要开关） |
| 阶段三 P1 | 自主任务独立上下文 | ⬜ 默认关闭，需手动开启 |
| 阶段四 P2 | 看护模式 GuardianRunner | ⬜ 默认关闭，需手动开启 |

## 2. 阶段一：结果健全性校验（P0-A）

**做什么**：`agent/turn_loop.py::_agentic_loop()` 在返回 `final_text` 前，
用 `perception/format_correction_detector.py::is_valid_final_result()` 判断
这段文本是不是"仍然是畸形协议残留"或"空文本"。命中时不会把脏文本当结果
返回，而是返回一段明确标注"本轮未获得有效回复"的哨兵文本，并在 Agent 实例
上置位 `_last_turn_result_invalid`。

`api/server.py` 把这个标志位透传给 `ObjectiveExecutor.on_turn_done(...,
valid=...)`：`valid=False` 时不会把内容写进 `step.result_summary`，而是
按现有的重试机制重新提交同一步（不推进 `current_step_idx`）。

**配置开关**：`FormatCorrectionConfig.result_sanity_check_enabled`
（默认 `True`）。设为 `False` 完全回退到升级前行为——脏结果会被当作正常
结果继续往下传（不推荐关闭，仅用于怀疑该校验本身误判时的临时回退）。

```json
{
  "format_correction": {
    "result_sanity_check_enabled": false
  }
}
```

## 3. 阶段二：Step 级重置能力（P0-B）

**做什么**：`ObjectiveExecutor.reset_step(exec_id, step_idx, reason="")`
清空目标 step 及其之后所有 step 的 `result_summary`/`artifacts`/
`turn_id`/`retry_count`/`error_msg`/`finished_at`，把 `current_step_idx`
拨回该 step，注入一条"本步骤已被重置，以下为更正后的上下文"的 guidance，
立即重新提交。

**触发来源**：
1. **自动**——阶段一判定结果无效且重试到达上限时，`_handle_invalid_step_result()`
   会走既有的"重试耗尽 → 尝试重新分解 → 仍不行则判 Objective failed"路径
   （不是直接调用 `reset_step`，`reset_step` 主要面向人工介入场景）。
2. **人工**——CLI 命令：

```
/agent goals reset-step <exec_id> <step_idx> [reason]
```

例如发现某个自主 Objective 的第 3 步（0-indexed 为 2）结果明显被污染：

```
/agent goals reset-step abc12345 2 人工发现结果被污染，需要重新执行
```

在 daemon 模式下，非本进程内的 Agent（比如通过 CLI attach 到远程 daemon）
会自动回退为通过 `DaemonClient` 发起
`POST /v1/objectives/{execution_id}/steps/{step_index}/reset` 请求，
本地/远程两种场景命令用法完全一致。

## 4. 阶段三：自主任务独立上下文（P1）

**做什么**：开启后，`autonomous` Objective 的每个 step 不再复用 Self 的
主 Session/Agent（即不再走共享的 `bridge.input_queue`），而是像 cron 任务
（见 [Cron 专属执行机制指南](cron-dedicated-execution-guide.md)）一样，
每次提交都在专属的后台线程里构建一个全新的、独立 Session 的 Agent 实例来
执行，执行完即丢弃：

- 不会和真人交互共用同一段对话历史；
- 不会跨自主任务之间共用历史；
- "上一步做到哪了"完全靠 `ObjectiveExecutor._submit_step()` 已有的
  `[前序步骤结果]`/`[前序步骤产出文件]` 结构化摘要传递，不依赖共享的
  session 历史。

实现见 `evolution/objective_agent_bridge.py`：
- `build_objective_agent()` — 与 `cron_agent_bridge.build_cron_agent()`
  同构：`auto_approve=True`（无人值守必须自动批准工具调用），registry 留空
  回退到全局默认工具集（全量继承主 Agent 的工具）。
- `ObjectiveIsolatedRunner` — 可以直接替换 `ObjectiveExecutor._submit_fn`
  的 drop-in 实现：`submit(message, initiator, meta) -> turn_id` 签名与
  默认的共享提交路径完全一致，内部用线程池并发跑，每个 step 构建 Agent →
  `run_turn()` → 复用阶段一的 `is_valid_final_result()` 做二次确认 →
  通过 `on_done`/`on_failed` 回调交回 `ObjectiveExecutor`。

**配置开关**（`AutonomyConfig`，见 [配置指南](config-guide.md#autonomyconfig好奇心评分--自主探索排序权重)）：

```json
{
  "autonomy": {
    "objective_isolated_context_enabled": true,
    "objective_isolated_inner_max_turns": 15,
    "objective_isolated_max_workers": 4
  }
}
```

| 字段 | 说明 |
|------|------|
| `objective_isolated_context_enabled` | 默认 `False`。开启后 Self 不再能在 REPL 里直接看到自主任务执行过程中的中间对话（因为跑在独立的、不广播到主 bridge 的 Agent 实例上），这是比阶段一/二更大的行为变化，建议先在测试环境观察一段时间再对生产 daemon 开启 |
| `objective_isolated_inner_max_turns` | 隔离上下文模式下单次 step 的 `run_turn()` 内部预算（`max_turns`），与 `cron.inner_max_turns` 同一档位 |
| `objective_isolated_max_workers` | 隔离上下文模式下最多同时有几个 step 在独立线程里跑 Agent。这是安全阀，不是主要并发控制手段——真正的并发上限仍由 `max_concurrent_objectives_cap` 等既有机制决定 |

**回退**：设 `objective_isolated_context_enabled=false`（默认值），
`_submit_fn` 恢复为提交进 Self 共享 `bridge.input_queue` 的路径，行为与
升级前完全一致。daemon 关闭时会调用
`ObjectiveIsolatedRunner.shutdown(wait=False)` 停止接受新 step，不强行
打断正在跑的线程。

## 5. 阶段四：看护模式 GuardianRunner（P2）

**做什么**：一个不依赖 `GoalSpec`/`GoalJudge`（不要求验收标准）的轻量监督
层，跟踪每个 `autonomous` Objective execution 最近几步的结果摘要，识别
"原地打转"（连续多步结果高度相似），在多次恢复无效后触发既有的收尾路径。
它**不做** DONE/CONTINUE 语义裁定（那是 Goal 模式下 GoalJudge 的职责），
只回答"最近是不是没有实质进展"，终止条件都是客观的：

- 执行完预定步骤 → 正常完成，不归 Guardian 管；
- 达到最大轮次（`guardian_max_rounds`）→ 判 `failed`；
- 连续多次判定"卡住"且恢复额度耗尽（`StuckSignal.GIVE_UP`）→ 先尝试
  `_attempt_redecompose()`（重新分解剩余步骤），成功则继续，失败/不可用
  则判 `failed`。

实现见 `evolution/guardian.py::GuardianRunner`，复用了
`role_agents/stuck_detector.py` 里已经和验收判定解耦的
`StuckDetector`/`ProgressTracker`（[Goal 模式指南](goal-mode-guide.md)
里同一套卡住检测机制）——不是重新发明一套相似度比较逻辑。

> cron 任务已经在 `evolution/cron_job_executor.py::run_job()` 里内联直接
> 使用 `StuckDetector` 完成了同等效果的卡住检测，不需要迁移到
> `GuardianRunner`；`GuardianRunner` 主要补给此前完全没有跨 step 卡住检测
> 能力的 `autonomous` Objective 路径。

**配置开关**（`AutonomyConfig`）：

```json
{
  "autonomy": {
    "guardian_mode_enabled": true,
    "guardian_max_rounds": 20,
    "guardian_stuck_similarity_threshold": 0.92,
    "guardian_stuck_consecutive_limit": 3,
    "guardian_max_recoveries": 2
  }
}
```

| 字段 | 说明 |
|------|------|
| `guardian_mode_enabled` | 默认 `False`。纯增量观察层，关闭时 `ObjectiveExecutor` 行为与升级前完全一致 |
| `guardian_max_rounds` | 单个 execution 最多允许提交多少个 step（含重试），达到即视为"到点了"，触发失败收尾；`<=0` 表示不限制 |
| `guardian_stuck_similarity_threshold` | 连续几步结果摘要的文本相似度达到该值视为"疑似卡住"，透传给内部的 `StuckDetector` |
| `guardian_stuck_consecutive_limit` | 连续多少步都被判"疑似卡住"才真正判定为"卡住" |
| `guardian_max_recoveries` | 判定卡住后最多给几次恢复机会（下一步注入"换个思路"提示，不终止），额度耗尽后才走重新分解/失败路径 |

判定 `RECOVER` 时，Guardian 会给**下一个** step 的 `pending_guidance` 追加
一条"最近几步结果高度相似，看起来没有实质进展，请换一种思路或方法尝试"
的提示，与 `/agent goals reset-step` 注入 guidance 的机制一致（都是复用
`_submit_step()` 已有的 `pending_guidance` 拼装逻辑）。

**回退**：设 `guardian_mode_enabled=false`（默认值），`on_turn_done()` 里
新增的看护逻辑整段跳过，行为与升级前完全一致。

## 6. 常见问题

**Q：四个开关能同时开吗？**
能，互相独立、互不冲突。阶段三（独立上下文）和阶段四（Guardian）经常一起
开启：独立上下文避免脏历史污染 Self 主 session，Guardian 负责在独立上下文
里也能及时发现"原地打转"并收尾，两者是互补关系。

**Q：开启阶段三后，为什么看不到自主任务执行过程中的中间输出了？**
预期行为。隔离上下文模式下每个 step 跑在一个独立的、不广播到主 bridge 的
Agent 实例上，Self 的 REPL/看板只能看到 `ObjectiveExecutor` 已经落盘的
`step.result_summary` 等结构化状态，看不到该 step 内部的多轮工具调用过程。
如果需要观察这部分内容，可以临时关闭 `objective_isolated_context_enabled`
排查，排查完再开回来。

**Q：Guardian 判定为 `failed` 的 Objective，`progress_notes` 里能看出是不是
Guardian 触发的吗？**
能，`progress_notes` 会带 `"guardian:"` 前缀（例如
`"guardian: 连续多轮无实质进展，重新分解不可用/已尝试过"`），与其它失败
原因（提交失败、达到重试上限等）区分开，便于看板/日志排查。

## 7. 相关文档

- [Stage 9 自主运行时指南](self-evolution-stage9-guide.md) — `AutonomousLoop`/`ObjectiveExecutor`/`CronScheduler` 整体架构
- [自主 Daemon 设计](autonomous_daemon_design.md) — daemon 进程内 `AutonomousLoop` 的 tick 档位与状态机
- [Cron 专属执行机制指南](cron-dedicated-execution-guide.md) — `cron_agent_bridge.py`/`CronJobExecutor` 已有的独立 Agent 实例模式，阶段三是同一模式在 Objective 上的对应实现
- [Goal 模式指南](goal-mode-guide.md) — `StuckDetector`/`ProgressTracker`/dead-end 清单的原始实现与设计动机
- [配置指南](config-guide.md#autonomyconfig好奇心评分--自主探索排序权重) — `AutonomyConfig` 完整字段列表
- [命令与工具参考](commands-and-tools-reference.md) — `/agent goals reset-step` 等 CLI 命令完整列表

---

*最后更新：2026-08（新增本文档，覆盖 `next_doc/daemon_autonomous_state_recovery_plan.md` 四个阶段的使用说明）*
