# world_simulator 单主体模式的最小 State/Belief 分离（4.3 细化子方案）

> **状态**：**第一批（`life_sim` 验证批）、第二批（推广批）代码均
> 已于 2026-09-19 完成**（见 `PROJECT.md` 对应条目）。**⚠️ 本文档
> 第 7 节原本要求"第一批先人工跑 3~5 次真实模拟验证 LLM 效果，通过
> 后才推进第二批"——第一批的这项人工验证实际上从未执行，第二批是
> 应用户明确要求"不等验证、直接实现"而跳过这道前置条件推进的，不
> 代表验证已经通过。也就是说：`belief_fields`/`beliefs` 这套机制
> 目前只保证了"代码能跑、格式能落盘、有测试覆盖"，完全没有任何证据
> 表明 LLM 真的能稳定产出有意义的认知偏差——第 9 节"最大风险"里
> 点出的顾虑原样成立，且因为直接推广到了所有模板，风险敞口比原计划
> 的"先在 life_sim 小范围验证"更大。如果后续真实使用发现 LLM 总是
> 让认知值约等于真实值（没有意义的偏差），或者为没有变化的字段瞎
> 编"认知更新"，应该按本文档第 7 节末尾的建议直接放弃这个方向，而
> 不是继续投入调 prompt。**
> **上游文档**：`next_doc/world_simulator_realism_transparency_and_
> retrospective_roadmap_v3_plan.md`（第三轮，阶段三十二）4.3 节——
> 该节按项目一贯的节制原则，建议"先出细化子方案、在一个模板里小
> 范围验证"再排期实施，本文档就是那份子方案。4.1/4.2/4.4/4.6 已在
> 阶段三十二完成，本方案是在它们之上的后续增量，不改动已完成部分
> 的行为。
> **参考依据**：用户上传的参考文档《万能模拟器到底如何构建？——从
> 世界模型、因果系统到可交互的未来世界引擎》第六节"State vs
> Belief"，核心例子是"市场需求真实是 100，Agent 认为是 70~130"。

## 1. 现状与问题

`multi_entity_mode`（阶段若干，已完成）已经实现了"每个主体私有
`entities` vars + 共享 `shared_vars`"的结构，但这解决的是"多个主体
互相看不到对方的私有信息"这个问题，只在多方谈判等明确的多主体
场景启用。

单主体模板（`life_sim`/`group_evolution` 等）完全没有"用户/主角对
某个关键外部变量的认知 vs 这个变量的真实值"这种区分——`SimState.
vars` 里的每一个数字，模拟里的"你"都被默认为**完全知道**，这和
现实明显不符：现实里人对市场行情、他人真实意图、竞争对手实力这类
信息，永远只有一个基于有限信息的估计，而不是精确值。这也是很多
"事后诸葛亮"式复盘（"当时你要是知道真实情况就不会这么选了"）在
现有系统里说不出来的原因——系统里根本没有存过"你当时以为的是
什么"。

## 2. 目标

- 让声明为"认知不确定"的字段，在模拟里同时存在"真实值"（引擎/LLM
  推进用的就是这个，不受认知偏差影响）和"当前认知值"（角色自己
  以为的，可能与真实值有偏差）两条轨道。
- 让用户在时间线/详情页能直接看到"真实：X，你以为：Y"这种对比，
  这是这个功能对用户唯一有直接价值的展示点——如果做出来但用户
  感受不到这个对比，这个功能就没有意义。
- **不**做成 `multi_entity_mode` 那种每个主体各自独立认知的完整版
  （那属于已有机制的适用范围，不重复造轮子），也**不**做信息更新
  的时间延迟机制（"这个信息要 3 步之后才会被知道"）——只做"认知
  可能与真实不同"这一层。

## 3. 数据结构设计

### 3.1 `SimManifest.settings.belief_fields`

```json
"belief_fields": ["market_demand", "competitor_strength"]
```

字符串数组，声明 `vars`（或 `vars` 的嵌套路径，格式同
`resource_fields` 已有的点号路径约定，如 `market.demand`）里哪些
顶层/嵌套字段是"认知可能失真"的。**默认空数组**，表示不启用，
所有已有模拟的行为完全不变，向后兼容。

由创建向导阶段可选填写（同 `resource_fields`/`causal_lines` 的
既有 UI 模式：`ScenarioDraft` 给建议值，用户可编辑），也可以在
"⚙️ 模拟设置"里事后补充声明——但**事后声明只对声明之后新产生的
step 生效**，不回填历史（同 `causal_lines`/`resource_relations`
事后编辑的既有语义，不引入特例）。

### 3.2 `SimState.beliefs`

```python
beliefs: Dict[str, Any] = field(default_factory=dict)
"""当前 Agent/角色自己认为的估计值（阶段待定，4.3 节，State/Belief
分离）：key 是 `settings.belief_fields` 里声明的字段路径，value 是
角色当前的估计——可以是单一数值（"角色觉得是 85"），也可以是
`{"low": 70, "high": 130, "point": 100}` 这种区间估计（更贴近参考
文档"70~130"的例子，格式不强制，取决于 `advance_step` 这一步给出
的是点估计还是区间）。

只对 `settings.belief_fields` 声明过的字段有意义；`vars` 里同名
字段永远是"真实值"，不受这里的认知偏差影响——引擎/后续推进用的
是 `vars`，`beliefs` 纯粹是叙事和展示层面的"认知快照"，不参与任何
计算。默认空字典，表示当前这一步没有更新认知（沿用上一步的认知，
展示层按"未变化"处理，不强行补一个和上一步相同的值）。
"""
```

**关键设计取舍**：`beliefs` 不是每一步都必须重新给出——只有当
`advance_step` 认为"这一步角色对某个 belief_fields 字段的认知发生
了变化"（比如"意识到市场并没有那么火爆"）才输出对应 key；没有
认知更新的字段留空，`app.py` 展示时沿用"最近一次有记录的认知值"
（同 `key_drivers`/`uncertain_fields` 这类"稀疏标注"字段的一贯
处理方式，避免每一步都强迫 LLM 给一个可能没有变化、纯粹为了填字段
而编造的值）。

### 3.3 `ScenarioDraft.belief_fields` / 初始 `beliefs`

`generate_scenario` 阶段允许（不强制）直接给出初始认知偏差，让
"一开始就存在认知偏差"这个更有戏剧性的场景可以直接体现（比如
"你觉得竞争对手很弱，但实际上他们已经在秘密融资"）。`ScenarioDraft`
新增可选字段 `belief_fields`（同 `resource_fields`，创建向导可
编辑）和 `state0` 的初始 `beliefs`（随 `vars` 一起在
`materialize_simulation()` 落盘）。

## 4. Prompt 改动（`advance_step.yaml`）

在现有"如果这一步 `next_vars` 里有明显属于主观估计的字段，可以
给 `uncertain_fields`"这段说明之后，追加一段**仅当
`belief_fields` 非空时才拼进 prompt**（同 `causal_lines_hint` 等
条件性提示的既有模式，不给没声明这个功能的模拟增加任何 prompt
噪音）：

```
这次模拟声明了以下字段属于"角色认知可能与真实值不同"：
{belief_fields_hint}（如 market_demand、competitor_strength）。
`next_vars` 里这些字段请继续填写真实值（引擎按真实值推进后续
情节，不受角色认知偏差影响）。如果这一步角色对其中某个字段的
认知发生了变化（比如"意识到市场并没有那么火爆"），额外输出一个
可选字段 `beliefs`：一个对象，key 是发生认知变化的字段名，value
是角色现在认为的估计值（可以是单一数字，也可以是
{"low": ..., "high": ..., "point": ...} 这种区间估计，取决于这一步
角色的认知有多确定）。认知没有变化的字段不要输出——不要为了填满
这个字段而编造没有依据的"认知更新"。narrative 里如果提到角色的
认知和真实情况有出入，应该让这种"认知-真实"的落差自然体现在情节
里（比如角色因为误判市场规模做出了某个决策），而不是生硬地报数字。
```

## 5. `app.py` 展示

在 `_render_vars_display()`（现有的关键变量展示函数）里，对声明
了 `belief_fields` 的字段，如果同时存在于当前 `state.vars`（真实
值）和最近一次有记录的 `state.beliefs`（认知值），额外渲染一行
对比：

```
市场需求：真实 100 · 你以为 70~130（认知偏差）
```

真实值和认知值一致时不特别标注（避免"认知偏差"标签变成每次都在
的噪音，只在真正存在落差时才提示）；仅有真实值、从未出现过认知
记录的字段按普通字段展示，不强行渲染"未知的认知"占位。

## 6. 范围克制（重申，与父文档一致）

- 不做完整的"信息延迟建模"（信息要几步之后才被知道）。
- 不做每个 NPC 各自独立认知的完整版（复用 `multi_entity_mode`）。
- 不做"认知值自动收敛到真实值"的机制——认知什么时候更新、更新成
  什么，完全由 `advance_step` 每一步的叙事判断决定，不引入额外的
  时间/概率模型。
- 不对 `beliefs` 做任何校验或约束（不要求区间必须包含真实值等）——
  这是角色主观认知的记录，允许"角色的估计完全错误"这种情况，这
  正是这个功能要展示的东西。

## 7. 分批实施与验证计划（本方案与父文档的核心差异点）

父文档建议"先在一个模板里小范围验证 LLM 能不能稳定输出真实值和
认知值两套一致的数据"，具体拆成两批：

**第一批（验证批，仅 `life_sim` 模板）**：
1. `state_model.py` 加 `belief_fields` 设置 + `SimState.beliefs`
   字段（向后兼容，默认空）。
2. `advance_step.yaml` prompt 改动（仅当 `belief_fields` 非空时
   生效，其它模板的行为完全不受影响）。
3. `app.py` 真实值/认知值对比展示。
4. 单元测试：`SimState.beliefs` 的序列化/反序列化、
   `_render_vars_display` 对比逻辑的展示格式（不依赖真实 LLM
   调用，用固定的 fake state 数据验证展示文本）。
5. **人工验证**：至少手动跑 3~5 次 `life_sim` 真实模拟（真实调用
   LLM，不是 mock），声明 1~2 个 `belief_fields`，观察：
   - LLM 是否会为没有认知变化的字段乱填 `beliefs`（噪音过多）；
   - 给出的认知值是否呈现出有意义的"偏差"，而不是每次都和真实值
     几乎相同（如果 LLM 倾向于让认知值总是约等于真实值，这个功能
     就失去了价值，需要调整 prompt 措辞或放弃）；
   - `narrative` 是否真的把认知落差写进了情节，还是只在结构化
     字段里孤立地报数字。

**第二批（推广批，触发条件：第一批验证通过）**：

> **实施状态：代码已于阶段三十五（2026-09-19）完成，⚠️ 未满足本节
> 原定的触发条件**（"第一批验证通过"——第一批的人工验证根本没有
> 执行，见本文档顶部状态说明）。按用户明确要求跳过验证直接实施，
> 具体交付：
> 1. `ScenarioDraft.belief_fields` + `ScenarioDraft.beliefs`
>    （`spec_generator.py`），`generate_scenario.yaml` prompt 新增
>    对应说明（不使用 `advance_step` 那种"仅当已声明才提示"的条件
>    式写法，而是同 `resource_fields`/`uncertain_fields` 一样的
>    "无条件可选建议"写法——因为这一步之前用户还没有机会声明
>    `belief_fields`，只能靠 skill 主动判断这次模拟是否需要这个
>    功能）。
> 2. `engine/materialize.py::materialize_simulation()` 新增
>    `beliefs` 参数，落到 `state0.beliefs`；`create_simulation()`
>    （CLI 一步到位路径）透传 `draft.beliefs`。
> 3. `app.py` 创建向导新增"认知偏差声明字段"文本框（同
>    `resource_fields` 的既有 UI 模式：草稿建议值展示 + 可编辑，
>    最终结果存进 `settings.belief_fields`），并展示 skill 给出的
>    初始认知偏差摘要（只读展示，不提供额外编辑入口，同
>    `field_provenance` 的既有取舍）。
> 4. **未做**"推广到其它模板"里"根据第一批验证结果调整不同模板的
>    prompt 措辞"这部分——因为第一批验证没做，没有任何结果可以
>    参考，`advance_step.yaml`/`generate_scenario.yaml` 里的
>    `belief_fields` 相关 prompt 对所有模板一视同仁，没有针对任何
>    具体模板做过调整或验证。
> 新增 4 个单元测试（`ScenarioDraft` 解析、`materialize_simulation`
> 落盘、`create_simulation` 透传），全部通过（252 passed）；**没有
> 也不可能有任何针对本节核心问题——"LLM 能不能稳定输出有意义的
> 认知偏差"——的验证结果**，这需要真实调用 LLM 才能回答，单元测试
> 只能证明数据管道本身是通的。

1. `ScenarioDraft.belief_fields` + 初始 `beliefs`（`generate_
   scenario` 阶段）。
2. 创建向导 UI 支持声明 `belief_fields`（同 `resource_fields`
   模式）。
3. 推广到其它模板（`group_evolution` 等），根据第一批验证结果
   决定是否需要针对不同模板调整 prompt 措辞。

**如果第一批验证发现 LLM 无法稳定产出有意义的认知偏差**：本方案
建议直接放弃这个方向，而不是继续加大投入试图"调 prompt 调出效果"
——这类"依赖 LLM 主观判断力"的功能，如果验证阶段就看不到效果，
后续大概率也很难通过工程手段挽救，符合项目一贯的节制原则。

## 8. 涉及文件

- `world_simulator/state_model.py`（`belief_fields` 设置 +
  `SimState.beliefs`，第一批）
- `world_simulator/spec_generator.py`（`ScenarioDraft.
  belief_fields`，第二批）
- `workflows/advance_step.yaml`（prompt，第一批）
- `world_simulator/engine/materialize.py`（初始 `beliefs` 透传，
  第二批）
- `app.py`（真实值/认知值对比展示，第一批；创建向导声明入口，
  第二批）
- `tests/test_spec_and_engine.py`（新增 `beliefs` 相关用例）

## 9. 风险提示

- 最大风险是"LLM 主观判断力不足"——同父文档已经点出的顾虑，这也是
  为什么本方案坚持先做验证批、再决定要不要推广。
- `beliefs` 的取值格式（数字 vs 区间对象）不做强类型约束，
  `app.py` 展示层需要同时兼容两种格式，实现时要注意别因为格式
  分支写漏了某种情况导致展示报错——建议展示函数对未知格式统一
  降级为"原样转字符串展示"，不抛异常中断整个页面渲染。
