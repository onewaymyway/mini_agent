# Wiki 式知识库指南

对应设计文档：项目根目录《wiki式知识库重构计划.md》(`wiki-style-knowledge-base-refactor-plan.md`)（阶段一~四）、《wiki式知识库改进计划.md》(`wiki-style-knowledge-base-improvement-plan.md`)（P0-P4）、《wiki知识库提取与组织层改进计划.md》(`wiki-knowledge-base-extraction-and-organization-plan.md`)（O1-O4、E1-E3，§十）。这是[图书馆式知识索引](library-index-guide.md)（分类树 + 实体索引 + 目录）之外的一套**平行新实现**，不替换旧系统，两者在过渡期并存运行，直到新检索路径经过实际验证效果稳定。

## 一、为什么需要这套新系统

图书馆式索引把"归类"这一件事做得很细（分类树自动生长/合并、实体去噪/近重复合并），但它的核心假设是"每条知识只有一个最合适的位置"——一条记忆只能挂一个分类号，一个实体只有一段滚动覆盖的摘要字符串。这个假设在纸质图书馆里成立，但软件工程知识天然是网状的：一个模块的设计离不开它依赖的模块、它取代的旧方案、它引出的新问题，这些关系没有地方能被显式表达。

Wiki 式知识库用三条设计原则解决这个问题：

1. **知识以 md 文件为唯一真相**——人可以直接打开、直接读、直接手改，不需要通过工具才能查看知识内容。
2. **页面之间的关系是一等公民**——`depends_on`、`supersedes`、`absorbs` 这些关系本身就是知识的一部分，不是挂载关系的副产品。
3. **索引全部是可重建的编译产物**——`_index/` 下的四个文件随时可以删除、随时能从 md 重新生成，不会因为索引损坏丢失任何真实信息。

## 二、目录结构

```
.agent/wiki/                    # project scope；global scope 是 ~/.agent/wiki/
├── entities/     # 实体型：模块、工具、bug 模式、外部依赖
├── decisions/    # 决策型：某个取舍为什么这么定
├── processes/    # 流程型：怎么做某件事的标准步骤
├── experiences/  # 经验型：非正式的踩坑总结、直觉性知识
├── topics/       # 专题页：聚合多篇页面的综合叙事（阶段四新增，LLM 自动生成）
├── _migration_map.json   # entity_id → page_id 映射，双写路径依赖的持久状态（不属于可随时删除的 _index/）
└── _index/       # 全部为脚本生成的派生产物，可随时删除重建
    ├── graph.json          # 页面间链接图
    ├── tags.json           # tag -> 页面列表
    ├── backlinks.json      # 反向链接
    ├── search_index.json   # 关键词倒排索引
    └── _manifest.json      # indexer.py 自用的增量重建状态（mtime+hash）
```

页面格式是 frontmatter + 结构化正文：

```yaml
---
id: role-agent-dispatcher
type: entity          # entity | decision | process | experience | topic
tags: [judge-system, dispatcher]
status: active         # active | deprecated | superseded | revisited
                       # decision 类型页面单独用一套生命周期：settled | revisited | overturned（见九·2）
confidence: 0.8
created: 2026-06-01
updated: 2026-07-10
links:
  - target: turn-judge
    relation: absorbs
    note: "Phase6b将TurnJudge的职责迁移至此"
source_entries: [entry_a1b2, entry_c3d4]   # 指回原始 MemoryStore 记忆，可追溯
grounded_hit_count: 3       # O1：被 LLM 精排判定为回答依据的累计次数，影响检索排序权重
knowledge_state: fresh      # O4：fresh | stale | superseded，知识生命周期状态（独立于上面的 confidence 数值分数）
last_validated_at: 2026-07-18T00:00:00+00:00   # O4：最近一次被确认仍然有效的时间
validated_by: [grounded_hit]                    # O4：触发确认的来源类型列表
---

正文...包含 [[turn-judge]] 这样的弱引用...
```

正文内的 `[[page-id]]` 是自然行文中的弱引用（自动记为 `relation: mentions`），frontmatter 的 `links` 是结构化强关系。两类链接并存于 `WikiPage.links`，通过 `source` 字段区分（`"frontmatter"` | `"body"`），`strong_links()`/`weak_links()` 两个方法分别取用。若同一 target 既被 frontmatter 声明又在正文里被提及，解析时丢弃重复的弱引用，只保留强关系。

## 三、模块地图（`src/mini_agent/wiki/`）

| 模块 | 阶段 | 作用 |
|---|---|---|
| `parser.py` | 一 | 解析单个 md 页面：frontmatter + 正文 + `[[link]]` |
| `graph.py` | 一 | `GraphIndex`：内存图结构，正向边+反向边，`expand()` 一跳扩展 |
| `indexer.py` | 一 | 遍历 `wiki/` 生成 `_index/` 下四个派生索引，支持增量模式 |
| `writer.py` | 一 | 原子写：新建/更新页面、追加 section、更新 status |
| `validator.py` | 一 | 死链检测、id 冲突检测、孤儿页面提示 |
| `migration.py` | 二 | `migrate_entity_store()` 一次性导出 + `mirror_entity()` 双写共用函数 |
| `dedup.py` | 二 | 页面相似度判断：默认规则+LLM，embedding 作为可选路径 |
| `search.py` | 三 | 三段式检索：规则粗筛 → 图扩展 → LLM 精排 |
| `topics.py` | 四 + P3 + 外部知识化P2 | 专题页生成：tag 聚类 + 强链接密度（规则）与不依赖 embedding 的 LLM 直接聚类两条路径并存，候选池合并去重后 LLM 综合聚合；新增 `build_topic_digest()`/`build_topic_digest_section()` 生成极简专题页索引，供外部知识抽取 prompt 注入使用 |
| `promotion.py` | P4 | wiki 转正为主索引的三项标准量化：每日快照、检索 A/B 对比日志、连续达标判断（只观测，不切换） |
| `decision_writer.py` | 决策提炼 | 决策候选落盘：命中已有决策页则更新/推翻，命中不到才新建 |
| `world_writer.py` | P1 + O4 + 外部知识化P1 | 世界模型候选（entities[]/facts[]）批量落盘，fact 以正文内锚点注释（`#fact-N`）实现独立状态标记；`queue_entities()`/`queue_facts()` 支持 `source_kind` 参数，供对话来源（`world_model`，默认值）之外的调用方（如 `external_input/knowledge_extractor.py`）标注来源 |
| `entity_digest.py` | E3 | 生成极简实体索引摘要，反向注入抽取 prompt，让模型识别实体时能复用已有 id |
| `index_reader.py` | O1 | 读取 `indexer.py` 生成的派生索引供 `search.py`/`dedup.py` 复用，避免全量 `parse_page` 扫描 |
| `lifecycle.py` | O4 | 统一知识生命周期状态机：`mark_page_state()`/`touch_validated()`/`stale_candidate_scan()` |
| `stats.py` | P0 + E2 + O4 | 内容来源分布、抽取批次充分性、知识生命周期状态分布统计，供 `/wiki stats` 展示 |

`history/extraction_trigger.py`（E1）与 `history_manager.py::maybe_trigger_extraction()`（E1）不在 `wiki/` 目录下，但属于同一条提取管线：独立于 compact 的候选窗口探测器，解耦"是否该抽取"与"是否该压缩"两个信号源，详见「十一」。

## 四、写入侧：与图书馆式索引双写

`LibraryIndex.__init__` 的可选参数 `wiki_paths: Optional[AgentPaths]`（默认 `None`）控制是否启用双写：

- `on_new_entry()` 挂载实体后，尝试把实体镜像进 `wiki/entities/*.md`（`_mirror_entities_to_wiki()`）。
- `mark_stale_from_correction()` 标记某实体 `superseded` 时，同步把新状态写回对应 wiki 页面的 frontmatter。
- `consolidate()` 每轮重写过摘要的实体也会被镜像（步骤 5），镜像前先判重（见下）。

所有镜像动作都包在 `try/except` 里，失败只是"少镜像一次"，不会让分类树/实体索引/编年目录这些主索引的写入跟着失败——wiki 目前的定位是**镜像层**，不是**真相来源**。

`MemoryConfig.wiki_enabled: bool = True` 是这条双写路径的总开关，`perception/memory_factory.py::_build_library_index()` 据此决定是否把 `wiki_paths` 传给 `LibraryIndex`。关闭后 `wiki/` 目录完全不被触碰，行为与不存在这套系统时一致。

> **实现记录**：这个开关是本轮（阶段三）补上的接线。核对代码时发现 `wiki_paths` 参数虽然在阶段二就加进了 `LibraryIndex.__init__`，但 `memory_factory.py` 从未真正传过这个参数——阶段二的双写代码路径在真实运行的 agent 里此前一直不会被触发，只有手动构造 `LibraryIndex` 时才生效。

### 判重：默认规则打分 + LLM 确认，而非 embedding

`consolidate()` 把实体镜像进 wiki 前，先用 `wiki/dedup.py::find_similar_page()` 判断是否已有语义相近的既有页面：

- **默认方案**：tag 重合度 + 正文关键词 Jaccard 相似度加权打分。分数落在高阈值以上直接判定相似；落在中间不确定区间时，只对分数最高的 top-1 候选调一次 LLM 做 YES/NO 确认；低于低阈值直接判定不相似。全程不需要 embedding 依赖。
- **可选方案**：显式传入 `embed_call` 时切换为 embedding 余弦相似度，两条路径互斥，由调用方决定用哪个。

命中相似页面则把这条更新并入该页面的"历史沿革"，而不是各自新建成割裂的两篇页面——这是原计划"替代 `difflib` 字符串相似度判重"的具体落地方式。

## 五、检索侧：三段式检索（阶段三）

`wiki/search.py::wiki_shelf_search()` 是图书馆式索引 `shelf_search`（两步检索：定位书架 → 架内精排）的**平行实现**，通过 `LibraryIndex.wiki_search(query, k, llm_call, tags)` 暴露：

```
query
  │
  ▼
第一段 规则粗筛（零 LLM 成本）
    对 query 与每篇既有页面做 tag 重合度 + 正文关键词 Jaccard 相似度加权打分，
    取 top tag_top_n（默认 25）篇候选
  │ 零命中 → 返回空结果（stage_reached="none"），调用方应回退到 shelf_search
  ▼
第二段 图扩展（零 LLM 成本）
    命中候选的 frontmatter 强链接展开一跳（GraphIndex.expand(strong_only=True)），
    把依赖/取代/因果关系带入候选池——这是相对分类树检索的核心增量能力
  │ 没有传 llm_call → 到此为止，返回候选（stage_reached="rule"|"graph"）
  ▼
第三段 LLM 精排
    候选收窄到 rerank_top_n（默认 8）篇后，把完整正文（不是摘要）交给 llm_call
    排序并生成综合回答，回答后标注"基于页面: ..."，解析进
    WikiSearchResult.grounded_page_ids（stage_reached="llm"）
```

`WikiSearchResult` 字段：`pages`（候选页面列表）、`answer`（综合回答，未走 LLM 精排时为空）、`grounded_page_ids`（LLM 标注的依据页面）、`stage_reached`（实际走到了哪一段，供调用方/人工判断检索质量）。

**这套检索最初是"平行实现"，现在已经是默认优先路径**（wiki 式知识库改进计划 P4 §6.5）：`context_builder.py` 每轮检索优先尝试 `wiki_search`，`grounded_page_ids` 非空才采用其结果，否则退回 `shelf_search`。`wiki_paths=None`、wiki/ 下没有页面、或没有 `llm_call` 走不到 LLM 精排时，`wiki_search` 自然返回空/无 grounded 结果，退化行为与"两条路径完全独立运行"时期完全一致——只是现在默认顺序是"先试 wiki，不行再兜底 shelf"，而不是两条各自平行、由人工挑选。详见「八·2」。

## 六、专题页生成（阶段四 + P3 检索与聚合优化）

`wiki/topics.py::consolidate_topics()` 是 `consolidate()` 步骤 7 的入口，解决"没有可读的综合层"问题——一次跨模块的大重构（比如判断/调度系统整合），此前没有任何地方能承载"这件事的完整来龙去脉"，只能靠人去几个实体的摘要里拼凑。

候选来自两条并存的路径：

1. **tag + 链接密度（规则，阶段四原有路径）**：按 tag 对全部非 topic 类型页面分组，**页面数达到阈值**（`min_pages`，默认 4）**且组内 frontmatter 强链接密度达到阈值**（`min_density`，默认 0.5，定义为"组内强链接边数 / 组内页面数"）才算候选——只是恰好共享同一个 tag 不够，必须真的紧密关联。
2. **LLM 直接聚类（P3 新增，`find_topic_candidates_llm_cluster`）**：不依赖任何 embedding 模型——把规则路径没覆盖到的候选页面的 id/tags/正文摘要整体交给同一个 `llm_call`，一次调用输出"哪几篇页面在讲同一件事"（JSON 数组 `[{"topic": ..., "page_ids": [...]}]`），弥补规则路径抓不住的场景：tag 不同、没有强链接，但语义上确实相关的实体/事实/经验页面（这类页面在 P1/P2 落地后大量产生）。聚类簇成员数阈值 `llm_cluster_min_pages` 默认 3（比规则路径的 4 更低，因为语义聚类天然比"共享 tag"更精确）。`consolidate_topics(..., use_llm_clustering=False)` 可关闭这条路径，退回纯规则行为。

两个候选池合并时按页面重合度（Jaccard）去重（`_merge_candidate_pools`，阈值默认 0.5）：规则路径候选优先保留，LLM 候选与已接受候选重合度过高则判定为重复候选丢弃，避免同一批页面被两条路径各生成一篇内容重复的专题页。

命中候选后，把组内全部页面正文交给 LLM 综合改写成一篇叙事（不是逐篇复述），写入 `topics/<tag>.md`，frontmatter 用 `relation: absorbs` 声明对每篇成员页面的强链接，并新增 `cluster_source`（`tag_density` | `llm_cluster`）与（LLM 聚类路径特有的）`topic_label` 字段，标注该专题页来自哪条候选路径。已经生成过专题页的 tag（读取既有 `topics/*.md` 里的 `source_tag` 附加字段）会被排除，避免同一批页面反复触发生成——当前版本只支持"生成新专题页"，不支持"更新既有专题页"。

只在传入 `llm_call` 时生效——两条路径的候选生成规则本身只负责"值不值得生成"的判断（LLM 聚类路径连"候选生成"这一步都要靠 LLM），没有 LLM 就没有能力生成综合叙事正文，直接跳过而不是勉强拼接。

## 七、`consolidate()` 完整步骤（含 wiki 相关的 5/6/7）

`LibraryIndex.consolidate(store, llm_call=None, wiki_dedup=True, wiki_embed_call=None)`：

1. 分类树生长（未分类候选聚类出新节点）
2. 分类树合并（语义重合的节点收敛）
3. 实体摘要批量重写（含冲突检测）
4. 实体巩固：去噪 + 近重复合并
5. **wiki 镜像**：本轮重写过摘要的实体，判重后镜像/并入 wiki 页面
6. **wiki 索引重建**：步骤 5 有任何写入时，触发 `indexer.py::build_index(incremental=True)`，刷新 `_index/` 下四个派生文件
7. **专题页生成**：tag+链接密度（规则）与不依赖 embedding 的 LLM 直接聚类两条候选路径并存，合并去重后触发 LLM 综合聚合成 `topics/*.md`
7b. **转正评估每日快照**（P4）：无条件记一条当日快照（同一天幂等），累积 `source_kind` 目标占比与校验错误数，供 `/wiki promotion` 判断三项转正标准

返回值新增字段：`wiki_mirrored`、`wiki_dedup_merged`、`wiki_index_rebuilt`、`wiki_pages_indexed`、`wiki_topics_generated`（新生成的专题页 id 列表）。`/evolve consolidate` 报告会展示这些统计。

## 八、`/wiki` CLI 命令（阶段四 + P4）

| 命令 | 说明 |
|---|---|
| `/wiki <page-id>` | 展示指定页面的 frontmatter 概要、正文、frontmatter 强关系，以及从 `_index/backlinks.json` 读出的反向链接 |
| `/wiki list [--type T]` | 列出全部页面，可按 `type`（entity/decision/process/experience/topic）过滤 |
| `/wiki search <query> [--deep]` | `LibraryIndex.wiki_search()` 的命令行封装，展示三段式检索走到了哪一段、综合回答、候选页面（LLM 精排标注过的打 ★），用于人工对比新旧检索路径的实际效果；同时顺带跑一次 `shelf_search` 并把两边"是否给出有依据的结果"记一条 A/B 对比样本，供 `/wiki promotion` 累积统计。`--deep` 强制走 O2 的多跳衰减图扩展（`max_hops=2`），不传则规则粗筛候选数量不足以覆盖 `rerank_top_n` 时自动升级 |
| `/wiki rebuild [--full]` | 手动触发一次索引重建（默认增量，`--full` 强制全量），相当于把 `consolidate()` 步骤 6 单独拎出来手动跑一次，并展示 `validator.py` 校验出的死链/孤儿页面问题 |
| `/wiki stats` | 内容来源分布统计（P0），展示 `by_type`/`by_entity_type`/`by_source_kind`/`by_knowledge_state`（O4）以及抽取批次充分性指标（E2 方案B） |
| `/wiki promotion` | wiki 转正为主索引的三项标准（P4）当前达成情况：内容占比连续达标天数、校验无错误连续天数、检索 A/B 命中率对比，末尾给出仅供参考的一句话结论——**该命令只读、不触发任何切换动作** |
| `/wiki lifecycle-scan [--days N]` | O4：手动触发一次知识生命周期巡检（`stale_candidate_scan()`），把长期未被验证过的 `fresh` 页面标记为 `stale`。默认阈值取 `MemoryConfig.lifecycle_stale_threshold_days`（90 天），只做标记，默认不影响检索排序 |
| `/wiki fallback-cleanup [--days N]` | 归并/标记 `session-facts` 兜底页里长期未被合并的 fact（见 §十一·5 新增子命令小节） |
| `/wiki quarantine [list\|repair\|retry\|purge]` | 解析失败页面隔离区（见 §十四）：`list`（默认）只读展示当前 pending/needs_human 记录；`repair` 手动触发一轮"全量扫描 + 尝试修复"，跟 `sys:wiki_quarantine_repair` cron job 是同一份逻辑；`retry` 把 `needs_human` 记录重置为 `pending` 并立即重新触发一轮修复，用于"新增了修复策略、想把之前救不回来的旧记录重新捞一遍"的场景；`purge` 删除"确定救不回来"的问题页面（磁盘文件 + 隔离区记录一起清），两步确认，默认只预览、加 `--yes` 才真正执行 |

`/wiki <page-id>` 找不到 backlinks 时会提示先跑 `/wiki rebuild`，而不是静默显示"无 backlinks"——backlinks 只存在于 `_index/` 里，页面本身刚写入还没重建索引时是看不到的。

## 八·2、wiki 转正为主索引的评估（P4）

`wiki/promotion.py` 把《wiki 式知识库改进计划》P4 定义的三条"转正"标准从文字描述变成可持续观测的量化指标：

1. **内容占比**：连续 14 天，`world_model + decision + experience_success + experience_session_reflection` 四类 `source_kind` 合计占比 >= 50%（即不再是"错题本"）。
2. **校验通过**：连续 7 天 `validator.py` 全量校验无 error 级别问题（死链/id 冲突）。
3. **检索 A/B**：`wiki_search` 与 `shelf_search` 的 grounded 命中率对比，样本量 >= 20 条才下结论，`wiki_hit_rate >= shelf_hit_rate` 才算达标。

三项标准同时满足才是 `overall_ready=True`。数据来源：`consolidate()` 每轮自动记一条每日快照（`_index/promotion_log.jsonl`），`/wiki search` 每次顺带记一条 A/B 对比样本（`_index/search_ab_log.jsonl`），两份都是可随时删除重新累积的观测记录，不是知识本身。

**实际切换（应用户明确要求追加执行，详见改进计划 §6.5）**：`context_builder.py::refresh_turn_context()` 现在默认（`MemoryConfig.library_wiki_search_primary = True`）优先尝试 `wiki_search`，只有拿到有依据的结果（`grounded_page_ids` 非空，需要 `llm_call` 走完 LLM 精排）才采用其输出并跳过 `shelf_search`；未命中/无可用 `llm_call`/异常时自动退回原有 `shelf_search → merge_search → 全库 search` 链路，接口行为与切换前完全一致。**这次切换是在没有任何真实 P4 观测数据的情况下执行的**，与"先持续观测达标再切"的原始设计意图不完全一致——生产使用前建议先跑 `/wiki promotion` 确认三项标准是否站得住脚，不放心可以随时把 `library_wiki_search_primary` 设为 `False` 完全退回旧默认路径，不需要改代码。若想完全关闭"处理用户输入前自动检索"这一行为本身（而不只是切换检索路径），把 `MemoryConfig.per_turn_retrieval_enabled` 设为 `False` 即可——`refresh_turn_context()` 会在到达 `wiki_search`/`shelf_search` 之前直接返回，两个开关都不再生效。

## 九、新增的落盘文件（均可重建或明确标注为持久状态）

在 `.agent/wiki/`（project scope）和 `~/.agent/wiki/`（global scope）下各自独立一套，路径定义见 `storage/paths.py`：

| 文件/目录 | 内容 | 是否可随时删除重建 |
|---|---|---|
| `entities/`、`decisions/`、`processes/`、`experiences/`、`topics/` | 页面 md 文件 | 否——这是知识本身，唯一真相 |
| `_migration_map.json` | entity_id → page_id 映射 | 否——双写路径依赖的持久状态，删除会导致同一实体被重复创建新页面 |
| `_index/graph.json` / `tags.json` / `backlinks.json` / `search_index.json` | 派生索引 | 是——`/wiki rebuild` 随时可重建 |
| `_index/_manifest.json` | indexer 自用的增量重建状态 | 是——删除后下次退化为全量重建 |
| `_index/promotion_log.jsonl` | P4 每日快照（占比、校验错误数） | 是——删除只是重新开始累积观测天数，不丢知识本身 |
| `_index/search_ab_log.jsonl` | P4 检索 A/B 对比样本 | 是——删除只是重新开始累积样本，不丢知识本身 |
| `_index/topics_reconsolidation_log.jsonl` | O3 topic 再巩固事件日志（追加次数、是否触发 `needs_review`） | 是——删除只是重新开始累积观测，不丢已追加进 topic 正文的内容 |
| `_quarantine.json` | §十四：解析失败页面的问题记录（路径、错误信息、修复状态/尝试次数） | 是——纯诊断/修复调度状态，删除只是丢失历史检测记录，下次 `sys:wiki_quarantine_repair` 扫描会重新发现仍然存在的问题页面；注意这与 `/wiki quarantine purge` 不同——`purge` 删的是记录指向的页面 md 文件本身（不可恢复），不是这份记录文件 |
| `decision_candidates_pending.jsonl`（workdir 根目录，非 wiki/ 下） | compact 提炼出的决策候选，尚未经巩固循环批量落盘 | 否——删除会丢失尚未处理的候选（不影响已落盘的 decisions/*.md），见九·2 |
| `extraction_cursor.json`（workdir 根目录） | E1 独立抽取触发器的 `last_extracted_index` 游标 | 否——删除会导致下次扫描重新覆盖已抽取过的历史区间，产生重复抽取（不会丢已落盘知识，只是浪费一次 LLM 调用） |
| `extraction_trigger_log.jsonl`（workdir 根目录） | E1 规则扫描命中记录，供校准触发阈值 | 是——删除只是重新开始累积观测样本 |

## 九·2、决策/取舍知识提炼（对应《决策/取舍知识提炼计划.md》）

工程决策的价值在"为什么这么做、否决了什么方案、为什么否决"，这类知识只有在
有人打算换一种做法时才需要被翻出来——目标不是"记录得全"，而是**防止同一个
已经被否决过的方案被重新提出、重新论证一遍**。这条提炼线独立于 lesson
（规则触发）和 correction（人类显式纠正）之外，因为决策往往发生在一切顺利、
没有报错、没有人纠正的正常推进过程中，前两条线完全覆盖不到。

### 提取时机：复用 compact 的 LLM 调用，不新增调用次数

`history/compression.py::LLMSummaryStrategy` 原本请求纯文本摘要，现在把输出
改成结构化 JSON（`system/compress_summarizer.md` / `user/compress_summary_request.md`
两个 prompt 已同步改造）：

```json
{
  "compact_summary": "……（原有的上下文恢复摘要，字段含义不变）",
  "decisions": [
    {
      "topic": "分类树合并策略",
      "options_considered": ["纯规则相似度", "纯LLM判断所有实体对", "规则+LLM兜底"],
      "chosen": "规则+LLM兜底",
      "rejected_because": {"纯LLM判断所有实体对": "组合数会爆炸，成本不可控"},
      "related_entities": ["classification-tree"]
    }
  ]
}
```

`history/decision_extraction.py::parse_decision_response()` 负责解析这段 JSON
（容错：允许被代码块包裹；解析失败时整段原文退化为 `compact_summary`，
`decisions` 置空，不阻断 compact 本身）。`decisions` 允许为空数组——大多数
turn 段落里不存在值得记录的决策，只有"讨论了多个方案并做出取舍"的段落才会
产生条目。由 `CompressConfig.extract_decisions`（默认 `True`）控制是否启用
这一步，关闭后 `LLMSummaryStrategy` 的行为与改造前完全一致。

### 落盘：巩固循环批量合并后，命中已有决策页则更新状态，命中不到才新建

`history/compression.py::LLMSummaryStrategy` 提炼出的决策候选不再由 compact 直接
落盘，而是先由 `wiki/decision_writer.py::queue_candidates()` 原样 append 到
`.agent/decision_candidates_pending.jsonl`（pending 队列），真正的落盘延后到
巩固循环（`evolution/consolidation.py::run_consolidation` →
`decision_writer.consolidate_pending()`）批量执行，见九·2 末尾"批量节流新建"
一节的详细说明。

`consolidate_pending()` 先做**批内合并**：同一批 pending 候选里 `topic` slug
相同或 `related_entities` 有交集的，视为指向同一件事，只保留最新一条 `chosen`
（这一步就是解决"逐条即时落盘导致碎片化"的关键）。合并后的候选交给
`process_candidates()` 走三分支逻辑（这部分逻辑完全没变）：

1. **命中已有决策页且 `chosen` 与已有页面一致** → 只更新 `source_entries` /
   `updated`，不新建重复内容。
2. **命中已有决策页但 `chosen` 不一致** → 说明决策被重新做过：旧页面
   `status` 改为 `overturned`，新建一条决策页并用
   `links: relation=supersedes` 指回旧页面，旧页面反向追加
   `links: relation=superseded_by` 指向新页面，形成双向可追溯的沿革链条。
3. **未命中任何已有决策页** → 新建一条决策候选页（`status=settled`）。

"命中"的判定：`related_entities` 中的某个 id，是否与某个既有 decision 页面
`frontmatter.links` 里 `relation=affects/part_of` 的 target 重合——直接复用
`parser.py` 已经解析好的 `WikiPage.strong_links()`，不需要给实体侧单独建索引。

"新建"这个动作（分支 2 和分支 3）额外套一层节奏治理冷却（复用
`evolution/consolidation.py::rhythm_is_allowed()`/`record_proposal()`，key 为
`decision_new:<topic slug>`，默认冷却 1 天，`CompressConfig.
decision_batch_min_interval_days` 可调），避免同一个决定短时间内被反复提炼出
候选而多次新建页面；"更新"不受此限制。

决策页 `confidence` 固定为 `0.5`，独立于 lesson（规则触发，0.6）与 human
correction（人类显式纠正，0.7）两档——决策复盘是 agent 对自己历史行为的二次
解读，主观重构风险高于前两者，不能直接套用同一套 confidence 语义。

`status` 生命周期改为决策页专用的三段：`settled`（尚未被重新审视）→
`revisited`（被重新提起讨论但维持原判，本轮实现未接入自动判定，需人工/后续
迭代补充触发路径）→ `overturned`（被推翻，此时必有一条 `superseded_by` 指向
替代页面）。`parser.py::STATUS_VALUES` 为兼容 entity/topic 等页面沿用的旧词表
（`active`/`deprecated`/`superseded`/`revisited`），采用合并词表而非按 type
拆分校验；`validator.py` 新增 `_check_supersession_pairs()`，校验
`supersedes`/`superseded_by` 是否成对出现、`status=overturned` 的页面是否都
有 `superseded_by` 出边，缺失时给 warning（不阻断整体索引重建）。

### 提案前主动召回：决策提炼真正的价值出口

`evolution/decision_recall.py::recall_related_decisions()` 直接复用
`wiki/search.py::wiki_shelf_search()` 的三段式检索（不新增检索通路），把候选
限定在 `type=decision` 的页面，按 `status` 分成两类分别渲染：

- 命中 `status=settled`（或 `revisited`）的历史决策 → 提示"这个方向已经被
  采纳过，请先确认是否要重复论证"
- 命中 `status=overturned` 的历史决策 → 提示"这个方案之前被考虑过又被否决，
  请先确认新提案是否与被否决的方案相同"

返回一段可以直接注入 prompt 的提醒文字（没有命中时返回空字符串）。这一步
设计成同步查询接口，由调用方在生成新的架构改动/重构提案之前主动调用——不同于
`evolution/lesson_to_reminder.py` 那种"离线批量生成 reminder 文件、靠
`ReminderLoader` 轮询加载"的模式，因为决策召回的触发条件（"即将提出新方案"）
本质上是语义性的，没有 lesson 场景里"连续失败 N 次"那样可以离线预生成的确定
性信号。

**已接入的触发点**：`cli/commands/evolve.py::_spawn_evolution_agent()`——
这是当前仓库里唯一"生成新方案"的确定性入口：`/evolve review` 把攒够证据的
lesson 分组 spawn 给 evolution-agent 去提炼 skill 提案。spawn 之前会以各
lesson 分组的 `key` 拼成 `proposal_summary`，调用 `recall_related_decisions()`
查一遍相关历史决策，命中时把提醒文字前置拼进 `spawn_named_agent(..., context=...)`
的 `context` 参数，让 evolution-agent 先看到提醒再动笔。查询/渲染失败不影响
`/evolve review` 本身（静默降级）。

如果后续 `AutonomousLoop` 长出新的"自动生成重构目标"入口，同一套
`recall_related_decisions()` 可以原样复用；目前只有这一个确定性入口，暂不提前
挂载其它位置。

**日常对话里的两条消费路径**（区别于上面"自我演化提案"这一条离线路径）：

- **路径 C（工具化，`recall_decisions`）**：`tools/builtin.py` 注册了一个只读、
  免审批的 `recall_decisions` 工具（`CompressConfig.decision_recall_tool_enabled`
  控制是否注册，默认开启），由 agent 在自己意识到"这是个需要取舍的架构/技术
  决定"时主动调用，不依赖任何门控命中——哪怕路径 B 的启发式没命中，agent 主动
  查也能拿到收益，同时覆盖"用户直接问技术选型"这类场景。
- **路径 B（前置门控自动注入）**：`agent/reminders_correction.py::
  _maybe_recall_decisions_for_user_message()` 挂在 `turn_loop.py` 里跟
  `_inject_reminders_for_user_intent()` 同一个时机——每轮用户消息入队后，先用
  `decision_recall.should_trigger_recall()` 做便宜的关键词判断（"要不要"/
  "换成"/"选型"/"重构成"等），命中才真正调用 `recall_related_decisions()`。
  命中结果走跟 lesson reminder 完全一样的一次性注入机制（`_inject_reminder()`，
  同轮去重，不常驻占用 context）。默认关闭
  （`CompressConfig.decision_recall_turn_gate_enabled=False`），先观察启发式
  命中率再决定是否默认打开。

### 本轮未完成事项（按原计划三阶段对照）

- 阶段一（结构与置信度体系）：已完成。
- 阶段二（提取改造）：已完成，包括原先缺失的"巩固循环批量决定是否新建"的
  节流批处理——现在 compact 只把候选写入 pending 队列，真正落盘延后到
  `run_consolidation()` 批量执行，见上文"落盘"一节与
  [巩固循环指南 4.4 节](self-evolution-consolidation-guide.md#44-决策候选批量落盘对应决策取舍知识提炼计划)。
- 阶段三（图关联与召回）：检索/召回逻辑已完成，"提案前主动召回"也已接入
  `/evolve review` 的 `_spawn_evolution_agent()`（见上一节）。仍未覆盖的：
  尚未在真实重构场景中长期观察 `overturned` 沿革链条的召回效果——这需要
  实际跑几轮 `/compact` + `/evolve review` 积累数据后才能评估，属于"需要
  使用周期"而非"代码未写"的遗留项。

## 十、提取层与组织层改进计划（O1-O4、E1-E3，均已完成）

对应设计文档《wiki知识库提取与组织层改进计划.md》(`wiki-knowledge-base-extraction-and-organization-plan.md`)，是对上面「阶段一~四 + P0-P4」落地后暴露出的两类深层问题的后续深化：**提取时机/耦合度/知识盲视**（E1-E3），以及**组织结构的信度分层/图谱表达力/动态性/生命周期一致性**（O1-O4）。建议实施顺序 O1 → E3 → E1 → E2 → O2 → O3 → O4，全部条目已按此顺序完成实现。

### O1：全量扫描架构分层

`wiki/search.py`/`wiki/dedup.py` 原本每次调用都对 `wiki/` 全量 `parse_page`。O1 让两者优先复用 `indexer.py` 已经生成的 `_index/` 派生索引（`wiki/index_reader.py::load_index()`），只有索引缺失或 `_manifest.json` 校验发现明显过期时才退回全量扫描——纯性能优化，不改变检索结果。

同时引入知识信度分层：页面 frontmatter 新增 `grounded_hit_count`（见上面 frontmatter 示例），每次被 LLM 精排判定为"回答依据"就 +1（`wiki/writer.py::increment_grounded_hit_count()`），`_rule_score()` 打分公式变为：

```
score = 0.6 * token_jaccard + 0.4 * tag_jaccard + confidence_weight * log(1 + grounded_hit_count)
```

`confidence_weight`（`MemoryConfig.wiki_confidence_weight`，默认 0.1）设为 0 时与改动前排序结果完全一致，属于纯粹的回归保护开关。§4.2.3 设想的"按项目子系统二级目录分区"本次未实现，只记录演进路径，留待页面规模真正成为瓶颈时再评估。

### E3：抽取"看不到"已有知识库

原本 `world_extraction.py` 抽取实体时完全"裸识别"，同一实体在不同 session 用不同措辞命名，全靠事后 `dedup.py` 补救。E3 新增 `wiki/entity_digest.py::build_entity_digest()`，生成一份极简实体索引（`id + entity_type + 一句话描述`）反向注入抽取 prompt：

```
当前已知实体（如果新识别的实体和下列某一项指代同一个东西，请复用其 id，不要新建）：
- client-pool（模块）：负责多 LLM provider 的 API key 轮换与故障转移
...
```

`EntityCandidate.reused_existing_id` 由模型自报"这是已有的哪个实体"，`world_writer.py::_write_or_merge_entity()` 优先信任这个字段，但仍用 `dedup.py::score_similarity()` 校验一次分数（低于阈值则忽略模型判断，退回规则判重）——"模型优先判断 + 规则兜底"两段式，而不是纯规则判重。由 `CompressConfig.entity_digest_enabled`（默认开启）控制。

### E1：抽取时机与 compact 解耦

原本 `decision_extraction.py`/`world_extraction.py` 只在 compact 触发时被调用，而 compact 靠 token 预算触发，跟"这段对话是否已经积累了值得提炼的知识"是两个独立信号。E1 新增 `history/extraction_trigger.py::scan_for_extraction_window()`：零 LLM 成本的规则扫描（连接词密度"因为/所以/决定/改为"等 + 轮次计数兜底），命中候选窗口时 `history_manager.py::maybe_trigger_extraction()` 异步排队一次"仅抽取、不压缩"的轻量 LLM 调用，独立于 `compaction.py` 的触发路径。

`last_extracted_index` 持久化在 `extraction_cursor.json`，避免同一段内容被反复抽取。由 `CompressConfig.extraction_trigger_enabled`/`extraction_trigger_dispatch_enabled` 两级开关控制（前者控制"是否扫描并记日志"，后者控制"命中后是否真的发起 LLM 调用"）。**[2026-07 更新]** 两者均已默认开启（应用户明确要求提前打开，跳过了原计划设想的"先观察 `extraction_trigger_log.jsonl` 校准阈值再打开"的观察期）；如需临时退回只记录不抽取，可单独把 `extraction_trigger_dispatch_enabled` 设为 `False`。

### E2：抽取任务耦合度过高

`compress_summarizer.md` 单次 LLM 调用要同时产出 `{compact_summary, decisions[], entities[], facts[]}`，模型容易把注意力集中在排在 schema 首位、语义最直接的摘要任务上，导致结构化抽取字段被敷衍。

- **方案 B（已生效）**：调整 JSON schema 字段顺序为 `{decisions[], entities[], facts[], compact_summary}`，并在 prompt 里显式要求"先完整识别 decisions/entities/facts，最后再给出 compact_summary"。`wiki/stats.py::compute_extraction_stats()` 新增 `avg_entities_per_extraction`/`avg_facts_per_extraction` 观测指标，`/wiki stats` 展示，用真实数据判断方案是否有效。
- **方案 A（随 E1 自然解决）**：E1 落地后"轻量抽取"本身就是独立触发的单独调用，天然把结构化抽取和摘要任务拆开，无需额外实现。
- **方案 C（机制就位、待观测期后人工切换）**：`CompressConfig.extract_world_model`/`extract_decisions` 两个开关目前仍是默认 `True`（compact 路径继续做结构化抽取），待观测确认 E1 独立触发路径产出不弱于 compact 路径后，再手动关闭——这是目前改进计划里唯一仍处于"代码就位、待人工决策"状态的事项，其余均已是默认行为。

### O2：实体关系图过于扁平

`wiki/graph.py::GraphIndex.expand()` 原本只支持一跳扩展。O2 新增多跳衰减扩展：

```python
def expand(self, page_ids, *, strong_only=False, max_hops=1, decay=0.5) -> dict[str, float]:
    """返回 {page_id: weight}；第一跳权重为 decay，第二跳 decay**2……
    同一节点多路径可达时取最大权重（不是累加）。"""
```

`expand_legacy()` 保留原一跳签名（返回 `set[str]`）供既有调用点不受影响。`wiki_shelf_search()` 默认仍 `max_hops=1`；候选数量明显不足以覆盖 `rerank_top_n`，或用户显式传 `/wiki search --deep` 时自动/强制升级到 `max_hops=2`，权重字段标注进 LLM 精排 prompt 供参考。

### O3：topic 聚类是纯事后归纳

原本 `topics.py::consolidate_topics()` 只会新建 topic 页面，已有 topic 不会随新增相关页面更新，逐渐"静态失真"。O3 在生成新候选簇之前先跑一遍"再巩固"扫描：`_find_topic_reconsolidation_candidates()` 检查已有 topic 关联 tag 集合与新增页面的重合度，达标的新页面走 `append_to_topic_page()` 追加进已有 topic 正文（"新增关联" section + 补充 `absorbs` 链接），而不是参与新一轮聚类。

`TopicConfig.reconsolidation_interval_runs`（默认 5）控制扫描频率，每次成功的再巩固写入一条事件到 `_index/topics_reconsolidation_log.jsonl`。累计追加次数超过软上限（8 次）时标记 `needs_review: true`，提示后续考虑拆分该 topic，而不是无限追加。

### O4：统一知识生命周期状态机

decision/entity/experience/fact 四类内容原本各自维护自己的状态语义，没有统一入口。O4 新增 `wiki/lifecycle.py`：

- `mark_page_state(paths, page_id, *, confidence, reason="", validated_by="", anchor=None)`：跨页面类型的统一状态标记入口（`confidence` 取值 `fresh`/`stale`/`superseded`，写入独立的 `knowledge_state` 字段——之所以不复用已有的数值型 `confidence` frontmatter 字段，是因为两者语义完全不同，复用会造成同名字段类型冲突）。`anchor` 传入形如 `client-pool#fact-3` 时按 fact 锚点粒度标记，不影响整份页面状态。
- `touch_validated(paths, page_id, *, validated_by)`：隐式验证（比如被检索命中），`stale → fresh` 会回升，`superseded` 不会因隐式验证回升。
- `stale_candidate_scan(paths, *, threshold_days=90)`：巡检，把久未验证的 `fresh` 页面标记为 `stale`，可用 `/wiki lifecycle-scan` 手动触发。

fact 不再是完全无状态的正文片段：`world_writer.py::queue_facts()` 落盘时在正文里生成 `<!-- fact_id: <page-id>#fact-N; knowledge_state: fresh -->` 锚点注释，`mark_page_state(..., anchor=...)` 可以原地更新这行注释而不影响同页面其它 fact。人类纠正检测（`reminders_correction.py`/`library_index.py::mark_stale_from_correction()`）在原有基础上，额外对已镜像进 wiki 的实体页面调用 `mark_page_state(..., confidence="superseded")`。

`wiki/search.py::_rule_score()` 新增 `lifecycle_discount_enabled` 参数（默认关闭）：开启后 `stale` 页面打五折、`superseded` 页面粗筛阶段归零。`/wiki stats` 新增 `by_knowledge_state` 分布展示。

O4 未覆盖的部分（详见实施记录 §5）：`stale_candidate_scan()` 尚未自动挂载进 `consolidate()` 巡检链路，仍需手动 `/wiki lifecycle-scan` 触发；LLM 精排 prompt 尚未按 section 粒度排除 `superseded` 内容；人类纠正的覆盖广播仅限纠正事件命中实体自身对应的页面，未做基于 `source_entries` 的跨页面血缘追溯。

## 十一·5、下一阶段改进：退轨评估 / 巩固熔断 / 世界知识 / daemon 定时（本轮新增）

对应 `next_doc/wiki_next_phase_improvement_plan.md`，四个互相独立的补丁，均已实现：

| 模块 | 文件 | 作用 |
|---|---|---|
| 双轨制退出评估 | `wiki/decommission.py` | `check_and_plan()` 只读评估三项转正标准是否达标，达标给出「关闭 `legacy_index_enabled` → 观察 ≥2周 → 移除旧索引文件」三步执行清单；`check_ready_transition()` 在"未就绪→就绪"翻转的瞬间提醒一次（daemon 侧写 `activity_digest.jsonl`，`/evolve consolidate` 侧打印一行提示），不做任何自动下线动作 |
| 陈旧专题页标注 | `wiki/gap_scanner.py::mark_stale_topics()` | topic 页面 `absorbs` 链接指向的成员页面里 `knowledge_state != fresh` 占比超过阈值（默认 0.6）时，把该 topic 标记为过时，只标注不删除 |
| 巩固分步超时熔断 | `evolution/step_runner.py` | `run_step()` 给 `consolidate()` 每个子步骤独立的超时预算（线程+轮询，不用 `signal.alarm`），超时即跳过、不重试，下一轮巩固自然覆盖；`ConsolidationReport.step_timings` 记录每步耗时供排查 |
| 世界知识独立触发信号 | `history/extraction_trigger.py` | 新增 `trigger_reason="entity_density"`，规则扫描纯描述性内容里的"新词"密度（不依赖"因为/所以"这类决策语境连接词），与既有 `connective_density` 并行、互不干扰 |
| 知识缺口主动扫描 | `wiki/gap_scanner.py::scan_gaps()` | 规则扫描浅层实体（强链接 ≤1）、孤儿页面、陈旧专题页，零 LLM 成本；是否派发补全子任务由 `/wiki gap-scan --dispatch` 决定 |
| 兜底页清理 | `wiki/fallback_cleanup.py` | 对创建超过 N 天（默认 30）且从未被判重合并过的 `session-facts-<date>.md` 页面重新跑一次判重，命中则合并，命中不到则标记 `stale`（页面级粒度，不细到逐条 fact） |
| daemon 定时任务 | `evolution/cron_scheduler.py::_BUILTIN_JOBS` | 新增 `sys:wiki_gap_scan`（12h）、`sys:wiki_fallback_cleanup`（7d）两个内置 job，与已有 `sys:consolidation`（6h）并行、互不影响 |

### 新增 `/wiki` 子命令

| 命令 | 说明 |
|---|---|
| `/wiki gap-scan [--max-results N] [--dispatch]` | 触发一次知识缺口扫描（浅层实体/孤儿页面/陈旧专题页），默认只打印报告；`--dispatch` 把每条缺口包装成任务提交进 `InputQueue`（仅在 daemon `autonomous_loop` 上下文里可用，交互式 CLI 会话没有 `InputQueue`，会提示而非报错） |
| `/wiki fallback-cleanup [--days N]` | 对超过 N 天（默认 30）未处理的 `session-facts` 兜底页重新判重，命中合并、未命中标 `stale` |

### 命令行输入提示（本轮补上的缺口 + 本次追加）

`cli/commands/wiki.py::handle_wiki_cmd` 早已支持上述两个子命令，但驱动 REPL 里
`Tab` 补全 / 敲 `/` 弹出候选列表的命令定义表 `ui/terminal.py::_COMMANDS` 之前
**从未注册过 `/wiki` 这个顶级命令**——不影响命令本身能不能跑（`handle_wiki_cmd`
自己解析 `args`，跟补全表是两套独立逻辑），但用户在交互式终端里敲 `/wiki `
不会有任何提示，新加的 `gap-scan`/`fallback-cleanup` 更是无从发现，只能翻文档
才知道存在。当时补上后仍然漏了一层：`quarantine` 作为 `/wiki` 的子命令本身
**也从未出现在补全表里**（`handle_wiki_cmd` 里 `elif sub == "quarantine":`
分支之下还有 `list`/`repair`/`retry`/`purge` 四个二级子命令，同样完全没有
提示），本次一并补上，顺带把新增的 `retry` 子命令（见 §十四 needs_human
重试）也纳入：

```python
(
    "/wiki", "Browse wiki knowledge base pages / gap-scan / cleanup",
    [
        "list", "search", "rebuild", "stats", "promotion",
        ("lifecycle-scan", ["--days"]),
        ("gap-scan", ["--max-results", "--dispatch"]),
        ("fallback-cleanup", ["--days"]),
        (
            "quarantine",
            [
                "list",
                "repair",
                "retry",
                ("purge", ["--status", "--path", "--yes"]),
            ],
        ),
    ],
),
```

补全效果：敲 `/w` → 弹出 `/wiki`；敲 `/wiki ` → 弹出全部 9 个子命令；敲
`/wiki gap-scan ` → 弹出 `--max-results`/`--dispatch`；敲
`/wiki fallback-cleanup ` / `/wiki lifecycle-scan ` → 弹出 `--days`；敲
`/wiki quarantine ` → 弹出 `list`/`repair`/`retry`/`purge`；敲
`/wiki quarantine purge ` → 进一步弹出 `--status`/`--path`/`--yes`
（补全表支持任意深度嵌套，`_descend()` 递归下钻，不只两层）。
回归测试见 `tests/test_wiki_slash_completer.py`（用
`inspect.getsource(handle_wiki_cmd)` 反解出真实处理的子命令集合，和补全表逐一
比对，防止未来再次出现"能处理但没提示"的不一致；并用 `_build_slash_completer()`
真跑一遍补全验证实际行为，不只是核对表结构）。

## 十一、与图书馆式索引的关系与后续计划

- `MemoryStore` 的原始 jsonl 记忆条目保持不变，作为"证据层"不受影响。Wiki 页面是"提炼层"，`source_entries` 字段始终指回原始证据，保证可追溯。
- 旧的 `classification.py`/`entity_index.py`/`catalog.py` 在过渡期并存，新知识双写，待新检索效果验证稳定后逐步下线旧路径——**这一步本次有意保持未完成**：三段式检索刚刚落地，还没有经过任何实际使用周期的 A/B 验证，现在下线旧路径会让"先不替换，AB 对比"失去意义。
- 后续工作方向：实际跑一段时间积累 A/B 数据、根据真实 tag 分布调优专题页生成阈值、评估是否需要支持"更新既有专题页"而不只是生成新的。

## 十二、外部世界知识接入（外部数据知识化计划 P1，本轮新增）

背景：`external_input/` 的 4 个 RSS 源（`channel=agent_watch`）此前只有一条
"命中关键词 → `alerts.jsonl` → 人工点掉即彻底消失"的消费链路，标题背后的
内容从未沉淀进 wiki。P1 补上"看到了"到"记住了"这一步，且**不新建存储/
组织体系**——只是给 `world_writer.py` 增加了一个新的候选来源。

- **新模块** `external_input/knowledge_extractor.py`：新增 cron job
  `sys:external_knowledge_extractor`（`interval:21600`，6 小时一次，与
  `sys:consolidation` 错峰），复用
  `perception/system_events.py::poll_since()` 的游标机制（独立
  `consumer_name="external_knowledge_extractor"`），只处理
  `channel == "agent_watch"` 的 `external.watch.new_item` 事件。
- **抽取**：批量（默认 15 条/批）调用 `LLMHelper.ask()`
  做轻量摘要抽取，产出 schema 与对话侧 `history/world_extraction.py` 完全
  一致的 `entities[]`/`facts[]`，直接复用 `EntityCandidate`/`FactCandidate`
  两个数据结构解析，不新建候选类型。单条解析失败只计数、不阻塞同批其它
  事件。
- **落盘**：调用 `wiki/world_writer.py::queue_entities()`/`queue_facts()`
  时显式传 `source_kind="external_watch"`
  （`world_writer.EXTERNAL_WATCH_SOURCE_KIND`），区别于对话来源的默认值
  `world_model`；真正的判重/新建/合并仍然统一走巩固循环的
  `consolidate_pending()`，不分叉出第二套落盘逻辑。`wiki/stats.py` 的
  `by_source_kind` 统计因此可以直接看到"外部世界知识占比"，无需额外埋点。
- **默认关闭**：daemon 启动时在 `api/server.py` 里补注册该 job（`ensure_job`
  + 首次创建时立即 `disable()`），与改进计划 §4 的"新增 job 先以 disabled
  状态接入，人工评估几天后再手动开启"一致，需要到 Cron Jobs 看板手动启用。
- **尚未实现**（计划里的后续阶段，见
  `next_doc/external_knowledge_wiki_and_self_improvement_plan.md`）：
  P4 外部知识接入自我改进候选生成、P5 更贴合场景的来源类型。
  P3 `sys:tech_radar_search` 已实现，见 §十二·3。

### 十二·2、技术专题页优先聚合（外部数据知识化计划 P2，本轮新增）

P1 长期运行会把每条命中事件都提炼成独立 entity 页面，几个月后积累大量
碎片页面。P2 不新增机制，只是让 P1 的抽取 prompt "先看一眼有没有现成的
专题页可以合并"：

- **专题页种子**：关注领域预先在 wiki 里建好的 `topics/*.md` 页面，直接
  复用 `wiki/topics.py` 现成的生成能力（`generate_topic_page()`/
  `consolidate_topics()`）或手工创建，不是本次改动新增的机制。
- **新函数** `wiki/topics.py::build_topic_digest()`/
  `build_topic_digest_section()`：与 `wiki/entity_digest.py::build_entity_digest()`
  同构，只产出 `专题页 id（label）：一句话摘要` 的极简索引（默认最多 30
  条，按 `updated` 倒序），没有任何专题页时返回空字符串。
- **抽取 prompt 注入**：`knowledge_extractor.py` 每次 run 只扫描一次现有
  专题页，把该索引注入 prompt，并在输出 schema 里新增可选字段
  `topic_id`——模型判断某条资讯明显属于某个已有专题时填这个字段。
- **命中专题**：直接对该专题页 `wiki/writer.py::append_section()` 追加一段
  "外部资讯"记录（标题+摘要+来源链接），**不经过** `world_writer.py` 的
  entity 判重/新建流程，避免同一条信息既进了专题页又单独建了一个 entity
  页面。`topic_id` 在当前专题页集合里找不到匹配（模型幻觉/专题页已删除）
  时静默忽略，退回下面的兜底逻辑。
- **未命中兜底**：没有 `topic_id` 或匹配不到的候选，原样走 P1 既有的
  `queue_entities`/`queue_facts` 流程，不额外新增第二套落盘机制。
- **验收方式**：`wiki/stats.py` 现有的 `by_source_kind` 统计已经能看出
  `external_watch` 类页面的绝对数量增长趋势是否放缓；`external_watch`
  条目里"归入已有专题页 vs 独立新建 entity"的比例目前需要人工核对
  `topics/*.md` 的"外部资讯" section 与 `entities/` 下 `source_kind:
  external_watch` 页面数量，未新增自动化指标（与计划原文"人工审查即可"
  一致）。

### 十二·3、主动检索反哺 wiki（外部数据知识化计划 P3，本轮新增）

P1/P2 打通的是"被动订阅"（RSS 事件）→ wiki 的消费链路；`web_search`
工具此前是纯消耗品——每次调用的检索结果只活在当轮对话里，没有落盘/复用
机制。P3 把它接进同一套落盘管道，变成可复用的投资。

- **新模块** `external_input/tech_radar_search.py`：新增 cron job
  `sys:tech_radar_search`（`interval:86400`，每天一次，与 `sys:self_eval`
  对齐），与事件驱动的 P1 不同，本模块**没有** `system_events.jsonl`
  游标，用独立状态文件 `AgentPaths.external_input_tech_radar_state`
  记录种子轮转 offset。
- **种子来源**：`_collect_seed_pool()` 优先取
  `wiki/gap_scanner.py::scan_gaps()` 已有的知识缺口页面 id，不足部分追加
  `agent_config.json` 里 `tech_radar.keywords` 手工配置的关注关键词
  （去重，前者优先）。
- **频率控制**：每次运行只处理 `tech_radar.daily_seed_limit`（默认 5）个
  种子；种子池更大时按轮转游标滚动处理，循环到末尾自动回绕到开头——几天
  内可以覆盖完整个种子池，而不是每次都只处理最前面那几个。
- **检索**：直接调用既有 `tools/builtin.py::web_search()`，不新增检索
  通道；单个种子检索失败只计数、不阻塞其它种子；全部检索失败或 LLM 调用
  失败时**不推进轮转游标**，下次运行重新处理同一批种子。
- **抽取与落盘**：复用 P1 的 `EntityCandidate`/`FactCandidate` 与
  `wiki/world_writer.py::queue_entities()`/`queue_facts()`，落盘时传
  `source_kind="external_search"`（`world_writer.EXTERNAL_SEARCH_SOURCE_KIND`，
  区别于 P1 的 `"external_watch"`），供 `wiki/stats.py` 的
  `by_source_kind` 统计分别看到"被动订阅"与"主动检索"两类外部知识占比。
- **可追溯性**：每条候选的 `source_entries` 里都带上
  `tech_radar_search:{run_id}:{种子关键词}` 追溯标记，再附加检索结果中
  解析到的真实 URL（最多 3 条），满足"能追溯到是哪次运行、针对哪个种子
  产生"的验收要求。
- **默认关闭**：与 P1 一致，daemon 启动时首次创建该 job 即调用
  `disable()`，需要到 Cron Jobs 看板手动启用。

### 十二·4、外部知识接入自我改进候选生成（外部数据知识化计划 P4，本轮新增）

P1-P3 已经把"外部世界正在发生什么"沉淀进了 wiki，但 `soft_goal_deriver.py`
原有的四路信号采集全部来自系统内部状态，没有一路桥接"这条外部知识是否
值得作为一个改进方向"。P4 补上这个桥接，且明确"只产出草稿供人工审核"。

- **新模块** `evolution/external_trend_capability_link.py`：新增 cron job
  `sys:external_trend_capability_link`（`interval:604800`，每周一次，与
  `sys:decision_profile_update` 对齐）。
- **数据源**：`source_kind` 属于 `external_watch`/`external_search`
  的 wiki 页面 + `capability_map` 中 confidence 低或 total_calls 极少
  的能力条目（阈值与 `soft_goal_deriver.py` 保持一致）。
- **匹配**：用 LLM 做一次轻量匹配，产出的候选要求 `capability_domain`/
  `wiki_page_ids` 必须真实来自输入数据，不满足的候选事后过滤，不完全
  信任 LLM 自称的引用。
- **去重**：同一 (能力域, wiki 页面 id 集合) 组合 14 天内不重复产出。
- **落点（两处，均不直接建 Goal / 不自动改代码）**：
  1. 结构化候选写入状态文件，供
     `evolution/soft_goal_deriver.py::SoftGoalDeriver._from_external_knowledge()`
     消费——这一路新信号进入既有的 `_DeriveCandidate`/
     `derive_candidates()`/`commit_goals()` 流程（`source_tag=
     "external_knowledge"`，在 `commit_goals()` 里被标记
     `needs_review`，与 workthread/lesson 两路一致），仍然遵循
     "autonomous 档位下才 derive、其余档位只记录不生成"的既有规则。
  2. 人类可读草稿写入 `.agent/wiki/external_trend_capability_candidates.md`
     （`AgentPaths.external_trend_capability_candidates_path`），格式
     与 `decision_profile_builder.py::_write_profile_md()` 一致，人工
     审核后再决定是否实施。
- **默认关闭**：与 P1/P3 一致，daemon 启动时首次创建该 job 即调用
  `disable()`，需要到 Cron Jobs 看板手动启用。

## 十三、外部知识反馈闭环补充（外部知识反馈闭环计划 P1/P2/P5，本轮新增）

上一节 §十二 打通的是"外部事件/检索 → wiki 沉淀 → 自我改进候选"的
生产链路；本节三项补的是"只生产、不巡检/不回看"的空隙——不新增数据源，
在既有链路上补一层巡检-统计-回看。

### 十三·1、人工候选队列过期巡检（P1）

`NoveltyJudge` Stage②产出、等待人工在看板"🌟 新颖信号候选"面板确认/
忽略的 `.agent/notification/novelty_candidates.jsonl`，此前没有任何时间
维度的过期机制——`pending` 状态的候选会无限期挂着。`evolution/
candidate_queue_triage.py` 新增 cron job `sys:candidate_queue_triage`
（`interval:86400`，每天一次，零 LLM 成本，默认 enabled），把超过 30 天
（`STALE_PENDING_TTL_SECONDS`）仍是 `pending` 的记录状态改写为
`"expired"`（不是 `"dismissed"`——保留"人工主动忽略"与"系统因超时自动
降级"两种语义的区分），不删除记录、不影响 `confirmed`/`dismissed` 状态
的记录。详见 `docs/watchlist-notification-guide.md` §6.4、
`next_doc/external_knowledge_feedback_loop_improvement_plan.md` P1。

### 十三·2、wiki 页面利用率审计（P2，统计层）

wiki 页面写入后此前没有机制追踪其是否真的被检索/引用过，`gap_scan`
只判断"内容薄不薄"，不判断"有没有被用上"。`wiki/search.py::
wiki_shelf_search()` 的两处返回点新增轻量埋点（无命中不记录、失败静默
吞掉，不影响检索主流程），追加写入 `AgentPaths.wiki_usage_log_path`
（`.agent/wiki/usage_log.jsonl`）。`evolution/wiki_utility_audit.py`
新增 cron job `sys:wiki_utility_audit`（`interval:604800`，每周一次，
零 LLM 成本，默认 enabled），把最近 30 天的埋点聚合为每页
`hit_count`/`grounded_count`/`last_used_at`，落盘
`.agent/wiki/usage_stats.json`（`load_wiki_usage_stats()` 只读加载），
同一次运行顺带修剪超过 90 天的日志记录。**本次只做统计层**，不改
`gap_scanner.py`/`decommission.py` 的判断逻辑——先让统计跑一段时间、
看到真实的利用率分布形态后再决定去留权重怎么定。详见
`next_doc/external_knowledge_feedback_loop_improvement_plan.md` P2。

### 十三·3、月度战略回顾（P5）

`daily_digest`（天）、§十二·4 `external_trend_capability_link`（周）
之外，此前没有更高层的、跨越数周的综合回看。`evolution/
monthly_trend_retrospective.py` 新增 cron job
`sys:monthly_trend_retrospective`（`cron:0 0 1 * *`，每月 1 日一次，
零 LLM 成本，默认 enabled——纯规则聚合已有状态文件，不需要人工前置
配置），每月汇总三路信号：

1. **候选采纳情况**：过去 4 周 `external_trend_capability_link`
   （§十二·4）产出的候选中，有多少条对应的能力域已经被采纳成 Goal
   （跟 `GoalBacklog` 现存目标标题匹配）。
2. **wiki 专题页增长**：复用 `wiki/stats.py::compute_stats()` 的
   `by_source_kind` 快照，与上一轮运行保存的快照做差值，看各类外部
   知识页面这个周期内各新增了多少。
3. **能力变化趋势**：复用 `evolution/consolidation.py::
   load_capability_map()`，与上一轮保存的 `domain -> confidence`
   快照做差值，列出变化幅度最大的 Top 10 能力域。

产出只有一份人类可读文档
（`.agent/wiki/monthly_trend_retrospective/<YYYY-MM>.md`），不产出结构化
候选、不接入任何下游自动消费链路，供 `decision_profile_update`/
`soft_goal_deriver` 人工参考，不自动创建 Goal、不自动修改代码。首次
运行（无上一轮快照可比）时增长/变化会把全量值当作"从无到有"展示，
属于预期行为。详见
`next_doc/external_knowledge_feedback_loop_improvement_plan.md` P5。

## 十四、wiki 问题页面检测与自动修复（本轮新增）

`wiki/stats.py::compute_stats()` / `wiki/indexer.py::build_index()`
遇到解析失败的页面（frontmatter 缺字段、`links` 格式不对等
`PageParseError`）此前只是 `log_exception` 后跳过——问题数据会一直
留在磁盘上，每次扫描都重新触发同一条异常日志，没有持久化记录，也没有
任何自动修复机制。

`wiki/quarantine.py`（发现 + 记录）与 `wiki/quarantine_repair.py`
（修复策略 + 修复循环）补上这两块空白：

- **发现与记录**：`quarantine.record_issue()` 在解析失败时写一条
  `QuarantineRecord`（页面路径、错误类型/信息、状态、检测次数、修复
  尝试次数）到 `.agent/wiki/_quarantine.json`（整表 JSON，同类小文件
  参考 `usage_stats.json` 的写法），同一页面重复检测到同一问题只合并
  计数，不重复建记录。`compute_stats()`/`build_index()` 的解析失败
  分支都已接入。`quarantine.scan_and_record()` 提供一个独立的全量扫描
  入口（这是因为 `build_index()` 不是每次都会被调用，"发现问题"这个
  机制需要一个稳定的周期性触发点），扫描时还会对隔离区里已有记录、但
  现在能正常解析的页面做"自愈确认"（可能是人工手动修好的）并自动
  摘除记录。
- **自动修复**：`quarantine_repair.py` 用一个显式的修复策略注册表
  （`_FIXERS`），只处理"确定是数据笔误、改法唯一"的情况，不做任何
  语义猜测。首批两条策略均来自真实故障（`frontmatter.links` 写成裸
  字符串列表，如 `links: [tushare]` 而不是
  `links: [{target: tushare}]`）：`links` 列表内的字符串项 →
  `{"target": 字符串}`；`links` 整个字段没包成列表 → 包一层。后续又
  补了一条 `_fix_missing_id_and_type`：`id`/`type` 是 `parse_page()`
  唯一强制的两个必填字段，但落盘约定（`wiki/writer.py`：文件名固定为
  `<page_id>.md`，存放目录由 `type` 唯一决定）已经把这两个值写在
  `page_path` 本身里了，反推回去即可，不涉及编造内容，属于跟
  `_fix_string_links` 同等级的"确定改法"——这条修复上线前，缺
  `id`/`type` 的页面因为 LLM 兜底也明确拒绝"编一个 id"而永远修不好，
  常年积压在 `needs_human` 里。每次修复后都会重新完整 `parse_page()`
  验证，确认真的能通过校验才落盘，半吊子的修复（改完还是解析失败）
  不写文件。单个页面自动修复尝试超过 `DEFAULT_MAX_REPAIR_ATTEMPTS`
  （默认 5 次）仍未成功，状态转 `needs_human`，不再参与后续自动修复
  循环，避免对一份自动策略解决不了的坏数据每个 cron 周期都重复尝试。
- **LLM 兜底修复（opt-in，默认关闭）**：规则策略只覆盖
  "改法唯一"的已知故障模式，遇到没见过的结构性问题（YAML 语法错误、
  字段缺失/拼写错误等）只能转 `needs_human`。`MemoryConfig.
  wiki_quarantine_llm_repair_enabled=True` 时，规则修复兜底失败的页面
  会额外尝试一次 LLM 修复——复用 `llm_helper` opt-in 模式，把原始页面
  文本 + 错误信息交给模型，只要求它修正导致解析失败的结构性问题，不碰
  正文/不臆造信息。跟规则修复共用同一个"改完必须重新通过 `parse_page()`
  才落盘"的校验闸门，且额外要求输出必须是带 frontmatter 的完整页面
  （否则判定失败，不落盘）；LLM 调用异常/无输出/结果仍解析失败都当作
  这次修复未成功，转下一轮或最终 `needs_human`，不会比规则修复更
  "激进"。修复成功时 `repaired_by` 记为 `llm_repair`，跟规则策略名区分
  以供追溯。喂给模型的 prompt 里附带了 frontmatter 的必填字段/合法取值
  （`PAGE_TYPES`/`STATUS_VALUES`）说明，以及从文件名/所在目录机械推导出
  的 `id`/`type` 建议值（跟 `_fix_missing_id_and_type` 同一套推导逻辑，
  不是猜测），减少模型选错枚举值或编错 `id` 的概率。
  **整篇缺失 `---` frontmatter 块**（`no_frontmatter_block`，本次修复）
  这类此前会直接跳过、不给 LLM 机会的情况，现在也会走到这条兜底分支——
  之前 `attempt_repair_page()` 一旦匹配不到 frontmatter 就直接
  `return`，即使调用方传了 `llm_helper` 也用不上；现在改成只记录
  `rule_reason="no_frontmatter_block"`、继续往下走，让模型从正文内容
  （配合上面的 `id`/`type` 强提示）重新搭出一段合法 frontmatter。
- **cron 接入**：`sys:wiki_quarantine_repair`（`interval:21600`，每 6
  小时一次，本地回调 handler，默认 enabled）跑
  "全量扫描 + 对 pending 记录逐个尝试修复"的完整循环，即
  `run_quarantine_repair_cycle()`。daemon 启动时在 `api/server.py`
  里补注册（跟 `sys:wiki_utility_audit` 同构写法），`llm_helper`
  惰性获取（handler 触发时才按 `wiki_quarantine_llm_repair_enabled`
  决定要不要拿 `agent.llm_helper`），开关关闭时零 LLM 成本，行为与
  改动前完全一致。
- **CLI**：`/wiki quarantine`（等价于 `/wiki quarantine list`）展示
  当前 pending/needs_human 记录；`/wiki quarantine repair` 手动触发
  一轮扫描+修复，跟 cron job 跑的是同一份逻辑（含同一个 LLM 兜底
  开关），用于不想等定时任务、想立刻看到修复结果的场景。`needs_human`
  状态的记录人工改好对应文件后，下次扫描（cron 或手动 `repair`）会
  自动确认并摘除，不需要额外的"标记已处理"操作。
- **重试已转人工的记录（`retry`，本次新增）**：`repair`/cron 只处理
  `status == pending` 且 `repair_attempts < DEFAULT_MAX_REPAIR_ATTEMPTS`
  的记录——一旦某条记录之前因为"没有匹配策略"或"尝试次数耗尽"转成了
  `needs_human`，即使后来新增了能救回它的修复策略（比如上面的
  `_fix_missing_id_and_type`），它也不会被自动重新捡回来，永远停在
  `needs_human`，这正是这批"缺 id/type"页面长期积压 44 条不动的原因。
  `quarantine.reset_to_pending()` 把指定状态（默认只筛 `needs_human`）
  的记录重置为 `pending`、`repair_attempts` 清零；CLI `/wiki quarantine
  retry` 调用它之后立即触发一轮 `run_quarantine_repair_cycle()`，一步
  做完"重置 + 用最新策略重新尝试"。跟 `purge` 一样只信任隔离区记录里的
  路径、不会碰任何当前是 `pending`/`repaired` 状态的记录（除非显式传
  别的 `statuses`）。
- **清理（`purge`，本轮新增）**：规则/LLM 修复都要求"改完必须重新
  通过 `parse_page()` 才落盘"，遇到结构性缺失（比如整篇没有 `---`
  frontmatter 块）这类连基本结构都不具备的坏数据，两条修复路径都无能
  为力，只能长期停在 `needs_human`。`quarantine.purge_quarantined()`
  提供直接删除的出口：只删除隔离区里**已有记录**的页面（磁盘文件 +
  隔离区记录一起清），不会碰任何能正常解析的 wiki 页面，也不读取/校验
  页面内容本身，只信任隔离区记录里的路径。CLI 是
  `/wiki quarantine purge [--status pending|needs_human|repaired|all]
  [--path <page_path>]... [--yes]`：默认只筛 `needs_human`（自动修复
  已放弃、最典型的"确定没救"的一批）；不加 `--yes` 时只预览命中数量、
  不做任何写操作，跟 `/agent goals spec confirm` 的两步确认惯例一致，
  确认无误后加 `--yes` 才真正执行删除，不可恢复。

当前局限：修复策略目前只覆盖 `links` 字段的两类已知笔误，其它类型的
`PageParseError`（比如缺失必填字段、YAML 语法本身损坏）会被记录但暂时
没有对应的自动修复策略，停在 `pending` 状态直到人工介入、后续版本补充
新的修复策略，或者判断这批数据本身不值得保留后用 `purge` 直接清掉；
YAML 语法损坏这类"没有确定改法"的问题被有意排除在自动修复范围之外，
不做猜测性修复。

## 相关文档

- 项目根目录 `next_doc/external_knowledge_wiki_and_self_improvement_plan.md` — 外部数据知识化与自我改进闭环 P1-P5 的完整设计动机与实现记录（本节对应 P1）
- 项目根目录 `next_doc/external_knowledge_feedback_loop_improvement_plan.md` — 外部知识反馈闭环 P1-P5 的完整设计动机与实现记录（本文档 §十三 对应 P1/P2/P5，P3 见 `docs/watchlist-notification-guide.md` §6.1，P4 见 `docs/external-input-gateway-guide.md` §11.2）

- [图书馆式知识索引指南](library-index-guide.md) — 旧的分类树/实体索引/两步检索系统，仍是当前的主索引
- [巩固循环 后台循环指南（Stage 8）](self-evolution-consolidation-guide.md) — `consolidate()` 挂载的完整巡检流程
- 项目根目录 `next_doc/wiki-style-knowledge-base-refactor-plan.md` — 阶段一~四的完整设计动机、阶段划分与逐条实现记录
- 项目根目录 `next_doc/wiki-style-knowledge-base-improvement-plan.md` — P0-P4 的完整设计动机与实现记录
- 项目根目录 `next_doc/wiki-knowledge-base-extraction-and-organization-plan.md` — O1-O4、E1-E3 的完整设计动机与问题分析（§十涉及部分的原始设计文档）
- 项目根目录 `next_doc/wiki-extraction-layer-plan-o1-record.md` ~ `_O4实施记录.md`、`_E1实施记录.md` ~ `_E3实施记录.md`（含 E2 方案B专项记录）— 每一项的详细实施记录、与原计划的差异说明、验收方式
- 项目根目录 `next_doc/wiki_next_phase_improvement_plan.md` — 退轨评估 / 专题页退场 / 巩固分步熔断 / 世界知识独立触发 / daemon 定时任务的完整设计动机与实施状态总览
- 项目根目录 `next_doc/wiki_next_phase_implementation_record.md` — 上述改进的逐文件改动清单、设计决策修正、测试验证记录

---

*首次编写：2026-07（wiki 式知识库阶段一~四：md 页面存储 + 双写镜像 + 三段式检索 + 专题页生成 + `/wiki` 命令）*
*更新：2026-07（提取层与组织层改进计划 O1-O4、E1-E3 全部完成：索引复用与信度分层、实体摘要反哺抽取、抽取与 compact 解耦、抽取任务拆分、多跳图扩展、topic 再巩固、统一知识生命周期状态机）*
*更新：2026-07（下一阶段改进：退轨评估 `wiki/decommission.py`、专题页退场标注、`consolidate()` 分步超时熔断、`entity_density` 独立触发信号、`/wiki gap-scan`/`fallback-cleanup` 新命令、daemon 新增 2 个内置 cron job；并补上此前遗漏的 `/wiki` 命令行 Tab 补全提示）*
*更新：2026-08（外部知识反馈闭环计划 P1/P2/P5：候选队列过期巡检 `sys:candidate_queue_triage`、wiki 利用率审计 `sys:wiki_utility_audit`（统计层）、月度战略回顾 `sys:monthly_trend_retrospective`，见新增 §十三）*

