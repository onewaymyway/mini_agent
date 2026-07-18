# wiki 提取层与组织层改进计划 · E3 实施记录

> 对应 `wiki知识库提取与组织层改进计划.md` §3（问题 E3：抽取"看不到"已有
> 知识库），已实施 §3.2.1（实体索引反向注入）与 §3.2.2（接入抽取
> prompt）。原计划把 §3.2.1 拆成"过渡版"（O1 之前，无相关性排序）与
> "完整版"（依赖 O1 排序）两阶段——本次开工时 O1 已完成（见
> `wiki提取层改进计划_O1实施记录.md`），因此直接实现完整版，不再区分
> 两阶段，详见下文 §3 的说明。

## 1. 改动内容

### 1.1 §3.2.1 新增极简实体索引摘要生成器

- 新增 `src/mini_agent/wiki/entity_digest.py`：
  - `build_entity_digest(paths, *, max_entities=40, relevance_hint=None)`：
    扫描 `entities/` 目录（只扫实体类型页面，不是全库扫描），每条产出
    `- <id>（<entity_type 中文标签>）：<一句话描述>`，一句话描述取正文
    "## 概述" 小节首个非空行（取不到则退回正文首个非空行），单条截断到
    60 字符。
  - 排序依据与原计划 §3.2.1 一致：`relevance_hint` 命中 > `grounded_hit_count`
    （取 `log1p`，复用 O1 §4.2.2 沉淀在 frontmatter 里的字段）> `updated`
    最近。数量上限 `max_entities`，超出部分直接截断。
  - `build_entity_digest_section(...)`：在 digest 前加一段英文表头
    （"Already-known entities: ..."），供直接注入 prompt；没有任何实体
    页面或读取异常时返回空字符串，调用方据此完全不注入这一段，等同于
    本次改动前的"裸识别"行为。
  - **未按原计划区分"过渡版/完整版"两阶段**：`entities/` 目录本身规模远
    小于全库（search.py/dedup.py 面对的才是全库扫描问题），直接
    `parse_page` 扫描该目录成本可控，不需要依赖 O1 的 `_index/` 派生索引
    来提速；本次真正复用 O1 的产出只是 `grounded_hit_count` 这一个排序
    字段，因此一次性实现了排序完整版。

### 1.2 §3.2.2 接入抽取 prompt

- `src/mini_agent/prompts/system/compress_summarizer.md`：
  - `entities[]` schema 新增字段 `reused_existing_id`：模型如果判断新识别
    的实体与下方注入的"已知实体索引"中某一项是同一个东西，把该已知项的
    id 填在这里，否则为 `null`。
  - 在 `entities`/`facts` 字段说明段落末尾新增 `{{ entity_digest_section }}`
    占位符，用于注入 §1.1 生成的实体索引文本（空字符串时该占位符被整段
    替换为空，不留孤立表头）。
- `src/mini_agent/history/world_extraction.py::EntityCandidate`：新增字段
  `reused_existing_id: Optional[str] = None`，`to_dict`/`from_dict` 均支持
  往返（空字符串/缺失均归一化为 `None`）。
- `src/mini_agent/history/compression.py::LLMSummaryStrategy.compress()`：
  在渲染 system prompt 之前调用 `build_entity_digest_section()`，结果作为
  `entity_digest_section` 变量传给 `pm.render("system/compress_summarizer",
  entity_digest_section=...)`；异常/未开启时传空字符串，不影响 compact
  主流程。`relevance_hint` 目前传入 `project_root` 字符串（粗粒度的"当前
  workdir"信号，取代原计划设想的"当前对话涉及的文件路径"，后者需要额外
  从历史消息里提取路径，本次未实现，留作后续按需增强）。

### 1.3 落盘侧：reused_existing_id 的两段式判重

- `src/mini_agent/wiki/dedup.py`：新增公开函数 `score_similarity(text,
  tags, page)`，是 `find_similar_page_rules` 内部 `_rule_score` 的公开
  包装，供调用方对**单个已知候选**单独打分（而不是在全部 `existing_pages`
  里找最高分的一篇）。
- `src/mini_agent/wiki/world_writer.py::_write_or_merge_entity()`：
  - 优先信任 `candidate.reused_existing_id`：若该 id 命中 `existing_pages`
    中某一篇，用 `score_similarity` 校验一次分数，`>= 0.15`
    （`_REUSED_ID_MIN_SCORE`，取自原计划 §3.4 建议阈值）才采用，直接
    合并进该页面（追加"新增认知" section），不再走规则判重。
  - 分数不足 / `reused_existing_id` 指向不存在的页面 / 未提供
    `reused_existing_id`：退回原有的 `find_similar_page`（规则打分 +
    可选 LLM 确认）流程，行为与本次改动前完全一致。
  - 这就是原计划 §3.2.2/§3.4 描述的"模型优先判断 + 规则兜底"两段式，
    不是纯规则判重，也不是无条件信任模型输出。

## 2. 验收方式（对应原计划 §3.3/§3.4）

- `tests/test_entity_digest.py`：
  - `build_entity_digest`/`build_entity_digest_section` 在无实体页面时
    返回空字符串；数量上限截断；`relevance_hint` 命中排第一；
    `grounded_hit_count` 更高的条目排在更近期更新的条目之前；一句话描述
    正确提取"## 概述"小节首句、附带中文 entity_type 标签。
  - `EntityCandidate.reused_existing_id` 的序列化/反序列化往返，以及
    空字符串/缺失字段归一化为 `None`。
  - `consolidate_pending()` 端到端场景：`reused_existing_id` 命中且分数
    达标时合并进指定页面（不新建页面、新增认知被追加进正文）；分数不足
    （模型误判）时忽略该字段、按规则判重重新决策（本例中规则判重同样
    判定不相似，因此新建页面，且原页面未被误判内容污染）；
    `reused_existing_id` 指向不存在的页面时不报错、静默退回新建流程。
  - 全部 11 个用例通过（`pytest tests/test_entity_digest.py -q`）。
- 回归：`tests/test_wiki_index_reuse.py`、`tests/test_extraction_stats.py`、
  `tests/test_context_builder_wiki_search_primary.py`、
  `tests/test_wiki_promotion.py`、`tests/test_wiki_topics_llm_cluster.py`
  共 43 项既有用例全部保持通过，`wiki/dedup.py::find_similar_page` /
  `wiki/world_writer.py::consolidate_pending` 未提供 `reused_existing_id`
  时的行为不变。
- 原计划 §3.3 建议的"对照实验：接入 entity_digest 前后统计一段时间内
  `find_similar_page` 命中率变化"本次**未包含**在改动范围内——这项需要
  真实运行数据积累，建议后续跑一段真实使用周期后用 `/wiki stats` 或
  简单统计脚本核对，作为该项改动生效与否的量化依据（延续 O1/E2 记录里
  一贯的做法：先落地机制，用真实数据验证效果）。

## 3. 与原计划的差异说明

- §3.2.1 的"过渡版/完整版"两阶段合并为一次实现（见 §1.1 最后一条），
  原因是 `entities/` 目录扫描本身不构成 O1 要解决的全库扫描性能问题，
  详细理由见该处注释。这一决定不影响后续 O2/O3/O4 的推进——它们依赖的
  是 O1 的 `_index/` 派生索引与 `search.py`/`graph.py` 的改动，与本次
  `entity_digest.py` 的实现方式无关。
- `relevance_hint` 用 `project_root` 字符串代替原计划设想的"当前对话涉及
  的文件路径"，是本次一个刻意缩小的范围，见 §1.2 说明。

## 4. 风险与兜底（延续原计划 §3.4）

- `reused_existing_id` 是模型自报的，`score_similarity` 校验兜底防止
  "过度复用"误判（见 §1.3）。
- `build_entity_digest`/`build_entity_digest_section` 内部统一
  `try/except` 吞掉异常，读取失败静默降级为不注入任何实体索引，不阻断
  抽取主流程。
- `CompressConfig.entity_digest_enabled`（默认 `True`）可以完全关闭本次
  改动，退回 E3 之前的"裸识别"行为；`entity_digest_max_entities`
  （默认 40）控制注入 prompt 的 token 开销。

## 5. 未在本次实施范围内的项

- 原计划 §0 问题清单中除 O1、E2 方案B、E3 之外的其余项（E1、O2、O3、O4，
  以及 E2 方案 A/C）仍**尚未实施**，原因和顺序建议见原计划 §8"总体实施
  排期建议"。按该排期，E3 完成后下一项是 E1（抽取时机解耦）。
- `relevance_hint` 未接入"当前对话涉及的文件路径"这一更精细的信号，见
  §3 差异说明，留作后续按需增强。
- 原计划 §3.3 的"对照实验"（`find_similar_page` 命中率前后对比）需要真实
  运行数据，未包含在本次改动范围。
