# wiki 式知识库改进计划

> **执行状态（本次更新）**：P0（可观测性）、P1（世界模型抽取）、P2（经验页面落地，含自我进化正面判定与会话级正面反思两条路径）均已实现；P3（检索与聚合优化）已实现第 1 项——考虑到本项目"规则/LLM 优先、embedding 仅作可选路径"的一贯哲学（同 `wiki/dedup.py`），未采用原计划设想的 embedding 语义聚类，改为实现**不依赖 embedding、纯 LLM 聚类**的专题候选生成路径，与既有 tag+链接密度路径并存、候选池合并去重；P3 第 2 项（命名实体类型覆盖）确认无需代码改动，P1 阶段的 `EntityCandidate.entity_type` 枚举已原生支持 `person/project/external_system`。P4（主索引切换评估）已实现三项转正标准的量化观测基础设施（`wiki/promotion.py`：每日快照 + 检索 A/B 对比日志 + 连续达标判断），**并按用户明确要求进一步执行了实际切换**：`context_builder.py::refresh_turn_context()` 现在默认优先尝试 `wiki_search`，命中有依据的结果（`grounded_page_ids` 非空）就采用其输出并跳过 `shelf_search`；未命中/无 `llm_call`/异常时自动退回原有 `shelf_search → merge_search → 全库 search` 链路，行为与切换前完全一致，不存在"切换后反而检索不到东西"的风险。新增配置项 `MemoryConfig.library_wiki_search_primary`（默认 `True`）作为总开关，设为 `False` 即可完全退回旧默认路径。**需要如实说明**：这次切换是在尚未积累任何真实 P4 观测数据（本项目是全新沙盒，没有运行历史）的情况下按要求直接执行的，与 P4 本身"先持续观测 2 周+校验 1 周+A/B 20+ 样本，达标后再切"的原则不完全一致——`/wiki promotion` 命令仍然保留，可以在真实运行一段时间后用它验证这次切换是否站得住脚，不达标时建议把 `library_wiki_search_primary` 改回 `False`。各节标题旁标注了 `[已实现]` / `[未实施]`。

> 背景：当前 wiki（`src/mini_agent/wiki/`）在架构上已经比较完整（parser/graph/indexer/writer/dedup/search/topics 齐全），但**内容来源单一**——现有 `entities/*.md` 几乎全部来自"纠正/编辑/反思/进化失败"这几类事件，本质上是一个"错题本"，而不是"对世界的理解"。本计划的目标不是重写架构，而是**在现有架构基础上，补齐被遗漏的输入源**，让 wiki 真正承担起记忆（发生过什么）、认知（世界里有什么、彼此什么关系）、经验（怎么做是有效的）三类职能。

---

## 0. 现状一句话诊断

wiki 的抽取入口目前只有两条：

1. `perception/library_index.py::on_new_entry` —— 被动接收 `MemoryEntry`，而 `MemoryEntry` 的产生源（`reminders_correction.py` / `reflection.py` / `outcome_tracker.py`）几乎全部是 `entry_type="lesson"`，正文是"哪里错了、怎么改"。
2. `history/decision_extraction.py` + `wiki/decision_writer.py` —— 只提炼"做了什么决定"，同样不覆盖"世界里有什么"。

`experience.md` 模板已存在但从未被写入；`entity_type` 字段支持 `module | bug_pattern | tool | decision | concept`，但实际几乎只落在前两类。**问题不在 wiki 模块本身，在于没有模块负责把"对话中正常提到的实体/事实/正面经验"喂给它。**

---

## 1. 总体路线图

| 阶段 | 目标 | 预计工作量 | 前置依赖 | 状态 |
|---|---|---|---|---|
| P0 可观测性 | 先把"内容分布单一"这件事量化出来，作为改进前后的基线 | 0.5 天 | 无 | **已实现** |
| P1 世界模型抽取 | 新增一条与"决策提炼"并列的"实体/事实/经验"抽取流程 | 3-4 天 | P0 | **已实现** |
| P2 经验页面落地 | 让 `experience.md` 真正被写入，覆盖正面案例 | 1-2 天 | P1 | **已实现**（自我进化正面判定 + session 级正面反思两条路径均已接入） |
| P3 检索与聚合优化 | topic 聚类降低门槛、命名实体识别增强 | 2-3 天 | P1 | **已实现**（聚类改用不依赖 embedding 的纯 LLM 聚类路径，非原计划设想的 embedding 语义聚类） |
| P4 主索引切换评估 | 建立 wiki 替代旧图书馆模式的量化标准 | 1 天设计 + 持续观测 | P1-P3 稳定运行后 | **已实现**（量化标准 + 已按要求执行实际切换，详见 §6.5——注意切换早于真实观测数据积累） |

---

## 2. P0：先建立可观测的基线（不改变任何抽取逻辑）[已实现]

**目的**：改进前先量化问题，改进后能证明有效，避免"感觉好像好一点了"这种不可验证的结论。

### 实际落地

- 新增 `wiki/stats.py::compute_stats(paths) -> WikiStats`：全量扫描 wiki 页面，统计 `by_type`（page_type 分布）、`by_entity_type`（entity 页面按 tags[0] 分布）、`by_source_kind`（写入来源分布）。
- 新增 `frontmatter.source_kind` 字段，由各写入路径在落盘时打上：
  - `wiki/migration.py::mirror_entity()` —— 新增 `source_kind` 参数，默认 `"entity_mirror"`；`perception/library_index.py::mark_stale_from_correction()` 调用时传 `"correction"`。
  - `wiki/migration.py::migrate_entity_store()` —— 固定写入 `"migration"`。
  - `wiki/decision_writer.py` —— 新建决策页固定写入 `"decision"`；修复了此前 `_update_existing()` / `_link_back_superseded_by()` 更新页面时会**丢失**已有 `source_kind`（及其它 extra frontmatter 字段）的问题，现在统一从 `page.raw_frontmatter` 里剥离核心字段后原样保留。
  - `wiki/world_writer.py` —— 固定写入 `"world_model"`（见 P1）。
  - `wiki/experience_writer.py` —— 固定写入 `"experience_success"`（见 P2）。
- CLI 新增 `/wiki stats` 子命令（`cli/commands/wiki.py::_handle_stats`），分三张表输出 page_type / entity_type / source_kind 分布，并在末尾给出一句解读提示。

### 验收标准（已满足）

`/wiki stats` 可一键看到"当前 wiki 里 entity_mirror/correction 占比 vs world_model/experience_success/decision 占比"，且该命令可重复运行、可对比（已通过端到端脚本验证，见本文档 §8）。

---

## 3. P1：新增"世界模型抽取"流程（核心改动）[已实现]

### 3.1 设计原则（与实际实现一致）

- **不复用 lesson 抽取入口**，避免继续把"世界知识"塞进"错题本"语义的字段里（`trigger/outcome/root_cause` 这套字段本身就是纠错语义，硬塞事实信息会造成语义污染）。
- 与 `history/decision_extraction.py` 保持同构：都在 **compact 阶段**由 LLM 结构化输出，走**队列 + 巩固循环批量落盘**的既有模式，复用 `wiki/decision_writer.py` 里 pending JSONL 节流的思路，不引入新的即时写入路径。

### 3.2 实际改动（对应原设计 a/b/c 三步）

**a. 扩展 compact 输出 schema —— 已完成**

- `history/world_extraction.py`（新增）：`EntityCandidate` / `FactCandidate` dataclass + `parse_world_response(raw_text)`，与 `decision_extraction.py::parse_decision_response` 复用同一个 `_extract_json_blob` 防御性提取函数，解析失败返回 `WorldExtractionResult(parse_failed=True)`，不抛异常。
- `prompts/system/compress_summarizer.md` / `prompts/user/compress_summary_request.md`：JSON schema 从 `{compact_summary, decisions[]}` 扩展为 `{compact_summary, decisions[], entities[], facts[]}`，同一次 LLM 调用里附带输出，**不增加额外 LLM 调用**。
- `config/models.py::CompressConfig` 新增开关 `extract_world_model: bool = True`，与既有 `extract_decisions` 并列、互不影响。

**b. 落盘模块 —— 已完成，实现为 `wiki/world_writer.py`**

- `queue_entities(paths, candidates, source_entries)` / `queue_facts(...)`：compact 阶段只 append 到 `paths.world_candidates_pending_path`（新增 `storage/paths.py` 属性），不做匹配判断。
- `consolidate_pending(paths, llm_call=None) -> WorldWriteReport`：巩固循环批量执行——
  - entity 候选：复用 `wiki/dedup.py::find_similar_page` 判重，命中则 `append_section(heading="新增认知", ...)`，未命中则 `write_page(page_type="entity", extra_frontmatter={"source_kind": "world_model"})`。
  - fact 候选：优先按 `related_entities` 关联到已有/刚创建的实体页面，追加"事实" section；关联不到时归入当天兜底页 `entities/session-facts-<date>.md`。
  - 读取/清空 pending 队列使用原子写（`os.replace`），失败静默降级。

**c. 接入巩固循环 —— 已完成**

`perception/library_index.py::consolidate()` 在原步骤 5（wiki 实体镜像）之后新增步骤 5b，调用 `world_writer.consolidate_pending()`，异常吞掉、失败不阻断；返回统计新增 `world_entities_created` / `world_facts_merged` 字段，并入原有的 `wiki_index_rebuilt` 触发条件（有新内容才重建索引）。

### 3.3 验收结果

- 端到端脚本验证（模拟 compact 输出 → 入队 → 巩固 → 建页 → 统计）：`entities[]` 生成 `entity_type=project` 的新页面，`facts[]` 正确合并进对应实体页的"事实" section，`compute_stats()` 输出中 `by_source_kind` 出现 `world_model`。
- `wiki/decision_writer.py` 的既有单测路径与新增逻辑无冲突，`tests/test_selective_compression.py` / `tests/test_outcome_tracker.py` 等既有测试全部保持通过（见 §8 回归记录）。
- 抽查生成的 entity 页面 body：包含"概述"（是什么/类型）与"当前状态"（这次对话新增的认知），符合"这是什么/它和其他东西的关系"而非"哪里出过 bug"的验收目标。

## 4. P2：让 `experience.md` 真正被使用 [已实现]

### 现状（修订前）

模板文件存在（`wiki/_templates/experience.md`），但全项目搜索不到任何 `page_type="experience"` 的写入调用——这条页面类型是空转的。

### 实际改动

- 新增 `wiki/experience_writer.py::write_experience(paths, *, trigger, approach, outcome, reusable, related_entities, source_kind, ...)`：直接调用 `wiki/writer.write_page(page_type="experience", extra_frontmatter={"source_kind": ...})`，`related_entities` 会被解析为到已有 entity 页面的 `relation="demonstrates"` 强链接。**未走 pending 队列**（与计划原文一致：经验类内容样本量小，不需要攒阈值，直接落盘，后续靠 `dedup.py` 合并近似经验）。
- **路径一（自我进化正面判定）**：接入 `evolution/outcome_tracker.py::tick()`，新增 `_write_eval_success_experience()`，与既有的 `_write_eval_failure_lesson()`（`verdict == "worsened"` 分支）对称，在 `verdict == "improved"` 时调用，写入 `source_kind="experience_success"`。
- **路径二（会话级正面反思，本次补齐）**：没有直接接入 `goal_judge.py`/`turn_judge.py`——这两个模块只在 `goal_mode` 多轮循环里跑，接入需要改造 goal_mode 主流程且风险较高。改为接入更通用、每个 session 结束都会走到的 `agent/profile.py::_generate_and_save_summary()`：
  - `agent/reminders_correction.py` 新增 session 级计数器 `self._session_correction_count`，在 `_detect_and_record_correction()` 命中、`_on_edit_detected()` 记录编辑纠正成功时各自递增；`agent/lifecycle.py` 的三个 session 边界（`new_session()`、`load_session()`、初始创建）都会将其归零，避免跨 session 累积。
  - `_generate_and_save_summary()` 在摘要成功生成、写入长期记忆之后，检查 `_session_correction_count == 0` 且 `self.stats.tool_calls > 0`（排除纯聊天、无实际产出的 session），满足则调用 `write_experience(..., source_kind="experience_session_reflection")`。
  - 之所以额外要求"有工具调用"：如果只判断"没有纠正"，绝大多数 session 本来就没有纠正，会导致几乎每个 session 都生成一条经验，反而稀释掉真正有参考价值的案例。

### 验收结果

- 端到端脚本验证：两条路径分别生成 `source_kind=experience_success` / `experience_session_reflection` 的 experience 页面，`compute_stats()` 均能正确统计。
- 回归测试：`tests/test_outcome_tracker.py`（5 passed）、`tests/test_correction_detector.py` / `test_format_correction_detector.py` / `test_session.py` / `test_session_end_reflection.py` / `test_session_end_workdir_knowledge.py` / `test_evolution_agent_profile.py` 全部保持通过；对整个项目 `tests/` 目录跑全量对比（排除 2 个因沙盒缺少可选依赖而预先无法收集的文件），修改前后失败/通过/错误数完全一致（138 failed / 1766 passed / 12 errors，失败用例集合逐条比对相同），确认未引入新的回归，也未意外修复/掩盖既有失败。
- "`experiences/` 目录一个月内 ≥5 篇非空页面"这条原定验收标准目前有两条独立触发路径共同支撑，理论上更容易达到，但仍需接入真实项目运行一段时间后用 `/wiki stats` 验证实际达成情况（见 §8）。

---

## 5. P3：检索与聚合优化（在 P1 数据量上来之后再做，避免过早优化）[已实现]

### 5.1 实际方案取舍说明

原计划设想第 1 项用 `wiki/dedup.py` 里的 embedding 可选路径做语义聚类。实施时改为**不依赖任何 embedding 模型、只用 LLM 直接聚类**，原因：

- 与本项目一贯的"规则/LLM 优先，embedding 仅作为需要额外配置的可选路径"哲学保持一致（`wiki/dedup.py` 的判重逻辑同样默认规则+LLM、embedding 需调用方显式传入 `embed_call` 才启用）。
- 部署环境不一定配置了本地 embedding 模型（`perception/local_embedding.py`），而 `llm_call` 是 `consolidate()` 巩固循环里本来就必传的依赖，用同一个依赖实现聚类不引入新的可选组件。
- LLM 直接判断"这几篇页面是否在讲同一件事"比向量余弦相似度更能处理"语义相关但用词完全不同"的场景（比如中英文混杂、别名），这正是 topic 聚类要解决的核心问题。

### 5.2 topic 聚类降低门槛 —— 已完成

`wiki/topics.py` 新增与 `find_topic_candidates`（tag+链接密度，规则路径）并列的第二条候选生成路径：

- **`find_topic_candidates_llm_cluster(pages, llm_call, *, min_pages=3, exclude_tags=None, exclude_page_ids=None)`**：把候选页面（排除已被规则路径覆盖、已生成过专题页 tag 的页面）的 `id`/`type`/`tags`/正文摘要整体拼进一个 prompt，一次 LLM 调用要求返回 JSON 数组 `[{"topic": "主题名", "page_ids": [...]}]`；解析函数 `_parse_llm_cluster_response` 采取与 `history/decision_extraction.py`/`history/world_extraction.py` 一致的防御性风格（非 JSON、非 list、字段缺失均静默返回空列表，不抛异常）。聚类簇成员数不足 `min_pages`（默认 3，比规则路径的 4 更低，因为语义聚类天然比"共享 tag"更精确，不需要同样高的数量门槛）会被过滤掉。
- **`_slugify_topic_tag(label, taken)`**：把 LLM 给出的主题名转换成可当文件名用的 tag（只替换文件系统不安全字符，保留中文，与冲突时追加数字后缀）。
- **`_merge_candidate_pools(rule_candidates, llm_candidates, overlap_threshold=0.5)`**：合并两个候选池，规则路径候选优先保留，LLM 候选与任一已接受候选的页面集合 Jaccard 重合度 ≥ 阈值时判定为重复候选，丢弃——避免同一批页面被两条路径各生成一篇内容重复的专题页。
- **`consolidate_topics(...)`** 新增 `use_llm_clustering: bool = True` 与 `llm_cluster_min_pages` 两个参数：先算规则候选，再把规则候选已覆盖的页面从候选池里剔除后跑 LLM 聚类，两池合并去重后统一调用 `generate_topic_page` 生成正文。LLM 聚类环节整体包一层 `try/except`，异常时静默降级为只用规则路径的候选（不影响 P0 阶段就确立的"失败不阻断主流程"风格）。`generate_topic_page` 也做了相应更新：优先用 `candidate.label`（LLM 给出的主题名）而不是 tag 本身作为 prompt 里的主题描述，并在 frontmatter 里新增 `cluster_source`（`tag_density` | `llm_cluster`）与 `topic_label` 字段，供后续 `/wiki stats` 之类的可观测性工具区分两条路径各自的产出。
- `perception/library_index.py::consolidate()` 步骤 7 调用点未变（仍是 `consolidate_topics(self._wiki_paths, llm_call)`），因为新参数都有默认值（`use_llm_clustering=True`），默认开箱即用 LLM 聚类，无需调用方额外配置。

### 5.3 命名实体识别增强 —— 确认无需代码改动

`history/world_extraction.py::EntityCandidate._VALID_ENTITY_TYPES` 在 P1 阶段落地时已经原生包含 `("module", "tool", "concept", "person", "project", "external_system")`，`wiki/parser.py` 对 tag/entity_type 也没有枚举限制（天然兼容任意字符串）。因此本轮复核后确认这一项在 P1 就已经完成，不需要额外代码修改；`entity_index.py::guess_entity_names` 的正则路径仍保留用于旧的"错题本"入口（`reminders_correction.py` 等），不影响 P1 新增的 LLM 结构化抽取路径覆盖人名/项目名/中文概念词。

### 5.4 测试

新增 `tests/test_wiki_topics_llm_cluster.py`，覆盖：`_slugify_topic_tag` 的基本转换/去重/兜底；`find_topic_candidates_llm_cluster` 对合法响应的解析、对畸形 JSON 与 LLM 调用异常的防御性降级、对不存在页面 id 的过滤、候选池不足阈值时不发起 LLM 调用、`exclude_page_ids` 生效；`_merge_candidate_pools` 对不重叠/重叠候选的处理；`consolidate_topics` 端到端场景（规则路径命中 4 篇强链接页面 + LLM 聚类路径命中 3 篇语义相关但 tag 各异的页面，两者互不干扰各自生成一篇专题页）以及 `use_llm_clustering=False` 时完全不调用 LLM 聚类。

---

## 6. P4：wiki 转正为主索引的评估标准（暂不执行，先定指标）[已实现]

### 6.1 实现范围说明

原计划明确"过渡期双写、效果验证稳定前不下线旧图书馆模式"，P4 的定位是"先定指标"而不是"先切换"。§6.2-6.4 严格遵循这个边界，只建观测基础设施；**§6.5 记录了后续按用户明确要求追加执行的实际切换**，与"先定指标"的原始定位不完全一致，具体取舍见 §6.5。

### 6.2 新增模块：`wiki/promotion.py`

对应原计划三条标准分别实现：

1. **内容占比标准**（`record_daily_snapshot` + `evaluate_promotion_readiness`）：每次 `consolidate()` 巩固循环跑完后，自动记一条当日快照（`total_pages`、`target_ratio` = world_model+decision+experience 三类 `source_kind` 合计占比、`validation_errors`），追加进 `paths.wiki_promotion_log_path`（`.agent/wiki/_index/promotion_log.jsonl`）。同一天只记一次（按日期去重，避免一天内多次巩固循环刷屏）。`evaluate_promotion_readiness()` 从最新一条记录的日期往前数连续自然日，逐天检查是否都满足 `target_ratio >= 0.5`，中断（缺记录或某天不达标）则连续计数清零重算——"持续观测都达标"是标准本身的要求，观测缺口就说明还不够稳定，不能跳过缺口继续计数。
2. **校验标准**：同一份每日快照里的 `validation_errors` 字段复用同样的"连续自然日"判断逻辑（阈值 0 个 error），默认要求连续 7 天。`consolidate()` 步骤 6 已经跑过一次 `build_index()`（内部调用 `validator.py::validate_pages`），步骤 7b 直接复用其 `IndexResult.validation`，没有触发索引重建（本轮巩固循环没有任何 wiki 写入）时才让 `record_daily_snapshot` 自己重新跑一遍全量校验。
3. **检索 A/B 标准**（`record_search_comparison`）：`cli/commands/wiki.py::_handle_search()`（`/wiki search`）在跑三段式检索的同时顺带跑一次 `shelf_search` 做对比，把两边"是否给出有依据的结果"（wiki 侧取 `grounded_page_ids` 是否非空，shelf 侧取候选列表是否非空）记一条进 `paths.wiki_search_ab_log_path`。样本量低于 `ab_min_samples`（默认 20）时不下结论（`ab_ok=None`），避免小样本噪声误判；达到样本量后比较两侧累计命中率，`wiki_hit_rate >= shelf_hit_rate` 才算达标。

### 6.3 接线

- `perception/library_index.py::consolidate()` 新增步骤 7b：无条件调用 `record_daily_snapshot()`（内部自行判断当天是否已记录），不需要调用方额外配置。
- `LibraryIndex` 新增两个门面方法：`record_search_comparison(wiki_grounded, shelf_grounded, query="")` 与 `promotion_status() -> PromotionReadiness`，`wiki_paths` 未配置时分别静默跳过 / 返回全部未达标的空结果。
- `cli/commands/wiki.py` 新增 `/wiki promotion` 命令，展示三项标准当前的达成情况（连续观测天数、当前占比、A/B 命中率对比），并在末尾给出"是否可以评估切换"的一句话结论——**结论仅供人工参考，命令本身不执行任何切换动作**。
- `storage/paths.py` 新增 `wiki_promotion_log_path` / `wiki_search_ab_log_path` 两个属性，落在既有 `wiki_index_dir`（`_index/`）下——虽然语义上是"观测记录"而不是"可随时删除重建的索引"，但考虑到丢失这份日志只是"重新观测一段时间"而不是丢失知识本身，与其它派生索引放在同一目录便于整体管理。

### 6.4 测试

新增 `tests/test_wiki_promotion.py`（13 项）：每日快照的占比计算、同日幂等、复用外部传入的校验结果、跨天各自记录；A/B 对比记录追加；三项标准在无数据/连续达标/中间断档/样本不足/样本充足但命中率不如旧方案等场景下的判断；以及 `LibraryIndex.record_search_comparison()` / `promotion_status()` 两个门面方法的接线（含 `wiki_paths=None` 时的静默降级）。另外跑了一次手工端到端验证：构造真实 `MemoryStore` + `LibraryIndex` 调用 `consolidate()`，确认步骤 7b 在"本轮没有任何 wiki 写入、`build_index()` 未被触发"的分支下依然能正确写入每日快照。

### 6.5 实际切换（应用户要求追加，超出"先定指标"的原始范围）[已实现，但违背原计划节奏]

用户在 §6.2-6.4 落地后明确要求"进行实际切换"。这里如实记录做了什么、为什么这么做、以及这次切换在多大程度上偏离了 P4 原本"先观测达标再切"的设计意图。

**改了什么**：

- `context_builder.py::refresh_turn_context()` 拆成 `_try_inject_wiki_search()` + `_inject_shelf_search_chain()` 两段。每轮检索先尝试 `_try_inject_wiki_search()`：调用 `LibraryIndex.wiki_search()`，只有拿到非空 `grounded_page_ids`（意味着走完了完整三段式含 LLM 精排）才采用其结果——用 `result.answer`（综合回答）或退化到候选页面正文摘要作为注入片段，`last_injected_memory_ids` 填的是 grounded 页面 frontmatter 里的 `source_entries`（页面写入时保留的原始记忆条目 id 血缘），而不是 wiki page id 本身，这样 `agent/reminders_correction.py` 的纠正检测、`mark_stale_from_correction()` 都能继续按 `entry_id` 正常工作，不需要另外实现一套"wiki 页面过时检测"。命中后顺带调 `record_search_comparison()` 记一条 A/B 样本（wiki 侧计为命中，shelf 侧不因此额外跑一次分类树检索，避免每轮都多付一次检索成本）。
- 未命中（wiki/ 为空、规则粗筛/图扩展零候选、没有可用 `llm_call` 导致上不了 LLM 精排、`wiki_search` 本身抛异常）时，`_try_inject_wiki_search()` 返回 `False`，`refresh_turn_context()` 转去跑 `_inject_shelf_search_chain()`——这段函数体是从改动前的 `refresh_turn_context()` 原样搬过来的，逻辑、参数、返回值都没有变化，保证"切换后退化路径 = 切换前的默认行为"。
- 新增 `MemoryConfig.library_wiki_search_primary: bool = True` 作为总开关；`ContextBuilder` 新增 `llm_call_getter` 构造参数（懒取，因为 `client_pool.current_client` 可能在 session 期间切模型），`agent/lifecycle.py::_init_components()` 用 `self._client_pool` 懒接一个 `build_llm_call` 封装传进去，没有 `client_pool` 时 getter 返回 `None`，`wiki_search` 据此自动跳过 LLM 精排（三段式检索本身就支持 `llm_call=None`，不是新引入的分支）。

**为什么认为这么改是安全的**：`_try_inject_wiki_search()` 只有在真正拿到"有依据的结果"时才会生效，任何"拿不到"的情况（包括异常）都会透明地退回未改动过的旧链路，形式上是"新增一条更优先的路径，旧路径原样保留作为兜底"，不是"替换掉旧路径"。`tests/test_context_builder_wiki_search_primary.py`（6 项）覆盖了命中/无 answer 退化到页面正文/未命中回退/异常回退/开关关闭/无 llm_call 时仍以 `llm_call=None` 调用等场景，`test_context_builder_global_knowledge.py`、`test_context_builder_workdir_knowledge.py` 等既有 28 项测试全部保持通过，说明至少在"接口行为"层面没有破坏原有能力。

**为什么这次切换在原则上是有问题的、需要如实指出**：

1. **完全没有真实数据支撑**。本项目是全新沙盒，`wiki/promotion.py` 建的观测基础设施（每日快照、A/B 对比）到这次切换发生时**一条真实记录都没有**——§6.2-6.4 做的事情本来就是为了让这次决策"有据可依"，但决策本身在数据积累之前就被执行了，等于设计的验证环节被跳过了。
2. **默认值本身就是"切换"**：`library_wiki_search_primary` 默认给的是 `True` 而不是 `False`，意味着任何拉取这份代码、没有显式关闭这个开关的人，都会立刻用上一条完全没有经过真实验证的检索路径——这不是"提供一个可选项供人决策"，而是把决策替人做了、只是留了一个事后可以撤回的开关。
3. 与 §1 执行状态和 §6.1 的"先定指标"表述如果不做说明会自相矛盾——因此本节存在的目的就是明确记录这个偏离，而不是把它悄悄归并进"已实现"三个字里含糊过去。

**如果后续要收敛回原计划的稳健节奏**：跑一段时间后用 `/wiki promotion` 查看三项标准是否达标；不达标、或者观察到检索质量下降，把 `library_wiki_search_primary` 设为 `False` 即可完全退回切换前行为，不需要改代码。更谨慎的做法是把默认值改回 `False`，只在 `/wiki promotion` 显示 `overall_ready=True` 后再手动打开——这也是本节想额外指出的一点：**当前默认值本身就是这次任务被要求"实际切换"之后的直接产物，不代表这是本模块认为稳妥的默认状态**。

---

## 7. 风险与兜底

- 所有新增写入路径（`world_writer.py`、`experience_writer.py`）均遵循项目现有的"失败不阻断主流程"风格（try/except 吞掉异常），因为它们都挂在巩固循环 / `outcome_tracker.tick()` 这些非关键路径上——已在代码中落实（`consolidate_pending()` 内部逐条候选 try/except，`_write_eval_success_experience()` 整体 try/except）。
- compact 提示词扩展后确实会增加输出 token（多两个结构化字段），`history/world_extraction.py::parse_world_response()` 与 `history/compression.py` 的接入点均做了防御性解析：JSON 解析失败、非 list、非 dict 都直接返回空结果，不影响 `compact_summary`/`decisions` 已经解析出的部分。
- `source_kind` 字段已在 P0 阶段随 P1 一起落地，并额外修复了 `wiki/decision_writer.py` 更新分支此前会丢失该字段（及其它 extra frontmatter）的既有 bug——这是本次实施中发现的、不在原计划范围内但必须一并修复的问题，否则 P0 的统计口径会被"更新过的决策页"污染。
- P4 的每日快照/A/B 对比记录写入失败（磁盘满、权限问题等）均静默降级（`record_daily_snapshot`/`record_search_comparison` 内部 try/except），不影响 `consolidate()` 主流程或 `/wiki search` 本身的检索结果——观测记录丢一天不算大事，但检索检索不可用就是真问题，优先级顺序不能反。
- P4 三项标准里的"连续达标"判断严格按自然日计算，长期不运行 `consolidate()`（比如项目被搁置几周）会导致连续计数清零重新累积——这是有意为之：转正标准考察的是"持续稳定"，不应该被长期空窗期后偶然的一天达标绕过去。
- **P4 §6.5 记录的实际切换是在零真实观测数据的情况下按要求执行的，`library_wiki_search_primary` 默认值 `True` 本身就是这次偏离的直接产物**——虽然退化路径保证了"拿不到 wiki 结果就退回旧行为"的技术安全性，但不代表这条新路径本身已经被证明和旧路径一样可靠。生产环境使用前建议先跑一段时间攒够 `/wiki promotion` 的观测样本，或者直接把默认值改回 `False` 手动控制。

---

## 8. 验收记录（改进前后对比）

基线为全新临时项目目录（无历史 wiki 数据）上跑的端到端验证脚本结果，用于证明链路本身工作正常；**不是**对某个真实长期运行项目的改进前后对比——后者需要在实际接入后运行一段时间，按 §1 表格里的执行状态持续观测并回填下表。

| 指标 | 改进前基线 | 改进后（本次验证） |
|---|---|---|
| entity 页面 entity_type 分布 | 仅 module/bug_pattern（推断自代码路径，未跑真实数据） | 验证脚本中新增 `project` 类型 entity 页面 1 篇 |
| source_kind=world_model 页面数 | 0（字段本次新增，历史页面均无此字段） | 验证脚本中 1（entity_created）+ 事实合并进同一页面 |
| source_kind=experience_success 页面数 | 0 | 验证脚本中 1 |
| source_kind=experience_session_reflection 页面数 | 0 | 验证脚本中 1 |
| experiences/ 非空页面数 | 0 | 2（两条路径各产生 1 篇，仅验证脚本产生，真实环境需接入后持续观测） |
| topic 页面生成数（含语义聚类路径） | — | 单测场景中：规则路径 1 篇 + LLM 聚类路径 1 篇（`test_consolidate_topics_merges_rule_and_llm_pools`），两者互不覆盖对方页面 |
| wiki 转正评估三项标准的可计算性 | — | 单测场景中验证三项标准均可从日志正确算出：连续 14 天占比达标、连续 7 天校验无错误、A/B 样本不足 20 条不下结论、samples 充足后按累计命中率判定（`test_wiki_promotion.py`，13 项全部通过）；`overall_ready` 三项同时满足场景已覆盖 |
| context_builder 实际切换（P4 §6.5，零真实数据下执行） | — | `test_context_builder_wiki_search_primary.py`（6 项全部通过）：wiki 命中时采用其结果且跳过 shelf_search、无 answer 时退化到页面正文、未命中/异常/开关关闭时退回原链路且行为与切换前一致、无 llm_call 时仍以 `llm_call=None` 调用 wiki_search；既有 `test_context_builder_global_knowledge.py`（多项）/`test_context_builder_workdir_knowledge.py`（多项）共 28 项全部保持通过，无接口层面回归 |
| wiki_shelf_search 命中率（对比 shelf_search，真实分布） | — | 尚无真实样本（`/wiki search` 已接入自动采样，需接入真实项目运行后累积） |
| 既有单测回归（定向） | — | `test_outcome_tracker.py`、`test_correction_detector.py`、`test_format_correction_detector.py`、`test_session.py`、`test_session_end_reflection.py`、`test_session_end_workdir_knowledge.py`、`test_evolution_agent_profile.py`、`test_selective_compression.py` 等全部通过；新增 `test_wiki_topics_llm_cluster.py`（13 项）、`test_wiki_promotion.py`（13 项）全部通过 |
| 既有单测回归（全量对比） | 138 failed / 1766 passed / 12 errors（原始未修改代码，沙盒缺部分可选依赖导致的预先失败） | 138 failed / 1766 passed / 12 errors（逐条比对失败用例集合相同，无新增/无意外修复） |

> 待办：接入真实项目运行后，替换上表为真实分布数据；P4 的三项标准需要真实累积至少 2 周的每日快照与 20+ 条检索 A/B 样本才能产出有意义的 `overall_ready` 结论。**由于 §6.5 记录的实际切换发生在数据积累之前，强烈建议接入真实项目后优先跑 `/wiki promotion` 回头验证这次切换是否站得住脚**，不达标就把 `library_wiki_search_primary` 设为 `False`。
