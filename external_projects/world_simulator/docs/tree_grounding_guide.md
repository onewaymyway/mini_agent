# 因果树接地指南（第二十二轮 WP3 · P5c / 3c）

> 实现：`world_simulator/tree_grounding.py`；接线：`engine/advance.py`、`causal_tree.py`、`state_model.py`、
> `consistency_guard.py`、`quality_signals.py`、`app.py`。设计与取舍：
> `next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP3 / §9。

## 它解决什么

此前未来树是"标签树"：`prerequisites` 只被 C3 事后告警一次，触发条件是自由文本，互斥全靠 LLM 自觉，
`likelihood` 从不对账。开启本功能后，树的**结构**开始约束世界。引擎只做结构性裁决，**不改任何数值，
不判断条件语义是否合理**，每次动作都透明记录。

## 开关（`manifest.settings`，默认都关）

| 开关 | 作用 |
|---|---|
| `tree_grounding_enabled` | 总开关。关闭时提示词占位符为空串、不裁决，行为与之前逐字节一致；独立于 `causal_engine_enabled` |
| `tree_auto_transition` | 子开关，需总开关开启才生效。开启后引擎才自动置 `active` / `invalidated`；关闭时只给建议 |
| `likelihood_nominal` | 可选，`{"high":0.8,"medium":0.5,"low":0.2}`，仅用于体检账本对账；引擎不内置任何档位对应的概率 |

界面：设置页「高级：因果树接地」三项；详情页「🌳 因果树接地」面板（当前建议/约束）；时间线每步显示降级/自动迁移记录；体检里显示校准账本。

## 分支新增的两个可选字段

只在填写时才出现在规整后的分支里（旧数据/兜底模板形状不变）。

- `trigger_condition`：结构化触发条件，与外生事件同一套写法（`event_sampler.evaluate_condition`）：
  `{"var":"a.b","op":">=","value":5}` 或 `{"tech":"技术id","min_stage":"developer"}`；列表表示全部满足。
  非 dict / dict 列表的写法被忽略。与自由文本的 `trigger_conditions`（复数）是两个字段。
  写法非法、变量不存在 → 视为**不满足**。
- `exclusive_group`：同一条线内同名即互斥。

## 裁决码（`SimState.tree_grounding`，每项 `{code, action, severity, message, line_id, branch_id, detail?}`）

| 码 | 动作 | 含义 |
|---|---|---|
| G1 | downgraded | 本步新变 `active`，但可解析到的前置分支未 `resolved` → 降为 `emerging` |
| G2 | downgraded | 本步新变 `active`，但互斥组内已有 `active/resolved` → 降为 `emerging` |
| G3 | flagged | 互斥组内多个 `resolved`：只记录，不改写（`resolved` 是对"已发生"的陈述） |
| G4 | auto_activated | （仅自动迁移）触发条件满足且前置/互斥放行 → 自动置 `active` |
| G5 | auto_invalidated | （仅自动迁移）同组有分支本步新 `resolved` → 同组 `dormant/emerging` 落败者置 `invalidated` |
| G0 | error | 接地内部出错，本步跳过，不改树 |

降级/自动迁移的**实际结果**写回 `manifest.settings["causal_lines"]`（随分支快照走），并同步修正本步
`tree_updates` 审计中的实际状态（降级项带 `grounded:true`，自动项是 `auto:true` 的新审计条目）。
顺序：技术裁决之后、因果引擎入队之前、快照之前，所以自动激活能被因果引擎的"树分支 active"触发源看到，
一致性守卫 C3 看到的是降级后的树。

## 判定口径

- 只处理每条线 `future_tree.branches` 的**顶层**分支。
- 前置解析与 C3 一致：同线按 id → 跨线按 id **唯一**命中 → 否则（自由文本/歧义/自指）算"无法核验"，**不阻断**。
  "满足" = 前置分支状态为 `resolved`（严格口径，`active` 不算）。
- 只对**本步新变为 active**（含本步新增就是 active）的分支强制；**不追溯**降级此前已 active 的分支。
- 同步内顺序：先处理新 `resolved`（占住互斥组），再处理新 `active`；同组两个同时新 `active`，先到先得。
- 条件取**本步结束后**的变量与 `tech_state`。

## 提示词与建议

开启后每步在 `{tree_grounding_hint}` 里带规则说明，并追加当前建议（纯读不写）：`trigger_met`（建议 active）、
`trigger_blocked`（条件满足但被前置/互斥拦住）、`exclusive_loser`（建议 invalidated）、`prereq_unmet`。
未开自动迁移时这些只是参考，是否采纳由 LLM 在 `tree_updates.status_updates` 里声明。

## likelihood 校准账本

沿分支快照链找"某分支在相邻两份快照间变为终结状态"的事件，记终结那一刻的 `likelihood` 与结局
（`resolved` 记命中，`expired/invalidated` 记未命中），按档位汇总命中率；高档命中率低于低档且两档样本都
≥ `ledger_min_n`（默认 3）时报**倒挂**。给了 `likelihood_nominal` 才算与名义值的差。出现在
`analyze_history()["c8_likelihood_ledger"]`，体检里只读。**P9 起**可另外写入跨实例 `knowledge_base`（`settings.kb_calibration_writeback`，默认开，
需开启树接地；档位样本 < `kb_min_samples` 不写；标注"LLM 自报"；可撤销），见
[`knowledge_writeback_guide.md`](./knowledge_writeback_guide.md)。

## 已知边界（如实）

- 引擎无法判断 `trigger_condition` 写得对不对；LLM 写的条件是猜测，不是事实。
- LLM 直接标 `resolved` 而前置未满足不拦截（只有 C3 对 `active` 告警）。
- 降级会造成"叙事说已激活、引擎说仍是 emerging"的错位，只能记录不能消除。
- 只看顶层分支；`advance_lines()` 独立推进路径自 P8 起也跑本模块，但线不产出 `tree_updates`，所以只有结构化 `trigger_condition` 的自动迁移会生效，提示词也没喂给线（见 [`independent_line_mechanisms_guide.md`](./independent_line_mechanisms_guide.md)）。
- 账本样本通常很少；`likelihood` 是主观档位，不是概率；只覆盖 WP0 之后写入快照的步。
- **没有在真实 LLM 下验证**（LLM 是否会填 `trigger_condition`、是否遵守协议、降级频率均未知）。
- `app.py` 只做了 `streamlit.testing.AppTest` 冒烟，设置页保存流程未实跑。

## 相关

[`causal_engine_guide.md`](./causal_engine_guide.md) · [`tree_effects_guide.md`](./tree_effects_guide.md) · [`event_sampling_guide.md`](./event_sampling_guide.md)
