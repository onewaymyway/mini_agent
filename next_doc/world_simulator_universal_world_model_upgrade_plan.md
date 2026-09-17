# world_simulator 向"通用世界模型/实验引擎"演进计划

> **状态**：阶段九（4.1 节，资源类字段代码层校验）、阶段十（4.2 节，
> 对比实验升级为重复采样 + 结果聚合）、阶段十一（4.3 节，关键变量
> 可信度标注）、阶段十二（4.4 节，Problem Compiler 雏形，范围收窄为
> "记录 + 默认值参考"）、阶段十三（4.5 节，最小版因果摘要）、阶段
> 十四（4.6 节，Problem Compiler 进阶：目标驱动的自动排序）、阶段
> 十五（4.7 节，Evidence Chain 进阶：结构化因果链）均已完成，见
> `PROJECT.md` 对应交付记录。演进计划 4.1~4.7 节至此全部落地。阶段
> 十六（4.8 节，资源转移关系建模，通用规则引擎最小可行版本）已完成，
> 见 `PROJECT.md` 对应交付记录。演进计划 4.1~4.8 节至此全部落地。
> 阶段十七~十八（4.9~4.11 节，含一个不排期项）是对原第 6 节"本次不做
> 的事"的后续可执行计划，均**尚未实施**，见各节"优先级与依赖"说明。
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
> （不在本文档范围内，是文档以外的记录整理工作）。

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

不追求一次性做完（见第 4.6~4.11 节）：完整 Entity/Relationship 图
数据库、通用规则引擎 DSL、Hierarchical Agent、Reality Sync——这些
投入产出比在 4.1~4.5 节验证完之前偏低，但已经按"条件触发 + 设计
草案"的方式拆成了具体计划，不是无限期搁置，见第 4.6~4.11 节的
"优先级与依赖"说明。

## 3. 现状与已完成迭代对参考方案的覆盖情况

| 参考方案概念 | 现状 | 覆盖程度 |
| --- | --- | --- |
| Adaptive Time Resolution（第十八节） | `time_granularity_mode: fixed/auto/guided`，`SimState.time_granularity`/`granularity_changed`/`granularity_reason` | 已覆盖，含"延续上一步、变化需给理由"的约束 |
| Branch Engine / Fork / Version（第二十、三十一节） | `branch_manager.py`：fork/switch/delete/compare，`list_branches_detailed()` 带创建时间/来源/进度 | 部分覆盖：有 fork/compare，无 merge，无 skill/prompt 版本记录 |
| Experiment Engine 雏形（第二十二节） | `autopilot.run_comparison_experiment()`（多方案横向对比）+ `run_repeated_experiment()`（同方案重复采样）+ `analysis.aggregate_field_stats()`（统计聚合） | 阶段十已完成：横向对比 + 分布分析都有了，敏感性分析是 `run_repeated_experiment()` 的直接复用（未单独包装） |
| Action（第八节） | `custom_option`/`chosen_option`：用户/自动挡可以选择或新增选项 | 弱覆盖：Action 存在，但没有代码层的合法性/资源校验（本文档 4.1 节要补） |
| Resource / Rule（第七、十节） | `settings.resource_fields` + `engine._apply_resource_guard()`：数值下限校验，越界记入 `SimState.resource_violations` | 部分覆盖（阶段九已完成）：只做了"下限"这一种约束，转移/生产关系建模与通用规则引擎的最小可行版本见 4.8 节（计划中） |
| Uncertainty / Confidence（第二十八节） | `SimState.uncertain_fields`（阶段十一）：`generate_scenario`/`advance_step` 可选输出，`confidence` 限定高/中/低三档 | 阶段十一已完成：按需标注估计值型字段，不做全量精确概率 |
| Problem Compiler（第十四、十五节） | `spec_generator.ScenarioDraft.objectives`/`SimManifest.settings.objectives`（阶段十二） | 阶段十二已完成"记录 + 默认值参考"；自动排序/推荐见 4.6 节（计划中） |
| Belief 与 State 分离（第五节） | 全局 `vars`，无多主体私有信念 | 未覆盖，条件触发的计划见 4.9 节 |
| Evidence Chain / Debug Trace（第二十九、三十节） | `SimState.key_drivers`（阶段十三）：`advance_step` 可选输出 1~3 条短语 | 阶段十三已完成最小版"划重点"标签；完整结构化因果链见 4.7 节（计划中） |
| Entity/Relationship 图结构（第三、六节） | 自由 JSON `vars`，无图结构 | 未覆盖，条件触发的计划见 4.9 节 |
| 通用规则引擎 DSL（第十节） | 只有 4.1 节"数值下限"这一种硬编码校验 | 未覆盖，最小可行版本（不是完整 DSL）见 4.8 节（计划中） |
| Hierarchical Agent / Dynamic Cognition Router（第十九节） | 无 | 未覆盖，触发条件与设计草案见 4.10 节（不排期） |
| Reality Sync（第四十二节） | 无 | 未覆盖，轻量版（手动校准输入）计划见 4.11 节（条件触发） |

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

### 4.6（P1，已完成，见阶段十四）Problem Compiler 进阶：目标驱动的自动排序

**问题**：阶段十二只做了 `objectives` 的记录 + 「对比实验」页面默认值
参考，"哪个结果更好"仍然完全靠用户肉眼比较统计摘要。当对比实验的
分支数变多（比如阶段十的重复采样跑 10~20 次），肉眼比较的成本会
明显上升，`objectives` 声明的指标应该能直接驱动一个"按声明方向排序"
的辅助视图——但不做"自动选出最优解并替用户决定"，排序结果仍然只是
辅助参考，最终决策权留给用户。

**方案**：

1. `objectives` 的每一项从纯字符串升级为可选的结构化形式（**向后
   兼容**：仍然接受纯字符串，只是额外支持一种更精确的写法）：
   `{"label": "资产净值", "field": "resources.cash", "direction": "max"}`
   （`field` 可选，留空表示这一条指标本质是描述性的、不参与自动排序，
   只在 UI 上展示；`direction` 只能是 `"max"`/`"min"`，缺省 `"max"`）。
   纯字符串写法（阶段十二的行为）等价于 `{"label": <字符串>, "field":
   None}`——不参与排序，只展示，保证旧数据/旧 skill 输出不受影响。
2. `world_simulator/analysis.py` 新增 `rank_by_objectives()`：输入
   `aggregate_field_stats()` 已经算出的统计摘要（或者一组 `vars`）+
   声明了 `field` 的 `objectives` 列表，对每条分支/重复样本按"每个
   目标字段的值是否更优（按 `direction`）"计算一个简单的胜出计数或
   归一化打分，返回排序后的列表——刻意用最朴素的"逐项胜负计数"而不是
   加权求和，因为给不同指标分配权重本身是一个需要用户输入的主观决定，
   本次不引入权重配置这层复杂度。
3. `app.py`「对比实验」页面：跑完实验后，如果 `manifest.settings.
   objectives` 里有至少一条声明了 `field` 的指标，新增一个"按关注
   指标排序"的展示区（表格形式，列出每条分支在各个目标字段上的值 +
   综合胜出计数），明确标注"仅供参考，不代表系统认定的最优解"。
4. 创建向导"关注指标"编辑框旁边新增一个可折叠的"高级：声明可排序
   字段"小节，不想用结构化写法的用户完全可以忽略，继续用逗号分隔的
   纯文本（阶段十二行为不变）。

**涉及文件**：`state_model.py`（`SimManifest.settings` 文档更新，
`objectives` 支持两种写法的说明）、`spec_generator.py`
（`ScenarioDraft.objectives` 类型标注放宽为
`List[Union[str, Dict[str, Any]]]`）、`world_simulator/analysis.py`
（新增 `rank_by_objectives()`）、`app.py`（创建向导"高级"折叠区、
对比实验页面排序展示区）、对应测试。

**验收标准**：声明 `objectives: [{"label": "资产净值", "field":
"cash", "direction": "max"}]` 后跑一次重复实验（5 次以上），排序
展示区能看到 5 条分支按 `cash` 值降序排列，且界面上有"仅供参考"的
明确提示；只用纯字符串声明 `objectives`（旧写法）时，排序展示区不
出现（因为没有 `field` 可排序），其余行为与阶段十二完全一致。

**优先级与依赖**：P1，依赖阶段十（`aggregate_field_stats()`）和
阶段十二（`objectives` 字段）已经落地，建议在收集到"用户是否真的
会在对比实验里声明可排序指标"的使用反馈后启动；如果反馈显示
`objectives` 使用率很低，这一节可以降级为不做。

**实施记录**：已按上述方案实现（阶段十四）。`world_simulator/
analysis.py` 新增 `normalize_objectives()`（把纯字符串/结构化字典
两种写法统一成 `Objective` dataclass，字典项按 `label`/`field`/
`direction` 读取，`direction` 非法值一律当 `"max"`）与
`rank_by_objectives()`（只对声明了 `field` 的目标计分，逐项胜负
计数——每个目标字段取这批分支里的最优值，达到最优值的分支各记 1
分，按总分降序排列，同分保持原始顺序；一条都没声明 `field` 时返回
空列表）；`spec_generator.ScenarioDraft.objectives` 类型放宽为
`List[Any]`，`from_dict()` 对字典项原样保留、不强制转字符串；
`state_model.py` 的 `settings.objectives` 文档补充结构化写法说明。
`app.py` 创建向导"关注指标"编辑框下新增可折叠"高级：声明可排序
字段"小节（JSON 数组输入，纯文本写法完全不受影响、留空跳过）；
实例详情页"模拟设置"区块同款折叠区；「对比实验」页面"重复模式"
结果区在 `manifest.settings.objectives` 存在声明了 `field` 的条目
时新增"按关注指标排序"展示（每条分支的目标字段取值 + 胜出项数，
明确标注"仅供参考，不代表系统认定的最优解"）。验收标准已通过
`tests/test_analysis.py` 新增 8 个用例（纯字符串/结构化/混合写法、
max/min 方向、缺失值不计分、自定义标签、无可排序字段返回空列表）
与 `tests/test_spec_and_engine.py` 新增 1 个用例（`ScenarioDraft.
from_dict()` 解析结构化 `objectives`）覆盖，未接入真实 LLM 手动
验证——`generate_scenario` 阶段 skill 仍只输出纯字符串建议值，
结构化写法目前只能由用户在"高级"折叠区手动声明，skill 端是否要
主动建议可排序字段留给收集到真实使用反馈后再决定。

### 4.7（P2，已完成，见阶段十五）Evidence Chain 进阶：结构化因果链

**问题**：阶段十三的 `key_drivers` 只是"划重点"的短语标签，无法回答
"这个短语具体是从哪个变量的哪次变化推出来的"——用户仍然需要回读
`narrative` 才能建立"驱动因素 → 具体变量变化"的对应关系。

**方案**：

1. `advance_step` 新增可选输出字段 `causal_links`：一个数组，每项
   `{"driver": "现金储备见底", "affected_fields": ["cash",
   "stage"], "effect": "被迫从「自由职业」转为「求稳定工作」"}`——
   把 `key_drivers` 里的短语和"具体受影响的字段"、"具体的影响后果"
   关联起来，仍然是自由文本 + 字段名列表，不是可执行的因果图（完整
   可点击因果调试器投入产出比依然偏低，这一步只是把"划重点"升级为
   "划重点 + 说明影响了什么"，不做更复杂的图结构/可视化溯源）。
2. `SimState` 新增 `causal_links: List[Dict[str, Any]]`（默认空
   列表，`key_drivers` 与 `causal_links` 可以同时输出，也可以只输出
   `key_drivers`——`causal_links` 是给"想深入看一步"的用户的可选
   进阶信息，不强制每次都给）。
3. UI：`app.py` 在 `_key_drivers_html()` 渲染的标签上加一个可点击/
   可展开的详情（Streamlit 的 `st.expander` 或 `st.popover`，视当时
   Streamlit 版本支持情况选择），点开显示对应的 `affected_fields`/
   `effect`；没有 `causal_links` 时退化为阶段十三的纯标签展示，不
   强制升级 UI 复杂度。

**涉及文件**：`state_model.py`、`workflows/advance_step.yaml`、两个
`SKILL.md`、`engine.py`（解析落盘）、`app.py`（展示升级）、对应测试。

**验收标准**：一次推进里给出 `causal_links`，UI 上点开"现金储备见底"
这个标签能看到"受影响字段：cash, stage"+"被迫从自由职业转为求稳定
工作"的说明；不给 `causal_links` 只给 `key_drivers` 时，展示与阶段
十三完全一致（向后兼容）。

**优先级与依赖**：P2，依赖阶段十三（`key_drivers`）已经落地，且
明确建议先收集"用户是否会去点开 `key_drivers` 标签找更多信息"的
真实使用数据再启动——如果标签本身都很少被点开，做更详细的因果链
展示收益有限。

**实施记录**：已按上述方案实现（阶段十五）。`state_model.SimState`
新增 `causal_links`（字典列表，默认空，`from_dict()` 跳过非字典项
不报错中断）；`workflows/advance_step.yaml` 与两个模板 `SKILL.md`
都补充了可选输出说明；`engine.py::advance()` 从 `advance_step` 输出
里解析 `causal_links` 落到 `next_state`；`app.py` 的
`_key_drivers_html()` 升级为按 `driver` 文本匹配 `causal_links`，
匹配到的标签用原生 `<details>/<summary>` 渲染成可点击展开（点开显示
"受影响字段"/"具体后果"），没有匹配到的标签退化为阶段十三的纯标签
展示，新增配套 CSS。验收标准已通过 `tests/test_state_and_store.py`/
`tests/test_spec_and_engine.py` 新增的两个用例覆盖（序列化往返含
非字典项过滤、从 LLM 输出解析到 `next_state`），未接入真实 LLM
手动验证——这一节本身建议先收集"用户是否会去点开 `key_drivers` 标签"
的真实使用反馈，UI 已就绪，是否要往更完整的因果图方向继续投入留给
后续决定。

### 4.8（P2，已完成，见阶段十六）资源转移/生产关系建模——通用规则引擎的最小可行版本

**问题**：4.1 节只做了"单字段下限"这一种约束，参考方案第七、十节讲的
"转移"（A 减少的量等于 B 增加的量，比如"花钱买库存"）、"生产"（A
按某个速率持续产出 B，比如"雇佣的员工持续产出收入"）这类资源之间的
*关系*完全没有校验，LLM 完全可能算出"花了 100 现金，库存却只增加了
20"这种不守恒的结果而不被发现。

**方案**（刻意不做参考方案设想的完整 DSL，只做"关系声明 + 事后一致性
检查"这一种最小可行版本，验证思路是否值得投入更多再说）：

1. `SimManifest.settings` 新增可选字段 `resource_relations`：一个
   列表，每项声明一对"转移关系"：`{"type": "transfer", "from":
   "cash", "to": "inventory.value", "tolerance": 0.1}`（`tolerance`
   是允许的相对误差比例，默认 0.1，即允许 10% 的"汇率损耗/交易成本"
   之类的合理偏差，不强制精确守恒）。**只做 `transfer`（转移，两个
   字段的变化量应大致相反）这一种关系类型**，`production`（生产/
   持续产出，需要引入"速率"和"时间粒度换算"，复杂度明显更高）留到
   这一版验证完之后再决定要不要做，不在阶段十六范围内。
2. `engine.py` 新增 `_check_resource_relations()`：`advance()` 落盘
   `next_vars` 前，对每条声明的 `transfer` 关系，计算
   `delta_from = next_vars[from] - current_vars[from]`、
   `delta_to = next_vars[to] - current_vars[to]`，检查
   `abs(delta_from + delta_to) <= tolerance * max(abs(delta_from),
   abs(delta_to))`——**不拒绝推进、不修改数值**（和 4.1 节的下限
   校验不同，这里没有"应该是多少"的唯一正确答案，没法像下限那样
   直接夹值），只记录不一致，新增 `SimState.relation_violations`
   （列表，每项 `{from, to, delta_from, delta_to}`）供时间线展示
   "这一步的资源转移不太守恒，可能是 AI 算错了或者有未说明的损耗"。
3. UI：`app.py` 时间线卡片新增 `relation_violations` 非空时的提示行
   （样式参考 `resource_violations`，但措辞明确是"不一致提示"而不是
   "已自动纠正"，因为这里没有修改任何数值）。

**涉及文件**：`state_model.py`（`SimManifest.settings.
resource_relations`、`SimState.relation_violations`）、
`spec_generator.py`（`ScenarioDraft` 可选新增 `resource_relations`
建议值，写法同 `resource_fields`）、`generate_scenario.yaml`/两个
`SKILL.md`（可选输出说明）、`engine.py`（新增一致性检查）、`app.py`
（创建向导编辑框、时间线展示）、对应测试。

**验收标准**：声明 `resource_relations: [{"type": "transfer",
"from": "cash", "to": "inventory.value"}]`，构造一次 LLM 输出让
`cash` 减少 100、`inventory.value` 只增加 20（超出默认容差），
`next_state.relation_violations` 里能看到这条记录，时间线上有提示；
`cash` 减少 100、`inventory.value` 增加 95（容差内）时不产生记录。

**优先级与依赖**：P2，依赖阶段九（`resource_fields`/
`resource_violations` 的实现模式可以直接复用）；建议在阶段九上线
一段时间、收集到"用户是否真的在乎资源守恒"的反馈后再启动，如果
`resource_violations` 本身触发频率很低，说明 LLM 在这方面已经足够
自觉，这一节的优先级可以下调。

**实施记录**：已按上述方案实现（阶段十六）。`state_model.SimState`
新增 `relation_violations`（字典列表，默认空，`from_dict()` 跳过非
字典项不报错中断，格式同 `causal_links` 的容错处理）；
`state_model.SimManifest.settings` 文档补充 `resource_relations` 的
格式说明；`engine.py` 新增 `_normalize_resource_relations()`（只识别
`type == "transfer"`，非法/无法解析的项直接跳过）与
`_check_resource_relations()`（对每条声明的关系计算
`delta_from`/`delta_to`，超出容差记为一条不一致，**不修改任何数值、
不拒绝推进**），`advance()` 里用**夹值之后**的 `next_vars`（不是 LLM
原始值）与 `current.vars` 对比——检查应该看"最终真实落盘的变化"，
不是被下限校验修正之前的中间值；`spec_generator.ScenarioDraft` 新增
`resource_relations` 字段承接 skill 的建议值（写法同 `resource_
fields`）；`generate_scenario.yaml` 与两个 `SKILL.md` 补充了可选输出
说明（`advance_step` 阶段不需要 LLM 参与，检查完全在代码层完成，
`advance_step.yaml` 未改动）；`app.py` 在创建向导与实例详情"模拟
设置"区块都新增"高级：声明资源转移关系"折叠区（JSON 数组输入，写法
参考阶段十四的"高级：声明可排序字段"），时间线卡片（含游戏化章节
视图）新增 `relation_violations` 非空时的提示行（新增 CSS
`ws-chapter-relation-violation`，措辞明确是"看起来不太守恒"的不一致
提示，不是"已自动纠正"）。验收标准已通过
`tests/test_spec_and_engine.py` 新增的三个用例覆盖（一次超出容差的
真实不一致、一次容差内的对照、`ScenarioDraft.resource_relations`
解析）与 `tests/test_state_and_store.py` 新增的一个序列化往返用例
（含非字典项过滤），未接入真实 LLM 手动验证。落地过程中发现一处
需要规避的坑：`generate_scenario.yaml` 提示词里如果举例用带"."的
字段路径（如 `"inventory.value"`）或小数（如 `0.1`）拼进大括号
示例，会被 `tests/test_workflow_prompt_placeholders.py`（阶段八之后
新增的回归测试，见该文件顶部的事故复盘）判定为潜在的占位符误判
风险——最终示例改用不含"."的占位字段名（`"字段A"`/`"字段B"`）和
文字描述（"默认十分之一"）规避，`tolerance` 的默认值 0.1 只在代码
（`_normalize_resource_relations()`）里出现，不再出现在 prompt 的
大括号示例里。`production`（生产/持续产出）关系类型仍未实现，按
方案"验证完转移这一种类型再决定"的节奏，留给收集到真实反馈后再评估。

### 4.9（P3，条件触发，阶段十七）Belief 与 State 分离 + Entity/Relationship 图结构

**问题**（两节合并讨论，因为服务同一个场景）：现在 `vars` 是"全局
唯一真相"，`group_evolution` 模板把群体当一个整体推演，无法表达
"多个独立主体、每个主体有自己的信息/立场，且互相不完全知道对方的
真实状况"这类场景（参考方案第五节 Belief、第三/六节 Entity/
Relationship 图结构本质都是为了支撑"多主体互动"这个用例）。

**触发条件**（在满足以下条件之前不启动，这是明确的"条件触发"而不是
无限期搁置）：出现至少一个真实使用场景，要求"同一次模拟里有 2 个及
以上独立主体，且主体之间存在信息不对称或需要分别追踪各自的资源/
状态"（比如"模拟一场谈判，双方对彼此的底线互相不知情"、"模拟多家
公司在同一市场里的竞争，各自不知道对手的真实成本结构"）。现有的
`life_sim`（单主角）、`group_evolution`（群体当整体）两个模板都不
需要这个能力，勉强用现在的结构也能各自打个补丁（比如在 `vars` 里
用 `"甲方视角"`/`"乙方视角"` 两个子对象模拟"信息不对称"），只是不
优雅——在触发条件出现之前，用这种"打补丁"的方式过渡即可，不值得
现在就投入图结构。

**方案草案**（触发后启动，供到时候参考，不是现在就要精确到字段名）：

1. 新增一个可选的模板级能力声明：`SimManifest.settings.
   multi_entity_mode: bool`（默认 `False`）。为 `True` 时，`vars`
   的顶层约定一个 `entities: Dict[str, Dict[str, Any]]` 结构
   （每个 entity 一个 id，值是这个 entity 自己的私有 `vars`），以及
   一个 `shared_vars: Dict[str, Any]`（所有 entity 共享的公开信息，
   比如"当前谈判轮次"）——这是最小化的"Entity + 私有 Belief"结构，
   不是完整的关系图数据库（不引入 Relationship 的独立建模，entity
   之间的关系暂时仍靠 `narrative` 自由文本表达）。
2. `advance_step` 的推进粒度从"整体一次性输出" 改为"仍然一次调用，
   但要求 skill 分别给出每个 entity 的 `next_vars` 更新"——不拆成
   每个 entity 单独调用 LLM（会成倍增加调用成本，且割裂了"大家在同
   一个场景里互动"的上下文），只是要求单次输出里区分"谁知道什么"。
3. UI：详情页"关键变量"展示区，`multi_entity_mode` 为真时按 entity
   分 tab 展示各自的私有 `vars` + 一个"共享信息"区块。

**涉及文件（预估，供触发后细化）**：`state_model.py`、
`workflows/advance_step.yaml`、新增或复用现有 skill（可能需要一个
新模板如 `negotiation-template`，而不是改造现有两个模板）、
`app.py`。

**验收标准（预估）**：一个"双方谈判"模拟，甲方的 `vars.entities.
甲方` 里能看到"对乙方底线的猜测"这类甲方私有信念，且这个猜测和
`vars.entities.乙方` 里乙方自己的真实底线可以不一致，UI 上分 tab
展示不互相泄露。

**优先级与依赖**：P3，条件触发（见上），不分配固定阶段编号的启动
时间——阶段十七是"触发后要做的事"的编号占位，不代表现在就要排期。

### 4.10（不排期，仅记录设计草案）Hierarchical Agent / Dynamic Cognition Router

**问题**：参考方案第十九节讲的是面向百万级 Agent 的分层认知调度
（比如"大部分 Agent 用便宜模型跑简化逻辑，少数关键 Agent 才用完整
LLM 推理"），这是为"模拟一个包含大量个体的复杂系统"（比如"模拟一整
座城市每个居民的行为"）设计的架构。

**为什么不排期**：`world_simulator` 当前的应用场景边界很明确——
单实例对应"一个人生"或"一个群体（当整体推演）"，从未出现过、也没有
计划支持"同一个模拟里有成百上千个需要独立决策的个体"这种量级的场景。
在没有这类场景之前设计分层调度架构是纯粹的过度工程——不仅没有使用
方可以验证设计对不对，维护一套"用不上"的复杂度本身就是负担。

**触发条件**：出现真实需求，要求单次模拟包含"数量级明显超过个位数
的独立决策主体"（比如"模拟一个 50 人的部落，每个人有独立目标"），
且这类场景验证了 4.9 节的"少量多主体"方案（`entities` 字典）在
性能/成本上明显撑不住（比如每步都要对 50 个 entity 分别给出决策，
单次 `advance_step` 调用的输出体积/推理时间已经不可接受）。

**设计草案（触发后的第一步，不是最终方案）**：如果触发，第一步应该
是"分层"本身而不是"调度框架"——把 entity 分成"关键角色"（需要完整
LLM 推理，走现有 `advance_step` 流程）和"背景角色"（用规则/简化
逻辑批量更新，不调用 LLM，比如按预设的简单趋势外推 `vars`），而不是
一开始就设计一套通用的"认知路由器"。这样即使只做了这一步也有独立
价值（背景角色确实不需要每步都调用一次 LLM），且不需要预判"路由
策略"这类目前完全没有真实场景验证过的抽象。

**优先级与依赖**：不排期，触发条件出现后重新评估——评估时应该优先
看"4.9 节的 `entities` 字典能不能通过简单加一层「关键/背景」分类
撑住"，而不是默认需要一整套新架构。

### 4.11（P3，条件触发，阶段十八）Reality Sync 轻量版：外部数据手动校准输入

**问题**：参考方案第四十二节讲的 Reality Sync 是"接入真实外部数据源，
持续校准模型参数"（比如"接入真实的行业薪资统计数据，让模拟里的薪资
增长率贴近现实"）。完整版需要稳定的数据源接入 pipeline，现阶段没有
明确的数据源需求，投入这个不划算；但"完全不管真实数据、纯靠 LLM
的先验知识瞎猜"确实是一个可以改进的点，而且改进成本可以做得很低。

**触发条件**：出现真实使用场景，用户明确表示"希望某个模拟的初始
参数/演化趋势能贴合某个具体的真实数据"（比如"模拟创业过程，希望
初始融资环境参考 2024 年真实的天使轮平均估值区间，而不是 LLM 随便
编一个"）。

**方案**（轻量版，不做自动数据源接入——由用户手动提供校准信息，
系统只负责"把这份信息喂给 LLM 并要求参考"，不做任何自动抓取/更新）：

1. `SimManifest.settings` 新增可选字段 `calibration_notes`：一个
   字符串（或字符串列表），用户在创建向导/详情页手动填入的"真实世界
   参考信息"（比如"参考：2024 年国内一线城市应届硕士平均起薪
   1.2~1.8 万/月"），原样喂给 `generate_scenario`/`advance_step` 的
   prompt，要求 skill 在生成/推进时"参考但不照抄"这份信息（是否
   采信、怎么融入仍由 LLM 判断，系统不做任何数值层面的强制校准）。
2. 这个字段**留空是完全合法的默认状态**（大多数模拟不需要），不
   引入任何自动数据抓取逻辑——如果之后真的出现"需要自动拉取实时
   数据"的场景，那是一个明显更大的项目（需要评估数据源可靠性、
   更新频率、接入成本），不在这个轻量版范围内，届时应该另开一份
   独立的方案文档而不是塞进这个字段。

**涉及文件**：`state_model.py`（`SimManifest.settings.
calibration_notes` 文档）、`spec_generator.py`/`workflows/*.yaml`
（prompt 新增可选输入段落）、`app.py`（创建向导/详情页新增文本框）、
对应测试。

**验收标准**：填入一条 `calibration_notes` 后，`generate_scenario`
调用的 prompt 里能看到这段文本被原样传入（用现有的 monkeypatch 打桩
测试验证 `inputs` 字典里包含这段文本，不需要接入真实 LLM 验证"是否
真的被采信"，那属于 prompt 质量调优范畴）。

**优先级与依赖**：P3，条件触发（见上）。这一节和真正的"Reality Sync"
（自动数据源接入）是两个不同量级的东西——轻量版只是"让用户能手动
喂一句参考信息"，不代表要开始做数据源 pipeline；如果触发条件出现，
可以直接排期，不需要等其它节先完成。

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
  标签），未做完整的可点击因果调试器（完整版见 4.7 节，阶段十五）。
- **阶段十四**（已完成）：本文档 4.6 节，Problem Compiler 进阶——
  目标驱动的自动排序（`objectives` 结构化写法 + `rank_by_objectives()`
  逐项胜负计数排序），未引入权重配置。
- **阶段十五**（已完成）：本文档 4.7 节，Evidence Chain 进阶——结构化
  因果链（`causal_links`，UI 用 `<details>` 可展开标签），仍不是
  完整的可点击因果图。

- **阶段十六**（已完成）：本文档 4.8 节，资源转移关系建模——通用规则
  引擎的最小可行版本（只做 `transfer` 一种关系类型，`production` 未
  实现）。
- **阶段十七**（条件触发，非固定排期）：本文档 4.9 节，Belief 与
  State 分离 + Entity/Relationship 图结构——触发条件是出现真实的
  "多主体信息不对称"场景，见该节详细说明。
- **Hierarchical Agent / Dynamic Cognition Router**（不排期，无
  阶段编号）：本文档 4.10 节，触发条件是出现"数量级明显超过个位数
  的独立决策主体"场景，且验证了阶段十七的方案撑不住，见该节说明。
- **阶段十八**（条件触发，非固定排期）：本文档 4.11 节，Reality
  Sync 轻量版——外部数据手动校准输入，触发条件是出现真实的"希望
  模拟贴合具体真实数据"需求，见该节说明。

九、十、十一、十二、十三五个阶段彼此独立，不强制按顺序做，实际按
这个顺序完成，因为九是十、十一的地基（资源校验做完，统计摘要和
可信度标注展示才有更可靠的数据可用），十二的"对比实验默认值参考"
又依赖十的统计摘要能力已经存在；十三与其它几个阶段相互独立，只是
按文档顺序排在最后完成。至此演进计划 4.1~4.5 节全部落地。阶段十四
依赖阶段十（`aggregate_field_stats()`）和阶段十二（`objectives`
字段）已经落地，按文档顺序在它们之后完成；阶段十五依赖阶段十三
（`key_drivers`）已经落地，与阶段十四相互独立，实际按文档顺序在
其后完成。至此演进计划 4.6~4.7 节也已落地。

4.8~4.11 节（阶段十六~十八，含一个不排期项）是对原"本次不做的事"
清单（资源关系建模、Belief/图结构、Hierarchical Agent、Reality
Sync）的后续可执行拆解：阶段十六（资源转移关系建模）已完成，验收
标准见 4.8 节"实施记录"；阶段十七、十八和 Hierarchical Agent 是"条件
触发"项目——设计草案已经写好，但明确要求出现对应的真实场景才启动，
避免在没有验证需求的情况下过度设计。

## 6. 尚未启动项目的汇总（原"本次不做的事"，已拆解为可执行计划）

以下条目此前只是一句话式地记录"暂不做"，现已分别拆解为 4.8~4.11 节
的具体计划（含问题/方案/涉及文件/验收标准/优先级与依赖），本节只做
索引，不重复内容：

| 原条目 | 对应计划 | 状态 |
| --- | --- | --- |
| 通用规则引擎 DSL（转移/生产关系） | 4.8 节，阶段十六 | 已完成（只做 `transfer` 一种关系类型，`production` 未实现，见 4.8 节"实施记录"） |
| Belief 与 State 分离 + 完整 Entity/Relationship 图结构 | 4.9 节，阶段十七 | 条件触发（见该节触发条件） |
| Hierarchical Agent / Dynamic Cognition Router | 4.10 节，不排期 | 条件触发（见该节触发条件） |
| Reality Sync | 4.11 节，阶段十八（轻量版） | 条件触发（见该节触发条件）；完整版（自动数据源接入）仍不在计划内，触发后需另开文档 |

没有条目再处于"没有任何计划、纯粹搁置"的状态——即使是条件触发项，
也已经写清楚了"什么条件出现时启动"和"启动后第一步做什么"，不是
无限期不了了之。

## 7. 参考资料

- 用户上传文档：《万物模拟器到底如何构建？——从世界模型到通用模拟
  引擎的完整方案》（未纳入版本库，仅本次对话上下文引用其观点）。
- `next_doc/world_simulator_external_project_plan.md`：原始方案与
  阶段一~七的完整交付记录。
- `external_projects/world_simulator/PROJECT.md`：已知限制与变更
  记录。
