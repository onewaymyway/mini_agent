# Goal 跨轮次诊断报告（Cycle Diagnostics）指南

> Stage 1（只读诊断）实现，见
> `next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md`。
> 能力 B（交互式调优 draft/confirm/apply）尚未实施，见该文档 Stage 2/3。

## 解决什么问题

周期性执行的 Goal/cron 任务，有时候你想知道的不是"某一次执行跑得怎么样"，
而是**"这个任务整体跑得怎么样"**——已经跑了多少轮、当前处于哪个执行阶段、
有没有健康问题、cron 触发是不是正常、最近几轮都产出了什么、用的目录结构和
判定规则是什么。这些信息此前分散在 `goals.json`、`goal_cycle_archive.jsonl`、
`.agent/goal_execution_phase/<goal_id>.json`、`cron_jobs.json`、产出目录的
`manifest.json` 好几个地方，需要自己翻文件拼图。诊断报告把这些一次性聚合
展示。

**这是纯只读聚合，不产生任何副作用**：不修改任何状态、不触发任何调度、不
调用 LLM（规则聚合已经能回答"整体状态如何"，机制说明是静态模板文本）。

## 命令

```
/agent goals diagnose <goal_id>
```

REST：

```
GET /v1/goals/{goal_id}/cycle_diagnostics
```

返回 `{"diagnostics": {...}}`，字段结构见下。Goal 不存在时 CLI 打印错误，
REST 返回 404。

## 报告包含什么

- **概览**：`cycle_count`（已完成轮次数）、`recurring`（是否周期性）、
  `schedule`（绑定的 cron 表达式，非周期性 Goal 为空）、`status`、
  `created_at`、`last_scheduled_at`。
- **执行阶段**：`execution_phase_mode`/`execution_phase_locked`（见
  [Goal 执行阶段指南](goal-execution-phase-guide.md)）、最近几条阶段变迁
  历史（`phase_history_summary`）。
- **健康告警**：`recent_health_alerts`——直接复用
  `execution_phase.check_phase_health()` 的既有判定（长期卡在 explore /
  阶段反复横跳），报告只是把当前是否命中阈值展示出来，**不会**因为读了
  一次报告就影响告警冷却计时（冷却状态仍由通知系统在真正发送通知时落盘）。
- **Cron 健康**：`cron_health`——绑定的 CronJob 的 `run_count`/
  `consecutive_skip_count`/`enabled` 等，非周期性 Goal 或未绑定 cron 时为
  `null`。
- **最近轮次产出**：`recent_cycle_summaries`，默认最近 10 轮，优先取产出
  目录里的 `manifest.json`（"热数据"），不够时回退读
  `goal_cycle_archive.jsonl` 里更早的归档轮次（"冷数据"），拼成一份连续
  时间线；归档条目带 `"archived": true` 标记，产出目录路径见
  `output_dir` 字段。
- **进展记录尾部**：`progress_notes_tail`，最近若干行，不是全量。
- **机制说明**：`mechanism_notes`，一组针对这个 Goal 当前配置生成的说明
  文字（产出目录规则是 `cycle_%04d` 还是 `run_%04d`、阶段判定是 auto 还是
  手动锁定、执行规范是否已确认），纯静态模板 + 变量替换，不调用 LLM。

## 性能说明

`goal_cycle_archive.jsonl` 可能随长期运行的周期性 Goal 无限增长。诊断报告
只从文件尾部往前读够所需轮数的那一段（`_tail_jsonl_records()`），不做全
文件扫描，轮次数量增长不会显著拖慢单次诊断。

## 与能力 B（交互式调优）的关系

诊断报告是能力 A，纯读取；后续的能力 B（根据诊断结果生成"改哪些参数"的
草案，经用户确认后才真正应用）依赖同一份诊断数据，但属于不同的 Stage，
尚未实施。当前想要根据诊断结果调整 schedule/priority/执行阶段/执行规范，
仍需分别使用 `/agent goals recur`、`update_fields`（REST PATCH）、
`/agent goals phase set`、`/agent goals spec generate` + `confirm` 这些既有
命令。
