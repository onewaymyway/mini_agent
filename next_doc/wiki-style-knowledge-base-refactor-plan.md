# Wiki 式知识库重构计划

## 一、设计理念

知识库的价值不在于"存了多少条"，而在于两件事：**能不能被人直接读懂**，**知识之间的关系能不能被显式表达**。

现有的"图书馆模式"（分类树 + 实体索引 + 目录）本质上是在优化"归类"这一件事——一条记忆挂在哪个书架、挂在哪个实体下。但工程知识很少是孤立的：一个模块的设计离不开它依赖的模块、它取代的旧方案、它引出的新问题。图书馆模式能回答"关于 X 有哪些记录"，回答不了"X 和 Y 是什么关系""这个方案是不是已经被否决过一次了"。

新方案的核心理念：

1. **知识以 md 文件为唯一真相**，人可以直接打开、直接读、直接手改，不需要通过工具才能查看知识内容。
2. **页面之间的关系是一等公民**，不是挂载关系的副产品。`depends_on`、`supersedes`、`part_of`、`conflicts_with` 这些关系本身就是知识的一部分。
3. **索引全部是可重建的编译产物**，可以随时删除、随时用脚本从 md 重新生成，不会因为索引损坏丢失任何真实信息。
4. **不同类型的知识用不同的结构组织**，而不是用同一套"摘要字符串"字段硬套所有场景。

## 二、当前现状

现有实现分布在 `src/mini_agent/perception/` 下：

- `classification.py`：分类树（书架），单棵树，一条记忆挂一个分类号（code），支持规则分类、LLM 分类、节点生长与合并。
- `entity_index.py`：实体索引，`Entity` 对象持有 `related_entry_ids`（指回原始记忆）、`category`（指向分类树节点）、`summary`（滚动重写的单一摘要字符串）、`superseded_notes`（最近 5 条被推翻的旧结论）。
- `catalog.py`：分类号到记忆条目的指针索引，以及编年时间线（`knowledge_timeline`）。
- `library_index.py`：把上述三者串起来的门面（Facade），提供 `on_new_entry`、`shelf_search`（两步检索：先定位书架再精排，候选不足时回退全库搜索）、`consolidate`（批量巩固：分类树生长/合并、实体摘要重写、实体去噪/近重复合并）。

底层的原始记忆条目（`MemoryEntry`）存放在 `MemoryStore`（jsonl），这一层保持不变，是所有知识的证据来源，不在本次重构范围内。

## 三、当前问题

对照代码逐条列出：

1. **Entity 之间没有边。** `Entity` dataclass 里没有任何字段表达"这个实体和那个实体是什么关系"，唯一的关联方式是"共享同一个 `category`"，这是一种非常粗的弱关联（同一个书架下可能有几十个毫不相关的实体）。
2. **summary 是滚动覆盖的单一字符串。** `rewrite_summary` 每次重写都是把新证据和旧 summary 一起丢给 LLM，生成一段新文字整体替换旧的。正常演变的历史细节会随着多次重写被逐渐稀释，只有明确冲突时才会被写进 `superseded_notes`（且只保留最近 5 条）。
3. **分类是单一归属，无法表达跨类目知识。** 一条记忆只能挂一个 `category`，现实中一个 bug 修复往往同时属于"并发问题"和"某个具体子系统"两个维度，现有结构只能二选一。
4. **没有可读的综合层。** 分类树节点只是指针容器，不是内容页；一次跨多个模块的大重构（比如判断/调度系统的整合）目前没有任何地方能承载"这件事的完整来龙去脉"，只能靠人去几个实体的 summary 里拼凑。
5. **知识不可直接阅读和编辑。** 所有内容都在 JSON/JSONL 里，人工想看/改一条知识必须通过命令行工具，无法像打开一份文档一样直接浏览。
6. **反馈状态是单例全局变量。** `LibraryIndex._last_shelf_code` 是实例上的单一属性，`record_retrieval_feedback` 依赖它兜底判断"上一次命中的是哪个书架"，在多会话并发场景下存在互相覆盖、反馈错位的风险。
7. **实体去重靠字符串相似度。** `consolidate_entities` 用 `difflib.SequenceMatcher` 比较实体名字符串相似度来判断是否为同一实体，对语义相近但字面不同的情况（比如中英文别名、缩写）识别能力有限。

## 四、为什么要构建新方案

图书馆模式的分类树，本质上假设"每条知识只有一个最合适的位置"，这个假设在纸质图书馆里成立（一本书只能放一个书架），但在知识图谱场景里是错的——软件工程知识天然是网状的，不是树状的。继续在分类树上打补丁（多标签、次要标签等）只是在一个不匹配的模型上做修修补补，边际收益会越来越低。

同时，现有系统对"人"不友好——知识全部封装在工具接口后面，想直接看一眼某个模块的历史沿革，必须走 CLI 命令或者读 JSON 源文件，这和"知识库应该是可以被浏览、被信任、被直接编辑"的直觉是冲突的。参考成熟的 wiki 系统（无论是传统 MediaWiki 还是近年的 LLM/Agent 知识库实践）验证过的模式——**内容用人类可读的文本格式存储，关系用显式链接表达，索引作为可随时重建的派生层**——能同时解决"关系表达能力不足"和"不可直接阅读"这两个问题，而且这套模式本身经过了长期验证，不是需要从零摸索的新范式。

## 五、新方案设计

### 5.1 目录结构

```
.agent/wiki/
├── entities/     # 实体型：模块、工具、bug 模式、外部依赖
├── decisions/    # 决策型：某个取舍为什么这么定（见配套文档）
├── processes/    # 流程型：怎么做某件事的标准步骤
├── experiences/  # 经验型：非正式的踩坑总结、直觉性知识
├── topics/       # 专题页：聚合多篇页面的综合叙事
└── _index/       # 全部为脚本生成的派生产物，可随时删除重建
    ├── graph.json          # 页面间链接图
    ├── tags.json           # tag -> 页面列表
    ├── backlinks.json      # 反向链接
    └── search_index.json   # 关键词 + 向量粗筛索引
```

> **实现记录**：`AgentPaths`（`storage/paths.py`）新增了 `wiki_dir` 及以上全部子目录/索引文件的属性，`ensure_wiki_dirs()` 一次性创建全部目录。另外实际实现中还有一个不在上面目录树里的文件——`wiki/_migration_map.json`，用于维护旧 `entity_index.py` 的 `entity_id` 到 wiki `page_id` 的映射。它特意没放进 `_index/`：`_index/` 下的文件全部"可随时删除重建"，但这个映射文件是双写路径依赖的持久状态，删掉它会导致同一个旧实体在下次镜像时被当成"第一次出现"重新建一篇新页面，所以单独放在 `wiki/` 根目录下，语义上更接近"数据"而不是"索引"。

### 5.2 页面格式：frontmatter + 结构化正文

所有页面共享统一的 frontmatter 骨架，正文 section 因类型而异：

```yaml
---
id: role-agent-dispatcher
type: entity          # entity | decision | process | experience
tags: [judge-system, dispatcher, phase6]
status: active         # active | deprecated | superseded | revisited
confidence: 0.8
created: 2026-06-01
updated: 2026-07-10
links:
  - target: turn-judge
    relation: absorbs
    note: "Phase6b将TurnJudge的职责迁移至此"
source_entries: [entry_a1b2, entry_c3d4]   # 指回原始 MemoryStore 记忆，可追溯
---
```

正文内的 `[[page-id]]` 语法用于自然行文中的弱引用（自动被解析为 `relation: mentions`），frontmatter 的 `links` 字段表达结构化的强关系。两类链接并存，弱链接兜底人工书写时遗漏的关联，强链接支撑图遍历。

> **实现记录**：`wiki/parser.py` 里 `WikiLink.source` 字段区分 `"frontmatter"`（强关系）与 `"body"`（弱引用），`WikiPage.strong_links()` / `weak_links()` 两个方法分别取用。若同一个 target 既被 frontmatter 声明为强关系、又在正文里被 `[[..]]` 提及，解析时会丢弃重复的弱引用，只保留强关系，避免图上出现同一对节点间的两条冗余边。另外实现中允许 frontmatter 携带核心字段之外的附加字段（比如迁移脚本写入的 `legacy_entity_id`），解析时原样保留在 `WikiPage.raw_frontmatter` 里，不会因为出现未预期字段而报错——这是为过渡期的双写场景留的口子。

### 5.3 解析与索引：独立的 `wiki/` 模块

新增 `src/mini_agent/wiki/`：

- `parser.py`：解析单个 md 文件（frontmatter + 正文 + 正文内 `[[link]]`）
- `graph.py`：汇总全部页面的 links，构建内存图结构（`dict[id, list[edge]]`，不引入额外依赖）
- `indexer.py`：遍历 `wiki/` 目录，生成 `_index/` 下四个派生文件；支持增量模式（对比 mtime/hash，只重新解析改动过的文件）
- `writer.py`：新建/更新页面，复用现有的原子写模式（tmp + `os.replace`）
- `validator.py`：校验 frontmatter 必填字段、`type` 枚举合法性、`links.target` 是否指向真实存在的页面（防止死链）

向量索引复用现有的 `perception/local_embedding.py`，不新增向量化能力。

> **实现记录 / 与原计划的偏差**：这一条在原计划里写的是"向量索引复用 `local_embedding.py`"，实际实现下来做了一个调整——**默认不启用 embedding**。`wiki/indexer.py` 生成的 `search_index.json` 目前只是关键词倒排索引（复用一套极简分词：英文按 word boundary、中文按单字），不含向量。更关键的是，`wiki/dedup.py`（页面相似度判断，主要用于 `consolidate()` 判断"这条更新该并入哪篇既有页面"）的**默认方案改成了规则打分 + LLM 兜底确认**，`local_embedding.py` 的余弦相似度只作为一条可选路径保留（`find_similar_page_embedding`，需要调用方显式传入 `embed_call` 才会启用）。原因见 5.4 节的实现记录。

### 5.4 检索：三段式，规则粗筛 + 图扩展 + LLM 精排

1. **规则粗筛（零 LLM 成本）**：tag 匹配 + 关键词倒排 + 本地向量粗筛，拿到 top-N 候选（N 可以给到 20~30，因为这一步很便宜）。
2. **图扩展（零 LLM 成本）**：命中页面的 `links` 展开一跳，把结构化相关的页面（依赖/取代/因果关系）自动带入候选池。这是相对分类树检索的核心增量能力。
3. **LLM 精排**：候选收窄到 5~8 篇后，把**完整正文**（不是摘要）一起交给 LLM 排序并生成综合回答，同时要求标注"回答主要基于哪几篇页面"，供后续反馈定位到具体页面（解决现有 `_last_shelf_code` 单例全局状态的反馈错位问题）。

> **实现记录**：三段式检索本身（阶段三）尚未实现，还停留在计划阶段。但第 2 步"图扩展"用到的底层能力已经在阶段一随 `graph.py` 一起落地了——`GraphIndex.expand(page_ids, strong_only=...)` 对命中页面做一跳扩展，`strong_only=True` 时只走 frontmatter 强链接、不走正文 `[[..]]` 弱引用，就是为了避免真正接入检索时被泛泛的 `mentions` 关系稀释候选质量。等阶段三实现三段式检索时可以直接调用这个方法，不需要重新设计图遍历逻辑。
>
> 另外，"规则粗筛"和"LLM 精排"之间原计划隐含的路径是"本地向量粗筛 → 差得多的候选靠 LLM 精排兜底"。目前 `wiki/dedup.py` 已经落地的规则打分 + LLM 确认（用于 `consolidate()` 的判重场景）验证了一版不依赖向量的组合思路是可行的——tag 重合度 + 关键词 Jaccard 相似度打分，分数落在中间不确定区间时才问一次 LLM 做 YES/NO 确认（只问排名第一的候选，不是每个候选都问，避免调用数量随候选数线性增长）。阶段三实现检索的"规则粗筛"步骤时，可以复用同一套分词与打分逻辑，而不一定需要先跑通向量索引。

### 5.5 与现有系统的关系

`MemoryStore` 的原始 jsonl 记忆条目保持不变，作为"证据层"不受影响。Wiki 页面是"提炼层"，`source_entries` 字段始终指回原始证据，保证可追溯。旧的 `classification.py`/`entity_index.py`/`catalog.py` 在过渡期并存，新知识双写，待新检索效果验证稳定后逐步下线旧路径。

> **实现记录**：双写路径已经在阶段二落地。`LibraryIndex.__init__` 新增了可选参数 `wiki_paths: Optional[AgentPaths] = None`，默认 `None`——不传就是纯旧行为，零改动、零额外依赖。传入后：`on_new_entry` 挂载实体后会尝试把实体镜像进 `wiki/entities/*.md`；`mark_stale_from_correction` 标记某实体 `superseded` 时，会同步把新状态写回对应 wiki 页面的 frontmatter；`consolidate()` 每轮重写过摘要的实体也会被镜像。所有镜像动作都包在 `try/except` 里，失败只是"少镜像一次"，不会让分类树/实体索引/编年目录这些主索引的写入跟着失败——wiki 目前的定位仍然是"镜像层"，不是"真相来源"。

## 六、具体改进计划

### 阶段一：基础设施（不影响现有功能）✅ 已完成

- [x] 新建 `src/mini_agent/wiki/` 模块骨架：`parser.py`、`graph.py`、`indexer.py`、`writer.py`、`validator.py`
- [x] 定义四种类型的 frontmatter schema 与 `_templates/` 模板（另外补了一份 `topic.md` 模板，供阶段四专题页生成时直接用）
- [x] 实现 md 文件的原子写（复用现有 `_atomic_write_*` 模式：tmp + `os.fsync` + `os.replace`）
- [x] 实现 `indexer.py` 的全量重建逻辑，跑通"手写几篇 md → 生成 `_index/`"的最小闭环（含增量模式：`_manifest.json` 记录 mtime + hash，未改动文件跳过重新解析）

### 阶段二：迁移与双写 ✅ 已完成

- [x] 写一次性导出脚本：`wiki/migration.py::migrate_entity_store()`，把 `entity_index.py` 里现有的实体逐个转换成 `entities/*.md`，`summary` 映射到"当前状态"section，`superseded_notes` 映射到"历史沿革"section，`related_entry_ids` 映射到 `source_entries`。脚本可重复运行，只处理映射表里还没记录过的新实体（增量迁移）。
- [x] `library_index.on_new_entry` 增加双写路径：命中已有实体页则追加"历史沿革"，命中不到则新建页面（`wiki/migration.py::mirror_entity()`，`on_new_entry` 与 `consolidate()` 共用同一个函数）。
- [x] `consolidate()` 增加新增步骤（步骤 5）：候选批量生成新页面、页面相似度判断改为**规则打分 + LLM 确认**（`wiki/dedup.py::find_similar_page_rules`），**不是**原计划设想的 embedding——替代 `difflib` 字符串相似度的目标达成了，但具体实现路径变了，理由见下方"与原计划的偏差"。embedding 方案（`find_similar_page_embedding`）作为可选路径保留，调用方显式传 `embed_call` 才会启用。

> **与原计划的偏差：为什么去重方案默认不用 embedding**
>
> 原计划 5.3/6 节设想的是复用 `perception/local_embedding.py` 做向量相似度。实际落地时改成默认规则 + LLM，主要考虑：
> 1. `local_embedding.py` 依赖本地 embedding 模型加载（onnxruntime），是一个相对重的可选依赖，而阶段一明确要求"不影响现有功能"——如果 `consolidate()` 的默认路径就要求这个依赖可用，等于把一个可选功能变成了隐性必需项。
> 2. 规则打分（tag 重合度 + 关键词 Jaccard）在"同一实体反复被提及"这种最常见的场景下已经够用；真正语义相近但字面差异大的情况（中英文别名、缩写）交给 LLM 兜底确认，比无差别上向量模型更省资源，且不需要额外的模型下载/加载步骤。
> 3. 保留了 embedding 作为可选路径（`wiki_embed_call` 参数），需要更强语义召回能力的场景仍然可以手动接入，两条路径的阈值、打分方式都在 `wiki/dedup.py` 里显式区分，不是简单的"能力降级"。
>
> 详见 `wiki/dedup.py` 顶部注释里的完整设计说明。

### 阶段三：检索切换 ✅ 已完成（AB 并行部分）

- [x] 实现三段式检索（规则粗筛 → 图扩展 → LLM 精排），作为 `shelf_search` 的平行实现，先不替换，AB 对比效果
- [x] 实现 `indexer.py` 增量模式，接入巩固循环触发点
- [x] 实现 backlinks 重建（`consolidate` 增加第六步：扫描全部 links 反向构建索引）

> **实现记录**：新增 `wiki/search.py::wiki_shelf_search()`，三段严格对应 5.4 节：
> 1. 规则粗筛复用 `wiki/indexer.py::_tokenize` 与 `wiki/dedup.py` 同一套 tag 重合度 + 关键词 Jaccard 加权打分（`_RULE_TAG_WEIGHT=0.4` / `_RULE_TOKEN_WEIGHT=0.6`，与 dedup.py 权重保持一致），取 top `tag_top_n`（默认 25）篇候选。
> 2. 图扩展直接复用阶段一已经实现的 `GraphIndex.expand(strong_only=True)`，把候选的 frontmatter 强链接展开一跳。
> 3. LLM 精排：候选收窄到 `rerank_top_n`（默认 8）篇后，把完整正文交给 `llm_call`，要求回答后另起一行以「基于页面:」标注依据的页面 id，解析进 `WikiSearchResult.grounded_page_ids`——对应 5.4 节"供后续反馈定位到具体页面"。没有传 `llm_call` 时止步于第二段，返回图扩展后的候选（`stage_reached="rule"|"graph"`）。
>
> 通过 `LibraryIndex.wiki_search(query, k, llm_call, tags)` 暴露，`wiki_paths=None` 时返回空结果（调用方应回退到 `shelf_search`），与旧的两步检索完全并存、互不影响，符合"先不替换，AB 对比"的要求。
>
> `consolidate()` 新增步骤 6：只在步骤 5（wiki 镜像/判重合并）本轮实际产生了任何写入时才触发 `wiki/indexer.py::build_index(incremental=True)`，避免每次巩固循环空跑一次全库解析；重建内容含 `backlinks.json`（`GraphIndex.backlinks_to_dict()` 阶段一已实现，这里只是把"手动跑一次"变成"巩固循环自动触发一次"）。重建失败被吞掉，不影响巩固循环主统计——与步骤 5 wiki 镜像同样的"锦上添花不阻断主流程"原则。返回值新增 `wiki_index_rebuilt` / `wiki_pages_indexed` 两个字段。
>
> **遗留问题的顺带修复**：核对代码时发现 `wiki_paths` 参数虽然在阶段二就加进了 `LibraryIndex.__init__`，但 `perception/memory_factory.py::_build_library_index()` 从未真正传过这个参数——也就是说阶段二"双写路径已经在阶段二落地"这句话只在单元测试/手动构造 `LibraryIndex` 时成立，实际跑起来的 agent 里 `wiki_paths` 永远是 `None`，双写代码路径从未被触发过。这次顺手把接线补上：`MemoryConfig` 新增 `wiki_enabled: bool = True` 总开关，`_build_library_index()` 据此传入 `wiki_paths=paths if wiki_enabled else None`。默认开启，关闭后完全回到"wiki/ 目录不被触碰"的旧行为，风险可控。

### 阶段四：专题页与收尾 ✅ 部分完成

- [x] 巩固循环中加入"专题页生成"判断：分类下实体数达到阈值且 links 密度较高时，触发 LLM 聚合成 `topics/*.md`
- [ ] 验证新检索路径效果稳定后，下线 `classification.py`/`catalog.py` 旧路径，`entity_index.py` 保留但仅作为历史数据参考 —— **有意保持未完成**：这一条的前提是"验证新检索路径效果稳定"，三段式检索本次只是刚刚落地、尚未经过任何实际使用周期的 A/B 验证，现在下线旧路径会让"先不替换，AB 对比效果"（阶段三第一条的明文要求）失去意义。旧路径（分类树/实体索引/编年目录）保持不动，等 `wiki_search()` 实际跑过一段时间、有数据支撑效果对比后再回来做这一条。
- [x] 补充 `/wiki <page-id>` 类 CLI 命令，供人工直接浏览页面及其 backlinks

> **实现记录**：新增 `wiki/topics.py`。`find_topic_candidates()` 按 tag 对全部非 topic 类型页面分组，页面数达到 `min_pages`（默认 4）且组内 frontmatter 强链接密度（组内边数 / 组内页面数）达到 `min_density`（默认 0.5）才算候选；已经生成过专题页的 tag（读取既有 `topics/*.md` frontmatter 里的 `source_tag` 附加字段）会被排除，避免同一批页面反复触发生成。`generate_topic_page()` 把候选组全部正文交给 LLM 综合改写成一篇叙事，写入 `topics/<tag>.md`，frontmatter 用 `relation: absorbs` 声明对每篇成员页面的强链接（与 `_templates/topic.md` 模板里的示例关系一致）。`consolidate_topics()` 是 `consolidate()` 步骤 7 的入口，只在传入 `llm_call` 时生效——规则本身只负责"值不值得生成"的判断，没有 LLM 就没有能力生成综合叙事正文，直接跳过而不是勉强拼接。任何单个候选生成失败都不影响其余候选继续处理。
>
> `/wiki` slash 命令（`cli/commands/wiki.py`，注册进 `cli/repl.py` 的命令路由）：
> - `/wiki <page-id>`：展示 frontmatter 概要、正文、frontmatter 强关系，以及从 `_index/backlinks.json` 读出的反向链接（backlinks 索引不存在或未涵盖该页面时提示先跑 `/wiki rebuild`，而不是静默显示"无 backlinks"）。
> - `/wiki list [--type T]`：列出全部页面，可按 `type` 过滤。
> - `/wiki search <query>`：`LibraryIndex.wiki_search()` 的命令行封装，展示三段式检索走到了哪一段（`stage_reached`）、综合回答，以及候选页面列表（LLM 精排标注过的页面打 ★），用于人工对比新旧检索路径的实际效果——这也是留着旧路径不下线的直接使用场景。
> - `/wiki rebuild [--full]`：手动触发一次索引重建（默认增量，`--full` 强制全量），相当于把 `consolidate()` 步骤 6 单独拎出来手动跑一次，并展示 `validator.py` 校验出的死链/孤儿页面问题。

## 七、实现记录汇总（阶段一 & 阶段二）

### 新增文件

| 文件 | 作用 |
|---|---|
| `src/mini_agent/wiki/__init__.py` | 模块包初始化，导出核心公共 API |
| `src/mini_agent/wiki/parser.py` | 解析单个页面：frontmatter、正文、正文内 `[[link]]` |
| `src/mini_agent/wiki/graph.py` | `GraphIndex`：内存图结构，正向边 + 反向边，支持一跳扩展 |
| `src/mini_agent/wiki/indexer.py` | 遍历 `wiki/` 生成 `_index/` 下四个派生索引，支持增量模式 |
| `src/mini_agent/wiki/writer.py` | 原子写：新建/更新页面、追加 section、更新 status |
| `src/mini_agent/wiki/validator.py` | 死链检测、id 冲突检测、孤儿页面提示 |
| `src/mini_agent/wiki/migration.py` | `migrate_entity_store()` 一次性导出脚本 + `mirror_entity()` 双写共用函数 |
| `src/mini_agent/wiki/dedup.py` | 页面相似度判断：默认规则+LLM，embedding 作为可选路径 |
| `src/mini_agent/wiki/_templates/*.md` | 五种页面类型（含 topic）的 frontmatter 骨架 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/storage/paths.py` | 新增 `wiki_dir` 及全部子目录/索引文件属性，`ensure_wiki_dirs()` |
| `src/mini_agent/perception/library_index.py` | 新增可选构造参数 `wiki_paths`（默认 `None`，不影响旧调用方）；`on_new_entry`/`mark_stale_from_correction`/`consolidate()` 接入 wiki 双写与判重逻辑 |

### 关键设计取舍

- **wiki 目前是镜像层，不是主索引**：所有双写操作失败都被吞掉，不影响图书馆索引（分类树/实体索引/编年目录）的主流程。这是为了让阶段二可以在不确定新方案长期效果的情况下先跑起来看效果，符合原计划 5.5 节"新知识双写，待新检索效果验证稳定后逐步下线旧路径"的过渡期定位。
- **默认不依赖 embedding**：既是 5.3 节偏差记录里说的资源/依赖考虑，也是这次单独明确提出的需求——判重的默认路径改成规则打分 + LLM 兜底确认，embedding 保留为显式选配项。
- **entity_id → page_id 映射持久化**：`wiki/_migration_map.json` 不算在原计划的四个 `_index/` 派生文件之列，是双写路径能够正确识别"这个实体是不是已经镜像过"的必要状态。

## 八点五、实现记录汇总（阶段三 & 阶段四）

### 新增文件

| 文件 | 作用 |
|---|---|
| `src/mini_agent/wiki/search.py` | 三段式检索：规则粗筛 → 图扩展 → LLM 精排，`shelf_search` 的平行实现 |
| `src/mini_agent/wiki/topics.py` | 专题页生成：tag 聚类 + 强链接密度判断 + LLM 综合聚合成 `topics/*.md` |
| `src/mini_agent/cli/commands/wiki.py` | `/wiki <page-id>\|list\|search\|rebuild` slash 命令 |

### 修改文件

| 文件 | 改动 |
|---|---|
| `src/mini_agent/perception/library_index.py` | 新增 `wiki_search()` 方法；`consolidate()` 新增步骤6（索引重建）、步骤7（专题页生成），返回值新增 `wiki_index_rebuilt`/`wiki_pages_indexed`/`wiki_topics_generated` |
| `src/mini_agent/perception/memory_factory.py` | `_build_library_index()` 补上此前遗漏的 `wiki_paths` 接线，据 `cfg.memory.wiki_enabled` 决定是否传入 |
| `src/mini_agent/config/models.py` | `MemoryConfig` 新增 `wiki_enabled: bool = True` 总开关 |
| `src/mini_agent/wiki/__init__.py` | 导出新增的 `search.py`/`topics.py` 公共 API，更新模块阶段说明 |
| `src/mini_agent/cli/commands/__init__.py` | 注册 `handle_wiki_cmd` |
| `src/mini_agent/cli/repl.py` | 接入 `/wiki` 命令路由 |
| `src/mini_agent/cli/commands/evolve.py` | `/evolve consolidate` 报告展示新增的 wiki 索引重建 / 专题页生成统计 |

### 关键设计取舍

- **三段式检索止步于"平行实现"**：`wiki_search()` 不修改 `shelf_search` 的任何行为，`wiki_paths=None` 时永远返回空结果——严格遵守阶段三"先不替换，AB 对比效果"的要求，替换与否留给后续根据实际数据决定。
- **专题页生成只做"新建"不做"更新"**：同一 tag 一旦生成过专题页就被排除出后续候选，避免巩固循环反复重新生成同一批页面；代价是专题页可能会随着源页面更新而逐渐过时，这是本轮有意接受的简化，见"下一步"第3条。
- **顺手修复 `wiki_paths` 接线遗漏**：这不是阶段三/四计划里列出的条目，但没有这条修复，阶段三/四所有新增能力都无法在真实运行的 agent 里被触发（`wiki_paths` 会一直是 `None`）。修复本身用一个新增的默认开启的配置开关兜底，风险可控，且是让本轮工作实际生效的必要前提。

## 八、下一步

阶段三（检索切换）与阶段四（专题页与收尾）的代码部分已完成，`wiki_search()` / `/wiki` 系列命令 / 专题页生成均已接入并通过端到端手工验证。剩下的工作主要是"用起来"而不是"写出来"：

1. **实际跑一段时间，积累 A/B 数据**：`/wiki search` 与旧的 `shelf_search`（隐式走在正常检索路径里）并行存在，需要在真实使用场景里对比两者命中质量，才能支撑阶段四第二条"验证新检索路径效果稳定后下线旧路径"的决策——这条本次有意保持未完成，理由见阶段四实现记录。
2. **专题页生成阈值调优**：`_MIN_PAGES_PER_TAG=4` / `_MIN_LINK_DENSITY=0.5` 是按经验给的初始值，实际跑起来后可能需要根据真实 tag 分布调整，避免"太容易触发导致专题页质量参差"或"太难触发导致这个能力形同虚设"两个极端。
3. **多轮更新的专题页**：`consolidate_topics()` 当前只支持"生成新专题页"，同一 tag 下后续新增的成员页面不会自动并入已有专题页（会被 `exclude_tags` 排除，避免重复生成）——如果这个场景在实践中常见，需要扩展成"更新既有专题页"的能力。
4. **`wiki_enabled` 默认开启后的观察**：这次顺带修复了 `wiki_paths` 从未被真正传给 `LibraryIndex` 的接线遗漏（见阶段三实现记录），双写路径是第一次在真实 agent 运行中被触发，需要留意实际运行中 wiki 镜像/判重/索引重建是否有性能或稳定性问题。