# 用户侧 Personal Model / State / Context Pack / Priority Briefing 指南

> 对应设计文档：`next_doc/personal_ai_alignment_upgrade_plan.md`（阶段一～
> 四实施记录见同目录 `personal_ai_alignment_upgrade_stage{1,2,3,4}_
> implementation_record.md`）。四个阶段均已完成，本文档是"毕业"后的稳定
> 功能指南，后续该功能演进时请原地更新本文档，不要另开新文档分叉。

## 0. 这份指南解决什么问题

外部参考文档提出的 Personal AI 理想架构要求三个模型——**Personal
Model**（我是谁）、**Goal Model**（我想去哪）、**World Model**（世界现在
怎样）——并在其上用 **Context Engine** 组装出结构化 **Context Pack** 喂给
判断环节。本仓库对照检查后发现：mini_agent 已经在
[self-awareness-identity-guide.md](self-awareness-identity-guide.md) 一线
跑出了一套成熟的"证据→LLM归纳→矛盾不覆盖只降权"治理机制，以及完整的
Goal 状态机（`goal_mode/`）、Wiki 知识底座（`wiki/`）——但这套机制当时
只服务于 **Agent 自己的**自我画像，没有覆盖外部文档反复强调的**用户侧**
Personal Model。

本次改进按四个阶段补齐这块拼图，全部只读组装/复用已有机制，**不新建
证据治理框架、不替换 `UserProfile`、不新建顶层状态存储、不改变
`context_builder.py` 现有检索注入逻辑**：

| 阶段 | 解决什么 | 产出 |
|---|---|---|
| 一 | 用户是谁、看重什么、边界在哪 | `UserProfile.derived` 新增 `values`/`risk_preference`/`constraints`，带 `source`/`confidence` 分级 |
| 二 | 用户现在什么处境 | `personal_state_snapshot()` 只读物化快照 |
| 三 | 该把什么喂给模型判断 | `build_context_pack()` 结构化 Context Pack，试点接入 GoalJudge |
| 四 | 该把什么呈现给用户看 | `priority_briefing()` 四段式优先级简报 |

## 1. 阶段一：Personal Model 证据分级扩展

### 1.1 新增字段

在 `profile.py::UserProfile.derived` 已有的 `tech_stack`/`habits` 之外，
新增三个命名空间，复用相同的"文本 + 时间戳"结构范式：

| 字段 | 含义 | 维护方 | `source` |
|---|---|---|---|
| `derived["values"]` | 用户决策取向（更看重效率还是稳妥等） | `evolution/user_signal_profile_builder.py`（LLM 归纳） | `ai_inference` |
| `derived["risk_preference"]` | 用户对不同风险程度建议的接受倾向 | 同上 | `ai_inference` |
| `derived["constraints"]` | 用户明确说过的约束（如"不要自动发消息"） | `UserProfileManager.add_constraint()`（用户显式声明） | `user_stated` |

每条记录新增两个字段：

- `source`：`user_stated`（用户话里明确说的）/ `ai_observation`（从行为
  直接观察到）/ `ai_inference`（AI 推测）。
- `confidence`：沿用 `agent_value_profile_builder.py` 已有的置信度量纲。

**关键约束**：三者绝不能混在一起呈现或被后续决策直接当作既定事实使用。
`ai_inference` 记录展示时必须带角标区分（CLI 展示为"【推测】"），且不
作为其它子系统的直接前提证据，只能作为参考。

### 1.2 证据来源与归纳流程

`values`/`risk_preference` 的证据源是
`evolution/suggestion_feedback_ledger.py` 已经在维护的、覆盖
`soft_goal_deriver`/`improvement_backlog_merge` 等多路建议来源的统一
账本（每个 category 的 accepted/rejected 累计计数），不新增采集点。

归纳流程直接复用 `agent_value_profile_builder.py` 已经验证过的
"证据 → LLM 归纳 → 矛盾不覆盖只降权"结构（共享
`evolution/evidence_pattern.py::merge_evidence_patterns()`），与 Agent
自我画像那套是**同一治理范式在不同受益人上的应用**，不是重新发明。

`constraints` 按方案定义必须是"用户明确说过的"，不走 LLM 归纳，由调用方
（CLI）在用户明确表达约束时直接调用 `add_constraint()` 落盘，
`source` 固定为 `user_stated`、`confidence` 固定为 `1.0`（用户自己说的
话不需要置信度打折）。

### 1.3 使用方式（CLI）

```
/user_signal_profile                          # 展示当前 values/risk_preference/constraints
/user_signal_profile update                   # 触发一次 values/risk_preference 归纳（需要 llm_helper）
/user_signal_profile constraint add <text>    # 显式记录一条约束（source=user_stated）
/user_signal_profile constraint remove <text> # 移除一条约束
/user_signal_profile constraint list          # 只列出 constraints
```

命令实现见 `cli/commands/user_signal_profile_cmd.py`，与
`/agent_value_profile`（归纳 Agent 自己的历史选择行为）是姊妹命令，两者
共享同一套治理算法但服务不同受益人，互不干扰。

### 1.4 与既有 `tech_stack`/`habits`/`preferences` 的关系

`values`/`risk_preference`/`constraints` 是 `derived` 命名空间下新增的
条目，与已有的 `tech_stack`/`habits`（无来源分级，`generate()` 归纳）、
`preferences`（用户显式设置，见
[用户画像指南](user-profile-guide.md) §7）并存，互不冲突——`profile.py`
顶部注释已明确"其余 key 由各自模块维护"的约定。

**与 `decision-profile-guide.md` 的关系（重要，避免混淆）**：
[决策画像指南](decision-profile-guide.md) 描述的
`evolution/decision_profile_builder.py` 是另一条独立线，证据源是
`wiki/decisions/*.md` 里的单条决策记录，归纳结果落在独立的
`.agent/wiki/user_value_profile.md`（纳入 wiki 体系，供检索式问答与
`next_action_advisor` 排序加权使用）。本节的 `derived["values"]` 证据源
是**建议采纳/拒绝账本**，归纳结果落在 `profile.json` 里（供
`context_builder.py` 直接注入 system prompt）。两套机制目前都在归纳
"用户价值取向"这同一个概念，但数据源、存储位置、下游消费方完全不同，
**是否要打通留待后续单独评估**，本阶段维持两者并存、互不感知的现状，
不强行合并（合并前需要先确认两套账本的证据质量/覆盖面是否真的等价）。

## 2. 阶段二：Personal State Snapshot

### 2.1 是什么

新增 `perception/personal_state_snapshot.py::personal_state_snapshot(paths)`，
只读聚合四类已落盘数据源，产出一份"现在是什么"的物化快照：

1. 当前活跃 Goal 及其状态（`GoalBacklog.active_goals()`，按优先级降序
   取前 20 条）
2. 当前进度 vs 计划的偏差（每个活跃 Goal 的 `execution_phase.py` 阶段
   状态 + 全局 `goal_stuck_stats.py` 统计）
3. 当前待处理的主动建议数量与紧急度（消费已有的
   `initiative_inbox.initiative_inbox_snapshot()`）
4. 当前 Personal Model 中的约束摘要（读阶段一的 `list_constraints()`）

**与 Memory 的关键区别**：快照本身**不落盘、不追加历史**——每次调用都
是从源数据实时重新计算的结果，回答的是"现在是什么"而不是"发生过
什么"。任一子聚合异常都不影响其它子聚合，各自 `try/except`，最终兜底
`_empty_snapshot()`。

### 2.2 已知限制

- 进度偏差信号是"已落盘的阶段状态摘要"（`mode`/`cycles_in_mode`/
  `last_health_alert_kind`），不重新触发 `check_phase_health()` 的完整
  判定链路——避免在只读快照里得出和 AutonomousLoop 主循环不一致的判断。
- `urgent_count` 的语义是"低置信度需要用户确认"（`confidence < 0.4`），
  不是真正的时间紧急度，如果后续有独立的紧急度信号源应该替换掉这个
  近似。
- `constraints` 没有独立的 active/inactive 状态，"标记为 active 的约束
  摘要"实际返回的是全部已记录的 constraints。

### 2.3 API

`GET /v1/self/personal_state`（`api/routes.py`），与
`/self/fairness_diagnostics`、`/self/initiative_inbox` 完全同构：解析
`paths` → 调用聚合函数 → 异常兜底为空结构，owner-only。

## 3. 阶段三：Context Pack 组装器（试点接入 GoalJudge）

### 3.1 是什么

新增 `context_builder.py::build_context_pack(paths, goal_text, query="")`，
产出字段固定的 `ContextPack`：

```
Goal: <当前目标摘要>
Current State: <阶段二快照摘要>
Relevant Decisions: <相关历史决策，复用 wiki/decision_consumption.py>
Relevant Experience: <wiki experiences/ 目录检索命中的经验页>
World Context: <source_kind 属于外部知识类别的 wiki 页面>
Current Evidence: <阶段一 user_stated/ai_observation 记录；
                    ai_inference 记录单独列出并标注"推测，非用户明确事实">
Risk: <健康告警数 / 卡住比例 / 低置信度待决策候选数>
```

`ContextPack.to_prompt_block()` 按顺序拼接非空小节，任一字段为空时对应
小节整体省略，不留空标题。

这与 `ContextBuilder` 类（每轮 system prompt 的检索式拼接）是两条**并行**
的路径，互不替代：`ContextBuilder` 解决"这一轮 system prompt 里塞什么"，
`build_context_pack()` 解决"某个具体判断点需要一份怎样的结构化快照"。
现有 wiki 检索片段是 `Relevant Decisions`/`Relevant Experience` 的数据
来源之一，不是被取代。

### 3.2 试点接入位置：GoalJudge

新增配置开关 `cfg.goal_mode.context_pack_enabled`（**默认 `False`**），
与已有的 `decision_consumption_enabled` 完全同构：`role_agents/
goal_judge.py::run_goal_judge()` 在开关打开且调用方传入 `paths` 时，
组装 Context Pack 拼进 `prompts/user/goal_judge_request.md` 的
`{{context_pack_block}}` 位置。`goal_mode/runner.py` 已经在传
`paths=self._paths`，打开配置开关即可生效，无需改动调用点。

方案要求"第一阶段只在 Goal 执行相关的关键决策点试点，不铺开到所有 LLM
调用点"——本阶段只接入 GoalJudge 一个判断点，CoachAgent/SpecBuilder 等
其它判断点暂未涉及，需要先观察试点效果再评估是否扩大接入范围。

### 3.3 已知限制

- Relevant Experience / World Context 目前大概率长期为空：取决于
  `wiki/experiences/` 目录、`source_kind` 属于外部知识类别的页面是否
  已经积累了数据，本模块只负责"有则读出来"，不负责"确保有数据"。
- Context Pack 的 `Relevant Decisions` 与 GoalJudge 既有的
  `referenced_decisions_block` 存在部分信息重叠，两者是独立、可分别
  开关的功能，本阶段刻意没有去重合并。
- 没有做"接入前后判断质量对比"的量化评估，真实效果需要在打开开关后
  观察一段时间。

## 4. 阶段四：Priority Briefing（优先级简报）

> ⚠️ **命名消歧说明**：仓库里已经存在一个名字很像的**完全不同**功能——
> "每日融合日报"，见 [daily-digest-guide.md](daily-digest-guide.md)
> （`/digest daily` 命令，产出 `.agent/daily_reports/*.md`，端点
> `GET /v1/digest/daily`），是行为时间分布 + Goal 当日进展的**回顾型**
> 日报，明确不生成任何建议。本节描述的是**另一个**独立机制——
> `perception/priority_briefing.py::priority_briefing()`，端点
> `GET /v1/self/priority_briefing`，是本方案（`personal_ai_alignment_
> upgrade_plan.md`）阶段四的产出，语义是"简报聚合视图"而非"回顾报告"。
> 为避免和上面那个已有功能撞名，本方案的产出**没有采用"Daily Digest"
> 这个名字**，改叫 Priority Briefing——四段内容里"今天最重要的事"其实
> 就是"当前优先级排序"，改名后语义反而更贴切。两者**互不依赖、互不
> 替代**，是否要合并留待后续单独评估。

### 4.1 是什么

新增 `perception/priority_briefing.py::priority_briefing(paths)`，合成
四段式简报：

```
今天最重要的事：<阶段二快照里 Top N 活跃 Goal>
AI 已完成：<goal_backlog 中最近完成的 Goal>
需要你决定：<initiative_inbox 中置信度较低、需要用户确认的候选>
风险：<阶段二快照里的健康告警 / 卡住比例>
```

纯只读聚合，不落盘、不追加历史，不提供任何写操作（接受/拒绝/确认候选
仍然要去 `initiative_inbox` 对应的原生入口操作）。

### 4.2 数据来源

| 段落 | 数据来源 | 说明 |
|---|---|---|
| 今天最重要的事 | 阶段二 `personal_state_snapshot()` 的 `active_goals` | 直接取前 N 条，不重新排序 |
| AI 已完成 | `goal_backlog.py::GoalBacklog.all_nodes()` | 筛 `status == "completed"`，按 `last_touched_at` 降序 |
| 需要你决定 | `initiative_inbox.initiative_inbox_snapshot()` | 筛 `confidence < 0.4`（与阶段二 `urgent_count` 同一阈值） |
| 风险 | 阶段二快照的 `progress` 字段 | 只做展示形态转换，不重新计算 |

### 4.3 API

`GET /v1/self/priority_briefing`（`api/routes.py`），与
`/self/personal_state` 完全同构。前端呈现载体（Kanban 新 tab / 首页
改造）尚未落地，留待有实际 UI 改动需求时再评估，目前只提供聚合函数 +
API。

### 4.4 已知限制

- "AI 已完成"目前只包含已完成的 Goal，不包含"建议采纳记录"——
  `suggestion_feedback_ledger.py` 目前只有分类计数，没有保留具体建议
  标题，无法可靠还原成展示条目，如实只呈现 Goal 完成这一半。
- 不区分"用户主动完成"与"AI 自主执行完成"（`GoalNode.status` 不记录
  是谁改的）。
- `top_n`/`recent_completed_limit`/`urgent_confidence_threshold` 均为
  硬编码默认值，未接入配置系统。

## 5. 配置项汇总

| 配置项 | 位置 | 默认值 | 说明 |
|---|---|---|---|
| `goal_mode.decision_consumption_enabled` | `cfg.goal_mode` | `False` | GoalJudge 是否检索并注入相关历史决策（阶段三之前的既有开关，非本方案新增，列出便于对照） |
| `goal_mode.context_pack_enabled` | `cfg.goal_mode` | `False` | GoalJudge 是否注入结构化 Context Pack（阶段三新增） |

`values`/`risk_preference`/`constraints` 本身没有独立的开关字段，归纳
动作由 `/user_signal_profile update` 手动触发或后续接入 cron（当前未
接入定时任务，需手动执行）。

## 6. API 汇总

| Method | Path | 阶段 | 说明 |
|---|---|---|---|
| GET | `/v1/self/personal_state` | 二 | 用户当前处境物化快照 |
| GET | `/v1/self/priority_briefing` | 四 | 优先级简报（今天最重要的事/AI已完成/需要你决定/风险） |

两者均为 owner-only、只读、失败兜底为空结构，与仓库内 `/self/
fairness_diagnostics`、`/self/initiative_inbox` 同一约定，未编写 HTTP
层测试（与后两者现状一致），聚合逻辑本身由函数级单测覆盖：
`tests/test_personal_state_snapshot.py`（阶段二）、
`tests/test_context_pack.py`（阶段三）、`tests/test_priority_briefing.py`
（阶段四）。

## 7. 与其它已有机制的关系一览

- **Agent 自我画像**（[self-awareness-identity-guide.md](self-awareness-identity-guide.md)）
  —— 本方案证据治理范式的"原型"，服务对象是 Agent 自己而非用户，两者
  共享同一套 `evidence_pattern.py` 算法。
- **决策画像**（[decision-profile-guide.md](decision-profile-guide.md)）
  —— 另一条独立的用户价值取向归纳线，证据源、存储位置、消费方均与
  阶段一不同，详见 §1.4。
- **每日融合日报**（[daily-digest-guide.md](daily-digest-guide.md)）
  —— 命名容易联想到本方案阶段四，但完全独立的回顾型日报；本方案的
  阶段四产出为避免撞名特意改叫 Priority Briefing，详见 §4 开头的说明。
- **主动性候选收件箱**（`initiative_inbox.py`，见
  [growth-advisor-guide.md](growth-advisor-guide.md)）—— 阶段二/四均
  直接消费其聚合结果，不重复实现候选收集。
- **用户画像**（[user-profile-guide.md](user-profile-guide.md)）——
  `derived["values"/"risk_preference"/"constraints"]` 是其 `derived`
  命名空间下新增的字段，详见 §1。

---

*最后更新：2026-09（阶段四产出由 `daily_digest` 更名为 `priority_briefing`，
避免与仓库既有的"每日融合日报"撞名，见
next_doc/personal_ai_alignment_upgrade_plan.md）*
