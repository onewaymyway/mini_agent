# 外部数据知识化与自我改进闭环 改进计划

- **版本**: v1.4（P1-P4 已实现，P5 待实施/可选）
- **变更记录**:
  - v1.1：P1（外部事件 → wiki 抽取管道）已实现，见该节内的"实现记录"标注。
  - v1.2：P2（技术专题页优先聚合）已实现，见该节内的"实现记录"标注。
  - v1.3：P3（主动检索反哺 wiki，`sys:tech_radar_search`）已实现，见该节内的"实现记录"标注。
  - v1.4：P4（外部知识接入自我改进候选生成，`sys:external_trend_capability_link`）已实现，见该节内的"实现记录"标注。
- **背景任务**: 对现有外部数据输入/处理/使用机制（External Input Gateway、web_search 工具、wiki 知识库）做一次现状梳理，识别"外部世界信息未被沉淀为可复用知识"的断层，规划补齐路径
- **关联文档**:
  - `docs/external-input-gateway-guide.md`（外部输入网关，含 §11 当前实际数据流向）
  - `docs/wiki-knowledge-base-guide.md`（wiki 知识库）
  - `docs/watchlist-notification-guide.md`（`GoalRelevanceEngine`/`NoveltyJudge`）
  - `docs/mini_agent_核心理念与长期规划.md`（本计划的优先级排序依据其中"数据采集不应先于数据消费"一条）

---

## 1. 现状盘点

### 1.1 三层现状

**输入层**：`external_input/` 下仅有两类内置 `ExternalInputSource`——`WatchInputSource`（RSS/JSON API/HTML diff）与 `WeatherInputSource`（Open-Meteo）。当前 `.agent/external_input/sources.yaml` 实际配置了 5 个 source：`beijing_weather`（channel: `weather`）+ 4 个 RSS（`arxiv_cs_ai`/`hn_frontpage`/`sspai_feed`/`ithome_feed`，统一 `channel: agent_watch`，`params.keywords` 做标题前置过滤）。另有 `web_search` 工具，但它是对话轮次内**按需调用**的一次性检索，不是后台机制。

**处理层**：`GatewayPoller` 产生的事件统一落进 `system_events.jsonl`（`external.*` 命名空间），有三条独立消费链路：

| 消费者 | 判断的问题 | 命中后的落点 |
|---|---|---|
| `IngestionPolicy`（`policy.py`） | 事件是否配置了路由规则 | `notify_only` → `alerts.jsonl`；`enqueue_turn` → 直接提交 `InputQueue`（当前项目实际配置**未使用**此落点） |
| `GoalRelevanceEngine`（`goal_relevance.py`，`sys:goal_relevance_judge`） | 事件是否与**已有** Goal/Objective 相关 | 挂载/推进已有 Goal |
| `NoveltyJudge`（`novelty_judge.py`，`sys:novelty_importance_judge`） | 事件本身是否新颖重要，值得单独追踪 | `novelty_candidates.jsonl`，人工在看板确认后**创建新 Goal**（唯一允许创建新 Goal 的入口） |

**使用层**：wiki 的写入管道（`wiki/world_writer.py::queue_entities()`/`queue_facts()`，巩固循环 `consolidate_pending()` 批量判重落盘）目前**只被 `history/world_extraction.py` 调用**——即只从对话历史（compact 阶段）抽取实体/事实候选。`external.*` 事件从未作为世界模型的输入源。

### 1.2 核心断层

1. **"看到了" ≠ "记住了"**：4 个技术资讯 RSS 源持续产生 `new_item` 事件，命中关键词的标题最终只是一条 `alerts.jsonl` 里等人工点掉的通知；标题背后的内容（哪怕只是摘要）从未被提炼进 wiki，人工点掉后这条信息彻底消失，下次遇到同一主题等于从零开始。
2. **`web_search` 是消耗品，不是投资**：每次工具调用的检索结果只活在当轮对话里，没有落盘/复用机制，重复主题重复检索。
3. **外部信号未接入自我改进候选生成**：`evolution/soft_goal_deriver.py` 现有的信号采集方法（`_from_capability_map`/`_from_work_index`/`_from_lesson_review`/`_from_unexplored_capabilities`）均来自系统内部状态，没有一路来自"外部世界正在发生什么"。即使 wiki 里已经积累了相关外部知识，也没有桥接到"这是否值得作为一个改进方向"的评估环节。
4. **来源类型偏通用，不贴合"追踪技术动态"场景**：RSS 标题信息量有限（例如 `arxiv_cs_ai` 的 RSS 往往只有标题没有摘要），`html_diff`/`json_api` 这类通用 fetcher 也不是为"追踪某个 repo 的版本更新"这类场景设计的。

---

## 2. 设计目标

1. **不新建外部知识的存储/组织体系**——复用现成的 wiki 判重、写入、专题页、巩固循环机制，`external.*` 只是 `world_writer.py` 的新增来源之一，而不是另起一套。
2. **先打通"已有采集"的消费，再考虑扩大采集范围**——严格遵循"数据采集不应先于数据消费"，本计划的阶段顺序即体现这一点：P1（打通现有 4 个 RSS 源已产生的事件）优先于 P4（新增来源类型）。
3. **成本可控、默认不放大 LLM 调用**——新增的 cron job 全部走"低频批量"节奏（对齐现有 `sys:consolidation`/`sys:daily_digest` 一类节拍），不逐事件即时调用 LLM。
4. **外部知识最终要能反哺自我改进候选**，而不是止步于"多了一个知识库"。

---

## 3. 分阶段实施计划

### P1 —— 外部事件 → wiki 抽取管道（优先级最高，补最大的断层）✅ 已实现

> 实现记录（本次改动）：新增 `src/mini_agent/external_input/knowledge_extractor.py`
> （`run_external_knowledge_extraction_once()` + `ensure_external_knowledge_extractor_job()`），
> 在 `api/server.py` daemon 启动流程里注册 `sys:external_knowledge_extractor`
> job（首次创建即 `disable()`，符合 §4"默认先 disabled 接入"）。
> `wiki/world_writer.py::queue_entities()`/`queue_facts()`/`consolidate_pending()`
> 新增 `source_kind` 参数（默认沿用原来的 `"world_model"`），本模块调用时传
> `EXTERNAL_WATCH_SOURCE_KIND="external_watch"`。测试见
> `tests/test_external_input_knowledge_extractor.py`。详见
> `docs/wiki-knowledge-base-guide.md` §十二、
> `docs/external-input-gateway-guide.md` §11.2。

新增 cron job `sys:external_knowledge_extractor`，建议 `interval:21600`（6 小时一次，与 `sys:consolidation` 错峰）：

- 消费入口：复用 `perception/system_events.py::poll_since(consumer_name="external_knowledge_extractor", event_types=["external.watch.new_item"])`，游标机制与其余消费者一致，不新造持久化。
- 范围限定：只处理 `payload.channel == "agent_watch"` 的事件（即当前 4 个技术资讯 RSS 源产生的、已经过标题关键词过滤的条目），避免把天气类事件也拉进来。
- 抽取逻辑：对每条命中事件，用 `LLMHelper`（遵循"主对话循环之外的 LLM 调用统一走 LLMHelper"的开发规范）做一次轻量摘要抽取，产出 `EntityCandidate`/`FactCandidate`（复用 `history/world_extraction.py` 里已有的数据结构，不新建）。
- 落盘：直接调用 `wiki/world_writer.py::queue_entities()`/`queue_facts()`，`source_entries` 填事件的 `id`/`url`。候选 frontmatter 里新增 `source_kind="external_watch"`（区别于对话来源的默认值 `world_model`），供 `wiki/stats.py` 统计"外部世界知识占比"，同时不影响巩固循环里已有的判重/合并逻辑（`consolidate_pending()` 一视同仁处理 pending 队列，无需分叉）。
- 失败处理：单条抽取失败跳过、不阻塞整批（与 `evolution/step_runner.py::run_step()` 的"超时跳过不重试"思路一致）。

**验收标准**：运行若干天后，`wiki/entities/` 或对应专题页里能看到明确标注 `source_kind: external_watch` 的条目，且与 `alerts.jsonl` 里对应的通知能对上（人工核对 1:1 抽样即可）。

### P2 —— 技术专题页优先聚合（低成本，紧接 P1）✅ 已实现

> 实现记录（本次改动）：`wiki/topics.py` 新增 `build_topic_digest()`/
> `build_topic_digest_section()`。`knowledge_extractor.py` 每次 run 扫描
> 一次现有专题页，注入抽取 prompt 并新增可选输出字段 `topic_id`；命中时
> 调用 `wiki/writer.py::append_section()` 直接追加进专题页（跳过
> entity 判重/新建流程），未命中原样走 P1 兜底逻辑。测试见
> `tests/test_wiki_topic_digest.py`（digest 生成）与
> `tests/test_external_input_knowledge_extractor.py`（topic_id 命中/未命中
> 两种路径）。详见 `docs/wiki-knowledge-base-guide.md` §十二·2。
>
> 专题页种子（"为关注领域预先建好专题页"）本身不是代码改动——复用
> `generate_topic_page()`/`consolidate_topics()` 或人工创建即可，本轮
> 未新增种子配置文件/CLI 命令。

P1 产出的候选如果每条都各自建一个零散 entity 页，几个月后会积累大量碎片页面。建议：

- 为关注领域预先在 wiki 里建好专题页种子（复用 `wiki/topics.py` 现有的专题页生成/再巩固能力），例如"AI Agent 架构动态"“LLM 相关论文追踪”。
- P1 的抽取 prompt 里注入这些专题页的现有内容摘要（复用 `wiki/entity_digest.py::build_entity_digest()` 同类做法），引导模型优先判断"这条新闻应该追加进哪个专题页"，而不是无脑新建 entity。
- 无法匹配任何专题页的候选，走现有兜底逻辑（`entities/session-facts-<date>.md` 或独立 entity 页），不额外新增机制。

**验收标准**：`wiki/stats.py` 统计里，`source_kind=external_watch` 的候选归入已有专题页的比例应逐周上升（人工审查即可，不需要新增自动化指标）。

### P3 —— 主动检索反哺 wiki（把 web_search 从消耗品变成可复用投资）✅ 已实现

> 实现记录（本次改动）：新增 `src/mini_agent/external_input/tech_radar_search.py`
> （`run_tech_radar_search_once()` + `ensure_tech_radar_search_job()`），
> 在 `api/server.py` daemon 启动流程里注册 `sys:tech_radar_search` job
> （首次创建即 `disable()`，符合 §4"默认先 disabled 接入"）。种子采集
> `_collect_seed_pool()` 依次合并 `wiki/gap_scanner.py::scan_gaps()` 缺口
> 页面 id 与 `agent_config.json` 里 `tech_radar.keywords` 手工关键词（去重，
> 前者优先）；新增 `config/models.py::TechRadarConfig`
>（`enabled`/`keywords`/`daily_seed_limit`/`max_search_results`），
> `config/loader.py` 解析 `agent_config.json` 里的 `tech_radar` 配置块。
> 种子池可能大于每日上限，新增独立轮转游标文件
> `AgentPaths.external_input_tech_radar_state`（不复用事件游标机制，因为
> 本模块没有消费 `system_events.jsonl`），按顺序滚动处理、循环到末尾自动
> 回绕，几天内覆盖完整个种子池。检索直接调用既有
> `tools/builtin.py::web_search()`，抽取产出的 `entities[]`/`facts[]`
> 复用 P1 的 `EntityCandidate`/`FactCandidate` 与
> `wiki/world_writer.py::queue_entities()`/`queue_facts()`，落盘时打
> `source_kind="external_search"`（`world_writer.EXTERNAL_SEARCH_SOURCE_KIND`，
> 常量在 P1 阶段已预留，本次去掉"预留"注释正式启用）。`source_entries`
> 里记录 `tech_radar_search:{run_id}:{seed}` 追溯标记 + 检索结果中解析到的
> 真实 URL（最多 3 条），满足验收标准里的可追溯要求。单个种子检索失败、
> 单条解析失败均不阻塞其余条目；全部检索失败或 LLM 调用失败时不推进轮转
> 游标，下次运行重新处理同一批种子。测试见
> `tests/test_external_input_tech_radar_search.py`。

新增 cron job `sys:tech_radar_search`，建议 `interval:86400`（每天一次，节奏对齐 `sys:self_eval`）：

- 种子来源：优先复用 `wiki/gap_scanner.py` 已有的知识缺口扫描结果；缺口扫描暂未覆盖的领域，退化为几个手工配置在 `agent_config.json` 里的关注关键词列表（初期先简单实现，不追求自动发现）。
- 执行：对每个种子调用 `web_search` 工具（走既有 `tools/builtin.py::web_search()`，不新增检索通道），取回结果后摘要提炼，同样走 P1 的 `queue_entities`/`queue_facts` 落盘管道，`source_kind="external_search"`。
- 频率控制：每次运行处理的种子数量设上限（例如 5 个/天），避免 cron 触发即引发大量 LLM/网络调用；具体上限做成配置项而非硬编码。

**验收标准**：wiki 里能看到 `source_kind: external_search` 的条目，且能追溯到是哪次 `sys:tech_radar_search` 运行、针对哪个种子产生的（落盘时记录触发上下文）。

### P4 —— 外部知识接入自我改进候选生成 ✅ 已实现

> 实现记录（本次改动）：新增 `src/mini_agent/evolution/external_trend_capability_link.py`
> （`run_external_trend_capability_link_once()` +
> `ensure_external_trend_capability_link_job()`），在 `api/server.py` daemon
> 启动流程里注册 `sys:external_trend_capability_link` job（首次创建即
> `disable()`，符合 §4"默认先 disabled 接入"）。数据源两路：
> `_load_external_knowledge_pages()` 扫描 wiki 全量页面筛出
> `source_kind` 属于 `external_watch`/`external_search` 的条目（复用
> `wiki/indexer.py::discover_pages()` + `wiki/parser.py::parse_page()`，
> 与 `wiki/stats.py::compute_stats()` 同款用法）；
> `_load_weak_capabilities()` 复用
> `evolution/consolidation.py::load_capability_map()`，筛出
> confidence 低或 total_calls 极少的条目（阈值与
> `soft_goal_deriver.py` 里的 `CONFIDENCE_LOW`/`MIN_CALLS_FOR_KNOWN`
> 保持数值一致）。用 `LLMHelper` 做一次轻量匹配，产出的候选要求
> `capability_domain`/`wiki_page_ids` 必须真实存在于输入数据里，否则
> 事后过滤掉。落点两处：结构化候选写入
> `AgentPaths.external_trend_capability_link_state_path`（14 天去重
> 窗口，同一 (能力域, wiki 页面 id 集合) 组合不重复产出），供
> `evolution/soft_goal_deriver.py::SoftGoalDeriver._from_external_knowledge()`
> 消费——新增的这一路信号进入既有的 `_DeriveCandidate`/
> `derive_candidates()`/`commit_goals()` 流程（`source_tag="external_knowledge"`，
> 在 `commit_goals()` 里被标记 `needs_review`，与 workthread/lesson 两路
> 一致），不绕开"autonomous 档位下才 derive"的既有规则；人类可读草稿写入
> `AgentPaths.external_trend_capability_candidates_path`
> （`.agent/wiki/external_trend_capability_candidates.md`），格式与
> `evolution/decision_profile_builder.py::_write_profile_md()` 一致，供人工
> 审核。测试见 `tests/test_external_trend_capability_link.py`。详见
> `docs/wiki-knowledge-base-guide.md` §十二·4、
> `docs/memory-and-self-evolution-complete-reference.md`。

新增 cron job `sys:external_trend_capability_link`，建议 `interval:604800`（每周一次，节奏对齐 `sys:decision_profile_update`）：

- 输入：P1-P3 沉淀的外部知识专题页（`source_kind` 为 `external_watch`/`external_search` 的条目）+ `AgentSelfModel`/`capability_map` 的能力评估结果。
- 关联逻辑：轻量匹配外部知识专题页涉及的主题与 `capability_map` 中 `confidence < CONFIDENCE_LOW` 或 `total_calls` 极少的能力条目，产出一份"外部动态 × 能力薄弱点"的候选草稿。
- 落点：**只生成草稿文档，不自动创建 Goal、不自动修改代码**——本质是在 `evolution/soft_goal_deriver.py` 现有的信号采集方法基础上新增一个 `_from_external_knowledge()`，产出的候选进入既有的 `_DeriveCandidate` 流程，仍然遵循"autonomous 档位下才 derive、其余档位只记录不生成"的既有规则，不改变整体风险模型。
- 人工审核：产出物形式与 `decision_profile_builder.py`/P12-P14 一类改进计划草稿一致，先是草稿，人工审核后再决定是否实施——对应理念文档"阶段 A→B"（先诊断/记录，再提案）而非直接跳到"自我修改"。

**验收标准**：每周能看到一份结构化的"外部技术趋势 × 自身能力薄弱点"候选草稿，且候选有明确的、可追溯到具体 wiki 条目和 capability_map 条目的依据（不是凭感觉生成的建议）。

### P5（可选，视 P1-P4 实际效果决定是否投入）—— 更贴合场景的来源类型

现有 `watch`（RSS/json_api/html_diff）在"追踪技术动态"场景下信息密度有限。可视需要新增：

- `arxiv_api` 类型：直接调用 arXiv 官方 API 拿到结构化 abstract，而非解析 RSS 标题。
- `github_release` 类型：追踪关注 repo 的 Release/Tag 更新。

两者都严格遵循 `ExternalInputSource` 扩展点（`@register_source`），不改动网关核心代码，实现方式参考 `builtin/watch.py`/`builtin/weather.py`。

**本阶段暂不安排具体实施时间**——先看 P1-P4 运行数周后，"来源信息密度不够"是否真的成为瓶颈，避免在验证消费价值之前又扩大采集。

---

## 4. 新增 cron job 汇总

| job id | 频率 | 对齐节拍 | 是否消耗 LLM | 落点 |
|---|---|---|---|---|
| `sys:external_knowledge_extractor` ✅ | `interval:21600` | 类似 `sys:consolidation` | 是（批量、轻量摘要） | `wiki/world_writer.py` pending 队列 |
| `sys:tech_radar_search` ✅ | `interval:86400` | 类似 `sys:self_eval` | 是（web_search + 摘要，有每日上限） | 同上 |
| `sys:external_trend_capability_link` ✅ | `interval:604800` | 类似 `sys:decision_profile_update` | 是（低频、一次性关联） | 改进候选草稿文档 + `soft_goal_deriver._from_external_knowledge()` 消费的结构化候选（不绕开既有 derive/commit 安全阀） |

三者均以 `sys:` 前缀注册（不可删除只可 disable，与既有内置 job 治理规则一致），默认建议**先以 disabled 状态接入**，人工评估几天后再手动开启，降低新增机制的试错成本。已实现的三个 job（`sys:external_knowledge_extractor`/`sys:tech_radar_search`/`sys:external_trend_capability_link`）在首次创建时均已按此规则调用 `disable()`。

---

## 5. 风险与取舍

- **摘要抽取质量**：`external.*` 事件的抽取质量依赖标题/摘要本身的信息量，RSS 标题信息不足时抽取出的 entity/fact 可能空洞——这是 P5 里"更结构化来源"想解决的问题，P1 阶段先接受这个局限，观察实际效果再决定是否值得投入 P5。
- **专题页质量退化风险**：P2 依赖模型判断"这条新闻该归入哪个专题页"，判断错误会污染专题页内容。缓解方式是复用 wiki 已有的"陈旧专题页自动标注过时"机制兜底，而不是额外新增校验逻辑。
- **P4 的关联可能是噪音**：外部技术趋势与能力薄弱点的匹配是轻量规则/LLM 判断，存在牵强附会的风险——因此明确规定只产出草稿供人工审核，不自动生成 Goal，风险敞口与现有 `decision_profile_builder`/软目标 derive 的既有安全阀一致。

## 6. 暂不推进

- 自动根据外部知识 wiki 生成并提交自我修改代码的提案（阶段 D，缺少沙箱与回滚机制前不做，与核心理念文档保持一致）。
- 扩大到 webhook/邮件/IM 等全新外部输入渠道接入（本计划聚焦"已有采集的消费能力"，渠道扩展不是当前瓶颈）。
- P4 产出的候选自动转化为 `enqueue_turn`（直接触发 Agent 推理）——`IngestionPolicy` 当前项目里也未启用此落点，本计划不改变这一现状。
