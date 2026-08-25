# 成长顾问（Growth Advisor）指南

> 对应方案：`next_doc/growth_advisor_design.md`（P1 原始方案）、
> `next_doc/growth_advisor_improvement_plan_v2.md`（P4-0~P4-7，已全部
> 完成）、`next_doc/growth_advisor_improvement_plan_v3.md`（P5-0~P5-6，
> 已全部完成）；逐阶段实施细节见
> `next_doc/growth_advisor_implementation_record.md`。P6（反馈粒度细化
> + LLM 增强路径可观测性）、Goal/Cron 打通、调研信息获取与整理等后续
> 能力方向的落地细节，已迁移到
> [growth-advisor-directions-history.md](growth-advisor-directions-history.md)
> （演进日志 §2.7-2.10 等，按方案批次组织）。
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
  然后停在原地等用户发出下一条明确指令。演进日志 §2.12的"采纳即启动"就是
  这条理念在当前版本的落地：默认把"采纳"和"开始持续调研"绑在一起，
  而不是让两者成为需要用户分别触发的两个独立动作。
- **"持续"意味着不能在原地打转**。每一轮推进都应该在上一轮的基础上
  往深/往新走——避免重复讲已经讲过的内容、避免用不同措辞重写同一份
  素材。这要求执行规范/模板层面有专门的"增量"约束和"已覆盖话题"记忆
  （见 演进日志 §2.12 growth_pursuit 模板的 handoff_fields 设计），而不能只
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
  哪一步了"？（演进日志 §2.9末尾、演进日志 §2.12提到的"看板要能看到正在被推进的
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

> **§2.5-2.24a（各能力方向的落地细节）已迁移到**
> [growth-advisor-directions-history.md](growth-advisor-directions-history.md)，
> 按方案批次组织；下面 §5 配置表里标注的"（演进日志 §2.9 节）"这类引用，指向的就是
> 演进日志里同编号的小节。

## 3. 默认行为速览

`GrowthAdvisorConfig.enabled` 默认 `True`（opt-out），不需要任何额外
配置，系统会：

1. 每天 22:30（`sys:growth_advisor_daily` cron job）自动跑一遍 2.1~2.4
   节的完整流程；
2. 每 30 天（`sys:growth_monthly_retrospective`）生成一次月度复盘统计
   （数量/采纳率/主题排行 + 跨候选的"成长主题地图"聚合）；
3. 用户在看板/CLI/API 上采纳一个候选后（`auto_pursue_on_accept`
   默认开启），自动落地成 Goal 并绑定每天一轮的周期性执行，持续在
   同一份 wiki 页面上追加素材——见 演进日志 §2.12。

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
  按"最近是否突增"优先排序，一键重新生成（**[演进日志 §2.13 A1]** 已经落地
  成 Goal 且绑定了周期性执行的候选不会出现在这里——它的素材已经由
  自主持续调研接管）
- **[演进日志 §2.13 D1/D2 新增]**"🔄 正在自主推进"折叠区：列出所有已采纳
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
- **关键词列表支持批量操作**（**新增**）：内置主题隐藏列表、已隐藏的
  内置主题、"🟡 待确认"（LLM 学到的、未确认）、"🔵 已确认/用户自定义"
  四个分组各自的每一行左边加了一个复选框；勾选一个或多个后，该分组
  上方/旁边会出现对应的批量操作按钮（"🙈 批量隐藏"/"↩️ 批量恢复"/
  "✅ 批量保留"+"❌ 批量不要"/"❌ 批量删除"，按钮上带勾选数量），一次
  点击对所有勾选项生效，不需要逐条点。没有勾选任何项时批量按钮不
  显示；单条操作的原有按钮仍然保留，两者并存，按需选用。执行批量
  操作后勾选状态会自动清空。
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
- **板块相互独立，互不拖累**（**修复**）：本 tab 由多个独立板块拼成
  （诊断信息、健康度趋势、回访、对齐、正在自主推进、报告刷新提示、
  待处理候选等）。此前 `/growth/summary` 概览接口一旦超时/报错（候选、
  报告、Goal 数量稍多时常见，典型报错是 `HTTPConnectionPool(...):
  Read timed out.`），会导致整个 tab 直接中断渲染，后面所有跟这次
  请求无关、各自独立拉数据的板块一起消失。现在改为：概览接口失败时
  只在依赖它的候选统计/主题地图等位置显示"暂时无法显示" + 一个
  "🔄 重试概览"按钮，其它板块正常渲染；任意单个板块内部报错也只在
  原地提示一行，不影响其它板块。此外，"该回访一下了"/"有兴趣但还没
  建目标"/"正在自主推进"/"报告可以更新一下了"/"健康度趋势"这几个
  完全自包含的板块各自独立刷新（`st.fragment`）：点里面的按钮只重跑
  这个板块本身，不会带着整页（含其它板块已展开的折叠区状态）一起
  刷新。
- **候选去重覆盖冷却期内的相似方向**（**修复**，见演进日志 §2.6.1"修复记录"）：
  开启 `duplicate_direction_llm_check_enabled` 后，语义判重现在也会
  覆盖"冷却期内被 dismiss 过的候选"，不再只依赖字面完全一致的标题
  去重，避免待处理候选里反复出现措辞不同但已经被忽略过的方向。

### CLI

```
/growth              # 展示当前待处理候选（等价于 /growth list）
/growth scan          # 手动触发一轮信号扫描 + 候选生成 + Top-N 调研报告
/growth accept <id>   # 采纳某个候选
/growth dismiss <id> [reason]  # 忽略某个候选（30 天内不会重新生成同一
                       # 方向）；reason 可选，见 演进日志 §2.7，不传等价于
                       # unspecified（行为与 P6 之前一致）
/growth report <id>   # 查看（或按需生成）某候选的调研报告正文
/growth retrospective # 查看月度成长复盘统计
/growth align          # 兴趣方向 ⇄ 目标 对齐分析（见 演进日志 §2.9）：哪些方向
                       # 有兴趣但没建目标、哪些已建目标但停滞
/growth adopt-goal <id> # 把候选落地成一个 GoalBacklog 目标（要求候选
                       # 已有调研报告，见 演进日志 §2.9阶段 B）
```

回访、关键词管理、类别静音、探索位这些更细的操作目前只在看板/API 提供
入口，CLI 保持精简。

### API

```
GET  /v1/growth/summary                              # 候选队列 + 报告列表 + 复盘统计 + 首次触达状态 + 诊断快照
POST /v1/growth/first_touch_ack                       # 标记首次触达提示已展示（幂等）
POST /v1/growth/scan                                   # 手动触发一轮扫描
POST /v1/growth/candidates/{id}/accept|dismiss          # 采纳 / 忽略；dismiss 可选 body {"reason": "..."}（见 演进日志 §2.7）；accept 响应体新增 `pursuit` 字段（见 演进日志 §2.12，`auto_pursue_on_accept=false` 时不出现）
GET  /v1/growth/followups                              # 待回访候选列表（含 question_hint 提问措辞）
POST /v1/growth/followups/{id}/progressed|stalled       # 回答一次回访
POST /v1/growth/keywords                                # 添加自定义关键词主题
POST /v1/growth/keywords/{topic}/confirm                # 确认保留一个待确认主题
POST /v1/growth/keywords/{topic}/remove                 # 删除自定义主题 / 隐藏内置主题
POST /v1/growth/keywords/{topic}/restore                 # 恢复一个被隐藏的内置主题
GET  /v1/growth/reports/refresh_candidates               # "值得刷新"的报告列表（已进入自主持续调研的候选不再出现，见 演进日志 §2.13 A1）
POST /v1/growth/candidates/{id}/report/refresh            # 重新生成该候选的调研报告
POST /v1/growth/candidates/{id}/adopt_goal                # 落地成 GoalBacklog Goal，交给 Goal/Cron 体系继续调研
GET  /v1/growth/reports/{id}                             # 某份调研报告的完整元数据 + 正文
GET  /v1/growth/health_trend                             # 健康度趋势快照序列（v4 N1，见 演进日志 §5.5）
GET  /v1/growth/pursuits                                  # 正在被自主推进的方向列表（本次新增，见 演进日志 §2.13 D1）
```

## 5. 常用配置项（`agent_config.json` / `growth_advisor` 块）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关，关闭后信号扫描/候选生成/cron job 全部跳过 |
| `auto_pursue_on_accept` | `true` | 采纳候选时是否自动完成"生成报告 → 落地为 Goal → 生成并确认执行规范 → 绑定周期性"整条链路（见 演进日志 §2.12），是本文档里少数默认开启的增强开关之一 |
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
| `goal_alignment_enabled` | `true` | （演进日志 §2.9 节）兴趣方向 ⇄ 目标 对齐分析总开关，纯规则式关键词匹配，零 LLM 成本 |
| `goal_alignment_stalled_days` | `21` | （演进日志 §2.9 节）已关联 Goal 的方向，`active` 状态下超过这么多天没被 touch 就判定为"停滞"，独立于 `followup_review_days` |
| `goal_alignment_llm_enabled` | `false` | （演进日志 §2.9 节）对齐分析是否额外做一次 LLM 语义匹配，找出关键词匹配漏掉的"字面不同、实质同一件事"配对；结果只出现在建议列表，不自动写入关联关系 |
| `feedback_pattern_llm_enabled` | `false` | （`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md` 方向 2 第二步）诊断面板"反馈模式"区块是否额外调一次 LLM，把规则式统计出来的忽略原因/类别分布归纳成一两句自然语言（`llm_insight`）；只在规则式统计样本已达标时才触发，结果只是展示，不影响任何排序/加权 |
| `report_two_stage_enabled` | `false` | （演进日志 §2.10 节）报告生成先让 LLM 提炼 3-4 个具体问题再逐一回答，替代固定的四段式结构；多一次 LLM 调用，默认关闭 |
| `report_dismiss_reason_adaptive_enabled` | `true` | （演进日志 §2.10 节）报告曾被标"内容太笼统"时，下次生成追加针对性提醒；不产生额外 LLM 调用，默认开启 |
| `report_active_search_enabled` | `false` | （演进日志 §2.11 节）手动触发调研报告（有 `web_search_fn` 的调用路径）时，被动扫描命中 0 条素材才现查一次；会实际发起检索调用，默认关闭 |
| `report_active_search_max_calls` | `1` | （演进日志 §5.6阶段二）单次报告最多用几个关键词角度各查一次；调大会按倍数增加检索调用次数，默认 `1` 与改动前行为一致 |
| `cron_triggered_active_search_enabled` | `false` | （演进日志 §2.11 节）`sys:growth_advisor_daily` cron 路径是否也触发主动检索，每天最多处理 `cron_triggered_active_search_daily_limit` 个"证据数最高但没有外部背景"的候选；会实际发起检索调用，默认关闭 |
| `cron_triggered_active_search_daily_limit` | `1` | （演进日志 §2.11 节）cron 主动检索每个自然日的预算上限，开关关闭时不生效 |
| `reorganize_every_n_cycles` | `10` | （演进日志 §2.14 节）`growth_pursuit` 模板累计满这么多轮，下一轮 prompt 里附加一段"顺带整理一下"的提示；配成 0 或负数视为关闭 |
| `pursuit_digest_enabled` | `true` | （演进日志 §2.14 节）每轮持续调研完成后是否暂存"本轮新增摘要"，等下一次实际推送时打包带出，不额外消耗推送额度 |
| `goal_alignment_adopt_all_max_batch` | `3` | （演进日志 §2.15 节）`/growth align --adopt-all` / 看板"全部采纳"单次最多批量落地的方向数，避免一次点击触发过多 LLM 调用 |
| `pursuit_increment_llm_review_enabled` | `false` | （演进日志 §2.19 节）`evaluate_cycle_increment()` 规则式判定"疑似低增量"后，是否再追加一次 LLM 语义复核；结果只作诊断展示，不覆盖规则式判断、不影响 B2 饱和度 streak 计数；会实际发起一次 LLM 调用，默认关闭 |
| `pursuit_style_llm_enabled` | `false` | （演进日志 §2.20，`growth_advisor_ideal_advisor_gap_and_roadmap_plan.md` 方向 6）调研风格（技能实操类/知识理论类/习惯养成类）分类默认走零成本的规则式关键词匹配，打开后额外调一次 LLM 复核/纠偏；只在 `auto_pursue_candidate()` 首次落地一个 Goal 时触发一次，不是每轮都调 |
| `pursuit_style_reclassify_every_n_cycles` | `8` | （演进日志 §2.21，方向 6 动态修正）累计满多少轮后，用该方向最近几轮实际产出的内容重新判定一次调研风格（可能改写此前判定的结果）；`<=0` 关闭。目前只走规则式重判，不透传 `llm_helper` |
| `report_quality_auto_upgrade_enabled` | `false` | （演进日志 §2.22，方向 7）某个方向的报告被反馈"内容太笼统"累计达到阈值时，是否自动把下一份报告临时升级为 LLM 生成（不修改全局 `report_quality_llm_enabled`）；只在调用方确实拿得到 `llm_helper` 时才生效 |
| `report_quality_auto_upgrade_threshold` | `2` | （演进日志 §2.22 节）触发上面自动升级所需的"报告没写好"累计次数；`<=0` 视为关闭 |
| `notification_context_aware_throttle_enabled` | `false` | （演进日志 §2.23 节）最近一周对话密度明显低于历史周均值时，是否软性抬高推送置信度门槛（依然可能推送，只是需要更高置信度）；不产生额外调用 |
| `notification_low_activity_ratio_threshold` | `0.3` | （演进日志 §2.23 节）判定"明显更安静"的密度比值门槛，最近一周条目数 / 基线周均值低于这个值才触发；`<=0` 视为关闭 |
| `notification_low_activity_confidence_boost` | `0.15` | （演进日志 §2.23 节）命中"更安静"时在 `notification_min_confidence` 基础上额外加多少（封顶 1.0） |
| `pursuit_long_unviewed_threshold` | `5` | （演进日志 §2.24a，方向 4）某方向的素材已经比用户上次查看时新了多少轮，就计入 `pursuits_portfolio_summary()` 的"建议关注"分类 |
| `pursuit_self_check_every_n_cycles` | `5` | （演进日志 §2.24a，方向 5）累计满多少轮后追加一段"生成自测题"的提示；`<=0` 关闭 |
| `report_external_drift_min_changes` | `1` | （演进日志 §5.8 节）外部世界变化驱动刷新时，判定"值得刷新"所需的最少变化条数 |
| `report_external_drift_refresh_enabled` | `false` | （演进日志 §5.8 节）打开后，候选关联的外部资讯发生变化达到 `report_external_drift_min_changes` 条时，提示报告可以刷新 |

另外 `memory_backfill.cron_run_backfill_enabled`（默认 `true`，v4 N2）
控制 cron 任务收尾是否自动回填记忆，属于 `memory_backfill` 配置块而非
`growth_advisor` 块，详见 演进日志 §5.5 N2 与 `docs/memory-backfill-guide.md`。

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

> **§5.5-5.9（v4/自主检索/报告分层/外部刷新/诊断性能优化）已迁移到**
> [growth-advisor-directions-history.md](growth-advisor-directions-history.md)
> 对应批次。

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
- Goal/Cron 打通（演进日志 §2.9 节）目前只做了"对齐分析 + 一键落地 + 回访读取
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
- 调研信息获取（演进日志 §2.10 节）目前只做了"复用现有 wiki 素材做摘录 + 结构
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
- 演进日志 §2.13落地的 B1/B2 增量质量/饱和度信号目前只是"提示"，不影响
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
- 演进日志 §2.20落地的方向 6（调研风格智能分类）目前只影响 `growth_pursuit`
  模板每一轮 prompt 里追加的一段文字提示，不做任何"根据风格切换成
  完全不同的模板结构/wiki 页面组织方式"——生成结果最终仍然取决于
  执行模型是否真的照做这段提示，不是强约束。分类曾经只在 Goal 首次
  落地时判定一次，演进日志 §2.21的动态修正已经补上"按累计轮次用实际产出
  内容重新判定"这一层，但修正的粒度仍然是"整个方向一个标签"，不会
  区分"这个方向早期偏理论、后期转向实操"这种阶段性变化；规则式
  关键词表覆盖面有限，边界情况（比如"数据分析"这类既偏实操又偏
  理论的主题）容易被兜底成默认的"知识理论类"，开启 `pursuit_style_
  llm_enabled` 能缓解但不能完全消除误判；动态修正的重判目前不透传
  `llm_helper`，即便全局开启该配置，重判这一步也只走规则式路径。
- 演进日志 §2.22落地的方向 7（报告质量自动闭环）只处理了"要不要换成 LLM
  生成"这一种最粗粒度的改进方式，不会根据具体的负反馈内容做更细
  的调整（比如用户觉得报告"太空泛"和"跟实际情况不符"，理想情况下
  应该对应不同的改进策略，目前统一按"升级成 LLM 生成"一刀切处理）；
  升级只发生在下一次生成时，不会主动重新生成已经存在的旧报告；且
  只有调用方（cron 触发路径）确实具备 `llm_helper` 时才会真正生效，
  纯模板环境下这个开关不产生任何效果。
- 演进日志 §2.23落地的推送情境感知只是"软性抬高置信度门槛"，不是真正理解
  用户当前的精力/心情/时间可用性——对话密度骤降也可能只是因为用户
  在忙别的事、或者单纯这几天没怎么打开客户端，跟"不想被打扰"不是
  一回事，信号本身就是间接推断，存在误判空间；密度比值的窗口（最近
  7 天 vs 前 4 周周均值）是固定的，不会根据用户的历史使用节奏自适应
  调整，对使用频率本来就很低的用户（比如每周互动一两次）可能不适用
  （基线数据不足时函数会返回 `None`，直接跳过软性调整，但也意味着
  这类用户永远享受不到这个能力）。
- 演进日志 §2.24落地的调研路径关联信号只做"共现提示"，不做"依赖顺序判断"
  ——即便两个方向存在明显的先后关系（比如"Python 基础"应该先于
  "数据分析"），系统也只会说"这两个有关联"，不会建议学习顺序，这是
  刻意的克制而不是遗漏，但也确实是规划维度分析里提到的"纵向路径
  设计"能力缺口尚未填补的部分；关键词共现本身对措辞高度敏感（同一
  个概念换一种说法就匹配不到），且只扫描 `covered_subtopics` 这一个
  字段，如果某个方向没有用 `growth_pursuit` 模板、或者执行时没有按
  约定写 handoff 块，这个方向就完全不会出现在关联信号里。
