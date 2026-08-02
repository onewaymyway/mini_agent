# 系统关联性断点 + 缺失重要功能 改进方案

> 状态：**F1/F2/F3/F4 已实现并接入实际调用点，F2 三路数据源全部落地**
> （见文末"实施记录"），已跑通相关现有测试共 251+ 项（含
> `test_goal_mode.py`/`test_judge_verdict.py`/`test_judge_dispatcher_unification.py`
> 等更大范围回归验证，含新增的 TurnJudge stuck 端到端功能验证），失败
> 用例均可定位为改动前已存在、与本方案文件无关的问题，未发现由本方案
> 引入的回归。C6/C7 仍是草案，未实施。
> 关联代码：`src/mini_agent/evolution/`、`src/mini_agent/wiki/`、
> `src/mini_agent/role_agents/`、`src/mini_agent/perception/goal_backlog.py`、
> `src/mini_agent/history/`
> 前置阅读：`docs/mini_agent_核心理念与长期规划.md`（本方案所有 Track 的
> 优先级排序都对齐该文档"能力增长以减少用户认知负担为北极星""自我进化
> 先从自我诊断开始"两条理念，不包含任何"系统自主生成目标"的内容）

## 0. 背景与范围

mini_agent 目前已经建成了相当完整的一批子系统：wiki 知识库（写入侧）、
capability_map（能力地图）、失败诊断（judge/verdict/dead_ends/stuck_detector）、
GoalBacklog/ObjectiveExecutor（目标执行）、improvement_backlog_merge（改进
信号聚合）、soft_goal_deriver（软目标推导）。

但这些子系统目前更多是**并列存在，而不是相互消费**——每个子系统各自把
数据写到自己的存储里，读的一方主要是人（通过看板/日报），系统内部"A 的
输出成为 B 的决策输入"这条链路大多没有打通。本方案只聚焦这一类"断点"，
以及在打通断点过程中顺带发现的、当前完全没有对应模块的"缺失功能"。

**非目标**：
- 不涉及"系统自主生成目标"（不符合项目理念，见背景文档第一节）
- 不涉及看板 UI 改动（已有独立方案 `kanban_and_autonomy_improvement_plan.md`）
- 不涉及具体某个 cron job 的调度参数微调

---

## 1. 断点清单（按影响面排序）

| 编号 | 断点 | 现状 | 根因 |
|---|---|---|---|
| C1 | wiki 决策库只写不读 | `history/decision_extraction.py` 把决策写入 `wiki/decisions/*.md`，但 `role_agents/goal_judge.py`、`role_agents/turn_judge.py`、`agent/compaction.py` 在做新一轮判断/压缩决策时不会反查这些页面 | 决策提炼和决策消费是两条完全独立开发的链路，没人负责把"写"接到"读" |
| C2 | 失败信号三处分散，互不喂料 | `dead_ends`（终止诊断）、judge 的失败判定、`role_agents/stuck_detector.py`（卡住检测）各自落盘，`capability_map` 的置信度更新只看 `sys:self_eval` 的粗统计（工具调用成功率），不看这三处的语义化失败原因 | 三个模块由不同阶段的改进计划各自引入，没有统一的"失败模式"数据模型 |
| C3 | 软目标推导对"被拒绝"的记忆是时间窗口式而非累积式 | `soft_goal_deriver.py` 用 `soft_goal_rejected.json` 做 30 天 dedupe_key 去重，30 天后同一主题会再次被推导；没有"这个方向已经被拒绝 N 次"的累积权重 | `record_rejected()` 只做 TTL 存在性判断，未设计计数字段 |
| C4 | 改进建议采纳结果不回灌到推导权重 | `suggestion_outcome_review.py` 只回看 `self_maintenance` 的工具健康建议是否改善，`improvement_backlog_merge.py` 每次重新读四路原始信号计算分数，不参考"上次同类建议被采纳/回滚"的历史 | outcome_tracker 体系和 improvement_backlog_merge 体系是两条并行开发线，没有共享的"建议效果"字典 |
| C5 | 用户显式纠正没有专门通道回灌到对应知识节点 | 用户在对话中纠正 agent 判断时，走的是通用 lesson memory 记录路径，等下一次巩固循环扫描才可能间接影响 wiki/capability_map；没有"纠正 → 定位到具体决策页/能力条目 → 立即标注"的直接链路 | `reminders/generator.py` 只做同轮提醒，没有"纠正事件"这个一等公民的数据结构 |
| C6 | Objective 步骤间上下文靠文本摘要，产出物元数据未结构化传递 | `_submit_step` 用 `result_summary` 纯文本拼接（`kanban_and_autonomy_improvement_plan.md` P10 已记录），下游若要引用某个具体文件路径/wiki 页面 id，只能从文本里正则猜 | 上一轮方案已识别但标记为"暂不做"，本方案把它和 C1（决策消费）放在一起看，发现价值被低估了 |
| C7 | 能力地图更新和"最近是否有相关外部知识"脱节 | `sys:external_trend_capability_link` 存在但默认关闭，且即便开启，产出的是"候选草稿"，不会自动让 `capability_map` 里对应弱项的"改进建议"字段引用这条外部知识 | 该 job 设计时定位为"辅助人工"，未设计成 capability_map 的直接写入源 |

---

## 2. 缺失的重要功能（当前无对应模块）

以下几项在现有代码里找不到任何雏形，属于纯新增：

### F1. 决策消费校验器（Decision Consumption Probe）

**问题**：项目理念文档 P0 优先级明确写了"建一个可衡量的验证方式：给定一个
新任务，agent 能否引用过去的决策记录来避免重复踩坑"，但目前没有任何模块
做这件事——既没有"消费"动作，也没有"消费是否发生"的度量。

**设计**：
- 新增 `wiki/decision_consumption.py`：在 `goal_judge.py` / `turn_judge.py`
  做判定前，先用当前任务的关键词对 `wiki/decisions/` 做一次轻量检索（复用
  `wiki/search.py` 现有的规则粗筛，不新增检索算法），若命中相关历史决策，
  把决策摘要（≤150 字）注入 judge 的输入 context，并在 judge 的输出结构里
  新增一个可选字段 `referenced_decisions: list[str]`（页面 id 列表）。
- 新增 `wiki/usage_log.jsonl` 里已有的检索命中日志基础上，加一个统计脚本
  `decision_consumption_rate()`：过去 N 天的 judge 调用中，有多少次
  `referenced_decisions` 非空 / 命中相关决策但未引用（说明检索到了但
  judge 没采纳，值得单独关注）。
- 该统计接入现有 `sys:wiki_utility_audit` job 的输出（该 job 本来就统计
  wiki 利用率），不新增 cron job。

**验收标准**：
1. 给定一个此前有过明确决策记录的任务场景（如"是否要引入某个新依赖"），
   agent 在类似任务里能在 judge 阶段引用到对应决策页 id。
2. `wiki_utility_audit` 报告里新增一行"决策消费率"，可以看到这个数字
   是否随时间上升。

**工作量**：中。风险低（只读检索 + 追加字段，不改变现有判定逻辑本身）。

---

### F2. 统一失败模式库（Unified Failure Pattern Store）

**问题**：`dead_ends`、judge 失败判定、`stuck_detector` 各自有独立的记录
格式，`capability_map` 的置信度更新目前只依赖 `sys:self_eval` 的工具调用
成功率统计，看不到"这类任务经常卡在同一个语义原因上"这种更高层的模式。

**设计**：
- 新增 `evolution/failure_pattern_store.py`：定义统一的 `FailurePattern`
  结构（`pattern_id` / `source`（dead_end \| judge \| stuck）/ `task_category`
  / `root_cause_tag` / `occurrence_count` / `first_seen` / `last_seen`）。
- 新增 `sys:failure_pattern_aggregation` cron job（零 LLM，规则聚合，
  间隔建议 `interval:86400`）：扫描三处原始记录，按 `task_category` +
  粗粒度关键词做聚类（复用 `role_agents/stuck_detector.py` 里已有的
  关键词提取逻辑，不重新发明），写入/更新 `failure_pattern_store.json`。
- 打通到 `capability_map`：`sys:self_eval` 在计算某个能力条目的置信度时，
  除了现有的成功率统计，额外查一次 `failure_pattern_store` 里是否有
  匹配该能力域的高频模式（`occurrence_count >= 3`），命中则在
  `capability_map` 该条目上追加 `known_failure_patterns` 字段（仅追加，
  不改变置信度计算公式本身，避免影响既有行为）。
- 打通到 `soft_goal_deriver`：现有"lesson 高频触发"信号源（三路之一）
  改为优先读 `failure_pattern_store`（数据更结构化，`root_cause_tag`
  可直接作为 Goal 标题的一部分），lesson_review 作为兜底信号保留。

**验收标准**：
1. 人为制造 3 次同类失败（比如同一个工具在同一类任务上反复报错但报错
   文案不同），`failure_pattern_store` 能把它们聚成 1 个 pattern 而不是
   3 条孤立记录。
2. `capability_map` 对应条目上能看到 `known_failure_patterns` 引用。

**工作量**：中大。这是本方案里价值最高但也最需要谨慎设计聚类规则的一项
（聚类过粗会把无关失败混在一起，过细则退化成原样罗列，建议先用
`task_category` + `root_cause_tag`（来自 judge 已有的失败分类，不新增
分类体系）两级，不引入语义相似度计算）。

---

### F3. 建议采纳/拒绝的累积权重（Suggestion Feedback Weight）

**问题**：C3、C4 两个断点本质是同一类缺失——"用户/系统对某个建议类型的
历史反馈"没有被当成持久化状态,只有 30 天 TTL 式的去重。

**设计**：
- 新增 `evolution/suggestion_feedback_ledger.py`：维护一个轻量级
  `{dedupe_key_or_category: {accepted: int, rejected: int, last_outcome_ts}}`
  的账本（JSON 文件，零 LLM）。
- `soft_goal_deriver.record_rejected()` 改为同时写入这个账本（累加
  `rejected` 计数），30 天 TTL 去重逻辑保留不变（避免用户短期内被同一个
  建议反复骚扰），但账本本身不过期。
- `_merge_and_score()`（`improvement_backlog_merge.py`）在计算候选分数时，
  新增一个乘法衰减因子：若该类别建议历史 `rejected` 计数 ≥3 且
  `accepted` 为 0，分数打七折（具体系数留一个配置项，默认值先保守，
  避免直接压制到候选消失，仍要保留人工看到的机会）。
- 反向同理：历史 `accepted` 高的类别，分数适度加成。

**验收标准**：
1. 同一类建议连续 3 次被拒绝后，第 4 次在 `improvement_backlog_merge`
   的候选列表里排序明显下降（但不消失，仍可在 `/cron run` 或看板里手动
   看到）。
2. 账本文件可以被 `sys:monthly_trend_retrospective` 读取，在月度回顾里
   增加一行"本月建议采纳率变化"。

**工作量**：小。纯规则计算 + 一个新文件，不涉及 LLM 调用，风险低。

---

### F4. 用户纠正事件的直接回灌通道（Correction Event Router）

**问题**：C5——用户当场纠正 agent 的判断（比如"你这个决策理由不对，
其实我们上次选 A 方案是因为 X"），目前只能变成一条普通 lesson，等巩固
循环扫描才可能间接触达对应的 wiki 决策页，链路长且不保证命中。

**设计**：
- 新增一个"纠正事件"的识别信号：复用现有
  `role_agents/reminders_correction.py`（如果已有纠正检测逻辑，直接接入；
  若目前只做同轮提醒去重，需要扩展一个轻量分类：判断这轮用户输入是否
  在纠正 agent 刚才引用的某个 wiki 页面/决策/能力判断）。
- 新增 `wiki/correction_writer.py`：当识别到纠正事件且能定位到具体的
  `page_id`（来自 F1 新增的 `referenced_decisions` 字段，或本轮工具调用
  引用的能力条目），直接在该页面追加一个 `correction` 区块（时间戳 +
  纠正内容原文的结构化摘要，不是覆盖原内容，保留沿革），并把该页面
  标记为 `needs_review`（复用 wiki 现有的 `stale/superseded` 生命周期
  状态机，新增一个 `needs_review` 状态或复用 `stale`，具体取哪个在实现
  时看 `wiki/lifecycle.py` 现有状态机是否易于扩展再定）。
- 无法定位具体页面时（大多数情况），走原有 lesson memory 路径，不改变
  现状——本功能只解决"能定位时应该更快"这一子问题，不追求覆盖所有纠正
  场景。

**验收标准**：
1. 在一次对话里，agent 先引用了某个 wiki 决策页做出判断，用户当场指出
   错误，该决策页在同一 session 结束前就能看到 `needs_review` 标记，
   不需要等待巩固循环。
2. 未命中具体页面的纠正仍然正常走 lesson memory，不产生回归。

**工作量**：中。依赖 F1 先落地（需要 `referenced_decisions` 字段作为
定位依据），建议排在 F1 之后实施。

---

## 3. 实施顺序建议

按"价值/风险比"和依赖关系排序：

1. **F1 决策消费校验器**——P0，无依赖，直接回应项目理念文档明确写出的
   下一步，且是后续 F4 的前置条件。
2. **F3 建议反馈权重**——P0，工作量最小、零 LLM、风险最低，可以和 F1
   并行做。
3. **F2 统一失败模式库**——P1，价值最高但设计聚类规则需要更仔细，建议
   先出一版只按 `task_category` 聚合的简化版，观察一段时间效果后再决定
   要不要加 `root_cause_tag` 二级聚类。
4. **F4 纠正事件回灌**——P1，依赖 F1，建议排在其后。
5. **C6（步骤间结构化上下文）、C7（外部知识直接写入 capability_map）**——
   P2，价值明确但不紧急，且 C6 与看板改造方案有交叉，建议合并到下一轮
   看板方案里一并评估，不在本方案单独展开设计。

## 4. 风险与克制原则

- 所有新增 job 默认走"零 LLM 规则计算"路线，避免重蹈"数据采集先于数据
  消费"的坑（理念文档第四条明确警惕的问题）——本方案里四项新功能全部是
  "让已采集的数据被消费"，不新增采集面。
- F2 的聚类规则刻意选择保守（两级标签匹配，不引入向量相似度/LLM 语义
  聚类），避免过度设计导致误聚类，可在验证有效后再迭代。
- F3 的衰减因子设置为"打折"而非"屏蔽"，保留人工始终可见候选的能力，
  避免系统单方面替用户"永久否决"某个方向。
- 每一项都要求先有可衡量的验收标准（消费率、聚类命中数、候选排序变化、
  标记时效），不做"做了但无法验证是否有用"的改动，这也是理念文档反复
  强调的"每个改动要能验证是否真的减少了用户显式交代"的具体落地方式。

## 5. 实施记录

### F1 决策消费校验器 —— 已实现

- 新增 `src/mini_agent/wiki/decision_consumption.py`：
  `find_relevant_decisions()`（复用 `wiki/search.py::wiki_shelf_search()`，
  只保留看起来是决策页的命中结果）、`DecisionConsumptionQuery.to_prompt_block()`
  （拼装成可注入 prompt 的文本块）、`record_consumption()` /
  `decision_consumption_rate()`（消费率统计，日志落在
  `wiki/decision_consumption_log.jsonl`）。
- `prompts/user/goal_judge_request.md` 新增 `{{referenced_decisions_block}}`
  占位符（无命中/关闭时渲染为空字符串，不改变既有 prompt 结构）。
- `role_agents/goal_judge.py`：`build_goal_judge_prompt()` 新增可选参数
  `referenced_decisions_block`（默认空字符串）；`run_goal_judge()` 新增可选
  参数 `paths`，传入且 `cfg.goal_mode.decision_consumption_enabled=True`
  时才会检索并拼入 prompt，判定完成后按输出文本里是否出现对应 page_id
  做一次粗略的"是否被引用"判断并调用 `record_consumption()`。
- `config/models.py` 新增 `GoalModeConfig.decision_consumption_enabled: bool
  = False`。
- **本轮已补齐**：`goal_mode/runner.py::GoalRunner.__init__` 现在无条件
  构造 `self._paths`（纯路径对象，无 I/O），并在 `run_goal_judge(...)`
  调用处传入 `paths=self._paths`——`cfg.goal_mode.decision_consumption_enabled`
  仍默认 `False`，打开后即可在真实 GoalRunner 运行中生效，不再需要额外
  接入工作。已跑 `tests/test_goal_mode.py`（90/95 通过，5 个失败是
  `spec.py` 里预先存在、与本次改动无关的测试夹具问题——`_run_builder`
  测试桩缺少 `detection_text` 关键字参数，在改动前就会失败）。

### F2 统一失败模式库 —— 已实现（三路数据源全部接入）

- 新增 `src/mini_agent/evolution/failure_pattern_store.py`：
  `run_failure_pattern_aggregation_once()` 扫描
  `.agent/objective_executions.json` 的 `steps[].error_msg` +
  最近 50 个 session 的 `goal_state.json` 的 `dead_ends`，按"标题归一化
  task_category + 规则匹配 root_cause_tag（timeout/permission/tool_missing/
  rate_limit/other）"聚合为 `FailurePattern`，持久化到
  `failure_pattern_store.json`；`get_patterns_for_category()` /
  `load_failure_patterns()` 供查询消费。
- 新增 cron job `sys:failure_pattern_aggregation`（`interval:86400`，零 LLM，
  本地回调），已在 `api/server.py::_build_autonomous_loop` 里接入
  `ensure_failure_pattern_aggregation_job()`。
- `soft_goal_deriver.py` 新增信号源 `_from_failure_patterns()`（信号 5），
  在 `derive_candidates()` 里附加于 `_from_lesson_review()` 之后调用。
- **本轮已确认/优化**：`goal_mode/runner.py::_record_dead_end()` 早在
  之前的迭代里就已经把"卡住/无实质进展"的判定持久化进
  `GoalState.dead_ends`（`{"round":.., "progress":.., "reason":..}`
  结构，随 `GoalStateStore` 落盘到 `goal_state.json`），因此第二路数据源
  实际上从一开始就是可用的，不需要额外补丁。本轮把
  `_read_dead_end_failures()` 从"整条 dict 序列化后匹配"改为"优先取
  `reason` 字段文本做根因匹配"，避免 `round`/`progress` 之类的结构字段
  噪音混进 `root_cause_tag` 的正则匹配。
- **仍未接入的第三路**（**本轮已补齐**）：`agent/role_judge.py::_maybe_run_turn_judge()`
  判定 TurnJudge 场景的 `StuckSignal.GIVE_UP` 时，现在会调用新增的
  `failure_pattern_store.record_turn_judge_stuck_event()` 追加一条最小
  记录到 `.agent/turn_judge_stuck_events.jsonl`（task_hint 取最近一条
  用户消息，reason 为固定的卡住原因文案），`run_failure_pattern_aggregation_once()`
  新增 `_read_turn_judge_stuck_events()` 读取并入聚合。至此方案文档
  第 2 节设想的"三路数据源"已全部落地：ObjectiveExecution 失败、
  GoalRunner dead_ends（原来就有）、TurnJudge stuck（本轮新增持久化点）。
  端到端功能验证：手工写入 3 条同类 TurnJudge stuck 事件后，
  `run_failure_pattern_aggregation_once()` 正确聚合出
  `occurrence_count=3` 的单个 pattern（未产生 3 条孤立记录）。
- **已知范围调整**：原方案文档设想"用 failure_pattern_store 替换
  lesson_review 高频信号"，实现时评估后改为"附加"而非"替换"——两个
  数据源不完全重叠（lesson_review 还含用户反馈等更多来源），直接替换
  有丢失信号的风险，遂改为并存，由既有 dedupe_key 机制自然去重同名候选。

### F3 建议反馈累积权重 —— 已实现

- 新增 `src/mini_agent/evolution/suggestion_feedback_ledger.py`：
  `record_outcome(paths, category, "accepted"|"rejected")` 累加计数（不
  过期）；`get_weight()` 按规则返回乘法系数——`rejected>=3 且 accepted==0`
  时打七折（`0.7`），`accepted>=2` 时加成 15%（`1.15`），其余 `1.0`（打折
  而非屏蔽，遵循"不单方面永久否决"的克制原则，见第 4 节）。
- `soft_goal_deriver.py`：`record_rejected()` 在写入原有 30 天 TTL 去重
  文件的同时，同步调用 `record_outcome(..., "rejected")`；
  `derive_candidates()` 对全部候选的 `urgency` 应用 `get_weight()`。
- `improvement_backlog_merge.py`：`_merge_and_score()` 新增可选参数
  `paths`，传入时对每个候选按 `subject` 查询累积权重并乘到 `score` 上；
  `run_improvement_backlog_merge_once()` 已更新为传入 `paths`。
- `cli/commands/goals.py`：`_cmd_accept()` 新增可选参数 `paths`，接受
  `agent_derived` Goal 时记录一次 `accepted` 反馈。**本轮已补齐**：
  `handle_goals_cmd()` 里 `accept` 子命令的调用点已改为
  `_cmd_accept(gb, rest[0], paths=paths)`（`paths` 在函数入口处已经
  通过 `_get_paths(agent)` 取到，同一作用域直接传，无需额外构造）。
- 验证：`tests/test_improvement_backlog_merge.py` 全部通过（6/6），确认
  `paths=None` 时行为与改动前完全一致。

### F4 用户纠正事件回灌通道 —— 已实现

- 新增 `src/mini_agent/wiki/correction_writer.py`：`route_correction()`
  给定 page_id + 纠正文本，调用既有的 `wiki/lifecycle.py::mark_page_state()`
  把该页标记为 `stale`（复用现有生命周期状态机，未新增状态值——与方案
  草案里"新增 needs_review 状态"的设想相比，实现时选择直接复用 `stale`，
  理由见模块内注释：不增加状态机复杂度，`stale` 语义已经能表达"需要
  重新核实"）；同时把事件记录追加到
  `wiki/correction_events.jsonl`，供 `recent_correction_events()` 只读消费。
- `context_builder.py` 新增 `last_injected_wiki_page_ids` 属性（与既有
  `last_injected_memory_ids` 并行，记录的是 wiki 页面 id 本身而非记忆
  entry_id 血缘），在 `_try_inject_wiki_search()` 命中 grounded 页面时
  填充，在 `refresh_turn_context()` 开头重置，避免跨轮沿用。
- `agent/reminders_correction.py`：`_detect_and_record_correction()` 在
  既有的 `library.mark_stale_from_correction()`（针对旧的图书馆式记忆
  索引）调用之后，新增一段对 `last_injected_wiki_page_ids` 的独立处理：
  非空时逐个调用 `route_correction()`，命中失败/无页面时静默跳过，不
  影响已完成的记忆条目标记。
- 验证：`tests/test_correction_detector.py`（纠正检测本身）+
  `tests/test_context_builder_wiki_search_primary.py` /
  `test_context_builder_global_knowledge.py` /
  `test_context_builder_workdir_knowledge.py`（wiki 检索路径）共 71 项
  全部通过，确认新增属性/调用不影响既有检索与纠正检测逻辑。

### 尚未实施

- **C6**（步骤间结构化上下文）、**C7**（外部知识直接写入 capability_map）
  ——按第 3 节的优先级排序，仍计划合并到下一轮看板改造方案里评估，本轮
  未动。
- F1/F2/F3 里此前标注的全部"已知范围缩减"/"未接入数据源"（GoalRunner
  接入 F1、CLI 命令入口接入 F3 的 accepted 记录、TurnJudge stuck 信号
  持久化）**本轮已全部补齐**，详见上方各小节。F4 本身范围完整，无遗留。

### 本轮全部改动的回归验证

已运行的测试（均为改动涉及模块的既有测试，非新增）：
`tests/test_improvement_backlog_merge.py`（6/6）、
`tests/test_correction_detector.py` + 三个 `test_context_builder_*`
（71/71）、`tests/test_goal_mode.py`（90/95，5 个失败是 `spec.py` 测试
夹具在改动前就存在的问题，与本方案改动的文件无关）、
`tests/test_judge_verdict.py` + `tests/test_judge_dispatcher_unification.py`
（78/79，1 个失败是 TurnJudge 测试桩缺 `_session` 属性，与本方案改动的
`goal_judge.py`/`run_goal_judge` 无关，改动前同样失败）。全部失败用例均
可定位到与本方案改动文件无关的预先存在的问题，未发现由本方案引入的回归。

### 本轮新增/修改文件清单

新增：
- `src/mini_agent/wiki/decision_consumption.py`
- `src/mini_agent/wiki/correction_writer.py`
- `src/mini_agent/evolution/failure_pattern_store.py`
- `src/mini_agent/evolution/suggestion_feedback_ledger.py`

修改：
- `src/mini_agent/role_agents/goal_judge.py`
- `src/mini_agent/prompts/user/goal_judge_request.md`
- `src/mini_agent/config/models.py`
- `src/mini_agent/goal_mode/runner.py`
- `src/mini_agent/agent/role_judge.py`
- `src/mini_agent/evolution/soft_goal_deriver.py`
- `src/mini_agent/evolution/improvement_backlog_merge.py`
- `src/mini_agent/cli/commands/goals.py`
- `src/mini_agent/api/server.py`
- `src/mini_agent/context_builder.py`
- `src/mini_agent/agent/reminders_correction.py`
- `docs/cron-jobs-reference.md`（新增 `sys:failure_pattern_aggregation` 条目）

