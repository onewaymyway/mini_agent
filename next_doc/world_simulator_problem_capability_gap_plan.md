# world_simulator 对照《万能模拟器》参考文档的差距分析：Problem / Capability 缺失（第八轮差距分析）

> **状态（第九轮批次一，已完成）**：本文档 2.1（`Problem` 结构化）
> 和 2.2（`Desired State` 结构化）两节已实施完成——`SimState` 新增
> `problems` 字段、`SimManifest.settings` 新增 `desired_state` 字段，
> 涉及的 workflow prompt（`generate_scenario.yaml`/`advance_step.
> yaml`/`world_builder.yaml`/`world_evolve.yaml`）、三个模板
> `SKILL.md`、`app.py` 创建向导与"模拟设置"页面的编辑入口、时间线
> "🧩 问题"展示区块均已同步更新，测试见 `tests/test_state_and_store.
> py`（`test_state_roundtrip_preserves_problems`/`test_manifest_
> roundtrip_preserves_desired_state`）与 `tests/test_spec_and_engine.
> py`（`test_scenario_draft_from_dict_parses_desired_state`/`test_
> resolve_hints_desired_state_hint_variants`/`test_advance_parses_
> problems_from_llm_output`）。2.3（`Capability` 对象）和 2.4
> （Problem Discovery Engine）**尚未开始**，按第 5 节建议顺序，
> 需要先观察 2.1/2.2 在真实使用中的效果（尤其是"LLM 是否会认真
> 区分 `problems`/`narrative`，而不是把已有的话换个地方重复一遍"
> 这条第 7 节点名过的风险）之后再评估是否推进，不建议立即接着做。
>
> 以下是本文档最初的盘点内容（保留作为背景与后续 2.3/2.4 的依据），
> 阅读时请注意 2.1/2.2 节描述的是**实施前的设计方案**，具体实现细节
> 以上面"已完成"状态说明和实际代码为准（比如 `problems` 最终没有
> 单独在 `generate_scenario` 阶段输出，只在 `advance_step`/
> `world_evolve` 阶段输出——创建时刻的"初始问题"这个概念被判断为
> 价值有限，落地时收窄了范围）。
>
> **原始状态说明**：本文档只做盘点 + 方案设计，尚未开始实施。这是用户上传
> 《万能模拟器到底如何构建？——从世界状态、问题空间到通用现实模拟
> 引擎》一文后，要求"对照这份文档，重新盘点项目还有哪些改进方向、
> 距离文档说的理想状态还有哪些差距"的产物。本文档**不是**要一次性
> 把下面所有方向都做完，是先把差距摆清楚，供用户决定下一步做不做、
> 先做哪个。
>
> **参考依据**：用户上传的参考文档《万能模拟器到底如何构建？——从
> 世界状态、问题空间到通用现实模拟引擎》全文，核心论点是"不要模拟
> 世界中的数字如何变化，而要模拟现实中什么问题存在、谁想改变它、
> 谁拥有解决它的能力、他们会采取什么行动，以及这些行动会把世界带
> 向哪里"（文档第六十八节）。
>
> **上游关系**：本文档与 `next_doc/world_simulator_remaining_
> minimal_systems_gap_survey_plan.md`（第七轮差距分析）是同一系列
> 的后续，但视角不同——第七轮盘点的是"项目自己历史方案里标注为
> 精简版、有明确补全方向"的系统，六条 C 类清单已经在阶段三十七
> （`world_simulator_c_category_precision_upgrade_improvement_plan.
> md`）全部完成。本文档是对照**新到的参考文档**重新从零盘点，
> 发现的差距和第七轮完全不重叠，是一个新的方向而不是第七轮的延续。

---

## 1. 结论先行：项目现状与文档理想状态的差距在哪一条轴上

项目经过 37 个阶段的迭代，在"状态数值如何演化、如何校验、如何
分叉、如何归因"这条轴上已经打磨得相当细：资源下限校验
（`resource_guard.py`）、资源转移/产出一致性检查（`resource_
relations`）、因果线未来树（`causal_tree.py`）、跨线耦合
（`causal_graph.py`）、反身性（`reflexivity.py`）、贡献拆解
（`attribution.py`）、多分支对比（`branch_manager.py` +
`hypothesis.py` + `autopilot.run_comparison_experiment()`）、
决策校验层拦截"指标调节语言"（`decision_validation.py` +
`engine/option_heuristics.py`）——这些都符合参考文档的具体原则
（尤其是原则 6"不要给用户参数调节，要给用户真实决策"、原则 9
"不要只模拟一条未来"）。

但参考文档反复强调的另一条轴——**"问题驱动发展"**（第三、六、
七、八节）——项目几乎完全没有对应的数据结构。文档原文：

> 万能模拟器不是多个领域模拟器的简单集合……真正驱动发展的不是
> "状态"，而是状态中的张力（第五节）。

> 问题应该成为模拟器的第一等公民（第六节标题）。

项目现状：`problem` 这个词在全部业务代码里只出现在两处历史注释
里（指"Problem Compiler"这个已经改名的旧提法），没有任何一个
`Problem` 类/字段。"问题"完全靠 LLM 在 `narrative` 自由文本里
顺带提及，不能被结构化查询、不能追问根因、不能追踪生命周期。这是
本文档要盘点的核心缺口。

---

## 2. 差距清单（按改动面从小到大排序，不代表价值排序）

延续项目一贯原则：新增字段/开关，旧路径完全保留；不引入自动化的
语义理解或数值计算模型（"这是不是同一个问题""根因是什么"这类
判断继续交给 LLM，代码只管结构、存储、展示）；先设计最小字段集，
不追求一次性覆盖文档里 `Problem` 的全部字段。

### 2.1 `Problem` 结构化：最小字段集（**已完成**，第九轮批次一）

**现状**：`settings.objectives` 是当前最接近"问题/目标"的字段，
但它是扁平的指标列表（`{"label": "资产净值", "field":
"resources.cash", "direction": "max"}`），本质仍是"数值要不要
变大/变小"，和参考文档第十一节明确批评的"Gap 不应该主要表现成
数字"是同一类做法。文档设想的 `Problem`（第二十三节）包含
`Observer`/`Affected Entities`/`Symptom`/`Root Causes`/
`Blocked Goals`/`Missing Capabilities`/`Candidate Solutions`/
`New Problems` 等字段，项目里完全没有对应结构。

**完整版方向（最小可行版本，不是文档全字段）**：

- `SimState` 新增可选字段 `problems: List[Dict[str, Any]]`
  （默认空列表），每项最小字段集：
  ```json
  {
    "id": "problem_1",
    "symptom": "资金不足，无法招聘核心工程师",
    "blocked_goal": "在 12 个月内完成产品原型",
    "missing_capabilities": ["种子轮融资", "早期客户验证"],
    "status": "emerging"
  }
  ```
  `status` 只取文档第二十四节生命周期的一个精简子集：
  `emerging` / `active` / `solved` / `transformed`（不做完整
  的 9 段生命周期，理由见下方"范围克制"）。
- `advance_step.yaml`/两个模板 `SKILL.md` 新增可选输出说明：
  "如果这一步的发展暴露/解决/重新定义了某个问题，请在
  `problems` 里给出对应记录"——**可选**，skill 不输出时
  `problems` 保持空列表，不影响任何已有行为（同 `resource_
  fields`/`objectives` 一贯的"留空=未启用"取舍）。
- `engine.py` 落盘时只做"透传 + 校验最小字段是否存在"，不做
  任何语义判断（不判断"这是不是同一个问题的延续"，不自动合并/
  去重——同项目一贯"结构由代码管，语义由 LLM/用户管"的分工）。
- `app.py` 时间线/详情页新增"🧩 问题"展示区块（纯展示，参考
  已有"🔗 跨线影响关系"折叠区的实现方式），按 `status` 分组
  展示。

**范围克制**：不做 `Root Causes`/`Candidate Solutions`/
`Dependencies` 等文档原文的其余字段——先验证"结构化问题列表本身
对用户是否有展示价值"，如果验证后确实有用，再考虑第二批扩展
字段（同项目"先最小可行、按真实反馈扩展"的一贯节奏）；不做
`Problem` 之间的因果关联图（那是 2.4 节的后续方向，依赖本节
先落地）。

**风险**：低——纯新增可选字段 + 展示层，旧实例/旧数据不受影响。

### 2.2 `Desired State` 结构化（**已完成**，第九轮批次一）

**现状**：`objectives` 只能表达"关注哪个指标、往哪个方向"，无法
表达文档第十节设想的"一组条件 + 约束 + 偏好 + 假设"这种结构
（原文 YAML 例子：`conditions`/`constraints`/`preferences`/
`assumptions` 四类）。

**完整版方向**：`SimManifest.settings` 新增可选字段
`desired_state`：
```json
"desired_state": {
  "conditions": ["financial_independence", "meaningful_work"],
  "constraints": ["cannot relocate", "limited capital"],
  "assumptions": ["current_technology_available"]
}
```
由 `generate_scenario` 阶段的 skill 给出建议值（同 `objectives`
的既有承接方式：`spec_generator.ScenarioDraft` 新增对应字段），
用户在创建向导/详情页"模拟设置"里可编辑。**不参与任何自动排序或
校验计算**，纯粹是"记录这次模拟的目标条件是什么"，供 2.1 节的
`problems.blocked_goal` 引用、供 2.4 节的 Problem Discovery
Engine 读取。

**范围克制**：不实现文档原文的 `preferences` 字段（和现有
`policy_feedback.py` 的"用户反馈反哺画像"功能有一定概念重叠，
避免引入两套相似但不统一的"偏好"表达）；不做"desired_state
随时间自动演化"（文档第九节"理想状态是移动的吸引子"）——这个
留给用户自己在详情页手动编辑来体现，不引入自动漂移逻辑。

**风险**：低，字段扩展 + 表单展示，同已完成的 `resource_
fields`/`objectives` 同一量级。

**依赖关系**：建议和 2.1 节一起设计（`problems.blocked_goal`
最好能对应到 `desired_state.conditions` 里的一项），但两者可以
分两个独立批次实施，互不阻塞。

### 2.3 `Capability` 对象

**现状**：技术/能力发展完全隐藏在 `vars`/`narrative` 自由文本里，
没有文档第十二、十三节设想的"能力"结构（`Prerequisites`/
`Applications`/`Limitations`/`Effects`/`Enables`）和九段生命周期
（不可行→实验室可行→……→社会常态化）。

**完整版方向**：`SimState` 新增可选字段 `capabilities_gained:
List[Dict[str, Any]]`（这一步新增的能力记录，不是全量能力
清单——全量清单可以由 `app.py` 在展示层对历史做累加，不需要
引擎每步重算），最小字段集：
```json
{
  "capability": "能够自动分析潜在客户的付费意愿",
  "enables": ["更精准的销售话术", "更快的产品迭代"],
  "limitations": ["无法替代真实用户访谈"]
}
```
展示层同 2.1 节。

**范围克制**：不做九段生命周期的显式建模（`不可行/实验室可行/
开发者可用/……`）——这需要为每条能力单独追踪阶段变化，改动面
和不确定性都明显更大，建议等 2.1/2.2 落地并观察到真实使用反馈
后再评估是否需要；不做"能力→行动空间自动扩展"的关联计算
（文档第十六节 Capability→Action Space），行动空间是否扩大继续
交给 LLM 在生成候选选项时体现，不引入代码层面的能力-选项映射。

**风险**：低偏中——概念上比 2.1/2.2 更依赖 LLM 稳定输出有意义
的内容，建议放在 2.1/2.2 之后、先观察"问题列表"本身是否好用，
再决定要不要加这一层。

### 2.4 Problem Discovery Engine（主动发现问题）

**现状**：项目现在完全被动——"问题"只在 LLM 叙事里顺带出现，
没有一个专门步骤系统性扫描"当前哪些目标被什么挡住了"，更没有
文档第四十七、四十八节说的 Latent Problem（还没爆发但已能从
结构推出来的问题）。

**完整版方向**：新增 `problem_discovery.py`，仿照 `hypothesis.
py`/`retrospective.py` 的既有调用手法（`WorkflowStore`/
`WorkflowRunner` + `extract_agent_json_output()`，一次轻量
`type: agent` workflow 调用）：`suggest_problems(cfg,
workspace_root, current_vars, desired_state, history)`——把
当前 `vars` + 2.2 节的 `desired_state` + 最近几步历史喂给 LLM，
要求输出"当前可观察到的问题"和"从现有结构可以推导但还没爆发的
潜在问题"两组列表（对应文档"Observed Problems"/"Latent
Problems"），作为**建议**展示给用户，用户可以选择性采纳进 2.1
节的 `problems` 列表（同 `hypothesis.suggest_experiment_
design()` 的"只建议、不自动写入"取舍）。

**范围克制**：不做"结构性问题"/"冲突目标"/"潜在未来问题"的
细分类别（文档原文四类，本方案先只做前两类）；不做自动周期性
触发（比如"每 N 步自动跑一次"）——只在用户主动点击"让系统扫描
潜在问题"时调用，避免增加不必要的 LLM 调用成本。

**风险**：中——依赖 2.1/2.2 先落地（没有 `desired_state`，
Discovery Engine 的输入就退化成只有 `vars`，效果会打折扣），
建议排在这两项之后。

---

## 3. 明确不建议现在做的方向（及理由）

对照参考文档，以下几个方向我认为**不建议**规划实施，原因分别
说明：

- **Reality Renderer 独立分层**（文档第二十八、二十九节：内部
  数值和人类可读描述应该分层）：项目现在 `narrative` 和 `vars`
  是同一次 LLM 调用里一起生成的，理论上存在"两者对不上"的风险，
  但要认真补这一层（新增"内部状态→结构化事件→渲染成叙事"的
  中间表示）改动成本很高。建议**先不做**，除非真的观察到
  "narrative 讲的事和 vars 变化对不上"的真实案例——这符合项目
  一贯的"条件触发"原则，比提前设计一整套渲染层更稳妥。
- **制度/组织/技术类型区分**（文档第五十三、五十四节）：项目
  现有模板（life_sim/group_evolution/negotiation）本身靠 skill
  的 prompt 承载"是什么类型的主体"，在数据模型层面再区分
  Technology/Organization/Institution 三个类型，收益不明确，
  容易变成"为了像文档而像文档"。**不建议现在做**。
- **First Possible Event 检测**（文档第四十节"第一次发生"）：
  依赖 2.3 节 `Capability` 对象先落地且经过验证，且和已有的
  `achievements.py`（固定 6 个游戏化徽章）在产品定位上有一定
  重叠，容易做出两套互相不呼应的"里程碑"机制。**留到 2.3 节
  验证之后再评估**，不在本轮规划。
- **九段技术生命周期**：见 2.3 节范围克制部分，理由同上。

---

## 4. 和项目自己已识别的"未验证项"的关系

项目自己的第七轮盘点（`world_simulator_remaining_minimal_
systems_gap_survey_plan.md` 第 2 节 A 类）里还留着三项**代码已
完成、但从未做真实验证**的机制：`belief_state_separation`
（Belief 与 State 分离）、`agent_preview`（自动挡决策预览）、
`independent_line_advance`（多因果线独立推进）。这三项和本文档
的差距在概念上有一定关联（分别对应参考文档"Fact vs Assumption
分层""Development Frontier 探索""多时间尺度"），但**不建议
用本文档的新方向去掩盖这三项的验证缺口**——如果这些已完成机制
本身没有被验证过真的有效，在它们之上再叠 Problem/Capability
结构会让"到底哪层出了问题"更难排查。

建议顺序：**先抽时间验证这三项 A 类机制，再启动本文档第 2 节的
任何一条**，或者至少两者并行、不要让本文档的新结构完全阻塞掉
那三项的验证工作。

---

## 5. 建议的实施顺序

```text
第一优先（结构性、后续工作的地基）：
  2.1 Problem 结构化（最小字段集）—— ✅ 已完成（第九轮批次一）
  2.2 Desired State 结构化 —— ✅ 已完成（第九轮批次一）
  ——这两项已一起设计（字段互相引用：`problems.blocked_goal` 可参考
     `desired_state.conditions`），分两个批次的字段/展示改动最终在
     同一批次内一起落地（改动面小，未强制拆分成两次交付）。

第二优先（依赖第一优先落地并观察到真实反馈后再做）：
  2.3 Capability 对象（最小字段集，不做九段生命周期）
  2.4 Problem Discovery Engine（依赖 2.1/2.2 的字段作为输入）

不建议规划：
  Reality Renderer 独立分层 —— 等真实观察到 narrative/vars
    不一致的案例再动手
  制度/组织/技术类型区分 —— 收益不明确
  First Possible Event 检测 —— 依赖 2.3 落地并验证之后再评估
  九段技术生命周期 —— 改动面大，不确定性高，同上

平行于以上：
  第七轮盘点里遗留的 3 项 A 类验证（belief 分离/agent preview/
  独立因果线）优先级不低于本文档任何一条，建议不要被新方向
  持续挤后。
```

---

## 6. 涉及文件（预估，实际实施时以当批方案细化为准）

- `world_simulator/state_model.py`：`SimState` 新增
  `problems`/`capabilities_gained`，`SimManifest.settings`
  新增 `desired_state`。
- `world_simulator/spec_generator.py`：`ScenarioDraft` 新增
  对应承接字段；`resolve_hints()` 新增提示文案。
- `workflows/generate_scenario.yaml`/`workflows/advance_step.
  yaml`：新增可选输出说明 + 占位符。
- `skills/life-sim-template/SKILL.md`、
  `skills/group-evolution-template/SKILL.md`、
  `skills/negotiation-template/SKILL.md`：补充可选输出说明
  （同资源字段/关系声明的既有做法，三个模板一起改，不落下）。
- `app.py`：新增"🧩 问题"/"🎯 理想状态"展示区块；2.4 节额外
  新增一个交互入口（"让系统扫描潜在问题"按钮）。
- 新增 `world_simulator/problem_discovery.py`（仅 2.4 节需要）。
- 对应新增/扩展的 `tests/`：`test_state_and_store.py`（字段
  序列化往返）、`test_spec_and_engine.py`（透传/校验行为）、
  新增 `test_problem_discovery.py`（若实施 2.4 节）。

---

## 7. 风险提示

- 本文档全部内容**尚未实施**，以上"涉及文件"和字段设计是基于
  现有代码结构的预估，实际实施时应该先出对应批次的细化子方案
  （同 `world_simulator_belief_state_separation_plan.md` 的
  既有做法），不要直接照本文档字段定义开工。
- 2.1/2.2 节的价值高度依赖 LLM 能否稳定产出有意义的 `problems`/
  `desired_state` 内容，而不是把 `narrative` 里已经有的话换个
  地方重复一遍——这和第七轮盘点里"belief 分离"的风险是同一类
  （"LLM 是否会认真区分两个字段，还是敷衍两份一样的内容"），
  建议第一批落地后**先在 `life_sim` 小范围人工验证 3~5 次**，
  确认 LLM 产出的 `problems` 确实比现有 `narrative` 更有信息量、
  确实能被用户用来做决策参考，再决定要不要推广到其它模板和
  2.3/2.4 节——不要重复"跳过验证直接推广"这个第七轮盘点里已经
  点名过的教训（见 `world_simulator_belief_state_separation_
  plan.md` 开头的状态说明）。
- 本文档第 2 节四项如果全部做完，`SimState` 会新增三个列表型
  字段（`problems`/`capabilities_gained`）+ `SimManifest.
  settings` 新增一个嵌套字段（`desired_state`），需要注意
  `from_dict()`/`to_dict()` 的旧数据兼容（同项目一贯的"新增
  字段默认空值，旧数据缺失该字段时安全兜底"做法）。
- **（第九轮批次一实施后新增）** 2.1/2.2 已完成落地，`SimState.
  problems`/`SimManifest.settings.desired_state` 均已按上面的
  兼容性要求实现并通过测试。**下一步不是立即做 2.3/2.4**，而是
  按本节前面的提示，先在 `life_sim` 小范围人工创建/推进 3~5 次，
  确认 LLM 产出的 `problems` 确实比现有 `narrative` 更有信息量、
  `desired_state` 确实被认真区分而不是和 `objectives`/`narrative`
  重复，再决定是否推进 2.3（`Capability` 对象）/2.4（Problem
  Discovery Engine）。

---

## 8. 参考资料

- 用户上传的参考文档《万能模拟器到底如何构建？——从世界状态、
  问题空间到通用现实模拟引擎》全文，尤其第三、五、六、七、九、
  十、十一、十二、二十三、二十四、四十、四十七、四十八节。
- `next_doc/world_simulator_remaining_minimal_systems_gap_
  survey_plan.md`（第七轮差距分析，六条 C 类已在阶段三十七
  全部完成，本文档与其内容不重叠）。
- `next_doc/world_simulator_belief_state_separation_plan.md`
  （"先小范围验证再推广"这条教训的原始出处）。
- `world_simulator/hypothesis.py`、`world_simulator/
  retrospective.py`（2.4 节 Problem Discovery Engine 建议
  复用的调用手法参考实现）。
