# wiki 式知识库改进计划

> **执行状态（本次更新）**：P0（可观测性）、P1（世界模型抽取）、P2（经验页面落地，含自我进化正面判定与会话级正面反思两条路径）均已实现，通过端到端功能验证，并对全量测试套件做了修改前后逐条比对（无新增回归）；P3/P4 保留为设计，尚未实施。各节标题旁标注了 `[已实现]` / `[未实施]`。

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
| P3 检索与聚合优化 | topic 聚类降低门槛、命名实体识别增强 | 2-3 天 | P1 | 未实施 |
| P4 主索引切换评估 | 建立 wiki 替代旧图书馆模式的量化标准 | 1 天设计 + 持续观测 | P1-P3 稳定运行后 | 未实施 |

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

## 5. P3：检索与聚合优化（在 P1 数据量上来之后再做，避免过早优化）[未实施]

1. **topic 聚类降低门槛**：`wiki/topics.py::find_topic_candidates` 目前要求同 tag 下 ≥4 篇且强链接密度 ≥0.5，这个阈值只对"决策沿革链"这种本就强关联的场景友好。给 `consolidate_topics` 增加一条基于 `wiki/dedup.py` 里 embedding 可选路径的**语义聚类候选生成函数**（不改变现有 tag+密度路径，二者并存，两套候选池合并后去重），使得实体/事实类内容也有机会被聚合成专题页。
2. **命名实体识别增强**：`entity_index.py::guess_entity_names` 目前只能抓代码标识符（正则 `xxx.py` / 长度≥4 英文单词），抓不到人名、项目名、中文概念词。P1 的 LLM 结构化抽取已经承担了这部分能力，因此这里**不需要改正则本身**，只需要确保 `EntityCandidate.entity_type` 覆盖 `person/project/external_system` 等新分类，并在 `wiki/parser.py` 的 tag 体系里允许这些新 entity_type 值（当前无枚举限制，天然兼容，只需在文档/校验里显式承认）。

---

## 6. P4：wiki 转正为主索引的评估标准（暂不执行，先定指标）[未实施]

当前设计文档明确"过渡期双写、效果验证稳定前不下线旧图书馆模式"。建议提前定义"转正"的量化条件，避免长期停留在镜像层地位：

- 连续 2 周，`/wiki stats` 显示 `world_model` + `decision` + `experience` 三类来源合计占比 ≥ 50%（即不再是"错题本"）。
- `wiki_shelf_search`（三段式）与旧 `shelf_search`（分类树两步）做 A/B，wiki 侧 grounded 命中率不低于旧方案。
- `validator.py` 全量校验无 error 级别问题（死链/id 冲突）持续 1 周。

满足以上三条后，评估把 `library_index_enabled` 的默认检索路径切到 wiki_search，旧图书馆模式转为只读归档。

---

## 7. 风险与兜底

- 所有新增写入路径（`world_writer.py`、`experience_writer.py`）均遵循项目现有的"失败不阻断主流程"风格（try/except 吞掉异常），因为它们都挂在巩固循环 / `outcome_tracker.tick()` 这些非关键路径上——已在代码中落实（`consolidate_pending()` 内部逐条候选 try/except，`_write_eval_success_experience()` 整体 try/except）。
- compact 提示词扩展后确实会增加输出 token（多两个结构化字段），`history/world_extraction.py::parse_world_response()` 与 `history/compression.py` 的接入点均做了防御性解析：JSON 解析失败、非 list、非 dict 都直接返回空结果，不影响 `compact_summary`/`decisions` 已经解析出的部分。
- `source_kind` 字段已在 P0 阶段随 P1 一起落地，并额外修复了 `wiki/decision_writer.py` 更新分支此前会丢失该字段（及其它 extra frontmatter）的既有 bug——这是本次实施中发现的、不在原计划范围内但必须一并修复的问题，否则 P0 的统计口径会被"更新过的决策页"污染。

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
| topic 页面生成数（含语义聚类路径） | — | 未实施（P3） |
| wiki_shelf_search 命中率（对比 shelf_search） | — | 未实施（P4 前置） |
| 既有单测回归（定向） | — | `test_outcome_tracker.py`、`test_correction_detector.py`、`test_format_correction_detector.py`、`test_session.py`、`test_session_end_reflection.py`、`test_session_end_workdir_knowledge.py`、`test_evolution_agent_profile.py`、`test_selective_compression.py` 等全部通过 |
| 既有单测回归（全量对比） | 138 failed / 1766 passed / 12 errors（原始未修改代码，沙盒缺部分可选依赖导致的预先失败） | 138 failed / 1766 passed / 12 errors（逐条比对失败用例集合相同，无新增/无意外修复） |

> 待办：接入真实项目运行后，替换上表为真实分布数据，并把 P3、P4 排入下一轮迭代。
