# 树影响世界指南（第二十二轮 WP3 · P5d / 3d）

> 实现：`world_simulator/tree_effects.py`；接线：`engine/advance.py`、`causal_tree.py`、`causal_engine.py`
> （`needs_elapsed`/提示词/原因标签）、`app.py`。设计与取舍：
> `next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 / §9。

## 它解决什么

此前未来树只是展示：分支被标 `active` 之后，世界里什么都不会发生（P5c 只管"能不能 active"）。
开启本功能后，分支可以**声明**"我一旦激活，会对世界的哪里施加什么压力"，引擎把它转成因果引擎（P5a）的
待兑现项——树第一次真正推动世界。引擎**不改任何数值**，只保证"该交代的不会被悄悄忘掉"。

## 开关（`manifest.settings`，默认关）

| 开关 | 作用 |
|---|---|
| `tree_effects_enabled` | 子开关，**必须同时开启 `causal_engine_enabled` 才生效**（队列、处置、统计都是因果引擎的）。单独开它什么都不做，设置页会提示。独立于 `tree_grounding_enabled`（可单独用，也可与接地搭配） |

关闭时：提示词占位符 `{tree_effects_hint}` 为空串、不入队，行为与之前**逐字节一致**。

## 分支新增的可选字段 `effects_if_active`

数组，每项形如因果边：`to_line_id`（必填：目标因果线 id 或技术节点 id）、`mechanism`、`sign`
（positive/negative/mixed，认 `+`/`-` 等别名）、`strength`（high/medium/low）、`delay_days`（模拟世界天数）、
`delay_steps`（旧式整数步，降级兜底）、`note`、`condition`（同 `trigger_condition` 的结构化谓词）。
只在填写时才出现在规整后的分支里（旧数据/兜底模板形状不变）；缺 `to_line_id` 的条目、非 dict 条目被丢弃，
非法数字字段被丢弃，`condition` 写法非法被忽略。由 LLM 在新增分支时可选填，或用户在设置里手填——
**LLM 填的是猜测，不是事实**。

## 入队规则

- 分支**本步新变为 `active`**（推进前不是 active；含本步新增就是 active），且 P5c 接地之后**仍是** active
  → 每条声明入队一个待兑现项。被接地降级回 `emerging` 的不入队；此后一直保持 active 不重复入队；
  离开 active 再回来才再次入队（同一条未结案则只累加 `retrigger_count`）。
- 待兑现项 id `tree:{线id}/{分支id}->{目标}@{步}`，`edge_id` 为去掉 `@步` 的部分，带 `source: "tree"`、
  `branch_id`、`trigger_reason: "tree_effect"`。之后与声明的因果边走**同一条路径**：延迟到期 → 到期因果
  压力注入提示词（显示为 `线/分支 → 目标`）→ LLM 用 `effect_dispositions` 回报 → 引擎校验（E1–E4）、
  结案、`edge_stats()` 统计。
- `condition` 按**本步结束后**的变量求值，不满足则不入队。
- 时间：树声明了 `delay_days` 时，`needs_elapsed` 为真，每步索要 `elapsed_days`（与边的 `delay_days` 一致）。
- 顺序（`advance()`）：技术裁决 → P5c 接地 → 因果引擎处置上一步回报 → 边入队 → **树声明入队** → 快照。
  `causal_pending` 属于分支作用域动态状态，分叉即回滚。

## 违规码（`SimState.causal_violations`，沿用因果引擎口径）

| 码 | 含义 |
|---|---|
| E5 | 某条声明缺 `to_line_id`（该条被丢弃），或 `sign`/`strength` 取值不认识（该字段按未声明处理，条目仍入队） |
| E6 | 待兑现队列已满（与边入队共用 `max_pending`），本条未入队 |
| E7 | 目标既不是已登记的因果线也不是技术节点（疑似笔误）——**仍入队**（不阻断，与技术模型 T9 同一取舍），只留痕 |
| E0 | 本模块内部出错，本步未入队（不拖垮推进） |

## 界面

设置页「高级：因果树接地」里一个复选框（因果引擎没开时给警告）；详情页「🔗 因果引擎」面板里新增
「🌳 树分支声明的影响」列表（含各声明在本分支的入队/兑现/部分/抵消统计）；每步时间线的因果入队/交代
记录沿用 P5a 的展示（`edge_id` 以 `tree:` 开头即来自树）。

## 已知边界（如实）

- 引擎**无法验证**声明是否合理，也无法验证 `realized` 是否真的写进了状态——统计是 LLM 自报。
- 只看顶层分支；`advance_lines()` 独立推进路径自 P8 起也入队（仅在分支被树接地自动迁移为 active 时触发，因为线不产出 `tree_updates`）；不追溯（开启前已 active 的分支不补入队）。
- 树边统计不回写 `knowledge_base`（`causal_engine.kb_outcomes` 只认 `declared_causal_graph` 里的边；
  树声明是实例内的假设）。
- **从第 0 步（种子步，没有快照）分叉时，新分支沿用当前工作副本**，其中包含主线已排的待兑现项与树状态——
  这是 P0 起就记录的既有边界，不是本阶段引入（技术模型靠"种子锚定"单独规避；因果树/待兑现项没有做同样的锚定）。
  从任何有快照的步分叉则正确回滚。
- 只看"本步新变为 active"，不看分支为何变 active，也不看激活的方向/幅度；`sign`/`strength` 只随提示词展示。
- **没有在真实 LLM 下验证**（LLM 是否会填 `effects_if_active`、是否如实交代、额外 token 成本均未知）；
  `app.py` 只做了 `streamlit.testing.AppTest` 冒烟，设置页保存流程未实跑。

## 相关

[`causal_engine_guide.md`](./causal_engine_guide.md) · [`tree_grounding_guide.md`](./tree_grounding_guide.md) · [`causal_view_guide.md`](./causal_view_guide.md)（树边在着色关系图/到期时间线里的展示）
