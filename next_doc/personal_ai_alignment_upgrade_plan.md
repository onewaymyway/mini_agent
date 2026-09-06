# Personal AI 架构对齐升级方案（用户侧 Personal Model / State / Context Pack / 证据治理）

> 实施状态：**阶段一、阶段二、阶段三已完成**（见
> `next_doc/personal_ai_alignment_upgrade_stage1_implementation_record.md`、
> `next_doc/personal_ai_alignment_upgrade_stage2_implementation_record.md`、
> `next_doc/personal_ai_alignment_upgrade_stage3_implementation_record.md`）。
> 阶段四尚未开始。

> 前置阅读：
> - 外部参考文档《Personal AI 到底应该如何实现？——从个人知识空间到自主行动系统的
>   完整架构》（用户上传，未入库，核心论断见本文档 §1）
> - `next_doc/initiative_systems_unification_plan.md`（主动性子系统整合方案——本方案
>   与其阶段三/四强相关，是在其之上补一块此前未覆盖的拼图，不是重复立项）
> - `next_doc/self_awareness_identity_evolution_plan.md`（自我意识/身份演化计划——
>   本方案在方法论上大量复用它已经跑通的证据治理范式，但应用对象不同，见 §2）
> - `src/mini_agent/profile.py`、`src/mini_agent/context_builder.py`、
>   `src/mini_agent/perception/initiative_inbox.py`、
>   `src/mini_agent/evolution/agent_value_profile_builder.py`

## 0. 背景

外部文档提出了一套 Personal AI 的理想架构，核心论断是：真正的 Personal AI
不是 Chatbot+Memory，而应该建立三个模型——**Personal Model**（我是谁）、
**Goal Model**（我想去哪）、**World Model**（世界现在怎样）——并在此之上用
**Context Engine** 把知识/状态/目标/证据组装成结构化 **Context Pack** 提供给
Agent 判断，形成"理解→研究→判断→规划→行动→观察→反馈→学习→重新规划"的
长期闭环。

对照 mini_agent 当前实现逐条核对后，发现一个结构性错位：mini_agent 已经在
`self_awareness_identity_evolution_plan.md` 一线上跑出了一套相当成熟的
"证据 → LLM 归纳 → 落盘（矛盾不覆盖只降权）"治理机制，以及完整的 Goal 状态机
（`goal_mode/`）、Wiki 知识底座（`wiki/`）、跨系统候选收件箱
（`initiative_inbox.py`）——但这套治理机制目前建的是 **Agent 自己的**
身份/价值观/能力自画像，而不是外部文档反复强调、真正对应 Personal Model 的
**用户侧**画像。`profile.py::UserProfile` 是唯一对应用户侧的模块，但目前只有
`tech_stack`/`habits`/`preferences` 几个字段，既没有证据来源分级（用户明确
说的 vs AI 从行为推测的），也没有和"当前状态"做区分（Memory 记录了发生过
什么，但没有一个物化的"现在是什么"快照供各子系统统一读取）。

同时，`context_builder.py` 目前是"wiki 命中时注入相关片段"的检索式拼接，还
不是外部文档强调的、字段固定的结构化 **Context Pack**（Goal + 当前状态 +
相关决策历史 + 相关经验 + 世界上下文 + 证据 + 风险 + 候选策略）；产品呈现层
面也是多个独立 Kanban tab，缺一个把"今天最重要的事 / AI 已完成 / 需要你
决定 / 风险 / 机会"聚合成单一简报的入口。

这些缺口不是要另起一套系统，而是把已经在 Agent 自我建模、Goal 状态机、
Initiative Inbox 三条线上验证过的机制，**补齐到用户侧**，并在其上加一层
结构化组装。

## 1. 核心理念

1. **Personal Model 的主语是用户，不是 Agent**——mini_agent 已经证明了
   "证据→LLM归纳→矛盾不覆盖只降权"这套治理范式是可行的，但它目前只服务于
   Agent 自我认知；这套范式本身与受益人无关，应该原样复用到用户画像上，而
   不是重新发明一套。
2. **Memory 记录"发生过什么"，State 回答"现在是什么"**——两者必须分离。
   现有各子系统（goal_mode/growth_advisor/capability_learning）各自维护
   局部状态，但没有一个跨系统共享的、"用户当前整体处境"的物化快照；
   `initiative_systems_unification_plan.md` §4.5 已经预留了 `work_index`/
   `WorkThread` 作为顶层信号源的位置，本方案是把这个位置的内容具体设计
   出来，并明确其"State 而非 Memory"的定位。
3. **证据必须分级，且分级本身就是可追溯性的基础**——User Fact（用户明确
   说的）/ AI Observation（AI 观察到的行为）/ AI Inference（AI 推测的）
   三者绝不能混在一起呈现或混在一起被后续决策直接当作既定事实使用，否则
   长期运行后 AI 的猜测会逐渐"固化"成 AI 自己认定的用户事实。
4. **Context 应该是结构化的 Pack，不是检索片段的拼接**——检索（全文/向量/
   图关系）解决"找到相关信息"，但喂给模型的最终形态应该是字段固定、可
   复现、可审计的 Context Pack，而不是一次性的检索结果堆叠。
5. **用户面对的应该是一份简报，不是多个后台面板**——已经有的
   `initiative_inbox` 是很好的"候选收件箱"，但收件箱是给用户处理建议用的，
   不等于"日报"；日报应该是更高一层的、面向"用户今天该看什么"的合成视图。

这五条同样不是五个独立改进点，而是同一件事的五个环节：**先把"用户是谁、
现在什么状态"这件事做扎实（1+2+3），再决定"该把什么喂给模型判断"
（4），最后决定"该把什么呈现给用户看"（5）。**

## 2. 与已有机制的关系（避免重复造轮子）

- **不新建证据治理机制**，直接复用 `agent_value_profile_builder.py` 已经
  验证过的"证据源 → LLM 归纳 → 落盘，矛盾不覆盖只降权"三层结构，把它的
  证据源从"StateRepo commit 风险分级"换成"用户在对话/行为中留下的信号"，
  归纳目标从 `AgentValueProfile` 换成扩展后的 `UserProfile.derived`。
- **不替换 `UserProfile`**，在其 `derived` 命名空间下新增字段并扩展现有的
  "文本+`last_confirmed_at`"结构，追加 `source`/`confidence`，与
  `growth_advisor` 已经在用的 `growth_focus_areas` 等命名空间约定共存，
  互不冲突（`profile.py` 顶部注释已明确"其余 key 由各自模块维护"的约定）。
- **不新建顶层状态存储**，`work_index`/`WorkThread` 的具体落地放在本方案，
  但对外只暴露一个只读聚合视图，做法上延续 `initiative_inbox.py`/
  `fairness_diagnostics.py` 这类"只读聚合、不侵入原模块"的既有模式。
- **不改变 `context_builder.py` 现有的检索注入逻辑**，在其之上新增一层
  组装器，检索结果作为 Context Pack 的一个字段来源，而非被替代。
- **不新建推送/展示框架**，Daily Digest 直接消费 `initiative_inbox` +
  新增的 State 快照 + Goal 进度趋势（`perception/execution_phase.py`/
  `goal_stuck_stats.py` 已有数据），只做合成，不重复采集。

## 3. 目的

- 让 mini_agent 真正拥有一个关于"用户是谁、现在什么状态"的画像，而不是
  只有 Agent 自己的自我画像——这是"数字分身"定位能够成立的前提，缺了这块，
  再强的执行/调研能力也只是"更聪明的工具"而不是"理解你的助手"。
- 让 AI 的推测与用户的明确陈述在存储层面就被区分，避免长期运行后出现
  "AI 把自己的猜测当成用户事实"的治理风险，这个风险会随着自主执行范围
  扩大（`initiative_systems_unification_plan.md` 阶段二已经打通目标树
  执行）而被放大，宜早不宜迟。
- 让"当前处境"有一个可以被各子系统统一读取的物化快照，减少候选生成时
  各条线各自扫描、互不感知、方向可能冲突的问题（该问题已在
  `initiative_systems_unification_plan.md` §2 第 5 点被明确记录为待解决
  的顶层信号缺失）。
- 让喂给模型的 Context 更稳定、更可审计，为将来"AI 为什么这么判断"的
  可追溯需求打基础。
- 让用户能在一个地方看到"现在最重要的事"，降低在多个 Kanban tab 间自行
  拼装优先级的认知负担。

## 4. 改进方案

### 4.1 用户侧 Personal Model 扩展（证据分级）

在 `profile.py::UserProfile.derived` 现有的 `tech_stack`/`habits` 之外，
复用相同的"文本 + 时间戳"结构范式，新增覆盖外部文档列出的关键维度中当前
明显缺失、且有现成信号来源的几项：`values`（决策取向，可从
`agent_value_profile_builder.py` 已经在扫的 StateRepo commit 场景之外，
新增"用户对 AI 建议的采纳/拒绝"作为证据源）、`risk_preference`
（低/中/高风险偏好，来自用户对高风险操作确认/拒绝的历史）、
`constraints`（用户明确说过的约束，如"不要自动发消息"）。

每条 `derived` 记录新增两个字段：
- `source`: `user_stated`（用户话里明确说的）/ `ai_observation`
  （从行为直接观察到，无需推测）/ `ai_inference`（AI 推测）。
- `confidence`: 沿用 `agent_value_profile_builder.py` 已有的置信度量纲
  约定，不新造一套标准。

三者中 `ai_inference` 类记录展示时必须带角标区分，且不作为其余子系统
（如 growth_advisor 判断"用户可能感兴趣"）的直接前提证据——只能作为参考，
避免推测链式放大。归纳流程直接复用 `agent_value_profile_builder.py` 的
"证据→LLM归纳→矛盾不覆盖只降权"结构，新写一个面向用户信号源的归纳器，
两者共享同一套矛盾处理逻辑（抽取成公共函数，避免复制粘贴分叉）。

### 4.2 顶层 State 快照（Personal State Snapshot）

新增 `perception/personal_state_snapshot.py`，只读聚合现有分散数据源，
产出一份"现在是什么"的物化快照，不做任何新增采集：

- 当前活跃 Goal 及其状态（读 `goal_backlog.py`/`goal_mode/`）
- 当前进度 vs 计划的偏差（读 `execution_phase.py`/`goal_stuck_stats.py`）
- 当前待处理的主动建议数量与紧急度（读 `initiative_inbox.py`）
- 当前 Personal Model 中标记为 `active` 约束的摘要（读 4.1 扩展后的
  `UserProfile.derived`）

快照本身**不落盘为历史记录**（这是与 Memory 的关键区别——它是"现在"的
计算结果，随时可以从源数据重新计算，不追加历史），只在被请求时实时计算，
类似 `fairness_diagnostics_snapshot()` 的既有模式。

### 4.3 Context Pack 组装器

在 `context_builder.py` 现有检索注入逻辑之上，新增
`build_context_pack(goal, query, paths)`，产出字段固定的结构：

```
Goal: <当前目标摘要>
Current State: <4.2 的快照摘要>
Relevant Decisions: <相关决策历史，复用现有 wiki decision 抽取层>
Relevant Experience: <相关经验条目，若已有类似结构则复用，否则本阶段留空>
World Context: <world_search/external_trend 相关信号，若无归零，不强求>
Current Evidence: <4.1 中 source=user_stated/ai_observation 的相关记录，
                    ai_inference 记录单独列出并标注"推测，非事实">
Risk: <4.2 快照中的进度偏差/待决策事项>
```

现有的 wiki 检索片段作为 `Relevant Decisions`/`Relevant Experience` 的
数据来源之一填入，而不是被这个新结构取代。第一阶段只在 Goal 执行相关的
关键决策点（如 goal_mode 的判断环节）试点接入，不铺开到所有 LLM 调用点，
避免一次性改变太多现有 prompt 组装路径。

### 4.4 Daily Digest（每日简报）

新增只读聚合视图（Kanban 新 tab 或现有首页改造，具体载体留到实现阶段
评估），消费 4.2 的快照 + `initiative_inbox` + Goal 进度趋势，合成：

```
今天最重要的事：<Top N 活跃 Goal 的下一步动作>
AI 已完成：<近期成功执行的 Goal/建议采纳记录>
需要你决定：<initiative_inbox 中 confidence 较低或标记需要确认的候选>
风险：<4.2 快照中的进度偏差项>
```

不提供写操作（与 `initiative_inbox` 现有原则一致，写操作留在各自原生
tab），纯合成展示层，不侵入任何现有模块。

## 5. 为什么这样划分

- 4.1（Personal Model 扩展）是其余三项的数据基础——没有分级的用户画像，
  4.2 的快照和 4.3 的 Context Pack 都无从谈起，优先做。
- 4.2（State 快照）依赖 4.1 提供的部分字段（约束摘要），但主要数据源是
  已有的 Goal/Initiative 模块，改动面小、纯只读聚合，可紧接 4.1 之后做，
  且能独立验证价值（哪怕 4.3/4.4 不做，快照本身也可以先接入现有 kanban
  展示）。
- 4.3（Context Pack）依赖 4.1 + 4.2 都就绪后才有实际内容可填，且涉及
  改动 Prompt 组装路径，风险高于前两项，需要先在小范围试点验证不引入
  回归，故排第三。
- 4.4（Daily Digest）纯展示合成，依赖前三项都有数据后价值才完整体现，
  且改动面最集中在前端呈现层，风险最低，放最后即可，不阻塞前面三项的
  独立上线。

## 6. 改进阶段划分

- **阶段一：Personal Model 证据分级扩展**（对应 4.1）
  扩展 `UserProfile.derived` 新增 `values`/`risk_preference`/
  `constraints` 三个维度，抽取 `agent_value_profile_builder.py` 的
  "证据→归纳→矛盾降权"逻辑为共享函数，新写面向用户信号源的归纳器复用之。
  新增单元测试覆盖：新字段的 `source`/`confidence` 写入与迁移（沿用
  `_migrate_text_items` 的迁移模式）、矛盾证据不覆盖只降权的行为、
  `ai_inference` 记录的角标展示。

- **阶段二：Personal State Snapshot**（对应 4.2）
  新增 `perception/personal_state_snapshot.py`，只读聚合现有 Goal/
  Initiative/Profile 数据，新增对应 API 路由与测试。验证快照可独立接入
  现有 Kanban 某个面板展示，不依赖后续阶段。

- **阶段三：Context Pack 组装器（试点接入）**（对应 4.3）
  新增 `build_context_pack()`，先在 goal_mode 判断环节小范围试点，
  对比接入前后的判断质量/稳定性，确认无回归后再评估是否扩大接入范围。

- **阶段四：Daily Digest**（对应 4.4）
  合成展示层，消费前三阶段产出的数据，落地为 Kanban 新 tab 或首页改造，
  具体载体在本阶段开始时结合当时的 Kanban 整体布局评估决定。
