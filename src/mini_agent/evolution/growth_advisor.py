"""成长顾问 Growth Advisor（对应 next_doc/growth_advisor_design.md）。

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
"""

from __future__ import annotations

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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GrowthReport":
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
    ) -> Optional[GrowthCandidate]:
        """尝试新增一条候选。规则（对应方案第 3 节"克制"要求）：
            - evidence_refs 数量不达标 → 不生成，返回 None
            - 已存在同 dedupe_key 的 pending/accepted 候选 → 合并证据、
              不重复创建
            - 曾被 dismissed 且仍在冷却期内 → 跳过，返回 None
            - pending 数量已达上限 → 跳过，返回 None（避免无限堆积）
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
DISMISS_REASON_UNSPECIFIED = "unspecified"             # 未指定原因（兼容旧数据/旧调用方）
_VALID_DISMISS_REASONS = frozenset(
    {
        DISMISS_REASON_NOT_INTERESTED,
        DISMISS_REASON_BAD_TIMING,
        DISMISS_REASON_REPORT_NOT_USEFUL,
        DISMISS_REASON_UNSPECIFIED,
    }
)
# 参与"方向/类别置信度衰减"的 dismiss 原因——REPORT_NOT_USEFUL 不在这
# 个集合里，是本次改动的核心：它不代表用户对这个方向不感兴趣。
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


def growth_candidate_derive(paths, cfg, profile) -> list[GrowthCandidate]:
    """消费 `profile.derived["growth_focus_areas"]`（由 growth_signal_scan
    产出），对证据数达标、未命中 excluded_topics 的主题生成/合并候选到
    backlog，返回本次新增或有更新的候选列表。
    """
    focus_areas: dict[str, list[str]] = dict(
        (getattr(profile, "derived", {}) or {}).get("growth_focus_areas", {})
    )
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


def generate_growth_report(
    paths,
    candidate: GrowthCandidate,
    *,
    llm_helper: Optional[Callable[[str], str]] = None,
    is_exploration: bool = False,
    profile=None,
    cfg=None,
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
    2.4 节] 可选参数，仅当 `cfg.report_include_external_context` 为真
    且 `llm_helper` 也传入时生效——把 `_external_signal_count_for_topic()`
    统计到的外部资讯数量作为"背景参考"额外拼进喂给 LLM 的 prompt，明确
    要求"这些只是外部背景信息，报告的核心判断仍然要基于用户自己的记忆
    证据"。**不影响候选的置信度/排序**——这里只改 LLM prompt 的输入，
    `candidate.confidence`、`evidence_count_at_generation` 等落盘字段
    完全不受这个开关影响，对齐 2.3/2.4 节"仅展示、不影响判断"的克制
    设计。两个参数任一缺失、或规则模板路径（`llm_helper is None`）时，
    这段逻辑整体跳过，向后兼容此前所有不传这两个参数的调用方。
    """
    report_id = uuid.uuid4().hex[:12]
    slug = f"{_slugify(candidate.title)}-{report_id[:6]}"

    body = None
    source = "template"
    if llm_helper is not None:
        external_context_section = ""
        if profile is not None and cfg is not None and getattr(cfg, "report_include_external_context", False):
            try:
                effective = _effective_topic_keywords(profile)
                info = effective.get(candidate.title)
                if info:
                    ext_count = _external_signal_count_for_topic(paths, candidate.title, info["keywords"])
                    if ext_count > 0:
                        external_context_section = (
                            f"\n[外部背景参考，仅供了解，不改变你的判断] "
                            f"最近 30 天外部世界大约有 {ext_count} 条跟这个方向相关的资讯。"
                            "这只是背景信息，报告的核心判断仍然要基于用户自己的记忆证据，"
                            "不要因为这个数字改变你对该方向重要性的评估。\n"
                        )
            except Exception:
                external_context_section = ""

        prompt = (
            "请为以下用户成长方向候选撰写一份简短调研报告（Markdown，"
            "包含：为什么值得关注、可以怎么入门、常见资源/路径、"
            "预计投入与见效周期，4 个小节即可，不要超过 500 字）：\n"
            f"主题：{candidate.title}\n理由：{candidate.rationale}\n"
            f"{external_context_section}"
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


def reports_needing_refresh(paths, cfg=None) -> list[dict]:
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
    """
    min_new = getattr(cfg, "report_refresh_min_new_evidence", _DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE) if cfg is not None else _DEFAULT_REPORT_REFRESH_MIN_NEW_EVIDENCE
    reports_by_id = {r.report_id: r for r in list_reports(paths)}
    out = []
    for c in GrowthBacklog(paths).load_all():
        if not c.report_id:
            continue
        report = reports_by_id.get(c.report_id)
        if report is None:
            continue
        if report.evidence_count_at_generation < 0:
            # [P5-1] 哨兵值：生成时的证据数快照缺失（反序列化自这个字段
            # 引入之前的旧数据），不做"证据从 0 涨到现在"这种误判，直接
            # 跳过，不计入待刷新。
            continue
        new_evidence = c.evidence_count - report.evidence_count_at_generation
        if new_evidence >= min_new:
            recent_delta = _recent_evidence_delta(
                paths, c.dedupe_key(), window_days=_REPORT_REFRESH_RECENT_BURST_WINDOW_DAYS
            )
            out.append(
                {
                    "candidate_id": c.candidate_id,
                    "title": c.title,
                    "report_id": report.report_id,
                    "evidence_count": c.evidence_count,
                    "evidence_count_at_generation": report.evidence_count_at_generation,
                    "new_evidence": new_evidence,
                    "recent_evidence_delta": recent_delta,
                }
            )
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
_LLM_CALL_TYPES = ("signal_augment", "report_quality", "topic_category", "goal_alignment_match")


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
    paths, cfg, candidates_by_id: dict[str, GrowthCandidate], reports: list[GrowthReport], profile=None
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

    min_conf = getattr(cfg, "notification_min_confidence", 0.6)
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

        message = NotificationMessage(
            title=f"成长顾问：{best_report.title}",
            body=best_report.summary,
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


def run_daily_cycle(paths, cfg, profile, memory_store, *, llm_helper: Optional[Callable[[str], str]] = None) -> dict[str, Any]:
    """`sys:growth_advisor_daily` 与 `/growth scan` 共用的主流程：
    信号扫描 -> 候选生成 -> （置信度达标的）Top-N 生成调研报告 ->
    （P2 新增）按 4.2 节节流规则决定要不要推送一条通知。

    P3：`llm_helper` 只有在 `cfg.llm_signal_augment_enabled=True` 时才会
    真正传给 `growth_signal_scan`（默认 False，零 LLM 成本）——即使调用方
    在有 agent 上下文的场景下总是能拿到 `llm_helper`，是否使用仍然由
    这个显式开关控制，不因为"恰好有"就默认用上。
    """
    if not getattr(cfg, "enabled", True):
        return {"skipped": True, "reason": "growth_advisor disabled"}

    scan_llm_helper = llm_helper if getattr(cfg, "llm_signal_augment_enabled", False) else None
    growth_signal_scan(paths, profile, memory_store, llm_helper=scan_llm_helper)
    new_candidates = growth_candidate_derive(paths, cfg, profile)

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
    reports = [
        generate_growth_report(
            paths, c, llm_helper=report_llm_helper, is_exploration=is_exploration,
            profile=profile, cfg=cfg,
        )
        for c, is_exploration in selected
    ]

    candidates_by_id = {c.candidate_id: c for c in top}
    freq = getattr(cfg, "notification_frequency", "daily")
    if freq == "weekly_digest":
        notification = _maybe_dispatch_weekly_digest(paths, cfg, profile)
    else:
        notification = _maybe_dispatch_notification(paths, cfg, candidates_by_id, reports, profile)

    # [v4 N1] 每日流程收尾时顺带记一条全局健康度快照 + 做一次降采样
    # 压缩，跟 growth_topic_trend 的既有节奏一致（每天一次，不影响主
    # 流程返回结构）。快照/压缩失败不应该影响本轮扫描/候选生成/推送
    # 已经产出的结果，静默降级。
    try:
        _record_health_snapshot(paths, cfg, profile, memory_store)
        compact_health_trend_storage(paths)
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

    return {
        "skipped": False,
        "new_candidates": [c.candidate_id for c in new_candidates],
        "reports": [r.report_id for r in reports],
        "notification": notification,
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

def _external_signal_count_for_topic(
    paths, topic: str, keywords: list[str], *, window_days: int = 30,
) -> int:
    """[growth_advisor_improvement_plan_v4.md 方向二 2.3 节] 粗略统计：
    最近 `window_days` 天内，wiki 里有多少条 `source_kind` 属于
    `external_watch`/`external_search`（外部检索/外部观察产出，见
    `wiki/world_writer.py` 的 `EXTERNAL_WATCH_SOURCE_KIND`/
    `EXTERNAL_SEARCH_SOURCE_KIND`）的页面，标题/正文命中了该主题的
    关键词。

    复用 `growth_signal_scan()` 对记忆做关键词匹配的同一套简单规则
    （小写子串匹配，不引入新的匹配算法/embedding）。**只读聚合，不改变
    任何置信度计算**——这是本函数存在的唯一边界：外部世界的资讯量本身
    跟"用户自己是否感兴趣"没有必然关系，只能作为展示补充，不能参与
    `_confidence_from_evidence()` 这类判断用户投入程度的计算。

    单个页面解析失败（frontmatter 缺失/格式错误等，`wiki/quarantine.py`
    已经有独立的隔离区机制处理这类问题）时静默跳过，不影响其它页面的
    统计，也不在这里重复记录隔离区问题（那是 `wiki/stats.py::
    compute_stats()` 的职责，本函数只是"顺手数一下"，不承担 wiki 健康度
    治理的职责）。
    """
    if not keywords:
        return 0
    try:
        from mini_agent.wiki.indexer import discover_pages
        from mini_agent.wiki.parser import parse_page
        from mini_agent.wiki.world_writer import (
            EXTERNAL_WATCH_SOURCE_KIND, EXTERNAL_SEARCH_SOURCE_KIND,
        )
    except Exception:
        return 0

    cutoff_date = (
        __import__("datetime").date.today()
        - __import__("datetime").timedelta(days=window_days)
    ).isoformat()
    lowered_keywords = [k.lower() for k in keywords if k]
    count = 0
    try:
        page_paths = discover_pages(paths)
    except Exception:
        return 0
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
            count += 1
    return count


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
def diagnostics_snapshot(paths, cfg, profile, memory_store, profile_cfg=None) -> dict[str, Any]:
    """成长顾问的自检信息：当前配置快照、上一次信号扫描命中了哪些主题
    各多少条（只给计数，不回显记忆原文——诊断信息也要遵守"知情但克制"
    的边界）、扫描窗口内一共有多少条记忆可供扫描。纯只读聚合，不做任何
    写入，可以随时安全调用（哪怕从未跑过一次扫描）。

    [next_doc/memory_backfill_and_profile_update_plan.md 看板展示]
    `profile_cfg`（`ProfileConfig`）为可选参数，仅用于取
    `stale_after_days` 来计算画像"待复核"条目——不传时（比如老调用方/
    测试还没升级）该字段直接退化为空列表，不影响函数原有行为。
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
