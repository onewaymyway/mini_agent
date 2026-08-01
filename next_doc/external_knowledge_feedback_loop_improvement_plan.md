# 外部知识反馈闭环 改进计划

- **版本**: v1.0
- **变更记录**:
  - v1.0：初版，规划 P1-P5；P1（`sys:candidate_queue_triage`）已实现，见该节内的"实现记录"标注。
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

### P2 —— `sys:wiki_utility_audit`（wiki 页面使用率回溯）📋 已设计，未实现

- 目标：回溯任务执行/对话历史中对 wiki 页面的引用/检索命中，产出每个页面的"近期利用率"
  指标，反馈给 `gap_scan`/`decommission` 的去留判断，把"内容薄不薄"和"有没有被用上"两个
  维度都纳入考量。
- 推荐节奏：`interval:604800`（对齐 `sys:decision_profile_update`）。
- 关键前置缺口：当前 wiki 检索/引用路径（`wiki/search.py`）没有埋点记录"这次检索/引用命中
  了哪个 page_id"，需要先补一层轻量埋点（写入 `.agent/wiki/usage_log.jsonl`）才能做统计，
  工作量比 P1 大一档，且涉及检索热路径的改动，需要更谨慎的性能评估，故本次先只做设计、
  不实现，留作后续独立阶段。

### P3 —— `sys:relevance_threshold_calibration`（阈值自校准）📋 已设计，未实现

- 目标：回看 `GoalRelevanceEngine` Stage①→Stage②的通过率与 Stage②最终判定分布，对
  `DEFAULT_PREFILTER_THRESHOLD` 一类硬编码阈值做小步长、有上下限的自动微调，并记录每次
  调整的前后值和依据。
- 推荐节奏：`interval:604800`。
- 关键风险：调整策略本身需要先有足够样本量（建议至少积累 4 周判定数据后才允许首次调整），
  且需要一个"人工一键回滚到默认阈值"的逃生通道，避免校准逻辑本身跑偏后无法挽回；本次先
  只做设计留档，避免仓促上线一个会自我漂移的参数。

### P4 —— `sys:ecosystem_positioning_scan`（生态定位扫描）📋 已设计，未实现

- 目标：复用 `tech_radar_search.py` 已有的"检索 → LLM 抽取 → 落盘 wiki"管道，但种子来源
  从"自身能力弱点"换成"同类 agent 框架/相关开源项目近期变化"，产出的候选与
  `external_trend_capability_link` 的候选分开落点（不同 `source_kind`/候选文件），
  定位为"看别人在解决什么我还没意识到是问题的问题"，作为对 P4（原计划）"只补已知短板"
  这一视野局限的补充。
- 推荐节奏：`interval:604800`。
- 依赖：需要先确定一份"同类项目"种子列表的维护方式（人工配置 vs 自动发现），本次先只做
  设计，留待与用户确认种子列表来源后再实现。

### P5 —— `sys:monthly_trend_retrospective`（月度战略回顾）📋 已设计，未实现

- 目标：汇总过去 4 周 `external_trend_capability_link` 候选采纳情况、wiki 专题页增长、
  `self_eval` 能力变化趋势，生成一份月度回顾文档，供 `decision_profile_update`/
  `soft_goal_deriver` 参考。
- 推荐节奏：`cron:0 0 1 * *`（每月 1 日）。
- 本次先只做设计，待 P1-P4 跑出一段时间数据后再实现，否则回顾文档在早期会因数据量不足
  而空洞。

## 4. 本次实施范围小结

本次（v1.0）只实施 P1，P2-P5 留档设计、明确标注未实现及各自的关键前置缺口/风险，不打
"实现了但是半成品"的擦边球——这与项目里 P12/P13 阶段"部分内容显式延后"的一贯做法一致。
