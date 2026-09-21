# world_simulator 对照《万能模拟器》参考文档的第九轮差距分析：
还差什么、哪些是"最小实现"而非完整实现

> **状态**：本文档只做盘点 + 方案设计，尚未开始实施。基于对
> `external_projects/world_simulator/PROJECT.md`（阶段一～阶段三十七，
> 468 个测试全部通过）和源码（`world_simulator/*.py`）的实地核查，
> 逐条对照用户提供的参考文档《万能模拟器到底如何构建？——从世界状态、
> 问题空间到通用现实模拟引擎》，找出**参考文档已经明确提出、但代码
> 里还没有 / 只有最小版本**的部分。不要求马上实施，先把全貌摆出来。
>
> **后续进展**：1.1 节已经通过 `next_doc/world_simulator_tenth_
> round_problem_discovery_automation_plan.md` 三个批次全部实施
>完成。1.2～1.6、第 2 节验证债务、第 3 节末文档流程债务的具体
> 执行方案见 `next_doc/world_simulator_eleventh_round_remaining_
> gaps_plan.md`（第十一轮，待实施）。

---

## 0. 先说结论：项目已经走了多远

这不是从零开始的评估。核查 `next_doc/world_simulator_*.md`（14 份既有
方案文档）与 `PROJECT.md` 变更记录发现，**参考文档里几乎每一条核心
概念都已经被识别、讨论过、并且相当一部分已经写成了代码**：

| 参考文档概念 | 项目现状 |
| --- | --- |
| 第四节 Desired State / Gap / Capabilities | `manifest.settings.desired_state`（conditions/constraints/preferences，非单一数值目标）已实现 |
| 第六、七节 Problem 作为第一等公民 / Problem Graph | `SimState.problems`（symptom/blocked_goal/missing_capabilities/status）已实现，但见下文 1.1 |
| 第十一节 Gap 不应是数字 | `missing_capabilities` 是字符串数组而非百分比，已符合 |
| 第十二节 Capability 对象 | `SimState.capabilities_gained`（capability/enables/limitations）已实现，但见下文 1.2 |
| 第十五节 决策选项必须是行动而非参数调节 | `engine/option_heuristics.py` 启发式检测"指标+数字"式选项并标警告，`decision_validation.py` 归一化 risk/urgency/action_type |
| 第十七节 Causal Engine（延迟、条件、传播） | `causal_graph.py` + `relationship.py`（delay_steps/propagation_path/reversible）+ `attribution.py` 归因，但见下文 1.4 |
| 第五十七节 What If / Counterfactual | `hypothesis.py`（project_line_futures/run_hypothesis_worlds/find_robust_outcomes/build_counterfactual_matrix）已实现 |
| 第五十九节 Fact/Assumption/Hypothesis/Prediction 分层 | `knowledge_base.py` 的 `confidence`/`evidence`/`valid_range` 部分覆盖，但见下文 1.6 |
| 第六十六节 十条设计原则 | 逐条都能在项目里找到对应机制（原则 6→option_heuristics；原则 7→LLM 只产出 narrative/options，`engine.py` 负责状态落盘；原则 9→branch_manager 多分支） |

也就是说，**这个项目不是"距离万能模拟器很远"，而是"已经把参考文档
翻译成了一版可运行的最小系统，现在需要判断哪些最小实现该长成完整
实现"**。这正是本文档要回答的问题。

**一个需要先纠正的记录缺口**：`state_model.py` 中 `problems`/
`capabilities_gained` 两个字段的 docstring 明确写着"第九轮批次一/
批次二，`next_doc/world_simulator_problem_capability_gap_plan.md`"，
但 `PROJECT.md` 的变更记录里检索不到这两个字段对应的条目（用规范化
文本比对，`world_simulator_problem_capability_gap_plan` 这个文件名
从未出现在 `PROJECT.md` 里）。也就是说**这两个字段已经落地、有代码
和 docstring，但没有走项目一贯"每次改动都在 PROJECT.md 记一笔"的
流程**。建议下一次改动 `state_model.py` 时顺手把这笔历史补记录上，
否则"哪个字段是哪一轮加的"这条项目一直维护的可追溯性会出现断层。

---

## 1. 六个仍是"最小实现"的核心方向（按参考文档章节对照）

### 1.1 Problem：缺 Root Causes / Candidate Solutions / Dependencies，
### 更缺"问题之间的因果图"

> **进度更新（2026-09-21）**：本节梳理出的问题已被拆分到
> `next_doc/world_simulator_tenth_round_problem_discovery_
> automation_plan.md` 继续推进——该文档批次一（自动触发机制）已
> 实施完成；批次二（`depends_on`/`root_causes`/`candidate_
> solutions` 结构化字段）、批次三（问题图可视化）待实施。本节原文
> 保留作为差距分析的历史记录，不再是"待办"的唯一入口。

**参考文档要求**（第六、四十八、四十九节）：Problem 不只是"症状+
缺什么能力"，还应该有 Root Causes（根因）、Candidate Solutions（候选
方案）、Dependencies（依赖哪些其它问题先解决），并且多个 Problem
之间应该能组成 **Problem Space**（个人问题→企业问题→社会问题→技术
问题→制度问题→新结构问题这样的层级网络），而不是互相孤立的列表。

**代码现状**：`SimState.problems` 只有 `id/symptom/blocked_goal/
missing_capabilities/status` 五个字段（`state_model.py` 573-611
行），docstring 明确写着"不做 Root Causes/Candidate Solutions/
Dependencies 等参考文档原文的其余字段；不做问题之间的因果关联图"。
`problem_discovery.py` 能建议/采纳/撤回问题建议，但产出的仍然是
独立的一条条问题，没有结构化的"问题 A 依赖问题 B 先解决"或"问题 A
是问题 B 的根因"这类边。

**差距**：现在只能回答"这一步存在什么问题"，无法回答"这些问题之间
是什么关系""解决哪个问题会连带影响哪些问题"——而这恰恰是参考文档
反复强调的"问题空间"而非"问题列表"。

**建议的完整版方向**（風险从低到高排列，可以分批做）：
1. `problems` 新增可选字段 `depends_on`（字符串数组，引用其它
   `problems[].id`，表示"这个问题要解决，通常需要先解决哪些问题"）——
   纯声明式，不做自动拓扑排序或强制校验，同项目一贯的"提示而非强制"
   风格。风险低，字段级扩展。
2. 新增可选字段 `root_causes`（字符串数组，1~3 条短语）和
   `candidate_solutions`（字符串数组，1~3 条短语，注意不是完整的
   Decision Option，只是问题被发现时 LLM 顺带给出的方向性提示，
   真正的行动选择仍然走 `ChoiceOption`）。风险低。
3. `app.py` 新增一个"问题图"只读视图：把同一实例历史里出现过的所有
   `problems`（按 `id` 去重、保留最新 `status`）用 `depends_on` 连边，
   复用阶段三十七第四批已经验证过的 `st.graphviz_chart()` 方案
   （同因果关系图的实现路径）。风险中——展示层为主，但需要先看
   真实数据下问题数量是否可控（同因果图当初的顾虑一致）。

**刻意不建议做的部分**：不做"问题自动判重/合并"（同已有
`problems.id` 的一贯取舍，这是 LLM/用户的判断，不是代码能安全自动
化的部分）；不做根因的自动推断（`root_causes` 只是 LLM 顺带给出的
陈述，不引入因果推断算法）。

---

### 1.2 Capability：没有九段成熟度生命周期，也没有
### "能力→行动空间"的哪怕最小关联

**参考文档要求**（第十二、十三、十六节）：技术/能力不该是一个百分比
成熟度，而应该有阶段性的生命周期（不可行→实验室可行→专家可用→
开发者可用→普通用户可用→成本足够低→大规模应用→基础设施化→社会
常态化），并且新能力出现后应该扩大 Action Space（可选行动集合）。

**代码现状**：`capabilities_gained` 的 docstring（613-647 行）明确
写着"不做参考文档九段生命周期的显式建模……留给后续观察到真实需求后
再评估；不做'能力→行动空间自动扩展'的关联计算"。目前 `enables`
字段只是给用户看的说明性文字，不会影响后续 `ChoiceOption` 的生成
逻辑——即"能力越多、可选行动应该越多"这条参考文档的核心因果链，
现在完全没有被工程化，纯粹靠 LLM 在写 narrative/options 时"自己想
到"要不要用上新能力。

**差距**：这是六个方向里**最贴近参考文档核心命题**（"发展 = Action
Space 的扩大"）却**兑现程度最低**的一处——目前项目对这条链路的处理
方式，本质上还是参考文档第二节批评的"LLM 直接决定现实如何发展"，
只是换了个字段名而已；只不过因为 `capabilities_gained` 目前只是
"说明性文字，不影响引擎"，还没有真正造成"LLM 编故事"的风险，而是
处于"builder 已经搭好但没人用"的状态。

**建议的完整版方向**：
1. 生命周期字段：`capabilities_gained[].maturity_stage`（可选枚举，
   `"lab"/"expert"/"developer"/"consumer"/"cheap_at_scale"/
   "widespread"/"infrastructure"/"normalized"` 之一，允许 skill 在
   一项能力被反复提及时更新它的阶段）。**不做**自动阶段推进算法——
   阶段变化仍然由 LLM 在叙事里体现，代码只做存储和展示；`app.py`
   新增按能力分组、展示阶段变化时间线的只读视图。风险低偏中（字段
   扩展 + 新展示逻辑，不碰引擎主循环）。
2. "能力→行动空间"的**最小**关联，不做自动映射，而是做"提示注入"：
   `engine/advance.py` 组装下一步 prompt 时，把当前活跃分支历史里
   `capabilities_gained` 的累计列表（复用 4.3 节已经提到的"由
   `app.py` 遍历 history 收集"的思路，改为引擎侧收集）作为一段
   "已获得能力"上下文喂给生成 `options` 的 prompt，明确要求 LLM
   "候选行动应该体现已获得能力带来的新可能性，而不是忽略它们"。
   这仍然是参考文档原则 7（"LLM 负责假设/方案，Simulation Engine
   负责约束"）框架下的做法——**不是**代码层面强制"有能力 X 就必须
   出现选项 Y"，只是把"能力清单"这个此前只存在于展示层的信息，接入
   决策生成的输入侧,让"能力扩大行动空间"这条链路第一次在 prompt
   层面真正被使用，而不是完全依赖 LLM 自己记得。风险中——改动面
   涉及 `engine/advance.py` 的 prompt 组装逻辑，需要先验证"喂了
   能力清单之后，选项质量是否真的变化"，不是纯展示层扩展。

**刻意不建议做的部分**：不做"能力→可选行动"的结构化自动映射表
（这需要给每个能力预先定义"解锁哪些行动模板"，本质上是回到了
参考文档批评的"技术树"思路，只是换了个名字；参考文档真正想要的是
"LLM 理解能力意味着什么、自己判断该开放什么行动"，而不是代码硬编码
映射关系）。

---

### 1.3 Desired State：仍是一次性声明，没有"移动的吸引子"这条动态性

**参考文档要求**（第九节）：理想状态不该是固定终点，而应该随着能力
提升不断演化——"原来的理想 → 新能力出现 → 新可能性出现 → 理想状态
发生变化"。

**代码现状**：`manifest.settings.desired_state` 是创建实例时声明的
一份 `conditions/constraints/preferences`，之后可以在"⚙️ 模拟设置"
里手动编辑，但**没有任何机制在能力/问题发生实质变化时提示用户"要不
要重新审视理想状态"**——理想状态的更新完全被动，依赖用户自己想起来
去改。

**差距**：这是参考文档里一个相对独立、篇幅不大但概念上很重要的点
（"Desired State 应该是动态的"），目前项目里约等于"没做"，只是恰好
因为 `desired_state` 本身可编辑，没有被完全锁死。

**建议的完整版方向**：不做自动改写 `desired_state`（这是用户的
价值判断，代码不该替用户决定"理想应该变成什么"），而是做**提示**：
`app.py` 在某一步 `capabilities_gained` 非空、或某个 `problems`
状态变为 `"solved"`/`"transformed"` 时，在详情页新增一条不打断
流程的提示条"这一步出现了新能力/问题状态变化，要不要重新看看理想
状态是否需要调整"，点击可直接跳转到"⚙️ 模拟设置"的 `desired_state`
编辑区。风险低——纯 UI 提示，不改任何数据结构或引擎逻辑。

---

### 1.4 Causal Engine：仍是"LLM 叙事 + 事后归一化记录"，
### 不是"条件化传播的模拟内核"

**参考文档要求**（第十七节）：因果引擎应该处理"在什么条件下、影响
多大范围、经过哪些中间节点、延迟多久、是正反馈还是负反馈"，能够
描述一条完整的多级传播链（如"AI 成本下降→服务降价→企业采用→生产率
提升→岗位需求变化→劳动力结构变化→教育变化"这种跨越 6~7 个中间
节点的链条）。

**代码现状**：`causal_graph.py` 把历史里声明的因果链（`cause`→
`effect`，带 `relation_type`）聚合成边集合用于展示；`relationship.py`
给"实体间关系"加了 `delay_steps`/`propagation_path`/`reversible`
这几个**提示性**字段；`attribution.py` 能对某个目标字段做贡献拆解。
但**这三者都是"记录/展示已经由 LLM 叙述出来的因果关系"，不存在一个
会真正做"传播计算"的内核**——比如"A 影响 B，延迟 3 步，B 又会以
多大幅度影响 C"这种链式传播，完全依赖 LLM 在每一步的 narrative 里
自己把多级影响都写出来，代码不做任何强制或校验传播是否完整。

**这不是疏漏，而是项目一贯的、写在多份方案文档里的取舍**："不做
自动化的数值计算或语义模型（因果关系、置信度等仍然由 LLM/用户
判断，代码只做归一化和存储）"。这个取舍本身是合理的（真正的因果
推断引擎是一个远超当前项目定位的研究课题），但它意味着**参考文档
第十七节设想的 Causal Engine，在这个项目里目前的完整程度上限就是
"结构化记录 + 可视化"，不会再长成"计算引擎"**，这一点建议在项目
文档里明确写清楚（属于"设计边界"而不是"待办事项"）。

**建议的方向**（如果要继续投入，只值得做"让记录更接近参考文档设想"
这一层，不建议做计算引擎）：
1. `causal_links` 补充 `delay_steps`（这条因果关系从 cause 到
   effect 隔了几步生效，选填，同 `relationship.py` 已有字段的风格）
   和 `magnitude`（自由文本，"轻微/明显/剧烈"这类定性描述，不做
   量化）。风险低，纯字段扩展。
2. `causal_graph.py` 的图视图上，把 `delay_steps` 非空的边用不同
   线型标出（比如虚线表示"延迟生效"），帮助用户在看图时区分"即时
   影响"和"滞后影响"，仍然是纯展示层改动。风险低。

---

### 1.5 制度/组织/技术协同演化（Co-evolution）没有被显式建模

**参考文档要求**（第五十三、五十四节）：制度可以理解为"重复问题的
解决机制"，技术、组织、制度、社会应该共同演化，而不是单向的
"技术→社会"。

**代码现状**：`group_evolution` 模板可以模拟组织/群体，`relationship.py`
的 `authority`/`dependency` 关系类型能部分表达制度性约束，但项目里
**没有一个专门的"制度"概念**——制度既不是 entity，也不是
capability，也不是 problem 的解决方案类型，找不到落点。

**评估**：这是六个方向里**最不建议现在投入**的一处。原因：
（a）现有三个模板（life_sim/group_evolution/negotiation）都不是
以"制度设计"为核心场景的模板，缺少真实使用场景验证"制度该怎么建模"
这个问题该怎么问；（b）参考文档本身也承认这是"最终目标"（第六十四
节"开放世界"阶段）才需要的复杂度，MVP（第六十一节）明确说"不要一
开始就模拟整个世界"。**建议**：等某个新模板明确需要"多个主体的
制度性协作/冲突"这个场景（比如做一个"创业公司与监管环境"或"多国
博弈"模板）时，再回头看要不要把"制度"提升为独立概念，现在强行加
一个用不上的字段没有意义。

---

### 1.6 Reality Data 分层（Fact/Assumption/Hypothesis/Prediction）
### 只有 confidence 一个维度，没有类型区分

**参考文档要求**（第五十九节）：World Model 应该明确区分 Fact（已
发生的事实）、Assumption（假设）、Hypothesis（有待验证的猜想）、
Prediction（对未来的预判）、Observation（观察），因为现实信息可能
错误、过时、冲突、不完整。

**代码现状**：`knowledge_base.py` 的 `KnowledgeItem` 有
`confidence`（连续值）、`validated_count`/`contradicted_count`
（累计验证/反驳次数），本质上是用"置信度会随验证次数变化"这一个
连续维度，隐式覆盖了参考文档想要的分层——一条 `confidence` 很低、
`validated_count` 为 0 的知识，效果上约等于一条"Hypothesis"；
`confidence` 高、`validated_count` 多的约等于"Fact"。但**没有一个
显式的类型标签**，用户/下游代码无法直接按"这是事实还是假设"筛选，
只能靠数值区间自己判断阈值。

**评估**：优先级低。理由：置信度连续值本身已经能回答"这条知识现在
有多可信"，这是参考文档想要解决的核心问题（避免把不可靠信息和事实
混为一谈）；额外加一个离散的 `kind` 字段主要是"分类展示更直观"，
边际价值不如前面几项高。**如果要做**，最小版本是给 `KnowledgeItem`
新增一个可选的、纯展示用的 `kind` 派生逻辑（`app.py` 层面按
`confidence`/`validated_count` 阈值自动打标签"事实性/假设性/
待验证"，不新增数据字段，不需要 LLM 额外声明），风险很低但也价值
有限，可以作为其它批次的"顺手加一句"，不必单独立项。

---

## 2. 仍未清偿的验证债务（不是代码问题，是"写完没人跑"）

这一条不是本轮新发现，是延续 `world_simulator_remaining_minimal_
systems_gap_survey_plan.md` 第 2 节从未清偿的旧账，本轮复核仍然
成立，一并列在这里提醒：

- `belief_fields`/`beliefs`（State/Belief 分离）——认知值是否真的
  会和真实值产生有意义的偏差，还是 LLM 偷懒让两者一直相等，从未
  用真实 `life_sim` 实例验证过。
- Agent Preview（自动挡决策预览）——是否真的帮用户提前发现自动挡
  要犯的错，从未验证。
- 独立推进因果线（`independent_line_advance`）——是否让某条线"很久
  没动静、体验割裂"，从未验证。

**这三项依然比本文档新提出的六个方向优先级更高**：在"哪些机制真的
有用"这件事上继续堆新代码，只会让问题更难看清。建议的顺序仍然是
先跑验证，再决定新一轮从 1.1～1.4 里选哪个开始。

---

## 3. 优先级建议汇总

```text
第一优先（真正该做的事，不是写代码）：
  第 2 节：A 类验证债务三项——belief_state / agent_preview /
  independent_line_advance，从未用真实数据验证过。

如果要继续写代码，按价值/风险排序：
  1.1 批次1（depends_on 字段）      —— 最小改动，直接补上"问题空间"缺口
  1.3（desired_state 动态提示）      —— 纯 UI 提示，风险最低
  1.4 批次1（delay_steps/magnitude） —— 复用已验证的字段扩展模式
  1.1 批次2（root_causes/candidate_solutions） —— 字段扩展
  1.2 批次1（maturity_stage 生命周期） —— 中等改动，需要先设计展示
  1.1 批次3（问题图可视化）          —— 复用已验证的 graphviz 方案
  1.2 批次2（能力接入 prompt）       —— 本轮里改动面/不确定性最大，
                                        建议放最后，且先小范围验证
                                        "喂能力清单是否真的提升选项
                                        质量"，不理想就退回

不建议现在做：
  1.5 制度/组织/技术协同演化 —— 没有真实场景需求，等新模板出现再评估
  1.6 Fact/Hypothesis 类型标签 —— 边际价值低，可以顺手做但不必立项

文档流程债务：
  补记 `problems`/`capabilities_gained` 两个已实现字段在 PROJECT.md
  里缺失的变更记录条目，恢复项目一贯的可追溯性。
```

---

## 4. 参考资料

- 用户提供文档：《万能模拟器到底如何构建？——从世界状态、问题空间到
  通用现实模拟引擎》（第四、六、七、九、十一、十二、十三、十五、
  十六、十七、五十三、五十四、五十七、五十九、六十一、六十六节为
  本文档主要对照依据）。
- `external_projects/world_simulator/PROJECT.md`（阶段一～三十七，
  实地核查变更记录得出"已完成"部分）。
- `next_doc/world_simulator_remaining_minimal_systems_gap_survey_
  plan.md`（第七轮差距分析，其 C 类六项已在阶段三十七全部完成，
  本文档在此基础上做第九轮盘点）。
- `next_doc/world_simulator_problem_capability_gap_plan.md`（源码
  docstring 引用的"第九轮批次一/二"方案文档，其字段已实现但未见
  于 `PROJECT.md` 变更记录，见本文档第 0 节记录缺口说明）。
