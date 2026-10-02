# 因果引擎使用指南（第二十二轮 WP3 · P5a：边升级 + 待兑现因果队列）

> 设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 的 3a + 3b。
> 代码：`world_simulator/causal_engine.py`。开关：`settings.causal_engine_enabled`
> （**默认关闭**，关闭时提示词、落盘、界面与没有这个功能时完全一致）。
> 本阶段**只做 3a + 3b**；3c 树接地、3d 树影响世界、3e 界面着色留给后续子阶段。

## 它解决什么、不解决什么

原来 `declared_causal_graph` 里的边只是展示/提示文本：声明了"经济线影响行业线"，但源头真的有进展后，
没人保证这条影响**被交代**。开启后，引擎：

1. 源头线出现实质进展时，把这条边**入队**（待兑现）；
2. 延迟期到了，在提示词里提醒 LLM"这条因果该交代了"；
3. LLM 用 `effect_dispositions` 回报 `realized / dampened / postponed / countered` 及原因；
4. 引擎校验回报、透明记录违规、累计每条边的兑现统计。

**它不做的事**（请先看清，否则会高估它）：

- **不改任何数值、不改任何分支状态。** `realized` 就要 LLM 自己写进 `next_vars`/叙事，引擎只记账。
- **无法验证 `realized` 是否真的体现在状态里。** 兑现统计是"LLM 说兑现了多少"，不是世界里真实兑现的度量。
- 不判断这条因果语义上合不合理；`sign`/`strength` 只随提示词展示，不参与任何计算。
- 账本显著变化**不是**触发源（"显著"需要阈值，没有数据前不拍脑袋）。
- **没有在真实 LLM 下验证过**：LLM 遵守 `effect_dispositions` 协议的程度、额外 token 成本都未知。

## 快速开始

设置页 → "高级：因果引擎" → 勾选开启；并在"声明的因果结构"里写边：

```json
[
  {"id": "rate_to_invest", "from_line_id": "econ", "to_line_id": "industry",
   "mechanism": "降息降低融资成本，带动投资", "sign": "+", "strength": "medium",
   "delay_days": 90, "confidence": "low"},
  {"from_line_id": "chip", "to_line_id": "industry", "note": "旧格式也可以，延迟缺省=下一步到期"}
]
```

## 边字段（3a）

| 字段 | 说明 |
|---|---|
| `from_line_id` / `to_line_id` | 必填。可以是因果线 id，也可以是技术节点 id（技术阶段迁移会触发）。不能指向自己 |
| `id` | 可选；缺省为 `from->to`。重复 id 只保留第一条 |
| `mechanism` / `note` | 一句话机制/备注，随提示词展示 |
| `sign` | `+`/`positive`、`-`/`negative`、`mixed`；不认识的值视为未声明 |
| `strength` / `confidence` | `high`/`medium`/`low`；不认识的值视为未声明（不猜默认档） |
| `delay_days` | 延迟（模拟世界里的天数），用各步 `elapsed_days` 累计；负数视为未声明 |
| `delay_steps` | 旧式整数步延迟，仍可用 |
| `condition` | 入队条件，写法同外生事件（`{"var":"a.b","op":">=","value":5}` 或 `{"tech":"id","min_stage":"developer"}`，数组=全部满足；变量不存在=不满足） |
| `enabled` | 默认 true；false 时不入队 |

## 每步发生什么

1. 调用 LLM 前：若有**已到期**的待兑现项，`{causal_pending_hint}` 注入提示词（含机制/方向/强度/已过天数/
   本世界历史兑现情况）。没有到期项时提示词为空。
2. LLM 输出后，**先处置**上一步遗留项（`effect_dispositions`），**再入队**本步新触发的边——顺序不能反，
   否则本步新入队的项会被同一步的回报处置掉。
3. 触发源（边的 `from_line_id` 命中任一即入队）：`line_advanced`（`line_updates` 里有且 `advanced`
   不是 false）、`tree_branch`（未来树分支被印证或置为 active）、`sampled_event`（抽中且未被上限压掉的
   事件 `affects` 含该 id）、`tech_transition`（技术模型本步发生迁移；被驳回的 `held` 不算）。
4. 同一条边已有未结案项时，只累加 `retrigger_count`，不新增条目。

### 延迟与"精度降级"

延迟按触发**之后**各步 `elapsed_days` 之和比较（不含触发那一步）。区间内任何一步缺有效
`elapsed_days` → 退化为按步计数（声明了 `delay_steps` 就按它，否则缺数据的那步过去即视为到期），
提示词里标"时间精度降级"。声明了 `delay_days` 的边会让提示词向 LLM 索要 `elapsed_days`
（技术模型/事件采样已在索要时不重复）。

### 处置校验与违规码

| 码 | 含义 | 结果 |
|---|---|---|
| E1 | 引用了不存在/已结案的待兑现项（或非对象条目） | 忽略 |
| E2 | `disposition` 取值非法 | 忽略 |
| E3 | 延迟期未到就处置 | 忽略（不算"被忽略"计数） |
| E4 | `dampened/postponed/countered` 缺 `reason` | 忽略，该项按"未被交代"计 |
| E6 | 待兑现队列已满 | 该边本步不入队 |
| E0 | 引擎自身出错 | 本步不处理，推进照常 |

自动结案（审计里带 `auto: true`）：`postponed` 超过 `max_postpone`（默认 3）次 → `expired`；到期后连续
`max_ignored`（默认 3）步没被交代 → `unaddressed`。全部**透明记录，不静默**。

## 参数（`settings.causal_params`，非法值忽略）

`max_postpone`=3、`max_ignored`=3、`max_pending`=40。**都是通用占位值，不是领域事实。**

## 兑现统计

从**分支历史**推导（`SimState.causal_queued` / `effect_dispositions`），所以天然按分支正确。
`realized_rate = realized / (realized + dampened + countered)`；`postponed`/自动结案不进分母。
提示词里只有有结论次数 ≥ 3 时才显示历史兑现情况，避免小样本误导。

## 分支行为

`causal_pending` 已加入分支作用域动态状态（`dynamic_state.DYNAMIC_KEYS`）：分叉即回滚到那一刻的队列，
两条时间线各自结案、互不污染（有测试）。只对开启之后新写入的步生效。

## 与计划的偏离

1. 延迟单位用 `delay_days`（elapsed 天数）。`relationship.py` 里关系的延迟已在 **P5b** 同样改用
   `delay_days`，见下方"P5b 补充"。
2. 计划 3b 的"回写 `knowledge_base`"已在 **P5b** 实现，见下方"P5b 补充"。
3. 计划 3b 触发源里的"账本中显著变化"、"分支状态变化"中除 active 以外的状态——**没做**。
4. 新增了 E1–E6 校验、`max_postpone`/`max_ignored`/`max_pending` 三个上限（计划未写），用来防止队列无限增长
   和"声明了却永远挂着"。

## P5b 补充（2026-10-01）

**兑现统计回写 `knowledge_base`**（`settings.causal_kb_writeback`，**默认开**，仅因果引擎开启时生效；
设为 `false` 关闭）：

| 处置 | 回写 |
|---|---|
| `realized` | `validated_count` +1 |
| `countered` | `contradicted_count` +1 |
| `dampened` / `postponed` / 自动结案（`expired`/`unaddressed`） | 不回写（没有明确结论） |

- 条目的 `cause`/`effect` 取边两端的可读名（因果线 `label`，或技术节点 `name`，查不到用 id）。
  先按**文本完全相同**匹配已有条目，找不到再用与 `record_causal_links()` 相同的 Jaccard 阈值，都没有才
  新建 `hypothesis` 条目。
- **幂等**：条目 `evidence` 里记 `"{sim_id}@{branch}#step{N}:{edge_id}"`，同一分支同一步同一条边只计一次。
- **如实说明**：兑现与否是 LLM 自报，引擎无法核验，写进跨模拟知识库的是"某个世界里 LLM 说它兑现/被抵消了"。
  这一来源与 `record_causal_links()` 的 `validated_count`（同一因果在叙事里再次出现）不同，可能叠加到同一条目。
- 回写在推进落盘之后执行，失败被吞掉，不影响本次推进。

**关系延迟改用 elapsed 单位**：见 `relationship.py` 模块 docstring。关系新增可选 `delay_days`，
`delay_steps` 保留为旧数据/降级兜底。

## 已知边界

- 只看"源头有进展"，不看进展的方向/幅度。
- LLM 可能无视协议：不给 `effect_dispositions` 时，到期项会累计"未被交代"并最终自动结案——
  这会被量化，但不会被消除。
- `advance_lines()` 独立推进路径不入队、不处置。
- 开启后每步可能多写一份完整快照（`causal_pending` 变化时），历史文件会变大。
- 未在真实 LLM 与真实看板下验证（界面只做了 `streamlit.testing.AppTest` 冒烟）。

## 相关

- 设计与取舍：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 / §9
- 技术模型：[`tech_model_guide.md`](./tech_model_guide.md)；外生事件：[`event_sampling_guide.md`](./event_sampling_guide.md)
- 树影响世界（分支 `effects_if_active` 入本队列，`edge_id` 以 `tree:` 开头）：[`tree_effects_guide.md`](./tree_effects_guide.md)。
- 树接地（前置强制/互斥组/结构化触发条件/校准账本）：[`tree_grounding_guide.md`](./tree_grounding_guide.md)；其自动激活会作为本引擎的"树分支 active"触发源被看到。
