# Goal 交互式调优（Cycle Tuning）指南

> Stage 2（规则+结构化调优 draft/confirm/apply/reject）/ Stage 3（可选的
> LLM 自然语言意见解析，默认关闭）均已实现，见
> `next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md`。
> 依赖 [诊断报告](goal-cycle-diagnostics-guide.md) 的数据，但属于独立能力
> （能力 B）。

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
                                  — 直接生成结构化草案
/agent goals tune <goal_id> "<自然语言改进意见>"
                                  — Stage 3（可选，需开启配置）：不含 '=' 时
                                    按自然语言意见处理，尝试用 LLM 解析成
                                    白名单参数改动
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
                                                       或 Body: {"nl_text": str}（Stage 3，可选，
                                                       需开启配置，可能返回 proposal=null）
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

## Stage 3（可选）：自然语言意见解析

Stage 1/2 已经能覆盖"命令行/接口直接传结构化 `param=value`"和"规则触发
建议"两种场景。如果想直接说一句人话（比如"这个任务最近老是被跳过，帮我
放宽一下触发间隔"）让系统自动映射到白名单参数，可以打开这一层可选增强：

1. 在 `agent_config.json` 里设置：

   ```json
   { "cycle_tuning": { "tuning_llm_parse_enabled": true } }
   ```

2. CLI：命令里不含任何 `param=value`（即没有 `=`）时，整段文本按自然语言
   处理：

   ```
   /agent goals tune goal_abc123 这个任务最近老是被跳过，帮我放宽一下触发间隔
   ```

   REST：

   ```json
   POST /v1/goals/{goal_id}/tuning_proposals
   { "nl_text": "这个任务最近老是被跳过，帮我放宽一下触发间隔" }
   ```

3. 解析出的改动会先生成 `status="draft"` 的草案（`source="user_request"`，
   虽然经过了 LLM 转译，改动意图仍然来自用户），**不会自动生效**——仍然
   要走正常的 `confirm` → `apply` 两步，请在确认前仔细核对 diff，确认
   LLM 理解的映射符合你的本意。

**边界与失败回退**（见
`perception/cycle_tuning.py::parse_nl_request_to_changes()`）：

- 只能映射到 `WHITELIST_PARAMS` 里的五个参数；LLM 即使编出一个不存在的
  参数名，也会在解析阶段被丢弃，不会进入草案（双重校验：`build_tuning_
  proposal()` 本身仍然会对最终结果再做一次白名单校验）。
- 开关未开启、没有可用的 LLM、LLM 输出无法解析成合法 JSON、或判断"这条
  意见无法映射到任何白名单参数"（比如"暂停一阵子"——这应该走
  `/agent goals unrecur`，不是调优参数改动）：都会静默返回"未能生成
  草案"，CLI/REST 会提示改用具体的 `param=value` 命令，不会报错中断，也
  不会强行猜一个可能有害的改动。
