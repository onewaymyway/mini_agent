# world_simulator 改进计划：Problem Discovery Engine 从"手动扫描"
到"引擎自动运行" + 问题空间结构化（第十轮）

> **状态**：批次一（自动触发机制）已实施完成，详见本文档"实施记录"
> 一节及 `PROJECT.md` 对应变更记录；批次二（Problem 结构化字段）、
> 批次三（问题图可视化 + 可选信号触发）待实施。本文档合并两个来源的问题：
> （1）用户直接指出的设计缺陷——"扫描潜在问题"目前需要人工在看板上
> 点按钮，但这本该是模拟引擎在推进过程中自己做的事；
> （2）`next_doc/world_simulator_ninth_round_theory_gap_analysis_
> plan.md` 第 1.1 节梳理出的、对照参考文档《万能模拟器》第六、
> 四十八、四十九节仍未兑现的部分——Problem 缺根因/候选方案/依赖
> 关系，更缺"问题之间的因果图"。
>
> 两者其实是同一个模块（`problem_discovery.py`，第九轮批次三）尚未
> 完整的两个维度：**"什么时候扫"**和**"扫出来的东西够不够结构化"**。
> 本文档把它们放在一起规划，因为批次二（结构化字段）会直接影响批次一
> （自动触发）里"自动扫描的结果该怎么处理"这个设计决策。

---

## 1. 背景：现状到底哪里不对

### 1.1 用户提出的问题：扫描是手动的，但引擎推进不是

`problem_discovery.suggest_problems()` 是一次独立的、按需调用的 LLM
工作流（`workflows/problem_discovery.yaml`），当前**唯一**的调用入口
是 `app.py` 里"🔍 扫描潜在问题"折叠区的一个按钮（`app.py` 3560~3600
行附近）。模块自己的 docstring 也写明了这个取舍：

> "不做自动周期性触发——只在调用方（通常是用户在 UI 上点击'扫描潜在
> 问题'）主动调用时才发起一次 LLM 调用，避免增加不必要的 LLM 调用
> 成本。"

这在**手动挡**场景下勉强说得过去（用户本来就要逐步点"推进"，多点一
个"扫描"按钮不算太突兀），但暴露出两个更严重的问题：

1. **自动挡（autopilot）场景下这个功能完全用不上。**
   `autopilot.run_autopilot_step()`/`run_batch_autopilot()`（
   `autopilot.py`）是设计给"无人值守持续推进"用的——整个自动挡的
   意义就是不需要人守在看板前一步步点。但 Problem Discovery 的唯一
   触发方式是"人在看板上点按钮"，这意味着**自动挡模式下，模拟器
   永远不会系统性扫描问题**，只能依赖 LLM 在 `advance_step` 的
   narrative 里"顺便想到"要不要输出 `problems`。这直接违背了参考
   文档第六节"问题应该成为模拟器的第一等公民"的定位——公民权利
   不该只在有人盯着的时候才生效。
2. **就算在手动挡下，"要不要扫描"这个判断也不该完全外包给用户。**
   用户大概率不知道"这一步是不是该扫一下问题了"——这恰恰是模拟
   引擎该替用户判断的事（类似 `resource_guard.py` 自动检查资源
   下限、`option_heuristics.py` 自动检测可疑选项，都是"引擎主动
   发现问题、不需要用户想起来去触发"的既有模式，Problem Discovery
   目前是这一批"主动发现"机制里唯一一个还停留在"用户手动触发"的
   反例）。

### 1.2 之前梳理出的问题：扫出来的东西不够结构化

即使扫描本身变成自动的，`suggest_problems()` 目前的产出仍然只是
`{"symptom": ..., "blocked_goal": ..., "missing_capabilities": [...]}`
——每条问题互相独立，没有：

- `depends_on`：这个问题要解决，通常需要先解决哪些别的问题；
- `root_causes`：这个问题的根因是什么；
- `candidate_solutions`：有哪些方向性的候选方案（不是完整的
  `ChoiceOption`，只是提示）。

没有这些字段，即使自动扫描每一步都在跑，用户看到的也只是一份不断
增长的"问题清单"，看不出问题之间的结构——而参考文档第四十九节
明确说"一个世界不是只有几个问题，它实际上存在 Problem Space"。**如果
只解决"自动触发"而不解决"结构化"，自动扫描只会让用户更快地被一堆
互相看不出关系的问题淹没**，体验不一定变好。这是两个问题要放在一起
规划的核心原因。

---

## 2. 改进目标

1. Problem Discovery 的**发现**环节不再要求用户记得去点按钮——
   手动挡下引擎按周期自动扫描并把结果摆在用户面前；自动挡下引擎
   自动扫描并自动把确认建议汇入下一步推进的提示，全程不需要人工
   介入也能工作。
2. 扫描出的问题带上 `depends_on`/`root_causes`/`candidate_solutions`
   三个可选字段，`app.py` 能把同一实例历史里出现过的问题用
   `depends_on` 连成一张图（复用阶段三十七第四批已经验证过的
   `st.graphviz_chart()` 方案）。
3. **不改变**项目一贯的核心原则："建议而非强制"——自动扫描出的
   问题仍然不会直接改写任何已落盘的 `SimState.problems`，最终
   "这一步的 `problems` 里到底写什么"仍然由 LLM 在 `advance_step`
   时自行判断，代码只负责让"建议"更及时地出现、更有结构。

---

## 3. 改进方案（分三个批次，建议按顺序做）

### 批次一：自动触发机制（核心，直接回应用户提出的问题）

**新增配置字段**：`manifest.settings.problem_discovery_auto_scan_
interval`（整数，默认 `0` 表示关闭，向后兼容——同项目一贯"新增开关
默认不改变已有行为"的惯例）。用户在"⚙️ 模拟设置"里可以设为比如 `3`，
表示"每推进 3 步自动扫描一次"。

**手动挡（`engine/advance.py::advance()`）**：
- 在 `advance()` 末尾、`next_state` 构造完成之后（复用已有的
  "推进完成后做辅助检查"的位置，类似 `resource_guard`/
  `option_heuristics` 的调用时机），如果
  `problem_discovery_auto_scan_interval > 0` 且
  `next_state.step % interval == 0`，自动调用一次
  `suggest_problems()`，结果存入新字段
  `manifest.settings["last_auto_problem_scan"]`
  （`{"step": ..., "source": (sim_id, branch), "suggestions": {...}}`，
  结构等同于目前 `app.py` 里 `st.session_state["problem_discovery_
  suggestions"]` 的临时结果，只是从"会话状态"变成"落盘设置"，这样
  刷新页面/换设备也能看到上一次自动扫描的结果）。
- `app.py` 详情页在"🔍 扫描潜在问题"折叠区里，如果
  `last_auto_problem_scan` 存在且对应当前 `sim_id`/`branch`/
  `step`，直接展示这份结果（复用现有的建议展示 + "采纳"按钮逻辑，
  不用重新发起 LLM 调用）；用户仍然可以随时手动点"立即扫描一次"
  跳过等待周期，手动按钮**保留**，不是替换关系。
- **LLM 调用成本控制**：自动扫描默认关闭，用户主动设置间隔才会
  产生额外调用；间隔本身就是最简单直接的成本控制手段，不引入更
  复杂的"智能判断该不该扫"逻辑（批次三讨论一个可选的加强方向）。

**自动挡（`autopilot.py::run_autopilot_step()`/`run_batch_
autopilot()`）**：这是本批次真正解决用户核心诉求的地方。

- 同样受 `problem_discovery_auto_scan_interval` 控制，触发时机同
  手动挡（按 `step` 取模）。
- **自动挡下没有人来点"采纳"**，因此建议列表默认**自动**汇入
  `confirmed_problem_suggestions`（不同于手动挡默认只展示、等用户
  点采纳）。这个"自动挡下自动采纳、手动挡下需要用户确认"的差异化
  处理，**不违反"建议而非强制"原则**：`confirmed_problem_
  suggestions` 本身只是下一次推进 prompt 里的一句 hint
  （`{confirmed_problem_suggestions_hint}`，见 `engine/advance.py`
  267 行），下一步 `advance_step` 里的 LLM 仍然有权不采纳、不会
  强制写进 `problems`——真正决定"现实里到底发生了什么问题"的关卡
  没有移动，只是自动挡下把"确认关注"这一步人工操作省略掉了（同
  自动挡本身"由代理按用户设定的原则自主决策"的既有定位一致，
  Problem Discovery 的确认在这里等同于自动挡代理会做的一类决策）。
- 复用现有"`urgency=critical` 触发暂停等待用户介入"的先例
  （`engine.py::advance()` 里 `major_decision` 逻辑）：如果自动
  扫描出的 `observed` 问题非空，**不**强制暂停自动挡（问题本身不是
  "决策选项"，不适用现有的暂停判断标准），只在下一次自动挡运行小结
  里额外提示"本轮自动扫描发现 N 条问题，已计入下一步推进提示"，
  让用户事后查看时知道发生过什么，不打断持续推进这条自动挡的核心
  卖点。

**刻意不做的部分**：
- 不做"每一步都自动扫描"（`interval` 最小值仍然可以设成 1，但不
  提供"无需配置、默认每步都扫"的行为——LLM 调用成本仍然由用户
  显式决定要付多少）。
- 不做"根据剧情复杂度动态调整扫描频率"的智能节奏——留给批次三
  讨论，本批次只做最简单的"固定步数间隔"。

**风险**：中。改动涉及 `engine/advance.py`（推进主循环）和
`autopilot.py`（自动挡循环）两处核心路径，需要保证"关闭时行为完全
不变"这条回归线，建议加充分的"`interval=0`/未设置时不触发任何
额外调用"的测试。

---

### 批次二：Problem 结构化字段（`depends_on`/`root_causes`/
`candidate_solutions`）

**`state_model.py`**：`SimState.problems` 每项新增三个可选字段
（默认缺省，旧数据/未声明时完全兼容）：

- `depends_on`：字符串数组，引用同一实例历史里出现过的其它
  `problems[].id`，表示"这个问题通常需要先解决哪些别的问题"。纯
  声明式，**不做**自动拓扑排序、不做循环依赖校验——同 `causal_
  links`/`relationships` 一贯的"提示而非强制"风格，谁引用了谁完全
  由 LLM 自己判断。
- `root_causes`：字符串数组（1~3 条短语），这个问题的根因是什么。
- `candidate_solutions`：字符串数组（1~3 条短语），可能的解决方向
  提示——**不是** `ChoiceOption`，不会出现在下一步的分支选项里，
  只是给用户看的说明性文字（同 `capabilities_gained.enables` 目前
  的定位一致）。

**`problem_discovery.suggest_problems()`**：`workflows/problem_
discovery.yaml` 的 prompt 相应更新，要求 LLM 在给出 observed/latent
建议时，如果能判断出来，顺带给这三个字段（找不到就留空数组，不强行
编造）；`suggest_problems()` 的返回结构和 `adopt_problem_suggestion()`
的采纳逻辑同步扩展这三个字段的透传。

**刻意不做的部分**：同第九轮理论差距分析文档 1.1 节结论——不做
"问题自动判重/合并"，不做根因的自动推断算法，`depends_on` 引用的
`id` 是否真的存在不做强制校验（`id` 本来就是自由文本，同 `problems.
id` 现有取舍一致）。

**风险**：低。纯字段扩展 + prompt 调整，不改变任何已有校验/落盘
逻辑。

---

### 批次三：问题图可视化 + （可选）信号触发的智能扫描节奏

**问题图可视化**：`app.py` 新增"🕸️ 问题关系图"只读视图（放在"🔍
扫描潜在问题"折叠区旁边或内部新增一个 tab）——把同一实例当前分支
历史里出现过的所有 `problems`（按 `id` 去重、展示最新 `status`）
用 `depends_on` 连边，复用阶段三十七第四批已经验证过的
`_causal_graph_edges_to_dot()` 同款思路（新增一个类似的
`_problem_graph_edges_to_dot()` 纯函数，输入是问题列表，输出 DOT
字符串），用 `st.graphviz_chart()` 渲染。节点上可以展示 `status`
（emerging/active/solved/transformed 用不同颜色区分）。这一步依赖
批次二的 `depends_on` 字段先落地。

**（可选加强）信号触发的扫描节奏**：批次一的"固定步数间隔"是最简单
的方案，但不一定是最有效的——参考文档第四十七、四十八节强调的是
"结构性张力"而不是"到点了就扫一下"。可选的加强方向：在
`resource_guard.py` 检测到某个 `resource_fields` 字段跌破下限、或
`ChoiceOption.urgency == "critical"` 出现时，**额外**触发一次自动
扫描（不替代固定间隔，是"固定间隔"和"信号触发"两者取并集）。这一层
需要先观察批次一上线后固定间隔本身是否已经够用，**不建议**在批次一
验证之前就直接做这一层，避免同时改两个变量导致"扫描到底有没有
用"这个问题更难判断清楚。

**风险**：图可视化部分低（同批次一的既有验证过的模式）；信号触发
部分中——建议放在批次一实际使用一段时间、观察"固定间隔是否已经
够用"之后再决定要不要做，不是本轮的强制交付物。

---

## 4. 建议的实施顺序与验收方式

```text
批次一（自动触发机制）—— 优先级最高，直接解决用户提出的核心问题
  验收：
  - interval=0/未设置时，advance()/run_autopilot_step() 行为与改动前
    完全一致（回归测试）。
  - interval>0 时，手动挡按步数间隔正确触发，结果落盘到
    settings.last_auto_problem_scan，app.py 正确展示且不重复发起
    LLM 调用。
  - 自动挡下按同样间隔触发，结果自动汇入 confirmed_problem_
    suggestions，下一步 advance_step 的 prompt 能看到对应 hint
    （复用已有的 _format_confirmed_problem_suggestions 校验点）。
  - 人工验证（同项目一贯对"真实效果"类改动的要求）：开一个
    life_sim 自动挡实例，设置 interval=3，实际跑 10 步以上，检查
    "自动扫描出的问题是否真的合理""是否明显增加了不必要的 LLM
    调用负担"，不理想可以退回 interval=0 默认关闭，不强行推广。

批次二（结构化字段）—— 第二优先，依赖批次一但可以并行开发
  验收：单元测试覆盖新字段的默认值/序列化往返/旧数据兼容；
  suggest_problems() 的 workflow 集成测试覆盖新字段的解析与截断
  兜底（沿用现有测试风格）。

批次三（问题图 + 可选信号触发）—— 第三优先，图可视化部分改动小
  可以紧跟批次二做；信号触发部分建议观察批次一实际使用效果后再决定。
```

---

## 4.1 实施记录：批次一（自动触发机制）已完成

**完成时间**：2026-09-21。**范围**：与本文档第 3 节"批次一"设计
基本一致，一处实现细节与设计时的设想不同，记录如下：

- **实际实现比原方案设想更省改动面**：原方案设想"手动挡在
  `engine/advance.py::advance()` 里接一次，自动挡在
  `autopilot.py::run_autopilot_step()`/`run_batch_autopilot()`
  里再接一次"。实地实现时发现，`autopilot.py` 的两个入口最终都会
  调用 `engine.advance.advance()`（`chosen_by="autopilot"`），而
  `advance()` 内部本来就有 `effective_chosen_by` 这个变量记录"这一步
  实际生效的选择是不是自动挡代选"——直接复用这个既有信号就能在
  `advance()` 一处判断"该不该自动确认"，不需要在 `autopilot.py` 里
  重复一份触发逻辑，**`autopilot.py` 本次未改动**。
- 新增函数放在 `problem_discovery.py`（`_safe_auto_scan_problems()`），
  没有像 `engine/knowledge.py` 里其它 `_safe_*` 包装那样单独拆
  文件——因为它需要直接调用同模块的 `suggest_problems()`，放在一起
  更符合内聚性，风格上仍然遵循 `_safe_record_causal_links`/
  `_safe_evaluate_reflexivity`"吞异常、不影响主流程"的既有约定。
- `app.py` 的"🔍 扫描潜在问题"折叠区改为：本次会话还没手动点过按钮时，
  优先展示 `settings.last_auto_problem_scan`（如果匹配当前
  `sim_id`/`branch`）里的结果，不重新发起 LLM 调用；手动按钮完全
  保留，用户随时可以点它立即重新扫描一次。

**未变化的部分（与方案一致）**：`problem_discovery_auto_scan_
interval` 默认 0（关闭）；手动挡下只记录、不自动写入
`confirmed_problem_suggestions`，自动挡下才自动写入；扫描结果始终
只是下一次 `advance_step` prompt 里的一句 hint，不强制改写
`problems`。

详细改动清单见 `PROJECT.md` "第十轮批次一" 条目；测试见
`tests/test_problem_discovery.py` 新增的 5 个
`_safe_auto_scan_problems()` 用例。

---

## 5. 与既有设计原则的一致性说明

- **"建议而非强制"没有被打破**：批次一让扫描"自动发生"，但扫描
  结果本身仍然只是 hint，是否体现进 `problems` 仍然由 `advance_
  step` 时的 LLM 决定；自动挡下的"自动采纳"只是省略了人工点击
  "采纳"这一步操作，没有跳过"LLM 最终判断"这道关卡。
- **成本可控、默认不改变行为**：`problem_discovery_auto_scan_
  interval` 默认 `0`，同项目历次新增开关的一贯惯例——旧实例、
  未升级配置的用户不会突然多出 LLM 调用。
- **不引入新的重量级依赖**：问题图复用已经验证过的
  `st.graphviz_chart()` 方案，不新增图计算库。
- **范围边界明确**：不做问题自动判重/合并、不做根因自动推断、不做
  依赖关系的循环检测——这些判断继续留给 LLM/用户，同项目对
  `causal_links`/`relationships`/`capabilities_gained` 等既有结构
  化字段的一贯取舍完全一致。

---

## 6. 参考资料

- 用户在本轮对话中直接提出的问题：Problem Discovery Engine 的扫描
  触发方式应该是引擎自动进行，而不是需要人工手动点按钮。
- `next_doc/world_simulator_ninth_round_theory_gap_analysis_plan.md`
  第 1.1 节（Problem 结构化字段与问题图的差距分析）。
- `world_simulator/problem_discovery.py` 模块 docstring（第九轮
  批次三现状与"范围克制"说明的直接依据）。
- `world_simulator/autopilot.py`（自动挡循环现状，`run_autopilot_
  step`/`run_batch_autopilot`）。
- `world_simulator/engine/advance.py`（`confirmed_problem_
  suggestions_hint` 注入点，267 行附近）。
- `PROJECT.md` 阶段三十七第四批（因果关系图 `st.graphviz_chart()`
  实现，问题图可视化直接复用的既有模式）。
