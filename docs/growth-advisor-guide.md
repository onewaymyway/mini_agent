# 成长顾问（Growth Advisor）指南

> 对应方案：`next_doc/growth_advisor_design.md`；实施记录：
> `next_doc/growth_advisor_implementation_record.md`（P1 + P2 里程碑）。

## 1. 这是什么

`evolution/` 目录下已有的一整套模块（`soft_goal_deriver` /
`decision_profile_builder` / `objective_outcome_tracker` ...）服务的是
**Agent 自己**的自我进化：从历史反馈里归纳 Agent 该怎么改进。

成长顾问是同一套"证据 → 候选 → 采纳/忽略反馈"范式，但服务对象换成了
**用户自己**：从你和 Agent 的历史交互里，发现一些反复出现、可能值得你
投入的成长方向，给出候选和一份轻量调研报告——**只是建议，采纳与否始终
由你决定**。

## 2. 默认行为

`GrowthAdvisorConfig.enabled` 默认 `True`（opt-out），也就是说不需要任何
额外配置，系统会：

1. 每天 22:30（`sys:growth_advisor_daily` cron job）自动扫描最近 90 天的
   记忆信号，按关键词统计出候选成长方向；
2. 证据数达到 3 条（`min_evidence_count`）以上的方向才会真正生成候选，
   避免"看了一眼就瞎建议"；
3. 对置信度最高的最多 2 个候选（`max_reports_per_run`）自动生成一份调研
   报告；
4. 每 30 天（`sys:growth_monthly_retrospective`）生成一次月度复盘统计。

**推送节流（P2）**：达到 `notification_min_confidence` 阈值（默认 `0.6`）
的调研报告，会通过既有的通知渠道（含看板"关注与通知"tab）推送，但
`notification_frequency=daily` 时**当天最多推 1 条**（`notification_max_per_day`），
且是当天新生成里置信度最高的一条；把 `notification_frequency` 设为
`kanban_only` 可以完全关掉主动推送，只在看板里看。看板"🌱 成长顾问"
tab 本身随时可看，不受这条节流限制。

## 3. 怎么用

### 看板

打开 `mini_agent_kanban` 看板，切到 **"🌱 成长顾问"** tab：

- 顶部四个指标：候选总数 / 已采纳 / 已忽略 / 已生成报告数
- "🔍 立即为我看看" 按钮：手动触发一轮扫描（不用等每天 22:30）
- 待处理候选卡片：每条显示标题、理由、置信度、证据条数，可以
  ✅ 采纳 / 🙈 忽略 / 📄 查看调研报告
- 指标卡下方：推荐采纳率 + 可展开的"按主题看采纳/忽略排行"（P2 新增）
- 首次打开该 tab 会有一条一次性提示，说明已开启该功能、用了哪些数据

### CLI

```
/growth              # 展示当前待处理候选（等价于 /growth list）
/growth scan          # 手动触发一轮信号扫描 + 候选生成 + Top-N 调研报告
/growth accept <id>   # 采纳某个候选
/growth dismiss <id>  # 忽略某个候选（30 天内不会重新生成同一方向）
/growth report <id>   # 查看（或按需生成）某候选的调研报告正文
/growth retrospective # 查看月度成长复盘统计
```

### API

```
GET  /v1/growth/summary                          # 候选队列 + 报告列表 + 复盘统计
POST /v1/growth/scan                              # 手动触发一轮扫描
POST /v1/growth/candidates/{id}/accept            # 采纳
POST /v1/growth/candidates/{id}/dismiss           # 忽略
GET  /v1/growth/reports/{id}                      # 某份调研报告正文
```

## 4. 常用配置项（`agent_config.json` / `growth_advisor` 块）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关，关闭后信号扫描/候选生成/cron job 全部跳过 |
| `generation_frequency` | `"daily"` | `daily` / `every_12h` / `weekly` / `manual` |
| `notification_frequency` | `"daily"` | `daily` / `weekly_digest` / `kanban_only`；`weekly_digest` 当前与 `daily` 走同一套节流（见实施记录"已知简化"） |
| `notification_max_per_day` | `1` | `notification_frequency=daily` 时，单日最多推送条数 |
| `notification_min_confidence` | `0.6` | 低于此置信度的报告只更新看板、不推送 |
| `min_evidence_count` | `3` | 生成候选所需的最少证据条数 |
| `max_pending_candidates` | `10` | 候选队列 pending 状态上限 |
| `max_reports_per_run` | `2` | 每轮 cron 最多生成的调研报告数 |
| `dismissed_cooldown_days` | `30` | 候选被忽略后的冷却期（天） |
| `excluded_topics` | `[]` | 关注领域黑名单，命中的主题直接跳过 |

不想要这个功能，把 `enabled` 设为 `false` 即可；已经生成的候选/报告数据
不会被自动清除，需要的话手动删除 `.agent/growth_backlog.jsonl` /
`.agent/growth_reports.jsonl` / `.agent/wiki/growth/` 目录。

## 5. 数据存放位置

- `.agent/growth_backlog.jsonl` — 候选队列
- `.agent/growth_reports.jsonl` — 调研报告索引
- `.agent/growth_feedback_ledger.jsonl` — 采纳/忽略反馈流水
- `.agent/wiki/growth/*.md` — 调研报告正文

## 6. 当前局限（P1 + P2 阶段）

- 关键词表覆盖面有限（见 `evolution/growth_advisor.py` 里的
  `_TOPIC_KEYWORDS`），不识别的主题不会被发现，可以直接改代码扩表；
- 调研报告默认走规则模板，信息密度不如 LLM 生成版本（LLM 增强是可选项，
  需要调用方传入 `llm_helper`）；
- 候选置信度会按历史 dismiss 次数打折（复利衰减、有下限），但衰减系数
  是经验取值，不是从真实反馈数据拟合出来的；
- `notification_frequency=weekly_digest` 暂时没有真正的"打包成一条周
  摘要"逻辑，效果上等价于 `daily`；
- 首次触达提示目前只在单次浏览器会话内生效，不是跨会话持久化；
- 看板仍是列表 + 按钮，不是真正的拖拽式看板视图（P3 计划项）；
- `excluded_topics` 黑名单仍只能改配置文件，看板暂无可视化编辑入口。
