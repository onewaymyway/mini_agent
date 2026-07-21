# 每日融合日报（Daily Digest）

对应设计文档：`next_doc/主动推荐与数字分身机制设计方案.md` 第 4.1 节（阶段一）。

## 是什么

把三条原本割裂的数据线合并成一份用户能一眼看懂的日报：

1. `perception/behavior/analyzer.py` 已产出的行为时间分布（app/域名时长、git 提交次数）
2. `perception/goal_backlog.py` 中当天 `last_touched_at` 有变化的 Goal/Objective
3. 上述两者的展示整合（不重新采集数据，只做合流）

明确不做的事：不生成任何"建议"，日报只做回顾。建议属于 `/next`（见
`docs/next-action-advisor-guide.md`），两者刻意分开展示，避免用户分不清
"这是回顾还是建议"。

## 使用方式

```
/digest daily              # 生成"昨天"的融合日报
/digest daily 2026-07-20   # 生成指定日期的融合日报
```

产出文件：
- `.agent/daily_reports/<YYYY-MM-DD>.json`（结构化）
- `.agent/daily_reports/<YYYY-MM-DD>.md`（人类可读，带 wiki frontmatter，
  未来可被 wiki 检索纳入，用于回答"上周都在忙什么"这类回顾问题）

## 定时任务

内置 cron job `sys:daily_digest`，`cron:0 22 * * *`（每天 22:00），
task_template 会调用一次 `/digest daily`。可通过 `/cron disable sys:daily_digest`
关闭。

## 启动展示

CLI/daemon 启动时会自动检查是否存在 `shown_at` 为空的日报，若有则打印一行摘要，
例如：

```
📋 2026-07-20 日报：提交 5 次，2 个目标有进展（`/digest daily 2026-07-20` 查看完整内容）
```

展示后立即回填 `shown_at`，同一份日报不会重复打印。

## 已知限制

- 目标进展的判定只看 `last_touched_at` 是否落在当天窗口内，不区分"进展多大"，
  这是有意的简化：判断"进展质量"需要额外的语义层，留给后续迭代。
- 若 behavior 采集未启用（`perception/behavior/`），行为分布部分会显示"暂无数据"，
  不影响目标进展部分正常展示。
