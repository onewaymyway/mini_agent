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

## 未做（按方案标注为 P3 剩余项，本轮不在范围内）

- 看板里的拖拽式看板视图（当前是列表 + 按钮，不是真正的多列看板）。
- `growth_signal_scan` 的 LLM 增强版归纳（P1/P2/P3 均保持零 LLM 成本的
  规则式实现；调研报告生成阶段已支持可选 LLM 增强，见 P1 记录）。
- 月度复盘的跨候选能力地图聚合（当前只有数量统计 + 采纳率 + 主题排行）。
- `notification_frequency=weekly_digest` 的真实周摘要打包逻辑（见上方
  P2 部分"已知简化"，本轮未改动）。

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
- `_maybe_dispatch_notification` 的节流状态只按"自然日"计数，没有考虑
  时区跨天的边界情况（用的是 `time.localtime()`，即运行进程所在机器的
  本地时区）；对单机自托管场景够用，多时区/云端多副本部署场景需要另外
  处理。
