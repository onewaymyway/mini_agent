# wiki 提取层与组织层改进计划 · O3 实施记录

> 对应 `wiki-knowledge-base-extraction-and-organization-plan.md` §6（问题 O3：topic 聚类是纯
> 事后归纳），实施 §6.2.1（"再巩固"扫描步骤）与 §6.2.2（触发频率控制）。
> 依赖 O1（已完成），按 §8 排期属于第三批，与已完成的 O2 同批。

## 1. 改动内容

### 1.1 §6.2.1 再巩固扫描

`src/mini_agent/wiki/topics.py`：

- 新增 `_topic_entity_tag_set(topic_page, pages_by_id) -> set[str]`：一个
  topic 页面的"关联实体集合"= 自身 tag ∪ 已 `absorbs` 的成员页面 tag
  并集。topic 页面生成时本身只带一个 tag（source tag/slug），真正反映
  "这个主题都涉及哪些概念"的是它已吸收成员的 tag 集合，因此取并集。
- 新增 `_find_topic_reconsolidation_candidates(existing_topic_pages,
  new_pages_since_last_run, pages_by_id, *, overlap_threshold=0.35)`：
  对每个已有 topic，用其关联 tag 集合与候选新页面的 tag 集合算 Jaccard
  重合度，达标且尚未被该 topic 吸收的页面视为"应当并入"的候选。
- 新增 `append_to_topic_page(paths, topic_page, new_pages, *,
  soft_cap=8)`：
  - 追加一个"新增关联" section（每个新页面一条 `[[page_id]]` 弱引用 +
    正文首行摘要）；
  - 补充 frontmatter `absorbs` 强链接（复用扩展后的
    `writer.append_section()`，见 §1.3）；
  - frontmatter 新增/递增 `reconsolidation_count` 字段，超过 `soft_cap`
    （默认 8，对应计划 §6.4 风险与兜底提到的软上限）时写入
    `needs_review: true`，提示后续巩固循环/人工考虑拆分该 topic；
  - 任一环节异常均返回 `None`，调用方（`consolidate_topics`）据此跳过
    该候选继续处理下一个，不中断整体巩固循环。

### 1.2 §6.2.2 触发频率控制

`consolidate_topics()` 新增 `reconsolidation_interval_runs: int = 5`、
`reconsolidation_overlap_threshold: float = 0.35` 两个参数：

- 累计运行次数持久化在 `AgentPaths.wiki_topics_run_counter_path`
  （`wiki/_index/topics_run_counter.json`，`{"run_count": N}`），每次
  `consolidate_topics()` 调用时 +1 并落盘（读写失败静默降级为 0/忽略，
  不影响主流程）。
- `run_count % reconsolidation_interval_runs == 0` 时才执行一次再巩固
  扫描：收集所有已有 topic 页面已吸收的成员 id 作为排除集合，对剩余
  候选页面跑 `_find_topic_reconsolidation_candidates`，命中的逐个调用
  `append_to_topic_page`。
- 参与再巩固并成功并入的页面 id 从本次"生成新候选簇"的输入页面集合中
  剔除（`pages_for_new_candidates`），再传给 `find_topic_candidates` /
  `find_topic_candidates_llm_cluster`，避免同一批页面既被并入已有
  topic、又被拿去生成内容重叠的新 topic 页——与计划原文"两段逻辑共享
  同一个已处理页面排除集合"的要求一致（本实现直接过滤输入页面集合，
  等价于共享排除集合，未额外引入 `exclude_page_ids` 形参改造
  `find_topic_candidates`，改动面更小）。
- 每次成功的再巩固写入追加一条事件到新增的
  `AgentPaths.wiki_topics_reconsolidation_log_path`
  （`wiki/_index/topics_reconsolidation_log.jsonl`），记录
  `{topic_id, added_page_ids, run_count}`，供后续用真实数据校准扫描
  频率与重合度阈值（延续本计划一贯的"先观测、再调参"纪律，同
  `extraction_trigger_log.jsonl`/`search_ab_log.jsonl` 的做法）。
- `consolidate_topics()` 的返回值（新生成的 topic page_id 列表）语义不变
  ——再巩固产生的变更只追加进既有页面正文，不产生新页面，因此不计入
  返回值；调用方如需观测再巩固活动，读取上述 jsonl 日志即可。

### 1.3 `writer.append_section()` 扩展

`src/mini_agent/wiki/writer.py::append_section()` 新增两个可选关键字
参数（默认值保持原行为完全不变，现有调用点无需改动）：

- `extra_links: Optional[list[WikiLink]] = None`：追加时顺带补充的
  frontmatter 强链接，与既有 `page.strong_links()` 按 `target` 去重合并
  （新链接覆盖同 target 的旧记录）。
- `extra_frontmatter_updates: Optional[dict] = None`：追加时顺带更新/
  新增的非核心 frontmatter 字段，与既有 `raw_frontmatter` 合并（新值
  覆盖同名旧值）。

这两个参数是 `append_to_topic_page()` 的直接依赖——原计划 §6.2.1 设想
"复用 `writer.py::append_section()`"，但原 `append_section()` 只支持
追加正文、不支持同时更新链接和 frontmatter 字段，因此在其基础上做了
最小扩展，而不是在 `topics.py` 里另起一套写入逻辑，避免出现两条重复
的"追加 section + 原子写回"实现。

### 1.4 `AgentPaths` 新增路径

`src/mini_agent/storage/paths.py`：

- `wiki_topics_run_counter_path`：`wiki/_index/topics_run_counter.json`。
- `wiki_topics_reconsolidation_log_path`：
  `wiki/_index/topics_reconsolidation_log.jsonl`。

两者均落在既有的 `wiki/_index/` 派生目录下，语义上属于"可重建的运行时
观测记录"，不是知识本身，与 `promotion_log.jsonl`/`search_ab_log.jsonl`
同类。

### 1.5 `library_index.py::consolidate()` 步骤 7 文档同步

更新步骤 7 的说明文字，补充再巩固扫描的触发条件与观测日志位置。
`consolidate_topics()` 调用点本身未改动（新参数均有默认值，透传行为
不变），后续如需暴露 `reconsolidation_interval_runs` 为外部可配置项，
可以和 E2 方案 C 的 `CompressConfig` 一样走配置项模式，本次未做——
`/wiki stats` 等观测工具尚未展示再巩固相关指标，先用默认值跑一段观测期
更符合本计划的执行纪律。

## 2. 验收方式（对应原计划 §6.3）

新增 `tests/test_wiki_topics_reconsolidation.py`（12 项用例，全部通过）：

- `_find_topic_reconsolidation_candidates`：重合度阈值上下的场景判定
  （命中/不命中）、已被吸收的页面被正确排除。
- `append_to_topic_page`：追加内容后正文包含新页面引用、frontmatter
  链接正确补充为 `absorbs` 关系、`reconsolidation_count` 正确递增、
  超过 `soft_cap` 后 `needs_review` 被置为 `true`、空候选列表直接返回
  `None`。
- 端到端场景（`consolidate_topics`）：
  - 已有一个吸收 4 篇成员的 topic，新增一篇高度相关（tag 重合）但尚未
    被吸收的页面，`reconsolidation_interval_runs=1` 时该新页面被追加进
    既有 topic 正文与链接，且**不**生成新 topic 页
    （`created == []`），再巩固日志文件包含对应事件记录。
  - `reconsolidation_interval_runs=5` 且当前是第一次运行（`run_count=1`，
    `1 % 5 != 0`）时不触发再巩固，新页面既不被并入也不出现在日志里。
  - 运行计数在两次 `consolidate_topics()` 调用后正确累加持久化到
    `topics_run_counter.json`。

回归：`tests/test_wiki_topics_llm_cluster.py`（13 项）、
`tests/` 目录下全部 `wiki`/`writer` 相关用例（含扩展后的
`writer.append_section()`）共 51 项既有用例全部保持通过；`consolidate`/
`library_index` 相关用例（14 项）保持通过。

## 3. 与原计划的差异说明

- 计划原文 §6.2.1 的重合度定义是"其关联实体集合（frontmatter links）与
  新增页面集合的 tag/entity 重合度"，本次实现把"关联实体集合"具体化为
  "topic 自身 tag ∪ 已吸收成员的 tag 并集"，与新页面的 tag 集合做
  Jaccard 相似度比较，而不是直接比较 frontmatter links 的页面 id 与新
  页面 id（那样定义下"重合度"没有实际意义，因为新页面本身还不在链接
  里）。这是对原文字面表述的合理具体化，不是范围缩减。
- 计划原文提到"两段逻辑共享同一个已处理页面排除集合，复用 P3 已有的
  `exclude_page_ids` 参数"——`find_topic_candidates`（tag+密度路径）
  本身不支持 `exclude_page_ids` 形参（只支持 `exclude_tags`），本次
  实现选择直接从输入页面集合里过滤掉已再巩固的页面
  （`pages_for_new_candidates`）后再传给两条候选生成路径，效果与"排除
  集合"等价，但不需要改造 `find_topic_candidates` 的签名，改动面更小。
- `soft_cap`（8 次）沿用计划原文 §6.4 给出的具体数值，未做进一步调参
  ——按计划纪律，留给后续观测期用真实的 `topics_reconsolidation_log.jsonl`
  数据校准。

## 4. 风险与兜底（延续原计划 §6.4）

- 再巩固扫描频率默认每 5 次巩固循环触发一次，成本集中在"遍历所有已有
  topic 页面的关联 tag 集合"，与新聚类候选生成相比开销可控。
- `append_to_topic_page()` 写入失败（磁盘异常等）静默返回 `None`，
  调用方继续处理下一个候选，不影响本轮巩固循环的其余步骤。
- 话题漂移风险通过 `reconsolidation_count` + `needs_review` 软上限缓解
  ——达到阈值只是标记，不阻止继续追加，也不自动拆分，符合项目"失败/
  越界不阻断主流程，只做标记留给下一环节处理"的一贯风格。
- 运行计数与日志读写全部包裹 `try/except`，损坏或缺失时最坏情况是
  "再巩固扫描节奏偏移"（比如又从 0 开始计数），不影响新专题页生成这条
  主路径的正确性。

## 5. 未在本次实施范围内的项

- O4（统一知识生命周期状态机）依赖 O1-O3、E1-E3 均验证稳定，仍在第四批，
  未开始。
- E2 方案 C 仍是"机制已就位、待观测期后人工执行"的状态，本次未推进。
- 再巩固触发频率（`reconsolidation_interval_runs`）与重合度阈值
  （`reconsolidation_overlap_threshold`）尚未接入 `CompressConfig`/
  `TopicConfig` 之类的显式配置对象供运行时调整，目前只能通过调用方传参
  覆盖默认值；`/wiki stats` 也尚未展示再巩固相关的观测指标。这两项留给
  观测期后按需再做，避免在没有真实数据支撑的情况下过早决定配置项的
  默认值。
