# 成长顾问 × Goal/Cron 打通改进方案

> 前置阅读：`next_doc/growth_advisor_design.md`（P1 原始方案）、
> `docs/growth-advisor-guide.md`（现状整理版）、
> `next_doc/goal_cron_binding_plan.md`（Goal ⇄ Cron 绑定机制）、
> `src/mini_agent/perception/goal_backlog.py`、
> `src/mini_agent/evolution/goal_cron_bridge.py`。

## 0. 问题

成长顾问（`evolution/growth_advisor.py`）现在是一套完整闭环：
`memory 记忆 → 信号扫描 → 候选 → 调研报告 → 采纳/忽略反馈 → 30天回访`。

但这套闭环从头到尾**只读 memory_store**，跟另一套同样成熟的闭环——
`GoalBacklog`（用户的长期目标/子目标）+ `goal_cron_bridge`（目标的周期性
自动推进）——完全没有交叉：

- 成长顾问发现"你好像对 A 方向很感兴趣"，但不知道用户到底有没有把 A
  立成一个 Goal 去推进。
- 用户已经有一个 Goal 长期 `active` 但 `last_touched_at` 很久没动、
  或者绑定的 cron 周期任务反复 `failed`，这些强信号完全没有反馈进
  成长顾问的候选/回访机制。
- 30 天回访时，唯一能用的"用户是不是还在关注"的证据是 memory 关键词
  证据数走势（`_topic_trend_rising`），比起直接看对应 Goal 的
  真实状态，是一种更弱、更间接的代理指标。
- 候选被采纳后，除了等 30 天问一句"有没有推进"，没有任何机制帮用户
  把"这是个值得投入的方向"落地成一个可执行、可追踪的 Goal。

## 1. 目标（本轮范围）

不改动 `GoalBacklog` / `goal_cron_bridge` 的核心职责，只在
`growth_advisor.py` 里新增一层"桥接"逻辑，分三个可独立上线的阶段：

- **阶段 A：对齐分析（只读）** — 找出"有兴趣信号但没有对应 Goal"和
  "已经链接到 Goal 但该 Goal 停滞"的方向，暴露给 CLI/诊断面板。
- **阶段 B：一键落地（候选 → Goal）** — 候选被采纳时，用户可以显式
  选择"以这个候选为基础创建一个 Goal"，调研报告正文作为 Goal 的
  `description`，候选反向记一个 `linked_goal_id`。
- **阶段 C：用 Goal/Cron 真实状态替代回访的代理指标** — 一个候选如果
  已经 `linked_goal_id`，30 天回访/回访问法优先看这个 Goal 的真实
  `status`/`last_touched_at`/`recurring`/`cycle_count`，而不是继续
  只看 memory 证据数走势；没有链接 Goal 的候选行为完全不变。

非目标（本轮不做，留给后续）：
- 不做"cron 周期任务反复失败 → 自动生成成长候选"的反向链路（证据源
  从 Goal 状态变成 cron 执行日志，需要单独设计 cron 执行历史的读取
  接口，工作量更大，留给下一轮）。
- 不做 autonomous 档位下的"自动创建 Goal"（涉及自主性边界，需要经过
  `stage9_plan.md` 第七节的档位讨论，本轮所有新增写操作都是用户
  显式触发）。
- 不改动看板前端（`apps/mini_agent_kanban`），本轮只做后端能力 +
  CLI，看板接入留给后续单独排期（接口设计上预留，函数返回值都是
  看板可以直接消费的结构）。

## 2. 阶段 A：对齐分析

新增函数 `goal_growth_alignment(paths, profile, *, goal_backlog=None,
min_confidence=0.5, stalled_days=21)`：

- 输入候选池：`growth_focus_areas`（信号扫描结果）中证据数达标的主题，
  以及 `GrowthBacklog` 里 `accepted` 状态的候选（两者取并集，按
  `dedupe_key` 去重）。
- 用简单的关键词包含匹配（复用 `normalize_title_key`）在
  `goal_backlog.all_nodes()` 里找 `level=="goal"` 且标题/tags 命中同一
  归一化 key 的节点——不引入 embedding，保持跟现有关键词表机制同等
  的复杂度量级。
- 输出两类结果：
  - `unmatched_interests`：命中但找不到对应 Goal 的方向列表，每项带
    `topic`/`evidence_count`/`confidence`（如果来自已采纳候选）。
  - `linked_goals`：候选已经通过阶段 B 显式 `linked_goal_id` 关联到
    某个 Goal 的方向，每项带 Goal 的
    `status`/`last_touched_at`/`recurring`/`cycle_count`/`stalled`
    （`stalled` = `status=="active"` 且
    `now - last_touched_at > stalled_days*86400`）。
- 纯只读聚合，不写任何状态，可随时安全调用（对齐现有
  `diagnostics_snapshot` 的"只读诊断"惯例）。
- 新增 CLI 子命令 `/growth align` 展示这两个列表。
- `diagnostics_snapshot()` 增加
  `goal_alignment.unmatched_interests_count` /
  `goal_alignment.stalled_linked_goals_count` 两个计数字段（明细走
  `/growth align`，跟现有"诊断面板只给计数，明细走专门入口"的惯例
  一致）。`goal_backlog` 不可用（比如没有项目路径/加载失败）时这两个
  字段整体缺省为 `None`，不影响诊断面板其余部分。

配置新增（`GrowthAdvisorConfig`）：
- `goal_alignment_enabled: bool = True`（规则式匹配零 LLM 成本，跟
  项目"默认零成本开启"的一贯原则一致）。
- `goal_alignment_stalled_days: int = 21`（独立于
  `followup_review_days`，"目标多久没碰算停滞"和"候选采纳多久后
  回访"是两个不同的默认值，不应该耦合）。

## 3. 阶段 B：候选 → Goal 一键落地

新增函数 `adopt_candidate_as_goal(paths, candidate, *, goal_backlog=None,
extra_tags=None) -> GoalNode`：

- 前置条件：候选必须已经有调研报告（`candidate.report_id` 非空）——
  没有报告就直接建 Goal，`description` 里没有实质内容，体验上不如
  先引导用户 `/growth report <id>` 生成一份。没有报告时函数抛
  `ValueError`，调用方（CLI）负责转成友好提示。
- `goal_backlog.add_goal(title=candidate.title, description=<报告摘要
  + 报告路径引用>, source="user", tags=["growth_advisor", <类别>])`。
  `source="user"`——这是用户显式点击"采纳并建目标"的结果，不是 Agent
  自主决定，跟 `source_initiator` 的语义（"谁触发了这次调用"）是两个
  维度，不冲突。
- `GrowthBacklog.set_linked_goal(candidate_id, goal.id)`（新增方法，
  `GrowthCandidate` 新增字段 `linked_goal_id: Optional[str] = None`，
  默认值兼容旧数据反序列化）。
- 候选状态如果还是 `pending`，顺带流转成 `accepted`（语义上"建了
  Goal 去推进"就是一种采纳，不需要用户再单独 accept 一次）；已经是
  `accepted` 则不变。
- CLI 新增 `/growth adopt-goal <candidate_id>`。

不做的事：不自动调用 `make_goal_recurring` 绑定 cron——是否要做成
周期性任务是用户对这个 Goal 的进一步决定，落地成 Goal 之后走既有的
`/goal recur` 系列命令即可，成长顾问不越界代replace Goal 管理的职责。

## 4. 阶段 C：回访优先用 Goal 真实状态

`pending_followups(paths, cfg=None, *, goal_backlog=None)` 和
`followup_question_hint(paths, candidate, *, cfg=None,
goal_backlog=None)` 都新增可选的 `goal_backlog` 参数（默认
`None`，不传时行为与现在完全一致，向后兼容）：

- 有 `goal_backlog` 且候选 `linked_goal_id` 命中一个存在的 Goal 时，
  新增函数 `_goal_progress_signal(goal_backlog, goal_id, *,
  stalled_days)` 返回 `"progressing" | "stalled" | None`：
  - Goal `status` 是 `completed`（子目标层面的"这个方向已经做完了"）
    → 直接判定候选 `followup_status="progressed"`，跳过主动询问，
    直接写回（复用 `record_followup`，避免用户还要手动确认一件已经
    显而易见的事）。
  - Goal `status` 是 `active` 且 `last_touched_at` 在 `stalled_days`
    内 → `"progressing"`，本轮跳过主动询问（比 memory 证据数走势更
    直接，且能覆盖"用户在推进但没怎么提到关键词"的情况，比如推进
    动作主要发生在代码/工具调用里，没留下多少带关键词的 memory）。
  - Goal `status` 是 `active` 但已停滞、或 `paused`/`abandoned`/
    `failed`/`cancelled` → `"stalled"`，正常展示回访卡片，问法换成
    Goal 专属的"这个方向对应的目标看起来有一阵没动了，要不要先放一放
    /需要我帮你重新规划一下"。
  - 找不到对应 Goal（比如被用户从 GoalBacklog 里删了/查询异常）→
    `None`，退化到原有的 memory 证据数走势逻辑，不报错。
- 没有 `linked_goal_id`，或调用方没传 `goal_backlog`（比如老的调用
  路径还没升级）→ 完全走原有逻辑，一行代码都不受影响。

CLI/cron 调用点（`growth_cmd.py` 的 `scan`/看板未来的回访卡片渲染）
在能拿到项目路径时顺带构造一个 `GoalBacklog(paths)` 传进去；拿不到就
不传，函数自身保证向后兼容，不会因为调用方没升级而报错。

## 5. 数据结构变更小结

- `GrowthCandidate` 新增字段：`linked_goal_id: Optional[str] = None`。
  `from_dict` 走既有的"未知字段过滤 + 已知字段兜底默认值"机制，旧数据
  天然兼容。
- `GrowthBacklog` 新增方法：`set_linked_goal(candidate_id, goal_id)`。
- `GrowthAdvisorConfig` 新增两个字段（见第 2 节），默认值保证不改变
  现有用户的行为观感（只是多了只读的对齐视图）。

## 6. 实施顺序与验收

1. 阶段 A（对齐分析，纯读）→ 补测试 → 更新
   `docs/growth-advisor-guide.md` 新增 2.9 节。
2. 阶段 B（候选 → Goal）→ 补测试 → 文档同步更新。
3. 阶段 C（回访接入 Goal 信号）→ 补测试 → 文档同步更新。

每个阶段独立可用、独立测试、独立提交说明，互不阻塞——即使只做完
阶段 A，也已经是一个对用户有价值的增量（看清"兴趣和目标是否对齐"）。

## 7. 与现有设计哲学的一致性检查

- **克制**：阶段 B 的"建 Goal"永远是用户显式触发的一次性动作，不
  在 `run_daily_cycle()` 里自动发生。
- **默认可用、可选增强**：阶段 A 默认开启但零 LLM 成本（纯关键词
  匹配，复用 `normalize_title_key`）；阶段 C 不引入任何新开关，纯粹
  是"有更好的信号就优先用"，且完全向后兼容不传 `goal_backlog` 的
  调用方。
- **不做本不该由这个模块负责的事**：成长顾问不接管 Goal 的生命周期
  管理（不自动改 Goal 状态、不自动建 cron），只做"建议 + 一键起步 +
  更聪明地判断要不要打扰用户"。
