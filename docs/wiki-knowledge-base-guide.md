# Wiki 式知识库指南

对应设计文档：项目根目录《wiki式知识库重构计划.md》。这是[图书馆式知识索引](library-index-guide.md)（分类树 + 实体索引 + 目录）之外的一套**平行新实现**，不替换旧系统，两者在过渡期并存运行，直到新检索路径经过实际验证效果稳定。

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
| `topics.py` | 四 + P3 | 专题页生成：tag 聚类 + 强链接密度（规则）与不依赖 embedding 的 LLM 直接聚类两条路径并存，候选池合并去重后 LLM 综合聚合 |
| `decision_writer.py` | 决策提炼 | 决策候选落盘：命中已有决策页则更新/推翻，命中不到才新建 |

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

**这套检索目前只是"平行实现"，不替换 `shelf_search`**：`wiki_paths=None` 或 wiki/ 下没有页面时永远返回空结果，两条路径完全独立运行，便于 A/B 对比效果后再决定是否收敛。

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

返回值新增字段：`wiki_mirrored`、`wiki_dedup_merged`、`wiki_index_rebuilt`、`wiki_pages_indexed`、`wiki_topics_generated`（新生成的专题页 id 列表）。`/evolve consolidate` 报告会展示这些统计。

## 八、`/wiki` CLI 命令（阶段四）

| 命令 | 说明 |
|---|---|
| `/wiki <page-id>` | 展示指定页面的 frontmatter 概要、正文、frontmatter 强关系，以及从 `_index/backlinks.json` 读出的反向链接 |
| `/wiki list [--type T]` | 列出全部页面，可按 `type`（entity/decision/process/experience/topic）过滤 |
| `/wiki search <query>` | `LibraryIndex.wiki_search()` 的命令行封装，展示三段式检索走到了哪一段、综合回答、候选页面（LLM 精排标注过的打 ★），用于人工对比新旧检索路径的实际效果 |
| `/wiki rebuild [--full]` | 手动触发一次索引重建（默认增量，`--full` 强制全量），相当于把 `consolidate()` 步骤 6 单独拎出来手动跑一次，并展示 `validator.py` 校验出的死链/孤儿页面问题 |

`/wiki <page-id>` 找不到 backlinks 时会提示先跑 `/wiki rebuild`，而不是静默显示"无 backlinks"——backlinks 只存在于 `_index/` 里，页面本身刚写入还没重建索引时是看不到的。

## 九、新增的落盘文件（均可重建或明确标注为持久状态）

在 `.agent/wiki/`（project scope）和 `~/.agent/wiki/`（global scope）下各自独立一套，路径定义见 `storage/paths.py`：

| 文件/目录 | 内容 | 是否可随时删除重建 |
|---|---|---|
| `entities/`、`decisions/`、`processes/`、`experiences/`、`topics/` | 页面 md 文件 | 否——这是知识本身，唯一真相 |
| `_migration_map.json` | entity_id → page_id 映射 | 否——双写路径依赖的持久状态，删除会导致同一实体被重复创建新页面 |
| `_index/graph.json` / `tags.json` / `backlinks.json` / `search_index.json` | 派生索引 | 是——`/wiki rebuild` 随时可重建 |
| `_index/_manifest.json` | indexer 自用的增量重建状态 | 是——删除后下次退化为全量重建 |
| `decision_candidates_pending.jsonl`（workdir 根目录，非 wiki/ 下） | compact 提炼出的决策候选，尚未经巩固循环批量落盘 | 否——删除会丢失尚未处理的候选（不影响已落盘的 decisions/*.md），见九·2 |

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

## 十、与图书馆式索引的关系与后续计划

- `MemoryStore` 的原始 jsonl 记忆条目保持不变，作为"证据层"不受影响。Wiki 页面是"提炼层"，`source_entries` 字段始终指回原始证据，保证可追溯。
- 旧的 `classification.py`/`entity_index.py`/`catalog.py` 在过渡期并存，新知识双写，待新检索效果验证稳定后逐步下线旧路径——**这一步本次有意保持未完成**：三段式检索刚刚落地，还没有经过任何实际使用周期的 A/B 验证，现在下线旧路径会让"先不替换，AB 对比"失去意义。
- 后续工作方向：实际跑一段时间积累 A/B 数据、根据真实 tag 分布调优专题页生成阈值、评估是否需要支持"更新既有专题页"而不只是生成新的。

## 相关文档

- [图书馆式知识索引指南](library-index-guide.md) — 旧的分类树/实体索引/两步检索系统，仍是当前的主索引
- [巩固循环 后台循环指南（Stage 8）](self-evolution-consolidation-guide.md) — `consolidate()` 挂载的完整巡检流程
- 项目根目录《wiki式知识库重构计划.md》— 完整设计动机、阶段划分与逐条实现记录

---

*首次编写：2026-07（wiki 式知识库阶段一~四：md 页面存储 + 双写镜像 + 三段式检索 + 专题页生成 + `/wiki` 命令）*
