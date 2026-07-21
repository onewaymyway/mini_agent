# 主动推荐、日报生成与决策画像机制设计方案

## 一、为什么要改

`mini_agent` 目前已经具备了相当完整的"感知层"能力：`perception/behavior/` 能采集浏览器/窗口/应用生命周期事件，`goal_backlog.py` 能维护跨会话的目标层级，`soft_goal_deriver.py` 能发现停滞项目并 derive 出候选 Goal，`behavior/analyzer.py` 每天能生成行为聚合摘要。这些能力**分别都存在**，但目前的问题是：

1. **数据没有真正"合流"**。行为聚合（analyzer.py）、目标进展（goal_backlog）、代码提交（git）三条数据线各自产出，用户要理解"我这一天/这一周到底怎么样"需要自己拼。
2. **建议是被动的**。soft_goal_deriver 的产出目前只在用户主动执行 `/digest` 时才可见，不会在合适的时机主动递给用户，用户不打开命令就等于不存在。
3. **没有"用户是怎么做决策的"这一层认知**。decision_extraction 记录的是一条条孤立的历史决策，没有人从中反向提炼出"这个人做决策时真正在乎什么"，导致 agent 没法在推荐/回答"为什么当初"这类问题时体现出真正懂用户，而不是懂项目。

这三点合起来，正是上一轮分析里指出的"文档愿景与项目现状"之间最后也是最难的一段距离：**从"记录行为"到"理解人"**。

## 二、现状（基于代码实际检查）

| 已有能力 | 文件 | 现状说明 |
|---|---|---|
| 行为原始事件采集 | `perception/behavior/collectors/*` | 覆盖 active_window / cdp_browser / app_lifecycle / idle / now_playing，完整 |
| 行为日聚合 | `perception/behavior/analyzer.py::generate_daily_summary` | 已产出 `~/.agent/behavior/analysis/<日期>.json/.md`，但只覆盖"时间分布"，不含目标进展和代码提交 |
| 停滞目标发现 | `evolution/soft_goal_deriver.py` | 已能从 `work_index` 找出 30 天无进展且 `next_suggested` 非空的 WorkThread，derive 成 Goal，写入 GoalBacklog（`source="agent_derived"`） |
| 建议展示 | `/digest` 命令 | 已能以"💡 Agent 建议"形式展示 agent_derived 的 Goal，但**只在用户主动输入 `/digest` 时才触发**，没有启动自动展示、没有看板入口、没有推送 |
| 定时任务框架 | `evolution/cron_scheduler.py` | 已有 `sys:` 前缀内置 job 机制（consolidation/workdir_sync/self_eval/goal_review 等），新增日报/推荐 job 可以直接复用这套框架，不需要新造轮子 |
| 决策记录 | `history/decision_extraction.py` + `wiki/decision_writer.py` | 能提炼单条决策并写入 wiki 决策页，但**没有任何模块对多条决策做归纳**，没有"用户价值取向"这一层产出物 |

结论：日报和推荐所需的**底层数据和最基础的候选发现逻辑其实都已经就绪**，真正缺的是"合流—排序—克制地展示"这一层，以及决策画像这个全新的归纳层。这意味着这次改造的性价比很高，不需要重建感知层。

## 三、理念与目标

**核心理念**：不打断、不新增用户负担、只在真正有价值的信息上开口。

具体落到三条原则：

1. **只推理，不询问**——不问用户"今天打算做什么"，所有输入都来自已经存在的行为/目标/代码数据，用户不需要多做任何一次录入。
2. **有证据才说话**——任何"建议"或"画像模式"必须能追溯到具体的行为事件、Goal 变化或历史决策记录，不允许 LLM 凭空生成看似合理但无依据的判断。少于一定证据量的模式不成立，宁可不说，不能编。
3. **克制优先于全面**——展示频率、时机都要设阈值，目标是"用户觉得每次开口都有用"，而不是"信息量最大化"。

**目标**：
- 让用户在启动 agent 或打开看板时，能看到一句"合流后"的、有理由支撑的"接下来该做什么"提示，而不需要主动执行任何命令；
- 让用户每天能自动拿到一份融合了行为、目标、代码的日报，而不是三份割裂的原始数据；
- 让 agent 逐步积累一份基于真实历史决策、可追溯、可自我修正的"用户价值画像"，并先用在两个低风险场景（决策问答、推荐排序），暂不做"代用户做决策"这种高风险用法。

## 四、方案

整体架构分三层，自下而上依次是：数据合流层 → 展示克制层 → 画像归纳层。三者都挂在已有的 cron_scheduler 框架上，运行节奏依次变慢（小时级 → 日级 → 周级），这是有意为之：候选发现要快，展示要稳，画像归纳要慢，避免画像因短期波动而抖动。

### 4.1 每日融合报告（daily_digest）

新增 `evolution/daily_digest.py`，**不是重做 analyzer.py，而是在其产出之上再融合两条数据线**：

- 读取 `behavior/analyzer.py` 当天的 `.json` 聚合结果（时间分布）
- 读取当天 GoalBacklog 中各 Goal/Objective 的 `cumulative_progress` 差值（今天相对昨天的进展）
- 读取当天 git commit 记录（复用现有 GitHub 接入逻辑）

三者合并输出到 `.agent/daily_reports/<YYYY-MM-DD>.md`，采用与 wiki 页面一致的 frontmatter，使其未来可以被 wiki 检索纳入（例如回答"上周都在忙什么"这类回顾问题）。

cron 接入：新增内置 job `sys:daily_digest`，`schedule="cron:0 22 * * *"`（每天固定时间生成，也可以选择"当天第一次交互时补生成前一天"这种懒惰触发方式，更省资源，具体见改进计划的验证阶段）。

### 4.2 主动推荐层（next_action_advisor）

新增 `evolution/next_action_advisor.py`，**明确定位为 soft_goal_deriver 的"排序+讲道理"层，而不是重新做候选发现**：

1. **候选来源**：直接复用 `soft_goal_deriver` 已经 derive 出的 `agent_derived` Goal，以及一个新增的轻量规则——"注意力错配"检测（最近行为事件大量集中在某类活动，但该活动未挂靠任何登记 Goal，且持续超过阈值时长）。
2. **LLM 排序**：仅对候选（通常 3～8 条）做一次 LLM 调用，输出 `{rank, goal_id, reason, evidence_refs}`，evidence_refs 必须指向具体的行为事件 ID / commit / Goal 进展记录，不允许无引用的理由。
3. **克制阈值**：候选为空或全部低于置信度阈值时，不生成任何输出（区别于"生成了一条平庸建议"）。
4. **产出**：写入 `.agent/next_actions.json`，字段包含 `shown_at`（初始为空，展示后回填，用于去重）。

### 4.3 展示时机与渠道

日报和推荐两类信息**分开展示，不合并成一条**，避免用户分不清"这是回顾还是建议"：

| 渠道 | 触发条件 | 展示内容 |
|---|---|---|
| CLI/daemon 启动打印 | 存在 `shown_at` 为空的日报摘要或推荐条目 | 各一行摘要，如"昨天：mini_agent 提交 5 次，wiki 计划 P4 完成度 80%→100%" / "💡 建议：wiki 提取层 O3 已停滞 12 天，`/next` 查看详情" |
| Kanban 看板 | 常驻 | 新增"今日/昨日日报"卡片 + "建议"卡片（区别于任务栏），点开显示完整内容与 evidence |
| daemon 主动推送 | 仅当"注意力错配"信号连续超过设定时长（如 2 小时）| 走已有多客户端推送通道，一次会话最多推送一次，避免打断式骚扰 |

启动打印逻辑挂在 `cli/repl.py` 启动流程里，读一次两份文件里 `shown_at` 为空的条目，打印后立即回填时间戳——复用现有 storage 原子写模式，不引入新的并发风险。

### 4.4 决策画像层（decision_profile_builder）

新增 `evolution/decision_profile_builder.py`，定位为全新模块，分三层递进，刻意做得比前两者更保守：

**第一层（已有）**：`decision_extraction.py` 产出的单条决策事实，不变。

**第二层（新增，周级归纳）**：定期扫描过去若干周的决策记录，用 LLM 做归纳而非生成，格式固定为：

```
模式：优先选择"零回归"而非"更快上线"
证据：[decision_id: xxx, xxx, xxx]（至少 3 条独立证据才成立一条模式）
置信度：随独立证据数量递增
```

不足 3 条证据的模式不落地，避免单次事件被过度泛化为"价值观"。

**第三层（画像文档）**：`.agent/user_value_profile.md`，纳入 wiki 体系，字段包含 `pattern / confidence / evidence_refs / first_observed / last_reinforced / contradicted_by`。**矛盾处理是关键设计点**：新证据与已有模式冲突时，不直接覆盖，而是记录到 `contradicted_by` 并下调置信度，让"偏好本身在变化"这件事也成为一种可追溯的信息，而不是被静默抹掉。

**画像的两个初期用法（明确限定范围，暂不做更激进的用法）**：

1. 回答"为什么当初做了/放弃了 X"类问题：纯检索式，先给结论再展开引用具体 decision_id，不脑补细节。
2. 给 next_action_advisor 的排序做加权：如画像中已有"偏好先做架构再做 UI"这类高置信度模式，则在同优先级候选间按此模式调整排序，仅影响排序，不替代候选本身。

"模拟用户直接做决策"这类更激进的数字分身用法明确排入远期，且需要用户主动开关，不默认启用——提炼错的画像造成的负面体验（用户觉得"被误解"）比没有这个功能更糟。

cron 接入：新增内置 job `sys:decision_profile_update`，`interval:604800`（周级）。

## 五、改进计划

按风险从低到高、依赖关系从少到多排序，分三个阶段推进，每个阶段结束后先观察实际效果再决定是否继续：

**阶段一：日报融合与展示（daily_digest）**
- 实现 `evolution/daily_digest.py`，合并 behavior 聚合 + Goal 进展差值 + git commit
- 接入 `sys:daily_digest` cron job
- 实现启动打印摘要 + 看板"日报"卡片
- 验证目标：观察用户是否真的每天会看这条摘要，作为后续两个阶段是否值得做的先验证据

**阶段二：推荐排序层（next_action_advisor）**
- 先只做候选合流与规则层（复用 soft_goal_deriver + 新增"注意力错配"规则），**不接 LLM 排序**，跑一段时间观察规则本身是否准（停滞判断/错配判断是否符合直觉）
- 规则验证通过后，再接入 LLM 排序与理由生成层
- 接入 `sys:next_action_digest` cron job（`interval:10800`）
- 实现启动打印 + 看板"建议"卡片 + 有阈值限制的 daemon 推送

**阶段三：决策画像（decision_profile_builder）**
- 待阶段一、二积累的行为/目标数据量足够（建议至少运行 4～6 周后再启动本阶段，避免样本不足导致模式失真）
- 先实现第二层归纳逻辑，人工检查产出的模式是否合理、证据是否真实存在
- 确认归纳质量后再接入画像文档产出与两个初期用法（决策问答检索、推荐排序加权）
- 更激进的"模拟用户决策"用法明确不在本轮计划内，留待用户主动开关的独立后续设计


## 六、实施记录（首轮落地）

以下为按本方案完成的第一轮代码落地，供后续迭代参照。

**阶段一：日报融合（已实现）**
- 新增 `evolution/daily_digest.py`：合并 `behavior/analyzer.py` 的行为聚合、
  `GoalBacklog` 当天进展变化，产出 `.agent/daily_reports/<日期>.json/.md`
- 新增 `/digest daily [日期]` 命令（`cli/commands/digest_cmd.py`）
- 新增内置 cron job `sys:daily_digest`（`cron:0 22 * * *`，默认开启）
- `cli/repl.py` 启动时自动打印未展示过的日报一行摘要，展示后回填 `shown_at` 去重

**阶段二：主动推荐（已实现规则层，LLM 排序层留待观察后再启用）**
- 新增 `evolution/next_action_advisor.py`：候选来自"停滞目标"（优先级≥1 且
  超过 7 天无进展）和"注意力错配"（6 小时窗口内单一活动占比超 50% 且与任何
  active Goal 无关键词重合）两条规则，默认规则排序，`rank_with_llm=True` 时
  可切换到 LLM 排序（要求引用已有 evidence_refs，失败自动回退规则排序）
- 新增 `/next [refresh]` 命令（`cli/commands/next_action_cmd.py`）
- 新增内置 cron job `sys:next_action_digest`（`interval:10800`，默认开启，
  候选为空时不生成输出）
- 启动打印钩子同日报一起接入 `cli/repl.py`，两者分行展示，不合并

**阶段三：决策画像（已实现归纳与矛盾处理逻辑，cron job 默认关闭）**
- 新增 `evolution/decision_profile_builder.py`：复用 `wiki/decision_writer.py`
  已有的决策页加载逻辑，要求归纳出的模式至少 3 条独立证据支持才落地，矛盾
  证据记录到 `contradicted_by` 并下调置信度而非覆盖
- 新增 `/profile [update]` 命令（`cli/commands/profile_cmd.py`）
- 新增内置 cron job `sys:decision_profile_update`（`interval:604800`，
  **默认 `enabled: False`**，待阶段一二稳定运行数周、决策记录数据积累
  足够后由用户手动 `/cron enable` 开启）

**尚未落地、留待后续迭代的部分（第一轮遗留，已在第二轮全部处理，见下）**
- ~~看板前端的"日报"/"建议"卡片~~ → 第二轮已实现
- ~~"注意力错配"持续超时的 daemon 主动推送~~ → 第二轮已实现
- ~~decision_profile 对 next_action_advisor 排序结果的加权~~ → 第二轮已实现
- 反拖延式"计划 vs 实际"对比、"模拟用户决策"等更激进用法，按方案要求
  明确不在本轮范围内（第二轮同样不做）


## 七、实施记录（第二轮：补齐遗留 + 修复一个分发 Bug）

### 7.0 前置修复：`/digest` `/profile` 命令分发冲突（Bug，非新功能）

排查第一轮遗留项时发现：`cli/repl.py` 命令分发链里，`/digest` 和 `/profile`
各自存在两个 `elif name == "..."` 分支——排在前面的是既有功能（`/profile`
强制刷新用户画像、`/digest` 显示自主活动摘要），排在后面的才是本方案新增的
日报/决策画像命令。Python 的 `elif` 链一旦命中就不会再往下比较，导致新增的
两个分支**从代码提交那天起就从未真正执行过**，第一轮"已实现"的说法只在
"文件里有这段代码"的意义上成立，命令本身不可达。

修复方式：
- `/digest daily [日期]` 改为只在显式带 `daily` 子命令时才路由到新逻辑
  （`elif name == "digest" and parts[1:2] == ["daily"]`），不带参数时继续走
  原有的"自上次交互以来的自主活动摘要"分支，两者不再互斥。
- 决策画像命令改名为 `/decision_profile`（不再叫 `/profile`），与已经在用的
  `sys:decision_profile_update` cron job 命名保持一致，同时彻底避开与既有
  `/profile`（强制刷新用户画像）的重名。所有相关文档、命令行提示
  （`cli/parser.py`、`ui/terminal.py` 自动补全列表）、cron job 的
  `task_template` 文案同步更新。

### 7.1 配置化：新增 `DigestAdvisorConfig`（`agent_config.json` → `digest_advisor`）

第一轮所有阈值（停滞天数、注意力窗口/占比、是否接 LLM 排序等）都是模块级
写死常量，无法通过配置文件调整，也没有清晰的功能总开关。第二轮新增
`config/models.py::DigestAdvisorConfig`，通过 `config/loader.py` 从
`agent_config.json` 的 `digest_advisor` 字段解析，覆盖：

| 字段 | 默认值 | 作用 |
|---|---|---|
| `daily_digest_enabled` | `true` | `sys:daily_digest` job 首次注入时的初始 enabled |
| `daily_digest_startup_print_enabled` | `true` | 启动时是否打印日报摘要一行 |
| `next_action_enabled` | `true` | `sys:next_action_digest` job 首次注入时的初始 enabled |
| `next_action_startup_print_enabled` | `true` | 启动时是否打印推荐摘要一行 |
| `next_action_rank_with_llm` | `false` | 是否启用阶段二第 2 步的 LLM 排序层 |
| `next_action_stale_days` | `7.0` | 停滞目标判定天数阈值 |
| `next_action_stale_priority_floor` | `1` | 参与停滞判定的最低优先级 |
| `next_action_attention_window_hours` | `6.0` | 注意力错配观察窗口（小时） |
| `next_action_attention_mismatch_ratio` | `0.5` | 注意力错配占比阈值 |
| `next_action_profile_weighting_enabled` | `false` | 是否用决策画像对推荐排序加权 |
| `next_action_profile_weighting_min_confidence` | `0.5` | 参与加权的模式最低置信度 |
| `next_action_push_enabled` | `false` | 是否启用注意力错配 daemon 主动推送 |
| `next_action_push_threshold_hours` | `2.0` | 持续多久才触发一次推送 |
| `next_action_push_max_per_session` | `1` | 同一 daemon 会话内每个信号最多推送次数 |
| `decision_profile_enabled` | `false` | `sys:decision_profile_update` job 首次注入时的初始 enabled |
| `decision_profile_min_evidence_count` | `3` | 归纳一条模式所需的最少独立决策记录数 |

这些字段只影响：cron job **首次**被写入 `cron_jobs.json` 时的初始 `enabled`
状态（用户之后通过 `/cron enable|disable` 的手动修改不会被配置覆盖）、
`next_action_advisor.py` 里此前写死的模块级常量、以及启动打印的开关。

### 7.2 Kanban 看板：日报 / 推荐 / 决策画像卡片

新增三个只读 REST 端点（`api/routes.py`）：`GET /v1/digest/daily[?date=]`、
`GET /v1/next_actions`、`GET /v1/decision_profile`，均直接读取对应模块已经
落盘的 JSON/Markdown 文件，不重复触发生成（避免看板刷新页面时意外触发一次
LLM 调用）。`apps/mini_agent_kanban/client.py` 新增 `daily_digest()` /
`next_actions()` / `decision_profile()` 三个方法，`app.py` 的"目标看板" Tab
在 Objective 执行进度下方新增三栏并排卡片展示。

### 7.3 decision_profile → next_action_advisor 排序加权（已接入）

`next_action_advisor._apply_profile_weighting()`：读取
`decision_profile_state.json` 中置信度 ≥
`next_action_profile_weighting_min_confidence` 的模式，若候选的
title/reason 与某条模式的关键词有重合，在**同一类别内部**（`stale_goal`
内部或 `attention_mismatch` 内部）优先排到前面。不跨类别提升（`stale_goal`
始终整体先于 `attention_mismatch`），不新增候选、不影响候选发现逻辑本身，
遵循方案里"仅影响排序，不替代候选本身"的限定。默认关闭
（`next_action_profile_weighting_enabled=false`），因为画像本身的归纳质量
需要先经人工检查确认。

### 7.4 注意力错配 daemon 主动推送（已接入）

`next_action_advisor.check_persistent_attention_mismatch()`：每次
`AutonomousLoop._tick_passive()` 执行时（不经过 LLM 对话轮次，避免让模型
自己判断"要不要提醒"这种需要精确跨 tick 状态跟踪的逻辑），重新扫描当前
错配信号，与 `.agent/attention_mismatch_state.json` 里记录的
"首次发现时间/推送次数"比对：
- 信号首次出现：记录首次发现时间，本次不推送
- 持续存在且已超过 `next_action_push_threshold_hours`、且该信号推送次数
  未超过 `next_action_push_max_per_session`：生成一条推送消息
- 信号消失：清除跟踪记录，下次重新计时

推送本身复用 `InputQueue.enqueue(initiator="scheduled")`——与
`CronScheduler` 提交任务走的是同一条通道，因此推送消息会像普通一轮对话
一样通过已有的多客户端 SSE 推送流转发给所有连接中的客户端（看板/微信/
移动端），不需要另外新建一套推送机制。总开关默认关闭
（`next_action_push_enabled=false`），避免默认状态下产生打断式提醒。

### 7.5 决策画像证据数量阈值配置化

`decision_profile_builder.py` 的 `MIN_EVIDENCE_COUNT` 模块常量保留作为
默认值，`generate_decision_profile()` / `_llm_summarize_patterns()` 新增
`min_evidence_count` 参数，`/decision_profile update` 命令从
`digest_advisor.decision_profile_min_evidence_count` 读取并传入，不再写死。

### 7.6 本轮仍未做、维持第一轮结论的部分

反拖延式"计划 vs 实际"对比、"模拟用户直接做决策"等更激进的数字分身用法，
仍然明确不在计划内——这类功能一旦画像归纳有误，产生的是"被误解"的负面
体验，比没有这个功能更糟，需要用户主动开关的独立后续设计，不通过本方案
的迭代顺带实现。


