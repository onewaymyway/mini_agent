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
- 跑存量 `tests/test_growth_advisor*.py`、
  `tests/test_growth_advisor_active_search_and_lifecycle.py`、
  `tests/test_growth_advisor_cron_search_and_status_history.py` 相关文件
  确认无回归

## 4. 后续方向（未实施，方向级规划）

以下方向改动面更大或需要先观察阶段一/二实际效果再决定，本轮不实施：

- **报告与学习素材分层**：现在"报告"（决策向简报）和"落地成 Goal 后的
  周期执行内容"之间没有按用户投入程度分层的"学习素材"产物（结构化路径 +
  资源清单 + 首个可执行任务）。改动面涉及新的产物形态和落盘结构，建议
  单独立项设计
- **外部世界变化驱动的刷新**：`reports_needing_refresh()` 目前只看用户
  自己新增的记忆证据，不看外部世界本身是否发生变化；可以考虑复用阶段一
  新增的结构化摘录做"跟上次报告生成时的摘录内容比对，差异明显才提示刷新"，
  但需要先积累阶段一落地后的实际数据再评估值不值得做
- **生成后自检**：报告正文写完后，回头核对是否真的引用了摘录内容、标注
  是否属实，目前只在 prompt 里"要求"模型做，没有验证步骤

---

## 5. 实施记录

- **状态**：阶段一、阶段二均已实施完成。详细摘要见
  `next_doc/growth_advisor_implementation_record.md` "自主检索与学习
  素材生成改进" 一节（避免跟本文档重复维护同一份内容）。
- **改动文件**：
  - `src/mini_agent/evolution/growth_advisor.py`（`_active_search_
    excerpts_for_topic()` 重构，新增 `_excerpts_from_extracted_
    candidates()`/`_run_single_active_search_query()`/`_build_active_
    search_queries()`；两个调用点透传 `max_calls`）
  - `src/mini_agent/config/models.py`（`report_active_search_max_calls`
    注释更新为"已激活"）
  - `tests/test_growth_advisor_active_search_material.py`（新增，11 项）
  - `docs/growth-advisor-guide.md`（新增 5.6 节 + 配置表格行）
  - `next_doc/growth_advisor_implementation_record.md`（新增实施摘要）
- 第 4 节列出的后续方向（学习素材分层、外部世界变化驱动刷新、生成后
  自检）仍未实施，维持方向级规划。
