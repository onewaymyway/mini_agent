"""成长顾问 Growth Advisor（对应 next_doc/growth_advisor_design.md）。

设计理念（对后续改动的前提，详见 docs/growth-advisor-guide.md 1.5 节）：
成长顾问的定位是**自主**——自主根据用户需求规划成长方向，并在用户
选择的方向上自主、持续地收集整理素材供用户学习。"采纳"应该是一个
起点而不是终点；"持续"意味着每一轮都要有实质性增量，不能原地打转；
自主不等于替用户做主——用户始终能看到进展、能随时暂停/调整。任何
改动如果会让机制退回"每一步都要人工衔接"，应视为偏离定位，而不是
一个可选的实现方式。

与 evolution/ 目录下服务"Agent 自我进化"的模块（soft_goal_deriver /
decision_profile_builder / objective_outcome_tracker ...）是姊妹关系：
那些模块把用户的反馈/记忆折射回 Agent 自身的行为改进，这个模块则是把
同一批记忆信号折射回**用户自己的成长方向**——候选生成、调研报告生成、
反馈台账三层结构完全复用 evolution/ 里已经跑通的"证据 → 候选 → 采纳/
忽略反馈回路"范式（方案第 3 节 P1 里程碑）。

P1 范围（信号扫描 → 候选生成 → 调研报告 → 看板展示，已完成）：
    - GrowthCandidate / GrowthReport 数据模型
    - GrowthBacklog：候选队列（pending/accepted/dismissed/expired），
      按 dedupe_key 去重、dismissed 有冷却期、pending 有数量上限
    - GrowthFeedbackLedger：用户对候选/报告的采纳/忽略反馈流水
    - growth_signal_scan()：规则式信号扫描（不依赖 LLM），从 memory
      entries 的 tags/summary 里做关键词频次统计，写回
      UserProfile.derived["growth_focus_areas"] /
      UserProfile.derived["growth_gaps"]
    - growth_candidate_derive()：从 focus areas 里挑选证据数达标的方向，
      生成/追加候选到 backlog（克制：达不到 min_evidence_count 的方向
      不生成候选，命中 excluded_topics 的直接跳过）
    - generate_growth_report()：为一个候选生成调研报告（Markdown），
      P1 阶段用规则式模板兜底；如调用方传入 llm_helper（可调用对象，
      签名 `llm_helper(prompt: str) -> str`），则优先用它起草正文——
      同 decision_profile_builder 的"可选 LLM 增强，缺省仍要能跑"原则。

P2 范围（反馈驱动的置信度调权 + 推送节流接入 + 复盘深度归因，本次新增）：
    - `_feedback_multiplier()` / `_dismiss_counts_by_dedupe_key()`：读取
      GrowthFeedbackLedger 里的历史 dismiss 记录，同一方向被忽略过的
      次数越多，下次（冷却期过后）重新生成候选时默认置信度打的折扣越
      大，但不会打到 0——呼应方案第 6 节"不是完全屏蔽，避免用户当时忙、
      后来又感兴趣的情况被永久拒绝"。
    - `_maybe_dispatch_notification()`：把 `run_daily_cycle()` 产出的
      调研报告接入已有的 `NotificationDispatcher`（复用 email/kanban
      channel）与 `notification.reports_store`，落实方案第 4.2 节的
      推送节流规则——`notification_frequency=kanban_only` 时不推送；
      否则当天最多推 `notification_max_per_day` 条（默认 1 条），且必须
      是这一轮新生成报告里置信度最高、达到 `notification_min_confidence`
      阈值的一条；节流状态落盘在 `paths.growth_state_path`。
    - `monthly_retrospective_summary()` 新增 `acceptance_rate`（采纳率）
      与 `top_accepted_topics` / `top_dismissed_topics`（按候选标题聚合
      的采纳/忽略排行），作为方案第 6 节"推荐命中率"指标的落地。

P3 范围（首次触达提示跨会话持久化 + 黑名单可视化编辑，本次新增）：
    - `first_touch_notice_shown()` / `mark_first_touch_notice_shown()`：
      把方案第 8 节第 1 条"首次触达必须透明告知，但不能每次都打断"落到
      跨会话持久化，状态复用 `growth_state_path`（与推送节流状态同一个
      文件，互不覆盖）。看板侧通过 `POST /growth/first_touch_ack` 落盘。
    - `excluded_topics` 黑名单的看板可视化编辑：不是在这个模块加代码，
      而是修好了通用配置编辑器（`kanban/app.py` 的
      `_render_config_field_widget`）里 list 类型字段此前被当纯文本框
      处理的缺口，改成一行一项的文本域——这个修复对所有 list 类型配置
      字段生效，不止 `excluded_topics`。

P3 范围（本次新增）——`notification_frequency=weekly_digest` 的真实周摘要
打包：
    - `_maybe_dispatch_weekly_digest()`：独立于 `_maybe_dispatch_notification`
      的按天节流路径，按"距上次推送是否满 7 天"（而非自然日）判断是否
      触发；到期后把窗口期内新生成的全部调研报告标题打包成一条摘要消息
      一次性推送，不再逐条推。`run_daily_cycle()` 按 `notification_frequency`
      分流：`weekly_digest` 走这里，其余（`daily`/`kanban_only`）仍走
      `_maybe_dispatch_notification`。

P3 范围（本次新增，第二项）——月度复盘的跨候选能力地图聚合：
    - `growth_topic_map()`：按 `dedupe_key` 聚合 backlog 里全部历史候选
      （含同一主题因 dismiss 冷却结束后重新生成的多条记录），产出每个
      主题的当前状态/历史累计采纳与忽略次数/历史峰值置信度/首次出现与
      最近更新时间，按最近更新时间倒序排列。思路对齐
      `self_model_snapshot.py`（Agent 自己的能力弱项趋势），只是聚合对象
      换成了用户的成长方向推进轨迹。`monthly_retrospective_summary()`
      新增 `topic_map` 字段直接复用该函数，`GET /growth/summary` 已经
      透传整个 `retrospective`，未新增 API 端点。

P3 范围（本次新增，第四项）——`growth_signal_scan` 的 LLM 增强版归纳
（默认关闭，opt-in）：
    - `_llm_augment_topics()`：只对关键词表命中不到的近期记忆条目（数量
      不足 `_LLM_AUGMENT_MIN_UNMATCHED` 时直接跳过，避免为了归纳专门调
      一次 LLM 却大概率凑不满候选证据阈值）做一次 LLM 归纳，把发现的新
      主题与规则命中结果按 `normalize_title_key` 去重合并；entry_ids 必须
      是调用方提供的合法子集，任何解析失败/字段缺失都直接丢弃对应结果，
      不让异常向上传播、也不影响规则式扫描已经拿到的结果。
    - `growth_signal_scan()` / `run_daily_cycle()` 新增可选 `llm_helper`
      形参（约定同 `generate_growth_report`），但只有
      `GrowthAdvisorConfig.llm_signal_augment_enabled=True` 时
      `run_daily_cycle()` 才会真正把它传给扫描函数——即使调用方处于有
      agent 上下文的场景（因而总能拿到 `llm_helper`），默认仍然按纯规则
      式运行，保持"`enabled=True` 默认开启不产生额外 LLM 成本"的底线不变。
    - CLI `/growth scan`/`/growth report`、API `POST /growth/scan` 新增/
      修正了把 `agent.llm_helper`（`LLMHelper` 实例，不可直接调用）包成
      `Callable[[str], str]` 闭包再传下去的逻辑——顺带修掉了 `/growth
      report` 里此前直接把 `LLMHelper` 实例当函数传给 `generate_growth_
      report` 的既有 bug（`LLMHelper` 没有 `__call__`，此前这条路径一旦
      真的没有已生成报告、需要现场生成，会在调用 `llm_helper(prompt)`
      时抛 `TypeError`，被 `generate_growth_report` 内部的 try/except 吞掉
      后静默回退模板——功能上不报错，但"LLM 优先起草"从未真正生效过）。

仍不在本次范围内（见方案 P3 剩余项，占位在 next_doc 文档里）：
    - 看板里的拖拽式看板视图（当前仍是列表 + 采纳/忽略两个动作）

P4-1 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次新增）
——关键词表持久化 + 看板展示 profile / 关键词信息：
    - `_effective_topic_keywords()`：运行时合并内置 `_TOPIC_KEYWORDS` +
      `profile.derived["growth_topic_keywords"]`（用户增量），减去
      `growth_topic_keywords_removed` 里标记隐藏的内置主题。
      `growth_signal_scan()` 改用它替代直接引用模块常量。
    - `_llm_augment_topics()` 归纳出的新主题会经 `_persist_learned_topics()`
      写入 `profile.derived["growth_topic_keywords"]`
      （`source="llm_learned", confirmed_by_user=False`），不再是"用完即弃"。
    - `add_custom_topic_keyword()` / `remove_topic_keyword()` /
      `confirm_topic_keyword()`：看板侧"➕ 添加自定义主题"/"❌ 删除"/
      "✅ 保留"三个操作对应的后端函数。
    - `diagnostics_snapshot()` 新增 `signal_scan.topics_detail`（带
      source/confirmed_by_user 的关键词表明细）与 `user_profile`
      （`UserProfile.derived` 的 summary/tech_stack/habits 只读快照，
      不含 preferences），供看板"Agent 对你的了解"区块渲染。
    - 前置修复（P4-0，见 `profile.py::UserProfileManager.generate()`）：
      画像生成从整体覆盖 `profile.derived` 改成合并式更新，避免
      `growth_focus_areas`/`growth_topic_keywords` 被定期画像刷新静默清空。

P4-2 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——关键词表"自动学习稳定后转正"：
    - `_update_keyword_learning_streaks()`：`growth_signal_scan()` 每次
      扫描结束时，对每个待确认的 `llm_learned` 主题更新连续命中计数
      （`consecutive_scan_hits`）——本次扫描命中则 +1，未命中则清零；
      连续命中达到 `_AUTO_CONFIRM_STREAK`（默认 3）次后自动把
      `confirmed_by_user` 置为 `True`（同时打上 `auto_confirmed=True`
      标记，供看板区分"用户手动保留"和"系统自动保留"），不需要用户
      记得去手动点确认。`user_added` 主题创建时已是确认状态，不参与
      这个计数。

P4-3 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——反馈学习细化 + 采纳后回访：
    - `_TOPIC_CATEGORIES` / `_category_of()` / `_category_dismiss_counts()`
      / `_category_feedback_multiplier()`：把内置主题粗分成"技术类/管理类/
      表达类"（未登记主题归"其他类"），同一类别下累计的 dismiss 次数会
      用比单主题衰减温和得多的系数（`_CATEGORY_DECAY_FACTOR=0.95`，下限
      `_MIN_CATEGORY_MULTIPLIER=0.7`）压低同类新主题的初始置信度，
      `growth_candidate_derive()` 里与原有的单主题 `_feedback_multiplier`
      相乘生效，两者独立衰减、互不覆盖。
    - `pending_followups()` / `record_followup()`：候选被采纳
      `GrowthAdvisorConfig.followup_review_days`（默认 30）天后，如果还
      没有回访记录，进入待回访列表；用户在看板上回答"progressed"（有
      推进）或"stalled"（没推进）后写回候选（`followup_status`）并追加
      到 `GrowthFeedbackLedger`（`action="followup_progressed"` /
      `"followup_stalled"`），回访只发生一次，不强制回答。
    - `_followup_adjustment_by_dedupe_key()`：把历史回访结果折算成按
      `dedupe_key` 的置信度调节系数（stalled 温和降权、progressed 温和
      加权、封顶 1.0），供同一方向因 dismiss 冷却结束后重新生成候选时
      参考，避免"确实采纳过、只是没空推进"的方向被当成和普通 dismiss
      同等强度的负面信号对待。

P4-4 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——报告质量分级 / 增量刷新：
    - `GrowthAdvisorConfig.report_quality_llm_enabled`：独立于
      `llm_signal_augment_enabled` 的另一个 opt-in 开关（默认关闭）——
      那个控制"扫描阶段要不要多花一次 LLM 归纳新主题"，这个控制
      "`run_daily_cycle()` 生成调研报告正文时要不要多花一次 LLM 调用换
      更高信息密度"；默认仍是零成本模板报告，两个开关互不影响。
    - `GrowthReport.evidence_count_at_generation`：生成报告那一刻候选的
      证据数快照；`reports_needing_refresh()` 拿候选当前证据数与这个
      快照比较，差值达到 `report_refresh_min_new_evidence`（默认 3）才
      认为"值得提示刷新"，避免证据每多 1 条就打扰用户。
    - `refresh_growth_report()`：为候选重新走一遍 `generate_growth_
      report()`，生成新报告并把候选的 `report_id` 指向新报告；旧报告
      不删除、不覆盖，只是不再是候选"当前挂着"的那份。

P4-5 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——通知策略细化：
    - `GrowthAdvisorConfig.category_notification_frequency`：按类别
      （"技术类"/"管理类"/"表达类"/"其他类"）覆盖推送偏好，目前只识别
      `"kanban_only"` 这一种覆盖值（把某个类别完全静音：仍在看板展示，
      但 `_maybe_dispatch_notification`/`_maybe_dispatch_weekly_digest`
      都不会主动推送这个类别的报告）——不支持给某个类别单独设置和全局
      不同的 daily/weekly_digest 频率，那需要拆分出按类别独立的节流
      状态，留给更明确的需求出现后再做。
    - `_category_acceptance_rate()` / `_notification_priority_score()`：
      多份报告都达到 `notification_min_confidence` 门槛时，不再单纯取
      置信度最高的一条，而是用"置信度 × 该类别历史采纳率加权"算一个
      优先级分数（历史采纳率高的类别加权最多到 1.3 倍，历史上常被忽略
      的类别打到 0.7 折，没有历史决策数据的类别按中性 0.5 处理，既不      加分也不减分），取优先级最高的一条推送。

P4-6 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——看板概念统一 + 趋势视图：
    - `diagnostics_snapshot()["topic_hit_counts_note"]`：显式文字说明
      诊断面板"最近一次扫描"命中计数跟 `growth_topic_map` 历史累计口径
      不同——不是合并成一个视图（两者语义确实不同，硬合并会丢信息），
      而是在两处都加提示，让用户知道"数字对不上是正常的"。
    - `growth_topic_trend_path`（新文件 `growth_topic_trend.jsonl`）：
      `growth_candidate_derive()` 每处理一个主题就追加一条快照
      （`evidence_count`/`confidence`/`scanned_at`），独立于
      `growth_backlog.jsonl`（那个只存当前状态，merge 会覆盖历史证据数，
      没法看走势）。`_topic_trend_series()` 按 `dedupe_key` 查询，最多
      回看最近 `_DEFAULT_TREND_MAX_POINTS`（20）个快照点。
    - `growth_topic_map()` 每行新增 `evidence_trend` 字段（该主题的
      走势序列），看板用文字箭头（↗/↘/→）渲染成一行"证据数走势"说明，
      没有引入图表库。

P4-7 范围（对应 next_doc/growth_advisor_improvement_plan_v2.md，本次
新增）——自定义黑名单细化：
    - 排查后发现 `remove_topic_keyword()` 早在 P3 就已经支持隐藏内置
      主题（写入 `growth_topic_keywords_removed`），`POST /growth/
      keywords/{topic}/remove` 的接口文档也一直写着"🙈 隐藏"——但看板
      从来没有实际渲染过内置主题的隐藏按钮，也没有地方能看到"我隐藏过
      哪些内置主题、想不想恢复"，是后端功能完整、前端 UI 一直缺失的
      情况，不是需要重新设计黑名单机制。
    - `hidden_builtin_topics(profile)`：返回当前被隐藏的内置主题列表。
    - `restore_builtin_topic_keyword(profile, topic)`：`remove_topic_
      keyword()` 的对称操作，把内置主题从黑名单摘掉——特意不复用
      `add_custom_topic_keyword()`（那个会把主题转成一条自定义关键词
      记录，需要用户重新填关键词，对"恢复内置主题"这个场景没必要，
      内置关键词本来就还在 `_TOPIC_KEYWORDS` 常量里）。
    - `diagnostics_snapshot()` 新增 `hidden_builtin_topics` 字段；
      API 新增 `POST /growth/keywords/{topic}/restore`；看板在内置主题
      列表下面加了"🙈 隐藏某个内置主题"折叠区块（逐个主题一个隐藏按钮）
      和"已隐藏的内置主题"列表（带"↩️ 恢复"按钮）。

P5 范围（对应 next_doc/growth_advisor_improvement_plan_v3.md，本次
新增，进行中）——跳出"补功能"视角的结构性盲区复盘：
    - P5-1（迁移期检查清单）：`GrowthReport.evidence_count_at_generation`
      默认值从 `0` 改为 `-1`（哨兵值，语义是"生成时的证据数快照缺失"，
      不是"生成时证据数真的是 0"），`reports_needing_refresh()` 显式
      跳过负值，修复 P4-4 上线当天旧报告被批量误判为"该刷新"的问题；
      同时把这类"给已落盘 dataclass 加字段"的通用检查项沉淀进
      `next_doc/dataclass_field_migration_checklist.md`，不止服务这一次
      修复。
    - P5-5（配置类型校验兜底）：`config/param_registry.py::load_nested_
      block()` 新增 dict 类型字段的显式校验，类型不匹配时回退默认值 +
      记 warning 日志，而不是原样透传导致脏值在某个随机调用点才报错
      （修复的是这次 review 发现的 `category_notification_frequency`
      被编辑器错误存成字符串这个真实场景的根因，不止 growth_advisor
      一个模块受益）。
    - P5-3（自定义/学习到的主题也能参与类别系统）：新增
      `GrowthAdvisorConfig.topic_category_llm_enabled`（默认关闭）+
      `classify_topic_category_llm()`（复用 `llm_helper` opt-in 模式，
      4 选 1 粗粒度分类，明确不引入 embedding）+
      `profile.derived["growth_topic_categories"]` 持久化；`_category_
      of()` 现在接受可选 `profile` 参数，命中已归类的自定义/学习到主题
      时不再统一落进"其他类"，类别级反馈学习（P4-3）/静音（P4-5）/
      推送优先级（P4-5）对这部分主题同样生效。触发点：`add_custom_
      topic_keyword()`/`confirm_topic_keyword()`（看板手动添加/确认）
      + `run_daily_cycle()`（cron 自动转正路径），均是可选 `cfg`/
      `llm_helper` 参数，不传时行为与改动前完全一致。
    - P5-0（数据生命周期 / 存储卫生，已完成）：
      - `growth_topic_trend.jsonl` 降采样：`compact_topic_trend_storage()`
        对超过 60 天的旧快照按"同一主题同一周只留最新一条"压缩，
        `growth_candidate_derive()` 每轮 cron 顺带调用。
      - `growth_reports_index.jsonl` 分层存储：`compact_reports_index_
        storage()` 把"不再是任何候选当前挂着的那份 + 生成超过 180 天"
        的旧报告移到新增的 `growth_reports.archive.jsonl`；新增
        `get_report_by_id()`（活跃索引查不到再查归档，避免旧报告链接
        变成 404）与 `list_reports(include_archived=True)`（累计统计
        用），修正了实现过程中发现的两处真实倒退风险（按 id 查报告
        404、`monthly_retrospective_summary()` 的报告总数计数变少）。
        不接入 `run_daily_cycle()` 自动触发，留给人工/未来月度 cron。
      - `growth_feedback_ledger.jsonl` 的分层存储（消费方全是累计统计
        语义，需要先转聚合计数再归档，改动量级更大）延后到下一轮。
    - P5-2（置信度模型引入"证据分布度"，已完成）：新增
      `_distribution_multiplier()`，按证据对应记忆条目的时间戳分桶
      （天粒度）计算分布度，接入 `growth_candidate_derive()` 的乘子
      叠加链（`topic × category × followup × distribution`）；时间戳
      单独存 `profile.derived["growth_evidence_timestamps"]`，由
      `growth_signal_scan()` 在扫描窗口内整体覆盖式写入，不改
      `evidence_refs` 本身的 `list[str]` 结构。
    - P5-4（回访/报告刷新接入被动信号，已完成）：`pending_followups()`
      到期候选先看一眼 `_topic_trend_rising()`（读 P4-6 的
      `growth_topic_trend.jsonl` 快照），窗口期内证据数在涨就跳过这次
      主动询问、顺延到下一轮（不持久化"已推迟"状态，纯按当次快照现算）；
      `followup_question_hint()` 给回访卡片挑更贴切的提问措辞。
      `reports_needing_refresh()` 新增 `recent_evidence_delta`（最近 14
      天内新增证据数，见 `_recent_evidence_delta`），排序优先级从单纯按
      `new_evidence` 总量改成"最近突增优先，总量其次"。
    - P5-6（候选生成排序里的"探索位"，已完成）：新增
      `GrowthAdvisorConfig.exploration_slot_enabled`（默认关闭）+
      `exploration_recent_window`（默认 5）。开启后 `run_daily_cycle()`
      的 `_select_candidates_for_reports()` 会在 `max_reports_per_run`
      名额里最多留 1 个给"最近几轮报告里没出现过的类别"（`_recent_
      report_categories()` 读 `list_reports()` 最近若干份算出类别集合，
      复用 P5-3 的 `_category_of()`），所有类别都已出现过则退化成正常
      按置信度选，不强行制造探索。选中的探索位候选生成报告时
      `generate_growth_report(..., is_exploration=True)`，正文/摘要各
      带一句"这是我们不太确定你会不会感兴趣的新方向"的标注
      （`GrowthReport.is_exploration` 字段透传给前端）。只动了 Top-N
      报告生成，未改推送优先级排序——探索位报告仍照常走
      `notification_min_confidence`/类别静音过滤，不额外强推。至此
      `growth_advisor_improvement_plan_v3.md` 的 P5-0 ~ P5-6 全部完成，
      进度以 `next_doc/growth_advisor_implementation_record.md` 的 P5
      章节为准。

P6 范围（对应 next_doc/growth_advisor_improvement_plan_v3.md「反馈粒度
细化 + LLM 增强路径可观测性」，本次新增）：
    - **反馈粒度细化**：`GrowthFeedbackLedger.record()` 新增可选
      `reason` 参数，dismiss 时可以说明原因（`DISMISS_REASON_NOT_
      INTERESTED` 不感兴趣 / `DISMISS_REASON_BAD_TIMING` 时机不对 /
      `DISMISS_REASON_REPORT_NOT_USEFUL` 方向没错但报告没写好 /
      `DISMISS_REASON_UNSPECIFIED` 未指定，默认值，兼容旧数据）。
      `_dismiss_counts_by_dedupe_key()` / `_category_dismiss_counts()`
      两个驱动置信度衰减的核心统计函数，都排除了
      `report_not_useful` 原因的记录——此前"方向不对"和"报告写得不好"
      被合并成同一个 dismiss 信号，会错误地永久压低一个其实有效、只是
      报告质量差的方向。新增 `_report_quality_dismiss_counts()` 单独
      统计报告质量信号（纯诊断用途，不参与任何置信度计算），接入
      `monthly_retrospective_summary()`（`report_quality_flags_total` /
      `top_report_quality_flags`）与看板月度复盘区块。CLI `/growth
      dismiss <id> [reason]` 与 API `POST /growth/candidates/{id}/
      dismiss`（body `{"reason": "..."}`）都支持传这个新参数，不传
      时行为与此前版本完全一致。看板列表视图新增忽略原因下拉选择；
      拖拽视图暂不支持指定原因（拖拽忽略记为 unspecified，看板文案有
      提示）。
    - **LLM 增强路径可观测性**：三个 opt-in LLM 调用点（`growth_signal_
      scan()` 的信号增强扫描、`generate_growth_report()` 的报告正文
      润色、`classify_topic_category_llm()` 的主题分类）此前调用失败
      只落一条 `log_exception` 或者原样吞掉退回默认路径，用户在诊断
      面板里完全看不出"这些我主动打开的增强开关，是不是真的在正常
      工作"。新增 `_record_llm_call_status()` / `llm_call_status_
      snapshot()`，把每次调用的结果（`success` / `no_new_topics` /
      `empty_response` / `skipped_insufficient_unmatched` /
      `parse_error` / `error`）记一份"最近一次"快照到
      `growth_advisor_state.json`，`diagnostics_snapshot()` 新增
      `llm_call_status` 字段透出。`classify_topic_category_llm()` /
      `maybe_classify_topic_category()` / `add_custom_topic_keyword()`
      / `confirm_topic_keyword()` 新增可选 `paths` 参数用于透传状态
      记录能力，默认 `None`（不记录），向后兼容。看板诊断面板新增
      「LLM 增强调用状态」区块，逐个调用点展示最近结果。

Goal/Cron 打通范围（对应 next_doc/growth_advisor_goal_cron_integration_
plan.md，本次新增）：
    - **阶段 A 对齐分析**：新增 `goal_growth_alignment()`，默认纯规则式
      关键词匹配（零 LLM 成本），比对"证据数达标的兴趣方向/已采纳
      候选"和 GoalBacklog 里的 Goal 标题，找出"有兴趣但没建目标"和
      "已建目标但停滞"（`GrowthAdvisorConfig.goal_alignment_stalled_
      days`，默认 21 天没 touch）两类方向。`goal_alignment_llm_
      enabled`（默认 `False`）打开后额外对规则匹配不上的双方做一次
      LLM 语义匹配（`_llm_match_interests_to_goals()`），命中的配对
      放进独立的 `llm_suggested_matches`（建议，不自动写入关联关系，
      幻觉 id 会被过滤），调用结果计入 `goal_alignment_match` LLM 调用
      状态。`diagnostics_snapshot()` 新增 `goal_alignment` 计数字段，
      CLI 新增 `/growth align`。
    - **阶段 B 一键落地**：新增 `adopt_candidate_as_goal()`，把一个
      已有调研报告的候选创建成 GoalBacklog Goal（`description` 用
      报告摘要 + 路径引用），候选反向记 `linked_goal_id`（新增字段，
      旧数据反序列化兜底为 `None`）；`GrowthBacklog.set_linked_goal()`
      负责这次写入。用户显式触发（CLI `/growth adopt-goal <id>`），
      不在 `run_daily_cycle()` 里自动发生。
    - **阶段 C 回访读取 Goal 真实状态**：`pending_followups()` /
      `followup_question_hint()` 新增可选 `goal_backlog` 参数，候选
      已关联 Goal 时优先用 `_goal_progress_signal()` 判断（Goal
      `completed` → 直接记 progressed，不占用一次询问；`active` 且
      近期有 touch → 顺延；停滞/暂停/放弃/失败/取消 → 正常展示回访
      卡片，措辞换成 Goal 专属提示）；未关联 Goal 或未传
      `goal_backlog` 时完全退化为原有 memory 证据数走势逻辑，向后
      兼容，`api/routes.py` 的 `/v1/growth/followups` 已同步升级。

调研信息获取与整理范围（对应
next_doc/growth_advisor_research_quality_plan.md，本次新增）：
    - **外部资讯从计数升级为摘录**：`_external_signal_count_for_topic()`
      重构为 `_external_signal_matching_pages()`（找命中页面）+
      `len()`，行为不变；新增 `_external_signal_excerpts_for_topic()`
      复用同一段匹配逻辑，额外截取正文摘录。`generate_growth_report()`
      在 `report_include_external_context=True` 时把摘录拼进 prompt，
      要求 LLM 引用处标注来源（`（参考：页面id）`），不新增配置开关，
      是对既有开关的行为增强。
    - **忽略原因驱动针对性调整**：新增 `report_dismiss_reason_
      adaptive_enabled`（默认 `True`），复用已有的 `_report_quality_
      dismiss_counts()`，命中 `report_not_useful` 历史时在 prompt
      追加"避免空泛"的强约束，不产生新的 LLM 调用。
    - **两段式生成（先提纲、后填充）**：新增 `report_two_stage_
      enabled`（默认 `False`）+ `_generate_report_outline()`，打开后
      先让 LLM 提炼 3-4 个具体问题（`_REPORT_OUTLINE_MAX_QUESTIONS`
      上限）再逐一回答正文，替代固定四段式；提纲阶段失败/空响应/
      解析失败都静默退回单段式 prompt。调用状态计入新增的
      `report_outline` LLM 调用类型，接入诊断面板既有区块。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

# 与 objective_outcome_tracker.normalize_title_key 保持完全一致的去重规则，
# 两处独立实现是为了不引入 evolution 内部模块间的横向依赖（该函数本身
# 已经是从 soft_goal_deriver 抽出来复用的稳定契约，这里直接复制其算法）。
def normalize_title_key(title: str) -> str:
    s = (title or "").lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(sorted(s.split()))


def _llm_find_duplicate_direction(
    new_title: str,
    existing_titles: list[str],
    llm_helper: Callable[[str], str],
) -> Optional[str]:
    """[候选去重 LLM 语义判重] 判断 `new_title` 是否和 `existing_titles`
    里的某一个本质上是同一个方向，只是措辞不同（精确标题去重
    `normalize_title_key` 已经在调用方处理过字面重复，这里只处理"没有
    字面重复，但明显是同一件事"的情况，比如"学习 Rust 异步编程"和
    "掌握 Rust async/await"）。

    `existing_titles` 混合了两类候选方向的标题——当前 pending/accepted
    的 GrowthCandidate 和已采纳为 Goal 的方向，调用方（`add_or_merge`）
    负责区分匹配结果属于哪一类、该合并证据还是直接跳过，这个函数本身
    只做纯粹的语义匹配判断，不关心匹配对象的类型。

    命中返回 `existing_titles` 里那个原始字符串（不是改写/归一化后的
    版本，方便调用方直接拿去查表）；LLM 判定没有重复（约定输出
    `NONE`）、输出解析不出、列表为空、或调用异常时返回 `None`——和
    `draft_outline_with_llm()`/`generate_outline_suggestion_from_answer()`
    同款"起草辅助而非关键路径"的克制：判断失败时退回"当作不重复"，
    最坏情况是多生成一条候选（用户可以手动 dismiss），比误判成"重复"
    进而悄悄丢掉一个用户真正关心的新方向成本更低。

    不做多轮重试、不做批量/embedding 相似度——`existing_titles` 量级
    (max_pending_candidates + 活跃 Goal 数量，通常几十以内) 一次性列进
    prompt 交给 LLM 判断即可，没有必要引入额外的检索基础设施。
    """
    if not existing_titles:
        return None
    try:
        listed = "\n".join(f"- {t}" for t in existing_titles)
        prompt = (
            f"下面是一个人已经在关注/推进的一些成长方向：\n{listed}\n\n"
            f"现在有一个新提议的方向：「{new_title}」\n\n"
            "这个新方向和上面列表中的某一个是否本质上是同一件事（只是"
            "措辞、范围表述不同），而不是一个真正新的、值得单独列出的"
            "方向？如果是，请只输出上面列表中那一项**完全一致**的原文"
            "（逐字复制，不要改写、不要加编号或标点）。如果不是（这是"
            "一个真正新的方向，或者只是相关但不算同一件事），请只输出"
            "NONE。不要输出除以上两种情况之外的任何内容。"
        )
        raw = llm_helper(prompt)
    except Exception:
        return None
    if not raw or not raw.strip():
        return None
    answer = raw.strip().splitlines()[0].strip()
    if answer.upper() == "NONE":
        return None
    # 严格要求逐字匹配列表中的原文，不接受近似匹配——LLM 输出格式漂移
    # （比如多加了引号/编号）时，宁可判定为"没找到匹配"退回不重复，也
    # 不去猜它想指哪一个，理由同函数文档字符串。
    for t in existing_titles:
        if t.strip() == answer:
            return t
    return None


JOB_ID_DAILY = "sys:growth_advisor_daily"
JOB_ID_MONTHLY = "sys:growth_monthly_retrospective"

# 候选状态机：pending -> accepted | dismissed；超过 TTL 未处理 -> expired
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DISMISSED = "dismissed"
STATUS_EXPIRED = "expired"

_VALID_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED, STATUS_DISMISSED, STATUS_EXPIRED)

# pending 候选超过这么多天没人处理，下次扫描时自动标记为 expired
# （避免看板里堆积"已经不新鲜"的建议，呼应方案第 8 节"克制"原则）。
PENDING_TTL_DAYS = 45

# 一条 memory entry 的 tag 至少要在窗口内出现这么多次，才有资格被当作
# "growth_focus_area"候选主题（与 decision_profile_builder 的
# MIN_EVIDENCE_COUNT 同量级但独立配置，通过 GrowthAdvisorConfig 传入）。
_DEFAULT_MIN_EVIDENCE_COUNT = 3

# 信号扫描只看最近这么多天的记忆，避免陈年旧事一直反复被提起
SIGNAL_SCAN_WINDOW_DAYS = 90


# ────────────────────────── 数据模型 ──────────────────────────


@dataclass
class GrowthCandidate:
    candidate_id: str
    title: str
    rationale: str                       # 为什么值得关注（面向用户的一句话）
    evidence_refs: list[str] = field(default_factory=list)   # memory entry_id 列表
    evidence_count: int = 0
    confidence: float = 0.0              # 0~1，由 evidence_count 归一化得到
    status: str = STATUS_PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    report_id: Optional[str] = None      # 生成过调研报告后回填
    # [P4-3] 采纳后回访：accepted_at 记录首次被 set_status(accepted) 的时间
    # （只在从非 accepted 状态转入时写一次，之后即便 attach_report 等操作
    # 更新 updated_at 也不会覆盖它，保证 30 天窗口计算的是"何时被采纳"而
    # 不是"最后一次被改动"）；followup_status 为 None 表示尚未回访，
    # "progressed"/"stalled" 为用户回答后的结果，回访只发生一次。
    accepted_at: Optional[float] = None
    followup_status: Optional[str] = None
    # [growth_advisor_goal_cron_integration_plan.md 阶段 B] 这个候选
    # 是否已经通过 `adopt_candidate_as_goal()` 落地成一个 GoalBacklog
    # 里的 Goal 节点——`None` 表示尚未落地（绝大多数候选的常态）。
    # 落地后的候选行为不变（仍然可以被 dismiss/继续走原有回访路径），
    # 只是阶段 C 的回访逻辑会优先参考这个 Goal 的真实状态。旧数据反
    # 序列化时缺该字段，`from_dict` 走既有的已知字段兜底默认值机制，
    # 自然落到 `None`，等价于"未落地"，不需要额外迁移。
    linked_goal_id: Optional[str] = None
    # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 3]
    # 这条候选的信号来源标记，只在**创建时**写入、合并证据时不覆盖
    # （见 `GrowthBacklog.add_or_merge`）——`"signal_scan"`（默认值，
    # 绝大多数候选：来自对话记忆的关键词命中）或 `"pursuit_spinoff"`
    # （来自另一个正在推进方向的 `open_questions` 反复出现，见
    # `extract_spinoff_topics_from_pursuits()`）。纯展示用途，不参与
    # 任何排序/置信度计算；旧数据反序列化时缺该字段，`from_dict` 落到
    # 默认值 `"signal_scan"`，等价于"按原有口径来自信号扫描"，不需要
    # 额外迁移。
    origin: str = "signal_scan"
    # [growth_advisor_autonomous_search_and_material_improvement_plan.md
    # 方向"报告与学习素材分层"] 生成过学习素材后回填，跟 `report_id`
    # 是两个独立字段——一个候选可能只有报告没有素材（用户还在"值不值得
    # 投入"的决策阶段），也可能报告和素材都有（已经决定投入、要开始
    # 执行了）。`None` 表示尚未生成过学习素材，旧数据反序列化时缺该
    # 字段自然落到 `None`，不需要额外迁移。
    material_id: Optional[str] = None

    def dedupe_key(self) -> str:
        return normalize_title_key(self.title)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GrowthCandidate":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class GrowthReport:
    report_id: str
    candidate_id: str
    title: str
    slug: str
    summary: str                         # 报告摘要（看板里展示用）
    body_path: str                       # 相对/绝对路径，正文落在 wiki_growth_dir
    created_at: float = field(default_factory=time.time)
    source: str = "template"             # "template" | "llm"
    # [P4-4][P5-1 修正] 生成这份报告时候选的证据数快照，供
    # `reports_needing_refresh()` 判断"生成之后又新增了多少证据"，决定是否
    # 提示用户"要不要更新一下"。
    #
    # 哨兵值说明：默认值曾经是 `0`，导致 P4-4 上线当天所有此前生成的旧报告
    # （`from_dict()` 反序列化时这个字段在旧数据里缺失，落到默认值 `0`）
    # 被下游误判为"证据从 0 涨到了现在这么多，该刷新了"，触发一批批量误报
    # ——这不是 bug，是新增字段时"老数据在默认值下会被怎么解读"没有想清楚
    # 的一次真实案例，见 next_doc/growth_advisor_improvement_plan_v3.md
    # P5-1。现在默认值改成 `-1`（"生成时的证据数快照缺失"的哨兵值，而不是
    # "生成时证据数真的是 0"），`reports_needing_refresh()` 遇到负值直接
    # 跳过，不计入待刷新列表。新生成的报告（见 `generate_growth_report()`）
    # 永远显式传入真实的 `candidate.evidence_count`（>= 0），只有反序列化
    # 旧数据时才会落到这个哨兵默认值。
    evidence_count_at_generation: int = -1
    # [P5-6] 这份报告是否来自"探索位"（growth_advisor_improvement_plan_v3.md
    # P5-6）——探索位是从达标候选里，为最近几轮报告里没出现过的类别特意
    # 留出的一个名额，不是置信度/证据最强的那个。默认值 `False` 对旧数据/
    # 未开启探索位开关时生成的报告完全透明（跟改动前行为一致），只有
    # `run_daily_cycle` 在 `cfg.exploration_slot_enabled=True` 且确实选中
    # 了探索位候选时才会显式传 `True`。
    is_exploration: bool = False
    # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 7]
    # 这份报告是否因为该方向此前被反复标"报告没写好"
    # （`DISMISS_REASON_REPORT_NOT_USEFUL`）而自动升级为 LLM 生成——
    # 区别于 `source="llm"`（可能只是全局 `report_quality_llm_enabled`
    # 打开导致，不代表这里有过负反馈）。默认 `False`，旧数据反序列化
    # 时缺该字段自然落到 `False`，等价于"不是因为质量信号被升级的"，
    # 不需要额外迁移。纯展示/诊断用途，不参与任何排序计算。
    quality_auto_upgraded: bool = False
    # [阶段三：生成后自检，growth_advisor_autonomous_search_and_material_
    # improvement_plan.md 第 4 节] 正文写完后，核对是否真的引用了拼进
    # prompt 的外部摘录、有没有编造一个摘录列表里不存在的引用来源。
    # 只有 `report_include_external_context` 开启且这次确实拿到了非空
    # 摘录列表、正文由 LLM 生成时才会计算，取值：
    #   {"excerpts_total": int,      # 拼进 prompt 的摘录总数
    #    "cited_count": int,         # 正文里出现引用、且能对上摘录 id 的条数
    #    "citation_mentions_total": int,  # 正文里『（参考：xxx）』的总次数
    #    "hallucinated_refs": list[str]}  # 正文引用了但对不上任何摘录 id 的原文片段（截断展示，非结构化诊断用途，不参与任何排序/巩固判断）
    # 默认 `None`：没开启外部背景、没有摘录、或正文走的是规则模板兜底，
    # 三种情况都表示"这次没有可核对的引用"，不是"核对通过"，前端/CLI
    # 展示时应区分对待。旧数据反序列化缺该字段时也落到 `None`，向后兼容。
    citation_check: Optional[dict] = None
    # [growth_advisor_autonomous_search_and_material_improvement_plan.md
    # 方向"外部世界变化驱动的刷新"] 生成这份报告时实际拼进 prompt 的
    # 外部摘录的轻量指纹——`[{"id": 页面id, "hash": 摘录内容前 12 位
    # md5}]`，只在 `report_include_external_context` 开启且这次确实
    # 拿到非空摘录时写入，其余情况为 `None`。跟 `citation_check`（核对
    # LLM 有没有如实标注引用）回答的是不同问题：这个字段回答"生成之后，
    # 外部世界（本地已抓取到的那部分）有没有发生变化"，供
    # `reports_needing_refresh()` 在 `report_external_drift_refresh_
    # enabled` 开启时比对使用。只存 id + 内容指纹，不存摘录原文，避免
    # 索引文件无限增长。旧数据反序列化缺该字段时落到 `None`，等价于
    # "这份报告没有可比对的外部摘录基线"，不会被误判为"世界变了"。
    external_excerpt_fingerprint: Optional[list[dict]] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GrowthReport":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class GrowthLearningMaterial:
    """[growth_advisor_autonomous_search_and_material_improvement_plan.md
    方向"报告与学习素材分层"] "报告"（`GrowthReport`）回答"值不值得
    投入"，是决策向的简报；这个类回答"投入之后怎么学"，是执行向的
    结构化产物——固定三段：学习路径（有序步骤）、资源清单、第一个可
    执行任务。两者是平行但独立的产物，不是同一份文档的不同版本，故意
    不共用一个 dataclass（字段语义不同：报告是自由格式 Markdown 正文
    + 摘要，素材是结构化字段 + 拼出来的正文）。
    """
    material_id: str
    candidate_id: str
    title: str
    slug: str
    # 有序学习步骤，每步一句话（例如"先读官方 quickstart，跑通一个最小
    # 示例"），供 CLI/看板直接渲染成一个有序列表，不需要额外解析正文。
    learning_path: list[str] = field(default_factory=list)
    # 资源清单：每条是一句话描述（可能包含链接），跟 learning_path 平行
    # 但独立——不是每个学习步骤都对应一条资源，也不强制一一对应。
    resources: list[str] = field(default_factory=list)
    # 建议现在就能动手做的第一个具体任务（不是"了解一下"这种笼统建议），
    # 呼应改进计划里"结构化路径 + 资源清单 + 首个可执行任务"的定位。
    first_task: str = ""
    body_path: str = ""          # 正文落在 wiki_growth_dir 下的 Markdown 文件
    created_at: float = field(default_factory=time.time)
    source: str = "template"     # "template" | "llm"
    # 生成这份素材时依据的报告 id（如果是从一份已有报告"升级"而来），
    # 素材也可以在没有报告的情况下独立生成（比如用户跳过报告直接要
    # 学习素材），此时为 `None`——不强制素材依赖报告存在。
    based_on_report_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GrowthLearningMaterial":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ────────────────────────── JSONL 存取工具 ──────────────────────────


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text(text + ("\n" if rows else ""), encoding="utf-8")


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ────────────────────────── GrowthBacklog ──────────────────────────


class GrowthBacklog:
    """候选队列的读写封装，落盘为 `growth_backlog.jsonl`（每次整表重写，
    数据量级是"用户成长方向候选"，天然不大，不需要 append-only）。
    """

    def __init__(self, paths) -> None:
        self._paths = paths
        self._path = paths.growth_backlog_path

    def load_all(self) -> list[GrowthCandidate]:
        return [GrowthCandidate.from_dict(d) for d in _read_jsonl(self._path)]

    def save_all(self, candidates: list[GrowthCandidate]) -> None:
        _write_jsonl(self._path, [c.to_dict() for c in candidates])

    def pending(self) -> list[GrowthCandidate]:
        return [c for c in self.load_all() if c.status == STATUS_PENDING]

    def get(self, candidate_id: str) -> Optional[GrowthCandidate]:
        for c in self.load_all():
            if c.candidate_id == candidate_id:
                return c
        return None

    def expire_stale(self, ttl_days: int = PENDING_TTL_DAYS) -> int:
        """把超过 ttl_days 还是 pending 的候选标记为 expired，返回处理条数。"""
        cutoff = time.time() - ttl_days * 86400
        all_c = self.load_all()
        n = 0
        for c in all_c:
            if c.status == STATUS_PENDING and c.created_at < cutoff:
                c.status = STATUS_EXPIRED
                c.updated_at = time.time()
                n += 1
        if n:
            self.save_all(all_c)
        return n

    def add_or_merge(
        self,
        title: str,
        rationale: str,
        evidence_refs: list[str],
        *,
        min_evidence_count: int,
        max_pending: int,
        dismissed_cooldown_days: int,
        confidence_multiplier: float = 1.0,
        origin: str = "signal_scan",
        llm_helper: Optional[Callable[[str], str]] = None,
        existing_goal_titles: Optional[list[str]] = None,
    ) -> Optional[GrowthCandidate]:
        """尝试新增一条候选。规则（对应方案第 3 节"克制"要求）：
            - evidence_refs 数量不达标 → 不生成，返回 None
            - 已存在同 dedupe_key 的 pending/accepted 候选 → 合并证据、
              不重复创建
            - [本次新增] 没有字面重复，但 `llm_helper` 判定和某个已存在
              的 pending/accepted 候选或已采纳的 Goal 是同一个方向 →
              命中候选时合并证据（同字面去重分支）；只命中 Goal（没有
              对应候选）时直接跳过，不创建——这个方向已经在推进了，不
              需要再单独生成一条候选来"提醒"用户。`llm_helper` 为
              `None`（未开启 `duplicate_direction_llm_check_enabled` 或
              拿不到 LLM 上下文）时这一步整体跳过，行为与改动前完全
              一致，只保留原有的精确标题去重。
            - 曾被 dismissed 且仍在冷却期内 → 跳过，返回 None
            - pending 数量已达上限 → 跳过，返回 None（避免无限堆积）

        `origin`：[方向 3] 只在真正**新建**候选时写入 `GrowthCandidate.
        origin`；命中已有候选走合并分支时保留原候选的 origin 不变
        （一个话题最初来自信号扫描、后来恰好也被 spinoff 命中，不应该
        把来源标记改写成 spinoff——先到先得，origin 只反映"这个话题
        第一次被发现"的来源）。
        """
        if len(evidence_refs) < min_evidence_count:
            return None

        key = normalize_title_key(title)
        all_c = self.load_all()

        for c in all_c:
            if c.dedupe_key() != key:
                continue
            if c.status in (STATUS_PENDING, STATUS_ACCEPTED):
                merged = sorted(set(c.evidence_refs) | set(evidence_refs))
                c.evidence_refs = merged
                c.evidence_count = len(merged)
                c.confidence = _confidence_from_evidence(c.evidence_count)
                c.updated_at = time.time()
                self.save_all(all_c)
                return c
            if c.status == STATUS_DISMISSED:
                cooldown_cutoff = time.time() - dismissed_cooldown_days * 86400
                if c.updated_at > cooldown_cutoff:
                    return None  # 冷却期内，不重新生成

        if llm_helper is not None:
            active_candidates = {
                c.title: c for c in all_c if c.status in (STATUS_PENDING, STATUS_ACCEPTED)
            }
            candidate_titles = list(active_candidates.keys())
            goal_titles = list(dict.fromkeys(existing_goal_titles or []))
            # 候选标题排在前面：命中候选比命中 Goal 更常见（Goal 数量
            # 通常远小于历史候选量），排列顺序本身不影响 LLM 判断，只是
            # 沿用"先到先得"的一贯习惯，不承载额外语义。
            all_titles = candidate_titles + [t for t in goal_titles if t not in active_candidates]
            try:
                match = _llm_find_duplicate_direction(title, all_titles, llm_helper)
            except Exception as exc:
                from mini_agent.errors import log_exception
                log_exception(exc, where="mini_agent.growth_advisor.add_or_merge_duplicate_check")
                match = None
            if match is not None:
                matched_candidate = active_candidates.get(match)
                if matched_candidate is not None:
                    merged = sorted(set(matched_candidate.evidence_refs) | set(evidence_refs))
                    matched_candidate.evidence_refs = merged
                    matched_candidate.evidence_count = len(merged)
                    matched_candidate.confidence = _confidence_from_evidence(len(merged))
                    matched_candidate.updated_at = time.time()
                    self.save_all(all_c)
                    return matched_candidate
                # 只命中已采纳的 Goal，没有对应的候选可合并证据——这个
                # 方向已经在推进中，不生成新候选，直接跳过。
                return None

        pending_count = sum(1 for c in all_c if c.status == STATUS_PENDING)
        if pending_count >= max_pending:
            return None

        base_confidence = _confidence_from_evidence(len(set(evidence_refs)))
        cand = GrowthCandidate(
            candidate_id=uuid.uuid4().hex[:12],
            title=title,
            rationale=rationale,
            evidence_refs=sorted(set(evidence_refs)),
            evidence_count=len(set(evidence_refs)),
            # confidence_multiplier < 1.0 时说明这个方向此前被 dismiss 过
            # （见 _feedback_multiplier），新建候选默认置信度打折但不清零。
            confidence=round(base_confidence * confidence_multiplier, 3),
            origin=origin,
        )
        all_c.append(cand)
        self.save_all(all_c)
        return cand

    def set_status(self, candidate_id: str, status: str) -> Optional[GrowthCandidate]:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        all_c = self.load_all()
        for c in all_c:
            if c.candidate_id == candidate_id:
                c.status = status
                c.updated_at = time.time()
                # [P4-3] 只在首次转入 accepted 时打时间戳，重复 accept（理论上
                # 不应该发生，但幂等处理更安全）不会把 accepted_at 往后推。
                if status == STATUS_ACCEPTED and c.accepted_at is None:
                    c.accepted_at = c.updated_at
                self.save_all(all_c)
                return c
        return None

    def attach_report(self, candidate_id: str, report_id: str) -> None:
        all_c = self.load_all()
        for c in all_c:
            if c.candidate_id == candidate_id:
                c.report_id = report_id
                c.updated_at = time.time()
        self.save_all(all_c)

    def attach_material(self, candidate_id: str, material_id: str) -> None:
        """[growth_advisor_autonomous_search_and_material_improvement_
        plan.md 方向"报告与学习素材分层"] 跟 `attach_report()` 是同一种
        写入模式——生成一份学习素材后把 `material_id` 回填到候选上，
        供 CLI/API"这个候选有没有学习素材"的判断直接读候选字段，不用
        每次都去扫素材索引。"""
        all_c = self.load_all()
        for c in all_c:
            if c.candidate_id == candidate_id:
                c.material_id = material_id
                c.updated_at = time.time()
        self.save_all(all_c)

    def set_linked_goal(self, candidate_id: str, goal_id: str) -> Optional[GrowthCandidate]:
        """[growth_advisor_goal_cron_integration_plan.md 阶段 B] 把候选
        跟一个已创建的 GoalBacklog Goal 节点关联起来，供后续对齐分析/
        回访逻辑读取。不校验 `goal_id` 是否真实存在——那是调用方
        （`adopt_candidate_as_goal()`）的职责，这里只是一次纯粹的
        字段写入，保持跟 `attach_report()` 同等的简单程度。
        """
        all_c = self.load_all()
        found = None
        for c in all_c:
            if c.candidate_id == candidate_id:
                c.linked_goal_id = goal_id
                c.updated_at = time.time()
                found = c
        if found is not None:
            self.save_all(all_c)
        return found


def _confidence_from_evidence(evidence_count: int, cap: int = 8) -> float:
    """证据条数 → 0~1 置信度的简单饱和映射（超过 cap 条封顶为 1.0）。"""
    return round(min(evidence_count, cap) / cap, 3)


# ────────────────────────── GrowthFeedbackLedger ──────────────────────────


class GrowthFeedbackLedger:
    """用户对候选/报告的采纳/忽略流水（append-only），供未来（P2）用于
    调整同类候选的置信度权重——本次先只落盘，不做加权，避免过度设计。
    """

    def __init__(self, paths) -> None:
        self._path = paths.growth_feedback_ledger_path

    def record(
        self, candidate_id: str, action: str, *, note: str = "", reason: Optional[str] = None
    ) -> None:
        """记一条反馈流水。

        [反馈粒度细化] `reason` 仅在 `action == STATUS_DISMISSED` 时有意义，
        取值见 `_VALID_DISMISS_REASONS`：区分"这个方向我不关心/时机不对"
        （方向级负向信号，会参与置信度衰减）与"方向没错，只是这份报告写得
        不好"（`DISMISS_REASON_REPORT_NOT_USEFUL`，不参与方向/类别衰减，
        只计入报告质量诊断，见 `_report_quality_dismiss_counts`）。不传
        （`None`）等价于历史上没有这个字段时的行为——记为
        `DISMISS_REASON_UNSPECIFIED`，仍然参与衰减，保证旧数据 / 旧调用方
        不传这个新参数时行为完全不变。
        """
        if action == STATUS_DISMISSED:
            reason = reason or DISMISS_REASON_UNSPECIFIED
            if reason not in _VALID_DISMISS_REASONS:
                raise ValueError(f"invalid dismiss reason: {reason}")
        else:
            reason = None
        _append_jsonl(
            self._path,
            {
                "candidate_id": candidate_id,
                "action": action,          # "accepted" | "dismissed"
                "reason": reason,          # None | 见 _VALID_DISMISS_REASONS
                "note": note,
                "ts": time.time(),
            },
        )

    def all_entries(self) -> list[dict]:
        return _read_jsonl(self._path)


# ────────────────────────── P2：反馈驱动的置信度调权 ──────────────────────────

# 每被 dismiss 一次，新建候选的默认置信度衰减为原来的这个比例（复利式衰
# 减，而不是线性扣分，理由是"第 1 次忽略"和"第 5 次忽略"传达的信号强度
# 显然不该线性对待）。下限见 _MIN_FEEDBACK_MULTIPLIER——不会打到 0，避免
# "用户当时忙、后来又感兴趣"被永久拒绝（方案第 6 节明确要求）。
_DISMISS_DECAY_FACTOR = 0.85
_MIN_FEEDBACK_MULTIPLIER = 0.4

# [反馈粒度细化] dismiss 原因枚举。"方向级负向信号"（会压低同方向/同
# 类别未来置信度）与"报告质量信号"（不影响置信度，只影响是否值得改进
# 报告生成方式）分开统计——此前两者被合并成同一个 dismiss 计数，导致
# "候选被用户 dismiss，是因为方向不对，还是因为报告写得不痛不痒"这两种
# 完全不同的情况被同等地拿去衰减方向置信度，可能错误地永久压低一个其实
# 有效、只是报告质量差的方向。
DISMISS_REASON_NOT_INTERESTED = "not_interested"      # 这个方向我不关心
DISMISS_REASON_BAD_TIMING = "bad_timing"               # 方向可以，但现在不是时候
DISMISS_REASON_REPORT_NOT_USEFUL = "report_not_useful"  # 方向没错，报告没写好
DISMISS_REASON_ALREADY_EXISTS = "already_exists"       # 和已有方向重复，不是新方向
DISMISS_REASON_UNSPECIFIED = "unspecified"             # 未指定原因（兼容旧数据/旧调用方）
_VALID_DISMISS_REASONS = frozenset(
    {
        DISMISS_REASON_NOT_INTERESTED,
        DISMISS_REASON_BAD_TIMING,
        DISMISS_REASON_REPORT_NOT_USEFUL,
        DISMISS_REASON_ALREADY_EXISTS,
        DISMISS_REASON_UNSPECIFIED,
    }
)
# 参与"方向/类别置信度衰减"的 dismiss 原因——REPORT_NOT_USEFUL 不在这
# 个集合里，是本次改动的核心：它不代表用户对这个方向不感兴趣。
# ALREADY_EXISTS 同理不在集合里：用户忽略是因为"这条候选和我已经在做的
# 事重复了"，不是对这个方向本身不感兴趣，不应该压低同方向/同类别未来
# 的置信度——否则等于因为流程重复生成的问题惩罚了一个用户明明感兴趣的
# 方向。
_DIRECTION_NEGATIVE_DISMISS_REASONS = frozenset(
    {DISMISS_REASON_NOT_INTERESTED, DISMISS_REASON_BAD_TIMING, DISMISS_REASON_UNSPECIFIED}
)


def _dismiss_counts_by_dedupe_key(paths) -> dict[str, int]:
    """统计每个 dedupe_key（归一化标题）历史上被"方向级负向原因"
    dismiss 过多少次（`DISMISS_REASON_REPORT_NOT_USEFUL` 不计入，见上）。

    GrowthFeedbackLedger 只记录 candidate_id，需要反查 backlog 里对应
    候选的标题才能归一化到 dedupe_key——包括已经不在 pending 状态、甚至
    早已被 expire_stale 清理状态的旧候选（backlog 是整表重写，历史记录
    仍在文件里，只是 status 字段变了），因此这里读全量 load_all()。
    """
    id_to_key = {c.candidate_id: c.dedupe_key() for c in GrowthBacklog(paths).load_all()}
    counts: dict[str, int] = {}
    for entry in GrowthFeedbackLedger(paths).all_entries():
        if entry.get("action") != STATUS_DISMISSED:
            continue
        if entry.get("reason", DISMISS_REASON_UNSPECIFIED) not in _DIRECTION_NEGATIVE_DISMISS_REASONS:
            continue
        key = id_to_key.get(entry.get("candidate_id"))
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _report_quality_dismiss_counts(paths) -> dict[str, int]:
    """[反馈粒度细化] 按候选标题统计"方向没错、报告没写好"
    （`DISMISS_REASON_REPORT_NOT_USEFUL`）的次数——纯诊断用途，不参与
    任何置信度计算。用于在月度复盘 / 诊断面板里回答"哪些方向的报告质量
    该优先改进"，跟"哪些方向该少推荐"是两个独立的问题。用原始标题
    （而不是归一化的 dedupe_key）计数，跟 `monthly_retrospective_summary`
    里其它 `top_*_topics` 排行的展示口径保持一致。
    """
    id_to_title = {c.candidate_id: c.title for c in GrowthBacklog(paths).load_all()}
    counts: dict[str, int] = {}
    for entry in GrowthFeedbackLedger(paths).all_entries():
        if entry.get("action") != STATUS_DISMISSED:
            continue
        if entry.get("reason") != DISMISS_REASON_REPORT_NOT_USEFUL:
            continue
        title = id_to_title.get(entry.get("candidate_id"))
        if title:
            counts[title] = counts.get(title, 0) + 1
    return counts


# ────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 7] 报告质量自动闭环 ──────────
# 现状：`_report_quality_dismiss_counts()` 已经在记录"哪些方向的报告
# 被反馈写得不好"，但此前只是"记录下来给人看"（月度复盘/诊断面板），
# 从没被用来反过来指导报告生成策略本身。这里补一层最小的自动闭环：
# 某个方向的报告被标"内容太笼统"累计达到阈值时，下一次生成自动临时
# 切到 LLM 生成路径（即便全局 `report_quality_llm_enabled` 是关闭
# 的），而不是继续用固定模板反复产出同样质量的内容。

def _should_auto_upgrade_report_quality(paths, candidate: GrowthCandidate, cfg=None) -> bool:
    """[方向 7] 只读判断：这个候选的下一份报告要不要因为质量信号自动
    升级为 LLM 生成。`cfg.report_quality_auto_upgrade_enabled=False`
    （默认）时直接返回 `False`，零成本、零行为变化——这是一个新增的
    LLM 调用触发点，对齐"增加调用成本的能力默认关闭"的一贯原则。开启
    后，只有该方向的 `report_not_useful` 累计次数达到
    `cfg.report_quality_auto_upgrade_threshold`（默认 2）才返回
    `True`；调用方是否真的有 `llm_helper` 可用仍由调用方自己判断，本
    函数只回答"要不要"，不管"能不能"。
    """
    if not getattr(cfg, "report_quality_auto_upgrade_enabled", False):
        return False
    threshold = getattr(cfg, "report_quality_auto_upgrade_threshold", 2)
    if threshold is None or threshold <= 0:
        return False
    counts = _report_quality_dismiss_counts(paths)
    return counts.get(candidate.title, 0) >= threshold


def _feedback_multiplier(dismiss_count: int) -> float:
    if dismiss_count <= 0:
        return 1.0
    return max(_MIN_FEEDBACK_MULTIPLIER, round(_DISMISS_DECAY_FACTOR ** dismiss_count, 3))


# ────────── [P4-3] 按主题类别聚合的反馈学习 ──────────
# next_doc/growth_advisor_improvement_plan_v2.md P4-3 第一条：连续忽略同一
# 类别下的多个主题，应该影响同类新主题的初始置信度，而不是各自独立衰减。
# 内置主题按语义分成三个粗类别，未登记的主题（用户自定义 / LLM 学到）统一
# 归为"其他"——不强行归类，避免猜错类别反而引入噪音。类别信号天然比单
# 主题信号弱（用户忽略"Python 工程实践"不代表讨厌"前端与可视化"，即便
# 两者都在"技术类"），所以衰减因子明显比 _DISMISS_DECAY_FACTOR 温和，
# 下限也更高。
_TOPIC_CATEGORIES: dict[str, str] = {
    "Python 工程实践": "技术类",
    "前端与可视化": "技术类",
    "数据分析": "技术类",
    "系统设计与架构": "技术类",
    "AI/LLM 应用": "技术类",
    "项目管理": "管理类",
    "写作与表达": "表达类",
}
# 4 个可选类别标签（内置主题只出现在前 3 个里，"其他类"是默认兜底 +
# LLM 归类可选返回值）。P5-3 的 LLM 归类结果只接受这 4 选 1，其余一律
# 判定为解析失败，兜底"其他类"。
_TOPIC_CATEGORY_LABELS = ("技术类", "管理类", "表达类", "其他类")
_CATEGORY_DECAY_FACTOR = 0.95
_MIN_CATEGORY_MULTIPLIER = 0.7


def _category_of(topic: str, profile=None) -> str:
    """主题 → 类别。内置 7 个主题走硬编码表；`profile` 非 None 时，额外
    查一次 `profile.derived["growth_topic_categories"]`（P5-3 LLM 归类的
    持久化结果），仍未命中的落回"其他类"（跟不传 profile 时的行为完全
    一致，向后兼容）。"""
    category = _TOPIC_CATEGORIES.get(topic)
    if category is not None:
        return category
    if profile is not None:
        learned = _learned_topic_categories(profile)
        category = learned.get(topic)
        if category in _TOPIC_CATEGORY_LABELS:
            return category
    return "其他类"


def _category_dismiss_counts(paths, profile=None) -> dict[str, int]:
    """按类别统计历史"方向级负向原因" dismiss 次数（同一类别下不同主题的
    忽略次数累加；`DISMISS_REASON_REPORT_NOT_USEFUL` 不计入，理由同
    `_dismiss_counts_by_dedupe_key`——报告写得不好不该连累同类别其他方向
    的初始置信度）。
    """
    id_to_title = {c.candidate_id: c.title for c in GrowthBacklog(paths).load_all()}
    counts: dict[str, int] = {}
    for entry in GrowthFeedbackLedger(paths).all_entries():
        if entry.get("action") != STATUS_DISMISSED:
            continue
        if entry.get("reason", DISMISS_REASON_UNSPECIFIED) not in _DIRECTION_NEGATIVE_DISMISS_REASONS:
            continue
        title = id_to_title.get(entry.get("candidate_id"))
        if not title:
            continue
        category = _category_of(title, profile)
        counts[category] = counts.get(category, 0) + 1
    return counts


def _category_feedback_multiplier(dismiss_count: int) -> float:
    if dismiss_count <= 0:
        return 1.0
    return max(_MIN_CATEGORY_MULTIPLIER, round(_CATEGORY_DECAY_FACTOR ** dismiss_count, 3))


# ──────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2] ────────────
# 反馈模式统计展示：`_dismiss_counts_by_dedupe_key` / `_category_dismiss_
# counts` 只是把反馈拿去调权重的具体数值，不回答"用户到底更容易忽略
# 什么样的方向"这个更高层的问题。这里只做第一步——纯统计展示，不接入
# 任何排序/置信度计算（第二步 LLM 归纳按方案文档明确暂不排期）。

# 只看最近这么多条 dismiss 记录——反馈模式应该反映"最近的倾向"，不是
# "从有记录以来的全部历史"（用户的兴趣会变化，陈年的忽略记录不该继续
# 影响"最近是不是有共性"这个判断）。
_FEEDBACK_PATTERN_RECENT_WINDOW = 20
# 样本数低于这个值时不给"摘要文字"（凑不出有意义的共性判断，硬给反而
# 可能误导），但计数本身仍然照常返回，供看板按需展示原始分布。
_FEEDBACK_PATTERN_MIN_SAMPLE = 5
# 某个原因/类别在样本里占比达到这个比例才认为"有共性"，值得写进摘要
# 文字——避免样本刚好凑够 5 条、其中 3 条随手点了同一个原因就被解读成
# "模式"。
_FEEDBACK_PATTERN_DOMINANT_RATIO = 0.5

_DISMISS_REASON_LABELS = {
    DISMISS_REASON_NOT_INTERESTED: "不感兴趣",
    DISMISS_REASON_BAD_TIMING: "时机不对",
    DISMISS_REASON_REPORT_NOT_USEFUL: "报告没写好",
    DISMISS_REASON_ALREADY_EXISTS: "已存在该主题",
    DISMISS_REASON_UNSPECIFIED: "未说明原因",
}


def _llm_summarize_feedback_pattern(
    reason_distribution: dict[str, int],
    category_distribution: dict[str, int],
    sample_size: int,
    llm_helper: Callable[[str], str],
    *,
    status_out: Optional[dict] = None,
) -> str:
    """[growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2 第二步]
    把规则式统计出来的分布数字，让 LLM 组织成一句更自然的归纳文字。

    跟 `_llm_match_interests_to_goals` 同款"能用就用，用不了就当没发生"
    的克制：LLM 返回空、超长、或者看起来在编造分布里没有的数字，直接
    丢弃整段输出，调用方退回规则式的 `summary_text`，不会因为 LLM 输出
    异常而污染诊断面板。不做任何 JSON 解析——这里只要一段自然语言，
    比 `_llm_match_interests_to_goals` 要处理结构化匹配简单得多。
    """
    if status_out is not None:
        status_out["outcome"] = "error"
    reason_lines = "\n".join(
        f"- {_DISMISS_REASON_LABELS.get(r, r)}：{n} 次" for r, n in reason_distribution.items()
    )
    category_lines = "\n".join(f"- {c}：{n} 次" for c, n in category_distribution.items())
    prompt = (
        "下面是用户最近忽略成长顾问推荐方向时留下的统计数字，样本共 "
        f"{sample_size} 条。请用一到两句自然、口语化的中文帮用户归纳一下"
        "这些数字反映出的倾向，只基于给出的数字说话，不要编造数字或做"
        "任何数字之外的推测。如果数字本身看不出明显规律，就直接说"
        "看不出明显规律，不要牵强附会。只输出这一到两句话，不要标题、"
        "不要列表、不要多余的开场白。\n\n"
        f"按忽略原因统计：\n{reason_lines or '（无数据）'}\n\n"
        f"按方向类别统计：\n{category_lines or '（无数据）'}"
    )
    raw = llm_helper(prompt)
    if not raw or not raw.strip():
        if status_out is not None:
            status_out["outcome"] = "empty_response"
        return ""
    text = raw.strip()
    # 防御性截断：万一 LLM 没听指令输出了一大段，砍到一个摘要该有的长度，
    # 避免污染诊断面板的展示。
    if len(text) > 300:
        text = text[:300].rstrip() + "……"
    if status_out is not None:
        status_out["outcome"] = "success"
    return text


def growth_feedback_pattern_summary(
    paths, profile=None, *, cfg=None, llm_helper: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """对最近 `_FEEDBACK_PATTERN_RECENT_WINDOW` 条 dismiss 反馈做一次简单
    的分组统计（按 `dismiss_reason` 分组、按候选标题对应的类别分组），
    产出一段人类可读的摘要文字——纯粹是"让用户和系统都能看见这个模式"，
    不产出任何用于排序/加权的数值，也不自动据此调整任何候选排序（对齐
    方案文档"诊断增强不影响主流程"的一贯取舍，以及本文档第 8 节额外
    补充的"系统从数据里归纳出的结论一律止步于展示"这条更保守的原则）。

    只读，不写任何持久化；任何异常都不应该影响调用方（诊断面板）其它
    部分，调用方应自行 try/except 包裹（跟其它诊断聚合函数的既有约定
    一致，这里不内部吞异常，保持函数本身纯粹可测试）。

    [方向 2 第二步] `cfg.feedback_pattern_llm_enabled=True` 且传入
    `llm_helper`（都满足才触发，同 `goal_alignment_llm_enabled` 的
    opt-in 约定）、且样本数已经达标（`has_enough_data=True`）时，额外
    调一次 LLM 把上面的分布数字组织成一句更自然的归纳文字，放进返回值
    的 `llm_insight`（不存在时为空字符串）——跟规则式的 `summary_text`
    并列展示，不替换它，也不会被用于任何排序/加权计算。LLM 调用失败/
    输出为空/不像样时静默留空，不影响函数其它部分的返回。

    返回：
        {"has_enough_data": bool,          # 样本数是否达到最低门槛
         "sample_size": int,               # 参与统计的 dismiss 条数
         "reason_distribution": {reason: count},
         "category_distribution": {category: count},
         "summary_text": str,              # 人类可读摘要，样本不足或
                                            # 没有明显共性时给出对应说明
         "llm_insight": str}               # [方向 2 第二步] LLM 归纳的
                                            # 一两句自然语言总结，未开启/
                                            # 未触发/失败时为空字符串
    """
    dismiss_entries = [
        e for e in GrowthFeedbackLedger(paths).all_entries()
        if e.get("action") == STATUS_DISMISSED
    ]
    dismiss_entries.sort(key=lambda e: e.get("ts", 0))
    recent = dismiss_entries[-_FEEDBACK_PATTERN_RECENT_WINDOW:]
    sample_size = len(recent)

    if sample_size == 0:
        return {
            "has_enough_data": False,
            "sample_size": 0,
            "reason_distribution": {},
            "category_distribution": {},
            "summary_text": "目前还没有任何忽略记录，暂时看不出反馈模式。",
            "llm_insight": "",
        }

    reason_counts: dict[str, int] = {}
    for e in recent:
        reason = e.get("reason") or DISMISS_REASON_UNSPECIFIED
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    id_to_title = {c.candidate_id: c.title for c in GrowthBacklog(paths).load_all()}
    category_counts: dict[str, int] = {}
    for e in recent:
        title = id_to_title.get(e.get("candidate_id"))
        if not title:
            continue
        category = _category_of(title, profile)
        category_counts[category] = category_counts.get(category, 0) + 1

    if sample_size < _FEEDBACK_PATTERN_MIN_SAMPLE:
        summary_text = (
            f"最近只有 {sample_size} 条忽略记录，样本还太少，暂时看不出"
            "明显的共性模式。"
        )
        return {
            "has_enough_data": False,
            "sample_size": sample_size,
            "reason_distribution": reason_counts,
            "category_distribution": category_counts,
            "summary_text": summary_text,
            "llm_insight": "",
        }

    lines = []
    dominant_reason = max(reason_counts.items(), key=lambda kv: kv[1])
    if dominant_reason[1] / sample_size >= _FEEDBACK_PATTERN_DOMINANT_RATIO:
        label = _DISMISS_REASON_LABELS.get(dominant_reason[0], dominant_reason[0])
        pct = round(dominant_reason[1] / sample_size * 100)
        lines.append(
            f"最近 {sample_size} 次忽略里，有 {dominant_reason[1]} 次（约 {pct}%）"
            f"的原因是「{label}」。"
        )

    if category_counts:
        dominant_category = max(category_counts.items(), key=lambda kv: kv[1])
        if dominant_category[1] / sample_size >= _FEEDBACK_PATTERN_DOMINANT_RATIO:
            lines.append(
                f"被忽略的方向里，「{dominant_category[0]}」占比较高"
                f"（{dominant_category[1]}/{sample_size}）。"
            )

    if not lines:
        lines.append("最近的忽略记录里没有看出明显的共性模式。")

    llm_insight = ""
    llm_enabled = getattr(cfg, "feedback_pattern_llm_enabled", False) if cfg is not None else False
    if llm_enabled and llm_helper is not None:
        status_out: dict[str, Any] = {"outcome": "error"}
        try:
            llm_insight = _llm_summarize_feedback_pattern(
                reason_counts, category_counts, sample_size, llm_helper, status_out=status_out,
            )
            _record_llm_call_status(
                paths, "feedback_pattern_insight", status_out.get("outcome", "success"),
            )
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.growth_feedback_pattern_summary_llm")
            _record_llm_call_status(paths, "feedback_pattern_insight", "error", detail=str(exc)[:200])
            llm_insight = ""

    return {
        "has_enough_data": True,
        "sample_size": sample_size,
        "reason_distribution": reason_counts,
        "category_distribution": category_counts,
        "summary_text": "".join(lines) if len(lines) == 1 else " ".join(lines),
        "llm_insight": llm_insight,
    }


# ────────── [P4-3] 采纳后回访（followup） ──────────
# 方案第二条：候选被采纳后，隔一段时间（默认 30 天，见
# GrowthAdvisorConfig.followup_review_days）问一次"这个方向后续有没有真的
# 推进"，答案写入 GrowthFeedbackLedger（action="followup_progressed" /
# "followup_stalled"），作为置信度调权的额外信号源——"stalled" 视为比普通
# dismiss 更弱的负向信号（用户当初确实感兴趣，只是没推进，不代表方向选
# 错了），"progressed" 则是正向信号，让同一方向即便之后被重新生成候选也
# 不会一直背着旧的 dismiss 折扣。
_FOLLOWUP_STALLED_FACTOR = 0.9
_FOLLOWUP_PROGRESSED_FACTOR = 1.05
_VALID_FOLLOWUP_OUTCOMES = ("progressed", "stalled")


def record_followup(paths, candidate_id: str, outcome: str) -> Optional[GrowthCandidate]:
    """记录一次回访结果，写回候选并追加到反馈台账。"""
    if outcome not in _VALID_FOLLOWUP_OUTCOMES:
        raise ValueError(f"invalid followup outcome: {outcome}")
    backlog = GrowthBacklog(paths)
    all_c = backlog.load_all()
    for c in all_c:
        if c.candidate_id == candidate_id:
            c.followup_status = outcome
            c.updated_at = time.time()
            backlog.save_all(all_c)
            GrowthFeedbackLedger(paths).record(candidate_id, f"followup_{outcome}")
            return c
    return None


def _followup_adjustment_by_dedupe_key(paths) -> dict[str, float]:
    """把历史回访结果折算成按 dedupe_key 的置信度调节系数，供
    `growth_candidate_derive` 在同一方向因 dismiss 冷却结束后重新生成候选
    时参考——避免"曾经采纳过、只是没推进"的方向永远只看普通 dismiss 折扣。
    """
    id_to_key = {c.candidate_id: c.dedupe_key() for c in GrowthBacklog(paths).load_all()}
    adjustments: dict[str, float] = {}
    for entry in GrowthFeedbackLedger(paths).all_entries():
        action = entry.get("action") or ""
        if not action.startswith("followup_"):
            continue
        key = id_to_key.get(entry.get("candidate_id"))
        if not key:
            continue
        current = adjustments.get(key, 1.0)
        if action == "followup_stalled":
            current = round(current * _FOLLOWUP_STALLED_FACTOR, 3)
        elif action == "followup_progressed":
            current = min(1.0, round(current * _FOLLOWUP_PROGRESSED_FACTOR, 3))
        adjustments[key] = current
    return adjustments


# ────────── [P5-2] 置信度模型引入"证据分布度" ──────────
# next_doc/growth_advisor_improvement_plan_v3.md P5-2：`_confidence_from_
# evidence()` 只看证据条数，不看证据出现的时间分布——"一天内集中出现 5
# 条"和"5 周内每周出现 1 条"权重完全一样，但后者更像持续关注，前者更可能
# 只是某一天恰好聊得比较多。这里按跟 `_feedback_multiplier` 同款的"乘法
# 叠加"结构新增一个 `_distribution_multiplier`，分布越分散乘子越接近/
# 超过 1.0，全部集中在一两天则打折。
#
# 数据依赖：`evidence_refs` 本身只存 entry_id（不改这个结构，保持跟现有
# 大量测试用例里直接传字符串列表 `["e1", "e2", "e3"]` 的假设兼容），时间
# 戳单独存一份 `profile.derived["growth_evidence_timestamps"]`
# （`{entry_id: created_at}`），由 `growth_signal_scan()` 在扫描窗口内的
# entries 上顺带建好、每次扫描整体覆盖（不做增量合并，天然跟随
# `window_days` 有界，不会无限增长）。查不到时间戳的 entry_id（比如证据
# 是很久以前的扫描留下来、早就滚出当前窗口的旧记忆）直接忽略，不参与
# 分布计算；如果一个主题的证据全部查不到时间戳，乘子退化为中性值 1.0
# （没有分布信息时不惩罚，也不加成——这是保底行为，不是数据缺陷）。
_DISTRIBUTION_MIN_MULTIPLIER = 0.85
_DISTRIBUTION_MAX_MULTIPLIER = 1.1


def _distribution_multiplier(
    evidence_refs: list[str], evidence_timestamps: dict[str, float]
) -> float:
    """按证据对应记忆条目的时间戳分桶（天粒度），分布越分散乘子越高。

    `spread_ratio = distinct_day_buckets / entries_with_known_timestamp`：
    全部证据落在同一天 → ratio 趋近 `1/n`（打折）；每条证据都落在不同的
    天 → ratio = 1.0（加成）。只有 1 条有时间戳的证据时，分布这件事本身
    没有意义，直接返回中性值 1.0，不参与打折/加成。
    """
    known_ts = [evidence_timestamps[ref] for ref in evidence_refs if ref in evidence_timestamps]
    if len(known_ts) < 2:
        return 1.0
    day_buckets = {int(ts // 86400) for ts in known_ts}
    spread_ratio = len(day_buckets) / len(known_ts)
    multiplier = _DISTRIBUTION_MIN_MULTIPLIER + (
        _DISTRIBUTION_MAX_MULTIPLIER - _DISTRIBUTION_MIN_MULTIPLIER
    ) * spread_ratio
    return round(multiplier, 3)


# ────────── [P4-5] 通知策略细化：类别级推送偏好 + 重要程度分级 ──────────
# next_doc/growth_advisor_improvement_plan_v2.md P4-5。两条独立能力：
#   1. 类别静音：某个类别的候选完全不主动推送（仍在看板展示），
#      通过 GrowthAdvisorConfig.category_notification_frequency 配置。
#   2. 重要程度分级：多份报告都达到 notification_min_confidence 门槛时，
#      不是简单取置信度最高的一条，而是结合"这个类别历史采纳率高不高"
#      算一个综合优先级分数——证据充分 + 历史上这类方向经常被采纳，应该
#      比"刚好卡线但历史上这类方向常被忽略"的方向优先级更高。


def _category_notification_muted(cfg, topic: str, profile=None) -> bool:
    """某个主题所属类别是否被配置为 kanban_only（完全静音，只看板展示、
    不主动推送）。目前只识别这一种覆盖值，其余原样透传给全局频率逻辑。"""
    overrides = getattr(cfg, "category_notification_frequency", None) or {}
    return overrides.get(_category_of(topic, profile)) == "kanban_only"


def _category_acceptance_rate(paths, profile=None) -> dict[str, float]:
    """按类别统计历史采纳率（已做出 accept/dismiss 决策的候选里，
    accept 占比），只统计有过决策的类别，未出现过决策的类别不在返回值里
    （调用方对缺失类别应视为中性 0.5，既不加分也不减分）。"""
    accepted: dict[str, int] = {}
    decided: dict[str, int] = {}
    for c in GrowthBacklog(paths).load_all():
        if c.status not in (STATUS_ACCEPTED, STATUS_DISMISSED):
            continue
        category = _category_of(c.title, profile)
        decided[category] = decided.get(category, 0) + 1
        if c.status == STATUS_ACCEPTED:
            accepted[category] = accepted.get(category, 0) + 1
    return {cat: round(accepted.get(cat, 0) / n, 3) for cat, n in decided.items() if n > 0}


# 优先级分数 = confidence * (_PRIORITY_BASE + _PRIORITY_RATE_WEIGHT * acceptance_rate)
# rate=0（历史上这类方向从没被采纳过）时打 0.7 折，rate=1（历史上逢推
# 必采纳）时打 1.3 倍，rate 缺失（这个类别还没有过任何决策）时按中性 0.5
# 处理，等价于 1.0 倍——不因为"数据不够"就惩罚或奖励。
_PRIORITY_BASE = 0.7
_PRIORITY_RATE_WEIGHT = 0.6


def _notification_priority_score(confidence: float, acceptance_rate: Optional[float]) -> float:
    rate = acceptance_rate if acceptance_rate is not None else 0.5
    return round(confidence * (_PRIORITY_BASE + _PRIORITY_RATE_WEIGHT * rate), 3)


# ────────────────────────── 信号扫描 growth_signal_scan ──────────────────────────


# 中文/英文成长方向关键词 → 归一化主题名。规则式 MVP，先覆盖高频场景；
# 后续如需扩展，直接往这个表里加词条即可，不需要改扫描逻辑。
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "Python 工程实践": ["python", "pytest", "packaging", "asyncio"],
    "前端与可视化": ["react", "frontend", "streamlit", "前端", "可视化"],
    "数据分析": ["pandas", "sql", "数据分析", "dataframe"],
    "系统设计与架构": ["架构", "设计模式", "microservice", "系统设计"],
    "写作与表达": ["写作", "文案", "表达", "沟通"],
    "项目管理": ["项目管理", "排期", "计划", "复盘"],
    "AI/LLM 应用": ["llm", "prompt", "agent", "大模型", "rag"],
}


# ─────────── [P4-1] 关键词表持久化：profile.derived["growth_topic_keywords"] ───────────
# next_doc/growth_advisor_improvement_plan_v2.md 第 3 节。内置表继续留在
# 代码里（_TOPIC_KEYWORDS），profile.derived 只存增量：用户自定义
# （source="user_added"）+ LLM 学到但待确认（source="llm_learned"）。

def _effective_topic_keywords(profile) -> dict[str, dict[str, Any]]:
    """合并内置关键词表 + 用户 profile 里的增量，减去用户隐藏的内置主题。

    返回 {topic: {"keywords": [...], "source": "built_in"|"user_added"|
    "llm_learned", "confirmed_by_user": bool, "added_at": float|None}}，
    供 growth_signal_scan / diagnostics_snapshot 统一消费，替代此前直接
    引用模块常量 `_TOPIC_KEYWORDS` 的写法。
    """
    derived = dict(getattr(profile, "derived", {}) or {})
    removed = set(derived.get("growth_topic_keywords_removed") or [])
    custom = dict(derived.get("growth_topic_keywords") or {})

    result: dict[str, dict[str, Any]] = {}
    for topic, kws in _TOPIC_KEYWORDS.items():
        if topic in removed:
            continue
        result[topic] = {
            "keywords": list(kws),
            "source": "built_in",
            "confirmed_by_user": True,
            "added_at": None,
            "consecutive_scan_hits": 0,
            "auto_confirmed": False,
        }
    for topic, info in custom.items():
        if not isinstance(info, dict):
            continue
        kws = [k for k in (info.get("keywords") or []) if k]
        if not kws:
            continue
        result[topic] = {
            "keywords": kws,
            "source": info.get("source") or "user_added",
            "confirmed_by_user": bool(info.get("confirmed_by_user", False)),
            "added_at": info.get("added_at"),
            "consecutive_scan_hits": int(info.get("consecutive_scan_hits", 0) or 0),
            "auto_confirmed": bool(info.get("auto_confirmed", False)),
        }
    return result


def _clean_keywords(raw) -> list[str]:
    """清洗用户/LLM 提供的关键词：去空白、去重（大小写不敏感）、丢弃空项。"""
    if isinstance(raw, str):
        raw = re.split(r"[,，、\n]+", raw)
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw or []:
        kw = str(item).strip()
        if not kw:
            continue
        key = kw.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(kw)
    return cleaned


def add_custom_topic_keyword(
    profile, topic: str, keywords, *, cfg=None, llm_helper: Optional[Callable[[str], str]] = None, paths=None
) -> dict[str, Any]:
    """用户在看板上手动添加一个自定义主题，直接标记为已确认。

    `cfg`/`llm_helper` 是 P5-3 可选参数：`cfg.topic_category_llm_enabled`
    打开且传入了 `llm_helper` 时，额外做一次类别归类（见
    `maybe_classify_topic_category()`）；不传或开关关闭时是零成本空操作，
    不影响此前的调用方（如 API 路由，目前没有同步 llm_helper 可用）。
    """
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic must not be empty")
    cleaned = _clean_keywords(keywords)
    if not cleaned:
        raise ValueError("keywords must not be empty")

    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    entry = {
        "keywords": cleaned,
        "source": "user_added",
        "confirmed_by_user": True,
        "added_at": time.time(),
    }
    custom[topic] = entry
    derived["growth_topic_keywords"] = custom
    # 用户主动加回来的主题，如果之前被隐藏过，取消隐藏
    removed = [t for t in (derived.get("growth_topic_keywords_removed") or []) if t != topic]
    derived["growth_topic_keywords_removed"] = removed
    profile.derived = derived
    maybe_classify_topic_category(profile, topic, cleaned, cfg, llm_helper=llm_helper, paths=paths)
    return entry


def remove_topic_keyword(profile, topic: str) -> bool:
    """删除/隐藏一个主题：自定义主题直接从增量表移除；内置主题记入
    `growth_topic_keywords_removed` 黑名单（下次扫描时会被排除）。
    """
    topic = (topic or "").strip()
    if not topic:
        return False
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    changed = False
    if topic in custom:
        del custom[topic]
        derived["growth_topic_keywords"] = custom
        changed = True
    if topic in _TOPIC_KEYWORDS:
        removed = set(derived.get("growth_topic_keywords_removed") or [])
        if topic not in removed:
            removed.add(topic)
            derived["growth_topic_keywords_removed"] = sorted(removed)
            changed = True
    if changed:
        profile.derived = derived
    return changed


# [P4-7] next_doc/growth_advisor_improvement_plan_v2.md P4-7：`remove_topic_
# keyword()` 早在 P3 就已经支持隐藏内置主题（写入 `growth_topic_keywords_
# removed`），`POST /growth/keywords/{topic}/remove` 的 docstring 也一直
# 写着"🙈 隐藏"——但看板从来没有实际渲染过内置主题的隐藏按钮，也没有地方
# 能看到"我隐藏过哪些内置主题、想不想恢复"，功能有一半是空的。这里补上
# 对称的另一半：查询 + 恢复，不重新发明黑名单机制。
def hidden_builtin_topics(profile) -> list[str]:
    """当前被隐藏的内置主题列表（按名称排序），供看板渲染"已隐藏"区块。"""
    derived = dict(getattr(profile, "derived", {}) or {})
    removed = derived.get("growth_topic_keywords_removed") or []
    return sorted(t for t in removed if t in _TOPIC_KEYWORDS)


def restore_builtin_topic_keyword(profile, topic: str) -> bool:
    """把一个被隐藏的内置主题恢复回来——只是从
    `growth_topic_keywords_removed` 黑名单里摘掉，不会像
    `add_custom_topic_keyword()` 那样把它转成一条自定义关键词记录（那需要
    用户重新填一遍关键词，对"恢复内置主题"这个场景没有必要，内置关键词
    本来就还在 `_TOPIC_KEYWORDS` 常量里，不需要重建）。"""
    topic = (topic or "").strip()
    if not topic or topic not in _TOPIC_KEYWORDS:
        return False
    derived = dict(getattr(profile, "derived", {}) or {})
    original = derived.get("growth_topic_keywords_removed") or []
    if topic not in original:
        return False  # 本来就没被隐藏
    derived["growth_topic_keywords_removed"] = [t for t in original if t != topic]
    profile.derived = derived
    return True


def confirm_topic_keyword(
    profile, topic: str, *, cfg=None, llm_helper: Optional[Callable[[str], str]] = None, paths=None
) -> bool:
    """用户在看板上点"✅ 保留"，把一个待确认（通常是 llm_learned）的
    自定义主题标记为已确认。对内置主题/不存在的主题是安全的空操作。

    `cfg`/`llm_helper`：见 `add_custom_topic_keyword()` 同名参数说明——
    P5-3 的可选归类钩子，确认转正是方案里明确提到的另一个触发时机。
    """
    topic = (topic or "").strip()
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    entry = custom.get(topic)
    if not isinstance(entry, dict):
        return False
    if entry.get("confirmed_by_user"):
        return False
    entry = dict(entry)
    entry["confirmed_by_user"] = True
    custom[topic] = entry
    derived["growth_topic_keywords"] = custom
    profile.derived = derived
    maybe_classify_topic_category(
        profile, topic, list(entry.get("keywords") or []), cfg, llm_helper=llm_helper, paths=paths
    )
    return True


def _learned_topic_categories(profile) -> dict[str, str]:
    """读取 P5-3 持久化的自定义/学习到主题的类别归类结果
    （`profile.derived["growth_topic_categories"]`，`{topic: category}`）。
    """
    derived = dict(getattr(profile, "derived", {}) or {})
    raw = derived.get("growth_topic_categories") or {}
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if v in _TOPIC_CATEGORY_LABELS}


def _persist_topic_category(profile, topic: str, category: str) -> None:
    """把一个主题的归类结果写入 `profile.derived["growth_topic_
    categories"]`。已有记录会被覆盖（重新分类场景），调用方负责判断是否
    需要重新分类。"""
    if category not in _TOPIC_CATEGORY_LABELS:
        return
    derived = dict(getattr(profile, "derived", {}) or {})
    categories = dict(derived.get("growth_topic_categories") or {})
    categories[topic] = category
    derived["growth_topic_categories"] = categories
    profile.derived = derived


def classify_topic_category_llm(
    topic: str, keywords: list[str], llm_helper: Callable[[str], str], *, paths=None
) -> Optional[str]:
    """[P5-3] 用 LLM 把一个主题粗分类到 4 个内置类别之一（技术类/管理类/
    表达类/其他类）。复用 `llm_signal_augment_enabled` 同款的"opt-in、
    宽松吸收"模式——解析失败、返回值不在 4 个类别里，一律返回 None，
    调用方兜底为"其他类"（不倒退现有行为）。明确不用 embedding：这是
    4 选 1 的粗粒度分类，LLM 一次调用即可给出可解释的分类理由，边际复杂度
    比维护一份类别参考向量更低，见
    next_doc/growth_advisor_improvement_plan_v3.md P5-3。"""
    prompt = (
        "请把下面这个用户成长方向主题归到 4 个类别之一：技术类/管理类/"
        "表达类/其他类。技术类指编程、工程、数据、系统设计等技术能力；"
        "管理类指项目管理、团队协作、time management 等；表达类指写作、"
        "演讲、沟通表达；不属于以上三类的一律归为其他类。\n"
        f"主题：{topic}\n关键词：{', '.join(keywords)}\n"
        "只输出类别名称本身（4 选 1），不要有其他文字。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception as exc:
        if paths is not None:
            _record_llm_call_status(paths, "topic_category", "error", detail=str(exc)[:200])
        return None
    if not raw:
        if paths is not None:
            _record_llm_call_status(paths, "topic_category", "empty_response")
        return None
    text = raw.strip()
    for label in _TOPIC_CATEGORY_LABELS:
        if label in text:
            if paths is not None:
                _record_llm_call_status(paths, "topic_category", "success", detail=label)
            return label
    if paths is not None:
        _record_llm_call_status(paths, "topic_category", "parse_error", detail=text[:100])
    return None


def maybe_classify_topic_category(
    profile,
    topic: str,
    keywords: list[str],
    cfg=None,
    *,
    llm_helper: Optional[Callable[[str], str]] = None,
    paths=None,
) -> Optional[str]:
    """[P5-3] 主题新增/确认转正时的归类入口：`topic_category_llm_enabled`
    关闭（默认）或没有可用的 `llm_helper` 时是零成本空操作；开启且已有
    `llm_helper` 时调用一次 LLM 分类并持久化。已经分类过的主题不会重复
    调用（分类结果倾向于长期稳定，不需要每次都重新问）——如需强制重新
    分类，调用方直接调 `_persist_topic_category()` 覆盖。
    """
    if not getattr(cfg, "topic_category_llm_enabled", False):
        return None
    if llm_helper is None:
        return None
    if topic in _TOPIC_CATEGORIES:
        return None  # 内置主题已经有硬编码类别，不需要 LLM 归类
    if topic in _learned_topic_categories(profile):
        return None  # 已经分类过，不重复调用
    category = classify_topic_category_llm(topic, keywords, llm_helper, paths=paths)
    if category is None:
        return None
    _persist_topic_category(profile, topic, category)
    return category



def _persist_learned_topics(profile, new_topics: dict[str, list[str]]) -> None:
    """把 `_llm_augment_topics` 新发现的主题写入
    `profile.derived["growth_topic_keywords"]`（source=llm_learned，
    confirmed_by_user=False）。已经存在于增量表/内置表里的主题不会被
    重复写入或覆盖已有状态（例如已被用户确认过的不会被打回未确认）。
    """
    if not new_topics:
        return
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    changed = False
    for topic in new_topics:
        if topic in custom or topic in _TOPIC_KEYWORDS:
            continue
        # LLM 没有直接给出关键词，用主题名自身兜底作为关键词，
        # 保证下次规则扫描也能命中同一批记忆。
        custom[topic] = {
            "keywords": [topic],
            "source": "llm_learned",
            "confirmed_by_user": False,
            "added_at": time.time(),
        }
        changed = True
    if changed:
        derived["growth_topic_keywords"] = custom
        profile.derived = derived


# ─────────── [P4-2] 关键词表"自动学习稳定后转正" ───────────
# next_doc/growth_advisor_improvement_plan_v2.md 第 4 节 P4-2。同一个
# llm_learned 待确认主题，如果连续这么多次扫描都有新证据支持（本次 hits
# 里出现），就自动把 confirmed_by_user 置为 True，不需要用户手动点确认。
_AUTO_CONFIRM_STREAK = 3


def _update_keyword_learning_streaks(profile, hits: dict[str, list[str]]) -> None:
    """在每次 growth_signal_scan 结束时调用：更新每个待确认自定义主题的
    连续命中计数，达到 `_AUTO_CONFIRM_STREAK` 时自动转正。

    - 本次扫描命中该主题（`topic in hits` 且证据非空）→ streak += 1；
      达到阈值 → `confirmed_by_user = True`，streak 清零（转正后不需要
      再继续计数）。
    - 本次扫描没有命中 → streak 重置为 0（要求"连续"，中断一次就重来，
      避免"隔三差五命中一次"也被误判为稳定信号）。
    - 只处理 `source == "llm_learned"` 且尚未确认的主题；`user_added`
      的主题创建时就已经是确认状态，不需要这个机制；已确认的主题不再
      追踪 streak（避免白白维护一个用不上的计数器）。
    - 用户手动删除/隐藏过的主题不会出现在 `_effective_topic_keywords()`
      的结果里，因而也不会出现在 `hits` 里，天然不会被这里"复活"。
    """
    derived = dict(getattr(profile, "derived", {}) or {})
    custom = dict(derived.get("growth_topic_keywords") or {})
    if not custom:
        return

    changed = False
    for topic, entry in list(custom.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("source") != "llm_learned" or entry.get("confirmed_by_user"):
            continue
        entry = dict(entry)
        hit_this_scan = bool(hits.get(topic))
        streak = int(entry.get("consecutive_scan_hits", 0) or 0)
        if hit_this_scan:
            streak += 1
        else:
            streak = 0
        entry["consecutive_scan_hits"] = streak
        if streak >= _AUTO_CONFIRM_STREAK:
            entry["confirmed_by_user"] = True
            entry["auto_confirmed"] = True
            entry["consecutive_scan_hits"] = 0
        custom[topic] = entry
        changed = True

    if changed:
        derived["growth_topic_keywords"] = custom
        profile.derived = derived


def growth_signal_scan(
    paths, profile, memory_store, *,
    window_days: int = SIGNAL_SCAN_WINDOW_DAYS,
    llm_helper: Optional[Callable[[str], str]] = None,
) -> dict[str, list[str]]:
    """扫描最近 window_days 内的 memory entries，按 `_TOPIC_KEYWORDS` 做
    命中统计，把 {主题: [entry_id...]} 写入
    `profile.derived["growth_focus_areas"]`（结构化，供 candidate_derive
    直接消费），并返回该结果供调用方（cron / CLI）立即使用。

    这是规则式实现（P1），不依赖 LLM——保证 `enabled=True` 默认开启时
    不会给每个用户都额外产生 LLM 调用成本。

    P3：如果调用方传入 `llm_helper`（签名 `llm_helper(prompt: str) ->
    str`，同 `generate_growth_report` 的约定），会在规则扫描结束后额外
    做一次 LLM 增强归纳（见 `_llm_augment_topics`），从关键词表命中不到
    的近期记忆里尝试发现新主题，补充进返回结果——但只有调用方同时传入
    `llm_helper` 时才会触发，函数本身不读取 `GrowthAdvisorConfig`（是否
    要传 `llm_helper` 由调用方根据 `cfg.llm_signal_augment_enabled` 决定），
    保持这个函数纯粹、可测试。
    """
    cutoff = time.time() - window_days * 86400
    hits: dict[str, list[str]] = {}

    effective_keywords = _effective_topic_keywords(profile)
    entries = memory_store.all_entries() if memory_store is not None else []
    recent_entries = [e for e in entries if getattr(e, "created_at", 0) >= cutoff]
    for entry in recent_entries:
        haystack = " ".join(
            [getattr(entry, "summary", "") or ""]
            + list(getattr(entry, "tags", []) or [])
        ).lower()
        for topic, info in effective_keywords.items():
            if any(kw.lower() in haystack for kw in info["keywords"]):
                hits.setdefault(topic, []).append(getattr(entry, "entry_id", "") or "")

    if llm_helper is not None:
        # [LLM 增强路径可观测性] 无论最终是成功、空转还是异常，都记一份
        # 结果快照——这里不是"失败时才记"，是每次触发都记，这样诊断面板
        # 展示的才是"最近一次"，而不是"最近一次失败"（成功之后再失败一次
        # 也应该看得到最新状态）。
        status_out: dict[str, Any] = {"outcome": "error"}
        try:
            before_topics = set(hits.keys())
            hits = _llm_augment_topics(hits, recent_entries, llm_helper, status_out=status_out)
            new_topics = set(hits.keys()) - before_topics - set(effective_keywords.keys())
            if new_topics:
                _persist_learned_topics(profile, {t: hits[t] for t in new_topics})
            _record_llm_call_status(
                paths, "signal_augment", status_out.get("outcome", "success"),
                detail=f"new_topics={status_out.get('new_topic_count', len(new_topics))}",
            )
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.growth_signal_scan_llm_augment")
            _record_llm_call_status(paths, "signal_augment", "error", detail=str(exc)[:200])

    # [P4-2] 待确认自定义主题的连续命中计数 + 达标自动转正，必须在
    # _persist_learned_topics 之后调用（保证本次新学到的主题也能立刻开始
    # 计数），并且在最终写回 growth_focus_areas 之前调用（避免被后面的
    # `profile.derived = derived` 覆盖掉）。
    try:
        _update_keyword_learning_streaks(profile, hits)
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor.growth_signal_scan_auto_confirm")

    derived = dict(getattr(profile, "derived", {}) or {})
    derived["growth_focus_areas"] = hits
    derived["growth_focus_areas_updated_at"] = time.time()
    # [P5-2] 证据分布度乘子需要的时间戳数据，只覆盖当前扫描窗口内的
    # entries——整体覆盖（不是增量 merge），天然跟随 window_days 有界，
    # 不会无限增长；早于窗口的旧证据本来也已经不在这里，`_distribution_
    # multiplier` 查不到时间戳时会安全地退化为中性乘子 1.0。
    derived["growth_evidence_timestamps"] = {
        (getattr(e, "entry_id", "") or ""): getattr(e, "created_at", 0)
        for e in recent_entries
        if getattr(e, "entry_id", "")
    }
    profile.derived = derived
    return hits


# 一次 LLM 增强归纳最多送多少条"规则未命中"的记忆条目，避免 prompt 无限
# 增长；条目本身不多的账号成本也很低，条目多的账号只取最近的一批。
_LLM_AUGMENT_MAX_ENTRIES = 40
# 未命中条目太少时不值得为了归纳专门调一次 LLM（大概率凑不满
# min_evidence_count，调了也白调）。
_LLM_AUGMENT_MIN_UNMATCHED = 3


def _llm_augment_topics(
    hits: dict[str, list[str]],
    recent_entries: list,
    llm_helper: Callable[[str], str],
    *,
    status_out: Optional[dict] = None,
) -> dict[str, list[str]]:
    """在规则式 `hits` 基础上，对关键词表命中不到的近期记忆条目做一次
    LLM 归纳，尝试发现 `_TOPIC_KEYWORDS` 没覆盖到的新主题。

    只处理"未命中"的条目——已经被规则命中的条目不重复送给 LLM，既省
    token，也避免 LLM 把规则已经归好的话题换个说法再归一遍造成主题碎片
    化。返回的新主题会按 `normalize_title_key` 与已有主题去重合并，不
    会产生"一个意思两个不同大小写/标点的 key"这种重复。

    任何解析失败、字段缺失、entry_id 对不上号的情况，都直接丢弃对应
    条目/主题而不是让异常向上传播——LLM 输出不可信，只做"能用就用，
    用不了就当没发生"的宽松吸收。
    """
    matched_ids = {eid for ids in hits.values() for eid in ids}
    unmatched = [e for e in recent_entries if (getattr(e, "entry_id", "") or "") not in matched_ids]
    if len(unmatched) < _LLM_AUGMENT_MIN_UNMATCHED:
        if status_out is not None:
            status_out["outcome"] = "skipped_insufficient_unmatched"
        return hits

    unmatched = unmatched[-_LLM_AUGMENT_MAX_ENTRIES:]
    id_to_entry = {getattr(e, "entry_id", "") or "": e for e in unmatched}
    valid_ids = set(id_to_entry.keys())

    lines = []
    for eid, e in id_to_entry.items():
        summary = (getattr(e, "summary", "") or "").strip().replace("\n", " ")[:200]
        lines.append(f"- entry_id={eid}: {summary}")
    prompt = (
        "以下是一批用户最近的记忆摘要，逐条带有 entry_id。请找出其中反复\n"
        "出现、可能值得用户系统学习/深入投入的成长方向（不要包括日常琐事、\n"
        "一次性事件）。只根据已发生的内容归纳，不要编造。\n"
        "只输出 JSON 数组，不要有其他文字，每个元素形如：\n"
        '{\"topic\": \"简短主题名\", \"entry_ids\": [\"命中的 entry_id\", ...]}\n'
        "entry_ids 必须原样从下面列表里选，不要发明新的 id。没有发现\n"
        "任何值得关注的主题时输出空数组 []。\n\n" + "\n".join(lines)
    )

    raw = llm_helper(prompt)
    if not raw or not raw.strip():
        if status_out is not None:
            status_out["outcome"] = "empty_response"
        return hits

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            if status_out is not None:
                status_out["outcome"] = "parse_error"
            return hits
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            if status_out is not None:
                status_out["outcome"] = "parse_error"
            return hits

    if not isinstance(parsed, list):
        if status_out is not None:
            status_out["outcome"] = "parse_error"
        return hits

    merged = {k: list(v) for k, v in hits.items()}
    existing_keys = {normalize_title_key(k): k for k in merged}

    for item in parsed:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic") or "").strip()
        raw_ids = item.get("entry_ids")
        if not topic or not isinstance(raw_ids, list):
            continue
        ids = sorted({str(i) for i in raw_ids if str(i) in valid_ids})
        if not ids:
            continue

        key = normalize_title_key(topic)
        canonical = existing_keys.get(key)
        if canonical is None:
            existing_keys[key] = topic
            merged[topic] = ids
        else:
            merged[canonical] = sorted(set(merged.get(canonical, [])) | set(ids))

    if status_out is not None:
        new_count = len(merged) - len(hits)
        status_out["outcome"] = "success" if new_count > 0 else "no_new_topics"
        status_out["new_topic_count"] = max(new_count, 0)

    return merged


# ────────────────────────── 候选生成 growth_candidate_derive ──────────────────────────


def growth_candidate_derive(
    paths, cfg, profile, *, goal_backlog=None, llm_helper: Optional[Callable[[str], str]] = None,
) -> list[GrowthCandidate]:
    """消费 `profile.derived["growth_focus_areas"]`（由 growth_signal_scan
    产出），对证据数达标、未命中 excluded_topics 的主题生成/合并候选到
    backlog，返回本次新增或有更新的候选列表。

    [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 3]
    `goal_backlog`：可选，传入时额外调用 `extract_spinoff_topics_
    from_pursuits()` 挖掘"正在推进方向里反复出现但从未被吸收"的衍生
    话题，并入本轮候选生成输入——走同一套证据数阈值/置信度计算，只是
    额外打一个 `origin="pursuit_spinoff"` 标记（仅在该主题此前未被
    信号扫描命中过时才标记来源为 spinoff；如果同一个标题恰好也被
    memory 信号命中了，仍按信号扫描的证据优先合并，不覆盖对方证据，
    只是把两边证据取并集）。不传（沿用旧调用点）时行为与改动前完全
    一致。任何异常都不影响原有 memory 信号路径。

    `llm_helper`：[候选去重 LLM 语义判重] 只有在
    `cfg.duplicate_direction_llm_check_enabled=True` 时才会真正透传给
    `backlog.add_or_merge()` 用于语义判重（见该函数文档字符串）；开关
    关闭或未传入时这一步整体跳过，只保留原有的精确标题去重，行为与
    改动前完全一致。同时会用 `goal_backlog.active_goals()` 的标题作为
    "已存在方向"的补充来源（拿不到 `goal_backlog` 时为空列表）。
    """
    focus_areas: dict[str, list[str]] = dict(
        (getattr(profile, "derived", {}) or {}).get("growth_focus_areas", {})
    )
    spinoff_origins: set[str] = set()
    if goal_backlog is not None:
        try:
            spinoff_hits = extract_spinoff_topics_from_pursuits(paths, goal_backlog)
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.growth_candidate_derive_spinoff")
            spinoff_hits = {}
        for topic, refs in spinoff_hits.items():
            if topic not in focus_areas:
                spinoff_origins.add(topic)
            existing = focus_areas.setdefault(topic, [])
            focus_areas[topic] = sorted(set(existing) | set(refs))

    dedup_llm_helper = llm_helper if getattr(cfg, "duplicate_direction_llm_check_enabled", False) else None
    active_goal_titles: list[str] = []
    if dedup_llm_helper is not None and goal_backlog is not None:
        try:
            active_goal_titles = [g.title for g in goal_backlog.active_goals() if g.title]
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.growth_candidate_derive_goal_titles")
            active_goal_titles = []

    excluded = {t.strip().lower() for t in getattr(cfg, "excluded_topics", []) or []}
    backlog = GrowthBacklog(paths)
    backlog.expire_stale()

    min_evidence_count = getattr(cfg, "min_evidence_count", _DEFAULT_MIN_EVIDENCE_COUNT)
    max_pending = getattr(cfg, "max_pending_candidates", 10)
    cooldown_days = getattr(cfg, "dismissed_cooldown_days", 30)

    # P2：反馈驱动的置信度调权（方案第 6 节）——先一次性读取历史 dismiss
    # 统计，逐主题查表即可，避免在循环里重复扫描 ledger。
    dismiss_counts = _dismiss_counts_by_dedupe_key(paths)
    # P4-3：类别级反馈（同一类别下的忽略会温和地拖累同类新主题的初始置信度）
    # + 采纳后回访调节（stalled/progressed），三者相乘得到最终 multiplier。
    category_dismiss_counts = _category_dismiss_counts(paths, profile)
    followup_adjustments = _followup_adjustment_by_dedupe_key(paths)
    # [P5-2] 由 growth_signal_scan() 顺带建好，本轮候选生成直接查表使用。
    evidence_timestamps: dict[str, float] = dict(
        (getattr(profile, "derived", {}) or {}).get("growth_evidence_timestamps", {})
    )

    produced: list[GrowthCandidate] = []
    # 按证据数从多到少处理，保证 max_pending 限额下优先生成信号更强的候选
    for topic, refs in sorted(focus_areas.items(), key=lambda kv: -len(kv[1])):
        if topic.strip().lower() in excluded:
            continue
        if topic in spinoff_origins:
            rationale = f"你正在推进的另一个方向里，「{topic}」这个问题反复被提到但一直没有展开，可能是值得单独投入的方向。"
        else:
            rationale = f"最近记忆里与「{topic}」相关的内容出现了 {len(set(refs))} 次，可能是值得投入的方向。"
        key = normalize_title_key(topic)
        topic_multiplier = _feedback_multiplier(dismiss_counts.get(key, 0))
        category_multiplier = _category_feedback_multiplier(
            category_dismiss_counts.get(_category_of(topic, profile), 0)
        )
        followup_multiplier = followup_adjustments.get(key, 1.0)
        distribution_multiplier = _distribution_multiplier(refs, evidence_timestamps)
        multiplier = round(
            topic_multiplier * category_multiplier * followup_multiplier * distribution_multiplier,
            3,
        )
        cand = backlog.add_or_merge(
            title=topic,
            rationale=rationale,
            evidence_refs=refs,
            min_evidence_count=min_evidence_count,
            max_pending=max_pending,
            dismissed_cooldown_days=cooldown_days,
            confidence_multiplier=multiplier,
            origin="pursuit_spinoff" if topic in spinoff_origins else "signal_scan",
            llm_helper=dedup_llm_helper,
            existing_goal_titles=active_goal_titles,
        )
        # [P4-6] 每轮都记一条趋势快照，不管这次是否达标生成/更新了候选
        # ——证据数本身在低于阈值时也是有意义的"正在积累"信号，只有在
        # 达标之后才看得到候选反而会让走势图开局就是一条空白。
        _record_topic_trend_snapshot(
            paths, topic, len(set(refs)), cand.confidence if cand is not None else None
        )
        if cand is not None:
            produced.append(cand)
    # [P5-0] 每轮扫描结束后顺带做一次趋势文件降采样压缩，摊销掉的是"每天
    # 一次、读一次全量文件"的成本，跟这个函数本身已经是"整轮 cron 只跑
    # 一次"的调用频率一致，不会引入额外的高频 IO。
    compact_topic_trend_storage(paths)
    return produced


# ────────────────────────── 调研报告生成 ──────────────────────────


def _slugify(title: str) -> str:
    key = normalize_title_key(title).replace(" ", "-")
    return key or uuid.uuid4().hex[:8]


# 提纲阶段最多要求几个问题——太多会让正文阶段的 prompt 和输出都跟着
# 膨胀，4 个跟原有固定四段式的小节数保持一致，体验上是"更具体的四段"
# 而不是"篇幅明显变长的报告"。
_REPORT_OUTLINE_MAX_QUESTIONS = 4


def _generate_report_outline(
    paths, candidate: GrowthCandidate, external_context_section: str,
    llm_helper: Callable[[str], str],
) -> Optional[list[str]]:
    """[growth_advisor_research_quality_plan.md 阶段 2] 让 LLM 针对这个
    候选主题提炼几个具体问题，供正文阶段逐一回答，替代固定的"四个小节"
    结构（"为什么值得关注/怎么入门/……"对每个主题都问一样的问题，容易
    写成放之四海皆准的通用建议）。

    只输出 JSON 字符串数组；任何解析失败/空响应/异常都返回 `None`，
    调用方（`generate_growth_report()`）据此退回原有的单段式 prompt，
    不让报告生成本身失败——这是"能用就用，用不了就当没发生"的一贯
    容错原则，跟 `_llm_augment_topics()` 等既有 LLM 增强调用点一致。
    """
    prompt = (
        "针对下面这个用户成长方向候选，请提出 3-4 个『这份调研报告应该\n"
        "重点具体回答』的问题——要具体、可操作，避免『怎么入门』这种\n"
        "放之四海皆准的泛泛提问，最好能结合候选理由里透露出的用户处境。\n"
        "只输出 JSON 字符串数组，不要有其他文字，例如：\n"
        '["问题1", "问题2", "问题3"]\n\n'
        f"主题：{candidate.title}\n理由：{candidate.rationale}\n"
        f"{external_context_section}"
    )
    try:
        raw = llm_helper(prompt)
    except Exception as exc:
        _record_llm_call_status(paths, "report_outline", "error", detail=str(exc)[:200])
        return None
    if not raw or not raw.strip():
        _record_llm_call_status(paths, "report_outline", "empty_response")
        return None

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            _record_llm_call_status(paths, "report_outline", "parse_error")
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            _record_llm_call_status(paths, "report_outline", "parse_error")
            return None

    if not isinstance(parsed, list):
        _record_llm_call_status(paths, "report_outline", "parse_error")
        return None
    questions = [str(q).strip() for q in parsed if str(q).strip()]
    questions = questions[:_REPORT_OUTLINE_MAX_QUESTIONS]
    if not questions:
        _record_llm_call_status(paths, "report_outline", "no_new_topics")
        return None
    _record_llm_call_status(paths, "report_outline", "success", detail=f"questions={len(questions)}")
    return questions


_CITATION_REF_RE = re.compile(r"[（(]\s*参考[：:]\s*([^）)]+)[）)]")


def _check_report_citations(body: str, excerpts: list[dict[str, str]]) -> dict:
    """[阶段三：生成后自检，growth_advisor_autonomous_search_and_material_
    improvement_plan.md 第 4 节] 核对 `body` 里出现的『（参考：xxx）』标注
    跟拼进 prompt 的 `excerpts`（阶段一/二产出，元素含 `id` 字段）对不对
    得上，不涉及任何 LLM 调用，纯字符串比对。

    设计取舍：
    - prompt 里只要求"标注页面 id"，没规定 LLM 必须逐字复制完整 id
      （例如 `active_search:python 入门#entity:pandas` 可能被简写成
      `pandas` 或只保留 `#entity:pandas` 后半段），所以用**双向子串
      包含**判断是否"对得上"：`ref in excerpt_id` 或 `excerpt_id in ref`
      任一成立即算命中，避免把"合理简写"误判成"编造引用"而产生大量
      噪音；代价是可能漏判个别确实编造但恰好凑巧是某个 id 子串的引用，
      这类假阴性对"自检"这个诊断性质的功能可以接受，优先控制误报率。
    - 每条 excerpt 最多被计入 `cited_count` 一次，即使正文里对同一条
      摘录标注了多次引用，不重复计数（避免"引用同一条摘录 5 次"跟
      "引用了 5 条不同摘录"在统计上分不清）。
    - `hallucinated_refs`：正文里出现了『（参考：xxx）』但 xxx 跟任何
      一条摘录 id 都对不上——可能是真的编造，也可能是 LLM 引用了自己
      记忆里的其它内容、格式凑巧符合标注要求；这里只做记录，不做任何
      自动纠正或阻断生成，交给下游（看板/CLI）展示决定怎么处理。截断
      到最多 5 条、每条最多 60 字，避免诊断字段本身无限增长。
    """
    excerpt_ids = [str(e.get("id", "")).strip() for e in excerpts if e.get("id")]
    refs = [m.strip() for m in _CITATION_REF_RE.findall(body or "") if m.strip()]

    cited_ids: set[str] = set()
    hallucinated: list[str] = []
    for ref in refs:
        matched = False
        for eid in excerpt_ids:
            if not eid:
                continue
            if ref in eid or eid in ref:
                cited_ids.add(eid)
                matched = True
                break
        if not matched:
            hallucinated.append(ref[:60])

    return {
        "excerpts_total": len(excerpt_ids),
        "cited_count": len(cited_ids),
        "citation_mentions_total": len(refs),
        "hallucinated_refs": hallucinated[:5],
    }


def _compute_excerpt_fingerprint(excerpts: list[dict[str, str]]) -> list[dict]:
    """[growth_advisor_autonomous_search_and_material_improvement_plan.md
    方向"外部世界变化驱动的刷新"] 把一份摘录列表压缩成轻量指纹
    `[{"id": ..., "hash": ...}]`，只存 12 位 md5 前缀，不存原文——
    足够判断"同一个页面 id 的内容有没有变"，不需要也不应该把摘录原文
    存进只追加的报告索引文件（避免无限增长，且原文已经在报告正文里
    留了痕迹，没必要重复存一份）。"""
    return [
        {"id": e.get("id", ""), "hash": hashlib.md5((e.get("excerpt") or "").encode("utf-8")).hexdigest()[:12]}
        for e in excerpts
        if e.get("id")
    ]


def external_signal_drift_for_report(paths, report: "GrowthReport", profile) -> Optional[dict]:
    """[growth_advisor_autonomous_search_and_material_improvement_plan.md
    方向"外部世界变化驱动的刷新"] 拿一份报告生成时的外部摘录指纹
    （`report.external_excerpt_fingerprint`）跟"现在被动扫描能拿到的
    摘录"做比对，判断"外部世界（本地已抓取部分）有没有发生变化"。

    纯只读比对，**不触发任何新的检索或 LLM 调用**——只读本地已经抓取
    落盘的 wiki 页面（`_external_signal_excerpts_for_topic()` 走的是
    被动扫描路径），成本接近于 0，可以放心在 `reports_needing_refresh()`
    这种可能被频繁轮询的入口里调用。

    返回 `None` 表示"没有可比对的基线或当前主题信息"（这份报告当时
    没有外部摘录 / 候选没有 profile 里配置的主题关键词 / 任何异常），
    不是"没有变化"——调用方不应把 `None` 当作"确认无变化"处理。

    返回值非 `None` 时：
    `{"new_excerpt_ids": [...],       # 现在能看到但当时没见过的页面 id
      "changed_excerpt_ids": [...],   # 当时见过、但内容指纹变了的页面 id
      "drift_count": int}`            # 上面两个列表长度之和，用于跟
                                       # `report_external_drift_min_changes`
                                       # 阈值比较
    """
    baseline = getattr(report, "external_excerpt_fingerprint", None)
    if not baseline:
        return None
    try:
        effective = _effective_topic_keywords(profile)
        info = effective.get(report.title)
        if not info:
            return None
        keywords = info["keywords"]
        current_excerpts = _external_signal_excerpts_for_topic(paths, report.title, keywords)
        current = _compute_excerpt_fingerprint(current_excerpts)
        baseline_by_id = {row["id"]: row["hash"] for row in baseline if row.get("id")}
        new_ids: list[str] = []
        changed_ids: list[str] = []
        for row in current:
            eid = row.get("id")
            if not eid:
                continue
            if eid not in baseline_by_id:
                new_ids.append(eid)
            elif baseline_by_id[eid] != row.get("hash"):
                changed_ids.append(eid)
        return {
            "new_excerpt_ids": new_ids,
            "changed_excerpt_ids": changed_ids,
            "drift_count": len(new_ids) + len(changed_ids),
        }
    except Exception:
        return None


def generate_growth_report(
    paths,
    candidate: GrowthCandidate,
    *,
    llm_helper: Optional[Callable[[str], str]] = None,
    is_exploration: bool = False,
    profile=None,
    cfg=None,
    web_search_fn=None,
    quality_auto_upgraded: bool = False,
) -> GrowthReport:
    """为一个候选生成调研报告并落盘。

    P1 默认走规则模板（保证零 LLM 成本也能跑通闭环）；如果调用方传入
    `llm_helper`（例如 cron job 触发时由 Agent 自己的 LLM 会话承担），
    优先用它起草正文，模板兜底失败时的输出。

    `is_exploration`：[P5-6] 调用方（`run_daily_cycle`）判定这份报告来自
    "探索位"时传 `True`——给正文和摘要各加一句管理预期的标注（"这是我们
    不太确定你会不会感兴趣的新方向"），避免被当成"我们觉得这个特别
    重要"。默认 `False`，不影响现有的正常路径。

    `profile`/`cfg`：[N4，growth_advisor_improvement_plan_v4.md 方向二
    2.4 节；growth_advisor_research_quality_plan.md 阶段 1/2/3/4]
    可选参数，`llm_helper` 也传入时才生效，控制以下几件事：

    - `cfg.report_include_external_context`：把 `_external_signal_
      excerpts_for_topic()` 取到的真实摘录（不再只是一个数量）拼进
      prompt，并要求 LLM 引用到的地方标注来源（页面 id）——**不影响
      候选的置信度/排序**，只改 LLM prompt 的输入，`candidate.
      confidence`、`evidence_count_at_generation` 等落盘字段完全不受
      这个开关影响，对齐"仅展示、不影响判断"的克制设计。
    - `cfg.report_dismiss_reason_adaptive_enabled`（默认开启）：如果
      这个方向之前的报告被标过"内容太笼统"
      （`_report_quality_dismiss_counts()` 命中），追加一句强约束
      提醒 LLM 别再写得空泛。不产生额外 LLM 调用，只改 prompt 文字。
    - `cfg.report_two_stage_enabled`（默认关闭）：先让 LLM 提炼几个
      具体问题（`report_outline` 调用），再要求逐一回答，替代固定的
      "四个小节"结构；提纲阶段失败（异常/空响应/解析失败）时静默退回
      单段式 prompt，不影响正文生成本身。

    `web_search_fn`：[growth_advisor_active_search_and_lifecycle_plan.md
    方向一] 可选，签名 `web_search_fn(query: str, max_results: int) ->
    str`（跟 `tools/builtin.py::web_search()` 一致）。仅当
    `cfg.report_active_search_enabled` 开启、`llm_helper` 非 `None`、
    且被动扫描（上面 `report_include_external_context` 那段）命中 0
    条素材时，才会用它现查一次并把结果落一份 wiki 页面供复用；不传时
    行为与改动前完全一致。

    以上参数任一缺失、或规则模板路径（`llm_helper is None`）时，相应
    逻辑整体跳过，向后兼容此前所有不传这些参数的调用方。

    `quality_auto_upgraded`：[growth_advisor_ideal_advisor_gap_and_
    roadmap_plan.md 方向 7] 调用方（`run_daily_cycle`）判定这份报告是
    因为该方向此前被反复标"报告没写好"而临时把这一份报告升级成 LLM
    生成时传 `True`——只影响正文开头追加的一句提示和 `GrowthReport.
    quality_auto_upgraded` 字段，不影响是否真的走 LLM 路径（那由调用
    方传入的 `llm_helper` 是否非 `None` 决定）。默认 `False`，不影响
    现有调用方。
    """
    report_id = uuid.uuid4().hex[:12]
    slug = f"{_slugify(candidate.title)}-{report_id[:6]}"

    body = None
    source = "template"
    # [阶段三：生成后自检，growth_advisor_autonomous_search_and_material_
    # improvement_plan.md 第 4 节] 记录本次实际拼进 prompt 的摘录列表，
    # 供正文生成后核对"是否真的引用了摘录、标注是否属实"。默认空列表，
    # 未开启 `report_include_external_context` 或没取到摘录时保持为空，
    # 下面的自检直接跳过，不影响任何既有行为。
    used_excerpts: list[dict[str, str]] = []
    if llm_helper is not None:
        external_context_section = ""
        if profile is not None and cfg is not None and getattr(cfg, "report_include_external_context", False):
            try:
                effective = _effective_topic_keywords(profile)
                info = effective.get(candidate.title)
                if info:
                    keywords = info["keywords"]
                    ext_count = _external_signal_count_for_topic(paths, candidate.title, keywords)
                    excerpts: list[dict[str, str]] = []
                    if ext_count > 0:
                        excerpts = _external_signal_excerpts_for_topic(paths, candidate.title, keywords)
                    elif (
                        web_search_fn is not None
                        and cfg is not None
                        and getattr(cfg, "report_active_search_enabled", False)
                    ):
                        # [方向一：真正的主动检索] 被动扫描没有可用素材，
                        # 且调用方具备检索工具，现查一次（或多次，见阶段
                        # 二 max_calls）而不是直接放弃。
                        excerpts = _active_search_excerpts_for_topic(
                            paths, candidate, keywords,
                            web_search_fn=web_search_fn, llm_helper=llm_helper,
                            max_calls=int(getattr(cfg, "report_active_search_max_calls", 1) or 1),
                        )
                    if excerpts:
                        used_excerpts = excerpts
                        excerpt_lines = "\n".join(
                            f"- 参考：{e['id']}（{e['date']}）：{e['excerpt']}" for e in excerpts
                        )
                        count_desc = (
                            f"最近 30 天外部世界大约有 {ext_count} 条" if ext_count > 0
                            else f"现查到 {len(excerpts)} 条"
                        )
                        external_context_section = (
                            f"\n[外部背景参考，仅供了解，不改变你的判断] "
                            f"{count_desc}跟这个方向相关的资讯，"
                            "以下是其中几条的摘录（可以参考，但不要照抄，仍然要结合用户自己的"
                            "处境；如果确实参考了某条，请在对应内容后用"
                            "『（参考：页面id）』的形式标注来源，没有参考到的不要提）：\n"
                            f"{excerpt_lines}\n"
                            "这只是背景信息，报告的核心判断仍然要基于用户自己的记忆证据，"
                            "不要因为这个数字改变你对该方向重要性的评估。\n"
                        )
            except Exception:
                external_context_section = ""
                used_excerpts = []

        dismiss_reason_note = ""
        if cfg is not None and getattr(cfg, "report_dismiss_reason_adaptive_enabled", True):
            try:
                if _report_quality_dismiss_counts(paths).get(candidate.title, 0) > 0:
                    dismiss_reason_note = (
                        "\n注意：这个方向之前生成的报告曾被反馈『内容太笼统』，"
                        "这次请务必给出具体、可操作、贴合用户实际处境的建议，"
                        "避免空泛的通用性建议。\n"
                    )
            except Exception:
                dismiss_reason_note = ""

        outline_questions: Optional[list[str]] = None
        if cfg is not None and getattr(cfg, "report_two_stage_enabled", False):
            outline_questions = _generate_report_outline(
                paths, candidate, external_context_section, llm_helper
            )

        if outline_questions:
            questions_block = "\n".join(f"{i}. {q}" for i, q in enumerate(outline_questions, 1))
            prompt = (
                "请为以下用户成长方向候选撰写一份简短调研报告（Markdown），"
                "逐一具体回答下面这几个问题（每个问题一个小节，标题用问题原文"
                "或精简版均可，不要超过 500 字）：\n"
                f"{questions_block}\n\n"
                f"主题：{candidate.title}\n理由：{candidate.rationale}\n"
                f"{external_context_section}{dismiss_reason_note}"
            )
        else:
            prompt = (
                "请为以下用户成长方向候选撰写一份简短调研报告（Markdown，"
                "包含：为什么值得关注、可以怎么入门、常见资源/路径、"
                "预计投入与见效周期，4 个小节即可，不要超过 500 字）：\n"
                f"主题：{candidate.title}\n理由：{candidate.rationale}\n"
                f"{external_context_section}{dismiss_reason_note}"
            )
        try:
            body = llm_helper(prompt)
            if body and body.strip():
                source = "llm"
                _record_llm_call_status(paths, "report_quality", "success")
            else:
                _record_llm_call_status(paths, "report_quality", "empty_response")
        except Exception as exc:
            body = None
            _record_llm_call_status(paths, "report_quality", "error", detail=str(exc)[:200])

    # [阶段三：生成后自检] 只有真的拼过摘录进 prompt、且正文确实由 LLM
    # 生成时才有核对的意义——规则模板兜底路径不引用任何外部摘录，检查
    # 没有对象，直接跳过（`citation_check` 落 `None`，不产生误报）。
    citation_check: Optional[dict] = None
    if used_excerpts and body and source == "llm":
        try:
            citation_check = _check_report_citations(body, used_excerpts)
        except Exception:
            citation_check = None

    # [方向"外部世界变化驱动的刷新"] 只要这次真的拿到了摘录（无论最终
    # 正文走的是 LLM 还是模板兜底），就记一份指纹留作基线——这里回答
    # "外部世界（本地已抓取部分）有没有变化"，跟上面 `citation_check`
    # （回答"LLM 有没有如实引用"）是两个独立问题，不共用同一个触发
    # 条件。
    external_excerpt_fingerprint: Optional[list[dict]] = None
    if used_excerpts:
        try:
            external_excerpt_fingerprint = _compute_excerpt_fingerprint(used_excerpts)
        except Exception:
            external_excerpt_fingerprint = None

    if not body:
        body = (
            f"# {candidate.title}\n\n"
            f"## 为什么值得关注\n{candidate.rationale}\n\n"
            "## 可以怎么入门\n"
            "- 先花 30 分钟检索该方向的入门资料，建立整体轮廓\n"
            "- 找一个与近期实际任务相关的小切口先动手试一次\n\n"
            "## 常见资源/路径\n"
            "- 官方文档 / 权威教程（优先）\n"
            "- 社区实践案例，关注踩坑记录\n\n"
            "## 预计投入与见效周期\n"
            "建议先按 1~2 周的轻量投入评估是否继续深入，避免一次性重投入。\n"
        )

    summary = candidate.rationale
    if is_exploration:
        # [P5-6] 探索位候选：正文和摘要各加一句管理预期的标注，避免被
        # 当成"我们觉得这个特别重要"。
        note = "> 这是我们不太确定你会不会感兴趣的新方向，证据还不算多，供参考。\n\n"
        body = note + body
        summary = "[探索方向] " + summary
    if quality_auto_upgraded:
        # [方向 7] 因质量信号自动升级：跟探索位标注同一种"管理预期"的
        # 展示方式，让用户知道这份报告为什么跟以往不太一样，而不是悄悄
        # 换了生成方式却不告诉用户。
        note = "> 这个方向之前的报告被反馈过内容太笼统，这一份自动换成了更详细的生成方式。\n\n"
        body = note + body

    report_path = paths.growth_report_path(slug)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(body, encoding="utf-8")

    report = GrowthReport(
        report_id=report_id,
        candidate_id=candidate.candidate_id,
        title=candidate.title,
        slug=slug,
        summary=summary,
        body_path=str(report_path),
        source=source,
        evidence_count_at_generation=candidate.evidence_count,
        is_exploration=is_exploration,
        quality_auto_upgraded=quality_auto_upgraded,
        citation_check=citation_check,
        external_excerpt_fingerprint=external_excerpt_fingerprint,
    )
    _append_jsonl(paths.growth_reports_index_path, report.to_dict())
    GrowthBacklog(paths).attach_report(candidate.candidate_id, report_id)
    return report


def list_reports(paths, *, include_archived: bool = False) -> list[GrowthReport]:
    """返回活跃索引（`growth_reports.jsonl`）里的报告，默认不含已被
    `compact_reports_index_storage()` 归档掉的旧报告——这跟改动前的行为
    完全一致（`compact_reports_index_storage()` 是本轮 P5-0 新增的功能，
    不调用它就不会有任何报告被移出这个文件）。`include_archived=True`
    时额外并入归档文件，供"报告生成总数"这类累计统计使用。"""
    rows = _read_jsonl(paths.growth_reports_index_path)
    if include_archived:
        rows = rows + _read_jsonl(paths.growth_reports_archive_path)
    return [GrowthReport.from_dict(d) for d in rows]


def get_report_by_id(paths, report_id: str) -> Optional[GrowthReport]:
    """按 `report_id` 查单份报告：先查活跃索引（常见路径，几乎所有调用都
    是查"当前候选挂着的那份"），查不到再查归档文件——保证"直接打开一份
    很久以前生成、后来被刷新替换掉的旧报告"这类场景在 P5-0 归档上线后
    仍然可用，不会因为报告被移出活跃索引就变成 404。"""
    for d in _read_jsonl(paths.growth_reports_index_path):
        if d.get("report_id") == report_id:
            return GrowthReport.from_dict(d)
    for d in _read_jsonl(paths.growth_reports_archive_path):
        if d.get("report_id") == report_id:
            return GrowthReport.from_dict(d)
    return None


# ────────────────────── 学习素材（执行向，跟报告分层）──────────────────────
#
# [growth_advisor_autonomous_search_and_material_improvement_plan.md
# 方向"报告与学习素材分层"] `generate_growth_report()` 生成的是决策向
# 简报（回答"值不值得投入"），下面这组函数生成的是执行向的结构化
# 产物（回答"投入之后怎么学"）——固定三段：学习路径、资源清单、第一
# 个可执行任务，见 `GrowthLearningMaterial` docstring。设计上刻意跟
# 报告完全独立（各自的索引文件、正文目录、生成函数），互不依赖：素材
# 可以基于已有报告生成（复用报告正文归纳三段结构，`based_on_report_id`
# 记录来源），也可以在候选还没有报告时独立生成。


def _learning_material_template_body(
    title: str, rationale: str, learning_path: list[str],
    resources: list[str], first_task: str,
) -> str:
    path_lines = "\n".join(f"{i}. {step}" for i, step in enumerate(learning_path, 1)) or "（暂无）"
    resource_lines = "\n".join(f"- {r}" for r in resources) or "（暂无）"
    return (
        f"# {title} · 学习素材\n\n"
        f"{rationale}\n\n"
        "## 学习路径\n"
        f"{path_lines}\n\n"
        "## 资源清单\n"
        f"{resource_lines}\n\n"
        "## 现在就可以做的第一件事\n"
        f"{first_task}\n"
    )


def _default_learning_path(title: str) -> list[str]:
    """规则模板兜底的学习路径——三步走的通用骨架，不针对具体主题，
    只保证"没有 LLM 时也能给出一个能直接照做的最小闭环"，跟
    `generate_growth_report()` 的规则模板同等定位（零成本兜底，不追求
    针对性）。"""
    return [
        f"用 30 分钟检索『{title}』的官方文档或权威入门教程，建立整体轮廓",
        "挑一个跟近期实际任务相关的小切口，动手做一次最小可行的尝试",
        "记录下卡住的地方，下次调研或请教时优先解决这些具体问题",
    ]


def generate_learning_material(
    paths,
    candidate: GrowthCandidate,
    *,
    llm_helper: Optional[Callable[[str], str]] = None,
    report: Optional[GrowthReport] = None,
) -> GrowthLearningMaterial:
    """为一个候选生成"学习素材"（执行向：学习路径 + 资源清单 + 第一个
    可执行任务），跟 `generate_growth_report()`（决策向简报）是两个
    独立的生成入口，可以先后调用、也可以只调用其中一个。

    `report`：可选，传入该候选已有的调研报告时，规则模板兜底路径会
    引用报告的 `summary` 作为素材开头的一句话背景（素材不是报告的
    简单复制，只是复用"为什么值得关注"这一句，避免重复归纳）；不传
    时用 `candidate.rationale` 兜底，效果等价。

    `llm_helper`：非 `None` 时优先让 LLM 生成三段结构化内容（要求
    返回 JSON：`{"learning_path": [...], "resources": [...],
    "first_task": "..."}`），解析失败/异常/空响应时静默退回规则模板，
    保证任何情况下都有可用产物、不会因为 LLM 抖动就生成失败。
    """
    material_id = uuid.uuid4().hex[:12]
    slug = f"{_slugify(candidate.title)}-{material_id[:6]}"
    background = (report.summary if report is not None and report.summary else candidate.rationale)

    learning_path: list[str] = []
    resources: list[str] = []
    first_task = ""
    source = "template"

    if llm_helper is not None:
        prompt = (
            "请为以下用户成长方向候选，生成一份『学习素材』的结构化内容，"
            "只返回 JSON（不要任何多余文字/Markdown 代码块标记），格式：\n"
            '{"learning_path": ["步骤1", "步骤2", "步骤3"], '
            '"resources": ["资源1", "资源2"], "first_task": "一句话描述的具体任务"}\n'
            f"主题：{candidate.title}\n背景：{background}\n"
            "learning_path 要求 3~6 步、有先后顺序、每步一句话可执行；"
            "resources 要求 2~5 条、具体到可检索的名称（不要求真实链接）；"
            "first_task 要求是现在就能动手做的具体任务，不要写成\"了解一下\"这种笼统建议。"
        )
        try:
            raw = llm_helper(prompt)
            if raw and raw.strip():
                text = raw.strip()
                if text.startswith("```"):
                    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
                    text = re.sub(r"```\s*$", "", text).strip()
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    match = re.search(r"\{.*\}", text, re.DOTALL)
                    parsed = json.loads(match.group(0)) if match else None
                if isinstance(parsed, dict):
                    lp = [str(s).strip() for s in (parsed.get("learning_path") or []) if str(s).strip()]
                    rs = [str(s).strip() for s in (parsed.get("resources") or []) if str(s).strip()]
                    ft = str(parsed.get("first_task") or "").strip()
                    if lp and ft:
                        learning_path, resources, first_task = lp, rs, ft
                        source = "llm"
        except Exception:
            learning_path, resources, first_task, source = [], [], "", "template"

    if not learning_path or not first_task:
        learning_path = _default_learning_path(candidate.title)
        resources = resources or ["官方文档 / 权威教程（优先）", "社区实践案例，关注踩坑记录"]
        first_task = first_task or f"花 30 分钟检索『{candidate.title}』的入门资料，写下 3 个具体问题"
        source = "template"

    body = _learning_material_template_body(
        candidate.title, background, learning_path, resources, first_task,
    )
    body_path = paths.growth_material_path(slug)
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_path.write_text(body, encoding="utf-8")

    material = GrowthLearningMaterial(
        material_id=material_id,
        candidate_id=candidate.candidate_id,
        title=candidate.title,
        slug=slug,
        learning_path=learning_path,
        resources=resources,
        first_task=first_task,
        body_path=str(body_path),
        source=source,
        based_on_report_id=(report.report_id if report is not None else None),
    )
    _append_jsonl(paths.growth_materials_index_path, material.to_dict())
    GrowthBacklog(paths).attach_material(candidate.candidate_id, material_id)
    return material


def list_materials(paths) -> list[GrowthLearningMaterial]:
    """返回全部已生成的学习素材（跟 `list_reports()` 一样，只追加不
    轮转——学习素材数量远小于报告，暂不需要归档机制）。"""
    return [GrowthLearningMaterial.from_dict(d) for d in _read_jsonl(paths.growth_materials_index_path)]


def get_material_by_id(paths, material_id: str) -> Optional[GrowthLearningMaterial]:
    for d in _read_jsonl(paths.growth_materials_index_path):
        if d.get("material_id") == material_id:
            return GrowthLearningMaterial.from_dict(d)
    return None


# [P5-0] next_doc/growth_advisor_improvement_plan_v3.md 存储卫生第二部分：
# growth_reports_index.jsonl 只追加不轮转，长期运行会无限增长。审计结论
# （见 growth_advisor_implementation_record.md P5-0 章节的"依赖全量读取的
# 函数清单"）：唯一依赖"当前挂着的那份报告"语义的 `reports_needing_
# refresh()` 只通过 `candidate.report_id` 查表，从不关心已经被替换掉的
# 旧报告；`_maybe_dispatch_weekly_digest()` 的窗口只有 7 天，远小于下面
# 的归档窗口，不会被影响。真正需要小心的是"通过 report_id 直接查看某份
# 报告正文"这个场景（看板/CLI 里可能存着旧的 report_id 链接）——为此新增
# `get_report_by_id()` 兜底查归档文件，不会因为报告被归档就 404。
_REPORTS_ARCHIVE_WINDOW_DAYS = 180


def compact_reports_index_storage(paths, *, now: Optional[float] = None) -> int:
    """把"已经不是任何候选当前挂着的那份、且生成时间超过归档窗口"的旧
    报告从活跃索引移到归档文件，返回被归档的条数（0 表示无操作，不触发
    写盘）。供人工维护脚本或未来的月度 cron 调用；不在
    `run_daily_cycle()` 的每日路径里自动触发（报告归档比 topic_trend
    降采样更需要谨慎，不适合悄悄地每天跑）。"""
    now = now if now is not None else time.time()
    cutoff = now - _REPORTS_ARCHIVE_WINDOW_DAYS * 86400
    active_rows = _read_jsonl(paths.growth_reports_index_path)
    if not active_rows:
        return 0
    attached_ids = {c.report_id for c in GrowthBacklog(paths).load_all() if c.report_id}
    stale_rows = [
        r for r in active_rows
        if r.get("report_id") not in attached_ids and r.get("created_at", now) < cutoff
    ]
    if not stale_rows:
        return 0
    kept_rows = [r for r in active_rows if r not in stale_rows]
    paths.growth_reports_archive_path.parent.mkdir(parents=True, exist_ok=True)
    with paths.growth_reports_archive_path.open("a", encoding="utf-8") as f:
        for r in stale_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    _write_jsonl(paths.growth_reports_index_path, kept_rows)
    return len(stale_rows)


# ────────── [P4-6] 主题证据数走势 ──────────
# next_doc/growth_advisor_improvement_plan_v2.md P4-6 第二条："growth_topic_map
# 目前只有峰值/当前两个值，没有中间时间序列，加一个简单的按扫描轮次的证据
# 数走势"。用一个独立的只追加 jsonl 记录每轮 `growth_candidate_derive()`
# 处理到的每个主题的证据数快照，跟 `growth_backlog.jsonl`（只存当前状态，
# 合并会覆盖历史证据数）分开，避免为了"看走势"污染候选队列本身的数据
# 结构。按 dedupe_key 存，兼容同一主题标题的大小写/空格差异。

# 单个主题查询/展示时最多回看多少个快照点——按扫描轮次而不是按天数，
# 因为 cron 频率是可配的，"最近 N 轮"比"最近 N 天"更贴合"扫描轮次"这个
# 原始诉求。
_DEFAULT_TREND_MAX_POINTS = 20

# [P5-0] next_doc/growth_advisor_improvement_plan_v3.md 存储卫生：
# growth_topic_trend.jsonl 是纯只追加文件，长期运行（每天一轮 cron，每轮
# 每个 focus topic 都记一条）行数会无限增长，但 `_topic_trend_series()`
# 只关心"最近 N 个点"，早期的高频快照对展示价值很低。这里做一个轻量
# 降采样：超过 `_TREND_RAW_WINDOW_DAYS` 天的旧快照，同一个主题
# （`dedupe_key`）同一周只保留最新的一条（代表"这一周末的证据数"），
# 窗口内的近期快照保持逐条不动，不改变 `_topic_trend_series()` 的调用
# 契约（依然是"按时间正序，最多取最近 limit 条"），因为被压缩掉的都是
# 早已经落在 limit 窗口之外的历史点。
_TREND_RAW_WINDOW_DAYS = 60


def _compact_topic_trend_rows(rows: list[dict], *, now: Optional[float] = None) -> list[dict]:
    """纯函数：对 growth_topic_trend 的行做降采样，返回压缩后的新列表
    （不做任何 IO）。`now` 参数只为测试可控注入，默认取当前时间。"""
    now = now if now is not None else time.time()
    cutoff = now - _TREND_RAW_WINDOW_DAYS * 86400
    recent = [r for r in rows if r.get("scanned_at", 0) >= cutoff]
    old = [r for r in rows if r.get("scanned_at", 0) < cutoff]
    if not old:
        return rows
    # 按 (dedupe_key, 周编号) 分桶，桶内只保留 scanned_at 最大的那条。
    buckets: dict[tuple, dict] = {}
    for r in old:
        ts = r.get("scanned_at", 0)
        week_bucket = (r.get("dedupe_key"), int(ts // (7 * 86400)))
        existing = buckets.get(week_bucket)
        if existing is None or ts > existing.get("scanned_at", 0):
            buckets[week_bucket] = r
    out = list(buckets.values()) + recent
    out.sort(key=lambda r: r.get("scanned_at", 0))
    return out


def compact_topic_trend_storage(paths, *, now: Optional[float] = None) -> int:
    """对落盘的 growth_topic_trend.jsonl 做一次降采样压缩，返回被压缩掉
    的行数（0 表示本次没有可压缩的旧数据，不会触发写盘）。供
    `growth_candidate_derive()` 每轮 cron 顺带调用，也可以单独调用（比如
    手动维护脚本），是幂等操作。"""
    rows = _read_jsonl(paths.growth_topic_trend_path)
    if not rows:
        return 0
    compacted = _compact_topic_trend_rows(rows, now=now)
    removed = len(rows) - len(compacted)
    if removed > 0:
        _write_jsonl(paths.growth_topic_trend_path, compacted)
    return removed


def _record_topic_trend_snapshot(
    paths, topic: str, evidence_count: int, confidence: Optional[float]
) -> None:
    _append_jsonl(
        paths.growth_topic_trend_path,
        {
            "dedupe_key": normalize_title_key(topic),
            "topic": topic,
            "scanned_at": time.time(),
            "evidence_count": evidence_count,
            "confidence": confidence,
        },
    )


def _topic_trend_series(paths, dedupe_key: str, limit: int = _DEFAULT_TREND_MAX_POINTS) -> list[dict]:
    """返回某个主题按时间正序排列的快照点，最多取最近 `limit` 个（早期
    的点丢弃，展示更关心"最近的走势"而不是完整历史）。"""
    rows = [
        {"scanned_at": r["scanned_at"], "evidence_count": r["evidence_count"], "confidence": r.get("confidence")}
        for r in _read_jsonl(paths.growth_topic_trend_path)
        if r.get("dedupe_key") == dedupe_key
    ]
    rows.sort(key=lambda r: r["scanned_at"])
    return rows[-limit:] if limit else rows


# ────────── [v4 N1] 诊断面板健康度趋势化 ──────────
# next_doc/growth_advisor_improvement_plan_v4.md 方向三：
# `diagnostics_snapshot()` 只有"当下"，没有"变化"。这里新增一个平行于
# `growth_topic_trend.jsonl`（单主题证据数走势）的"全局健康度"快照序列，
# 复用同一套"只追加 + 定期降采样"模式。字段选取原则：只记
# `diagnostics_snapshot()` 已经在展示的数字，避免"趋势图上的数字"和
# "诊断面板上的数字"来源不一致造成用户困惑。

_HEALTH_TREND_RAW_WINDOW_DAYS = 60
_DEFAULT_HEALTH_TREND_MAX_POINTS = 30


def _compact_health_trend_rows(rows: list[dict], *, now: Optional[float] = None) -> list[dict]:
    """纯函数：对 growth_health_trend 的行做降采样，返回压缩后的新列表
    （不做任何 IO）。跟 `_compact_topic_trend_rows()` 是平行实现——健康度
    快照本身就是"每天最多一条"（见 `_record_health_snapshot()`），所以当前
    阶段这里基本不会真的压缩掉数据；预留这个函数主要是为未来"提高记录
    频率"这类改动留一个安全网，不需要到时候再补治理。"""
    now = now if now is not None else time.time()
    cutoff = now - _HEALTH_TREND_RAW_WINDOW_DAYS * 86400
    recent = [r for r in rows if r.get("recorded_at", 0) >= cutoff]
    old = [r for r in rows if r.get("recorded_at", 0) < cutoff]
    if not old:
        return rows
    # 按天分桶，桶内只保留 recorded_at 最大的那条（同一天多条快照的场景，
    # 当前阶段每日一条不会触发，但函数本身要能正确处理）。
    buckets: dict[int, dict] = {}
    for r in old:
        ts = r.get("recorded_at", 0)
        day_bucket = int(ts // 86400)
        existing = buckets.get(day_bucket)
        if existing is None or ts > existing.get("recorded_at", 0):
            buckets[day_bucket] = r
    out = list(buckets.values()) + recent
    out.sort(key=lambda r: r.get("recorded_at", 0))
    return out


def compact_health_trend_storage(paths, *, now: Optional[float] = None) -> int:
    """对落盘的 growth_health_trend.jsonl 做一次降采样压缩，返回被压缩掉
    的行数（0 表示本次没有可压缩的旧数据，不会触发写盘）。幂等操作，跟
    `compact_topic_trend_storage()` 的调用契约一致。"""
    rows = _read_jsonl(paths.growth_health_trend_path)
    if not rows:
        return 0
    compacted = _compact_health_trend_rows(rows, now=now)
    removed = len(rows) - len(compacted)
    if removed > 0:
        _write_jsonl(paths.growth_health_trend_path, compacted)
    return removed


def _record_health_snapshot(paths, cfg, profile, memory_store) -> dict[str, Any]:
    """在 `run_daily_cycle()` 每轮结束时记一条全局健康度快照，供看板画
    趋势图。只应该在 `run_daily_cycle()` 这个既有的每日调用点触发，不应该
    被其它地方高频调用——`diagnostics_snapshot()` 内部有几处"扫描记忆
    全量"的计算（比如 backfill_candidates_count），每天一次调用可以接受，
    更高频率需要重新评估开销。返回写入的快照字典，供调用方需要时直接
    使用（比如测试断言），不强制调用方重新读盘。"""
    snap = diagnostics_snapshot(paths, cfg, profile, memory_store)
    row = {
        "recorded_at": time.time(),
        "total_entries": snap["memory"]["total_entries"],
        "entries_in_scan_window": snap["memory"]["entries_in_scan_window"],
        "backfill_candidates_count": snap["memory"]["backfill_candidates_count"],
        "pending_followups_count": snap["pending_followups_count"],
        "reports_needing_refresh_count": snap["reports_needing_refresh_count"],
        "topics_tracked_count": len(snap["signal_scan"]["topics_tracked"]),
    }
    _append_jsonl(paths.growth_health_trend_path, row)
    return row


def health_trend_series(paths, *, limit: int = _DEFAULT_HEALTH_TREND_MAX_POINTS) -> list[dict]:
    """返回最近 `limit` 个健康度快照，按时间正序，供看板画折线图/API
    `GET /growth/health_trend` 直接返回。跟 `_topic_trend_series()` 的
    调用契约一致（早期的点丢弃，只关心"最近的走势"）。"""
    rows = [
        {
            "recorded_at": r.get("recorded_at"),
            "total_entries": r.get("total_entries"),
            "entries_in_scan_window": r.get("entries_in_scan_window"),
            "backfill_candidates_count": r.get("backfill_candidates_count"),
            "pending_followups_count": r.get("pending_followups_count"),
            "reports_needing_refresh_count": r.get("reports_needing_refresh_count"),
            "topics_tracked_count": r.get("topics_tracked_count"),
        }
        for r in _read_jsonl(paths.growth_health_trend_path)
    ]
    rows.sort(key=lambda r: r.get("recorded_at") or 0)
    return rows[-limit:] if limit else rows


# ────────── [P5-4] 回访/报告刷新接入被动信号，减少主动打扰 ──────────
# next_doc/growth_advisor_improvement_plan_v3.md P5-4：回访（P4-3）/报告
# 刷新提示（P4-4）都是纯被动的"到点就问"，没有先看一眼手边已有的
# `growth_topic_trend.jsonl`（P4-6）判断"用户是不是其实还在关注"。这里
# 两处都复用同一个"看趋势快照"的思路，都是纯只读判断（不新增存储、不
# 持久化"已经推迟过一次"这类状态）——每次调用都基于当时的快照数据现算，
# 跟 `reports_needing_refresh()` 本来就是"纯只读聚合"的风格保持一致，
# 也避免了"要不要撤销已经推迟的标记"这类需要额外语义澄清的状态管理。


def _topic_trend_rising(paths, dedupe_key: str, *, window_days: int) -> Optional[bool]:
    """判断某个主题最近 `window_days` 天内的证据数走势是"在涨"还是"走平/
    下降"。数据点不足（窗口内少于 2 个快照点）时返回 `None`，代表"没有
    足够信息判断"——调用方应当把 `None` 当成"不确定"处理，不能默认成
    "在涨"或"没在涨"中的任何一个，避免在数据稀疏时把不确定性伪装成
    确定的判断。
    """
    series = _topic_trend_series(paths, dedupe_key)
    cutoff = time.time() - max(0, window_days) * 86400
    in_window = [p for p in series if p["scanned_at"] >= cutoff]
    if len(in_window) < 2:
        return None
    return in_window[-1]["evidence_count"] > in_window[0]["evidence_count"]


def _goal_progress_signal(goal_backlog, goal_id: str, *, stalled_days: int) -> Optional[str]:
    """[growth_advisor_goal_cron_integration_plan.md 阶段 C] 从一个已
    关联的 Goal 节点读取真实状态，判断这个方向"是不是还在推进"，作为
    比 memory 证据数走势更直接的回访信号来源。

    返回 `"progressed"` / `"stalled"` / `None`（找不到 Goal 或
    `goal_backlog` 不可用，调用方应退化到原有的 memory 证据数走势逻辑，
    不能当成"确定没推进"）。
    """
    if goal_backlog is None or not goal_id:
        return None
    try:
        goal = goal_backlog.get(goal_id)
    except Exception:
        return None
    if goal is None:
        return None
    if goal.status == "completed":
        return "progressed"
    if goal.status == "active":
        cutoff = time.time() - max(0, stalled_days) * 86400
        return "progressed" if (goal.last_touched_at or 0) >= cutoff else "stalled"
    # paused / abandoned / failed / cancelled 都算"没在推进"
    return "stalled"


def followup_question_hint(paths, candidate: GrowthCandidate, *, cfg=None, goal_backlog=None) -> str:
    """给回访卡片挑一句更贴合实际状态的提问，而不是固定的"有推进/没空"。
    看板侧读取这个字段决定文案，不影响 `pending_followups()`/
    `record_followup()` 本身的行为（回答仍然只有 progressed/stalled 两种，
    只是问法更贴切）。

    [growth_advisor_goal_cron_integration_plan.md 阶段 C] 候选已关联
    Goal 时优先用 Goal 真实状态措辞；未关联或 `goal_backlog` 未传入时
    完全走原有的 memory 证据数走势逻辑，行为不变。
    """
    days = getattr(cfg, "followup_review_days", 30) if cfg is not None else 30
    if candidate.linked_goal_id:
        stalled_days = getattr(cfg, "goal_alignment_stalled_days", 21) if cfg is not None else 21
        signal = _goal_progress_signal(goal_backlog, candidate.linked_goal_id, stalled_days=stalled_days)
        if signal == "stalled":
            # [growth_advisor_autonomy_deepening_plan.md 方向 A2] 停滞的
            # 原因粗分两类：素材已经饱和（B2 信号）vs 执行本身没有真正
            # 跑起来（cron 被跳过/失败，或者压根没绑定成功）。前者是
            # "方向讲得差不多了"，后者是系统自己的问题——两种情况的
            # 用户措辞不应该一样，否则会让用户误以为"这个方向不值得
            # 继续"，实际上只是执行环节卡住了。只对已经绑定了周期性
            # 执行的 Goal 做这个区分（`recurring=True`）；一次性 Goal
            # 停滞的语义跟"自主持续调研"场景不同，走原有措辞。
            goal = None
            try:
                goal = goal_backlog.get(candidate.linked_goal_id)
            except Exception:
                goal = None
            if goal is not None and getattr(goal, "recurring", False):
                saturation = get_pursuit_saturation(paths, goal.id)
                if saturation.get("saturated"):
                    return (
                        f"「{candidate.title}」最近 {saturation.get('streak')} 轮新增内容不多了，"
                        "是这个方向已经了解得差不多，还是希望换个角度继续深挖？"
                        "可以考虑把频率降低一些，或先告一段落。"
                    )
                return (
                    f"「{candidate.title}」绑定的自主调研看起来有一阵没真正推进——"
                    "更像是执行环节遇到了问题（比如任务被跳过/失败），"
                    "建议去「🎯 目标」tab 看一眼执行状态，而不是这个方向本身不值得继续。"
                )
            return f"「{candidate.title}」对应的目标看起来有一阵没动了，要不要先放一放，或者重新规划一下？"
        if signal == "progressed":
            return f"「{candidate.title}」对应的目标最近还在推进，要不要跟我说说进展？"
    rising = _topic_trend_rising(paths, candidate.dedupe_key(), window_days=days)
    if rising is False:
        return f"最近「{candidate.title}」相关的记忆变少了，是先放一放了吗？"
    return f"「{candidate.title}」这个方向，后续有没有真的推进？"


def pending_followups(paths, cfg=None, *, goal_backlog=None) -> list[GrowthCandidate]:
    """返回已采纳、满足回访窗口、且尚未回访过的候选（供看板渲染"这个方向
    后续有没有推进？"的回访卡片）。

    [P5-4] 到期候选先看一眼窗口期内的证据数走势：如果趋势判断结果是
    "在涨"（`_topic_trend_rising` 返回 `True`），证据本身已经说明用户
    还在关注，直接跳过这次主动询问、顺延到下一轮再看（不持久化"已推迟"
    状态——纯按当次快照现算，下次调用如果趋势不再涨了自然就会展示，也
    不需要考虑"如何撤销推迟标记"）。趋势不确定（快照点不足，返回
    `None`）或判断为走平/下降时，仍按原逻辑展示回访卡片。

    [growth_advisor_goal_cron_integration_plan.md 阶段 C] 候选已关联
    Goal 且传入了 `goal_backlog` 时，优先用 Goal 真实状态判断：
    - Goal 已 `completed` → 视为显而易见的"已推进"，直接写回
      `record_followup(..., "progressed")`，不占用一次主动询问；
    - Goal 仍在正常推进（`active` 且近期有 touch）→ 跳过本轮，顺延；
    - Goal 已停滞/暂停/放弃/失败/取消 → 正常展示回访卡片（问法见
      `followup_question_hint()`）。
    未关联 Goal，或未传入 `goal_backlog`（老调用点未升级）时，完全
    退化为原有的 memory 证据数走势逻辑，不影响既有行为。
    """
    days = getattr(cfg, "followup_review_days", 30) if cfg is not None else 30
    stalled_days = getattr(cfg, "goal_alignment_stalled_days", 21) if cfg is not None else 21
    cutoff = time.time() - max(0, days) * 86400
    out = []
    for c in GrowthBacklog(paths).load_all():
        if c.status != STATUS_ACCEPTED or c.followup_status is not None:
            continue
        if c.accepted_at is None or c.accepted_at > cutoff:
            continue

        if c.linked_goal_id and goal_backlog is not None:
            signal = _goal_progress_signal(goal_backlog, c.linked_goal_id, stalled_days=stalled_days)
            if signal == "progressed":
                goal = None
                try:
                    goal = goal_backlog.get(c.linked_goal_id)
                except Exception:
                    goal = None
                if goal is not None and goal.status == "completed":
                    # 已经完成，不需要用户再确认，直接记录并跳过展示。
                    record_followup(paths, c.candidate_id, "progressed")
                    continue
                continue  # 仍在正常推进，顺延一轮，不主动打扰
            if signal == "stalled":
                out.append(c)
                continue
            # signal is None：找不到 Goal 或 goal_backlog 异常，退化到
            # memory 证据数走势逻辑（走到下面的兜底分支）。

        if _topic_trend_rising(paths, c.dedupe_key(), window_days=days) is True:
            continue  # 证据还在涨，顺延一轮，不主动打扰
        out.append(c)
    return sorted(out, key=lambda c: c.accepted_at or 0)


# ────────── [P4-4] 报告质量分级 / 增量刷新 ──────────
# next_doc/growth_advisor_improvement_plan_v2.md P4-4：默认模板报告保持
# 零成本，`report_quality_llm_enabled` 是独立于 `llm_signal_augment_enabled`
# 的另一个 opt-in 开关——后者控制"扫描阶段要不要多花一次 LLM 调用去归纳
# 新主题"，这个开关控制"生成调研报告正文时要不要多花一次 LLM 调用换取
# 更高信息密度"，两者互不影响，用户可以只开一个。

# 候选证据数比上一次生成报告时又新增达到这个数量，才提示"可以刷新了"，
# 避免证据每多 1 条就被打扰。
_DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE = 3
# [P5-4] 判断"新增证据是不是最近突然冒出来的"用的窗口——证据是这两天
# 突然涨的，可能是用户主动在推进这件事，看板展示顺序应该比"证据在几个
# 月里慢慢攒够阈值"的更靠前。
_REPORT_REFRESH_RECENT_BURST_WINDOW_DAYS = 14


def _recent_evidence_delta(paths, dedupe_key: str, *, window_days: int) -> Optional[int]:
    """从趋势快照里估算最近 `window_days` 天内新增了多少证据：用最新一个
    快照点减去"窗口边界之前最后一个快照点"（如果全部快照都落在窗口内，
    说明这个候选本身历史就短，直接把最早的一个点当基线，相当于把全部
    证据都算作"最近"）。快照点不足 2 个（数据不够判断）时返回 `None`，
    调用方应当把这种情况当"没有额外信息"处理，退化为只按 `new_evidence`
    总量排序，不能默认成 0（0 意味着"确定没有最近突增"，跟"不知道"是
    两回事）。
    """
    series = _topic_trend_series(paths, dedupe_key)
    if len(series) < 2:
        return None
    cutoff = time.time() - max(0, window_days) * 86400
    baseline = series[0]
    for point in series:
        if point["scanned_at"] <= cutoff:
            baseline = point
        else:
            break
    return max(0, series[-1]["evidence_count"] - baseline["evidence_count"])


def reports_needing_refresh(paths, cfg=None, *, goal_backlog=None, profile=None) -> list[dict]:
    """返回"生成之后证据又显著增长、值得提示用户刷新一下"的报告列表。
    只看每个候选**当前挂着的那份报告**（`candidate.report_id`），已经被
    刷新过的旧报告不会重复出现。纯只读聚合，不做任何写入。

    [P5-4] 排序不再单纯按 `new_evidence` 总量：额外算一个
    `recent_evidence_delta`（最近 14 天内新增的证据数，见
    `_recent_evidence_delta`），证据是最近突然涨的排在前面——即便总量
    暂时不如另一个"证据在几个月里慢慢攒够阈值"的候选，前者更可能是
    用户正在主动推进、当下更值得优先刷新。没有足够趋势快照数据判断的
    候选（`recent_evidence_delta is None`）退化按 `new_evidence` 排序，
    不会被误判成"没有最近突增"而排到最后。

    `goal_backlog`：[growth_advisor_autonomy_deepening_plan.md 方向 A1]
    可选。传入时，已经落地成 Goal 且该 Goal 处于 `recurring=True` 的
    候选会被跳过——它的素材已经由 `growth_pursuit` 周期性执行接管
    （每天/每周自动往 wiki 页面追加），不再需要"报告刷新"这条独立
    路径，两套机制回答的是同一个问题，继续并存只会让用户搞不清该看
    哪一个。不传（`None`，默认值）时行为与改动前完全一致，向后兼容
    所有既有调用方。

    `profile`：[growth_advisor_autonomous_search_and_material_
    improvement_plan.md 方向"外部世界变化驱动的刷新"] 可选。只有同时
    传入 `profile` 且 `cfg.report_external_drift_refresh_enabled` 开启
    时，才会额外用 `external_signal_drift_for_report()` 判断"外部世界
    是否发生变化"，作为跟"证据数增长"平行的第二个触发条件（两者是
    OR 关系——任一条件满足就纳入待刷新列表）。不传 `profile`（默认
    `None`）或 `cfg` 里没开这个开关时，行为跟改动前完全一致，不会
    多算一次比对开销。命中该条件的行返回时会带 `external_drift` 字段
    （否则不带该键——保持返回结构对"没启用这个方向"的调用方完全
    不变，而不是恒定输出一个 `None` 占位）。
    """
    min_new = getattr(cfg, "report_refresh_min_new_evidence", _DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE) if cfg is not None else _DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE
    drift_enabled = profile is not None and bool(getattr(cfg, "report_external_drift_refresh_enabled", False))
    drift_min_changes = getattr(cfg, "report_external_drift_min_changes", 1) if cfg is not None else 1
    reports_by_id = {r.report_id: r for r in list_reports(paths)}
    out = []
    for c in GrowthBacklog(paths).load_all():
        if not c.report_id:
            continue
        if goal_backlog is not None and c.linked_goal_id:
            try:
                goal = goal_backlog.get(c.linked_goal_id)
            except Exception:
                goal = None
            if goal is not None and getattr(goal, "recurring", False):
                continue
        report = reports_by_id.get(c.report_id)
        if report is None:
            continue

        drift = None
        drift_trigger = False
        if drift_enabled:
            try:
                drift = external_signal_drift_for_report(paths, report, profile)
            except Exception:
                drift = None
            drift_trigger = drift is not None and drift.get("drift_count", 0) >= drift_min_changes

        if report.evidence_count_at_generation < 0:
            # [P5-1] 哨兵值：生成时的证据数快照缺失（反序列化自这个字段
            # 引入之前的旧数据），证据数这条信号不做"从 0 涨到现在"这种
            # 误判；但外部世界变化信号跟证据数快照无关，不受这个哨兵值
            # 影响，仍然可能单独触发。
            new_evidence = None
            evidence_trigger = False
        else:
            new_evidence = c.evidence_count - report.evidence_count_at_generation
            evidence_trigger = new_evidence >= min_new

        if not (evidence_trigger or drift_trigger):
            continue

        recent_delta = _recent_evidence_delta(
            paths, c.dedupe_key(), window_days=_REPORT_REFRESH_RECENT_BURST_WINDOW_DAYS
        )
        row = {
            "candidate_id": c.candidate_id,
            "title": c.title,
            "report_id": report.report_id,
            "evidence_count": c.evidence_count,
            "evidence_count_at_generation": report.evidence_count_at_generation,
            "new_evidence": new_evidence if new_evidence is not None else 0,
            "recent_evidence_delta": recent_delta,
        }
        if drift_enabled and drift_trigger:
            row["external_drift"] = drift
        out.append(row)
    return sorted(
        out,
        key=lambda row: (-(row["recent_evidence_delta"] or 0), -row["new_evidence"]),
    )


def refresh_growth_report(
    paths, candidate_id: str, *, llm_helper: Optional[Callable[[str], str]] = None,
    profile=None, cfg=None,
) -> Optional[GrowthReport]:
    """为一个候选重新生成一份调研报告（新 report_id/新文件），并把候选
    的 `report_id` 指向新报告——旧报告仍留在 `growth_reports_index.jsonl`
    历史记录里（不删除、不覆盖），只是不再是候选"当前挂着"的那份，
    `reports_needing_refresh()` 之后也不会再把它算作"待刷新"。

    `profile`/`cfg`：[N4] 透传给 `generate_growth_report()`，控制是否
    附带外部资讯背景（见该函数 docstring）；不传时行为与改动前完全
    一致。"""
    candidate = GrowthBacklog(paths).get(candidate_id)
    if candidate is None:
        return None
    return generate_growth_report(paths, candidate, llm_helper=llm_helper, profile=profile, cfg=cfg)


# ────────────────────────── P2：推送节流状态（growth_advisor_state.json） ──────────────────────────


# ────────── [growth_advisor_goal_cron_integration_plan.md] Goal/Cron 打通 ──────────


def _load_goal_backlog_safely(paths):
    """尽力构造一个 `GoalBacklog`，任何失败（比如 `paths` 不是完整的
    `AgentPaths`、goals.json 损坏）都静默返回 `None`——对齐分析/回访
    信号都被设计为"拿不到就退化，不报错"，这个辅助函数是唯一一处需要
    真正 import `perception.goal_backlog` 的地方。
    """
    try:
        from mini_agent.perception.goal_backlog import GoalBacklog
        return GoalBacklog(paths)
    except Exception:
        return None


def goal_growth_alignment(
    paths, profile, *, cfg=None, goal_backlog=None,
    min_confidence: float = 0.5, stalled_days: Optional[int] = None,
    llm_helper: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """[growth_advisor_goal_cron_integration_plan.md 阶段 A] 对齐分析：
    找出"有兴趣信号但没有对应 Goal"和"已经关联 Goal 但该 Goal 停滞"的
    两类方向。默认纯只读的规则式关键词匹配，不写任何状态，可随时安全
    调用。

    `cfg.goal_alignment_enabled=False` 时直接返回空结果（`enabled=False`），
    供 CLI/看板判断要不要展示这块功能。

    `llm_helper` + `cfg.goal_alignment_llm_enabled=True`（都满足才触发，
    同 `growth_signal_scan()` 的 `llm_helper` 约定）时，额外对"规则
    没匹配上的兴趣方向"和"规则没匹配上的 Goal"做一次语义匹配，结果放进
    返回值的 `llm_suggested_matches`（不是 `linked_goals`——这些是
    "建议你看看要不要关联"，不是关键词精确匹配那种确定关系，也不会自动
    写入任何持久化的 `linked_goal_id`）。
    """
    enabled = getattr(cfg, "goal_alignment_enabled", True) if cfg is not None else True
    if not enabled:
        return {
            "enabled": False, "unmatched_interests": [], "linked_goals": [],
            "llm_suggested_matches": [],
        }

    if stalled_days is None:
        stalled_days = getattr(cfg, "goal_alignment_stalled_days", 21) if cfg is not None else 21

    if goal_backlog is None:
        goal_backlog = _load_goal_backlog_safely(paths)

    goals: list = []
    if goal_backlog is not None:
        try:
            goals = [n for n in goal_backlog.all_nodes() if n.level == "goal"]
        except Exception:
            goals = []
    goal_keys_by_id = {g.id: normalize_title_key(g.title) for g in goals}
    goal_by_key: dict[str, Any] = {}
    for g in goals:
        goal_by_key.setdefault(goal_keys_by_id[g.id], g)

    # 候选兴趣来源一：本轮/上一轮信号扫描的 focus areas（证据数达标）。
    derived = dict(getattr(profile, "derived", {}) or {})
    focus_areas: dict[str, list[str]] = derived.get("growth_focus_areas") or {}
    min_evidence_count = getattr(cfg, "min_evidence_count", 3) if cfg is not None else 3
    interest_topics: dict[str, dict[str, Any]] = {}
    for topic, ids in focus_areas.items():
        if len(ids) < min_evidence_count:
            continue
        interest_topics[normalize_title_key(topic)] = {
            "topic": topic, "evidence_count": len(ids), "confidence": None,
        }

    # 候选兴趣来源二：已采纳的候选（不管是否达标于本轮扫描窗口，历史上
    # 曾经被采纳过就说明用户认可这个方向值得关注）。
    backlog = GrowthBacklog(paths)
    accepted_candidates = [c for c in backlog.load_all() if c.status == STATUS_ACCEPTED]
    for c in accepted_candidates:
        key = c.dedupe_key()
        entry = interest_topics.setdefault(key, {
            "topic": c.title, "evidence_count": c.evidence_count, "confidence": c.confidence,
        })
        if entry.get("confidence") is None:
            entry["confidence"] = c.confidence
        entry["candidate_id"] = c.candidate_id
        entry["linked_goal_id"] = c.linked_goal_id

    unmatched_interests = []
    linked_goals = []
    matched_goal_ids: set[str] = set()
    now = time.time()
    for key, info in interest_topics.items():
        conf = info.get("confidence")
        if conf is not None and conf < min_confidence:
            continue
        linked_goal_id = info.get("linked_goal_id")
        goal = goal_backlog.get(linked_goal_id) if (linked_goal_id and goal_backlog is not None) else None
        if goal is None:
            goal = goal_by_key.get(key)
        if goal is None:
            unmatched_interests.append({
                "topic": info["topic"],
                "evidence_count": info.get("evidence_count"),
                "confidence": conf,
                "candidate_id": info.get("candidate_id"),
            })
            continue
        matched_goal_ids.add(goal.id)
        stalled = goal.status == "active" and (goal.last_touched_at or 0) < now - stalled_days * 86400
        linked_goals.append({
            "topic": info["topic"],
            "goal_id": goal.id,
            "goal_title": goal.title,
            "goal_status": goal.status,
            "last_touched_at": goal.last_touched_at,
            "recurring": goal.recurring,
            "cycle_count": goal.cycle_count,
            "stalled": stalled,
        })

    llm_suggested_matches: list[dict[str, Any]] = []
    llm_enabled = getattr(cfg, "goal_alignment_llm_enabled", False) if cfg is not None else False
    if llm_enabled and llm_helper is not None and unmatched_interests:
        candidate_goals = [g for g in goals if g.id not in matched_goal_ids]
        status_out: dict[str, Any] = {"outcome": "error"}
        try:
            matches = _llm_match_interests_to_goals(
                unmatched_interests, candidate_goals, llm_helper, status_out=status_out
            )
            matched_topics = {m["topic"] for m in matches}
            unmatched_interests = [
                r for r in unmatched_interests if r["topic"] not in matched_topics
            ]
            llm_suggested_matches = matches
            _record_llm_call_status(
                paths, "goal_alignment_match", status_out.get("outcome", "success"),
                detail=f"matches={len(matches)}",
            )
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.goal_growth_alignment_llm_match")
            _record_llm_call_status(paths, "goal_alignment_match", "error", detail=str(exc)[:200])

    return {
        "enabled": True,
        "unmatched_interests": sorted(
            unmatched_interests, key=lambda r: -(r.get("evidence_count") or 0)
        ),
        "linked_goals": sorted(linked_goals, key=lambda r: (not r["stalled"], r["topic"])),
        "llm_suggested_matches": llm_suggested_matches,
    }


# 一次对齐分析 LLM 语义匹配，最多各送多少条"规则没匹配上的兴趣方向 /
# Goal"，避免 prompt 无限增长；同 `_LLM_AUGMENT_MAX_ENTRIES` 的克制原则。
_GOAL_ALIGNMENT_LLM_MAX_ITEMS = 20


def _llm_match_interests_to_goals(
    unmatched_interests: list[dict[str, Any]],
    candidate_goals: list,
    llm_helper: Callable[[str], str],
    *,
    status_out: Optional[dict] = None,
) -> list[dict[str, Any]]:
    """对"规则关键词匹配没匹配上"的兴趣方向和 Goal 各自取一批，让 LLM
    判断有没有语义上实际相关但字面不重合的配对（比如兴趣叫"数据分析
    能力"、Goal 叫"提升可视化技能"）。

    只有 LLM 输出的 `topic`/`goal_id` 都能在各自的候选池里对上号才会被
    采纳，防止幻觉匹配（编出不存在的 topic 或 goal_id）；任何解析失败
    都直接返回空列表而不是让异常向上传播——LLM 输出不可信，这里是纯粹
    的"能用就用，用不了就当没发生"。
    """
    if not unmatched_interests or not candidate_goals:
        if status_out is not None:
            status_out["outcome"] = "skipped_insufficient_unmatched"
        return []

    interests = unmatched_interests[:_GOAL_ALIGNMENT_LLM_MAX_ITEMS]
    goals = candidate_goals[:_GOAL_ALIGNMENT_LLM_MAX_ITEMS]
    valid_topics = {r["topic"] for r in interests}
    valid_goal_ids = {g.id for g in goals}

    topic_lines = "\n".join(f"- {r['topic']}" for r in interests)
    goal_lines = "\n".join(f"- goal_id={g.id}: {g.title}" for g in goals)
    prompt = (
        "下面是两份列表：一份是用户最近反复关注、但还没有对应目标的\n"
        "「兴趣方向」；另一份是用户已经建立的「目标」标题（可能措辞跟\n"
        "兴趣方向不完全一样，但实质是同一件事）。请找出确实是同一件事、\n"
        "只是字面表述不同的配对（不要牵强附会，宁可漏判也不要错配）。\n"
        "只输出 JSON 数组，不要有其他文字，每个元素形如：\n"
        '{"topic": "兴趣方向原文", "goal_id": "对应的 goal_id"}\n'
        "topic 必须原样从下面兴趣方向列表里选，goal_id 必须原样从目标\n"
        "列表里选，不要发明新的值。没有发现任何确定的配对时输出空数组 []。\n\n"
        f"兴趣方向：\n{topic_lines}\n\n目标：\n{goal_lines}"
    )

    raw = llm_helper(prompt)
    if not raw or not raw.strip():
        if status_out is not None:
            status_out["outcome"] = "empty_response"
        return []

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            if status_out is not None:
                status_out["outcome"] = "parse_error"
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            if status_out is not None:
                status_out["outcome"] = "parse_error"
            return []

    if not isinstance(parsed, list):
        if status_out is not None:
            status_out["outcome"] = "parse_error"
        return []

    goal_by_id = {g.id: g for g in goals}
    out: list[dict[str, Any]] = []
    seen_topics: set[str] = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        topic = item.get("topic")
        goal_id = item.get("goal_id")
        if topic not in valid_topics or goal_id not in valid_goal_ids or topic in seen_topics:
            continue
        goal = goal_by_id[goal_id]
        out.append({
            "topic": topic, "goal_id": goal.id, "goal_title": goal.title,
            "goal_status": goal.status, "matched_via": "llm",
        })
        seen_topics.add(topic)

    if status_out is not None:
        status_out["outcome"] = "success" if out else "no_new_topics"
    return out


# ────────── [growth_advisor_autonomy_deepening_plan_v2.md 方向 2] 对齐分析 LLM 建议一键确认 ──────────
# `goal_growth_alignment()` 的 `llm_suggested_matches` 此前只停留在
# "展示给你看"——要正式关联，用户只能走 `/growth adopt-goal`（新建一个
# Goal，跟建议的意思不一样）或手动改标题让关键词匹配上。这里补一个
# "确认这条建议、把兴趣方向关联到已存在的 Goal"的入口，是 A3（批量落地）
# 的自然延伸：A3 解决"没有 Goal，需要新建"，这里解决"已有语义相关的
# Goal，需要关联而不是新建"。


def confirm_llm_suggested_match(
    paths, topic: str, goal_id: str, *, goal_backlog=None,
) -> dict[str, Any]:
    """把一条 `llm_suggested_matches` 里的建议写成正式关联：找到 `topic`
    对应的候选记录，把它的 `linked_goal_id` 指向 `goal_id`（复用
    `GrowthBacklog.set_linked_goal()`，不新建 Goal，`goal_id` 必须是
    已经存在的 Goal）。

    `topic` 没有对应候选记录时（比如只是 focus_areas 里的一个兴趣信号，
    还没走到候选生成这一步）无法直接关联，返回
    `{"ok": False, "reason": ...}`，提示先走一轮 `/growth scan`。

    `goal_id` 在 `goal_backlog` 里找不到对应节点时同样拒绝——不校验
    `topic`/`goal_id` 是否真的出现在某一次 `llm_suggested_matches`
    里（调用方通常是刚从那份列表里选出来的一条，这里只保证两端都是
    真实存在的记录，避免关联到一个不存在的 Goal）。

    返回：
        {"ok": True, "candidate_id": ..., "goal_id": ..., "goal_title": ...}
        或 {"ok": False, "reason": "..."}
    """
    if not topic:
        return {"ok": False, "reason": "topic 不能为空。"}
    if not goal_id:
        return {"ok": False, "reason": "goal_id 不能为空。"}

    if goal_backlog is None:
        goal_backlog = _load_goal_backlog_safely(paths)
    goal = goal_backlog.get(goal_id) if goal_backlog is not None else None
    if goal is None:
        return {"ok": False, "reason": f"目标 {goal_id} 不存在（可能已被删除）。"}

    backlog = GrowthBacklog(paths)
    all_c = backlog.load_all()
    target_key = normalize_title_key(topic)
    cand = None
    for c in all_c:
        if c.dedupe_key() == target_key or c.title == topic:
            cand = c
            break
    if cand is None:
        return {
            "ok": False,
            "reason": f"「{topic}」没有对应的候选记录，无法直接关联（先走一轮 /growth scan 生成候选）。",
        }

    updated = backlog.set_linked_goal(cand.candidate_id, goal.id)
    if updated is None:
        return {"ok": False, "reason": "写入关联失败（候选记录可能已被并发修改）。"}
    return {
        "ok": True, "candidate_id": cand.candidate_id,
        "goal_id": goal.id, "goal_title": goal.title,
    }


# ────────── [growth_advisor_autonomy_deepening_plan.md 方向 A3] 对齐分析结果批量落地 ──────────
# `/growth align` 之前只是展示"有兴趣信号但没建目标"的列表，落地还是要
# 对每一条分别调用 accept + auto_pursue_candidate()。这里补一个批量入口，
# 复用已有的 `auto_pursue_candidate()`，单次处理条数受
# `cfg.goal_alignment_adopt_all_max_batch` 节流，避免一次性触发过多
# LLM 调用（生成报告 + 生成执行规范）。


def batch_adopt_unmatched_interests(
    paths, cfg, profile, *,
    goal_backlog=None, cron_scheduler=None, llm_helper: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """[方向 A3] 对 `goal_growth_alignment()` 找出的"有兴趣信号但没建
    目标"列表做批量落地：对其中已经有对应候选记录（`candidate_id` 非
    空）的条目，依次调用 `auto_pursue_candidate()`（复用"采纳即启动"
    整条链路：生成报告 → 落地成 Goal → 生成并确认执行规范 → 绑定周期
    性）。没有 `candidate_id` 的条目（比如只是 focus_areas 里的一个
    兴趣信号，还没走到候选生成这一步）无法直接采纳，原样跳过、计入
    `skipped`，不会因此报错。

    单次最多处理 `cfg.goal_alignment_adopt_all_max_batch`（默认 3）条，
    按 `evidence_count` 从高到低排序取前 N 个——避免用户手滑触发一次
    意外的成本爆炸；未被处理到的条目下次调用仍然会出现在
    `goal_growth_alignment()` 的结果里，不会丢失，用户可以多次调用
    直到列表清空。

    任一条目内部失败都不影响其余条目继续处理（`auto_pursue_candidate()`
    本身已经是"任一步骤失败不影响已完成部分"的容错设计），失败信息
    记在对应条目的 `errors` 里。

    [growth_advisor_autonomy_deepening_plan_v2.md 方向 4 方案一] 每次
    调用都会重新跑一遍 `goal_growth_alignment()`，如果两次调用之间有
    新的信号扫描发生，`unmatched_interests` 的 `evidence_count` 排序
    可能变化，导致"还剩几条"这个数字对应的具体条目不稳定。为了让用户
    能看清楚具体是哪些方向还没处理（而不是一个可能对不上的数字），
    额外返回 `remaining_topics`：本次调用结束时仍待处理的 topic 列表
    （按本次返回时的 `evidence_count` 降序），只是让"未处理的有哪些"
    对用户可见，不改变实际处理顺序——顺序仍由下一次调用时的最新排序
    决定。

    返回：
        {"processed": [{"topic", "candidate_id", "goal_id", "errors"}, ...],
         "skipped": [{"topic", "reason"}, ...],
         "remaining_count": int,  # 本次未处理到的、仍待落地的条数
         "remaining_topics": list[str]}  # 上述条目对应的 topic 名称列表
    """
    if goal_backlog is None:
        goal_backlog = _load_goal_backlog_safely(paths)

    alignment = goal_growth_alignment(
        paths, profile, cfg=cfg, goal_backlog=goal_backlog, llm_helper=llm_helper,
    )
    unmatched = alignment.get("unmatched_interests", []) if alignment.get("enabled", True) else []

    max_batch = getattr(cfg, "goal_alignment_adopt_all_max_batch", 3) if cfg is not None else 3
    if max_batch is None or max_batch < 0:
        max_batch = 3

    eligible = [r for r in unmatched if r.get("candidate_id")]
    ineligible = [r for r in unmatched if not r.get("candidate_id")]
    # [方向 A3] 已排好序（goal_growth_alignment 按 evidence_count 降序），
    # 直接按顺序取前 N 个即可，不需要重新排序。
    to_process = eligible[:max_batch]
    remaining = eligible[max_batch:]

    backlog = GrowthBacklog(paths)
    processed = []
    for row in to_process:
        cid = row["candidate_id"]
        entry: dict[str, Any] = {"topic": row["topic"], "candidate_id": cid, "goal_id": None, "errors": []}
        try:
            cand = backlog.get(cid)
            if cand is None:
                entry["errors"].append("候选记录已不存在（可能已被删除），已跳过。")
                processed.append(entry)
                continue
            if cand.status != STATUS_ACCEPTED:
                cand = backlog.set_status(cid, STATUS_ACCEPTED)
                GrowthFeedbackLedger(paths).record(cid, STATUS_ACCEPTED, reason=None)
            pursuit = auto_pursue_candidate(
                paths, cand, goal_backlog=goal_backlog, cron_scheduler=cron_scheduler,
                cfg=cfg, llm_helper=llm_helper, profile=profile,
            )
            entry["goal_id"] = pursuit["goal"].id if pursuit.get("goal") else None
            entry["errors"] = pursuit.get("errors", [])
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.growth_advisor.batch_adopt_unmatched_interests")
            entry["errors"].append(f"批量落地失败：{e}")
        processed.append(entry)

    skipped = [{"topic": r["topic"], "reason": "没有对应的候选记录，无法直接落地（先走一轮 /growth scan 生成候选）"} for r in ineligible]
    return {
        "processed": processed,
        "skipped": skipped,
        "remaining_count": len(remaining),
        "remaining_topics": [r["topic"] for r in remaining],
    }


def adopt_candidate_as_goal(
    paths, candidate: GrowthCandidate, *, goal_backlog=None, extra_tags: Optional[list[str]] = None,
) -> Any:
    """[growth_advisor_goal_cron_integration_plan.md 阶段 B] 把一个候选
    "落地"成 GoalBacklog 里的一个 Goal 节点：

    - 要求候选已经有调研报告（`report_id` 非空），没有报告时抛
      `ValueError`——调用方（CLI/API）负责提示用户先 `/growth report
      <id>` 生成一份，体验上比\"建了个没有实质内容的 Goal\"更好。
    - Goal 的 `description` 用报告摘要 + 报告路径引用（不整篇塞报告
      正文——`description` 是给后续任务执行读的上下文，太长反而稀释
      重点，完整内容用户/Agent 需要时可以按路径读）。
    - 候选反向记 `linked_goal_id`；如果候选此前还是 `pending`，顺带
      流转成 `accepted`（\"建了 Goal 去推进\"本身就是一种采纳）。

    返回创建的 `GoalNode`。
    """
    if not candidate.report_id:
        raise ValueError(
            f"候选 {candidate.candidate_id} 还没有调研报告，请先执行 "
            "`/growth report <candidate_id>` 生成报告后再落地成目标。"
        )

    if goal_backlog is None:
        goal_backlog = _load_goal_backlog_safely(paths)
    if goal_backlog is None:
        raise RuntimeError("无法访问 GoalBacklog（项目路径不可用），无法创建目标。")

    report = get_report_by_id(paths, candidate.report_id)
    summary = report.summary if report is not None else candidate.rationale
    body_path = report.body_path if report is not None else ""
    description_parts = [candidate.rationale]
    if summary and summary != candidate.rationale:
        description_parts.append(summary)
    description_parts.append(f"来源：成长顾问候选 {candidate.candidate_id}")
    if body_path:
        description_parts.append(f"完整调研报告：{body_path}")
    description = "\n\n".join(p for p in description_parts if p)

    tags = ["growth_advisor"]
    if extra_tags:
        tags.extend(extra_tags)

    goal = goal_backlog.add_goal(
        title=candidate.title,
        description=description,
        source="user",
        tags=tags,
    )

    backlog = GrowthBacklog(paths)
    backlog.set_linked_goal(candidate.candidate_id, goal.id)
    if candidate.status == STATUS_PENDING:
        backlog.set_status(candidate.candidate_id, STATUS_ACCEPTED)
        GrowthFeedbackLedger(paths).record(candidate.candidate_id, STATUS_ACCEPTED, reason=None)

    return goal


# ────────────────── 采纳即启动：自主持续调研 ──────────────────
# [与用户讨论后的方向性改动] 此前"采纳"只是一个反馈信号，"落地成 Goal"、
# "生成执行规范"、"绑定周期性"三步都要用户各自手动点一次；成长顾问的
# 定位应该是"自主规划方向、自主持续收集素材"，而不是"每一步都要人工
# 衔接的流水线"。这里把四步收敛成一个函数，`cfg.auto_pursue_on_accept`
# 默认开启时由"采纳"这个动作本身直接触发，用户不需要再逐步点。

def auto_pursue_candidate(
    paths,
    candidate: GrowthCandidate,
    *,
    goal_backlog=None,
    cron_scheduler=None,
    cfg: Optional["GrowthAdvisorConfig"] = None,
    llm_helper: Optional[Callable[[str], str]] = None,
    profile=None,
) -> dict[str, Any]:
    """把一个候选从"已采纳"一路自动推进到"持续、周期性地被推进"：

    1. 候选没有调研报告 → 先用零成本规则模板生成一份（不强依赖 LLM，
       保证这条自动化路径在没有 `llm_helper` 时也能跑通）。
    2. `adopt_candidate_as_goal()` 落地成 Goal（若候选此前已经落地过，
       直接复用已有的 `linked_goal_id`，不会重复建 Goal）。
    3. 用 `cfg.auto_pursue_template_id`（默认 `growth_pursuit`，专门
       为"持续深化、不原地打转"设计）生成一版执行规范草稿并**直接
       确认**——这是"自动衔接"的核心：用户不需要再手动跑一遍模板选择
       /反馈迭代循环。草稿生成失败（比如 LLM 不可用）不会中断整个
       流程，只是这个 Goal 暂时没有执行规范（等价于沿用通用行为），
       后续用户/Agent 仍可以手动补一份。
    4. 绑定周期性（`cfg.auto_pursue_schedule`，默认每天一次）。已经
       绑定过的话 `make_goal_recurring()` 会复用旧 job，不会重复创建。

    任一后续步骤失败都不影响前面已经完成的部分（Goal 建立成功就是
    成功），失败信息记录在返回 dict 的 `errors` 里，供调用方（API 层）
    以尽力而为的方式呈现给用户，而不是让整个"采纳"动作因为执行规范
    生成失败就跟着 500。

    返回：
        {
            "goal": GoalNode | None,
            "spec": GoalExecutionSpec | None,
            "cron_job": CronJob | None,
            "report_generated": bool,
            "errors": list[str],
        }
    """
    if cfg is None:
        from mini_agent.config.models import GrowthAdvisorConfig
        cfg = GrowthAdvisorConfig()

    result: dict[str, Any] = {
        "goal": None, "spec": None, "cron_job": None,
        "report_generated": False, "errors": [],
    }

    if goal_backlog is None:
        goal_backlog = _load_goal_backlog_safely(paths)
    if goal_backlog is None:
        result["errors"].append("无法访问 GoalBacklog（项目路径不可用），已跳过自动持续调研。")
        return result

    # 1. 报告
    if not candidate.report_id:
        try:
            report = generate_growth_report(
                paths, candidate, llm_helper=llm_helper, profile=profile, cfg=None,
            )
            GrowthBacklog(paths).attach_report(candidate.candidate_id, report.report_id)
            candidate.report_id = report.report_id
            result["report_generated"] = True
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.growth_advisor.auto_pursue_candidate.report")
            result["errors"].append(f"生成调研报告失败：{e}")
            return result

    # 2. 落地成 Goal（若已落地过，复用）
    goal = None
    if candidate.linked_goal_id:
        try:
            goal_backlog.load()
            goal = goal_backlog.get(candidate.linked_goal_id)
        except Exception:
            goal = None
    if goal is None:
        try:
            goal = adopt_candidate_as_goal(paths, candidate, goal_backlog=goal_backlog)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.growth_advisor.auto_pursue_candidate.adopt")
            result["errors"].append(f"落地为 Goal 失败：{e}")
            return result
    result["goal"] = goal

    # 2.5 [方向 6] 调研风格分类——只在 Goal 尚未分类过时判定一次（风格是
    # 持续属性，不需要每次自动推进都重算），失败静默跳过、不影响主流程。
    if not getattr(goal, "growth_pursuit_style", None):
        try:
            style = determine_pursuit_style(
                candidate.title, extra_text=candidate.rationale,
                cfg=cfg, llm_helper=llm_helper, paths=paths,
            )
            goal_backlog.update_fields(goal.id, growth_pursuit_style=style)
            goal.growth_pursuit_style = style
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.growth_advisor.auto_pursue_candidate.style")

    # 3. 执行规范草稿 + 直接确认
    try:
        from mini_agent.perception import goal_execution_spec as ges
        from mini_agent.config import load_config

        builder_cfg = load_config()
        builder = ges.GoalExecutionSpecBuilder(builder_cfg)
        spec = builder.build_draft(
            goal.id, goal.title, goal.description,
            schedule=cfg.auto_pursue_schedule,
            template_id=cfg.auto_pursue_template_id,
        )
        ges.GoalExecutionSpecBuilder.confirm(spec)
        ges.save_spec(paths, goal.id, spec)
        try:
            goal_backlog.update_fields(goal.id, execution_spec_confirmed=True)
        except Exception:
            pass
        result["spec"] = spec
        if getattr(spec, "generation_error", None):
            result["errors"].append(f"执行规范草稿生成时出现问题（已按空白草稿确认，可后续手动补充）：{spec.generation_error}")
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where="mini_agent.evolution.growth_advisor.auto_pursue_candidate.spec")
        result["errors"].append(f"生成/确认执行规范失败，该 Goal 暂时沿用通用行为：{e}")

    # 4. 绑定周期性
    if cron_scheduler is not None:
        try:
            from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
            job = make_goal_recurring(
                goal_backlog, cron_scheduler, goal.id, cfg.auto_pursue_schedule,
            )
            result["cron_job"] = job
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.growth_advisor.auto_pursue_candidate.recur")
            result["errors"].append(f"绑定周期性执行失败：{e}")
    else:
        result["errors"].append("当前上下文拿不到 CronScheduler（非 daemon 模式），已跳过绑定周期性；Goal 已创建，可稍后在「🎯 目标」tab 手动设为周期性。")

    return result


def _today_str() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def _load_growth_state(paths) -> dict:
    p = paths.growth_state_path
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_growth_state(paths, state: dict) -> None:
    p = paths.growth_state_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ──────────── [growth_advisor_autonomy_deepening_plan.md 方向 B1/B2] ────────────
# 增量质量自动校验 + 饱和度信号：`growth_pursuit` 模板的 per_cycle_criteria
# 目前全部是 manual_review——没有自动化手段发现"一个方向已经连续好几轮
# 都在重复讲差不多的内容"。这里补一层零成本的规则式初筛：比对相邻两轮
# handoff 里的 `covered_subtopics` 差集，重叠比例过高就标记"疑似低增量"；
# 连续多轮都被标记，则判定这个方向"疑似饱和"，供上层（reap_finished_
# cycles / 看板）据此提示用户"要不要降频/告一段落"，而不是让周期性
# 执行无限期按固定节奏空转下去——纯诊断信号，不自动拦截产出、不自动
# 停止周期性执行，最终"要不要慢下来"仍然由用户决定（对齐 1.5 节"自主
# 不等于替用户做主"）。

_PURSUIT_SATURATION_DEFAULT_THRESHOLD = 3
_PURSUIT_INCREMENT_OVERLAP_THRESHOLD = 0.6


_LLM_REVIEWED_DEFAULTS = {"llm_reviewed": False, "llm_verdict": None, "llm_reason": ""}


def evaluate_cycle_increment(
    paths, goal_id: str, *,
    overlap_threshold: float = _PURSUIT_INCREMENT_OVERLAP_THRESHOLD,
    llm_helper: Optional[Callable[[str], str]] = None,
    llm_review_enabled: bool = False,
) -> dict:
    """比较某个 Goal 最近两轮 manifest 的 handoff 数据，判断本轮相比上一轮
    是否"疑似低增量"。纯规则式（`covered_subtopics` 集合差集占比），零
    LLM 成本，不判"失败"、不阻断任何流程——只读，供调用方自行决定要不要
    据此提示用户或计入 `record_pursuit_cycle_signal()` 的饱和度计数。

    [growth_advisor_autonomy_deepening_plan_v2.md 方向 1] `llm_helper` +
    `llm_review_enabled=True`（都满足才触发，同项目里其余 LLM 增强调用点
    一致的 opt-in 约定）时，只在规则式初筛已经判定 `low_increment=True`
    的轮次上，追加一次 LLM 语义复核——规则式初筛只能发现"字面上没什么
    新词"，发现不了"子话题标签凑巧重复、但其实内容已经往前推进了"这种
    更隐蔽的误判。复核结果单独放进 `llm_reviewed`/`llm_verdict`/
    `llm_reason` 三个字段，**不覆盖** `low_increment` 本身——两种信号都
    应该在诊断面板/看板里可见，调用方是否要据此调整展示（比如提示"规则
    判定低增量，但 LLM 认为其实有实质推进"）自行决定；`record_pursuit_
    cycle_signal()` 的 streak 计数仍然只看规则式 `low_increment`，不因为
    这一步而改变既有的计数口径。LLM 调用失败/未开启/规则未判定为低增量
    时，三个字段保持默认值（`llm_reviewed=False`）。

    返回：
        {"evaluated": bool,       # 是否成功做出了判断（轮次不足/没有
                                   # handoff 数据时为 False，此时
                                   # low_increment 恒为 False，不误判）
         "low_increment": bool,
         "overlap_ratio": float | None,       # 仅 evaluated=True 时有值
         "new_subtopics_count": int | None,
         "covered_subtopics_count": int | None,
         "reason": str,           # evaluated=False 时说明原因
         "llm_reviewed": bool,    # 是否实际触发了 LLM 复核
         "llm_verdict": bool | None,  # True=LLM 同意确实低增量；
                                       # False=LLM 认为其实有实质推进
         "llm_reason": str}       # LLM 复核给出的简短理由
    """
    from mini_agent.evolution import output_workspace
    from mini_agent.perception.goal_execution_spec import get_handoff_data

    base_dir = output_workspace.goal_output_base_dir(paths, goal_id)
    manifests = output_workspace.read_all_manifests(base_dir)
    if len(manifests) < 2:
        return {
            "evaluated": False, "low_increment": False,
            "overlap_ratio": None, "new_subtopics_count": None,
            "covered_subtopics_count": None,
            "reason": "轮次不足（少于 2 轮），暂无法比较增量",
            **_LLM_REVIEWED_DEFAULTS,
        }

    prev_handoff = get_handoff_data(manifests[-2].get("progress_note") or "") or {}
    curr_handoff = get_handoff_data(manifests[-1].get("progress_note") or "") or {}

    def _as_set(handoff: dict, key: str) -> set:
        v = handoff.get(key)
        if isinstance(v, list):
            return {str(x).strip() for x in v if str(x).strip()}
        return set()

    prev_subtopics = _as_set(prev_handoff, "covered_subtopics")
    curr_subtopics = _as_set(curr_handoff, "covered_subtopics")
    if not curr_subtopics:
        return {
            "evaluated": False, "low_increment": False,
            "overlap_ratio": None, "new_subtopics_count": None,
            "covered_subtopics_count": None,
            "reason": "本轮 handoff 未提供 covered_subtopics，跳过判断（可能是该 Goal 没有用 growth_pursuit 模板，或执行时没有按约定写 handoff 块）",
            **_LLM_REVIEWED_DEFAULTS,
        }

    new_subtopics = curr_subtopics - prev_subtopics
    overlap_ratio = 1.0 - (len(new_subtopics) / len(curr_subtopics))
    low_increment = overlap_ratio >= overlap_threshold
    result = {
        "evaluated": True,
        "low_increment": low_increment,
        "overlap_ratio": round(overlap_ratio, 3),
        "new_subtopics_count": len(new_subtopics),
        "covered_subtopics_count": len(curr_subtopics),
        "reason": "",
        **_LLM_REVIEWED_DEFAULTS,
    }

    if low_increment and llm_review_enabled and llm_helper is not None:
        status_out: dict[str, Any] = {"outcome": "error"}
        try:
            verdict, reason = _llm_review_cycle_increment(
                prev_subtopics, curr_subtopics, new_subtopics, llm_helper, status_out=status_out,
            )
            result["llm_reviewed"] = True
            result["llm_verdict"] = verdict
            result["llm_reason"] = reason
            _record_llm_call_status(
                paths, "pursuit_increment_review", status_out.get("outcome", "success"),
                detail=reason[:200],
            )
        except Exception as exc:
            from mini_agent.errors import log_exception
            log_exception(exc, where="mini_agent.growth_advisor.evaluate_cycle_increment_llm_review")
            _record_llm_call_status(paths, "pursuit_increment_review", "error", detail=str(exc)[:200])

    return result


def _llm_review_cycle_increment(
    prev_subtopics: set, curr_subtopics: set, new_subtopics: set,
    llm_helper: Callable[[str], str], *, status_out: Optional[dict] = None,
) -> tuple[bool, str]:
    """[方向 1] 对一个已经被规则式初筛标记为"疑似低增量"的轮次，追一次
    语义复核——只传子话题的标题集合（不传完整正文，控制 prompt 体积），
    让 LLM 判断"这些标题上的重叠，是不是意味着内容真的在原地打转"。

    返回 `(verdict, reason)`：`verdict=True` 表示 LLM 同意规则式判断
    （确实是低增量），`False` 表示 LLM 认为其实有实质推进（规则判断
    可能是误判，比如新增子话题的标题恰好重复用词，但讨论的具体内容
    不同）。解析失败/空响应时抛出异常，由调用方统一走失败路径（不在这
    里返回"默认同意规则判断"这种隐性兜底，避免掩盖复核本身失效的情况）。
    """
    prompt = (
        "你在协助复核一个持续调研方向的\"本轮是否有实质推进\"判断。\n"
        "规则式初筛已经判定本轮\"疑似低增量\"（新增子话题标题占比过低）。\n"
        "请你只根据子话题标题本身的语义，判断这次真的是在重复讲同样的\n"
        "内容，还是新增的标题虽然用词与已有标题接近，但实际讨论的是\n"
        "不同的具体内容（比如\"性能优化\" vs \"启动性能优化\"这种从大类\n"
        "细化到具体子问题的情况，不应算作低增量）。\n\n"
        f"上一轮已覆盖的子话题：{sorted(prev_subtopics) or '（无）'}\n"
        f"本轮全部子话题：{sorted(curr_subtopics) or '（无）'}\n"
        f"本轮新增子话题：{sorted(new_subtopics) or '（无）'}\n\n"
        "只输出一个 JSON 对象，不要输出其它任何文字，格式：\n"
        '{"has_real_progress": true/false, "reason": "一句话理由（30 字以内）"}\n'
        "has_real_progress=true 表示你认为本轮其实有实质推进（不同意\n"
        "规则式\"低增量\"的判断）；false 表示你同意确实是低增量。"
    )
    raw = llm_helper.ask(prompt)
    if not raw or not raw.strip():
        if status_out is not None:
            status_out["outcome"] = "empty_response"
        raise ValueError("LLM 复核返回空响应")

    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except Exception as exc:
        if status_out is not None:
            status_out["outcome"] = "parse_error"
        raise ValueError(f"LLM 复核响应解析失败：{exc}") from exc

    if not isinstance(parsed, dict) or "has_real_progress" not in parsed:
        if status_out is not None:
            status_out["outcome"] = "parse_error"
        raise ValueError("LLM 复核响应缺少 has_real_progress 字段")

    has_real_progress = bool(parsed.get("has_real_progress"))
    reason = str(parsed.get("reason") or "").strip()
    if status_out is not None:
        status_out["outcome"] = "success"
    # verdict 与 has_real_progress 相反：verdict=True 表示"同意规则判断
    # （确实低增量）"，has_real_progress=True 表示"LLM 认为有实质推进"。
    return (not has_real_progress), reason


def record_pursuit_cycle_signal(
    paths, goal_id: str, low_increment: bool, *,
    saturation_threshold: int = _PURSUIT_SATURATION_DEFAULT_THRESHOLD,
    llm_reviewed: bool = False, llm_verdict: Optional[bool] = None, llm_reason: str = "",
) -> dict:
    """维护某个 Goal 的"连续低增量轮次"计数（存进 growth_state.json 的
    `pursuit_saturation` 子字典，按 goal_id 分桶，不为这个单一信号新开
    一份持久化文件——对齐"复用已有存储位置"的既有取舍）。

    - `low_increment=True` → streak +1；`False` → streak 归零、
      `notified` 归位（一旦这轮不再是低增量，视为"已经缓过来"，之前的
      饱和提示状态失效，下次再连续低增量会重新触发一次新的提示）。
    - streak 达到 `saturation_threshold`（默认 3）判定为"疑似饱和"。
    - `notified` 用于避免同一次饱和状态被重复提示——`newly_saturated`
      只在"刚刚跨过阈值、之前还没提示过"时为 `True`，调用方（比如
      `goal_cron_bridge.reap_finished_cycles()`）据此决定是否要推一次
      "要不要降频"的通知，而不是每轮都重复打扰用户。

    [growth_advisor_autonomy_deepening_plan_v2.md 方向 1] `llm_reviewed`/
    `llm_verdict`/`llm_reason` 是 `evaluate_cycle_increment()` 的 LLM
    复核结果（未开启该开关或未触发复核时保持默认值），这里只是原样
    存一份"最近一次"快照（`get_pursuit_saturation()` 据此展示）并追加
    进趋势记录，不参与 streak 的加减逻辑——streak 仍然只看
    `low_increment` 这一个规则式信号，避免"规则说低增量、LLM 说不是"
    被静默合并成一个结论。

    [growth_advisor_autonomy_deepening_plan_v2.md 方向 3] 除了更新
    `pursuit_saturation` 当前状态这个快照，顺带向
    `growth_pursuit_saturation_trend.jsonl` 追加一条历史记录，供
    `get_pursuit_saturation_trend()` 读取——只读的当前状态回答不了
    "降频之后有没有缓过来""是不是一直饱和"这类需要看走势的问题，这里的
    追加写入是诊断性质，失败不影响 streak 计数本身（`_load_growth_
    state()`/`_save_growth_state()` 已经在最外层完成，这里追加失败只是
    少一条趋势记录，不影响函数返回值）。

    返回：{"streak": int, "saturated": bool, "newly_saturated": bool}
    """
    state = _load_growth_state(paths)
    sat = state.setdefault("pursuit_saturation", {})
    entry = dict(sat.get(goal_id) or {"streak": 0, "notified": False})
    if low_increment:
        entry["streak"] = int(entry.get("streak", 0)) + 1
    else:
        entry["streak"] = 0
        entry["notified"] = False
    saturated = entry["streak"] >= saturation_threshold
    newly_saturated = saturated and not entry.get("notified", False)
    if newly_saturated:
        entry["notified"] = True
    entry["llm_reviewed"] = bool(llm_reviewed)
    entry["llm_verdict"] = llm_verdict
    entry["llm_reason"] = llm_reason or ""
    sat[goal_id] = entry
    _save_growth_state(paths, state)
    try:
        _append_jsonl(paths.growth_pursuit_saturation_trend_path, {
            "goal_id": goal_id,
            "recorded_at": time.time(),
            "low_increment": bool(low_increment),
            "streak": entry["streak"],
            "saturated": saturated,
            "llm_reviewed": bool(llm_reviewed),
            "llm_verdict": llm_verdict,
            "llm_reason": llm_reason or "",
        })
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor.record_pursuit_cycle_signal_trend")
    return {"streak": entry["streak"], "saturated": saturated, "newly_saturated": newly_saturated}


def get_pursuit_saturation(paths, goal_id: str) -> dict:
    """只读查询某个 Goal 当前的饱和度状态，供看板/API 展示，不产生任何
    写入。没有记录过时返回 streak=0（尚未判定过饱和）。"""
    state = _load_growth_state(paths)
    entry = (state.get("pursuit_saturation") or {}).get(goal_id) or {}
    threshold = _PURSUIT_SATURATION_DEFAULT_THRESHOLD
    streak = int(entry.get("streak", 0))
    return {
        "streak": streak,
        "saturated": streak >= threshold,
        "threshold": threshold,
        # [方向 1] 最近一轮 LLM 复核结果快照（未开启/未触发复核时保持
        # 默认值），只读展示，不参与 saturated 的判断。
        "llm_reviewed": bool(entry.get("llm_reviewed", False)),
        "llm_verdict": entry.get("llm_verdict"),
        "llm_reason": entry.get("llm_reason") or "",
    }


# ──────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 1] ────────────
# 素材参与度信号：`pursuit_saturation` 衡量的是"素材本身有没有新内容"，
# 完全不衡量"用户吸收了多少"。这里补一个最小化的埋点——看板"📄 素材"
# 按钮被点击时，记一次"当前轮次"快照，跟 `get_pursuit_saturation()` 一样
# 存进 `growth_state.json`（新增 `pursuit_material_views` 子字典，跟
# `pursuit_saturation` 平行，不新开文件），只存"最近一次查看时的轮次"，
# 不记录停留时长——看板技术上拿不到，也没必要为这一个信号引入额外的
# 前端埋点体系（对齐方案文档第 1 节的取舍）。


def record_pursuit_material_view(paths, goal_id: str, cycle_count: int) -> dict:
    """记录一次"用户点开了这个方向的素材"事件——只存"最近一次查看时的
    轮次"，覆盖式写入（不追加历史，历史走势对这个信号价值不大，读一次
    "最近一次是第几轮"就足够支撑第 4 节的"长期无人查看"判断）。

    失败尽力而为：调用方（看板按钮点击处理）不应该因为这次埋点写入
    失败就影响"打开素材"这个主操作本身，因此这里内部不抛出磁盘 IO
    之外的异常（`_load_growth_state`/`_save_growth_state` 本身已经是
    尽力而为的读写）。

    返回：{"goal_id", "last_viewed_cycle", "viewed_at"}
    """
    state = _load_growth_state(paths)
    views = state.setdefault("pursuit_material_views", {})
    now = time.time()
    views[goal_id] = {"last_viewed_cycle": int(cycle_count), "viewed_at": now}
    _save_growth_state(paths, state)
    return {"goal_id": goal_id, "last_viewed_cycle": int(cycle_count), "viewed_at": now}


def get_pursuit_material_engagement(paths, goal_id: str, current_cycle: int) -> dict:
    """只读查询某个 Goal 的素材参与度：素材已经比用户上次查看时新了
    几轮。从未查看过时 `last_viewed_cycle` 为 `None`，
    `cycles_since_last_view` 直接等于 `current_cycle`（相当于"从头到
    现在都没看过"）。不产生任何写入。

    返回：{"last_viewed_cycle": int | None, "current_cycle": int,
    "cycles_since_last_view": int}
    """
    state = _load_growth_state(paths)
    entry = (state.get("pursuit_material_views") or {}).get(goal_id) or {}
    last_viewed_cycle = entry.get("last_viewed_cycle")
    current_cycle = int(current_cycle or 0)
    if last_viewed_cycle is None:
        cycles_since_last_view = current_cycle
    else:
        last_viewed_cycle = int(last_viewed_cycle)
        cycles_since_last_view = max(0, current_cycle - last_viewed_cycle)
    return {
        "last_viewed_cycle": last_viewed_cycle,
        "current_cycle": current_cycle,
        "cycles_since_last_view": cycles_since_last_view,
    }


_PURSUIT_LONG_UNVIEWED_DEFAULT_THRESHOLD = 5


def pursuits_portfolio_summary(
    paths, goal_backlog, *, long_unviewed_threshold: int = _PURSUIT_LONG_UNVIEWED_DEFAULT_THRESHOLD,
) -> dict:
    """[growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 4]
    对"🔄 正在自主推进"分区里全部方向做一次轻量聚合，回答"我现在该
    先看哪几个方向"这个全局问题——纯粹是把已经分散展示的饱和度信号
    （方向 B2）和参与度信号（方向 1）组织成一句摘要，不引入任何新的
    判断维度、不产生新的持久化，也不做排序/推荐算法，只是简单的分类
    计数 + 列出具体是哪几个方向落在"建议关注"这一类。

    分类规则（同一个方向可能同时落入两类，去重后只算一次"建议关注"）：
    - 饱和未处理：`get_pursuit_saturation()` 判定 `saturated=True`；
    - 长期无人查看：`cycles_since_last_view >= long_unviewed_
      threshold`（且素材确实已经有内容，即 `cycles_since_last_view
      > 0`，避免刚创建、还没来得及有第一轮增量的方向被误判）；
    - 其余归为"正常推进"。

    只处理打了 `growth_advisor` 标签且 `recurring=True` 的 Goal——跟
    `/growth/pursuits` 的"🔄 正在自主推进"口径完全一致，暂停的方向不
    参与统计（用户已经主动暂停，不需要系统再提示"建议关注"）。

    返回：{"total": int, "saturated_count": int, "long_unviewed_count":
    int, "attention_needed": [{"goal_id", "title", "reasons": [...]}],
    "normal_count": int}
    """
    backlog = GrowthBacklog(paths)
    attention: list[dict] = []
    saturated_count = 0
    long_unviewed_count = 0
    total = 0
    for c in backlog.load_all():
        if not c.linked_goal_id:
            continue
        goal = goal_backlog.get(c.linked_goal_id)
        if goal is None or not goal.recurring:
            continue
        total += 1
        reasons = []
        saturation = get_pursuit_saturation(paths, goal.id)
        if saturation.get("saturated"):
            reasons.append("saturated")
            saturated_count += 1
        engagement = get_pursuit_material_engagement(paths, goal.id, goal.cycle_count)
        cycles_since = engagement.get("cycles_since_last_view") or 0
        if cycles_since >= long_unviewed_threshold and cycles_since > 0:
            reasons.append("long_unviewed")
            long_unviewed_count += 1
        if reasons:
            attention.append({"goal_id": goal.id, "title": c.title, "reasons": reasons})
    return {
        "total": total,
        "saturated_count": saturated_count,
        "long_unviewed_count": long_unviewed_count,
        "attention_needed": attention,
        "normal_count": total - len(attention),
    }


# ────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 规划维度候选] 调研路径关联信号 ──────────
# 现状：多个方向并行推进时，`pursuits_portfolio_summary()` 只回答"该先
# 看哪几个"，不回答"这几个方向之间是不是有关联"。这里补一个纯规则式
# 的共现信号：如果方向 A 最近几轮实际产出的内容里，反复出现方向 B 的
# 关键词，就提示"这两个方向内容上有关联，值得互相参考"——刻意不判断
# "谁是谁的前置知识"这种更强的因果结论（共现不等于依赖顺序，规则式
# 关键词匹配也做不到这种语义判断），只做"值得关注"级别的弱提示，跟
# 方案文档"规划维度"里"资源分配/决策权交还用户"的一贯克制一致。

_PURSUIT_RELATED_MIN_SHARED_KEYWORDS = 2
_PURSUIT_RELATED_LOOKBACK_CYCLES = 5


def related_pursuit_directions(paths, goal_backlog, profile=None) -> list[dict]:
    """[规划维度候选] 对"🔄 正在自主推进"分区里全部方向两两之间做一次
    关键词共现扫描：方向 A 最近几轮实际产出的内容（`covered_subtopics`
    累积文本，复用方向 6 动态修正已有的 `_recent_covered_subtopics_
    text()`）里，如果命中方向 B 的关键词（`_effective_topic_
    keywords()` 登记的那一份）达到 `_PURSUIT_RELATED_MIN_SHARED_
    KEYWORDS`（默认 2）个，就记一条"A 的内容提到了 B"的关联信号。

    只处理打了 `growth_advisor` 标签且 `recurring=True` 的 Goal，口径
    跟 `pursuits_portfolio_summary()` 一致。`profile` 缺失、某个方向
    没有登记关键词、或没有可用的产出内容时，该方向自然不参与匹配，
    不报错、不影响其它方向的计算。方向是有意义的（A 的内容提到 B，
    不代表 B 的内容也提到 A），因此两个方向互相提到时会各出现一条
    独立记录，不做去重合并。

    返回：[{"goal_id", "title", "related_goal_id", "related_title",
    "shared_keywords": [...]}]，纯只读聚合，不产生新的持久化。
    """
    backlog = GrowthBacklog(paths)
    pursued: list[tuple[str, Any]] = []
    for c in backlog.load_all():
        if not c.linked_goal_id:
            continue
        goal = goal_backlog.get(c.linked_goal_id)
        if goal is None or not goal.recurring:
            continue
        pursued.append((c.title, goal))
    if len(pursued) < 2:
        return []

    effective_keywords = _effective_topic_keywords(profile) if profile is not None else {}
    relations: list[dict] = []
    for title_a, goal_a in pursued:
        content_a = _recent_covered_subtopics_text(paths, goal_a.id, _PURSUIT_RELATED_LOOKBACK_CYCLES)
        if not content_a:
            continue
        content_a_lower = content_a.lower()
        for title_b, goal_b in pursued:
            if goal_a.id == goal_b.id:
                continue
            info_b = effective_keywords.get(title_b)
            keywords_b = list(info_b.get("keywords") or []) if isinstance(info_b, dict) else []
            if not keywords_b:
                continue
            shared = [kw for kw in keywords_b if kw.lower() in content_a_lower]
            if len(shared) >= _PURSUIT_RELATED_MIN_SHARED_KEYWORDS:
                relations.append({
                    "goal_id": goal_a.id,
                    "title": title_a,
                    "related_goal_id": goal_b.id,
                    "related_title": title_b,
                    "shared_keywords": shared,
                })
    return relations


# ──────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 3] ────────────
# Goal 执行内容反哺信号扫描：候选 → Goal 此前是单向的，Goal 绑定周期性
# 执行之后每一轮实际产出的 `open_questions` 完全不会反哺回成长顾问的
# 候选池，持续调研过程中牵出的相邻新兴趣点只能永远沉默地躺在那里。这里
# 补一个规则式初筛：同一段 open_questions 文本在最近几轮里反复出现、又
# 从未被任何一轮的 `covered_subtopics` 吸收，就当作一条"衍生话题"信号，
# 并入 `growth_signal_scan()` 的候选生成输入（不直接生成候选，避免绕开
# 用户"采纳"这一步）。

# 只看每个方向最近这么多轮的 open_questions（早于这个窗口的历史不再
# 重新翻出来，避免陈年话题反复刷屏）。
_SPINOFF_LOOKBACK_CYCLES = 3
# 同一段文本在窗口内至少出现这么多次才算"反复出现"（单轮提到的问题很
# 可能下一轮就自然被吸收，不构成独立信号）。
_SPINOFF_MIN_OCCURRENCES = 2


def extract_spinoff_topics_from_pursuits(paths, goal_backlog) -> dict[str, list[str]]:
    """扫描全部已落地成 Goal 且 `recurring=True` 的成长方向（口径与
    `pursuits_portfolio_summary()` 一致：遍历 `GrowthBacklog` 里
    `linked_goal_id` 指向的、仍在周期性推进的 Goal），从其最近几轮
    manifest 的 `handoff.open_questions` 里挖掘"反复出现、但从未被
    任何一轮 `covered_subtopics` 吸收"的衍生话题。

    返回 `{topic_text: [evidence_refs...]}`——跟 `growth_signal_scan()`
    产出的 `growth_focus_areas` 同构，供调用方直接并入同一份 dict 交给
    `growth_candidate_derive()` 处理，走同一套证据数阈值/置信度计算。
    `evidence_refs` 是合成 id（`pursuit_spinoff:{goal_id}:{窗口内相对
    轮次编号}`），不是真实 memory entry_id，只用于让 `evidence_count`
    可计数、可去重。

    规则式初筛，零 LLM 成本，只读不写：任何单个 Goal 的 manifest 读取
    失败都不影响其它 Goal，最终吸收不到时返回空字典。
    """
    from mini_agent.evolution import output_workspace
    from mini_agent.perception.goal_execution_spec import get_handoff_data

    result: dict[str, list[str]] = {}
    try:
        backlog = GrowthBacklog(paths)
        candidates = backlog.load_all()
    except Exception:
        return result

    seen_goal_ids: set[str] = set()
    for c in candidates:
        if not c.linked_goal_id or c.linked_goal_id in seen_goal_ids:
            continue
        goal = goal_backlog.get(c.linked_goal_id)
        if goal is None or not goal.recurring:
            continue
        seen_goal_ids.add(c.linked_goal_id)

        try:
            base_dir = output_workspace.goal_output_base_dir(paths, goal.id)
            manifests = output_workspace.read_all_manifests(base_dir)
        except Exception:
            continue
        if not manifests:
            continue

        ever_covered: set[str] = set()
        for m in manifests:
            handoff = get_handoff_data(m.get("progress_note") or "") or {}
            for s in handoff.get("covered_subtopics") or []:
                s = str(s).strip()
                if s:
                    ever_covered.add(s)

        window = manifests[-_SPINOFF_LOOKBACK_CYCLES:]
        occurrence_cycles: dict[str, list[int]] = {}
        for idx, m in enumerate(window, start=1):
            handoff = get_handoff_data(m.get("progress_note") or "") or {}
            for q in handoff.get("open_questions") or []:
                q = str(q).strip()
                if not q:
                    continue
                occurrence_cycles.setdefault(q, []).append(idx)

        for q, cycles in occurrence_cycles.items():
            if len(cycles) < _SPINOFF_MIN_OCCURRENCES or q in ever_covered:
                continue
            refs = [f"pursuit_spinoff:{goal.id}:{n}" for n in cycles]
            existing = result.setdefault(q, [])
            result[q] = sorted(set(existing) | set(refs))

    return result


_PURSUIT_SATURATION_TREND_RAW_WINDOW_DAYS = 60
_DEFAULT_PURSUIT_SATURATION_TREND_MAX_POINTS = 30


def _compact_pursuit_saturation_trend_rows(rows: list[dict], *, now: Optional[float] = None) -> list[dict]:
    """纯函数：对饱和度趋势的行做降采样，返回压缩后的新列表（不做任何
    IO）。跟 `_compact_health_trend_rows()` 是同一套模式——按 goal_id +
    天分桶，桶内只保留时间最新的一条，避免"每轮一条、永久累积"。"""
    now = now if now is not None else time.time()
    cutoff = now - _PURSUIT_SATURATION_TREND_RAW_WINDOW_DAYS * 86400
    recent = [r for r in rows if r.get("recorded_at", 0) >= cutoff]
    old = [r for r in rows if r.get("recorded_at", 0) < cutoff]
    if not old:
        return rows
    buckets: dict[tuple, dict] = {}
    for r in old:
        ts = r.get("recorded_at", 0)
        key = (r.get("goal_id"), int(ts // 86400))
        existing = buckets.get(key)
        if existing is None or ts > existing.get("recorded_at", 0):
            buckets[key] = r
    out = list(buckets.values()) + recent
    out.sort(key=lambda r: r.get("recorded_at", 0))
    return out


def compact_pursuit_saturation_trend_storage(paths, *, now: Optional[float] = None) -> int:
    """对落盘的 growth_pursuit_saturation_trend.jsonl 做一次降采样压缩，
    返回被压缩掉的行数（0 表示本次没有可压缩的旧数据，不会触发写盘）。
    幂等操作，跟 `compact_health_trend_storage()` 的调用契约一致——建议
    跟它一样接在 `run_daily_cycle()` 尾部调用，不需要单独的调度入口。"""
    rows = _read_jsonl(paths.growth_pursuit_saturation_trend_path)
    if not rows:
        return 0
    compacted = _compact_pursuit_saturation_trend_rows(rows, now=now)
    removed = len(rows) - len(compacted)
    if removed > 0:
        _write_jsonl(paths.growth_pursuit_saturation_trend_path, compacted)
    return removed


def get_pursuit_saturation_trend(
    paths, goal_id: str, limit: int = _DEFAULT_PURSUIT_SATURATION_TREND_MAX_POINTS,
) -> list[dict]:
    """返回某个 Goal 最近 `limit` 条"这一轮是否低增量"的时间序列，按
    时间正序，供看板"🔄 正在自主推进"分区展开后画一条简单走势（跟已有的
    "证据数走势"箭头展示风格一致，不需要引入图表库）。只读，不产生任何
    写入。跟 `_topic_trend_series()` / `health_trend_series()` 的调用
    契约一致（早期的点丢弃，只关心"最近的走势"）。"""
    rows = [
        {
            "recorded_at": r.get("recorded_at"),
            "low_increment": r.get("low_increment"),
            "streak": r.get("streak"),
            "saturated": r.get("saturated"),
            "llm_reviewed": r.get("llm_reviewed", False),
            "llm_verdict": r.get("llm_verdict"),
            "llm_reason": r.get("llm_reason") or "",
        }
        for r in _read_jsonl(paths.growth_pursuit_saturation_trend_path)
        if r.get("goal_id") == goal_id
    ]
    rows.sort(key=lambda r: r.get("recorded_at") or 0)
    return rows[-limit:] if limit else rows


def process_pursuit_cycle_completion(paths, goal, *, llm_helper=None, cfg=None) -> Optional[dict]:
    """[方向 B1/B2 的组装入口] 一个"成长顾问自主推进"的 Goal 某一轮循环
    完成后调用：算这一轮的增量质量 → 更新饱和度计数 → 刚跨过阈值时
    返回一条可供上层（`goal_cron_bridge.reap_finished_cycles()`）推送
    的提示信息。只处理打了 `growth_advisor` 标签的 Goal，其余 Goal 直接
    跳过返回 `None`（这个信号只对成长顾问的持续调研场景有意义，通用
    周期性 Goal 不受影响）。

    `llm_helper` + `cfg.pursuit_increment_llm_review_enabled=True`
    （都满足才触发）[方向 1]：透传给 `evaluate_cycle_increment()`，只在
    规则式初筛已经判定"疑似低增量"的轮次上追加一次 LLM 复核，复核结果
    随 streak 一起存进 `pursuit_saturation`，不改变 streak 本身的计数
    口径（见 `record_pursuit_cycle_signal()` 的说明）。`llm_helper` 为
    `None` 或开关关闭时行为与改动前完全一致。

    任何异常都不向上抛——这是诊断增强，不能反过来影响
    `reap_finished_cycles()` 的计数主流程；调用方应当把本函数包在
    try/except 里（`reap_finished_cycles()` 已经这样做）。

    返回 `None`（未触发提示）或
        {"goal_id", "goal_title", "streak", "message"}（刚跨过饱和
        阈值，建议向用户推一条提示）。
    """
    if "growth_advisor" not in (getattr(goal, "tags", None) or []):
        return None
    llm_review_enabled = bool(getattr(cfg, "pursuit_increment_llm_review_enabled", False)) if cfg is not None else False
    increment = evaluate_cycle_increment(
        paths, goal.id, llm_helper=llm_helper, llm_review_enabled=llm_review_enabled,
    )
    signal = record_pursuit_cycle_signal(
        paths, goal.id, increment.get("low_increment", False),
        llm_reviewed=increment.get("llm_reviewed", False),
        llm_verdict=increment.get("llm_verdict"),
        llm_reason=increment.get("llm_reason", ""),
    )
    if not signal.get("newly_saturated"):
        return None
    return {
        "goal_id": goal.id,
        "goal_title": goal.title,
        "streak": signal["streak"],
        "message": (
            f"「{goal.title}」最近 {signal['streak']} 轮新增内容不多了，"
            "是这个方向已经了解得差不多，还是希望换个角度继续深挖？"
            "可以考虑把频率降低一些，或先告一段落。"
        ),
    }


# ────────── [growth_advisor_autonomy_deepening_plan.md 方向 C1] 定期整理 ──────────
# `growth_pursuit` 模板此前只会"线性追加"，轮次一多页面会越来越长、缺乏
# 组织。这里不新增独立的 cron job 或数据结构，只是在满足轮次条件时往
# 拼给模型的 prompt 里多插一段"这一轮顺带整理一下"的指令——仍然是同一个
# 执行循环里的一种特殊模式。


def reorganize_hint_for_cycle(goal, cycle_no: int, cfg=None) -> Optional[str]:
    """[方向 C1] 只对打了 `growth_advisor` 标签、且这一轮轮次号能整除
    `cfg.reorganize_every_n_cycles`（默认 10，<=0 视为关闭）的 Goal 返回
    一段拼进子 Objective description 的整理指令；其余情况返回 None。

    纯规则式判断（轮次号取模），零 LLM 成本，不读取任何执行历史——是否
    真的需要整理由模型在这一轮执行时自行判断，这里只是"提醒"，不代表
    这一轮的产出会因此被强制要求包含整理内容（对齐 per_cycle_criteria
    仍然是 manual_review 的既有克制）。
    """
    if "growth_advisor" not in (getattr(goal, "tags", None) or []):
        return None
    every_n = getattr(cfg, "reorganize_every_n_cycles", 10) if cfg is not None else 10
    if every_n is None or every_n <= 0:
        return None
    if cycle_no <= 0 or cycle_no % every_n != 0:
        return None
    return (
        f"【本轮附加提示（第 {cycle_no} 轮，累计满 {every_n} 轮）】\n"
        "这一轮除了正常新增内容，请先花一点时间对现有 wiki 页面做一次\n"
        "重新组织：合并重复表述、按子话题分节、把 handoff.open_questions\n"
        "里已经解决的问题移出——组织之后再继续本轮新增部分，不要为了整理\n"
        "而删减已有的有效信息。"
    )


# ────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 5] 学习效果自测 ──────────
# `growth_pursuit` 模板产出的是持续增厚的读书笔记，从来不检验"用户是不是
# 真的理解/能应用这些内容"。这里复用 C1 已经验证过的"按累计轮次触发额外
# prompt 指令"模式：不新增独立的判分/交互系统，只是在满足轮次条件时往
# 拼给模型的 prompt 里多插一段"顺带生成几道自测题"的指令，仍然是同一个
# 执行循环里的一次追加产出。刻意不做自动判分、不要求用户提交答案——一旦
# 引入"系统给用户的理解程度打分"，就跨过了 growth_advisor_design.md 明确
# 写的非目标（"不做心理评估/主观判断"）的边界。


def self_check_hint_for_cycle(goal, cycle_no: int, cfg=None) -> Optional[str]:
    """[方向 5] 只对打了 `growth_advisor` 标签、且这一轮轮次号能整除
    `cfg.pursuit_self_check_every_n_cycles`（默认 5，<=0 视为关闭）的
    Goal 返回一段拼进子 Objective description 的自测指令；其余情况
    返回 None。

    纯规则式判断（轮次号取模），不额外触发 LLM 调用（复用同一次执行
    循环里已有的调用），也不读取任何执行历史——生成的自测题质量、
    要不要真的生成，都留给模型在这一轮执行时自行判断，这里只是"提醒"，
    对齐 per_cycle_criteria 仍然是 manual_review 的既有克制。
    """
    if "growth_advisor" not in (getattr(goal, "tags", None) or []):
        return None
    every_n = getattr(cfg, "pursuit_self_check_every_n_cycles", 5) if cfg is not None else 5
    if every_n is None or every_n <= 0:
        return None
    if cycle_no <= 0 or cycle_no % every_n != 0:
        return None
    return (
        "【本轮附加提示（第 {cycle_no} 轮，累计满 {every_n} 轮）】\n"
        "这一轮除了正常新增内容，请基于目前已覆盖的 covered_subtopics，\n"
        "额外生成 3~5 道可以自问自答的检验问题（附简短参考答案要点），\n"
        "追加到 wiki 页面末尾一个独立小节「## 自测：第 {cycle_no} 轮小结」。\n"
        "这不是一个测验系统，不需要用户当场提交答案，只是帮用户自己在\n"
        "阅读时判断这几个问题答不答得上来；请不要对用户的掌握程度做任何\n"
        "评价或判分，只提供问题和参考答案要点。"
    ).format(cycle_no=cycle_no, every_n=every_n)


# ────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 6] 调研风格智能分类 ──────────
# 现状：无论学技术、读理论书、还是养习惯，`growth_pursuit` 模板的产出方式
# 完全一样。这里补一层"调研风格"分类——跟 P5-3 的 `_category_of()`（"是
# 什么话题"）是两个正交维度，这里回答的是"这类话题该怎么调研/呈现"。
# 规则式关键词匹配是默认路径（零成本、总是跑），LLM 只在 opt-in 开启且
# 传了 llm_helper 时对规则结果做一次复核/纠偏——跟 `classify_topic_
# category_llm()` 同一套"规则默认、LLM 增强"的取舍，避免引入新的强依赖。
_PURSUIT_STYLE_LABELS = ("技能实操类", "知识理论类", "习惯养成类")

# 关键词命中数最多的风格胜出；全都不命中时兜底"知识理论类"（读书笔记式
# 持续调研是 growth_pursuit 模板最初、也是最通用的产出形态，作为默认最
# 保守）。关键词表刻意保持简短、只覆盖高置信度词，宁可漏判归入默认值，
# 不强行猜测引入噪音——跟 `_TOPIC_CATEGORIES` 只登记 7 个高置信度主题
# 而不是穷举所有可能主题的取舍一致。
_PURSUIT_STYLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "技能实操类": (
        "编程", "开发", "工程", "框架", "库", "工具", "代码", "调试",
        "部署", "实战", "项目实践", "动手", "python", "javascript",
        "sql", "api", "架构", "设计模式", "算法实现",
    ),
    "习惯养成类": (
        "习惯", "打卡", "坚持", "自律", "作息", "锻炼", "运动", "冥想",
        "早起", "戒", "养成", "日常", "健身", "饮食",
    ),
}


def _infer_pursuit_style_rule(topic: str, extra_text: str = "") -> str:
    """[方向 6] 规则式关键词匹配，零 LLM 成本、总是可用。只在 `_PURSUIT_
    STYLE_KEYWORDS` 登记的两类（技能实操类/习惯养成类）里找关键词命中，
    命中数最多的胜出；平局或全不命中一律兜底"知识理论类"（既是最通用的
    默认产出形态，也避免在证据不足时武断猜测）。
    """
    text = f"{topic} {extra_text}".lower()
    best_label = "知识理论类"
    best_count = 0
    for label, keywords in _PURSUIT_STYLE_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw.lower() in text)
        if count > best_count:
            best_count = count
            best_label = label
    return best_label


def classify_pursuit_style_llm(
    topic: str, keywords: list[str], llm_helper: Callable[[str], str], *, paths=None
) -> Optional[str]:
    """[方向 6] 用 LLM 把一个成长方向归到 3 种调研风格之一。跟
    `classify_topic_category_llm()` 同款"opt-in、宽松吸收"模式：解析
    失败、返回值不在 3 个标签里，一律返回 None，调用方兜底沿用规则式
    结果（不倒退现有行为，不会因为 LLM 抽风就丢掉一个可用的分类）。
    """
    prompt = (
        "请把下面这个用户成长方向归到 3 种调研/呈现风格之一：技能实操类/"
        "知识理论类/习惯养成类。技能实操类指需要动手案例、可复现操作步骤"
        "的技术或技能学习；知识理论类指需要结构化知识脉络的理论、书籍、"
        "概念性学习；习惯养成类指需要短周期打卡提醒、而不是持续增厚知识"
        "库的行为习惯培养。\n"
        f"方向：{topic}\n关键词：{', '.join(keywords)}\n"
        "只输出风格名称本身（3 选 1），不要有其他文字。"
    )
    try:
        raw = llm_helper(prompt)
    except Exception as exc:
        if paths is not None:
            _record_llm_call_status(paths, "pursuit_style", "error", detail=str(exc)[:200])
        return None
    if not raw:
        if paths is not None:
            _record_llm_call_status(paths, "pursuit_style", "empty_response")
        return None
    text = raw.strip()
    for label in _PURSUIT_STYLE_LABELS:
        if label in text:
            if paths is not None:
                _record_llm_call_status(paths, "pursuit_style", "success", detail=label)
            return label
    if paths is not None:
        _record_llm_call_status(paths, "pursuit_style", "parse_error", detail=text[:100])
    return None


def determine_pursuit_style(
    topic: str,
    *,
    extra_text: str = "",
    keywords: Optional[list[str]] = None,
    cfg=None,
    llm_helper: Optional[Callable[[str], str]] = None,
    paths=None,
) -> str:
    """[方向 6] 调研风格分类的统一入口：规则式结果总是先算出来（零成本、
    保证总能返回一个合法标签）；`cfg.pursuit_style_llm_enabled=True` 且
    传了 `llm_helper` 时，额外调一次 LLM 复核，命中就用 LLM 结果覆盖，
    LLM 不可用/解析失败/未开启时静默沿用规则式结果——这一步失败绝不
    影响返回值的可用性。
    """
    rule_label = _infer_pursuit_style_rule(topic, extra_text)
    if not getattr(cfg, "pursuit_style_llm_enabled", False):
        return rule_label
    if llm_helper is None:
        return rule_label
    llm_label = classify_pursuit_style_llm(topic, keywords or [], llm_helper, paths=paths)
    return llm_label if llm_label in _PURSUIT_STYLE_LABELS else rule_label


# 每种风格对应的 prompt 追加指令，接入 `growth_pursuit` 模板每一轮的子
# Objective description（见 goal_cron_bridge._append_growth_pursuit_
# style_hint()）。跟 C1/方向 5 的"按轮次追加提示"是同一种零成本接入
# 方式的变体：这里不按轮次触发，而是每一轮都带上（风格是这个方向的
# 持续属性，不是某个特定轮次才需要的提醒）。
_PURSUIT_STYLE_PROMPT_ADDENDUM: dict[str, str] = {
    "技能实操类": (
        "这是一个偏技能实操类的方向：请多给可复现的操作步骤、代码/命令"
        "示例、动手练习，少堆砌纯概念性描述。"
    ),
    "知识理论类": (
        "这是一个偏知识理论类的方向：请注意维护清晰的结构化知识脉络"
        "（概念之间的关系、层级），帮助形成系统性理解，而不是零散知识点"
        "的简单堆砌。"
    ),
    "习惯养成类": (
        "这是一个偏习惯养成类的方向：请以短小的打卡式进展记录/提醒为主，"
        "不需要持续增厚成篇的知识库内容；重点关注是否坚持、有没有中断，"
        "而不是新增了多少新知识。"
    ),
}


def pursuit_style_hint(goal, cfg=None) -> Optional[str]:
    """[方向 6] 只对打了 `growth_advisor` 标签、且已经有 `growth_pursuit_
    style` 标记（在 `auto_pursue_candidate()` 落地时写入）的 Goal 返回
    一段拼进子 Objective description 的风格提示；其余情况返回 None
    （对齐"未分类时不影响任何现有行为"的既有克制）。`cfg` 目前只是为了
    跟其它 hint 函数保持统一签名，风格提示本身不依赖任何配置项。
    """
    if "growth_advisor" not in (getattr(goal, "tags", None) or []):
        return None
    style = getattr(goal, "growth_pursuit_style", None)
    addendum = _PURSUIT_STYLE_PROMPT_ADDENDUM.get(style) if style else None
    if not addendum:
        return None
    return f"【调研风格提示】{addendum}"


# ────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 6 动态修正] ──────────
# `auto_pursue_candidate()` 只在 Goal 首次落地时基于候选标题/rationale
# 判定一次调研风格——这是"用还没开始调研之前的一句话猜风格"，猜错的
# 概率不低（比如"数据分析"这类主题，光看标题很难判断用户更想要动手
# 案例还是结构化理论）。这里补一层周期性的动态修正：累计满 N 轮后，
# 改用"这个方向实际已经产出的内容"（`covered_subtopics` 累积文本）
# 重新分类一次——内容比标题更能反映这个方向实际走向的是哪种风格。跟
# C1/方向 5 同一种"按累计轮次触发"的接入模式，但这里触发的是一次状态
# 更新（可能改写 `growth_pursuit_style`），不是往 prompt 里追加文字。

_PURSUIT_STYLE_RECLASSIFY_LOOKBACK_CYCLES = 5


def _recent_covered_subtopics_text(paths, goal_id: str, lookback: int) -> str:
    """[方向 6 动态修正] 汇总最近 `lookback` 轮 manifest 里 handoff 的
    `covered_subtopics`，拼成一段文本供重新分类时当 `extra_text` 用。
    读取失败/没有数据时返回空字符串，调用方据此自然退化为"只用标题
    分类"（等价于改动前的行为）。
    """
    try:
        from mini_agent.evolution import output_workspace
        from mini_agent.perception.goal_execution_spec import get_handoff_data
        base_dir = output_workspace.goal_output_base_dir(paths, goal_id)
        manifests = output_workspace.read_all_manifests(base_dir)
    except Exception:
        return ""
    if not manifests:
        return ""
    topics: list[str] = []
    for m in manifests[-lookback:]:
        handoff = get_handoff_data(m.get("progress_note") or "") or {}
        v = handoff.get("covered_subtopics")
        if isinstance(v, list):
            topics.extend(str(x).strip() for x in v if str(x).strip())
    return " ".join(topics)


def maybe_reclassify_pursuit_style(
    paths, goal_backlog, goal, cycle_no: int, *, cfg=None, llm_helper: Optional[Callable[[str], str]] = None,
) -> Optional[str]:
    """[方向 6 动态修正] 只对打了 `growth_advisor` 标签、且这一轮轮次号
    能整除 `cfg.pursuit_style_reclassify_every_n_cycles`（默认 8，`<=0`
    视为关闭）的 Goal 触发一次重新分类；其余情况直接返回 `None`（不
    触碰任何字段）。

    用最近几轮实际产出的 `covered_subtopics` 文本（而不是当初落地时的
    候选标题/rationale）重新跑一次 `determine_pursuit_style()`——规则式
    路径零成本，`pursuit_style_llm_enabled` 开启时同样可以走 LLM 复核。
    新结果与当前值不同才写回（`goal_backlog.update_fields()` +
    直接更新 `goal.growth_pursuit_style`，两者都做是为了不依赖调用方
    传入的 `goal` 对象是否与 backlog 内部节点是同一个引用）；相同则
    不产生任何写入，避免无意义的 `last_touched_at` 刷新。没有可用的
    `covered_subtopics` 文本（轮次还太少、或非 growth_pursuit 模板）时
    直接跳过，不会用"没有新内容"误判成某个具体风格。

    返回新分类结果（触发且结果变化时）或 `None`（未触发/未变化/没有
    可用文本）。任何异常都不应该影响 Goal 触发主流程，调用方
    （`goal_cron_bridge`）负责包一层 try/except。
    """
    if "growth_advisor" not in (getattr(goal, "tags", None) or []):
        return None
    every_n = getattr(cfg, "pursuit_style_reclassify_every_n_cycles", 8) if cfg is not None else 8
    if every_n is None or every_n <= 0:
        return None
    if cycle_no <= 0 or cycle_no % every_n != 0:
        return None
    extra_text = _recent_covered_subtopics_text(
        paths, goal.id, _PURSUIT_STYLE_RECLASSIFY_LOOKBACK_CYCLES,
    )
    if not extra_text:
        return None
    new_style = determine_pursuit_style(
        goal.title, extra_text=extra_text, cfg=cfg, llm_helper=llm_helper, paths=paths,
    )
    if new_style == getattr(goal, "growth_pursuit_style", None):
        return None
    goal_backlog.update_fields(goal.id, growth_pursuit_style=new_style)
    goal.growth_pursuit_style = new_style
    return new_style


# ────────── [growth_advisor_autonomy_deepening_plan.md 方向 C2] 本轮新增摘要推送 ──────────
# 复用 2.4 节已有的推送节流机制（notification_frequency/notification_
# max_per_day），不新增一套独立的通知逻辑——每轮执行结束后先把"本轮新增
# 了什么"存进 growth_state.json 的一个待推送队列，等下一次真正触发推送
# （日常报告推送 / 周摘要）时顺带打包进同一条消息，不额外占用推送额度。

_PURSUIT_DIGEST_MAX_PENDING = 30


def record_pursuit_cycle_digest(paths, goal, cfg=None) -> Optional[dict]:
    """[方向 C2] 一轮持续调研完成后调用：从最近两轮 manifest 的 handoff
    里算出本轮新增的 `covered_subtopics`，整理成一条"本轮新增摘要"，
    存进 `growth_state.json` 的 `pending_pursuit_digests` 队列（不立即
    推送）。只处理打了 `growth_advisor` 标签的 Goal，其余直接跳过。

    `cfg.pursuit_digest_enabled=False` 时整体跳过（不产生任何暂存），
    默认开启。队列超过 `_PURSUIT_DIGEST_MAX_PENDING` 条时丢弃最旧的——
    这是一份"待展示的摘要"，不是审计日志，丢一点旧数据不影响正确性。

    返回新增的摘要条目（供调用方测试/日志），本轮没有新增子话题或没有
    可比较的 handoff 数据时返回 None（不落一条空摘要）。
    """
    if cfg is not None and not getattr(cfg, "pursuit_digest_enabled", True):
        return None
    if "growth_advisor" not in (getattr(goal, "tags", None) or []):
        return None
    increment = evaluate_cycle_increment(paths, goal.id)
    if not increment.get("evaluated"):
        return None
    new_count = increment.get("new_subtopics_count") or 0
    if new_count <= 0:
        return None

    from mini_agent.evolution import output_workspace
    from mini_agent.perception.goal_execution_spec import get_handoff_data

    base_dir = output_workspace.goal_output_base_dir(paths, goal.id)
    manifests = output_workspace.read_all_manifests(base_dir)
    if len(manifests) < 2:
        return None
    prev_handoff = get_handoff_data(manifests[-2].get("progress_note") or "") or {}
    curr_handoff = get_handoff_data(manifests[-1].get("progress_note") or "") or {}

    def _as_set(handoff: dict, key: str) -> set:
        v = handoff.get(key)
        if isinstance(v, list):
            return {str(x).strip() for x in v if str(x).strip()}
        return set()

    new_subtopics = sorted(_as_set(curr_handoff, "covered_subtopics") - _as_set(prev_handoff, "covered_subtopics"))
    if not new_subtopics:
        return None

    entry = {
        "goal_id": goal.id,
        "goal_title": goal.title,
        "new_subtopics": new_subtopics[:8],
        "at": time.time(),
    }
    state = _load_growth_state(paths)
    pending = list(state.get("pending_pursuit_digests") or [])
    pending.append(entry)
    if len(pending) > _PURSUIT_DIGEST_MAX_PENDING:
        pending = pending[-_PURSUIT_DIGEST_MAX_PENDING:]
    state["pending_pursuit_digests"] = pending
    _save_growth_state(paths, state)
    return entry


def _pop_pending_pursuit_digest_lines(paths) -> list[str]:
    """取出并清空当前全部待推送的"本轮新增摘要"，格式化成可以直接拼进
    通知正文的若干行文本。只在调用方确实要发出一条推送消息时调用——
    调用即清空，避免同一条摘要被打包进两条不同的推送消息里重复出现。"""
    state = _load_growth_state(paths)
    pending = list(state.get("pending_pursuit_digests") or [])
    if not pending:
        return []
    state["pending_pursuit_digests"] = []
    _save_growth_state(paths, state)
    lines = []
    for entry in pending:
        subtopics = "、".join(entry.get("new_subtopics") or [])
        lines.append(f"- 「{entry.get('goal_title', '')}」本轮新增：{subtopics}")
    return lines


def peek_pending_pursuit_digests(paths) -> list[dict]:
    """只读查询当前待推送的摘要队列，供看板"🔄 正在自主推进"分区展示
    "还没推送的最新进展"，不产生任何写入、不清空队列。"""
    state = _load_growth_state(paths)
    return list(state.get("pending_pursuit_digests") or [])


# ────────────────────────── [LLM 增强路径可观测性] ──────────────────────────
# 此前 `_llm_augment_topics` / 报告正文 LLM 起草 / `classify_topic_category_llm`
# 三处 LLM 增强调用失败（异常、空响应、JSON 解析失败）都只落一条
# `log_exception` 或者干脆原样吞掉退回默认路径，用户在诊断面板里完全看
# 不出"这些 opt-in 开关我开了，但它是不是真的在生效"——对一个用户主动
# 选择开启的能力来说，静默失败比默认关闭更容易造成误解（以为在用 LLM，
# 实际上一直在退化路径）。这里统一记一份"最近一次调用结果"快照，复用
# growth_advisor_state.json（同样是低频写的小文件，不需要单独开一个）。

# 可能的 outcome 取值（不同调用点用得到的子集不同，均为诊断展示用途，
# 不参与任何业务判断）：
#   "success"                         —— 正常拿到可用结果
#   "no_new_topics" / "empty_response" —— 调用成功但没有新增有效内容
#   "skipped_insufficient_unmatched"   —— 未命中记忆条目太少，没必要调用（仅信号增强）
#   "parse_error"                      —— 有响应，但解析失败（仅信号增强）
#   "error"                            —— 调用本身抛出异常
_LLM_CALL_TYPES = (
    "signal_augment", "report_quality", "topic_category", "goal_alignment_match", "report_outline",
    "pursuit_increment_review",
)


def _record_llm_call_status(paths, call_type: str, outcome: str, *, detail: str = "") -> None:
    """记录一次 LLM 增强调用的结果快照，供 `diagnostics_snapshot` 展示。
    只保留"最近一次"，不追加历史（这是一份健康检查用的状态，不是审计
    日志；需要历史趋势的话应该看各调用点各自落盘的业务数据，比如
    `growth_reports.jsonl` 里 `source` 字段的分布）。"""
    state = _load_growth_state(paths)
    llm_status = dict(state.get("llm_call_status") or {})
    llm_status[call_type] = {
        "outcome": outcome,
        "detail": (detail or "")[:300],
        "ts": time.time(),
    }
    state["llm_call_status"] = llm_status
    _save_growth_state(paths, state)


def llm_call_status_snapshot(paths) -> dict[str, dict]:
    """[诊断] 三个 LLM 增强调用点各自"最近一次调用结果"，未触发过的
    调用点不会出现在返回值里（区别于"触发过但失败"）。"""
    state = _load_growth_state(paths)
    return dict(state.get("llm_call_status") or {})


# ────────────────────────── P3：首次触达提示的跨会话持久化 ──────────────────────────
# 方案第 8 节第 1 条："默认开启，但首次触达必须透明告知"。P2 阶段看板只用
# st.session_state 做了单次会话内的提示（见 P2 实施记录"已知简化"），这里
# 补上跨会话持久化：状态落盘复用 growth_advisor_state.json，跟推送节流
# 状态放在同一个文件里（同样是"低频写的小文件"，不需要单独开一个文件）。


def first_touch_notice_shown(paths) -> bool:
    """看板是否已经展示过首次触达提示（跨会话持久化，落盘查询）。"""
    return bool(_load_growth_state(paths).get("first_touch_notice_shown"))


def mark_first_touch_notice_shown(paths) -> None:
    """记录首次触达提示已经展示过，之后不再重复弹出。"""
    state = _load_growth_state(paths)
    if not state.get("first_touch_notice_shown"):
        state["first_touch_notice_shown"] = True
        state["first_touch_notice_shown_at"] = time.time()
        _save_growth_state(paths, state)


# ────────────────────────── P3：weekly_digest 真实周摘要打包 ──────────────────────────
# 此前 notification_frequency="weekly_digest" 与 "daily" 走同一套按天节流的
# 逻辑（见 P2 实施记录"已知简化"），效果只是"daily 但通常不会真的每天都
# 推"，并不是方案第 4.2 节要求的"把一周内的报告打包成一条"。这里补上真正
# 的周频聚合：状态里新增 `last_weekly_digest_at`（时间戳，不是自然日），
# 距上次推送不满 7 天则跳过；到期后把窗口内新生成的报告标题打包成一条
# 摘要消息一次性推送，而不是逐条推。

WEEKLY_DIGEST_INTERVAL_DAYS = 7


# ────────── [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 感知维度候选] 推送的情境感知（软性节流） ──────────
# 现状：推送节流此前完全是静态规则（置信度阈值、每天最多几条），不会
# 感知"用户最近是不是明显没那么活跃"。这里补一个最小的情境感知信号：
# 用最近一周的记忆条目数 vs 更早几周的周均值算一个密度比值，只在
# 显著低于历史水平时，把有效置信度门槛稍微抬高一点（软性因子——依然
# 可能推送，只是需要更高的置信度），而不是硬性阻断或跳过整轮推送。
# 判断"这几天不适合推新方向"完全靠间接信号推断，本身就不精确，选择
# "软性抬高门槛"而不是"直接不推"，是为了在信号可能误判时把伤害限制在
# "少数中等置信度报告延后一天"，而不是"确实想看却因为误判被拦下"。

_CONVERSATION_DENSITY_RECENT_DAYS = 7
_CONVERSATION_DENSITY_BASELINE_WEEKS = 4


def _recent_conversation_density_ratio(
    memory_store, *, recent_days: int = _CONVERSATION_DENSITY_RECENT_DAYS,
    baseline_weeks: int = _CONVERSATION_DENSITY_BASELINE_WEEKS, now: Optional[float] = None,
) -> Optional[float]:
    """[情境感知候选] 最近 `recent_days` 天的记忆条目数，相对于再往前
    `baseline_weeks` 周的周均值，算出一个密度比值——`< 1` 表示比历史
    水平更安静，`>= 1` 表示持平或更活跃。

    返回 `None`（而不是 `0`）的情况：`memory_store` 缺失、或基线窗口
    内条目数为 0（没有足够历史数据支撑"最近变安静了"这个判断，强行
    算出一个比值反而可能是噪音）——调用方遇到 `None` 应该视为"没有
    可用信号"，不套用任何软性调整，等价于改动前的行为。
    """
    if memory_store is None:
        return None
    now = now if now is not None else time.time()
    recent_cutoff = now - recent_days * 86400
    baseline_start = recent_cutoff - baseline_weeks * recent_days * 86400
    try:
        entries = memory_store.all_entries()
    except Exception:
        return None
    recent_count = 0
    baseline_count = 0
    for e in entries:
        created_at = getattr(e, "created_at", 0) or 0
        if created_at >= recent_cutoff:
            recent_count += 1
        elif created_at >= baseline_start:
            baseline_count += 1
    if baseline_count <= 0:
        return None
    baseline_weekly_avg = baseline_count / baseline_weeks
    if baseline_weekly_avg <= 0:
        return None
    return recent_count / baseline_weekly_avg


def _effective_notification_min_confidence(paths, cfg, memory_store=None) -> float:
    """[情境感知候选] 在配置的 `notification_min_confidence` 基础上，
    如果开启了 `notification_context_aware_throttle_enabled` 且能算出
    密度比值、且比值低于 `notification_low_activity_ratio_threshold`
    （默认 0.3，即"最近一周活跃度不到历史周均值的 30%"），额外加上
    `notification_low_activity_confidence_boost`（默认 0.15，封顶
    1.0）——软性抬高门槛，不是直接跳过推送。任何一步拿不到数据/未
    开启，都原样返回配置里的 `notification_min_confidence`，跟改动前
    行为一致。
    """
    base = getattr(cfg, "notification_min_confidence", 0.6)
    if not getattr(cfg, "notification_context_aware_throttle_enabled", False):
        return base
    ratio = _recent_conversation_density_ratio(memory_store)
    if ratio is None:
        return base
    threshold = getattr(cfg, "notification_low_activity_ratio_threshold", 0.3)
    if ratio >= threshold:
        return base
    boost = getattr(cfg, "notification_low_activity_confidence_boost", 0.15)
    return min(1.0, base + boost)


def _maybe_dispatch_weekly_digest(paths, cfg, profile=None) -> Optional[dict]:
    """`notification_frequency == "weekly_digest"` 时的推送逻辑：每 7 天
    最多推一次，内容是窗口期内（上次推送至今，首次则取最近 7 天）新生成
    的全部调研报告标题打包成一条摘要，而不是逐条推送。

    与 `_maybe_dispatch_notification` 的按天节流是互斥的两套路径，由
    `run_daily_cycle` 按 `notification_frequency` 分流调用，不会同时触发。
    """
    try:
        state = _load_growth_state(paths)
        now = time.time()
        last_at = state.get("last_weekly_digest_at")
        if last_at and (now - last_at) < WEEKLY_DIGEST_INTERVAL_DAYS * 86400:
            return None

        window_start = last_at if last_at else (now - WEEKLY_DIGEST_INTERVAL_DAYS * 86400)
        window_reports = [r for r in list_reports(paths) if r.created_at >= window_start]
        # [P4-5] 类别被静音的报告不进摘要打包，逻辑与 _maybe_dispatch_notification
        # 一致——静音是"完全不主动推送"，不是"降低频率"。
        window_reports = [r for r in window_reports if not _category_notification_muted(cfg, r.title, profile)]

        if not window_reports:
            # 没有新报告也要推进"上次检查时间"，避免每次 daily cycle 都
            # 重新计算同一个空窗口——但不落一条空摘要消息。
            state["last_weekly_digest_at"] = now
            _save_growth_state(paths, state)
            return None

        window_reports.sort(key=lambda r: -r.created_at)
        lines = [f"- {r.title}" for r in window_reports]
        body = (
            f"过去 {WEEKLY_DIGEST_INTERVAL_DAYS} 天为你生成了 {len(window_reports)} "
            f"份成长调研报告：\n" + "\n".join(lines)
        )
        # [方向 C2] 顺带打包本次窗口期内积累的"正在自主推进"方向的本轮新增
        # 摘要，不额外消耗推送额度——这条消息本来就要发，只是多拼几行。
        digest_lines = _pop_pending_pursuit_digest_lines(paths)
        if digest_lines:
            body += "\n\n正在自主持续调研的方向本轮新增：\n" + "\n".join(digest_lines)

        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        from mini_agent.notification import reports_store

        message = NotificationMessage(
            title=f"成长顾问周摘要（{len(window_reports)} 份报告）",
            body=body,
            source="growth_weekly_digest",
            meta={"report_ids": [r.report_id for r in window_reports]},
        )
        results = NotificationDispatcher(paths).dispatch(message)
        reports_store.append_report(
            paths,
            {
                "title": message.title,
                "body": message.body,
                "source": message.source,
                "report_ids": [r.report_id for r in window_reports],
                "created_at": message.created_at,
                "acknowledged": False,
            },
        )
        state["last_weekly_digest_at"] = now
        _save_growth_state(paths, state)
        return {
            "report_ids": [r.report_id for r in window_reports],
            "count": len(window_reports),
            "channels": results,
        }
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor._maybe_dispatch_weekly_digest")
        return None


def _maybe_dispatch_notification(
    paths, cfg, candidates_by_id: dict[str, GrowthCandidate], reports: list[GrowthReport], profile=None,
    memory_store=None,
) -> Optional[dict]:
    """方案第 4.2 节推送节流：看板展示不受限，主动推送（通知中心/邮件）
    才需要节流——本函数只负责"要不要推、推哪一条"，看板轮询走的是
    `/growth/summary` 只读端点，跟这里完全独立、不受影响。

    规则：
        - `notification_frequency == "kanban_only"` 或本轮没有新报告 ->
          不推送。
        - 先按 `notification_min_confidence` 过滤、再排除类别被静音
          （`category_notification_frequency` 配成 `"kanban_only"`）的
          报告；剩下的按 [P4-5] 优先级分数（置信度 × 类别历史采纳率加权，
          见 `_notification_priority_score`）取最高的一条；全部被过滤掉
          -> 不推送（"宁可不推，不为了凑数硬推"，方案第 4.2 节原文）。
        - 当天（自然日，本地时区）已推送次数达到 `notification_max_per_day`
          -> 不再推送，状态落盘在 `paths.growth_state_path`。
        - 任何一步异常都不应该打断 `run_daily_cycle` 主流程，统一
          try/except + log_exception 兜底，返回 None。

    `memory_store`：[情境感知候选] 可选，透传给
    `_effective_notification_min_confidence()` 用于计算最近对话密度；
    不传或 `cfg.notification_context_aware_throttle_enabled=False`
    时，有效置信度门槛就是配置里的 `notification_min_confidence`，
    行为与改动前完全一致。
    """
    reports = list(reports or [])
    if not reports:
        return None
    freq = getattr(cfg, "notification_frequency", "daily")
    if freq in ("kanban_only", "weekly_digest"):
        # weekly_digest 走独立的 _maybe_dispatch_weekly_digest()，不复用
        # 这里的按天节流；防御性地在这里也短路一次，避免调用方误接错分支
        # 时把 weekly_digest 误当成 daily 逐条推送。
        return None

    min_conf = _effective_notification_min_confidence(paths, cfg, memory_store)
    max_per_day = getattr(cfg, "notification_max_per_day", 1)
    # [P4-5] 按类别历史采纳率算优先级分数，而不是单纯比置信度；同时把
    # 类别被静音（category_notification_frequency=="kanban_only"）的
    # 报告排除在候选之外，不管置信度多高都不推送。
    category_rates = _category_acceptance_rate(paths, profile)

    scored: list[tuple[float, GrowthReport]] = []
    for r in reports:
        cand = candidates_by_id.get(r.candidate_id)
        conf = cand.confidence if cand is not None else 0.0
        if conf < min_conf:
            continue
        if _category_notification_muted(cfg, r.title, profile):
            continue
        priority = _notification_priority_score(conf, category_rates.get(_category_of(r.title)))
        scored.append((priority, conf, r))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    _best_priority, best_conf, best_report = scored[0]

    try:
        state = _load_growth_state(paths)
        today = _today_str()
        if state.get("last_notify_date") != today:
            state["last_notify_date"] = today
            state["notify_count_today"] = 0
        if state.get("notify_count_today", 0) >= max_per_day:
            return None

        from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage
        from mini_agent.notification import reports_store

        body = best_report.summary
        # [方向 C2] 这条日常推送本来就要发出，顺带打包正在自主持续调研
        # 方向的本轮新增摘要，不额外消耗 notification_max_per_day 额度。
        digest_lines = _pop_pending_pursuit_digest_lines(paths)
        if digest_lines:
            body += "\n\n正在自主持续调研的方向本轮新增：\n" + "\n".join(digest_lines)
        message = NotificationMessage(
            title=f"成长顾问：{best_report.title}",
            body=body,
            source="growth_report",
            meta={
                "candidate_id": best_report.candidate_id,
                "report_id": best_report.report_id,
                "confidence": best_conf,
            },
        )
        results = NotificationDispatcher(paths).dispatch(message)
        reports_store.append_report(
            paths,
            {
                "title": message.title,
                "body": message.body,
                "source": message.source,
                "candidate_id": best_report.candidate_id,
                "report_id": best_report.report_id,
                "confidence": best_conf,
                "created_at": message.created_at,
                "acknowledged": False,
            },
        )
        state["notify_count_today"] = state.get("notify_count_today", 0) + 1
        _save_growth_state(paths, state)
        return {"report_id": best_report.report_id, "confidence": best_conf, "channels": results}
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor._maybe_dispatch_notification")
        return None


# ────────── [P5-6] Top-N 报告生成里的"探索位" ──────────
# next_doc/growth_advisor_improvement_plan_v3.md P5-6：候选/推送排序此前
# 完全是"证据/置信度越高越优先"的纯利用策略，长期跑下去容易强化用户已经
# 感兴趣的类别，冷门但可能有价值的新方向永远排不上号。这里只做一个轻量
# 版本，不是完整的 bandit 算法：`max_reports_per_run` 名额里最多留 1 个
# 给"最近几轮报告里没出现过的类别"，如果所有类别都出现过，退化成正常按
# 置信度选（不强行制造探索）。默认关闭（`exploration_slot_enabled=False`），
# 这改变了"证据不够强就不推荐"的一贯克制原则，需要显式打开。
_DEFAULT_EXPLORATION_RECENT_WINDOW = 5


def _recent_report_categories(paths, cfg, profile=None) -> set[str]:
    """最近几轮报告（默认最近 5 份，见 `cfg.exploration_recent_window`）
    覆盖过哪些类别——用于判断某个类别算不算"最近已经出现过"。只读
    `list_reports()`（不含已归档的旧报告，语义上"最近"本来就不该看很久
    以前的历史），空历史返回空集合（意味着任何类别都算"没出现过"，第一次
    跑探索位选择时不会因为没有历史数据而拒绝探索）。"""
    window = getattr(cfg, "exploration_recent_window", _DEFAULT_EXPLORATION_RECENT_WINDOW) if cfg is not None else _DEFAULT_EXPLORATION_RECENT_WINDOW
    reports = sorted(list_reports(paths), key=lambda r: -r.created_at)[: max(window, 0)]
    return {_category_of(r.title, profile) for r in reports}


def _select_candidates_for_reports(
    candidates: list[GrowthCandidate], cfg, paths, profile=None
) -> list[tuple[GrowthCandidate, bool]]:
    """[P5-6] `run_daily_cycle` 里"选哪些候选生成报告"的入口，返回
    `(candidate, is_exploration)` 的列表，取代原来单纯的
    `sorted(...)[:max_reports]`。

    - 开关关闭（默认）或 `max_reports_per_run < 2`（没有多余名额可以留给
      探索位，硬留会导致"利用位"归零，不是本方案的取舍）时，行为跟改动
      前完全一致：纯按置信度取 Top-N，`is_exploration` 全部是 `False`。
    - 开启且名额 >= 2 时：正常按置信度取前 `max_reports - 1` 个作为
      "利用位"；剩下的候选里，优先选一个类别不在 `_recent_report_
      categories()` 里的（按原有置信度顺序找第一个符合条件的，不是随机
      选）；如果剩下的候选一个都没有，或者所有候选的类别都已经在最近几轮
      出现过，退化成正常按置信度选（保持 Top-N 的总数不变，只是
      `is_exploration` 都是 `False`）。
    """
    max_reports = getattr(cfg, "max_reports_per_run", 2) if cfg is not None else 2
    ranked = sorted(candidates, key=lambda c: -c.confidence)
    if max_reports <= 0 or not ranked:
        return []
    if not getattr(cfg, "exploration_slot_enabled", False) or max_reports < 2:
        return [(c, False) for c in ranked[:max_reports]]

    normal_n = max_reports - 1
    normal = ranked[:normal_n]
    remaining = ranked[normal_n:]
    if not remaining:
        return [(c, False) for c in normal]

    recent_categories = _recent_report_categories(paths, cfg, profile)
    explore_idx = None
    for i, c in enumerate(remaining):
        if _category_of(c.title, profile) not in recent_categories:
            explore_idx = i
            break

    if explore_idx is None:
        # 所有候选类别都已经在最近几轮出现过，不强行制造探索，退化成
        # 正常按置信度选（跟名额 = normal_n + 1 时的默认行为一致）。
        return [(c, False) for c in normal] + [(remaining[0], False)]

    explore_pick = remaining[explore_idx]
    return [(c, False) for c in normal] + [(explore_pick, True)]


# ────────────────────────── 每日流程封装（供 cron / CLI 复用） ──────────────────────────


def run_daily_cycle(
    paths, cfg, profile, memory_store, *,
    llm_helper: Optional[Callable[[str], str]] = None,
    web_search_fn=None,
) -> dict[str, Any]:
    """`sys:growth_advisor_daily` 与 `/growth scan` 共用的主流程：
    信号扫描 -> 候选生成 -> （置信度达标的）Top-N 生成调研报告 ->
    （P2 新增）按 4.2 节节流规则决定要不要推送一条通知。

    P3：`llm_helper` 只有在 `cfg.llm_signal_augment_enabled=True` 时才会
    真正传给 `growth_signal_scan`（默认 False，零 LLM 成本）——即使调用方
    在有 agent 上下文的场景下总是能拿到 `llm_helper`，是否使用仍然由
    这个显式开关控制，不因为"恰好有"就默认用上。

    `web_search_fn`：[next_doc/growth_advisor_cron_search_and_status_
    history_plan.md 方向一] 可选，签名与 `generate_growth_report()` 的
    同名参数一致。只有在 `cfg.cron_triggered_active_search_enabled=True`
    且本参数非 `None` 时才会真正触发 cron 路径的主动检索（见
    `_maybe_run_cron_triggered_active_search()`）；不传时行为与本机制
    引入前完全一致，调用方（cron job / `/growth scan`）是否具备检索
    工具、要不要打开这条路径，仍然由调用方自己决定。
    """
    if not getattr(cfg, "enabled", True):
        return {"skipped": True, "reason": "growth_advisor disabled"}

    scan_llm_helper = llm_helper if getattr(cfg, "llm_signal_augment_enabled", False) else None
    growth_signal_scan(paths, profile, memory_store, llm_helper=scan_llm_helper)
    # [方向 3] goal_backlog 拿不到（非 daemon 上下文等）时安全退化为 None，
    # growth_candidate_derive 内部行为等价于改动前（只用 memory 信号）。
    goal_backlog = _load_goal_backlog_safely(paths)
    new_candidates = growth_candidate_derive(paths, cfg, profile, goal_backlog=goal_backlog, llm_helper=llm_helper)

    # [P5-3] 除了看板上"手动添加/确认"两个触发点，cron 每日流程本身也会
    # 让主题"转正"（`_update_keyword_learning_streaks` 的自动确认路径），
    # 这里顺带给本轮新增候选里还没分类过的主题补一次归类，开关关闭或没
    # 有 llm_helper 时是零成本空操作（`maybe_classify_topic_category`
    # 内部已经判断）。
    if getattr(cfg, "topic_category_llm_enabled", False) and llm_helper is not None:
        effective_keywords = _effective_topic_keywords(profile)
        for c in new_candidates:
            info = effective_keywords.get(c.title)
            kws = list(info.get("keywords") or []) if isinstance(info, dict) else []
            maybe_classify_topic_category(profile, c.title, kws, cfg, llm_helper=llm_helper, paths=paths)

    # [P5-6] 默认关闭时，`_select_candidates_for_reports` 的行为跟改动前
    # 完全一致（纯按置信度取 Top-N）；开启后至多把其中 1 个名额换成
    # "探索位"（最近几轮报告没出现过的类别）。
    selected = _select_candidates_for_reports(new_candidates, cfg, paths, profile)
    top = [c for c, _ in selected]
    # [P4-4] report_quality_llm_enabled 独立于 llm_signal_augment_enabled：
    # 默认仍是零成本模板报告，只有显式打开这个开关才会在生成报告正文时
    # 用 llm_helper 换取更高信息密度（同样是 opt-in，不因为"恰好有" llm_helper
    # 就默认用上）。
    report_llm_helper = llm_helper if getattr(cfg, "report_quality_llm_enabled", False) else None
    reports = []
    for c, is_exploration in selected:
        # [方向 7] 报告质量自动闭环：全局模板路径下，如果这个方向的
        # 报告已经被反复标"内容太笼统"、且当前上下文确实拿得到
        # `llm_helper`（比如 cron 触发），临时把这一份报告升级为 LLM
        # 生成——不修改全局 `report_quality_llm_enabled` 开关本身，
        # 只影响这一次调用，其余方向仍走原来的路径。
        per_report_llm_helper = report_llm_helper
        quality_auto_upgraded = False
        if per_report_llm_helper is None and llm_helper is not None:
            if _should_auto_upgrade_report_quality(paths, c, cfg):
                per_report_llm_helper = llm_helper
                quality_auto_upgraded = True
        reports.append(generate_growth_report(
            paths, c, llm_helper=per_report_llm_helper, is_exploration=is_exploration,
            profile=profile, cfg=cfg, quality_auto_upgraded=quality_auto_upgraded,
        ))

    candidates_by_id = {c.candidate_id: c for c in top}
    freq = getattr(cfg, "notification_frequency", "daily")
    if freq == "weekly_digest":
        notification = _maybe_dispatch_weekly_digest(paths, cfg, profile)
    else:
        notification = _maybe_dispatch_notification(paths, cfg, candidates_by_id, reports, profile, memory_store=memory_store)

    # [v4 N1] 每日流程收尾时顺带记一条全局健康度快照 + 做一次降采样
    # 压缩，跟 growth_topic_trend 的既有节奏一致（每天一次，不影响主
    # 流程返回结构）。快照/压缩失败不应该影响本轮扫描/候选生成/推送
    # 已经产出的结果，静默降级。
    try:
        _record_health_snapshot(paths, cfg, profile, memory_store)
        compact_health_trend_storage(paths)
        # [growth_advisor_autonomy_deepening_plan_v2.md 方向 3] 饱和度
        # 趋势的降采样压缩跟健康度趋势同一个节奏，不需要单独的调度点。
        compact_pursuit_saturation_trend_storage(paths)
    except Exception:
        pass

    # [BUGFIX：目标看板"完成率趋势"长期无数据] 之前 D.1 的
    # `record_objective_completion_snapshot()` 只挂在 HTTP 路由
    # `POST /v1/growth/scan`（api/routes.py::post_growth_scan）里调用，
    # 而 `sys:growth_advisor_daily` 这个 cron job 的 run_mode 是
    # "message"——到点后是把 task_template 文本（"/growth scan"）作为一
    # 条消息投递给 Agent 主循环，由 Agent 在对话轮次里把它当 slash 命令
    # 执行，实际调用的是 `cli/commands/growth_cmd.py::handle_growth_cmd`
    # 这条完全独立的代码路径，同样调用本函数 `run_daily_cycle()`，但从
    # 未经过那个 HTTP 路由。结果是：cron 每天真正触发的那次扫描永远不会
    # 记录 Objective 完成率快照，只有用户在看板上点"立即为我看看"（直接
    # 打这个 HTTP 接口）才会记一条——这正是"daemon 已经跑了很多天，完成
    # 率趋势却一直没有数据"的根因。
    # 修复：把快照记录移到这里，跟上面的健康度快照放在同一个"每日流程
    # 收尾"位置，这样无论调用方是 cron message、CLI `/growth scan`、还是
    # 看板 HTTP 路由，只要真正跑过一次 `run_daily_cycle()` 就会记一条，
    # 不再依赖某一条特定的调用路径。routes.py 里原来的调用点已同步移除，
    # 避免看板手动触发时因为两处都调用而重复记两条同一天的快照。
    try:
        from mini_agent.evolution.objective_trend import (
            record_objective_completion_snapshot,
            compact_objective_completion_trend_storage,
        )
        record_objective_completion_snapshot(paths)
        compact_objective_completion_trend_storage(paths)
    except Exception:
        pass

    # [growth_advisor_improvement_plan_v4.md 方向二 2.2 节 / N3] 默认
    # 关闭——这会实际修改 agent_config.json，属于有外部效果的写操作，
    # 只在用户显式打开时才会触发。跟健康度快照收尾一样静默降级，不
    # 影响本轮已经产出的扫描/候选生成/推送结果。
    if getattr(cfg, "sync_confirmed_topics_to_tech_radar_enabled", False):
        try:
            sync_confirmed_topics_to_tech_radar(paths, profile, cfg)
        except Exception:
            pass

    # [next_doc/growth_advisor_cron_search_and_status_history_plan.md
    # 方向一] 默认关闭；`_maybe_run_cron_triggered_active_search()` 内部
    # 已经做了完整的 try/except 兜底，这里不需要再包一层。
    cron_active_search = _maybe_run_cron_triggered_active_search(
        paths, cfg, new_candidates,
        llm_helper=llm_helper, web_search_fn=web_search_fn, profile=profile,
    )

    return {
        "skipped": False,
        "new_candidates": [c.candidate_id for c in new_candidates],
        "reports": [r.report_id for r in reports],
        "notification": notification,
        "cron_active_search": cron_active_search,
    }


# ────────── N3：关键词表 → tech_radar 种子同步（方向二 2.2 节）──────────

def sync_confirmed_topics_to_tech_radar(paths, profile, cfg) -> int:
    """把成长顾问里"已确认"（`confirmed_by_user=True`）且当前未被隐藏的
    主题关键词，同步进 `TechRadarConfig.keywords`，供
    `tech_radar_search.py` 的主动检索种子池使用。

    - 只同步 confirmed 状态的主题（内置主题 / `user_added` / 已转正的
      `llm_learned`），待确认的候选主题不同步——避免把还在观察期的
      候选也拉去消耗外部检索配额，对齐成长顾问一贯"证据不够强就不
      推荐"的克制原则。
    - 幂等：`TechRadarConfig.keywords` 里已存在的关键词（大小写不敏感）
      不重复添加，见 `config_catalog.apply_list_seed_merge()`。
    - 不做反向删除：用户在成长顾问里隐藏/删除一个主题，不会自动从
      tech_radar 种子池移除——两者语义不同（"不想再被成长顾问追踪"
      不等于"不想再关注外部世界动态"），删除 tech_radar 种子仍然需要
      用户去配置里手动做。
    - 严格走跟"看板保存配置"完全一致的写入路径（`config_catalog.
      apply_list_seed_merge()` + `write_config_file()`，2.5 节风险项 1
      的明确要求），不直接拼 JSON 写文件。
    - 由调用方（`run_daily_cycle()`）保证只在
      `cfg.sync_confirmed_topics_to_tech_radar_enabled` 开启时调用——本
      函数内部不重复判断这个开关（跟 `_record_health_snapshot()` 一样
      "不感知调用时机、只管做事"的定位），但会做一次 `paths` 是否具备
      `project_root` 的防御性判断。

    返回本次新增的种子数量（0 表示没有新增，包括"没有已确认主题"和
    "已确认主题的关键词都已经在种子池里了"两种情况，调用方不需要区分）。
    异常向上抛出，由调用方决定如何静默降级。
    """
    project_root = getattr(paths, "project_root", None)
    if project_root is None:
        return 0

    effective = _effective_topic_keywords(profile)
    confirmed_keywords: list[str] = []
    for topic, info in effective.items():
        if not info.get("confirmed_by_user"):
            continue
        confirmed_keywords.extend(info.get("keywords") or [])
    if not confirmed_keywords:
        return 0

    from mini_agent.config import config_catalog as _cc
    from mini_agent.config.loader import _load_config_file

    config_path = project_root / "agent_config.json"
    raw_file_cfg = _load_config_file(config_path) if config_path.exists() else {}

    new_raw, added = _cc.apply_list_seed_merge(
        raw_file_cfg, "tech_radar", "keywords", confirmed_keywords,
    )
    if added > 0:
        _cc.write_config_file(config_path, new_raw)
    return added


# ────────── N4：外部资讯命中 → 展示补充信号 / 报告背景（方向二 2.3/2.4 节）──────────

def _external_signal_matching_pages(
    paths, topic: str, keywords: list[str], *, window_days: int = 30,
) -> list:
    """[growth_advisor_improvement_plan_v4.md 方向二 2.3 节 /
    growth_advisor_research_quality_plan.md 阶段 1] 找出最近
    `window_days` 天内，wiki 里 `source_kind` 属于
    `external_watch`/`external_search`（外部检索/外部观察产出，见
    `wiki/world_writer.py` 的 `EXTERNAL_WATCH_SOURCE_KIND`/
    `EXTERNAL_SEARCH_SOURCE_KIND`）、标题/正文命中该主题关键词的页面，
    按更新时间倒序返回页面对象列表。

    `_external_signal_count_for_topic()`（纯计数，参与展示）和
    `_external_signal_excerpts_for_topic()`（摘录，参与报告生成）共享
    这段"找到命中页面"的逻辑，避免同一套过滤条件维护两份。

    复用 `growth_signal_scan()` 对记忆做关键词匹配的同一套简单规则
    （小写子串匹配，不引入新的匹配算法/embedding）。**只读聚合，不改变
    任何置信度计算**——这是本函数存在的唯一边界：外部世界的资讯量本身
    跟"用户自己是否感兴趣"没有必然关系，只能作为展示补充，不能参与
    `_confidence_from_evidence()` 这类判断用户投入程度的计算。

    单个页面解析失败（frontmatter 缺失/格式错误等，`wiki/quarantine.py`
    已经有独立的隔离区机制处理这类问题）时静默跳过，不影响其它页面的
    统计，也不在这里重复记录隔离区问题（那是 `wiki/stats.py::
    compute_stats()` 的职责，本函数只是"顺手找一下"，不承担 wiki 健康度
    治理的职责）。
    """
    if not keywords:
        return []
    try:
        from mini_agent.wiki.indexer import discover_pages
        from mini_agent.wiki.parser import parse_page
        from mini_agent.wiki.world_writer import (
            EXTERNAL_WATCH_SOURCE_KIND, EXTERNAL_SEARCH_SOURCE_KIND,
        )
    except Exception:
        return []

    cutoff_date = (
        __import__("datetime").date.today()
        - __import__("datetime").timedelta(days=window_days)
    ).isoformat()
    lowered_keywords = [k.lower() for k in keywords if k]
    matched: list = []
    try:
        page_paths = discover_pages(paths)
    except Exception:
        return []
    for md_path in page_paths:
        try:
            page = parse_page(md_path)
        except Exception:
            continue
        source_kind = str(page.raw_frontmatter.get("source_kind") or "")
        if source_kind not in (EXTERNAL_WATCH_SOURCE_KIND, EXTERNAL_SEARCH_SOURCE_KIND):
            continue
        # created/updated 是 date.today().isoformat() 生成的 "YYYY-MM-DD"
        # 字符串（见 wiki/writer.py），字符串字典序比较等价于按日期比较，
        # 不需要额外解析成 datetime 对象。取 updated 优先（更新过的页面
        # 说明最近仍有活跃信号），没有 updated 时退回 created。
        page_date = page.updated or page.created
        if page_date and page_date < cutoff_date:
            continue
        haystack = " ".join([page.id, page.body[:2000]] + list(page.tags)).lower()
        if any(kw in haystack for kw in lowered_keywords):
            matched.append(page)
    matched.sort(key=lambda p: p.updated or p.created or "", reverse=True)
    return matched


def _external_signal_count_for_topic(
    paths, topic: str, keywords: list[str], *, window_days: int = 30,
) -> int:
    """粗略统计命中页面数量，纯粹是 `_external_signal_matching_pages()`
    结果的 `len()`——行为跟重构前完全一致，见该函数 docstring 了解
    过滤规则细节。"""
    return len(_external_signal_matching_pages(paths, topic, keywords, window_days=window_days))


def _external_signal_excerpts_for_topic(
    paths, topic: str, keywords: list[str], *, window_days: int = 30, max_excerpts: int = 2,
) -> list[dict[str, str]]:
    """[growth_advisor_research_quality_plan.md 阶段 1] 取命中页面里
    最近的 `max_excerpts` 条，各截取正文前 ~150 字作为摘录，供报告生成
    时真正"看到内容"而不只是"知道有几条"。

    返回 `[{"id": 页面 id, "date": 更新/创建日期, "excerpt": 摘录}, ...]`，
    按最近更新时间倒序。任何页面解析失败已经在
    `_external_signal_matching_pages()` 里被跳过，这里不需要重复处理。
    """
    pages = _external_signal_matching_pages(paths, topic, keywords, window_days=window_days)
    out: list[dict[str, str]] = []
    for page in pages[:max_excerpts]:
        excerpt = " ".join((page.body or "").split())[:150]
        out.append({
            "id": page.id,
            "date": page.updated or page.created or "",
            "excerpt": excerpt,
        })
    return out


# ────────── cron 无人值守路径的主动检索预算调度（next_doc/
# growth_advisor_cron_search_and_status_history_plan.md 方向一）──────────
# `run_daily_cycle()` 此前完全不触发 N4/方向一的主动检索——覆盖面只能靠
# 人工触发 `/growth report`。这里借用 `tech_radar_search.py` 同一套
# "控频率+控预算"节流思路（每天最多处理个位数种子），但预算维度换成
# "cron 每天最多对几个候选触发一次"，状态记账复用既有的
# `growth_advisor_state.json`（跟 `notify_count_today` 同一个自然日
# 计数器风格，见 `_maybe_dispatch_notification`）。

CRON_ACTIVE_SEARCH_STATE_DATE_KEY = "cron_active_search_date"
CRON_ACTIVE_SEARCH_STATE_COUNT_KEY = "cron_active_search_count_today"


def _maybe_run_cron_triggered_active_search(
    paths, cfg, candidates: list[GrowthCandidate], *, llm_helper, web_search_fn, profile=None,
) -> Optional[dict]:
    """[方向一] `run_daily_cycle()` 收尾时调用：从本轮候选里挑出"证据数
    最高但从未有过任何外部背景"的若干个，触发定向检索。

    - `cfg.cron_triggered_active_search_enabled` 为 `False`（默认）、
      `llm_helper`/`web_search_fn` 任一缺失时直接跳过——跟方向一手动
      触发路径一样，检索能力必须由调用方注入，本函数不导入
      `tools/builtin.py::web_search`。
    - 每个自然日最多处理 `cfg.cron_triggered_active_search_daily_limit`
      个候选（默认 1），计数落盘在 `growth_advisor_state.json`，与
      `notify_count_today` 同一自然日翻转规则，互不共享计数。
    - 候选选取：按 `confidence` 降序遍历 `candidates`，跳过
      `_external_signal_count_for_topic()` 命中数 > 0 的（已经有外部
      背景，不重复劳动，延续"只在完全没有素材时才补"的既有边界）。
    - 复用 `_active_search_excerpts_for_topic()` 完成"检索→LLM 抽取→
      落盘 wiki"，不重新实现一套；无论这次检索是否真的抽出内容，都会
      占用当天的预算名额（避免同一个屡查屡空的候选反复重试、把当天\n      预算耗在它一个人身上——是否值得继续查它，交给\n      `tech_radar_search.py` 的质量反馈闭环处理，本函数不做重试判断）。
    - 任何一步异常都不应该打断 `run_daily_cycle` 主流程，整体
      try/except + log_exception 兜底。
    """
    if not getattr(cfg, "cron_triggered_active_search_enabled", False):
        return None
    if llm_helper is None or web_search_fn is None:
        return None
    if not candidates:
        return None

    try:
        daily_limit = max(0, int(getattr(cfg, "cron_triggered_active_search_daily_limit", 1)))
        if daily_limit <= 0:
            return None

        state = _load_growth_state(paths)
        today = _today_str()
        if state.get(CRON_ACTIVE_SEARCH_STATE_DATE_KEY) != today:
            state[CRON_ACTIVE_SEARCH_STATE_DATE_KEY] = today
            state[CRON_ACTIVE_SEARCH_STATE_COUNT_KEY] = 0
        remaining = daily_limit - int(state.get(CRON_ACTIVE_SEARCH_STATE_COUNT_KEY, 0))
        if remaining <= 0:
            return None

        effective_keywords = _effective_topic_keywords(profile) if profile is not None else {}
        ranked = sorted(candidates, key=lambda c: -c.confidence)

        triggered: list[str] = []
        for candidate in ranked:
            if remaining <= 0:
                break
            info = effective_keywords.get(candidate.title)
            keywords = list(info.get("keywords") or []) if isinstance(info, dict) else []
            try:
                if _external_signal_count_for_topic(paths, candidate.title, keywords) > 0:
                    continue
            except Exception:
                continue

            excerpts = _active_search_excerpts_for_topic(
                paths, candidate, keywords,
                web_search_fn=web_search_fn, llm_helper=llm_helper,
                max_calls=int(getattr(cfg, "report_active_search_max_calls", 1) or 1),
            )
            remaining -= 1
            state[CRON_ACTIVE_SEARCH_STATE_COUNT_KEY] = int(state.get(CRON_ACTIVE_SEARCH_STATE_COUNT_KEY, 0)) + 1
            if excerpts:
                triggered.append(candidate.candidate_id)

        _save_growth_state(paths, state)
        if not triggered:
            return {"triggered_candidate_ids": []}
        return {"triggered_candidate_ids": triggered}
    except Exception as exc:
        from mini_agent.errors import log_exception
        log_exception(exc, where="mini_agent.growth_advisor._maybe_run_cron_triggered_active_search")
        return None


# ────────── 方向一：真正的主动检索（growth_advisor_active_search_and_
# lifecycle_plan.md），阶段一/二见 next_doc/growth_advisor_autonomous_
# search_and_material_improvement_plan.md ──────────

_DEFAULT_ACTIVE_SEARCH_MAX_EXCERPTS = 3
_DEFAULT_ACTIVE_SEARCH_EXCERPT_CHARS = 200
_DEFAULT_ACTIVE_SEARCH_FALLBACK_EXCERPT_CHARS = 150


def _excerpts_from_extracted_candidates(
    query: str, entities: list, facts: list, *, max_excerpts: int,
) -> list[dict[str, str]]:
    """[阶段一] 把已经抽取出的结构化 `EntityCandidate`/`FactCandidate` 转成
    报告可直接引用的摘录，而不是让报告消费未经处理的原始检索文本。这些
    候选本身就是 LLM 已经从检索结果里提炼出的独立信息点，信息密度比截断
    原始文本更高，`id` 也能精确到具体是哪个实体/事实，便于报告 prompt 里
    要求的"标注来源"落到实处。

    实体优先于事实排列（实体通常是更适合展开讲的主题性信息），超过
    `max_excerpts` 的部分丢弃——上限本身已经由调用方控制候选数量。
    """
    out: list[dict[str, str]] = []
    today = _today_str()
    for e in entities:
        if len(out) >= max_excerpts:
            break
        text = f"{e.name}：{e.description}".strip()
        out.append({
            "id": f"active_search:{query}#entity:{e.name}",
            "date": today,
            "excerpt": text[:_DEFAULT_ACTIVE_SEARCH_EXCERPT_CHARS],
        })
    for idx, f in enumerate(facts, 1):
        if len(out) >= max_excerpts:
            break
        out.append({
            "id": f"active_search:{query}#fact:{idx}",
            "date": today,
            "excerpt": f.statement[:_DEFAULT_ACTIVE_SEARCH_EXCERPT_CHARS],
        })
    return out


def _run_single_active_search_query(
    paths, candidate: "GrowthCandidate", query: str, *,
    web_search_fn, llm_helper, max_results: int, max_excerpts: int,
) -> list[dict[str, str]]:
    """对一个具体的查询角度执行一次"检索 → 抽取 → 落盘 pending 队列 →
    构造摘录"的完整流程。任何一步失败都静默返回空列表，不抛出——供调用方
    在多角度循环里对每个角度独立容错，一个角度失败不影响其它角度。
    """
    if web_search_fn is None or llm_helper is None:
        return []
    try:
        raw_text = web_search_fn(query, max_results=max_results)
    except Exception:
        return []
    if not raw_text or not str(raw_text).strip():
        return []

    try:
        from mini_agent.external_input.tech_radar_search import (
            _build_search_extraction_prompt, _parse_search_extraction_response, _URL_RE,
            _MAX_SOURCE_URLS_PER_SEED,
        )
        from mini_agent.history.world_extraction import EntityCandidate, FactCandidate
        from mini_agent.wiki.world_writer import EXTERNAL_SEARCH_SOURCE_KIND, queue_entities, queue_facts
    except Exception:
        return []

    prompt = _build_search_extraction_prompt([(query, raw_text)])
    try:
        raw_response = llm_helper(prompt)
    except Exception:
        return []
    parsed = _parse_search_extraction_response(raw_response)
    item = parsed.get(1)
    if item is None:
        return []

    source_urls = _URL_RE.findall(raw_text)[:_MAX_SOURCE_URLS_PER_SEED]
    source_entries = [f"growth_advisor_active_search:{candidate.candidate_id}:{query}"] + source_urls

    entities: list = []
    for e in (item.get("entities") or []):
        if not isinstance(e, dict):
            continue
        c = EntityCandidate.from_dict(e)
        if c.is_meaningful:
            entities.append(c)
    facts: list = []
    for f in (item.get("facts") or []):
        if not isinstance(f, dict):
            continue
        c = FactCandidate.from_dict(f)
        if c.is_meaningful:
            facts.append(c)
    if entities:
        queue_entities(paths, entities, source_entries=source_entries, source_kind=EXTERNAL_SEARCH_SOURCE_KIND)
    if facts:
        queue_facts(paths, facts, source_entries=source_entries, source_kind=EXTERNAL_SEARCH_SOURCE_KIND)

    # [阶段一] 优先用已抽取的结构化候选构造摘录；抽取结果为空（比如检索
    # 结果是纯噪音，模型没抽出任何有意义的实体/事实）时才退回原来的
    # "原始文本截断"摘录，保证任何情况下都不会比改动前拿到更少的信息。
    if entities or facts:
        return _excerpts_from_extracted_candidates(query, entities, facts, max_excerpts=max_excerpts)
    excerpt = " ".join(str(raw_text).split())[:_DEFAULT_ACTIVE_SEARCH_FALLBACK_EXCERPT_CHARS]
    return [{"id": f"active_search:{query}", "date": _today_str(), "excerpt": excerpt}]


def _build_active_search_queries(candidate: "GrowthCandidate", keywords: list[str], *, max_calls: int) -> list[str]:
    """[阶段二] 构造最多 `max_calls` 个查询角度：第一个沿用改动前的
    "标题 + 第一个关键词"（`max_calls<=1` 时行为与改动前完全一致），之后
    依次用关键词表里后续的关键词各拼一个查询。关键词数量不够时不重复
    拼凑同一个角度，宁可少查也不做无意义的重复调用。
    """
    if max_calls < 1:
        max_calls = 1
    if not keywords:
        return [candidate.title]
    queries = [f"{candidate.title} {kw}" for kw in keywords[:max_calls]]
    # 去重但保持顺序（关键词表理论上不应有重复，这里只是防御性处理）
    seen: set = set()
    out = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out or [candidate.title]


def _active_search_excerpts_for_topic(
    paths,
    candidate: "GrowthCandidate",
    keywords: list[str],
    *,
    web_search_fn,
    llm_helper,
    max_results: int = 5,
    max_calls: int = 1,
    max_excerpts_per_call: int = _DEFAULT_ACTIVE_SEARCH_MAX_EXCERPTS,
) -> list[dict[str, str]]:
    """[growth_advisor_active_search_and_lifecycle_plan.md 方向一] 被动
    扫描（`_external_signal_matching_pages()`）没有可用素材时，现查一次
    （或多次，见下方 `max_calls`）并落一份 wiki 页面供后续复用。任何一步
    失败都静默返回空列表，退回"没有外部背景"的原有路径，不让检索失败拖垮
    报告生成本身。

    复用 `external_input/tech_radar_search.py` 同一套 `EntityCandidate`/
    `FactCandidate` 抽取管道与 `EXTERNAL_SEARCH_SOURCE_KIND` 落盘标记，
    `source_entries` 前缀换成 `growth_advisor_active_search:<candidate_
    id>` 以便跟巡检产生的页面区分来源。

    `max_calls`：[阶段二，growth_advisor_autonomous_search_and_material_
    improvement_plan.md] 默认 1（与改动前行为完全一致，只查一个角度）。
    大于 1 时，会用关键词表里后续的关键词各追加一个查询角度（见
    `_build_active_search_queries()`），对应 `config/models.py::
    GrowthAdvisorConfig.report_active_search_max_calls` 这个此前预留但
    未消费的字段。多次查询各自独立容错，某一个角度失败不影响其它角度；
    最终摘录按 `id` 去重合并，受 `max_excerpts_per_call`（此时语义是
    "摘录总数上限"）约束，避免随 `max_calls` 增大让报告 prompt 线性膨胀。
    """
    if web_search_fn is None or llm_helper is None:
        return []
    queries = _build_active_search_queries(candidate, keywords, max_calls=max_calls)

    merged: list[dict[str, str]] = []
    seen_ids: set = set()
    for query in queries:
        if len(merged) >= max_excerpts_per_call:
            break
        remaining = max_excerpts_per_call - len(merged)
        excerpts = _run_single_active_search_query(
            paths, candidate, query,
            web_search_fn=web_search_fn, llm_helper=llm_helper,
            max_results=max_results, max_excerpts=remaining,
        )
        for ex in excerpts:
            if ex["id"] in seen_ids:
                continue
            seen_ids.add(ex["id"])
            merged.append(ex)
            if len(merged) >= max_excerpts_per_call:
                break
    return merged


def growth_topic_map(paths) -> list[dict]:
    """跨候选的主题聚合视图（方案第 6 节"能力地图"聚合，对齐
    `self_model_snapshot.py` 的思路——只是问题从"Agent 自己的能力弱项
    清单变长变短"换成了"用户在每个成长方向上的推进轨迹"）。

    按 `dedupe_key`（归一化标题）聚合 backlog 里**全部**历史候选（包括
    因 dismiss 冷却期结束后重新生成、标题相同但 candidate_id 不同的多
    条记录），得到每个主题的：
        - 当前状态（取 updated_at 最新的一条）
        - 历史累计被采纳/被忽略次数（一个主题可能经历多轮 dismiss ->
          冷却 -> 重新生成 -> 再次 dismiss/accepted）
        - 历史出现过的最高置信度（衡量该方向证据积累的峰值，不因为
          某次 dismiss 后置信度被打折而"倒退"）
        - 首次出现时间 / 最近更新时间

    只做聚合展示，不做任何预测/排序推荐——聚合结果按 `updated_at` 倒序
    返回，供看板/CLI 直接渲染成一张列表，不引入新的落盘文件。
    """
    all_c = GrowthBacklog(paths).load_all()
    if not all_c:
        return []

    groups: dict[str, list[GrowthCandidate]] = {}
    for c in all_c:
        groups.setdefault(c.dedupe_key(), []).append(c)

    rows: list[dict] = []
    for key, items in groups.items():
        items_sorted = sorted(items, key=lambda c: c.updated_at)
        latest = items_sorted[-1]
        rows.append(
            {
                "topic": latest.title,
                "candidate_id": latest.candidate_id,
                "current_status": latest.status,
                "current_confidence": latest.confidence,
                "peak_confidence": max(c.confidence for c in items),
                "times_accepted": sum(1 for c in items if c.status == STATUS_ACCEPTED),
                "times_dismissed": sum(1 for c in items if c.status == STATUS_DISMISSED),
                "occurrences": len(items),
                "first_seen_at": min(c.created_at for c in items),
                "last_updated_at": latest.updated_at,
                # [P4-6] 简单的证据数走势（最近若干轮扫描的快照点），
                # 供看板画一条走势线/文字趋势，不是新的权威数据源，纯粹
                # 从 growth_topic_trend.jsonl 里按 dedupe_key 查出来。
                "evidence_trend": _topic_trend_series(paths, key),
            }
        )

    rows.sort(key=lambda r: -r["last_updated_at"])
    return rows


def growth_topic_lifecycle(paths, dedupe_key: str, *, goal_backlog=None) -> list[dict]:
    """[growth_advisor_active_search_and_lifecycle_plan.md 方向二] 某个
    成长方向的完整生命周期时间线：发现 → 每次生成调研报告 → 每次采纳/
    忽略 → 落地成 Goal → Goal 当前状态。

    纯只读聚合，不新增落盘文件，从 `growth_backlog.jsonl`、
    `growth_reports_index.jsonl`（含归档）、`growth_feedback_ledger.
    jsonl`、`goal_backlog`（可选，缺失时静默跳过 Goal 相关事件）四处
    现有数据拼出来，按 `ts` 正序返回，供看板/CLI 渲染成一条时间线。
    """
    all_c = GrowthBacklog(paths).load_all()
    items = [c for c in all_c if c.dedupe_key() == dedupe_key]
    if not items:
        return []

    events: list[dict] = []

    first_created = min(c.created_at for c in items)
    events.append({
        "stage": "discovered",
        "ts": first_created,
        "label": f"首次被信号扫描发现：{items[0].title}",
        "detail": "",
    })

    candidate_ids = {c.candidate_id for c in items}
    report_ids = {c.report_id for c in items if c.report_id}
    if report_ids:
        for report in list_reports(paths, include_archived=True):
            if report.report_id in report_ids:
                events.append({
                    "stage": "report_generated",
                    "ts": report.created_at,
                    "label": f"生成调研报告：{report.summary[:40]}",
                    "detail": report.report_id,
                })

    ledger = GrowthFeedbackLedger(paths).all_entries()
    for entry in ledger:
        if entry.get("candidate_id") not in candidate_ids:
            continue
        action = entry.get("action")
        if action == STATUS_ACCEPTED:
            events.append({
                "stage": "accepted", "ts": entry.get("ts", 0.0),
                "label": "用户采纳了这个方向", "detail": "",
            })
        elif action == STATUS_DISMISSED:
            reason = entry.get("reason") or ""
            events.append({
                "stage": "dismissed", "ts": entry.get("ts", 0.0),
                "label": "用户忽略了这个方向" + (f"（{reason}）" if reason else ""),
                "detail": reason,
            })

    linked = next((c for c in items if c.linked_goal_id), None)
    if linked is not None:
        events.append({
            "stage": "goal_linked", "ts": linked.updated_at,
            "label": "落地成一个具体目标（Goal）", "detail": linked.linked_goal_id,
        })
        if goal_backlog is not None:
            try:
                goal = goal_backlog.get(linked.linked_goal_id)
            except Exception:
                goal = None
            if goal is not None:
                _TERMINAL_STATUSES = ("completed", "abandoned", "failed", "cancelled")

                def _goal_status_event(status: str, ts: float) -> Optional[dict]:
                    if status == "completed":
                        return {"stage": "goal_completed", "ts": ts, "label": "目标已完成", "detail": ""}
                    if status in ("abandoned", "failed", "cancelled"):
                        return {"stage": "goal_stalled", "ts": ts, "label": f"目标已停滞（{status}）", "detail": status}
                    if status == "active":
                        return {"stage": "goal_active", "ts": ts, "label": "目标进行中", "detail": ""}
                    return None

                # [next_doc/growth_advisor_cron_search_and_status_history_
                # plan.md 方向三] 优先用 `status_history` 还原完整往复：
                # 一个 Goal 完成过一次又被重新打开，能看出"goal_completed
                # -> goal_reopened -> ..."而不是只剩最后一次 set_status
                # 之后的"当前状态"。旧数据/尚未经历过一次显式 set_status
                # 的 Goal 没有历史（空列表），退回原来的"只看当前状态"
                # 兜底路径，保持向后兼容。
                history = list(getattr(goal, "status_history", None) or [])
                if history:
                    prev_status = None
                    for entry in history:
                        if not isinstance(entry, dict):
                            continue
                        status = entry.get("status")
                        ts = entry.get("at") or linked.updated_at
                        if (
                            status == "active"
                            and prev_status is not None
                            and prev_status in _TERMINAL_STATUSES
                        ):
                            events.append({
                                "stage": "goal_reopened", "ts": ts,
                                "label": f"目标重新被打开（此前状态：{prev_status}）",
                                "detail": prev_status,
                            })
                        else:
                            evt = _goal_status_event(status, ts)
                            if evt is not None:
                                events.append(evt)
                        prev_status = status
                else:
                    status = getattr(goal, "status", None)
                    ts = getattr(goal, "last_touched_at", None) or getattr(goal, "created_at", linked.updated_at)
                    evt = _goal_status_event(status, ts)
                    if evt is not None:
                        events.append(evt)

    events.sort(key=lambda e: e["ts"])
    return events


def monthly_retrospective_summary(paths) -> dict[str, Any]:
    """月度成长复盘统计。P2 在 P1 的数量统计基础上新增 `acceptance_rate`
    （采纳率）与按候选标题聚合的采纳/忽略排行——对应方案第 6 节"推荐命中
    率"这类自我评估指标；跨候选的能力地图聚合仍留给 P3。"""
    backlog = GrowthBacklog(paths)
    all_c = backlog.load_all()
    ledger = GrowthFeedbackLedger(paths).all_entries()
    accepted = sum(1 for c in all_c if c.status == STATUS_ACCEPTED)
    dismissed = sum(1 for c in all_c if c.status == STATUS_DISMISSED)
    pending = sum(1 for c in all_c if c.status == STATUS_PENDING)
    decided = accepted + dismissed
    acceptance_rate = round(accepted / decided, 3) if decided else None

    accepted_topics: dict[str, int] = {}
    dismissed_topics: dict[str, int] = {}
    for c in all_c:
        if c.status == STATUS_ACCEPTED:
            accepted_topics[c.title] = accepted_topics.get(c.title, 0) + 1
        elif c.status == STATUS_DISMISSED:
            dismissed_topics[c.title] = dismissed_topics.get(c.title, 0) + 1

    top_accepted = sorted(accepted_topics.items(), key=lambda kv: -kv[1])[:5]
    top_dismissed = sorted(dismissed_topics.items(), key=lambda kv: -kv[1])[:5]

    # [反馈粒度细化] "方向没错、报告没写好"单独列出来，不混进
    # top_dismissed_topics（那份排行反映的是"用户对这个方向本身的态度"，
    # 报告质量问题是另一个维度的信号，混在一起会让人误以为这些方向也
    # 不受欢迎）。
    report_quality_counts = _report_quality_dismiss_counts(paths)
    top_report_quality_flags = sorted(report_quality_counts.items(), key=lambda kv: -kv[1])[:5]

    return {
        "total_candidates": len(all_c),
        "accepted": accepted,
        "dismissed": dismissed,
        "pending": pending,
        "acceptance_rate": acceptance_rate,
        "feedback_events": len(ledger),
        "reports_generated": len(list_reports(paths, include_archived=True)),
        "top_accepted_topics": top_accepted,
        "top_dismissed_topics": top_dismissed,
        # [反馈粒度细化] 报告质量待改进的方向排行（dismiss 原因=
        # report_not_useful），不参与任何置信度计算，仅供参考。
        "report_quality_flags_total": sum(report_quality_counts.values()),
        "top_report_quality_flags": top_report_quality_flags,
        "topic_map": growth_topic_map(paths),
    }


# ────────────────────────── P3（用户反馈追加）：诊断快照 ──────────────────────────
# 真实用户反馈："运行了一天，成长顾问里的数据都是 0"——排查下来往往不是
# bug，而是"关键词表没命中"/"证据数没达标"/"cron 没跑过"这类看不见的
# 中间状态：候选数=0 本身不区分"扫描过但没匹配到"和"压根没扫描过"。这个
# 函数把决定"为什么是 0"的关键中间量整理成一份可读快照，配合看板展示，
# 让用户自己就能判断卡在哪一步，不用非得来问。
def diagnostics_snapshot(
    paths, cfg, profile, memory_store, profile_cfg=None,
    *, llm_helper: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """成长顾问的自检信息：当前配置快照、上一次信号扫描命中了哪些主题
    各多少条（只给计数，不回显记忆原文——诊断信息也要遵守"知情但克制"
    的边界）、扫描窗口内一共有多少条记忆可供扫描。纯只读聚合，不做任何
    写入，可以随时安全调用（哪怕从未跑过一次扫描）。

    [next_doc/memory_backfill_and_profile_update_plan.md 看板展示]
    `profile_cfg`（`ProfileConfig`）为可选参数，仅用于取
    `stale_after_days` 来计算画像"待复核"条目——不传时（比如老调用方/
    测试还没升级）该字段直接退化为空列表，不影响函数原有行为。

    [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2 第二步]
    `llm_helper` 为可选参数，透传给 `growth_feedback_pattern_summary()`
    用于生成 `feedback_pattern.llm_insight`；不传时该字段自然为空
    字符串，不影响函数其它部分。
    """
    derived = dict(getattr(profile, "derived", {}) or {})
    focus_areas: dict[str, list[str]] = derived.get("growth_focus_areas") or {}
    last_scan_at = derived.get("growth_focus_areas_updated_at")

    entries = []
    if memory_store is not None:
        try:
            entries = memory_store.all_entries()
        except Exception:
            entries = []
    cutoff = time.time() - SIGNAL_SCAN_WINDOW_DAYS * 86400
    entries_in_window = sum(1 for e in entries if getattr(e, "created_at", 0) >= cutoff)

    # [P4-1] 关键词表按来源展示（内置/系统学到待确认/用户自定义），
    # 而不是只给一个不带来源信息的主题名列表。
    effective_keywords = _effective_topic_keywords(profile)
    topics_detail = [
        {
            "topic": topic,
            "keywords": info["keywords"],
            "source": info["source"],
            "confirmed_by_user": info["confirmed_by_user"],
            "consecutive_scan_hits": info.get("consecutive_scan_hits", 0),
            "auto_confirmed": info.get("auto_confirmed", False),
        }
        for topic, info in effective_keywords.items()
    ]

    # [P4-1] 看板"Agent 对你的了解"区块：只透出 LLM 生成的画像部分
    # （summary/tech_stack/habits），不包含 preferences（用户显式设置的
    # 偏好是另一回事，混在一起展示容易让用户误解）。
    # [next_doc/memory_backfill_and_profile_update_plan.md 方向二]
    # tech_stack/habits 从纯字符串列表升级为 `{text, last_confirmed_at}`
    # 结构后，这里改成只透出 text 部分——保持看板对外展示的字段类型不变
    # （仍是字符串列表），新增的 last_confirmed_at 是内部维护"新鲜度"用的，
    # 暂不在诊断面板展示，避免一次性引入太多新字段。旧格式（纯字符串）
    # 数据也兼容处理，防止 profile.py 迁移逻辑还没跑到时这里报错。
    derived_profile = dict(getattr(profile, "derived", {}) or {})

    def _as_text_list(raw) -> list[str]:
        out = []
        for item in raw or []:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
            else:
                text = str(item).strip()
            if text:
                out.append(text)
        return out

    user_profile_snapshot = {
        "summary": derived_profile.get("summary") or "",
        "tech_stack": _as_text_list(derived_profile.get("tech_stack")),
        "habits": _as_text_list(derived_profile.get("habits")),
        "updated_at": derived_profile.get("updated_at"),
        # [next_doc/growth_advisor_diagnostics_and_language_fix_plan.md
        # 方向二] 透出当前检测到的用户常用语言，方便用户在诊断面板确认
        # "为什么画像是这个语言"；没生成过画像时为空串。
        "preferred_language": derived_profile.get("preferred_language") or "",
    }

    # [next_doc/memory_backfill_and_profile_update_plan.md 看板展示]
    # "待复核"条目：距今超过 stale_after_days 天没有被新证据再次印证的
    # tech_stack/habits，只给文本列表（不暴露具体时间戳，跟上面
    # user_profile_snapshot 对外展示的字段类型保持一致的克制程度）。
    # stale_after_days 优先取 profile_cfg，取不到时退回 ProfileConfig 的
    # dataclass 默认值（90 天），不因为调用方没传 profile_cfg 就直接跳过。
    stale_after_days = getattr(profile_cfg, "stale_after_days", None)
    if stale_after_days is None:
        stale_after_days = 90
    try:
        from mini_agent.profile import _migrate_text_items, stale_items
        now = time.time()
        stale_tech = stale_items(
            _migrate_text_items(derived_profile.get("tech_stack") or [], fallback_ts=now),
            now=now, stale_after_days=stale_after_days,
        )
        stale_habits = stale_items(
            _migrate_text_items(derived_profile.get("habits") or [], fallback_ts=now),
            now=now, stale_after_days=stale_after_days,
        )
    except Exception:
        stale_tech, stale_habits = [], []
    user_profile_snapshot["stale_tech_stack"] = stale_tech
    user_profile_snapshot["stale_habits"] = stale_habits
    user_profile_snapshot["stale_after_days"] = stale_after_days

    # [next_doc/memory_backfill_and_profile_update_plan.md M1 看板展示]
    # 记忆回填候选数：只读扫描（对齐 CLI `/memory backfill --dry-run` 的
    # 判定逻辑），不触发任何生成/写入，供看板解释"为什么记忆总条数这么
    # 少"以及"现在还有多少存量 session 没被回填"。扫描失败（比如
    # session 目录不可读）不影响诊断面板其它部分，静默降级为 0。
    backfill_candidates_count = 0
    try:
        from mini_agent.evolution.memory_backfill import scan_sessions_for_backfill
        from mini_agent.session import SessionManager
        # 这里的 min_turns 只用于诊断展示的粗略计数，取
        # MemoryBackfillConfig 的 dataclass 默认值即可，不强依赖调用方
        # 把完整配置传进来（cfg 是 GrowthAdvisorConfig，不包含这个字段）。
        sm = SessionManager(project_root=getattr(paths, "project_root", None))
        backfill_candidates_count = len(scan_sessions_for_backfill(
            sm, min_turns_for_backfill=4,
        ))
    except Exception:
        backfill_candidates_count = 0

    return {
        "config": {
            "enabled": getattr(cfg, "enabled", True),
            "min_evidence_count": getattr(cfg, "min_evidence_count", None),
            "max_pending_candidates": getattr(cfg, "max_pending_candidates", None),
            "dismissed_cooldown_days": getattr(cfg, "dismissed_cooldown_days", None),
            "notification_frequency": getattr(cfg, "notification_frequency", None),
            "notification_min_confidence": getattr(cfg, "notification_min_confidence", None),
            "excluded_topics": list(getattr(cfg, "excluded_topics", []) or []),
            "llm_signal_augment_enabled": getattr(cfg, "llm_signal_augment_enabled", False),
        },
        "signal_scan": {
            "window_days": SIGNAL_SCAN_WINDOW_DAYS,
            "last_scan_at": last_scan_at,
            "topics_tracked": list(effective_keywords.keys()),
            "topics_detail": topics_detail,
            # 只给每个主题命中了多少条，不回显 entry_id/记忆原文
            "topic_hit_counts": {topic: len(ids) for topic, ids in focus_areas.items()},
        },
        "memory": {
            "total_entries": len(entries),
            "entries_in_scan_window": entries_in_window,
            # [next_doc/memory_backfill_and_profile_update_plan.md M1]
            # 还有多少存量 session 符合回填条件（summary 为空、轮次达标）
            # 但尚未被回填——配合 cron_jobs.sys:memory_backfill_scan 的
            # last_run_at 一起看，能解释"记忆总条数"为什么偏低。
            "backfill_candidates_count": backfill_candidates_count,
        },
        "user_profile": user_profile_snapshot,
        # [P4-3] 待回访候选数量，供看板在诊断区提示"有 N 个方向该回访了"，
        # 具体列表通过 GET /growth/followups 单独获取（避免每次
        # /growth/summary 都要多做一遍 accepted_at 过滤）。
        "pending_followups_count": len(
            pending_followups(paths, cfg, goal_backlog=_load_goal_backlog_safely(paths))
        ),
        # [P4-4] 待刷新报告数量，明细走 GET /growth/reports/refresh_candidates。
        "reports_needing_refresh_count": len(reports_needing_refresh(paths, cfg)),
        # [P4-5] 按类别的历史采纳率（供看板解释"为什么这条被优先推送了"），
        # 只包含有过至少一次 accept/dismiss 决策的类别。
        "category_acceptance_rate": _category_acceptance_rate(paths, profile),
        # [P4-6] 显式说明诊断面板"命中计数"跟主题地图"历史累计"口径不同，
        # 避免用户看到两处数字对不上以为哪里坏了——诊断面板永远只是"最近
        # 一次扫描"的快照，历史累计要看 growth_topic_map/月度复盘。
        "topic_hit_counts_note": (
            "上面「最近一次信号扫描」里的命中次数，只是最新一轮扫描的快照，"
            "跟下面「成长主题地图」里的历史累计次数是两个不同的口径——"
            "地图数字只增不减，这里的数字每次扫描都会重新统计。"
        ),
        # [P4-7] 被隐藏的内置主题列表，供看板渲染"已隐藏"区块 + 恢复按钮。
        "hidden_builtin_topics": hidden_builtin_topics(profile),
        # [LLM 增强路径可观测性] 三个 opt-in LLM 调用点各自"最近一次调用
        # 结果"，key 是 _LLM_CALL_TYPES 里的调用点名字，没触发过的调用点
        # 不出现在里面（区别于"触发过但失败"）。开关本身是否开启看上面
        # config 区块，这里只回答"开了之后实际跑得怎么样"。
        "llm_call_status": llm_call_status_snapshot(paths),
        # [反馈粒度细化] "方向没错、报告没写好"的累计次数，跟
        # dismiss（方向级）分开统计，明细走月度复盘的
        # top_report_quality_flags。
        "report_quality_flags_count": sum(_report_quality_dismiss_counts(paths).values()),
        # [growth_advisor_goal_cron_integration_plan.md 阶段 A] 只给计数，
        # 明细走 `/growth align`（或未来的 GET /growth/align）。
        # `goal_backlog` 不可用（比如没有项目路径/加载失败）或功能被
        # `goal_alignment_enabled=False` 关闭时，两个字段整体为 `None`，
        # 不影响诊断面板其余部分。
        "goal_alignment": _goal_alignment_diagnostics_summary(paths, cfg, profile),
        # [growth_advisor_ideal_advisor_gap_and_roadmap_plan.md 方向 2]
        # 反馈模式统计——纯展示，不参与任何排序/置信度计算，见函数
        # docstring 里的取舍说明。任何异常都不该拖垮整个诊断面板，失败
        # 时退化为"看不出模式"的默认结构。第二步 LLM 归纳是否触发由
        # `cfg.feedback_pattern_llm_enabled` + 有没有传 `llm_helper`
        # 共同决定，跟 `goal_alignment_llm_enabled` 同款 opt-in 约定。
        "feedback_pattern": _feedback_pattern_diagnostics_summary(paths, cfg, profile, llm_helper),
        # [growth_advisor_autonomous_search_and_material_improvement_
        # plan.md 阶段三后续：生成后自检结果的展示] 汇总最近报告的
        # `citation_check` 诊断字段——引用命中率、检测到编造引用的报告
        # 数——供看板判断"标注来源"这条 prompt 要求实际执行得怎么样，
        # 纯展示，不影响任何排序/生成逻辑。
        "citation_check": _citation_check_diagnostics_summary(paths),
    }


def _citation_check_diagnostics_summary(paths) -> dict[str, Any]:
    """[growth_advisor_autonomous_search_and_material_improvement_plan.md
    阶段三后续：生成后自检结果的展示] 汇总活跃索引里带 `citation_check`
    的报告（`generate_growth_report()` 只在"开了外部背景 + 拿到非空
    摘录 + LLM 生成"这个组合下才会写这个字段，多数报告该字段仍是
    `None`，不计入汇总）：

    - `reports_checked`：有 `citation_check` 的报告数（分母）
    - `reports_with_hallucination`：其中至少出现过一条编造引用的报告数
    - `total_excerpts_offered` / `total_excerpts_cited`：所有这些报告
      加总的"摘录总数"/"被正确引用的摘录数"，用于估算总体引用命中率
    - `citation_hit_rate`：`total_excerpts_cited / total_excerpts_offered`
      （分母为 0 时该字段为 `None`，不是 `0.0`——"没有可核对的样本"跟
      "样本都没命中"是两回事）

    纯只读聚合，任何异常都退化为"看不出数据"的默认结构，不拖垮整个
    诊断面板（跟本函数所在的 `diagnostics_snapshot()` 里其它汇总子
    函数保持一致的容错风格）。
    """
    try:
        reports = list_reports(paths)
        checked = [r for r in reports if getattr(r, "citation_check", None)]
        total_offered = 0
        total_cited = 0
        with_hallucination = 0
        for r in checked:
            cc = r.citation_check or {}
            total_offered += int(cc.get("excerpts_total", 0) or 0)
            total_cited += int(cc.get("cited_count", 0) or 0)
            if cc.get("hallucinated_refs"):
                with_hallucination += 1
        return {
            "reports_checked": len(checked),
            "reports_with_hallucination": with_hallucination,
            "total_excerpts_offered": total_offered,
            "total_excerpts_cited": total_cited,
            "citation_hit_rate": (total_cited / total_offered) if total_offered > 0 else None,
        }
    except Exception:
        return {
            "reports_checked": 0,
            "reports_with_hallucination": 0,
            "total_excerpts_offered": 0,
            "total_excerpts_cited": 0,
            "citation_hit_rate": None,
        }


def _feedback_pattern_diagnostics_summary(paths, cfg, profile, llm_helper) -> dict[str, Any]:
    try:
        return growth_feedback_pattern_summary(paths, profile=profile, cfg=cfg, llm_helper=llm_helper)
    except Exception:
        return {
            "has_enough_data": False,
            "sample_size": 0,
            "reason_distribution": {},
            "category_distribution": {},
            "summary_text": "反馈模式统计暂时不可用。",
            "llm_insight": "",
        }


def _goal_alignment_diagnostics_summary(paths, cfg, profile) -> dict[str, Any]:
    try:
        alignment = goal_growth_alignment(paths, profile, cfg=cfg)
    except Exception:
        return {"unmatched_interests_count": None, "stalled_linked_goals_count": None}
    if not alignment.get("enabled", True):
        return {"unmatched_interests_count": None, "stalled_linked_goals_count": None}
    stalled_count = sum(1 for g in alignment.get("linked_goals", []) if g.get("stalled"))
    return {
        "unmatched_interests_count": len(alignment.get("unmatched_interests", [])),
        "stalled_linked_goals_count": stalled_count,
    }
