# 成长顾问：真正的主动检索 + 成长轨迹时间线可视化 改进计划

关联背景：`growth_advisor_improvement_plan_v4.md`（N4 外部资讯摘录）、
`growth_advisor_research_quality_plan.md`（阶段 1 摘录）、
`external_knowledge_wiki_and_self_improvement_plan.md`（P3 tech_radar 主动检索）、
`growth_advisor_improvement_plan_v2.md`（P4-6 证据数走势）。

本次聚焦两个此前明确列为"非目标"或尚未做的方向，不涉及其余既有功能的行为变更。

## 方向一：真正的主动检索（report_active_search）

### 现状与问题

`generate_growth_report()` 里 N4 的"外部背景参考"完全依赖
`_external_signal_matching_pages()` 对**已有 wiki 页面**的被动扫描——这些
页面来自 `tech_radar_search.py`（定期巡检种子关键词）或
`knowledge_extractor.py`（被动订阅事件）。如果某个成长候选主题冷门、
两条被动管道都还没覆盖到，`_external_signal_count_for_topic()` 返回 0，
`external_context_section` 整段跳过，报告退回纯粹基于用户记忆证据的内容
——即使调用方（比如手动触发 `/growth report` 的那次 Agent 会话）此时
明明具备 `web_search` 工具、完全可以现查。

### 方案

`generate_growth_report()` 新增可选参数 `web_search_fn`（默认 `None`，
不影响任何现有调用方）。当且仅当：
- `cfg.report_active_search_enabled` 为 `True`（默认关闭，opt-in）
- `llm_helper` 非 `None`（复用报告本就走 LLM 生成路径这一前提，规则
  模板路径不引入检索）
- `_external_signal_count_for_topic()` 命中数为 0（被动管道确实没有
  可用素材，不是"素材不够多就补"，避免和被动巡检重复劳动）
- 调用方传入了 `web_search_fn`（判断"调用方是否具备检索工具"的逻辑
  下放给调用方——`generate_growth_report()` 自己不导入
  `tools/builtin.py::web_search`，因为它可能在没有工具上下文的纯离线
  路径被调用，例如测试、cron 兜底）

才触发一次定向检索：以候选标题+关键词拼一个检索 query，调用
`web_search_fn(query, max_results=...)`，结果喂给 `llm_helper` 做一次
轻量抽取（复用 `tech_radar_search.py` 的 `EntityCandidate`/
`FactCandidate` 抽取管道与 prompt 风格，不重新发明一套），产出：

1. 当次报告 prompt 里的 `external_context_section`（跟 N4 已有格式一致，
   只是数据来源从"扫描已有 wiki 页面"变成"这次现查的结果"）；
2. 原样落一份 wiki 页面（`source_kind="external_search"`，跟
   `tech_radar_search.py` 共用同一个 `EXTERNAL_SEARCH_SOURCE_KIND`
   标记，只是 `source_entries` 前缀换成
   `growth_advisor_active_search:<candidate_id>`，供 `wiki/stats.py`
   审计时能区分是巡检产生还是报告生成时按需触发的），供下次同主题的
   被动扫描/其它候选复用，不重复检索。

失败处理：`web_search_fn` 抛异常、返回空、LLM 抽取失败/解析失败，任一
环节出错都静默跳过，退回"没有外部背景"的原有路径（跟 N4 已有的
`except Exception: external_context_section = ""` 是同一个兜底原则），
不让检索失败拖垮报告生成本身。

单次报告最多触发 1 次检索（`report_active_search_max_calls`，默认 1，
预留字段但当前不做多轮检索的复杂度），避免一份报告为了"凑素材"打
多个 API 调用。

### 非目标

- 不做"素材不够多再补几条"的增量检索——只在**完全没有**被动素材时
  触发，是"至少有点东西"而不是"素材质量优化"。
- 不新增检索通道/不允许 `generate_growth_report()` 自己创建
  `web_search` 实现，检索能力必须由调用方注入，保持这个函数在无工具
  上下文时依然可安全调用（跟 `llm_helper` 现有的注入模式一致）。
- `run_daily_cycle()`（cron 无人值守路径）默认不会传入
  `web_search_fn`，因为 cron 触发时是否具备检索工具、检索预算如何
  控制是更大的调度问题，留给以后有真实需求时再接入；这次只打通
  "手动触发报告生成、调用方本身有 agent 上下文" 这一条路径（CLI/API
  的 `/growth report <id>` 命令，那里已经能拿到 Agent 的工具集）。

## 方向二：成长轨迹时间线可视化（growth_topic_lifecycle）

### 现状与问题

`growth_topic_map()`（P3，`growth_advisor_improvement_plan_v2.md`）已经
按 `dedupe_key` 聚合了同一主题的全部历史候选记录，还带上 P4-6 的
`evidence_trend`（证据数走势点），但整体呈现是**静态列表**——一行一个
主题，看不出"这个方向是怎么走过来的"：什么时候第一次被信号扫描发现、
什么时候用户确认/采纳、有没有落地成 Goal、Goal 现在是进行中/停滞/
完成。用户想回顾"我这半年都在关注什么、真正做成了什么"时，得自己在
`growth_topic_map()`、`growth_feedback_ledger.jsonl`、`GoalBacklog` 之间
脑内拼图。

### 方案

新增 `growth_topic_lifecycle(paths, dedupe_key, *, goal_backlog=None) ->
list[dict]`，聚合同一 `dedupe_key` 下所有可追溯的数据源，产出一条按
时间正序排列的事件列表（不新增任何落盘文件，纯只读聚合，跟
`growth_topic_map()`/`goal_growth_alignment()` 是同一治理原则）：

| 事件 stage | 时间来源 | 数据来源 |
|---|---|---|
| `discovered`（首次被信号扫描发现） | 最早一条候选的 `created_at` | `GrowthBacklog.load_all()` |
| `report_generated`（每次生成过调研报告） | `GrowthReport` 落盘时间（复用 `report_id` 关联） | `list_reports()` |
| `accepted` / `dismissed`（每次状态流转） | `GrowthFeedbackLedger` 各条目 `ts` | `GrowthFeedbackLedger.all_entries()` |
| `goal_linked`（落地成 Goal） | 候选 `linked_goal_id` 非空那条记录的 `updated_at` | `GrowthBacklog` |
| `goal_completed` / `goal_stalled` / `goal_active`（Goal 当前状态） | `GoalNode.status` / `last_touched_at` | 传入的 `goal_backlog`（跟 `goal_growth_alignment()` 一样，缺失时静默跳过这一段，不报错） |

每个事件是 `{"stage": str, "ts": float, "label": str, "detail": str}`
的简单字典，`label` 是给看板直接渲染的一句话（中文，跟其它模块的
文案风格一致），`detail` 是可选的补充信息（比如报告 id、goal id）。
函数本身只做"从各数据源捞出时间点、排序、拼文案"，不引入任何新的
判断/推荐逻辑。

`growth_topic_map()` 的返回行不直接内嵌完整时间线（避免主列表 payload
膨胀、每次列出全部主题时都要拼所有子事件），改为看板/CLI 需要展开某个
主题详情时按需调用 `growth_topic_lifecycle(paths, dedupe_key)` 单独取。

### 非目标

- 不做"预测下一步会怎样"之类的推断，只是如实呈现已发生的事件序列。
- 不新增落盘文件——时间线完全从既有的 `growth_backlog.jsonl`、
  `growth_reports_index.jsonl`（含归档）、`growth_feedback_ledger.jsonl`、
  `GoalBacklog` 四处现有数据聚合得到。
- 看板的时间线图形渲染（时间轴 UI 组件）本次不做，先把数据聚合函数
  和 CLI 文字版打通，图形化留给看板专项迭代（`next_doc/看板主交互界面
  改进方案.md` 系列）。

## 验收标准

- `report_active_search_enabled=True` 且传入 `web_search_fn`/`llm_helper`
  时，一个此前 `_external_signal_count_for_topic()` 命中 0 条的候选，
  生成报告后能在 prompt 里看到 `external_context_section` 非空，且
  wiki 里新增一条 `source_kind="external_search"`、
  `source_entries` 含 `growth_advisor_active_search:` 前缀的页面。
- 关闭开关或不传 `web_search_fn` 时，行为与改动前完全一致（含
  `generate_growth_report()` 所有既有调用方，不需要改动调用方代码）。
- `growth_topic_lifecycle()` 对一个经历过"发现 → 生成报告 → 采纳 →
  落地成 Goal → Goal 完成"完整流程的 dedupe_key，返回的事件列表
  `stage` 顺序与 `ts` 单调不减；对只有部分阶段的候选，缺失阶段直接
  不出现在列表里，不补空事件。
