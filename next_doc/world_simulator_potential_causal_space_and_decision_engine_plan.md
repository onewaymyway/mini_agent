# world_simulator 改进计划（第四轮）：从"固定数量选项"到"潜在因果空间 + 动态决策引擎"

> **状态**：第 5 节的**六个固定批次已全部完成并通过全部测试
> （316 passed）**——第一批（4.1+4.5+4.8）、第二批（4.2+4.3+4.6）、
> 第三批（4.4+4.13）、第四批（4.9+4.11）、第五批（4.10）、第六批
> （4.7），详见 `PROJECT.md`"阶段三十三第一批~第六批"条目。**跨批次
> 的 4.12（引擎与 LLM 分工的渐进式重构）尚未开始**，含方案原文建议
> 随第二批/第四批同步做但实际未做的前两步——这是唯一一处和第 5 节
> 建议时间点不一致的地方，已在 PROJECT.md 第六批记录里如实说明。
> 按用户本轮明确要求，**不做"节制/暂缓"式取舍**：下面第 4 节列出的
> 每一条差距都必须给出实际可行的改进方案，不允许写"建议以后再做、
> 本轮不纳入"，只允许在方案内部说明"最小可行版本相比参考文档完整
> 设想省略了什么、为什么"。第 5 节给出建议的实施顺序，但顺序只是
> 依赖关系上的先后，不代表排在后面的条目"可以不做"。
>
> **前置文档**：
> - `next_doc/world_simulator_external_project_plan.md`（原始方案，
>   阶段一~七）
> - `next_doc/world_simulator_universal_world_model_upgrade_plan.md`
>   （第一轮缺口分析，阶段九~十九）
> - `next_doc/world_simulator_toward_universal_simulator_plan.md`
>   （第二轮前半，阶段二十~二十五）
> - `next_doc/world_simulator_universal_simulator_gap_analysis_and_
>   roadmap_v2_plan.md`（第二轮后半，阶段二十七~三十一）
> - `next_doc/world_simulator_realism_transparency_and_retrospective_
>   roadmap_v3_plan.md`（第三轮，阶段三十二~三十五，含 Action Space
>   结构化第一步：`ChoiceOption.risk_level`/`reversibility`/
>   `affected_lines`/`key_uncertainty`）
> - `next_doc/world_simulator_causal_line_future_tree_plan.md`
>   （因果线"未来因果树"，`causal_tree.py`，阶段二十六）
> - `next_doc/world_simulator_agent_preview_and_adaptive_policy_plan.md`
>   （自动挡决策者画像 + Agent Preview，阶段三十二后续~三十四）
>
> **本轮起因**：用户在实际使用中发现 `advance_step` 每一步的候选
> 选项数量是"用户设一个数字、AI 尽量凑够"（`settings.options_count`
> → `option_count_hint` → prompt 里"N 个左右"），与剧情实际发展脱节；
> 同时选项缺少"为什么现在出现""有多紧急"这类结构化信息。第一次分析
> 只覆盖了这两点，用户随后提供了参考文档**《万物模拟器如何真正
> 构建？——从潜在因果空间到动态决策引擎》**（与前三轮参考的《万物/
> 万能模拟器到底如何构建》系列文档同源、聚焦"决策引擎"这一个子
> 话题的加深版本），要求对照这篇文档重新做一次更全面的差距分析，
> 并且这次要求"全部列为要改进内容"，不做取舍。本文档就是这次全面
> 分析和方案设计的产出。

---

## 1. 背景

### 1.1 现状：选项生成是"一次性 LLM 输出的副产品"，不是"决策引擎的结果"

`world_simulator` 当前每一步推进（`workflows/advance_step.yaml`，
`type: skill_agent`）都是**一次 LLM 调用产出全部内容**：新状态变量
`next_vars`、叙事 `narrative`、因果线进展 `line_updates`/
`tree_updates`、以及下一步候选选项 `options`。`engine/advance.py`
只做落盘、分支管理、暂停/恢复这些编排工作，不参与"要不要出现选项、
出现几个、为什么出现"的任何计算——这些完全交给 LLM 在同一次输出里
顺带决定，而"顺带决定几个"这件事又被一个用户手填的静态数字
（`options_count`，默认 4，范围 2~8）绑架了：

```python
# world_simulator/spec_generator.py
raw_count = settings.get("options_count")
options_count = max(1, min(options_count, 8))
"option_count_hint": f"{options_count} 个左右",
```

`skills/life-sim-template/SKILL.md` 里更是直接写着"**候选选项数量
按用户设置来，不要自己拍脑袋**"——这条指令的方向和"选项应该由情境
决定"完全相反。

### 1.2 已有的相关基础设施（本轮要复用，不重新发明）

项目里已经有几块和"决策"相关的机制，本轮方案要在这些基础上扩展，
而不是推倒重来：

- **因果线（`causal_lines`）**：阶段二十二起，每条线有 `id`/`label`/
  `time_granularity`/`advance_every_n_steps`；
- **未来因果树（`causal_tree.py`，阶段二十六）**：每条线一棵
  `future_tree`，2~3 个 `branches`（`id`/`description`/
  `likelihood`/`status`，3 态：`open`/`confirmed`/`pruned`），
  `apply_tree_updates()` 支持推进中印证/排除/新增分支；
- **因果线耦合结构化（`causal_graph.py`，阶段二十七）**：
  `causal_links` 可选带 `relation_type`（`one_way`/`two_way`/
  `indirect`/`feedback_loop`）和 `source_line_id`，`build_causal_
  graph()` 能聚合出"线到线"的邻接关系视图；
- **Action Space 结构化第一步（阶段三十二）**：`ChoiceOption` 已有
  `risk_level`/`reversibility`/`affected_lines`/`key_uncertainty`
  四个可选字段，`app.py::_option_meta_html()` 有一套"未声明字段不
  展示、不伪造默认值"的渲染范式；`autopilot.py` 的 `conditional_
  policies` 已经能按 `risk_level`/`reversibility` 六个枚举值声明
  情境化倾向；
- **`major_decision` + `pause_on_major_decision`**：自动挡遇到 LLM
  判定的"人生重大转折点"时会暂停，等待用户介入。

这些机制单独看都是对的方向，但彼此之间**没有被一条"决策引擎"的主线
串起来**——这正是参考文档指出的核心问题。

---

## 2. 参考文档核心理念

《万物模拟器如何真正构建？——从潜在因果空间到动态决策引擎》的论述
链条可以概括为以下几条原则（原文第三十六节"最终原则"的展开）：

1. **不是预测一个未来，而是建立未来空间**：系统应该维护"可能未来
   空间"（潜在因果空间），而不是每步只吐出一条确定路径；但也**不能
   一次性把完整未来树全部生成**，而应该"预先建立潜在因果空间，在
   模拟过程中逐步激活、展开、剪枝、合并和重构"。
2. **底层是因果图，不是简单的树**：多条因果线之间存在交叉影响
   （"AI 技术 → 行业 ← 经济""职业 ← 技能"），需要图结构而不是
   独立的线。
3. **每条因果线应有自己的潜在未来结构**（`CausalLine` 对象：当前
   状态/趋势/不确定性/关键事件/潜在节点/候选行动/当前激活节点/
   与其它线的关系）。
4. **状态可以量化，决策必须语义化**：内部可以用数值描述世界
   （能力=0.72），但用户面对的必须是现实世界的行动/路线（"转向
   推理模型路线""换工作"），不能是"把技术能力从 0.72 提升到
   0.80"这种对现实没有直接意义的操作。
5. **需要独立的"决策语义层"**：把"现实世界事件 → 决策语义层 →
   现实行动 → 用户选择 → 转换回世界状态"作为标准链路，模拟器负责
   把现实行动转成状态变化，而不是让用户直接操作状态变量。
6. **关键节点应该是"现实事件"，而不是指标阈值**：内部指标可以触发
   节点，但节点本身要有现实语义（"AI 开始能稳定承担中等复杂度的
   软件开发任务"，而不是"指标=0.83"）。
7. **`KeyNode` 数据结构**：`id`/所属因果线/现实语义/触发条件/前置
   节点/影响节点/相关因果线/候选行动/行动后果/时间窗口/紧急程度/
   不确定性/当前状态；状态是一个 6 态生命周期：`Dormant`（潜伏）→
   `Emerging`（正在形成）→ `Active`（已激活）→ `Resolved`（已解决）
   / `Expired`（错过）/ `Invalidated`（因世界变化而失效）。
8. **选项数量绝不能固定**：应该是"监测因果线 → 发现变化 → 判断是否
   形成决策机会 → 寻找真实可干预位置 → 生成 Action Space → 过滤
   不可行行动 → 合并重复行动 → 识别真正不同的分支 → 形成实际选项"
   这条流程的**结果**，不是 UI 参数——没有决策点就不给、只有一个
   有效行动就给一个、有大量理论行动要压缩成"有意义的决策前沿"。
9. **`DecisionOpportunity`（决策机会）是比"选项数组"更大的对象**：
   包含 `Trigger`（触发事件/触发因果线/触发原因）、`Context`（当前
   状态/约束/不确定性）、`Urgency`（紧急程度/时间窗口/截止节点）、
   `Action Space`（若干行动）、`Consequences`（短中长期后果）；
   **选项只是决策机会的一个组成部分**。
10. **必须区分 Decision Reason 和 Action Reason**：前者回答"为什么
    现在需要做决定"（通常是这一批选项共享的背景），后者回答"为什么
    这个具体行动现在值得进入 Action Space"（每个选项各自的理由）。
11. **紧急程度必须独立于风险**：高风险不等于高紧急（"十年后可能被
    AI 替代"可以是 `Risk=High, Urgency=Low`；"明天合同到期"可以是
    `Risk=Medium, Urgency=Very High`）。紧急度综合"距离关键节点的
    时间 + 延迟成本 + 机会窗口是否关闭 + 不可逆程度 + 是否有补救
    机会"判断，分四档并绑定行为：`Low`（继续模拟）/`Medium`（提醒
    用户）/`High`（进入决策节点）/`Critical`（暂停模拟等待决策）。
12. **可干预性（Actionability）**：不是所有重要事件都该变成用户
    选项——"全球经济衰退"普通用户改变不了，系统应沿因果链继续往下
    找（经济→行业→企业→个人职业→个人现金流），把决策点落在用户
    真正能施加有效影响的位置。
13. **"什么都不做"是一种带后果的行动（Baseline），但不能机械
    出现**：应该允许显式的"不干预/继续"选项，代表"允许世界沿当前
    因果趋势发展"，但只有在"不作为"确实是这一步的一个真实后果分支
    时才需要，不是每次都塞一个。
14. **应该支持组合/条件行动**：现实世界很少是单选题（"先获取信息，
    再决定是否转型"），Action Space 应支持单行动/组合行动/连续
    行动/条件行动。
15. **用户选择后因果图必须真正改变**：选择本身是"世界历史中的真实
    因果事件"，会触发跨线传导；核心操作是**剪枝 + 扩展**——选中的
    方向进一步展开新的未来空间，未选中的方向降低优先级/失效。
16. **渐进式展开，避免组合爆炸**：优先展开当前路径 + 高影响/高
    概率/高风险/高价值/用户明确关注的路径，其它路径保持"压缩状态"
    （暂未展开），相关条件变化时再展开。
17. **三层未来空间**：`Potential Future Space`（潜在未来空间，先验
    结构）→ `Active Causal Space`（当前活跃因果空间，正在展开的
    局部）→ `Observed History`（已发生的历史）；预生成结构是**先验
    认知**，不是剧本，会随新信息动态改写。
18. **LLM 与模拟引擎必须明确分工**：引擎负责状态管理/时间推进/
    规则执行/因果关系/约束/数值计算/资源变化/节点状态/分支管理/
    剪枝/事件调度/历史记录/一致性；LLM 负责理解输入/建立世界语义/
    生成潜在节点/生成现实行动候选/解释因果关系/处理开放世界信息/
    发现可能遗漏/扩展因果空间/解释模拟结果。
19. **选项是"从当前世界状态中发现的真实干预空间"，不是"模型为了
    完成交互而创造的文本"**：`State → Causal Change → Real-world
    Event → Intervention Point → Real-world Action Space →
    Simulation Options`，而不是简单的 `State → LLM → Options`。
20. **评价标准不是"预测得准"，而是"决策有意义"**：因果一致性、
    决策真实性、分支差异性、动态性、可解释性、跨线影响、渐进展开、
    不确定性区分，这八条比"预测准确率"更重要。

---

## 3. 现状与参考文档的差距全景（13 条，全部纳入本轮方案）

| 编号 | 差距 | 现状 | 对应参考文档章节 |
|---|---|---|---|
| 4.1 | 选项数量固定 | `options_count` 是目标值而非上限，SKILL.md 明确"按设置来，不要自己拍脑袋" | 十五 |
| 4.2 | 缺少 Decision Reason / Action Reason 分离 | 完全没有"为什么出现这批选项"和"为什么这个选项值得选"的字段 | 十七、十八 |
| 4.3 | 紧急度未建模，且未与风险区分 | 无任何紧急度字段 | 十九、二十 |
| 4.4 | 无"可干预性下沉"约束 | prompt 未要求把宏观事件下沉到用户可执行的位置 | 二十一 |
| 4.5 | 选项可能退化为"指标调节" | 无任何禁止性约束，`SKILL.md` 只要求"方向不同" | 四、九、十一、十二 |
| 4.6 | "不作为"语义模糊 | 只能靠"选项数组为空"表达，无法区分"没有决策点"和"不作为本身有后果" | 二十二 |
| 4.7 | 不支持组合/条件行动 | `ChoiceOption` 是纯原子单选 | 二十三 |
| 4.8 | 三个模板 SKILL.md 与两个 workflow 未反映以上任何新约束 | 见 4.1~4.7 | 全部 |
| 4.9 | 无独立 `KeyNode` 对象/6 态生命周期 | 只有 `future_tree.branches`，3 态 | 十四 |
| 4.10 | 无 `DecisionOpportunity` 一等公民对象 | `SimState.options` 是扁平数组 | 十六 |
| 4.11 | 无三层未来空间/渐进式展开 | 所有因果线一视同仁，均为单层浅树 | 二十六、二十七 |
| 4.12 | 引擎与 LLM 分工不清 | 引擎只做落盘编排，节点状态机/剪枝逻辑全部在 LLM 自由输出里 | 二十九、三十三 |
| 4.13 | 跨线级联只是"记录"，不是"主动计算" | `tree_updates`/`causal_graph` 是事后归并，不参与选项生成前置计算 | 六、七、二十四、二十五 |

下面第 4 节逐条给出可行方案；第 5 节给出考虑到相互依赖关系的实施
顺序建议（不代表排后的可以不做）。

---

## 4. 逐条改进方案

### 4.1 选项数量作为推演结果，而不是 UI 目标值

**问题**：`option_count_hint` 当前语义是"请生成 N 个左右"。

**方案**：
- `spec_generator.py::_resolve_time_and_options_hints()` 里
  `option_count_hint` 改为软上限语义，措辞从"个左右"改为"最多不
  超过 N 个（软上限，用于防止候选列表刷屏，不是目标数量）"；
- `workflows/advance_step.yaml` 的 prompt 按参考文档第十五节的流程
  重写：先判断"这一步是否有因果线走到需要主体做选择的分岔点"，没有
  就给空数组；只有一个有效行动给一个；多个有实质区分度的行动给
  多个；超过软上限优先合并/精简而不是硬砍或强行拆分凑数；
- `skills/life-sim-template/SKILL.md`、`negotiation-template/
  SKILL.md`、`group-evolution-template/SKILL.md` 里"候选选项数量
  按用户设置来，不要自己拍脑袋"这句**必须删除并替换**为上面的
  判断流程说明；
- `app.py` 两处 `st.number_input("每一步候选方向数量", ...)`
  （创建向导 + 运行中设置面板）文案改为"候选方向数量上限（软上限，
  实际数量由情境决定）"。
- **验收点**：新增测试用例，构造"因果线明显平稳、无分岔点"的
  `current_vars_json`，断言（在有真实/模拟 LLM 输出的测试里）不会
  被 prompt 误导成"必须凑够 4 个"；至少要有 prompt 内容断言测试
  （检查新措辞已经写入 yaml/SKILL.md，不含旧的"按设置来"字样）。

### 4.2 拆分 Decision Reason 与 Action Reason

**问题**：之前的方案只提了一个笼统的 `trigger_reason`，混淆了"为什么
现在要做决定"（这一批选项共享）和"为什么这个行动值得列入"（每个
选项独立）。

**方案**：
- `SimState` 新增一个可选顶层字段 `decision_reason: str = ""`——
  这一批选项作为一个整体出现的原因（对应 `DecisionOpportunity.
  Trigger`，见 4.10，本节先只落地字段，不做完整对象包装）；
- `ChoiceOption` 新增 `action_reason: str = ""`——为什么这个具体
  行动值得列入候选（区别于已有的 `description`，`description` 说
  "选了会怎样"，`action_reason` 说"为什么现在值得考虑这个方向"）；
- `advance_step.yaml`/三个模板 `SKILL.md` 的 prompt 里给出参考
  文档第十七、十八节的例子作为正例（"职业转型机会正在形成"→
  触发原因是宏观趋势；"转向 AI 相关岗位"→行动原因是"利用已有能力
  降低转型成本"），要求两者都填，`decision_reason` 允许在没有明显
  多选项分岔、只有单一选项时留空（此时"为什么出现"已经足够简单，
  不强求）。
- **兜底**：`decision_reason`/`action_reason` 缺失时不重试整步
  （沿用项目一贯"宽松兜底"风格），`engine.py` 在解析后对非空
  `options` 数组里缺失 `action_reason` 的项目补一句通用占位文案
  （"未说明具体原因，按情境综合判断"），避免展示层出现空白。

### 4.3 紧急度独立建模：四档 + 时间窗口 + 与暂停机制联动

**问题**：无紧急度字段，风险和紧急度被隐含地混为一谈。

**方案**：
- `ChoiceOption` 新增：
  ```python
  urgency: Optional[str] = None   # low / medium / high / critical
  time_window: str = ""           # 人类可读窗口描述，如"数周内"
  ```
  归一化规则同 `risk_level`：不认识的取值退化为 `"medium"`，`None`
  表示未声明，不伪造默认值。
- prompt 里给出参考文档第二十节的判断依据（距离关键节点的时间/
  延迟成本/机会窗口是否关闭/不可逆程度/是否有补救机会）和四档的
  行为含义；
- **行为联动**（不只是展示 badge）：`engine.py::advance()` 落盘后，
  如果新状态的 `options` 中存在任意一项 `urgency == "critical"`，
  按**等同于 `major_decision = True`** 的方式处理——复用现有
  `autopilot.py` 的 `pause_on_major_decision` 判定路径（不新增
  独立开关，直接在判定 `should_pause` 的地方增加"或存在 critical
  urgency 选项"这一条件），触发暂停等待用户介入；
- `autopilot.py::_CONDITION_IF_VALUES`/`_CONDITION_LABELS` 增加
  `urgency` 的三个可声明取值（`low`/`medium`/`high`，`critical`
  不放进条件策略——它直接触发暂停，不需要"倾向"这种软策略），让
  `conditional_policies` 能写"当某选项紧急程度为 high 时，优先
  选择该选项而不是拖延"；
- `_build_decision_context()` 补一句通用提示："存在 critical 选项
  时模拟会暂停等待用户，你不需要处理；存在 high 选项时请优先处理，
  不要因犹豫不决而放着不选"。
- `app.py::_option_meta_html()` 新增紧急度 badge（`critical` 用
  醒目色、`high` 次醒目、`medium`/`low` 弱化），并展示
  `time_window`（有值才展示）。
- **验收点**：`tests/test_autopilot.py` 新增用例验证"存在 critical
  选项时暂停判定为 True"；`tests/test_state_model.py`（如无则新建）
  验证 `urgency`/`time_window` 的序列化/归一化/兼容旧数据（缺字段
  时默认值正确）。

### 4.4 可干预性下沉（Actionability）

**问题**：完全没有"把不可干预的宏观事件下沉到用户可执行位置"这条
约束。

**方案**：
- `advance_step.yaml`/三个模板 `SKILL.md` prompt 新增一段硬性说明
  （直接引用参考文档第二十一节的例子）：

  > 如果这一步的关键变化本身是主体无法直接干预的宏观/外部事件
  > （行业震荡、宏观经济、竞争对手动作、自然灾害等），**不要把这个
  > 宏观事件本身包装成一个候选选项**——先在 `narrative`/`next_vars`
  > 里如实呈现这个宏观变化，然后沿因果链继续往下想："这件事最终
  > 影响到主体自己能采取行动的地方是什么"，把候选选项落在这个
  > 更下游、主体真正能施加影响的位置（例如"全球经济衰退"不直接
  > 变成选项，但"调整职业风险""增加现金储备"可以）。
- 这是纯语义约束，代码层面无法校验"是否真的做到了下沉"，因此不做
  代码校验，只做 prompt 强指令 + 正反例；
- **可选的辅助校验**（弱信号，不做硬性拦截）：`engine.py` 解析
  `options` 后，如果某个选项的 `label`/`description` 与
  `key_drivers`（本步宏观驱动因素列表）高度重合（简单的字符串
  相似度或关键词重叠即可，不需要语义模型），记录一条 `warnings`
  审计信息（类似现有 `uncertain_fields` 的"仅展示，不阻断"风格），
  提示"这个选项可能是把宏观事件直接包装成了选项，建议检查"——这
  只是辅助人工发现问题的信号，不是强制校验，避免误伤真正合理的
  选项。
- **验收点**：prompt 内容断言测试（新约束文案已写入）；`engine.py`
  的相似度警示逻辑（如果实现）需要单元测试覆盖"明显重合"和"明显
  不重合"两种情况。

### 4.5 选项必须是现实行动语义，不能是内部指标调节

**问题**：无任何约束，理论上 LLM 可能生成"提升技术能力 15%"这种
伪选项。

**方案**：
- `advance_step.yaml`/`generate_scenario.yaml`/三个模板 `SKILL.md`
  统一补一条硬性约束（引用参考文档第九、十一、十二节的正反例，
  按模板类型各给 1~2 组）：

  > 候选选项必须是现实世界里可以理解、可以执行的具体行动、路线或
  > 决策（比如"换工作""转向推理模型路线""提高利率""进入新市场"），
  > **不能是抽象的内部指标调节**（比如"提升职业竞争力 20%""增加
  > 收入 15%""模型能力 +0.1"这种）。内部数值当然会因为选项被选中
  > 而变化，但呈现给用户/写进 `label`/`description` 的必须是行动
  > 语义，不是数值语义。

  `life-sim-template`/`negotiation-template`/`group-evolution-
  template` 三个模板分别对应参考文档第十二节"职业线/财务线"、
  谈判场景的报价/让步行动、群体演化场景的政策/集体行动，各自补
  1~2 组贴合自己模板的正反例（而不是三个模板抄同一组"职业线"
  例子）。
- **辅助校验**（同 4.4，弱信号非强制）：`label`/`description` 里
  出现"提升/增加/降低 + 百分比/数字"这类模式时，记录一条 `warnings`
  审计信息，供人工抽查，不阻断流程（真正的语义判断只能靠 LLM 自身
  质量，代码只能做启发式提醒）。
- **验收点**：prompt 内容断言测试；如实现启发式校验，补对应正则
  匹配的单元测试。

### 4.6 显式的"维持现状/不作为"基准选项语义

**问题**：现状只有"选项数组为空"一种表现形式，无法区分"没有决策
点"和"有决策点、且不作为本身有实质后果"。

**方案**：
- 约定：当且仅当"不作为"是这一步的一个真实后果分支时，`options`
  数组里显式包含一项 `id` 固定前缀 `continue_`（比如
  `continue_status_quo`），并要求 `description` 里说明"维持现状会
  怎样"（对应参考文档第二十二节的 Baseline，"职业风险↑/转型成本↑/
  机会窗口↓"这种走向说明）；
- prompt 里明确：**这不是每一步都要有的机械项**——没有决策点时
  仍然是空数组；有决策点但"不选也是正常default 走向、没有额外
  代价"时也不需要这一项（现有"给空数组表示默认走向"的语义保留）；
  只有"不选这件事本身会带来一个和'选了某个选项'同样值得说明的
  后果"时才需要显式列出；
- `ChoiceOption.from_dict`/`to_dict` 不需要新字段，`id` 前缀约定
  即可识别，`app.py` 展示层可以给 `continue_` 开头的选项一个统一
  的视觉区分（比如放在选项列表最后、加一个"维持现状"的小标签），
  不需要新增数据结构；
- **验收点**：prompt 内容断言测试；`app.py` 展示层的 `continue_`
  前缀识别逻辑补单元测试（如该逻辑被抽成独立函数）。

### 4.7 组合行动/条件行动的最小结构化支持

**问题**：`ChoiceOption` 是纯原子单选，无法表达"先侦察再决定"这类
现实中常见的策略。

**方案（最小可行版本，不做完整的步骤序列结构）**：
- `ChoiceOption` 新增：
  ```python
  action_type: str = "single"   # single / combo / conditional
  ```
  归一化：不认识的取值归一化为 `"single"`。
- **不**新增"子步骤数组"这种更复杂的嵌套结构——`combo`/
  `conditional` 类型的选项，其"组合了什么/条件是什么"直接写在
  `description` 自然语言里（比如"先花一段时间了解行业信息，如果
  确认转型信号明确，再启动跳槽准备"），`action_type` 只是给展示层
  和自动挡一个"这是一个多步/条件性方案"的结构化提示，不做进一步
  拆解。这是相比参考文档第二十三节完整设想（结构化的步骤序列、
  每步独立的触发条件）的明确简化，原因：结构化步骤序列涉及"这个
  选项被选中后，后续几步如何被系统记住并强制执行中间步骤"这一
  整套新的状态机（选项本身要跨越多个 `advance()` 调用生效），
  属于比"给选项加个标签"重得多的工程量，值得等 4.9/4.10（KeyNode/
  DecisionOpportunity）落地、有了更完整的节点生命周期管理之后再
  自然地长出"多步方案"的执行追踪能力，而不是现在单独垒一个和其它
  机制不接轨的临时结构。
- prompt 里说明：`combo`/`conditional` 类型的选项被选中后，
  `advance()` 照常只推进一步，中间的"如果...再..."这种条件判断由
  下一次 `advance_step` 调用时 LLM 根据 `narrative` 里记录的
  "用户选择了先做 A 观察情况"这个既成事实自行判断是否触发后续
  条件，不需要引擎做任何特殊记账——这是当前"最小可行"的做法，
  局限是"条件是否真的被认真检查"完全依赖 LLM 记性/推理质量，没有
  代码层面的强制保证。
- **验收点**：`ChoiceOption.from_dict` 归一化单元测试；prompt
  断言测试。

### 4.8 三个模板 SKILL.md + 两个 workflow yaml 的同步改造

**问题**：4.1~4.7 的所有 prompt 改动都要落到具体文件，且三个模板
不能只简单复制粘贴同一套例子。

**方案**：
- 逐文件改造清单：
  - `workflows/advance_step.yaml`：4.1（数量软上限）、4.2
    （decision_reason/action_reason）、4.3（urgency/time_window）、
    4.4（可干预性下沉）、4.5（现实行动语义）、4.6（continue_ 前缀
    约定）、4.7（action_type）全部涉及，是改动量最大的文件；
  - `workflows/generate_scenario.yaml`：4.5（现实行动语义，初始
    状态的候选选项同样适用）需要同步，4.1~4.4/4.6/4.7 主要发生在
    "推进"阶段，创建阶段的 prompt 视情况精简引用（不需要完整重复
    一遍，可以引用"后续每一步推进时的规则同样适用"）；
  - `skills/life-sim-template/SKILL.md`：删除"按用户设置来，不要
    自己拍脑袋"，替换为 4.1 的判断流程；补 4.5 的职业线/财务线
    正反例（复用参考文档第十二节）；
  - `skills/negotiation-template/SKILL.md`：补谈判场景自己的
    4.5 正反例（如"要价提高 10%"这种数值化让步 vs "提出延长交付
    周期换取价格让步"这种行动化让步）；确认是否也有类似"按设置来"
    的复述，如有同步删除；
  - `skills/group-evolution-template/SKILL.md`：补群体演化场景
    自己的 4.5 正反例（如"群体凝聚力 +10%"vs"发起集体决议/推举
    代表谈判"）；同上检查是否有需要同步删除的旧措辞。
- **执行方式**：本节列出的是改动范围和思路，具体逐句 diff 在方案
  确认后、正式改代码时再产出，避免文档本身过长；实施时每个文件
  改完都跑一遍对应的 prompt 断言测试。
- **验收点**：新增/扩展 `tests/test_spec_and_engine.py`（或专门新建
  一个 `tests/test_decision_engine_prompts.py`）里的 prompt 文案
  断言，覆盖三个模板 + 两个 workflow 的关键新增措辞。

### 4.9 KeyNode 独立对象 + 6 态生命周期

**问题**：现状把"节点"和"因果线的一个未来分支描述"混为一谈
（`future_tree.branches` 只有 3 态：`open`/`confirmed`/`pruned`），
没有参考文档设想的独立、带完整生命周期的节点对象。

**方案（在现有 `causal_tree.py` 基础上扩展，不是另起炉灶）**：
- 把 `future_tree.branches` 的每一项升级为参考文档的 `KeyNode`
  形状，新增字段（在现有 `branch` 字典基础上追加，向后兼容——
  旧数据缺这些字段时按"未声明"处理）：
  ```text
  semantic_event: str        # 现实语义描述（可以复用现有 description）
  trigger_conditions: str    # 触发条件，一句话
  prerequisites: List[str]   # 前置节点 id 列表（同一条线内部，或跨线）
  candidate_actions: List[str]  # 这个节点一旦激活，通常对应哪些候选行动方向
  time_window: str           # 复用 4.3 的时间窗口描述
  urgency: Optional[str]     # 复用 4.3 的四档紧急度
  ```
- **状态机从 3 态扩展为 6 态**：`dormant`（潜伏，默认初始状态，
  对应现有的 `open` 中"暂未显现"的部分）/`emerging`（正在形成）/
  `active`（已激活，对应现有 `open` 中"已经明确摆上台面"的部分）/
  `resolved`（已解决，对应现有 `confirmed`）/`expired`（错过窗口）/
  `invalidated`（因世界变化而失效，对应现有 `pruned`）。
  **兼容策略**：`causal_tree.normalize_future_tree()` 里做一次
  旧值到新值的映射（`open→dormant`、`confirmed→resolved`、
  `pruned→invalidated`），新建的分支默认走 6 态，历史数据不需要
  批量迁移，读取时映射展示即可；
- `causal_tree.set_branch_status()` 支持新的 6 个取值；`app.py`
  的状态图标从"●已印证/○开放/◐已偏离/✕已排除"四态扩展为对应
  6 态的图标（新增"🌱形成中""⌛已错过"两个）；
- **激活判定**：`dormant→emerging→active` 的推进主要还是靠 LLM
  在 `advance_step` 输出的 `tree_updates` 里显式声明（不做成
  engine 侧自动推断的复杂规则引擎，那属于 4.12 更大的分工调整），
  这一节只是把"能表达的状态"从 3 个扩到 6 个，暂不改变"谁来判断
  状态该怎么变"这件事的责任方（仍是 LLM 判断，engine 只做合并
  落盘）。
- **验收点**：`tests/test_causal_tree.py` 扩展旧数据迁移测试
  （`open`/`confirmed`/`pruned` 正确映射到新 6 态某个值）+ 新
  字段的序列化/归一化测试。

### 4.10 DecisionOpportunity 作为一等公民对象

**问题**：`SimState.options` 是扁平数组，没有"这一批选项共享的
决策背景"这个更高层级的容器，选项之间的关系（比如都是因为同一个
`KeyNode` 激活而出现）体现不出来。

**方案（最小可行版本：先做"摘要级"容器，不做完整的独立持久化
子系统）**：
- `SimState` 新增一个可选字段：
  ```python
  decision_opportunity: Optional[Dict[str, Any]] = None
  """本状态这一批 `options` 共享的决策背景（参考文档 Decision
  Opportunity 的精简版）：
  {
    "trigger_line_ids": [...],       # 触发这批选项的因果线 id 列表
    "trigger_node_ids": [...],       # 触发的具体 KeyNode id（如适用，4.9 落地后可用）
    "decision_reason": "...",        # 同 4.2，这里作为唯一存储位置，
                                      # ChoiceOption 上不再重复这个字段，
                                      # 4.2 的方案相应调整为"decision_reason
                                      # 挂在这里，而不是挂在 SimState 顶层"
    "context_note": "...",           # 一句话背景补充（可选）
  }
  为 None 表示这一步没有形成需要特别说明背景的决策机会（比如
  options 为空，或者只是简单延续、不需要额外解释）。
  """
  ```
  **与 4.2 的关系澄清**：4.2 最初设计把 `decision_reason` 放在
  `SimState` 顶层，这里进一步收纳进 `decision_opportunity` 这个
  容器里，理由是这样"触发线/触发节点/决策原因"三者能放在一起，
  比散落成三个平级字段更接近参考文档的对象设计，且给以后（`options`
  真正需要标注"属于哪个 opportunity"时）留了自然的挂载点。
- **不做的部分**：不做成独立的、可以跨多个 `SimState` 追踪"同一个
  决策机会经历了 emerging→active→resolved 全过程"的持久化对象
  （那需要独立 id 空间和跨状态引用，属于更大的模型改动）；这一步
  的 `decision_opportunity` 只是"这一个状态节点的背景说明"，不是
  一个可以被其它状态引用的实体。如果后续验证下来确实需要跨状态
  追踪同一个决策机会的演变，再单独立项做"决策机会 id 化"。
- **验收点**：`state_model.py` 序列化/反序列化测试（含 `None` 默认
  值和字段缺失的向后兼容）；`app.py` 展示层新增一个"为什么现在需要
  决定"的小节，展示 `decision_opportunity.decision_reason`（为空
  则不展示这个小节，不伪造内容）。

### 4.11 三层未来空间与渐进式展开

**问题**：所有因果线目前都是同样浅的单层 2~3 分支树，没有"哪条线
该展开更深、哪条线该保持压缩"的机制，谈不上参考文档的"渐进式展开"。

**方案（最小可行版本：先做"展开深度"的分级标记 + 一条简单的
打分规则，不做完整的三层持久化模型）**：
- `future_tree` 新增一个顶层可选字段：
  ```text
  expansion_level: str  # "compressed" | "expanded"，默认 "compressed"
  ```
  `compressed`（对应参考文档 Potential Future Space）：只保留
  现有的 2~3 个粗粒度分支，不再往下细化；`expanded`（对应 Active
  Causal Space）：允许分支本身再挂一层子分支（`branches[].sub_
  branches`，形状同 `branches`，可选字段，不写就是没有再展开）。
- **打分规则（决定哪条线该 `expanded`）**：在 `advance_step` prompt
  里给出参考文档第二十六节的判断依据，作为 LLM 自行判断的指导
  （不做成 engine 侧的量化打分代码，因为"高影响/高概率/高风险/
  用户关注"这几个维度本质上仍是语义判断，勉强量化成分数反而是
  伪精确）：
  - 这一步的 `line_updates` 实际有进展的线（"当前路径"）；
  - 该线因果分支的 `likelihood` 明显偏高，或者对应的选项
    `risk_level`/`urgency` 偏高（"高影响/高风险"）；
  - 用户在因果线详情页填过 `user_feedback`（"用户明确关注"，
    这个信号已经存在，直接复用）。
  满足以上任一条件的线，允许（不是强制）在 `tree_updates` 里把
  某个分支标为 `expansion_level: expanded` 并给出 `sub_branches`；
  其余线维持 `compressed`，`causal_tree.normalize_future_tree()`
  对未声明的情况默认 `compressed`，完全向后兼容。
- `app.py` 因果线总览页对 `expanded` 的分支多渲染一层缩进展示
  `sub_branches`，`compressed` 的线维持现有展示方式不变。
- **不做的部分**：不做"Observed History"作为独立的第三层数据
  结构——现有的 `history`（`store.py` 落盘的状态序列）本身就是
  已发生历史的完整记录，不需要另建一份；这一节的改动范围严格
  限定在"因果树内部的展开深度标记"，不涉及历史存储层。
- **验收点**：`causal_tree.py` 新增 `expansion_level` 的归一化/
  默认值测试；`sub_branches` 递归结构的形状校验测试（只校验形状，
  不校验内容质量，延续项目一贯风格）。

### 4.12 引擎与 LLM 分工的渐进式重构

**问题**：这是本轮里改动面最大、也最容易做过头的一条——参考文档
理想状态是"引擎主导状态机、LLM 被引擎调用做语义子任务"，现状是
"一次 LLM 调用产出一切，引擎只做落盘编排"。**完全重写成参考文档
的理想架构不现实**（相当于重写整个 `engine/` 目录并改变所有
workflow 的调用方式），但"全部列为要改进内容"意味着即使是这一条，
也要给出一个可以实际执行、分步推进的方案，而不是回避。

**方案（分三个可以独立验收的小步骤，都在本轮方案范围内，允许
分先后实施，但都要落地，不列为"以后再说"）**：

1. **第一步（校验前移）**：把目前"LLM 输出什么就信什么"的几个
   环节，改成"LLM 输出建议值 + engine 做一次结构性校验/归一化"。
   这一部分其实 4.3/4.5/4.9/4.11 的"归一化""启发式警示"已经是这个
   方向的具体实例（`urgency`/`risk_level` 的归一化，4.4/4.5 的
   启发式重合检测），本条的增量是**把这些校验统一收敛到一个新的
   `world_simulator/decision_validation.py` 模块**，而不是分散在
   `state_model.py`/`engine.py` 各处零散判断——为后续第二、三步
   的扩展提供一个明确的"引擎侧决策校验层"入口，职责单一、便于
   继续加规则。
2. **第二步（节点状态推导前移到引擎）**：4.9 的 6 态生命周期目前
   仍然完全靠 LLM 在 `tree_updates` 里显式声明状态变化。第二步是
   给 `causal_tree.py` 增加一个**引擎侧的辅助推导函数**
   `suggest_status_transitions(line, current_step)`——基于
   `advance_every_n_steps`（已有）、`time_window`/`urgency`（4.9
   新增）等结构化字段，计算"这个节点如果超过 time_window 还是
   `dormant`/`emerging`，是不是该自动推进到 `expired`"这类**纯
   规则、不需要语义判断**的状态迁移，作为**建议**附加到
   `causal_lines_hint` 提示里让 LLM 参考确认（不是引擎直接改写
   状态——避免引擎单方面的规则判断和 LLM 叙事对不上），迈出"引擎
   开始主动参与部分状态计算"的第一步，而不是永远被动等待 LLM
   告知。
3. **第三步（尝试拆分"世界演化"与"决策生成"为两次调用）**：这是
   最接近参考文档理想状态、但成本也最高的一步——把当前
   `advance_step.yaml` 一次调用同时产出 `next_vars`/`narrative`/
   `options` 的方式，拆成两个 workflow 步骤：
   - 第一步 `world_evolve`：只产出 `next_vars`/`narrative`/
     `line_updates`/`tree_updates`（世界怎么变化了），**不产出
     `options`**；
   - 第二步 `decision_generate`：把第一步的输出 + 更新后的
     `future_tree`/`decision_validation` 结果作为输入，**专门**
     负责判断"这一步是否形成决策机会、生成什么样的 Action Space"，
     对应参考文档的 `Decision Engine` 这一独立模块。

   **这一步的取舍说明**：拆成两次 LLM 调用会带来明显的成本和延迟
   翻倍（`workflow` 层面两次 `skill_agent` 调用而不是一次），且
   两次调用之间需要把第一次的完整输出原样传给第二次（`vars`/
   `narrative`/`tree_updates` 全部要喂进第二次的 prompt），工程上
   可行但代价不小。**本轮方案的结论是：先只做第一、二步（校验前移
   + 状态推导建议），第三步（拆分两次调用）作为本节方案里明确写出
   的下一步，具体触发条件是**——如果 4.1~4.11 落地后，实测发现
   "一次调用里 LLM 要同时兼顾世界演化和决策生成、顾此失彼"的问题
   依然突出（比如选项质量持续不达预期、`decision_reason`/
   `action_reason` 敷衍了事），再启动第三步的两次调用拆分；如果
   4.1~4.11 已经能明显改善选项质量，则第三步可以继续观察、不急于
   实施。这不是"回避这条差距"，而是给出了一个**带明确触发条件的
   分阶段执行计划**，且已经把第一、二步的具体实现方式定下来并会
   在本轮实施。
- **验收点**：`decision_validation.py` 模块的单元测试（校验规则
  逐条覆盖）；`suggest_status_transitions()` 的单元测试（构造
  超过 `time_window` 仍是 `dormant` 的节点，断言给出"建议
  expired"的提示文本）；第三步暂不要求验收点（进入触发条件观察期）。

### 4.13 跨线级联的主动计算

**问题**：`tree_updates`/`causal_graph.py` 目前是"LLM 说完之后
引擎负责归并落盘"的事后记录机制，不是参考文档设想的"选择 A 之后
主动计算/提示应该级联影响哪些其它线"的主动机制。

**方案**：
- 复用已有的 `causal_graph.build_causal_graph(history)`——它已经
  能从历史 `causal_links` 聚合出"线到线"的邻接关系（`source_line`
  → `target_line`，按 `relation_type` 统计出现次数）。本节的增量
  是**把这个聚合结果反过来喂给下一次 `advance_step` 的 prompt**
  （目前 `build_causal_graph` 只在 `app.py` 展示层被调用，没有
  被喂回 prompt）：
  - `spec_generator.py` 新增 `_resolve_causal_graph_hint()`，在
    `advance` 阶段调用 `causal_graph.build_causal_graph(history)`，
    找出"这一步有进展的线"在历史上关联度最高的下游线（按
    `relation_type` 为 `one_way`/`two_way`/`feedback_loop` 且
    出现次数较高的边），生成一句提示："根据以往记录，`line_A` 的
    变化历史上经常连带影响 `line_B`（`relation_type`: xxx，
    出现 N 次）——如果这一步 `line_A` 有实质进展，请考虑是否也
    需要在 `line_updates`/`tree_updates` 里体现对 `line_B` 的
    连带影响，不代表这次一定会发生，仅作为提示参考"；
  - 这是"用历史统计做提示"的轻量级主动化，不是真正的因果推断
    引擎（不做贝叶斯网络/结构方程这类真正的因果计算，那超出当前
    项目"克制、不做伪精确"的一贯取舍，且需要的数据量在单次模拟
    的历史长度下也不现实）；
- **验收点**：`tests/test_causal_graph.py`（如无则新建）验证
  `_resolve_causal_graph_hint()` 在有/无足够历史数据两种情况下的
  输出（数据不足时返回空字符串，不编造关联）。

---

## 5. 建议实施顺序（依赖关系排序，不代表优先级取舍）

1. **第一批**：4.1（数量软上限）+ 4.5（现实行动语义）+ 4.8（对应的
   prompt/SKILL.md 文件改造）——这三条互相强绑定（都是同一批
   prompt 文件的改动），且是用户最初反馈的核心痛点，收益最直接。
2. **第二批**：4.2（Decision Reason/Action Reason）+ 4.3（紧急度
   四档+暂停联动）+ 4.6（continue_ 前缀约定）——都是 `ChoiceOption`/
   `SimState` 的字段级扩展，互相独立但适合一起做（同一批数据结构
   改动，一次性走完 `from_dict`/`to_dict`/`app.py` 展示的完整链路，
   避免来回改同一批文件三次）。
3. **第三批**：4.4（可干预性下沉）+ 4.13（跨线级联提示）——都是
   "往 prompt 里加一段基于历史/规则的提示"，且 4.13 依赖历史数据
   积累到一定长度才有意义，适合放在有真实模拟数据之后验证效果。
4. **第四批**：4.9（KeyNode 6 态生命周期）+ 4.11（渐进式展开
   expansion_level）——都是对 `causal_tree.py` 的结构扩展，建议
   放在前三批验证过 prompt 改造思路确实有效之后再做，避免同时改
   太多层。
5. **第五批**：4.10（DecisionOpportunity 容器）——依赖 4.2（把
   `decision_reason` 收纳进这个容器）和 4.9（`trigger_node_ids`
   引用 KeyNode id）已经落地，放在最后做整合最省事。
6. **第六批**：4.7（action_type 组合/条件行动标记）——本身独立
   性最强，随时可以插入到任意一批之间，放在最后只是因为优先级
   相对最低（现实使用中"单一选项"仍是大多数场景）。
7. **第十二条（4.12）跨越多个批次**：第一步（校验前移到
   `decision_validation.py`）应该跟第二批一起做（正好是把第二批
   新增的归一化规则收敛进去的时机）；第二步（状态推导建议）应该
   跟第四批一起做（依赖 4.9 的 `time_window`/`urgency` 字段）；
   第三步（两次调用拆分）按 4.12 节所述，是带触发条件的观察项，
   不排入固定批次。

---

## 6. 风险与验证方式

- **prompt 膨胀风险**：4.1~4.7 全部落地后，`advance_step.yaml` 的
  prompt 会显著变长，需要关注 token 成本和 LLM 遵循度是否下降——
  建议每批实施后都用真实/接近真实的场景跑几次人工检查（参考项目
  一贯做法，如 4.3/4.5 roadmap v3 文档里"本该先人工跑几次真实模拟
  确认效果"的教训），不能只靠单元测试断言"prompt 文案写对了"就
  认为万事大吉。
- **新字段被 LLM 敷衍填写的风险**：`action_reason`/`decision_
  reason` 这类新增的"解释性"字段，最容易出现"为了满足格式要求
  随便写一句空话"的情况（类似现有 `referenced_policy` 已经暴露过
  的"综合判断，没有明确依据"这种诚实但信息量低的兜底）——这是
  语义质量问题，代码层面无法根治，只能通过实测持续观察、必要时
  调整 prompt 措辞或增加更具体的例子。
- **6 态生命周期/渐进式展开可能"没人用"的风险**：4.9/4.11 增加的
  新能力（`sub_branches`/6 态状态）如果 LLM 实际很少用上（还是
  倾向于给最简单的浅层输出），功能会变成"摆设"——建议这两条落地
  后先观察几轮真实模拟的实际输出分布，如果确实很少被用到，再
  考虑是否要在 prompt 里加更强的引导，而不是急着继续往这个方向
  加更多结构。

---

## 7. 与本文档配套的落地形式说明

按用户要求，本文档只做方案设计，不包含代码改动。后续如果确认
按本文档实施，建议仍然按第 5 节的批次划分，每批分别提交代码改动
和对应测试，并在 `PROJECT.md` 里按批次登记阶段编号（衔接现有
"完成阶段三十二"之后，下一个可用编号为**阶段三十三**起），保持
项目一贯的"一个批次一次交付记录"的节奏，而不是一次性把 13 条全部
揉进一次巨大的改动提交。
