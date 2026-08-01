# 外部知识反馈闭环 改进计划

- **版本**: v1.5
- **变更记录**:
  - v1.0：初版，规划 P1-P5；P1（`sys:candidate_queue_triage`）已实现，见该节内的"实现记录"标注。
  - v1.1：P2（`sys:wiki_utility_audit`，统计层）已实现，见该节内的"实现记录"标注。
  - v1.2：P3（`sys:relevance_threshold_calibration`）已实现，见该节内的"实现记录"标注。
  - v1.3：P4（`sys:ecosystem_positioning_scan`）已实现，"同类项目"种子列表
    维护方式选择"人工配置"（`ecosystem_positioning.seeds`），见该节内的
    "实现记录"标注。
  - v1.4：P5（`sys:monthly_trend_retrospective`）已实现，见该节内的
    "实现记录"标注。P1-P5 全部实施完毕，本计划正文规划的五处空隙已补齐。
  - v1.5：看板集成——新增只读汇总端点 `GET /v1/evolution/
    feedback_loop_summary`，看板"🔌 外部输入"页签新增"🧠 外部知识反馈
    闭环（P1-P5）"折叠面板组，把 P1-P5 五个模块的运行状态可视化，见
    §5。
- **背景任务**: 在 `external_knowledge_wiki_and_self_improvement_plan.md`（P1-P5 均已实现）打通的
  "外部事件/检索 → wiki 沉淀 → 自我改进候选"链路基础上，针对现状复盘发现的几处"只生产、
  不巡检/不校准/不回看"的空隙做补齐，不新增数据源，聚焦在现有链路上补一层
  巡检-反馈-校准。
- **关联文档**:
  - `next_doc/external_knowledge_wiki_and_self_improvement_plan.md`（P1-P5，本计划的前置基础）
  - `next_doc/watchlist_notification_goal_design.md`（`GoalRelevanceEngine`/`NoveltyJudge` 候选队列设计）
  - `docs/external-input-gateway-guide.md`

---

## 1. 现状复盘：五处空隙

1. **只生不消**：`tech_radar_search`/`external_trend_capability_link` 的候选队列在各自模块内部
   已经有 TTL 自清理（`STALE_CANDIDATE_TTL_SECONDS`，每次运行时顺带过滤），但**唯一面向人工审核
   的候选队列** `notification/novelty_candidates.jsonl`（`NoveltyJudge` Stage② 产出，等待人工
   confirm/dismiss）**没有任何时间维度的过期机制**——只有一个总量止损上限
   （`MAX_RAW_CANDIDATES_TOTAL`/`MAX_CANDIDATES_TOTAL`，只在 Stage①原始队列生效），
   `pending` 状态的候选会无限期挂着，旧的低价值候选可能一直占着人工审核视野。
2. **沉淀了但不知道有没有用**：wiki 页面写入后，没有机制追踪其后续是否真的被任务/对话引用过，
   `gap_scan` 只判断"内容薄不薄"，不判断"有没有被用上"。
3. **阈值定了不再校准**：`goal_relevance.py::DEFAULT_PREFILTER_THRESHOLD = 0.12` 等硬编码阈值
   注释里明确写着"先给一个宽松默认值，跑一段时间观察"，但没有回头校准的机制。
4. **改进视野被"已知薄弱点"锁死**：`external_trend_capability_link` 只做"外部动态 × 自身能力弱点"
   匹配，不会主动对比同类 agent 框架/生态在做什么，视野局限于已经意识到的短板。
5. **只有日/周颗粒度，缺月度战略回看**：`daily_digest`(天)、`external_trend_capability_link`(周)
   之外没有更高层的、跨越数周的综合回看。

## 2. 设计目标（沿用既有原则）

1. 不新建候选/知识的存储体系，复用现有 jsonl/state 文件格式与锁机制（`ExclusiveFileLock`）。
2. 默认零 LLM 成本优先——能用规则/统计做的巡检、校准，不引入 LLM 调用。
3. 新增 cron job 全部走"低频批量"节奏，不逐事件触发。
4. 每个 job 只做"缺失才补"注册（`ensure_job`/`ensure_*_job` 模式），不破坏用户已手动调整的
   schedule/enabled。

## 3. 分阶段实施计划

### P1 —— `sys:candidate_queue_triage`（人工候选队列过期巡检）✅ 已实现

> 实现记录：新增 `src/mini_agent/evolution/candidate_queue_triage.py`
> （`run_candidate_queue_triage_once()` + `ensure_candidate_queue_triage_job()`），
> 在 `api/server.py` daemon 启动流程里注册 `sys:candidate_queue_triage` job
> （`interval:86400`，零 LLM 成本，默认 enabled，本地回调 handler，跟
> `report_tiers.py::ensure_report_tier_jobs` 同构）。

- 范围：`notification/novelty_candidates.jsonl` 中 `status == "pending"` 且
  `created_at` 超过 `STALE_PENDING_TTL_SECONDS`（默认 30 天）的记录。
- 动作：状态改写为 `"expired"`（**不是** `"dismissed"`——保留"人工主动忽略"与"系统因超时
  自动降级"两种语义的区分，供后续审计/校准使用），写入 `expired_at` 字段，全程持有
  与 `novelty_judge.py` 写路径共享的同一把 `ExclusiveFileLock`，避免并发写入冲突。
- 不删除记录（保留在文件中供追溯），不影响已经是 `confirmed`/`dismissed` 状态的记录。
- `goal_relevance_candidates.jsonl`/`novelty_candidates_raw.jsonl` 两个 Stage①候选队列本身是
  "已消费即算数"的游标消费模型，且已有总量止损，本阶段不重复处理，避免过度设计。
- 测试：新增 `tests/test_candidate_queue_triage.py`（6 用例，全部通过）：文件不存在、超龄
  pending 过期、未超龄 pending 不动、confirmed/dismissed 不受影响、单行损坏不阻塞整批、
  `ensure_candidate_queue_triage_job()` 注册与本地回调触发。
- 接入点：`api/server.py::HttpServer._build_autonomous_loop()` daemon 启动流程，`try/except`
  隔离失败（跟其余 `ensure_*_job` 保持一致，单个 job 注册失败不影响 daemon 启动）。

### P2 —— `sys:wiki_utility_audit`（wiki 页面使用率回溯）✅ 已实现（统计层）

> 实现记录：给 `wiki/search.py::wiki_shelf_search()` 的两处返回点补了一层轻量
> 埋点（`_record_usage()`，无命中不记录、失败静默吞掉，不影响检索主流程），
> 追加写入新增的 `AgentPaths.wiki_usage_log_path`
> （`.agent/wiki/usage_log.jsonl`）。新增
> `src/mini_agent/evolution/wiki_utility_audit.py`
> （`run_wiki_utility_audit_once()` + `load_wiki_usage_stats()` +
> `ensure_wiki_utility_audit_job()`），周期性（`interval:604800`）把最近 30
> 天（`AUDIT_WINDOW_SECONDS`）的埋点聚合为每页 `hit_count`/`grounded_count`/
> `last_used_at`，落盘 `wiki/usage_stats.json`；同一次运行顺带修剪超过 90 天
> （`LOG_RETENTION_SECONDS`）的日志记录，风格对齐 `sys:digest_trim`。

- **本次范围只做"统计层"，不改 `gap_scanner.py`/`decommission.py` 的判断逻辑**：
  `load_wiki_usage_stats()` 已经导出可供下游消费的数据结构，但故意先不接进
  gap_scan/decommission 的去留判断——先让统计跑一段时间、看到真实的利用率
  分布形态（比如"多少页面 30 天零命中"的真实占比）后再决定权重怎么定，
  避免凭空猜一个权重公式。这是"统计"和"策略"两个独立可验证阶段的显式切分，
  跟计划 §2 设计目标 1（不新建存储体系，先打通消费）保持一致的谨慎节奏。
- 埋点只在检索**有候选命中**时触发（`result.pages` 非空），零命中/无 wiki
  目录等早退路径不记录，避免噪音。
- 测试：新增 `tests/test_wiki_utility_audit.py`（7 用例，全部通过），覆盖
  埋点写入/不写入、窗口内聚合、窗口外不计入统计但仍保留日志、超保留期日志
  修剪、`ensure_wiki_utility_audit_job()` 注册与触发；对已有
  `tests/test_wiki_index_reuse.py`/`test_graph_expand.py`（共 17 用例）做了
  回归运行，全部通过，确认埋点不改变 `wiki_shelf_search()` 原有返回结果。
- 接入点：`api/server.py::HttpServer._build_autonomous_loop()`，`try/except`
  隔离失败，模式与 P1 完全一致。

### P3 —— `sys:relevance_threshold_calibration`（阈值自校准）✅ 已实现

> 实现记录：新增 `src/mini_agent/evolution/relevance_threshold_calibration.py`
> （`run_relevance_threshold_calibration_once()` + `load_calibrated_threshold()` +
> `reset_relevance_threshold()` + `ensure_relevance_threshold_calibration_job()`），
> 在 `api/server.py` daemon 启动流程里注册 `sys:relevance_threshold_calibration`
> job（`interval:604800`，零 LLM 成本，默认 enabled，本地回调 handler，跟
> `candidate_queue_triage.py`/`wiki_utility_audit.py` 同构）。落盘状态文件
> `.agent/external_input/relevance_threshold_state.json`（`AgentPaths.
> external_input_relevance_threshold_state`）。

- 前置补丁：`goal_relevance.py::run_goal_relevance_judge_once()` 此前只把
  Stage②的 `relevant`/`advance_worthy` 判定结果用于当次的 `attach_external_context`/
  `try_advance_goal` 副作用，判定完就丢，候选文件里查不到——本次补上，把这两个
  字段一并持久化回 `goal_relevance_candidates.jsonl` 对应记录（解析失败时保持
  缺省不写，不当成 `False` 参与后续统计，避免"解析失败"被误记为"判定不相关"）。
- 校准信号：只用 Stage②已判定候选的 `relevant_rate`（不单独统计 Stage①→Stage②
  的通过率——两者高度相关，且 Stage①本身没有"被拒绝的事件"落盘记录，无法回溯
  统计分母）。
  - `relevant_rate < 0.15`（`LOW_HEALTHY_RATE`）：Stage①筛得太松，调高阈值收紧
    （`+0.01`，即 `ADJUSTMENT_STEP`）。
  - `relevant_rate > 0.5`（`HIGH_HEALTHY_RATE`）：Stage①可能偏紧、有漏判风险，
    调低阈值放松（`-0.01`）。
  - 落在 `[0.15, 0.5]` 区间内：不调整。
  - 阈值调整全程 clamp 在 `[THRESHOLD_MIN=0.05, THRESHOLD_MAX=0.4]` 内。
- 风险应对（对应 §3 P3 原设计的两条关键风险）：
  1. **样本量与 warmup 门槛**：校准状态首次创建（`created_at`）后必须满
     `MIN_WARMUP_SECONDS`（28 天）才允许首次调整，且每次参与统计的样本数
     （新增的、已判定且成功解析出 `relevant` 字段的候选数）不低于
     `MIN_SAMPLE_SIZE`（20）；样本不足/仍在 warmup 期直接跳过调整，但读取
     游标（`last_reviewed_created_at`）依然前移，避免同一批候选被下一次
     运行重复计入、造成样本量"虚高"的假象。
  2. **人工一键回滚逃生通道**：`reset_relevance_threshold()`，把当前阈值
     重置回 `DEFAULT_PREFILTER_THRESHOLD`、清空调整历史，但保留一条
     `reason="manual_reset"` 的审计记录（不是连痕迹都不留），同时把
     `created_at` 重置为 now——等价于重新开始一轮 warmup 计时，避免重置后
     立刻又基于重置前的旧样本触发新一轮自动调整。
- 接入点：`evolution/autonomous_loop.py::_tick_maintenance()` 里 Stage①调用处
  （`run_goal_relevance_candidate_once()`）现在改为读取
  `load_calibrated_threshold()` 的当前生效值，而不是硬编码的
  `DEFAULT_PREFILTER_THRESHOLD`（文件不存在时内部退回默认值，零额外读盘
  成本）；`api/server.py::HttpServer._build_autonomous_loop()`，`try/except`
  隔离失败，模式与 P1/P2 完全一致。
- 测试：新增 `tests/test_relevance_threshold_calibration.py`（10 用例，全部
  通过），覆盖状态文件不存在返回默认值、样本不足/warmup 期跳过调整但游标
  前移、低/高 relevant_rate 触发调整方向、健康区间不调整、阈值上下限 clamp、
  未判定/解析失败候选不计入样本、`reset_relevance_threshold()` 回滚、
  `ensure_relevance_threshold_calibration_job()` 注册与本地回调触发；对
  `tests/test_goal_relevance_candidate.py`/`tests/test_goal_relevance_judge.py`
  （因新增 `relevant`/`advance_worthy` 持久化字段而回归运行）做了确认，
  全部通过。

### P4 —— `sys:ecosystem_positioning_scan`（生态定位扫描）✅ 已实现

> 实现记录：新增 `src/mini_agent/external_input/ecosystem_positioning_scan.py`
> （`run_ecosystem_positioning_scan_once()` +
> `ensure_ecosystem_positioning_scan_job()`），在 `api/server.py` daemon
> 启动流程里注册 `sys:ecosystem_positioning_scan` job（`interval:604800`，
> 零额外检索通道成本，本地回调 handler，跟
> `tech_radar_search.py::ensure_tech_radar_search_job` 同构）。

- **种子列表维护方式（原设计的前置依赖）**：本次选择"人工配置"这一支路——
  新增 `config/models.py::EcosystemPositioningConfig`（`ecosystem_positioning.
  seeds`，`agent_config.json` 里配置同类 agent 框架/开源项目名称列表），
  与 `TechRadarConfig.keywords` 同款取舍（初期先简单实现，不追求自动发现）。
  种子列表默认为空，job 首次创建时默认 `disabled`（不同于
  `tech_radar_search`——后者种子池天然有 `gap_scanner` 缺口兜底，本模块
  完全依赖人工配置，避免"注册了但什么都不做"的困惑），需要用户显式配置
  `seeds` 并启用该 job 后才会真正运行。
- **管道复用**：完全复用 `tech_radar_search.py` 的"检索 → LLM 抽取 → 落盘
  wiki"管道（种子轮转、`web_search` 调用、批量 LLM 抽取 prompt/解析、
  `wiki/world_writer.py::queue_entities()`/`queue_facts()` 落盘），独立
  实现而非导入私有函数（两个可独立演化的模块不产生隐式耦合，跟
  `tech_radar_search.py`/`knowledge_extractor.py` 的既有关系一致）。
- **候选分开落点**：新增 `wiki/world_writer.py::EXTERNAL_ECOSYSTEM_SOURCE_KIND`
  （`"external_ecosystem"`），与 `tech_radar_search.py` 的
  `EXTERNAL_SEARCH_SOURCE_KIND`（`"external_search"`）区分——
  `evolution/external_trend_capability_link.py::EXTERNAL_KNOWLEDGE_SOURCE_KINDS`
  只包含 `("external_watch", "external_search")`，不含本模块产出的页面，
  保持"看别人在做什么"是独立一路信号，不被直接拿去跟"自身已知弱点"强行
  匹配（符合原设计"产出的候选与 external_trend_capability_link 的候选
  分开落点（不同 source_kind/候选文件）"的要求）。
- 独立的种子轮转游标：`AgentPaths.external_input_ecosystem_positioning_state`
  （`.agent/external_input/state/ecosystem_positioning_scan_state.json`），
  与 `external_input_tech_radar_state` 同构但完全独立，不共享游标。
- 测试：新增 `tests/test_ecosystem_positioning_scan.py`（9 用例，全部
  通过），覆盖无 llm_helper/空种子跳过、种子轮转与回绕、entities/facts
  正确落盘并打 `source_kind="external_ecosystem"`、单种子检索失败不阻塞
  其它种子、全部检索失败/LLM 调用失败不推进游标、单条解析失败不阻塞其余
  条目、job 默认以 disabled 状态注册；对
  `tests/test_external_input_tech_radar_search.py`/
  `tests/test_entity_digest.py`/`tests/test_wiki_lifecycle.py`/
  `tests/test_external_input_knowledge_extractor.py`
  （因新增 source_kind 常量与 world_writer 用法回归运行）做了确认，
  全部通过。

### P5 —— `sys:monthly_trend_retrospective`（月度战略回顾）✅ 已实现

> 实现记录：新增 `src/mini_agent/evolution/monthly_trend_retrospective.py`
> （`run_monthly_trend_retrospective_once()` +
> `ensure_monthly_trend_retrospective_job()`），在 `api/server.py` daemon
> 启动流程里注册 `sys:monthly_trend_retrospective` job（`cron:0 0 1 * *`，
> 每月 1 日一次，零 LLM 成本，默认 enabled，本地回调 handler，跟
> `candidate_queue_triage.py`/`wiki_utility_audit.py`/
> `relevance_threshold_calibration.py` 同构）。

- 目标：汇总过去 4 周 `external_trend_capability_link` 候选采纳情况、wiki 专题页增长、
  `self_eval`/`capability_map` 能力变化趋势，生成一份月度回顾文档，供
  `decision_profile_update`/`soft_goal_deriver` 参考。
- 三路信号采集：
  1. **候选采纳情况**：读取 `external_trend_capability_link_state_path` 的
     `produced_keys`，筛出过去 28 天（`RETROSPECTIVE_WINDOW_SECONDS`）内产出的
     候选，逐条能力域跟 `GoalBacklog.all_nodes()` 里现存 Goal 标题做匹配（复用
     `soft_goal_deriver.py::_reverify_candidate_signal()` 里
     `source_tag == "external_knowledge"` 分支同款的标题拼接规则），判定是否
     已被采纳为 Goal。
  2. **wiki 专题页增长**：复用 `wiki/stats.py::compute_stats()` 拿当前
     `by_source_kind` 快照，与本模块自己状态文件里保存的上一轮快照做差值。
  3. **能力变化趋势**：复用 `evolution/consolidation.py::load_capability_map()`，
     与上一轮保存的 `domain -> confidence` 快照做差值，按变化幅度降序只保留
     Top 10（`MAX_CAPABILITY_HIGHLIGHTS`）。
- 落点：只产出一份人类可读文档
  （`AgentPaths.monthly_trend_retrospective_path(month)`，
  `.agent/wiki/monthly_trend_retrospective/<YYYY-MM>.md`），不产出结构化候选、
  不接入任何下游自动消费链路，不自动创建 Goal、不自动修改代码——P5 本身就是
  给人看的回顾终点，跟 P1-P4"产出候选供下游消费"的定位不同。
- 快照对比机制：不引入专门的时间序列存储，按"运行节奏（每月一次）快照 vs
  上一轮保存的快照"的方式计算环比增量，跟 `relevance_threshold_calibration.py`
  的"游标 + 状态快照"风格一致。首次运行（无上一轮快照）时 wiki 增长/能力变化
  会把全量值当作"从无到有"的增量展示，属于预期行为，不影响可用性。
- 测试：新增 `tests/test_monthly_trend_retrospective.py`（9 用例，全部通过），
  覆盖状态文件不存在时采纳统计返回零值、窗口内/外候选正确过滤计数、候选对应
  Goal 已存在时正确判定采纳、wiki 增长首次运行/环比对比、能力变化趋势首次
  运行/环比对比及 Top N 截断排序、端到端运行写出月度文档并保存快照、
  `ensure_monthly_trend_retrospective_job()` 注册与本地回调触发；对
  `tests/test_external_trend_capability_link.py`/`tests/test_goal_backlog.py`/
  `tests/test_wiki_utility_audit.py`/`tests/test_ecosystem_positioning_scan.py`/
  `tests/test_relevance_threshold_calibration.py`/
  `tests/test_candidate_queue_triage.py`（共 44 用例，因复用同批底层模块回归
  运行）做了确认，全部通过。

## 4. 本次实施范围小结

- v1.1 实施了 P1、P2（P2 只做统计层，不改 gap_scan/decommission 判断逻辑）。
- v1.2 实施了 P3（阈值自校准，含 warmup/最小样本量门槛与人工回滚逃生通道）。
- v1.3 实施了 P4（生态定位扫描；"同类项目"种子列表选择"人工配置"支路，
  job 默认 disabled，需用户配置种子后启用）。
- v1.4 实施了 P5（月度战略回顾；三路信号——候选采纳情况/wiki 专题页增长/
  能力变化趋势——全部复用 P1-P4 已沉淀的状态文件与既有统计模块，零 LLM
  成本，不引入新的数据源或存储体系）。
- 至此 P1-P5 全部实施完毕，§1 复盘的五处空隙（候选队列不过期、沉淀内容
  利用率不可见、阈值不校准、改进视野被已知弱点锁死、缺月度战略回看）
  均已补上对应机制。

## 5. 看板集成（v1.5 新增）

P1-P5 五个 cron job 本身通过 `ensure_job`/`register_local_handler` 注册
后，已经能在看板既有的"⏰ Cron 任务"页签里被通用地看到（调度信息、
启用/禁用切换、立即运行一次、执行历史）——**这部分不需要任何新代码**，
是 `CronScheduler`/`CronJobExecutor` 通用机制天然覆盖的。

本次补的是 P1-P5 各自**产出内容**的可视化（Cron 任务页签只展示"这个
job 跑没跑、跑得顺不顺"，不展示"跑出来的东西是什么"）：

- **新增只读端点** `GET /v1/evolution/feedback_loop_summary`
  （`src/mini_agent/api/routes.py`）：一次性聚合五个模块的当前状态，
  任一模块读取失败只影响自己（对应字段返回 `_error`），不阻塞其余
  四个——跟 P1-P5 各模块自身"单点失败不影响其余"的一贯风格一致。
- **看板新增面板组**：`apps/mini_agent_kanban/app.py::
  render_external_input_tab()` 里"🔌 外部输入"页签新增"🧠 外部知识
  反馈闭环（P1-P5）"折叠面板组，六个子面板：
  1. 🗂️ 候选队列过期巡检（P1）：pending/expired/confirmed/dismissed
     四态计数。
  2. 📖 wiki 利用率（P2）：有统计的页面数 + 命中次数 Top 10。
  3. 🎚️ 阈值自校准（P3）：当前生效阈值 + 最近 5 条调整记录。
  4. 🔗 外部趋势×能力薄弱点候选（P4a）：候选数 + 前 10 条详情（能力域/
     依据 wiki 页面/理由）。
  5. 🧭 生态定位扫描（P4b）：已沉淀 `external_ecosystem` 页面数 + 上次
     运行时间 + 启用引导提示。
  6. 📅 月度战略回顾（P5）：最新一期文档全文渲染 + 历史期数列表。
- **仍然全部只读**：跟 P1-P5 本身"不自动创建 Goal、不自动改代码"的
  定位一致，看板侧不新增任何写操作按钮——需要人工介入的动作（如启用
  `sys:ecosystem_positioning_scan`、重置阈值）仍然通过既有的"⏰ Cron
  任务"页签（启用/禁用）或直接调用
  `relevance_threshold_calibration.reset_relevance_threshold()` 完成，
  不重复造轮子。
- **测试**：新增 `tests/test_feedback_loop_summary_route.py`（3 用例，
  全部通过），覆盖空项目返回零值不报错、候选队列状态计数正确、月度
  回顾最新一期内容正确读取。
