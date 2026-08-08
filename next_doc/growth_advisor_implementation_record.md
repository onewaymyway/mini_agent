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

### P5-0：数据生命周期 / 存储卫生（部分完成——仅 growth_topic_trend 降采样）

- **范围说明**：方案原文对三个只追加文件的处理方式分层建议不同——
  `growth_topic_trend.jsonl`"相对独立、聚合意义不强，可以先单独做降
  采样"；`growth_reports_index.jsonl`/`growth_feedback_ledger.jsonl` 则
  要求"先盘点一遍还有哪些函数依赖读整个文件，确认压缩策略不会静默改变
  现有统计口径"。这次先落地风险更低、依赖更少的前者，后两个文件的分层
  存储按方案原文的建议方式（先审计再动代码）延后到下一轮，审计结论见
  下方"依赖全量读取的函数清单"，供下一轮直接复用不用重新盘点。
- **已实现（growth_topic_trend 降采样）**：
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
- **依赖全量读取的函数清单**（审计结论，供 P5-0 下一轮直接使用）：
  | 文件 | 读取方式 | 消费函数 | 是否只取"尾部"/可安全归档旧数据 |
  |---|---|---|---|
  | `growth_reports_index.jsonl` | `list_reports()` 全量读取 | `reports_needing_refresh()`：按 `candidate.report_id` 建字典后只看每个候选**当前挂着**的那份报告 | 是——旧报告（已被刷新替换、`candidate.report_id` 不再指向它）参与字典构建但从不被返回，可以安全归档 |
  | `growth_backlog.jsonl` | `GrowthBacklog.load_all()` 全量读取 | `_category_acceptance_rate()`：统计全部历史 accept/dismiss 决策占比 | 否——这是"累计统计"语义的全量聚合，且这个文件本身不是 append-only（`save_all()` 整表重写），数据量受"候选总数"自然限制，不在本轮 P5-0 讨论范围内 |
  | `growth_feedback_ledger.jsonl` | `GrowthFeedbackLedger.all_entries()` 全量读取 | `_dismiss_counts_by_dedupe_key()`：按 `dedupe_key` 累计历史 dismiss 次数；`_category_dismiss_counts()`：按类别累计；`_followup_adjustment_by_dedupe_key()`：读 `followup_*` 类型条目算调节系数；`monthly_retrospective_summary()`：统计当月 accept/dismiss 数 | 否——前三个都是"累计统计"语义，归档旧条目前必须先把归档掉的部分转成等价的聚合计数（比如 `{dedupe_key: {"dismissed": N}}`），否则会静默改变置信度调权结果；`monthly_retrospective_summary()` 只看当月，理论上可以只读最近窗口，但目前实现是筛全量后按月过滤，归档改造时需要同步改这里的读取方式 |

  结论：`growth_reports_index.jsonl` 的归档风险最低（消费方语义本来就是
  "只看当前挂着的那份"，历史条目对现有统计口径没有贡献），适合下一轮
  优先做；`growth_feedback_ledger.jsonl` 必须先把"归档旧条目"和"把旧
  条目累计进一份持久化的聚合计数"这两步绑定在一起做，不能只做归档，
  否则会破坏 P2/P4-3 的反馈调权和月度复盘统计，改动量级比方案原文预估
  的更大，建议单独排一轮而不是跟 reports_index 一起做。

### P5-2/P5-4/P5-6：尚未开工

方向和大致方案见 `next_doc/growth_advisor_improvement_plan_v3.md` 第 2
节对应小节；按文档第 3 节的建议顺序，下一步是完成 P5-0 剩余部分
（reports_index 归档，风险已确认可控），随后是 P5-2 → P5-4（有前置
依赖关系，需要一起规划），P5-6 留到最后（涉及产品方向判断，不是纯
技术决策）。

