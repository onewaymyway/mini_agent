# 自我叙事：增量式"当前状态"改造计划

> 前置文档：`next_doc/self_awareness_identity_evolution_plan.md`（阶段一/二/三已
> 全部完成）。本文件是该方案 §2.2（Identity 叙事主线）落地后的第二轮迭代，
> 只聚焦 `evolution/self_narrative.py` 一个模块的生成/存储/消费方式，不重新
> 评估其余 §2.x 各项。

## 0. 问题背景

`self_narrative.py` 当前实现（阶段二产物）每次生成都是"全量重新综合"：
`_gather_evidence()` 对六路证据源各取最近 5 条，拼进 prompt，让 LLM 独立写
一段全新的第一人称叙事，追加进 `self_narrative_log.jsonl`。这带来两个问题：

1. **叙事历史会无限增长，但增长本身不产生价值**——相邻两次生成看到的原始
   证据窗口高度重叠（同样"最近 5 条"),如果两次生成之间原始记录变化很小,
   产出的叙事会高度同质化重复。日志条目数量增长,但"能看出身份怎么演化"
   这件事并没有因此变得更清楚,反而被同质化内容淹没。
2. **叙事从未读回自己的历史**——生成时不知道"上一次我是怎么描述自己的",
   每次都是从原始事实重新拼出一段话，不是在已有理解上做修正/深化。这与
   "身份是一条演化主线"的设计目标本身是脱节的：现在的实现本质上仍然是
   "定期拍快照"，只是快照换成了一段文字，而不是一个字段。

## 1. 设计结论：两层信息 + 增量编辑式生成

与用户讨论后确认的方向：

- **原始层（不新建）**：`failure_pattern_store` / `sub_agent_experience` /
  `agent_value_profile_builder` / `lineage_view` 等现有证据源，本身就是
  "带时间戳的原始记录"，各自独立维护，本次改造不动它们的存储结构。
- **当前叙事层（重构生成逻辑，复用现有文件）**：`self_narrative_log.jsonl`
  的语义从"每次独立生成的快照集合"改为"当前理解的版本历史"——每条记录
  仍然追加式保存（供追溯"自我认识经历了哪些阶段"），但**生成方式**改为
  "读取上一版当前叙事 + 上一版之后新增的原始记录，编辑出下一版"，而不是
  "无视上一版，重新综合全部证据"。
- **消费方式**：任何需要"我现在是什么样"的场景，只取最新一条（即
  "当前状态"），历史版本只用于人工/看板回溯，不参与日常消费路径。

这个结构的关键收益：叙事历史的增长天然被"编辑式生成"抑制同质化（只有
真正的新证据才会改变措辞），"必要的历史信息保留"也不需要额外设计压缩
算法——直接交给 LLM 在编辑上一版时自然取舍，与项目"语义综合交给 LLM、
代码只管证据收集"的一贯分工一致，不新增判断逻辑。

## 2. 具体改动

### 2.1 证据分两类，只对"可追加型"做 delta 过滤

现有六路证据源按性质分两类，处理方式不同：

| 类型 | 来源 | 处理方式 |
|---|---|---|
| 可追加型（有天然的"新增"语义） | `sub_agent_experience`（`at` 字段）、`failure_pattern_store`（`last_seen` 字段）、`agent_value_profile`（`first_observed`/`last_reinforced`）、`lineage_view` 的新增 merge | 按上一版 `evidence_cursor` 过滤，只取更新的部分 |
| 快照型（只有"现在是什么样"，无版本概念） | `self_assessment`、`capability_map` top domains、`identity` | 不过滤，每次都给最新全量快照——它们代表"现状"，本身不是事件流 |

`evidence_cursor` 取"上一版生成时刻的时间戳"，作为本次判断"新增"的分界线。

### 2.2 存储结构：`self_narrative_log.jsonl` 每条记录新增一个字段

```json
{"at": 1735...,
 "narrative": "……",
 "purpose_summary": "……",
 "evidence_cursor": 1735...,
 "capability_focus_suggestions": ["……", "……"]}
```

- `evidence_cursor`：本条记录生成时依据的"新增证据"起始时间戳，供下一次
  生成过滤 delta 用。首次生成（无历史版本）时取值为 0（视为"全部都是新
  增"，等价于当前行为）。
- `capability_focus_suggestions`：见 §2.4，可选字段，允许为空数组。
- 不改动文件路径（`paths.self_narrative_log_path` 不变），不新建文件，
  向后兼容——旧记录没有这两个字段时按"值为空/取值 0"处理即可。

### 2.3 生成逻辑改动（`evolution/self_narrative.py`）

`generate_self_narrative()` 改动点：

1. 生成前先 `load_self_narrative_history(paths, limit=1)` 取上一版（若无
   历史版本，走当前行为：全量证据、独立生成）。
2. `_gather_evidence()` 增加一个 `since_cursor` 参数，对可追加型来源按
   §2.1 过滤；快照型来源不受影响。
3. **空 delta 跳过**：若可追加型来源过滤后全部为空，且快照型来源相比
   上一版没有实质变化（简单判断：`capability_map` top domains 集合是否
   变化、`self_assessment.updated_at` 是否更新即可，不做精确 diff），则
   本次不生成新版本，直接返回 `None`——延续既有"没有摩擦和洞察就不写"
   的克制原则，避免版本数量本身失控增长。
4. `_build_narrative_prompt()` 重写为编辑式指令：输入变成"上一版当前
   叙事全文" + "本次新增原始记录（delta）" + "当前快照型现状"，明确要求
   LLM **编辑更新**而不是重写——保留上一版里仍然成立、未被新证据推翻的
   内容，融入新变化；若新证据与上一版某个判断冲突，要求体现"我曾经
   认为…，现在看来…"这种修正式措辞（与 §2.6 漂移检测已有的措辞原则
   一致）。首次生成（无上一版）时退化为现有 prompt。
5. LLM 输出 JSON 新增一个可选字段 `capability_focus_suggestions`
   （0~N 条字符串，允许为空数组，不强行凑数），见 §2.4。
6. 写入时带上 `evidence_cursor = time.time()`（本次生成时刻，作为下次
   过滤的分界线）。

### 2.4 消费通道一：看板展示（现状不变）

`/self/portrait` 的 `self_narrative` 字段继续取 `load_self_narrative_history
(paths, limit=1)`，新增一个 `get_current_narrative(paths)` 便捷函数封装
"取最新一条"这个语义，避免各处消费者重复写这行代码。**这是本轮改造唯一
生效的消费路径**，纯只读展示，不改变任何执行/排序/权重逻辑——延续"自我
叙事仅作为观察者"的定位。

### 2.5 消费通道二（新增，仍不影响执行）：作为能力学习候选的第四路信号

`persona_candidates.py` 现有三路信号采集（`_collect_topic_signals` /
`_collect_wiki_miss_signals` / `_collect_failure_signals`），走同一套
"批量提炼 → LLM 判重 → cooldown 去重 → `pending` 状态"流程，最终决策权
在用户手上（`PersonaCandidateStore` 的 `pending → accepted | dismissed`
状态机不变）。

新增第四路：

- `self_narrative.py` 生成时产出的 `capability_focus_suggestions`
  （§2.3 步骤 5）——基于本次叙事综合出的"值得针对性学习/补强的方向"，
  语义上介于现有 `failure_pattern`（反复暴露的短板）和 `growth_topic`
  （已确认的成长方向）之间，但视角不同：前两者分别是"失败聚合"和"兴趣
  信号聚合"的直接产物，这一路是叙事整体综合后的判断，可能捕捉到单一
  信号源看不到的组合性洞察（比如"某个能力最近被反复委派给子任务但主
  身份从未直接练习过"这类只有综合视角才能看出的模式）。
- `persona_candidates.py` 新增 `_collect_narrative_signals(paths)`，读取
  `get_current_narrative(paths)` 的 `capability_focus_suggestions` 字段
  直接作为信号，不重新调用 LLM（复用已经生成好的内容，避免重复推理）。
- `_CANDIDATE_SOURCES` 增加 `"narrative_reflection"` 取值；
  `_build_extraction_prompt()` 增加对应信号区块；候选 `source` 字段按
  §2.7（原方案）已有的校验/回退逻辑扩展合法值集合即可，其余判重/落盘
  流程不变。
- 边界确认：这一步仍然只是"多一路建议进入候选池"，候选依旧停在
  `pending`，不自动创建 Track、不自动触发学习——不突破"决策权边界不变"
  这条项目一贯原则。

## 3. 实施阶段划分

### 阶段一：生成逻辑改造（核心）— 已完成（2026-08-30）
- `_gather_evidence()` 支持 `since_cursor` 过滤（仅可追加型来源）
- `_build_narrative_prompt()` 改为编辑式 prompt（上一版 + delta）
- 空 delta 跳过逻辑
- `evidence_cursor` 字段读写
- 新增 `get_current_narrative(paths)` 便捷函数
- 测试：延续现有 `tests/test_self_narrative.py` 补充增量场景用例
  （首次生成/无新增跳过/有新增编辑更新/新旧证据冲突措辞）

**已实现**：
- `evolution/self_narrative.py` 全量重写：`_gather_evidence()` 新增
  `since_cursor` 参数，证据分"可追加型"（`sub_agent_experience` 按
  `at` 过滤、`failure_pattern` 按 `last_seen` 过滤、
  `agent_value_profile` 按 `first_observed`/`last_reinforced` 日期字符串
  过滤）和"快照型"（`identity`/`self_assessment`/
  `capability_top_domains`/`drift_signals`/`lineage`，不过滤，每次全量）。
- 新增 `_snapshot_fingerprint()`（快照型证据的 sha256 指纹）和
  `_delta_is_empty()`（可追加型证据是否为空）；`generate_self_narrative()`
  在已有历史版本时用两者共同判断"自上一版以来有没有新证据"，都为
  "无变化"时直接返回 `None`，不调用 LLM、不生成新版本。
- `_build_narrative_prompt()` 分两路：无上一版时走原有的独立综合 prompt；
  有上一版时走编辑式 prompt（传入上一版全文 + delta + 当前快照，要求
  "编辑更新而不是重写"，冲突处用"我曾经认为…，现在看来…"措辞）。
- 新增 `get_current_narrative(paths)` 便捷函数，`api/routes.py`
  （`/self/portrait`）与 `cli/commands/self_narrative_cmd.py` 均已改为
  调用它，不再各自重复"取最后一条"的逻辑。
- **顺手修复的一个死循环 bug**：最初实现把 `identity` 计入快照指纹，
  但 `identity.purpose` 正是本模块每次生成结束时自己写回的字段——这样
  会导致指纹在每次生成后必然变化，"无新证据则跳过"永远失效。修复为
  指纹计算排除 `identity`（详见 `_snapshot_fingerprint` 注释），由
  `tests/test_self_narrative.py::test_second_generation_without_new_
  evidence_is_skipped` 覆盖回归。
- `storage/paths.py::self_narrative_log_path` 文档字符串同步更新，说明
  新增的 `evidence_cursor`/`snapshot_fingerprint`/
  `capability_focus_suggestions` 三个字段。

**测试**：`tests/test_self_narrative.py` 重写，17 个用例全部通过（新增
delta/指纹判断、"无新证据跳过"、"编辑式 prompt 包含上一版全文"、
`get_current_narrative` 等场景）；连带回归
`tests/test_self_model_drift.py`/`tests/test_sub_agent_experience.py`/
`tests/test_lineage_view.py` 共 34 passed。

### 阶段二：能力学习候选接入 — 已完成（2026-08-30）
- `capability_focus_suggestions` 字段生成（prompt + 解析）
- `persona_candidates.py::_collect_narrative_signals()`
- `_CANDIDATE_SOURCES` 扩展 + `source` 校验
- 测试：延续 `tests/test_persona_candidates_failure_signal.py` 同等模式
  补充 narrative 信号场景用例

**已实现**：
- `capability_focus_suggestions` 字段的生成/解析已在阶段一
  `self_narrative.py` 重写时一并完成（prompt 要求 LLM 输出该字段、
  `_parse_narrative_response` 解析、允许为空数组不强行凑数）；本阶段
  只需要在 `persona_candidates.py` 里消费它。
- `persona_candidates.py` 新增 `_collect_narrative_signals(paths, top_n)`：
  读取 `self_narrative.get_current_narrative()`（只取最新一条，不合并
  历史版本），返回其 `capability_focus_suggestions` 按 `top_n` 截断，
  不重新调用 LLM。
- `_CANDIDATE_SOURCES` 增加 `"narrative_reflection"`；`_build_extraction_
  prompt()` 新增第四路信号区块（"自我叙事综合判断后认为值得补强的方向"），
  提炼视角从"两个角度"扩展为"三个角度"，第三个角度直接问"是否值得采纳
  为候选"；`_extract_candidates_with_llm()` / `scan_persona_candidates()`
  同步接入第四路信号，`evidence_refs` 新增 `narrative_reflection:` 前缀。
- `PersonaCandidateConfig` 新增 `narrative_signal_top_n`（默认 5）。
- 决策权边界确认：这一路信号和前三路走完全相同的
  `pending → accepted | dismissed` 流程，候选落盘后仍需用户显式采纳，
  不自动创建 Track、不自动触发学习。

**测试**：新增 `tests/test_persona_candidates_narrative_signal.py`
（10 用例，覆盖信号采集/prompt 拼装/source 解析/scan 主流程），连带
`tests/test_persona_candidates_failure_signal.py` 等既有测试全部通过；
阶段一 + 阶段二相关测试合计 85 passed（含既有回归）。

### 阶段三（可选，视阶段一实际运行效果决定是否需要）
- 若阶段一运行一段时间后，`self_narrative_log.jsonl` 版本数依然显著
  增长（比如高频 cron 触发导致 delta 判断阈值过于敏感），再考虑是否
  需要周期性"元叙事"摘要（独立文件，不影响当前状态生成链路）。**不
  预先实现**，与 §2.3 原方案"中期可选、不预先实现用不上的接口"的取舍
  一致。

## 4. 设计原则（延续原方案 §4，本轮追加一条）

- 不臆造、不新增平行系统、决策权边界不变、纯只读优先——原方案四条
  全部延续。
- **追加原则**：编辑式生成不改变"追加式存档"的根本策略——每次生成
  仍然是追加一条新记录而非覆盖旧记录，"当前状态"只是消费时取值的
  约定（取最新一条），历史版本物理上完整保留，可随时回溯"当前状态"
  这个理解本身是怎么一步步变化的。
