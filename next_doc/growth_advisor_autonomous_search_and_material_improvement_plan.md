# 成长顾问：自主检索与学习素材生成改进计划

- **版本**: v1
- **前置文档**:
  - `next_doc/growth_advisor_design.md`（原始方案）
  - `next_doc/growth_advisor_research_quality_plan.md`（阶段 1-4，报告从
    "计数"升级为"摘录"、两段式生成、来源标注、忽略原因驱动调整）
  - `next_doc/growth_advisor_active_search_and_lifecycle_plan.md`（方向一：
    `report_active_search_enabled` 主动检索的最初落地）
  - `next_doc/growth_advisor_cron_search_and_status_history_plan.md`（方向
    一：cron 路径主动检索预算调度）
  - `next_doc/growth_advisor_improvement_plan_v4.md`（N4：外部资讯作为
    展示/报告背景）
- **触发背景**：针对"如何更好地自主根据用户需求进行自主检索、生成相关
  报告和学习素材"这个问题做的专项复盘。当前 `report_active_search_enabled`
  /`cron_triggered_active_search_enabled` 已经把主动检索这条能力从 0 到 1
  跑通，但深入读代码后发现检索策略、素材沉淀、报告与学习素材的定位这几个
  环节都还比较初级，本文档给出具体改进方向和分期实施计划。

---

## 0. 现状回顾（问题診断）

读 `src/mini_agent/evolution/growth_advisor.py` 里
`_active_search_excerpts_for_topic()` / `_maybe_run_cron_triggered_active_
search()` / `generate_growth_report()` / `reports_needing_refresh()` 这条
链路，定位到五类问题：

1. **检索策略偏浅偏窄**：单次单查询（标题 + 关键词表第一个关键词），没有
   基于 `candidate.rationale` 做多角度查询分解；`report_active_search_
   max_calls` 字段虽然已经预留（见 `config/models.py` 注释"预留字段供以后
   扩展为多个关键词各查一次时使用，不代表当前已支持多轮"），但从未被真正
   消费——当前实现无论配置成几，实际只发起 1 次检索
2. **触发条件是"有没有"不是"新不新"**：只在被动扫描命中数为 0 时才触发
   检索，已有 1 条历史命中就永久跳过，检索只解决"从无到有"、不解决"从旧
   到新"
3. **检索结果没有真正被用上**：`_active_search_excerpts_for_topic()` 已经
   调用抽取管道拿到了结构化的 `EntityCandidate`/`FactCandidate` 列表（用于
   `queue_entities`/`queue_facts`），但返回给报告生成的摘录只是**原始检索
   文本**截断 150 字（`excerpt = " ".join(str(raw_text).split())[:150]`），
   完全没有用抽取出来的结构化内容，等于"查了、抽取了，但报告里引用的还是
   没处理过的原始文本"
4. **单条摘录，证据基础薄**：无论被动扫描还是主动检索，喂给报告生成的
   摘录数量固定为 1-2 条、每条 150 字，报告的"事实依据"体量偏小
5. **报告和学习素材是同一个模板**：现在只有一种产物——≤500 字的简报式
   Markdown，不区分"值不值得投入"（决策向）和"投入之后怎么学"（执行向）

本计划聚焦 1、2、3、4（检索与素材沉淀这条链路本身），第 5 点改动面更大、
涉及产物形态改变，本文档第 4 节给出方向级规划，不在本轮实施。

---

## 1. 阶段一：检索结果真正沉淀使用（已实施）

**问题**：`_active_search_excerpts_for_topic()` 内部已经把 `raw_text` 喂给
`_build_search_extraction_prompt()` 抽取出 `entities`/`facts`，这些结构化
候选目前只用于 `queue_entities()`/`queue_facts()` 落盘到 pending 队列（要
等后续 `consolidate_pending()` 巩固循环才变成正式 wiki 页面），但报告生成
当次消费的摘录仍然是**未经处理的原始检索文本**截断。

**方案**：改为优先用已经抽取出的结构化 `entities`/`facts` 构造摘录——每条
`EntityCandidate.description`/`FactCandidate.statement` 本身就是 LLM 已经
提炼过的、跟主题相关的独立信息点，比原始文本截断信息密度更高、也更符合
"标注来源"的报告 prompt 要求（可以精确到"这条来自哪个实体/事实"）。

- 新增 `_excerpts_from_extracted_candidates()`：把 `entities`/`facts` 转成
  `[{"id": ..., "date": ..., "excerpt": ...}]` 格式，`id` 形如
  `active_search:<query>#entity:<name>` / `active_search:<query>#fact:<序号>`，
  单条摘录截断到 200 字（比原来的 150 字上限略宽松，因为这些是已经提炼过
  的独立信息点，不是从长文里硬截）
- **不改变落盘行为**：`queue_entities()`/`queue_facts()` 调用点不变，仍然
  走 pending 队列 + 后续 consolidate 巩固，本次改动只影响"当次报告消费什么
  内容"，不影响 wiki 正式知识库的沉淀节奏
- **保留兜底**：如果这次抽取没有产出任何有效 entities/facts（`is_meaningful`
  均为 False，比如检索结果是纯噪音），退回原来的"原始文本截 150 字"摘录，
  保证任何情况下都不会比改动前拿到更少的信息，向后兼容
- 摘录条数上限从固定 1 条改为 `max_excerpts_per_call`（默认 3），一次检索
  只要抽取质量够，天然能提供更多条摘录

## 2. 阶段二：多角度查询（激活 `report_active_search_max_calls`，已实施）

**问题**：`report_active_search_max_calls` 字段存在但从未被消费，检索永远
只发起 1 次、只用关键词表第一个关键词。

**方案**：

- `_active_search_excerpts_for_topic()` 新增 `max_calls: int = 1` 参数
  （默认值维持改动前行为——只查 1 次）
- 查询构造：第一次沿用 `candidate.title + keywords[0]`（不变）；如果
  `max_calls > 1` 且关键词表还有更多关键词，追加 `candidate.title +
  keywords[i]` 作为第 2、3…次查询的角度，数量不超过 `max_calls` 也不超过
  `len(keywords)`；关键词不够时不强行拼凑重复查询（宁可少查，不做无意义
  重复调用）
- 每次调用独立走"检索 → 抽取 → 阶段一摘录构造"的完整流程，多次调用的摘录
  合并去重（按 `id` 去重，理论上不同 query 抽出同名实体的情况会被合并成
  一条，不重复计入证据条数）后统一返回，仍然受 `max_excerpts_per_call` 
  （现在语义调整为"摘录总数上限"）的整体上限约束，避免摘录列表随
  `max_calls` 线性膨胀把报告 prompt 撑爆
- 单次调用失败（`web_search_fn` 异常/空结果）不影响其它角度的查询，各自
  独立 try/except，某一次查询失败只是那一个角度没有摘录，不影响整体
- `generate_growth_report()` 和 `_maybe_run_cron_triggered_active_search()`
  两个调用点都从 `cfg.report_active_search_max_calls` 读取并透传，`cfg`
  为 `None` 或字段缺失时退回默认值 1，向后兼容所有不传 `cfg` 的既有调用
  方（例如测试里直接调用 `_active_search_excerpts_for_topic()` 不传
  `max_calls` 的用例）
- **成本边界**：这一步默认值仍是 1（跟改动前一致），只有用户显式把
  `report_active_search_max_calls` 调大于 1 才会增加 `web_search_fn` 调用
  次数，符合"增加调用成本的能力默认不放大"的一贯原则——本身
  `report_active_search_enabled`/`cron_triggered_active_search_enabled`
  已经是 opt-in，这里只是让已经 opt-in 的用户能进一步控制"查几个角度"，
  不引入新的默认开销

## 3'. 阶段三：生成后自检（已实施）

**问题**：第 4 节此前列出的"生成后自检"——报告正文写完后，回头核对是否
真的引用了摘录内容、标注是否属实，之前只在 prompt 里"要求"模型做，没有
验证步骤。阶段一、二让检索和摘录构造本身更扎实了，但"模型有没有真的按
要求标注来源"仍然是纯粹依赖模型自觉，缺一步事后核对。

**方案**：

- `generate_growth_report()` 新增局部变量 `used_excerpts`，记录本次
  真正拼进 prompt 的摘录列表（阶段一/二产出的 `excerpts`）；只有走了
  `report_include_external_context` 分支且拿到非空摘录才会被设置，
  其余情况维持空列表，不影响任何既有行为。
- 新增纯函数 `_check_report_citations(body, excerpts) -> dict`：用正则
  `[（(]\s*参考[：:]\s*([^）)]+)[）)]` 提取正文里所有『（参考：xxx）』
  标注，跟 `excerpts` 的 `id` 列表做**双向子串匹配**（`ref in eid` 或
  `eid in ref`）——因为 prompt 只要求"标注页面 id"，没规定必须逐字
  复制完整 id，模型给出简写（比如只保留 `active_search:...#entity:
  pandas` 里的 `pandas`）应该算"对得上"而不是"编造"，双向子串匹配是
  在"漏判个别巧合的编造"和"把合理简写误判成编造"之间选择优先控制
  误报率。返回 `excerpts_total`/`cited_count`（同一条摘录被引用多次
  只计一次）/`citation_mentions_total`（标注出现的总次数）/
  `hallucinated_refs`（对不上任何摘录 id 的引用原文，截断到 5 条、
  每条 60 字）。
- `GrowthReport` 新增 `citation_check: Optional[dict] = None` 字段，
  只有正文由 LLM 生成（`source == "llm"`）且 `used_excerpts` 非空时才
  会调用 `_check_report_citations()` 并写入这个字段；未开启外部背景、
  没拿到摘录、或走规则模板兜底路径时保持 `None`——`None` 表示"这次
  没有可核对的引用"，跟"核对后确认没有编造"（`hallucinated_refs`
  为空列表）是两种不同的语义，不能混为一谈。
- **不做任何自动纠正或阻断**：`citation_check` 纯粹是诊断字段，不
  参与候选排序、不影响报告是否落盘，也不会因为检测到"编造引用"就
  重新生成或拒绝写入——按照"仅展示、不影响判断"的一贯设计原则，怎么
  处理这个诊断信息交给下游（看板/CLI，本轮未接入展示）决定。

## 3''. 阶段三后续：生成后自检结果的展示（已实施）

**背景**：阶段三把"引用是否对得上"落到了 `GrowthReport.citation_check`
字段，但当时只做诊断记录，没有接入任何展示，属于"算了但没人看得到"。

**方案**：

- `diagnostics_snapshot()` 新增 `citation_check` 区块（新增
  `_citation_check_diagnostics_summary()`）：汇总活跃索引里带
  `citation_check` 的报告——`reports_checked`（分母）、`reports_with_
  hallucination`（至少一条编造引用的报告数）、`total_excerpts_offered`/
  `total_excerpts_cited`（加总摘录数/命中数）、`citation_hit_rate`
  （总体命中率；分母为 0 时是 `None`，跟"命中率 0%"区分开）。纯只读
  聚合，异常时退化为"看不出数据"的默认结构，不拖垮整个诊断面板。
- `GET /growth/reports/{id}` 不需要改动——`report.to_dict()` 已经
  自动带上新增的 `citation_check` 字段，接口原样透出。
- CLI `/growth report <candidate_id>` 打印报告正文后，若这份报告带
  `citation_check`，追加一行摘要（引用命中比例 + 编造引用列表，或
  "未检测到编造引用"）；不带该字段（多数场景）时不打印任何额外内容，
  不改变既有输出格式。

## 3'''. 报告与学习素材分层（已实施）

**问题**：此前"报告"是唯一产物，兼顾"值不值得投入"（决策向）和"投入
之后怎么学"（执行向）两种诉求，写得笼统就两头都不够用。

**方案**：

- 新增 `GrowthLearningMaterial` dataclass（`material_id`/`candidate_id`/
  `title`/`slug`/`learning_path`/`resources`/`first_task`/`body_path`/
  `created_at`/`source`/`based_on_report_id`），跟 `GrowthReport` 平行
  但独立，故意不共用一个 dataclass——字段语义不同（报告是自由格式
  正文 + 摘要，素材是结构化字段 + 拼出来的正文）。
- 新增 `generate_learning_material(paths, candidate, *, llm_helper=None,
  report=None)`：
  - `report` 可选：传入时复用报告 `summary` 作为素材背景（不重复归纳
    "为什么值得关注"）；不传时用 `candidate.rationale` 兜底——素材不
    强制依赖报告存在，先后顺序随意。
  - `llm_helper` 非 `None` 时要求返回结构化 JSON（`learning_path`/
    `resources`/`first_task`），兼容代码块包裹（` ```json ... ``` `）
    和"JSON 前后有多余文字"两种常见偏差（用正则兜底提取 `{...}`）；
    解析失败、异常、空响应、或关键字段缺失（`learning_path` 为空或
    `first_task` 为空）都静默退回规则模板，不抛错、不生成半成品。
  - 规则模板兜底：`_default_learning_path()` 给出三步通用骨架（检索
    入门资料建立轮廓 → 挑小切口动手尝试 → 记录卡点留待下次解决），
    加两条通用资源提示和一条"检索并写下 3 个具体问题"的默认任务，
    保证任何情况下都有非空、可直接照做的产物。
  - 生成后写入 `.agent/wiki/growth/<slug>-material.md`、追加进
    `.agent/growth_materials.jsonl` 索引、通过新增的
    `GrowthBacklog.attach_material()` 回填候选的 `material_id` 字段
    （`GrowthCandidate` 新增该字段，跟 `report_id` 平行独立，旧数据
    反序列化自然落到 `None`）。
- 新增 `list_materials()`/`get_material_by_id()`，跟 `list_reports()`/
  `get_report_by_id()` 对称；素材数量远小于报告，暂不需要归档机制。
- **入口**：
  - CLI `/growth material <candidate_id>`：候选没有素材时生成并展示，
    已有时直接展示、不重复生成（跟 `/growth report` 的行为对称）。
  - API `POST /growth/candidates/{id}/material/generate`：每次调用都
    生成一份新的（不是"刷新替换"语义，多次生成的历史都保留在索引里，
    候选上挂着的 `material_id` 指向最新一份）+ `GET /growth/materials/
    {id}`（返回结构化字段 + 正文，跟 `GET /growth/reports/{id}` 对称）。
- **刻意不做的事**：素材没有接入报告专属的阶段一/二/三能力（外部背景
  摘录、生成后自检、"该不该刷新"判断）——这些是报告这个产物形态下
  解决"标注来源真实性"问题的机制，素材是全新的结构化产物，是否需要
  对齐这些能力取决于实际使用情况，本轮不预先假设；看板暂未接入学习
  素材的展示入口，目前只有 CLI/API。

## 3. 测试与回归

- 新增 `tests/test_growth_advisor_active_search_material.py`：
  - 阶段一：抽取到有效 entities/facts 时摘录来自结构化候选而非原始文本；
    抽取为空时退回原始文本摘录；摘录条数不超过 `max_excerpts_per_call`；
    `queue_entities`/`queue_facts` 落盘行为不受影响（仍然被调用、参数不变）
  - 阶段二：`max_calls=1`（默认）行为与改动前一致（只调用一次
    `web_search_fn`）；`max_calls=3` 时按关键词表数量发起对应次数的查询、
    关键词不够时不重复查询；单次查询异常不影响其它角度；多次调用的摘录
    按 `id` 去重
  - `generate_growth_report()`/`_maybe_run_cron_triggered_active_search()`
    两个调用点正确透传 `cfg.report_active_search_max_calls`，`cfg` 缺失
    该字段时退回 1
- 新增 `tests/test_growth_advisor_report_citation_check.py`：
  - `_check_report_citations()` 纯函数：完全命中、简写 id 仍算命中、
    编造引用被记入 `hallucinated_refs`、无任何引用、同一摘录多次引用
    只计一次
  - `generate_growth_report()` 端到端：引用命中、编造引用、外部背景
    关闭、没拿到摘录、模板兜底五种路径下 `citation_check` 的取值；
    该字段随 `to_dict()`/`from_dict()` 正确序列化、旧数据缺字段时
    反序列化落到 `None`
- 跑存量 `tests/test_growth_advisor*.py`、
  `tests/test_growth_advisor_active_search_and_lifecycle.py`、
  `tests/test_growth_advisor_cron_search_and_status_history.py`、
  `tests/test_growth_cmd_timeline_and_active_search_wiring.py` 相关
  文件确认无回归
- 新增（同一测试文件追加）：`_citation_check_diagnostics_summary()`
  在无报告/多报告混合命中与编造场景下的聚合结果；`diagnostics_
  snapshot()` 包含 `citation_check` 键；CLI `/growth report` 在报告带
  `citation_check` 时打印自检摘要、不带时不打印额外内容
- 新增 `tests/test_growth_advisor_learning_material.py`（18 项）：
  - 规则模板兜底路径产出非空三段结构、正文包含三个小节标题；索引
    落盘、候选 `material_id` 被回填
  - LLM 路径：结构化 JSON 被正确采用；代码块包裹的 JSON 被正确解析；
    非 JSON 响应/缺关键字段/异常/空响应四种偏差都退回规则模板
  - 素材基于已有报告生成时复用报告 `summary` 作为背景、
    `based_on_report_id` 正确回填；不传 `report` 时用候选
    `rationale` 兜底、`based_on_report_id` 为 `None`
  - `list_materials()`/`get_material_by_id()` 基本读取行为（含未知
    id 返回 `None`、默认空列表）
  - `GrowthLearningMaterial`/`GrowthCandidate.material_id` 的序列化
    往返及旧数据缺字段时的默认值
  - CLI `/growth material`：候选无素材时生成并展示、已有素材时直接
    展示不重复生成、未知候选报错不抛异常

## 4. 后续方向（未实施，方向级规划）

以下方向改动面更大或需要先观察实际效果再决定，本轮不实施：

- **外部世界变化驱动的刷新**：`reports_needing_refresh()` 目前只看用户
  自己新增的记忆证据，不看外部世界本身是否发生变化；可以考虑复用阶段一
  新增的结构化摘录做"跟上次报告生成时的摘录内容比对，差异明显才提示刷新"，
  但需要先积累阶段一落地后的实际数据再评估值不值得做
- **生成后自检结果的自动利用**：目前自检结果（`citation_check`）已经
  接入诊断面板和 CLI 展示，但还没有基于自检结果做任何自动动作——例如
  "某个方向的报告编造引用比例持续偏高，就换更强的 prompt 或提高
  `report_two_stage_enabled` 优先级重新生成一次"，属于比"展示"更进一步
  的"利用"，建议先观察展示上线后的实际数据分布，再决定要不要做
- **学习素材对齐报告的能力**：学习素材目前是全新产物，没有接入外部
  背景摘录、生成后自检、"该不该刷新"这些报告专属能力；也没有接入
  看板展示入口（目前只有 CLI/API）。是否需要对齐取决于实际使用情况，
  建议先观察素材的实际生成/查看频率再决定

---

## 5. 实施记录

- **状态**：阶段一、阶段二、阶段三（含"生成后自检结果的展示"）、以及
  "报告与学习素材分层"均已实施完成。详细摘要见 `next_doc/growth_
  advisor_implementation_record.md`"自主检索与学习素材生成改进"一节
  （避免跟本文档重复维护同一份内容）。
- **改动文件**：
  - `src/mini_agent/evolution/growth_advisor.py`（`_active_search_
    excerpts_for_topic()` 重构，新增 `_excerpts_from_extracted_
    candidates()`/`_run_single_active_search_query()`/`_build_active_
    search_queries()`；两个调用点透传 `max_calls`；`GrowthReport` 新增
    `citation_check` 字段，新增 `_check_report_citations()`，
    `generate_growth_report()` 记录 `used_excerpts` 并在正文生成后
    调用自检；`diagnostics_snapshot()` 新增 `citation_check` 区块，
    新增 `_citation_check_diagnostics_summary()`；`GrowthCandidate`
    新增 `material_id` 字段；新增 `GrowthLearningMaterial` dataclass、
    `generate_learning_material()`/`list_materials()`/
    `get_material_by_id()`；`GrowthBacklog` 新增 `attach_material()`）
  - `src/mini_agent/storage/paths.py`（新增 `growth_materials_index_
    path` 属性、`growth_material_path()` 方法）
  - `src/mini_agent/config/models.py`（`report_active_search_max_calls`
    注释更新为"已激活"）
  - `src/mini_agent/cli/commands/growth_cmd.py`（`/growth report` 打印
    正文后追加自检摘要；新增 `/growth material <id>` 子命令）
  - `src/mini_agent/cli/parser.py`（帮助文本新增 `/growth material` 行）
  - `src/mini_agent/api/routes.py`（新增 `POST /growth/candidates/{id}/
    material/generate`、`GET /growth/materials/{id}`）
  - `tests/test_growth_advisor_active_search_material.py`（新增，11 项，
    阶段一/二）
  - `tests/test_growth_advisor_report_citation_check.py`（新增，16 项，
    阶段三 + 展示）
  - `tests/test_growth_advisor_learning_material.py`（新增，18 项，
    报告与学习素材分层）
  - `docs/growth-advisor-guide.md`（5.6 节新增 + 更新，纳入阶段三与
    展示；新增 5.7 节说明学习素材分层；配置表格行）
  - `next_doc/growth_advisor_implementation_record.md`（实施摘要更新）
- 第 4 节剩余方向（外部世界变化驱动刷新、自检结果的自动利用、学习
  素材对齐报告能力）仍未实施，维持方向级规划。
