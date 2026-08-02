# 定时任务（Cron Job）完整参考

本文档汇总当前代码库里全部 `sys:` 前缀的内置 cron job——它们分散注册在
十几个不同模块里，各自的设计文档只描述"这一个 job 为什么存在"，没有一个
地方能看到"现在总共有多少个 job、都在做什么、多久跑一次、有没有花
LLM 调用"。本文档只做汇总和索引，不重复各 job 背后的详细设计取舍，每个
条目都链接回其原始设计文档，需要了解"为什么这么设计"时请跳转过去看。

管理这些 job 的命令行接口见
[命令与工具参考 · 定时任务](commands-and-tools-reference.md#定时任务srcmini_agentclicommandscronpy)；
调度器本身的机制（interval/cron 两种 schedule 格式、`sys:` 前缀治理规则、
`ensure_job`"缺失才补"语义）见
[Stage 9 自主运行时指南 · 5. 定时任务](self-evolution-stage9-guide.md#5-定时任务evolutioncron_schedulerpy)。

---

## 1. 两种注册方式

| 注册方式 | 位置 | 特点 |
|---------|------|------|
| **固定内置**（`_BUILTIN_JOBS`） | `evolution/cron_scheduler.py` | 9 个，首次 daemon 启动时一次性写入 `cron_jobs.json`，模块内静态列表 |
| **按需补注册**（`ensure_*_job()`） | 十余个独立模块 | daemon 每次启动时，`api/server.py::_build_autonomous_loop()` 依次调用各模块的 `ensure_*_job(paths, cron_scheduler)`；job_id 已存在则直接复用（不覆盖用户手动改过的 `schedule`/`enabled`），不存在才创建，默认 `enabled=True`（除非模块显式调用一次 `disable()`，见下方"默认禁用"标注） |

两种方式产出的 `CronJob` 对象治理规则完全一致：`sys:` 前缀的 job **可以
`disable`，不可以 `remove`**；均可用 `/cron set-schedule` 调整触发频率；
均持久化在同一份 `<project_root>/.agent/cron_jobs.json` 里。

**LLM 成本**列里"零 LLM"表示该 job 的 handler 全程规则计算/文件读写，
不调用大模型，也不消耗对话 turn；"含 LLM"表示 handler 内部会调用
`llm_helper` 做一次或多次批量 LLM 请求（成本由 cron 间隔控制，不是
每个 tick 都调）。

---

## 2. 固定内置 job（`cron_scheduler.py::_BUILTIN_JOBS`）

| job_id | 名称 | 默认 schedule | LLM 成本 | 默认启用 | 目的 |
|--------|------|--------------|---------|---------|------|
| `sys:consolidation` | 巩固循环扫描 | `interval:21600`（6h） | 零 LLM | 是 | 技能库冗余检查/去重、能力地图更新、晋升候选评估 |
| `sys:wiki_gap_scan` | wiki 知识缺口扫描 | `interval:43200`（12h） | 零 LLM 触发，可能派发的补全子任务另计 | 是 | 扫描浅层实体/孤儿页面/陈旧专题页，标注陈旧页并可派发补全子任务 |
| `sys:wiki_fallback_cleanup` | wiki 兜底页面清理 | `interval:604800`（7d） | 零 LLM | 是 | 归并/标记 session-facts 兜底页里长期未被合并的 fact |
| `sys:workdir_sync` | 工作区知识整合 | `interval:3600`（1h） | 零 LLM | 是 | 同步工作区文件变化到 WorkdirKnowledge，刷新 WorkThread 进展 |
| `sys:self_eval` | 能力自评 | `interval:86400`（24h） | 零 LLM（规则统计） | 是 | 回顾近 24h 工具使用/任务结果，更新 `capability_map` 置信度 |
| `sys:goal_review` | 目标清理 | `interval:43200`（12h） | 零 LLM | 是 | 标记已完成 Objective 为 completed，暂停超过 7 天无进展的 Objective |
| `sys:digest_trim` | 日志修剪 | `interval:604800`（7d） | 零 LLM | 是 | 修剪 `activity_digest.jsonl`，保留最近 30 天 |
| `sys:session_cleanup` | Session 清理 | `interval:604800`（7d） | 有 LLM（`--extract-first` 对待抽取的候选 session 各触发一次轻量抽取调用） | 是 | 清理长期不用的旧 session：跳过当前/pinned/goal 未结束/最近窗口内的，其余内容太少或已抽取的直接删，未抽取的先补跑一次离线抽取再删；详见 [evolution/session_cleanup.py](../src/mini_agent/evolution/session_cleanup.py) |
| `sys:self_maintain` | 自维护健康检查 | `interval:86400`（24h） | 零 LLM | 是 | 见 §3.1（本 job 同时是自诊断闭环深化计划的信号源） |
| `sys:daily_digest` | 每日融合日报 | `cron:0 22 * * *`（每天 22:00） | 零 LLM | 是 | 合并当天行为分布/目标进展/代码提交，生成融合日报 |
| `sys:next_action_digest` | 主动推荐排序 | `interval:10800`（3h） | 零 LLM | 是 | 对停滞目标/注意力错配候选排序生成推荐，候选为空则跳过 |
| `sys:decision_profile_update` | 决策画像归纳 | `interval:604800`（7d） | 零 LLM（规则归纳） | **否**（默认关闭） | 从历史决策记录归纳可追溯的用户价值模式，证据不足 3 条不落地 |

> `sys:consolidation`/`sys:wiki_gap_scan`/`sys:wiki_fallback_cleanup` 的
> handler 本身只做扫描/规则处理，但触发的下游动作（如 wiki 缺口补全）
> 可能由 agent 以正常 turn 执行，可能间接产生 LLM 调用——这里的"LLM 成本"
> 特指 job handler 自身，不含它派发出去的后续任务。

---

## 3. 按需补注册 job（`ensure_*_job()`，按 `api/server.py` 注册顺序排列）

### 3.1 自维护健康检查内部的四项检查（不是独立 job，附于 `sys:self_maintain`）

`sys:self_maintain` 的 handler（`evolution/self_maintenance.py::
SelfMaintenanceModule.health_check()`）内部做四项检查，结果合并写入同一份
`health_report`：

| 检查项 | 数据来源 | 判定方式 |
|--------|---------|---------|
| `stale_tools` | 最近 20 个 session 的 `traces.jsonl` | 样本量 ≥3 且失败率 ≥60% → 可能失效 |
| `stale_skills` | `skill_loader.tracker` | 超过 30 天未使用的已激活 skill |
| `conflicting_lessons` | `lesson_review.group_lessons()` 聚类 | 同一聚类内同时出现正面/负面关键词 |
| `skill_effectiveness`（自诊断闭环深化 P4） | 最近 30 个 session 的 `meta.json`（`skill_activations`/`tool_stats`） | 按是否激活该 skill 分组比较整体失败率，差异 ≥0.15 判定 `low_effectiveness`/`effective` |

详见 [记忆与自我进化完整参考 · 13. 自维护模块](memory-and-self-evolution-complete-reference.md#13-c4-自维护模块selfmaintenancemodule)。

### 3.2 外部知识反馈闭环计划（`next_doc/external_knowledge_feedback_loop_improvement_plan.md`）

| job_id | 名称 | schedule | LLM 成本 | 默认启用 | 目的 |
|--------|------|----------|---------|---------|------|
| `sys:candidate_queue_triage` | 候选队列过期巡检 | `interval:86400`（P1） | 零 LLM | 是 | 扫描 `novelty_candidates.jsonl`，把超过 30 天仍未处理的 pending 候选标记为 expired |
| `sys:wiki_utility_audit` | wiki 利用率审计 | `interval:604800`（P2） | 零 LLM | 是 | 聚合 `wiki/usage_log.jsonl` 近 30 天检索命中记录为每页利用率统计，修剪超过 90 天的日志 |
| `sys:relevance_threshold_calibration` | GoalRelevance 阈值自校准 | `interval:604800`（P3） | 零 LLM | 是 | 回看 Stage②已判定候选的 relevant 比例，对 Stage①阈值做小步长自动微调 |
| `sys:ecosystem_positioning_scan` | 生态定位扫描 | `interval:604800`（P4） | **含 LLM**（种子 web_search + 批量抽取） | **否**（种子依赖人工配置，默认关闭） | 以配置的同类 agent 框架/开源项目为种子检索，批量 LLM 抽取 entity/fact 写入 wiki（`source_kind=external_ecosystem`） |
| `sys:monthly_trend_retrospective` | 月度战略回顾 | `cron:0 0 1 * *`（P5，每月 1 日） | 零 LLM（纯规则聚合） | 是 | 汇总过去 4 周外部趋势候选采纳情况、wiki 专题页增长、能力置信度变化趋势，生成月度回顾文档 |

### 3.3 自诊断闭环深化计划（`next_doc/self_diagnosis_feedback_loop_deepening_plan.md`）

| job_id | 名称 | schedule | LLM 成本 | 默认启用 | 目的 |
|--------|------|----------|---------|---------|------|
| `sys:improvement_backlog_merge` | 改进信号聚合器 | `interval:86400`（P1） | 零 LLM | 是 | 汇总 self_maintenance/gap_scanner/decommission/self_model 四路信号为排序过的改进候选清单 |
| `sys:suggestion_outcome_review` | 建议采纳率回看 | `interval:1209600`（P2，14 天） | 零 LLM | 是 | 回看 2-4 周前 `self_maintenance` 提出的工具健康建议，对比当前失败率判断是否改善 |
| `sys:self_model_snapshot` | 能力自画像快照 | `interval:86400`（P3） | 零 LLM | 是 | 将 `capability_snapshot` 按时间戳存档，并与约 7 天前的快照做 diff，回看弱项清单是否收敛 |

（P4 skill 结果有效性审计未注册为独立 job，见 §3.1——设计上归并进
`sys:self_maintain` 本身，理由见该计划文档 P4 小节。）

### 3.4 外部数据知识化与自我改进闭环计划（`next_doc/external_knowledge_wiki_and_self_improvement_plan.md`）

| job_id | 名称 | schedule | LLM 成本 | 默认启用 | 目的 |
|--------|------|----------|---------|---------|------|
| `sys:external_knowledge_extractor` | 外部资讯 → wiki 知识抽取 | `interval:21600`（P1，6h） | **含 LLM**（批量摘要抽取） | **否**（默认关闭，建议先观察几天再手动开启） | 消费 `agent_watch` 频道 RSS 事件，批量 LLM 抽取 entity/fact 写入 wiki 待落盘队列（`source_kind=external_watch`） |
| `sys:tech_radar_search` | 主动检索反哺 wiki 知识雷达 | `interval:86400`（P3，24h） | **含 LLM**（web_search + 批量抽取） | **否**（默认关闭） | 以 `gap_scanner` 缺口 + 手工关键词为种子做 web_search，批量 LLM 抽取写入 wiki（`source_kind=external_search`） |
| `sys:external_trend_capability_link` | 外部技术趋势 x 能力薄弱点关联 | `interval:604800`（P4，7d） | **含 LLM**（语义匹配） | **否**（默认关闭） | 把 P1/P3 沉淀的外部知识 wiki 页面与 `capability_map` 薄弱能力域做 LLM 轻量匹配，产出候选草稿 |

### 3.5 关注对象通知分级计划（`next_doc/watchlist_notification_goal_design.md`）

| job_id | 名称 | schedule | LLM 成本 | 默认启用 | 目的 |
|--------|------|----------|---------|---------|------|
| `sys:watchlist_report_<tier_id>`（每个 tier 一个，动态生成） | 关注对象分级汇报（`<tier_id>`） | 由 `.agent/notification/report_tiers.yaml` 逐 tier 配置 | 零 LLM | 由配置决定 | 消费 `pending_hits.jsonl` 中对应 tier 的未读命中，生成摘要并通过 `NotificationDispatcher` 分发 |
| `sys:goal_relevance_judge` | 外部信号 Goal 相关性判定（LLM 批量） | `interval:600`（P5，10 分钟） | **含 LLM**（唯一引入 LLM 的环节） | 是 | 消费 `goal_relevance_candidates.jsonl` 中未判定候选，批量判断相关性并更新 `GoalNode.external_context` |
| `sys:novelty_importance_judge` | 新颖重要事件 LLM 重要性判定 | `interval:600`（10 分钟） | **含 LLM**（唯一引入 LLM 的环节） | 是 | 消费 `novelty_candidates_raw.jsonl` 中未判定候选，批量判断重要性，仅 `importance=high` 才进入待确认队列 |

### 3.6 外部输入可靠性/可观测性/归档计划（`next_doc/external_input_reliability_observability_archive_plan.md`）

| job_id | 名称 | schedule | LLM 成本 | 默认启用 | 目的 |
|--------|------|----------|---------|---------|------|
| `sys:archive_gc` | 外部输入/通知系统热文件长期归档 | `cron:0 3 * * *`（§4，每天凌晨 3 点） | 零 LLM | 是 | 把 `alerts.jsonl`/`pending_hits.jsonl`/`goal_relevance_candidates.jsonl`/`notification/reports.jsonl` 中已处理超过 `retention_hours` 的记录按自然月迁出到 `.agent/archive/` |

---

## 4. 按"是否含 LLM 调用"分类速查

**零 LLM 成本**（规则计算/文件读写，daemon 默认全部开启，可放心长期挂着跑）：
`sys:consolidation`、`sys:wiki_gap_scan`、`sys:wiki_fallback_cleanup`、
`sys:workdir_sync`、`sys:self_eval`、`sys:goal_review`、`sys:digest_trim`、
`sys:self_maintain`、`sys:daily_digest`、`sys:next_action_digest`、
`sys:decision_profile_update`、`sys:candidate_queue_triage`、
`sys:wiki_utility_audit`、`sys:relevance_threshold_calibration`、
`sys:monthly_trend_retrospective`、`sys:improvement_backlog_merge`、
`sys:suggestion_outcome_review`、`sys:self_model_snapshot`、
`sys:watchlist_report_<tier_id>`、`sys:archive_gc`。

**含 LLM 调用**（成本由 cron 间隔控制，非每 tick 触发；其中标 ⏸ 的默认关闭，
需要人工确认价值后再手动开启）：
`sys:ecosystem_positioning_scan` ⏸、`sys:external_knowledge_extractor` ⏸、
`sys:tech_radar_search` ⏸、`sys:external_trend_capability_link` ⏸、
`sys:goal_relevance_judge`、`sys:novelty_importance_judge`、
`sys:session_cleanup`（仅对待抽取的候选 session 逐个触发一次轻量抽取，
非候选/已抽取/无需抽取的 session 不产生 LLM 调用）。

## 5. 常用操作速查

```bash
# 查看当前全部 job（含默认禁用的）
/cron list --all

# 手动触发一次某个 job（不影响下次正常触发时间）
/cron run sys:improvement_backlog_merge

# 观察一段时间后手动开启一个默认关闭的 job
/cron enable sys:tech_radar_search

# 调整某个 job 的触发频率
/cron set-schedule sys:self_maintain interval:43200
```

完整命令列表见
[命令与工具参考 · 定时任务](commands-and-tools-reference.md#定时任务srcmini_agentclicommandscronpy)。

## 6. 维护本文档

新增 `sys:` job 时，请在对应设计计划落地的同一次改动里同步更新本文档
（在 §2 或 §3 对应小节加一行，并在 §4 速查表补上分类），避免出现"代码里
已经有十几个 job，但只有各自零散设计文档、没有统一清单"的情况——这正是
本文档要解决的问题，不应该重新出现。
