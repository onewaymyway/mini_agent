# Goal 交互式调优（Cycle Tuning）指南

> Stage 2 实现，见
> `next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md`。
> 依赖 [诊断报告](goal-cycle-diagnostics-guide.md) 的数据，但属于独立能力
> （能力 B）。Stage 3（自然语言意见 → 自动映射到白名单参数）尚未实施。

## 解决什么问题

看完[跨轮次诊断报告](goal-cycle-diagnostics-guide.md)之后，你可能想调整
这个 Goal 的调度频率、优先级、执行阶段等参数——但不希望自己去翻配置文件，
也不希望改动直接生效、没有一个"看一眼再确认"的环节。交互式调优提供
**草案（draft）→ 确认（confirm）→ 应用（apply）** 三步流程，改动本身经过
明确确认才会真正生效。

## 安全边界：白名单参数

调优机制**只能**修改以下五个参数，每个参数都复用已有的、独立测试覆盖的
修改入口，不允许通过这个机制修改任意代码/配置文件，也不会执行任意工具
调用：

| 参数 | 说明 | 复用的既有入口 |
|---|---|---|
| `schedule` | cron 调度频率，如 `interval:3600` / `cron:0 9 * * 1` | `make_goal_recurring()` |
| `priority` | Goal 优先级 | `GoalBacklog.update_fields()` |
| `execution_phase` | 手动切换执行阶段（`explore`/`converge`/`stable`/`tidy`/`auto`） | `execution_phase.set_mode()` |
| `task_template` | cron 触发时注入的任务描述模板 | `CronScheduler.update_task_template()` |
| `regenerate_spec` | 重新生成一份执行规范草稿（只生成，不自动确认） | `GoalExecutionSpecBuilder.build_draft()` |

**明确不支持**：修改 Goal 的 title/description 本体、产出目录结构/命名
规则、白名单之外的任何字段。扩大白名单需要单独评审。

## 命令

```
/agent goals tune <goal_id> <param>=<value> [<param2>=<value2> ...] [--reason <text>]
                                  — 直接生成结构化草案（不含自然语言解析）
/agent goals tune suggest <goal_id>
                                  — 基于诊断报告规则触发的候选草案
/agent goals tune list <goal_id>
                                  — 列出历史草案（含状态）
/agent goals tune confirm <goal_id> <proposal_id>
                                  — 确认草案（仍未生效）
/agent goals tune apply <goal_id> <proposal_id>
                                  — 应用已确认的草案
/agent goals tune reject <goal_id> <proposal_id> [reason...]
                                  — 拒绝草案，作废
```

示例：

```
/agent goals tune goal_abc123 priority=8 --reason "最近产出质量不错，提高优先级"
/agent goals tune confirm goal_abc123 tuning_xxxxxx
/agent goals tune apply goal_abc123 tuning_xxxxxx
```

## REST

```
POST   /v1/goals/{goal_id}/tuning_proposals          Body: {"changes": [...], "source"?: str}
POST   /v1/goals/{goal_id}/tuning_proposals/suggest   规则触发建议（可能返回 proposal=null）
GET    /v1/goals/{goal_id}/tuning_proposals           列出历史草案
POST   /v1/goals/{goal_id}/tuning_proposals/{id}/confirm
POST   /v1/goals/{goal_id}/tuning_proposals/{id}/apply
POST   /v1/goals/{goal_id}/tuning_proposals/{id}/reject   Body（可选）: {"reason": str}
```

## 四个状态：draft → confirmed → applied | rejected

- **draft**：草案已生成，尚未确认，不影响任何实际状态。
- **confirmed**：用户确认了"这份草案本身"，**仍未生效**——与
  `GoalExecutionSpec` 的确认语义一致，确认不代表立即执行。
- **applied**：真正调用了白名单参数对应的修改入口。每一项改动的成败在
  `apply_results` 里逐条列出（`{"param", "to", "ok", "detail"}`），某一项
  失败不影响其它项已经成功应用的部分，不会静默吞掉失败。应用完成后会
  自动追加一条 `progress_notes`（"根据诊断报告调优：... "）留痕。
- **rejected**：草案作废，不产生任何实际改动，同样会追加一条
  `progress_notes` 记录"提出过但被拒绝"，避免下次规则建议又提出同样的
  内容而你不记得已经考虑过。

## 规则触发的建议（`tune suggest`）

不调用 LLM，基于诊断报告里已经算出的信号直接生成候选草案：

- **cron 连续跳过达到阈值**（默认 5 次）且 `schedule` 是
  `interval:<秒>` 格式 → 建议把间隔翻倍。`cron:` 表达式格式没有一种
  确定性的"放宽"方式，不会为这种格式生成建议。
- **长期卡在 explore 阶段未收敛**（且处于 `auto` 模式、未被手动锁定）→
  建议重新生成一份执行规范草稿，供你对比是否要用新草案替换现状（这一步
  只生成草稿，不会自动确认生效，仍需 `/agent goals spec confirm`）。

两个信号都没命中时返回"当前没有基于诊断报告规则触发的调优建议"，不是
错误。

## `regenerate_spec` 的额外依赖

应用 `regenerate_spec` 改动需要 `AppConfig` 来构造
`GoalExecutionSpecBuilder`（与生成执行规范草稿走同一条路径）。CLI/REST
会尝试自动加载配置；如果这一项失败并提示"未提供 AppConfig"，可以改用
`/agent goals spec generate <goal_id>` 手动生成。

## 与 Stage 3 的关系

当前只支持两种草案生成方式：命令行/接口直接传结构化的 `param=value`，或
`tune suggest` 的规则触发建议。把一句自然语言意见（比如"这个任务最近老是
被跳过，帮我放宽一下"）自动映射到白名单参数，属于 Stage 3，尚未实施——
在此之前，请直接用 `tune <goal_id> schedule=interval:7200` 这样的结构化
命令。
