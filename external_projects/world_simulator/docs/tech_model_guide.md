# 技术发展模型使用指南（第二十二轮 WP1）

> 设计依据：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP1。
> 代码：`world_simulator/tech_model.py`。开关：`settings.tech_model_enabled`（**默认关闭**，
> 关闭时提示词、落盘、界面与没有这个功能时完全一致）。

## 它解决什么、不解决什么

以前技术只是 `capabilities_gained` 里一次性的自报 + 一个 6 档 `maturity_stage` 标签：
没有前置依赖，不随时间推进，能跳级也能倒退，代码不拦。

开启后，**引擎持有技术的权威状态**，LLM 只能"提议"，引擎按规则"裁决"，违规不静默改数，
而是驳回并把原因记在时间线上。

**它不做的事**（请先看清，否则会高估它）：

- 不判断"语义上合不合理"。引擎只检查结构：阶段、进度、前置、采用率、成本。
- 不内置任何领域数值。所有默认时长是**通用占位值，不是领域事实**（见下文"参数"）。
- 不模拟 S 曲线/学习率。采用率和成本只做**约束**（和≤1、成本不无理由上升）。
- 叙事说"已经上市"而引擎说"仍是 developer"的错位**无法被消除**，只会在下一步提示里把权威状态告诉 LLM。
- **没有在真实 LLM 下验证过**：LLM 是否会遵守协议、写出的 `tech_updates` 质量如何，未知。

## 快速开始

1. 设置页 → "高级：技术发展模型" → 勾选"开启"。
2. （可选）在"技术节点"里写种子，例如：

```json
[
  {"id": "material", "name": "关键材料", "stage": "expert",
   "typical_dwell_days": {"expert": 600}},
  {"id": "battery", "name": "固态电池", "stage": "lab",
   "typical_dwell_days": {"lab": 900},
   "requires": [{"tech_id": "material", "min_stage": "expert", "mode": "hard"}]}
]
```

3. 正常推进。详情页出现"🧬 技术发展模型"面板；每步若有驳回/夹值，会在该步出现"⚙️ 技术模型 Txx"提示。

没有种子也可以：LLM 会在出现值得追踪的技术时用 `tech_updates` 登记。

## 每步发生什么

1. **提示词**：`{tech_state_hint}` 把协议和当前权威状态（阶段/进度/停留/状态/瓶颈/前置）喂给 LLM，
   并要求输出 `elapsed_days`（这一步跨越的天数）和 `tech_updates`。
2. **时间推进（引擎做）**：每个节点
   `progress += elapsed / 典型停留 × 投入系数 × 瓶颈系数 × 软前置系数`，上限 1。
   LLM 自报的进度会被忽略。
3. **裁决阶段声明**：
   - 一步最多升一档（跨档记 T1，最多按一档处理）；
   - 仅当本步推进后进度满 **且** 硬前置满足才迁移，否则驳回（T2/T3），阶段不变，**也不"送"进度**；
   - 倒退必须带 `regression_reason`，否则驳回（T4）；
   - 同一步里互为前置的迁移，用**本步开始前**的阶段判断（同步不算满足）。
4. **停滞**：阶段内停留 > `stall_multiplier × 典型时长` 且进度未满 → 状态"停滞"，下一步提示要求用事件解释。
5. **采用率/成本**：同一 `market` 内采用率之和 ≤1（T6，夹到剩余额度）；`cost_index` 上升须带 `cost_shock_reason`（T7）。
6. **炒作 vs 现实**：`perceived_stage` 只存储和展示，不做约束。

## 违规码

| 码 | 含义 | 结果 |
|---|---|---|
| T0 | 技术模型本步出错 | 整步跳过，状态不变（info） |
| T1 | 一步跳过多档 | 最多升一档（若满足条件） |
| T2 | 声明迁移但进度未满 | 驳回 |
| T3 | 声明迁移但硬前置未满足 | 驳回 |
| T4 | 倒退没有 `regression_reason` | 驳回 |
| T5 | 新技术声明了高于 lab 的阶段 | 夹到 lab（除非 `preexisting`） |
| T6 | 同市场采用率之和 >1 | 本步更新的节点被夹 |
| T7 | 成本无冲击原因而上升 | 保留原值 |
| T8 | 试图修改 `requires`/`typical_dwell_days` | 忽略（info） |
| T9 | 前置引用了未登记的技术 / 提议缺 id | **不阻断**，仅记录（info） |
| T10 | 投入非 normal 但没给理由 | 按 normal（info） |
| T11 | 瓶颈被清除但没给理由 | 保留瓶颈（info） |

**为什么 T9 不阻断**：笔误会造成死锁。代价是这种前置形同虚设——看到 T9 请去设置里补登记。

## 时间：`elapsed_days`

- 也被外生事件采样使用（`event_sampling_guide.md`）：事件概率用最近几步的中位数估计本步跨度，
  所以只开事件采样时提示词同样会索要 `elapsed_days`。
- 是 LLM 给的**量级估计**，引擎只检查合法性（正数、有限、≤ 36500 天）。
- 没给/非法 → 按 `fallback_days_per_step`（默认 30 天）推算，审计里标 `elapsed_source: fallback`，
  界面提示"精度降级"。`SimState.elapsed_days` 此时为 `None`，不伪造。
- 一致性守卫 C6（数值波动）：带 `elapsed_days` 的步改按"每日变化率"比较，步长可变不再误报；
  没有 `elapsed_days` 的步沿用旧口径。两种口径的基线分开存，不混用。

## 参数（全部可覆盖）

优先级：节点自带 `typical_dwell_days` > `settings.tech_priors[kind]` > `tech_priors["default"]` > 内置占位值。

| 参数（`settings.tech_params`） | 默认 | 说明 |
|---|---|---|
| `default_dwell_days` | 365 | **通用占位值，不是领域事实** |
| `fallback_days_per_step` | 30 | `elapsed_days` 缺失时 |
| `stall_multiplier` | 3 | 停滞阈值 |
| `soft_penalty` | 0.5 | 每个未满足的软前置乘一次 |
| `regression_progress` | 0.5 | 倒退后的进度（占位值） |
| `investment_factors` | low 0.5 / normal 1 / high 1.5 | 占位值 |
| `bottleneck_factors` | low 0.75 / medium 0.5 / high 0.25 | 占位值 |
| `min_dwell_days`、`max_elapsed_days` | 1、36500 | 防止除零/离谱值 |

LLM 在登记新节点时给出的 `typical_dwell_days` 会标 `dwell_source: llm_estimate`、
`dwell_verified: false`，界面显示"典型时长未验证"。**这些数值没有任何来源保证，请自行校准。**

## 分支行为

`tech_state` 是分支作用域的动态状态（`dynamic_state.DYNAMIC_KEYS`）：分叉即回滚到那一刻的技术状态，
各分支互不污染。

- 种子在设置里、历史中还没有快照时，第一次推进前会先把种子锚定到分支头部，
  这样从推进前的步分叉也能回到种子状态。
- **代价**：`tech_state` 每步都变，开启后每步都会写完整快照（连带 `causal_lines`），历史文件会变大。
- 只对**新写入的步**隔离；开启前的旧步没有快照，不迁移。

## 已知边界

- `advance_lines()`（独立因果线推进）**不跑**技术模型：开了 `independent_line_advance` 的实例，
  技术状态不随时间前进。
- 保存设置里的"技术节点"会**覆盖**当前技术状态（含进度）。
- 拆分模式（`split_decision_calls`）下，`{tech_state_hint}` 喂给 `world_evolve`，`elapsed_days`/`tech_updates` 由它输出（有端到端测试）。
- 回测框架（WP5）尚未改用 `elapsed_days` 和 `tech_state` 算"阶段迁移时点偏差"——见 PROJECT.md 已知边界，留待后续。
- 默认违规策略是"降级 + 记录"；修复调用见下方"修复调用（P5b，opt-in）"。

## 修复调用（P5b，opt-in）

开关 `settings.tech_repair_enabled`，**默认关闭**（需要技术模型也开启）。关闭时不会加载 `tech_repair`
workflow、不额外调用 LLM，行为与之前逐字节一致。

- **触发**：本步出现 T4（倒退无原因）/T5（新技术阶段夹值）/T6（采用率超额）/T7（成本无冲击上升）
  之一。**T1/T2/T3 不触发**：进度由引擎按时间推算、硬前置由其它技术决定，重新提议改变不了裁决。
- **流程**（`engine/tech_repair.py`，最多一次）：技术状态回滚到本步开始前 → 让 LLM 重新给出 `tech_updates`
  → `constrain_repair()` 约束 → 引擎重新裁决 → 仅当可修复违规**严格减少**才采纳；否则保留第一次裁决。
- **约束**：不得新增原提议没有的 id；只有 `stage/regression_reason/preexisting/adoption/market/cost_index/
  cost_shock_reason` 的修改会被采纳（投入、前置、典型停留等一律还原）；阶段只能降不能升；修复结果里
  没给的可编辑字段视为撤回该声明。
- **降级**：workflow 缺失/执行失败/回复无法解析/重新裁决出错 → 恢复第一次裁决后的状态，记录 `status="failed"`。
- **记录**：`SimState.tech_repair = {status, codes_before, codes_after, violations_before, notes}`，
  `status` 为 `accepted`/`rejected`/`failed`；界面每步显示。`accepted` 时 `tech_violations` 是修复后
  **剩余**的违规，修复前的在 `violations_before`。
- **不能保证**：修复**只改技术提议，不改写叙事**，叙事与引擎状态的错位仍然存在；LLM 可能为通过检查而补写
  理由（提示词要求"没有叙事依据就撤回，不要编造"，但引擎无法验证）；额外一次 LLM 调用的成本；没有在真实
  LLM 下运行过。

## 相关

- 测试：`tests/test_tech_model.py`（54 个用例，见 `testing_guide.md`）
- 设计/记录：`next_doc/world_simulator_realism_tech_and_causal_engine_plan.md` §4 WP1、§9 P3
