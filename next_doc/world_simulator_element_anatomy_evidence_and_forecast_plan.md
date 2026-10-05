# world_simulator 第二十四轮：元素剖面（anatomy）、证据层、定量骨架与预测呈现

> 状态：**实施中**。第 10 节取值按推荐值执行（用户回复"继续"）。**A1、A2 已完成（2026-10-06）**；A3–A8 未开始。
> 实施进度见文末"实施进度"。
> 前置文档：
> - `world_simulator_realism_tech_and_causal_engine_plan.md`（第二十二轮：技术模型 / 事件采样 / 因果引擎）
> - `world_simulator_element_causal_lines_plan.md`（第二十三轮：统一元素模型，一个元素一条因果线）
> - `world_simulator_fifteenth_round_field_ledger_plan.md`（`field_ledger`，本轮的"可核对流水"沿用它的审计思路）

---

## 1. 背景 / 用户需求

用户的原话归纳：

1. 现在对技术的模拟太简单：没有对技术或项目做进一步拆解、没有分析其中的关键元素，而是直接整体模拟；
   模拟内容过于简略，对真实技术和项目的未来发展没有参考意义。
2. 希望模拟结果**更真实、过程更可解释、更能作为未来预测的参考**，并且**更好地向用户展现**这些内容。
3. 方案要通用，不能只给"技术"打补丁。

本文把"更真实 / 可解释 / 可作预测参考 / 好展示"落成五件事：**拆解、机制、证据、不确定性、展示**。

---

## 2. 现状诊断（基于对代码和文档的阅读，**没有用真实 LLM 跑过**）

### 2.1 元素是扁平的，只有一个标量在推进

- 技术元素 = 6 档 `maturity_stage` + 一个 `progress` + 一个 `bottleneck` 字符串 + `adoption`/`cost_index`
  （`tech_model.normalize_node`，存放在元素线的 `lifecycle` 里）。
- 进度由引擎按 `elapsed / 典型停留时长 × 投入系数 × 瓶颈系数 × 软前置系数` 推进（`tech_model.apply_step`），
  而"典型停留时长"是登记时 LLM 估的（`dwell_source: llm_estimate`、`dwell_verified: false`）——
  "这项技术何时成熟"最终被压成一个**没有出处的猜测数**。
- 非技术元素（项目、组织、政策……）连 `lifecycle` 都没有：只有未来树，加每步一句 `line_updates.summary`。
  第二十三轮解决了"每个元素一条线"，没有解决"一条线里面有什么"。

### 2.2 没有内部结构，瓶颈不是实体

- 没有子系统/工作包、没有带单位和阈值的关键指标、没有竞争路线。
- 瓶颈只是 `bottleneck` 字符串 + 严重度档（`bottleneck_factors`: low/medium/high → 0.75/0.5/0.25），
  "被突破"由 LLM 在叙事里宣布（T11 只要求给理由）。引擎看不到"瓶颈有哪几条解决路径、各自概率与时间"。
- 阶段迁移判据只有 `progress ≥ 1` + 硬前置，没有"可核验的达成条件"。

### 2.3 数值是 LLM 整体重写的，没有可运行的机制

- `next_vars` 每步整体替换，因果只有 `key_drivers` 短语 / `causal_links`。
- 看不出某个指标"为什么这样走"，也没法改一个假设推出下游变化。

### 2.4 没有外部证据

- 全仓库没有任何"检索/研究"环节。现实信息只有用户手填的 `calibration_notes`，
  以及知识库（`data/_knowledge/causal_knowledge.jsonl`）里**过去模拟自己沉淀的东西**（自引用）。
- 技术当前真实状态、指标、竞争路线全靠模型记忆，无出处、无时效。

### 2.5 每步内容量被摊薄

- 单次 `advance_step` 同时塞二十多个 hint（`tech_state_hint`、`sampled_events_hint`、各因果线……），
  每个元素分到的叙事篇幅极少。

### 2.6 不确定性与解释都不完整

- 一次模拟 = 一条故事线；`run_repeated_experiment` 存在，但没有聚合成"分布 / 区间 / 分支频率 / 假设敏感性"。
- 缺少"这个结论依赖哪几条假设"的呈现。

### 2.7 回测很薄

- `backtest_cases/` 只有 2 个案例（`internet_early.yaml`、`personal_computer.yaml`），
  比较的是里程碑年份召回 + 阶段迁移时点偏差；没有**指标数值序列**级别的案例。

### 2.8 展示按"机制"组织，而不是按"用户的问题"组织

- `app.py`（约 7600 行）里有三十多个 expander（技术面板、事件面板、因果引擎、一致性守卫……）。
- 用户真正想问的是："这项技术**能不能成、大概什么时候、卡在哪、靠什么假设、我该盯什么信号**"。
  目前没有任何一页能直接回答。

---

## 3. 目标、设计原则、非目标

### 3.1 核心思路：重新划分三方分工

| 角色 | 负责 |
|---|---|
| **引擎（确定性、可复现）** | 持有"定量骨架"：指标推进、瓶颈解决的随机抽样、里程碑判定、阶段派生、采用门槛、违规裁决 |
| **LLM** | 提出结构（拆解）、写叙事、解释"偏离模型"的事件与原因、在深度模式下为单个元素写细节 |
| **外部证据** | 为结构里的每个字段提供出处、时间、置信度；没有出处的显著标注"LLM 先验" |

这延续项目已有的"**引擎持有结构、LLM 判断语义**"原则（第二十二轮的 R1–R7 / T 码风格），
**不推翻**任何现有机制。

### 3.2 设计原则

1. **可选、可降级、可兼容**：没有剖面的元素行为逐字节不变；开关关闭时提示词、落盘、界面与没有本功能时完全一致
   （沿用项目惯例，用契约测试保证，参考 `tests/test_element_tech_adapter.py`）。
2. **通用模板，不为"技术"特判**：剖面按 `element_type` 取模板，不认识的类型用通用模板。
3. **不内置领域数值**：模板只规定"该关注哪些槽位"，不带任何领域事实或默认数值（延续 `tech_params` "占位值不是领域事实"的原则）。
4. **违规不静默改数**：引擎驳回/夹值必须记录原因（新增 `A` 码，风格同 `T` 码）。
5. **所有数字都带来源状态**：`sourced`（有出处）/ `llm_prior`（LLM 先验）/ `user_confirmed` / `user_edited`。
6. **区间是"参数不确定性的函数"，不是校准概率**：界面与导出必须明确这一点；可信度靠证据（A3）和回测（A8），不靠骨架本身。

### 3.3 非目标

- 不做实时数据订阅 / 行情接入；
- 不自动把联网研究结果标成"已核实"（核实只能由用户操作）；
- 不内置任何领域的先验分布库；
- 不迁移旧实例（见第 4 节第 7 条）；
- 不改动现有 T 码 / C 码 / 因果引擎 / 事件采样的规则本身，只在其旁边增加新机制。

---

## 4. 用户已确认的决策

| # | 问题 | 用户答复 | 对方案的约束 |
|---|---|---|---|
| 1 | 剖面范围 | **通用模板**，按 `element_type` | 见 5.2，模板 + 通用兜底 |
| 2 | 联网研究 | **允许，默认开**；无出处字段保留并显著标"LLM 先验" | 见 5.4，每个字段带 `basis` |
| 3 | 指标趋势模型 | **不限制**，但提供一些供选择 | 见 5.3.1，"推荐库 + 自定义 + 未知不拒绝" |
| 4 | 推进期深化 | 轻量 + **每元素单独调用的深度模式，默认开、可设上限** | 见 5.5，必须有上限与计数可见 |
| 5 | 深度拆解范围 | 默认只对"重点元素"（创建时约 5 个，向导可勾选/改数量/不限）做，其余保持轻量 | 见 5.2.3 / 5.5 |
| 6 | 蒙特卡洛与敏感性分析 | **要做** | 见 5.6，A4 骨架设计为纯函数 |
| 7 | 兼容 | 旧实例不迁移、新实例默认开启 | 见第 7 节 |
| 8 | 叙事展示 | 改成分层结构化展示 | 见 5.8 |

---

## 5. 方案

### 5.1 总体结构与数据落点

```
            创建 / 登记 / 手动刷新                          每步推进
┌───────────────────────────────┐        ┌───────────────────────────────────────────────┐
│ A2 创建期拆解（模板 + LLM）     │        │ pre : anatomy_engine 估计本步到期事项            │
│        ↓                      │        │        → {anatomy_hint}（既成事实，LLM 只叙述）   │
│ A3 element_research（联网）    │        │ LLM : advance_step / world_evolve 主调用         │
│        ↓ 带出处的剖面草稿       │        │        + 重点元素 anatomy_updates（轻量）         │
│ 用户在向导 / 档案页审阅确认     │        │ post: 按真实 elapsed_days 结算：指标/瓶颈/里程碑   │
│        ↓                      │        │        → 阶段派生、采用门槛、A 码裁决、trace 记账  │
│ 剖面落在元素线 `anatomy` 字段   │        │ deep: 触发的重点元素各一次 element_deepen 调用     │
└───────────────────────────────┘        │        （默认开，有每步上限）→ 再走同一套裁决      │
                                         │ 快照（随 causal_lines）→ 分支隔离                  │
                                         └───────────────────────────────────────────────┘
          A6：forecast.py 对"引擎骨架"做无 LLM 蒙特卡洛 / 敏感性 → 预测简报
```

**存储**：剖面是 `settings.causal_lines[i]` 上的**新增可选字段 `anatomy`**。理由：

- 现有的分支隔离、快照（`dynamic_state.DYNAMIC_KEYS` 已含 `causal_lines`）、未来树、`derived_edges`、
  元素分级（`element_tiers`）、展示全部按 `causal_lines` 工作，剖面放这里**零额外接线**即可分支回滚；
- 代价：快照体积增大 → 剖面里**不放长文本和证据原文**，只放数值状态和 `evidence_ids`；证据另存（见 5.4.3）。

**每步记录**：`SimState` 新增 `anatomy_trace`（本步指标/瓶颈/里程碑变化流水）、`anatomy_violations`（`A` 码）、
`anatomy_deep`（深度调用记录：哪些元素、为何触发、成败、耗时）。字段缺省为空时不落盘（与既有可选字段同惯例）。

### 5.2 元素剖面（anatomy）数据模型

#### 5.2.1 通用结构（所有 `element_type` 共用）

| 部分 | 内容 | 说明 |
|---|---|---|
| `components` | 构成/子系统 | 每项：`id`、`name`、`readiness`（0–1，可空）、`requires`（前置组件/指标引用）、`basis` |
| `metrics` | 关键指标 | 单位、当前值（带 `as_of` 与来源）、阈值/目标、边界、**趋势模型**（见 5.3.1）、`basis` |
| `bottlenecks` | 瓶颈（实体化） | 类型、阻塞对象、严重度、状态、**解决路径列表**（各自概率与时间区间）、`basis` |
| `approaches` | 竞争路线 | 并入现有 `substitutes`；各路线可挂自己的指标/瓶颈引用 |
| `adoption_gates` | 采用门槛 | 例如"成本 ≤ X 且寿命 ≥ Y → 才解锁某市场的采用上限"（结构化条件，见 5.3.4） |
| `milestones` | 里程碑 | 可核验的"什么算达成"，**阶段由它派生**（见 5.3.3） |
| `assumptions` | 关键假设 | 可被推翻；挂到指标/瓶颈上；声明"若不成立，骨架怎么改"（供敏感性分析） |
| `signals` | 先行信号 | 现实里该盯什么、先发生什么意味着走向哪条分支（A6 生成，可手改） |
| `meta` | `anatomy_status`（`none/draft/reviewed/verified`）、`key`（是否重点元素）、`researched_at`、`template` |

每个**叶子字段**都带一个来源状态 `basis`：

```json
{"state": "sourced | llm_prior | user_confirmed | user_edited", "evidence_ids": ["ev_0012"], "confidence": "high|medium|low"}
```

没有 `basis` 的字段视为 `llm_prior`。

#### 5.2.2 示例：「固态电池」（`element_type=technology`，示意，数值仅为演示格式，不是领域事实）

```json
{
  "components": [
    {"id": "electrolyte", "name": "固态电解质", "readiness": 0.5, "basis": {"state": "llm_prior"}},
    {"id": "interface",   "name": "电极/电解质界面", "readiness": 0.3},
    {"id": "mfg_process", "name": "规模化制造工艺", "readiness": 0.2, "requires": ["component:electrolyte"]}
  ],
  "metrics": [
    {"id": "energy_density", "name": "能量密度", "unit": "Wh/kg",
     "current": {"value": 350, "as_of": "2026-06", "basis": {"state": "sourced", "evidence_ids": ["ev_0003"]}},
     "target": {"op": ">=", "value": 400},
     "trend": {"kind": "logistic", "params": {"cap": {"value": 500, "low": 450, "high": 600}, "rate_per_year": {"value": 0.25, "low": 0.1, "high": 0.4}}}},
    {"id": "cost", "name": "单位成本", "unit": "USD/kWh", "trend": {"kind": "learning_curve", "params": {"b": {"value": 0.3, "low": 0.2, "high": 0.4}}, "driver": "metric:cum_output"}}
  ],
  "bottlenecks": [
    {"id": "interface_stability", "type": "physical", "blocks": ["component:interface", "milestone:pilot_line"],
     "severity": "high", "status": "open",
     "resolution_paths": [
       {"id": "coating", "desc": "界面涂层", "p_success": 0.5, "duration_days": {"low": 540, "mode": 900, "high": 1500}},
       {"id": "new_electrolyte", "desc": "新电解质体系", "p_success": 0.25, "duration_days": {"low": 900, "mode": 1500, "high": 2400}}
     ]}
  ],
  "milestones": [
    {"id": "pilot_line", "desc": "中试线跑通", "criteria": {"all": [{"bottleneck": "interface_stability", "status": "resolved"}, {"component": "mfg_process", "min_readiness": 0.5}]}, "maps_to_stage": "developer"}
  ],
  "adoption_gates": [
    {"id": "oem_entry", "market": "ev", "criteria": {"all": [{"metric": "cost", "op": "<=", "value": 120}, {"metric": "energy_density", "op": ">=", "value": 400}]}, "unlocks": {"adoption_cap": 0.1}}
  ],
  "assumptions": [
    {"id": "a_interface", "statement": "界面稳定性可在 5 年内工程化解决", "attached_to": ["bottleneck:interface_stability"], "prior_p_true": 0.5,
     "if_false": {"overrides": [{"ref": "bottleneck:interface_stability", "set": {"status": "open", "paths": "slow_only"}}]}}
  ]
}
```

#### 5.2.3 按 `element_type` 的通用模板（"槽位提示"，不含领域数值）

模板放在 `world_simulator/anatomy_templates.py`（纯数据 + 查询函数，可被设置覆盖）。作用：
创建期拆解 / 联网研究 / 深度调用的提示词按类型提示"该关注哪些槽位"，并决定界面默认展示哪些区块。
`element_type` 是自由文本（见第二十三轮），不识别的一律用 `default` 模板。

| `element_type` | components 典型 | metrics 典型 | bottlenecks 典型类型 | milestones 典型 |
|---|---|---|---|---|
| `technology` | 材料/算法/工艺/依赖设施 | 性能指标、成本、良率、寿命 | 物理/工艺/供应链/监管 | 实验室验证→中试→量产→规模化 |
| `project` | 工作包/团队/交付物 | 进度、预算消耗、关键交付完成数 | 资金/人才/审批/外部依赖 | 各验收判据 |
| `organization` | 业务线/产品/团队 | 营收、现金、市占、人员 | 现金/监管/人才/竞争 | 融资/上市/盈利等 |
| `person` | 技能/职位/关系网/资源 | 能力值、影响力、资源量 | 时间/健康/机会/约束 | 职业/事件节点 |
| `asset` | 构成/持有人结构 | 价格、波动、估值倍数、供需 | 流动性/下行风险 | 关键价位/事件 |
| `policy` | 条款/执行机构 | 覆盖率、强度、合规率 | 立法程序/反对力量/执行能力 | 提案→审议→颁布→生效→执行 |
| `market` | 细分/玩家/渠道 | 规模、增速、集中度、价格 | 准入/需求/供给 | 渗透率节点 |
| `resource` | 来源/渠道 | 储量/产能/价格/可得性 | 开采/运输/地缘 | 产能节点 |
| `event_series` | 触发源/传导渠道 | 频率、强度分布 | — | 与事件先验（`event_sampler`）对接 |
| `default` | 构成 | 关键数值 | 阻碍 | 阶段性节点 |

#### 5.2.4 重点元素（key）与非重点元素

- 只有**重点元素**（`anatomy.meta.key = true`）享受：创建期联网研究、深度模式调用、预测简报。
- 非重点元素：保持现在的轻量行为；若主调用的 `anatomy_updates` 里顺带给出了指标/瓶颈，也会被引擎接受（走同一套裁决），
  但不会主动为它多发调用。
- 选择规则（默认）：复用创建期预算裁剪的优先级——**先验边端点 > `technology` 类型 > LLM 给的顺序**，取前 `key_element_count`（默认 5）。
  向导里可勾选/改数量/不限；元素档案页里可随时"设为重点/取消重点"（设为重点会触发一次研究，可取消）。
- 约定与 `element_params` 一致：`null` = 不限，`0` 就是 0 个；非法值回退默认。

### 5.3 引擎定量骨架（`world_simulator/anatomy_engine.py`，纯 Python，不调 LLM）

> 设计目标：**骨架是纯函数**（输入：剖面状态 + 时间跨度 + 随机源；输出：新状态 + 记账），
> 这样推进期和蒙特卡洛（5.6）共用同一份实现，且结果可用种子复现。

#### 5.3.1 指标趋势：推荐库 + 自定义，不设白名单限制

趋势模型 `trend = {kind, params, driver?, source}`。**不限制可用种类**，分三层：

1. **推荐库（界面下拉可选，研究/拆解提示词也会优先让 LLM 从中选）**：
   `linear`、`exponential`（年增长率）、`saturating`（指数趋近上限）、`logistic`、
   `learning_curve`（Wright 定律，需累计量驱动）、`mean_reversion`、`random_walk_drift`、
   `piecewise_table`（分段/插值表）、`step_events`（事件跳变）。
2. **自定义**：`kind: "custom_expr"` + `expr`。表达式用**白名单 AST 求值器**：
   只允许数字、四则与幂、比较与三元、`min/max/abs/exp/log/sqrt`、变量 `t`（距 `as_of` 的天数）、
   `params.*`、`metric.<id>`（引用同元素其他指标）；禁止属性访问、下标、任意函数调用，限制表达式长度和节点数。
3. **未知 / 不会建模**：`kind: "llm_reported"` 或任何不认识的 `kind` → **不拒绝**，退化为
   "引擎不推算，只记录 LLM 每步报告的值"，在界面标"无引擎模型"，并记 `A` 码 info。

每个参数是 `{value, low, high, dist?, source}`：`low/high` 给蒙特卡洛用（`dist` 缺省 triangular，
可选 uniform/lognormal）；缺 `low/high` 的参数在蒙特卡洛中视为"点估计"并在敏感性分析里单独标出。

#### 5.3.2 瓶颈：解决路径、抽样、结算

- 一个瓶颈有 N 条解决路径，每条有 `p_success`、`duration_days {low, mode, high}`、可选前置（别的瓶颈/组件/指标条件）。
- 路径**启动时**（前置满足 + 投入允许）由引擎抽样：是否成功（`p_success`）、耗时（triangular）→ 记下**绝对到期模拟日**
  `resolve_at_day`；失败的路径转 `failed`，按 `fallback` 顺序启动下一条，全部失败则瓶颈保持 `open` 并标 `exhausted`。
- 随机源复用 `event_sampler.draw(sim_id, branch, step, salt, event_id)` 的确定性做法
  （同一 `sim_id/branch/step/salt` 必得同一结果，分支间互不影响，且可复现）。
- **LLM 不能宣布瓶颈解决**：只能在 `anatomy_updates` 里 `propose`（带理由和 `cause_ref`），
  引擎裁决——到期日已过则通过，否则驳回（`A2`）。外部冲击（事件/决策）确实应当提前/推迟解决时，
  必须以"带原因的偏离"形式声明（见 5.3.5），引擎重新锚定到期日并留痕。

#### 5.3.3 里程碑与阶段派生

- 里程碑的 `criteria` 用结构化条件（5.3.4）；每步结算后判定 `reached`，记录 `reached_step` / `reached_sim_day`。
- 阶段派生：元素带 `anatomy` 且 `anatomy_drives_stage` 开启时，`lifecycle.stage` = 所有 `maps_to_stage` 已达成里程碑中最高的一档；
  `progress` 变为**派生显示值**（该档内已达成里程碑占比），不再依赖"典型停留时长"这个无出处的猜测。
- **与现有 T 码的关系**：LLM 声明的阶段迁移仍先过 T1–T11（一步一档、硬前置、倒退须有理由……）；
  在此基础上新增校验——声明的迁移必须有对应里程碑达成，否则驳回（`A3`）。没有剖面的技术节点完全走旧逻辑。
  两套"真相来源"（停留时长 vs 里程碑）的优先级写入契约测试，避免出现第三种行为。

#### 5.3.4 条件语法扩展

现有结构化条件已有 `{"var": ...}`、`{"tech": ..., "min_stage": ...}` / `{"element": ...}`（用于事件先验、未来树接地）。
本轮新增三种引用，写法对齐现有风格：

```json
{"metric": "battery#energy_density", "op": ">=", "value": 400}
{"component": "battery#mfg_process", "min_readiness": 0.5}
{"bottleneck": "battery#interface_stability", "status": "resolved"}
```

`<元素 id>#<子项 id>` 为跨元素引用，解析复用 `element_registry.resolve`（精确规范化匹配，不模糊）。
**实施前先核对现有两处条件求值**（`event_sampler.evaluate_condition` 与 `tree_grounding` 的条件处理），
选一处扩展并让另一处复用，避免出现第三套求值器。这样未来树分支、事件先验、采用门槛都能挂到子项上。

#### 5.3.5 LLM 偏离与裁决（新增 `A` 码）

LLM 每步只需要报告"**偏离引擎模型的事件 + 原因**"。引擎记录 `模型预测值 vs 叙事值` 的偏差。
偏离被接受后默认 **rebase**（保持趋势斜率，把当前值平移到观测值；`deviation_policy` 可改为 `keep_model`）。

| 码 | 含义 | 结果 |
|---|---|---|
| A0 | 剖面引擎本步出错 | 整步跳过，状态不变（info） |
| A1 | 指标值与引擎模型偏差超过阈值且**没有原因/cause_ref** | 保留引擎值，记录偏差（warn） |
| A2 | 声称瓶颈已解决但路径未到期 | 驳回 |
| A3 | 声称里程碑/阶段迁移但判据未满足 | 驳回 |
| A4 | 指标超出声明的 `bounds` | 夹值 |
| A5 | 引用了不存在的 component/metric/bottleneck id | **不阻断**，仅记录（同 T9：笔误不应造成死锁） |
| A6 | 试图修改引擎持有的参数/路径概率/到期日 | 忽略（info） |
| A7 | 采用率超过采用门槛允许的上限 | 夹值（叠加既有 T6） |
| A8 | 深度调用失败 / 降级为轻量 | info |
| A9 | 关键指标的证据已过期仍在驱动引擎 | info（界面提示，不阻断） |
| A10 | 趋势模型为 `llm_reported`/未知，引擎未推算 | info |

#### 5.3.6 时间基准

骨架依赖模拟内时间：沿用 `elapsed_days`（第二十二轮）。开启 `anatomy` 的实例，
提示词必须索要 `elapsed_days`（与"只开事件采样也会索要"同样的处理）；缺失时按 `fallback_days_per_step`
推算并标 `elapsed_source: fallback`，**预测区间同步标"时间精度降级"**。累计模拟日存于 `anatomy_clock`
（若现有状态里已有可用的累计量则复用，A1 核对后定）。

#### 5.3.7 每步在 `advance()` 里的位置

对应现有流水线（`engine/advance.py`）：

1. **LLM 调用前**：`safe_build_hint`——估计本步基础跨度（复用 `event_sampler.estimate_basis_days`），
   把"预计本步到期的瓶颈路径 / 里程碑 / 门槛变化"作为**既成事实提示**注入 `{anatomy_hint}`
   （做法同 `sampled_events_hint`）；同时放入重点元素的**精简摘要**（指标当前值/预测、开放瓶颈状态、下一个里程碑）。
2. **LLM 主调用**：输出里新增 `anatomy_updates`（可选，轻量深化，见 5.5.1）。
3. **LLM 之后**：在 `mechanisms.apply_post_llm` 内、技术裁决之后接入 `anatomy_engine.apply_step`——
   按**真实** `elapsed_days` 结算趋势/到期瓶颈/里程碑，处理偏离，产出 `anatomy_trace` 与 `A` 码；
   阶段派生会改写 `lifecycle`，因此必须在 `tree_grounding`（条件可能读它）**之前**。
4. **深度调用**（5.5.2）：在 `element_discovery.safe_scan_in_step` 同一位置附近（`snapshot_and_check` 之前），
   使新结果进入本步快照；全部包在 try/except 里，失败只记 `A8`，**不影响本次推进**。
5. 独立推进路径 `advance_lines()`（P8）同样接入：各到点线给出的 `anatomy_updates` 合并规则对齐
   `tech_updates`（同一子项多线提议只采纳先到的，其余记 info）。

### 5.4 证据层

#### 5.4.1 研究 workflow：`element_research`

- 新增 `workflows/element_research.yaml`，`type: agent`（形态同 `element_discovery.yaml`：Agent 最终回复就是一个 JSON 对象，
  由 `agent_step_result.extract_agent_json_output` 解析）。**与 `element_discovery` 的区别**：研究 prompt 要求使用联网检索工具，
  不禁止工具调用（`element_discovery` 的 prompt 明确禁止工具）。
- 触发：创建时的重点元素（默认开）、新元素登记后补全（接入第二十三轮已有的 `pending_enrichment` 流程，仅对"值得研究"的元素：
  重点元素或被用户设为重点）、用户在档案页手动刷新。
- 输入：元素基本信息 + 模板槽位提示 + 已有剖面（刷新时）+ 研究预算（最多搜索次数）。
- 输出（JSON）：

```json
{
  "element_id": "...",
  "anatomy_draft": { "components": [...], "metrics": [...], "bottlenecks": [...], "approaches": [...],
                     "adoption_gates": [...], "milestones": [...], "assumptions": [...] },
  "evidence": [ {"ev_id": "tmp_1", "claim": "...(改写后的要点，不贴原文)", "value": 350, "unit": "Wh/kg",
                 "source_url": "...", "source_title": "...", "publisher": "...",
                 "published_at": "2026-03", "confidence": "high|medium|low"} ],
  "unresolved": ["没有找到出处的字段/问题"]
}
```

- 草稿里每个字段要么带 `evidence_ids`，要么显式 `basis: llm_prior`；**没出处的字段保留**但显著标注（用户已确认）。

#### 5.4.2 对网络内容的处理（安全与质量）

- 检索结果是**不可信数据**，不是指令：引擎只做 schema 校验、单位/量级合理性检查（例如值落在 `bounds` 之外 → 标记待审），
  **绝不执行**网页内容里的任何指令；URL 只用于展示。
- 证据只存**改写后的要点 + 出处链接**，不存网页原文。
- 研究失败 / 无联网 / 超预算 → 降级为"仅 LLM 先验"的剖面（A2 的创建期拆解结果），全部字段标 `llm_prior`，**不阻断创建**。

#### 5.4.3 证据存储

- 存放：`data/<sim_id>/evidence.jsonl`，**追加写**、不随分支回滚（证据是"研究时的外部事实"，不属于分支状态）。
  剖面里只存 `evidence_ids`，保持快照小。
- 每条：`ev_id`、`element_id`、`field_ref`、`claim`、`value/unit`、`source_url`、`source_title`、`publisher`、
  `published_at`、`retrieved_at`、`confidence`、`research_run_id`、`status`（`active/superseded/rejected`）。
- **时效**：`research_ttl_days`（默认 180，**现实时间**）之后标 `stale`；界面显示"证据已过期"，不自动删除，不自动刷新。
- 可选（A3 末尾评估）：跨模拟的证据缓存（`data/_evidence_cache/`，按规范化实体名 + 主题 + TTL 命中），避免同一技术重复研究；
  与现有 `data/_knowledge/` 分开，因为两者性质不同（外部事实 vs 模拟沉淀的因果经验）。

#### 5.4.4 审阅与"核实"

- 研究产出先是 `draft`。**草稿默认就驱动引擎**（否则"默认开联网研究"没有实际作用），但界面显著标"未审阅"，
  且**预测简报的置信等级会因为关键字段仍是 `llm_prior` / 未审阅而下调**（规则见 5.7）。
- 用户在向导/档案页逐字段"确认 / 编辑 / 驳回"→ `user_confirmed` / `user_edited` / 驳回后回到 `llm_prior`。
- 可选开关 `require_review_to_drive`（默认关）：开启后只有 `reviewed` 的字段才驱动引擎，其余只展示。

### 5.5 推进期深化

#### 5.5.1 轻量模式（同一次主调用，无额外调用）

- 提示词里只给**重点元素**（且在 `element_tiers` 分级为活跃的）一份精简摘要，长度受 `light_digest_chars` 约束，
  整体并入第二十三轮 E4 的 prompt 预算裁剪，**不让提示词无限增长**。
- LLM 在 `anatomy_updates` 里只报：`metric_deviations`（偏离 + 原因）、`bottleneck_proposals`、
  `new_subitems`（推进中发现的新组件/瓶颈/指标）、`signals`。全部由 5.3.5 的规则裁决。

#### 5.5.2 深度模式（每元素单独调用，**默认开**，有上限）

- 新增 `workflows/element_deepen.yaml`（`type: agent`，默认**不开联网**，由 `deep_allow_search` 控制）。
- **只对重点元素**，且仅在**触发条件**满足时调用（避免每步每元素都调）：
  里程碑本步达成 / 瓶颈状态变化或路径到期 / 指标偏离超过阈值 / 本步有事件命中该元素 /
  元素停滞 / 距上次深度调用已满 `deep_cadence_steps`（保底节奏）。
- 输入：该元素剖面 + 本步引擎结算结果（`anatomy_trace`）+ 主调用的相关叙事片段 + 命中的事件。
- 输出：该元素本步的**细节叙事**（几段而不是一句话，进入分层展示的 L2）+ `metric_deviations` / `bottleneck_proposals` /
  `new_subitems` / `signals`，**再走同一套 `A` 码裁决**（不给深度模式特权）。
- **上限与可见性**：`deep_max_calls_per_step`（默认 3）、`deep_max_calls_total`（默认不限）、`deep_time_budget_sec`；
  超限的元素顺延到下一步，并在界面显示"本步深度调用 2/3、顺延 1 个"。
- **成本提示**：每步最多 = 1 次主调用 + 最多 3 次深度调用（+ 现有的可选修复/扫描调用）。设置页明确写出，
  并提供一键"快速模式"（关闭深度模式，只剩轻量），不需要逐项关。

### 5.6 预测与不确定性（`world_simulator/forecast.py`）

- **蒙特卡洛**：对骨架（5.3，纯函数）做无 LLM 的快速模拟，`mc_runs` 默认 1000，种子固定可复现。随机来源：
  ① 参数不确定性（`low/high/dist`）；② 瓶颈路径成败与耗时；③ 假设 Bernoulli(`prior_p_true`)；
  ④ 带 `anatomy_effects` 的事件先验（给 `event_sampler` 先验新增**可选**结构化 `effects`，没有则不参与）；
  ⑤ 项目类元素的"失败/取消"风险（作为一个可选的路径/hazard）。
- **输出**：
  - 每个里程碑/门槛的达成时间 `P10/P50/P90` + "视野内未达成"的概率；
  - 每个指标的扇形区间（分位带）；
  - 瓶颈的解决顺序频率；
  - 未来树分支的**激活频率**（树分支的结构化条件在骨架状态上求值，复用 `tree_grounding` 的条件求值）；
  - **假设条件化结果**："假设 X 成立 / 不成立"下的里程碑 P50（由假设 Bernoulli 采样分层得到）。
- **敏感性分析**：对每个带 `low/high` 的参数做一次一次一个的上下界扰动，输出对关键里程碑 P50 的影响排序（龙卷风图）；
  用户在档案页**改一条假设/参数后可立刻重跑**（what-if）。
- **先行信号监测清单**：对每个关键不确定性给出"现实里该盯什么 / 先发生什么意味着走向哪条分支"，
  可手改，并接入现有的**现实回填**（`reality_check.py`）——用户回填真实发生的事，用来比对这些信号。
- **诚实标注**：所有区间旁固定标注"区间由已声明参数的不确定性产生，**不是校准过的概率**"，并显示参数中有多少比例是 `llm_prior`。

### 5.7 可解释性

- 每个结论可回溯：`指标变动 ← 机制（趋势模型 / 偏离 / 事件跳变）← 原因（事件 id / 决策选项 / 树分支 / 瓶颈路径）← 证据`。
  实现：`anatomy_trace` 每条记录带 `cause_ref` 与 `evidence_ids`，记录结构对齐 `field_ledger`
  （`field / kind / amount / value_before / value_after / reason`，加 `source: model|deviation|event|llm_reported|rebase`），
  **同样做"自洽核对"**：`value_before + Δ = value_after`、本步各条流水加总与指标净变化一致，不一致写入违规而不是静默修正。
- **置信等级由引擎按规则计算，不由 LLM 评判**，规则透明可展示，例如：
  - 关键指标/瓶颈中 `sourced` 或 `user_confirmed` 的比例；
  - 证据是否过期；
  - 关键参数区间宽度（`high/low` 比）；
  - 是否有同类回测案例（A8）。
  输出 `高/中/低` + 每条扣分原因。具体阈值在 A7 实施时写入文档并开放配置。

### 5.8 展示

沿用项目的展示载体（`app.py` 界面 + `html_export.py` 导出），**每个阶段自带对应展示**，不是最后一起补。

1. **元素档案页**（元素详情里新增，入口在因果线总览的元素行；`element_view.py` 增加档案视图所需的数据构造函数）：
   - 现状一句话 + 来源状态徽标（有出处 / LLM 先验 / 已确认 / 未审阅 / 过期）；
   - 组件就绪度；
   - 指标曲线 + 预测分位带 + 阈值线；
   - 瓶颈清单（路径、概率、预计到期、当前状态）；
   - 可编辑的假设与参数（改完可 what-if 重跑）；
   - 证据列表（有出处 / LLM 先验分开显示，带链接、日期、置信度）；
   - 依赖/关系图；
   - 逐步变化及原因（`anatomy_trace` 的可读形式）。
2. **预测简报**（每个模拟一页 + 每个重点元素一节）：结论一句话、置信等级及扣分原因、前三个关键不确定性、
   里程碑时间分布、假设条件化结果、先行信号监测清单。
3. **时间线叙事分层**：
   L1 一句话摘要 + 本步关键变化（里程碑/瓶颈/关键指标）→ L2 活跃重点元素变化卡（含深度叙事、证据）→ L3 完整叙事（默认折叠）。
4. **现有机制面板收进"高级"**：约三十个 expander 归入一个"高级机制"分组，**不删除、不改行为**，只改默认展开层级。
5. **HTML 导出**同步包含元素档案与预测简报（`html_export.py` / `html_export_mechanisms.py`），保持导出文件自包含。
6. **服务/CLI**：`docs/service_api.md` 增补档案、证据、预测的读取与重跑接口（`server.py` / `tool_api.py` 对应薄封装）。

---

## 6. 与现有机制的关系

| 现有机制 | 本轮如何衔接 |
|---|---|
| `tech_model`（T 码、`lifecycle`） | 不改规则；有剖面的技术元素，阶段由里程碑派生（5.3.3）；无剖面的节点完全走旧逻辑 |
| `event_sampler` | 复用确定性抽样 `draw`、基础跨度估计、条件求值；事件先验新增**可选** `effects`；不改现有字段语义 |
| `causal_engine` / `tree_grounding` / `tree_effects` | 条件语法扩展三种子项引用；未来树分支条件可挂子项；不改裁决规则 |
| `element_registry` / `element_tiers` | 剖面挂在元素线上；分级决定谁进入每步摘要；`resolve` 用于跨元素引用 |
| `element_discovery`（E5 周期扫描） | 新登记元素若满足"值得研究"则进入研究流程；扫描本身不变 |
| `field_ledger` | 沿用其"可自洽核对"思想，`anatomy_trace` 结构对齐；二者各管各的字段，不互相改写 |
| `reality_check` | 先行信号监测清单接入现实回填 |
| `knowledge_base` | 不动；证据另存，不混入因果知识库 |
| `backtest` | 新增指标级案例（A8），不改现有两个案例 |
| 分支（`dynamic_state`、`branch_manager`） | 剖面随 `causal_lines` 快照，分叉即回滚；`anatomy_trace` 属于各分支历史；证据库为追加写、跨分支共享 |

---

## 7. 兼容、开关与参数

- `settings.anatomy_enabled`：新实例由 `materialize_simulation()` 默认写入 `True`（调用方明确传值则尊重，同 `element_modeling_enabled`）；
  **旧实例 manifest 里没有该 key = 关闭，一切照旧、不迁移**。旧实例可在设置页手动开启（要求 `element_modeling_enabled` 已开启），
  开启只影响之后新增/编辑的剖面，不回填历史。
- 关闭时：提示词、落盘、界面与没有本功能时**逐字节一致**（契约测试覆盖：同一组输入，开关关闭前后输出完全相同）。
- 参数放 `settings.anatomy_params`，约定同 `element_params`：`null` = 不限、`0` 就是 0、非法值回退默认并列入 `invalid_param_keys`。

| key | 默认 | 说明 |
|---|---|---|
| `key_element_count` | 5 | 重点元素个数；`null` = 不限 |
| `research_on_create` | true | 创建时对重点元素联网研究 |
| `research_on_register` | true | 新登记且"值得研究"的元素补全研究 |
| `research_max_searches` | 6 | 单个元素一次研究的搜索次数上限 |
| `research_ttl_days` | 180 | 证据过期（现实天数） |
| `require_review_to_drive` | false | 开启后仅已审阅字段驱动引擎 |
| `anatomy_drives_stage` | true | 阶段由里程碑派生 |
| `deviation_policy` | `rebase` | 接受偏离后 `rebase` / `keep_model` |
| `light_digest_chars` | 600 | 每个重点元素摘要的字符预算 |
| `deep_enabled` | true | 深度模式开关（"快速模式"= false） |
| `deep_max_calls_per_step` | 3 | 每步深度调用上限 |
| `deep_max_calls_total` | null | 整个实例的深度调用总上限 |
| `deep_cadence_steps` | 3 | 保底节奏（无触发时每 N 步深化一次） |
| `deep_allow_search` | false | 深度调用是否允许联网 |
| `deep_time_budget_sec` | 待 A5 定 | 单步深度调用总耗时预算 |
| `mc_runs` | 1000 | 蒙特卡洛次数 |
| `mc_seed` | 固定 | 可复现 |
| `trend_recommended` | 推荐库列表 | 仅影响下拉与提示词偏好，**不限制可用类型** |

---

## 8. 分期实施

原则：**一次一个阶段**；每个阶段自带对应展示；每个阶段结束都要：更新 `docs/`（新增 `anatomy_guide.md` 并随阶段增补，
只写已落地行为）→ 跑通该阶段全部测试 → 更新本文进度 → 重新打包 zip。

### A1 剖面数据模型、存取层、兼容（**不改 LLM 行为**）
- 新增 `anatomy.py`（规范化 `normalize_anatomy`、各部分校验、`basis`/证据引用校验、读写适配）、`anatomy_templates.py`；
  `settings.anatomy_params` 读取与非法值回退；元素线 `anatomy` 字段规整（并入 `element_registry.normalize_element` 的"只规整出现了的字段"约定）；
  `materialize_simulation` 默认开启；核对累计模拟日（`anatomy_clock`）是否已有可复用来源。
- 展示：元素档案页**只读骨架版**（有剖面就显示结构与来源徽标，没有就不出现）。
- 测试：规范化各类非法输入、旧元素逐字节不变（契约）、快照/分叉回滚带剖面、参数回退。
- 验收：没有 `anatomy` 的任何旧数据/旧实例行为完全不变；有剖面的元素能存、读、快照、分叉。

### A2 创建期拆解 + 向导审阅
- 创建提示（`spec_generator._resolve_causal_lines_hint`，创建单次/拆分两条路径共用）：对**重点元素**按模板索要 `anatomy_seed`；
  `prepare_created_lines` 增加剖面规整与裁剪；重点元素选择逻辑（5.2.4）。
- 展示：向导新增"重点元素勾选 + 数量设置"和"剖面审阅/编辑"步骤（逐字段确认/编辑/驳回；全部字段显著标 `LLM 先验`）。
- 测试：模板选择、重点元素排序与数量边界（`null`/0/非法）、拆解草稿规整、两条创建路径一致。
- 验收：不开联网也能得到可审阅的、全部标 `llm_prior` 的剖面。

### A3 联网证据研究 + 出处
- **先核实**：宿主 agent 步骤是否真的带联网工具（见第 9 节风险 1）。
- `element_research.yaml`、`element_research.py`、证据存储（5.4.3）、时效标记、研究预算、失败降级。
- 展示：证据列表、来源状态徽标、过期提示、手动"刷新研究"按钮、研究进度提示。
- 测试：模拟 agent 输出（含畸形/缺字段/注入式文本）、单位量级检查、降级路径、TTL。
- 验收：关闭联网或联网失败时创建不被阻断；每个数值字段都能看出"有出处 / LLM 先验"。

### A4 引擎定量骨架
- `anatomy_engine.py`：趋势推荐库 + 安全表达式求值器 + 未知趋势退化；瓶颈路径抽样（种子复现）；里程碑结算与阶段派生；
  采用门槛；条件语法扩展（先统一两处现有求值）；`A` 码裁决；`anatomy_trace` 记账与自洽核对；
  `advance()` 与 `advance_lines()` 接入；`{anatomy_hint}`（既成事实 + 精简摘要）；要求 `elapsed_days`。
- 展示：指标曲线（已发生部分）、瓶颈状态、里程碑进度、逐步变化及原因。
- 测试：每种趋势的数值、表达式白名单（恶意输入）、种子复现与分支独立、`A1–A10` 各码、与 T 码并存的优先级契约、
  无剖面旧路径逐字节不变。
- 验收：同一输入同一种子结果完全一致；任何 LLM 声明都不能绕过 `A2/A3`。

### A5 推进期深化（轻量 + 深度）
- `anatomy_updates` 协议与裁决；`element_deepen.yaml`；触发条件、每步/总量上限、顺延逻辑、耗时预算、失败降级；
  与 `project.yaml` 入口超时的配合（见风险 2）。
- 展示：时间线分层（L1/L2/L3）、重点元素变化卡、"本步深度调用 n/上限、顺延 m 个"的可见计数。
- 测试：触发条件各分支、上限与顺延、失败不影响推进、`advance_lines` 合并规则。
- 验收：关闭 `deep_enabled` 即退回轻量，行为与 A4 完成时一致。

### A6 蒙特卡洛 / 敏感性 / 监测清单
- `forecast.py`（复用 A4 的纯函数骨架）；事件先验可选 `effects`；假设条件化；what-if 重跑；监测清单与 `reality_check` 接入。
- 展示：里程碑时间分布、扇形区间、龙卷风图、假设条件化对比、监测清单。
- 测试：固定种子结果、分位数正确性、假设分层、性能上限（`mc_runs` × 元素数 × 步数）、what-if 与重算一致。
- 验收：1000 次运行在目标规模（≈20 元素 × 100 步）下耗时可接受（阈值 A6 开始前先量一次再定）。

### A7 预测简报与导出收尾
- 预测简报页；置信等级规则与配置；HTML 导出（档案 + 简报）；"高级机制"分组；`service_api.md` 接口。
- 测试：导出自包含、置信规则各档位、旧实例导出不变。
- 验收：不看任何 expander，用户能在预测简报一页内回答"能不能成 / 何时 / 卡在哪 / 靠什么假设 / 盯什么信号"。

### A8 指标级回测
- `backtest_cases/` 新增带真实历史**数值序列**的案例（目标 4–6 个，覆盖学习曲线型、S 曲线型、项目工期型）；
  评分：区间覆盖率（P10–P90 是否包含真值）、分位损失（pinball）、里程碑时点误差。
- **防泄漏**：回测不跑实时检索，改用案例文件里提供的"截止日前资料包"作为证据来源，否则实时搜索会看到未来。
- 验收：能给出"骨架在这些案例上区间是否过窄/过宽"的客观数字，并反馈到置信等级规则（5.7）。

---

## 9. 风险与待核实事项

1. **联网能力待核实（A3 前必须确认）。** 我已确认宿主有内置 `web_search` 工具，且 workflow 的 agent 步骤使用完整默认工具注册表
   （`workflow/agent_spawn.py`）；但**没有找到独立的网页抓取/读取工具**。若只有搜索摘要，证据质量会受限
   （只能引用搜索结果摘要，无法读全文）。A3 开始前先实测一次 `type: agent` 步骤能否调用检索、能拿到什么，
   再决定是否需要 bash 抓取（受网络白名单约束）或改为"搜索摘要 + 用户确认"。
2. **超时。** `project.yaml` 里 `advance_simulation` 入口 `timeout_sec: 300`，`batch_advance_daily` 为 900。
   深度模式默认每步最多 3 次额外调用，可能超时。A5 需要：并行/顺序策略、`deep_time_budget_sec`、
   必要时调高入口超时或在自动挡下降低上限。
3. **LLM 遵守度未验证。** 与此前所有轮次一样，没有在真实 LLM 下验证 `anatomy_updates`、研究 JSON 的遵守情况与质量；
   这是推断。每个阶段保留"解析失败/不合规 → 降级 + 记录"路径。
4. **提示词膨胀。** 重点元素摘要受 `light_digest_chars` 和 E4 预算约束，但最终效果需在真实运行中观察。
5. **区间 ≠ 校准概率。** 参数的 `low/high` 多数初始来自 LLM 先验；A3 证据与 A8 回测才能提高可信度。界面必须持续标注。
6. **自定义表达式安全。** `custom_expr` 必须是白名单 AST 求值（无属性/下标/任意调用，限长限节点），并有恶意输入测试；
   不使用 `eval`。
7. **网页内容注入。** 检索结果当不可信数据处理（5.4.2）；不执行、不拼接进会改变行为的指令位置之外。
8. **两个"阶段真相来源"。** 停留时长 vs 里程碑；以契约测试固定优先级，避免行为漂移。
9. **时间基准依赖 LLM 报 `elapsed_days`。** 缺失时降级并在区间上标注；不伪造。
10. **快照/历史体积。** 剖面随 `causal_lines` 每步快照；`anatomy_trace` 字段设上限、证据外置以控制增长。
11. **指标与 `vars` 的镜像（未决）。** 指标是否需要同步写回 `vars.*` 以便旧模板读取？默认**不镜像**，
    引擎权威值通过 `{anatomy_hint}` 告诉 LLM；若 A4 发现模板确有需要，再单独设计，避免与 LLM 对 `next_vars` 的整体重写冲突。
12. **深度模式默认开的成本。** 已用 per-step 上限、触发条件、总量上限、"快速模式"一键关闭、界面计数四道约束；
    默认值是否合适需要你确认（见第 10 节）。
13. **同类字段命名冲突。** 新增 `milestones/metrics/components` 等键出现在元素线上，A1 需检查与现有 `causal_lines` 字段、
    未来树节点字段、`lifecycle` 字段无重名。

---

## 10. 我对你的回答所做的具体化（需要你确认，或直接说"按推荐"）

你的回答已经定了方向，下面这些是**我补的具体取值/取舍**，请确认或修改：

1. **草稿默认驱动引擎**（标"未审阅"、简报置信下调），而不是"必须审阅后才生效"；提供 `require_review_to_drive` 开关。
2. **深度模式默认不联网**（`deep_allow_search=false`）；联网研究只发生在"创建 / 新登记 / 手动刷新"。理由：深度调用每步都可能发生，联网会显著放大耗时与成本。
3. **深度模式默认值**：每步最多 3 次、总量不限、保底每 3 步一次、触发条件如 5.5.2。
4. **重点元素默认 5 个**，选择规则沿用创建期预算裁剪的优先级。
5. **证据时效 180 天（现实时间）**，过期只标注，不自动删除/刷新。
6. **证据按实例存放**（`data/<sim_id>/evidence.jsonl`），跨模拟缓存作为 A3 末尾的可选项，不进首版。
7. **接受偏离后默认 rebase**（保持斜率、平移当前值）。
8. **阶段由里程碑派生默认开启**（仅对有剖面的元素；无剖面一律旧逻辑）。
9. **`mc_runs` 默认 1000**，种子固定可复现。

---

## 11. 文档与交付清单

- 每个阶段：`docs/anatomy_guide.md`（新增，随阶段增补，只写已落地行为）、必要时更新 `docs/element_model_guide.md`、
  `docs/tech_model_guide.md`（只加交叉引用）、`docs/event_sampling_guide.md`（A6 的 `effects`）、
  `docs/backtest_guide.md`（A8）、`docs/service_api.md`（A7）、`docs/README.md` 索引。
- 本文随阶段更新"实施进度"；全部完成后补一份实施记录（`*_implementation_record.md`）。
- 每个阶段结束重新打包 zip。
- 说明：`next_doc/README.md` 目前没有为 world_simulator 系列文档设专门的索引分组，本文沿用现状，不在本次修改索引；
  如需把 world_simulator 系列归并成一个分组，可以单独做一次整理。

---

## 12. 实施进度

| 阶段 | 状态 | 说明 |
|---|---|---|
| A1 数据模型、存取层、兼容 | ✅ 2026-10-06 | `anatomy.py`、`anatomy_templates.py`、`normalize_element` 接线、新实例开关、合并保护、`build_profile` 只读档案、设置页勾选；31 个新用例、8 个变异全转红。详见 `external_projects/world_simulator/docs/anatomy_guide.md` |
| A2 创建期拆解 + 向导审阅 | ✅ 2026-10-06 | 创建提示（两条路径共用）、重点元素选择、`anatomy_seed` 落成草稿（引擎强制 `llm_prior`、剥引擎状态）、空壳、向导设置/勾选/逐条审阅；17 个新用例、9 个变异全转红，既有测试零修改。详见 `docs/anatomy_guide.md` §11 |
| A3 联网证据研究 + 出处 | ⏳ 未开始 | 开始前须先实测宿主 agent 步骤能否联网（风险 1） |
| A4 引擎定量骨架 | ⏳ 未开始 | |
| A5 推进期深化 | ⏳ 未开始 | |
| A6 蒙特卡洛 / 敏感性 / 监测清单 | ⏳ 未开始 | |
| A7 预测简报与导出收尾 | ⏳ 未开始 | |
| A8 指标级回测 | ⏳ 未开始 | |

### A1 与计划的偏差
1. `sourced` 必须带 `evidence_ids`，否则降为 `llm_prior`（计划未写）。
2. `anatomy_enabled` 仅在元素模式开启时默认写入 `True`（计划写"默认写入 True"；元素模式关闭时写它没有意义且会改动旧形态的 settings）。
3. 新增合并保护：带剖面的元素不能被并入（对应计划 §9 风险 13 的数据丢失面，计划未列）。
4. 累计模拟日（`anatomy_clock`）核对结论：复用每步 `elapsed_days`，不新增存储。
5. `deep_time_budget_sec` 留给 A5 定。

### A2 与计划的偏差
1. 无 `kind` 的旧式独立线不当作元素，不拆解。
2. 审阅粒度为"每条拆解"（指标现值随条目确认），不到叶子字段。
3. 向导不提供逐字段编辑控件，编辑通过因果线 JSON；"驳回"= 删除该条。
4. 驳回不自动清理悬空引用，交给体检报告。
