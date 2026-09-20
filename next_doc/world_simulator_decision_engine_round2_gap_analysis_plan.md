# world_simulator 改进计划（第五轮）：`Action`/`DecisionOpportunity`/
`CausalLine` 精简版对象的补全方向

> **状态**：本文档只做方案设计，尚未开始实施。按用户要求，先把
> 对照参考文档 `万物模拟器如何真正构建——从潜在因果空间到动态决策
> 引擎.md` 发现的进一步改进方向全部列出来，再按批次逐一实施。
> 本文档是第四轮方案（`next_doc/world_simulator_potential_causal_
> space_and_decision_engine_plan.md`，13 条差距 4.1~4.13，含 4.12
> 三步拆分调用）**全部完成之后**（阶段三十三第一~八批，339 passed）
> 的第二轮差距分析，聚焦第四轮遗留的"精简版对象"距离参考文档完整
> 设想还差什么，不重复第四轮已经解决的问题。

---

## 1. 背景：为什么还需要第二轮差距分析

第四轮方案（阶段三十三）已经把参考文档第 3 节列出的 13 条差距
（选项数量、Decision Reason/Action Reason 拆分、紧急度建模、
可干预性下沉、现实行动语义、维持现状基准选项、组合/条件行动、
KeyNode 6 态生命周期、DecisionOpportunity 容器、渐进式展开、
引擎与 LLM 分工、跨线级联提示）全部落地。但第四轮里多条方案在
"方案"小节里明确写了**"最小可行版本"**的取舍——比如 4.10 的
DecisionOpportunity 只做了"摘要级"容器、4.9 的 KeyNode 字段没有
覆盖参考文档列出的全部子字段、4.11 的渐进式展开只做了二值
`expansion_level`标记。这些取舍在当时都是合理的（先验证核心
思路是否有效，再决定要不要补全细节），但也确实构成了"距离参考
文档完整设想还有多远"的真实差距，值得系统性地再过一遍。

另外，第四轮方案完全聚焦在 `advance_step`（推进一步）这条链路，
参考文档第三十节的**初始化流程**、第十五节的**选项过滤/合并**、
第三十五节的**评估标准**这几块，第四轮方案里完全没有涉及。

本文档聚焦以下三类、共 8 条改进方向：

- **数据结构精简版补全**（5.1~5.4）：`Action`（`ChoiceOption`）、
  `DecisionOpportunity`、`CausalLine`、因果图，这四个对象目前都是
  参考文档完整设想的精简版，本节逐一分析该不该补、怎么补。
- **流程/引擎缺口**（5.5~5.7）：初始化阶段的一次性调用问题、选项
  去重的引擎侧兜底、评估标准的自评模块，这三条是第四轮方案完全
  没有覆盖的新方向。
- **生命周期精细化**（5.8）：三层未来空间目前只有二值标记，是否
  需要补第三层"已发生"的显式状态。

## 1.1 已有的相关基础设施（本轮要复用，不重新发明）

- `world_simulator/state_model.py`：`ChoiceOption`/`SimState.
  decision_opportunity`/`SimState.options` 的现有字段定义。
- `world_simulator/causal_tree.py`：`future_tree`/`branches`/
  `expansion_level`/`normalize_future_tree()`/
  `suggest_status_transitions()`。
- `world_simulator/causal_graph.py`：`resolve_causal_graph_hint()`
  （历史 `causal_links` 的只读聚合统计）。
- `world_simulator/trend.py`：基于历史归因的顺势/逆势判断（事后
  统计，不是因果线自身状态）。
- `world_simulator/engine/option_heuristics.py`：现有的选项相关
  启发式校验/警告风格，5.6 可以直接follow这个模块的写法。
- `world_simulator/engine/advance.py::_build_decision_opportunity()`：
  当前 DecisionOpportunity 的精简版构造函数，5.2 在此基础上扩展。

---

## 2. 差距全景（8 条，全部纳入本轮方案）

| 编号 | 差距 | 对应参考文档章节 | 类型 |
| --- | --- | --- | --- |
| 5.1 | `ChoiceOption` 缺前置条件/约束/分层后果/成本字段 | 十六、三十二（Action） | 数据结构 |
| 5.2 | `DecisionOpportunity` 缺批次级 urgency/risk/window/baseline | 十六、三十二 | 数据结构 |
| 5.3 | `CausalLine` 缺实时维护的趋势/当前激活节点字段 | 八 | 数据结构 |
| 5.4 | 因果图仍是历史统计聚合，不是维护中的一等公民结构 | 六 | 数据结构 |
| 5.5 | 创建阶段（`generate_scenario`）仍是单次调用 | 三十 | 流程 |
| 5.6 | 没有选项去重/合并的引擎侧兜底 | 十五 | 流程 |
| 5.7 | 评估标准（8 条）没有对应的自评模块 | 三十五 | 流程 |
| 5.8 | 三层未来空间只有二值 `expansion_level`，没有第三层显式状态 | 二十七 | 生命周期 |

按用户在第四轮已经明确过的原则延续：**不做"节制/暂缓"式取舍**，
下面每一条都给出实际可行的最小方案，不写"建议以后再做"；确实要
省略参考文档完整设想里的某部分，会在对应小节的"不做的部分"里
说明省略了什么、为什么。

---

## 3. 逐条改进方案

### 5.1 `ChoiceOption` 补全前置条件/约束/分层后果/成本字段

**问题**：参考文档第三十二节的 `Action` 对象包含
`prerequisites`（前置条件）、`constraints`（约束）、
`expected_consequences`（短/中/长期后果）、`time_cost`/
`resource_cost`（时间/资源成本），现有 `ChoiceOption` 只有
`risk_level`/`reversibility`/`affected_lines`/`key_uncertainty`/
`action_reason`/`urgency`/`time_window`/`action_type`——能说明
"为什么值得选""选了会怎样（笼统的一句 `description`）"，但没有
结构化的"需要什么前提""会消耗什么""短期/长期分别怎样"。

**方案（最小可行版本：只加最有信息增量的两个可选字段，不做完整
的六个子字段）**：
- `ChoiceOption` 新增两个可选字段：
  ```python
  prerequisites: List[str] = field(default_factory=list)
  """选择这个方向前需要满足的前提条件（一句话短语数组，比如
  "手头有 3 个月生活费缓冲"），不确定或没有明显前提就给空数组。"""

  consequences: Optional[Dict[str, str]] = None
  """{"short_term": "...", "long_term": "..."}——短期/长期分别会
  怎样（各一句话）。不确定的一档可以不填（比如只填 short_term，
  long_term 留空字符串或不出现在 dict 里）；整体为 None 表示这一步
  判断这个字段对当前选项没有额外信息增量（比如 description 已经
  把后果说得很清楚），不强制每个选项都填。"""
  ```
- **不做的部分**：不做 `constraints`（约束）——审视下来它和
  `prerequisites` 语义高度重叠（"需要什么条件"和"受什么约束"
  在自然语言层面很难要求 LLM 稳定区分开，容易变成同一句话填两遍），
  合并进 `prerequisites` 表达即可；不做 `time_cost`/`resource_cost`
  这种量化成本字段——这类字段一旦引入数值，很容易退化成第九节
  明确反对的"内部指标调节"（比如"时间成本：3 个月"这种量化在不同
  模板场景下几乎不可比较，与其伪量化不如让 `description`/
  `consequences.short_term` 里的自然语言描述承担这个信息，比如
  "需要投入几个月时间学习新技能"本身已经包含时间成本信息）。
- **涉及文件**：`state_model.py`（`ChoiceOption.to_dict/from_dict`）、
  三个模板 `SKILL.md`（选项字段规则新增两条可选字段说明）、
  `advance_step.yaml`/`decision_generate.yaml`/`generate_scenario.yaml`
  （prompt 里选项字段列表新增这两条，格式同 4.7/4.9 等既有字段的
  写法：给出定义 + 何时可以不填）、`app.py`（选项卡片展示层，
  `prerequisites` 非空时展示一行"前提"，`consequences` 非空时展示
  "短期/长期"两小行，全部按现有"字段为空就不渲染对应小节"的一贯
  风格处理）。
- **验收点**：`state_model.py` 序列化/反序列化测试（含默认值、
  部分字段缺失、`consequences` 只有 `short_term` 没有 `long_term`
  的形状校验）；`app.py` 展示层的渲染测试（复用现有测试文件里
  "字段为空不渲染"的既有断言模式）。

### 5.2 `DecisionOpportunity` 补全批次级 urgency/risk/opportunity_window/baseline_action

**问题**：现有 `decision_opportunity` 容器（`_build_decision_
opportunity()`）只有 `trigger_line_ids`/`trigger_node_ids`/
`decision_reason`/`context_note`，`urgency`/`risk` 目前只存在于
**单个选项**上，没有"这一批决策机会整体有多紧急"的聚合视角；
`continue_` 前缀的"维持现状"选项（4.6）和 `decision_opportunity`
之间也没有结构化关联，只是命名约定。

**方案（最小可行版本：聚合已有字段计算，不新增 LLM 调用）**：
- `_build_decision_opportunity()` 扩展为：
  ```python
  def _build_decision_opportunity(next_state: SimState) -> Optional[Dict[str, Any]]:
      if not next_state.options:
          return None
      urgency_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
      urgencies = [o.urgency for o in next_state.options if o.urgency]
      risk_rank = {"high": 2, "medium": 1, "low": 0}
      risks = [o.risk_level for o in next_state.options if o.risk_level]
      baseline = next((o.id for o in next_state.options if o.id.startswith("continue_")), None)
      return {
          "trigger_line_ids": sorted(next_state.line_updates.keys()) if next_state.line_updates else [],
          "trigger_node_ids": _collect_trigger_node_ids(next_state.tree_updates),
          "decision_reason": next_state.decision_reason,
          "context_note": "",
          # 5.2 新增：批次级聚合，取这一批 options 里最高的一档，
          # 不是重新判断，是对已有逐选项字段的纯计算聚合。
          "max_urgency": max(urgencies, key=lambda u: urgency_rank.get(u, -1)) if urgencies else None,
          "max_risk": max(risks, key=lambda r: risk_rank.get(r, -1)) if risks else None,
          "baseline_option_id": baseline,
      }
  ```
  `opportunity_window` 不额外新增字段——它在语义上就是"最紧急那个
  选项的 `time_window`"，直接从 `max_urgency` 对应的那个选项上
  读取即可，没有必要在 `decision_opportunity` 里重复存一份（避免
  两处数据不一致的维护负担）。
- **不做的部分**：不做"这一批选项本身也有一个独立于任何单个选项
  的 `risk`/`urgency`"这种语义（参考文档字面上确实是这么设计的），
  因为审视下来"决策机会本身有多紧急"和"最紧急的那个选项有多紧急"
  在实践中几乎总是同一个信息，重新让 LLM 单独判断一次容易变成
  重复劳动且两个数字互相矛盾时无法自洽；这里选择"聚合已有数据"
  而不是"新增一次判断"。
- **涉及文件**：`world_simulator/engine/advance.py`（上面的函数
  改动，纯 Python 聚合逻辑，不改动任何 prompt/workflow）、`app.py`
  （"为什么现在需要决定"小节旁边加一行"整体紧急度：xxx"，
  `max_urgency` 为 `critical` 时复用现有的暂停提示样式）。
- **验收点**：`_build_decision_opportunity()` 的单元测试（构造
  多个不同 `urgency`/`risk_level` 组合的 `options`，断言取到的是
  最高档；`options` 里没有任何选项声明 `urgency` 时 `max_urgency`
  为 `None`；`baseline_option_id` 在有/没有 `continue_` 前缀选项
  时的正确性）。

### 5.3 `CausalLine` 补充"当前趋势"作为线自身状态（不止事后归因）

**问题**：`trend.py` 现在是**事后统计式**的顺势/逆势判断——需要
调用 `attribution.summarize_contributions()` 之后才能算出来，
因果线对象本身没有一个"当前趋势是什么"的直接可读字段。参考文档
第八节把 `trend`/`变化速度` 列为因果线**自身应该维护**的状态。

**方案（最小可行版本：作为 `advance_step`/`world_evolve` 的可选
输出字段，由 LLM 每步判断，不做成引擎侧的量化打分）**：
- `line_updates`（现有字段：`advance_step` 推进时对某条线的更新）
  新增一个可选子字段 `trend`：
  ```text
  {"summary": "...", "trend": "accelerating" | "steady" | "decelerating" | "reversing", "vars": {...}}
  ```
  `trend` 四选一：`accelerating`（这条线在朝当前方向加速变化）、
  `steady`（匀速/平稳延续现有方向）、`decelerating`（这条线的变化
  正在放缓，可能即将转折）、`reversing`（方向已经反转）。不确定
  可以不填这个字段（沿用现有"可选字段，缺省不代表没有信息，只是
  这一步没有明确判断"的一贯风格）。落盘到 `SimState.line_updates[
  line_id]["trend"]`，`app.py` 因果线详情页在现有 `summary` 旁边
  加一个趋势徽章展示最近一次非空的 `trend` 取值。
- **与 `trend.py` 的关系**：两者不是替代关系——`trend.py` 回答
  "这个结果是被什么因果链驱动的、看起来是顺势还是逆势"（归因视角，
  依赖历史数据积累），这里新增的 `trend` 字段回答"这条线自己觉得
  现在处于什么阶段"（自评视角，每步都可以有）。两者可以互相印证
  但不合并成一个字段。
- **不做的部分**：不做"变化速度"的量化数值（比如"每步变化
  0.3"）——这类速度数值在不同因果线之间量纲完全不可比，容易变成
  没有实际用途的伪精确数字；`trend` 这个四档分类已经承载了"变化
  速度"里真正有用的定性信息（加速/匀速/减速/反转）。
- **涉及文件**：`world_evolve.yaml`（因果线更新说明新增这个可选
  子字段）、三个模板 `SKILL.md`（同步更新 `line_updates` 格式
  说明）、`causal_tree.py` 或 `state_model.py`（`trend` 取值的
  归一化：非法值忽略，不报错，同 `expansion_level` 的既有处理
  风格）、`app.py`。
- **验收点**：归一化函数的单元测试（合法四值直接透传、非法值/
  缺失时忽略该字段但不影响其余字段解析）；`app.py` 展示层测试
  （趋势徽章只在有历史 `trend` 记录时渲染）。

### 5.4 因果图从"历史统计聚合"升级为"可声明的稳定关系"

**问题**：`causal_graph.py::resolve_causal_graph_hint()` 每次都是
从头统计历史 `causal_links`，只能反映"过去发生过"，无法表达
"这条线一般来说会影响那条线，即使这次模拟还没有历史数据"这种
**先验的、稳定的结构性认知**（参考文档第六节强调的"因果图"，
不是"历史事件聚合表"）。

**方案（最小可行版本：允许 `generate_scenario` 阶段声明一份初始
因果图，作为 `causal_graph_hint` 的补充输入，不是替代统计聚合）**：
- `manifest.settings` 新增可选字段 `declared_causal_graph`：
  ```python
  declared_causal_graph: List[Dict[str, str]]
  # [{"from_line_id": "tech", "to_line_id": "industry", "note": "技术突破通常先影响行业格局，再传导到具体企业"}]
  ```
  由 `generate_scenario` 在初始化阶段可选产出（因果线之间存在
  明显的、创建时就能判断的先验关系时给出，比如"经济线通常影响
  行业线"这种在任何具体历史事件发生之前就成立的常识性结构）。
- `resolve_causal_graph_hint()` 改为：先渲染 `declared_causal_
  graph` 里的先验关系（标注"先验声明，尚无实际历史印证"），再
  渲染原有的历史统计聚合部分（标注"以下是本次模拟实际发生过的
  因果链统计"）——两部分都展示给 `advance_step`/`world_evolve`，
  由 LLM 自己判断参考权重，不做成"先验优先于统计"或反过来的
  强制规则。
- **不做的部分**：不做"因果图是持久化的、模拟过程中可以增删边"
  的完整图数据库式实现——`declared_causal_graph` 是创建时一次性
  声明、模拟过程中只读不改的静态先验，不支持中途新增/修改边（如果
  确实观察到某条先验关系被证伪，靠"历史统计聚合"那部分自然会
  逐渐反映出更真实的情况，不需要额外的图编辑机制）；这是"先验 +
  统计"的最小组合，不是完整的因果图维护子系统。
- **涉及文件**：`state_model.py`（`declared_causal_graph` 字段
  文档）、`generate_scenario.yaml`（可选输出说明）、三个模板
  `SKILL.md`、`causal_graph.py`（`resolve_causal_graph_hint()`
  改为两段式渲染）。
- **验收点**：`causal_graph.py` 单元测试（有/没有 `declared_
  causal_graph` 时两段式渲染的正确性，字段缺失/为空数组时完全
  不影响原有历史统计部分的输出，向后兼容）。

### 5.5 创建阶段（`generate_scenario`）的单次调用问题

**问题**：`advance_step` 已经在阶段三十三第八批支持拆分成
`world_evolve`+`decision_generate` 两次调用；但 `generate_
scenario.yaml`（初始化阶段：Profile/World Builder + 因果线 +
关键节点 + 触发条件 + 候选行动 + 跨线关系，对应参考文档第三十节
完整流程的七个子步骤）仍然是一次调用产出全部内容，存在同样的
"一次调用身兼多职、顾此失彼"风险，且这条链路涉及的子任务比
`advance_step` 更多（七个子步骤 vs 推进一步的两个子步骤）。

**方案（先给出可行方案，但明确标注为"观察后再决定是否启用"，
不是本轮就默认改变创建流程）**：
- 复用 4.12 第三步已经验证过的模式：新增
  `manifest.settings.split_creation_calls` 开关（默认 `False`），
  为 `True` 时把创建拆成两次调用：
  1. `world_builder.yaml`：只产出 `title`/`summary`/`vars`/
     `entities`（多主体模式）——对应参考文档"Profile/Entity
     Builder + World State Builder"。
  2. `causal_space_builder.yaml`：把第一次的输出喂进去，专门
     产出 `causal_lines`/`future_tree`（含 KeyNode 字段）——
     对应"Causal Line Generator + 关键节点生成 + 触发条件生成 +
     候选行动生成 + 跨因果线关系建立"。
  两次调用的结果合并方式与 `advance.py` 的做法完全一致
  （`{**data_2, **data_1}` 按字段不重叠原则合并）。
- **为什么标注"观察后再决定"而不是直接实施**：`advance_step` 的
  拆分有明确的现实必要性——世界演化和决策生成分属两种不同性质的
  判断（"发生了什么"vs"该不该有决策点"），职责边界清晰；创建
  阶段的七个子步骤边界不如推进阶段清晰（比如"关键节点生成"和
  "候选行动生成"高度耦合，很难干净地分到两次调用而不互相依赖对方
  的中间结果），生硬拆成两次调用可能只是增加了成本，却没有像
  `advance_step` 拆分那样带来清晰的职责收益。建议的做法是：先在
  真实使用中观察 `generate_scenario` 产出的因果线/关键节点质量
  是否确实因为"身兼多职"而下降，如果确认存在这个问题，再启动
  上面的拆分方案；如果初始化阶段的质量问题不明显（很可能如此，
  因为初始化阶段整体信息密度和复杂度低于连续推进多步之后的
  `advance_step`），则不必强行拆分。
- **涉及文件（如果确认要做）**：新增两个 workflow yaml、
  `materialize.py::create_simulation()` 分支逻辑（同
  `advance.py` 的分支写法）、三个模板 `SKILL.md`。
- **验收点（如果确认要做）**：参照 `test_split_decision_calls.py`
  的写法新增 `test_split_creation_calls.py`。

### 5.6 选项去重/合并的引擎侧兜底

**问题**：参考文档第十五节"过滤不可行行动/合并重复行动"这一步
目前完全依赖 LLM 在 prompt 里自觉执行，没有任何引擎侧的兜底
校验——如果 LLM 在一批 `options` 里给出了两个高度相似的选项
（比如"寻找新工作"和"换一份工作"），系统不会有任何提示。

**方案（最小可行版本：纯文本相似度启发式警告，不做语义去重）**：
- `option_heuristics.py` 新增函数：
  ```python
  def compute_option_warnings(options: List[ChoiceOption]) -> List[str]:
      """已有函数（阶段三十三第三批已实现"内部指标调节"等警告，
      见 PROJECT.md）。这里追加一类新警告：同一批 options 里两两
      比较 label，用简单的词汇重叠率（jaccard，按中文分词或字符
      2-gram 均可，不引入 embedding 依赖）算相似度，超过阈值
      （比如 0.6）时给出"选项 A 和选项 B 可能过于相似，考虑合并"
      的警告文本。"""
  ```
- **不做的部分**：不做真正的语义去重（不调用 embedding 模型
  计算语义相似度、不自动合并或删除某个选项）——这类警告只是给
  开发者/用户在质检时参考的信号，不改变 `options` 本身的内容，
  避免"引擎自作主张改写 LLM 输出"这种更高风险的行为；纯文本重叠
  的启发式注定会有漏报（语义相似但用词完全不同）和误报（用词
  相似但语义不同），这是刻意接受的精度取舍，与 `option_
  heuristics.py` 现有其它警告函数"宁可漏报、不做过度推断"的
  一贯风格一致。
- **涉及文件**：`option_heuristics.py`、`app.py`（如果已经有
  统一的警告展示位置就复用，没有就在选项列表上方加一行黄色提示，
  与现有警告展示风格一致）。
- **验收点**：`compute_option_warnings()` 的单元测试（构造明显
  相似的两个 label 断言触发警告；构造完全不同的两个 label 断言
  不触发；只有一个选项时不触发——没有"另一个"可比较）。

### 5.7 评估标准（8 条）的轻量自评模块

**问题**：参考文档第三十五节给出 8 条评价标准（因果一致性/决策
真实性/分支差异性/动态性/可解释性/跨线影响/渐进展开/不确定性
区分），现有 `retrospective.py`/`attribution.py` 主要回答"预测
准不准"，不是按这 8 条标准衡量"这次模拟的决策引擎设计是否真的
在起作用"。

**方案（最小可行版本：可统计的代理指标面板，不做 LLM 打分）**：
- 新增 `world_simulator/quality_signals.py`，提供一个纯函数
  `summarize_quality_signals(history: List[SimState]) -> Dict[str, Any]`，
  基于已有字段做统计（不发起任何新的 LLM 调用，不做主观判断）：
  - **可解释性密度**：有 `decision_reason`/`action_reason` 的
    决策点占全部决策点的比例。
  - **跨线影响密度**：`causal_links` 里带 `source_line_id`（4.4
    节已有字段，标注跨线来源）的条目占全部 `causal_links` 的
    比例。
  - **分支差异性信号**：`options` 数量分布（多选项批次的占比，
    区别于"全程只有单选项/无选项"）。
  - **渐进展开使用率**：`future_tree` 里 `expansion_level:
    expanded` 的分支占全部分支的比例。
  - **不确定性标注覆盖率**：`uncertain_fields` 非空的步数占全部
    步数的比例。
  这五项都是**客观计数统计**，不是"这次模拟质量得几分"这种主观
  打分——展示给用户时明确标注"这是统计代理指标，不是质量评分"，
  避免用户误把统计数字当成"系统自己说这次模拟很好/很差"。
- **不做的部分**：不做"因果一致性""决策真实性"这两条的自动化
  统计——这两条本质上需要理解语义内容才能判断（"这个后果和这个
  行动是否存在合理因果关系"不是靠计数能回答的问题），勉强做一个
  基于关键词匹配的伪指标反而会误导用户，不如老实承认这两条目前
  没有自动化的衡量方式，仍然依赖用户自己阅读 `narrative`/
  `causal_links` 判断；不做打分和排名（比如"这次模拟得 82 分"），
  只做原始统计数字的展示。
- **涉及文件**：新建 `quality_signals.py` + 对应测试；`app.py`
  在模拟总览页新增一个可折叠的"质量信号"小节（默认折叠，避免
  信息过载），展示上面五项统计数字。
- **验收点**：`quality_signals.py` 单元测试（构造已知的 history
  数据，断言五项统计数字计算正确；空 history 不报错，返回全零
  或 `None` 的合理默认值）。

### 5.8 三层未来空间补第三层"已发生"的显式状态

**问题**：`future_tree` 目前只有 `expansion_level: compressed/
expanded` 二值状态，对应参考文档第二十七节"潜在/活跃"两层；
"已发生"这一层目前只体现在 `history`（`store.py` 落盘的状态
序列）和 `tree_updates` 事件流里，分支对象本身没有一个"我已经
被确认发生过"的显式状态可以直接查询（需要遍历历史 `tree_updates`
才能拼出"这个分支最终有没有被确认过"）。

**方案（最小可行版本：给分支加一个只读的派生状态展示，不新增
持久化字段）**：
- 不新增新的持久化字段（避免和 `causal_tree.suggest_status_
  transitions()` 已经维护的 `status` 六态字段——`dormant`/
  `emerging`/`active`/`resolved`/`expired`/`invalidated`——产生
  第二套重复语义：`resolved` 本身已经承担了"已发生/已确认"这个
  含义，参考文档"已发生"这一层实际上已经被现有六态生命周期里的
  `resolved` 覆盖，不需要再造一个新字段）。
- 本节真正要补的是**展示层**：`app.py` 因果线详情页目前展示
  `branches` 时如果没有清晰区分"已 `resolved`（对应'已发生'）"
  和"仍是 `dormant`/`emerging`/`active`（对应'潜在/活跃'）"这两
  类，就在渲染时按 `status` 分组展示（"已发生"分组 vs "仍在
  演化中"分组），让参考文档"三层未来空间"里"已发生历史"这一层
  在界面上有直接对应的可视化位置，而不是需要用户自己去读
  `history` 才能拼凑出来。
- **不做的部分**：不新增数据模型改动——这条差距审视下来更准确的
  定性是"现有六态生命周期已经覆盖了参考文档的三层语义，只是展示层
  没有按这个方式分组"，属于纯展示层的调整，不是数据结构缺口；
  如果 `app.py` 现有展示已经足够清晰（需要先实际查看当前界面
  确认），这一条可能不需要任何代码改动，只需要在文档里明确记录
  "三层未来空间在数据层面已经用六态生命周期覆盖，不是遗留差距"。
- **涉及文件**：`app.py`（如确认需要调整分组展示）。
- **验收点**：先人工核查现有 `app.py` 因果线详情页的实际展示
  效果，确认是否已经足够清晰区分三层；如果需要调整，补展示层
  测试（断言 `resolved` 状态的分支被分到"已发生"分组）。

---

## 4. 建议实施顺序（依赖关系排序，不代表优先级取舍）

1. **第一批**：5.2（DecisionOpportunity 聚合）+ 5.6（选项去重
   警告）——都是纯 Python 聚合/统计逻辑，不需要改任何 prompt/
   workflow，改动风险最低、见效最快，适合先做建立信心。
2. **第二批**：5.1（ChoiceOption 补字段）——需要同步改三个模板
   `SKILL.md` + 三个 workflow yaml（`advance_step`/`world_evolve`/
   `decision_generate`/`generate_scenario`），改动模式和第四轮
   4.7/4.9 等字段级扩展完全一致，风险可控。
3. **第三批**：5.3（因果线 trend 字段）+ 5.4（因果图先验声明）——
   都涉及 `generate_scenario`/`advance_step` 或 `world_evolve` 的
   prompt 扩展，且 5.4 依赖 `generate_scenario` 能产出
   `declared_causal_graph`，适合放在一起做完整链路。
4. **第四批**：5.7（质量信号自评模块）——依赖前面几批已经落地的
   字段（`source_line_id`/`expansion_level`/`uncertain_fields`
   都是已有字段，不依赖新批次，可以随时插入，放在第四批只是因为
   优先级上"锦上添花"性质更明显，不影响推进/决策本身的质量，
   放前面也可以）。
5. **第五批**：5.8（三层未来空间展示层核查/调整）——第一步应该
   是先人工核查现有 `app.py` 展示效果，很可能不需要代码改动，
   放在最后是因为它依赖"确认到底需不需要改"这个前置判断，不适合
   一开始就假定需要改代码。
6. **5.5（创建阶段拆分调用）单独处理**：本条方案本身建议"先观察
   再决定"，不排入固定批次——具体做法见 5.5 小节，等真实使用中
   观察到 `generate_scenario` 确实存在质量问题时再启动。

---

## 5. 风险与验证方式

- **字段数量持续增加的复杂度负担**：`ChoiceOption`/`SimState.
  settings` 已经积累了相当数量的可选字段（详见 `state_model.py`
  里已有的"字段分组索引"），5.1/5.2/5.3 会继续增加——建议每批
  落地后都同步更新对应的字段分组索引，避免文档和代码脱节。
- **5.4（因果图先验声明）可能被 LLM 填得过于笼统**：`declared_
  causal_graph` 这种"先验关系"字段容易变成"经济线影响行业线"
  这种放之四海而皆准的空话，信息量低——建议落地后观察几轮真实
  创建流程的实际产出，如果确实普遍空泛，考虑在 prompt 里要求
  必须结合具体模拟场景给出，而不是给通用常识。
- **5.7 质量信号被误解为"质量评分"的风险**：即使明确标注"这是
  统计代理指标，不是质量评分"，用户仍然可能把数字理解成"分数
  越高越好"——展示文案需要格外小心，建议每项指标旁边都配一句
  说明"这个数字高不代表这次模拟一定更好，只是反映了某个维度的
  信息密度"，避免营造错误的"优化这个数字"的激励。

---

## 6. 与本文档配套的落地形式说明

按用户要求，本文档只做方案设计，不包含代码改动。后续如果确认
按本文档实施，建议延续第四轮的节奏：仍然按第 4 节的批次划分，
每批分别提交代码改动和对应测试，并在 `PROJECT.md` 里按批次登记
阶段编号（衔接现有"完成阶段三十三"之后，下一个可用编号为
**阶段三十四**起）。
