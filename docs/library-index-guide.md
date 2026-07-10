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

**新分类节点只在 Phase G 巡检时批量诞生**（`grow_from_candidates`）：未分类
候选按关键词重合度做简单聚类，某个簇积累到 `min_cluster_size`（默认 5）条
才新建一个节点，这对应图书馆"新学科出现才增设类目"的稳态性——避免分类树
被单条易变的记忆污染成一堆几乎重复的碎类目。

### 2. 实体目录（著者目录）—— `perception/entity_index.py`

分类目录回答"这类问题有哪些"，实体目录回答"关于 `daemon.py` 这个具体
模块，历史上有哪些记忆，当前共识是什么"。

- 新记忆写入时，从文本里启发式猜测实体名候选（模块名 `xxx.py`、长度 ≥4
  的标识符），命中已有实体（含别名）就挂上去，都不命中才新建实体卡片。
- **摘要不是每次写入都重写**：只递增 `pending_evidence_count`，真正的
  `summary` 重写只在 Phase G 巡检、且该实体积累证据数达到阈值（默认 3）
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
| `consolidate(store, llm_call=None)` | Phase G 巡检 | 批量生长分类树 + 批量重写实体摘要 |

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

## 三、Phase G 知识巩固

`evolution/phase_g.py` 的 `run_phase_g()` 新增了一步（8.6），在剪枝候选、
能力地图、晋升候选之后运行：

```python
report.knowledge_consolidation = library.consolidate(
    memory_backend, llm_call=knowledge_llm_call,
    min_cluster_size=knowledge_min_cluster_size,   # 默认 5
    summary_threshold=knowledge_summary_threshold, # 默认 3
)
# → {"new_categories": int, "remaining_unclassified": int, "entities_summarized": int}
```

`/evolve phase-g` CLI 命令会在报告里打印这三个数字。只对本地 `MemoryStore`
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
  `agent.py:_maybe_run_phase_g`（SessionEnd 自动触发）、
  `cli/commands/evolve.py:_handle_phase_g`（手动 `/evolve phase-g`）。

如果某个 backend 不需要 LLM 兜底（比如纯离线场景），保持默认
`_llm_classify_call=None` 即可，所有涉及 LLM 的路径都有非 LLM 退化实现。

## 五、开关与配置项

`config/models.py` 的 `MemoryConfig` 新增：

```python
library_index_enabled: bool = True         # 总开关：分类树/实体目录/两步检索全部
library_shelf_search_enabled: bool = True  # 只关闭"两步检索"，保留写入侧的分类/实体挂载
```

关闭 `library_index_enabled` 后，`MemoryStore` 的行为与改造前完全一致
（`library_index=None`，`add()`/`search()` 走原逻辑）。

## 六、新增的落盘文件（均可重建，非 StateRepo 管辖）

在 `.agent/`（project scope）和 `~/.agent/`（global scope）下各自独立一套：

| 文件 | 内容 |
|---|---|
| `classification_tree.json` | 分类节点表 |
| `unclassified_candidates.jsonl` | 待归类候选队列 |
| `entities.json` | 实体卡片（含滚动摘要） |
| `category_catalog.json` | 分类号 → entry_id 指针索引 |
| `knowledge_timeline.jsonl` | 知识生命周期事件流 |

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
  `set_llm_classify_call()`、`build_llm_call()`
- `context_builder.py` — 检索优先走 `shelf_search`，失败/不足回退原逻辑
- `evolution/phase_g.py` — 新增知识巩固步骤（8.6）
- `cli/commands/evolve.py` — `/evolve phase-g` 报告展示巩固统计
- `agent.py` — 接入 LLM 客户端到分类兜底 + Phase G 知识巩固
- `storage/paths.py` — 新增 5 个路径属性
- `config/models.py` — 新增 2 个开关

## 相关文档

- [记忆管理指南](./memory-management-guide.md) — `MemoryStore`/lesson/衰减机制的原有设计
- [自我演化 Stage 4-5 指南](./self-evolution-stage4-5-guide.md) — Phase G 后台循环的其它环节（剪枝/晋升）

---

*首次编写：2026-07（图书馆式索引：分类树自动生长 + 实体目录 + 两步检索 + Phase G 知识巩固）*
