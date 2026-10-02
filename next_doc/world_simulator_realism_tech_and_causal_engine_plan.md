# world_simulator 改进计划（第二十二轮）：让技术/事物发展更真实、因果树可执行可校验

> **状态（2026-10-02 更新）：P0–P5e 已全部实施完毕（未在真实 LLM 下验证）；后续补完阶段 P6–P10 已立项并排期，见文末 §10（尚未实施）。**
> 已完成：**P0（WP0 分支隔离）**、**P1（WP4 一致性守卫+真实性体检）**、**P2（WP5 回测框架）**、**P3（WP1 技术模型 + `elapsed_days`）**、**P4（WP2 外生事件采样）**、**P5a（WP3 的 3a 边升级 + 3b 待兑现因果队列）**、**P5b（§8 三个确认项落地：关系延迟改 elapsed、兑现统计回写知识库、技术违规修复调用）**、**P5c（WP3 的 3c 树接地）**、**P5d（WP3 的 3d 树影响世界）**、**P5e（WP3 的 3e 界面收尾：因果图着色/兑现率/到期时间线）**，详见文末 §9。**WP0–WP5 的原定阶段全部实施完毕；WP5/WP2/WP3 的遗留补完与收尾列为 P6–P10（§10）。**
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
  到期因果时间线。（P5e 已实现；实际状态为五档，见 §9 P5e。）

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
   （P3 已引入）。**`delay_steps` 语义修正**：✅ 已确认，`relationship.py` 也改用
   elapsed 单位（P5b 已实现：新增 `delay_days`，`delay_steps` 保留作旧数据/降级兜底）。
   **兑现统计回写 `knowledge_base`**：✅ 已确认回写（P5b 已实现，开关 `causal_kb_writeback` 默认开）。
3. **违规策略**（技术违规默认"降级+记录"，修复调用独立 opt-in）：✅ 已确认做修复调用
   （P5b 已实现，开关 `tech_repair_enabled` 默认关；只覆盖 T4–T7，不改写叙事）。
4. **回测案例**（真实技术史/数据）：✅ 2026-10-02 用户要求由 Claude 直接核对。已对照公开资料核对两个示例案例的
   **年份**（`verified: true`，核对记录在 yaml 头部；非专家审定，`stage`/`requires` 是建模判断未核对），
   并修正了模拟跨度不覆盖后几条真值的缺陷（见 §9 "回测案例核对"）。
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

### P2 / WP5 — 回测与校准（已完成，2026-09-30）

- **新增** `world_simulator/backtest.py`、`entrypoints/backtest.py`
  （`check`/`run`/`ab`/`rescore`）、`workflows/backtest_match.yaml`、
  `backtest_cases/`（2 个示例）、`docs/backtest_guide.md`；`requirements.txt`
  加 `pyyaml`。隐藏答案 = 名称别名化 + 年份整体平移；只对顺序与间隔打分
  （召回/精确/Kendall τ-b/区间误差/前置违反/阶段一致率）；对账写入
  `reality_checks.jsonl`；匹配结果可由用户覆盖（`rescore`）；A/B 只给分布。
- **与 §4 WP5 的偏离/细化**：① 默认匹配器是规则匹配（零成本可复现），LLM
  匹配需 `--matcher llm`，两者分数不要混比；② "阶段迁移时点偏差"需要 WP1 的
  `tech_state`，本阶段只做阶段**一致率**；③ 没有登记进 `project.yaml`（长时间
  按步数计费的 LLM 调用，手动运行）；④ 候选时间用案例声明的 `years_per_step`
  近似，等 `elapsed_days` 落地后改用引擎记录的跨度。
- **验收**：`pytest tests/` 805 passed（P1 后基线 762，新增 43）；变异验证
  （别名顺序/前置判定/范围边界）各有用例转红。
- **已知边界**：**没有在真实 LLM 下运行过**（涌现里程碑数量、匹配质量、成本
  未知）；训练数据污染只能缓解，绝对分数不可信；示例案例由 Claude 起草、
  **未经核对**；精确率是下界；A/B 不做显著性检验，n<5 写"不足以下结论"；
  在 P3 引入 `tech_model_enabled` 之前，A/B 能比较的只有现有 settings 与匹配方式。
- **下一阶段**：P3 / WP1（技术发展模型 + `elapsed_days`）。开始前还需要你回复
  §8 第 3 问（技术违规默认"降级+记录"、修复调用独立 opt-in 是否接受）。

### P3 / WP1 — 技术发展模型 + `elapsed_days`（已完成，2026-10-01）

- **新增** `world_simulator/tech_model.py`、`tests/test_tech_model.py`（54 个）、
  `docs/tech_model_guide.md`；**修改** `state_model.py`（`elapsed_days`/`tech_updates`/
  `tech_violations`）、`dynamic_state.py`（`tech_state` 入分支快照）、`engine/advance.py`、
  `consistency_guard.py`（C6 按 `elapsed_days` 归一，兑现 P1 遗留）、`app.py`、
  `workflows/advance_step.yaml`/`world_evolve.yaml`、三个模板 `SKILL.md`。
  规则 R1–R7 与违规码 T0–T11 见 `docs/tech_model_guide.md`。
- **与 §4 WP1 的偏离/细化**：① 进度完全由引擎按时间推算，LLM 自报被忽略；② 提前迁移
  被驳回后**不送进度**（计划写"降级为接近完成"，实际更严格）；③ 先验放
  `settings.tech_priors`/`tech_params`，因为代码里并不存在 `reference_priors`；所有默认数值
  是**占位值**；④ 登记新技术夹到最低档（T5），`preexisting` 例外并标"未经验证"；
  ⑤ `requires`/`typical_dwell_days` 仅登记时可给（T8）；⑥ 未登记前置**不阻断**（T9，避免
  笔误死锁，代价是形同虚设）；⑦ **种子锚定**（计划未写）：首次推进前把设置里的种子提交到
  分支头部，否则从推进前的步分叉拿到的是"已被推进"的技术状态（有回归测试）；
  ⑧ **`delay_steps` 修正推迟到 P5**（见 §8 第 2 问）；⑨ 修复调用未实现（§8 第 3 问未回复）。
- **验收**：`pytest tests/` **859 passed**（P2 后基线 805，新增 54）；变异验证 13 个
  （T1/T2/T3/T5/T6/T7、软前置、投入粘性、倒退进度、C6 归一、种子锚定、开关关闭仍执行、
  `tech_state` 不进快照）全部转红，1 个无操作对照存活；`streamlit.testing.AppTest` 冒烟：
  开启技术模型的实例详情页无异常、设置面板与技术树面板均渲染。拆分模式
  （`split_decision_calls`）有端到端测试。
- **已知边界**：**没有在真实 LLM 下运行过**（协议遵守度、`tech_updates` 质量、额外 token
  成本未知）；默认数值是占位值；LLM 典型时长"未验证"；`advance_lines()` 独立推进路径
  不跑技术模型；开启后每步写完整快照、历史文件变大；叙事与引擎状态的错位无法消除只能量化；
  设置页保存"技术节点"会覆盖当前技术状态；回测框架尚未改用 `elapsed_days`/`tech_state`
  （"阶段迁移时点偏差"未做）。
- **下一阶段**：P4 / WP2（外生事件采样，依赖 `elapsed_days`，已就绪）。开始前建议你
  确认：① `delay_steps` 推迟到 P5 是否接受；② 是否需要修复调用（§8 第 3 问）。

### P4 / WP2 — 外生事件采样（已完成，2026-10-01）

- **新增** `world_simulator/event_sampler.py`、`tests/test_event_sampler.py`（39 个）、
  `docs/event_sampling_guide.md`；**修改** `state_model.py`（`sampled_events`）、
  `engine/advance.py`、`app.py`、`workflows/advance_step.yaml`/`world_evolve.yaml`、
  三个模板 `SKILL.md`。规则（泊松概率/可复现种子/冷却/条件/上限）见
  `docs/event_sampling_guide.md`。
- **与 §4 WP2 的偏离/细化**：① **先验由用户手写，未做 `generate_scenario` 提议**
  （原计划"LLM 提议、标 `llm_estimate`、用户确认"未实现；理由与后续见指南）；
  ② "每分支不同 salt"改为**分支名进种子**，另加公共随机数开关，用于比较不同策略时
  让各分支抽到相同事件；③ 新增 `max_events_per_step` 上限；④ 最小条件谓词（变量
  比较+技术阶段）先在此实现，P5 的 3c 树接地可复用；⑤ 冷却从分支历史推导，**没有**
  新增分支作用域动态状态（比"再加一个 DYNAMIC_KEYS"更简单，且天然按分支正确）。
- **实施中发现并修掉的缺口**：`elapsed_days` 原先只在技术模型开启时索要/落盘，只开事件
  采样时基准天数永远是占位值——由一个失败测试暴露，现在任一功能开启即索要并落盘。
- **验收**：`pytest tests/` **898 passed**（P3 后基线 859，新增 39）；变异验证 15 个全部转红；
  `streamlit.testing.AppTest` 冒烟通过（面板/警告/每步事件渲染）。
- **已知边界**：**没有在真实 LLM 下运行过**；引擎无法验证叙事是否包含事件、无法阻止
  LLM 另外编造冲击；概率用**估计**跨度；没有内置先验，数值完全取决于用户；`affects`
  只是提示；事件间无相关性；`advance_lines()` 不做采样；只对新写入的步有效。
- **下一阶段**：P5 / WP3（因果结构可执行化），依赖 WP0/WP1/WP2 已就绪。P5 工作量最大
  （边升级、待兑现因果队列、树接地、树影响世界、界面），建议**再拆成子阶段**逐个交付
  （先 3a+3b 边与待兑现队列，再 3c 树接地，再 3d/3e）；同时需要你确认 §8 第 2 问
  （`delay_steps` 语义修正并入 P5）与第 3 问（是否需要修复调用）。

### P5a / WP3 的 3a+3b — 因果边升级 + 待兑现因果队列（已完成，2026-10-01）

- **做了什么**：新增 `world_simulator/causal_engine.py`。`declared_causal_graph` 的边新增可选字段
  （`id/mechanism/sign/strength/delay_days/condition/confidence/enabled`，旧格式兼容）；源头有进展
  （线推进 / 树分支印证或激活 / 抽中的外生事件 / 技术阶段迁移）时入队，延迟期到了向提示词注入"到期
  因果压力"，LLM 用 `effect_dispositions` 回报 `realized/dampened/postponed/countered`；引擎校验
  （E1–E4、E6、E0）、自动结案、从分支历史推导每条边的兑现统计。`causal_pending` 加入分支作用域
  动态状态（分叉即回滚）。界面：设置开关 + 详情页面板 + 每步提示。默认关闭，关闭时与之前逐字节等价。
- **验证**：`pytest tests/` 938 passed（基线 898，新增 40）；变异验证 21 个全部转红；
  `streamlit.testing.AppTest` 界面冒烟通过。文档见 `docs/causal_engine_guide.md`。
- **与 §4 WP3 的偏离/细化**：① `delay_steps` 语义修正只覆盖本模块新增的边（用 `delay_days`），
  `relationship.py` 的 `delay_steps` 未动；② 没做 `knowledge_base` 计数回写（跨实例副作用）；
  ③ 触发源没有"账本显著变化"；④ 新增 E1–E6 与三个队列上限。
- **已知边界**：没有在真实 LLM 下验证；引擎无法验证 `realized` 是否真的写进状态（统计是自报）；
  只看"源头有进展"不看方向/幅度；`advance_lines()` 不入队。
- **下一阶段**：P5b / 3c（树接地：前置强制、结构化触发条件与 `trigger_met` 建议——复用 P4 的
  条件谓词、`exclusive_group` 互斥组、`likelihood` 校准账本；自动迁移放子开关
  `tree_auto_transition` 默认关）。上述三个待确认项已在下面的 P5b 落地。

### P5b — §8 三个确认项的落地（已完成，2026-10-01）

- **做了什么**：① `relationship.py` 延迟改用 elapsed 单位（新增 `delay_days`，`delay_steps` 保留作兜底，
  旧实例逐字节不变）；② 因果边兑现统计回写 `knowledge_base`（`realized`→validated、`countered`→contradicted，
  幂等，开关 `causal_kb_writeback` 默认开）；③ 技术违规一次性修复调用（`tech_repair_enabled` 默认关，
  只覆盖 T4–T7，回滚→重新提议→受约束→重新裁决→仅在违规减少时采纳）。新增 `engine/tech_repair.py`、
  `workflows/tech_repair.yaml`、`tests/test_p5b_followups.py`；更新两份指南、`testing_guide.md`、`PROJECT.md`。
- **与计划的偏离/细化**：① 修复调用不覆盖 T1–T3（重新提议改变不了裁决）；② 修复**只改技术提议，不改写叙事**
  （§8 原文设想"让 LLM 重写被驳回的叙事"，风险更大，未采用）；③ 回写开关默认开（用户明确要求）；
  ④ `causal_graph.py` 里 `causal_links.delay_steps` 的布尔聚合仍按步，未改。
- **验收**：`pytest tests/` **989 passed**（P5a 后基线 938，新增 51）；变异验证 21 个全部转红，无存活。
- **已知边界**：没有在真实 LLM 下运行过；兑现统计是 LLM 自报，进入跨模拟知识库后无法区分真假兑现；
  与 `record_causal_links()` 的计数会叠加到同一条目；知识库回写无撤销机制；`app.py` 设置页保存流程未实跑；
  修复不消除叙事与状态的错位，LLM 可能为过检查而补写理由。
- **下一阶段**：P5c / 3c（树接地，已完成，见下）。

### P5c / WP3 的 3c — 因果树接地（已完成，2026-10-01）

- **做了什么**：新增 `world_simulator/tree_grounding.py`。(i) 前置强制 G1：本步新变 active 但可解析的前置未 resolved → 降回 emerging；
  (ii) 分支可选 `trigger_condition`（复用 `event_sampler.evaluate_condition`），满足时默认只作 `trigger_met` 建议，子开关
  `tree_auto_transition` 才自动置 active（G4）；(iii) `exclusive_group` 同线互斥（G2 降级 / G3 只记录），组内 resolved 后落败者建议或（自动）
  置 invalidated（G5）；(iv) likelihood 校准账本（终结时刻档位命中率 + 倒挂，进体检，可选 `likelihood_nominal` 对账）。
- **接线**：`causal_tree`（新字段仅填写时输出）、`SimState.tree_grounding`、`engine/advance.py`（技术裁决后/因果入队前/快照前；结果写回
  settings 并修正 `tree_updates` 审计）、`consistency_guard`/`quality_signals`、两个 workflow、三个 SKILL.md、`app.py`（设置/面板/每步记录/账本）。
- **开关**：`tree_grounding_enabled`（总，默认关）、`tree_auto_transition`（子，默认关）；关闭时逐字节等价。
- **验收**：`pytest tests/` **1034 passed**（基线 989，新增 `tests/test_tree_grounding.py` 45 个）；变异验证 17 个，首轮存活 1 个（G5 误伤已终结同组分支），补用例后全部转红。
- **与计划的偏离**：总开关独立于因果引擎；未改 `suggest_status_transitions()`；校准账本不写 `knowledge_base`（仍待确认）。
- **已知边界**：未在真实 LLM 下验证；引擎无法判断条件写得对不对；LLM 直接标 resolved 不拦；降级造成叙事/状态错位；只处理顶层分支，
  `advance_lines()` 不跑；设置页保存流程未实跑。详见 `docs/tree_grounding_guide.md`。
- **下一阶段**：P5d / 3d（树影响世界）与 3e（界面收尾）——需先给方案、你确认后再改代码。

### P5d / WP3 的 3d — 树影响世界（已完成，2026-10-02）

- **做了什么**：新增 `world_simulator/tree_effects.py`。分支可选声明 `effects_if_active`（形如因果边：`to_line_id`/`mechanism`/`sign`/
  `strength`/`delay_days`/`delay_steps`/`condition`）；分支**本步新变为 active**（P5c 接地后仍是）时，每条声明入 P5a 的待兑现队列
  （`edge_id` 形如 `tree:线/分支->目标`），之后与声明的因果边同一条路径：到期提示 → `effect_dispositions` → 校验/结案/统计。
  树第一次真正推动世界，但**仍不改任何数值**。子开关 `tree_effects_enabled`（默认关，必须同时开 `causal_engine_enabled`）。
  违规码 E5（声明不可用/取值不认识）、E6（队列满，与边共用上限）、E7（目标未登记，仍入队）、E0（出错兜底）。
- **接线**：`causal_tree`（新字段仅填写时输出）、`engine/advance.py`（边入队之后、快照之前；推进前状态索引在接地或树影响任一开启时计算）、
  `causal_engine`（`needs_elapsed` 计入树声明的 `delay_days`、提示词显示分支、新原因标签）、两个 workflow、三个 SKILL.md、`app.py`
  （设置复选框 + 因果引擎面板的"树分支声明的影响"）。文档见 `docs/tree_effects_guide.md`。
- **验收**：`pytest tests/` **1056 passed**（基线 1034，新增 `tests/test_tree_effects.py` 22 个）；变异验证 15 个，首轮存活 1 个
  （接地忽略自身开关），补用例后全部转红；`streamlit.testing.AppTest` 界面冒烟通过。
- **与计划的偏离/细化**：① 树声明的统计不回写 `knowledge_base`（树声明是实例内假设，不是跨世界证据）；② 声明目标可以是技术节点；
  未登记目标只记 E7 不阻断（与技术模型 T9 同一取舍）；③ 3e（因果图按边状态着色、兑现率图示、到期因果时间线）留给 P5e。
- **已知边界**：没有在真实 LLM 下验证；引擎无法验证声明合理性与兑现真假；只看顶层分支；`advance_lines()` 不入队；不追溯开启前已 active 的分支；
  从第 0 步（无快照）分叉沿用工作副本（P0 既有边界，本阶段未做种子锚定）；设置页保存流程未实跑。
- **下一阶段**：P5e / 3e（界面收尾，已完成，见下）。

### P5e / WP3 的 3e — 因果图着色 + 兑现率 + 到期时间线（已完成，2026-10-02）

- **做了什么**：新增 `world_simulator/causal_view.py`（纯函数，不调 LLM、不落盘、**无新开关**）。边状态由 `edge_stats()` 推出：
  假设（没有有结论的处置）/ 已观察（`realized≥1` 且兑现率≥50%）/ 已证伪（`realized==0` 且 `countered≥2`）/ 未定 / 已停用；
  `edges_to_dot()` 按状态着色（灰虚线/绿粗线/红/橙/浅灰点线），边标签带 `兑现率（兑现/有结论）`；`build_due_timeline()` 给出未结案项
  （已到期优先，天精度先于步精度）与最近 20 条处置记录。因果引擎面板新增"查看方式"单选（📋 兑现统计〔原有，默认〕/🕸️ 着色关系图/⏰ 到期时间线）。
  树边（P5d）与"声明已被删掉但历史里有兑现记录"的树边也画出来。阈值可用 `settings.causal_view_params` 覆盖。文档见 `docs/causal_view_guide.md`。
- **验收**：`pytest tests/` **1078 passed**（基线 1056，新增 `tests/test_causal_view.py` 22 个）；变异验证 24 个，首轮存活 2 个
  （`observed_min_realized` 被兑现率阈值遮住、天/步精度排序），补用例后全部转红；`streamlit.testing.AppTest` 对面板三个视图冒烟通过。
- **与 §4 WP3 3e 的偏离/细化**：① 状态由计划的三态扩为五档，新增"未定"（只有一条自报抵消就判证伪证据太薄；仅部分兑现是"效果发生但更弱"，
  不是证伪）和"已停用"；② 时间线放在因果引擎面板里，没有改每步时间线；③ 静态 HTML 导出未接入；④ 状态不落盘，每次现算。
- **已知边界**：状态/兑现率是 **LLM 自报**统计的函数，"已观察/已证伪"≠ 世界里被验证/被证伪，小样本波动大；没有浏览器级验证（真实配色/布局未见），
  边多时图会挤；`elapsed_days` 是估计值；没有在真实 LLM 下运行过；设置页保存流程未实跑。
- **遗留（不属于本计划各阶段、仍待你决定）**：① ~~§8 第 4 项回测案例的真实日期仍待你核对~~——已于 2026-10-02 由 Claude 核对年份并改 `verified: true`（见上"回测案例核对"；非专家审定）；
  ② 全部新机制均未在真实 LLM 下端到端验证——建议下一步用真实模型跑一遍回测（P2）并看体检（P1）的基线与开启后差异，再决定哪些默认值/阈值需要调整；
  ③ 校准账本、树声明统计均未写入 `knowledge_base`（P5c/P5d 已记录，仍待确认）；④ 静态 HTML 导出可按需接入着色/时间线。

### 回测案例核对（已完成，2026-10-02，随 §10 立项一并完成）

- **做了什么**：用户回复"你直接核对一下"。Claude 对两个示例案例的全部 11 个里程碑年份逐条检索公开资料核对，结果全部与草稿年份一致；
  `internet_early.yaml` / `personal_computer.yaml` 改为 `verified: true`，核对依据与口径写在 yaml 头部注释，`disclaimer` 同步改写。
- **核对中发现的缺陷**：案例的模拟跨度没有覆盖全部真值——互联网案例原 8 步×2 年 = 16 年（到 1985），6 条真值只有 1969/1983 两条在范围内
  （1986 起全在范围外，`recall` 只算范围内，等于只测了 2 条）；PC 案例原 8 步×1.5 年 = 12 年（到 1986），1990 的 Windows 3.0 永远测不到。
  已改为互联网 13 步（26 年，到 1995）、PC 11 步（16.5 年，到 1990.5），意图文本里的"二十年/十五年左右"同步改成"二十六年/十六年左右"。
  代价：回测单次的 LLM 调用数相应增加。`tests/test_backtest.py` 里原"案例必须未核对"的断言改为"已核对且跨度覆盖最后一条真值"。
- **口径提示**：TCP/IP "成为标准"的年份来源说法不一（DoD 宣布 1980/1982/1983，ARPANET 整体切换 1983-01-01），案例取切换年 1983；
  WWW "公开可用"取 1991-08 首个网站公开（1993-04 才免费开放）。换口径会改变打分。
- **边界**：年份由 Claude 通过网页检索核对，不是历史学家审定；只核对了年份，没有核对 `stage`/`requires`；没有在真实 LLM 下跑过回测。
- **验收**：`tests/test_backtest.py` 41 passed；另有 2 个 LLM 匹配器用例在本次沙箱里因缺 `fastapi`/`mini_agent.workflow` 无法运行（环境问题，
  与本改动无关，需在完整环境下复跑）。

## 10. 后续计划（P6–P10，2026-10-02 立项，**尚未实施**）

来源：P5e 之后对照代码核实的遗留缺口，用户逐项确认要做（含第 5 项"跨实例落库"）。执行方式不变：**一个阶段一个阶段做，每阶段更新文档、
按目录结构打包新增/修改文件**。每阶段开始前，若有设计分歧再给方案确认，没有则直接实施。

**顺序与理由**（与用户列出的顺序略有调整，供确认；理由是"先补引擎能力，最后做展示"）：

| 阶段 | 内容 | 依赖 | 理由 |
|---|---|---|---|
| P6 | 回测改用 `elapsed_days` / `tech_state`，补"阶段迁移时点偏差" | P3 | WP5 本身没收完；有了它才能量化 P3–P5 是否让结果更真实 |
| P7 | `generate_scenario` 提议事件先验（LLM 提议 + 用户确认） | P4 | WP2 原计划项，P4 时改成了纯手写 |
| P8 | `advance_lines()` 接入新机制 | P3/P4/P5 | 独立节奏因果线推进目前绕过全部新机制，是行为不一致 |
| P9 | C8 校准率与树声明统计跨实例写入 `knowledge_base` | P1/P5c/P5d | 用户已确认跨实例落库；放在 P8 之后，让写入的数据覆盖两条推进路径 |
| P10 | 静态 HTML 导出接入新机制 | P6–P9 | 纯展示，放最后一次性接齐 |

### P6 — 回测改用引擎记录的时间与技术状态（WP5 补完）

- **现状（已核实）**：`backtest.py` 候选时间 = `start_year + step × years_per_step`（模块 docstring 与结果里都标了"近似"），`elapsed_days`/`tech_state`
  在该文件中没有任何引用；"阶段迁移时点偏差"（原 §4 WP5 的指标）因此没做，只有阶段一致率。
- **做什么**：① 候选里程碑时间改为"起点 + 到该步为止累计的 `elapsed_days`/365.25"（引擎有记录时），缺失时**回退** `years_per_step` 并在报告里标注"时间精度降级"；
  ② 用分支快照里的 `tech_state` 提取"阶段迁移"事件（`last_transition_step` 与各技术的阶段序列），与真值的 `stage` 比较，输出**阶段迁移时点偏差**
  （按年，仅对能匹配上的里程碑算）；③ 跨度（horizon）也改按累计 `elapsed_days` 计算；④ `run_ab` 报告新增这两项分布；⑤ 回测案例的开关
  （`tech_model_enabled` 等）由案例 `settings:` 声明（目前只能靠 CLI 传），保证 ON/OFF 对照可复现。
- **验收**：补用例（有/无 `elapsed_days` 两条路径、降级标注、迁移时点偏差计算、跨度判定）；变异验证；既有回测用例仍绿。
- **风险/边界**：`elapsed_days` 是 LLM 估计值；没开技术模型的运行没有 `tech_state`，迁移偏差为空（不是 0）；仍无真实 LLM 验证。

### P7 — `generate_scenario` 提议事件先验（WP2 补完）

- **现状（已核实）**：`spec_generator.py` 只在判断机制开关时引用 `event_sampler`，没有任何生成先验的逻辑；先验目前全靠用户手写。
- **做什么**：场景生成时（用户开启 `event_sampling_enabled`，或在生成界面勾选"提议外生事件先验"）让 LLM 额外提议 `event_priors`，每条标
  `source=llm_estimate`、`verified=false`；**未确认的先验默认不参与抽样**（用户在设置页逐条确认或编辑后才生效——原设计"LLM 提议、用户确认"）；
  界面显著区分"用户确认/LLM 估计（未验证）"；`generate_scenario.yaml` 同步加字段说明与"不要编造精确数值、给出理由"的约束。
- **与 P4 的关系**：不改抽样规则，只新增"来源/确认"这一层；先验仍经 `event_sampler` 既有校验（取值范围、冷却、条件谓词）。
- **验收**：用桩 LLM 测试（提议→校验→未确认不抽样→确认后抽样）；非法提议被丢弃并记录；旧实例手写先验不受影响。
- **风险**：LLM 提议的概率就是猜测，"确认"不能变成一键全通过——界面需逐条显示 `rate_per_year` 与理由；先验是否离谱仍只能靠 P1/P2 的体检与回测暴露。

### P8 — `advance_lines()` 接入新机制

- **现状（已核实）**：`engine/advance_independent.py::advance_lines()` 中没有技术模型、事件采样、因果队列、树接地、树影响的调用（grep 无命中）；
  各阶段"已知边界"里反复写着"`advance_lines()` 不跑"。
- **做什么**：把 `advance()` 里这几步抽成**两条推进路径共用**的步骤函数（而不是在 `advance_lines()` 里再复制一遍），按同样顺序/开关接入：
  技术裁决 → 事件抽样 → 因果入队/到期注入 → 树接地 → 树影响入队 → 快照/守卫。
- **实施前需给方案确认的设计点**（会影响既有行为）：① 独立节奏线推进时，技术/事件的时间基准用线自己的跨度还是主线的；② 同一步内主线与线都推进时，
  抽样种子的 salt 如何区分以免重复抽到同一事件；③ 快照写入点（线推进是否产生新步）。
- **验收**：新机制全关时 `advance_lines()` 逐字节不变（回归）；开启时与 `advance()` 行为对等的端到端用例；重构后既有用例全绿。

### P9 — 校准率与树声明统计跨实例写入 `knowledge_base`

- **用户决策**：跨实例落库（✅ 已确认）。涵盖：① C8/`build_likelihood_ledger` 的 likelihood 档位命中率；② P5d 树声明（`effects_if_active`）的兑现统计
  （P5d 当时的取舍是"实例内假设、不回写"，现按用户决定改为回写，与 P5b 的边统计回写并列；**边统计回写已在 P5b 实现，本阶段不重做**）。
- **做什么**：复用 P5b 的幂等回写机制（`knowledge_base.py` 的 `EDGE_OUTCOMES` 一类），新增条目类型并带 `source_instance`/`origin`
  （`likelihood_calibration` / `tree_declaration`）；开关默认**开**（沿用 `causal_kb_writeback` 的先例，可显式关）；按（实例，分支，条目）幂等。
- **必须同时做的防护**（P5b 记录的已知边界在跨实例落库下会放大，故纳入本阶段）：① 按 `source_instance` 撤销/清理某实例回写的函数与 CLI；
  ② 条目带 `self_reported=true`，读取侧（提示词注入、界面）标注"LLM 自报统计，非验证事实"；③ 样本数低于阈值时不写入。
- **验收**：幂等、撤销、小样本不写入、读取侧标注；旧知识库文件向后兼容。
- **风险**：跨世界污染——一个设定离谱的实例会把偏差写进共享库、影响其它实例的提示词。撤销与标注是缓解，不是消除。

### P10 — 静态 HTML 导出接入新机制

- **现状（已核实）**：`html_export.py` 中没有对一致性警告、技术状态、抽样事件、因果引擎面板的引用；`_edges_to_dot` 仍是旧的无状态着色。
- **做什么**（均只读，按有无数据决定是否渲染，旧实例导出不变）：① 每步"🧭 一致性提示"与体检摘要；② 技术树（DAG 着色、阶段/进度、T 码违规）；
  ③ 每步抽样事件（含"未确认先验"标记）；④ 因果引擎：复用 `causal_view.py` 的 `edges_to_dot()` 与到期时间线；⑤ 树接地/树影响记录。
  复用纯函数，不在导出里重算业务逻辑；转义沿用 `_esc`。
- **验收**：断言型测试（特殊字符转义、无数据时不输出新区块、旧实例导出逐字节不变）。
- **边界**：没有浏览器级目视验证（与既往界面改动一致）。

### 仍不属于任何阶段的遗留

- 全部新机制均未在真实 LLM 下端到端验证——建议 P6 完成后用真实模型跑一遍回测（已核对的案例）并看体检基线与开启后差异，再决定默认值/阈值是否调整。
