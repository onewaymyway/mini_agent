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

## 未做（按方案标注为 P2/P3，本轮不在范围内）

- P2：候选置信度按用户历史 accept/dismiss 反馈动态调权（`GrowthFeedbackLedger`
  当前只落盘不参与排序）；`growth_signal_scan` 的 LLM 增强版归纳；月度复盘
  的深度归因与跨候选能力地图聚合。
- P3：看板里的拖拽式看板视图（当前是列表 + 按钮，不是真正的多列看板）；
  `excluded_topics` 黑名单的看板可视化编辑入口（当前只能改配置文件）；
  实际的主动推送/通知派发（`notification_frequency` 等配置项已存在但
  尚未接入通知调度器，当前只能通过看板/CLI 主动查看）。
- 首次进入看板的轻量提示（`first_touch_notice_enabled`）配置项已加，
  UI 侧尚未实现该一次性提示。

## 已知取舍/风险

- `growth_signal_scan` 的关键词表（`_TOPIC_KEYWORDS`）是初始 MVP 覆盖，
  命中面有限；扩展方式是直接往表里加词条，不需要改扫描逻辑，但目前
  没有从用户实际反馈里自动学习新主题词的机制。
- 调研报告的模板兜底内容比较通用（4 个固定小节），信息密度低于 LLM
  生成版本；`enabled=True` 默认开启的前提下这是刻意的取舍，保证零配置
  也能看到端到端效果。
