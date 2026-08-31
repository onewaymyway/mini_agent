# 主动推荐排序（Next Action Advisor）

对应设计文档：`next_doc/proactive-recommendation-and-digital-persona-design.md` 第 4.2 节（阶段二）。

## 是什么

`evolution/next_action_advisor.py` 明确定位为 `soft_goal_deriver.py` 的"排序 +
讲道理"层，而不是重新做候选发现：

- `soft_goal_deriver` 负责"发现该不该新建一个 Goal"，写入 `GoalBacklog`
- 本模块负责"在已有信息里，这次该优先提醒用户哪一个、为什么"，**只读不写**

候选来源三类：

1. **停滞目标**：`GoalBacklog` 中优先级 ≥1 且超过 7 天无 `last_touched_at` 更新的
   Goal/Objective
2. **注意力错配**：最近 6 小时窗口内，某个 app/域名的时长占比超过 50%，且其名称与
   任何 active Goal 的 title/tags 都没有关键词重合
3. **活跃度走势上升**（`momentum_goal`，默认关闭，见下方专节）：最近一段窗口内
   `status_history` 变更次数明显增多的 Goal——跟"停滞目标"回答的是相反方向的
   问题，两者候选集合天然不重叠

候选为空时不生成任何输出（克制阈值），不会为了"有话可说"而凑一条平庸建议。

## 分步落地（改进计划要求）

1. **规则层（当前默认路径）**：只用上述两条规则筛选 + 固定优先级排序
   （停滞目标 > 注意力错配），不接 LLM。建议先跑一段时间，观察规则本身
   是否符合直觉。
2. **LLM 排序层（`rank_with_llm=True`）**：对规则筛出的候选做一次 LLM 调用，
   要求输出必须引用已有 `evidence_refs`，不允许引入候选之外的新理由；
   LLM 调用失败时静默回退到规则排序。

## 使用方式

```
/next            # 查看当前推荐（不重新计算）
/next refresh    # 重新扫描候选并排序
```

产出文件：`.agent/next_actions.json`，包含 `rank/kind/ref_id/title/reason/evidence_refs`。

## 定时任务

内置 cron job `sys:next_action_digest`，`interval:10800`（3 小时一次），
task_template 调用 `/next refresh`。候选为空时任务本身也会跳过输出。

## 配置（`agent_config.json` → `digest_advisor`）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `next_action_enabled` | `true` | 控制 `sys:next_action_digest` cron job **首次**被写入 `cron_jobs.json` 时的初始 `enabled` 状态 |
| `next_action_startup_print_enabled` | `true` | 是否在启动时打印排名第一条推荐的摘要 |
| `next_action_rank_with_llm` | `false` | 是否启用下面"分步落地"第 2 步的 LLM 排序层 |
| `next_action_stale_days` | `7.0` | 停滞目标判定天数阈值（覆盖模块常量 `STALE_DAYS`） |
| `next_action_stale_priority_floor` | `1` | 参与停滞判定的最低优先级（覆盖 `STALE_PRIORITY_FLOOR`） |
| `next_action_attention_window_hours` | `6.0` | 注意力错配观察窗口小时数（覆盖 `ATTENTION_WINDOW_HOURS`） |
| `next_action_attention_mismatch_ratio` | `0.5` | 注意力错配占比阈值（覆盖 `ATTENTION_MISMATCH_RATIO`） |
| `next_action_profile_weighting_enabled` | `false` | 是否用决策画像对同类候选做排序加权（见下） |
| `next_action_profile_weighting_min_confidence` | `0.5` | 参与加权的画像模式最低置信度门槛 |
| `next_action_push_enabled` | `false` | 是否启用"注意力错配持续超时"的 daemon 主动推送（见下） |
| `next_action_push_threshold_hours` | `2.0` | 同一错配信号需要连续检测到多久才推送一次 |
| `next_action_push_max_per_session` | `1` | 同一 daemon 会话内、同一信号最多推送次数 |
| `next_action_momentum_enabled` | `false` | 是否启用"活跃度走势上升"第三条规则（见下） |
| `next_action_momentum_window_days` | `14.0` | 判定"最近"的窗口天数 |
| `next_action_momentum_min_recent_events` | `2` | 窗口内至少要有这么多次状态变更才算"在加速" |

`rank_with_llm`/停滞天数/注意力窗口等参数以前是 `next_action_advisor.py`
里写死的模块级常量，现在改为优先从上表读取；`generate_next_actions()`
未显式传 `cfg` 时仍回退到模块常量，向后兼容。

## 活跃度走势规则（`momentum_goal`，已接入，默认关闭）

`next_doc/personal_researcher_and_coach_capability_gap_plan.md` C3。
`next_action_momentum_enabled=true` 时，`/next refresh` 会额外跑
`_find_momentum_goals()`：对每个 active Goal/Objective，把
`GoalNode.status_history` 的时间戳序列当成一串累计计数点，套用跟成长
顾问 P5-4"报告要不要刷新"同一套"最新点减窗口基线点"算法（抽成了共享
函数 `growth_advisor._recent_delta_from_series()`），算出最近
`next_action_momentum_window_days` 天内发生了多少次状态变更；达到
`next_action_momentum_min_recent_events` 门槛的 Goal 生成一条
`momentum_goal` 候选，提醒"这个方向最近正在被频繁推进，可能值得趁热
打铁"。

排序位置在 `stale_goal` 和 `attention_mismatch` 之间——`stale_goal` 是
明确的既定目标（用户已经承诺要做、只是被搁置了），`momentum_goal` 是
"正在发生的积极信号"，`attention_mismatch` 只是"可能"分心，三者按
确定性从高到低排列。

**默认关闭的原因**：用状态变更次数代表"活跃度"是一个比较粗的代理指标，
这条规则还没有跑过真实数据验证，是否真的比现有两条规则更有参考价值
有待观察——机制已经就位，需要用户在 `agent_config.json` 显式开启才会
生效。

## decision_profile 排序加权（已接入，默认关闭）

`next_action_profile_weighting_enabled=true` 时，`/next refresh`（含 cron job
触发的那次）会读取 `evolution/decision_profile_builder.py` 归纳出的、置信度
≥ `next_action_profile_weighting_min_confidence` 的模式，对候选做**排序内
加权**：候选的 title/reason 与某条模式的关键词有重合时，在**同一类别内部**
（`stale_goal` 内部或 `attention_mismatch` 内部）排到更前面。不跨类别提升——
`stale_goal` 整体依然先于 `attention_mismatch`，只影响同类候选的相对顺序，
不新增候选、不改变候选发现逻辑本身。默认关闭，因为这依赖 `decision_profile`
本身的归纳质量，建议先人工检查 `.agent/wiki/user_value_profile.md` 里的模式
是否合理，再开启加权。详见 `docs/decision-profile-guide.md`。

## 注意力错配 daemon 主动推送（已接入，默认关闭）

`next_action_push_enabled=true` 时，`evolution/autonomous_loop.py` 的
`_tick_passive()` 每次 tick 都会调用
`next_action_advisor.check_persistent_attention_mismatch()`：

- 用 `.agent/attention_mismatch_state.json` 跟踪每个错配信号（按 app/域名
  key）**连续**被检测到的起始时间
- 信号首次出现：只记录，不推送
- 持续存在且已超过 `next_action_push_threshold_hours`、且该信号推送次数未
  超过 `next_action_push_max_per_session`：生成一条推送消息
- 信号消失：清除跟踪记录，下次重新计时

推送走 `InputQueue.enqueue(initiator="scheduled")`——与 `CronScheduler` 提交
任务是同一条通道，因此会像普通一轮对话一样通过已有的多客户端 SSE 推送流
转发给所有连接中的客户端（看板/微信/移动端），不需要另外新建推送机制。
默认关闭，避免默认状态下产生打断式提醒；这也是与 daily_digest/next_action
"只在用户主动查看或启动时才展示一次"原则的刻意区别——只有这一项因为设计
目的就是"持续超时才提醒"，才允许在非用户主动请求的情况下产生一次新的
对话轮次。

## 启动展示与看板

- CLI/daemon 启动时若存在 `shown_at` 为空的推荐，打印排名第一条的摘要，例如：

  ```
  💡 建议：wiki 提取层 O3——已 12 天无进展记录，优先级 2（`/next` 查看全部）
  ```

  可通过 `digest_advisor.next_action_startup_print_enabled=false` 关闭。

- Kanban 看板"📌 目标看板" Tab 有一张"💡 主动推荐"卡片，对接只读端点
  `GET /v1/next_actions`，展示同一份 `next_actions.json` 里排名前几条的
  内容，不会因为看板刷新页面而重复触发计算。详见 `docs/kanban-dashboard-guide.md`。

## 命令行提示

`/next [refresh]` 已加入 `cli/parser.py` 的 `--help` 文本与 `ui/terminal.py`
的斜杠命令自动补全列表。

## 有意暂不做的事

- 不做"计划 vs 实际"式反拖延对比（需要用户主动声明当天计划），本机制刻意
  只做纯行为推断，不引入任何需要用户额外输入的环节。
- 不做"模拟用户直接做决策"这类更激进的数字分身用法，画像归纳有误时造成的
  "被误解"负面体验比没有这个功能更糟，需要用户主动开关的独立后续设计。
