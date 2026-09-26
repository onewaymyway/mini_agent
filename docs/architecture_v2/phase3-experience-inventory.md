# Phase 3 现状盘点：经验类数据结构 → Experience 映射表

> 对应 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
> "现状盘点"要求的产出。目的：在做 `experience/recorder.py` /
> `experience/store.py` 扎实化之前，先摸清代码库里已经存在多少种
> "经验类"数据结构，判断哪些该被 Adapter 接进 Experience、哪些该原样
> 保留，避免重复定义或误删已有能力。
>
> 本文档只做**盘点 + 迁移优先级建议**，不代表任何一种旧结构已经接入
> `core/experience.py`——接入是 Sprint 3-1/3-2 的任务，接入完成后需要
> 在 `MIGRATION_STATUS.md` 补一行。

## 一、当前 `core/experience.py::Experience` 的字段现状

Sprint 1/2 阶段的 `Experience` 只有 5 个字段：`source` / `goal_text` /
`status` / `rounds_used` / `final_report` / `created_at`，是"够用即可"的
quick-win 版本，**远未覆盖**原方案 §7 定义的完整 yaml 结构（`context /
state_before / goal / action / reason / prediction / outcome /
state_after / evidence / lesson / causal_hypothesis / confidence`）。
Sprint 3-1 的"字段完整覆盖原文 §7 yaml 结构"验收标准，本质上是在问：
下面盘点到的每一种旧结构，各自能为 §7 的哪些字段提供数据来源。

## 二、扫描范围与方法

按 Phase 3 计划文档现状盘点小节列出的三个方向，逐一扩展进四类实际存在
的经验类结构（`grep`/`view` 实际源码确认，不凭空假设）：

1. `history_manager.py`（对话/操作历史）
2. `entry_type="lesson"` 的 `MemoryEntry`（分布在 `agent/reflection.py`、
   `agent/reminders_correction.py`、`evolution/outcome_tracker.py`、
   `evolution/failure_pattern_store.py` 等至少 4 个写入点，是原计划
   文档"evolution/ 下的 lesson/pattern 相关文件"这一条实际对应的真实
   载体——**当前代码库里没有独立的 `Lesson` 类，"lesson" 是 `MemoryEntry`
   的一个 `entry_type` 取值**，这是本次盘点与原计划文字表述的一处
   出入，需要显式记录）
3. `wiki/experience_writer.py`（`wiki/experiences/*.md`，人类可读的正面
   经验页面）
4. `evolution/failure_pattern_store.py::FailurePattern` /
   `evolution/decision_recall.py::DecisionRecallResult` /
   `evolution/decision_profile_builder.py::ValuePattern`（三种已结构化
   的"聚合/召回"类数据，原计划文档未点名，但属于广义"经验类结构"，
   本次盘点一并纳入，避免遗漏）

## 三、盘点结果

### 1. `history_manager.py`：对话/操作历史

- 现状：见 `docs/architecture_v2/phase2-event-inventory.md` 第一节
  （Phase 2 盘点已覆盖过一次），本身没有"经验"语义（没有
  outcome/lesson 这类判断性字段），是原始事实记录。
- 对 Experience 的定位：**不是独立的经验来源，是 `context` 字段的
  素材**——一次 Goal 执行时"当时对话历史是什么样"可以作为
  `Experience.context` 的一部分，但 `history_manager.py` 本身不需要
  被"迁移"成 Experience，只需要在 Sprint 3-1 实现 `context` 字段时，
  从这里取一份摘要塞进去。
- 迁移优先级：不适用（不是迁移对象，是数据源）。

### 2. `entry_type="lesson"` 的 `MemoryEntry`：结构化经验教训

- 现状：分布式写入点（至少 4 处：`agent/reflection.py:273`、
  `agent/reminders_correction.py:212/249/350`、
  `evolution/outcome_tracker.py:222`、
  `evolution/failure_pattern_store.py:277`），全部复用同一个
  `MemoryEntry` 数据类（`entry_type`/`source`/内容文本等公共字段），
  没有独立的 `Lesson` dataclass。`goal_mode/runner.py::_write_failure_lesson()`
  （Phase 1 已确认存在，见 `MIGRATION_STATUS.md`）是 Goal 执行失败时
  写入 `entry_type="lesson"` 的入口之一，与 Phase 2 的
  `ExperienceCreated` 事件同属"Goal 执行结束"这个时间点，天然适合作为
  Sprint 3-1 的第一条 Adapter 接入链路。
- 对 Experience 的定位：字段语义上与 §7 的 `lesson`（教训文本）+
  `causal_hypothesis`（为什么失败，`failure_pattern_store.py` 的
  `root_cause_tag` 规则匹配已经提供了一份粗粒度分类）高度重合，是四类
  盘点对象里**唯一同时具备"失败原因 + 教训文本"两个字段雏形**的结构。
- 迁移优先级：**最高**，与计划文档"建议选 lesson"的判断一致
  （计划文档写作时预设"lesson 是独立结构"，本次盘点澄清了它实际是
  `MemoryEntry` 的一个 `entry_type`，Adapter 需要按
  `entry_type == "lesson"` 过滤，而不是按类名区分，这是对原计划的
  一处必要澄清，不改变"选 lesson 作为第一条迁移链"的结论本身）。

### 3. `wiki/experience_writer.py`：正面经验 wiki 页面

- 现状：`write_experience()` 接受 `trigger`/`approach`/`outcome`/
  `reusable`/`related_entities`/`confidence` 等参数，直接落盘成
  `wiki/experiences/*.md` 页面（`source_kind="experience_success"`），
  是**只记录正面案例**的路径（与"lesson"路径对称但只覆盖失败/负面
  一侧形成互补，计划文档"作为人类可读表达层，保留"的判断准确）。
  字段上 `trigger` 对应 §7 的 `state_before`/`context`，`approach`
  对应 `action`，`outcome` 对应 `outcome`，`confidence` 字段名与 §7
  完全同名，是四类里**字段名与 §7 对齐度最高**的一个。
- 对 Experience 的定位：**保留为人类可读表达层，不做底层数据迁移**，
  与计划文档判断一致；但 Sprint 3-1 实现 `Experience.evidence`/
  `confidence` 字段时，可以直接参考本模块的字段命名和取值范围
  （`confidence: float`，`reusable: bool`），保持两边语义一致，避免
  未来出现"同一个概念在新旧两套系统里数值范围不一样"的隐性不一致。
- 迁移优先级：低（保留原样，只做字段命名对齐参考，不接 Adapter）。

### 4. 三类已结构化的聚合/召回数据（原计划未点名，本次盘点补充）

- `evolution/failure_pattern_store.py::FailurePattern` /
  `FailurePatternAggregationSummary`：按 `task_category` 聚合的失败
  次数 + `root_cause_tag` 分布，是**统计聚合层**，不是单次 Experience
  记录本身，天然对应 Sprint 3-2 的 `experience/patterns.py`（Analyzer
  雏形）要做的事情——"对同类失败的 Experience 做简单聚合统计"，
  `failure_pattern_store.py` 现有的聚类规则（标题归一化 task_category
  + 正则 root_cause 分类）可以直接作为 Analyzer 雏形的参考实现，
  不需要从零设计聚合算法。
- `evolution/decision_recall.py::DecisionRecallResult`：决策召回结果
  （settled/overturned 两类），是**检索结果的包装类**，不是存储结构
  本身，语义上更接近 Sprint 3-2 `experience/retrieval.py` 要产出的
  "检索结果如何注入 prompt"这一层，检索通路本身复用
  `wiki/search.py::wiki_shelf_search()`，说明这套仓库里已经有一套
  "规则粗筛 → 图扩展 → 可选 LLM 精排"的三段式检索基础设施可以复用，
  Sprint 3-2 的 Experience Retriever 不必重新发明检索算法。
- `evolution/decision_profile_builder.py::ValuePattern`：价值观/决策
  倾向画像，与"一次 Goal 执行的经验"语义距离较远（是跨多次决策的
  归纳画像，不是单次记录），本次盘点判断**不属于 Experience 迁移范围**，
  仅记录在案，避免后续误判"漏迁移了"。

## 四、结论与后续行动（迁移优先级表）

| 现有经验类结构 | 对应 §7 Experience 字段 | 迁移建议 | 优先级 |
|---|---|---|---|
| `history_manager.py` | `context`（素材来源，非迁移对象） | 不迁移，作为字段数据源 | 不适用 |
| `entry_type="lesson"` 的 `MemoryEntry`（4+ 写入点） | `lesson` / `causal_hypothesis` | **Sprint 3-1 第一条 Adapter 接入链路** | 高 |
| `wiki/experience_writer.py` | `state_before`/`action`/`outcome`/`confidence`（命名对齐参考） | 保留，不迁移，仅供字段命名参考 | 低 |
| `evolution/failure_pattern_store.py`（`FailurePattern`） | 无直接对应字段，是聚合统计层 | 作为 Sprint 3-2 Analyzer 雏形的参考实现 | 中（Sprint 3-2） |
| `evolution/decision_recall.py`（`DecisionRecallResult`） | 无直接对应字段，是检索结果包装层 | 作为 Sprint 3-2 Retriever 的检索通路参考（复用 `wiki_shelf_search`） | 中（Sprint 3-2） |
| `evolution/decision_profile_builder.py`（`ValuePattern`） | 不对应任何单次 Experience 字段 | 不属于本次迁移范围 | 不适用 |

- 本文档完成了 `04-phase3-experience-layer-sprint-plan.md` 现状盘点小节
  要求的产出，**不代表以上结构已经接入 Adapter**——是否接入、何时接入
  是 Sprint 3-1/3-2 的独立任务，接入时需要重新走一遍
  `12-execution-and-doc-sync-norms.md` 的文档同步规范（更新本文档的
  "迁移建议"列 + `MIGRATION_STATUS.md` 新增行）。
- 对计划文档原文的一处澄清（不算变更，理由同 Phase 2 盘点时的先例）：
  计划文档"evolution/ 下的 lesson/pattern 相关文件"这句表述预设了
  "lesson"是一个独立类，本次盘点确认它实际是 `MemoryEntry` 的一个
  `entry_type` 取值，分布在 4 个以上写入点，Adapter 设计时需要按
  `entry_type == "lesson"` 过滤而不是按类名导入；这是对"现状盘点"
  环节的正常执行结果，不改变"选 lesson 作为第一条迁移链"的结论，
  不需要走 `12-execution-and-doc-sync-norms.md` 第四节的变更记录流程。
