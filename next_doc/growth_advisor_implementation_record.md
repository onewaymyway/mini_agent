# 成长顾问 Growth Advisor —— 实施记录

> 关联方案：`next_doc/growth_advisor_design.md`
> 本记录只覆盖**已落地**的部分。未提及的内容视为未开始，请以方案原文的
> P1/P2/P3 里程碑为准继续核对。

## 已完成：P1 里程碑（信号扫描 → 候选生成 → 调研报告 → 看板展示）

### 后端核心模块

- 新增 `src/mini_agent/evolution/growth_advisor.py`：
  - `GrowthCandidate` / `GrowthReport` 数据模型。
  - `GrowthBacklog`：候选队列的读写封装（落盘为 `growth_backlog.jsonl`，
    每次整表重写）。实现了方案要求的三条克制规则：
    1. `add_or_merge()` 按标题归一化（`normalize_title_key`，算法与
       `objective_outcome_tracker.normalize_title_key` 保持一致）去重，
       同一方向重复出现只合并证据、不重复建候选；
    2. `pending` 数量达到 `max_pending_candidates` 上限后本轮不再新增；
    3. 曾被 `dismissed` 的候选在 `dismissed_cooldown_days`（默认 30 天）
       冷却期内不会被重新生成。
  - `expire_stale()`：`pending` 超过 `PENDING_TTL_DAYS`（45 天）自动标记
    为 `expired`，避免看板堆积陈旧建议。
  - `GrowthFeedbackLedger`：用户对候选的 accept/dismiss 反馈流水
    （append-only，落盘 `growth_feedback_ledger.jsonl`）。**P1 只落盘，
    不做置信度加权**——加权留给 P2，避免过度设计。
  - `growth_signal_scan()`：**规则式**（不依赖 LLM）信号扫描，按
    `_TOPIC_KEYWORDS` 关键词表对最近 `SIGNAL_SCAN_WINDOW_DAYS`（90 天）
    内的 memory entries 做命中统计，写回
    `UserProfile.derived["growth_focus_areas"]`。选择规则式而非 LLM
    归纳，是为了保证 `GrowthAdvisorConfig.enabled=True` 默认开启时不会
    给每个用户都额外产生 LLM 调用成本——这是本次实现与方案原文一个
    明确的取舍：**方案预留了 LLM 归纳的空间（调研报告生成阶段已经支持
    可选 LLM 增强），但信号扫描阶段 P1 先用零成本的关键词规则跑通闭环，
    LLM 版归纳留给 P2 作为增强项**。
  - `growth_candidate_derive()`：消费 `growth_focus_areas`，对证据数
    达到 `min_evidence_count`（默认 3，与 `decision_profile_builder` 的
    `MIN_EVIDENCE_COUNT` 同量级）、未命中 `excluded_topics` 黑名单的
    主题生成/合并候选。
  - `generate_growth_report()`：为候选生成调研报告并落盘到
    `wiki/growth/<slug>.md`。P1 默认走规则模板（保证零 LLM 成本也能跑
    通闭环）；调用方可传入 `llm_helper` 优先起草正文，LLM 调用失败时
    自动回退模板，不会中断流程。
  - `run_daily_cycle()`：信号扫描 → 候选生成 → Top-N（`max_reports_per_run`，
    默认 2）候选生成调研报告的主流程封装，供 cron job 与 CLI 复用。
    **不做任何推送/通知判断**——推送节奏由 `notification_frequency` 等
    独立配置项控制，是否触发通知留给上层调用方决定（P1 暂未接入实际的
    通知派发，看板轮询即可看到最新状态；通知派发留给后续迭代）。
  - `monthly_retrospective_summary()`：候选/反馈的数量统计，**P1 只做
    计数统计**，深度归因（比如"哪类候选更容易被采纳"）留给 P2。

### 配置

- `config/models.py` 新增 `GrowthAdvisorConfig`（与 `DigestAdvisorConfig`
  平级的姊妹配置块），关键字段：
  - `enabled: bool = True`（**opt-out 默认开启**，方案原文第 -1 节明确
    要求的"零成本用起来"取舍）
  - `generation_frequency` / `notification_frequency`：生成与推送两个
    维度独立配置（方案第 4.1/4.2 节要求）
  - `min_evidence_count` / `max_pending_candidates` / `max_reports_per_run` /
    `dismissed_cooldown_days`：候选克制阈值
  - `excluded_topics`：用户可配置的关注领域黑名单（方案第 5 节"设置
    入口"，P1 只做配置项存在，尚未在看板里做可视化编辑，见"未做"部分）
- 通过既有的 `NestedBlockSpec` 通用机制接入
  `param_registry.py` / `config_catalog.py` / `config/loader.py`，
  `AppConfig.growth_advisor` 可正常加载默认值（已用最小复现脚本验证）。

### Cron 调度

- `evolution/cron_scheduler.py` `_BUILTIN_JOBS` 新增两条内置 job：
  - `sys:growth_advisor_daily`（`cron:30 22 * * *`，默认 `enabled=True`）：
    触发 `/growth scan`。
  - `sys:growth_monthly_retrospective`（`interval:2592000`，默认
    `enabled=True`）：触发 `/growth retrospective`。
  两条 job 与其余 `sys:` job 一样遵循"用户可 disable、不可删除"的既有
  约定，未额外改动 `CronScheduler` 本体逻辑。

### CLI

- 新增 `cli/commands/growth_cmd.py`，子命令：
  `/growth [list]`、`/growth scan`、`/growth accept|dismiss <id>`、
  `/growth report <id>`、`/growth retrospective`。
- 接入 `cli/commands/__init__.py` 导出、`cli/repl.py` 分发、
  `cli/parser.py` 帮助文本。

### API

- `api/routes.py` 新增只读 + 少量显式动作端点（沿用已有的
  `/notification/watchlist` 系列风格，不引入配置编辑器）：
  - `GET /v1/growth/summary`
  - `POST /v1/growth/scan`
  - `POST /v1/growth/candidates/{id}/{accept|dismiss}`
  - `GET /v1/growth/reports/{id}`

### 看板

- `apps/mini_agent_kanban/client.py` 新增 `growth_summary()` /
  `growth_scan()` / `growth_candidate_action()` / `growth_report()`。
- `apps/mini_agent_kanban/app.py` 新增 `render_growth_tab()`：候选数/
  已采纳/已忽略/报告数四个指标 + 待处理候选列表（每条候选可采纳/忽略/
  查看调研报告）+ 手动"立即为我看看"扫描按钮。已接入顶部 `st.tabs(...)`
  的 "🌱 成长顾问" tab（位于"🧠 自我状态"之后，其余 tab 索引相应顺移）。

### 测试

- 新增 `tests/test_growth_advisor.py`（16 个用例，全部通过），覆盖信号
  扫描窗口过滤、候选生成的证据阈值/黑名单、backlog 去重合并/数量上限/
  冷却期/过期回收、报告生成的模板兜底与 LLM 回退、`run_daily_cycle`
  端到端、月度复盘统计、cron 内置 job 注册。

## 已完成：P2 里程碑（反馈驱动的置信度调权 + 推送节流接入 + 复盘深度归因）

### 反馈驱动的置信度调权

- `growth_advisor.py` 新增 `_dismiss_counts_by_dedupe_key()` /
  `_feedback_multiplier()`：
  - 读取 `GrowthFeedbackLedger` 里的历史 `dismissed` 记录，反查
    `GrowthBacklog`（整表读取，包含历史状态）把 `candidate_id` 映射回
    `dedupe_key`（归一化标题），统计每个方向历史上被忽略过几次；
  - `_feedback_multiplier(n)` 按 `0.85 ** n` 复利衰减，下限
    `_MIN_FEEDBACK_MULTIPLIER = 0.4`（不会打到 0，对应方案第 6 节"不是
    完全屏蔽，避免用户当时忙、后来又感兴趣的情况被永久拒绝"）。
- `GrowthBacklog.add_or_merge()` 新增 `confidence_multiplier` 参数，
  只在**新建**候选时生效（对已存在的 pending/accepted 候选做证据合并
  时不重算，避免已经在被用户关注的候选置信度无故被历史反馈拉低）。
- `growth_candidate_derive()` 在生成候选前，先一次性算好
  `dismiss_counts`，逐主题查表得到 multiplier 传给
  `add_or_merge`——性能上不会在候选数量增长时退化成 O(n × ledger 长度)。

### 推送节流接入 NotificationDispatcher

- 新增 `_maybe_dispatch_notification()`，在 `run_daily_cycle()` 生成完
  调研报告后调用，落实方案第 4.2 节的节流规则：
  1. `notification_frequency == "kanban_only"` 或本轮没有新报告 → 不推送；
  2. 只在达到 `notification_min_confidence` 的报告里选置信度最高的一条，
     全部达不到阈值则不推送；
  3. 当天（本地自然日）已推送次数达到 `notification_max_per_day` → 不
     再推送；节流状态（`last_notify_date` / `notify_count_today`）落盘
     在此前已预留但未使用的 `paths.growth_state_path`
     （`growth_advisor_state.json`）；
  4. 命中推送条件时，复用已有的 `NotificationDispatcher`（同时把消息
     写入 `notification/reports_store.py`，`source="growth_report"`，与
     方案第 3.3 节"新报告生成会同时出现在通知列表里，复用已有机制"的
     要求一致）；
  5. 全程 try/except + `log_exception(where="mini_agent.growth_advisor.\
     _maybe_dispatch_notification")` 兜底，不让通知发送失败影响
     `run_daily_cycle` 主流程。
- `run_daily_cycle()` 返回值新增 `notification` 字段（成功时含
  `report_id`/`confidence`/各渠道发送结果，未触发推送时为 `None`），
  CLI `/growth scan` 与 API `POST /growth/scan` 均沿用同一份返回值，未
  额外改动调用方代码。
- **已知简化**：`notification_frequency == "weekly_digest"` 目前与
  `daily` 走同一套节流规则，没有单独实现"打包成一条周摘要"的聚合步骤
  ——现在选择该档位实际效果等价于"daily 但通常不会真的每天都推"（受
  置信度阈值自然节流），后续如要做真正的周摘要，需要新增一个独立的
  周频 cron job 消费本周内的 `growth_reports.jsonl`。

### 月度复盘深度归因

- `monthly_retrospective_summary()` 新增：
  - `acceptance_rate`：`accepted / (accepted + dismissed)`，没有任何
    已决策候选时返回 `None`（避免显示一个没有意义的 0%）；
  - `top_accepted_topics` / `top_dismissed_topics`：按候选标题聚合的
    采纳/忽略次数排行（Top 5），对应方案第 6 节"推荐命中率"指标。
  - 深度的"跨候选能力地图聚合"（比如把多个相关主题聚成一张能力雷达图）
    仍未做，留给 P3。

### 看板

- `apps/mini_agent_kanban/app.py` `render_growth_tab()`：
  - 新增首次触达轻量提示（方案第 8 节第 1 条"默认开启，但首次触达必须
    透明告知"）：用 `st.info` 说明已开启、用了哪些数据、在哪关闭，展示
    一次后不再重复弹出。**已知简化**：当前用
    `st.session_state["_growth_first_touch_shown"]` 实现，只在单次浏览
    器会话内生效，不是跨会话持久化——`GrowthAdvisorConfig.\
    first_touch_notice_enabled` 配置项本身已存在，若要做到方案原文
    "本地记录、跨会话不重复"，需要新增一个专门的只读+ack API 端点
    （比如 `POST /v1/growth/first_touch_ack`），留给后续迭代。
  - 四个指标卡下方新增"推荐采纳率"文案与可展开的"按主题看采纳/忽略排行"
    区块，直接读 `/v1/growth/summary` 返回的 `retrospective.\
    acceptance_rate` / `top_accepted_topics` / `top_dismissed_topics`，
    没有新增 API 端点。

### 测试

- `tests/test_growth_advisor.py` 新增 4 个测试类、9 个用例（全部通过，
  加上原有 16 个共 25 个）：
  - `TestFeedbackWeighting`：冷却期后重新生成的候选置信度低于首次生成、
    衰减乘子有下限、`growth_candidate_derive` 端到端应用乘子；
  - `TestNotificationThrottle`：`kanban_only` 不推送、低于阈值不推送、
    达标推送且当天第二次触发被节流拦下、`run_daily_cycle` 返回值里带
    `notification` 字段；
  - `TestMonthlyRetrospectiveAttribution`：采纳率计算与主题排行、无
    决策记录时 `acceptance_rate` 为 `None`。

## 已完成：P3 里程碑（部分）——首次触达持久化 + 黑名单可视化编辑

### 首次触达提示跨会话持久化

- `growth_advisor.py` 新增 `first_touch_notice_shown(paths)` /
  `mark_first_touch_notice_shown(paths)`，落盘复用推送节流已经在用的
  `growth_advisor_state.json`（`paths.growth_state_path`），跟
  `last_notify_date`/`notify_count_today` 放在同一个文件里，读写各自的
  key、互不覆盖（`TestFirstTouchNotice.\
  test_shares_state_file_with_notification_throttle` 覆盖了这一点）。
- `api/routes.py`：
  - `GET /growth/summary` 响应新增 `first_touch_notice_shown` 字段；
  - 新增 `POST /growth/first_touch_ack`，幂等，供看板在展示过提示后
    调用一次即可，重复调用不会报错也不会重置已记录的展示时间。
- `apps/mini_agent_kanban/client.py` 新增 `growth_first_touch_ack()`。
- `apps/mini_agent_kanban/app.py` `render_growth_tab()`：不再用
  `st.session_state` 做单会话提示，改成读 `/growth/summary` 返回的
  `first_touch_notice_shown`，没展示过就显示提示并立即调用
  `growth_first_touch_ack()` 落盘。P2 记录里提到的"已知简化"到这里解决。

### `excluded_topics` 黑名单可视化编辑

- 没有新增专门的 API/端点，而是修好了通用配置编辑器
  （`kanban/app.py::_render_config_field_widget`）里 `ftype == "list"`
  分支缺失的问题——此前 list 类型字段（比如 `excluded_topics: list[str]`）
  会落进 `else` 分支被当成普通字符串处理，编辑体验很差（显示成 Python
  repr、保存也容易存脏数据）。现在改成一行一项的 `st.text_area`，读写都
  转换成 `list[str]`，空行自动过滤。
  这个修复**对所有 list 类型的配置字段生效**，不止 `excluded_topics`——
  `growth_advisor` 这个 `NestedBlockSpec` 早就通过既有机制接入了通用
  配置目录（见 P1 记录），本来就不需要为它单独开一个新端点，缺的只是
  前端这一个类型分支。

## 已完成：P3 里程碑（新增）——`weekly_digest` 真实周摘要打包

此前 `notification_frequency=weekly_digest` 与 `daily` 走同一套按天节流
逻辑，没有真正实现方案第 4.2 节"打包成一条周摘要"（见 P2 记录"已知
简化"）。本轮补上：

- `growth_advisor.py` 新增 `_maybe_dispatch_weekly_digest()`：
  - 状态新增 `last_weekly_digest_at`（时间戳，落盘在已有的
    `growth_advisor_state.json`，与推送节流/首次触达状态同一个文件），
    按"距上次推送是否满 `WEEKLY_DIGEST_INTERVAL_DAYS`（7）天"判断是否
    触发，而不是自然日/自然周；
  - 到期后，把窗口期（上次成功推送周摘要至今；首次触发时窗口取最近 7
    天）内 `list_reports()` 中新生成的**全部**调研报告标题打包进**一条**
    `NotificationMessage`（`source="growth_weekly_digest"`）一次性推送，
    不再逐条推；窗口内没有新报告时只推进 `last_weekly_digest_at`、不
    落一条空摘要消息；
  - 复用已有的 `NotificationDispatcher` 与 `notification/reports_store`
    （`report_ids` 列表记录本次打包的所有报告，而非单一 `report_id`）；
  - 全程 try/except + `log_exception(where="mini_agent.growth_advisor.\
    _maybe_dispatch_weekly_digest")` 兜底。
- `_maybe_dispatch_notification()`（daily/kanban_only 路径）新增防御性
  短路：`notification_frequency == "weekly_digest"` 时直接返回 `None`，
  避免调用方接错分支时把 weekly_digest 误当成 daily 逐条推送。
- `run_daily_cycle()` 按 `cfg.notification_frequency` 分流：
  `weekly_digest` → `_maybe_dispatch_weekly_digest()`；其余（`daily`/
  `kanban_only`）→ 原有的 `_maybe_dispatch_notification()`；两条路径
  互斥，不会同时触发。
- `config/models.py::GrowthAdvisorConfig.notification_frequency` 的字段
  注释同步更新；`docs/growth-advisor-guide.md` 第 5 节配置表与第 6 节
  "当前局限"同步更新（去掉"等价于 daily"的旧说明）。
- `tests/test_growth_advisor.py` 新增 `TestWeeklyDigest`（6 个用例，全部
  通过，加上原有 25 个共 31 个，另有 3 个既有用例不受影响，全文件合计
  34 个）：首次调用打包全部近期报告、7 天内第二次调用被节流、窗口内无
  新报告时不推送、时间戳回拨模拟"7 天已过"后可再次推送、
  `run_daily_cycle` 在 `weekly_digest` 档位下正确分流、daily 路径对
  `weekly_digest` 频率短路返回 `None`。

## 已完成：P3 里程碑（新增，第三项）——月度复盘的跨候选能力地图聚合

- `growth_advisor.py` 新增 `growth_topic_map(paths)`：
  - 按 `dedupe_key`（归一化标题）聚合 backlog 里**全部历史候选**（不只是
    当前 pending/accepted/dismissed 各一条——同一主题可能因 dismiss 冷却
    期结束后重新生成，产生多条 `candidate_id` 不同但标题相同的记录）；
  - 每个主题聚合出：`current_status`/`current_confidence`（取
    `updated_at` 最新的一条）、`peak_confidence`（历史出现过的最高置信度，
    不因某次 dismiss 后置信度被 `_feedback_multiplier` 打折而"倒退"）、
    `times_accepted`/`times_dismissed`/`occurrences`（历史累计次数）、
    `first_seen_at`/`last_updated_at`；
  - 按 `last_updated_at` 倒序返回，纯聚合展示，不做预测/自动排序推荐；
  - 思路对齐 `evolution/self_model_snapshot.py`（Agent 自己的能力弱项
    趋势快照），聚合对象换成了用户的成长方向推进轨迹，这也是方案第 6
    节"这一点跟 self_model_snapshot.py 让 Agent 能回答'能力弱点清单是
    变短了还是变长了'是同一个思路"的落地。
- `monthly_retrospective_summary()` 新增 `topic_map` 字段，直接复用
  `growth_topic_map()`；`GET /growth/summary` 本来就整体透传
  `retrospective`，未新增/改动 API 端点。
- 看板 `render_growth_tab()` 在"按主题看采纳/忽略排行"下方新增一个可
  折叠的"🗺️ 成长主题地图"区块，逐条展示每个方向的状态/峰值置信度/
  出现与采纳/忽略次数；CLI `/growth retrospective` 本来就是逐字段打印
  `monthly_retrospective_summary()` 的返回值，`topic_map` 无需额外改动
  即可显示。
- `docs/growth-advisor-guide.md` 第 3 节看板用法、第 6 节"当前局限"同步
  更新。
- `tests/test_growth_advisor.py` 新增 `TestGrowthTopicMap`（4 个用例，
  全部通过，加上此前 34 个，全文件合计 38 个）：空 backlog 返回空列表、
  单主题聚合当前状态、"忽略→冷却期后重新生成→再次决策"跨记录累计历史
  次数与峰值置信度、`monthly_retrospective_summary` 正确包含 `topic_map`。

## 已完成：P3 里程碑（新增，第四项）——`growth_signal_scan` 的 LLM 增强版归纳

- `growth_advisor.py` 新增 `_llm_augment_topics()`：
  - 只处理规则式关键词表命中不到的近期记忆条目，未命中条目数低于
    `_LLM_AUGMENT_MIN_UNMATCHED`（3）时直接跳过、不调用 LLM（大概率凑
    不满 `min_evidence_count`，调了也白调）；送 LLM 的条目数上限
    `_LLM_AUGMENT_MAX_ENTRIES`（40），避免 prompt 随记忆量无限增长；
  - LLM 输出要求是 JSON 数组 `[{"topic": ..., "entry_ids": [...]}]`，做了
    三层容错：先直接 `json.loads`，失败则用正则从文本里摘出 `[...]` 再
    解析一次，仍失败则整体丢弃、返回原规则结果；`entry_ids` 强制过滤成
    调用方提供的合法子集（LLM 编出来的 id 会被丢弃，不会污染 evidence_
    refs）；新主题按 `normalize_title_key` 与规则表已有主题去重合并
    （同名/同义主题不会产生重复 key）。
  - `growth_signal_scan()` 新增可选 `llm_helper` 形参（约定同
    `generate_growth_report`），传入时在规则扫描结束后调用一次
    `_llm_augment_topics()`，全程 try/except + `log_exception(where=
    "mini_agent.growth_advisor.growth_signal_scan_llm_augment")` 兜底，
    LLM 侧任何异常都不会影响规则式扫描已经拿到的结果。
- `config/models.py` 新增 `GrowthAdvisorConfig.llm_signal_augment_enabled`
  （默认 `False`，opt-in）——`run_daily_cycle()` 新增 `llm_helper` 形参，
  但只有这个开关为 `True` 时才会真正把它转发给 `growth_signal_scan()`；
  即使调用方（有 agent 上下文）总是能传入 `llm_helper`，默认路径仍然
  保持纯规则式、零 LLM 成本，不因为"恰好有"就默认用上。
- CLI `growth_cmd.py` 新增 `_get_llm_helper(agent)`：把 `agent.llm_helper`
  （`LLMHelper` 实例）包成 `growth_advisor` 期望的 `Callable[[str], str]`
  闭包（`lambda prompt: helper.ask(prompt)`），`/growth scan` 与 `/growth
  report` 都改用它。**顺带修了一个既有 bug**：`/growth report` 此前直接
  把 `agent.llm_helper`（不可调用的对象）当函数传给
  `generate_growth_report`，如果候选还没有报告、需要现场生成，一旦真的
  走到 `llm_helper(prompt)` 这一步会抛 `TypeError`，被 `generate_growth_
  report` 内部的 try/except 吞掉后静默回退模板——功能上从不报错，但
  "LLM 优先起草"这条路径实际上从未真正生效过，直到本次修复。
  `api/routes.py::post_growth_scan` 同步接入等价的闭包包装逻辑（仅在
  `cfg.llm_signal_augment_enabled=True` 且能拿到 `self_agent.llm_helper`
  时才构造，其余情况传 `None`）。
- `docs/growth-advisor-guide.md` 第 4 节配置表、第 6 节"当前局限"同步
  更新。
- `tests/test_growth_advisor.py` 新增 `TestLlmSignalAugment`（8 个用例，
  全部通过，加上此前 38 个，全文件合计 46 个）：无 `llm_helper` 时保持
  纯规则结果、传入合法 LLM 输出后新增主题且规则结果保留、LLM 编造的
  entry_id 被过滤、LLM 输出非 JSON 时优雅回退、LLM 调用抛异常不影响主
  流程、未命中条目太少时不触发 LLM 调用、新主题标题与规则表已有主题
  归一化后合并不产生重复 key、`run_daily_cycle` 按配置开关正确门控
  是否真正调用 LLM。

## 已完成：P3 里程碑（新增，第五项，P3 收尾）——看板拖拽式看板视图

- `apps/mini_agent_kanban/app.py` 新增：
  - `_sortable_available()`：探测可选依赖 `streamlit-sortables` 是否
    安装，未安装（`ImportError`）时返回 `False`；
  - `_render_growth_pending_list()`：把 P1 起就有的"列表 + 采纳/忽略/
    查看报告三按钮"渲染逻辑原样抽成独立函数，作为可选依赖缺失时的
    兜底路径，功能不变；
  - `_render_growth_kanban_dragdrop()`：可选依赖存在时启用，用
    `streamlit_sortables.sort_items(..., multi_containers=True)` 渲染
    "待处理 / 已采纳 / 已忽略"三列拖拽看板；每张卡片的显示标签由
    `_growth_card_label()` 生成（标题 + 置信度 + candidate_id 前 8 位，
    保证同一批渲染里标签唯一，因为同一主题可能因 dismiss 冷却期结束
    重新生成、标题重复）；拖拽后对比"跨列移动前后的 candidate_id 归属"
    而不是无脑对整列重放操作，只对真正发生了跨列移动的卡片调用一次
    `growth_candidate_action`，避免每次 `st.rerun()` 都对本来就已经在
    目标列的卡片重复调用；拖回"待处理"列不生效（后端
    `POST /growth/candidates/{id}/{action}` 本来就只支持 accept/dismiss，
    没有撤销这个操作，方案原文也没有这个需求，所以不強行模拟）。
  - `render_growth_tab()` 里原来的待处理候选渲染改成：
    `_sortable_available()` 为真时走拖拽看板，否则走原列表渲染。
- `apps/mini_agent_kanban/requirements.txt` 新增
  `streamlit-sortables>=0.3.0` 作为**可选**依赖（注释说明未安装时自动
  回退，不影响主功能）。
- `docs/growth-advisor-guide.md` 第 3 节看板用法、第 6 节"当前局限"
  （标题也改成"P1 + P2 + P3 全部完成"）同步更新。
- `tests/test_kanban_growth_dragdrop.py`（新文件，5 个用例，全部通过）：
  覆盖 `_sortable_available()` 返回类型、`_growth_card_label()` 同标题
  不同 candidate_id 生成不同标签且带上置信度信息、三列覆盖
  backlog 全部状态、未安装可选依赖时确实走兜底路径判断。只测纯函数，
  不驱动 Streamlit 组件交互（`_render_growth_kanban_dragdrop` 依赖真实
  `ScriptRunContext` 和前端拖拽事件，不在无头单测范围内）。
- 回归：`tests/test_growth_advisor.py`（46 例）+ 新增 5 例，全部通过。

## P3 里程碑至此全部完成

方案 `next_doc/growth_advisor_design.md` 里标注的 P3 计划项——首次触达
持久化、`excluded_topics` 可视化编辑、`weekly_digest` 真实周摘要打包、
月度复盘跨候选能力地图聚合、`growth_signal_scan` 的 LLM 增强版归纳、
看板拖拽式视图——本轮全部落地。后续如果要继续演进，建议先看用户在
`topic_map`/周摘要实际使用上给的反馈，而不是接着堆新功能。

## 已知取舍/风险

## 已完成：用户反馈追加项——看板"我的数据/诊断信息"面板

真实用户反馈："运行了一天，成长顾问里的数据都是 0"。排查下来这类问题
往往不是 bug，而是候选数=0 本身不区分"扫描过但没匹配到""证据数没达标"
"定时任务压根没跑过""功能被关掉了"这几种完全不同的中间状态——用户在
界面上看不到任何区分信号，只能来问。这次补的不是新的业务功能，是**可
观测性**：

- `growth_advisor.py` 新增 `diagnostics_snapshot(paths, cfg, profile,
  memory_store)`：纯只读聚合，返回三块信息——
  - `config`：当前生效的配置快照（`enabled`/`min_evidence_count`/
    `notification_frequency`/`excluded_topics`/`llm_signal_augment_enabled`
    等），解决"是不是被关掉了/阈值是不是设太高了"这个问题；
  - `signal_scan`：上次扫描时间、扫描窗口天数、每个内置主题各命中了
    多少条记忆（`topic_hit_counts`，**只给计数，不回显 entry_id 或记忆
    原文**——诊断信息也要遵守方案里"知情但克制"的边界）；
  - `memory`：记忆总条数 + 落在扫描窗口内的条数，解决"是不是压根没有
    记忆数据可扫"这个问题。
  - 哪怕从未跑过一次扫描（`profile.derived` 里没有 `growth_focus_areas`）
    也能安全调用，返回全零/空的快照而不是报错。
- `api/routes.py::get_growth_summary` 新增 `diagnostics` 字段，除了
  `diagnostics_snapshot()` 的内容，还额外拼进两个内置 cron job
  （`sys:growth_advisor_daily`/`sys:growth_monthly_retrospective`）的
  `enabled`/`last_run_at`/`next_run_at`/`run_count`/
  `consecutive_skip_count`（通过已有的 `_get_cron_scheduler()` 兜底入口
  获取，跟看板"⏰ Cron 任务"tab 读的是同一个调度器实例），解决"定时
  任务是不是真的没跑"这个此前只能去翻 Cron tab 手动核对 job id 的问题。
  非 daemon 模式下 `cron_jobs` 退化成一条 `_note` 说明，不报错。
- 看板 `render_growth_tab()` 顶部新增 `_render_growth_diagnostics()`：
  一个默认折叠的"🩺 我的数据 / 诊断信息"expander，把上面三块信息 + cron
  job 状态渲染成人类可读的文字列表，不做任何"建议你怎么做"式的解读——
  数字摆出来，"卡在哪一步"交给用户自己判断（这也是为什么标题里带了
  "为什么候选是 0？点开看"这种直白的引导文案，而不是自动给结论）。
- `tests/test_growth_advisor.py` 新增 `TestDiagnosticsSnapshot`（5 个
  用例，全部通过，加上此前 46 个，全文件合计 51 个）：从未扫描过时
  返回合法的空快照、配置值原样透出、扫描后主题命中计数与记忆窗口统计
  正确、快照里不泄露原始 entry_id/记忆内容、`memory_store=None` 时优雅
  降级不报错。
- `docs/growth-advisor-guide.md` 第 3 节看板用法同步更新。

## 已知局限（本项）

- `topic_hit_counts` 只反映**最近一次**扫描的结果，不是历史累计——如果
  用户看到某个主题这次是 0 条，不代表这个主题历史上从来没命中过（历史
  累计信息在 `growth_topic_map()`/月度复盘里，两者概念不同，没有在诊断
  面板里做交叉引用，后续如果发现用户会混淆可以再加一行说明）。
- cron job 状态只做了"读取展示"，没有像 P0-8 那类"主动告警"（见
  `daemon_stability_and_ux_improvement_plan.md`）那样在 job 连续多次
  跳过时主动推送通知——`consecutive_skip_count` 字段已经透出到诊断面板，
  用户需要主动打开才能看到，这是刻意的最小改动，不在本轮引入新的推送
  逻辑。

- `growth_signal_scan` 的关键词表（`_TOPIC_KEYWORDS`）是初始 MVP 覆盖，
  命中面有限；扩展方式是直接往表里加词条，不需要改扫描逻辑，但目前
  没有从用户实际反馈里自动学习新主题词的机制。
- 调研报告的模板兜底内容比较通用（4 个固定小节），信息密度低于 LLM
  生成版本；`enabled=True` 默认开启的前提下这是刻意的取舍，保证零配置
  也能看到端到端效果。
- 反馈置信度衰减（`_feedback_multiplier`）用的是固定的 `0.85` 衰减因子
  和 `0.4` 下限，是经验取值而非从真实用户数据拟合出来的，后续如果发现
  "同一方向被忽略一次就显著更少被推荐"或者相反"衰减太弱、老是被推同一
  个已经明确不感兴趣的方向"，直接调整这两个常量即可，不需要改调用方。
- `_maybe_dispatch_weekly_digest` 的窗口起点是"上次成功推送周摘要的
  时间戳"，不是自然周（周一到周日）；如果用户中途把 `notification_
  frequency` 从别的档位切到 `weekly_digest`，首次触发的窗口固定取"最近
  7 天"，不会回溯更早生成过的报告——这是刻意的取舍（避免把配置切换前
  积压的所有历史报告一次性打包成一条巨长的摘要）。
- `_maybe_dispatch_notification` 的节流状态只按"自然日"计数，没有考虑
  时区跨天的边界情况（用的是 `time.localtime()`，即运行进程所在机器的
  本地时区）；对单机自托管场景够用，多时区/云端多副本部署场景需要另外
  处理。

---

## P4（对应 `next_doc/growth_advisor_improvement_plan_v2.md`）：
关键词表持久化 + 看板展示 profile / 关键词信息

触发背景：两条真实用户反馈——"运行了一天，成长顾问里的数据都是 0"、
"看板应该增加用户的 profile 信息；应该展示成长顾问实际使用的关键词表，
且关键词应该保存到用户 profile 里"。本轮实施 P4-0（前置修复）与 P4-1
（关键词持久化 + 看板展示），P4-2 ~ P4-7 仍是方向级规划，未实施。

### P4-0：`profile.derived` 命名空间冲突修复

- `src/mini_agent/profile.py::UserProfileManager.generate()`：从
  `profile.derived = derived`（整体覆盖）改成合并式更新——只覆盖
  `PROFILE_GENERATED_KEYS`（`summary/tech_stack/habits/
  source_entry_count/updated_at`）这几个自己负责的字段，其余已存在的
  key（例如 `growth_advisor` 写入的 `growth_focus_areas`/
  `growth_topic_keywords`）原样保留。
- 新增模块级常量 `PROFILE_GENERATED_KEYS`，作为这个命名空间约定的唯一
  真源，后续任何模块往 `profile.derived` 加字段前可以对照检查是否会被
  这几个 key 覆盖。
- 搜索确认了 `profile.derived` 现有的所有读写点（`agent/profile.py`
  只读 `summary`、`growth_advisor.py` 读写若干 growth_* 前缀字段、
  `profile.py` 自身），没有发现其他模块假设 `generate()` 后
  `derived` 是"纯 LLM 输出、无外部写入"，改动是安全的。
- 测试：新增 `tests/test_profile.py`，覆盖"生成前手动写入的外部字段在
  `generate()` 后原样保留""`generate()` 只覆盖自己负责的固定字段"两个
  用例。

### P4-1：关键词表持久化 + 看板展示 profile / 关键词信息

- **数据模型**：`profile.derived["growth_topic_keywords"]` 存用户增量
  （`{topic: {keywords, source, confirmed_by_user, added_at}}`，
  `source` 为 `user_added` 或 `llm_learned`），`profile.derived
  ["growth_topic_keywords_removed"]` 存用户隐藏掉的内置主题名列表。
  内置表 `_TOPIC_KEYWORDS` 继续留在代码里，不整表复制进 profile。
- **`_effective_topic_keywords(profile)`**：运行时合并内置表 + 用户
  增量、排除 removed 列表，返回带 source/confirmed_by_user 信息的
  统一结构；`growth_signal_scan()` 改用它替代直接引用模块常量。
- **`_llm_augment_topics()` 归纳出的新主题不再"用完即弃"**：
  `growth_signal_scan()` 在 LLM 增强归纳后，对比增强前后的主题集合，
  把新发现的主题通过 `_persist_learned_topics()` 写入
  `profile.derived["growth_topic_keywords"]`
  （`source="llm_learned", confirmed_by_user=False`）。由于
  `_llm_augment_topics` 目前只返回主题名和命中的 entry_id、不返回具体
  关键词，持久化时用主题名自身兜底作为关键词——保证下次纯规则扫描
  （没有 `llm_helper`）也能命中同一批记忆，测试
  `test_llm_augmented_topic_persists_to_profile_and_is_unconfirmed`
  验证了这条链路。
- **用户操作三函数**：`add_custom_topic_keyword(profile, topic,
  keywords)`（`keywords` 支持字符串或列表，字符串按逗号/顿号/换行切分，
  内部统一走 `_clean_keywords()` 做去空白、大小写不敏感去重）、
  `remove_topic_keyword(profile, topic)`（自定义主题直接从增量表删除，
  内置主题记入 removed 黑名单）、`confirm_topic_keyword(profile,
  topic)`（把待确认的 `llm_learned` 主题标记为已确认，对内置/不存在的
  主题是安全空操作）。
- **`diagnostics_snapshot()` 新增两个字段**：`signal_scan.topics_detail`
  （每个主题的 keywords/source/confirmed_by_user，供看板分组展示）、
  `user_profile`（`summary`/`tech_stack`/`habits`/`updated_at`，不含
  `preferences`）。`topics_tracked` 保持原有字符串列表形状不变（非
  breaking change），`topics_detail` 是新增字段。
- **API**（`src/mini_agent/api/routes.py`）：新增
  `POST /growth/keywords`（新增自定义主题）、
  `POST /growth/keywords/{topic}/confirm`、
  `POST /growth/keywords/{topic}/remove`；`GET /growth/summary` 透传的
  `diagnostics` 里自动带上新增的 `topics_detail`/`user_profile`，未新增
  独立的 profile_snapshot 端点。
- **看板**（`apps/mini_agent_kanban/`）：`client.py` 新增
  `growth_keyword_add/confirm/remove` 三个方法；`app.py` 新增
  `_render_growth_profile_and_keywords()`，在诊断面板下方渲染"🧠 Agent
  对你的了解"（summary/tech_stack/habits）与"🔑 当前关键词列表"（内置/
  待确认/自定义三组，待确认主题带"✅ 保留"/"❌ 不要"按钮，自定义主题带
  "❌ 删除"按钮，底部附一个添加自定义主题的表单）。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestPersistedKeywords`
  （6 个用例：合并逻辑、增删改、LLM 归纳持久化、confirm 状态流转、
  diagnostics 快照内容）；全部通过，且未影响既有 59 项用例中的其余部分。

### P4 已知限制 / 留待后续

- `confirmed_by_user=False` 的待确认主题目前**仍然参与**候选生成（呼应
  设计文档 6 节的倾向），推送节流侧暂未对其单独降权——留给 P4-3/P4-5
  一起细化。
- 按主题类别的反馈学习细化（P4-3）、报告质量分级（P4-4）、通知策略细化
  （P4-5）、看板概念统一（P4-6）、任意主题的自定义黑名单细化（P4-7）
  均未实施，保持方向级规划状态，见 `growth_advisor_improvement_plan_v2.md`
  第 4 节。

### P4-2：关键词表"自动学习稳定后转正"

- `_AUTO_CONFIRM_STREAK = 3`：连续命中阈值常量，经验取值。
- `_update_keyword_learning_streaks(profile, hits)`：在
  `growth_signal_scan()` 每次扫描结束时调用（在 `_persist_learned_topics`
  之后、写回 `growth_focus_areas` 之前），遍历 `profile.derived
  ["growth_topic_keywords"]` 里 `source == "llm_learned"` 且尚未确认的
  主题：
  - 本次扫描 `hits` 里出现该主题（无论是规则命中还是 LLM 增强命中）→
    `consecutive_scan_hits += 1`；未出现 → 直接清零（要求"连续"，
    不是"累计"，中断一次就重新计数）。
  - 达到阈值后自动置 `confirmed_by_user = True`，并额外打上
    `auto_confirmed = True` 标记（区别于用户手动点"✅ 保留"的场景），
    同时把 `consecutive_scan_hits` 清零（已转正的主题不再需要继续维护
    这个计数器）。
  - `user_added`（用户手动添加，创建时已是确认状态）和已确认的
    `llm_learned` 主题不参与这个计数，避免无意义的写入开销。
  - 异常处理：整段逻辑包在 try/except 里，异常走
    `log_exception(..., where="mini_agent.growth_advisor.
    growth_signal_scan_auto_confirm")`，不影响本次扫描结果的返回。
- `_effective_topic_keywords()` / `diagnostics_snapshot()` 的
  `topics_detail` 同步透出 `consecutive_scan_hits`/`auto_confirmed`
  两个新字段，供看板展示"连续命中 N 次，满 3 次自动保留"的进度提示，
  以及给已经自动转正的主题打上"🤖 自动保留"标签，与用户手动确认的
  主题做视觉区分。
- 测试：`tests/test_growth_advisor.py` 新增 `TestKeywordAutoConfirmStreak`
  （3 个用例：连续命中后自动转正、未命中导致 streak 清零、`user_added`
  主题不参与计数），加上此前 P4-0/P4-1 的用例，`test_growth_advisor.py` +
  `test_profile.py` 合计 62 项全部通过。

### P4-3：反馈学习细化（类别级置信度调权）+ 采纳后回访

**类别级反馈**：
- `_TOPIC_CATEGORIES`：内置 7 个主题粗分三类——"技术类"（Python 工程
  实践/前端与可视化/数据分析/系统设计与架构/AI-LLM 应用）、"管理类"
  （项目管理）、"表达类"（写作与表达）；不在表里的主题（用户自定义 /
  LLM 学到）统一归"其他类"，不强行猜类别。
- `_category_dismiss_counts(paths)`：复用 `GrowthFeedbackLedger` 的
  dismiss 记录，反查候选标题所属类别后按类别累加次数（同一类别下不同
  主题的忽略会一起计入同一个计数器）。
- `_category_feedback_multiplier(count)`：衰减因子 `0.95`、下限 `0.7`
  ——比单主题的 `_feedback_multiplier`（`0.85`/`0.4`）明显温和，因为
  类别信号本身比"这个主题被明确忽略过"弱得多。
- `growth_candidate_derive()` 里三个乘子相乘生效：单主题历史 dismiss
  乘子 × 类别历史 dismiss 乘子 × 回访调节乘子（见下），互相独立、不覆盖，
  最终 `confidence = base_confidence * multiplier`。

**采纳后回访**：
- `GrowthCandidate` 新增 `accepted_at`（首次转入 `accepted` 状态时打
  时间戳，之后即使 `attach_report` 等操作刷新 `updated_at` 也不会被
  覆盖）、`followup_status`（`None`/`"progressed"`/`"stalled"`，回访
  只发生一次）两个字段；`GrowthBacklog.set_status()` 负责在状态转为
  `accepted` 且 `accepted_at` 尚未写过时打时间戳。
- 新配置项 `GrowthAdvisorConfig.followup_review_days`（默认 30）。
- `pending_followups(paths, cfg)`：返回已采纳、`accepted_at` 早于
  `now - followup_review_days` 天、且 `followup_status` 仍为 `None`
  的候选，按 `accepted_at` 升序排列。
- `record_followup(paths, candidate_id, outcome)`：`outcome` 限定
  `"progressed"`/`"stalled"`（其余值 `raise ValueError`），写回候选的
  `followup_status`，并追加一条 `action="followup_progressed"` /
  `"followup_stalled"` 的记录到 `GrowthFeedbackLedger`。
- `_followup_adjustment_by_dedupe_key(paths)`：把历史回访结果折算成
  按 `dedupe_key` 的置信度调节系数——`stalled` 温和降权
  （`×0.9`，可累计多次回访）、`progressed` 温和加权（`×1.05`，封顶
  `1.0`），供同一方向因 dismiss 冷却结束后重新生成候选时参考，避免
  "确实采纳过、只是没空推进"的方向被当成普通 dismiss 同等强度对待。
- `diagnostics_snapshot()` 新增 `pending_followups_count` 字段（待回访
  候选数量，不含明细，明细走独立端点）。
- **API**：新增 `GET /growth/followups`（返回待回访候选列表）、
  `POST /growth/followups/{id}/progressed`、
  `POST /growth/followups/{id}/stalled`。
- **看板**：`client.py` 新增 `growth_followups()`/
  `growth_followup_record()`；`app.py` 新增 `_render_growth_followups()`，
  在候选列表之上渲染"📮 该回访一下了（N 个方向）"折叠区块（有待回访项时
  默认展开），每条候选带"✅ 有推进"/"🕒 还没空"两个按钮。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestCategoryFeedbackWeighting`
  （4 个用例：乘子下限、`_category_of` 映射、同类别新主题被拖累、跨类别
  互不影响）、`TestAdoptionFollowup`（5 个用例：`accepted_at` 只在首次
  转入时写入、`pending_followups` 的窗口与状态过滤、`record_followup`
  落盘与台账、非法 outcome 拒绝、回访调节系数方向正确）；加上此前全部
  用例，`test_growth_advisor.py` + `test_profile.py` 合计 71 项全部通过。

### P4 已知限制 / 留待后续（更新）

- 类别映射（`_TOPIC_CATEGORIES`）只覆盖内置 7 个主题，用户自定义/
  LLM 学到的主题一律归"其他类"，不参与类别级反馈的双向影响（既不受
  其他类别拖累，也不拖累其他类别）——如果后续要支持用户给自定义主题
  指定类别，需要扩展 `profile.derived["growth_topic_keywords"]` 的
  数据结构，属于 P4-3 之外的新范围。
- 回访目前是"一次性"的（回答过一次后 `followup_status` 不会再重置），
  没有"再问一次"的机制；如果需要长期跟踪同一方向的多轮进展，需要另外
  设计（比如允许候选被再次采纳后重新进入回访队列），未列入本轮范围。
- 回访节流未接入 `_maybe_dispatch_notification`/`_maybe_dispatch_
  weekly_digest`（回访目前只在看板轮询时展示，不会主动推送提醒用户
  "该回访了"）——留给 P4-5（通知策略细化）一并考虑是否要独立于调研
  报告推送再开一条回访提醒通道。

### P4-4：报告质量分级 / 增量刷新

**报告质量分级**：
- `GrowthAdvisorConfig.report_quality_llm_enabled`（默认 `False`）：
  独立于 `llm_signal_augment_enabled` 的另一个 opt-in 开关。排查代码时
  发现一个此前没被记录过的既有 gap——`run_daily_cycle()` 虽然接受
  `llm_helper` 形参，但调用 `generate_growth_report()` 时从未把它传下去
  （只有 CLI `/growth report`、API `POST /growth/scan` 里手写的一次性
  报告生成路径才会用到 LLM），也就是说 cron 自动生成的报告实际上
  **一直**是模板，即使配置了 `llm_signal_augment_enabled=True` 也不
  影响报告正文——这不是 bug（默认模板是刻意选择），但此前没有一个显式
  开关能让用户选择"cron 自动生成时也用 LLM"，现在补上。
- `run_daily_cycle()` 改为：`report_llm_helper = llm_helper if cfg.
  report_quality_llm_enabled else None`，只有显式打开才会把 `llm_helper`
  传给每个候选的 `generate_growth_report()`。

**增量刷新**：
- `GrowthReport` 新增 `evidence_count_at_generation: int`：
  `generate_growth_report()` 生成报告时把候选当时的 `evidence_count`
  存进去，作为"这份报告是基于多少条证据写的"的快照。
- `reports_needing_refresh(paths, cfg)`：遍历所有候选，只看每个候选
  **当前挂着的那份报告**（`candidate.report_id`），比较候选当前
  `evidence_count` 与报告快照的差值；差值达到
  `report_refresh_min_new_evidence`（默认 3）才计入结果，按新增证据数
  从多到少排序。纯只读聚合，不修改任何状态。
- `refresh_growth_report(paths, candidate_id, llm_helper=None)`：内部
  直接复用 `generate_growth_report()`（新 `report_id`/新文件/新
  `evidence_count_at_generation` 快照），并把候选的 `report_id` 指向
  这份新报告；旧报告不删除、不覆盖，仍留在
  `growth_reports_index.jsonl` 里，只是不再是候选"当前挂着"的那份，
  之后也不会再出现在 `reports_needing_refresh()` 的结果里。
- `diagnostics_snapshot()` 新增 `reports_needing_refresh_count` 字段
  （数量，不含明细，明细走独立端点，与 P4-3 的
  `pending_followups_count` 处理方式保持一致）。
- **API**：新增 `GET /growth/reports/refresh_candidates`（返回待刷新
  报告列表）、`POST /growth/candidates/{id}/report/refresh`（触发刷新；
  路径特意用 `/report/refresh` 两段而不是单段 `refresh_report`，避免
  和已有的 `POST /growth/candidates/{id}/{action}` 通用 accept/dismiss
  路由发生前缀冲突——后者的 `{action}` 是单段路径参数，`report/refresh`
  两段不会被它提前匹配掉）。
- **看板**：`client.py` 新增 `growth_reports_refresh_candidates()`/
  `growth_candidate_refresh_report()`；`app.py` 新增
  `_render_growth_report_refresh_candidates()`，在回访区块下方渲染
  "🔄 有 N 份报告可以更新一下了"折叠区块，每行展示"新增证据 X 条
  （A → B）"和一个"🔄 更新"按钮。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestReportQualityAndRefresh`
  （6 个用例：生成报告时快照证据数、待刷新阈值判断、刷新后新旧报告都
  在历史记录里且候选正确重新指向、未知候选返回 `None`、
  `run_daily_cycle` 默认不使用 LLM 生成报告、显式打开
  `report_quality_llm_enabled` 后确实使用 LLM）；加上此前全部用例，
  `test_growth_advisor.py` + `test_profile.py` 合计 77 项全部通过。

### P4 已知限制 / 留待后续（再更新）

- `reports_needing_refresh()` 只看候选当前挂着的那一份报告，如果同一
  候选被反复刷新多次，历史上的旧报告会在 `growth_reports_index.jsonl`
  里越积越多且不会被清理——目前判断这是可接受的（报告文件本身不大，
  多版本历史反而有留档价值），如果后续觉得需要清理策略，可以再补一个
  显式的"清理未挂载的旧报告"维护动作，不在本轮范围。
- 报告刷新目前是用户手动触发（看板点"🔄 更新"），没有像通知节流那样
  自动决定"要不要主动提醒用户来刷新"——是否需要接入
  `_maybe_dispatch_notification` 留给 P4-5 一并评估。

### P4-5：通知策略细化（类别静音 + 优先级分数）

**类别静音**：
- `GrowthAdvisorConfig.category_notification_frequency`（`dict[str, str]`，
  默认空字典）：key 是类别名（复用 P4-3 的 `_TOPIC_CATEGORIES` 分类：
  "技术类"/"管理类"/"表达类"/"其他类"），value 目前只识别
  `"kanban_only"`——把这个类别完全静音（仍在看板正常展示，但
  `_maybe_dispatch_notification`/`_maybe_dispatch_weekly_digest` 都不会
  主动推送这个类别的报告），其余任意 value 原样透传给全局
  `notification_frequency` 逻辑，等价于未设置覆盖。**不支持**给某个
  类别单独设一个和全局不同的 daily/weekly_digest 频率——那需要拆分出
  按类别独立的节流状态（`last_notify_date`/`notify_count_today` 这些
  目前是全局共用的一份状态），本轮没有看到明确到这个粒度的需求，先不
  做，留在已知限制里。
- `_category_notification_muted(cfg, topic)`：查表判断，`_maybe_dispatch_
  notification()` 在打分之前先过滤掉被静音类别的报告；
  `_maybe_dispatch_weekly_digest()` 在打包摘要之前同样过滤，逻辑保持
  一致（都用 `report.title` 反查类别，报告本身已经带着候选标题，不需要
  再去查候选对象）。

**重要程度分级**：
- `_category_acceptance_rate(paths)`：遍历所有候选，只统计
  `status` 是 `accepted` 或 `dismissed`（已经做出决策）的候选，按
  `_category_of(candidate.title)` 分类累加，返回
  `{类别: accept 占比}`；没有任何决策记录的类别不出现在返回值里
  （调用方应视为中性 0.5，不是 0）。
- `_notification_priority_score(confidence, acceptance_rate)`：
  `score = confidence * (0.7 + 0.6 * rate)`，`rate` 缺失时按 0.5 处理
  （等价于乘以 1.0，既不加分也不减分）——历史上这类方向逢推必采纳
  （`rate=1`）最多把优先级抬到 1.3 倍，历史上几乎从不被采纳
  （`rate=0`）打 0.7 折。
- `_maybe_dispatch_notification()` 改为：先按 `notification_min_
  confidence` 过滤、再按类别静音过滤，剩下的按优先级分数（而不是原始
  置信度）排序取最高的一条；`notify_count_today` 节流和
  `notification_max_per_day` 上限逻辑不变。
- `diagnostics_snapshot()` 新增 `category_acceptance_rate` 字段（只读
  聚合结果，供看板"配置"区块展示"各类别历史采纳率（影响推送优先级）"
  这一行说明，帮助用户理解"为什么这条被优先推送了"，不是新的存储）。
- **看板**：`_render_growth_diagnostics()` 在配置信息下面新增一行类别
  采纳率展示（`category_acceptance_rate` 为空时不显示，不占地方）。
- **测试**：`tests/test_growth_advisor.py` 新增
  `TestNotificationCategoryAndPriority`（5 个用例：`_category_
  notification_muted` 只识别 `kanban_only`、静音类别即使高置信度也不
  推送、置信度打平时历史采纳率更高的类别优先推送、`_category_
  acceptance_rate` 只统计有决策的类别、weekly digest 排除被静音类别后
  没有可打包内容返回 `None`）；加上此前全部用例，
  `test_growth_advisor.py` + `test_profile.py` 合计 82 项全部通过。

### P4 已知限制 / 留待后续（再更新）

- 类别覆盖只支持"完全静音"，不支持"这个类别用 weekly_digest、其他类别
  用 daily"这种更细粒度的按类别独立频率——如果后续需要，得把
  `last_notify_date`/`notify_count_today`/`last_weekly_digest_at` 这些
  节流状态也拆成按类别维度，是相对大的改动，等有明确需求再做。
- 优先级分数目前只在"同一轮里选哪条报告推"这个场景生效（`_maybe_
  dispatch_notification` 内部），没有影响 `run_daily_cycle()` 里"选
  Top-N 候选生成报告"这一步的排序（那一步仍然按 `confidence` 排序）——
  这是有意的：报告生成本身不消耗推送配额，用置信度排序更符合"证据最
  充分的方向优先出报告"的直觉，历史采纳率是"值不值得主动打扰用户"的
  判断，不是"值不值得先研究"的判断，两者语义不同，不应该混用同一个
  排序键。

### P4-6：看板概念统一 + 趋势视图

**概念统一（加提示，不硬合并）**：
- `diagnostics_snapshot()` 新增 `topic_hit_counts_note`：一段固定文字，
  说明"最近一次信号扫描"命中计数是最新一轮快照、跟"成长主题地图"的
  历史累计是两个口径。看板在诊断面板的命中列表下方、以及主题地图
  expander 顶部都展示了对应提示（两处提示文案略有不同，分别贴合各自
  上下文，但传达的是同一件事）。

**趋势视图**：
- 新增独立文件 `growth_topic_trend.jsonl`（`AgentPaths.growth_topic_
  trend_path`），只追加不改写，跟 `growth_backlog.jsonl`（只存当前
  状态，merge 会覆盖历史证据数）分开，避免污染候选队列的数据结构。
- `_record_topic_trend_snapshot()`：`growth_candidate_derive()` 每处理
  一个主题（不管这轮证据是否达标生成/更新了候选）就追加一条
  `{dedupe_key, topic, scanned_at, evidence_count, confidence}`——低于
  阈值时 `confidence` 记为 `None`，但证据数本身仍然是有意义的"正在
  积累"信号，不因为还没达标就不记录（否则走势图会开局一大段空白）。
- `_topic_trend_series(paths, dedupe_key, limit=20)`：按 `dedupe_key`
  查询，时间正序返回，默认只保留最近 20 个点（早期历史丢弃，展示更
  关心"最近走势"）。
- `growth_topic_map()` 每行新增 `evidence_trend` 字段（该主题的走势
  序列）。看板渲染成一行文字箭头（比如 `3 ↗ 5 ↗ 8`），选择文字而不是
  真的画折线图——"不需要复杂图表，文字/简单折线都可以"，文字版本零
  额外依赖、渲染成本最低。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestTopicTrend`（5 个
  用例：低于阈值也记录快照、多轮快照按时间正序累积、`limit` 参数保留
  最近的点、`growth_topic_map` 携带 `evidence_trend`、诊断快照包含
  `topic_hit_counts_note`）；加上此前全部用例，`test_growth_advisor.py`
  + `test_profile.py` 合计 87 项全部通过。

### P4-7：自定义黑名单细化（发现后端早已支持，补齐 UI）

排查这一项之前，先确认了"P4-1 落地后是否已经覆盖到"（计划文档里写的
复核动作）——结论是：**后端在更早的 P3 阶段就已经完整支持**了，
`remove_topic_keyword()` 一直会把内置主题写入 `growth_topic_keywords_
removed` 黑名单（`growth_candidate_derive()` 消费时会跳过），
`POST /growth/keywords/{topic}/remove` 的接口文档也一直写着"🙈 隐藏"字
样。但看板代码里，内置主题那一行从来只是 `st.caption(...)` 纯展示，没
有配套的隐藏按钮，也没有任何地方能看到"我之前隐藏过哪些内置主题、要不
要恢复"——是后端功能完整、前端 UI 一直没跟上的情况，不需要重新设计
黑名单机制本身，只需要补 UI + 一个对称的"恢复"操作。

- `hidden_builtin_topics(profile)`：返回当前被隐藏的内置主题列表
  （从 `growth_topic_keywords_removed` 里筛出仍然是 `_TOPIC_KEYWORDS`
  常量成员的项，按名称排序）。
- `restore_builtin_topic_keyword(profile, topic)`：`remove_topic_
  keyword()` 的对称操作，只是把内置主题从黑名单里摘掉。特意**不**复用
  `add_custom_topic_keyword()`——那个函数是给"用户自己定义一个新主题
  + 关键词"用的，如果拿来"恢复"一个内置主题，会把它转成一条
  `source="user_added"` 的自定义记录，需要用户重新填一遍关键词，而
  内置关键词本来就还完整地留在 `_TOPIC_KEYWORDS` 常量里，不需要重建，
  用错函数反而会制造数据不一致。
- `diagnostics_snapshot()` 新增 `hidden_builtin_topics` 字段。
- **API**：新增 `POST /growth/keywords/{topic}/restore`。
- **看板**：内置主题那一行下面新增"🙈 隐藏某个内置主题"折叠区块（逐个
  主题一个隐藏按钮，用的还是已有的 `growth_keyword_remove` 端点，不是
  新端点），以及"已隐藏的内置主题"列表（划线展示 + "↩️ 恢复"按钮）。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestBuiltinTopicHideRestore`
  （5 个用例：默认没有隐藏项、隐藏后出现在列表里且恢复后从
  `_effective_topic_keywords()` 里重新可见、恢复未知/未隐藏主题返回
  `False`、恢复不会把主题转成自定义条目、诊断快照正确暴露隐藏列表）；
  加上此前全部用例，`test_growth_advisor.py` + `test_profile.py` 合计
  92 项全部通过。

### P4 全部子项完成小结

至此 `growth_advisor_improvement_plan_v2.md` 里 P4-0 ~ P4-7 全部完成
（P4-7 的"完成"是确认 + 补齐 UI 缺口，不是新功能开发）。已知限制汇总
见上面各小节末尾，比较值得后续留意的几条：
  - 类别覆盖（P4-5）只支持"完全静音"，不支持按类别设置独立频率；
  - 回访（P4-3）和报告刷新提示（P4-4）目前都不接入主动推送通道，只在
    看板轮询时展示；
  - 趋势快照（P4-6）文件只追加不轮转，长期运行后需要时可以再补清理
    策略。
这些都不是 bug，是本轮评估后判断"复杂度大于当前收益"而有意搁置的
范围边界，不是遗漏。

### Code Review 补丁（P4-3~P4-7 交付后的自查）

对本轮（P4-3~P4-7）全部改动做了一次代码审查，发现并修复一个真实 bug：

- **`category_notification_frequency`（P4-5 新增的 `dict` 类型配置字段）
  在看板通用配置编辑器里会被渲染成一个纯文本框，编辑后保存的是字符串
  而不是 dict**。根因：`GrowthAdvisorConfig` 的全部字段会通过
  `config_catalog.py` 的反射机制自动出现在看板"⚙️ 配置"里（`_type_name()`
  正确识别出 `"dict"` 类型），但 `apps/mini_agent_kanban/app.py` 的
  `_render_config_field_widget()` 只处理了 `bool`/`int`/`float`/`list`
  四种类型，`dict` 会落进最后的 `else` 分支被当成普通字符串——展示成
  Python `repr()`（`{'技术类': 'kanban_only'}`），保存时 `apply_updates()`
  不做类型校验会原样把这个字符串写进 JSON 配置文件，下次加载
  `GrowthAdvisorConfig` 时这个字段类型就错了（后续任何 `.get()` 调用都
  会抛 `AttributeError`）。排查后确认这不是这次新引入的设计缺陷，而是
  编辑器本身早就存在的通用缺口（`channel_weights` 等其他既有 dict 类型
  配置字段同样受影响，只是此前没有 dict 字段真正被暴露到看板配置页
  而没被发现）——`category_notification_frequency` 是第一个撞上这个坑
  的字段。
- **修复**：给 `_render_config_field_widget()` 加了 `dict` 分支，复用
  `list` 分支"一行一项"的编辑思路，格式是 `key=value`（空行/没有 `=`
  的行忽略），解析结果是 `dict[str, str]`——只覆盖当前唯一的使用场景
  （字符串到字符串的映射），不支持嵌套结构。这个修复对所有 dict 类型
  配置字段生效，不止 `category_notification_frequency`，跟 P3 阶段修
  `list` 类型字段时的做法（也是"通用改动，不止对 growth_advisor 生
  效"）保持一致的处理原则。
- 顺手清理了 `restore_builtin_topic_keyword()` 里一处重复读取
  `derived.get(...)` 的小效率问题（改成读一次存变量），不影响行为。
- 其余复核项（路由前缀冲突、`GrowthCandidate`/`GrowthReport` 的
  `from_dict` 对旧记录缺失新字段时的默认值兜底、`llm_helper` 闭包捕获
  写法是否与 `/growth/scan` 保持一致等）确认均无问题，不需要改动。
- 没有为这个修复新增自动化测试——`_render_config_field_widget()` 是
  纯 Streamlit UI 函数，这个代码库里同一函数的其余分支（`bool`/`int`/
  `float`/`list`）此前也都没有专门的单测覆盖，保持和现有测试范围一致；
  用 `_type_name()` 的直接调用验证了 dict 类型确实会被正确识别为
  `"dict"` 分支（回归风险最高的那一步），`test_kanban_growth_dragdrop.py`
  的 5 个既有用例也确认了 `app.py` 模块改动后仍能正常 import 并通过。

## P5（对应 next_doc/growth_advisor_improvement_plan_v3.md，进行中）

> P5 是一次跳出"补功能"视角的结构性复盘，7 个方向按文档第 3 节的优先级
> 顺序推进。以下按已完成/进行中的顺序记录，未提及的方向（P5-0/P5-2/
> P5-4/P5-6）视为未开工。

### P5-1：新增 dataclass 字段的迁移期检查清单（已完成）

- **问题回顾**：`GrowthReport.evidence_count_at_generation`（P4-4 新增）
  默认值是 `0`，上线当天所有此前生成的旧报告（`from_dict()` 反序列化
  时这个字段在旧数据里缺失，落到默认值 `0`）被 `reports_needing_
  refresh()` 误判为"证据从 0 涨到了现在这么多，该刷新了"，触发一批批量
  误报。
- **修复**：默认值从 `0` 改为 `-1`（哨兵值，语义是"生成时的证据数快照
  缺失"，不是"生成时证据数真的是 0"）；`reports_needing_refresh()` 遇到
  负值直接跳过，不计入待刷新列表。新生成的报告（`generate_growth_
  report()`）永远显式传入真实的 `candidate.evidence_count`（>= 0），
  只有反序列化引入这个字段之前的旧数据才会落到哨兵默认值。
- **文档**：新增 `next_doc/dataclass_field_migration_checklist.md`——
  给以后任何"给已经落盘过历史数据的 dataclass 加字段"的改动提供一份
  通用检查清单（不止服务 growth_advisor），把这次踩坑的教训抽象成可
  复用的流程项，而不是只在这一处打个补丁。
- **测试**：`tests/test_growth_advisor.py` 新增
  `test_legacy_report_missing_evidence_snapshot_not_flagged_for_refresh`
  ——直接从落盘的 jsonl 里 `pop()` 掉这个字段模拟真实的"字段引入之前"
  场景（而不是用当前 dataclass 构造后转 dict，那样测不出真实的反序列化
  缺失），验证不会被误判。

### P5-5：配置加载路径的类型校验兜底（已完成）

- **问题回顾**：`category_notification_frequency`（dict 类型配置字段）
  被看板编辑器错误存成字符串后，`config/param_registry.py::load_
  nested_block()` 对 dict 类型字段是"原样透传"，脏值会静默流入
  `GrowthAdvisorConfig`，直到某个随机调用点（比如 `.get()` 调用）才
  报错——UI 层此前已经修过展示 bug，但加载路径本身没有兜底，同类问题
  以后加别的复杂类型字段时还会复现。
- **修复**：`load_nested_block()` 新增对 dict 默认值/`default_factory`
  字段的显式类型校验：值不是 `dict` 时回退到该字段的默认值，并通过
  已有的 `errors.py::log_exception()`（`level=logging.WARNING`）记一条
  日志，附带字段名/dataclass 名/期望类型/实际类型，不让一个字段的脏
  数据拖垮整个 block 的加载。list 类型字段的回退逻辑同时复用了同一个
  `_fallback_field()` 辅助函数，行为保持一致。`Optional[str] = None`
  这类"合法空值"字段的显式 null 语义不受影响（校验只针对"非 None 但
  类型不对"的情况）。
- **改动范围说明**：这个改动不属于 growth_advisor 模块，是
  `config/param_registry.py` 里所有走 `NESTED_CONFIG_BLOCKS` 通用加载
  的配置块共享的核心路径（`autonomy`/`tech_radar`/`goal_mode`/
  `workdir_knowledge`/... 十几个 block 都会经过这里），growth_advisor
  只是这次撞上具体风险场景的模块，同时也是加固后的回归验证用例。
- **测试**：新增 `tests/test_param_registry_type_validation.py`（5 个
  用例：dict 字段类型错误时回退默认值且不崩溃、正确类型时正常生效、
  字段缺失时用默认值、`Optional[str]=None` 不被误判为类型不匹配、一个
  字段类型错误不拖垮同一 block 里其它字段的加载）；跑了更大范围的
  config/growth/skill 相关子集（136 通过 / 14 失败，逐一确认失败项都是
  与本次改动无关的既有环境缺口——`SkillLoader._auto_activate_blocked`
  属性不存在等，改动前就已存在，复现路径与本次改动无交集）。

### P5-3：自定义/学习到的主题也能参与类别系统（LLM 归类，不用 embedding）（已完成）

- **问题回顾**：`_TOPIC_CATEGORIES` 硬编码只覆盖内置 7 个主题，用户
  自定义和 LLM 学到的主题一律落进"其他类"，类别级反馈学习（P4-3）和
  类别静音/优先级加权（P4-5）对这部分完全不生效。
- **实现**：
  - `GrowthAdvisorConfig.topic_category_llm_enabled`（新字段，默认
    `False`，opt-in，零成本）。
  - `classify_topic_category_llm(topic, keywords, llm_helper)`：复用
    `llm_signal_augment_enabled` 同款的"opt-in、宽松吸收"模式——4 选 1
    粗粒度分类（技术类/管理类/表达类/其他类），解析失败、异常、返回值
    不在 4 个类别里，一律返回 `None`，调用方兜底为"其他类"（不倒退
    现有行为）。明确不引入 embedding：这是粗粒度分类场景，LLM 一次
    调用即可给出可解释的分类理由，边际复杂度比维护类别参考向量、调
    相似度阈值更低。
  - `_learned_topic_categories(profile)` / `_persist_topic_category(
    profile, topic, category)`：归类结果持久化到 `profile.derived[
    "growth_topic_categories"]`（`{topic: category}`），同一个主题不
    重复调用 LLM——分类对同一主题基本是一次性的。
  - `maybe_classify_topic_category(profile, topic, keywords, cfg, *,
    llm_helper=None)`：统一的归类入口，内部判断开关/`llm_helper`
    是否可用、是否是内置主题（内置主题类别始终由硬编码表决定，不接受
    LLM 归类覆盖）、是否已经分类过，全部满足才真正调用一次 LLM。
  - `_category_of(topic, profile=None)`：新增可选 `profile` 参数，
    内置主题优先，其次查 `profile` 的已归类结果，都没有才落"其他类"；
    不传 `profile`（默认值）时行为与改动前完全一致，向后兼容此前所有
    调用方。已在以下消费方就近传入 `profile`（这些函数本来就持有
    `profile`，改动只是多传一个参数）：`_category_dismiss_counts()`、
    `_category_notification_muted()`、`_category_acceptance_rate()`、
    `growth_candidate_derive()`、`_maybe_dispatch_weekly_digest()`、
    `_maybe_dispatch_notification()`、`diagnostics_snapshot()`，
    `run_daily_cycle()` 相应也把 `profile` 传给了两个 dispatch 函数。
  - 触发点（对应方案原文"用户新增自定义主题、或者 LLM 学到的主题被
    确认转正时"）：`add_custom_topic_keyword()` / `confirm_topic_
    keyword()` 新增可选关键字参数 `cfg=None, llm_helper=None`，默认
    值保证 `api/routes.py` 里现有的两处调用（`POST /growth/keywords`、
    `POST /growth/keywords/{topic}/confirm`，目前没有同步 `llm_helper`
    可用）行为完全不变；`run_daily_cycle()` 内部对本轮新增候选里还
    没分类过的主题额外补一次归类，覆盖 cron 触发的自动转正路径
    （`_update_keyword_learning_streaks()` 的自动确认）。
- **已知限制**：`topic_category_llm_enabled=True` 但开关先开后关（用户
  体验了一下又关掉）的场景下，已经持久化到 `profile.derived[
  "growth_topic_categories"]` 的归类结果会被保留（关掉开关只是不再
  产生新的分类，不会撤销已有分类）——这是方案文档"已知风险"一节明确
  倾向的语义，这里按此实现，不是遗漏。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestTopicCategoryLLM`
  （12 个用例，覆盖：无 profile 时行为不变、LLM 输出解析成功/失败
  /异常三种情况、开关关闭时空操作、无 `llm_helper` 时空操作、归类结果
  持久化后 `_category_of()` 能读到、不重复调用 LLM、内置主题不接受
  LLM 归类覆盖、`add_custom_topic_keyword()`/`confirm_topic_keyword()`
  两个触发点分别验证、以及一条端到端用例证明归类结果真的接入了类别级
  反馈学习——同类别下内置主题被 dismiss 过之后，一个刚归类完的自定义
  主题的初始置信度确实被拖低，不是只挂了个标签）；连同此前全部用例，
  `test_growth_advisor.py` 合计 104 项全部通过。

### P5-0：数据生命周期 / 存储卫生（已完成）

- **范围说明**：方案原文对三个只追加文件的处理方式分层建议不同——
  `growth_topic_trend.jsonl`"相对独立、聚合意义不强，可以先单独做降
  采样"；`growth_reports_index.jsonl`/`growth_feedback_ledger.jsonl` 则
  要求"先盘点一遍还有哪些函数依赖读整个文件，确认压缩策略不会静默改变
  现有统计口径"。第一轮先落地了风险更低的前者；这一轮基于审计结论
  （见下方表格）完成了 `growth_reports_index.jsonl` 的分层存储。
  `growth_feedback_ledger.jsonl` 因为消费方全部是"累计统计"语义，
  归档前必须先把旧条目转成等价的持久化聚合计数，改动量级明显大于
  reports_index，继续延后到下一轮单独排期。
- **已实现（growth_topic_trend 降采样，上一轮）**：
  - `_compact_topic_trend_rows(rows, *, now=None)`：纯函数，超过
    `_TREND_RAW_WINDOW_DAYS`（60 天）的旧快照按 `(dedupe_key, 周编号)`
    分桶，桶内只保留 `scanned_at` 最大的一条；60 天窗口内的近期快照
    原样保留、不做任何压缩。
  - `compact_topic_trend_storage(paths, *, now=None)`：读取 → 压缩 →
    （有压缩才）写回的落盘封装，返回被压缩掉的行数，`removed == 0` 时
    不触发任何写操作（幂等、无副作用）。
  - 接入点：`growth_candidate_derive()`（`run_daily_cycle()` 每轮 cron
    唯一会调用它的路径）每轮扫描结束后顺带调用一次——调用频率跟这个
    函数本身的调用频率一致（每天一次或用户手动触发一次 `/growth
    scan`），不会引入额外的高频 IO。
  - 为什么这个压缩策略是安全的：`_topic_trend_series()` 的调用契约本来
    就是"按时间正序，最多取最近 `limit`（默认 20）个点"，60 天窗口内
    正常 cron 频率（哪怕每天一轮）也远超 20 个点，被压缩掉的旧快照
    早就落在这个 limit 窗口之外、从未被这个函数返回过，因此降采样前后
    `_topic_trend_series()` 的任何返回值都不变。
- **已实现（growth_reports_index 分层存储，本轮新增）**：
  - `paths.growth_reports_archive_path`（新文件
    `growth_reports.archive.jsonl`）：只追加，存放归档掉的旧报告。
  - `compact_reports_index_storage(paths, *, now=None)`：归档条件是
    **同时满足**"不是任何候选当前挂着的那份"（`candidate.report_id`
    不指向它）**且**"生成时间超过 `_REPORTS_ARCHIVE_WINDOW_DAYS`
    （180 天）"——只满足其中一条都不归档：刚被刷新替换但还很新的报告
    留一段观察期，防止"报告生成后立刻被下一轮扫描判定过期归档"这种
    边界情况；仍被候选挂着的报告（不管多旧）永远不归档，因为
    `reports_needing_refresh()` 需要读到它。
  - **发现的额外风险点（审计时没预料到，实现时排查出来）**：
    `api/routes.py` 的 `GET /growth/reports/{report_id}` 和
    `cli/commands/growth_cmd.py` 的 `/growth report` 都存在"直接按
    `report_id` 查某一份报告正文"的场景（比如看板里存着旧链接、或
    候选当前挂着的就是这份报告）——如果归档后这两处还是只查活跃索引，
    归档掉的旧报告会从"能查到"变成 404，是一次真实的行为倒退。为此
    新增 `get_report_by_id(paths, report_id)`：先查活跃索引，查不到
    再查归档文件，两处调用都已经切换成这个函数，归档前后查询结果
    不变。
  - `list_reports(paths, *, include_archived=False)`：新增可选参数，
    默认行为（只读活跃索引）与改动前完全一致；`include_archived=True`
    时额外并入归档文件，供"报告生成总数"这类累计统计使用——
    `monthly_retrospective_summary()` 的 `reports_generated` 字段
    （审计时发现的另一处依赖全量计数的地方，此前只统计活跃索引的
    条数，归档上线后如果不改这里，这个数字会在报告被归档的那一刻
    突然"变少"，是另一个真实的行为倒退）已同步改成
    `include_archived=True`。
  - `_maybe_dispatch_weekly_digest()` 的窗口只有 7 天，远小于 180 天的
    归档窗口，确认不受影响，未改动；`reports_needing_refresh()` 只通过
    `candidate.report_id` 查表，从未查过已被替换的旧报告，也未改动。
  - **不在 `run_daily_cycle()` 里自动触发**：跟 topic_trend 降采样不同，
    报告归档改的是"能不能查到某份报告"这个用户可感知的行为，不适合
    悄悄地每天自动跑；留给人工维护脚本或未来单独排期的月度 cron 调用。
- **依赖全量读取的函数清单**（审计结论）：
  | 文件 | 读取方式 | 消费函数 | 处理方式 |
  |---|---|---|---|
  | `growth_reports_index.jsonl` | `list_reports()` 全量读取 | `reports_needing_refresh()`：只看当前挂着的那份；`_maybe_dispatch_weekly_digest()`：7 天窗口；`monthly_retrospective_summary()`：累计计数；看板/CLI 按 id 查询 | 已归档（本轮），归档条件 + `get_report_by_id()` 兜底 + `include_archived` 累计计数三者配合，确认无行为倒退 |
  | `growth_backlog.jsonl` | `GrowthBacklog.load_all()` 全量读取 | `_category_acceptance_rate()`：累计统计 | 否——这个文件本身不是 append-only（`save_all()` 整表重写），数据量受"候选总数"自然限制，不在 P5-0 讨论范围内 |
  | `growth_feedback_ledger.jsonl` | `GrowthFeedbackLedger.all_entries()` 全量读取 | `_dismiss_counts_by_dedupe_key()`/`_category_dismiss_counts()`/`_followup_adjustment_by_dedupe_key()`/`monthly_retrospective_summary()`：全部是累计统计语义 | 否——必须先把"归档"和"转成持久化聚合计数"绑定在一起做，不能只做归档，改动量级比 reports_index 更大，延后到下一轮单独排期 |
- **测试**：`tests/test_growth_advisor.py` 新增
  `TestReportsIndexArchive`（7 个用例：当前挂着的报告不归档、被替换的
  旧报告归档、被替换但还没超过窗口期的报告暂不归档、`get_report_by_id`
  归档后仍能查到 / 查不存在的 id 返回 `None`、`reports_needing_refresh`
  不受归档影响、月度复盘的累计报告数不因归档而变少、空文件时的空
  操作）；连同 `TestTopicTrend` 类新增的 4 个用例，`test_growth_advisor.py`
  合计 115 项全部通过。

### P5-2：置信度模型引入"证据分布度"（已完成）

- **问题回顾**：`_confidence_from_evidence()` 只看 `len(evidence_refs)`，
  "一天内集中出现 5 条"和"5 周内每周出现 1 条"权重完全一样，后者更像
  持续关注。
- **实现**：
  - `_distribution_multiplier(evidence_refs, evidence_timestamps)`：
    按证据对应记忆条目的时间戳分桶（天粒度），`spread_ratio =
    distinct_day_buckets / entries_with_known_timestamp`，线性映射到
    `[_DISTRIBUTION_MIN_MULTIPLIER=0.85, _DISTRIBUTION_MAX_MULTIPLIER=
    1.1]`——全部集中在一天打折，分布在多天加成。跟 `_feedback_
    multiplier`/`_category_feedback_multiplier`/`_followup_adjustment`
    同款"乘法叠加"结构，接入 `growth_candidate_derive()` 的乘子链
    （`topic × category × followup × distribution`）。
  - **未改动 `evidence_refs` 结构**（方案原文明确的风险点：大量既有
    测试直接传字符串列表 `["e1","e2","e3"]`）：时间戳单独存一份
    `profile.derived["growth_evidence_timestamps"]`
    （`{entry_id: created_at}`），由 `growth_signal_scan()` 在当前
    扫描窗口（`window_days`）内的 entries 上整体覆盖式写入（不是
    增量 merge），天然跟随窗口有界，不会无限增长；早于窗口的旧证据
    本来就已经不在这个窗口里。
  - **保底行为**：查不到时间戳的 entry_id（比如证据是很久以前的扫描
    留下、后来滚出窗口的旧记忆）直接忽略，不参与分布计算；一个主题
    的证据里"有已知时间戳的"少于 2 条时（含全部查不到的情况），
    `_distribution_multiplier` 直接返回中性值 `1.0`——没有分布信息
    时不惩罚也不加成，这是保底行为，不是数据缺陷；不传
    `growth_evidence_timestamps` 的 profile（旧数据、以及改动前的
    全部既有测试用例）行为与改动前完全一致。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestEvidenceDistribution`
  （7 个用例：无时间戳数据/单条时间戳退化为中性值 1.0、证据集中一天
  内打折、证据分散多周加成、未知 entry_id 被安全忽略且不影响其余证据
  的分桶计算、`growth_signal_scan` 只把窗口内 entries 的时间戳写入
  `growth_evidence_timestamps`（窗口外的不写入）、`growth_candidate_
  derive` 在证据分布更分散时给出更高置信度（对照组：其余乘子相同，
  唯一差异是分布度）、不带时间戳数据的 profile 置信度与改动前完全
  一致）；连同此前全部用例，`test_growth_advisor.py` 合计 122 项
  全部通过。

### P5-4：回访/报告刷新接入被动信号，减少主动打扰（已完成）

- **问题回顾**：P4-3 的采纳后回访、P4-4 的报告刷新提示都是"到点就问
  用户"，没有先用手边已有的 `growth_topic_trend.jsonl`（P4-6）做一次
  初筛——即便证据数还在涨，也照样弹回访卡片，体感不对。
- **实现**：
  - `_topic_trend_rising(paths, dedupe_key, *, window_days)`：取窗口期
    内的趋势快照，比较窗口内第一个点和最后一个点的证据数，判断"在涨"
    （`True`）/"走平或下降"（`False`）/"数据不够，判断不了"（`None`，
    窗口内少于 2 个快照点）。`None` 是一等公民，不能被简化成 `True`
    或 `False` 中的任何一个——数据不足时应该退回改动前的行为，不能
    悄悄改变语义。
  - `pending_followups()`：到期候选（原有的 status/window 判断不变）
    在纳入结果之前，多一步 `_topic_trend_rising()` 检查——`True` 直接
    跳过（顺延到下一轮，不主动打扰），`False`/`None` 都正常展示。
    **不持久化"已推迟"状态**：每次调用都基于当次快照现算，这样"不再
    涨了自然就展示"是自动生效的，不需要考虑"如何撤销推迟标记"这类
    额外的状态管理语义（对应方案原文"不引入完整的推迟状态机"的取舍）。
  - `followup_question_hint(paths, candidate, *, cfg=None)`：给看板提供
    更贴切的提问文案——走势判断为"走平/下降"（`False`）时换成"最近这个
    方向的记忆变少了，是先放一放了吗？"，其余情况（含判断不了的
    `None`）用原有的默认问法。只影响文案，`record_followup()` 的
    progressed/stalled 两个合法答案不变。
  - `_recent_evidence_delta(paths, dedupe_key, *, window_days)`：从趋势
    快照估算最近 `window_days`（默认 14）天内新增了多少证据——用最新
    快照点减去"窗口边界之前最后一个快照点"（如果全部快照都在窗口内，
    用最早的点做基线，相当于把全部证据都算作"最近"）。快照点不足 2 个
    时返回 `None`（不是 `0`——"判断不了"和"确定没有最近突增"是两回事，
    调用方要能区分）。
  - `reports_needing_refresh()`：每条待刷新记录新增 `recent_evidence_
    delta` 字段，排序键从单纯 `-new_evidence` 改成 `(-recent_evidence_
    delta_or_0, -new_evidence)`——证据是最近突然涨的排在前面，即便总量
    暂时不如另一个"慢慢攒够阈值"的候选；`recent_evidence_delta is
    None`（没有趋势快照数据）时退化为按 `new_evidence` 排序，不会被
    误判成"没有最近突增"而排到最后。
  - `api/routes.py` 的 `GET /growth/followups` 响应给每条候选额外拼了
    `question_hint` 字段（用 `followup_question_hint()` 算出来），
    `GET /growth/reports/refresh_candidates` 不用改代码——新字段跟着
    `reports_needing_refresh()` 的返回值自然透传。
  - 删除了旧的、被新版本完全覆盖的 `pending_followups()` 定义（新版本
    加了趋势检查，函数签名和基础行为——status/window 判断——不变，
    只是在原实现基础上插入了一步判断，之前误留了新旧两份定义，本轮
    一并清理）。
- **已知限制**：`_topic_trend_rising()`/`_recent_evidence_delta()`
  都依赖 `growth_candidate_derive()` 每轮 cron 顺带写入的趋势快照——
  如果一个候选是通过测试直接构造 backlog（不经过 `growth_candidate_
  derive()`）产生的，不会有对应的趋势快照，两个函数都会返回 `None`，
  行为退化为改动前的样子，这是设计上的保底行为，不是遗漏。
- **测试**：`tests/test_growth_advisor.py` 新增
  `TestFollowupAndRefreshPassiveSignals`（10 个用例，覆盖：趋势判断
  数据不足返回 `None`、走势上升/下降两种判断、回访窗口到期但证据还在
  涨时被推迟、走平时正常展示、完全没有趋势数据时正常展示（向后兼容）、
  回访问法在走平时换措辞/默认措辞、`_recent_evidence_delta` 数据不足
  返回 `None`、报告刷新排序优先"最近突增"而不是单纯总量、没有趋势
  数据时退化为按总量排序）；连同此前全部用例，`test_growth_advisor.py`
  合计 132 项全部通过。

### P5-6：候选生成排序里的"探索位"（已完成）

- **问题回顾**：`run_daily_cycle()` 里 Top-N 报告生成此前是纯"利用"
  策略——`sorted(new_candidates, key=lambda c: -c.confidence)[:max_reports]`，
  证据/置信度越高越优先，长期跑下去容易强化用户历史上感兴趣的类别，
  证据没那么强但可能有价值的新方向永远排不上号。
- **产品判断**：方案原文明确这项改动"涉及对'要不要主动打破用户已经
  建立的信任预期'的产品判断，不是纯技术决策"，不建议在没有明确产品
  决策前直接实施。落地时采用的解法是**默认关闭 + opt-in 开关**——技术
  实现和产品判断解耦：代码本身不替用户做"要不要打破克制原则"的决定，
  只是把这个选项做出来，默认行为（`exploration_slot_enabled=False`）
  与改动前逐字节一致，是否打开留给后续更明确的产品决策/用户反馈。
- **实现**（不引入完整 bandit 算法，只做一个轻量版本）：
  - `GrowthAdvisorConfig` 新增两个字段：`exploration_slot_enabled: bool
    = False`（总开关）、`exploration_recent_window: int = 5`（判断"某
    类别最近是否出现过"时往回看的报告数量）。
  - `_recent_report_categories(paths, cfg, profile=None)`：取
    `list_reports()`（不含已归档的旧报告——语义上"最近"本来就不该看
    很久以前的历史）里最新的 `exploration_recent_window` 份，用 P5-3
    已有的 `_category_of()` 算出每份报告标题对应的类别，返回类别集合。
    历史为空时返回空集合（意味着任何类别都算"没出现过"，冷启动时不会
    因为没有历史数据而拒绝探索）。
  - `_select_candidates_for_reports(candidates, cfg, paths, profile=None)`：
    取代原来内联的 `sorted(...)[:max_reports]`，返回
    `list[tuple[GrowthCandidate, bool]]`（候选 + 是否探索位）：
    - 开关关闭，或 `max_reports_per_run < 2`（没有多余名额可以留给
      探索位，硬留会导致"利用位"归零，不是本方案的取舍）：行为与
      改动前完全一致，纯按置信度取 Top-N，全部 `is_exploration=False`。
    - 开关开启且名额 >= 2：先按置信度取前 `max_reports - 1` 个作为
      "利用位"；剩下的候选按原有置信度顺序找**第一个**类别不在
      `_recent_report_categories()` 里的，作为探索位；如果剩下的候选
      一个都没有，或者所有候选类别都已经在最近几轮出现过，退化成正常
      按置信度选（`is_exploration` 全部 `False`，Top-N 总数不变）——
      不强行制造探索，对应方案原文"如果所有类别都出现过，退化成正常
      按置信度选"。
  - `GrowthReport` 新增 `is_exploration: bool = False` 字段（默认值对
    旧数据/未开启开关时生成的报告完全透明，跟 P5-1 的字段迁移检查清单
    要求一致：明确写清楚老数据在默认值下会被怎么解读——这里默认值
    `False` 不会触发任何下游批量提示，是中性的）。
  - `generate_growth_report()` 新增 `is_exploration: bool = False` 参数：
    为 `True` 时给正文加一句 Markdown 引用块标注（"这是我们不太确定你
    会不会感兴趣的新方向，证据还不算多，供参考。"），摘要加
    `[探索方向] ` 前缀——管理用户预期，避免探索位候选被当成"我们觉得
    这个特别重要"，对应方案原文"报告/推送里可以加一句标注"。不传该
    参数时行为与改动前完全一致。
  - `run_daily_cycle()` 改用 `_select_candidates_for_reports()` 选出
    候选列表，逐个把 `is_exploration` 透传给 `generate_growth_report()`；
    `api/routes.py` 的报告详情/列表端点直接走 `report.to_dict()` 序列化，
    新字段自动透出，未额外改动路由代码。
- **范围取舍（未做的部分）**：方案原文提到"候选生成的 Top-N 排序、
  通知推送的优先级选择"两处，本次只落地了前者（Top-N 报告生成）——
  这是方案里给出的主要设计（`max_reports_per_run` 名额分配），后者
  （`_maybe_dispatch_notification()` 的 `_notification_priority_score`
  排序）未改动：探索位候选依然会经过现有的
  `notification_min_confidence` 阈值和类别静音过滤，可能被推送也可能
  只留在看板，这跟改动前的推送节流逻辑保持一致，不额外强推探索位
  报告，属于方案"宁可不推、不为了凑数硬推"的克制原则在推送层的自然
  延续。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestExplorationSlot`
  （8 个用例，覆盖：开关默认关闭时行为与改动前完全一致、
  `max_reports_per_run < 2` 时即便开关打开也不产生探索位、探索位优先
  选最近没出现过的类别、所有类别都已在最近报告里出现过时退化成正常
  按置信度选、空候选列表返回空列表、`_recent_report_categories()`
  正确遵守 `exploration_recent_window` 窗口大小、
  `generate_growth_report()` 在 `is_exploration=True`/默认两种路径下的
  正文与摘要标注、`run_daily_cycle()` 端到端验证开关打开时确实能同时
  产出一份利用位报告和一份探索位报告）；连同此前全部用例，
  `test_growth_advisor.py` 合计 140 项全部通过。

至此 `growth_advisor_improvement_plan_v3.md` 的 7 个方向（P5-0 ~ P5-6）
已全部落地。

---

## N1：诊断面板健康度趋势化（growth_advisor_improvement_plan_v4.md 方向三）

对应 `next_doc/growth_advisor_improvement_plan_v4.md` 方向三 3.1-3.3 节，
是 v4 优先级表里排在第一位（改动量级最小、可作为后续 N2 的验收工具）。

- **新增文件**：`<project_root>/.agent/growth_health_trend.jsonl`
  （`AgentPaths.growth_health_trend_path`），跟 `growth_topic_trend.jsonl`
  是平行但独立的只追加文件，每行一条全局健康度快照。字段严格取自
  `diagnostics_snapshot()` 已经在展示的数字（`total_entries` /
  `entries_in_scan_window` / `backfill_candidates_count` /
  `pending_followups_count` / `reports_needing_refresh_count` /
  `topics_tracked_count`），不引入新的统计口径，避免"趋势图上的数字"和
  "诊断面板上的数字"来源不一致。
- **`growth_advisor.py` 新增**：
  - `_record_health_snapshot(paths, cfg, profile, memory_store)`：调用
    `diagnostics_snapshot()` 取当前快照，抽取上面 6 个字段落盘一条
    `growth_health_trend.jsonl`；只应该在 `run_daily_cycle()` 这个既有
    的每日调用点触发，不应该被其它地方高频调用（docstring 里已明确
    写清楚，对齐方案文档 3.5 节的风险提示）。
  - `_compact_health_trend_rows()` / `compact_health_trend_storage()`：
    跟 `_compact_topic_trend_rows()` / `compact_topic_trend_storage()`
    平行实现，按天分桶降采样。当前阶段快照本身就是"每天最多一条"，
    降采样基本不会真的触发，属于"从设计时就带上治理机制，不留给未来
    补"（对应方案文档"已知风险汇总"第 3 条）。
  - `health_trend_series(paths, *, limit=30)`：返回最近 `limit` 个快照，
    按时间正序，供 API/看板直接消费。
  - `run_daily_cycle()` 收尾处新增一段 `try/except`：调用
    `_record_health_snapshot()` + `compact_health_trend_storage()`，
    失败时静默降级，不影响本轮扫描/候选生成/推送已经产出的返回值——
    对齐"排障用的旁路增强不能反过来影响主流程"的一贯原则（跟
    `CronJobExecutor._write_output_manifest()` 的收尾定位类似）。
- **API 新增**：`GET /growth/health_trend?limit=N`（`api/routes.py`），
  独立于 `/growth/summary`，返回 `{"health_trend": [...]}`。
- **看板新增**：`apps/mini_agent_kanban/client.py` 新增
  `AgentClient.growth_health_trend(limit=30)`；`app.py` 新增
  `_render_growth_health_trend()`，在"🌱 成长顾问"tab 的诊断区块下方
  加一个默认折叠的"📈 健康度趋势"expander，用户展开时才请求接口（减少
  默认加载数据量，对应方案文档 3.3 节的取舍），用 `st.line_chart` 画
  `记忆总条数` / `待回填候选数` / `关注主题数` 三条线；无历史数据时给
  出"至少运行几天后才能看到走势"的提示文案。
- **`AgentPaths` 新增**：`growth_health_trend_path` 属性。
- **测试**：`tests/test_growth_advisor.py` 新增 `TestHealthTrend`
  （6 个用例：`run_daily_cycle()` 正常路径记一条快照且字段正确、
  `enabled=False` 时不记录、`health_trend_series()` 的 `limit` 保留最近
  的点、降采样按天分桶压缩旧点并保留最新的一条、空文件降采样是
  no-op、`diagnostics_snapshot()` 内部抛异常时不影响 `run_daily_cycle()`
  的主返回值且不留下半条脏数据）；连同此前全部用例，
  `test_growth_advisor.py` 合计 165 项全部通过。
- **范围取舍（未做的部分）**：
  - 方向三 3.4 节提到的"跟方向一（cron 记忆回填 M3）联动，展示
    `cron_originated_entries`"以及"跟方向二联动，展示
    `external_signal_topics_count`"——这两个字段依赖 N2/N3 落地后才有
    数据来源，本次 N1 的快照结构预留了后续新增字段的空间（`_append_jsonl`
    写入的是普通 dict，新增字段对旧数据是纯新增列，不需要迁移），但
    暂不提前加空字段占位。
  - 方向三 3.1 节原文示例代码里提到的"每天最多一条"限制，本次实现里
    没有做"同一天重复调用只记一条"的显式去重——`run_daily_cycle()` 本身
    在 cron 场景下就是每天触发一次，手动 `/growth scan` 触发时多记几条
    快照不构成问题（`health_trend_series()` 只是取最近若干个点，多几个
    同一天的点不影响趋势图的可读性），如果未来需要严格"每天一条"的语义，
    可以在 `_record_health_snapshot()` 里加一个"距上次记录不足 N 小时则
    跳过"的判断，当前阶段不引入这个复杂度。

---

## N2：cron 记忆回填 M3（growth_advisor_improvement_plan_v4.md 方向一）

对应 `next_doc/growth_advisor_improvement_plan_v4.md` 方向一，实现方案 A
（cron 任务收尾时直接把最后一步产出摘要成一条记忆，不经过
`Session`/`summary` 中转），是 v4 优先级表里排在第二位、也是用户最初
反馈"daemon 跑的任务没有 memory"里更核心的一半。

- **新增函数（`evolution/memory_backfill.py`）**：
  - `generate_summary_from_text(text, llm_client, *, task_template="",
    max_chars=4000)`：对单段文本（而不是完整 `history`）做摘要，跟
    `generate_summary_offline()` 共享同一套 prompt 模板
    （`user/session_summary_request` + `system/summarizer`）。
    `task_template` 会拼进摘要输入的前缀（`[本次任务] ... [任务产出]
    ...`），避免摘要读起来是"做了后续处理"这种没有上下文的碎片。空
    文本直接返回空字符串，不发起 LLM 调用。
  - `_is_similar_to_recent_cron_summary(memory_backend, job_id, summary,
    *, similarity_threshold=0.85)`：[1.5 节 幂等与去重] 复用
    `StuckDetector` 同款的 `difflib.SequenceMatcher` 文本相似度判断
    （规则实现，不是 embedding），只跟该 job（按 `cron:<job_id>:`
    前缀过滤 `memory_backend.all_entries()`）最近一条已生成的记忆摘要
    比较，超过阈值判定为高度雷同。查询失败时静默返回 `False`（去重
    是锦上添花，不能成为记忆生成路径上的新故障点）。
  - `backfill_cron_run(job_id, run_id, last_text, *, memory_backend,
    llm_client, model="", task_template="", similarity_threshold=0.85)`：
    主入口，串联"生成摘要 → 去重检查 → 构造 `MemoryEntry` → upsert"。
    `session_id` 合成规则延续方案文档第 4 节风险项 2 已核实的结论：
    `cron:<job_id>:<run_id>` 前缀 + 冒号格式，跟真实 `Session.id`
    （纯十六进制无分隔符）取值空间不相交，`memory_store.py`/
    `growth_advisor.py` 对 `session_id` 全部是字符串相等比较或展示
    切片，不做格式解析。摘要为空或被判定去重时返回 `None`（不写入）。
- **`evolution/cron_job_executor.py` 改动**：
  - `CronJobExecutor.__init__` 新增三个可选属性
    `memory_backfill_cfg`/`memory_backend`/`llm_client`（默认
    `None`），跟已有的 `circuit_breaker` 走同样的"构造后属性赋值"
    接入模式，**不改变构造签名**——所有既有的 `CronJobExecutor(paths)`
    直接实例化写法（包括测试里的写法）不受影响。
  - 新增 `_maybe_backfill_memory(job, run_id, last_text)`：读三个属性，
    任何一个缺失（`memory_backfill_cfg is None`、
    `cron_run_backfill_enabled=False`、`memory_backend`/`llm_client`
    为 `None`）直接跳过；调用 `backfill_cron_run()`，整个方法异常
    兜底（`log_exception` 记录，不向上抛）。
  - `run_job()` 的 `finally` 块内，紧跟在 `_write_output_manifest()`
    之后新增一段调用：**严格限定** `final_status == STATUS_IDLE`
    （不含 `timed_out`/`needs_human_review`）且 `last_text.strip()`
    非空才会调用 `_maybe_backfill_memory()`——异常/卡死/超时的运行
    不产出记忆，对齐方案文档"这类运行本身信息价值低，强行摘要只会
    污染成长顾问的信号扫描"的判断。
- **`evolution/cron_job_runner.py` 改动**：`_run_job_thread()` 里在
  `executor.circuit_breaker = self._circuit_breaker` 之后新增三行
  属性赋值：`executor.memory_backfill_cfg =
  getattr(self._base_cfg, "memory_backfill", None)`、
  `executor.memory_backend = getattr(agent, "_memory", None)`、
  `executor.llm_client = getattr(agent, "_llm", None)`——`agent` 是
  `build_cron_agent()` 为本次 job 独占构造的一次性实例，它持有的项目
  记忆后端和 LLM 客户端就是记忆生成需要的全部依赖，不需要额外构造。
- **配置**：复用已有的 `MemoryBackfillConfig.cron_run_backfill_enabled`
  字段（`config/models.py`，默认 `True`，`config/loader.py` 已经在读
  `memory_backfill` 配置块，本次不需要新增迁移逻辑）——方案文档草稿
  阶段预留的字段名是 `cron_session_backfill_enabled`，实现时确认
  `config/models.py` 里已有语义相同但命名为
  `cron_run_backfill_enabled` 的字段（`memory_backfill_and_profile_
  update_plan.md` M1/M2 落地时已经加上，只是当时没有代码实际读取），
  复用它而不是另起一个重名字段。
- **测试**：
  - `tests/test_memory_backfill.py` 新增 `TestGenerateSummaryFromText`
    （3 个用例：空文本不触发 LLM 调用、正常摘要去除首尾空白、
    `task_template` 确实拼进了 prompt 输入）+ `TestBackfillCronRun`
    （6 个用例：正常写入且 `session_id` 格式正确、空摘要不写入、
    连续高度雷同摘要第二条被去重跳过、明显不同的摘要都写入、去重只
    跟同一个 job 的历史比较不跨 job 互相影响、`all_entries()` 查询
    失败时不阻止本次写入）。
  - `tests/test_cron_job_workspace_and_executor.py` 新增
    `TestCronJobExecutorMemoryBackfill`（8 个用例：正常收尾写入一条
    `cron:<job_id>:<run_id>` 记忆、`timed_out` 不写入、
    `needs_human_review` 不写入、空白 `last_text` 不写入、
    `memory_backfill_cfg` 保持默认 `None` 时静默跳过且不影响主流程、
    `cron_run_backfill_enabled=False` 时跳过、`memory_backend`/
    `llm_client` 缺失时静默跳过、记忆生成内部抛异常时不影响
    `run_job()` 的 outcome/状态落盘结果）。
  - 全部新增用例（`test_memory_backfill.py` 14 项、
    `test_cron_job_workspace_and_executor.py` 34 项）连同既有用例
    全部通过；额外跑了 `test_cron_job_runner.py`/
    `test_cron_job_runner_resource_arbiter.py`（19 项）确认属性赋值
    改动没有影响调用方的既有测试；`test_growth_advisor.py`
    （165 项）作为回归基线一并跑过，均通过。
- **范围取舍（未做的部分）**：
  - 方向一 1.6 节提到的"诊断面板展示 cron 产出记忆 vs 真实交互 session
    的比例"——依赖的字段（区分 `session_id` 是否带 `cron:` 前缀）在
    `diagnostics_snapshot()`/`_record_health_snapshot()`（N1）里还没有
    新增对应字段，留给后续需要时再补，`growth_health_trend.jsonl` 的
    快照结构对新增字段是纯新增列，不需要迁移。
  - 1.7 节 M4（cron 全面持久化 session）依然不做，维持方案文档"先
    观察 M3 至少一个迭代周期"的判断。
  - 未新增 CLI/看板层面的"本轮 cron 记忆回填统计"展示——`backfill_
    cron_run()` 返回值（`Optional[MemoryEntry]`）目前只在
    `_maybe_backfill_memory()` 内部消费后丢弃，调用方（cron 收尾流程）
    本身也不需要这个返回值参与任何决策；如果未来要展示"本次 cron
    触发是否新增了记忆"，可以在 `CronJobExecutor.run_job()`
    的 `RunOutcome` 里加一个可选字段，当前不属于 M3 范围。


---

## N3：关键词表 → tech_radar 种子同步（growth_advisor_improvement_plan_v4.md 方向二 2.2 节）

对应 `next_doc/growth_advisor_improvement_plan_v4.md` 方向二 2.2 节，
是 v4 优先级表里排在第三位、"改动集中、默认关闭零风险"的一个纯新增
桥接函数。**只实现 2.2 节（关键词表 → tech_radar 种子），不实现 2.3/
2.4 节（外部资讯作为展示/报告背景，划归 N4）**。

- **配置新增**：`GrowthAdvisorConfig.sync_confirmed_topics_to_tech_
  radar_enabled`（`config/models.py`，默认 `False`）——这会实际修改
  `agent_config.json` 的内容，属于有实际外部效果的写操作，对齐方案
  文档 2.2 节"不应该默认开启"的要求。
- **`config_catalog.py` 新增两个函数**（2.5 节风险项 1 要求"必须走跟
  看板保存配置完全一致的路径"，但 `apply_updates()` 按设计明确不收录
  list/dict 类型字段，`TechRadarConfig.keywords` 是 list，因此新增
  平行但独立的写路径，而不是硬塞进 `apply_updates()`）：
  - `apply_list_seed_merge(raw_file_cfg, block, field_name, new_items)`：
    对 list 字段做幂等合并（大小写不敏感去重），返回
    `(new_cfg, added_count)`，深拷贝、不修改传入对象，跟
    `apply_updates()` 的既有约定一致。
  - `write_config_file(config_path, raw_cfg)`：从 `patch_self_config()`
    里抽出来的原子写入逻辑（临时文件 + `os.replace`），供
    `apply_list_seed_merge()` 的调用方和 `PATCH /v1/self/config` 共用
    同一份实现——顺手把 `api/routes.py::patch_self_config()` 原地的
    写入代码改成调用这个新函数，消除重复实现（`tests/
    test_kanban_config_routes.py` 6 项回归测试确认这个重构没有改变
    该接口的行为）。
- **`growth_advisor.py` 新增**：
  - `sync_confirmed_topics_to_tech_radar(paths, profile, cfg) -> int`：
    调用 `_effective_topic_keywords(profile)` 取所有
    `confirmed_by_user=True` 的主题（含内置主题——内置主题在
    `_effective_topic_keywords()` 里恒为 `confirmed_by_user=True`，
    是既有语义，本次沿用不做特殊处理）的关键词，合并调用
    `config_catalog.apply_list_seed_merge()` + `write_config_file()`
    写入 `TechRadarConfig.keywords`。返回本次新增的种子数量。只增不
    减（不做反向删除，理由见方案文档 2.2 节：用户隐藏成长顾问主题
    不等于不想再关注该方向的外部动态）。`paths.project_root` 缺失时
    直接返回 0，不报错。
  - `run_daily_cycle()` 收尾处新增一段 `try/except`：仅当
    `cfg.sync_confirmed_topics_to_tech_radar_enabled` 为真时调用，
    异常静默降级，不影响本轮扫描/候选生成/推送已经产出的结果——跟
    N1 的健康度快照收尾是同一个"旁路增强不能反过来影响主流程"模式。
- **测试**：
  - 新增 `tests/test_config_catalog_list_seed_merge.py`（8 个用例：
    空 block 合并、大小写不敏感幂等去重、不修改传入的原始 dict、
    合并时保留 block 内其它字段、空白项被跳过、无新增项时
    `added == 0`；`write_config_file()` 写入可读、覆盖已有文件后
    临时文件不残留）。
  - `tests/test_growth_advisor.py` 新增 `TestSyncConfirmedTopicsToTechRadar`
    （7 个用例：正常同步已确认自定义主题关键词且不同步未确认主题、
    重复调用幂等、空 profile 时仍会同步内置主题关键词（预期行为，不是
    bug）、开关关闭时 `run_daily_cycle()` 不产生 `agent_config.json`
    写入、开关开启时确实写入、同步内部异常不影响
    `run_daily_cycle()` 的主返回值、`project_root` 缺失时返回 0）。
  - `test_kanban_config_routes.py`（6 项，验证 `patch_self_config()`
    重构没有改变行为）+ `test_growth_advisor.py`（172 项）+
    `test_config_catalog_list_seed_merge.py`（8 项）合计 186 项全部
    通过。
- **范围取舍（未做的部分）**：
  - 2.3 节（外部资讯命中 → 成长顾问候选展示补充信号，
    `_external_signal_count_for_topic()`）、2.4 节（调研报告可选纳入
    外部资讯背景，`cfg.report_include_external_context`）——按优先级
    表排在 N4，依赖 2.2 节跑出的种子同步先验证数据链路通畅，本次不做。
  - 2.5 节风险项 2（种子池膨胀，`daily_seed_limit` 不变导致覆盖一轮
    的周期变长）——按方案文档"可以接受，不算 bug"的判断，不在本次
    引入任何缓解机制（比如动态提升 `daily_seed_limit`），维持
    `tech_radar_search.py` 原有的轮转游标机制不变。
  - 没有在看板/CLI 层面新增"本轮同步了 N 个新种子"的展示——
    `sync_confirmed_topics_to_tech_radar()` 的返回值目前只在
    `run_daily_cycle()` 内部消费后丢弃；后续如果要展示，可以并入
    `run_daily_cycle()` 的返回结构或者健康度快照（N1）的字段，当前
    不属于 N3 范围。

---

## N4：外部资讯作为展示/报告背景（growth_advisor_improvement_plan_v4.md 方向二 2.3/2.4 节）

对应方向二 2.3 节（外部资讯命中 → 成长顾问候选展示补充信号）+ 2.4 节
（调研报告可选纳入外部资讯背景），v4 优先级表里排在第四位、"仅展示、
不影响判断"的克制设计，收益是锦上添花性质。

- **`growth_advisor.py` 新增**：
  - `_external_signal_count_for_topic(paths, topic, keywords, *,
    window_days=30) -> int`：只读聚合，扫描 `wiki/` 下 `source_kind`
    属于 `external_watch`/`external_search`（`wiki/world_writer.py` 的
    `EXTERNAL_WATCH_SOURCE_KIND`/`EXTERNAL_SEARCH_SOURCE_KIND`）的页面，
    复用 `growth_signal_scan()` 对记忆做关键词匹配的同一套简单规则
    （小写子串匹配，haystack 取 `page.id + body 前 2000 字 + tags`）。
    时间窗口过滤直接对 `created`/`updated`（`date.today().isoformat()`
    格式的 `"YYYY-MM-DD"` 字符串，见 `wiki/writer.py`）做字符串比较，
    等价于按日期比较，不需要额外解析成 `datetime` 对象。单个页面解析
    失败静默跳过，不重复承担 `wiki/quarantine.py` 的隔离区治理职责。
    **只统计，不改变任何置信度计算**——这是本函数存在的唯一边界。
  - `generate_growth_report()` 新增两个可选参数 `profile`/`cfg`：仅当
    `cfg.report_include_external_context` 为真、且报告走 LLM 生成路径
    （`llm_helper` 非 `None`）时，才会把 `_external_signal_count_for_
    topic()` 统计到的数量拼进喂给 LLM 的 prompt，并显式要求"这些只是
    外部背景信息，报告的核心判断仍然要基于用户自己的记忆证据"。**不
    改变** `candidate.confidence`/`evidence_count_at_generation` 等
    落盘字段——只影响 prompt 输入，不影响候选排序/推送判断，这是
    2.3/2.4 节反复强调的克制点。两个参数缺失、或走模板路径
    （`llm_helper is None`）时整体跳过，向后兼容此前所有不传这两个
    参数的调用方（包括 `api/routes.py` 里 `refresh_growth_report()`
    的既有调用点——该处暂未升级传入 `profile`/`cfg`，行为保持不变）。
  - `refresh_growth_report()` 同步新增透传的 `profile`/`cfg` 可选参数。
  - `run_daily_cycle()` 生成 Top-N 报告的调用点（`_select_candidates_
    for_reports()` 之后）新增 `profile=profile, cfg=cfg` 透传，是
    `report_include_external_context` 实际生效的唯一接入点。
- **配置新增**：`GrowthAdvisorConfig.report_include_external_context`
  （默认 `False`），**独立于** `report_quality_llm_enabled`（用户可能
  想要更好的报告质量但不想引入外部背景，两者应该能各自控制，对齐
  方案文档 2.4 节的明确要求）。
- **测试**：`tests/test_growth_advisor.py` 新增两个测试类：
  - `TestExternalSignalCountForTopic`（5 个用例：命中关键词的页面计入、
    非 external_watch/external_search 的 `source_kind` 被忽略、窗口期
    外的旧页面被忽略、空关键词列表直接返回 0 不扫描、wiki 目录不存在
    时返回 0）。
  - `TestReportIncludeExternalContext`（6 个用例：开关关闭时 prompt
    不包含外部背景段落、开关打开且确有外部信号命中时 prompt 包含该
    段落、开关打开后候选的 `confidence` 数值不变、缺失 `profile`/
    `cfg` 时安全降级为改动前行为、模板路径下开关不产生任何影响）。
  - 全部新增用例（11 项）连同既有 `test_growth_advisor.py`
    （172 项）、`test_config_catalog_list_seed_merge.py`（8 项）、
    `test_kanban_config_routes.py`（6 项）合计 196 项全部通过；另外
    跑了 `test_external_input_knowledge_extractor.py`（6 项，验证没有
    影响 `source_kind` 的既有写入逻辑）确认无回归。
- **范围取舍（未做的部分）**：
  - **没有把 `_external_signal_count_for_topic()` 接入
    `growth_topic_map()` 的看板展示**（2.3 节原文提到"候选卡片上加一句
    外部世界最近 N 条相关资讯"）——`growth_topic_map()` 按
    `dedupe_key`（归一化标题）聚合历史候选，本身不持有该主题当前的
    关键词列表，接入需要额外把 `profile`/关键词表传进这个纯只读聚合
    函数、并为每个主题都跑一次 wiki 全量扫描（`discover_pages()` +
    逐页 `parse_page()`），开销和改动面都明显超出"仅展示"的定位；
    2.3 节本身也只要求"新增一个只读聚合函数"，并未强制要求接入某个
    具体展示位。当前先把可复用的聚合函数做好、并在 2.4 节报告生成
    路径验证了数据链路通畅，看板展示位留给后续需要时再接，属于
    "基础设施先行，展示位按需接入"的取舍，不是遗漏。
  - `refresh_growth_report()` 在 `api/routes.py` 里的既有调用点没有
    升级传入 `profile`/`cfg`——该路由本身没有现成的 `profile` 对象
    可用（需要额外从 bridge/agent 上下文取），且"重新生成单份报告"
    这个场景本身调用频率低、用户可以接受暂时不带外部背景，不属于
    N4 的必要范围，后续如果需要可以单独补一个小改动。

## 诊断数据源修复 + 用户语言检测（growth_advisor_diagnostics_and_language_fix_plan.md）

- **触发背景**：用户反馈诊断面板"记忆总条数：0"跟健康度趋势里的
  "记忆总条数：99"对不上，且"LLM 增强调用状态"一直显示从未触发过；
  同时反馈"Agent 对你的了解"画像用英文生成，没跟随用户实际使用的语言。

### 方向一：`MemoryStore` 构造 bug

- **根因**：`src/mini_agent/api/routes.py`（`/growth/summary` 与"立即
  为我看看"手动扫描端点）、`src/mini_agent/cli/commands/growth_cmd.py`
  三处都写成了 `MemoryStore(paths)`——把整个 `AgentPaths` 实例当成
  `MemoryStore.__init__` 的 `path` 参数传了进去，`self._path` 因此不是
  一个真实文件路径，`all_entries()` 加载失败又被 `diagnostics_snapshot()`
  里的 `try/except Exception: entries = []` 静默吞掉，导致诊断面板永远
  显示 0 条记忆、手动扫描永远 0 命中、LLM 信号增强因"未匹配记忆数不足"
  永远被跳过。健康度趋势里的真实条数是 cron 任务通过 `memory_factory`
  正确构造的 store 记录的，跟诊断面板的数据源实际上是两套不同的（一套
  正确一套错误的）构造逻辑，并非统计口径本身不一致。
- **改动**：
  - `src/mini_agent/perception/memory_factory.py` 新增
    `build_default_memory_store(paths)`，统一从 `AgentPaths` 正确构造出
    project scope（`paths.workdir_memory`）的只读 `MemoryStore`。
  - `src/mini_agent/api/routes.py` 两处调用点、
    `src/mini_agent/cli/commands/growth_cmd.py::_get_memory_store` 均
    改为调用该工具函数。
- **测试**：`tests/test_growth_diagnostics_and_lang_fix.py::
  TestBuildDefaultMemoryStore`（2 个用例：写入后能跨实例读回、内部路径
  确实是 `paths.workdir_memory` 而不是 `AgentPaths` 实例本身）。

### 方向二：用户常用语言检测

- **设计**：不依赖"跟记忆条目同语言"这条隐式弱约束（一旦上游摘要文本
  本身语言跑偏，下游就没有基准可跟），改为显式检测 + 落盘：
  - 新增 `src/mini_agent/utils/lang_detect.py::detect_primary_language()`
    ——基于 Unicode 区间字符占比的轻量启发式（CJK 表意文字 / 假名 /
    谚文），无外部依赖、无 LLM 调用，支持 zh/ja/ko，其余退回 `en`。
  - `src/mini_agent/profile.py::UserProfileManager.generate()` 每次刷新
    画像时，用本轮参与 prompt 的 `delta_entries` 摘要文本重新检测，写入
    `profile.derived["preferred_language"]`；检测结果为默认值（`en`）
    且已有上一版检测结果时保留旧值，避免单次英文偏多的批次把语言冲回
    默认值（"sticky"策略，减少语言在批次间来回跳变）。
  - `prompts/system/profile_summarizer.md` 新增 `{{preferred_language}}`
    变量，明确要求模型直接使用检测结果输出，而不是自行根据记忆条目的
    语言判断。
  - `evolution/growth_advisor.py::diagnostics_snapshot()` 的
    `user_profile` 快照新增 `preferred_language` 字段；
    `apps/mini_agent_kanban/app.py::_render_growth_profile_and_keywords`
    在"Agent 对你的了解"区块下顺带展示这个检测结果，方便用户核对。
- **范围取舍**：本次只把检测结果接入了画像生成这一处 prompt；成长顾问
  报告生成、月度复盘等其它面向用户的生成类 prompt 尚未逐一排查接入，
  留给后续需要时再做（`profile.derived["preferred_language"]` 已经是
  可直接复用的落盘字段，接入成本很低）。
- **测试**：`tests/test_growth_diagnostics_and_lang_fix.py`：
  - `TestDetectPrimaryLanguage`（5 个用例：空输入、中文、英文、日文
    优先于中文判定、英文长文本里夹杂少量中文字符不误判）。
  - `TestProfileGeneratePreferredLanguage`（1 个用例：`generate()` 用
    中文记忆摘要跑一遍后 `derived["preferred_language"] == "zh"`）。

### 测试结果

- 新增测试文件 `tests/test_growth_diagnostics_and_lang_fix.py`
  （8 项）全部通过。
- 既有 `tests/test_growth_advisor.py`（182 项）跑下来 181 项通过，
  1 项失败（`TestTopicTrend::
  test_compact_topic_trend_storage_downsamples_old_points`）——该用例
  跟本次改动的两个方向（`MemoryStore` 构造 / 语言检测）均无关联，是
  跟自然日边界相关的既有时间敏感测试，本次未做改动也未修复，如需要
  应作为独立 issue 跟进。

## 自主检索与学习素材生成改进（growth_advisor_autonomous_search_and_material_improvement_plan.md，阶段一/阶段二/阶段三/学习素材分层/外部世界变化驱动刷新均完成）

- **触发背景**：针对"如何更好地自主检索、生成报告和学习素材"的专项
  复盘，定位到主动检索链路"查得浅、抽取结果没被用上"两处具体问题，
  完整方向拆解、验收标准见该计划文档本身，本条只记录实施摘要（详情
  不在此重复维护）。
- **阶段一**：`_active_search_excerpts_for_topic()` 改为优先用已抽取的
  结构化 `EntityCandidate`/`FactCandidate` 构造摘录（新增
  `_excerpts_from_extracted_candidates()`），抽取为空时退回原始文本
  截断兜底；不改变 `queue_entities`/`queue_facts` 落盘行为。
- **阶段二**：激活此前预留但未消费的 `report_active_search_max_calls`
  字段（新增 `_build_active_search_queries()`），默认值 `1` 保持改动前
  行为，调大后按关键词表追加查询角度，各角度独立容错、摘录按 `id`
  去重合并；`generate_growth_report()`/`_maybe_run_cron_triggered_
  active_search()` 两个调用点透传该配置。
- **阶段三**（本轮新增）：报告正文写完后新增一次"生成后自检"——
  `generate_growth_report()` 记录本次实际拼进 prompt 的摘录列表
  （`used_excerpts`），正文由 LLM 生成且摘录非空时调用新增的纯函数
  `_check_report_citations(body, excerpts)`，用双向子串匹配核对正文
  里『（参考：xxx）』标注是否对得上摘录 id（容忍 LLM 简写 id 的合理
  情况，避免把简写误判成编造），结果写入 `GrowthReport.citation_check`
  新字段（`excerpts_total`/`cited_count`/`citation_mentions_total`/
  `hallucinated_refs`）。只做诊断记录，不阻断报告生成、不影响候选
  排序；`citation_check` 为 `None` 表示"没有可核对的引用"（外部背景
  未开启/没拿到摘录/走模板兜底），旧数据反序列化缺该字段时同样落到
  `None`，向后兼容。
- **阶段三后续：生成后自检结果的展示**（本轮新增）：`diagnostics_
  snapshot()` 新增 `citation_check` 区块（新增 `_citation_check_
  diagnostics_summary()`），汇总活跃索引里带 `citation_check` 的报告——
  `reports_checked`/`reports_with_hallucination`/`total_excerpts_
  offered`/`total_excerpts_cited`/`citation_hit_rate`（分母为 0 时是
  `None`，跟"命中率 0%"区分开）；`GET /growth/reports/{id}` 不需要
  改动，`report.to_dict()` 自动带上新字段；CLI `/growth report
  <candidate_id>` 打印正文后，若报告带 `citation_check` 追加一行摘要
  （命中比例 + 编造引用列表，或"未检测到编造引用"），不带该字段时
  不打印任何额外内容。
- **报告与学习素材分层**（本轮新增）：新增 `GrowthLearningMaterial`
  dataclass（学习路径 + 资源清单 + 第一个可执行任务）跟 `GrowthReport`
  平行独立；新增 `generate_learning_material()`（可选复用已有报告
  `summary` 作为背景，LLM 结构化 JSON 生成 + 规则模板兜底）、
  `list_materials()`/`get_material_by_id()`；`GrowthCandidate` 新增
  `material_id` 字段，`GrowthBacklog` 新增 `attach_material()`；
  `AgentPaths` 新增 `growth_materials_index_path`/`growth_material_
  path()`；CLI 新增 `/growth material <id>` 子命令；API 新增 `POST
  /growth/candidates/{id}/material/generate` 与 `GET /growth/
  materials/{id}`。
- **外部世界变化驱动的刷新**（本轮新增，基础机制，不接入看板/CLI
  展示）：`GrowthReport` 新增 `external_excerpt_fingerprint` 字段
  （摘录 id + 内容前 12 位 md5 指纹，不存原文）；新增 `_compute_
  excerpt_fingerprint()`/`external_signal_drift_for_report()`（纯
  只读比对，不触发新检索/LLM 调用）；`reports_needing_refresh()`
  新增 `profile=` 参数，跟证据数信号是 OR 关系，命中时行里带
  `external_drift` 字段；新增配置项 `report_external_drift_refresh_
  enabled`（默认关闭）/`report_external_drift_min_changes`（默认
  1）；API `GET /growth/reports/refresh_candidates` 配置开启时才
  加载 profile 并透传。
- **测试**：新增 `tests/test_growth_advisor_active_search_material.py`
  （11 项，覆盖结构化摘录优先/兜底、摘录数量上限、多角度查询数量、
  关键词不足不重复拼凑、单角度失败不阻塞其它角度、多角度摘录去重、
  两个调用点的 cfg 透传）；新增 `tests/test_growth_advisor_report_
  citation_check.py`（16 项，覆盖 `_check_report_citations()` 纯函数
  的完全引用/简写引用/编造引用/无引用/重复引用五种场景、
  `generate_growth_report()` 端到端在五种路径下 `citation_check` 的
  取值及序列化兼容性、`_citation_check_diagnostics_summary()` 的空
  数据/多报告聚合、`diagnostics_snapshot()` 包含该键、CLI `/growth
  report` 的自检摘要打印/不打印两种场景）；新增 `tests/test_growth_
  advisor_learning_material.py`（18 项，覆盖规则模板兜底产出非空
  三段结构、LLM 结构化 JSON 解析（含代码块包裹）、四种偏差场景
  （非 JSON/缺字段/异常/空响应）退回模板、基于报告生成时复用摘要
  背景、独立生成时用候选 rationale 兜底、`list_materials()`/
  `get_material_by_id()` 基本行为、序列化兼容性、CLI `/growth
  material` 生成/复用/未知候选三种场景）；新增 `tests/test_growth_
  advisor_external_drift_refresh.py`（16 项，覆盖 `_compute_excerpt_
  fingerprint()` 纯函数、`external_excerpt_fingerprint` 字段的写入
  条件（有摘录时非空、无摘录/模板兜底时为 `None`，不要求 LLM 生成）、
  `external_signal_drift_for_report()` 的无基线/主题缺失/内容不变/
  内容变化/新增页面五种场景、`reports_needing_refresh()` 的默认行为
  不变/单独靠外部变化触发/证据数触发不带该字段/阈值生效四种场景）；
  全部通过。`tests/test_growth_advisor*.py` + `tests/test_growth_
  cmd_timeline_and_active_search_wiring.py`（去除 dragdrop 看板专用
  文件）429 项全部通过，无回归（环境本身缺 `fastapi` 依赖，`api/
  routes.py` 改动仅做了语法与人工核对，未跑该模块相关的 API 层测试，
  是环境限制而非本轮改动引入的问题）。
- **文档**：更新 `docs/growth-advisor-guide.md`"5.6 自主检索与素材
  沉淀改进"小节，标题纳入阶段三、新增"阶段三：生成后自检"子节说明
  `citation_check` 字段含义及取舍，补充自检结果已接入的两处展示
  （CLI/诊断面板），"已知边界"列表相应更新；新增"5.7 报告与学习
  素材分层"小节说明定位区分、生成方式、存储、入口及本轮未做的部分；
  新增"5.8 外部世界变化驱动的刷新"小节说明指纹写入时机、比对机制、
  跟证据数信号的 OR 关系、默认关闭及暂不接入展示的取舍；`config/
  models.py` 里 `report_active_search_max_calls` 的注释此前已更新为
  "已激活"，本轮无需再改。
- **改动文件**：
  - `src/mini_agent/evolution/growth_advisor.py`（`GrowthReport` 新增
    `citation_check`/`external_excerpt_fingerprint` 字段；新增
    `_check_report_citations()`/`_compute_excerpt_fingerprint()`/
    `external_signal_drift_for_report()`；`generate_growth_report()`
    记录 `used_excerpts` 并在正文生成后调用自检、计算摘录指纹、写入
    报告；`diagnostics_snapshot()` 新增 `citation_check` 区块，新增
    `_citation_check_diagnostics_summary()`；`GrowthCandidate` 新增
    `material_id` 字段；新增 `GrowthLearningMaterial` dataclass、
    `generate_learning_material()`/`list_materials()`/
    `get_material_by_id()`；`GrowthBacklog` 新增 `attach_material()`；
    `reports_needing_refresh()` 新增 `profile=` 参数）
  - `src/mini_agent/config/models.py`（新增 `report_external_drift_
    refresh_enabled`/`report_external_drift_min_changes`）
  - `src/mini_agent/storage/paths.py`（新增 `growth_materials_index_
    path` 属性、`growth_material_path()` 方法）
  - `src/mini_agent/cli/commands/growth_cmd.py`（`/growth report` 子
    命令打印正文后追加自检摘要；新增 `/growth material <id>` 子命令）
  - `src/mini_agent/cli/parser.py`（帮助文本新增 `/growth material` 行）
  - `src/mini_agent/api/routes.py`（新增 `POST /growth/candidates/{id}/
    material/generate`、`GET /growth/materials/{id}`；`GET /growth/
    reports/refresh_candidates` 配置开启时加载 profile 并透传）
  - `tests/test_growth_advisor_report_citation_check.py`（新增，16 项）
  - `tests/test_growth_advisor_learning_material.py`（新增，18 项）
  - `tests/test_growth_advisor_external_drift_refresh.py`（新增，16 项）
  - `docs/growth-advisor-guide.md`（5.6 节更新，新增 5.7/5.8 节）
  - `next_doc/growth_advisor_autonomous_search_and_material_improvement_
    plan.md`（第 5 节实施记录更新为阶段一/二/三（含展示）+ 学习素材
    分层 + 外部世界变化驱动刷新均已完成，第 4 节移除"生成后自检"
    "展示""报告与学习素材分层""外部世界变化驱动的刷新"四条，改为
    "自检结果的自动利用""学习素材对齐报告能力""外部世界变化驱动的
    刷新接入看板/CLI 展示"三条）
  - `next_doc/growth_advisor_implementation_record.md`（本节）
- 第 4 节剩余三个方向（自检结果的自动利用、学习素材对齐报告能力、
  外部世界变化驱动的刷新接入看板/CLI 展示）仍未实施，维持方向级规划。

## 候选去重：LLM 语义判重 + 新增 dismiss reason「已存在该主题」

- **触发背景**：实际使用中发现 `growth_candidate_derive()` 会反复给出
  和"已有方向"（pending/accepted 候选，或已经采纳为 Goal 正在推进的
  方向）本质相同、只是措辞不同的候选主题——原有的去重（`normalize_
  title_key` 精确标题匹配）只能拦住字面完全一致的重复，拦不住"学习
  Rust 异步编程" vs "掌握 Rust async/await" 这类语义重复。
- **方案**：新增 `_llm_find_duplicate_direction(new_title, existing_
  titles, llm_helper)`，把新主题和"已存在方向"列表（当前 pending/
  accepted 候选标题 + `goal_backlog.active_goals()` 标题）一次性交给
  LLM 判断是否本质同一件事；命中要求 LLM 逐字输出列表中的原文，输出
  解析不出/判定 NONE/调用异常时统一退回"不重复"（宁可多生成一条候选
  让用户手动 dismiss，也不误判丢掉真正的新方向）。`GrowthBacklog.
  add_or_merge()` 新增 `llm_helper`/`existing_goal_titles` 两个可选
  参数：在精确标题去重之后、真正新建候选之前插入这一步判断——命中
  候选就合并证据（和精确去重分支行为一致），只命中 Goal（没有对应
  候选）就直接跳过、不创建。`growth_candidate_derive()` 新增同名
  `llm_helper` 参数，新增 `GrowthAdvisorConfig.duplicate_direction_
  llm_check_enabled`（默认 `False`，与 `llm_signal_augment_enabled`/
  `topic_category_llm_enabled` 同一惯例：多一次 LLM 调用需要显式开
  启）控制是否真正启用；`run_daily_cycle()` 透传已有的 `llm_helper`
  给 `growth_candidate_derive()`，开关关闭时这条链路整体不生效，行为
  与改动前完全一致。
- 同时新增 dismiss reason `DISMISS_REASON_ALREADY_EXISTS`（"已存在该
  主题"）——给漏判（未开启判重开关，或 LLM 判断失败退回"不重复"）
  留一个人工纠正的出口。这个原因**不计入**方向/类别置信度衰减
  （`_DIRECTION_NEGATIVE_DISMISS_REASONS` 不包含它，理由和已有的
  `report_not_useful` 一致：用户忽略是因为"这是流程重复生成的问题"，
  不是"对这个方向不感兴趣"，不该压低同方向/同类别未来的置信度）。
  CLI（`/growth dismiss <id> already_exists`）和看板下拉框
  （`_GROWTH_DISMISS_REASON_OPTIONS`）同步新增这个选项。
- **测试**：新增 `tests/test_growth_advisor_duplicate_direction_check.py`
  （17 项，覆盖 `_llm_find_duplicate_direction()` 的空列表/NONE/精确
  匹配/格式漂移不匹配/异常五种场景，`add_or_merge()` 的无 llm_helper
  行为不变/命中候选合并证据/命中 Goal 跳过创建/无匹配新建/LLM 异常
  兜底五种场景，`growth_candidate_derive()` 的开关默认关闭忽略
  llm_helper/开关开启合并语义重复候选/开关开启命中 Goal 跳过三种场景，
  以及 `already_exists` reason 本身合法/不参与衰减/不影响 `_dismiss_
  counts_by_dedupe_key` 统计/未知 reason 仍被拒绝四项）；全部通过。
  `tests/` 下 `-k growth`（排除环境本身缺 `streamlit`/`fcntl` 相关
  依赖导致收集失败的 4 个无关文件）456 项全部通过，仅
  `test_compact_health_trend_storage_downsamples_old_points` 1 项失败
  ——该用例基于挂钟时间的天粒度分桶，单独重跑同样失败，是既有的与
  运行时刻相关的不稳定用例，跟本次改动无关（未触碰对应的 `compact_
  health_trend_storage`/`_compact_health_trend_rows` 代码路径）。
- **改动文件**：
  - `src/mini_agent/evolution/growth_advisor.py`（新增
    `_llm_find_duplicate_direction()`；`GrowthBacklog.add_or_merge()`
    新增 `llm_helper`/`existing_goal_titles` 参数及判重分支；
    `growth_candidate_derive()` 新增 `llm_helper` 参数并透传给
    `add_or_merge()`、补充活跃 Goal 标题来源；`run_daily_cycle()`
    调用点透传 `llm_helper`；新增 `DISMISS_REASON_ALREADY_EXISTS`
    常量，加入 `_VALID_DISMISS_REASONS`/`_DISMISS_REASON_LABELS`，
    不加入 `_DIRECTION_NEGATIVE_DISMISS_REASONS`）
  - `src/mini_agent/config/models.py`（`GrowthAdvisorConfig` 新增
    `duplicate_direction_llm_check_enabled`）
  - `src/mini_agent/cli/commands/growth_cmd.py`（dismiss reason 帮助
    文本与未知 reason 报错文案新增 `already_exists`）
  - `apps/mini_agent_kanban/app.py`（`_GROWTH_DISMISS_REASON_OPTIONS`/
    `_DISMISS_REASON_DIAGNOSTICS_LABELS` 新增 `already_exists` 选项）
  - `tests/test_growth_advisor_duplicate_direction_check.py`（新增，
    17 项）
  - `next_doc/growth_advisor_implementation_record.md`（本节）
- 未做：判重列表目前是"pending/accepted 候选标题 + 活跃 Goal 标题"
  一次性列进 prompt，量级通常在几十以内；如果未来候选/Goal 数量显著
  增长导致单次 prompt 过长，需要重新评估是否要分批或做预筛选，本次
  暂不处理（不属于当前真实观察到的问题）。

