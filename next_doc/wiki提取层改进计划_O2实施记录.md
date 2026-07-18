# wiki 提取层与组织层改进计划 · O2 实施记录

> 对应 `wiki知识库提取与组织层改进计划.md` §5（问题 O2：实体关系图过于
> 扁平），实施 §5.2.1（多跳衰减扩展）与 §5.2.2（接入 `search.py`）。
> 依赖 O1（已完成），按 §8 排期属于第三批。

## 1. 改动内容

### 1.1 §5.2.1 `GraphIndex` 多跳衰减扩展

`src/mini_agent/wiki/graph.py`：

- 原 `expand()`（一跳、返回 `set[str]`）**原样保留、改名为
  `expand_legacy()`**——行为、签名、返回值类型完全不变。
- 新增 `expand(page_ids, *, strong_only=False, max_hops=1, decay=0.5,
  max_candidates=None) -> dict[str, float]`：
  - 逐跳 BFS 扩展，第一跳权重为 `decay`，第二跳为 `decay**2`，以此类推。
  - 同一节点通过多条路径/多个跳数可达时**取最大权重**，不是累加——
    实现方式是每一跳只在 `hop_weight > weights.get(node, 0.0)` 时才更新，
    由于 `hop_weight` 随跳数单调递减，先到达（跳数更浅）的记录天然不会
    被后到达（跳数更深、权重更低）的记录覆盖。
  - `max_candidates` 不为 `None` 时按权重降序只保留前 N 个（计划
    §5.4 的硬上限要求，"内部需要设置一个硬上限"——实现为 `expand()`
    内部的一个可选截断步骤，由调用方决定是否传、传多少）。
  - `max_hops=1` 时候选集合与 `expand_legacy()` 完全一致（权重值本身是
    新增信息，`expand_legacy()` 不返回权重）。

### 1.2 §5.2.2 接入 `search.py`

`src/mini_agent/wiki/search.py::wiki_shelf_search`：

- 新增 `deep: Optional[bool] = None` 参数：
  - `None`（默认）：规则粗筛候选数 `len(rule_hit_ids) < rerank_top_n`
    时自动切到多跳扩展（`max_hops=2`），否则维持一跳——**默认行为、
    性能特征与本次改动前完全一致**（候选充足时走的仍是
    `expand_legacy()`）。
  - `True`：强制多跳（对应 `/wiki search --deep`）。
  - `False`：强制维持一跳，即使候选不足也不自动升级。
- 一跳路径继续用 `expand_legacy()`；多跳路径用新 `expand()`，
  `max_candidates=rerank_top_n * 3`（计划 §5.4 建议值）。
- `_llm_rerank()` 新增 `weights` 参数：多跳扩展带入的候选页面在喂给
  LLM 精排的 prompt 里标注 `graph_relation=indirect~<权重>`，并在
  prompt 说明文字里提示"权重越低说明关系跳数越深、越不直接，排序/引用
  时请酌情降低这类页面的优先级"——对应计划原文"把权重字段传给 LLM
  精排 prompt，作为候选页面排序的参考信息之一"。一跳权重（`1.0`，
  仅用于内部统一处理）不会被标注，不改变一跳模式下的 prompt 格式。
- `WikiSearchResult.stage_reached` 新增取值 `"graph_deep"`（未传
  `llm_call` 且实际走了多跳扩展时），区别于原有的 `"graph"`（一跳）。

### 1.3 CLI 接入

- `src/mini_agent/cli/commands/wiki.py`：`/wiki search <query> [--deep]`，
  `--deep` 可出现在 query 任意位置，解析后从参数里剥离，强制多跳检索；
  不传时维持原有的自动判断行为。
- `src/mini_agent/perception/library_index.py::LibraryIndex.wiki_search()`：
  新增 `deep: Optional[bool] = None` 透传参数，与 `confidence_weight`/
  `use_index` 同样的透传模式（`None` 时不传给 `wiki_shelf_search`，使用
  其自身默认值）。

## 2. 验收方式（对应原计划 §5.3）

- `tests/test_graph_expand.py`（10 项用例，全部通过）：
  - `expand()` 在三层依赖链（A→B→C）上：`max_hops=1` 候选集合与
    `expand_legacy()` 完全一致；`max_hops=2` 时 C 的权重（0.25）低于 B
    的权重（0.5）；`max_hops` 超过图实际深度时不报错、不产生幻觉节点。
  - 同一节点通过一跳与二跳两条不同路径可达时，取一跳的更高权重
    （0.5），不是累加、也不会被二跳的低权重覆盖。
  - 种子节点自身即使存在环也不会出现在扩展结果里。
  - `max_candidates` 硬上限：候选数超出时按权重截断到指定数量；不传
    时不截断。
  - `wiki_shelf_search`：三层依赖链页面场景下，`deep=True` 时两跳外的
    页面被检索到、`stage_reached="graph_deep"`；`deep=False` 时强制
    维持一跳、两跳外页面不出现、`stage_reached="graph"`。
  - 函数签名向后兼容性检查：`deep` 参数默认值为 `None`。
- 回归：`tests/test_context_builder_wiki_search_primary.py`、
  `tests/test_wiki_index_reuse.py`、`tests/test_wiki_promotion.py`、
  `tests/test_wiki_topics_llm_cluster.py`、`tests/test_entity_digest.py`、
  `tests/test_extraction_stats.py` 共 64 项既有用例全部保持通过。
  `tests/test_wiki_index_reuse.py::test_graph_from_dict_matches_build`
  原本调用的是旧 `expand()`（一跳、返回 `set`），本次同步改为调用
  `expand_legacy()`（保持原有校验意图：`build()` 与 `from_dict()`
  重建的图行为一致），并新增一条 `expand(max_hops=2)` 的等价性校验，
  扩大覆盖面而不是单纯改名迁移。

## 3. 与原计划的差异说明

- 计划原文 §5.2.2 提到"当规则粗筛候选数量过少，明显不足以覆盖
  `rerank_top_n` 时自动触发"——本次实现的自动判断条件是
  `len(rule_hit_ids) < rerank_top_n`（候选数严格小于目标数量），是这句话
  最直接的字面实现，没有引入额外的模糊阈值（比如"不足 80%"之类），
  保持判断逻辑简单可预测。
- `_llm_rerank()` 的权重标注格式（`graph_relation=indirect~<权重>`）是
  本次新设计的、没有先例的 prompt 片段格式，未来如果发现 LLM 精排对
  这个格式利用不佳，可以在不改动 `expand()`/`wiki_shelf_search()` 主体
  逻辑的前提下单独调整这一段文案。

## 4. 风险与兜底（延续原计划 §5.4）

- 硬上限截断已实现在 `expand()` 内部（`max_candidates` 参数），深度检索
  路径固定传入 `rerank_top_n * 3`，避免图密度高时多跳扩展候选数量爆炸
  拖慢 LLM 精排输入规模。
- 默认（`deep=None`）行为在候选充足时与改动前完全一致，只有候选明显不足
  时才会触发多跳路径，改动的性能面已被最小化。

## 5. 未在本次实施范围内的项

- O3（topic 再巩固）：按 §8 排期与 O2 同属第三批，依赖 O1，尚未实施。
- E2 方案 C 仍是"机制已就位、待观测期后人工执行"的状态（见 E1 实施
  记录 §5），本次未推进。
- O4（统一知识生命周期状态机）依赖第二、三批全部验证稳定，仍在第四批。
