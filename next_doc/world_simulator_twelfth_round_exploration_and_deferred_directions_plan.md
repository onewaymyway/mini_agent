# world_simulator 改进计划：探索模式新差距 + 此前搁置方向一并推进
# （第十二轮）

> **状态**：按第 8 节实施顺序逐阶段推进中。第 1 节（Exploration
> Mode）已完成，详见该节末尾的实施记录；其余各节仍在规划中，尚未
> 开始实施。

## 0. 这份文档是什么

用户在第十一轮计划（`world_simulator_eleventh_round_remaining_gaps_
plan.md`）全部完成后，要求对照参考文档《万能模拟器到底如何构建？
——从世界状态、问题空间到通用现实模拟引擎》重新做一轮全文通读式
差距分析。分析结论分两类：

1. **两个此前四轮分析都没覆盖到的真实新差距**（第 1、2 节）：
   - Exploration Mode 缺失——分支功能已存在，但完全靠用户手动一条
     条开，没有"批量探索多条候选路线、一次性生成多个可能世界"的
     机制（对应参考文档第三十四节第 3 小节、第五十、五十一节）。
   - 多主体各自的 Desired State 未建模——`desired_state` 是全局
     单一字段，没有"每个主体理想状态不同、可能互相冲突"的结构
     （对应参考文档第五十二节）。

2. **四个此前已经识别、但项目主动选择暂缓的方向**（第 3～6 节）：
   Reality Renderer 独立分层 + 五层输出框架 + First Possible Event
   （合并处理）、制度/组织/技术类型区分、技术/组织/制度协同演化、
   Fact/Assumption/Hypothesis/Prediction 类型标签。这四项此前分别
   在 `world_simulator_ninth_round_theory_gap_analysis_plan.md`
   1.5/1.6 节和 `world_simulator_problem_capability_gap_plan.md`
   第 3 节被评估为"不建议现在做"（收益不明确/依赖其它验证/边际
   价值低），**本轮用户明确要求这些方向也一并推进，不再搁置**——
   如实记录这是用户主动推翻此前"不建议"的结论，不是本轮重新评估
   后改变了判断（详见第 7 节"决定记录"）。

不重复抄写参考文档原文和此前四轮的详细论证，每一节只引用必要的
结论和现状核实，详细对照见文末"参考资料"。

---

## 1. Exploration Mode：批量分支探索（对应参考文档第三十四节第 3
## 小节、第五十、五十一节）【已完成】

**参考文档要求**：用户不应该一直手动控制模拟，第三种模式
Exploration Mode 应该由系统自动把"路线 A/B/C/D"各自铺成一条分支、
一次性生成多个可能世界，回答"如果当时选另一条路会怎样"；"决策的
价值不是改变一个指标，而是选择一个未来世界"。

**代码现状**：`branch_manager.py::fork_branch()` 已经支持"从历史
某一步开一条新分支"，`app.py` 的"从历史节点开一条新分支（回滚
重新选）"折叠区把这个能力暴露给用户，但**每次只能手动开一条**：
选历史节点 → 确认 → 切到新分支 → 再手动选一个候选方向 → 推进。
`problems[].candidate_solutions` 字段（1~3 条候选解决路线的短语）
已经存在，但没有任何机制把这些候选路线自动转成"每条路线一条
分支"批量铺开。`branch_manager.compare_timelines()` 已经支持并排
对比任意多条时间线。

**做法**：

1. `branch_manager.py` 新增 `explore_branches()`：给定
   `sim_id`/`from_step`/一组"路线"（每一项是一个
   `choice_option_id`，或一个 `custom_option` 字典），对每条路线
   依次调用 `fork_branch(from_step=..., switch=False)` 开一条独立
   分支，再对这条新分支调用 `engine.advance()`（传入对应的
   `choice_option_id`/`custom_option`），产出"分支 id → 推进结果/
   错误"的映射。**只做向前一步**，不做多步递归自动探索——分支
   数量会随步数指数增长，超出这一节要解决的问题范围，用户如果想
   让某条探索出来的分支继续往前跑，用已有的 `fast_forward` 机制
   在那条分支上单独操作即可，不在本节重复造轮子。
2. 每条路线的推进**互相独立、互不影响**：某条路线的
   `advance()` 调用失败（LLM 报错/校验失败）不应该影响其它路线的
   探索结果，也不应该留下一条"半成品"分支——失败时应该把已经为
   这条路线创建的分支删除（复用 `delete_branch()`），只保留成功
   探进一步的分支，并在返回结果里明确报告"这几条路线探索成功、
   这几条失败及失败原因"。
3. `app.py` 在候选选项列表旁新增"🧭 批量探索所有候选"入口：默认
   路线来源是当前 `options` 列表（每个候选选项各开一条分支），也
   允许用户从 `candidate_solutions`（如果对应 `problems` 条目有
   声明）里勾选想探索的路线；执行前明确提示"这会消耗 N 次 LLM
   调用"（N = 路线数量），需要用户二次确认才真正触发（同项目一贯
   "自动化操作消耗成本前需要用户确认"的风格，比如
   `fast_forward` 的既有确认交互）。
4. 探索完成后自动把新生成的分支全部加入"对比视图"的选择列表
   （复用 `st.session_state["compare_selection"]` + 已有的
   `compare_timelines()`），用户可以直接在对比视图里查看几条路线
   分别导致了什么样的"世界"。

**范围克制**：不做"自动结束/自动评分选出最优分支"这类判断——
展示对比是给用户看，选择权继续留在用户手中，不引入这个项目一贯
拒绝的"规则引擎"/自动裁决逻辑；不做多步递归自动探索（见上）；
不做"探索出来的分支自动合并回主线"（分支合并是已有的
`merge_branch()`，用户如果决定采纳某条探索分支的结果，用已有机制
手动合并，不在本节新增自动合并规则）。

**风险**：中——涉及一次性发起多次真实 LLM 调用（成本可控但不是
免费的，需要清晰的确认交互）；需要仔细设计"部分路线失败时不留
半成品分支"这条清理逻辑，避免用户看到一堆奇怪的空/坏分支。

**验收**：单元测试覆盖：给定 N 个候选选项 → 产出 N 条独立分支，
各自的 `chosen_option_id`/`chosen_by` 与来源路线一一对应；某条
路线推进失败时，其它路线的分支不受影响、失败路线不留下半成品
分支；候选路线为空时给出明确提示而不是静默不做任何事；
`explore_branches()` 是纯粹在已有 `fork_branch`/`advance`/
`delete_branch` 之上的编排，不重复实现这几个函数已有的逻辑。

**实施记录（2026-09-22）**：已按上述设计完成实施。
`branch_manager.py` 新增 `explore_branches()`/`ExploreRouteResult`；
`app.py`"推进下一步"候选选项列表下新增"🧭 批量探索所有候选"折叠区
（路线来源可选当前候选选项/问题的候选解决方向，执行前二次确认，
完成后自动把成功分支加入对比视图选择列表）。新增
`tests/test_explore_branches.py`（4 个测试：全部成功、部分失败且
不留半成品分支、候选路线为空报错、`custom_option` 路线），加上
原有的全部通过（545 passed）。详见 `PROJECT.md` 对应条目。

---

## 2. 多主体各自的 Desired State（对应参考文档第五十二节）

**参考文档要求**：当模拟规模扩大后，每个人、每个组织、每个国家
的 Desired State 都不同，这些目标可能冲突（企业要利润最大化，
员工要收入+自由，消费者要低价+高质量……），社会本质上可以看成
"大量不同 Desired States 之间的协作、竞争、冲突与妥协"，这也是
制度产生的基础。

**代码现状**：`manifest.settings.desired_state` 是**全局单一**
字段（`conditions`/`constraints`/`assumptions` 三个可选 key，
第九轮批次一，参考文档第十节）；`multi_entity_mode`/
`background_entities` 只解决"叙事/推进时有其它角色参与、背景角色
用简单趋势外推"，`vars.entities` 对引擎而言是不透明的自由 JSON，
没有任何结构化的"每个主体各自的理想状态"概念。

**做法**：

1. `desired_state` 新增一个可选的顶层 key `per_entity`：字典，
   键是主体名字（必须与 `vars.entities` 的键一致，同
   `background_entities` 的既有约定），值是同样结构的
   `{"conditions": [...], "constraints": [...], "assumptions":
   [...]}`——复用已有的三段式结构，不新发明字段。只有
   `multi_entity_mode == True` 时才有意义，`False` 时即使被写入
   也不影响任何提示或展示（避免非多主体模板意外触发新代码路径）。
   全局 `conditions`/`constraints`/`assumptions`（不带 `per_
   entity` 的部分）继续表示"没有细分到具体主体的整体理想状态"，
   两者可以共存，不互斥。
2. `spec_generator._resolve_desired_state_hint()` 扩展：声明了
   `per_entity` 时，除了原有的整体提示外，额外逐主体列出各自的
   理想状态，并附一句"不同主体的理想状态可能互相冲突，这是正常
   的、不代表存在能同时满足所有人的唯一最优解，候选行动可以体现
   这种冲突或权衡，不需要强行让所有主体都满意"。
3. `app.py` 创建向导/详情页"模拟设置"：只有 `multi_entity_mode
   == True` 时才展示"各主体的理想状态"编辑区（每个已声明的
   entity 一个可选的 conditions/constraints/assumptions 文本框，
   格式与现有的全局 `desired_state` 编辑控件一致）。
4. `problems[].blocked_goal`/`candidate_solutions` 不做任何改动
   ——"这个问题挡住了哪个主体的哪个目标"继续靠自由文本描述，不
   强制引用 `per_entity` 的某个 key（同 `desired_state.
   conditions` 一贯"不做精确匹配校验"的取舍）。

**范围克制**：不做主体间目标冲突的自动检测/裁决算法（不做"博弈论
求解器"、不判断"谁的理想状态应该优先"）；不要求每个 `entity`
都填 `per_entity`，缺省的主体视为"没有声明理想状态"，不影响推进；
不做"理想状态随时间自动演化"（同全局 `desired_state` 已有的
"不做自动漂移"取舍一致）。

**风险**：低偏中——是已完成的 2.2 节 `desired_state` 结构的自然
扩展，复用同一套字段读写/展示模式，主要复杂度在"何时展示、如何
与已有全局 `desired_state` 共存"的 UI 设计，不涉及引擎核心推进
逻辑改动。

**验收**：字段透传测试（`per_entity` 缺省/部分声明/全部声明，
`multi_entity_mode` 为 `True`/`False` 两种情况）；`_resolve_
desired_state_hint()` 在 `per_entity` 非空时产出的提示包含每个
主体的理想状态文本；`multi_entity_mode == False` 时 `per_entity`
即使被写入也不出现在提示里，向后兼容。

---

## 3. Reality Renderer 独立分层 + 五层输出框架 + First Possible
## Event（合并处理，对应参考文档第二十八、二十九、四十、四十一节）

> 这三个方向此前在 `world_simulator_problem_capability_gap_plan.md`
> 第 3 节被分别列为"不建议现在做"（原因分别是"改动成本高，等真实
> 观察到 narrative/vars 不一致案例再动手"、"依赖 Capability 对象
> 先落地并验证"）。本轮用户明确要求推进，鉴于三者高度相关（五层
> 框架的第一层就是 Capability，"第一次发生"本质是 Capability 从
> 无到有的特殊时刻），合并成一个最小可行版本一起做，不做参考文档
> 原文设想的"新建一整层内部状态→结构化事件→渲染"的独立计算引擎
> （那个改动面依然很大，合并处理不代表降低了这一点的风险评估，
> 只是三个概念可以复用同一批字段扩展，不需要三次分别改
> `capabilities_gained`）。

**参考文档要求**：
- 现实变化往往不是数字变化，而是"过去做不到的事情第一次变得
  可行"（第一次发生），比第 "Capability +5" 更有现实意义
  （第四十节）。
- 每一个重要变化都可以经过 Capability → Application → Behavior
  → Impact → Structure 五层，这五层可以成为 Reality Renderer 的
  核心框架（第四十一节）。
- 内部数值和人类可读描述应该分层渲染，避免"叙事讲的事和数值变化
  对不上"（第二十八、二十九节）。

**代码现状**：`capabilities_gained[]` 已有 `capability`/
`enables`/`limitations`/`maturity_stage`（第十一轮 2.3 节）四个
字段，`capability` 对应五层框架的第一层，`enables` 部分对应
Application 层（"让哪些事情变得可能"），但没有 Behavior/Impact/
Structure 三层的对应字段；没有任何"这是不是第一次达成"的标注；
`narrative`/`next_vars` 仍然是同一次 LLM 调用一起产出，没有中间
结构化表示；已有 `achievements.py`（固定 6 个游戏化徽章）和"第一次
发生"概念有一定重叠，但不是同一回事（`achievements.py` 是预设
的固定里程碑清单，不是"任意一次真正意义上的历史性突破"）。

**做法**（展示层归类的最小可行版本，不新建独立渲染层/计算引擎）：

1. `capabilities_gained[]` 新增两个可选字段：
   - `first_occurrence`：布尔值（默认 `False`），skill 判断"这是
     不是这次模拟历史上第一次达成"时可以标注（比如"这是这次模拟
     里第一次实现完全自动化生产"）——这是"First Possible Event"
     的最小化实现：不新建独立的 Event Engine，复用已有结构加一个
     标记位，由 LLM 在叙事里体现、声明式，不做任何自动判断/校验
     （同 `problems.status` 等既有字段一贯的取舍）。
   - `behavior_change`：一句话（可选），这个能力具体改变了什么
     行为模式，对应五层框架的 Behavior 层。
   - `structural_impact`：一句话（可选），是否已经观察到组织/
     产业结构层面的变化，对应 Impact/Structure 两层——不拆成两个
     独立字段，收敛成一个，因为叙事层面这两层界限往往很难清晰
     区分（同项目一贯"两段界限不清晰就合并"的取舍，类比第十一轮
     2.3 节把"大规模应用"和"社会常态化"合并成一段 `infrastructure`
     的理由）。
   四个字段（`capability`/`enables`/`behavior_change`/
   `structural_impact`）合起来近似覆盖五层框架的展示需求，仍然
   挂在同一个 `capabilities_gained` 条目下，不做成独立对象、不
   新建数据模型。
2. `app.py` 展示层：
   - `_capabilities_gained_html()` 对 `first_occurrence == True`
     的记录加特殊标记（⭐ 图标 + "首次达成"字样）。
   - 第十一轮 2.3 节新增的"📈 能力成熟度时间线"折叠区里，
     `behavior_change`/`structural_impact` 非空时作为该条能力的
     补充说明展示。
   - 新增一个独立的只读折叠区"⭐ 首次达成的里程碑"：遍历历史汇总
     所有 `first_occurrence == True` 的记录，按出现顺序列出，
     与 `achievements.py` 的固定徽章机制并列展示、互不影响、不
     产生任何联动（两套机制服务的场景不同：徽章是预设的固定
     里程碑，这里是"任意一次真正的历史性突破"）。
3. `workflows/advance_step.yaml`/`world_evolve.yaml` 里
   `capabilities_gained` 相关 prompt 段落补充这两个新字段的判断
   依据，明确"不确定就不填，不要为了凑内容而每步都编造"。

**不做的部分**（明确记录，避免误以为这是"完整版 Reality
Renderer"）：不新建"内部状态 → 结构化事件 → 渲染成叙事"的中间
表示层；`narrative`/`next_vars` 继续在同一次 LLM 调用里一起产出，
不做强制分离渲染；不做"检测 narrative 和 vars 是否对得上"的一致性
校验（这仍然是参考文档想解决但本轮不做的核心问题，只是做了展示层
标注，没有解决"两者可能对不上"这个根本风险）。

**范围克制**：不做自动判断"这是不是第一次"（完全靠 LLM/skill
自己判断，标注错了不做任何纠正机制）；不和 `achievements.py` 的
固定徽章机制合并或产生任何联动。

**风险**：低——复用第十一轮 2.3 节刚建立的 `capabilities_gained`
字段扩展模式，增量字段小，纯展示层，不碰引擎推进逻辑。

**验收**：字段透传测试（三个新字段缺省/部分声明/全部声明）；
"⭐ 首次达成的里程碑"折叠区聚合逻辑测试（覆盖历史里有/无
`first_occurrence` 记录两种情况）；`behavior_change`/
`structural_impact` 缺省时不影响任何已有展示，向后兼容。

---

## 4. 制度/组织/技术类型区分（对应参考文档第五十三节）

> 此前在 `world_simulator_problem_capability_gap_plan.md` 第 3
> 节被评估为"收益不明确，容易变成为了像文档而像文档，不建议现在
> 做"。本轮用户明确要求推进。

**参考文档要求**：法律、组织、市场、合同、货币、企业、社区、
平台都可以理解成"解决特定类型问题的社会技术"，Technology /
Organization / Institution 可以被放到统一的 Capability / Problem
Solving Framework 中。

**代码现状**：现有模板（`life_sim`/`negotiation_template`/
`group_evolution_template`）靠 skill 的 prompt 承载"这是什么类型
的主体"，数据模型层面没有任何 Technology/Organization/
Institution 的类型区分。

**做法**：

1. `capabilities_gained[]` 新增可选字段 `capability_kind`，三选一
   （默认 `"technology"`，不填也是这个值，向后兼容）：
   - `"technology"`：技术类能力（工具、方法、算法等）。
   - `"organization"`：组织类能力（新的分工方式、新岗位、新的
     协作流程等）。
   - `"institution"`：制度类能力（新规则、新合同形式、新市场
     机制、新的激励安排等）。
   这是参考文档"把 Technology/Organization/Institution 放进统一
   框架"这句话的最小落地方式——不新建三个独立的数据类型/数据表，
   只是给已有的 `capabilities_gained` 加一个分类标签，复用同一套
   存储和展示逻辑。
2. `app.py` 能力时间线展示按 `capability_kind` 加对应图标
   （🔧 技术 / 🏢 组织 / 📜 制度），"能力成熟度时间线"折叠区支持
   按类型筛选（可选，纯展示层交互）。
3. `workflows/advance_step.yaml`/`world_evolve.yaml` 补充三个类型
   的判断依据和例子（例如"新的绩效考核制度"是 institution，"新的
   跨部门协作流程"是 organization，"新的生产工具"是 technology）。

**范围克制**：不做"制度是如何形成的"过程建模（"重复问题 → 稳定
解决方案 → 形成制度"这条因果链，属于 Causal Engine 该管的事，
不在这里重复建，第 5 节的"协同演化观察"也只是统计展示，不是这条
因果链的计算实现）；不强制每条能力都归类，不确定就用默认值
`technology`，不强行分类；不做三种类型各自专属的字段扩展（比如
不给 `institution` 单独加"约束力"这类子字段），保持和其它
`capabilities_gained` 条目完全一样的字段集合。

**风险**：低——纯枚举字段扩展 + 展示层分组/筛选，复用已经验证过
的字段扩展模式，不涉及引擎推进逻辑。

**验收**：字段透传/默认值测试（不填时退化为 `"technology"`，
未知取值原样保留还是归一化默认值需要在实现时明确选一种并测试
覆盖）；展示层分组/图标测试。

---

## 5. 技术/组织/制度协同演化（对应参考文档第五十四节）

> 此前在第九轮 1.5 节被评估为"六个方向里最不建议现在投入的一处"，
> 第十一轮沿用该结论列入"不建议现在做"。本轮用户明确要求推进；
> 有了第 4 节 `capability_kind` 分类字段后，这里的最小版本变得
> 可行——不做真正的协同演化计算/推断，只做统计观察。

**参考文档要求**：真实世界经常不是技术单独变化，而是技术、组织、
制度共同演化、互相推动。

**代码现状**：无，依赖第 4 节 `capability_kind` 字段先落地。

**做法**：

1. `app.py` 新增一个纯统计的只读展示函数（复用"能力成熟度时间线"
   已经聚合出的历史数据）：按 step 或一个可配置的"步数窗口"（比如
   相邻 3 步内），检测是否存在 `capability_kind` **不同类型**
   （technology/organization/institution 中至少两种）同时出现
   新增能力记录的情况，命中时标注为"协同演化观察"，列出涉及的
   具体能力和所在步骤，不做任何"谁驱动了谁"的因果判断——纯粹是
   "这里同时出现了跨类型的变化，供你自己判断是否构成协同"这句
   提示。
2. 不新增任何数据字段，完全复用第 4 节 `capability_kind` 的统计
   结果得出，不需要 LLM 额外声明任何东西。

**范围克制**：不做"协同演化模式"的分类/推荐（不判断"这是技术
驱动型协同还是制度驱动型协同"这类归类）；不做自动触发机制/提醒
（只在用户主动展开这个折叠区时计算展示，不主动弹出通知）；窗口
宽度需要谨慎设计默认值，避免"随便挨着的两条记录都被算成协同"的
过度解读——建议默认窗口较窄（比如 2~3 步），并在展示文案里明确
"这只是时间上接近，不代表存在因果关系"。

**风险**：低偏中——统计逻辑本身简单，主要风险在"协同"判定标准
（时间窗口多宽算"同时"）的设计需要谨慎，避免制造虚假的"发现"。

**验收**：统计函数测试（覆盖窗口内有/无跨类型变化两种情况，边界
step 是否计入窗口的测试）；没有任何跨类型变化时不产生任何展示
（不为了"看起来有内容"而制造虚假关联）。

---

## 6. Fact/Assumption/Hypothesis/Prediction 类型标签（对应参考
## 文档第五十九节）

> 此前在第九轮 1.6 节被评估为"优先级低，边际价值有限"。本轮用户
> 明确要求推进；但重新核实代码现状后发现第九轮文档对现状的描述
> **已经过时**，需要先如实更正结论，再给出相应缩小后的最小改动
> （详见下方"现状核实"）。

**参考文档要求**：World Model 应该明确区分 Fact（已发生的事实）、
Assumption（假设）、Hypothesis（有待验证的猜想）、Prediction
（对未来的预判）、Observation（观察），因为现实信息可能错误、
过时、冲突、不完整。

**现状核实（更正第九轮文档的描述）**：第九轮文档描述
`knowledge_base.py::KnowledgeItem.confidence` 是"连续值"——**这个
描述已经不准确**。核实当前代码，`confidence` 实际上**已经是一个
四态离散枚举**：`confirmed`（已证实）/`supported`（有依据支持）/
`hypothesis`（待验证假设）/`speculative`（推测），`validated_
count`/`contradicted_count` 是累计验证/反驳次数计数器，与
`confidence` 是两个独立字段，不是"用一个连续值隐式覆盖类型区分"
的关系。也就是说，参考文档想要的"Fact vs Hypothesis"类型区分，
在因果知识库层面**已经存在**，只是枚举取值的英文命名和参考文档
的 `Fact`/`Hypothesis` 术语不完全一一对应（`confirmed`≈`Fact`、
`hypothesis`≈`Hypothesis`，但没有单独对应 `Assumption`/
`Prediction`/`Observation` 的取值）。

**做法**（在已经大部分被覆盖的基础上，缩小范围做剩余的最小改动）：

1. 纯展示层的中文标签映射（不新增任何数据字段）：`confirmed` →
   "已证实事实"、`supported` → "有依据的判断"、`hypothesis` →
   "待验证假设"、`speculative` → "推测"，用于因果知识库相关界面，
   让用户不需要理解四个英文枚举值的确切含义。
2. 不新增独立的 `Prediction`/`Observation` 类型——现状里
   `causal_graph_hint` 的"历史统计边"（已发生过的观察）和
   `declared_causal_graph` 的"先验声明"（创建时的假设/预判）
   已经是两段分开展示给 LLM 的内容，本质上是 Observation vs
   Assumption/Prediction 的一种朴素区分，重复建一层类型标签
   收益不明确，本轮不做。

**范围克制**：明确不新增数据字段，只做展示层的标签映射；不做
`confidence` 枚举值的重命名（避免破坏已有存量数据的兼容性）。

**风险**：极低——纯字符串映射，不涉及任何数据结构改动。

**验收**：展示函数测试（四个已知枚举值都有对应中文标签；未知/
非法取值兜底展示英文原值本身，不报错）。

---

## 7. 决定记录

**2026-09-22**：第 3～6 节涉及的四个方向，此前分别在
`world_simulator_ninth_round_theory_gap_analysis_plan.md` 1.5/1.6
节和 `world_simulator_problem_capability_gap_plan.md` 第 3 节被
评估为"不建议现在做"（原因分别是"改动成本高，等真实案例出现"、
"依赖其它验证"、"收益不明确"、"边际价值有限"）。用户在本轮明确
要求这四个方向也一并推进，不再搁置。这是**用户主动推翻此前的
"不建议"结论**，不是本轮重新评估后改变了判断——第 6 节额外发现
第九轮文档对 `confidence` 字段现状的描述已经过时，如实更正并
相应缩小了改动范围，这是本轮重新核实代码现状的结果，与"是否
推进这个方向"这个决定本身无关。

---

## 8. 建议的实施顺序

```text
第一优先（新差距，价值明确）：
  第 1 节 Exploration Mode（批量分支探索）—— 直接对应"决策的价值
    是选择一个未来世界"这条核心理念，风险中等但价值最明确
  第 2 节 多主体 Desired State —— 复用已完成的 desired_state 结构，
    风险低偏中

第二优先（合并处理的搁置方向，字段扩展模式已验证）：
  第 3 节 Reality Renderer 最小版 + First Possible Event —— 复用
    capabilities_gained 扩展模式，风险低
  第 4 节 制度/组织/技术类型区分 —— 纯枚举字段，风险低，建议排在
    第 3 节之后（第 5 节依赖它）
  第 5 节 技术/组织/制度协同演化 —— 依赖第 4 节的 capability_kind
    字段，纯统计展示，风险低偏中

第三优先（范围已大幅缩小的搁置方向）：
  第 6 节 Fact/Hypothesis 类型标签 —— 现状核实后发现大部分已被
    覆盖，只剩纯展示层映射，风险极低，可以随手做
```

---

## 9. 参考资料

- `万能模拟器到底如何构建？——从世界状态、问题空间到通用现实
  模拟引擎.md`——本文档所有对照原文的来源，第三十四、四十、
  四十一、五十二、五十三、五十四、五十九节。
- `next_doc/world_simulator_ninth_round_theory_gap_analysis_
  plan.md`——1.5（协同演化）、1.6（Fact/Hypothesis 类型标签）
  两节原始评估。
- `next_doc/world_simulator_problem_capability_gap_plan.md`——
  第 3 节"明确不建议现在做的方向"，Reality Renderer/制度类型
  区分/First Possible Event 三项原始评估。
- `next_doc/world_simulator_eleventh_round_remaining_gaps_
  plan.md`——2.3 节 `capabilities_gained.maturity_stage` 字段
  扩展模式，本文档第 3、4 节复用同一套设计取舍。
- `external_projects/world_simulator/world_simulator/branch_
  manager.py`——`fork_branch()`/`compare_timelines()`，第 1 节
  `explore_branches()` 的实现基础。
- `external_projects/world_simulator/world_simulator/
  knowledge_base.py`——`KnowledgeItem.confidence` 现状核实
  来源，第 6 节结论的依据。
- `external_projects/world_simulator/PROJECT.md`——完整变更
  历史。
