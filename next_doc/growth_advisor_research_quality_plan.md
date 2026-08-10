# 成长顾问：调研信息获取与整理改进方案

> 前置阅读：`next_doc/growth_advisor_design.md`（P1 原始方案）、
> `next_doc/growth_advisor_improvement_plan_v4.md`（方向二 2.3/2.4 节，
> 外部资讯计数的原始实现）、`docs/growth-advisor-guide.md`、
> `src/mini_agent/evolution/growth_advisor.py::generate_growth_report()`、
> `src/mini_agent/wiki/world_writer.py`（`external_watch`/`external_search`
> 来源标记）。

## 0. 问题

`generate_growth_report()` 现在的调研报告生成本质是"一个 prompt 直接
让 LLM 现编 500 字四段式内容"：

- 喂给 LLM 的输入只有 `candidate.title` + 一句 `rationale`，外部资讯
  即使 `report_include_external_context=True` 也只是一个**数字**
  （"最近 30 天大约有 12 条相关资讯"），页面本身的实际内容完全没被
  用上——等于告诉 LLM"外面有资料"却不给它看，报告正文只能靠 LLM 的
  训练知识现编，跟真实、最新的信息脱节，也没有任何可供用户验证的
  信息来源。
- 一次性要求 LLM 输出"为什么值得关注/怎么入门/常见资源/投入周期"
  四个小节，容易写成放之四海皆准的通用建议，跟用户自己的处境无关。
  这也是 `report_not_useful` 反馈里 `too_generic`（内容太笼统）这个
  忽略原因存在的直接原因——但目前这个反馈只是"记录下来给人看"
  （2.7 节明确标注留给未来），完全没有反过来影响下一次怎么生成。

## 1. 目标（本轮范围，已全部实施完成）

在 `generate_growth_report()` 现有框架内做增量改动，不引入新的存储
文件、不改变候选置信度/排序逻辑（延续"外部资讯只做展示补充，不参与
判断"的既有边界）：

- **阶段 1：外部资讯从"计数"升级为"摘录"** — 报告生成时真正读取
  命中的 wiki 页面内容片段，而不只是数一数有几条。
- **阶段 2：两段式生成（先提纲、后填充）** — 先让 LLM 针对候选主题
  提出几个具体问题，再逐个回答，替代"一次性要求四段通用内容"。
- **阶段 3：报告标注信息来源** — 阶段 1 的摘录如果被采纳进正文，
  标注引用来源（页面 id + 日期），用户能自己判断可信度。
- **阶段 4：忽略原因驱动针对性调整** — 复用已有的
  `_report_quality_dismiss_counts()`：如果这个方向之前的报告被标过
  "内容太笼统"，下次生成时明确要求 LLM 避免重蹈覆辙。

非目标（本轮不做，留给后续，理由见各自小节）：
- **跨候选知识复用**（多个相近主题共享检索/调研素材）——需要先有
  主题相似度判断能力，工作量接近一个独立的语义匹配模块，跟本轮
  "在现有生成链路里增量提质"的范围不匹配，留给后续单独排期。
- **报告新鲜度叠加"素材过时"信号**（`reports_needing_refresh()`
  目前只看证据数变化，本轮不追加"引用资讯是否过时"这个新维度）——
  需要先有阶段 1/3 落地、真正开始记录引用来源之后，才有"素材新鲜度"
  这个概念可用，属于自然的下一步，但本轮先把"有没有真实素材"这个
  更基础的问题解决。
- **月度复盘接入报告质量趋势**（"最近 too_generic 比例是不是在
  上升"）——同样需要阶段 4 先跑起来积累一段时间数据后才有趋势可看，
  本轮不做。
- **一次性触发新的外部检索**（候选没有可用 wiki 素材时主动调
  `web_search`）——现有 `_external_signal_count_for_topic()`
  是纯读取 wiki 现有页面，不涉及任何新的检索调用；引入"报告生成时
  触发检索"涉及调用方是否具备 web_search 工具、检索结果如何落盘复用
  等更大的设计面，留给后续单独讨论。本轮的"摘录"只从**已经存在**的
  wiki 页面（tech_radar 巡检 / 其它渠道产生的）里提取，不新增检索。

## 2. 阶段 1：外部资讯从"计数"升级为"摘录"

现有 `_external_signal_count_for_topic()` 遍历 wiki 页面、按关键词
匹配、返回命中数量。重构成两个函数共享同一段"找到命中页面"的逻辑：

- `_external_signal_matching_pages(paths, topic, keywords, *,
  window_days=30) -> list[WikiPage]`：抽出原函数里的遍历 + 过滤逻辑，
  返回匹配到的页面对象列表（按 `page.updated or page.created` 倒序），
  而不是直接返回数量。
- `_external_signal_count_for_topic(...)`：改成
  `len(_external_signal_matching_pages(...))`，**行为完全不变**，
  是纯粹的内部重构，不影响任何现有调用方/测试。
- 新增 `_external_signal_excerpts_for_topic(paths, topic, keywords, *,
  window_days=30, max_excerpts=2) -> list[dict]`：取
  `_external_signal_matching_pages()` 结果的前 `max_excerpts` 条，
  每条截取正文前 ~150 字（去除多余空白）作为摘录，连同 `page.id` 和
  日期一起返回：`{"id": ..., "date": ..., "excerpt": ...}`。

`generate_growth_report()` 里 `report_include_external_context=True`
且 `llm_helper` 不为空时，`external_context_section` 除了保留原有的
"大约 N 条相关资讯"这句话，额外拼上摘录（每条一行，格式见阶段 3），
并明确告知 LLM"可以参考这些摘录，但不要照抄，仍然要结合用户自己的
处境"（延续"仅供了解、不改变判断"的既有措辞原则，只是这次真的给了
可参考的内容）。这一步不新增配置开关——`report_include_external_
context` 已经是这个能力的总开关，此次是让它名副其实，而不是新增一个
容易让人困惑的近义开关。

## 3. 阶段 2：两段式生成（先提纲、后填充）

新增配置 `report_two_stage_enabled: bool = False`（默认关闭——两段式
意味着两次 LLM 调用，成本翻倍，遵循"增加调用成本的能力默认 opt-in"
的一贯原则，跟 `llm_signal_augment_enabled` 等既有开关同等语义）。

打开后，`generate_growth_report()` 在 `llm_helper` 可用时：

1. **提纲阶段**：向 LLM 提问"针对这个候选主题和已知信息，这份报告
   应该重点回答哪 3-4 个具体问题"（不是"怎么入门"这种泛泛的问题，
   而是要求具体，比如"从 Python 转 Go 需要先补哪些基础"）。只输出
   JSON 字符串数组。调用结果记入新增的 LLM 调用状态类型
   `report_outline`（跟 `signal_augment`/`report_quality`/
   `topic_category`/`goal_alignment_match` 并列，同样接入诊断面板
   的"LLM 增强调用状态"区块）。
2. **正文阶段**：把提纲阶段得到的问题列表拼进最终 prompt，明确要求
   "逐一回答以下问题"，替代原来固定的"四个小节"结构。

任何一步解析失败/空响应/异常，都直接跳过提纲阶段、退回现在的单段式
prompt（原有的"为什么值得关注/怎么入门/常见资源/投入周期"四段式），
不让报告生成整体失败——这是"能用就用，用不了就退回默认路径"的一贯
容错原则，跟 `_llm_augment_topics()` 等既有 LLM 增强调用点的容错方式
一致。

`report_two_stage_enabled=False`（默认）时，`generate_growth_report()`
的行为跟本方案实施前完全一致，不受影响。

## 4. 阶段 3：报告标注信息来源

阶段 1 产出的摘录如果被拼进 prompt，格式统一为：

```
- 参考：{page.id}（{date}）：{excerpt}
```

并在 prompt 里明确要求 LLM"如果确实参考了某条资料，请在对应内容后
用『（参考：{page.id}）』的形式标注来源；没有参考到的资料不要提"。
这样报告正文里出现的引用都能追溯到具体的 wiki 页面，用户可以自己
判断可信度，也降低了"看起来像事实、其实是 LLM 现编"的风险。

不引入新的存储字段——引用关系体现在报告正文的自然语言里，不做结构化
抽取，避免为了"来源可追溯"这一件事引入一套新的元数据体系，超出本轮
范围。

## 5. 阶段 4：忽略原因驱动针对性调整

新增配置 `report_dismiss_reason_adaptive_enabled: bool = True`
（默认开启——这一步不产生任何新的 LLM 调用，只是在已经要发的 prompt
里加一两句话，成本几乎为零，遵循"零成本的改进默认开启"原则，跟
`goal_alignment_enabled` 同等语义）。

`generate_growth_report()` 生成 prompt 前，复用已有的
`_report_quality_dismiss_counts(paths)`（无需新增任何统计逻辑——这个
函数在 2.7 节就已经存在，只是此前只用于诊断展示）：如果
`candidate.title` 对应的计数 > 0（说明这个方向之前的报告被标过
"内容太笼统"），在 prompt 末尾追加一句强约束：

```
注意：这个方向之前生成的报告曾被反馈"内容太笼统"，这次请务必给出
具体、可操作、贴合用户实际处境的建议，避免空泛的通用性建议。
```

`report_dismiss_reason_adaptive_enabled=False` 时完全跳过这一步，
prompt 内容与本方案实施前一致。

## 6. 数据结构变更小结

- 无新增持久化字段/文件——阶段 1/3 的摘录、来源标注都只出现在报告
  正文（已有的 `body_path` 文件）里，不做结构化落盘。
- `GrowthAdvisorConfig` 新增两个字段：`report_two_stage_enabled`
  （默认 `False`）、`report_dismiss_reason_adaptive_enabled`
  （默认 `True`）。
- `_LLM_CALL_TYPES` 新增 `"report_outline"`。

## 7. 实施顺序与验收

> 状态：阶段 1/2/3/4 均已实施完成，详见 `docs/growth-advisor-guide.md`
> 2.10 节、`src/mini_agent/evolution/growth_advisor.py` 模块头部
> docstring、`tests/test_growth_advisor_research_quality.py`。

1. 阶段 1（外部资讯摘录，纯读 + prompt 拼接）→ 补测试。✅
2. 阶段 3（来源标注，阶段 1 的直接延伸，同一批实施）→ 补测试。✅
3. 阶段 4（忽略原因驱动调整，复用现有统计）→ 补测试。✅
4. 阶段 2（两段式生成，成本最高、改动面最大，最后做）→ 补测试。✅
5. 每阶段完成后同步更新 `docs/growth-advisor-guide.md`
   （新增 2.10 节）与本文档、模块头部 docstring 变更历史。✅

每个阶段都在 `report_include_external_context`/
`report_two_stage_enabled`/`report_dismiss_reason_adaptive_enabled`
任一开关关闭时完全退化到当前行为，互相独立、可以分别开关，不互相
阻塞。

## 8. 与现有设计哲学的一致性检查

- **只读优先**：阶段 1/3/4 都不修改候选置信度、不新增持久化文件，
  延续"外部资讯只做展示补充"的既有边界。
- **成本对齐真实收益**：唯一新增 LLM 调用的阶段 2 默认关闭；阶段 4
  零额外成本，默认开启。
- **容错优先于完整**：任何一步的 LLM 调用失败都退回现有路径，不让
  报告生成本身失败——这是贯穿整个模块的既有原则，本轮延续。
