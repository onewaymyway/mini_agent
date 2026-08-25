# wiki 提取层与组织层改进计划 · O1 实施记录

> 对应 `wiki-knowledge-base-extraction-and-organization-plan.md` §4（问题 O1：全量扫描架构没有
> 为增长设计分层），已实施 §4.2.1（复用 indexer 增量索引）与 §4.2.2
> （知识信度分层字段）。§4.2.3（分区组织）按原计划本就"不在当前阶段
> 实现，只记录演进路径"，本次同样未实现。

## 1. 改动内容

### 1.1 §4.2.1 复用 indexer 的增量索引作为 search.py 的数据源

- 新增 `src/mini_agent/wiki/index_reader.py`：
  - `IndexData`：从 `_index/` 下 `tags.json` / `search_index.json` /
    `graph.json` 加载出的只读检索数据源（token→ids 倒排、tag→ids、
    `GraphIndex`、`id→Path`）。
  - `load_index(paths)`：读时校验——比对 `_manifest.json` 记录的文件列表
    / mtime 与磁盘当前状态，**任何不一致**（新增/删除/修改过但索引还
    没重建）都判定为过期，返回 `None`；只有完全一致才返回可复用的
    `IndexData`。不负责重建索引（重建时机不变，仍由
    `evolution/consolidation.py` 驱动的 `indexer.py::build_index()`
    负责）。
  - `find_page_path(paths, page_id)`：按 id 定位文件路径，不解析内容。
- `src/mini_agent/wiki/graph.py`：新增 `GraphIndex.from_dict()`，从
  `graph.json` 结构重建图（`to_dict()` 的逆操作），不需要重新
  `parse_page` 全部页面来 `GraphIndex.build()`。
- `src/mini_agent/wiki/search.py::wiki_shelf_search()` 重构为两条路径：
  - **索引路径**（默认，`use_index=True` 且 `load_index()` 命中）：用
    倒排索引做候选粗筛，只对真正命中 token/tag 的页面调用 `parse_page`
    （而不是全库），图扩展阶段复用索引里的 `GraphIndex`，新引入的页面
    id 按需惰性 `parse_page` 单个文件。
  - **全量扫描路径**（索引缺失/过期或 `use_index=False`）：与改动前
    完全一致的实现，作为兜底 / 回归对比基线。
  - 两条路径共享同一套 `_rule_score` / `_rule_prefilter` / 图扩展 /
    LLM 精排逻辑，只有"如何拿到候选 `WikiPage` 对象"这一步不同，保证
    结果语义一致（见验收 §2）。

### 1.2 §4.2.2 知识信度分层字段

- `src/mini_agent/wiki/writer.py`：新增
  `increment_grounded_hit_count(paths, page)`，把 frontmatter 的
  `grounded_hit_count` 字段 +1，只更新这一个字段（不刷新 `updated`，
  不追加正文），原子写。
- `src/mini_agent/wiki/search.py::_rule_score()` 公式调整为：

  ```
  score = 0.6 * token_jaccard + 0.4 * tag_jaccard
          + confidence_weight * log(1 + grounded_hit_count)
  ```

  `confidence_weight` 默认 `0.1`，`confidence_weight=0` 时与改动前排序
  结果完全一致（单测覆盖，见 §2）。
- `src/mini_agent/context_builder.py::_try_inject_wiki_search()`：LLM
  精排返回 `grounded_page_ids` 后，异步（同步调用但异常吞掉、不阻塞
  返回）回写这些页面的 `grounded_hit_count`。回写失败不影响本轮检索
  结果返回。
- `src/mini_agent/config/models.py::MemoryConfig` 新增：
  - `wiki_index_reuse_enabled: bool = True` — 对应 `use_index`。
  - `wiki_confidence_weight: float = 0.1` — 对应 `confidence_weight`。
  - `src/mini_agent/perception/library_index.py::LibraryIndex.wiki_search()`
    新增同名透传参数，把这两个配置项从 `context_builder.py` 一路传到
    `wiki_shelf_search()`。

## 2. 验收方式（对应原计划 §4.3/§4.4）

- `tests/test_wiki_index_reuse.py`：
  - `GraphIndex.from_dict` 与 `build()` 对同一批页面产出的出边/一跳扩展
    结果一致。
  - `load_index()` 在没跑过 `build_index()` 时返回 `None`；跑过之后
    返回可用索引；索引构建后又新增页面时重新判定为过期（返回 `None`）。
  - 索引路径与全量扫描路径对同一 query 返回**相同的候选页面 id 集合**
    （行为不变性）。
  - `confidence_weight=0` 与改动前排序结果一致（回归保护）。
  - `increment_grounded_hit_count` 累加写入正确、不影响其它字段。
- `tests/test_context_builder_wiki_search_primary.py`：既有测试的
  `_FakeLibrary.wiki_search` 签名补充了 `confidence_weight`/`use_index`
  两个新参数（默认值），6 项既有用例全部保持通过——`ContextBuilder` 的
  行为不变性得到保留。
- 性能对比（原计划 §4.3 建议的"200+ 页面测试 wiki 目录"基准测试）本次
  **未包含**在改动范围内，建议后续跑一次真实规模基准，用
  `/wiki stats` 或简单计时脚本对比索引路径 vs 全量扫描路径的
  `wiki_shelf_search()` 单次调用耗时，作为该项改动生效与否的量化依据。

## 3. 风险与兜底（延续原计划 §4.4）

- 索引读取失败（文件损坏、schema 不匹配）在 `load_index()` 内部统一
  用 `_read_json` 的 `try/except` 吞掉，返回 `None`，静默降级为全量
  扫描，不影响检索可用性。
- `grounded_hit_count` 回写属于非关键路径，`context_builder.py` 里用
  `try/except` 包裹，写入失败不影响本次检索结果返回。
- `confidence_weight` 默认值刻意设得较小（0.1），避免信度权重压过内容
  相关性本身；`wiki_index_reuse_enabled=False` 可以完全退回本次改动前
  的全量扫描行为。

## 4. 未在本次实施范围内的项

- §4.2.3 分区组织：按原计划本就是"记录演进路径，不在当前阶段实现"，
  维持不变。
- `wiki/dedup.py` 及其调用方（`world_writer.py` / `decision_writer.py`
  / `promotion.py`）里各自的 `discover_pages()` + 全量 `parse_page`
  扫描：这些调用点通常只扫描单一 `page_type` 目录（entities/decisions
  等），规模天然小于 `search.py` 面对的全库扫描，本次未一并接入索引
  复用，留作后续按需评估（原计划 §4.1 的分析重点也在 `search.py` /
  `dedup.py` 的"粗筛"场景，`dedup.py` 内部函数本身不做磁盘扫描，扫描
  逻辑在各调用方，接入方式与本次 `search.py` 的改法一致，可复用
  `wiki/index_reader.py` 的 `IndexData`，但需要调用方各自决定候选集
  合口径，暂不在本次一并处理）。
- O1 是 §0 问题清单里 E3/O2/O3 的前置依赖，本次完成后可以推进后续项，
  按原计划 §8 排期建议顺序为 E3 → E1 → O2 → O3 → O4。
