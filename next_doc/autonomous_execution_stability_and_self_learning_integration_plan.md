# 自动驱动执行稳定性改进计划：从"判定-恢复"到"归因-学习-预防"

> **实施状态（持续更新）**：阶段 0（观测先行）、阶段 1（归因与自检）、
> 阶段 2（分级响应）、阶段 3（判定过程回写经验，含 2D 失败模式聚合与预检
> 事件沉淀）已完成首批代码落地，均默认关闭或纯增量、向后兼容。阶段 4
> （多判官协同的实际决策接入 + 判官自动调整建议）尚未实现，仍是设计阶段，
> 按计划要求"先生成建议、人工确认"，不做全自动闭环。各阶段的具体改动
> 清单见文末"实施记录"一节。

> 基于 `mini_agent-master` 实际代码梳理（`role_agents/turn_judge.py`、`role_agents/goal_judge.py`、
> `role_agents/stuck_detector.py`、`role_agents/dispatcher.py`、`goal_mode/`、
> `auto_quarantine.py`、`evolution/`、`wiki/`）。承接分析结论：项目已有一套较完整的
> "单轮判定 → 目标判定 → 卡死检测 → 事后质检 → 崩溃恢复 → 环境自学习 → 长期无人值守调度"
> 闭环，本计划不重造这些机制，而是补齐三类缺口：**归因粒度不够**、**判定是二元的没有分级**、
> **执行期间产生的经验没有稳定沉淀为可复用的自学习信号**。
>
> 本计划的一个核心约束：**尽量复用已有基础设施**（`wiki/experience_writer.py`、
> `wiki/decision_writer.py`、`evolution/failure_pattern_store.py`、
> `evolution/lesson_to_reminder.py`、`perception/lesson_review.py`、`capability_learning.py`
> 等已经存在的经验沉淀链路），新增的是"把自动驱动执行过程中的判定结果、归因结论、恢复效果
> 系统性地接到这些链路上"，而不是另起一套记忆系统。

---

## 一、理念与原则

### 1.1 核心理念

自动驱动执行的终极目标不是"让 agent 一次性把当前这个任务做完"，而是**让每一次执行——无论
成功还是卡住——都变成下一次执行更聪明的原因**。当前的判定机制（TurnJudge/GoalJudge/
StuckDetector）解决的是"这一轮该怎么办"，但判定过程中产生的大量信息（为什么卡住、卡在哪一类
问题、什么样的恢复动作真正有效）目前大多止步于"这一轮用完就丢"或"只服务于当前这个 goal 的
恢复决策"，没有沉淀成跨会话、跨目标可复用的经验。这是最大的杠杆点。

### 1.2 设计原则

1. **异常保守，但保守要分级，不是非黑即白。**
   延续项目现有原则（判定失败一律回退到更安全的状态：`NEED_USER`/`CONTINUE`），但在
   "安全"和"不打扰用户"之间不应该只有两个选项。低置信度场景应该允许"先记录、后续可审阅"
   的中间态，而不是每次都直接打断用户。

2. **归因优先于反应。**
   卡住/失败首先要回答"为什么"，再决定"怎么办"。同一个"卡住"信号背后可能是完全不同的
   原因（环境缺依赖、目标本身有歧义、验收标准互相矛盾、工具调用格式持续出错），不同原因
   应该导向不同的恢复策略，而不是统一走"compact + 换角度提示"。

3. **预防优先于检测。**
   能在目标/验收标准冻结前发现的问题，不应该留给运行时的卡住检测去发现。检测机制是兜底，
   不应该是唯一防线。

4. **判定过程的产出即经验，不是用完即丢的中间态。**
   TurnJudge/GoalJudge/StuckDetector 每一次判定，本质上都在生产"这一类任务/这一类操作
   容易在什么地方出问题、什么恢复动作有效"这样的知识。这些知识现在大多只活在单次运行的
   内存或单个 goal 的落盘状态里，应该系统性地导出到项目已有的经验库（`wiki/experiences`、
   `failure_pattern_store`、`lesson`/`reminder` 体系），供后续所有会话检索复用。

5. **复用而非重造。**
   项目已经有相当成熟的经验沉淀基础设施（见下文"现有自学习基础设施盘点"）。新机制的
   首要任务是"把自动驱动执行这条线路接上去"，而不是设计新的存储格式或检索逻辑。

### 1.3 目标（可衡量）

- **减少无效恢复轮次**：同一 goal 内因"未归因、盲目 compact"导致的重复卡住次数下降。
- **减少不必要的用户打断**：低风险、高置信度的 `NEED_USER` 误报比例下降，用户对
  "系统自动处理的判断"的信任度上升（体现为用户主动查看事后摘要而非要求每次都问）。
- **卡死可预防率提升**：越来越多的卡住原因能在目标冻结阶段的自检环节被提前拦截，而不是
  靠运行时检测事后发现。
- **经验复用率提升**：新目标启动时，能命中历史上同类任务的失败模式/成功经验的比例上升，
  体现为 GoalJudge/GoalRunner 在 prompt 里引用历史经验的次数、以及同类失败模式的复发率
  下降。

---

## 二、现状盘点（简要，供本计划引用）

### 2.1 判定与恢复机制（已具备）

| 机制 | 位置 | 职责 |
|---|---|---|
| TurnJudge | `role_agents/turn_judge.py` | 单轮结束时判断是否真的需要真人介入 |
| GoalJudge | `role_agents/goal_judge.py` | 对照验收标准判定目标是否达成，支持只读工具自验证、过程正当性检查 |
| StuckDetector | `role_agents/stuck_detector.py` | 连续输出/反馈相似度过高 → 卡住判定，有限额度恢复 |
| ProgressTracker | `role_agents/stuck_detector.py` | 识别"平缓但非实质"的伪进展趋势 |
| RoleAgentDispatcher | `role_agents/dispatcher.py` | Evaluator/Coach 等角色 Agent 的触发与注入 |
| GoalState | `goal_mode/state.py` | 轮次边界原子落盘，含 dead_ends、progress_scores、replan_proposal |

### 2.2 自学习基础设施（已具备，本计划要接入的对象）

| 机制 | 位置 | 职责 |
|---|---|---|
| `wiki/experience_writer.py` | 正面经验写入 `wiki/experiences/*.md` |
| `wiki/decision_writer.py` / `decision_consumption.py` | 决策沉淀与检索，供判官参考历史决策 |
| `evolution/failure_pattern_store.py` | 跨来源（objective/goal/turn_judge）失败按 task_category 聚合 |
| `perception/lesson_review.py` | lesson 按 trigger 文本相似度聚类，T1/T2/T3 门槛判定 |
| `evolution/lesson_to_reminder.py` | 高频 lesson 自动转成前馈提醒（reminder），减少"事后才想起"的被动性 |
| `evolution/capability_learning.py` | capability track/ledger，能力置信度演化 |
| `evolution/self_model_drift.py` / `self_narrative.py` | agent 自我认知层面的漂移追踪 |
| `auto_quarantine.py` | 工具/技能环境不兼容自动拉黑 |

**结论**：自学习基础设施是齐全的，缺的是"自动驱动执行过程"和这套基础设施之间的**系统性桥接**，
目前只有零星几处接入（如 `turn_judge_stuck_events.jsonl` → `failure_pattern_store`）。

---

## 三、具体改进方案

### 方案 A：判官归因分类（Attribution Layer）

**问题**：当前卡住判定是"相似度高/进展分数不抬升"这种症状级信号，恢复动作统一是
"compact + 换角度提示"，不管卡住的真实原因是什么。

**方案**：
1. 在 GoalJudge 的扩展输出 schema 中新增 `stuck_category` 字段（仅在判定为
   `CONTINUE` 且检测到卡住信号时要求输出），取值来自一个有限枚举，复用
   `auto_quarantine.classify_error()` 已经建立的"环境不兼容类别"思路并向上扩展：
   - `env_blocked`（依赖缺失/权限不足/工具环境问题）
   - `goal_ambiguous`（目标或验收标准本身有歧义/矛盾，判官难以判定通过与否）
   - `tool_format_error`（工具调用持续格式错误，非语义问题）
   - `genuine_difficulty`（任务本身复杂，判官认为方向正确但需要更多轮次）
   - `unknown`（无法归类，走原有的通用 compact 恢复路径，保持向后兼容）
2. `GoalRunner._try_stuck_recovery` 按 `stuck_category` 分流：
   - `env_blocked` → 不做 compact，而是提示检查依赖/权限，必要时触发
     `auto_quarantine` 记录一次失败（把 goal 层面的环境问题和工具层面的自学习
     打通）。
   - `goal_ambiguous` → 不做通用 compact，直接走 `replan_proposal` 路径（复用
     已有机制），跳过"再试一次"的无效轮次。
   - `tool_format_error` → 走已有的轻量恢复（不需要深度 compact）。
   - `genuine_difficulty` / `unknown` → 走原有逻辑，行为不变。
3. **向后兼容**：`stuck_category` 缺省或解析失败时，完全等价于当前行为（走
   `unknown` 分支），不引入破坏性变更。

### 方案 B：验收标准自检（Pre-flight Check）

**问题**：验收标准在目标建立时一次性生成，标准本身若有歧义/互相矛盾/不可验证，会导致
GoalJudge 永远判不出 DONE，只能靠运行时卡住检测被动发现，浪费多轮执行。

**方案**：
1. 在 `goal_mode/spec.py` 的 GoalSpec 冻结前，插入一次轻量自检步骤（复用
   `GoalSpecBuilder` 已有的 LLM 调用链路，不新增独立 Agent）：要求每条验收标准
   要么带 `verification_command`，要么明确写出"人工/判官可一眼判断的证据形式"。
2. 自检发现问题（标准之间矛盾、无法验证）时，在冻结前提示用户或自动改写建议，
   而不是留到运行时才暴露。
3. 这一步产出的"标准质量问题模式"本身也值得沉淀（见方案 D），例如"某类目标描述
   容易生成不可验证的验收标准"，可以反哺未来的 GoalSpec 生成 prompt。

### 方案 C：分级响应（Graduated Response）替代二元判定

**问题**：`NEED_USER` vs `AUTO_CONTINUE`、`DONE` vs `CONTINUE` 都是二元的，低置信度场景
只能选择"打断用户"或"硬着头皮继续"，两者都有成本。

**方案**：
1. 引入第三态：`AUTO_CONTINUE_WITH_NOTE`——不打断执行，但把这一轮的判定依据、
   置信度、潜在风险写入一份可随时查看的"执行摘要"（复用 daily_digest / kanban
   已有的展示层，不新建 UI）。
2. 触发条件：判官给出结论但自评置信度较低（可以让判官在 JSON 输出里带一个
   `confidence` 字段，低于阈值时走这一态），或归因为 `genuine_difficulty` 且
   已消耗过一次恢复额度。
3. 用户可以选择"事后审阅摘要"而不是"事中被迫响应"，尤其适配 `goal_cron` 长时间
   无人值守场景。
4. **安全阀不变**：`process_flags`（过程正当性问题）、多判官矛盾（见方案 E）等
   高风险信号始终直接走 `NEED_USER`，不允许降级。

### 方案 D：判定过程结构化经验回写（Judge-to-Memory Bridge）

这是本计划与"agent 自我学习机制结合"的核心方案，与用户诉求直接对应。

**问题**：TurnJudge/GoalJudge/StuckDetector 每次判定都在产生有价值的信号
（卡在哪类原因、什么恢复动作真正有效、哪类目标描述容易生成有问题的验收标准），
但目前：
- `GoalState.dead_ends`/`recent_progress_reasons` 只服务于**当前这个 goal**
  的判定上下文，goal 结束后基本不再被其他会话引用。
- `failure_pattern_store` 已经在读 `turn_judge_stuck_events.jsonl` 和
  `dead_ends`，但只做粗粒度的 task_category 聚合计数，没有把"恢复动作是否
  有效"这个反馈信号沉淀进去。
- 正面经验（哪种应对方式**成功**化解了卡住）几乎没有被写入
  `wiki/experience_writer.py`——现有链路以负面事件（lesson）为主，正面经验
  路径存在但很少被自动驱动执行触发。

**方案**：在判定/恢复链路的三个节点上，系统性地补齐经验回写：

1. **卡住 → 恢复成功后，写正面经验**（新增回写点，复用 `wiki/experience_writer.py`）
   - 触发时机：`StuckDetector` 从 `RECOVER` 状态之后，若下一轮 `StuckSignal`
     重新变为 `NONE`（即真的走出了卡住），把"卡住原因（方案 A 的
     `stuck_category`）+ 采取的恢复动作 + 判官给出的关键提示"打包写入一条
     `entry_type="experience"` 的 wiki 页面。
   - 这样下次遇到同类 `stuck_category` 时，GoalJudge/GoalRunner 可以通过
     `decision_consumption.find_relevant_decisions()` 同款检索机制命中这条
     经验，直接把"上次这样做成功走出困境"作为提示注入，而不是每次都从头
     摸索恢复策略。

2. **恢复失败/`GIVE_UP` → 强化失败模式聚合的"归因维度"**（在
   `failure_pattern_store.py` 现有聚合逻辑上扩展字段，不改变其只读扫描的设计
   取舍）
   - 现有聚合是"按 task_category 计数"，扩展为"按 (task_category,
     stuck_category) 二维聚合"，能回答"这类任务反复卡在哪一类具体原因"而
     不只是"卡住了几次"。
   - `sys:self_eval`（capability_map 更新）在扫描 failure_pattern_store 时，
     可以针对性地降低该 (task_category, stuck_category) 组合相关能力的置信
     度，而不是笼统降低整个 task_category 的置信度——这样置信度调整更精准，
     不会因为一类原因（比如环境依赖问题）连累其他其实做得不错的子任务类型。

3. **GoalSpec 自检发现的标准质量问题 → 沉淀为 lesson**（对接方案 B）
   - 复用 `perception/lesson_review.py` 已有的聚类和门槛判定，把"某类目标
     描述容易生成不可验证验收标准"这类模式按现有 T1/T2/T3 门槛升级路径处理，
     达到门槛后经 `lesson_to_reminder.py` 自动转成 `pre_tool`
     （或新增一个语义等价的 `pre_goal_spec`）触发类型的前馈提醒，在下次
     构建 GoalSpec 时提前生效，而不是等错误重复发生够多次才被人工发现。

4. **判官自身可信度回溯**（轻量、渐进式，不阻塞其他方案落地）
   - 记录判官判定与"后续实际走向"是否一致的简单信号：例如某轮判定
     `CONTINUE`，紧接着下一轮就判 `DONE`（可能是过于保守），或者用户在
     `NEED_USER` 场景手动纠正了判官的结论。这类信号先按最小成本落一份
     `judge_calibration_events.jsonl`（参照 `recovery_event_log.py` 的
     "轻量、可有容量上限、允许非持久化"设计取舍），阶段性人工/半自动复盘，
     不急于在第一阶段就自动调整判官 prompt 或阈值。

### 方案 E：多判官一致性交叉校验

**问题**：TurnJudge、GoalJudge、Evaluator/Coach 是相对独立运行的，理论上可能对同一轮
给出矛盾结论（如 TurnJudge 判 `AUTO_CONTINUE`，GoalJudge 同轮判 `NEED_COMPACT`）。

**方案**：
1. 在 `RoleAgentDispatcher` 或 `GoalRunner` 汇总判定结果的地方，加一层轻量冲突检测：
   同一轮内多个判官给出的状态若指向相反动作（继续 vs 打断/压缩），记录冲突事件，
   并统一走"更保守"的一方（不新增决策逻辑，直接复用现有"异常即保守"的优先级，
   只是把判定来源从"单一判官"扩展到"多判官取最保守值"）。
2. 冲突事件本身也是值得关注的信号——频繁冲突可能说明某个判官的 prompt 或阈值需要
   调整，按方案 D.4 的判官可信度回溯思路一并记录、阶段性复盘。

---

## 四、阶段规划

原则：**每个阶段都要能独立交付价值、默认关闭或向后兼容、不阻塞下一阶段**，与项目现有
`next_doc` 文档一贯的"默认关闭 + 显式开关"风格保持一致。

### 阶段 0：观测先行（低风险，先落数据再谈决策）
**状态：已实现。**
- 落地方案 D.4 的 `judge_calibration_events.jsonl`（轻量记录，不接入任何自动决策）。
- 落地方案 E 的冲突检测（先只记录、不改变行为，验证冲突频率是否值得后续投入）——
  当前已提供 `record_conflict_event()` 与 `more_conservative_status()` 两个工具函数，
  尚未在 GoalJudge/TurnJudge 之外找到一个稳定的"同轮双判官都跑"的调用点自动触发，
  留给阶段 4 结合实际冲突场景接入调用方。
- 目标：用真实数据验证后续方案的必要性和优先级，避免"想象出来的问题"。

### 阶段 1：归因与自检（预防优先，性价比最高）
**状态：已实现。**
- 方案 A：GoalJudge 归因分类字段 + `GoalRunner` 分流恢复策略（`stuck_category`
  缺省时行为不变，默认新分流逻辑关闭，走原有 compact 路径，验证一段时间后再默认开启）。
- 方案 B：验收标准自检（GoalSpec 冻结前的轻量校验，默认开启，因为成本低、
  纯前置拦截不影响运行时行为）。

### 阶段 2：分级响应（减少不必要打断）
**状态：已实现（TurnJudge 交互式路径）。**
- 方案 C：`AUTO_CONTINUE_WITH_NOTE` 中间态，先只在 `goal_cron`（本身就是无人值守
  场景，用户对"事后审阅"接受度更高）落地，验证后再考虑扩展到交互式会话。

  **实际落地口径的调整**：先在 TurnJudge 交互式路径实现（成本更低、验证更快，
  该路径本来就是每轮都会触发），`goal_cron` 场景的接入留给后续——`role_agents/
  execution_notes.py` 的记录接口本身与调用方（交互式 / cron）无关，接入
  `goal_cron` 只需要在对应触发点调用同一个 `append_execution_note()`，改动成本低，
  不需要等到本阶段就设计新接口。
- 依赖阶段 1 的归因分类作为置信度判断的输入之一——当前版本 TurnJudge 的
  confidence 字段是判官独立自评的，尚未与 GoalJudge 的 stuck_category 打通
  （两者服务于不同触发点，打通的价值需要更多真实数据支撑，留待后续评估）。

### 阶段 3：判定过程回写经验（与自我学习机制打通，核心交付）
**状态：已实现（基础桥接完成，聚类升级路径为后续增强）。**
- 方案 D.1：卡住恢复成功 → 正面经验回写 `wiki/experience_writer.py`。
- 方案 D.2：`failure_pattern_store` 扩展二维聚合（task_category × stuck_category）。
- 方案 D.3：GoalSpec 自检问题接入 `lesson_review` 聚类升级路径。

  **实际落地口径的调整**：D.3 当前只完成了"事件记录 + 聚合"（`goal_spec_
  preflight_events.jsonl` → `run_failure_pattern_aggregation_once()`），
  还没有把这类事件接入 `perception/lesson_review.py` 的 T1/T2/T3 聚类升级
  门槛判定——先用 `failure_pattern_store` 现成的聚合与展示能力验证"这类
  预检问题是否真的高频、值得升级为前馈提醒"，值得投入后再对接完整的
  lesson 聚类链路，避免过早接入一条重量级流水线。
- 这三项都是"接入已有基础设施"的性质，互相独立，可以并行推进，不要求全部完成才上线。

### 阶段 4：多判官协同与自动化决策收紧（谨慎推进）
**状态：未实现，仍是设计阶段。**
- 方案 E 从"仅记录"升级为"实际影响判定"（取多判官最保守值）。
- 基于阶段 0-3 积累的真实校准数据，评估是否要开始自动调整判官 prompt/阈值
  （方案 D.4 后半段），这一步涉及判官行为的自动演化，风险较高，建议放在最后，
  且优先做成"生成调整建议，人工确认后应用"，而不是全自动生效。

---

## 五、与"agent 自我学习机制"结合的落地关系图

```
执行期间产生的信号
├─ TurnJudge/GoalJudge 判定结果 ──┐
├─ StuckDetector/ProgressTracker ─┼─→ [方案 A 归因分类] ─→ 分流恢复策略（阶段1）
├─ 恢复动作是否成功 ───────────────┘
│
├─→ 恢复成功 ─→ [方案 D.1] ─→ wiki/experience_writer.py ─→ wiki/experiences/*.md
│                                                          ─→ decision_consumption 检索复用（阶段3）
│
├─→ 恢复失败/放弃 ─→ [方案 D.2] ─→ failure_pattern_store（二维聚合）
│                                  ─→ capability_learning 精准降置信度（阶段3）
│
├─→ GoalSpec 自检问题 ─→ [方案 D.3] ─→ lesson_review 聚类 ─→ lesson_to_reminder
│                                                            ─→ 前馈提醒，下次生效（阶段3）
│
└─→ 判官判定 vs 实际走向不一致 ─→ [方案 D.4] ─→ judge_calibration_events.jsonl
                                                 ─→ 阶段性复盘 ─→（阶段4，谨慎）自动调整建议
```

这张图的核心含义：**自动驱动执行不再是一条与自学习机制平行的独立链路，而是自学习机制
最主要的经验来源之一**。现有的 `wiki`/`evolution` 体系此前更多是被"人工纠正"
（lesson 来源多为 human_feedback）和"离线巡检"（cron 扫描）驱动，接入本计划后，
"执行过程本身"会成为持续、高频、自动产生经验样本的来源，理论上能显著提升
`capability_map`、`reminder`、`experiences` 这些既有资产的更新速度和质量，
而不需要新建任何存储层。

---

## 六、风险与非目标

- **非目标**：本计划不改变判官"异常即保守"的根本原则，不追求"完全消除用户介入"，
  而是让必要的介入更精准、不必要的介入更少。
- **风险 1**：归因分类（方案 A）依赖 LLM 判官自我报告，存在误判可能——缓解措施是
  `unknown` 兜底 + 阶段 0 先收集数据验证分类准确性，不要一步到位替换现有逻辑。
- **风险 2**：经验回写（方案 D）如果不加节制，可能导致 `wiki/experiences` 膨胀、
  检索噪声增加——缓解措施是复用已有的 `wiki/dedup.py` 判重和 `lesson_review` 门槛
  机制，不新增独立的节流逻辑。
- **风险 3**：判官自动调整（阶段 4 后半段）风险最高，明确要求"先生成建议、人工确认"，
  不做全自动闭环，避免判官行为漂移不可控。

---

## 七、实施记录（按阶段列出实际改动文件）

### 阶段 0：观测先行

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/mini_agent/role_agents/judge_calibration.py` | 新增 | 判官校准事件 / 冲突事件 JSONL 记录，`more_conservative_status()` 供阶段 4 使用 |
| `src/mini_agent/goal_mode/runner.py` | 修改 | `_run_judge()` 末尾记录一次 `judge_calibration` 事件 |
| `src/mini_agent/agent/role_judge.py` | 修改 | TurnJudge 判定后记录一次 `judge_calibration` 事件（含 confidence，若有） |

### 阶段 1：归因与自检

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/mini_agent/config/models.py` | 修改 | `GoalModeConfig` 新增 `stuck_attribution_enabled`（默认 False）、`goal_spec_preflight_check_enabled`（默认 True）、`stuck_recovery_experience_write_enabled`（默认 False） |
| `src/mini_agent/prompts/fragments/goal_mode.md` | 修改 | 新增 `STUCK_ATTRIBUTION_INSTRUCTIONS` 片段（`stuck_category` 枚举定义） |
| `src/mini_agent/role_agents/goal_judge.py` | 修改 | `run_goal_judge()` 新增 `stuck_attribution_enabled` 参数，拼接进 system prompt |
| `src/mini_agent/goal_mode/runner.py` | 修改 | 解析 `stuck_category`；新增 `_try_attributed_recovery()` 按分类分流（env_blocked / goal_ambiguous / tool_format_error 有专属处理，genuine_difficulty / unknown 回退原逻辑）；`_record_dead_end()` 附带落盘 `stuck_category` |
| `src/mini_agent/goal_mode/spec.py` | 修改 | `GoalSpec` 新增 `validate_verifiability()` 启发式自检方法 |
| `src/mini_agent/cli/commands/goal_mode_cmd.py` | 修改 | 协商循环里调用自检、打印警告、记录预检事件 |

### 阶段 2：分级响应

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/mini_agent/config/models.py` | 修改 | `TurnJudgeConfig` 新增 `auto_continue_with_note_enabled`（默认 False）、`auto_continue_confidence_threshold`（默认 0.6） |
| `src/mini_agent/role_agents/execution_notes.py` | 新增 | 低置信度 `AUTO_CONTINUE_WITH_NOTE` 场景的执行摘要记录 / 读取接口 |
| `src/mini_agent/prompts/fragments/turn_judge.md` | 新增 | `CONFIDENCE_INSTRUCTIONS` 片段 |
| `src/mini_agent/prompts/system/turn_judge.md` | 修改 | 新增 `{{confidence_instructions}}` 插槽 |
| `src/mini_agent/role_agents/turn_judge.py` | 修改 | `run_turn_judge()` 按开关拼接 confidence 指令片段 |
| `src/mini_agent/agent/role_judge.py` | 修改 | 解析 `confidence` 字段；低于阈值时记执行摘要而非强制升级为 `NEED_USER` |

### 阶段 3：判定过程回写经验

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `src/mini_agent/goal_mode/runner.py` | 修改 | 新增 `_maybe_record_recovery_success()`，卡住恢复后确认无再次卡住则回写正面经验；三条归因分流路径与通用恢复路径都会设置 `_pending_recovery_context` |
| `src/mini_agent/evolution/failure_pattern_store.py` | 修改 | `_read_dead_end_failures()` 优先使用结构化 `stuck_category`（回退正则分类）；新增 `record_goal_spec_preflight_issue()` / `_read_goal_spec_preflight_issues()` 并接入 `run_failure_pattern_aggregation_once()`；新增 `get_stuck_category_breakdown()` 供后续 `sys:self_eval` 精准降置信度使用 |

### 阶段 4：未实现

设计已在第三节"方案 E"和第四节阶段规划中给出，代码层面仅提供了
`judge_calibration.more_conservative_status()` 这一个可复用的纯函数，尚未接入
任何实际决策路径。后续实施建议：先用阶段 0 积累至少若干周的
`judge_calibration_events.jsonl` / `judge_conflict_events.jsonl` 真实数据，
确认冲突频率和误判模式后，再决定是否值得投入。

### 后续可继续推进的方向（不影响当前已交付内容）

1. `goal_cron` 场景接入 `execution_notes.append_execution_note()`（阶段2遗留）。
2. `goal_spec_preflight_events.jsonl` 接入 `perception/lesson_review.py` 完整聚类升级门槛判定（阶段3 D.3 遗留）。
3. TurnJudge confidence 与 GoalJudge stuck_category 的联合判断（阶段2遗留，价值待评估）。
4. 阶段 4 的多判官冲突消解与判官自动调整建议生成。
