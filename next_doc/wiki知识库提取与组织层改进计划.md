# wiki 知识库改进计划 · 提取层与组织层

> 本计划是对 `next_doc/wiki式知识库改进计划.md`（P0-P4）的**后续深化**，不重复其已完成的工作，
> 聚焦该计划落地后暴露出的两类深层问题：**提取时机/耦合度/知识盲视**，以及
> **组织结构的可扩展性/图谱表达力/动态性/生命周期一致性**。
>
> 状态标注约定：`[设计]` 尚未实现、`[待评审]` 设计需要先过一轮 review、`[可直接排期]` 方案清晰可直接进入实现。

---

## 0. 问题清单与关联关系

| 编号 | 问题 | 所属层面 | 严重度 | 依赖关系 | 状态 |
|---|---|---|---|---|---|
| E1 | 抽取时机与对话粒度错配（绑死在 compact 上） | 提取 | 高 | 无前置依赖，可独立做 | 待实施 |
| E2 | 抽取任务耦合度过高，四子任务挤在一次 LLM 调用 | 提取 | 中 | 无前置依赖，可独立做 | 方案B已完成，方案A/C待E1 |
| E3 | 抽取"看不到"已有知识库，每次从零识别实体 | 提取 | 高 | 依赖组织层 O1（索引可增量读取）打底 | **已完成** |
| O1 | 全量扫描架构没有为增长设计分层 | 组织 | 中 | 无前置依赖，且是 E3/O2/O3 的基础设施 | **已完成**（§4.2.3分区组织除外，原计划本就不实现） |
| O2 | 实体关系图过于扁平，缺层级/路径概念 | 组织 | 中 | 依赖 O1（分层索引） | 待实施 |
| O3 | topic 聚类是纯事后归纳，不会动态更新已有页面 | 组织 | 中 | 依赖 O1 | 待实施 |
| O4 | decision/experience/entity/fact 四类页面缺统一知识生命周期状态机 | 组织 | 高 | 无前置依赖，但改动面最广，建议最后做 | 待实施 |

**建议实施顺序**：O1 → E3 → E1 → E2 → O2 → O3 → O4

理由：O1 是最基础的设施改动，且直接被 E3 依赖；E3 收益最大（直接改善"错题本→世界模型"转型的核心断点）；E1/E2 是提取管线的解耦，风险低、可并行推进；O2/O3 依赖 O1 的产出；O4 改动面最广（涉及四类页面的写入路径与纠正检测），放最后做，避免和前面的改动互相踩踏。

---

## 1. 问题 E1：抽取时机与对话粒度错配

### 1.1 现状问题

`history/decision_extraction.py` / `history/world_extraction.py` 目前只在 **compact 触发**这一个时间点被调用（挂在 `LLMSummaryStrategy` 里）。而 compact 的触发条件是 token 预算压力（`compaction.py` 的 auto-compact 阈值判断），和"这段对话里是否已经积累了值得提炼的知识"完全是两个独立的信号源。

后果：
- 一段富含决策/事实的短对话，如果全程没有触发 compact（token 预算够用），相关内容会一直留在 raw history 里，直到某次 session 结束或很久之后被别的更长对话"捎带"压缩掉才会被提炼——那时候上下文早已被压缩过，细节丢失。
- 反过来，一次 compact 可能横跨好几个话题差异很大的历史片段，一次性塞给 LLM 做"决策+实体+事实"抽取，抽取质量随片段跨度增大而下降。

### 1.2 改进方案 `[设计]`

**核心思路：把"是否该抽取"从"是否该压缩"中解耦出来，引入独立的、基于内容密度的触发器。**

#### 1.2.1 新增轻量级抽取候选窗口探测器

新增 `history/extraction_trigger.py`：

```python
@dataclass
class ExtractionWindowCandidate:
    start_index: int          # raw history 起始条目 index
    end_index: int            # 结束 index（不含）
    trigger_reason: str       # "connective_density" | "tool_pattern" | "turn_count" | "session_end"
    signal_score: float       # 触发强度，用于后续排队优先级排序


def scan_for_extraction_window(
    raw_entries: list[HistoryEntry],
    *,
    last_extracted_index: int,
    min_window_turns: int = 6,
    connective_keywords: tuple[str, ...] = ("因为", "所以", "决定", "改为", "放弃", "取代", "而不是"),
) -> Optional[ExtractionWindowCandidate]:
    """规则驱动、零 LLM 成本的候选窗口探测：
    - 统计 last_extracted_index 之后新增条目中触发关键词的密度
    - 达到阈值即返回候选窗口，交给巩固循环异步跑抽取
    - 不做语义判断，只做"值得看一眼"的粗筛，真正判断交给 LLM 抽取本身的
      is_meaningful 校验（复用 DecisionCandidate.is_meaningful 现有逻辑）
    """
```

触发规则（纯规则、无 LLM 成本，和现有 `dedup.py`/`indexer.py` 的"规则优先"哲学一致）：
1. **连接词密度**：新增条目文本中出现"因为/所以/决定/改为/放弃/取代/而不是"等词的密度超过阈值。
2. **轮次计数**：距上次抽取已经过去 `min_window_turns`（默认 6）轮且从未抽取过（避免长期空转的 session 永远不被抽取）。
3. **session 结束**：作为兜底，`agent/lifecycle.py` 的 session 结束钩子里，如果本次 session 有任何未被抽取的窗口，强制触发一次。

#### 1.2.2 抽取执行与 compact 解耦

新增 `history_manager.py::maybe_trigger_extraction()`，独立于 `compaction.py` 的触发路径：

```python
def maybe_trigger_extraction(self, llm_call: Optional[LLMCall]) -> None:
    """每轮工具调用批次结束后调用（成本极低，纯规则扫描）。
    命中候选窗口时，异步排队一次"仅抽取，不压缩"的 LLM 调用
    （复用 world_extraction.py / decision_extraction.py 的 parse 函数，
    但 prompt 换成 §2 中新增的"轻量抽取"专用模板，不再要求输出 compact_summary）。
    """
```

- 抽取结果依然走现有的 pending 队列（`world_candidates_pending_path` / decision 的 pending 队列），落盘判断逻辑完全复用，不新增巩固路径。
- `last_extracted_index` 持久化在 `AgentPaths` 下新增的 `extraction_cursor.json`，记录"raw history 里抽取到哪了"，避免同一段内容被反复抽取。
- **与 compact 共存**：compact 触发时，如果这段范围已经被 `maybe_trigger_extraction` 抽取过（cursor 已经越过），跳过 decisions/entities/facts 字段的抽取，只做纯摘要——避免同一段内容被抽两次、产生重复候选（重复候选本身有 dedup 兜底，但没必要浪费一次 LLM 调用）。

### 1.3 验收标准

- 新增单测覆盖：`scan_for_extraction_window` 对连接词密度/轮次计数/零信号三种场景的判定；`maybe_trigger_extraction` 与 `last_extracted_index` cursor 的推进和幂等性（同一段不会被抽两次）。
- 端到端场景：一个全程未触发 compact 的短 session（工具调用少、token 压力小），结束后依然能在 wiki 里看到对应的 decision/entity 候选被巩固写入。
- 回归：现有 compact 触发路径的抽取行为不变，`tests/test_selective_compression.py` 等既有测试保持通过。

### 1.4 风险与兜底

- 规则触发器可能出现"从不命中"（阈值过高）或"过于敏感"（阈值过低导致抽取过于频繁、LLM 调用成本上升）两种失衡，建议先以**只记录候选窗口、不实际发起 LLM 调用**的方式跑一段时间（打日志到新的 `extraction_trigger_log.jsonl`），用真实数据校准阈值，再打开实际抽取开关——这是吸取 P4 "零数据切换"教训后的做法。
- 新增触发路径全部遵循项目"失败不阻断主流程"风格：扫描异常、cursor 读写失败均静默降级为"本轮不触发"。

---

## 2. 问题 E2：抽取任务耦合度过高

### 2.1 现状问题

`prompts/system/compress_summarizer.md` 要求单次 LLM 调用同时产出 `{compact_summary, decisions[], entities[], facts[]}`。四个子任务里 `compact_summary` 排在 schema 首位、语义最直接（"总结这段话"），`decisions[]/entities[]/facts[]` 是需要额外识别+结构化的任务，模型倾向于把更多"注意力"放在容易完成、且直接影响下游可读性的摘要任务上，导致结构化抽取字段容易被敷衍（空列表或内容单薄）——这一点在 P1/P2 验收记录里也能看出端倪：真实抽取数据几乎都停留在"验证脚本各 1 条"，没有真实运行的量化对比来证明抽取充分性。

### 2.2 改进方案

#### 2.2.1 方案对比

| 方案 | 说明 | 成本 | 收益 |
|---|---|---|---|
| A. 拆成两次调用 | 摘要一次、结构化抽取一次，共享同一段历史输入 | +1 次 LLM 调用/次 compact，+延迟 | 两个任务互不挤占，抽取质量预期明显提升 |
| B. 调整 schema 字段顺序 | 把 `decisions/entities/facts` 放在 JSON schema 最前面，`compact_summary` 放最后 | 几乎零成本 | 利用模型对早期字段更"认真"的倾向，缓解但不根治问题 |
| C. 条件化触发 | 只有 §1 的抽取窗口探测器命中信号时才在这次 compact 里附带结构化抽取，其余 compact 只做纯摘要 | 需要 E1 先落地 | 减少无意义的结构化抽取调用，把"认真做"的次数留给真正有信号的窗口 |

> **实施状态：方案B 已完成**，详见 `next_doc/wiki提取层改进计划_E2方案B实施记录.md`
> （JSON schema 字段顺序调整 + `avg_entities_per_extraction`/`avg_facts_per_extraction`
> 观测基础设施 + `/wiki stats` 展示 + 单测）。方案 A/C 仍待 E1 落地。

**建议**：B 先做（成本几乎为零，`[可直接排期]`），作为立即生效的缓解措施；A 和 C 配合 E1 一起做——E1 落地后，"轻量抽取"本来就是独立触发的单独调用，天然解决了耦合问题（E1 §1.2.2 的 `maybe_trigger_extraction` 本身就是方案 A 的落地形式），因此 **E2 无需单独实现方案 A，E1 完成后 E2 自然被解决**；compact 路径本身在 E1 落地后可以简化为**只做摘要**，不再要求输出 decisions/entities/facts（方案 C）。

#### 2.2.2 具体改动

`[可直接排期]` 第一步（不依赖 E1，可立即做）：

- `prompts/user/compress_summary_request.md`：调整 JSON schema 顺序为 `{decisions[], entities[], facts[], compact_summary}`，并在 prompt 说明里加一句"请先完整识别 decisions/entities/facts，最后再给出 compact_summary"，显式引导模型的处理顺序。
- 加一个可观测性对照：`wiki/stats.py::compute_stats()` 新增按"抽取批次"统计的 `avg_entities_per_extraction` / `avg_facts_per_extraction`，改动前后对比数值，用真实数据判断 B 方案是否有效，而不是凭感觉。

`[设计]` 第二步（依赖 E1 完成后落地）：

- `history_manager.py::maybe_trigger_extraction()` 使用独立的、更简短的抽取专用 prompt（不含摘要要求），`compaction.py` 的 `LLMSummaryStrategy` 视 `CompressConfig.extract_world_model` / `extract_decisions` 开关情况，在 E1 稳定运行后逐步把这两个开关默认改为 `False`——因为结构化抽取的职责已经完全转移到独立触发路径，compact 只需要专心做摘要。**这个开关切换需要吸取 P4 §6.5 的教训：先双路径并存观测一段时间，比较独立触发路径与 compact 路径的抽取数量/质量，确认前者不弱于后者再关闭 compact 里的结构化抽取。**

### 2.3 验收标准

- B 方案：改动前后各跑 20 次 compact，对比 `avg_entities_per_extraction`/`avg_facts_per_extraction`，应有可观测提升（哪怕不大，只要方向对即可保留该改动，因为成本几乎为零）。
- C 方案：`CompressConfig` 新增 `extract_world_model_via_compact: bool`（默认 `True`，仅在观测到 E1 独立触发路径稳定产出后才建议手动关闭，不做自动切换，避免重蹈 P4 覆辙）。

---

## 3. 问题 E3：抽取"看不到"已有知识库

### 3.1 现状问题

`world_extraction.py` 的抽取 prompt 里不包含任何"当前 wiki 里已有哪些实体"的上下文。模型每次都在"裸识别"，后果：
- 同一实体在不同 session 里被不同措辞命名（比如"ClientPool"/"客户端池"/"key 轮换模块"），全靠 `dedup.py` 事后按 token/tag 相似度补救。
- 模型无法判断某条 fact 是否已经写过，容易产出语义重复、measures 不同的 fact 候选，加重 dedup 与巩固循环负担。
- 无法做"增量补充"——已有实体页面出现新认知时，理想情况是模型直接引用该实体 id 并追加事实，而不是重新造一个近义实体。

> **实施状态：已完成**，详见 `next_doc/wiki提取层改进计划_E3实施记录.md`
> （`wiki/entity_digest.py` 实体索引摘要生成器 + `EntityCandidate.reused_existing_id` +
> `wiki/dedup.py::score_similarity` 校验兜底 + system prompt 注入 + `entity_digest_enabled`
> 配置项 + 单测）。原计划设想的"过渡版/完整版"两阶段已合并为一次实现，理由见实施记录 §3。

### 3.2 改进方案 `[设计]`（已完成，见上方实施状态）

**核心思路：把"组织层"沉淀出的知识索引，反向注入"提取层"的 prompt 上下文，形成闭环。**

#### 3.2.1 新增极简实体索引摘要生成器

新增 `wiki/entity_digest.py`：

```python
def build_entity_digest(
    paths: AgentPaths,
    *,
    max_entities: int = 40,
    relevance_hint: Optional[str] = None,  # 当前 workdir / 当前对话涉及的文件路径等
) -> str:
    """生成一份用于注入抽取 prompt 的极简实体索引：
    id + entity_type + 一句话描述（取页面"概述" section 首句，而非全文）。
    格式示例：
        - ClientPool（模块）：负责多 LLM provider 的 API key 轮换与故障转移
        - GoalMode（模块）：多轮目标追踪与进度判断子系统
        ...
    不返回原始正文，避免占用过多 prompt token；数量上限 max_entities，
    优先级排序依据（复用 O1 分层索引落地后的信度字段，见 §4）：
        1. relevance_hint 命中（当前 workdir 相关的实体优先）
        2. grounded_hit_count 高的实体优先（信度高，见 §4.2）
        3. 最近更新的实体优先
    """
```

- 依赖 O1（§4）落地后的分层索引来做"相关性优先排序"和快速读取（不需要每次全量 parse 所有页面），因此 **E3 的完整实现依赖 O1 先完成**；在 O1 完成之前，可以先用简化版本（直接扫 `entities/` 目录取 frontmatter，不做相关性排序，只取最近修改的 N 篇）作为过渡实现，先把"注入已知实体"这件事跑通，再在 O1 落地后替换排序逻辑。

#### 3.2.2 接入抽取 prompt

`prompts/user/compress_summary_request.md`（或 E1 落地后的独立轻量抽取 prompt）新增一个可选注入段：

```
当前已知实体（如果新识别的实体和下列某一项指代同一个东西，请复用其 id，不要新建）：
{entity_digest}
```

`history/world_extraction.py::EntityCandidate` 新增字段 `reused_existing_id: Optional[str]`（模型如果判断是已有实体，直接填已知 id），`wiki/world_writer.py::consolidate_pending()` 优先信任这个字段做匹配，**未命中时才退回现有的 `dedup.py::find_similar_page` 规则判重**——形成"模型优先判断 + 规则兜底"的两段式，而不是纯规则判重。

#### 3.2.3 成本控制

- `entity_digest` 生成本身零 LLM 成本（纯字符串拼接），但会增加抽取 prompt 的输入 token（40 条实体 × 一句话，量级可控，预计几百 token）。
- 通过 `max_entities` 和 `relevance_hint`（当前 workdir 关联的实体优先）控制注入规模，避免随知识库增长无限膨胀 prompt。

### 3.3 验收标准

- 端到端场景：构造两轮对话，第一轮提到"ClientPool 模块"生成 entity 页面，第二轮用不同措辞（如"客户端池"）提到同一模块的新特性，验证 `reused_existing_id` 命中、事实被追加进同一页面而非新建页面。
- 对照实验：接入 `entity_digest` 前后，统计一段时间内 `dedup.py::find_similar_page` 的命中率变化（命中率下降说明模型自己判断准确率提升，重复候选减少）。

### 3.4 风险与兜底

- `reused_existing_id` 是模型自报的，存在误判风险（模型可能"过度复用"，把两个实际不同的实体误判成同一个）。兜底：`consolidate_pending()` 里即使 `reused_existing_id` 命中，也要用现有 `dedup.py` 的相似度算一次校验分，分数过低（比如 <0.15）则忽略模型的判断，走原有新建/规则判重流程——防止模型的误判被无条件信任。
- prompt 注入失败（`build_entity_digest` 异常）不阻断抽取主流程，静默降级为不注入任何实体索引（等同于当前行为）。

---

## 4. 问题 O1：全量扫描架构没有为增长设计分层

### 4.1 现状问题

`wiki/search.py` / `wiki/dedup.py` 每次调用都对 `wiki/` 目录下**全部**页面执行 `parse_page`。代码注释里承认"当前规模下足够快，真正变慢时可以复用 indexer.py 生成的 tags.json / search_index.json"——但目前没有任何模块真正复用这些已有的派生索引，`indexer.py` 本身其实已经实现了增量索引（`_manifest.json` 记录 mtime+hash，只重解析改动文件），这套能力目前只服务于 `build_index()` 自己，没有被 `search.py`/`dedup.py` 复用。

更深层的问题不只是性能，而是**语义分层缺失**：所有页面在检索粗筛（`_rule_score`）里权重完全相同，新写入、尚未被验证过的 fact，和被反复引用几十次的 decision，参与排序的方式没有任何区别。

> **实施状态：§4.2.1 / §4.2.2 已完成**，详见 `next_doc/wiki提取层改进计划_O1实施记录.md`
> （`wiki/index_reader.py` 复用 indexer 派生索引 + `grounded_hit_count` 信度分层字段 +
> `wiki_index_reuse_enabled`/`wiki_confidence_weight` 配置项 + 单测）。§4.2.3（分区组织）
> 按原计划本就"不在当前阶段实现，只记录演进路径"，维持不变。

### 4.2 改进方案 `[设计]`（§4.2.1/§4.2.2 已完成，§4.2.3 仍为设计阶段）

#### 4.2.1 复用 indexer 的增量索引作为 search/dedup 的数据源

- `wiki/search.py::wiki_shelf_search()` 和 `wiki/dedup.py::find_similar_page()` 改为优先读取 `paths.wiki_search_index` / `paths.wiki_tags_index`（indexer.py 已生成的 JSON 索引），只有索引缺失或明显过期（`_manifest.json` mtime 校验失败）时才退回全量 `parse_page` 扫描——这一步不改变对外行为，只改变数据来源，属于纯性能优化，风险低。
- `indexer.py::build_index()` 的触发时机不变（仍由 `consolidate()` 步骤 6 驱动），但新增一个轻量的"读时校验"：`search.py` 读索引前检查 `_manifest.json` 的 mtime 快照与磁盘实际 mtime 是否一致，不一致的少数文件单独 `parse_page` 补读，其余复用索引——避免"索引落后于实际内容"导致检索结果陈旧。

#### 4.2.2 引入知识信度分层字段

在 wiki 页面 frontmatter 新增字段 `grounded_hit_count: int`（默认 0），来源：

- `wiki/search.py::_llm_rerank()` 每次 LLM 精排返回 `grounded_page_ids` 后，由调用方（`context_builder.py::_try_inject_wiki_search()`）异步回写命中页面的 `grounded_hit_count += 1`（复用 `wiki/writer.py` 现有的 frontmatter 更新能力，走原子写）。
- `search.py::_rule_score()` 新增一项按 `log(1 + grounded_hit_count)` 的加权（对数避免头部页面赢者通吃），公式调整为：

```
score = 0.6 * token_jaccard + 0.4 * tag_jaccard + confidence_weight * log(1 + grounded_hit_count)
```

`confidence_weight` 作为可调参数（默认较小，比如 0.1，避免信度权重压过内容相关性本身），配置项放进 `MemoryConfig`。

- 这一项设计直接复用 P4 阶段已经建好的 `wiki_search_ab_log_path` 观测基础设施，`grounded_hit_count` 的回写和 A/B 采样共享同一个触发点，不新增观测系统。

#### 4.2.3 分区组织（可选，视规模增长再评估）

如果未来实体页面数量增长到全量扫描确实成为瓶颈（用 `/wiki stats` 观测页面总数作为触发信号，而非提前设计），可以考虑按 `wiki_type_dir` 现有的物理目录划分（entities/decisions/experiences/topics）为基础，进一步在 `entities/` 下按项目子系统（daemon/agent/wiki/goal_mode 等，可从 tag 前缀推断）做二级目录分区。**本计划不在当前阶段实现分区，只是记录该演进路径**，避免过早引入目录结构复杂度——这与项目一贯的"规模到了再优化"哲学一致。

### 4.3 验收标准

- 性能对比：构造 200+ 页面的测试 wiki 目录，对比改动前后 `wiki_shelf_search()` 单次调用耗时（预期显著下降，且随页面数增长曲线更平缓）。
- 行为不变性：`tests/test_context_builder_wiki_search_primary.py` 等既有测试全部保持通过——这次改动只优化数据来源和排序权重，不改变检索接口。
- 新增测试：`grounded_hit_count` 回写的幂等性、并发场景下的原子写正确性；`confidence_weight=0` 时排序结果应与改动前完全一致（回归保护）。

### 4.4 风险与兜底

- 索引读取失败（文件损坏、schema 不匹配）静默降级为全量扫描，不影响检索可用性，只影响性能。
- `grounded_hit_count` 回写属于非关键路径，写入失败不影响本次检索结果返回，遵循项目一贯的"失败不阻断主流程"风格。

---

## 5. 问题 O2：实体关系图过于扁平

### 5.1 现状问题

`wiki/graph.py::GraphIndex.expand()` 只支持一跳扩展，`strong_only` 二选一，没有区分"跳数"和"衰减权重"。对于 mini_agent 这种有明显层级依赖的项目（daemon → session → agent → tool），检索命中的候选页面往往只是某条依赖链的中间一环，一跳扩展带不出更深层但确实相关的知识，只能靠"运气好正好一跳可达"。

### 5.2 改进方案 `[设计]`

#### 5.2.1 多跳衰减扩展

`GraphIndex.expand()` 新增参数：

```python
def expand(
    self,
    page_ids: Iterable[str],
    *,
    strong_only: bool = False,
    max_hops: int = 1,
    decay: float = 0.5,
) -> dict[str, float]:
    """返回 {page_id: weight} 而非纯 set。
    第一跳权重为 decay，第二跳为 decay**2，以此类推；
    同一节点通过多条路径可达时取最大权重（不是累加，避免热门节点权重爆炸）。
    max_hops=1 时行为与改动前完全一致（返回值需要调用方从
    dict 转 set 兼容旧调用点，或提供 expand_legacy() 保持向后兼容签名）。
    """
```

- 向后兼容处理：新增 `expand()` 的同时保留 `expand_legacy()`（原签名、返回 `set[str]`），`search.py` 现有调用点先不动，新调用点（比如 §5.2.2 的多跳检索场景）用新签名。待新签名验证稳定后再逐步替换旧调用点，降低一次性改动风险。

#### 5.2.2 接入 search.py

`wiki_shelf_search()` 的图扩展阶段，默认仍用 `max_hops=1`（不改变现有行为和性能特征）；新增一个"深度检索"模式（比如 `/wiki search --deep` 或者当规则粗筛候选数量过少、明显不足以覆盖 `rerank_top_n` 时自动触发）用 `max_hops=2`，把权重字段传给 LLM 精排 prompt，作为候选页面排序的参考信息之一（"该页面是通过 2 跳关系间接关联，权重较低"），而不是无区分地和一跳节点混排。

### 5.3 验收标准

- 单测：`expand()` 多跳衰减权重计算正确性、同节点多路径取最大值而非累加、`max_hops=1` 与 `expand_legacy()` 行为一致性对比。
- 端到端场景：构造一条三层依赖链（A→B→C），验证查询 A 相关问题时，`max_hops=2` 模式下 C 能被检索到且权重低于 B。

### 5.4 风险与兜底

- 多跳扩展在图密度高的情况下候选数量可能爆炸式增长，`expand()` 内部需要设置一个硬上限（比如扩展后候选总数超过 `rerank_top_n * 3` 时提前截断，按权重排序取前 N），避免深度检索模式拖慢 LLM 精排输入规模。

---

## 6. 问题 O3：topic 聚类是纯事后归纳

### 6.1 现状问题

`wiki/topics.py::consolidate_topics()` 只在候选簇凑够 `min_pages` 时生成**新**的 topic 页面，已存在的 topic 页面不会因为后续新增的相关页面而更新——除非新页面又单独凑够阈值触发新一轮聚类，那样会生成一个内容重叠的新 topic 页，靠 `_merge_candidate_pools` 的 Jaccard 去重才勉强不产生重复页面。已有 topic 页面本质上是"某一时刻的静态快照"，会随时间逐渐失真。

### 6.2 改进方案 `[设计]`

#### 6.2.1 新增"再巩固"扫描步骤

`consolidate_topics()` 新增逻辑，在生成新候选簇**之前**先跑一遍已有 topic 的再巩固检测：

```python
def _find_topic_reconsolidation_candidates(
    existing_topic_pages: list[WikiPage],
    new_pages_since_last_run: list[WikiPage],
    *,
    overlap_threshold: float = 0.35,
) -> list[tuple[WikiPage, list[WikiPage]]]:
    """对每个已有 topic 页面，检查其关联实体集合（frontmatter links）
    与新增页面集合的 tag/entity 重合度，超过阈值的新页面视为
    "应当并入该 topic"的候选，而不是参与新一轮独立聚类。
    """
```

- 命中的新页面走类似 `decision_writer.py::_update_existing()` 的"追加 section"逻辑（`wiki/topics.py` 新增 `append_to_topic_page()`，复用 `writer.py::append_section()`），在 topic 页面正文追加一段"新增关联"，并更新 frontmatter 链接列表。
- 参与再巩固的新页面从后续"生成新候选簇"的候选池中剔除，避免同一批页面既被并入已有 topic、又被拿去生成新 topic（两段逻辑共享同一个"已处理页面"排除集合，复用 P3 已有的 `exclude_page_ids` 参数）。

#### 6.2.2 触发频率控制

再巩固扫描成本略高于新聚类（需要遍历所有已有 topic 页面的关联集合），不需要每次 `consolidate()` 都跑。新增 `TopicConfig.reconsolidation_interval_runs: int = 5`，每 5 次巩固循环跑一次再巩固扫描，其余次数只做增量的新候选簇生成——在及时性和成本之间取折中，具体数值可以在真实运行后用 `/wiki stats` 观测调整。

### 6.3 验收标准

- 单测：`_find_topic_reconsolidation_candidates` 对重合度阈值上下的场景判定；`append_to_topic_page` 追加内容后 frontmatter 链接正确更新；新页面被并入已有 topic 后不再出现在新聚类候选池里。
- 端到端场景：构造一个已有 3 篇页面的 topic，之后新增 2 篇高度相关但 tag 各异的页面，验证再巩固扫描后这 2 篇被追加进已有 topic 页面而非生成新页面。

### 6.4 风险与兜底

- 再巩固可能出现"话题漂移"——topic 页面被持续追加内容后语义逐渐偏离最初的主题标签。兜底：`append_to_topic_page()` 设置软上限（比如累计追加次数超过 8 次），超过后提示（写入 frontmatter 的 `needs_review: true` 标记）建议人工/下次 LLM 巩固时考虑拆分该 topic，而不是无限追加下去。

---

## 7. 问题 O4：四类页面缺统一知识生命周期状态机

### 7.1 现状问题

decision/entity/experience/fact（fact 目前依附于 entity 页面的"事实" section，没有独立页面类型）四类内容各自维护自己的状态语义：
- decision 有 `superseded_by` 字段，纠正检测能触发 `mark_stale_from_correction()`。
- entity 有"当前状态" section，但没有结构化的"是否过时"标记。
- experience 完全没有过期机制。
- fact 作为 entity 页面内的一个 section，没有独立状态，纠正检测目前也不覆盖它。

这导致：一条从 world_model 路径写入的 fact 被后续对话证明是错的，没有任何机制能把它标记为过时；纠正检测目前只覆盖 decision 页面来源的内容，覆盖面不完整。

### 7.2 改进方案 `[设计]`

#### 7.2.1 抽象统一的最小状态字段集合

在所有 wiki 页面 frontmatter 新增共享字段（不区分 page_type）：

```yaml
confidence: fresh        # fresh | stale | superseded
last_validated_at: 2026-07-18T00:00:00Z   # 最近一次被确认仍然有效的时间
validated_by: []          # 触发确认的来源类型列表，如 ["correction_check", "grounded_hit"]
```

- `fresh`：默认状态，新写入或最近被验证过。
- `stale`：被检测到可能过时（比如长期未被 grounded 命中、或关联的代码路径已变化），但尚未有明确纠正证据。
- `superseded`：有明确证据（纠正检测命中、或 decision 的 `superseded_by` 链）证明已被取代。

#### 7.2.2 统一状态更新接口

新增 `wiki/lifecycle.py`：

```python
def mark_page_state(
    paths: AgentPaths,
    page_id: str,
    *,
    confidence: str,
    reason: str,
    validated_by: str,
) -> bool:
    """跨页面类型的统一状态更新入口，内部按 page_type 分发到具体的
    frontmatter 更新逻辑（复用 writer.py 的原子写能力），
    不要求调用方关心目标页面是 decision/entity/experience 哪一种。
    """
```

- `agent/reminders_correction.py::mark_stale_from_correction()` 改造为调用 `mark_page_state(..., confidence="superseded")`，覆盖面从"仅 decision"扩展到"任意页面类型"——只要该纠正事件的 `related_entities`/`source_entries` 能血缘追溯到某个页面（血缘机制已存在，见 §6.5 的 `source_entries` 设计），就能统一标记。
- §4.2.2 的 `grounded_hit_count` 回写逻辑顺带更新 `last_validated_at` 和 `validated_by`（追加 `"grounded_hit"`），形成"被检索命中即视为一次隐式验证"的正反馈。
- 新增一个巡检任务（可以挂在 `consolidate()` 里作为新步骤，或独立定时任务）：`stale_candidate_scan()`，对 `last_validated_at` 超过配置阈值（比如 90 天）且从未被 `grounded_hit` 命中过的页面，自动标记为 `confidence="stale"`（不是 `superseded`，因为没有明确反证，只是"久未验证"），供后续检索排序（§4.2.2 的信度权重公式里，`stale` 页面的 `confidence_weight` 应该打折甚至归零）参考。

#### 7.2.3 fact 独立状态化

目前 fact 依附在 entity 页面的 section 里，没有独立 id，无法单独标记状态。改进：`wiki/world_writer.py::queue_facts()` 落盘时给每条 fact 生成一个页面内锚点 id（比如 `entity-id#fact-3`），`mark_page_state()` 支持这种"页面内锚点"粒度的状态标记（只更新对应 section 的内联标记，比如在 section 内容前加 `<!-- confidence: stale -->` 注释标记，不需要为每条 fact 单独开一个物理页面，避免页面数量爆炸）。

### 7.3 验收标准

- 单测：`mark_page_state` 对 decision/entity/experience 三类页面的分发正确性；fact 级别的锚点标记读写。
- 端到端场景：一条被纠正检测命中的 fact（而非 decision）能被正确标记为 `superseded`，且后续检索排序中该 fact 所在 section 不再被 LLM 精排优先引用（prompt 里可以直接排除标记为 superseded 的 section 内容，而不只是排除整个页面）。
- 回归：现有 `mark_stale_from_correction()` 对 decision 页面的行为保持不变（新实现是现有实现的超集，不是替换语义）。

### 7.4 风险与兜底

- 这是本计划里改动面最广的一项（涉及四类页面的写入路径），必须放在 O1-O3、E1-E3 都验证稳定后再做，降低同时改动多处基础设施的风险。
- `stale_candidate_scan()` 的阈值（90 天）需要结合真实运行数据校准，初期建议只记录不影响排序（先跑一段时间观察 stale 标记的比例是否合理），避免像 P4 §6.5 那样在没有数据支撑的情况下就让新状态直接影响检索行为。

---

## 8. 总体实施排期建议

| 阶段 | 内容 | 预计工作量 | 前置依赖 | 状态 |
|---|---|---|---|---|
| 第一批 | O1（索引复用 + 信度分层字段，不含分区） | 2-3 天 | 无 | **已完成**（`wiki提取层改进计划_O1实施记录.md`） |
| 第一批 | E2 方案 B（schema 字段顺序调整 + 观测对比） | 0.5 天 | 无，可与 O1 并行 | **已完成**（`wiki提取层改进计划_E2方案B实施记录.md`） |
| 第二批 | E3（实体索引反向注入抽取 prompt，直接实现完整版，O1 已就绪） | 2-3 天 | 建议 O1 完成后做完整版，过渡版可提前 | **已完成**（`wiki提取层改进计划_E3实施记录.md`） |
| 第二批 | E1（抽取时机解耦，独立触发器 + cursor 机制） | 3-4 天 | 无强依赖，可与 E3 并行 | 待实施 |
| 第三批 | E2 方案 C（compact 与独立抽取路径的开关切换） | 1 天 + 观测期 | 依赖 E1 完成并观测稳定 | 待实施 |
| 第三批 | O2（多跳衰减图扩展） | 2 天 | 依赖 O1 | 待实施 |
| 第三批 | O3（topic 再巩固） | 2 天 | 依赖 O1 | 待实施 |
| 第四批 | O4（统一知识生命周期状态机） | 3-4 天 | 依赖 O1-O3、E1-E3 均验证稳定 | 待实施 |

> 当前进度：第一批（O1、E2方案B）与第二批中的 E3 已完成，下一项建议是第二批剩余的
> E1（抽取时机解耦）——完成后 E2 方案 A 自然被解决（见 §2.2 说明），并为方案 C 的观测期打底。

> 每一批完成后都应该跑一段真实使用周期，用 `/wiki stats` / `/wiki promotion` 现有观测工具（以及本计划新增的 `avg_entities_per_extraction` 等指标）采集数据，确认改动方向有效后再进入下一批——这是吸取 P4 §6.5"零数据切换"教训后，本计划贯穿始终的执行纪律。

---

## 9. 风险汇总

- 所有新增写入/状态更新路径遵循项目一贯的"失败不阻断主流程"风格（try/except 吞掉异常，静默降级）。
- 任何涉及默认行为切换的改动（尤其 E2 方案 C、O4 的 stale 标记影响排序）**默认不开启新行为**，需要观测期数据支撑后手动开启，不重复 P4 §6.5 的路径。
- O2/O3/O4 均依赖 O1 的分层索引基础设施，建议严格按 §8 的顺序推进，不要并行开工跨批次的项目，避免基础设施变动时下游多处同时受影响、难以定位问题。
