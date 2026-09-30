# world_simulator 改进计划（第二十二轮）：让技术/事物发展更真实、因果树可执行可校验

> **状态（2026-09-30 更新）：方案已确认，按阶段实施中。**
> 已完成：**P0（WP0 分支隔离）**、**P1（WP4 一致性守卫+真实性体检）**，详见文末 §9。未开始：P2（WP5 回测）起。
> 本文只依据阅读代码得出结论，没有跑过真实 LLM，也没有在运行时复现
> 下文 §2.4 之外的缺陷——每一条"现状"都标注了代码位置，"推断"会明说。
> §2.4 的缺陷已在 P0 里写复现测试确认（先红后绿）。

## 0. 一页纸结论

现在"不真实"的根源不是提示词写得不够细，而是**结构性的**：

1. **世界怎么变，完全由一次 LLM 调用决定**。代码层只做"事后记账"
   （`resource_guard`/`field_ledger` 保证账对得上），不做"事前约束"
   （变化本身合不合理没人管）。
2. **技术不是一等公民**。它只是 `capabilities_gained`（叙事当下自报的
   一次性事件）+ 一个 6 档 `maturity_stage` 标签：没有前置依赖、没有随
   时间/投入推进的机制、成熟度可以跳级也可以倒退，代码不拦。
3. **因果树是"标签树"，不是"机制树"**：`prerequisites` 只存不查；
   `likelihood` 从不和结果对账；状态迁移无合法性检查；`causal_links`
   是事后自述，不是事前输入。
4. **一个真实缺陷**：因果树状态存在 `manifest.settings`，而它是**整个
   实例共享、不按分支隔离**的，分叉后两条时间线会互相污染因果树（§2.4）。
5. **没有衡量"真实"的尺子**：不能量化，就无法证明任何改动"更真实"。

方案分 6 个工作包（WP0–WP5），原则：**LLM 继续写故事，引擎负责持有
状态、约束合法性、按基准率抽样外生事件、并度量结果**；全部新机制
默认关闭（opt-in），旧实例行为不变。

推荐顺序：**WP0 修缺陷 → WP4 先量尺子 → WP5 回测基线 → WP1 技术模型
→ WP2 事件采样 → WP3 因果可执行化**（理由见 §5）。

## 1. 与项目既有哲学的关系（必须先讲清）

`relationship.py`、`attribution.py` 等模块反复强调"不做伪精确、避免
看起来是精确的因果引擎、实际是规则拍脑袋"。这条原则是对的，本方案
不推翻它，而是把"引擎该做什么"划清：

| 引擎**做** | 引擎**不做** |
|---|---|
| 持有权威状态（技术阶段/进度、待兑现因果） | 用固定公式取代 LLM 的叙事推理 |
| 拦截**结构性**不合法（跳级、前置未满足、已排除分支复活） | 判断语义上"合不合理" |
| 按**显式、可编辑**的基准率抽样外生事件 | 内置任何领域数值（所有先验由用户/知识库提供） |
| 违规**透明记录**（沿用 `resource_violations` 的做法） | 静默篡改数值 |
| 事后对账、算校准率 | 声称"概率精确" |

先验数值本身仍可能是 LLM 的猜测——所以每条先验带 `source` 与
`verified` 标记，界面上明确区分"用户确认"与"LLM 估计（未验证）"。

## 2. 现状诊断（读代码所得）

### 2.1 世界变化只由一次 LLM 输出决定，代码只做事后记账

`engine/advance.py::advance()`：`next_vars = dict(data.get("next_vars"))`
直接取 LLM 输出；随后 `_apply_resource_guard`（下限夹值）、
`_check_resource_relations`（转移守恒，只留痕）、`_check_field_ledger`
（记账一致，自动补记）都是**已经变了之后**核对"账"。没有任何一步在
LLM 写之前告诉它"哪些变化在当前状态下不可能"。
项目里唯一的随机性来源是 LLM 采样（`engine/` 下无 `random`），而 LLM
倾向输出"最可信的叙事"，**尾部事件与真实的方差被系统性抹平**（这一
条是对 LLM 行为的推断，项目内没有测量数据，见 WP4 的测量项）。

### 2.2 技术不是一等公民

- 数据：`SimState.capabilities_gained`（"只记录这一步新增"，
  `state_model.py`）+ `maturity_stage ∈ {lab, expert, developer,
  consumer, cheap_at_scale, infrastructure}`（`capability_discovery.py`）。
- 缺失：前置依赖、成熟度推进机制、扩散/成本曲线、瓶颈、替代竞争、
  停滞与倒退。代码里检索不到任何 `maturity_stage` 的顺序/跳级/倒退
  校验。结果是技术要么"突然出现"，要么"按叙事需要突然成熟"。
- 已有可复用的基础：6 档阶段本身就是采用阶段的序数；有独立节奏的
  因果线（`advance_every_n_steps` + `engine/advance_independent.py`）
  可以让"技术线按年推进、主线按月推进"；State/Belief 分离可以表达
  "炒作 vs 现实"。

### 2.3 因果树是标签树

- `causal_tree.py::_normalize_branch` 只**规整**`prerequisites`；全库
  没有任何代码读取它做判断（`state_model.py:103` 的 `prerequisites`
  属于 `ChoiceOption`，是另一回事）。
- `likelihood`（high/medium/low）只出现在 prompt 与展示里，从不与
  分支的最终结局（resolved/expired/invalidated）对账；`reality_check`
  只影响知识库条目的 `confidence`，不影响树。
- `apply_tree_updates` 接受任意状态迁移（如 `resolved` → `active`、
  前置未满足就 `active`），只有一个基于时间的
  `suggest_status_transitions`（建议 `dormant/emerging → expired`）。
- `causal_links`（`driver`/`effect`/`relation_type`/`delay_steps`）由
  LLM **事后**自述；`build_causal_graph` 只做聚合统计；
  `causal_graph_hint` 把出现次数反馈进 prompt，是统计而非机制。
  `delay_steps` 以"步"为单位，而步长可变（`time_granularity_mode`
  为 auto/guided 时）——同样的 `delay_steps=2` 可能是 2 个月也可能是
  2 年（推断：文档说明的语义如此，未逐处核实所有消费方）。
- 树与 `vars` 脱钩：分支被标为 `active` 时，不对任何变量/因果边施加
  影响，只是一个展示状态。

### 2.4 缺陷：因果树与待兑现效果不按分支隔离

`fork_branch`（`branch_manager.py`）只复制历史与自动挡配置
（`pilot_config` 是按分支独立存储的，这点做对了），**没有复制或回滚
`manifest.settings`**。而 `_apply_tree_updates`
（`engine/causal_lines.py`）把分支状态就地写进
`manifest.settings["causal_lines"]`；`relationship_pending_effects`、
`confirmed_*` 也在 `settings` 里。因此：

- 从第 3 步分叉，在新分支上推进 → 因果树状态被改写；
- 切回主线 → 主线看到的是被"另一条时间线"改写过的树。

对"因果树可信"这个目标这是硬伤：分支对比看到的树状态本就不可比。
（读代码所得；实施第一步是先写一个复现测试，复现不了则撤销此条。）

### 2.5 没有衡量"真实"的尺子

`quality_signals.py` 有 5 个计数指标，并明确说明"因果一致性"因需理解
语义而不做。本方案补的是**不需要语义**的结构性检查（跳级、前置违规、
效果未兑现、事件密度、变量波动异常），并加上回测（有已知答案的历史）。
两者都不能证明"语义上合理"，只能证明"结构上没有明显作弊"和"在有
答案的案例上更接近历史"，文档与界面都会这样标注。

## 3. 设计原则

1. **默认关闭，逐项 opt-in**：每个 WP 一个总开关（见 §6），旧实例与
   未开启时行为逐字节不变。
2. **先约束输入、再审核输出、最后才是可选修复**：引擎把权威状态写进
   prompt（"X 目前处于 developer，进度 0.7，前置 Y 未就绪"），LLM 据此
   写作；输出违规则透明记录；仅在开启时才发起一次修复调用（对照
   `ledger_correction` 的既有模式）。
3. **提议–审核**：LLM 提议状态变化（`tech_updates`），引擎裁决。
4. **透明**：违规、夹值、抽样事件、自动迁移都要在时间线上可见并可
   审计，不静默。
5. **动态状态按分支存储**（WP0 是其余所有 WP 的前置）。
6. **先量后改**：每个改进都要能用 WP4/WP5 的指标对比 before/after。

## 4. 方案

### WP0 — 修复分支污染（缺陷修复，其余 WP 的前置）

- 新增"分支作用域动态状态"：把 `causal_lines`、`relationship_pending_
  effects`（以及后续 WP 新增的 `tech_state`、`causal_pending`）作为
  `SimState.dynamic_snapshot` 的内容，**有变化时写入该步**，读取时向
  前回溯最近一份；`fork_branch` 时新分支的截断点节点自带当时的快照，
  天然实现"回滚到那一刻的树"。
- 兼容：旧实例没有快照 → `advance()` 回退读 `manifest.settings`
  （与今天完全一致）；只从新写入的步开始隔离，**不做数据迁移**。
- 改动点：`state_model.py`（新字段）、`engine/advance.py` /
  `engine/causal_lines.py`（读写改走快照）、`branch_manager.py`（无需
  额外复制逻辑，只需保证截断节点带快照）。
- 验收：复现测试（先红后绿）+ 既有测试全绿。

### WP4 — 一致性守卫 + 真实性体检（先做，作为尺子）

纯 Python、不调 LLM。`world_simulator/consistency_guard.py`（新）：

| 检查 | 含义 |
|---|---|
| C1 技术跳级/倒退 | `maturity_stage` 相邻步序数差 >1 或 <0（倒退需带原因才不告警） |
| C2 分支复活 | 已 `resolved/invalidated/expired` 的分支再次 `active/emerging` |
| C3 前置违规 | 分支 `active` 但 `prerequisites` 未满足（首次真正读取该字段） |
| C4 状态迁移非法 | 不在允许迁移表内的状态跳变 |
| C5 事件密度 | 连续 N 步每步都有 `major_decision`/`structural_change`/新能力 → "戏剧化偏差"信号（Q：阈值 N 由数据定，先只统计不告警） |
| C6 变量波动异常 | 数值叶子字段本步变化量相对其历史标准差的 z 值超阈（`_flatten_numeric_leaves` 已有，可复用） |
| C7 因果边覆盖 | `causal_links` 引用了未在 `declared_causal_graph` 声明的边的比例 |
| C8 树校准 | 已终结分支按创建时 `likelihood` 分组的命中率（high 到底有几成 resolved） |

产出：`SimState.consistency_warnings`（逐步，展示型，**默认不阻断、
不触发修复**）+ 实例级"真实性体检"报告（扩展 `quality_signals.py`，
并在界面标注"结构性代理指标，不是语义真实性评分"）。C8 的校准率也可
写入 `knowledge_base`，让 `likelihood` 首次有对账依据。

### WP5 — 回测与校准（有答案才能说"更真实"）

- `world_simulator/backtest.py` + `entrypoints/backtest.py`（新）：
  读 YAML 案例（起点设定、已知里程碑及日期、前置关系），对引擎**隐藏
  答案**地推进 N 步，提取涌现的里程碑（`capabilities_gained` / 技术
  阶段迁移 / 树中 resolved 分支），与真值匹配后计算：里程碑召回/精确、
  **顺序一致性**（Kendall τ）、**时间偏差**、阶段迁移时点偏差。
- 匹配需要语义：用一次 LLM 匹配调用，**结果可由用户覆盖**，并写入
  `reality_checks.jsonl`（复用 `reality_check.record_and_apply`），
  沿用"verdict 由人最终裁定"的既有取舍。
- **A/B**：同一案例，开关 ON/OFF 各重复 N 次，比较指标分布——这是唯一
  能证明后续 WP 有效的手段。
- **已知污染问题（必须如实标注）**：LLM 读过真实历史，可能背诵而非
  模拟。缓解：名称别名化、日期整体平移、只对**相对顺序与间隔**打分；
  无法根除，报告里显式注明。
- 样例案例我可以起草 1–2 个（如互联网早期里程碑），**但日期需要你
  核对**，不作为事实来源。

### WP1 — 技术发展模型（`settings.tech_model_enabled`，默认 False）

**数据**（`dynamic_snapshot.tech_state`，按分支）：每个技术节点
`{id, name, kind, stage, progress(0..1, 阶段内), requires:[{tech_id,
min_stage, hard|soft}], bottleneck, adoption(0..1)?, cost_index?,
substitutes:[...], market?, stalled_steps, last_transition_step,
perceived_stage?}`。阶段序数沿用现有 6 档，不新造枚举。

**时间基础设施（关键，见 Q2）**：新增可选数值字段
`SimState.elapsed_days`（LLM 给出本步跨越的大致天数），引擎的一切时间
相关机制（停留时长、延迟、基准率）都用它；缺失时退化为"按步计数"，
并在界面标注"精度降级"。这同时修正 §2.3 的 `delay_steps` 语义不一致。

**引擎规则（通用、参数全部可编辑，先验来自 `reference_priors`）**：

- R1 最多升一档/步；R2 硬前置：进入某档需前置技术达到 `min_stage`；
  软前置降低推进速度而非阻断。
- R3 进度推进：`progress += elapsed/典型停留时长 × 投入系数 ×
  瓶颈系数`；投入系数取自 LLM 在 `tech_updates` 里申报的
  `investment: low|normal|high`（附原因；引擎设上限，防止申报即通过）。
- R4 阶段迁移仅当 `progress≥1` 且前置满足；LLM 提前迁移 → 记入
  `tech_violations` 并把该迁移**降级为"接近完成"**（沿用
  `resource_violations` 的透明夹值模式）。
- R5 停留超过 `max_dwell` → 提示"停滞"，要求 LLM 以**事件**解释
  （资金寒冬/监管/技术瓶颈），倒退必须带 `regression_reason`。
- R6 扩散/成本只做**约束不做模拟**：同一 `market` 内采用率之和 ≤1；
  `cost_index` 非增（除非声明冲击）。不内置 S 曲线或学习率公式。
- R7（可选）炒作 vs 现实：`perceived_stage` 与真实 `stage` 的差距，
  复用 State/Belief 分离机制。

**接入**：`advance()` 调用 LLM 前生成 `tech_state_hint`（含各技术
"就绪/未就绪/停滞"判定）注入 prompt；解析 `tech_updates` 后走 R1–R7
裁决；三个模板 SKILL.md + `advance_step.yaml`/`world_evolve.yaml` 同步
加字段说明；界面新增技术树面板（DAG 着色、进度条、阻塞原因）。

### WP2 — 外生事件采样（`settings.event_sampling_enabled`，默认 False）

- `event_priors`：`{id, description, rate_per_year, affects:[line/tech/
  field], severity, cooldown_steps, condition?, source, verified}`。
- 抽样用**可复现种子** `(sim_id, branch, step, salt)`：同一步重跑结果
  一致；`run_repeated_experiment` 给每条分支不同 `salt`，第一次让
  "重复模拟"产生**真实的分布**而不只是 LLM 采样噪声。
- 抽中的事件作为"**本步已发生的事实**"注入 prompt 并记入
  `SimState.sampled_events`；同时允许并鼓励"本步无重大事件"的静默步，
  对冲戏剧化偏差。
- 先验初值由 `generate_scenario` 提议，标 `source=llm_estimate,
  verified=false`，需用户在界面确认；WP4/WP5 用来检验先验是否离谱。

### WP3 — 因果结构可执行化（`settings.causal_engine_enabled`，默认 False）

- **3a 边升级**：`declared_causal_graph` 条目在原有
  `{from_line_id,to_line_id,note}` 上增加可选
  `mechanism/sign/strength/delay(elapsed 单位)/condition/confidence`，
  旧格式照常可用。
- **3b 待兑现因果队列**：触发源（技术阶段迁移、分支状态变化、账本中
  显著变化、抽样事件）沿声明的边入队，到期后作为"到期因果压力"注入
  prompt；LLM 在 `effect_dispositions` 里回报 `realized/dampened/
  postponed/countered + 原因`。引擎累计每条边的**兑现率**，回写
  `knowledge_base` 的 `validated_count/contradicted_count`——图开始
  "学习哪些边在这个世界里真的成立"。复用 `relationship.queue_pending_
  effect` 的模式（按分支存储，依赖 WP0），不重复造轮子。
- **3c 树接地**：(i) 前置强制（未满足则 `active` 降为 `emerging` 并
  记录）；(ii) 结构化触发条件（小型谓词：变量比较 / 技术阶段 /
  分支状态），每步自动求值，扩展 `suggest_status_transitions` 给出
  `trigger_met` **建议**（自动迁移放在子开关 `tree_auto_transition`，
  默认关）；(iii) `exclusive_group` 互斥组；(iv) `likelihood` 校准
  账本（接 WP4-C8）。
- **3d 树影响世界**：分支可声明 `effects_if_active`，激活时转成 3b 的
  待兑现条目——树第一次真正推动世界，而不只是展示。
- **3e 界面**：因果图按边状态着色（假设/已观察/已证伪）、显示兑现率、
  到期因果时间线。

## 5. 分期与依赖

| 阶段 | 内容 | 依赖 | 理由 |
|---|---|---|---|
| P0 | WP0 分支隔离 | — | 缺陷；后续状态都要按分支存 |
| P1 | WP4 守卫+体检 | WP0 | 纯 Python、零风险、给现有实例出**基线数字** |
| P2 | WP5 回测框架 | WP4 | 有基线才能证明后续改动有效 |
| P3 | WP1 技术模型 | WP0 | 你最关心的部分之一 |
| P4 | WP2 事件采样 | WP1（可选） | 需要 `elapsed_days` |
| P5 | WP3 因果可执行 | WP0/WP1/WP2 | 依赖前面产生的触发源 |

如果你更想先看到"技术发展"的效果，可以 P0 之后直接做 P3、把 P1/P2
后移；代价是暂时没有量化基线。**每个阶段独立可交付、独立开关、
独立打 diff zip。**

## 6. 改动清单与开关

| 开关（`manifest.settings`） | 默认 | 关闭时 |
|---|---|---|
| `tech_model_enabled` | False | 与今天完全一致 |
| `event_sampling_enabled` | False | 同上 |
| `causal_engine_enabled` | False | 同上 |
| `tree_auto_transition` | False | 只给建议 |
| `consistency_repair_enabled` | False | 只记录警告，不额外调 LLM |

新增文件：`consistency_guard.py`、`backtest.py`、`tech_model.py`、
`event_sampler.py`、`causal_engine.py`、`entrypoints/backtest.py`、
对应 `tests/`。修改：`state_model.py`、`engine/advance.py`、
`engine/causal_lines.py`、`causal_tree.py`、`quality_signals.py`、
`spec_generator.py`、三个 SKILL.md、`advance_step.yaml`/
`world_evolve.yaml`/`generate_scenario.yaml`、`app.py`（6500+ 行，界面
改动只做静态检查，与既往一致，会如实写进实施记录）、`html_export.py`、
`PROJECT.md` 与 `docs/`。

## 7. 风险与已知限制

1. **LLM 仍可能无视提示**：引擎只能"约束+记录+可选修复"，不能保证
   叙事与状态一致；`C*` 检查会把不一致量化出来，而不是消除它。
2. **夹值可能造成叙事—状态错位**（叙事说已上市、引擎说仍在
   developer）。因此规则 R4 选择"降级为接近完成 + 记录"，并且把状态
   先写进 prompt 以减少发生；这不是零风险。
3. **先验是"垃圾进垃圾出"的入口**：初值可能只是 LLM 猜测，靠
   `verified` 标记与回测暴露，不能假装可靠。
4. **回测受训练数据污染**，只能缓解不能根除（§WP5）。
5. **Token/延迟成本**：开启后每步 prompt 变长；额外 LLM 调用只发生在
   显式打开的修复/匹配功能上。
6. **复杂度**：`PROJECT.md` 已 4000+ 行、已有 21 轮。阶段十七~十九曾
   在触发条件未满足时提前实施并被标为高风险。本方案把"先量后改"放在
   最前，就是为了避免重蹈覆辙——如果 P1/P2 的基线显示问题不在这里，
   后续 WP 应当调整或放弃。
7. `elapsed_days` 是 LLM 的估计值，本身有误差；只用于量级判断。

## 8. 需要你确认的问题（2026-09-30 回复记录）

1. **顺序**：✅ 按 §5 推荐——先 WP0 → WP4 → WP5，再 WP1/2/3。
2. **`elapsed_days`**：✅ 接受新增该可选数值时间字段作为引擎级基础设施
   （在 WP1 阶段引入；届时同时修正 `delay_steps` 在步长可变时含义不一致）。
3. **违规策略**（技术违规默认"降级+记录"，修复调用独立 opt-in）：⏳ 未回复，
   在 WP1 开始前再确认；WP4 只记录警告、不阻断、不修复，不依赖此项。
4. **回测案例**（真实技术史/数据）：⏳ 未回复，WP5 开始前再确认；没有则
   起草 1–2 个示例，日期由你核对。
5. **旧实例**：✅ WP0 只对新写入的步隔离，不迁移旧数据。

**执行方式（用户要求）**：一个阶段一个阶段执行，不一次改所有需求；每完成
一个阶段更新相关文档，并把所有修改/新增文件按目录结构打包供覆盖。

## 9. 实施记录

### P0 / WP0 — 分支隔离（已完成，2026-09-30）

- **缺陷复现**：先写 `tests/test_branch_dynamic_state.py`，确认"新分支上
  印证一个分支 → 切回主线，主线该分支仍是 `resolved`"为红，§2.4 属实。
- **与 §4 WP0 原设计的一处细化**：原设计是"读取改走快照"。实际有十几处
  既有读取方直接读 `manifest.settings`（界面/导出/prompt 构造等），逐处
  改会大面积动界面代码。实现改为：**`manifest.settings` 保留为"当前活跃
  分支的工作副本"**，读取方一处不改；快照随步落盘，分支操作负责对齐
  （离开分支时把工作副本提交到旧分支头部，进入分支时用其快照刷新）。
  语义与原设计等价（每条分支各持一份树、分叉即回滚到那一刻）。
- **改动**：`state_model.py`（`SimState.dynamic_snapshot`，`None` 时不输出，
  旧格式逐字节不变）、新增 `dynamic_state.py`、`engine/advance.py` 与
  `engine/advance_independent.py`（落盘前写快照；`relationship_pending_
  effects` 的入队前移到 `append_state()` 之前）、`branch_manager.py`
  （`fork_branch`/`switch_branch`/`merge_branch`）。纳入范围仍是
  `causal_lines` 与 `relationship_pending_effects`，后续 WP 的
  `tech_state`/`causal_pending` 只需在 `DYNAMIC_KEYS` 追加。
- **验收**：`pytest tests/` 739 passed（改动前基线 729，新增 10）。
- **已知边界**：旧实例只从新写入的步开始隔离；旧实例首次离开分支会往
  该分支头部提交一份快照（新写入，非回填）；旧实例从更早的步分叉时回滚
  到的是"源分支离开时的工作副本"；从非活跃且无快照的分支分叉，新分支
  沿用工作副本；快照是完整拷贝，`causal_lines` 很大时历史文件变大；
  `confirmed_*`/`suggested_causal_lines` 等其它 settings 列表未纳入；
  未在真实 LLM 与真实看板下验证，`app.py` 无改动。
- **下一阶段**：P1 / WP4（一致性守卫 + 真实性体检）。

### P1 / WP4 — 一致性守卫 + 真实性体检（已完成，2026-09-30）

- **新增** `world_simulator/consistency_guard.py`（纯 Python、不调 LLM）：
  C1–C8 八项检查，判定口径与阈值写在模块 docstring 里。逐步守卫结果写入
  新字段 `SimState.consistency_warnings`（空时不输出，旧格式不变）；
  `advance()` 在树更新前留深拷贝、落盘前调用 `safe_check_step()`（异常
  返回空列表）。实例级体检 `analyze_history()` 从历史重算，C2–C4 走 WP0
  的快照链；`quality_signals.summarize_realism_health()` 委托它。
  `app.py`：每步"🧭 一致性提示" + 折叠面板"🩺 真实性体检"。
- **与 §4 WP4 的偏离/细化**：① 守卫开关 `consistency_guard_enabled`
  **默认开**（计划 §0 写"新机制默认关闭"，但 WP4 只读只记录，关闭则拿不到
  C2–C4 逐步数据；可显式关）；② C5/C7 只统计不告警（按计划）；③ C8 校准
  率**没有**写入 `knowledge_base`（计划标为可选，有跨实例副作用，待你确认）；
  ④ 没给 `summarize_quality_signals()` 加 key，另设 `summarize_realism_health()`。
- **验收**：`pytest tests/` 762 passed（P0 后基线 739，新增 23）；做过变异
  验证（C2 判定置空 → 3 个用例转红）。
- **已知边界**：结构非语义、会有误报（C6 在步长可变时最明显，`elapsed_days`
  待 WP1）；C3 只能核验按 id 解析得到的前置，默认树无前置时恒为 0；C1 依赖
  模型填 `maturity_stage`，`regression_reason` 暂无 prompt 引导；旧实例的
  C2–C4 只覆盖 WP0 之后的步；`advance_lines()` 无逐步守卫；界面未在真实
  看板目视验证；**告警在真实 LLM 输出上的误报率未知**。
- **下一阶段**：P2 / WP5（回测框架）。开始前需要你给回测案例，或同意我
  起草 1–2 个示例（日期由你核对）。
