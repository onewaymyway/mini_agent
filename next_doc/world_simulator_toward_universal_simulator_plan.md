# world_simulator 迈向"万能模拟器"理想架构改进计划

> **状态**：阶段二十（4.12 节，Causal Knowledge Base）、阶段二十二
> （4.13 节，多尺度因果线）已完成，见 `PROJECT.md` 对应交付记录；
> 其余四个方向（阶段二十一、二十三、二十四、二十五）规划中，尚未
> 开始实施。
> **前置文档**：`next_doc/world_simulator_external_project_plan.md`（原始方案，
> 阶段一~七）、`next_doc/world_simulator_universal_world_model_upgrade_plan.md`
> （阶段九~十九，已把"参考方案"里资源约束/不确定性标注/Problem
> Compiler/Evidence Chain/Belief 与 State 分离/Hierarchical Agent/
> Reality Sync 轻量版这几块基本落地）。
> **方针调整说明**：前一份文档（升级计划）通篇采用"条件触发 + 最小
> 可行版本"的节制策略——没有真实使用场景验证价值之前不做大改动。
> **本文档是一次明确的方针调整**：用户要求不再等待触发条件，而是
> 按照用户提供的参考文档《万能模拟器：从多层因果世界到自我进化的
> 通用模拟系统》里描述的理想目标，主动把当前项目里"确实存在、且
> 参考文档反复强调"的结构性缺口补齐。这意味着本文档列出的方向
> **直接进入路线图，不再需要额外的触发条件才能启动**；但"直接做"
> 不等于"不评估代价"——每一节仍然如实标注对现有数据结构/推进流程
> 的改动范围和风险，供实施时参考，只是不再作为"是否启动"的前置
> 判断依据。

---

## 1. 背景：当前项目相对理想目标的结构性缺口

在完成阶段九~十九之后，`world_simulator` 已经覆盖了参考文档里
"让单次模拟更靠谱"的大部分具体机制：资源下限校验、资源转移关系
一致性检查、关键变量可信度标注、目标驱动的对比排序、结构化因果
摘要（`key_drivers`/`causal_links`）、多主体 Belief/State 分离、
现实校准轻量版、分层认知路由设计草案。

但逐条对照参考文档的核心论点后，有六个方向是"参考文档反复强调、
现状明确没有、且不是加一两个字段就能补上"的**系统级**缺口，而不是
局部的字段增强：

1. **因果知识完全不跨模拟复用**——每个 `sim_id` 相互孤立，没有
   `Causal Knowledge Base`；参考文档原则十二"每次模拟都应该让系统
   变得更强"完全没有承接对象。
2. **没有系统自动识别关键不确定性并生成多世界假设**——现有"对比
   实验"永远是用户手选方案，不存在 `Hypothesis Engine` 主动判断
   "这个变量的不确定性值得单独分叉"。
3. **因果线是隐式自由文本，没有多尺度并行时间**——`time_granularity`
   是全局单一值，不支持"技术线按年、谈判线按轮次"同时独立演化。
4. **模型结构在推进过程中不能变**——`vars` schema 基本在创建时定死，
   没有 `Model Regime Detection`/`Emergence` 机制去发现并固化模拟
   中途冒出的新实体/新机制。
5. **Reality Sync 只是静态校准文案，不是真正的反馈闭环**——没有
   "模拟结果 vs 后续真实发生的事"的对比记录，无法沉淀"这次模型准
   不准"。
6. **UI 上因果线不是一等公民**——用户看到的是逐步骤的状态卡片
   时间线，看不到"技术线/经济线/个人线"这种跨节点聚合的走势视图。

本文档把这六个方向拆成可以顺序实施的具体改动，编号延续升级计划
文档的阶段序号，从**阶段二十**开始。

## 2. 目标

不是把参考文档的完整架构（Problem Analyzer → Causal Compiler →
Causal Field → Multiple Worlds → Scenario Generator → ... →
Reality Feedback → Causal Knowledge Base 的完整闭环）一次性照搬，
而是按依赖关系把这六个方向逐一实现到"结构成立、端到端可跑通"的
程度，让 `world_simulator` 从"结构化叙事推演器"真正往参考文档定义
的"能够不断构建和运行世界的系统"靠拢。六个方向全部落地后，
`world_simulator` 应该具备：

- 模拟结果不再是"用完即丢"，而是持续沉淀为下次模拟可以复用的
  因果知识；
- 面对不确定性大的变量，系统主动给出"稳健结果"和"关键分叉点"，
  而不是只有用户手动对比的几条时间线；
- 一次模拟里可以有多条节奏不同、但相互耦合的因果线，而不是被迫
  用同一个时间粒度描述所有变化；
- 模拟中途出现的新结构（新实体、新机制）能被系统发现并正式纳入
  后续推进，而不是悄悄躺在自由 JSON 里没人管；
- 模拟与现实之间形成真正的反馈——不只是"校准文案"，而是"记录预测
  、对比结果、更新知识"的闭环；
- 用户在界面上能直接看到"哪几条因果线在主导这次模拟"，而不是只能
  逐步骤翻状态卡片。

## 3. 六个方向的依赖关系与实施顺序

```text
阶段二十（因果知识库）─────────────┐
                                    ↓
阶段二十二（多尺度因果线）──→ 阶段二十三（Model Regime / Emergence）
        │                              │
        ↓                              ↓
阶段二十五（因果线 UI）        阶段二十一（Hypothesis Engine / 多世界）
                                        │
                                        ↓
                              阶段二十四（Reality Loop 完整版）
```

- **阶段二十（因果知识库）** 不依赖任何现有大改动，是六个方向里
  风险最低、投入产出比最高的一个，排在最前面；同时它是阶段二十四
  （Reality Loop）反馈信息的最终落点，必须先有它，后面的反馈闭环
  才有地方可写。
- **阶段二十二（多尺度因果线）** 是数据结构层面的基础改动——把
  "一条隐式时间线"变成"多条显式因果线"，阶段二十三（模型结构在线
  修正）和阶段二十五（因果线 UI）都建立在这个结构之上，必须先做。
- **阶段二十三（Model Regime / Emergence）** 依赖阶段二十二提供的
  "因果线"作为"新机制应该挂在哪条线上"的锚点。
- **阶段二十一（Hypothesis Engine / 多世界）** 主要复用现有
  `branch_manager` 的分支能力，与阶段二十二没有强依赖，但生成的
  假设世界如果能挂载到具体因果线上会更有意义，因此排在阶段二十二
  之后。
- **阶段二十四（Reality Loop 完整版）** 依赖阶段二十（知识库）
  作为反馈的沉淀对象，也依赖阶段二十一（多世界）产出的"稳健结果"
  作为"要拿哪个预测去对照现实"的候选，排在最后。

---

## 4. 改进方案

### 4.12（P0，阶段二十，已完成，见 `PROJECT.md` 对应交付记录）Causal Knowledge Base：跨模拟复用的因果知识库

**问题**：`spec_generator.generate_scenario()`/`engine.advance()` 每次
调用都是"干净上下文"——即使系统之前已经跑过大量"AI 成本下降 →
采用率提高"这类结构相似的因果模式，也完全不会被下一次生成/推进
复用。`key_drivers`/`causal_links` 落盘后只服务于本实例的时间线
展示，没有任何机制把它们提炼成可检索、可复用的知识。

**方案**：

1. 新增一个独立于任何 `sim_id` 的全局存储：
   `data/_knowledge/causal_knowledge.jsonl`（复用现有
   `atomic_write_jsonl` 的追加写模式，不需要新的落盘机制）。每条
   记录对应参考文档第十九节描述的最小字段集：

   ```text
   KnowledgeItem
   ├── id
   ├── cause          # 一句话原因（自由文本，便于检索）
   ├── effect         # 一句话结果
   ├── mechanism      # 为什么成立（可选，一两句话）
   ├── confidence     # confirmed / supported / hypothesis / speculative
   ├── source_sim_id  # 提炼自哪个实例
   ├── source_template
   ├── created_at
   ├── validated_count   # 后续被其它模拟印证的次数
   └── contradicted_count # 后续被其它模拟证伪的次数
   ```

   不做参考文档里更复杂的 `Evidence`/`Valid Range`/`Version` 全字段
   （那属于成熟阶段的知识治理，本阶段先验证"存不存得下、检不检索
   得到、有没有用"这个最基本的闭环）。

2. **写入时机**：`engine.advance()` 落盘 `next_state` 时，如果
   `causal_links` 非空，把每一条 `{driver, affected_fields, effect}`
   转成一条 `KnowledgeItem`（`confidence` 固定为 `hypothesis`——
   单次模拟里出现的因果链，在跨实例验证之前不能算 `confirmed`），
   追加写入知识库。这一步是纯旁路操作，不影响 `advance()` 的返回值
   和现有调用方，失败也不应该让本次推进失败（参考
   `mini_agent.utils.atomic_write` 的写入本身已经是原子的，只需要
   在外层加 `try/except` 兜底，记日志但不抛出）。

3. **读取时机**：`create_simulation()`（`spec_generator.py`）生成
   初始状态之前，先按"用户意图关键词 + 模板类型"做一次简单的关键词
   匹配（不引入向量检索，避免为一个尚未验证价值的功能预先引入
   embedding 依赖），取匹配度最高的若干条 `KnowledgeItem`，拼进
   `generate_scenario` 的 prompt 作为"已知的相关因果模式，供参考"
   一段。`advance_step` 同理，在 `decision_context`/`inputs` 里追加
   一段"相关已知因果知识"。

4. **知识晋升的最小版本**：不做参考文档第四十六节完整的
   `Candidate → Experimental → Repeated Simulation → Validated`
   流水线，只做最简单的计数——如果同一条（或高度相似的）因果关系
   在不同 `sim_id` 里反复出现，`validated_count` 递增；如果某次模拟
   的 `resource_violations`/后续走势明显与某条知识矛盾（比如知识说
   "A 提高必然导致 B 提高"，但某次模拟里 A 提高后 B 反而下降），
   `contradicted_count` 递增。检索时优先展示 `validated_count` 高、
   `contradicted_count` 低的条目。判断"是否相似"用简单的字符串/
   关键词重叠即可，不引入语义相似度模型。

**范围克制**：不做完整的 `Cause/Effect/Mechanism/Direction/
Strength/Delay/Condition/Confidence/Evidence/Context/Valid Range/
Version` 全字段（参考文档第十九节列出的完整 schema）；不做知识库的
管理界面（增删改查 UI），第一阶段只验证"自动写入 + 自动检索
拼入 prompt"这条最基本的闭环是否真的能提升生成质量，界面留到
验证完之后再决定是否需要。

**涉及文件**：新增 `world_simulator/knowledge_base.py`（读写
`causal_knowledge.jsonl`、简单关键词检索、`validated_count`/
`contradicted_count` 更新逻辑）；`engine.py`（`advance()` 落盘后
调用知识库写入）；`spec_generator.py`（`create_simulation()` 生成
前调用知识库检索，拼入 prompt 输入）；两个模板的
`generate_scenario.yaml`/`advance_step.yaml` 增加"相关已知因果
知识"占位符说明。

**验收标准**：连续创建 3 个主题相似的模拟实例（比如都是"创业相关"），
第 3 个实例的 `generate_scenario` 调用能在 prompt 输入里看到前两次
模拟沉淀的 `KnowledgeItem`；`causal_knowledge.jsonl` 里能看到
`validated_count` 随相似因果关系反复出现而递增。

**优先级与依赖**：P0，无前置依赖，建议最先启动。

---

### 4.13（P1，阶段二十二，已完成，见 `PROJECT.md` 对应交付记录）多尺度因果线：Causal Line 成为一等公民

**问题**：`SimState.time_granularity` 是整个模拟状态的单一字段——
"这一步"要么整体按年推进，要么整体按轮次推进，无法表达"技术线按年
演化、谈判线按轮次推进"这种参考文档第七节强调的"不同因果线速度
完全不同、必须多尺度并行"的场景。`causal_links` 也只是挂在单个
状态节点上的 flat 列表，没有"这条因果线过去几步整体在往哪个方向走"
的结构。

**方案**：

1. 新增可选的 `SimManifest.settings.causal_lines`：一个因果线声明
   列表，每条形如 `{"id": "tech_line", "label": "技术线",
   "time_granularity": "年"}`。不声明（默认空列表）时行为与现在
   完全一致——沿用单一 `time_granularity`，向后兼容。

2. `SimState` 新增可选字段 `line_updates`：
   `Dict[str, Dict[str, Any]]`，key 是 `causal_lines` 里声明的
   `id`，value 形如 `{"time_label": "第 3 年", "summary": "AI 成本
   持续下降", "advanced": true}`——记录**这一步**里哪些因果线实际
   往前推进了、推进到了什么时间点，哪些因果线这一步没有变化
   （`advanced: false`，比如"经济线"这一步不需要更新）。`advance_step`
   阶段 skill 按 `causal_lines` 声明，自行判断这一步哪些线需要推进、
   推进到哪，engine 只做落盘，不做任何调度决策（延续"LLM 负责推理，
   engine 负责编排"的既有分工）。

3. `causal_links` 增加可选字段 `line_id`，把已有的"划重点"结构化
   因果链关联到具体因果线上（不填则视为"未归属到具体线，按旧行为
   展示"，向后兼容）。

4. 不引入"因果线之间的调度器"（比如"技术线每 5 步才触发一次"这种
   强制节奏控制）——由 LLM 在每次 `advance_step` 时自行判断"这一步
   该更新哪些线"，engine 不做强约束，避免把参考文档里"上层模型变化
   缓慢，下层模型变化快速"这句定性描述，过早工程化成一套复杂的
   调度规则。

**范围克制**：不做因果线之间"严格互锁的调度依赖"（比如"经济线必须
等技术线推进 3 步才能推进 1 步"这种精确耦合规则），只做"允许不同
线独立记录各自的推进节奏"这个最基本的数据结构支持；因果线之间的
真实耦合关系仍然靠 LLM 在 `narrative`/`causal_links` 里自由描述，
不引入正式的耦合规则引擎（那是参考文档"通用规则引擎 DSL"的范畴，
阶段十六已经在资源转移关系上验证过"最小可行版本"的做法，因果线
耦合如果后续需要，应该复用同样的克制思路单独立项，不在本节展开）。

**涉及文件**：`state_model.py`（`SimState.line_updates`、
`causal_links.line_id`）、`spec_generator.py`（`ScenarioDraft`
新增 `causal_lines` 承接 skill 建议值）、两个模板 `SKILL.md`/
`advance_step.yaml`（新增"多因果线"可选输出说明）、`app.py`（时间线
展示新增"按因果线筛选"的视图切换，为阶段二十五的因果线 UI 打基础）。

**验收标准**：创建一个声明了 `causal_lines: [{"id": "tech",
"label": "技术线", "time_granularity": "年"}, {"id": "negotiation",
"label": "谈判线", "time_granularity": "轮"}]` 的实例，推进几步后
能在 `line_updates` 里看到两条线各自独立的 `time_label` 序列
（比如技术线走到"第 2 年"，谈判线走到"第 5 轮"），互不干扰。

**优先级与依赖**：P1，依赖阶段二十（知识库）不是必需，但建议在
阶段二十之后启动，理由是两者都涉及 `spec_generator`/`advance_step`
的 prompt 输入结构调整，合并到相近的时间窗口里改动，减少两次
分别修改同一批 prompt 模板的重复成本。

---

### 4.14（P1，阶段二十三）Model Regime Detection / Emergence：允许模型结构在线修正

**问题**：`vars` 的字段结构基本在 `spec_generator.create_simulation()`
生成初始状态时就固定下来。`multi_entity_mode` 允许多个 entity，但
"模拟中途出现一个原来 schema 里没有的新实体/新机制"（参考文档
第四十一节"涌现"、第四十二节"Model Regime"）目前只能靠 LLM 在
`vars` 自由 JSON 里硬塞新字段，engine 完全不知情，也没有任何机制
判断"这个新结构要不要正式固化下来，影响后续推进"。

**方案**：

1. `advance_step` 阶段新增可选输出字段 `structural_change`：

   ```text
   {
     "detected": true,
     "kind": "new_entity" | "new_mechanism" | "regime_shift",
     "description": "谈判各方之间形成了稳定的联盟结构",
     "proposed_fields": {"alliance": {"members": [...], "rules": "..."}}
   }
   ```

   skill 在判断"这一步出现了原模型没有预期的稳定新结构"时才输出
   （大多数步骤为空，不强制每次都判断，减少无谓的输出负担，延续
   `granularity_changed`/`resource_violations` 这类"只在发生时才
   出现"字段的既有设计取舍）。

2. `engine.advance()` 接到非空 `structural_change` 时**不自动接受**
   ——落盘到 `SimState.structural_change`（新增字段），但不修改
   `manifest.settings` 里的任何 schema 声明。是否要把这个新结构
   正式固化（比如追加进 `multi_entity_mode` 的 entity 列表、或者
   追加一条 `causal_lines` 声明），交给用户在详情页里手动确认——
   这是参考文档"模型不是模拟结束后才修改，而是持续进化"的落地
   方式里最保守的一步：**先让系统"发现并展示"，不让系统自主"决定
   并改写"自己的结构**，避免一个判断失误的 LLM 输出直接污染
   `manifest.settings`。

3. `PROJECT.md`/本文档里记录清楚：**自动接受新结构**（不经用户
   确认、系统自己判断该固化就固化）是一个更进一步、风险明显更高
   的方向，本阶段不做，如果后续人工确认环节验证下来"用户几乎每次
   都会接受系统的建议"，再考虑要不要收紧成自动化。

4. Model Regime（世界运行规则整体切换，比如"AI 从工具变成经济
   主体"）复用同一个 `structural_change` 字段，`kind: "regime_shift"`
   时額外提示用户"这可能意味着此前的模拟假设需要整体重新评估"，
   不做自动的"旧模型失效判定"（参考文档第四十二节提到的"识别世界
   是否进入新的运行阶段"，判断本身交给用户和后续人工复盘，engine
   只负责把这个信号显式记录下来，不代替用户做判断）。

**范围克制**：不做"系统自动改写 `manifest.settings`"，不做完整的
`ModelVersion`/`ModelRegime`/`ModelGap` 对象体系（参考文档第五十六
节的数据结构清单）——只落地"发现 + 展示 + 人工确认要不要固化"这个
最小闭环，验证"LLM 主动报告结构性变化"这件事本身是否可靠、是否
有用。

**涉及文件**：`state_model.py`（`SimState.structural_change`）、
两个模板 `SKILL.md`/`advance_step.yaml`（新增可选输出说明）、
`app.py`（时间线卡片展示 `structural_change` 提示，提供"采纳为
正式结构"的操作入口，写回 `manifest.settings`）、
`engine.py`（新增 `apply_structural_change()`，供 `app.py` 的
"采纳"按钮调用，只在用户主动确认后才修改 `manifest.settings`）。

**验收标准**：一次多主体谈判模拟推进若干步后，`advance_step` 输出
`structural_change.kind == "new_entity"`，时间线上出现提示卡片，
点击"采纳"后 `manifest.settings.multi_entity_mode` 相关声明里能
看到新实体被正式加入，下一步推进时 prompt 输入里能看到这个新实体
已经作为"已知实体"存在。

**优先级与依赖**：P1，依赖阶段二十二（因果线结构）——`kind:
"new_mechanism"` 的新机制在展示时优先关联到具体因果线，体验上更
完整；不是强依赖，如果阶段二十二暂缓，本节退化为"新机制不关联
具体因果线，只作为独立提示展示"，不影响核心闭环。

---

### 4.15（P1，阶段二十一）Hypothesis Engine：自动识别关键不确定性并生成多世界

**问题**：`run_comparison_experiment()`/`run_repeated_experiment()`
已经能做"多方案横向对比"和"同方案重复采样估计分布"，但触发方式
永远是**用户手动指定**要对比哪几个方案/配置。参考文档第二十三~
二十五节强调的 `Hypothesis Engine`——系统自己判断"当前模拟里哪个
变量的不确定性最大、值得单独分叉出几个世界看"——完全没有对应
能力。

**方案**：

1. 新增 `world_simulator/hypothesis.py`，提供
   `suggest_critical_uncertainties(manifest, current_state) ->
   List[Dict]`：把当前状态的 `uncertain_fields`（阶段十一已有的
   可信度标注，`confidence: low` 的字段本身就是"高不确定性"的
   现成信号，不需要重新设计识别机制）按字段聚合，结合
   `vars` 里该字段的取值范围（如果 `resource_fields`/`objectives`
   里有相关声明可以参考，没有则退化为"只列出字段名，不给具体取值
   建议"），生成候选的"关键不确定性"列表，每项形如
   `{"field": "ai_cost_trend", "confidence": "low",
   "why": "该字段被标注为低置信度估计，且此前 causal_links 显示
   它是多条因果链的共同起点"}`——"是否是多条因果链的共同起点"直接
   复用已有的 `causal_links.affected_fields` 做一次简单计数，不
   引入新的因果图算法。

2. 新增 `run_hypothesis_worlds(cfg, workspace_root, data_dir,
   sim_id, field, hypotheses: List[Dict]) -> List[AutopilotStepResult]`：
   复用 `branch_manager.fork_branch()`（已有能力，阶段三就有）为
   每个假设（比如"AI 成本快速下降" / "AI 成本缓慢下降" / "AI 成本
   不变"）各开一条分支，分支创建时通过 `custom_option`/
   `manifest.settings` 里新增的 `hypothesis_override` 字段，强制
   这条分支后续推进时把该字段锚定在假设给出的方向上（复用
   `decision_context` 的 prompt 拼装机制，往里加一段"本分支假设
   `ai_cost_trend` 按‘快速下降’方向发展，后续推进请保持这个假设
   一致"），然后各自跑 `steps` 步。

3. 新增 `find_robust_outcomes(data_dir, sim_id, branch_ids:
   List[str], field: str) -> Dict`：复用阶段十已有的
   `aggregate_field_stats()`，对比多条假设分支在某个结果字段上的
   分布，如果**跨所有假设分支该字段的方向/量级基本一致**，标记为
   `Robust Outcome`（参考文档第二十四节定义："哪些结果在多个世界
   中都成立"）；如果差异巨大，反过来验证第 1 步识别出的"关键不
   确定性"确实关键。

4. `app.py` 新增一个轻量入口（可以就放在现有"对比视图"页面里加一个
   "让系统建议关键不确定性"按钮，不单独开新页面）：展示
   `suggest_critical_uncertainties()` 的结果，用户选定一个字段后
   一键触发 `run_hypothesis_worlds()`，跑完后展示
   `find_robust_outcomes()` 的对比结果。

**范围克制**：不做"系统自动决定要不要分叉"（分叉动作仍然需要用户
从建议列表里点一下确认，避免自动消耗大量 LLM 调用/分支数据）；
不做参考文档第四十八节的 `Experiment Design Engine`（自动设计现实
实验去区分假设），那依赖真实世界数据接入，属于阶段二十四的范畴，
本节不做。

**涉及文件**：新增 `world_simulator/hypothesis.py`；`app.py`
（对比视图页面新增"关键不确定性建议"入口）；`branch_manager.py`
（`fork_branch()` 如果需要支持"分叉时附加假设锚定"，评估是否要
在这里加一个可选参数，或者完全在 `hypothesis.py` 里组合调用现有
`fork_branch()` + `update_settings()` 实现，优先选后者，减少对
`branch_manager.py` 已有稳定接口的改动）。

**验收标准**：对一个"AI 成本"被标注为 `confidence: low` 的实例，
调用建议接口能看到 `ai_cost_trend` 被列为候选；分叉出三个假设
分支各推进 3 步后，`find_robust_outcomes()` 能正确区分出"哪些
结果字段在三条分支里都朝同一方向变化"和"哪些字段差异巨大"。

**优先级与依赖**：P1，依赖阶段十（`aggregate_field_stats()`）、
阶段十一（`uncertain_fields`）、阶段十五（`causal_links`）均已
落地，不需要等待阶段二十二/二十三，可以与它们并行推进。

---

### 4.16（P2，阶段二十四）Reality Loop 完整版：模拟与现实的真正反馈闭环

**问题**：阶段十八落地的 `calibration_notes` 是一段静态文本，原样
拼进 prompt，模拟结束后不会跟"后来真实发生了什么"做任何对比，
自然也谈不上"沉淀这次模型准不准"。

**方案**：

1. 新增 `RealityCheck` 记录结构，落盘到
   `data/<sim_id>/reality_checks.jsonl`：

   ```text
   RealityCheck
   ├── id
   ├── sim_id / branch / step   # 对应模拟里的哪一步预测
   ├── predicted_summary        # 模拟当时给出的预测内容
   ├── predicted_at
   ├── actual_outcome           # 用户事后手动填写的真实结果
   ├── recorded_at
   └── verdict                  # matched / partially_matched / diverged
   ```

2. `app.py` 在实例详情页新增"记录现实结果"入口：用户可以对时间线上
   任意一步，事后回来手动填写"后来实际发生了什么"，系统对比
   `predicted_summary` 与 `actual_outcome`（不做自动语义判定，
   `verdict` 由用户自己选，避免引入一个不成熟的自动匹配算法给出
   看似客观实则不可靠的判断）。

3. `verdict == diverged` 时，触发阶段二十知识库的
   `contradicted_count` 更新——如果这一步的 `causal_links` 对应
   的知识条目此前被判定为"预测错误"，对应知识条目的可信度应该
   下调，形成"现实反馈 → 修正因果知识库"的最小闭环（参考文档
   第四十三节"模拟失败也是知识"、第四十七节 `Reality Loop`）。

4. 不做参考文档第四十七节"完整版"要求的自动数据源接入
   （股票价格、经济指标之类的自动抓取）——这一步仍然是**手动
   记录**，只是把"手动"这件事从"写一段校准文案"升级为"真正对比
   预测和结果、且结果会反过来影响知识库"，这是从"轻量版"到"完整
   反馈闭环"之间最关键、投入产出比最高的一步；自动数据源接入
   如果未来需要，应该单独立项（依赖具体要接入哪些外部数据源，
   现阶段没有具体场景，不在本文档展开设计）。

**范围克制**：不做自动数据抓取、不做自动语义匹配判定 `verdict`，
只做"手动记录 + 反向影响知识库可信度"这个最小但完整的闭环。

**涉及文件**：新增 `world_simulator/reality_check.py`（读写
`reality_checks.jsonl`）；`knowledge_base.py`（新增
`update_confidence_from_reality_check()`）；`app.py`（时间线卡片
新增"记录现实结果"入口）。

**验收标准**：对某一步的预测记录一条 `verdict: diverged` 的现实
结果，能在知识库里看到对应因果知识条目的 `contradicted_count`
增加，下一次相关检索时这条知识的排序优先级相应下降。

**优先级与依赖**：P2，依赖阶段二十（知识库需要先存在）；建议在
阶段二十一（多世界/稳健结果）之后启动，理由是"稳健结果"可以作为
"值得拿去对照现实"的高价值候选，两者结合使用价值更大，但不是
强依赖，阶段二十一暂缓也不影响本节独立落地。

---

### 4.17（P2，阶段二十五）因果线 UI：从状态卡片时间线到因果线总览视图

**问题**：现有时间线视图是"按 step 顺序展示每个节点的
summary/vars/key_drivers"，`key_drivers`/`causal_links` 只是挂在
单个节点上的文字标签，用户看不到"技术线整体在往哪个方向走"这种
跨节点聚合的视图，与参考文档第五十节"因果线应该成为用户界面的
核心对象"的主张差距较大。

**方案**：

1. 依赖阶段二十二落地的 `SimState.line_updates` 结构，`app.py`
   新增一个"因果线总览"视图（可以作为实例详情页时间线区块上方的
   一个新增子标签页，不新开顶级页面，减少导航复杂度）：按
   `causal_lines` 声明的每条线，把历史上所有 `line_updates` 里
   该线的记录取出来，渲染成一行"该线的时间点序列 + 每个点的一句话
   摘要"，多条线上下并排展示（不追求参考文档里手绘的"波浪线走势图"
   那种复杂可视化，先用最朴素的"多行文字时间轴并排"验证"因果线
   总览"这个信息组织方式本身是否比现有的"单一状态卡片流"更好用）。

2. 点击某条因果线可以展开，看这条线关联的所有 `causal_links`
   （通过阶段二十二新增的 `line_id` 过滤）——对应参考文档第五十一
   节"第二层：因果线内部"。

3. 不做参考文档第五十二~五十三节"跨因果线连接"/"因果贡献"的完整
   交互式图结构（点击查看"哪些线影响了我" / "结果按因果因素拆解
   的比例"）——那需要正式的因果图数据结构和布局算法，投入明显
   更大，本阶段先把"因果线总览 + 展开看该线内部因果链"这两层做
   扎实，验证用户是否真的会用"按线看"这个视角，再决定要不要投入
   更复杂的图可视化。

**范围克制**：不引入专门的前端图可视化库（比如力导向图/桑基图），
延续项目现有的 Streamlit + 自定义 CSS 卡片风格，用最朴素的布局
实现"多线并排"的信息组织，不做视觉上复杂的走势图渲染。

**涉及文件**：`app.py`（新增因果线总览子视图，复用
`line_updates`/`causal_links.line_id`）。

**验收标准**：一个声明了两条因果线的实例，在"因果线总览"视图里
能看到两条线各自独立的时间点序列，点击其中一条能展开看到只属于
这条线的因果链条目。

**优先级与依赖**：P2，强依赖阶段二十二（`line_updates`/
`causal_links.line_id` 必须先存在），必须排在阶段二十二之后。

---

## 5. 分期路线图

- **阶段二十**：4.12 节，Causal Knowledge Base。无前置依赖，最先
  启动。**已完成**，见 `PROJECT.md` 对应交付记录。
- **阶段二十一**：4.15 节，Hypothesis Engine / 多世界与稳健结果。
  依赖阶段十/十一/十五（均已完成），可以与阶段二十并行。
- **阶段二十二**：4.13 节，多尺度因果线（Causal Line 结构化）。
  建议在阶段二十之后启动（共享 prompt 模板改动窗口），是阶段
  二十三、二十五的前置依赖。**已完成**，见 `PROJECT.md` 对应交付
  记录。
- **阶段二十三**：4.14 节，Model Regime Detection / Emergence。
  依赖阶段二十二。
- **阶段二十四**：4.16 节，Reality Loop 完整版。依赖阶段二十，
  建议在阶段二十一之后启动。
- **阶段二十五**：4.17 节，因果线 UI。依赖阶段二十二，必须排在
  其后。

六个阶段全部完成后，回过头更新本文档状态栏与
`PROJECT.md`/`world_simulator_universal_world_model_upgrade_plan.md`
的"现状覆盖表"，把"因果知识库"“多世界假设”“多尺度因果线”“模型
在线修正”“现实反馈闭环”“因果线 UI”六行补充进对照表。

## 6. 风险提示

本文档按用户要求不再以"等待触发条件"作为启动前提，但如实记录
以下风险，供实施顺序和验收时参考（风险本身不构成"是否启动"的
判断依据，只影响"实施时要格外小心什么"）：

- **阶段二十/二十四涉及新的持久化结构**（`causal_knowledge.jsonl`/
  `reality_checks.jsonl`），与现有 `state_history.jsonl` 一样存在
  "暂不支持并发写入"的已知限制（见 `PROJECT.md`），数据量大了之后
  同样需要评估从"整体重写/追加"换成更稳健的存储方式。
- **阶段二十二对 `SimState` 结构的改动涉及历史数据兼容**——
  `line_updates`/`causal_links.line_id` 均设计为可选字段，默认值
  为空，不影响旧实例的 `from_dict()` 解析，但仍需要补充针对"旧数据
  没有这些字段"的单测，避免历史实例在升级后展示异常。
- **阶段二十三"人工确认后才固化"的设计本身依赖用户认真使用这个
  确认环节**——如果用户为了推进速度习惯性地点"采纳"而不仔细核实，
  这个"保守"设计并不能真正防住污染 `manifest.settings` 的风险，
  验收时应该额外检查"采纳"操作的展示是否足够清晰（新结构的具体
  内容、来源、判断依据），而不只是有一个确认按钮就算完成。
- **阶段二十一每次生成假设世界都会消耗多倍 LLM 调用**（N 个假设 ×
  M 步），需要在 UI 上给出清晰的成本提示（比如"将会消耗约 N×M 次
  推进调用"），避免用户无意中触发大量调用。
- **全部六个方向均未接入真实 LLM 验证**——延续项目现有测试策略
  （`monkeypatch` 打桩 `WorkflowRunner`），每个阶段落地后仍然建议
  先用简单意图手动跑一遍真实调用做质量检查，再进入下一阶段。

## 7. 参考资料

- 用户上传文档：《万能模拟器：从多层因果世界到自我进化的通用模拟
  系统》（未纳入版本库，仅本次对话上下文引用其观点）。
- `next_doc/world_simulator_external_project_plan.md`：原始方案与
  阶段一~七的完整交付记录。
- `next_doc/world_simulator_universal_world_model_upgrade_plan.md`：
  阶段九~十九的完整交付记录，含"参考方案概念对照表"（第 3 节）。
- `external_projects/world_simulator/PROJECT.md`：已知限制与变更
  记录。
