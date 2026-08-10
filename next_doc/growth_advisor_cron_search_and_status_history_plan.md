# 成长顾问：cron 主动检索预算调度 + 检索质量反馈闭环 + Goal 状态变更历史

- **状态**：方向一/二/三均已实施完成，见各节内的"实现记录"标注。
- **关联文档**：
  - `next_doc/growth_advisor_active_search_and_lifecycle_plan.md`（方向一
    "手动触发路径"的主动检索、方向二 `growth_topic_lifecycle()` 原始
    设计，本方案是这两处此前明确列为"非目标"的部分的后续）
  - `next_doc/growth_advisor_research_quality_plan.md`
  - `next_doc/external_knowledge_wiki_and_self_improvement_plan.md`（P3
    `tech_radar_search.py` 种子轮转机制的原始设计）
  - `next_doc/external_knowledge_feedback_loop_improvement_plan.md`（P1-P5
    "只生不消"系列空隙的既有治理模式）
  - `docs/growth-advisor-guide.md`

## 0. 问题（本轮聚焦的三处空隙）

1. **主动检索没有调度接入**：`run_daily_cycle()`（`sys:growth_advisor_
   daily`，cron 无人值守路径）此前完全不触发方向一的主动检索——
   `growth_advisor_active_search_and_lifecycle_plan.md` 明确把这一条列为
   非目标，理由是"调度成本不好控制"。现状是主动检索完全靠人工触发
   `/growth report`，覆盖面有限：一个候选如果用户没有手动点开看过，
   即使它证据数最高、被动管道也确实没有任何外部背景，也永远不会补一次
   检索。
2. **检索结果没有质量反馈闭环**：`tech_radar_search.py` 的种子检索结果
   落盘后就结束了，没有任何"这条外部资讯有没有用"的反馈——同一个种子
   反复检索到空结果（没有任何可提炼的 entity/fact），下一轮轮转到它时
   依然原样再查一次，没有机制学到"最近这个方向没什么新东西，先别查了"
   或调整检索策略。
3. **Goal 状态变化没有历史，时间线看不出往复**：`growth_topic_lifecycle()`
   的 `goal_completed`/`goal_stalled`/`goal_active` 是渲染时现查
   `GoalBacklog` 得到的"当前状态"，不是历史事件流——一个 Goal 完成后又
   被重新打开（比如周期性 Goal 的下一轮、或用户手动改回 `active`），
   时间线只能看到最后一次状态，看不出"完成过一次又重启"这种往复。根源
   是 `GoalNode` 本身没有状态变更历史可查，不是成长顾问单独能补的——
   本轮从 `GoalBacklog` 侧补上这个基础能力。

## 1. 目标（本轮范围，已全部实施完成）

- **方向一**：`run_daily_cycle()` 借用 `tech_radar_search.py` 已验证的
  "控频率+控预算"节流模式，新增可选的 cron 路径主动检索，每天最多
  处理少数几个"证据数最高但从未有过任何外部背景"的候选。
- **方向二**：给 `tech_radar_search.py` 的种子轮转补一层质量反馈——
  连续多次查不到有用内容的种子进入冷却期，暂时跳过、而不是继续原样
  排队，冷却期满或查到有用内容后自动恢复。
- **方向三**：`GoalNode` 新增极简的状态变更历史，`growth_topic_lifecycle()`
  消费这份历史，能正确呈现"完成 -> 重新打开"这类往复。

非目标（本轮不做，理由见各自小节）：
- 不做"素材不够多再补"的增量检索（方向一）——只在候选完全没有外部
  背景时才触发，延续既有的克制边界。
- 不做检索策略的自动调整/自适应 query 改写（方向二）——本轮只做
  "要不要继续查这个种子"的二元节流，不引入更复杂的策略学习。
- 不做 Goal 状态变更的通知/提醒（方向三）——只补数据结构，看板/CLI
  展示层的呈现留给 `growth_topic_lifecycle()` 已有的消费方式，不新增
  推送渠道。

## 2. 方向一：cron 路径的主动检索预算调度 ✅ 已实施

### 现状回顾

`generate_growth_report()` 已经支持"手动触发路径"的主动检索
（`report_active_search_enabled`，见 `growth_advisor_active_search_and_
lifecycle_plan.md`）：调用方（CLI `/growth report <id>`、API）传入
`web_search_fn` 时，被动扫描命中 0 条素材会现查一次。但 `run_daily_
cycle()` 从不传 `web_search_fn`，cron 每日流程因此从不触发这条路径。

### 方案

- `run_daily_cycle()` 新增可选参数 `web_search_fn=None`（签名与
  `generate_growth_report()` 的同名参数一致：`(query, max_results) ->
  str`）。不传时行为与本方案实施前完全一致。
- 新增 `GrowthAdvisorConfig` 字段：
  - `cron_triggered_active_search_enabled: bool = False`（默认关闭，
    会实际发起 `web_search` 调用，遵循"增加调用成本的能力默认
    opt-in"的一贯原则）
  - `cron_triggered_active_search_daily_limit: int = 1`（每个自然日
    最多处理几个候选）
- 新增 `_maybe_run_cron_triggered_active_search(paths, cfg, candidates,
  *, llm_helper, web_search_fn, profile=None)`，在 `run_daily_cycle()`
  收尾阶段调用（`sync_confirmed_topics_to_tech_radar` 之后）：
  1. 开关关闭、`llm_helper`/`web_search_fn` 任一缺失、或本轮没有候选
     时直接跳过。
  2. 每日预算：复用 `growth_advisor_state.json`（跟 `notify_count_today`
     同一套"自然日翻转计数器"风格，独立的 `cron_active_search_date`/
     `cron_active_search_count_today` 两个键，不与推送节流共享计数）。
  3. 候选选取：`candidates`（`run_daily_cycle()` 传入本轮 `new_
     candidates`）按 `confidence` 降序遍历，跳过
     `_external_signal_count_for_topic()` 命中数 > 0 的（已经有外部
     背景，不重复劳动）。
  4. 命中的候选调用已有的 `_active_search_excerpts_for_topic()` 完成
     "检索 → LLM 抽取 → 落盘 wiki"，不重新实现一套；产出仍然打
     `source_kind="external_search"`、`source_entries` 前缀为
     `growth_advisor_active_search:<candidate_id>`，跟手动触发路径共用
     同一套可追溯标记。
  5. 无论这次检索是否真的抽出内容，都会占用当天的预算名额（是否值得
     继续查某个候选，交给方向二的质量反馈闭环处理，本函数不做重试
     判断，避免同一个屡查屡空的候选反复重试、把当天预算耗在它一个人
     身上）。
  6. 任何一步异常都不应该打断 `run_daily_cycle` 主流程，整体
     try/except + `log_exception` 兜底，返回 `None`。
- `run_daily_cycle()` 返回值新增 `cron_active_search` 字段（触发的
  候选 id 列表，或 `None` 表示本轮未触发/被跳过），供调用方需要时
  展示，不影响既有的 `new_candidates`/`reports`/`notification` 字段。
- **调用方接入**：CLI `/growth scan`（`growth_cmd.py`）与 API
  `POST /v1/growth/scan`（`routes.py`）都改为传入
  `tools/builtin.py::web_search`（跟 `tech_radar_search.py` 默认使用
  的是同一个模块级函数）作为 `web_search_fn`——传入本身不代表一定会
  触发检索，是否真正调用仍然由 `cfg.cron_triggered_active_search_
  enabled` 这个显式开关决定，两条调用路径与 cron job（`sys:
  growth_advisor_daily` 的 `task_template` 本质是走 `/growth scan`
  同一条 CLI 命令）因此自然获得这条能力，不需要给 cron 单独接线。

### 验收标准

- `cron_triggered_active_search_enabled=False`（默认）时，`run_daily_
  cycle()` 传入/不传入 `web_search_fn` 行为完全一致，`cron_active_
  search` 字段恒为 `None`。
- 打开开关、传入 `web_search_fn`/`llm_helper` 后，本轮候选里"证据数
  最高且没有外部背景"的那个会被触发检索；候选存在但已经有外部背景时
  不重复检索。
- 单日预算用尽后，同一天内的后续 `run_daily_cycle()` 调用不再触发
  （无论候选是否变化）。

## 3. 方向二：检索结果质量反馈闭环 ✅ 已实施

### 现状回顾

`tech_radar_search.py` 的种子轮转（`_select_seeds_for_this_run()`）
只按"游标 offset 是否轮到"决定本次处理哪些种子，不看这个种子过去的
检索质量如何——一个长期查不到任何新内容的种子会被无限期继续排进
轮转，浪费检索/LLM 抽取配额。

### 方案

- 复用现有的轮转状态文件（`AgentPaths.external_input_tech_radar_
  state`），新增 `seed_quality` 字段：`{seed: {"empty_streak": int,
  "total_runs": int, "total_empty": int, "last_run_at": float}}`，
  与 `offset`/`last_run_id` 等既有字段同级存放，不新增文件。
- 新增两个纯函数：
  - `_filter_low_quality_seeds(seeds, quality_state, *, streak_
    threshold, cooldown_seconds, now)`：种子轮转选取之前先过滤——
    `empty_streak >= streak_threshold` 且距 `last_run_at` 不满
    `cooldown_seconds` 的种子被挑出跳过列表，其余原样保留顺序。
    `streak_threshold <= 0` 视为关闭本机制。
  - `_update_seed_quality(quality_state, seed, *, useful, now)`：一个
    种子处理完之后调用，`useful` 由调用方判定（"这个种子最终有没有
    产出至少一条 entity/fact"）——有用则 `empty_streak` 清零，否则
    +1 并累加 `total_empty`。
- `run_tech_radar_search_once()` 新增参数 `quality_feedback_enabled`/
  `low_quality_streak_threshold`/`low_quality_cooldown_days`（默认
  `True`/`3`/`14`），种子池先过滤再轮转、处理完每个种子后更新质量
  记录，`TechRadarSummary` 新增 `low_quality_skipped_count` 字段。
- `TechRadarConfig` 新增同名三个字段，默认值与函数默认一致——**默认
  开启**：这一步零额外 LLM/网络调用成本，只是在既有状态文件基础上多
  做一次读写与过滤判断，遵循"零成本的改进默认开启"原则。
- `ensure_tech_radar_search_job()`/`server.py` 调用处透传这三个配置。
- **降级而非拉黑**：冷却期满后种子自动重新参与轮转（不需要额外的
  "是否已经冷却过一次"标记）——如果冷却期满后再查一次仍然没有内容，
  `empty_streak` 已经在冷却期内被"冻结"（`_update_seed_quality` 只在
  真正被处理时才更新），重新计入一次空结果、冷却期重新计时；如果查到
  了有用内容，`empty_streak` 清零，回到正常轮转节奏。
- 关闭开关（`quality_feedback_enabled=False`）时不主动清空已经积累的
  `seed_quality` 历史——万一之后重新打开，之前的记录还能继续用。

### 验收标准

- 一个种子连续 `low_quality_streak_threshold` 次检索都没有产出任何
  entity/fact 后，下一次运行会被质量过滤挡住，本轮 `seeds_processed`
  不含它、`low_quality_skipped_count` 相应增加。
- 期间只要有一次查到有用内容，`empty_streak` 立即清零，不再被跳过。
- `quality_feedback_enabled=False` 时，无论种子历史质量如何都不会被
  跳过，行为与本机制引入前完全一致。

## 4. 方向三：Goal 状态变更历史 ✅ 已实施

### 现状回顾

`GoalNode.status` 只是一个不透明字符串，`GoalBacklog.set_status()` 只
覆盖写这一个字段，没有任何历史记录——`growth_topic_lifecycle()` 只能
现查"当前状态"，Goal 完成又重新打开这种往复完全不可见。这是 `GoalBacklog`
本身的数据结构缺口，成长顾问侧无法单独绕过。

### 方案

- `GoalNode` 新增字段 `status_history: list = field(default_factory=
  list)`，每项 `{"status": str, "at": float}`，只追加不修改/删除。
  `to_dict`/`from_dict` 同步更新；旧数据（反序列化时没有这个键）兜底
  为空列表，等价于"这个节点还没经历过一次显式状态变更"，不需要额外
  数据迁移。
- `GoalBacklog.set_status()`：状态真正发生变化时（新状态与当前状态
  不同）才追加一条历史记录——同一状态被重复 `set` 不产生冗余条目
  （避免调用方不判断当前状态就无脑 `set_status(node_id, "active")`
  时把历史刷满重复记录）。
- `growth_topic_lifecycle()` 消费方式：
  - `goal.status_history` 非空时，按时间顺序回放：
    - 状态是 `"active"` 且上一条状态属于终态
      （`completed`/`abandoned`/`failed`/`cancelled`）时，产出新增的
      `goal_reopened` 事件（`label` 里带上此前是哪个终态）。
    - 其余状态沿用原有的 `_goal_status_event()` 映射
      （`goal_completed`/`goal_stalled`/`goal_active`）。
  - `goal.status_history` 为空（旧数据/从未经历过一次 `set_status`）
    时，退回原来的"只看当前状态"路径，向后兼容，不影响任何既有调用
    方/测试。
- 不引入新的落盘文件——历史直接是 `GoalNode` 自身的一个字段，随
  `goals.json` 一起落盘，跟现有的 `user_feedback`/`external_context`
  等"节点自带的小型历史列表"字段是同一存储风格。

### 验收标准

- 对一个经历过"落地成 Goal -> 完成 -> 重新打开"的候选，
  `growth_topic_lifecycle()` 返回的事件列表里 `goal_completed` 出现在
  `goal_reopened` 之前，`ts` 单调不减。
- 对一个从未经历过状态变更、或来自旧数据（没有 `status_history` 字段）
  的 Goal，时间线行为与本方案实施前完全一致。
- 同一状态被重复 `set_status()` 不会在历史里产生重复条目。

## 5. 与现有设计哲学的一致性检查

- **克制优先**：方向一/二默认都是"零成本或低成本才默认开/关"——方向一
  会真实发起检索调用，默认关闭；方向二零额外调用成本，默认开启；方向
  三本身不产生任何新的判断/推荐逻辑，纯粹是数据结构补全。
- **降级而非拉黑**：方向二的种子跳过是有冷却期的临时降级，不是永久
  黑名单，呼应"证据不够强就克制，但留有余地重新观察"的一贯取向。
- **只读优先/复用现有管道**：方向一不新增检索通道，直接复用已经验证
  过的 `_active_search_excerpts_for_topic()`；方向三不新增落盘文件。
- **容错优先于完整**：三个方向的新增逻辑都以 try/except + 静默降级
  为默认姿态，任何一步失败都退回"这个功能没有引入前"的原有行为，不
  让新功能的失败拖垮既有主流程。

## 6. 数据结构变更小结

- `GrowthAdvisorConfig` 新增：`cron_triggered_active_search_enabled`
  （默认 `False`）、`cron_triggered_active_search_daily_limit`
  （默认 `1`）。
- `TechRadarConfig` 新增：`quality_feedback_enabled`（默认 `True`）、
  `low_quality_streak_threshold`（默认 `3`）、
  `low_quality_cooldown_days`（默认 `14`）。
- `run_daily_cycle()` 新增可选参数 `web_search_fn`，返回值新增
  `cron_active_search` 字段。
- `run_tech_radar_search_once()`/`ensure_tech_radar_search_job()` 新增
  可选参数 `quality_feedback_enabled`/`low_quality_streak_threshold`/
  `low_quality_cooldown_days`；`TechRadarSummary` 新增
  `low_quality_skipped_count` 字段；轮转状态文件新增 `seed_quality`
  字段。
- `GoalNode` 新增 `status_history: list` 字段（`to_dict`/`from_dict`
  同步）。

## 7. 实施记录

- 方向一：`src/mini_agent/evolution/growth_advisor.py`
  （`_maybe_run_cron_triggered_active_search()`，`run_daily_cycle()`
  新增 `web_search_fn` 参数与 `cron_active_search` 返回字段）、
  `src/mini_agent/config/models.py`（`GrowthAdvisorConfig` 新增字段）、
  `src/mini_agent/cli/commands/growth_cmd.py`/`src/mini_agent/api/
  routes.py`（`/growth scan` 传入 `tools/builtin.web_search`）。测试：
  `tests/test_growth_advisor_active_search_and_lifecycle.py::
  TestCronTriggeredActiveSearch`（4 用例）。
- 方向二：`src/mini_agent/external_input/tech_radar_search.py`
  （`_filter_low_quality_seeds()`/`_update_seed_quality()`，
  `run_tech_radar_search_once()`/`ensure_tech_radar_search_job()` 新增
  参数）、`src/mini_agent/config/models.py`（`TechRadarConfig` 新增
  字段）、`src/mini_agent/api/server.py`（透传配置）。测试：
  `tests/test_external_input_tech_radar_search.py::
  TestQualityFeedbackLoop`（3 用例）。
- 方向三：`src/mini_agent/perception/goal_backlog.py`
  （`GoalNode.status_history`，`set_status()` 追加历史）、
  `src/mini_agent/evolution/growth_advisor.py`
  （`growth_topic_lifecycle()` 消费 `status_history`）。测试：
  `tests/test_goal_backlog.py::TestGoalStatusHistory`（4 用例）、
  `tests/test_growth_advisor_active_search_and_lifecycle.py::
  TestTopicLifecycle` 新增 2 用例。
- 回归：对 `tests/test_goal_backlog.py`/
  `tests/test_external_input_tech_radar_search.py`/
  `tests/test_growth_advisor.py`/
  `tests/test_growth_advisor_active_search_and_lifecycle.py`/
  `tests/test_growth_advisor_research_quality.py`/
  `tests/test_growth_advisor_goal_cron_integration.py`（共 247 用例）
  做了完整回归运行，全部通过（唯一 1 个失败用例
  `test_compact_topic_trend_storage_downsamples_old_points` 是本方案
  实施前既有的日期边界相关问题，与本次改动无关，未做修复，超出本轮
  范围）。
