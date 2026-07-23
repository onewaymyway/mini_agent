# 图书馆式知识索引指南

对应改造背景：`docs/memory-management-guide.md` 描述的 `MemoryStore` 原本是
"一锅平的文本"——检索靠 TF-IDF 全表扫描，没有分类、没有实体、没有时间线。
本次改造在**不改变 `memory.jsonl` 存储格式、不改变 `MemoryBackend` 接口**
的前提下，加了一层图书馆式的结构化索引：先分类上架，再定位书架检索，
而不是对全部记忆做暴力关键词匹配。

## 一、核心概念

### 1. 分类树（书架）—— `perception/classification.py`

一棵完全由系统运行时自动归纳生长的分类树，冷启动只有一个根节点
`000 未分类`。所有子节点都是运行时长出来的，代码里不预置任何人工分类表。

写入一条新记忆时的分类流程（`ClassificationTree.classify()`）：

```
第一步：规则匹配（关键词打分，命中已有节点关键词 ≥ 2 个即算命中）
    │ 未命中
    ▼
第二步：LLM 兜底（只能从现有节点里选一个最接近的，或回答 NONE；
       不能凭一次判断新建节点，避免相似说法反复调用制造大量重复节点）
    │ 仍未命中
    ▼
记为"未分类候选"，写入 unclassified_candidates.jsonl，
临时挂在 000 下（检索时仍可通过全库回退命中，不会丢失）
```

**新分类节点只在 巩固循环 巡检时批量诞生**（`grow_from_candidates`）：未分类
候选按关键词重合度做简单聚类，某个簇积累到 `min_cluster_size`（默认 5）条
才新建一个节点，这对应图书馆"新学科出现才增设类目"的稳态性——避免分类树
被单条易变的记忆污染成一堆几乎重复的碎类目。

### 2. 实体目录（著者目录）—— `perception/entity_index.py`

分类目录回答"这类问题有哪些"，实体目录回答"关于 `daemon.py` 这个具体
模块，历史上有哪些记忆，当前共识是什么"。

- 新记忆写入时，从文本里启发式猜测实体名候选（模块名 `xxx.py`、长度 ≥4
  的标识符），命中已有实体（含别名）就挂上去，都不命中才新建实体卡片。
- **摘要不是每次写入都重写**：只递增 `pending_evidence_count`，真正的
  `summary` 重写只在 巩固循环 巡检、且该实体积累证据数达到阈值（默认 3）
  时才批量发生（`rewrite_summary`），避免高频 lesson 反复触发重写造成
  抖动和不必要的 LLM 调用。
- 没有 LLM 时会退化为"取最近 3 条证据拼接"的朴素摘要，保证零依赖也能跑。

### 3. 分类目录 + 知识编年目录 —— `perception/catalog.py`

- `CategoryCatalog`：分类号 → entry_id 列表的**指针索引**，权威数据始终是
  `memory.jsonl`；这个文件随时可以从权威数据全量 `rebuild()`，不是新的
  真相来源。
- `knowledge_timeline.jsonl`：记录知识生命周期事件（`created` /
  `new_category` / 未来可扩展 `merged` / `superseded`），与
  `workdir_knowledge.py` 里 W2 的 session 活动时间线是不同维度——那个记的是
  "session 做了什么"，这个记的是"某条知识经历了什么"。

### 4. 外观类 `LibraryIndex` —— `perception/library_index.py`

把以上三者串起来，对外只暴露三个方法：

| 方法 | 调用时机 | 作用 |
|---|---|---|
| `on_new_entry(entry, llm_call=None)` | `MemoryStore.add()` 内部自动调用 | 分类 → 挂实体 → 更新目录 → 记编年事件 |
| `shelf_search(store, query, k, llm_call=None)` | `context_builder.py` 检索时 | 两步检索：先定位书架，架内候选太少才回退全库 |
| `consolidate(store, llm_call=None)` | 巩固循环 巡检 | 批量生长分类树 + 批量重写实体摘要 |

> **`wiki_paths` 与 `wiki_search()`**：`LibraryIndex.__init__` 另有一个可选参数 `wiki_paths: Optional[AgentPaths]`（默认 `None`），开启后 `on_new_entry`/`consolidate` 会把实体镜像进一套平行的 wiki 式知识库（md 页面 + 显式关系图），并额外暴露 `wiki_search(query, k, llm_call, tags)`——三段式检索（规则粗筛→图扩展→LLM精排）的入口，与本文档描述的两步检索完全并存、互不替换。详见 [Wiki 式知识库指南](./wiki-knowledge-base-guide.md)。

## 二、两步检索是如何工作的

原来的 `context_builder.py` 每轮对话都对 `MemoryStore` 里**全部条目**做一次
TF-IDF 打分。现在优先走：

```
query
  │
  ▼
ClassificationTree.classify_by_rule(query) → 分类号
  │ 未命中且有 llm_call
  ▼
ClassificationTree.classify_by_llm(query, llm_call) → 分类号
  │
  ▼
tree.related_codes(code)  # 自身 + "参见"分类，做架内扩展
  │
  ▼
从 CategoryCatalog 里取这些分类号下的 entry_id 集合
  │ 候选数 < 3（_MIN_SHELF_SIZE）
  ├─────────────→ 回退：store.search(query, k)（原有全库 TF-IDF）
  │ 候选数充足
  ▼
store.rank_subset(query, 候选集合, k)  # 只在候选集合内做 TF-IDF+时间衰减精排
```

`rank_subset` 与原 `search()` 的算法完全一致（同样的分词、同样的 TF-IDF、
同样调用 `evolution/memory_aging.py` 的衰减因子），唯一区别是候选集合从
"全部条目"换成"书架圈定的子集"，且 IDF 统计也只在子集范围内计算——书架内的
相关性排序不受书架外文档干扰。

**任何情况下都不会因为分类失误而查不到东西**：书架候选不足时无条件回退全库
检索；`context_builder.py` 里 `shelf_search` 抛异常也会被捕获并回退到原有
的 `merge_search`。

## 三、巩固循环 知识巩固

`evolution/consolidation.py` 的 `run_consolidation()` 新增了一步（8.6），在剪枝候选、
能力地图、晋升候选之后运行：

```python
report.knowledge_consolidation = library.consolidate(
    memory_backend, llm_call=knowledge_llm_call,
    min_cluster_size=knowledge_min_cluster_size,   # 默认 5
    summary_threshold=knowledge_summary_threshold, # 默认 3
)
# → {"new_categories": int, "remaining_unclassified": int, "entities_summarized": int}
```

`/evolve consolidate` CLI 命令会在报告里打印这三个数字。只对本地 `MemoryStore`
且带 `library_index` 的 backend 生效；其它 backend（未来的 Chroma/Redis 等）
尚未接入，会被静默跳过。

## 四、LLM 接入方式

分类兜底（`classify_by_llm`）和实体摘要重写（`rewrite_summary`）都需要一个
`llm_call: Callable[[str], str]`。**不需要单独配置一个新的 LLM provider**，
直接复用 `Agent` 当前正在用的 `LLMClientPool`：

- `perception/memory_factory.py` 提供 `build_llm_call(client)`，把任意
  `LLMClient` 包一层单轮无工具 `chat()` 调用，失败时返回空字符串（调用方
  已经把"LLM 不可用"当作一等公民处理：规则兜底 / 朴素拼接摘要）。
- `agent.py` 在创建 memory backend 之后，用
  `lambda prompt: build_llm_call(self._client_pool.current_client)(prompt)`
  把当前 `client_pool` 接上去——每次调用都动态取 `current_client`，天然跟随
  `client_pool` 的故障转移/模型切换。
- 三处调用点都已接好：`agent.py` 构造时（新增记忆时的分类兜底）、
  `agent.py:_maybe_run_consolidation`（SessionEnd 自动触发）、
  `cli/commands/evolve.py:_handle_consolidation`（手动 `/evolve consolidate`）。

如果某个 backend 不需要 LLM 兜底（比如纯离线场景），保持默认
`_llm_classify_call=None` 即可，所有涉及 LLM 的路径都有非 LLM 退化实现。

## 五、开关与配置项

`config/models.py` 的 `MemoryConfig` 新增：

```python
library_index_enabled: bool = True         # 总开关：分类树/实体目录/两步检索全部
library_shelf_search_enabled: bool = True  # 只关闭"两步检索"，保留写入侧的分类/实体挂载
library_index_user_scoped: bool = False    # 改进7：多用户场景下按 user_id 拆分独立书架（默认关闭，共享归并）
per_turn_retrieval_enabled: bool = True    # 每轮自动检索总闸：关闭后 refresh_turn_context() 直接跳过
                                            # wiki_search/shelf_search/merge_search，本轮不产生任何
                                            # 检索开销、不注入 "## Relevant past experience"；记忆写入、
                                            # lesson/纠正检测、consolidation 等不受影响
```

关闭 `library_index_enabled` 后，`MemoryStore` 的行为与改造前完全一致
（`library_index=None`，`add()`/`search()` 走原逻辑）。

关闭 `per_turn_retrieval_enabled` 则更彻底：处理用户输入前不再自动检索任何
记忆/wiki 文档（`library_wiki_search_primary`/`library_shelf_search_enabled`
这两个开关此时不再生效，因为检索入口本身被跳过了）。这是当前唯一能完全
关闭"每轮自动检索"这一行为、同时保留记忆写入与其他功能的开关。

## 六、新增的落盘文件（均可重建，非 StateRepo 管辖）

在 `.agent/`（project scope）和 `~/.agent/`（global scope）下各自独立一套：

| 文件 | 内容 |
|---|---|
| `classification_tree.json` | 分类节点表（含 `feedback_score`/`merged_into`） |
| `unclassified_candidates.jsonl` | 待归类候选队列 |
| `entities.json` | 实体卡片（含滚动摘要、`superseded_notes`） |
| `category_catalog.json` | 分类号 → entry_id 指针索引 |
| `knowledge_timeline.jsonl` | 知识生命周期事件流 |
| `knowledge_timeline_index.json` | 改进6：实体/分类 → 行号的侧车索引 |

以上路径定义见 `storage/paths.py` 的 `workdir_classification_tree` /
`workdir_unclassified_candidates` / `workdir_entity_index` /
`workdir_category_catalog` / `workdir_knowledge_timeline`。

## 七、涉及改动的文件清单

新增：
- `perception/classification.py`
- `perception/entity_index.py`
- `perception/catalog.py`
- `perception/library_index.py`

修改：
- `perception/memory_store.py` — `MemoryEntry` 新增 `category`/`entity_ids`；
  `MemoryStore` 新增 `library_index`/`llm_classify_call` 注入、
  `rank_subset()`、`rewrite_categories()`、`library` 属性
- `perception/memory_factory.py` — 构建/注入 `LibraryIndex`，新增
  `set_llm_classify_call()`、`build_llm_call()`；`create_memory_backend()`/
  `create_both_memory_backends()`/`_create()` 新增可选 `user_id` 参数（改进7）
- `perception/classification.py` — 新增 `feedback_score`/`merged_into`
  字段、`record_feedback()`、`resolve_code()`、`merge_similar_nodes()`（改进2/4）
- `perception/entity_index.py` — 新增 `superseded_notes` 字段、冲突检测
  （`rewrite_summary` 内）、`consolidate_entities()`（改进1/3）
- `perception/catalog.py` — 新增 `redirect()`、时间线侧车索引与
  `load_timeline_for()`（改进2/6）
- `perception/library_index.py` — 新增 `record_retrieval_feedback()`、
  `mark_stale_from_correction()`、`timeline_for()`；`consolidate()` 串联
  分类合并/实体巩固（改进1-7 的组合外观）
- `context_builder.py` — 检索优先走 `shelf_search`，失败/不足回退原逻辑；
  新增 `last_injected_memory_ids` 追踪（改进5）
- `evolution/consolidation.py` — 新增知识巩固步骤（8.6）
- `cli/commands/evolve.py` — `/evolve consolidate` 报告展示巩固统计；新增
  `/evolve timeline` 命令（改进6）
- `agent.py` — 接入 LLM 客户端到分类兜底 + 巩固循环 知识巩固；
  `_detect_and_record_correction` 接入 `mark_stale_from_correction`（改进5）
- `storage/paths.py` — 新增 6 个路径属性（含 `knowledge_timeline_index`）
- `config/models.py` — 新增 3 个开关（含 `library_index_user_scoped`）

## 八、七个改进方向的具体实现

首版落地后梳理出的进一步改进，全部已实现，均只在 巩固循环 巡检或明确的
调用点触发，不影响写入侧的实时性能：

### 1. 冲突检测与知识版本化
`EntityStore.rewrite_summary()` 重写摘要时，会显式要求 LLM 判断新证据是否
推翻了旧摘要——推翻时摘要以 `⚠矛盾已更新：` 开头并说明原因，旧结论归档进
`Entity.superseded_notes`（保留最近 5 条），而不是把新旧结论并列堆砌。无
LLM 时退化为关键词启发式（`_looks_contradictory`：旧摘要不含"已修复/不再
需要"等否定词、新证据含有则判定为冲突）。

### 2. 分类树的合并（收敛）机制
`ClassificationTree.merge_similar_nodes(threshold=0.6)` 在 巩固循环 巡检时，
对同一父节点下的活跃节点两两计算关键词集合的 Jaccard 相似度，超过阈值即
合并：较早创建的节点保留为规范节点，较晚的标记 `deprecated` 并设置
`merged_into` 指向规范节点。`classify_by_rule`/`classify_by_llm`/
`related_codes` 全部通过 `resolve_code()` 自动跳过已合并的旧节点；
`CategoryCatalog.redirect()` 把旧分类号下的 entry_id 并入新分类号，
`LibraryIndex.consolidate()` 同时会把历史记忆的 `category` 字段更新到
规范分类号，避免旧记忆在合并后"查不到"。

### 3. 实体名抽取的巩固：去噪 + 近重复合并
`EntityStore.consolidate_entities()`（巩固循环 调用）：
- **去噪**：正则抽取难免抓到噪音，实体名过短或落在常见停用词表里的
  （`self`/`config`/`return` 等）直接标记 `deprecated`。
- **近重复合并**：用 `difflib.SequenceMatcher` 计算实体名相似度，
  高于阈值（默认 0.82）直接合并；相似度处于中间地带（0.5~0.82）且提供了
  `llm_call` 时，才兜底问一次 LLM"这两个名字是否指同一个实体"，避免对
  所有实体两两组合都调用 LLM。

### 4. 检索命中质量的自我反馈
`CategoryNode` 新增 `feedback_score`（范围 `[-0.5, 1.0]`），`classify_by_rule`
打分时按 `(1 + feedback_score)` 加权。`LibraryIndex.record_retrieval_feedback
(useful, category=None)` 供调用方在确认某次检索命中"有用/没用"后调用，
`category` 缺省时用 `shelf_search` 最近一次命中的分类号。当前唯一的自动
调用点是改进5的纠正闭环（命中记忆后来被纠正 → 记一次负反馈）；正向反馈
（"确实有用"）的自动信号源目前还没有可靠的判定依据，留了 API 但没有自动
触发，等后续有更明确的"这条记忆被证实有效"信号（比如某个 skill 验证通过）
时再接上。

### 5. 纠正 → 定位旧知识 → 标记过时的完整闭环
`ContextBuilder` 新增 `last_injected_memory_ids`，记录本 turn 实际注入到
system prompt 里的记忆 entry_id。`agent.py::_detect_and_record_correction`
检测到人类纠正、生成新 lesson 之后，会调用
`LibraryIndex.mark_stale_from_correction(store, injected_ids, correction_text)`：
对这些记忆所属的分类记一次负反馈（改进4）、对其关联实体调用
`mark_superseded()`（改进1），并写一条 `event_type="superseded"` 的编年
事件。这是一个保守假设（"最近被注入的记忆大概率跟这次纠正相关"），不做
精确因果判定，宁可多标一次。

### 6. 时间线的查询能力
`catalog.append_knowledge_event()` 新增 `index_path` 参数，维护一份
`knowledge_timeline_index.json`（实体/分类号 → 行号列表 + 行数计数器），
`catalog.load_timeline_for(entity_id=, category=)` 据此直接定位行号读取，
不必扫描整个 `knowledge_timeline.jsonl`。`LibraryIndex.timeline_for()` 封装
了这个接口，CLI 新增 `/evolve timeline --entity <id>|--category <code>
[--limit N]` 命令。

### 7. 多用户/多 Agent 场景下的书架隔离
默认关闭（`library_index_user_scoped=False`，同项目内所有用户共享同一套
书架，经验互相归并）。需要按用户拆分时，`create_memory_backend(cfg,
user_id=...)` / `create_both_memory_backends(cfg, user_id=...)` 会给分类树、
实体目录等索引文件名加上 `.{user_id}` 后缀，实现文件级的软隔离；`memory.jsonl`
本身不受影响（依然是同一份数据，只是索引结构按用户各自一份）。

## 相关文档

- [记忆管理指南](./memory-management-guide.md) — `MemoryStore`/lesson/衰减机制的原有设计
- [自我演化 Stage 4-5 指南](./self-evolution-stage4-5-guide.md) — 巩固循环 后台循环的其它环节（剪枝/晋升）
- [Wiki 式知识库指南](./wiki-knowledge-base-guide.md) — **新增**：本系统的平行新实现，用 md 页面 + 显式关系图代替分类树/滚动摘要，解决"关系表达能力不足"与"知识不可直接阅读"两个结构性局限；`LibraryIndex.on_new_entry()`/`consolidate()` 会把实体双写镜像进 wiki（`wiki_paths` 非 `None` 时），两套检索（`shelf_search` vs `wiki_search`）并存，尚未替换

---

*首次编写：2026-07（图书馆式索引：分类树自动生长 + 实体目录 + 两步检索 + 巩固循环 知识巩固）*
