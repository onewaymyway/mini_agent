# 成长顾问（Growth Advisor）指南

> 对应方案：`next_doc/growth_advisor_design.md`（P1 原始方案）、
> `next_doc/growth_advisor_improvement_plan_v2.md`（P4-0~P4-7，已全部
> 完成）、`next_doc/growth_advisor_improvement_plan_v3.md`（P5-0~P5-6，
> 已全部完成）；逐阶段实施细节见
> `next_doc/growth_advisor_implementation_record.md`。P6（反馈粒度细化
> + LLM 增强路径可观测性）见本文档 2.7/2.8 节。
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
  定时任务没跑过 / 功能被关掉）

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

## 6. 数据存放位置

- `.agent/growth_backlog.jsonl` — 候选队列（整表重写，不是只追加）
- `.agent/growth_reports.jsonl` — 调研报告元数据索引（活跃窗口）
- `.agent/growth_reports.archive.jsonl` — 归档的旧报告元数据（P5-0，
  不再是任何候选当前挂着、生成超过 180 天的报告会被移到这里；查询侧
  自动兜底，不会因为归档就查不到）
- `.agent/growth_feedback_ledger.jsonl` — 采纳/忽略/回访反馈流水
- `.agent/growth_topic_trend.jsonl` — 按主题的证据数/置信度历史快照
  （P4-6，超过 60 天的旧快照会被降采样压缩）
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
  切换到 LLM 生成、或调整模板内容），这是留给未来版本的闭环。
