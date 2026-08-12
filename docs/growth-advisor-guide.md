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

## 1.5 设计理念（后续改动的前提，务必先读）

> 这一节记录的是与用户明确讨论、确认过的产品定位，**不是某一次迭代的
> 实现细节，而是判断"下一步该怎么改"的前提**——后续任何对成长顾问的
> 改进，都应该先对照这里的理念检验方向对不对，而不是只看"这一步技术
> 上能不能做"。如果某次改动会让机制偏离这个定位（比如又变回"每一步都
> 要人工衔接"），应该被视为回退，而不是一个可选的实现方式。

**核心定位**：成长顾问应该是**自主的**——

1. **自主根据用户需求，规划成长方向**：不是等用户明确提出"我想学 X"
   才动，而是从用户与 Agent 的历史交互里主动发现反复出现、值得投入的
   方向，主动给出候选（信号扫描 → 候选生成，见 2.1 节）。
2. **在用户选择的方向上，自主、持续地收集整理素材**：一旦用户认可了
   某个方向，成长顾问应该**自己接着往下推进**——不断收集、整理相关
   素材，提供给用户学习成长，而不是"生成一份报告就结束了，后面每一步
   都需要用户再手动点一下才会继续"。

**这个定位对具体设计的约束**（判断新功能/改动是否符合定位时可以对照）：

- **"采纳"应该是一个起点，而不是终点**。用户对一个方向表达认可之后，
  系统应该把这当作"可以开始自主推进了"的信号，而不是仅仅记一条反馈、
  然后停在原地等用户发出下一条明确指令。2.12 节的"采纳即启动"就是
  这条理念在当前版本的落地：默认把"采纳"和"开始持续调研"绑在一起，
  而不是让两者成为需要用户分别触发的两个独立动作。
- **"持续"意味着不能在原地打转**。每一轮推进都应该在上一轮的基础上
  往深/往新走——避免重复讲已经讲过的内容、避免用不同措辞重写同一份
  素材。这要求执行规范/模板层面有专门的"增量"约束和"已覆盖话题"记忆
  （见 2.12 节 growth_pursuit 模板的 handoff_fields 设计），而不能只
  套用通用的"随手调研一下"模板。
- **素材要能沉淀成用户可以直接拿去学习的东西**，而不是散落的、互不
  衔接的多份一次性报告。倾向于让同一个方向的素材汇聚到同一份持续更新
  的页面里，带来源标注，而不是每跑一轮就新开一份文件。
- **自主不等于替用户做主**。"自主"体现在"认可之后系统会自己接着做
  什么"，不体现在"系统可以不经用户同意就开始一个新方向"或者"用户
  想暂停/调整时做不到"——信号扫描/候选生成阶段仍然只是建议，采纳与否
  始终由用户决定（这条是整个机制从 P1 就有的底线，"自主持续调研"不
  改变这条底线，只是把"用户已经做出的选择"更彻底地执行下去）。用户
  应该随时能看到"哪些方向正在被自主推进"、随时能暂停或调整，而不是
  自主变成了一个用户看不见、也停不下来的黑箱。
- **成本/打扰节制的既有克制哲学（第 8 节 / 3 节"默认行为速览"）仍然
  适用**——"自主持续"不等于"无限制地花 LLM 成本/无限制地打扰用户"。
  新增的自主环节应该延续"默认给用户零成本的基础体验，增强能力
  opt-in"这条一贯的设计取舍（`auto_pursue_on_accept` 是这条原则下
  少数几个默认开启的例外，因为它本身就是"自主"这个核心定位的直接
  体现，而不是一个可有可无的增强）。

**后续改进时可以自问的几个问题**：

- 这个改动让"用户需要手动点一下才会继续"的环节变多了还是变少了？
- 每一轮新产出的素材，相比上一轮是真的有实质性推进，还是在换一种
  说法重复已有内容？
- 用户能不能在看板/CLI 上一眼看出"哪些方向正在被自主推进、进展到
  哪一步了"？（2.9 节末尾、2.12 节提到的"看板要能看到正在被推进的
  方向"是这条理念尚未完全做到的部分，后续如果要继续投入，应该优先
  往这个方向补，而不是继续叠加新的手动衔接点）
- 这个改动是不是又把某个原本自动衔接的步骤，重新变成了需要用户单独
  触发的动作？如果是，需要想清楚为什么这次要开这个例外。

> 对照这条理念梳理出的具体改进方向（增量质量校验、饱和度信号、看板
> 可见性等）见 `next_doc/growth_advisor_autonomy_deepening_plan.md`——
> 已识别但尚未实施，供后续按需认领。

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
走既有的 Goal 管理命令即可，成长顾问不代管 Goal 的生命周期。**看板/
API 入口（本次新增）**：`POST /v1/growth/candidates/{id}/adopt_goal`
（`client.growth_candidate_adopt_goal()`），"📄 查看调研报告"折叠区
里"🚀 落地为 Goal（继续调研）"按钮，此前这一步只有 CLI 能做，看板上
"采纳"了一个方向之后除了改个状态字段，没有任何入口能让成长顾问真的
"接着在这个方向上继续调研、收集素材"——用户体感上就是"采纳了但系统
什么都没做"，本质是"采纳"（`accept`，只是反馈信号）和"落地推进"
（`adopt_candidate_as_goal`，真正开始收集素材）是两个不同的动作，
此前只有前者暴露在看板里。

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

### 2.12 采纳即启动：从"每一步都要人工衔接"到"自主持续调研"（本次新增）

> 与用户讨论后的方向性改动：成长顾问的定位应该是"自主根据用户需求
> 规划成长方向，并在用户选择的方向上自主、不断地收集整理素材"，而
> 不是"信号扫描之后每一步都要人工点一下的流水线"。

**此前的问题**：2.9 节的"一键落地"虽然把"落地成 Goal"这一步接进了
看板，但完整链路仍然是四段全部手动衔接：

```
信号扫描 → 候选 → [人工采纳] → [人工"落地为Goal"] → [人工设周期性] → [人工生成执行规范并确认]
```

任何一步没做，链条就断在那——用户点了"✅ 采纳"之后，系统除了改一个
状态字段，什么都不会继续做，体感上就是"采纳了但什么都没发生"。

**现在的行为**：`GrowthAdvisorConfig.auto_pursue_on_accept`（**默认
开启**，是本次改动里少数默认开启而非 opt-in 的开关之一——"采纳"这个
动作本身现在就等价于"开始持续调研"，对齐上面的定位）。用户点
"✅ 采纳"（或看板拖拽到"已采纳"列）时，`auto_pursue_candidate()` 自动
依次完成：

1. **候选没有调研报告** → 先用零成本规则模板生成一份，保证这条自动
   路径不强依赖 LLM 也能跑通；
2. **落地为 Goal**（复用 2.9 节的 `adopt_candidate_as_goal()`；已经
   落地过的候选直接复用已有 `linked_goal_id`，不会重复建 Goal）；
3. **生成并直接确认一版执行规范**，使用新增的专用模板
   `growth_pursuit`（而不是通用的 `research_exploration`——后者是给
   "随手调研一下"用的最简骨架，不懂"持续深化同一个方向"这个场景的
   特殊性）：
   - 每一轮的产出物是同一份 `wiki/growth/<topic>.md`，**追加**新的、
     带来源标注的内容块，而不是每轮各生成一份互不衔接的新报告——
     久而久之这份页面本身就是一份可以直接拿去学习的素材库；
   - `handoff_fields` 包含 `covered_subtopics`（已经讲过的子话题，
     下一轮据此避免重复）、`open_questions`（上一轮留下但没展开的
     方向，下一轮优先从这里推进）、`last_source_urls`（引用来源
     去重）；
   - `per_cycle_criteria` 要求"本轮必须比上一轮有实质性增量"，而不是
     用不同措辞重写已有内容；
   - 执行规范生成失败（比如 LLM 暂不可用）不会中断整条链路，只是这个
     Goal 暂时沿用通用行为，后续可以在「🎯 目标」tab 手动补一份；
4. **绑定周期性**，调度节奏由 `GrowthAdvisorConfig.auto_pursue_
   schedule` 控制，**默认 `interval:86400`（每天一轮）**——与用户
   确认过的默认节奏一致。已经绑定过的话复用已有 cron job，不会重复
   创建。

四步中任一后续步骤失败都不影响前面已经成功的部分（比如"生成报告"
成功但"绑定周期性"失败时，Goal 仍然是已创建状态），失败信息通过
`accept` 接口响应体的 `pursuit.errors` 字段返回，看板据此用
`st.toast` 尽力而为地提示用户，不会让整个"采纳"动作因为某一个后续
子步骤出错就跟着报错。

**用户仍然拥有控制权**：

- 自动绑定的周期性执行可以随时在「🎯 目标」tab 里 `unrecur`（暂停），
  不会被自动重新绑定；
- 想关掉"采纳即启动"、回到此前"每一步手动确认"的行为，把
  `auto_pursue_on_accept` 设为 `False` 即可——此时"✅ 采纳"退回成
  单纯的反馈信号，2.9 节的"🚀 落地为 Goal（继续调研）"按钮继续按
  原有方式手动使用；
- 调度节奏（`auto_pursue_schedule`）和执行规范模板
  （`auto_pursue_template_id`）都可以在配置里覆盖，不强绑固定值。

**API**：`POST /v1/growth/candidates/{id}/accept` 的响应体新增
`pursuit` 字段：

```jsonc
{
  "ok": true,
  "candidate": { "...": "...", "linked_goal_id": "..." },
  "pursuit": {
    "goal": { "id": "...", "title": "..." },
    "cron_job": { "id": "...", "schedule": "interval:86400" },
    "report_generated": true,
    "errors": []
  }
}
```

`auto_pursue_on_accept=False` 时不会出现 `pursuit` 字段，行为与
此前完全一致。

**新增/变更文件**：

- `src/mini_agent/perception/goal_execution_spec_templates/growth_pursuit.json`
  （新增模板）；
- `src/mini_agent/config/models.py`：`GrowthAdvisorConfig` 新增
  `auto_pursue_on_accept` / `auto_pursue_schedule` /
  `auto_pursue_template_id` 三个字段；
- `src/mini_agent/evolution/growth_advisor.py`：新增
  `auto_pursue_candidate()`；
- `src/mini_agent/api/routes.py`：`post_growth_candidate_action()`
  的 `accept` 分支接入自动链路；
- `apps/mini_agent_kanban/client.py`：`growth_candidate_action()`
  的 `accept` 超时放宽到 90s（自动链路可能含 LLM 调用）；
- `apps/mini_agent_kanban/app.py`：列表视图/拖拽视图的"✅ 采纳"按钮
  展示 `pursuit` 结果的 toast 提示；报告查看折叠区的"已落地为 Goal"
  提示文案更新为"正在自主持续调研"。

### 2.13 落地 `growth_advisor_autonomy_deepening_plan.md`：A1 / A2 / B1 / B2 / D1 / D2（本次新增）

在 2.12 节"采纳即启动"之后，按 `next_doc/growth_advisor_autonomy_
deepening_plan.md` 的优先级建议（该文档第 6 节），实现了其中六个
方向。B3/C1/C2/A3 仍按方案文档标注的理由暂缓（B3/A3 工作量和收益
不确定，C1/C2 优先级低于先保证增量质量和可见性），保留在方案文档里
供后续按需认领。

**D1 + D2：看板可见性 + 就近控制**（方案文档"投入产出比最高"的一项）

- 新增只读聚合端点 `GET /v1/growth/pursuits`：跨 `GrowthBacklog` +
  `GoalBacklog` + `CronScheduler` + `growth_state.json` 四个既有
  数据源拼装，不新增持久化。返回每个"已采纳且关联了 Goal"的候选的
  周期性执行状态（第几轮、下次执行时间、cron job 是否启用）和饱和度
  信号（见下面 B2）。
- 看板成长顾问 tab 新增"🔄 正在自主推进"分区（`_render_growth_
  pursuits()`），列出全部处于自主持续调研状态的方向，每条直接带
  "⏸ 暂停"/"▶ 恢复"按钮（复用已有的 `unrecur_goal()`/`recur_goal()`，
  没有新增后端能力）和一个"📄 素材"入口——用户不需要跳到「🎯 目标」
  tab、也不需要理解"这背后是一个 Goal + 一个 cron job"，操作路径就近
  收在成长顾问自己的界面里。

**B1：增量质量的自动规则式初筛**

- 新增 `growth_advisor.evaluate_cycle_increment(paths, goal_id)`：
  读该 Goal 最近两轮的 manifest，从 `progress_note` 里的
  ```handoff``` 块取出 `covered_subtopics`，算本轮相对上一轮的新增
  子话题占比。占比过低（默认阈值 60% 重叠）判定"疑似低增量"。纯规则
  式（集合差集），零 LLM 成本，只读、不阻断任何流程——方案文档里
  "LLM 复核"这个可选的第二步（对被规则式标记的轮次再做一次语义级
  判断）本轮未实现，留在方案文档里作为后续可选增强。
- 轮次不足（少于 2 轮）或本轮 handoff 没有提供 `covered_subtopics`
  时，明确返回 `evaluated=False`，不会被误判成"低增量"。

**B2：饱和度信号**

- 新增 `growth_advisor.record_pursuit_cycle_signal()` /
  `get_pursuit_saturation()`：把"连续低增量轮次"计数存进
  `growth_state.json` 的 `pursuit_saturation` 子字典（按 `goal_id`
  分桶，不为这个信号单独开一份持久化文件）。连续达到阈值（默认 3
  轮）判定"疑似饱和"；不再低增量时计数归零，同一次饱和状态只提示
  一次（不重复打扰），归零后重新累计满阈值会触发新一轮提示。
- 新增 `growth_advisor.process_pursuit_cycle_completion(paths, goal)`
  把"算增量 → 记饱和度计数 → 刚跨过阈值时给出建议文案"串起来，只处理
  打了 `growth_advisor` 标签的 Goal，其余 Goal 直接跳过。
- 接入点：`goal_cron_bridge.reap_finished_cycles()` 里一轮子
  Objective 以 `completed` 收尾时，顺带调用新增的
  `_check_pursuit_saturation()`——刚判定饱和会通过既有通知网关
  （`notification/dispatcher.py`）推一条"最近几轮新增内容不多了，
  要不要降频/先告一段落"的提示。**只是提示，不自动降频或停止**，
  对齐 1.5 节"自主不等于替用户做主"——异常整体吞掉，不影响
  `reap_finished_cycles()` 的计数主流程。
- 看板"🔄 正在自主推进"分区里，饱和的方向会带一条 `st.warning` 提示，
  跟 D1 展示的执行状态在同一处呈现。

**A1：report/refresh 与 Goal 周期性并轨**

- `growth_advisor.reports_needing_refresh()` 新增可选参数
  `goal_backlog`：传入时，已经落地成 Goal 且 `recurring=True` 的
  候选会被跳过——它的素材已经由 `growth_pursuit` 周期性执行接管，
  不再需要"报告刷新"这条独立路径继续提示。不传（默认 `None`）时
  行为与改动前完全一致，向后兼容所有既有调用方。
- `GET /v1/growth/reports/refresh_candidates` 路由已经改为传入
  `goal_backlog`（拿不到 `GoalBacklog` 时优雅退化成不过滤）。

**A2：Goal 停滞时先区分原因，再决定怎么问**

- `followup_question_hint()` 对已绑定周期性执行的 Goal（`recurring=
  True`）区分两类停滞原因，措辞不再一律是"要不要先放一放"：
  - 命中 B2 饱和度信号（`get_pursuit_saturation().saturated`）→
    判定是"素材讲得差不多了"，问法沿用 B2 通知里的措辞（"最近 N 轮
    新增内容不多了，是已经了解得差不多，还是希望换个角度继续？"）；
  - 没有命中饱和度信号但 Goal 仍然判定停滞 → 更可能是执行本身没有
    真正跑起来（cron 被跳过/失败，或者当初绑定就没成功），措辞改为
    "看起来有一阵没真正推进——更像是执行环节遇到了问题，建议去
    「🎯 目标」tab 看一眼执行状态，而不是这个方向本身不值得继续"，
    避免用户把"系统的问题"误解成"我不想继续了"。
  - 一次性（非 recurring）Goal 停滞的语义跟"自主持续调研"场景不同，
    继续走原有措辞，不受这次改动影响。
- 这一步**只是措辞层面的自诊断/区分，不包含自动重试或自动修复**——
  方案文档 A2 提到的"能自愈的自愈"这部分（比如自动检测 cron job
  被 disable 并尝试重新绑定）本轮未实现，仍然需要用户去「🎯 目标」
  tab 手动确认/处理，留作后续可能的增强。

**新增/变更文件**：

- `src/mini_agent/evolution/growth_advisor.py`：新增
  `evaluate_cycle_increment()` / `record_pursuit_cycle_signal()` /
  `get_pursuit_saturation()` / `process_pursuit_cycle_completion()`；
  `reports_needing_refresh()` 新增 `goal_backlog` 参数；
  `followup_question_hint()` 对已绑定周期性执行的 Goal 区分"饱和"vs
  "执行卡住"两类停滞措辞（见下面 A2）；
- `src/mini_agent/evolution/goal_cron_bridge.py`：
  `reap_finished_cycles()` 一轮成功完成时接入
  `_check_pursuit_saturation()`；
- `src/mini_agent/api/routes.py`：新增
  `GET /v1/growth/pursuits`；`refresh_candidates` 路由改为传入
  `goal_backlog`；
- `apps/mini_agent_kanban/client.py`：新增 `growth_pursuits()`；
- `apps/mini_agent_kanban/app.py`：新增 `_render_growth_pursuits()`
  并接入 `render_growth_tab()`。

### 2.14 落地 `growth_advisor_autonomy_deepening_plan.md`：C1 / C2（本次新增）

在 2.13 节落地 A1/A2/B1/B2/D1/D2 之后，按方案文档第 6 节排在其后的
C1（定期整理）/ C2（新增摘要推送）也一并实施。A3/B3 仍按方案文档标注
的理由暂缓（工作量和收益都有较大不确定性，不承诺进入下一轮实施范围），
保留在方案文档里供后续按需认领。

**C1：定期整理，从"线性追加"到"顺带重新组织"**

- 新增 `growth_advisor.reorganize_hint_for_cycle(goal, cycle_no, cfg)`：
  纯规则式判断（轮次号对 `cfg.reorganize_every_n_cycles` 取模，默认
  10 轮，配成 0 或负数视为关闭），零 LLM 成本、不读取任何执行历史。
  只对打了 `growth_advisor` 标签的 Goal 生效。
- 接入点：`goal_cron_bridge.register_goal_cycle_handler()` 触发每一轮
  子 Objective 时，新增 `_append_execution_spec_context()` 之后的一步
  `_append_growth_reorganize_hint()`——累计满 N 轮的那一轮，会在拼给
  模型的 description 末尾追加一段"这一轮先花点时间合并重复表述、按
  子话题分节、把 handoff.open_questions 里已解决的问题移出，再继续
  本轮新增部分"的提示。仍然是同一个执行循环里的一种特殊模式，没有
  新增独立的 cron job 或数据结构，只是这一轮的 prompt 多了一段说明；
  是否真的需要整理由模型在执行时自行判断，不代表这一轮的产出会因此
  被强制要求包含整理内容（`per_cycle_criteria` 仍然是既有的
  `manual_review`，不新增自动校验）。
- 任何环节异常（拿不到配置/生成提示失败）都静默跳过，不影响 Goal
  触发主流程。

**C2：本轮新增摘要，复用已有推送节流，不额外消耗额度**

- 新增 `growth_advisor.record_pursuit_cycle_digest(paths, goal, cfg)`：
  一轮成功完成时，从最近两轮 manifest 的 handoff 里算出本轮新增的
  `covered_subtopics` 差集，整理成一条"本轮新增摘要"，存进
  `growth_state.json` 的 `pending_pursuit_digests` 队列（不为这个
  信号单独开一份持久化文件，复用 B2 的既有取舍）；没有新增子话题或
  没有可比较的 handoff 数据时不落任何记录。`cfg.pursuit_digest_
  enabled=False`（默认 `True`）时整体跳过。队列超过 30 条自动丢弃
  最旧的——这是一份待展示摘要，不是审计日志。
- 接入点：`goal_cron_bridge.reap_finished_cycles()` 一轮子 Objective
  以 `completed` 收尾、且是打了 `growth_advisor` 标签的 Goal 时，
  紧跟在 B2 的 `_check_pursuit_saturation()` 之后调用新增的
  `_record_pursuit_digest()`。
- **真正推送时才打包带出，不新增一套独立的通知逻辑**：
  `_maybe_dispatch_notification()`（`notification_frequency=daily`）
  和 `_maybe_dispatch_weekly_digest()`（`notification_frequency=
  weekly_digest`）在确实要发出一条消息时，各自调用新增的
  `_pop_pending_pursuit_digest_lines()`（取出并清空队列）把摘要行拼进
  同一条消息正文——不单独触发一次推送，也不占用
  `notification_max_per_day` 的额外额度；`notification_frequency=
  kanban_only` 时两条推送路径都不会触发，摘要会持续在队列里累积，
  直到用户切换回 daily/weekly_digest 或直接在看板查看。
- 看板可见性：新增只读函数 `growth_advisor.peek_pending_pursuit_
  digests()`（不清空队列），`GET /v1/growth/pursuits` 每条记录新增
  `pending_digest` 字段；看板"🔄 正在自主推进"分区（D1）每条方向下面
  新增一行 `🆕 本轮新增：...` 展示还没被推送出去的最新进展，不需要
  等到下一次日报/周报才看到。

**新增/变更文件**：

- `src/mini_agent/config/models.py`：`GrowthAdvisorConfig` 新增
  `reorganize_every_n_cycles`（默认 10）/ `pursuit_digest_enabled`
  （默认 `True`）两个字段；
- `src/mini_agent/evolution/growth_advisor.py`：新增
  `reorganize_hint_for_cycle()` / `record_pursuit_cycle_digest()` /
  `_pop_pending_pursuit_digest_lines()` / `peek_pending_pursuit_
  digests()`；`_maybe_dispatch_notification()` /
  `_maybe_dispatch_weekly_digest()` 在实际推送时打包摘要；
- `src/mini_agent/evolution/goal_cron_bridge.py`：新增
  `_append_growth_reorganize_hint()`（接入
  `register_goal_cycle_handler()`）与 `_record_pursuit_digest()`
  （接入 `reap_finished_cycles()`）；
- `src/mini_agent/api/routes.py`：`GET /v1/growth/pursuits` 每条记录
  新增 `pending_digest` 字段；
- `apps/mini_agent_kanban/app.py`：`_render_growth_pursuits()`
  每条方向新增"🆕 本轮新增"展示。

### 2.15 落地 `growth_advisor_autonomy_deepening_plan.md`：A3（本次新增）

在 2.14 节落地 C1/C2 之后，按方案文档第 6 节排在最后一批的 A3（对齐
分析结果批量落地）也一并实施。B3（跨主题去重/关联）仍按方案文档标注
的理由暂缓——明确留待后续单独评估候选规模是否值得投入，不属于这一轮
的实施范围。

**A3：对齐分析结果支持批量落地**

- 新增 `growth_advisor.batch_adopt_unmatched_interests()`：对
  `goal_growth_alignment()` 找出的"有兴趣信号但没建目标"列表，逐条
  复用已有的 `auto_pursue_candidate()`（生成报告 → 落地成 Goal → 生成
  并确认执行规范 → 绑定周期性）。只处理列表中已经有对应候选记录
  （`candidate_id` 非空）的条目——纯 focus_areas 信号但还没走到候选
  生成这一步的条目无法直接采纳，原样跳过、计入 `skipped`，不报错，
  提示"先走一轮 /growth scan 生成候选"。
- **节流**：新增配置 `goal_alignment_adopt_all_max_batch`（默认 3），
  单次最多处理这么多条（按 `evidence_count` 降序，跟
  `goal_growth_alignment()` 返回顺序一致，不重新排序），避免"批量"
  变成一次意外的成本爆炸（一次性触发多个"生成报告 + 生成执行规范"的
  LLM 调用）。未处理到的条目通过 `remaining_count` 告知调用方，下次
  再调用会继续出现在列表里，不会丢失。
- **CLI**：`/growth align --adopt-all`——在原有 `/growth align` 只读
  展示的基础上新增这个子命令，逐条打印落地结果（成功 → 目标 id；
  失败 → 具体原因），并在还有剩余条目时提示"可再次执行继续"。
- **API**：新增 `GET /v1/growth/align`（`goal_growth_alignment()` 的
  只读端点，此前只有 CLI 能查看，现在看板也能拉取）和
  `POST /v1/growth/align/adopt_all`（批量落地，内部复用同一个节流
  逻辑）。
- **看板**：成长顾问 tab 新增"🧭 有兴趣但还没建目标"折叠区，列出全部
  未匹配方向（标注是否有候选记录、能不能批量落地），带一个"🚀 全部
  采纳"按钮，点击后逐条 toast 反馈结果，剩余条目会提示"可再次点击
  继续"。

**新增/变更文件**：

- `src/mini_agent/config/models.py`：`GrowthAdvisorConfig` 新增
  `goal_alignment_adopt_all_max_batch`（默认 3）；
- `src/mini_agent/evolution/growth_advisor.py`：新增
  `batch_adopt_unmatched_interests()`；
- `src/mini_agent/cli/commands/growth_cmd.py`：`/growth align` 新增
  `--adopt-all` 子选项；
- `src/mini_agent/api/routes.py`：新增 `GET /v1/growth/align` 与
  `POST /v1/growth/align/adopt_all`；
- `apps/mini_agent_kanban/client.py`：新增 `growth_align()` /
  `growth_align_adopt_all()`；
- `apps/mini_agent_kanban/app.py`：新增 `_render_growth_alignment()`
  并接入 `render_growth_tab()`。

### 2.16 落地 `growth_advisor_autonomy_deepening_plan_v2.md`：方向 4（remaining_topics）/ 方向 5（批量暂停/调频）（本次新增）

v1 九个方向落地后，二次审视实现细节又识别出五处"有了但不够"的打磨点
（详见 v2 方案文档），按其第 6 节的优先级排序，先落地成本最低的两项：

**方向 4：A3 批量落地补充 `remaining_topics`**

- **现状问题**：`batch_adopt_unmatched_interests()` 每次调用都会重新跑
  一遍 `goal_growth_alignment()`，如果两次调用之间发生了新的信号扫描，
  `unmatched_interests` 的 `evidence_count` 排序可能变化——原本只返回
  一个 `remaining_count` 数字，用户不知道具体是哪几个方向还没处理。
- **改动**：`batch_adopt_unmatched_interests()` 返回值新增
  `remaining_topics: list[str]`——本次调用结束时仍待处理的 topic 名称
  列表（按本次返回时的 `evidence_count` 降序）。这只是让"还剩哪些"对
  用户可见，不引入新的状态持久化，也不改变实际处理顺序（顺序仍由下一
  次调用时的最新排序决定），对应方案文档"方案一（更简单）"的选择，
  未采用需要额外持久化"批量操作进度快照"的方案二。
- **CLI**：`/growth align --adopt-all` 在提示"还有 N 条未处理"之后，
  追加一行"待处理：<topic1>、<topic2>..."。
- **看板**："🚀 全部采纳"按钮点击后，剩余提示同样附上具体方向名称。

**方向 5：看板新增"批量暂停 / 批量调频"入口**

- **现状问题**：2.13 节 D2 做到了单个方向的"⏸ 暂停"/"▶ 恢复"，但同时
  有多个方向在自主推进时（比如要出差一段时间），只能逐个点，跟
  "就近控制、不用理解 Goal/Cron 内部机制"的理念有落差。
- **改动**：成长顾问 tab"🔄 正在自主推进"分区新增"⚙ 批量操作"入口
  （`st.popover`），提供两个动作：
  - **全部暂停**：对列表里全部方向依次调用 `unrecur_goal()`（跟单条
    "⏸ 暂停"完全同一个后端能力，只是循环调用）；
  - **全部调整频率**：提供"每天/每周"选择，对列表里全部方向依次调用
    `recur_goal()` 传入新的 schedule。
  两个动作都要求用户显式点击触发，不新增任何"系统自动决定暂停/降频"
  的逻辑，对齐 1.5 节"自主不等于替用户做主"。**不提供"全部恢复"**——
  按方案文档的判断，批量恢复的使用场景比批量暂停少见得多（恢复往往
  是回来后逐条重新评估"这个方向还要不要继续"），如果后续用户反馈确实
  需要再补。
- 没有新增后端接口——完全复用 D2 已有的 `stop_goal_recurrence()` /
  `make_goal_recurring()`，纯粹是看板侧循环调用 + 一个确认性质的批量
  提示（"将对全部 N 个正在自主推进的方向生效"）。

v2 方案文档其余两项（方向 1：B1 LLM 复核；方向 3：饱和度信号历史趋势）
仍按方案文档第 6 节的优先级排序留待后续实施，方向 2（对齐分析 LLM
建议一键确认）已在 2.17 节落地。

**新增/变更文件**：

- `src/mini_agent/evolution/growth_advisor.py`：
  `batch_adopt_unmatched_interests()` 返回值新增 `remaining_topics`；
- `src/mini_agent/cli/commands/growth_cmd.py`：`/growth align
  --adopt-all` 打印 `remaining_topics`；
- `apps/mini_agent_kanban/app.py`：`_render_growth_alignment()` 展示
  `remaining_topics`；`_render_growth_pursuits()` 新增"⚙ 批量操作"
  入口（批量暂停 / 批量调整频率）；
- `tests/test_growth_advisor_goal_cron_integration.py`：新增
  `TestBatchAdoptRemainingTopics`，覆盖 `remaining_topics` 在"部分
  处理"和"全部处理完"两种场景下的行为。

### 2.17 落地 `growth_advisor_autonomy_deepening_plan_v2.md`：方向 2（对齐分析 LLM 建议一键确认）（本次新增）

**现状问题**：`goal_alignment_llm_enabled=True` 时，`goal_growth_
alignment()` 会额外对"规则没匹配上的兴趣方向"和"规则没匹配上的
Goal"做一次语义匹配，结果放进 `llm_suggested_matches`（比如兴趣叫
"数据分析能力"、Goal 叫"提升可视化技能"）。这份建议此前只停留在
"展示给你看"——CLI/看板都能看到建议列表，但没有任何"确认这条建议、
正式关联"的入口，要正式关联只能走 `/growth adopt-goal`（新建一个
Goal，跟建议的意思不一样）或手动改标题让关键词匹配上。

**改动**：

- 新增 `growth_advisor.confirm_llm_suggested_match(paths, topic,
  goal_id, goal_backlog=None)`：找到 `topic` 对应的候选记录（按
  `dedupe_key()` 或标题原文匹配），把它的 `linked_goal_id` 指向
  `goal_id`（复用 `GrowthBacklog.set_linked_goal()`，不新建 Goal）。
  `topic` 没有对应候选记录、或 `goal_id` 在 `goal_backlog` 里找不到
  对应节点时，都返回 `{"ok": False, "reason": ...}`，不抛异常。
- **CLI**：`/growth align --confirm-match "<兴趣方向>" <goal_id>`——
  `/growth align` 展示 `llm_suggested_matches` 时，每条附带对应的
  确认命令，方便直接复制执行。
- **API**：新增 `POST /v1/growth/align/confirm_match`（请求体
  `{"topic": str, "goal_id": str}`）。
- **看板**："🧭 有兴趣但还没建目标"折叠区新增"🔗 语义相关的建议"子
  分区，列出 `llm_suggested_matches`，每条带一个"🔗 关联"按钮，点击
  即调用确认接口并 toast 反馈结果。
- 这条改进依赖 `goal_alignment_llm_enabled` 已经打开（默认关闭），
  跟既有的"新增能力默认 opt-in"取舍一致——建议本身不常出现，确认入口
  的收益也主要在打开这个开关的用户身上。

**新增/变更文件**：

- `src/mini_agent/evolution/growth_advisor.py`：新增
  `confirm_llm_suggested_match()`；
- `src/mini_agent/cli/commands/growth_cmd.py`：`/growth align` 新增
  `--confirm-match` 子选项；
- `src/mini_agent/api/routes.py`：新增
  `POST /v1/growth/align/confirm_match`；
- `apps/mini_agent_kanban/client.py`：新增
  `growth_align_confirm_match()`；
- `apps/mini_agent_kanban/app.py`：`_render_growth_alignment()` 新增
  "🔗 语义相关的建议"子分区；
- `tests/test_growth_advisor_goal_cron_integration.py`：新增
  `TestConfirmLlmSuggestedMatch`，覆盖成功关联、候选缺失、Goal 缺失
  三种场景。

### 2.18 落地 `growth_advisor_autonomy_deepening_plan_v2.md`：方向 3（饱和度信号历史趋势）（本次新增）

**现状问题**：`get_pursuit_saturation()`（2.13 节 B2）只返回某个 Goal
**当前**的 streak/saturated 状态，`pursuit_saturation` 在
`growth_state.json` 里也只存最新一条，不是时间序列——看不出"这个方向
饱和之后，用户听了建议真的降频了吗？降频之后新增内容是不是又回升了？
还是说不管频率怎么调都一直低增量"。

**改动**：

- 新增只追加文件 `growth_pursuit_saturation_trend.jsonl`
  （`AgentPaths.growth_pursuit_saturation_trend_path`），复用 v4 N1
  健康度趋势（`_record_health_snapshot()` / `compact_health_trend_
  storage()`）已经验证过的"按天降采样、旧数据自动压缩"模式——新增
  `_compact_pursuit_saturation_trend_rows()` / `compact_pursuit_
  saturation_trend_storage()`，按 `(goal_id, 天)` 分桶压缩，跟
  `growth_health_trend.jsonl` 是平行但独立的文件（不复用同一份文件，
  因为这里的记录天然按 goal_id 分桶，混在一份全局文件里查询反而更
  麻烦）。压缩调用接在 `run_daily_cycle()` 尾部，跟健康度趋势同一个
  节奏，不需要单独的调度点。
- `record_pursuit_cycle_signal()` 在更新 `pursuit_saturation` 当前
  状态的同时，顺带向这份文件追加一条记录（`goal_id`/`recorded_at`/
  `low_increment`/`streak`/`saturated`）——追加失败只是少一条趋势
  记录，不影响 streak 计数本身的返回值，对齐"诊断增强不影响主流程"
  的既有取舍。
- 新增只读函数 `get_pursuit_saturation_trend(paths, goal_id,
  limit=30)`，返回某个 Goal 最近若干轮"是否低增量"的时间序列，按
  时间正序。
- **API**：新增 `GET /v1/growth/pursuits/{goal_id}/saturation_trend`
  （按需拉取，不放进 `/growth/pursuits` 默认响应，避免每次打开 tab
  都拉取历史数据，跟 `/growth/health_trend` 的调用契约一致）。
- **看板**："🔄 正在自主推进"分区每条方向新增"📈 饱和度走势"折叠区，
  用 🟢/🔴 两种颜色的紧凑记号展示最近若干轮是否低增量，不引入图表库
  （风格延续 D1 已有的"证据数走势"箭头展示）。
- 成本可控：只是多写一条降采样记录，不产生新的 LLM 调用或额外的
  Goal 触发；本轮**不涉及**方案文档提到的"用趋势数据判断要不要重新
  触发回访卡片"这个后续判断点——那是在有了真实趋势数据之后才能评估
  的下一步，留给后续视实际情况决定。

v2 方案文档最后一项（方向 1：B1 LLM 复核）见下面 2.19 节，至此
`growth_advisor_autonomy_deepening_plan_v2.md` 五个方向全部落地。

**新增/变更文件**：

- `src/mini_agent/storage/paths.py`：新增
  `growth_pursuit_saturation_trend_path`；
- `src/mini_agent/evolution/growth_advisor.py`：
  `record_pursuit_cycle_signal()` 顺带追加趋势记录；新增
  `_compact_pursuit_saturation_trend_rows()` /
  `compact_pursuit_saturation_trend_storage()` /
  `get_pursuit_saturation_trend()`；`run_daily_cycle()` 尾部接入
  压缩调用；
- `src/mini_agent/api/routes.py`：新增
  `GET /v1/growth/pursuits/{goal_id}/saturation_trend`；
- `apps/mini_agent_kanban/client.py`：新增
  `growth_pursuit_saturation_trend()`；
- `apps/mini_agent_kanban/app.py`：`_render_growth_pursuits()` 每条
  方向新增"📈 饱和度走势"折叠区；
- `tests/test_growth_advisor_saturation_and_pursuit_visibility.py`：
  新增 `TestPursuitSaturationTrend`，覆盖趋势累积、按 goal_id 隔离、
  未记录时为空、压缩函数在无旧数据时为空操作四种场景。

### 2.19 落地 `growth_advisor_autonomy_deepening_plan_v2.md`：方向 1（B1 增量质量校验的 LLM 复核）（本次新增）

**现状问题**：2.13 节 B1 的 `evaluate_cycle_increment()` 只做规则式
初筛——比对相邻两轮 `covered_subtopics` 的集合差集占比，重叠比例过高
就标记"疑似低增量"。规则式初筛只能发现"字面上没什么新词"，发现不了
"子话题标题凑巧重复、但内容其实已经往前推进了"这种更隐蔽的误判——
比如上一轮和本轮的子话题标题都叫"性能优化"，规则式判断会认为这是
100% 重叠，但本轮实际讨论的可能是完全不同的具体子问题。

**改动**：

- `evaluate_cycle_increment()` 新增可选参数 `llm_helper` / `llm_
  review_enabled`（默认 `None`/`False`，不改变既有调用方不传参数时
  的行为）：只在规则式初筛已经判定 `low_increment=True` 的轮次上，
  才追加一次 LLM 复核——不对每一轮都调用，维持"规则式路径优先、LLM
  增强作为 opt-in 补充"的既有取舍。
- 新增 `_llm_review_cycle_increment()`：只把上一轮/本轮/新增的子话题
  标题集合（不传完整正文）拼进 prompt，让 LLM 判断"这次重叠是不是
  真的在原地打转"，返回结构化 JSON（`has_real_progress` +
  一句话理由）。解析失败/空响应/异常统一走失败路径（记一次
  `pursuit_increment_review` 的 `error`/`parse_error`/
  `empty_response` 状态，复用既有的 `_record_llm_call_status()`
  三态诊断机制，不吞掉真实失败让复核看起来"默认通过"）。
- 复核结果放进返回值新增的三个字段——`llm_reviewed`（是否实际触发
  过复核）、`llm_verdict`（`True`=LLM 同意规则式判断确实低增量，
  `False`=LLM 认为其实有实质推进，`None`=未触发）、`llm_reason`
  （一句话理由）——**不覆盖** `low_increment` 本身。两种信号刻意
  分开记录：`record_pursuit_cycle_signal()` 的 streak 计数仍然只看
  规则式 `low_increment`，不会因为 LLM 复核结果而改变计数口径，
  避免"规则说低增量、LLM 说不是"被静默合并成一个结论——这一点是
  方案文档明确要求的取舍，不是遗漏。
- `record_pursuit_cycle_signal()` 新增可选的 `llm_reviewed`/
  `llm_verdict`/`llm_reason` 参数，原样存进 `pursuit_saturation`
  当前状态快照（供 `get_pursuit_saturation()` 展示"最近一次"）并
  追加进 2.18 节已有的 `growth_pursuit_saturation_trend.jsonl`
  （复用同一份趋势文件，不新开一份存储）。不传这三个参数时行为与
  改动前完全一致（三个字段落盘为默认值），向后兼容所有既有调用方。
- 新增配置项 `growth_advisor.pursuit_increment_llm_review_enabled`
  （默认 `False`，opt-in——这是新增的 LLM 调用点，对齐"增加调用
  成本的能力默认关闭"的一贯原则）。`process_pursuit_cycle_completion()`
  新增 `llm_helper`/`cfg` 参数，读取这个开关决定要不要把 `llm_helper`
  透传给 `evaluate_cycle_increment()`。
- **接入点**：`goal_cron_bridge.reap_finished_cycles()` 新增可选的
  `llm_helper_provider` 参数（惰性 `Callable[[], Any]`，跟
  `tech_radar_search.py` 等 cron job 同一套约定），`_check_pursuit_
  saturation()` 内部加载 `cfg.growth_advisor` 并取一次 `llm_helper`
  透传下去；`AutonomousLoop` 新增同名构造参数，`api/server.py::
  _build_autonomous_loop()` 传入 `lambda: getattr(agent, "llm_
  helper", None)`（跟 `sys:tech_radar_search` 等既有 cron job 的
  `llm_helper_provider` 完全同一种惰性获取写法）。不传时（比如
  非 daemon 模式的测试路径）`evaluate_cycle_increment()` 拿不到
  `llm_helper`，复核这一步自动跳过，不影响主流程。
- **看板**：\"🔄 正在自主推进\"分区里，饱和警告下面只在
  `llm_reviewed=True` 时追加一行 `st.caption`——LLM 认为其实有实质
  推进时单独提示\"仅供参考，规则式判断不受影响\"，避免用户误以为
  规则式饱和结论已经被推翻；\"📈 饱和度走势\"折叠区里额外统计\"其中
  N 轮触发过 LLM 复核\"，具体理由仍以最新一条为准，不为每个历史点
  都展开详情（保持跟已有走势展示同样的\"不引入图表库、只做紧凑
  记号\"风格）。
- 任何异常整体吞掉，不影响 `reap_finished_cycles()` 的计数主流程——
  跟 B1/B2 落地时确立的取舍完全一致。

**新增/变更文件**：

- `src/mini_agent/config/models.py`：新增
  `GrowthAdvisorConfig.pursuit_increment_llm_review_enabled`；
- `src/mini_agent/evolution/growth_advisor.py`：
  `evaluate_cycle_increment()` 新增 `llm_helper`/`llm_review_enabled`
  参数及 `llm_reviewed`/`llm_verdict`/`llm_reason` 返回字段；新增
  `_llm_review_cycle_increment()`；`_LLM_CALL_TYPES` 新增
  `"pursuit_increment_review"`；`record_pursuit_cycle_signal()` 新增
  `llm_reviewed`/`llm_verdict`/`llm_reason` 参数并写入快照+趋势；
  `get_pursuit_saturation()`/`get_pursuit_saturation_trend()` 新增
  对应展示字段；`process_pursuit_cycle_completion()` 新增
  `llm_helper`/`cfg` 参数；
- `src/mini_agent/evolution/goal_cron_bridge.py`：
  `reap_finished_cycles()` / `_check_pursuit_saturation()` 新增
  `llm_helper_provider` 参数并透传 `cfg`；
- `src/mini_agent/evolution/autonomous_loop.py`：`AutonomousLoop`
  新增 `llm_helper_provider` 构造参数，`_tick_maintenance()` 透传给
  `reap_finished_cycles()`；
- `src/mini_agent/api/server.py`：`_build_autonomous_loop()` 传入
  `lambda: getattr(agent, "llm_helper", None)`；
- `apps/mini_agent_kanban/app.py`：`_render_growth_pursuits()` 饱和
  警告下追加 LLM 复核提示，走势折叠区追加复核轮次统计；
- `tests/test_growth_advisor_pursuit_increment_llm_review.py`：新增，
  覆盖默认关闭不触发调用、只在规则判定低增量时触发、LLM 同意/不同意
  两种结果都不覆盖规则式判断、streak 计数不受 LLM 结果影响、调用
  失败与响应解析失败的降级路径、`process_pursuit_cycle_completion()`
  按 `cfg` 开关决定是否透传 `llm_helper`、`reap_finished_cycles()`
  新签名可用等场景。

### 2.20 落地 `growth_advisor_ideal_advisor_gap_and_roadmap_plan.md`：方向 6（调研风格智能分类）（本次新增）

**现状问题**：无论是学一门技术、读一本理论书、还是培养一个习惯，
`growth_pursuit` 模板产出的方式完全相同——都是持续增厚的 wiki 页面，
没有区分"这类话题该怎么调研/呈现"。方案文档最初的建议是"先做用户
手动选择、暂不做自动判断"；后续与用户讨论后，直接跳过手动选择这一
中间态，做自动智能分类：规则式关键词匹配作为零成本默认路径（总是
可用），LLM 复核作为 opt-in 增强（默认关闭）。

**改动**：

- 新增 3 个调研风格标签：`技能实操类`/`知识理论类`/`习惯养成类`——
  跟 2.5 节的话题类别系统（技术类/管理类/表达类/其他类）是两个正交
  维度：类别回答"是什么话题"，风格回答"这类话题该怎么调研/呈现"。
- `_infer_pursuit_style_rule(topic, extra_text="")`：只登记"技能
  实操类"（编程/开发/工程/代码/api 等）和"习惯养成类"（习惯/打卡/
  坚持/作息/锻炼等）两类的高置信度关键词，命中数最多的胜出；全不
  命中或平局兜底"知识理论类"（读书笔记式持续调研是模板最初、也是
  最通用的产出形态，作为默认值最保守）。
- `classify_pursuit_style_llm()` / `determine_pursuit_style()`：跟
  2.5 节 `classify_topic_category_llm()` 同款"opt-in、宽松吸收"
  模式——规则式结果总是先算出来，`pursuit_style_llm_enabled=True`
  且有 `llm_helper` 时才额外调一次 LLM 复核，命中合法标签就覆盖，
  解析失败/异常/未开启都静默沿用规则式结果，不影响返回值可用性。
- `pursuit_style_hint()`：每种风格对应一段 prompt 追加指令（技能
  实操类多给可复现操作步骤/代码示例；知识理论类维护结构化知识
  脉络；习惯养成类以短小打卡式记录为主、不追求持续增厚知识库）。
  跟 2.14 节 C1/2.19 节不同，这里**每一轮都带上**（不按累计轮次
  取模触发）——风格是这个方向的持续属性，不是某个特定轮次才需要
  的提醒。
- **接入点**：`auto_pursue_candidate()` 落地成 Goal 之后，若尚未
  分类过（避免每次自动推进都重算），判定一次并写入 `GoalNode.
  growth_pursuit_style` 新字段；`goal_cron_bridge._trigger_cycle()`
  跟 C1/方向 5 的两个 hint 函数在同一处串联调用
  `_append_growth_pursuit_style_hint()`，往子 Objective description
  里追加风格提示；任何环节异常静默跳过，不影响 Goal 触发主流程。
- **看板**："🔄 正在自主推进"分区每条方向的调度信息行追加
  `🧭 <风格>` 标记（未分类的旧 Goal 不展示，不影响既有布局）。
- **API**：`GET /growth/pursuits` 每条方向新增 `pursuit_style` 字段，
  纯只读透出。

**新增/变更文件**：

- `src/mini_agent/config/models.py`：新增
  `GrowthAdvisorConfig.pursuit_style_llm_enabled`（默认 `False`）；
- `src/mini_agent/perception/goal_backlog.py`：`GoalNode` 新增
  `growth_pursuit_style: Optional[str] = None` 字段（同步 `to_dict`/
  `from_dict`）；
- `src/mini_agent/evolution/growth_advisor.py`：新增
  `_PURSUIT_STYLE_LABELS`/`_PURSUIT_STYLE_KEYWORDS`/
  `_infer_pursuit_style_rule()`/`classify_pursuit_style_llm()`/
  `determine_pursuit_style()`/`_PURSUIT_STYLE_PROMPT_ADDENDUM`/
  `pursuit_style_hint()`；`auto_pursue_candidate()` 新增落地后的
  风格判定步骤；
- `src/mini_agent/evolution/goal_cron_bridge.py`：新增
  `_append_growth_pursuit_style_hint()` 并接入 `_trigger_cycle()`；
- `src/mini_agent/api/routes.py`：`GET /growth/pursuits` 响应新增
  `pursuit_style` 字段；
- `apps/mini_agent_kanban/app.py`：`_render_growth_pursuits()` 调度
  信息行追加风格标记；
- `tests/test_growth_advisor_pursuit_style.py`：新增，覆盖规则式
  分类（各风格关键词命中/无命中兜底/extra_text 参与匹配）、LLM 分类
  （合法/非法标签、空响应、异常）、统一入口（默认只用规则/开关关闭
  忽略 helper/开启无 helper 时降级/开启且有效时覆盖/LLM 非法值时
  降级）、`pursuit_style_hint()`（非标签 Goal 不生效/未分类不生效/
  三种风格都能正确生成提示/非法风格值返回 `None`）。

## 3. 默认行为速览

`GrowthAdvisorConfig.enabled` 默认 `True`（opt-out），不需要任何额外
配置，系统会：

1. 每天 22:30（`sys:growth_advisor_daily` cron job）自动跑一遍 2.1~2.4
   节的完整流程；
2. 每 30 天（`sys:growth_monthly_retrospective`）生成一次月度复盘统计
   （数量/采纳率/主题排行 + 跨候选的"成长主题地图"聚合）；
3. 用户在看板/CLI/API 上采纳一个候选后（`auto_pursue_on_accept`
   默认开启），自动落地成 Goal 并绑定每天一轮的周期性执行，持续在
   同一份 wiki 页面上追加素材——见 2.12 节。

除 `enabled` 本身与 `auto_pursue_on_accept` 外，本文档提到的所有
细化能力（LLM 增强扫描、LLM 报告正文、LLM 主题分类、按类别静音、
探索位……）默认全部关闭，只有总开关和"采纳即启动"是"零成本/默认
开启"，其余是"opt-in 增强"——这是这套机制一以贯之的设计取舍，加新
能力不改变这条底线。

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
  按"最近是否突增"优先排序，一键重新生成（**[2.13 节 A1]** 已经落地
  成 Goal 且绑定了周期性执行的候选不会出现在这里——它的素材已经由
  自主持续调研接管）
- **[2.13 节 D1/D2 新增]**"🔄 正在自主推进"折叠区：列出所有已采纳
  且关联了 Goal 的方向，逐条展示第几轮、下次执行时间、连续低增量时
  的"疑似饱和"提示，并带"⏸ 暂停"/"▶ 恢复"/"📄 素材"按钮，不需要跳到
  「🎯 目标」tab
- "📄 查看调研报告"折叠区（**[修复]** 与候选当前状态无关）：所有挂着
  `report_id` 的候选（不管 pending/accepted/dismissed/expired）都能在
  这里下拉选中查看正文——此前"📄 查看报告"按钮只出现在待处理候选卡片/
  拖拽看板的"待处理"列上，候选一旦被采纳/忽略或过期，报告就从界面上
  找不到入口了（顶部指标数字仍显示总数，但点不到），现在始终可查，与
  列表/拖拽视图各自的报告按钮并存；同一处新增"🚀 落地为 Goal（继续
  调研）"按钮（**新增**，需候选已有报告），点击后创建对应 GoalBacklog
  Goal——这是"采纳一个方向"之后，让成长顾问真正继续深入调研收集素材的
  衔接点，此前只有 CLI `/growth adopt-goal <id>` 能做，看板上完全没有
  入口，容易让人误以为"采纳了但系统什么都没做"
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
POST /v1/growth/candidates/{id}/accept|dismiss          # 采纳 / 忽略；dismiss 可选 body {"reason": "..."}（见 2.7 节）；accept 响应体新增 `pursuit` 字段（见 2.12 节，`auto_pursue_on_accept=false` 时不出现）
GET  /v1/growth/followups                              # 待回访候选列表（含 question_hint 提问措辞）
POST /v1/growth/followups/{id}/progressed|stalled       # 回答一次回访
POST /v1/growth/keywords                                # 添加自定义关键词主题
POST /v1/growth/keywords/{topic}/confirm                # 确认保留一个待确认主题
POST /v1/growth/keywords/{topic}/remove                 # 删除自定义主题 / 隐藏内置主题
POST /v1/growth/keywords/{topic}/restore                 # 恢复一个被隐藏的内置主题
GET  /v1/growth/reports/refresh_candidates               # "值得刷新"的报告列表（已进入自主持续调研的候选不再出现，见 2.13 节 A1）
POST /v1/growth/candidates/{id}/report/refresh            # 重新生成该候选的调研报告
POST /v1/growth/candidates/{id}/adopt_goal                # 落地成 GoalBacklog Goal，交给 Goal/Cron 体系继续调研
GET  /v1/growth/reports/{id}                             # 某份调研报告的完整元数据 + 正文
GET  /v1/growth/health_trend                             # 健康度趋势快照序列（v4 N1，见 5.5 节）
GET  /v1/growth/pursuits                                  # 正在被自主推进的方向列表（本次新增，见 2.13 节 D1）
```

## 5. 常用配置项（`agent_config.json` / `growth_advisor` 块）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关，关闭后信号扫描/候选生成/cron job 全部跳过 |
| `auto_pursue_on_accept` | `true` | 采纳候选时是否自动完成"生成报告 → 落地为 Goal → 生成并确认执行规范 → 绑定周期性"整条链路（见 2.12 节），是本文档里少数默认开启的增强开关之一 |
| `auto_pursue_schedule` | `"interval:86400"` | 自动绑定周期性时使用的调度表达式，默认每天一轮 |
| `auto_pursue_template_id` | `"growth_pursuit"` | 自动生成执行规范草稿时使用的模板 id |
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
| `feedback_pattern_llm_enabled` | `false` | （`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md` 方向 2 第二步）诊断面板"反馈模式"区块是否额外调一次 LLM，把规则式统计出来的忽略原因/类别分布归纳成一两句自然语言（`llm_insight`）；只在规则式统计样本已达标时才触发，结果只是展示，不影响任何排序/加权 |
| `report_two_stage_enabled` | `false` | （2.10 节）报告生成先让 LLM 提炼 3-4 个具体问题再逐一回答，替代固定的四段式结构；多一次 LLM 调用，默认关闭 |
| `report_dismiss_reason_adaptive_enabled` | `true` | （2.10 节）报告曾被标"内容太笼统"时，下次生成追加针对性提醒；不产生额外 LLM 调用，默认开启 |
| `report_active_search_enabled` | `false` | （2.11 节）手动触发调研报告（有 `web_search_fn` 的调用路径）时，被动扫描命中 0 条素材才现查一次；会实际发起检索调用，默认关闭 |
| `cron_triggered_active_search_enabled` | `false` | （2.11 节）`sys:growth_advisor_daily` cron 路径是否也触发主动检索，每天最多处理 `cron_triggered_active_search_daily_limit` 个"证据数最高但没有外部背景"的候选；会实际发起检索调用，默认关闭 |
| `cron_triggered_active_search_daily_limit` | `1` | （2.11 节）cron 主动检索每个自然日的预算上限，开关关闭时不生效 |
| `reorganize_every_n_cycles` | `10` | （2.14 节）`growth_pursuit` 模板累计满这么多轮，下一轮 prompt 里附加一段"顺带整理一下"的提示；配成 0 或负数视为关闭 |
| `pursuit_digest_enabled` | `true` | （2.14 节）每轮持续调研完成后是否暂存"本轮新增摘要"，等下一次实际推送时打包带出，不额外消耗推送额度 |
| `goal_alignment_adopt_all_max_batch` | `3` | （2.15 节）`/growth align --adopt-all` / 看板"全部采纳"单次最多批量落地的方向数，避免一次点击触发过多 LLM 调用 |
| `pursuit_increment_llm_review_enabled` | `false` | （2.19 节）`evaluate_cycle_increment()` 规则式判定"疑似低增量"后，是否再追加一次 LLM 语义复核；结果只作诊断展示，不覆盖规则式判断、不影响 B2 饱和度 streak 计数；会实际发起一次 LLM 调用，默认关闭 |
| `pursuit_style_llm_enabled` | `false` | （2.20 节，`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md` 方向 6）调研风格（技能实操类/知识理论类/习惯养成类）分类默认走零成本的规则式关键词匹配，打开后额外调一次 LLM 复核/纠偏；只在 `auto_pursue_candidate()` 首次落地一个 Goal 时触发一次，不是每轮都调 |

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
  P6 的 dismiss 原因细化让"哪些反馈该参与衰减"更准确了，
  `next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_plan.md`
  方向 2 补上了一层纯统计展示（`growth_feedback_pattern_summary()`，
  诊断面板"反馈模式"区块），能看出"最近更容易忽略什么原因/类别的
  方向"；第二步在此之上加了一个默认关闭的 opt-in 开关
  `feedback_pattern_llm_enabled`，打开后额外调一次 LLM 把这段统计
  转成更自然的一两句归纳文字（`llm_insight` 字段，诊断面板里以
  `💡` 前缀跟规则式摘要并列展示）。但无论第一步的统计还是第二步的
  LLM 归纳，都明确止步于"展示给你看"，不会被用来自动调整任何衰减
  系数或候选排序——方案文档第 8 节把这一条列为比既有"用户始终有
  最终决定权"底线更保守的额外取舍，衰减系数本身仍未接入任何自动
  校准；
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
  Goal 状态"三件事：候选 → Goal 本身仍是单向的（Goal 完成/停滞状态会
  反哺回访判断，但 Goal 的 `progress_notes` 更新不会自动同步成候选的
  新证据）；`next_doc/growth_advisor_ideal_advisor_gap_and_roadmap_
  plan.md` 方向 3 补上了一条相邻但更窄的反哺路径——`extract_spinoff_
  topics_from_pursuits()` 会把持续调研过程中反复出现、却从未被
  `covered_subtopics` 吸收的 `open_questions` 并入下一轮信号扫描的
  候选生成输入（打 `origin="pursuit_spinoff"` 标记，仍然要用户手动
  采纳才会变成新方向），但这只覆盖"衍生话题"这一种反哺场景，不是
  "Goal 进展本身反哺候选证据"这种更通用的双向同步；对齐分析默认仍是关键词包含匹配，`goal_alignment_llm_
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
- 2.13 节落地的 B1/B2 增量质量/饱和度信号目前只是"提示"，不影响
  执行流程本身；且 B1 只做了规则式初筛（`covered_subtopics` 差集
  占比），`growth_advisor_autonomy_deepening_plan.md` 里提到的
  "LLM 语义级复核"这个可选增强步骤还没有做；相近主题之间仍然不共享
  素材、也不会互相去重/合并提示（方案文档方向 B3，明确标注工作量/
  收益不确定，暂缓）；对齐分析结果的批量落地（方向
  A3）、wiki 页面的定期重新整理（方向 C1）、"本轮新增摘要"推送
  （方向 C2）也都还没有实施——这些是 `next_doc/growth_advisor_
  autonomy_deepening_plan.md` 里已识别但尚未排期的部分，供后续
  按需认领。方向 A2（Goal 停滞时区分"素材饱和"vs"执行卡住"两类
  原因）已实施，但只做到"措辞区分、引导用户去看执行状态"这一层，
  不包含自动检测/重试执行失败的能力——这部分自愈能力仍然是留白。
- 2.20 节落地的方向 6（调研风格智能分类）目前只影响 `growth_pursuit`
  模板每一轮 prompt 里追加的一段文字提示，不做任何"根据风格切换成
  完全不同的模板结构/wiki 页面组织方式"——生成结果最终仍然取决于
  执行模型是否真的照做这段提示，不是强约束；分类只在 Goal 首次落地
  时判定一次，不会随着后续轮次的实际产出内容动态修正（比如一个方向
  最初被归为"知识理论类"，但用户后来其实更想要动手案例，目前没有
  机制发现并重新分类，需要手动改 `.agent/goals.json` 里的
  `growth_pursuit_style` 字段）；规则式关键词表覆盖面有限，边界
  情况（比如"数据分析"这类既偏实操又偏理论的主题）容易被兜底成
  默认的"知识理论类"，开启 `pursuit_style_llm_enabled` 能缓解但
  不能完全消除误判。
