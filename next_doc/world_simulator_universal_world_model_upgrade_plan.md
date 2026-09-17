# world_simulator 向"通用世界模型/实验引擎"演进计划

> **状态**：阶段九（4.1 节，资源类字段代码层校验）、阶段十（4.2 节，
> 对比实验升级为重复采样 + 结果聚合）、阶段十一（4.3 节，关键变量
> 可信度标注）、阶段十二（4.4 节，Problem Compiler 雏形，范围收窄为
> "记录 + 默认值参考"）、阶段十三（4.5 节，最小版因果摘要）均已完成，
> 见 `PROJECT.md` 对应交付记录。演进计划 4.1~4.5 节至此全部落地。
> **前置文档**：`next_doc/world_simulator_external_project_plan.md`（原始方案，
> 阶段一~七已完成，见该文档状态栏）；用户提供的外部参考
> 《万物模拟器到底如何构建？——从世界模型到通用模拟引擎的完整方案》
> （以下简称"参考方案"）。
> **关系说明**：阶段七之后，项目又完成了两轮未在 `PROJECT.md` 里正式
>编号的迭代——(a) 分支列表信息增强 + 手动挡/自动挡自定义选项 +
> 多策略对比实验；(b) 自动时间粒度（`time_granularity_mode`:
> `fixed`/`auto`/`guided`）。这两轮已落地，本文档不再重复设计，只在
> 第 3 节里说明它们对应参考方案的哪些概念、覆盖到什么程度；建议在
> 启动本文档的阶段八之前，先补一次 `PROJECT.md` 的阶段编号回填
> （不在本文档范围内，见第 6 节"本次不做的事"）。

---

## 1. 背景

`world_simulator` 目前的能力可以概括为："结构化叙事推演器"——每一步
把当前状态整体丢给 LLM，LLM 输出下一状态的完整 JSON（`next_vars`/
`narrative`/`options`），engine 只做落盘、分支、暂停/恢复这些编排工作，
不参与任何"世界如何变化"的计算。这个模式在阶段一~七里验证了核心闭环
（生成→推进→分支→自动挡→多模板→游戏化视图），跑起来是通的，但离
"真正可运行、可实验、可信任的世界"还有结构性差距。

用户提供的参考方案系统化地指出了这类系统容易踩的坑，核心论点是：

1. 世界不是一堆数据，是 **Entity + Relationship + Resource + Rule +
   Event** 组成的、真正能"发生事情"的结构；
2. **LLM 该管理解/推理/生成，不该管执行/约束/计算**——现在恰恰是
   LLM 在一次调用里同时做了"决策解读"和"世界物理引擎"两件事；
3. 单次模拟意义有限，真正有价值的是**批量实验 + 统计分析**（敏感性、
   稳健性、关键变量），而不是看一条故事线；
4. 每个数值都应该有**可信度/来源标注**，否则"73.2% 的成功率"只是
   伪精确。

这份文档把参考方案的通用框架对照到 `world_simulator` 的具体代码
（`state_model.py`/`engine.py`/`autopilot.py`/`app.py`），拆成可以
分期落地的具体改动。

## 2. 目的

不追求一次性对齐参考方案的完整架构（那是一个"万物模拟器"级别的长期
项目，参考方案自己也建议"不要一开始就追求模拟整个世界"）。本次改进
的目的是：

- 把"LLM 一次性包办决策+世界演化+资源结算"这个最大的架构风险往
  "LLM 负责推理、代码负责约束"的方向收一步，哪怕只覆盖最基础的资源
  类硬约束；
- 把"多策略对比实验"从"确定性方案横向对比"升级为"同一方案的分布/
  敏感性分析"雏形，让"对比实验"这个已有功能真正开始产生参考方案
  第二十二~二十六节讲的那种价值；
- 给关键但本质是估计值的变量补上可信度标注，防止用户被伪精确的数字
  误导；
- 为后续（阶段十以后）做敏感性分析、目标驱动的自动排序打地基
  （Problem Compiler 雏形：显式提炼目标/约束/关键不确定性）。

不追求的目标（见第 6 节）：完整 Entity/Relationship 图数据库、通用
规则引擎 DSL、Hierarchical Agent、Reality Sync——这些投入产出比在
现阶段偏低，先按下不表。

## 3. 现状与已完成迭代对参考方案的覆盖情况

| 参考方案概念 | 现状 | 覆盖程度 |
| --- | --- | --- |
| Adaptive Time Resolution（第十八节） | `time_granularity_mode: fixed/auto/guided`，`SimState.time_granularity`/`granularity_changed`/`granularity_reason` | 已覆盖，含"延续上一步、变化需给理由"的约束 |
| Branch Engine / Fork / Version（第二十、三十一节） | `branch_manager.py`：fork/switch/delete/compare，`list_branches_detailed()` 带创建时间/来源/进度 | 部分覆盖：有 fork/compare，无 merge，无 skill/prompt 版本记录 |
| Experiment Engine 雏形（第二十二节） | `autopilot.run_comparison_experiment()`（多方案横向对比）+ `run_repeated_experiment()`（同方案重复采样）+ `analysis.aggregate_field_stats()`（统计聚合） | 阶段十已完成：横向对比 + 分布分析都有了，敏感性分析是 `run_repeated_experiment()` 的直接复用（未单独包装） |
| Action（第八节） | `custom_option`/`chosen_option`：用户/自动挡可以选择或新增选项 | 弱覆盖：Action 存在，但没有代码层的合法性/资源校验（本文档 4.1 节要补） |
| Resource / Rule（第七、十节） | `settings.resource_fields` + `engine._apply_resource_guard()`：数值下限校验，越界记入 `SimState.resource_violations` | 部分覆盖（阶段九已完成）：只做了"下限"这一种约束，无转移/生产关系建模、无通用规则引擎（本文档 4.1 节） |
| Uncertainty / Confidence（第二十八节） | `SimState.uncertain_fields`（阶段十一）：`generate_scenario`/`advance_step` 可选输出，`confidence` 限定高/中/低三档 | 阶段十一已完成：按需标注估计值型字段，不做全量精确概率 |
| Problem Compiler（第十四、十五节） | `spec_generator.ScenarioDraft.objectives`/`SimManifest.settings.objectives`（阶段十二） | 阶段十二已完成：仅"记录 + 对比实验默认值参考"，未做自动排序/推荐 |
| Belief 与 State 分离（第五节） | 全局 `vars`，无多主体私有信念 | 未覆盖，暂不做（见第 6 节） |
| Evidence Chain / Debug Trace（第二十九、三十节） | `SimState.key_drivers`（阶段十三）：`advance_step` 可选输出 1~3 条短语 | 阶段十三已完成：只做最小版"划重点"标签，不做完整可点击因果调试器 |

## 4. 改进方案

### 4.1（P0，已完成，见阶段九）资源类字段的代码层校验——"LLM 负责推理，代码负责约束"往前一步

**问题**：`engine.advance()` 把 LLM 返回的 `next_vars` 整体替换落盘，
不做任何校验。无论是 `custom_option`（用户/自动挡跳出候选列表提出的
自定义选项）还是普通候选选项，行动的资源成本完全依赖 LLM 的"自觉"，
现实里"花的钱超过账上现金"这种最基础的约束都拦不住。

**方案**：

1. 在 `SimManifest.settings` 里新增可选字段 `resource_fields`：一个
   字段名列表，声明 `vars` 里哪些字段是"资源类数值字段"（比如
   `["cash", "resources.amount"]`，支持一层嵌套路径），以及每个字段
   的下限（默认 `0`，即"不能为负"）。这个列表由 `generate_scenario`
   阶段的 skill 在生成初始 `vars` 时一并给出建议值（新增可选输出字段
   `resource_fields`），用户在创建向导里可以看到并编辑，不强制要求
   每次都填（留空则不做任何校验，行为与现在完全一致，向后兼容）。
2. `engine.py` 新增一个很薄的 `resource_guard.py`（或者就放在
   `engine.py` 内部一个私有函数，视代码量决定是否值得单独拆文件）：
   `advance()` 落盘 `next_vars` 之前，对 `resource_fields` 里声明的
   每个字段做一次下限检查——低于下限时**不拒绝这次推进**（不想让
   一个校验失败卡死用户的模拟），而是：
   - 把该字段夹到下限（比如 `cash` 强制不低于 0）；
   - 在 `next_state` 上标记一个新增字段 `resource_violations`
     （列表，每项 `{field, llm_value, clamped_value}`），供时间线展示
     "系统纠正了一处不合理的数值"，保持透明而不是静默篡改。
3. UI（`app.py`）：时间线卡片如果 `resource_violations` 非空，加一行
   醒目提示（类似粒度切换的高亮样式），点开能看到具体是哪个字段、
   LLM 给的原始值和被纠正后的值。

**范围克制**：这一步**不**引入通用规则引擎、不做资源之间的转移/生产
关系建模（参考方案第七节的 `transfer`/`production` 暂不做），只做
"数值下限"这一种最简单、最高频出问题的约束，验证"代码校验 LLM 输出"
这条路径本身是否好用、够不够用，为后续要不要扩展积累经验。

**涉及文件**：`state_model.py`（`SimState.resource_violations`、
`SimManifest.settings` 文档）、`spec_generator.py`（`ScenarioDraft`
新增 `resource_fields`，`generate_scenario.yaml`/两个 `SKILL.md` 的
`generate_scenario` 输出段落新增字段说明）、`engine.py`（新增校验
逻辑）、`app.py`（创建向导展示/编辑 `resource_fields`，时间线展示
`resource_violations`）。

**验收标准**：给一个"创业模拟"实例声明 `resource_fields: ["cash"]`，
连续推进到 LLM 给出负现金的那一步，`next_state.vars.cash` 落盘为
`0`（不是负数），`resource_violations` 里能看到原始负值，时间线上
能看到高亮提示。

**实施记录**：已按上述方案实现（阶段九）。`engine.py::advance()` 新增
`_apply_resource_guard()`（含嵌套路径读写辅助函数），在落盘 `next_vars`
前对 `manifest.settings.resource_fields` 声明的字段做下限检查并原地
夹值，越界项记入新增的 `SimState.resource_violations`；
`spec_generator.ScenarioDraft` 新增同名字段承接 skill 的建议值；
`generate_scenario.yaml` 与两个 `SKILL.md` 补充了可选输出说明；
`app.py` 在创建向导与实例详情"模拟设置"区块都提供了逗号分隔的编辑框，
时间线卡片（含游戏化章节视图）新增越界提示的高亮渲染。验收标准已通过
`tests/test_spec_and_engine.py` 两个新增用例覆盖（一次真实越界、一次
未越界的对照），未接入真实 LLM 手动验证。

### 4.2（P0，已完成，见阶段十）对比实验升级：从"多方案横向对比"到"同方案分布分析"

**问题**：现在的 `run_comparison_experiment()` 是 N 个命名策略各跑
一次，属于确定性对比；参考方案第二十二~二十六节的核心价值是
"同一个方案在不确定性下的分布长什么样"（Monte Carlo）、"哪个变量最
敏感"（Sensitivity Analysis）、"哪个方案在最坏情况下也能活下来"
（Robustness），这些现在都做不了。

**方案**（分三个独立能力，可以分别验收，不要求一次做完）：

1. **重复采样（Monte Carlo 雏形）**：`autopilot.py` 新增
   `run_repeated_experiment()`：给定同一个策略 profile + 起点分支 +
   推进步数 + 重复次数 `n_repeats`，从同一节点 fork 出 `n_repeats`
   条分支，用**完全相同**的 profile 各自独立推进（差异只来自 LLM
   输出本身的随机性），返回每条分支的关键结果。
2. **结果聚合**：新增 `analysis.py`（放在 `world_simulator/` 下）：
   输入一组 `SimState`（多条分支的终态）+ 用户指定要关注的
   `vars` 字段路径列表，计算这些字段的均值/最大/最小/标准差（数值型
   字段）或众数分布（枚举型字段），输出一个简单的统计摘要——不需要
   引入 numpy/pandas，几十行纯 Python 就够。
3. **UI**：「对比实验」页面新增一个"重复模式"开关：关闭时是现有的
   "N 个不同策略各跑一次"；打开时变成"1 个策略 × N 次重复"，跑完后
   展示 4.2.2 的统计摘要（而不是简单列出每条分支的完成情况）。

**敏感性分析（单变量扰动，作为本节的延伸，可以放到下一期）**：给定
一个初始变量（比如 `initial_cash`）和几个扰动值（比如"25 万/30 万/
35 万"），各自 fork 一条分支、跑相同步数，比较关键结果的差异幅度，
粗略回答"这个变量有多敏感"——实现上是"重复采样"能力的直接复用（把
"随机性"换成"人为设定的初始值差异"），不需要额外的新机制。

**涉及文件**：`autopilot.py`（`run_repeated_experiment()`）、新增
`world_simulator/analysis.py`（纯函数统计聚合，无持久化，参考
`achievements.py` 阶段七的"纯函数计算，无新增持久化结构"取舍）、
`app.py`（对比实验页面新增"重复模式"）、对应测试。

**验收标准**：对同一个策略跑 5 次重复实验，能看到 5 条独立分支
+ 一份"关键变量均值/极差"的统计摘要；结果里能看出"这个策略的产出
其实波动很大"或者"很稳定"这类此前完全看不出来的信息。

**实施记录**：已按上述方案实现（阶段十）。`autopilot.py` 新增
`run_repeated_experiment()`（复用 `run_comparison_experiment()` 的分支
管理逻辑，把"N 份不同 profile"换成"1 份 profile 复制 N 次"）；新增
`world_simulator/analysis.py::aggregate_field_stats()` 做统计聚合
（数值型均值/极差/标准差，枚举型分布，纯 `statistics` 标准库实现）；
`app.py`「对比实验」页面新增"重复模式"开关 + 关注字段输入框 + 统计
摘要展示。敏感性分析部分按计划直接复用 `run_repeated_experiment()`
（把随机性换成人为设定的初始值差异），未单独包装成一层新 UI/函数，
详见 `PROJECT.md` 阶段十"实施记录"一段的说明。验收标准已通过
`tests/test_autopilot.py::test_run_repeated_experiment_forks_n_branches_with_same_profile`
+ `tests/test_analysis.py`（6 个用例）覆盖，未接入真实 LLM 手动验证。

### 4.3（P1，已完成，见阶段十一）关键变量的可信度标注

**问题**：`vars` 里"年龄=23"（基本确定）和"创业成功率=18%"（本质是
LLM 主观推断）现在展示上一视同仁，用户没法分辨该信哪个。

**方案**：不对全量字段做这件事（成本高、收益低），只对
"驱动性强、本质是估计值"的字段按需标注：

1. `advance_step.yaml`/`generate_scenario.yaml` 新增可选输出字段
   `uncertain_fields`：LLM 可以在这里列出这一步/初始状态里"哪些字段
   是自己主观估计出来的、置信度不高"，格式
   `[{"field": "startup_success_rate", "confidence": "low", "note": "..."}]`
   （`confidence` 只分高/中/低三档，不追求精确概率，避免"伪精确"
   本身重演）。
2. `SimState` 新增 `uncertain_fields: List[Dict[str, str]]`（默认空
   列表，不影响旧数据）。
3. UI：详情页"当前变量"展示区（如果有结构化展示，目前是 `st.json`
   原样展示 `vars`）在被标注的字段旁边加一个小图标 + tooltip 显示
   `confidence`/`note`。

**涉及文件**：`state_model.py`、`workflows/*.yaml`、两个
`SKILL.md`、`engine.py`（解析落盘）、`app.py`（展示）。

**验收标准**：一个"是否创业"的模拟里，`startup_success_rate` 这类
字段能在 UI 上看到"低置信度"标注和一句话说明，而 `age` 这类确定性
字段不受影响、不会被过度标注。

**实施记录**：已按上述方案实现（阶段十一）。`state_model.SimState`
新增 `uncertain_fields`（列表，每项 `{field, confidence, note}`，
`confidence` 限定 `high`/`medium`/`low` 三档）；
`spec_generator.ScenarioDraft` 新增同名字段承接 skill 的建议值；
`generate_scenario.yaml`/`advance_step.yaml` 与两个模板 `SKILL.md`
都补充了可选输出说明；`engine.py` 的 `materialize_simulation()`/
`create_simulation()`/`advance()` 三处都已打通传递与解析，`advance()`
从 `advance_step` 输出里按需解析进 `next_state.uncertain_fields`；
`app.py` 新增 `_uncertain_fields_html()`，在实例详情页与游戏化视图
"关键变量"展开区、`st.json` 原始展示之上新增置信度徽章 + 一句话说明的
列表渲染。验收标准已通过 `tests/test_state_and_store.py`/
`tests/test_spec_and_engine.py` 新增的四个用例覆盖（序列化往返、
落盘到 `state0`、从 LLM 输出解析到 `next_state`），未接入真实 LLM
手动验证。

### 4.4（P2，已完成，见阶段十二；范围收窄为"记录 + 默认值参考"，未做自动排序）Problem Compiler 雏形：显式目标/约束/关键不确定性

**问题**：`generate_scenario` 现在一步到位生成初始状态，没有先提炼
"这次模拟到底要优化什么目标"。这个缺口会在 4.2 节的敏感性分析/未来
的"自动推荐更优策略"上反噬——没有显式目标，系统没法自动判断"哪个
结果算更好"，只能靠用户肉眼比较。

**方案草案**（留到 4.1/4.2/4.3 验证完之后再启动，本文档先占位）：

- `ScenarioDraft` 新增 `objectives: List[str]`（这次模拟主要关心的
  指标，比如"财富""自由度""风险"）；
- 创建向导在"生成提案草稿"之后、"确认创建"之前，新增一个"确认关注
  指标"的编辑步骤（复用现有"编辑草稿"UI 的模式，不是新页面）；
- 4.2 节的统计摘要、未来的"自动推荐"功能，都可以直接引用
  `manifest.settings.objectives` 里声明的字段路径做排序，而不用每次
  重新问用户"你想看哪个指标"。

本节不在本次迭代范围内实施，先写入计划文档留痕，等 4.1~4.3 落地并
收集到真实使用反馈后再决定是否启动、以及具体设计是否需要调整。

**实施记录**：阶段十二已按"方案草案"的核心思路实现，但主动收窄了范围：
只做`objectives: List[str]`的记录与展示，**不实现**"自动判断哪个结果
算更好"/自动排序——用户手动比较结果这件事本身不是本次要解决的问题，
过早引入自动排序反而会在没有真实反馈的情况下把"什么算好"这个主观
判断权交给系统。具体实现：`spec_generator.ScenarioDraft.objectives`
承接 `generate_scenario` 阶段 skill 的建议值（`generate_scenario.yaml`
与两个模板 `SKILL.md` 都已补充可选输出说明）；创建向导新增"关注指标"
编辑框（复用"编辑草稿"UI 的模式，同一个页面内新增一个字段，没有新增
页面/步骤），确认创建后随 `settings.objectives` 一起存进 manifest，
实例详情页"模拟设置"区块可随时增删；「对比实验」页面的"关注哪些变量
字段做统计摘要"输入框会用 `manifest.settings.objectives` 作为默认值
（可以再改），把 4.2 节的统计摘要和这里的"关注指标"声明串起来，减少
"每次都要重新想清楚看哪个字段"的重复劳动。`engine.py` 不因这个字段
的声明与否改变任何推进/校验逻辑，纯粹是记录 + UI 默认值参考。验收：
`tests/test_spec_and_engine.py` 新增两个用例（`ScenarioDraft.objectives`
解析、`materialize_simulation()` 落盘到 `manifest.settings.objectives`），
未接入真实 LLM 手动验证。是否要在此基础上做"自动排序/推荐"，留给
收集到真实使用反馈后再决定（与原方案的谨慎态度一致）。

### 4.5（P2，已完成，见阶段十三）最小版因果摘要

**问题**：`narrative` 是自由文本，用户没法快速抓住"这一步变化的关键
驱动因素是什么"，只能通读整段叙事。

**方案**：`advance_step` 新增可选输出字段 `key_drivers`（1~3 条短
语，比如"市场需求超预期""现金储备见底被迫收缩"），不是参考方案第
二十九、三十节讲的完整 Evidence Chain/Debug Trace（那个投入产出比
在现阶段太低），只是给 `narrative` 补一个"划重点"的结构化摘要，
时间线卡片可以用几个标签样式展示，比逐字读叙事更快抓住关键信息。

**优先级说明**：这一节收益尚不确定（需要看实际使用中用户是否真的
觉得"读不过来叙事"），列为 P2 可选项，实施顺序上排在 4.1~4.3 之后，
如果时间/精力有限可以跳过。

**实施记录**：已按上述方案实现（阶段十三）。`state_model.SimState`
新增 `key_drivers`（字符串列表，默认空，`state0` 一般不产生这个字段，
只在 `advance()` 每一步推进时按需解析）；`workflows/advance_step.yaml`
与两个模板 `SKILL.md` 都补充了"1~3 条短语、不要凑数"的可选输出说明；
`engine.py::advance()` 从 `advance_step` 输出里解析
`data.get("key_drivers")` 落到 `next_state.key_drivers`；`app.py` 新增
`_key_drivers_html()`，在时间线卡片（含独立时间线视图与游戏化章节
视图）的叙事文本之前，以"🔑 短语"标签样式展示，不影响没有这个字段时
的原有展示。验收标准通过 `tests/test_state_and_store.py`/
`tests/test_spec_and_engine.py` 新增的两个用例覆盖（序列化往返、从
LLM 输出解析到 `next_state`），未接入真实 LLM 手动验证——这一节本身
收益不确定，建议收集真实使用反馈后再决定要不要往"结构化因果链"方向
进一步投入。

## 5. 分期路线图

延续 `PROJECT.md` 已有的阶段编号习惯（阶段一~七已完成）：

- **阶段八**（回填，非本次工作）：把"分支信息增强 + 自定义选项 +
  多策略对比实验""自动时间粒度"两轮已落地但未编号的迭代补记到
  `PROJECT.md`。
- **阶段九**（已完成）：本文档 4.1 节，资源类字段代码层校验。
- **阶段十**（已完成）：本文档 4.2 节，对比实验升级为重复采样 + 统计聚合
  （敏感性分析作为阶段十的延伸任务，已按"直接复用重复采样"的方式
  说明，未单独实现新机制/新 UI，见实施记录）。
- **阶段十一**（已完成）：本文档 4.3 节，关键变量可信度标注。
- **阶段十二**（已完成，范围收窄）：本文档 4.4 节，Problem Compiler
  雏形——只做 `objectives` 的记录与"对比实验"页面默认值参考，未实现
  自动排序/推荐（原方案草案里的这部分留给收集到真实反馈后再决定）。
- **阶段十三**（已完成）：本文档 4.5 节，最小版因果摘要（`key_drivers`
  标签），未做完整的可点击因果调试器（见第 6 节"本次不做的事"）。

九、十、十一、十二、十三五个阶段彼此独立，不强制按顺序做，实际按
这个顺序完成，因为九是十、十一的地基（资源校验做完，统计摘要和
可信度标注展示才有更可靠的数据可用），十二的"对比实验默认值参考"
又依赖十的统计摘要能力已经存在；十三与其它几个阶段相互独立，只是
按文档顺序排在最后完成。至此演进计划 4.1~4.5 节全部落地，后续是否
在这些基础上进一步投入（比如 4.4 节的自动排序、4.5 节的完整因果链），
留给收集到真实使用反馈后再评估。

## 6. 本次不做的事（及原因）

- **完整 Entity/Relationship 图结构**（参考方案第三、六节）：现在
  `vars` 是自由 JSON 已经能覆盖当前"单主角/单群体"场景的表达需求，
  引入图结构收益要等到"多主体互相博弈"这类场景成为主要用例时才明显，
  现在做投入产出比低。
- **通用规则引擎 DSL**（`FormulaRule`/`ConditionalRule`/
  `ProbabilityRule`/`ScheduleRule`，参考方案第十节）：4.1 节只做了
  "数值下限"这一种最简单的硬编码校验，没有做成可配置的规则系统——
  先用最小实现验证"代码校验 LLM 输出"这条路径的价值，再决定要不要
  投入做通用引擎。
- **Belief 与 State 分离**（参考方案第五节）：需要多主体私有信念的
  场景（比如"竞争对手不知道你现金流紧张"）目前还不是核心用例，
  `group_evolution` 模板现在也是把群体当一个整体推演，不是模拟内部
  多个个体互相博弈。
- **Hierarchical Agent / Dynamic Cognition Router**（第十九节）：
  面向百万级 Agent 的分层认知，和我们"单实例、单/双主角"的应用场景
  不匹配。
- **Reality Sync**（第四十二节）：接入真实外部数据校准模型，属于
  长期方向，现阶段没有可靠的数据源接入需求。
- **Evidence Chain / Debug Trace 完整版**（第二十九、三十节）：只做
  4.5 节的最小版"划重点"摘要，不做完整的可点击因果调试器。

## 7. 参考资料

- 用户上传文档：《万物模拟器到底如何构建？——从世界模型到通用模拟
  引擎的完整方案》（未纳入版本库，仅本次对话上下文引用其观点）。
- `next_doc/world_simulator_external_project_plan.md`：原始方案与
  阶段一~七的完整交付记录。
- `external_projects/world_simulator/PROJECT.md`：已知限制与变更
  记录。
