# 成长顾问（Growth Advisor）指南

> 对应方案：`next_doc/growth_advisor_design.md`（P1 原始方案）、
> `next_doc/growth_advisor_improvement_plan_v2.md`（P4-0~P4-7，已全部
> 完成）、`next_doc/growth_advisor_improvement_plan_v3.md`（P5-0~P5-6，
> 已全部完成）；逐阶段实施细节见
> `next_doc/growth_advisor_implementation_record.md`。P6（反馈粒度细化
> + LLM 增强路径可观测性）见本文档 2.7/2.8 节；Goal/Cron 打通见
> `next_doc/growth_advisor_goal_cron_integration_plan.md` 与本文档
> 2.9 节；调研信息获取与整理见
> `next_doc/growth_advisor_research_quality_plan.md` 与本文档 2.10 节。
> 代码入口：`src/mini_agent/evolution/growth_advisor.py`（模块头部
> docstring 是最权威的阶段变更历史，本文档是面向使用者的整理版）。

## 1. 这是什么

`evolution/` 目录下已有的一整套模块（`soft_goal_deriver` /
`decision_profile_builder` / `objective_outcome_tracker` ...）服务的是
**Agent 自己**的自我进化：从历史反馈里归纳 Agent 该怎么改进。

成长顾问是同一套"证据 → 候选 → 采纳/忽略反馈"范式，但服务对象换成了
**用户自己**：从你和 Agent 的历史交互里，发现一些反复出现、可能值得你
投入的成长方向，给出候选和一份轻量调研报告——**只是建议，采纳与否始终
由你决定**。

经过 P1 → P4 → P5 三轮迭代，现在这套机制包含：信号扫描、候选生成（含
反馈调权 + 类别聚合调权 + 证据分布度调权 + 探索位）、调研报告生成（模板
/LLM 两种起草方式，含增量刷新）、采纳后回访（含被动信号初筛）、推送
节流（日频/周摘要/按类别静音）、关键词表的自学习与用户自定义、数据存储
的生命周期管理，以及一整套诊断自查能力。下面按"用户能看到/能配置的
东西"组织，不逐个复述实现细节（那些在 `growth_advisor.py` 的模块
docstring 和 `next_doc/growth_advisor_implementation_record.md` 里）。

## 2. 核心机制一览

### 2.1 信号扫描 → 候选生成（每天自动跑一次）

1. `growth_signal_scan()` 扫描最近 90 天（`SIGNAL_SCAN_WINDOW_DAYS`）的
   记忆条目，按关键词表（内置 7 个主题 + 用户自定义 + LLM 学到的）做
   命中频次统计，写回 `profile.derived["growth_focus_areas"]`。
   - 默认纯规则式，零 LLM 成本；打开 `llm_signal_augment_enabled`
     （默认关闭）后，会对关键词表命中不到的近期记忆额外做一次 LLM
     归纳，尝试发现规则表覆盖不到的新主题（只在有 agent 上下文的调用
     路径——CLI `/growth scan`、API `/growth/scan`——才会真正用上）。
   - 归纳出的新主题不是用完即弃：会写入
     `profile.derived["growth_topic_keywords"]`（`source="llm_learned"`,
     `confirmed_by_user=False`），之后每次扫描都命中则计入"连续命中"
     计数，达到 3 次（`_AUTO_CONFIRM_STREAK`）后自动转正
     （`confirmed_by_user=True`），不需要用户记得手动确认。
2. `growth_candidate_derive()` 从达标（证据数 >= `min_evidence_count`，
   默认 3）的主题生成/合并候选到 backlog（`GrowthBacklog`，状态机
   `pending → accepted | dismissed`，超过 45 天未处理自动 `expired`）。
   置信度不是单纯看证据条数，是一条乘子链：
   `topic 衰减 × 类别衰减 × 回访调权 × 证据分布度`——
   - **主题级衰减**：同一方向历史上被 dismiss 过越多次，下次（冷却期
     过后）重新生成时初始置信度打的折扣越大（有下限，不会打到 0）；
   - **类别级衰减**（P4-3）：内置主题分成"技术类/管理类/表达类"（+
     兜底"其他类"），同类别下累计的 dismiss 次数会用更温和的系数压低
     同类新主题的初始置信度；
   - **回访调权**（P4-3）：某方向被采纳后如果回访结果是"没空推进"
     （stalled），后续同方向重新生成时温和降权；"确实在推进"
     （progressed）则温和加权；
   - **证据分布度**（P5-2）：证据集中在一两天内刷出来 vs 分散在几周里
     持续出现，后者更像真实的持续关注，置信度会更高——不改变
     `evidence_refs` 的数据结构，时间戳单独存
     `profile.derived["growth_evidence_timestamps"]`。
3. 命中 `excluded_topics` 黑名单的方向直接跳过；`max_pending_
   candidates`（默认 10）限制 pending 候选总量，避免无限堆积。

### 2.2 调研报告生成（Top-N，含"探索位"）

`run_daily_cycle()` 从本轮新增候选里，按置信度取最多
`max_reports_per_run`（默认 2）个生成调研报告：

- 默认走规则式 Markdown 模板（零 LLM 成本）；打开
  `report_quality_llm_enabled`（默认关闭）后额外调一次 LLM 换取更高
  信息密度的正文，与 `llm_signal_augment_enabled` 相互独立。
- **探索位（P5-6，默认关闭）**：打开 `exploration_slot_enabled` 后，
  Top-N 名额里最多留 1 个不再是"置信度最高"，而是留给"最近
  `exploration_recent_window`（默认 5）份报告里没出现过的类别"——
  避免长期只强化用户历史上感兴趣的类别，冷门但可能有价值的新方向
  永远排不上号。如果候选覆盖的类别最近都出现过，退化成正常按置信度
  选，不强行制造探索。被选中的探索位报告，正文和摘要会各带一句
  "这是我们不太确定你会不会感兴趣的新方向"的标注
  （`GrowthReport.is_exploration=True`），管理预期，避免被当成"我们
  觉得这个特别重要"；这个标注只影响 Top-N 报告生成，不改变推送阈值/
  类别静音这些既有的节流逻辑，探索位报告一样可能被推送也可能只留在
  看板。
- **增量刷新（P4-4）**：报告生成时会记一份"当时的证据数快照"
  （`evidence_count_at_generation`），之后证据数又新增达到
  `report_refresh_min_new_evidence`（默认 3）条，就会被
  `reports_needing_refresh()` 判定为"值得提示刷新"；排序不是单纯按
  新增总量，而是"最近 14 天内突增"优先（P5-4，复用证据分布度同款的
  时间戳分桶思路）——证据这两天突然涨的，比"几个月里慢慢攒够阈值"的
  更可能是用户正在主动推进，看板/CLI 都可以按需重新生成
  （`refresh_growth_report()` / `POST /growth/candidates/{id}/report/
  refresh`），旧报告不删除，只是不再是候选"当前挂着"的那份。

### 2.3 采纳后回访（含被动信号初筛）

候选被采纳 `followup_review_days`（默认 30）天后，如果用户还没回答过
"有没有推进"，`pending_followups()` 会把它纳入待回访列表；用户回答
`progressed`（有推进）或 `stalled`（没推进）后写回候选、记入反馈台账、
参与后续置信度调权（回访只发生一次，不强制回答）。

**被动信号初筛（P5-4）**：到期前会先看一眼这段时间的证据数走势
（复用 P4-6 的 `growth_topic_trend.jsonl` 快照）——如果窗口期内证据数
还在涨，说明用户大概率还在关注，会直接跳过这次主动询问、顺延到下一轮
（不持久化"已推迟"状态，纯按当次快照现算，证据不再涨了自然就会展示）；
证据走平或下降时才正常展示回访卡片，提问措辞也会换成更贴合实际状态的
"最近这个方向的记忆变少了，是先放一放了吗？"。

### 2.4 推送节流（generation 与 interruption 分开治理）

看板"🌱 成长顾问"tab 随时可看，不受推送节流影响；**主动推送**（通知
中心/邮件等渠道）单独节流：

- `notification_frequency=daily`（默认）：先按 `notification_min_
  confidence`（默认 0.6）过滤，再排除类别被静音的报告，剩下的按
  "置信度 × 该类别历史采纳率加权"（P4-5，`_notification_priority_
  score`）取优先级最高的一条，当天最多推 `notification_max_per_day`
  （默认 1）条；全部被过滤掉就不推送（"宁可不推，不为了凑数硬推"）。
- `notification_frequency=weekly_digest`：不逐条推，按"距上次推送是否
  满 7 天"（非自然周）把窗口期内新生成的全部报告标题打包成一条摘要
  推送。
- `notification_frequency=kanban_only`：完全不主动推送，只更新看板。
- **按类别覆盖（P4-5）**：`category_notification_frequency` 可以给
  单个类别（"技术类"/"管理类"/"表达类"/"其他类"）单独配成
  `"kanban_only"`，把这个类别完全静音（仍在看板展示，不主动推送），
  目前只支持这一种覆盖值，不支持按类别设置独立的 daily/weekly_digest
  频率。

### 2.5 关键词表 / 自定义主题 / 类别系统

- 内置 7 个主题（`_TOPIC_KEYWORDS`）+ 用户自定义（看板"➕ 添加自定义
  主题"）+ LLM 学到、待确认或已自动转正的主题，三者运行时合并成一份
  有效关键词表；用户可以隐藏某个内置主题（不删除，只是黑名单标记，
  可随时"↩️ 恢复"，见 P4-7）。
- **类别归类（P5-3）**：内置主题硬编码归入"技术类/管理类/表达类"三个
  类别，自定义/LLM 学到的主题默认落进兜底的"其他类"。打开
  `topic_category_llm_enabled`（默认关闭）后，新增/确认转正主题时会
  额外调一次 LLM 做"4 选 1"粗粒度分类（不引入 embedding），结果持久化
  在 `profile.derived["growth_topic_categories"]`，此后类别级反馈学习
  （2.1 节）、按类别静音（2.4 节）、推送优先级加权（2.4 节）对这些
  自定义主题同样生效。开关先开后关不会撤销已经分类的结果，只是不再
  产生新的分类。

### 2.6 数据生命周期 / 存储卫生（P5-0）

只追加不轮转的 jsonl 文件长期运行会无限增长，P5-0 做了两处压缩（都不是
简单删历史，而是先确认不会破坏依赖全量历史的统计口径）：

- `growth_topic_trend.jsonl` **降采样**：超过 60 天的旧快照，按"同一
  主题同一周只留最新一条"压缩，`growth_candidate_derive()` 每轮 cron
  顺带自动执行。
- `growth_reports_index.jsonl` **分层存储**：`compact_reports_index_
  storage()` 把"不再是任何候选当前挂着的那份 + 生成超过 180 天"的旧
  报告移到 `growth_reports.archive.jsonl`；查询侧同步兜底
  （`get_report_by_id()` 查不到活跃索引会再查归档文件，`list_reports
  (include_archived=True)` 供累计统计用），不会因为报告被归档就出现
  404 或"报告生成总数"突然变少。这一步**不接入 `run_daily_cycle()`
  自动触发**（改的是"能不能查到某份报告"这个用户可感知的行为，不适合
  悄悄每天跑），留给人工维护脚本或未来单独排期的月度 cron。
- `growth_feedback_ledger.jsonl` 的分层存储还没做（消费方全是累计
  统计语义，需要先转持久化聚合计数再归档，改动量级更大，见
  `growth_advisor_improvement_plan_v3.md` P5-0 小节），当前数据量还
  不大，不紧急。

### 2.7 忽略原因：区分"方向错"和"报告差"（P6）

此前"忽略一个候选"只有一个动作，系统没法知道你是"这个方向我压根不
关心"还是"方向没错，只是这份报告写得不痛不痒"——两者被当成同一种
负向信号，都会拿去压低这个方向（以及同类别）下次重新出现时的初始
置信度。这可能错误地永久打压一个其实有价值、只是报告碰巧写得不好的
方向。

现在忽略候选时可以带一个可选的原因：

| 原因 | 含义 | 是否影响置信度 |
| --- | --- | --- |
| `not_interested` | 这个方向我不关心 | 是，正常参与方向/类别衰减 |
| `bad_timing` | 方向可以，但现在不是时候 | 是，正常参与方向/类别衰减 |
| `report_not_useful` | 方向没错，是报告没写好 | **否**，不压低这个方向今后的置信度 |
| `unspecified`（默认，不传原因时的取值） | 未指定 | 是，行为与 P6 之前完全一致 |

`report_not_useful` 的次数单独统计（`_report_quality_dismiss_counts()`），
接入月度复盘的"报告质量待改进"排行和看板诊断面板，作为"这些方向该
优先改进报告生成方式，而不是该少推荐"的信号，跟"最常被忽略"排行是
两个独立的维度。

- **看板**：待处理候选卡片新增一个"忽略原因"下拉框（默认"不说明
  原因"），点"🙈 忽略"时一并提交；拖拽式看板视图暂不支持指定原因
  （拖拽忽略统一记为 `unspecified`，卡片文案有提示，想细化原因请切到
  列表视图操作）。
- **CLI**：`/growth dismiss <id> [reason]`，`reason` 可省略。
- **API**：`POST /growth/candidates/{id}/dismiss`，body 可选
  `{"reason": "..."}`；不传 body 或不传该字段都等价于旧版本行为。

旧数据兼容：P6 之前写入的 dismiss 记录没有 `reason` 字段，读取时统一
视为 `unspecified`，继续正常参与衰减，不会因为升级就让历史反馈失效。

### 2.8 LLM 增强调用状态可见（P6）

`llm_signal_augment_enabled` / `report_quality_llm_enabled` /
`topic_category_llm_enabled` 这三个 opt-in 开关，此前调用失败（异常、
空响应、JSON 解析失败）只会静默退回默认路径（规则式扫描 / 模板报告 /
"其他类"兜底），用户完全看不出"我打开的这个增强开关，到底有没有在
正常工作"——对一个用户主动选择打开的能力来说，静默失败比默认关闭更
容易造成误解。

现在诊断面板（"🩺 我的数据 / 诊断信息"）新增一块"LLM 增强调用状态"，
逐个展示三个调用点各自"最近一次调用结果"：

- ✅ 成功 / 成功但本次没有新发现
- ⚠️ 调用成功但响应为空 / 响应解析失败
- ℹ️ 未命中记忆太少，本次跳过调用（仅信号增强扫描）
- ❌ 调用抛出异常（附截断后的错误信息）

只记"最近一次"，不追加历史（这是健康检查用的状态，不是审计日志）；
从未触发过的调用点会显示"尚未触发过"，跟"触发过但失败"区分开。

### 2.9 Goal/Cron 打通：对齐分析 + 一键落地 + 回访用真实进度（本次新增）

> 对应方案：`next_doc/growth_advisor_goal_cron_integration_plan.md`。

此前成长顾问从头到尾只读 memory 记忆，跟另一套同样成熟的机制——
`GoalBacklog`（跨会话目标层级）+ `goal_cron_bridge`（目标的周期性自动
推进）——完全没有交叉。现在打通了三层：

**对齐分析（阶段 A，默认开启，默认零 LLM 成本，可选 LLM 增强）**：
`goal_growth_alignment()` 默认用关键词匹配（跟内置主题关键词表同等
复杂度，不引入 embedding），比对"证据数达标的兴趣方向 / 已采纳候选"
和"GoalBacklog 里的 Goal 标题"，找出三类需要关注的情况：

- **有兴趣信号但没建目标**：说明这个方向反复被聊到，但还没有落成一个
  可追踪的目标；
- **已建目标但停滞**：Goal `status="active"` 且 `last_touched_at`
  超过 `goal_alignment_stalled_days`（默认 21 天）没动过；
- **LLM 建议的潜在配对**（`goal_alignment_llm_enabled=True` 时才有，
  默认关闭）：关键词匹配的局限在于，用户"随口聊起"一个方向和"正式
  定成目标"时的措辞经常不一样（比如兴趣叫"数据分析能力"、Goal 叫
  "提升可视化技能"），字面对不上但实质是一件事。打开这个开关后，
  `/growth align` 会对"两条规则都没匹配上的兴趣方向 + Goal"各取一批
  （各 20 条上限），额外调一次 LLM 做语义匹配，命中的配对单独列在
  "LLM 建议关注的潜在配对"里——这是建议，不是确定关系，不会自动写入
  任何持久化的关联；LLM 输出的 `topic`/`goal_id` 只有能在候选池里对上
  号才会被采纳，防止幻觉匹配；调用结果计入诊断面板的"LLM 增强调用
  状态"区块（`goal_alignment_match`，见 2.8 节），跟其余三个既有 LLM
  调用点同等可观测。

CLI：`/growth align`（`goal_alignment_llm_enabled=True` 时自动带上
LLM 增强）；诊断面板新增
`goal_alignment.unmatched_interests_count` /
`goal_alignment.stalled_linked_goals_count` 两个计数（明细走
`/growth align`，跟诊断面板一贯"只给计数，明细走专门入口"的惯例一致；
诊断快照本身不触发 LLM 调用，保持零成本）；
`goal_alignment_enabled=False` 或 GoalBacklog 不可用时两个计数整体为
`None`，不影响诊断面板其余部分。

**一键落地（阶段 B，用户显式触发，不会自动发生）**：一个候选已经生成
过调研报告后，可以 `/growth adopt-goal <candidate_id>` 直接创建一个
GoalBacklog Goal——标题用候选标题，`description` 用报告摘要 + 报告
路径引用，打上 `growth_advisor` 标签；候选反向记一份
`linked_goal_id`，如果候选此前还是 `pending` 会顺带流转成
`accepted`。落地之后要不要把这个 Goal 设成周期性任务（绑定 cron），
走既有的 Goal 管理命令即可，成长顾问不代管 Goal 的生命周期。

**回访优先用 Goal 真实状态（阶段 C，向后兼容）**：候选一旦有
`linked_goal_id`，30 天回访（2.3 节）判断"要不要展示回访卡片"时，
优先看这个 Goal 的真实状态而不是 memory 证据数走势：

- Goal 已 `completed` → 视为显而易见的"已推进"，自动记录，不占用一次
  主动询问；
- Goal `active` 且近期有 touch → 仍在正常推进，跳过本轮、顺延；
- Goal 已停滞（超过 `goal_alignment_stalled_days` 没动）、或
  `paused`/`abandoned`/`failed`/`cancelled` → 正常展示回访卡片，
  问法换成"这个方向对应的目标看起来有一阵没动了，要不要先放一放，或者
  重新规划一下？"，比原来"有没有真的推进？"更贴合实际状态。

没有关联 Goal 的候选、或调用方没有传入 GoalBacklog（比如某些老的调用
路径尚未升级），行为跟此前完全一致，不受任何影响。

### 2.10 调研信息获取与整理：从"计数/现编"到"真摘录/有来源/更具体"（本次新增）

> 对应方案：`next_doc/growth_advisor_research_quality_plan.md`。

此前调研报告生成（`generate_growth_report()`）本质是"一个 prompt
直接让 LLM 现编 500 字四段式内容"：外部资讯即使打开
`report_include_external_context` 也只是一个数字（"大约有 12 条相关
资讯"），页面内容完全没被用上；报告结构固定是"为什么值得关注/怎么
入门/常见资源/投入周期"四段，容易写成放之四海皆准的通用建议。这次
做了四处增量改进，互相独立，任一开关关闭都退化到改动前的行为：

- **外部资讯从"计数"升级为"摘录"**（复用 `report_include_external_
  context` 这一个开关，不新增开关）：报告生成时真正取最近 2 条命中
  wiki 页面的正文摘录（不只是数一数有几条），并要求 LLM 引用到的地方
  用『（参考：页面id）』标注来源——用户能自己判断报告里的内容是不是
  真的有依据，不是 LLM 凭训练知识现编。
- **忽略原因驱动针对性调整**（`report_dismiss_reason_adaptive_
  enabled`，默认开启，零额外成本）：复用已有的 `report_not_useful`
  统计（2.7 节），如果这个方向之前的报告被标过"内容太笼统"，生成
  prompt 时追加一句强约束，要求 LLM 这次给出具体、贴合用户处境的
  建议，不要重蹈覆辙。
- **两段式生成：先提纲、后填充**（`report_two_stage_enabled`，默认
  关闭——多一次 LLM 调用，成本翻倍）：打开后先让 LLM 针对候选主题
  提炼 3-4 个具体问题（不是"怎么入门"这种泛泛提问），再逐一回答，
  替代固定的四段式结构。提纲阶段调用失败/空响应/解析失败都静默退回
  单段式 prompt，不影响报告生成本身；调用结果计入诊断面板"LLM 增强
  调用状态"区块的新调用点 `report_outline`。

CLI/看板无需任何操作——这几项都发生在 `generate_growth_report()`
内部，`/growth report <id>`、`/growth scan` 自动带上新行为，行为差异
只体现在报告正文本身（有真实摘录 + 来源标注、更具体的结构）。

### 2.11 cron 主动检索预算调度 / 检索质量反馈闭环 / Goal 状态历史（本次新增）

> 对应方案：`next_doc/growth_advisor_cron_search_and_status_history_plan.md`。

补齐三处此前留白的空隙：

- **cron 路径也能触发主动检索**（`cron_triggered_active_search_
  enabled`，默认关闭）：此前主动检索只有"手动触发调研报告"这一条
  路径能用，`sys:growth_advisor_daily`（cron 无人值守路径）从不触发。
  打开开关后，`run_daily_cycle()`（`/growth scan` 与 cron job 共用）
  每个自然日最多对 `cron_triggered_active_search_daily_limit`（默认
  1）个"证据数最高但从未有过任何外部背景"的候选触发一次定向检索，
  复用既有的检索 → LLM 抽取 → 落盘 wiki 管道，产出跟手动触发路径共用
  同一个 `source_kind="external_search"` 标记。预算按自然日计数，
  跟推送节流是两套独立的计数器。
- **主动检索的质量反馈闭环**（`tech_radar.quality_feedback_enabled`，
  默认开启，零额外成本）：`sys:tech_radar_search` 种子轮转此前不看
  历史检索质量——一个连续查不到任何有用内容的种子会被无限期继续
  排队。现在种子连续 `tech_radar.low_quality_streak_threshold`
  （默认 3）次检索都没有抽出 entity/fact，会在
  `tech_radar.low_quality_cooldown_days`（默认 14 天）冷却期内暂时
  跳过；冷却期内只要有一次查到有用内容，或冷却期满，都会自动重新
  参与轮转——是降级不是拉黑。
- **Goal 状态变更历史**：`GoalBacklog.set_status()` 现在会给 Goal
  节点追加一条 `{"status", "at"}` 历史记录（状态真正变化时才追加，
  重复 `set` 同一状态不产生冗余条目）。`growth_topic_lifecycle()` 消费
  这份历史后，能在时间线里正确呈现"完成过一次又被重新打开"这种
  往复（新增 `goal_reopened` 事件），而不只是展示最后一次状态；旧数据
  没有这份历史时自动退回原有的"只看当前状态"展示，不受影响。

三者都不需要用户在看板/CLI 做任何额外操作——前两项通过配置开关生效，
Goal 状态历史是数据结构层面的补全，`growth_topic_lifecycle()` 的既有
调用方（看板/CLI 展开某个主题详情）自动获得更完整的时间线。

## 3. 默认行为速览

`GrowthAdvisorConfig.enabled` 默认 `True`（opt-out），不需要任何额外
配置，系统会：

1. 每天 22:30（`sys:growth_advisor_daily` cron job）自动跑一遍 2.1~2.4
   节的完整流程；
2. 每 30 天（`sys:growth_monthly_retrospective`）生成一次月度复盘统计
   （数量/采纳率/主题排行 + 跨候选的"成长主题地图"聚合）。

除 `enabled` 本身外，本文档提到的所有细化能力（LLM 增强扫描、LLM 报告
正文、LLM 主题分类、按类别静音、探索位……）默认全部关闭，只有总开关是
"零成本默认开启"，其余是"opt-in 增强"——这是这套机制一以贯之的设计
取舍，加新能力不改变这条底线。

## 4. 怎么用

### 看板

打开 `mini_agent_kanban` 看板，切到 **"🌱 成长顾问"** tab：

- 顶部四个指标：候选总数 / 已采纳 / 已忽略 / 已生成报告数
- "🔍 立即为我看看" 按钮：手动触发一轮扫描（不用等每天 22:30）
- 待处理候选卡片：安装了可选依赖 `streamlit-sortables` 时，是三列
  （待处理 / 已采纳 / 已忽略）拖拽式看板，拖动卡片到目标列即完成
  ✅ 采纳 / 🙈 忽略（拖回"待处理"不支持撤销）；未安装该依赖时自动回退
  到列表 + 按钮样式（标题/理由/置信度/证据条数 + ✅ 采纳 / 🙈 忽略 /
  📄 查看调研报告三个按钮），两种展示方式功能等价，只是交互形式不同；
  探索位报告在标题旁会有一句"探索方向"标注
- "有没有推进？"回访卡片：满足回访窗口且被动信号初筛没有跳过的候选
  会展示在这里，两个按钮对应 progressed/stalled
- "可以刷新一下这份报告"提示：`reports_needing_refresh()` 命中的报告，
  按"最近是否突增"优先排序，一键重新生成
- 指标卡下方：推荐采纳率 + 可展开的"按主题看采纳/忽略排行"
- 再下方：可展开的"🗺️ 成长主题地图"——按主题聚合的完整推进轨迹，每个
  方向显示当前状态、历史峰值置信度、历史累计出现/采纳/忽略次数、证据
  数走势（文字箭头 ↗/↘/→，没有引入图表库）
- 内置主题列表下方有"🙈 隐藏某个内置主题"折叠区块和"已隐藏的内置主题"
  列表（带"↩️ 恢复"按钮）
- 首次打开该 tab 会有一条一次性提示，说明已开启该功能、用了哪些数据
  （跨会话持久化，展示过一次后不会再弹）
- 顶部一个默认折叠的"🩺 我的数据 / 诊断信息"面板：当前配置快照（开关/
  证据阈值/推送频率/黑名单/各个 LLM 增强开关）、最近一次信号扫描的
  时间与每个内置主题各命中了多少条记忆（只给计数，不回显记忆原文，
  且明确标注这个计数跟"成长主题地图"的历史累计口径不是一回事，数字
  对不上是正常的）、记忆总条数与落在扫描窗口内的条数、两个 cron job
  是否启用/上次运行时间/累计运行次数——出现"候选一直是 0"这类情况时，
  打开这个面板通常就能看出卡在哪一步（关键词没命中 / 证据数不够 /
  定时任务没跑过 / 功能被关掉）。**"记忆总条数"长期是 0 或很低**，
  通常不是信号扫描本身的问题，而是长期记忆覆盖率不足——见
  `docs/memory-backfill-guide.md`（`next_doc/
  memory_backfill_and_profile_update_plan.md` 方向一）。

### CLI

```
/growth              # 展示当前待处理候选（等价于 /growth list）
/growth scan          # 手动触发一轮信号扫描 + 候选生成 + Top-N 调研报告
/growth accept <id>   # 采纳某个候选
/growth dismiss <id> [reason]  # 忽略某个候选（30 天内不会重新生成同一
                       # 方向）；reason 可选，见 2.7 节，不传等价于
                       # unspecified（行为与 P6 之前一致）
/growth report <id>   # 查看（或按需生成）某候选的调研报告正文
/growth retrospective # 查看月度成长复盘统计
/growth align          # 兴趣方向 ⇄ 目标 对齐分析（见 2.9 节）：哪些方向
                       # 有兴趣但没建目标、哪些已建目标但停滞
/growth adopt-goal <id> # 把候选落地成一个 GoalBacklog 目标（要求候选
                       # 已有调研报告，见 2.9 节阶段 B）
```

回访、关键词管理、类别静音、探索位这些更细的操作目前只在看板/API 提供
入口，CLI 保持精简。

### API

```
GET  /v1/growth/summary                              # 候选队列 + 报告列表 + 复盘统计 + 首次触达状态 + 诊断快照
POST /v1/growth/first_touch_ack                       # 标记首次触达提示已展示（幂等）
POST /v1/growth/scan                                   # 手动触发一轮扫描
POST /v1/growth/candidates/{id}/accept|dismiss          # 采纳 / 忽略；dismiss 可选 body {"reason": "..."}（见 2.7 节）
GET  /v1/growth/followups                              # 待回访候选列表（含 question_hint 提问措辞）
POST /v1/growth/followups/{id}/progressed|stalled       # 回答一次回访
POST /v1/growth/keywords                                # 添加自定义关键词主题
POST /v1/growth/keywords/{topic}/confirm                # 确认保留一个待确认主题
POST /v1/growth/keywords/{topic}/remove                 # 删除自定义主题 / 隐藏内置主题
POST /v1/growth/keywords/{topic}/restore                 # 恢复一个被隐藏的内置主题
GET  /v1/growth/reports/refresh_candidates               # "值得刷新"的报告列表
POST /v1/growth/candidates/{id}/report/refresh            # 重新生成该候选的调研报告
GET  /v1/growth/reports/{id}                             # 某份调研报告的完整元数据 + 正文
GET  /v1/growth/health_trend                             # 健康度趋势快照序列（v4 N1，见 5.5 节）
```

## 5. 常用配置项（`agent_config.json` / `growth_advisor` 块）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关，关闭后信号扫描/候选生成/cron job 全部跳过 |
| `generation_frequency` | `"daily"` | `daily` / `every_12h` / `weekly` / `manual` |
| `notification_frequency` | `"daily"` | `daily`（当天新报告里优先级最高的一条，最多 `notification_max_per_day` 条）/ `weekly_digest`（每 7 天把窗口期内新生成的全部报告打包成一条摘要推送）/ `kanban_only`（只更新看板，不推送） |
| `notification_max_per_day` | `1` | `notification_frequency=daily` 时，单日最多推送条数 |
| `notification_min_confidence` | `0.6` | 低于此置信度的报告只更新看板、不推送 |
| `min_evidence_count` | `3` | 生成候选所需的最少证据条数 |
| `max_pending_candidates` | `10` | 候选队列 pending 状态上限 |
| `max_reports_per_run` | `2` | 每轮 cron 最多生成的调研报告数 |
| `dismissed_cooldown_days` | `30` | 候选被忽略后的冷却期（天） |
| `first_touch_notice_enabled` | `true` | 是否展示首次进入看板的一次性知情提示 |
| `excluded_topics` | `[]` | 关注领域黑名单，命中的主题直接跳过（看板"⚙️ 配置"tab 可视化编辑，一行一个） |
| `llm_signal_augment_enabled` | `false` | 打开后信号扫描阶段会在规则式关键词扫描之外，额外调一次 LLM 尝试从命中不到的近期记忆里归纳新主题；只在有 agent 上下文的调用路径生效 |
| `followup_review_days` | `30` | 候选被采纳这么多天后，若还没回访过就进入待回访列表（会先经过 P5-4 的被动信号初筛） |
| `report_quality_llm_enabled` | `false` | 打开后生成调研报告正文时额外调一次 LLM 换取更高信息密度；默认零成本模板 |
| `report_refresh_min_new_evidence` | `3` | 候选证据数比上次生成报告时新增达到这个数量，才提示"可以刷新了" |
| `category_notification_frequency` | `{}` | 按类别（"技术类"/"管理类"/"表达类"/"其他类"）覆盖推送偏好，目前只识别 `"kanban_only"` 这一种覆盖值（完全静音该类别的主动推送） |
| `topic_category_llm_enabled` | `false` | 打开后，自定义/LLM 学到的主题新增或确认转正时会额外调一次 LLM 做"4 选 1"粗粒度分类，结果持久化，使这些主题也能参与类别级反馈学习/静音/推送优先级 |
| `exploration_slot_enabled` | `false` | 打开后，`max_reports_per_run` 名额里最多留 1 个给"最近几轮报告没出现过的类别"，其余候选仍按置信度选；关闭时行为与改动前完全一致 |
| `exploration_recent_window` | `5` | 判断"某类别最近是否出现过"时，往回看最近多少份报告（不含已归档的旧报告） |
| `sync_confirmed_topics_to_tech_radar_enabled` | `false` | （v4 N3）打开后 `run_daily_cycle()` 收尾时把已确认关键词幂等同步进 `TechRadarConfig.keywords`；会实际修改 `agent_config.json` |
| `report_include_external_context` | `false` | （v4 N4）打开后调研报告（LLM 生成路径）会把外部资讯命中数作为背景信息，独立于 `report_quality_llm_enabled` |
| `goal_alignment_enabled` | `true` | （2.9 节）兴趣方向 ⇄ 目标 对齐分析总开关，纯规则式关键词匹配，零 LLM 成本 |
| `goal_alignment_stalled_days` | `21` | （2.9 节）已关联 Goal 的方向，`active` 状态下超过这么多天没被 touch 就判定为"停滞"，独立于 `followup_review_days` |
| `goal_alignment_llm_enabled` | `false` | （2.9 节）对齐分析是否额外做一次 LLM 语义匹配，找出关键词匹配漏掉的"字面不同、实质同一件事"配对；结果只出现在建议列表，不自动写入关联关系 |
| `report_two_stage_enabled` | `false` | （2.10 节）报告生成先让 LLM 提炼 3-4 个具体问题再逐一回答，替代固定的四段式结构；多一次 LLM 调用，默认关闭 |
| `report_dismiss_reason_adaptive_enabled` | `true` | （2.10 节）报告曾被标"内容太笼统"时，下次生成追加针对性提醒；不产生额外 LLM 调用，默认开启 |
| `report_active_search_enabled` | `false` | （2.11 节）手动触发调研报告（有 `web_search_fn` 的调用路径）时，被动扫描命中 0 条素材才现查一次；会实际发起检索调用，默认关闭 |
| `cron_triggered_active_search_enabled` | `false` | （2.11 节）`sys:growth_advisor_daily` cron 路径是否也触发主动检索，每天最多处理 `cron_triggered_active_search_daily_limit` 个"证据数最高但没有外部背景"的候选；会实际发起检索调用，默认关闭 |
| `cron_triggered_active_search_daily_limit` | `1` | （2.11 节）cron 主动检索每个自然日的预算上限，开关关闭时不生效 |

另外 `memory_backfill.cron_run_backfill_enabled`（默认 `true`，v4 N2）
控制 cron 任务收尾是否自动回填记忆，属于 `memory_backfill` 配置块而非
`growth_advisor` 块，详见 5.5 节 N2 与 `docs/memory-backfill-guide.md`。

不想要这个功能，把 `enabled` 设为 `false` 即可；已经生成的候选/报告数据
不会被自动清除，需要的话手动删除 `.agent/growth_backlog.jsonl` /
`.agent/growth_reports.jsonl` / `.agent/growth_reports.archive.jsonl` /
`.agent/growth_feedback_ledger.jsonl` / `.agent/growth_topic_trend.jsonl`
/ `.agent/wiki/growth/` 目录。

`excluded_topics` 现在可以直接在看板「⚙️ 配置」tab 的"🌱 成长顾问"分类
里编辑（一行一个主题关键词），不需要再手改 `agent_config.json`；配置
加载路径本身也做了类型校验兜底（P5-5）——字段类型跟 dataclass 声明明显
不匹配时（比如本该是 dict 的字段被存成字符串），会回退到该字段的默认值
并记一条 warning 日志，不会导致 Agent 起不来或者错误值静默流入下游。

## 5.5 v4 新增能力（N1~N4，`next_doc/growth_advisor_improvement_plan_v4.md`）

在 P1~P6（本文档 3~5 节描述的基线）之上，v4 计划的四个方向已全部落地
（详见 `next_doc/growth_advisor_implementation_record.md` 对应章节）。
四项均遵循同一个原则：**默认不改变任何既有行为**——新增的写操作/外部
信号全部走独立开关，默认关闭或默认不产生副作用。

### N1：诊断面板健康度趋势化

`diagnostics_snapshot()` 只反映"当下"，无法看出"这周记忆总条数涨了
多少"。v4 新增 `.agent/growth_health_trend.jsonl`：`run_daily_cycle()`
每天结束时追加一条快照（`total_entries` / `entries_in_scan_window` /
`backfill_candidates_count` / `pending_followups_count` /
`reports_needing_refresh_count` / `topics_tracked_count`），超过窗口期
的旧快照会被降采样压缩（复用 `growth_topic_trend.jsonl` 同款机制）。

- 看板"🌱 成长顾问"tab 的诊断面板新增一个可折叠的"📈 健康度趋势"区块，
  用折线图展示上述几个指标的走势；
- API：`GET /v1/growth/health_trend`（独立于 `/growth/summary`，看板
  展开该区块时才请求，不影响默认加载速度）。

这是纯只读展示能力，不需要额外配置即可生效（只要总开关 `enabled` 为
真、且 `sys:growth_advisor_daily` 正常运行）。

### N2：cron 记忆回填（对应 `docs/memory-backfill-guide.md` 方向一 M3）

daemon/cron 任务此前完全不产出记忆——`cron_agent_bridge.py` 每次触发都
重新构建 Agent、不跨触发保留 session 历史，M1/M2 的存量回填天然扫不到
这类运行。v4 在 `CronJobExecutor.run_job()` 的收尾 `finally` 块里新增
一次"记忆化"：

- **触发条件**：仅当本次运行正常收尾（`final_status == idle`，不含
  `timed_out`/`needs_human_review`）且有实质产出文本时才生成记忆；
- **摘要生成**：`memory_backfill.py::generate_summary_from_text()`，
  跟离线批量回填共享同一套摘要 prompt，额外把 job 的任务描述拼进输入
  避免摘要读起来没有上下文；
- **`session_id` 格式**：`cron:<job_id>:<run_id>`，跟真实 `Session.id`
  取值空间不相交；
- **去重**：同一 job 连续触发如果产出的摘要跟该 job 最近一条已生成的
  记忆高度雷同，跳过写入（避免"每小时检查一次待办"这类高频重复任务把
  记忆库刷成同质化内容），只影响本次记忆生成，不影响任务本身的其它
  收尾逻辑（比如产出物清单照常写）。

配置项：`memory_backfill.cron_run_backfill_enabled`（默认 `true`），
关闭后 cron 任务恢复到 v4 之前"不产出记忆"的行为。上线效果可以直接用
N1 的健康度趋势图观察——`total_entries` 应该能看到回升。

### N3：成长顾问关键词表 → tech_radar 检索种子同步

成长顾问的关键词表（`profile.derived["growth_topic_keywords"]`）和
外部输入网关的 `TechRadarConfig.keywords` 此前是两套互不感知的"用户
关注点"表达。v4 新增单向桥接：`sync_confirmed_topics_to_tech_radar()`
把已确认（`confirmed_by_user=True`，含内置主题）的关键词幂等合并进
`TechRadarConfig.keywords`，供 `tech_radar_search.py` 的主动检索种子池
使用。只增不删——隐藏/删除一个成长顾问主题不会反向删除对应的 tech_radar
种子（两者语义不同：用户可能仍想关注外部动态，只是不想让它出现在成长
顾问候选里）。

- 配置项：`growth_advisor.sync_confirmed_topics_to_tech_radar_enabled`
  （**默认 `false`**——这会实际修改 `agent_config.json`，属于有外部
  效果的写操作，需要用户显式打开）；
- 触发时机：`run_daily_cycle()` 收尾处，跟 N1 的健康度快照同一个"旁路
  增强不能反过来影响主流程"模式，异常静默降级；
- 写入路径复用配置系统既有的原子写入（`config_catalog.py` 新增
  `apply_list_seed_merge()` / `write_config_file()`），不会绕过校验
  直接改 JSON 文件。

打开后需要注意：`daily_seed_limit`（默认 5，不受本次改动影响）不变，
关键词表持续增长会让种子池覆盖一轮的周期变长——`tech_radar_search.py`
本身的轮转游标机制能兜住（不丢种子，只是变慢），是已知、可接受的权衡。

### N4：外部资讯作为候选/报告的展示背景（不参与判断）

`knowledge_extractor.py` 沉淀进 wiki、带 `source_kind` 为
`external_watch`/`external_search` 标记的条目，现在可以作为成长顾问
调研报告的"背景参考"：

- `_external_signal_count_for_topic()`：只读聚合，统计最近 N 天内 wiki
  里有多少条外部资讯条目命中了某主题的关键词；
- `generate_growth_report()` 新增可选参数 `profile`/`cfg`，仅当
  `report_include_external_context` 为真、且报告走 LLM 生成路径
  （`report_quality_llm_enabled` 打开且有 agent 上下文）时，才会把这个
  计数作为背景信息拼进 prompt，并明确要求 LLM"报告的核心判断仍要基于
  用户自己的记忆证据"。

**关键约束**：这个数字只影响报告正文的 prompt 输入，**不会**改变
`candidate.confidence`/证据数等任何落盘字段，也不影响候选排序或推送
优先级——外部世界讨论的热度不等于用户自己的兴趣，成长顾问一贯坚持
"置信度只反映用户自己证据"的原则在这里没有被打破。

配置项：`growth_advisor.report_include_external_context`（默认
`false`），**独立于** `report_quality_llm_enabled`（可以只要更好的
报告质量、不要外部背景，两者分开控制）。当前只接入了报告生成路径，
看板候选卡片上还没有展示这个计数（基础设施先行，展示位留给后续按需
接入）。

## 6. 数据存放位置

- `.agent/growth_backlog.jsonl` — 候选队列（整表重写，不是只追加）
- `.agent/growth_reports.jsonl` — 调研报告元数据索引（活跃窗口）
- `.agent/growth_reports.archive.jsonl` — 归档的旧报告元数据（P5-0，
  不再是任何候选当前挂着、生成超过 180 天的报告会被移到这里；查询侧
  自动兜底，不会因为归档就查不到）
- `.agent/growth_feedback_ledger.jsonl` — 采纳/忽略/回访反馈流水
- `.agent/growth_topic_trend.jsonl` — 按主题的证据数/置信度历史快照
  （P4-6，超过 60 天的旧快照会被降采样压缩）
- `.agent/growth_health_trend.jsonl` — 全局健康度快照（v4 N1，每天
  一条，同样有降采样机制）
- `.agent/growth_advisor_state.json` — 推送节流状态 + 首次触达提示
  状态
- `.agent/wiki/growth/*.md` — 调研报告正文

## 7. 当前局限（P1 ~ P6 全部完成后的已知边界）

- 关键词表覆盖面终归有限：内置 7 个主题之外，靠用户自定义 + LLM
  归纳/自动转正机制补充，不识别的主题不会被发现；
- 调研报告默认走规则模板，信息密度不如 LLM 生成版本，需要显式打开
  `report_quality_llm_enabled` 且调用方处于有 agent 上下文的场景；
- 各类衰减/加权系数（dismiss 衰减、类别衰减、回访调权、证据分布度、
  类别历史采纳率加权）都是经验取值，不是从真实反馈数据拟合出来的——
  P6 的 dismiss 原因细化让"哪些反馈该参与衰减"更准确了，但衰减系数
  本身仍未接入任何自动校准；
- `growth_feedback_ledger.jsonl` 尚未纳入数据生命周期管理（P5-0 延后
  项），长期运行会持续增长，当前数据量还不影响性能；
- 探索位（`exploration_slot_enabled`）默认关闭：这项改动主动打破了
  "证据不够强就不推荐"的一贯克制原则，需要用户显式选择打开，不会
  悄悄改变默认的推荐排序行为；打开后也只影响 Top-N 报告生成，不影响
  推送优先级排序；
- 月度复盘仍只有数量统计 + 采纳率 + 主题排行 + 跨候选主题地图 + P6
  新增的报告质量排行；地图目前只是聚合展示，不做预测或自动排序推荐；
- 看板拖拽式视图依赖可选包 `streamlit-sortables`，未安装时自动回退到
  列表 + 按钮；即便安装了，从"已采纳"/"已忽略"拖回"待处理"也不生效
  （后端 API 本来就不支持撤销采纳/忽略这个操作）；拖拽视图目前也不
  支持指定 P6 新增的忽略原因，拖拽忽略统一记为 `unspecified`，想细化
  原因需要切到列表视图；
- LLM 增强调用状态（P6，`llm_call_status`）只保留"最近一次"，不是
  历史趋势；只有 `classify_topic_category_llm()` 调用点传了 `paths`
  才会被记录，若未来新增其它 LLM 增强调用点，需要记得同样接入状态
  记录，否则会退回 P6 之前"静默失败"的旧行为；
- 报告质量信号（`report_not_useful`）目前只是"记录下来给人看"，还
  没有被用来反过来指导报告生成策略本身（比如自动为高频被标记的方向
  切换到 LLM 生成、或调整模板内容），这是留给未来版本的闭环；
- Goal/Cron 打通（2.9 节）目前只做了"对齐分析 + 一键落地 + 回访读取
  Goal 状态"三件事：候选 → Goal 是单向的（Goal 完成/停滞状态会反哺
  回访判断，但 Goal 的 `progress_notes` 更新不会自动同步成候选的新
  证据）；对齐分析默认仍是关键词包含匹配，`goal_alignment_llm_
  enabled` 打开后能补上"字面不同、实质同一件事"这类配对（比如候选叫
  "数据分析能力"、Goal 叫"提升可视化技能"），但 LLM 建议目前只停在
  "展示给你看"，还没有"一键确认成正式关联"的入口（要正式关联仍然要
  走 `/growth adopt-goal` 或手动改标题让关键词匹配上）；也
  没有接入 cron 执行历史（比如某个绑定 cron 的 Goal 反复
  `_notify_cycle_failed`）作为新的候选证据来源——这是
  `next_doc/growth_advisor_goal_cron_integration_plan.md` 明确标注的
  非目标，留给后续单独排期；看板前端也还没有接入 `/growth align` /
  `adopt-goal` 的可视化入口，目前只有 CLI；
- 调研信息获取（2.10 节）目前只做了"复用现有 wiki 素材做摘录 + 结构
  更具体 + 忽略原因驱动调整"三件事：不会在报告生成时主动触发新的
  外部检索（只从已经存在的 wiki 页面里取材，候选主题在 wiki 里完全
  没有相关页面时，摘录部分自然为空，报告仍然退回"只基于用户自己记忆
  证据"的路径）；相近主题之间不共享调研素材（比如"数据分析"和"数据
  可视化"会各自独立检索/生成，不会互相复用）；报告的"新鲜度"判断
  （`reports_needing_refresh()`）也还没有把"引用的外部资讯是否过时"
  纳入触发条件；月度复盘也还没有接入"报告质量趋势"（比如 too_generic
  比例是不是在上升）——这几项都是
  `next_doc/growth_advisor_research_quality_plan.md` 明确标注的
  非目标，留给后续单独排期。
