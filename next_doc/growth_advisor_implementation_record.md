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
