# 四项优先改进指南

> 对应设计文档：`four_priority_improvements_design.md`。
> 本文档描述四项改进的**实际实现**、配置方式、行为边界与失败降级策略。
> 全部四项改进都遵循同一条原则：**默认关闭或默认等价于改造前行为，
> 任何内部异常都静默降级，不阻断 agent 主流程可用性**。

## 总览

| 方案 | 一句话 | 默认状态 | 核心模块 |
|------|--------|----------|----------|
| ① 记忆语义检索 | TF-IDF 精确匹配 + 本地离线 embedding 语义召回，混合排序 | **关闭**（`embedding_enabled=False`） | `perception/local_embedding.py`<br>`perception/hybrid_memory_backend.py` |
| ② 记忆巩固 | 淘汰旧 lesson 前先尝试归纳成一条抽象规律，而不是直接丢弃 | **开启**（`consolidation_enabled=True`） | `evolution/memory_consolidation.py` |
| ③ 自主探索好奇心评分 | 用"信息增益"补充"确定性问题紧急度"，让 agent 主动探索几乎未验证过的能力；探索结果无论成败都回写记忆 | 部分开启（novelty 信号默认参与排序，`novelty_weight=0.5`） | `evolution/soft_goal_deriver.py`<br>`perception/exploration_sandbox.py` |
| ④ Affordance 权重校准 | 用 `outcome_tracker` 的效果回填数据，周期性调整 AffordanceMap 三路来源的展示权重 | **开启**（Phase G 周期自动运行，失败即用默认权重） | `perception/affordance_calibration.py` |

---

## 方案一：记忆语义检索（混合 TF-IDF + 本地离线 Embedding）

### 问题

`MemoryStore` 原有检索完全基于 TF-IDF + n-gram 精确匹配，措辞不同但语义
相同的查询召回不到相关记忆（"数据库连接失败" vs "无法连上 DB"）。

### 实现

- `perception/local_embedding.py::LocalEmbeddingModel`：包装本地 ONNX
  Runtime 推理，不调用任何云端 API。内置候选模型：
  - `bge-small-zh-v1.5`（默认，中文场景，512 维）
  - `embedding-gemma-300m`（多语言，支持 MRL 截断到 128/256/512 维）
  - 也可以填一个本地路径作为自定义模型
  首次调用时若本地缓存目录（默认 `~/.agent/models/`）没有模型文件，会从
  Hugging Face 下载一次，之后离线复用。**模块顶层不 import 任何推理依赖
  （`onnxruntime`/`tokenizers`），只有真正调用 `embed()` 时才 import**，
  确保 `embedding_enabled=False` 时零依赖引入。

- `perception/hybrid_memory_backend.py::HybridMemoryBackend`：包装内部
  `MemoryStore`，`add`/`upsert`/`search_by_tag`/`delete_by_session`/`reload`
  全部委托给内部 store（行为完全不变）。`search()` 改为 TF-IDF 全量分数 +
  embedding 余弦相似度全量分数，各自 min-max 归一化后按权重合并。
  embedding 调用失败或未提供 `embed_call` 时自动退化为纯 TF-IDF 结果
  （与 `MemoryStore.search()` 逐条一致）。

- embedding 向量持久化：不引入外部向量数据库，在 `<memory_path>.embeddings.jsonl`
  影子文件里维护 `entry_id -> 向量` 映射，首次访问时懒加载，并为历史遗留
  的旧条目（写入时 embedding 未开启）补算向量。

- `perception/lesson_review.py::group_lessons()` 新增可选 `embed_call`
  参数：聚类判定从"关键词 Jaccard ≥ 阈值"扩展为"Jaccard ≥ 阈值 **或**
  embedding cosine similarity ≥ 0.75"（两路取并集，关键词路径作为兜底，
  防止语义聚类的假阳性合并两个不相关 lesson）。`embed_call=None`（默认）
  时完全退化为原有纯关键词行为。

### 配置

```json
{
  "memory": {
    "backend": "hybrid",
    "embedding_enabled": true,
    "embedding_model": "bge-small-zh-v1.5",
    "embedding_tfidf_weight": 0.5,
    "embedding_weight": 0.5
  }
}
```

`backend` 保持 `"local"`（默认值）时改动完全不生效；改成 `"hybrid"` 但
`embedding_enabled=False` 时，`memory_factory.py::_load_hybrid()` 会直接
返回内部 `MemoryStore`，等价于 `"local"`。

### 安装

embedding 功能需要额外依赖（`onnxruntime`、`tokenizers`、`huggingface_hub`、
`numpy`），未安装且开启开关时会捕获异常并记录 warning 日志，自动降级为
纯 TF-IDF，不阻断 agent 启动。

### 测试

`tests/test_hybrid_memory_backend.py`：
- `embed_call=None` 时结果与 `MemoryStore.search()` 逐条一致
- mock embedding 验证语义召回能找到 TF-IDF 召回不到的条目
- embedding 调用抛异常时自动降级

---

## 方案二：记忆巩固——从"淘汰"变成"归纳"

### 问题

`MemoryStore.add()` 超出 `max_entries` 时纯粹按 `created_at` 淘汰最旧的
一批条目，没有"多条具体经历 → 一条抽象规律"的归纳过程，类似的失败教训
会被反复遗忘又重新踩坑。

### 实现

`evolution/memory_consolidation.py::consolidate_before_eviction()`：

1. 只对 `entry_type == "lesson"` 的待淘汰条目做归纳（`summary` 型条目是
   session 记录，归纳会丢失时间线信息，继续走原有淘汰）。
2. 复用 `lesson_review.py::group_lessons()` 聚类，不重新实现相似度判断；
   可选传入 `embed_call` 让聚类也吃到方案一的语义相似度。
3. 聚类规模 `>= consolidation_min_group_size`（默认 3）才触发归纳，避免
   "仅两条偶然相似的经历"被过度抽象成误导性规则。
4. 归纳产物：
   - 有 `llm_call` 时：生成一条 LLM 摘要的抽象规律（`entry_type="consolidated_lesson"`）
   - 无 `llm_call` 时：降级为规则拼接——取聚类里 `occurrence_count` 最高
     的一条作为代表，其余条目的 `occurrence_count` 累加到它身上
   - `occurrence_count` 累加原有条目之和、`confidence` 取最高值、
     `source="consolidated"`
5. 未参与任何达标分组的 lesson、以及全程异常，都原样走物理淘汰
   （`([], entries_to_evict)`），保证归纳只是可选的增量步骤。

`memory_store.py::add()` 的淘汰逻辑改为：先调用
`consolidate_before_eviction()`，再用 `consolidated + keep` 替换原有
`self._entries`；任何异常都记录日志并退化为纯淘汰。

`evolution/memory_aging.py` 新增 `"consolidated"` source 的半衰期基准
（45 天，介于 `human_feedback`（90天）与 `self_reflection`（30天）之间——
归纳产物是"反复验证过的规律"，比单次自我反思更可靠，但不如人类直接反馈
权威），`compute_half_life_days()` 判断条件从 `entry_type == "lesson"`
扩展为 `entry_type in ("lesson", "consolidated_lesson")`。

### 配置

```json
{
  "memory": {
    "consolidation_enabled": true,
    "consolidation_min_group_size": 3
  }
}
```

关闭 `consolidation_enabled` 后 `MemoryStore.add()` 的淘汰行为与改造前
完全一致（逐条回归验证见测试）。

### 测试

`tests/test_memory_consolidation.py`：
- 5 条相似 lesson 触发淘汰后归纳成 1 条，`occurrence_count` 总和不丢失
- `llm_call=None` 走规则拼接降级路径
- 聚类规模不足时走原有物理淘汰
- `summary` 型条目永不参与归纳
- `consolidation_enabled=False` 时 `MemoryStore` 行为与改造前一致

---

## 方案三：自主探索——好奇心/新颖度评分 + 探索结果回写记忆

### 问题

`SoftGoalDeriver` 原有三路信号（capability_map 失败信号 / work_index /
lesson_review）全部是"确定性问题"驱动——已经出过错、有明确失败记录的领域
才会被关注。"几乎没试过、不确定能不能做好"的领域没有任何信号会主动去
探索，agent 对自己能力边界的认知只会越来越窄化到"已知的坑"。同时
`ExplorationSandbox` 跑完一次探索后，无论成功失败都没有回写记忆，
导致同样的探索性错误可能被重复"发现"。

### 实现

**好奇心评分**（`evolution/soft_goal_deriver.py`）：

- `_DeriveCandidate` 新增 `novelty: float = 0.0` 字段（默认 0，旧三路
  信号不产出 novelty，回归行为不变）。
- 新增第四路信号 `_from_unexplored_capabilities()`：扫描 `capability_map`
  中 `total_calls < exploration_min_calls_threshold`（默认 2）的能力条目，
  `novelty = 1 / (1 + total_calls)`（调用越少，novelty 越高）。
- `_recently_explored_domains()`：读取 `activity_digest.jsonl` 中最近
  `already_explored_cooldown_days`（默认 30 天）内的
  `type="exploration_result"` 记录，命中的领域 novelty 打 0.1 折，避免
  探索预算被重复消耗在同一领域。
- 排序键从纯 `urgency` 改为 `urgency + novelty_weight * novelty`
  （`novelty_weight` 默认 0.5，来自新增的 `AutonomyConfig`；设为 0 等价于
  改造前纯 urgency 排序）。

**探索结果回写记忆**（`perception/exploration_sandbox.py`）：

- `ExplorationSandbox` 构造函数新增可选 `memory_backend` 参数。
- `_record_exploration_outcome()`：探索完成后（无论 `success` 是
  `True` 还是 `False`）写入一条 `entry_type="lesson", source="exploration"`
  的 `MemoryEntry`。失败探索同样有价值——防止未来的 `SoftGoalDeriver`
  或 `skill_propose` 再次把同一条已验证不可行的路径列为候选。
- `make_exploration_sandbox()` 工厂函数新增 `memory_backend` 透传参数；
  `AutonomousLoop` 调用处best-effort 构造一个 memory backend 传入（构造
  失败时 `memory_backend=None`，探索流程本身不受影响）。

### 配置

```json
{
  "autonomy": {
    "novelty_weight": 0.5,
    "exploration_min_calls_threshold": 2,
    "already_explored_cooldown_days": 30.0
  }
}
```

### 测试

`tests/test_curiosity_scoring.py` / `tests/test_exploration_outcome_recording.py`：
- 旧三路信号 novelty 默认值为 0；`novelty_weight=0` 时排序结果与改造前
  完全一致
- `total_calls` 越少 novelty 越高
- 最近探索过的领域正确降权
- 探索成功/失败都生成 lesson memory 条目；`memory_backend=None` 时静默跳过

---

## 方案四：Affordance 排序权重的闭环校准

### 问题

`AffordanceAnalyzer.analyze()` 对 `known_issues`/`unexplored_areas`/
`high_risk_zones` 三路信号的排序权重是硬编码的 1:1:1，无法根据"这类提示
实际有没有用"自我调整。

### 实现

`perception/affordance_calibration.py`：

- `AffordanceWeights`（`known_issues_weight`/`unexplored_areas_weight`/
  `high_risk_zones_weight`，均默认 1.0，`clamp()` 到 `[0.3, 2.0]` 区间）。
- `calibrate(paths)`：读取 `outcome_tracker.get_all(paths)` 中
  `status == "resolved"` 的记录，用关键词启发式（`_classify_source()`）
  尝试关联到三路来源之一，按 `verdict`（`improved`/`worsened`）调整对应
  权重（学习率 0.1），持久化到
  `<project_root>/.agent/affordance_weights.json`。关联失败（都不像任何
  一路来源）的记录直接跳过，不影响其它来源权重。全程异常静默降级为
  `AffordanceWeights()` 默认值。
- `load_weights(paths)`：读取上次持久化的权重，文件不存在/异常返回默认值。

`perception/affordance_analyzer.py`：

- `AffordanceAnalyzer.analyze()` 新增可选 `weights: Optional[AffordanceWeights]`
  参数，为 `None` 时使用全 1.0 默认权重（回归保证）。
- `_rank_opportunities()` 用 `known_issues_weight` 缩放已知问题的排序
  分数，`unexplored_areas_weight` 决定"探索能力盲区"提示是否展示。
- `inject_affordance_map()` 调用处自动加载 `load_weights(paths)` 并传入。

`evolution/phase_g.py::run_phase_g()`：

- 在 `outcome_tracker.tick()` 之后新增一步 `calibrate(paths)` 调用，
  结果记录在 `PhaseGReport.affordance_weights_updated` 字段。失败静默
  降级，不阻断 Phase G 主流程其余步骤。

### 测试

`tests/test_affordance_calibration.py`：
- 关键词分类器正确识别三类文本
- `improved`/`worsened` 记录正确调整对应权重
- 权重被限制在 `[0.3, 2.0]`
- 关联失败的记录不影响权重
- `weights=None` 时 `AffordanceAnalyzer.analyze()` 结果与传入默认
  `AffordanceWeights()` 完全一致

---

## 回归保证一览

| 改动点 | 关闭/默认时的行为 |
|--------|-------------------|
| `backend="local"`（默认） | `memory_factory` 不构造 `HybridMemoryBackend` |
| `embedding_enabled=False`（默认） | `_load_hybrid()` 直接返回原始 `MemoryStore` |
| `embed_call=None` | `group_lessons()`/`HybridMemoryBackend.search()` 逐条结果与原实现一致 |
| `consolidation_enabled=False` | `MemoryStore.add()` 淘汰逻辑与改造前完全一致 |
| `novelty_weight=0` | `SoftGoalDeriver` 排序与改造前纯 `urgency` 排序一致 |
| `memory_backend=None` | `ExplorationSandbox` 探索流程不受影响，只是不回写记忆 |
| `weights=None` | `AffordanceAnalyzer.analyze()` 与传入默认 `AffordanceWeights()` 结果一致 |

以上七条均有对应单元测试覆盖（详见各方案"测试"小节），全量测试
`pytest tests/` 通过（新增 24 个用例，原有用例零回归）。

## 相关文档

- [配置系统指南](config-guide.md) — `MemoryConfig`/`AutonomyConfig` 完整字段说明
- [记忆管理指南](memory-management-guide.md) — Lesson Memory 与记忆检索基础机制
- [记忆与自我进化完整参考](memory-and-self-evolution-complete-reference.md) — 记忆/自我进化/具身智能的交叉耦合点
- [Phase G 指南](self-evolution-phase-g-guide.md) — Phase G 周期扫描的完整流程
- [效果回填追踪指南](self-evolution-outcome-tracking-guide.md) — `outcome_tracker` 的数据来源与判定逻辑
