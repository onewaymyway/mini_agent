# 实施记录：Personal Researcher / Personal Coach 缺口改进（全部七项）

> 对应计划文档：`personal_researcher_and_coach_capability_gap_plan.md`。
> 本记录覆盖全部七项：R1、C1（第一轮）、R2、C2（第二轮）、C3、C4、R3
> （第三轮，应用户要求跳过"先观察 C1/C2 效果"的建议前提，直接落地）。
> 第三轮的 C3/R3 配置默认关闭——机制完整实现，但需要用户显式开启才
> 生效，这是"代码写出来"和"默认打开影响所有用户"之间的折中。

## 1. R1：通用持续调研模板 `research_topic`

### 1.1 做了什么

- 新增 `src/mini_agent/perception/goal_execution_spec_templates/research_topic.json`。
  与 `growth_pursuit.json` 共享同一套骨架（追加式产出物 + 三个跨轮次
  `handoff_fields`：`covered_subtopics`/`open_questions`/
  `last_source_urls` + "本轮必须有实质性增量"的 `per_cycle_criteria`），
  唯一区别是去掉了"必须来自成长顾问候选"的前提，`keywords` 换成了
  通用调研场景的词（"持续研究"/"持续调研"/"持续追踪"/"跟踪进展"/
  "长期关注"/"调研主题"），产出物路径改为 `wiki/research/<topic-slug>.md`。
- 未新增任何判定逻辑——模板匹配复用了
  `goal_execution_spec.py::suggest_template()` 已有的关键词粗筛机制
  （对 title+description 做子串计数，命中最多的模板胜出），`research_topic`
  和 `growth_pursuit` 是同一套匹配路径下的两个平行候选，不会互相冲突
  （两者关键词集合没有重叠）。
- `src/mini_agent/storage/paths.py` 新增 `wiki_research_dir` 属性和
  `research_topic_path(slug)` 方法，与已有的 `wiki_growth_dir` /
  `growth_report_path()` 对称，供未来需要直接拼路径的调用方使用
  （目前 agent 执行时是从模板的 `naming_pattern` 字段读取路径规则，
  这两个 helper 是为了保持代码风格一致，不是当前链路的必需依赖）。

### 1.2 用户如何使用

- `/agent goals add "持续追踪 XX 项目的社区讨论进展"`（或对话里表达
  类似意图）创建 Goal 后，在生成执行规范时会命中 `research_topic`
  模板（前提是标题/描述里出现了模板 `keywords` 列表中的词，命中规则
  与其它模板一致，用户也可以在 UI 里手动改选）。
- 后续绑定周期性执行（如 `/agent goals recur <goal_id> "interval:86400"`），
  每轮会往 `wiki/research/<topic-slug>.md` 追加新内容，不会重复讲已经
  覆盖过的子话题。

### 1.3 没做的部分（对应计划 R3，按建议延后）

- 交叉验证质量门（R3）——计划文档明确不建议默认开启，需要先观察
  R1 落地后是否真的出现"事实性错误堆积"的问题。

（R2——信源可信度标注——已在本轮落地，见第 3 节。）

## 2. C1：Goal 的"长期方向"分组

### 2.1 数据模型

- `src/mini_agent/perception/goal_backlog.py`：
  - 新增 `Direction` dataclass（`id`/`title`/`created_at`/`description`），
    独立于 `GoalNode`，不参与任何执行/判定逻辑，纯展示聚合。
  - `GoalNode` 新增可选字段 `direction_id: Optional[str] = None`，已
    接入 `to_dict()`/`from_dict()`，不影响 `GoalNode.level` 的既有
    两值约束（`"goal"`/`"objective"`），也不参与 GoalJudge 判定。
  - `GoalBacklog` 新增 `_directions: dict[str, Direction]`，随
    `load()`/`save()` 一起持久化在同一份 `goals.json` 里（顶层新增
    `"directions"` 数组），跟着 `_locked()` 走同一把跨进程文件锁，
    没有引入新的锁/新的存储文件。
  - 新增方法：`add_direction` / `list_directions` / `get_direction` /
    `rename_direction` / `delete_direction` / `assign_direction` /
    `goals_by_direction`。`delete_direction` 只清空关联 Goal 的
    `direction_id`（置 None），不会级联删除 Goal 或影响其执行状态，
    与既有"删除父节点不破坏子节点执行状态"的取舍一致。

### 2.2 API

新增于 `src/mini_agent/api/routes.py`：

| Method | Path | 说明 |
|---|---|---|
| GET | `/v1/directions` | 列出全部长期方向 |
| POST | `/v1/directions` | 新建（`{"title","description"?}`） |
| PATCH | `/v1/directions/{id}` | 重命名/改备注 |
| DELETE | `/v1/directions/{id}` | 删除（关联 Goal 自动清空分组） |
| POST | `/v1/goals/{goal_id}/direction` | 关联/取消关联（`{"direction_id": str｜null}`） |

`GET /v1/goals` 的返回值新增 `directions` 字段（与 `goals`/
`objectives` 平级），看板一次请求即可拿到全部数据，不需要为分组视图
单独发请求。

### 2.3 看板 UI

- `apps/mini_agent_kanban/client.py` 新增对应的
  `directions()`/`add_direction()`/`update_direction()`/
  `delete_direction()`/`assign_goal_direction()` 方法。
- `apps/mini_agent_kanban/app.py` 新增 `_render_goal_direction_overview()`，
  在"📌 目标看板"tab 里以折叠区块"🧭 按长期方向聚合"展示（复用
  `_render_cycle_health_overview` / 成长主题地图同款"默认折叠 + 不
  额外发请求，直接用已经拿到手的 goals_data"的模式）：
  - 新建方向（表单）
  - 每个方向卡片：标题、关联 Goal 数、关联 Goal 列表（标题+状态）、
    删除按钮
  - 未分组 Goal → 选择目标 + 选择方向 → 关联按钮

### 2.4 没做的部分（对应计划 C3/C4，按建议延后）

- `next_action_advisor` 复用成长顾问证据走势规则（C3）——计划建议先
  观察 C1/C2 效果再评估是否投入。
- 全部 Goal 综合月报（C4）——明确依赖 C1 落地且需要先看分组是否被
  用户实际使用起来，C1 刚落地，暂不启动。
- CLI（`mini_agent goals ...`）目前没有补 direction 相关子命令，
  只能通过看板 UI 或直接调用 API 操作；如果后续证明分组是高频操作，
  可以补一版 CLI。

（C2——`decision_profile` 参与 Goal 创建提示——已在本轮落地，见第 4 节。）

## 3. R2：信源可信度标注

### 3.1 做了什么

- `research_topic.json` 与 `growth_pursuit.json` 的 `per_cycle_criteria`
  第三条（"新增内容块标注信息来源"）扩写为同时要求标注一个粗粒度
  可信度标签——`official`（官方文档/项目自身仓库或网站）/
  `community`（论坛、社区讨论、个人博客）/ `secondary`（转载、聚合站点、
  未标明原始出处）。
- `research_topic.json` 的 `special_constraints` 补一条说明：标签只是
  零成本粗判（按域名/来源性质判断），标注在内容块里即可（如
  "来源：xxx.com（community）"），不需要额外调用工具或 LLM 做语义级
  核实——与计划文档"轻量级、不引入额外 LLM 调用"的定位一致。

### 3.2 实现方式说明

这一项**没有新增任何 Python 代码**——`handoff_fields`/
`per_cycle_criteria`/`special_constraints` 都是执行规范模板里的纯文本
字段，最终作为 prompt 片段注入给执行 Goal 的 LLM（见
`goal_execution_spec.py` 里 `HandoffField`/`Criterion` 到 prompt 文本的
拼接逻辑），标签本身由 LLM 在追加内容块时按指引写入 wiki 页面正文，
不是由 Python 代码解析/校验的结构化字段。这与计划文档"规则可以简单到
零成本判定，不强求语义级判断"的定位吻合：既然连信源类型的判定本身
都不要求精确，标签校验也没有必要用代码强制。

### 3.3 影响范围

`growth_pursuit`/`research_topic` 两个模板都改了（计划文档 §1.3 的
表述聚焦在 Personal Researcher 场景，但 `last_source_urls` 去重字段
本身是两个模板共享的骨架，为保持两者行为一致，标签要求同步补齐）。
不影响任何已有 Goal 的历史内容——只影响之后新触发的周期。

## 4. C2：`decision_profile` 参与 Goal 创建时的提示

### 4.1 做了什么

- `src/mini_agent/evolution/decision_profile_builder.py` 新增
  `match_goal_against_profile(paths, text, min_confidence=0.6)`：
  跟 `next_action_advisor._apply_profile_weighting()` 同一套"关键词
  重合"判定口径（Goal 的 title+description 与某条高置信度模式的
  `pattern` 文本有词汇重合即命中，取匹配到的置信度最高的一条）。
  读取失败（画像从未生成过，`sys:decision_profile_update` 默认关闭
  时的常见情况）或没有命中时静默返回 `None`。
- `src/mini_agent/api/routes.py::add_goal()`：Goal 创建成功后调用一次
  该函数，命中时把匹配到的模式附在响应体的 `decision_profile_hint`
  字段里；不管命中与否，Goal 都正常创建，这一步不写回 `GoalNode` 任何
  字段，纯粹是路由层的一次性只读查询。
- 看板 `apps/mini_agent_kanban/app.py`：命中时把提示文本存进
  `st.session_state["_new_goal_decision_hint"]`，跨 `st.rerun()` 在
  "➕ 新建目标"表单上方用 `st.info()` 展示一次（`st.toast()` 装不下
  完整提示文本，改用这个模式），读取后立即清空，不会反复出现。
- CLI `src/mini_agent/cli/commands/goals.py::_cmd_add_goal()`：新增
  `paths` 参数（调用方 `handle()` 已经持有 `paths`，顺手传入），
  Goal 添加成功后同样跑一次匹配，命中时用 `R.print_info()` 打印一行
  提示。

### 4.2 用户如何使用

- 开启 `sys:decision_profile_update` cron job 并积累足够样本
  （`decision_profile_min_evidence_count`，默认至少 3 条独立决策证据）
  一段时间后，`wiki/user_value_profile.md` 会有一些高置信度模式。
- 之后在看板"➕ 新建目标"表单提交，或 CLI 敲
  `/agent goals add "标题"`，如果标题/描述与某条模式的关键词有重合，
  会看到一句"这个方向和你过去反复表现出的『XX』倾向一致（仅供参考）"
  的提示。

### 4.3 没做的部分

- 计划文档 C2 原文还提到"成长顾问生成候选待采纳时"也应该展示同样的
  提示——本轮只落地了"用户新建 Goal"这一个触发点（`POST /v1/goals`
  + CLI `/agent goals add`），成长顾问候选采纳流程（`accept_candidate`
  一类入口）尚未接入。这部分不复杂（可以直接复用
  `match_goal_against_profile()`），但涉及改动 `growth_advisor.py`
  这条已经比较成熟的链路，按"尽量不改变 growth_advisor.py 现有行为"
  的既定取舍（计划文档 0.3 节），留到确认这条提示在"新建 Goal"场景
  确实有用之后再评估是否补上。

## 5. C3：`next_action_advisor` 复用证据走势规则（第三轮，跳过"观察效果"前提直接实施）

> 用户明确要求跳过计划文档建议的"先观察 C1/C2 效果"前提，直接把机制
> 写出来。因此这里的落地方式是"代码完整实现，但配置默认关闭"——机制
> 存在，默认不生效，用户需要显式开启才会影响推荐排序。

### 5.1 做了什么

- `src/mini_agent/evolution/growth_advisor.py`：把 `_recent_evidence_
  delta()` 内部"最新快照点减去窗口基线点"这段纯计算逻辑抽成通用函数
  `_recent_delta_from_series(series, window_days)`——输入变成一串
  `(timestamp, cumulative_count)` 点，不再绑定"必须是成长顾问的证据
  快照文件"这个前提。`_recent_evidence_delta()` 本身改为薄封装，读盘
  逻辑不变，对外行为完全一致。
- `src/mini_agent/evolution/next_action_advisor.py`：新增
  `_find_momentum_goals()`——用 `GoalNode.status_history` 的时间戳序列
  （Goal 没有独立的"证据快照"文件，状态变更次数的时间分布是目前最
  省成本的活跃度替代信号）套用上面那个共享函数，产出新的候选类型
  `momentum_goal`（"最近活跃度上升"，跟 `stale_goal`"太久没碰"回答的
  是相反方向的问题，两者候选集合天然不重叠）。接入 `generate_next_
  actions()`，仅当 `cfg.next_action_momentum_enabled=True` 时才会跑；
  `_rule_based_rank()` 里的优先级顺序调整为
  `stale_goal < momentum_goal < attention_mismatch`。
- `src/mini_agent/config/models.py::DigestAdvisorConfig` 新增三个字段：
  `next_action_momentum_enabled`（默认 `False`）、
  `next_action_momentum_window_days`（默认 14，与成长顾问 P5-4 的
  "最近突增"窗口口径一致）、
  `next_action_momentum_min_recent_events`（默认 2，避免 1 次状态变更
  就被误判成"在加速"）。

### 5.2 如何开启

在 `agent_config.json` 的 `digest_advisor` 块里设置
`"next_action_momentum_enabled": true`，`sys:next_action_digest` cron
job（或 `/next refresh`）下一次运行就会带上这条规则。

### 5.3 已知局限

- 用状态变更次数代表"活跃度"是一个比较粗的代理指标——比如"手动改了
  一次优先级"和"完成了一整轮周期性执行"在 `status_history` 里可能长得
  差不多（前者未必对应，因为改优先级不写 status_history，只有状态真正
  变化才追加记录，见 `set_status()` 的去重逻辑；但"状态在短时间内多次
  流转"本身跟"这个 Goal 正在被认真推进"并不总是等价，例如误操作导致的
  反复切换状态也会被计入）。这正是计划文档要求默认关闭、需要先观察
  真实效果的原因，机制已经就位，是否值得默认打开取决于后续观察。

## 6. C4：月度复盘扩大到全部 Goal（第三轮）

### 6.1 做了什么

- `growth_advisor.py::monthly_retrospective_summary()` 新增可选参数
  `goal_backlog`；传入时结果里新增 `goal_overview` 字段，来自新增的
  `_goal_overview_for_retrospective()`：统计全部 `level="goal"` 节点的
  总数、按 `status` 的分布、以及按 C1 的 `direction_id` 分组情况
  （每个方向下的 Goal 数 + active 数，未分组的归到"（未分组）"）。
  不传 `goal_backlog`（默认 `None`）时行为与改动前完全一致，
  `goal_overview` 键不会出现在返回值里。
- CLI `growth_cmd.py::/growth retrospective` 现在会加载 `GoalBacklog`
  并传入，多打印一段"全部 Goal 概览"（跟成长顾问自己的候选统计分开
  展示，不混排——两者是不同粒度的信息）。
- API 路由 `GET /v1/growth/...`（月度复盘那个聚合端点）同步传入
  `goal_backlog`，加载失败时静默降级为不带 `goal_overview`（`try/except`
  包裹，不拖垮整份月报）。
- `cron_scheduler.py` 里 `sys:growth_monthly_retrospective` 这个 cron
  job 的 `description`/`task_template` 文本更新，说明现在也会覆盖全部
  Goal 概览。

### 6.2 兼容性

`goal_overview` 是可选新增字段，任何异常（`GoalBacklog` 加载失败等）
都被 `try/except` 吞掉、不影响函数其余部分正常返回；旧调用方不传
`goal_backlog` 参数完全不受影响。

## 7. R3：交叉验证质量门（第三轮，跳过"观察效果"前提直接实施）

### 7.1 做了什么

- `src/mini_agent/config/models.py::GoalExecutionSpecConfig` 新增
  `research_quality_gate_enabled: bool = False`。
- `GoalExecutionSpecBuilder.build_draft()`：加载 `research_topic`/
  `growth_pursuit` 模板、且该配置开启时，在拼进 prompt 的模板骨架
  副本（不修改磁盘上的模板文件/`load_template()` 返回的原始 dict）的
  `per_cycle_criteria` 里追加一条要求——"本轮结束前对新增内容做一次
  自我核验，标注置信度（高/中/低）"。只在生成第 1 版草稿时生效（
  `revise()` 不使用模板骨架，不涉及这个逻辑）；用户后续对这份草稿的
  任何手改/确认流程完全不受影响。

### 7.2 实现方式说明

**没有引入额外的独立 LLM 调用**——计划文档原文允许"类似让 agent 对
本轮新增内容做一次自我核验"，这里的实现是把这个要求作为 prompt 里
`per_cycle_criteria` 的一条，由执行 Goal 的 agent 在同一次执行过程里
多想一步、多写一段自我核验文字，而不是额外起一次独立的 LLM 请求去
"审查"已经生成的内容。这样成本增量只是"这一轮多花一点思考/输出",
不是"翻倍调用次数"，比计划文档担心的"额外一次 LLM 调用"更轻量。

### 7.3 如何开启

在 `agent_config.json` 的 `goal_execution_spec` 块里设置
`"research_quality_gate_enabled": true`，之后新建/重新生成的
`research_topic`/`growth_pursuit` 模板草稿会自动带上这条自我核验要求。
已经生成过的执行规范不会被追溯修改——用户需要重新生成（`/agent goals
spec generate` 或看板对应按钮）才会应用新的骨架。

### 7.4 已知局限

- "自我核验"依赖执行 agent 认真执行这条指引，本质上是软约束（跟
  R2 的信源标签一样，`verification_method` 都是 `manual_review`，
  没有代码层面的强制校验）。这正是计划文档要求默认关闭的原因——
  收益能否覆盖"额外思考步骤挤占本轮篇幅"这个隐性成本，需要观察真实
  使用情况才能判断，机制已经就位。

## 8. 兼容性说明（更新）


- `goals.json` 新增的 `"directions"` 顶层键、`GoalNode.direction_id`
  字段对旧数据完全向后兼容：`Direction.from_dict()`/
  `GoalNode.from_dict()` 均用 `.get()` 兜底默认值（`directions` 缺失时
  兜底空列表，`direction_id` 缺失时兜底 `None`），旧版本写入的
  `goals.json` 可以被新代码直接加载，不需要迁移脚本。
- `research_topic.json` 是纯新增文件，不改变任何已有模板/已有 Goal
  的行为；`growth_pursuit` 模板改动仅限 `per_cycle_criteria` 文本描述，
  不涉及 `handoff_fields`/`deliverables` 等结构性字段，正在进行中的
  周期不受影响（下一轮触发时 prompt 会带上更新后的文本）。
- `add_goal()` 响应体新增的 `decision_profile_hint` 字段是可选的
  （只在命中时出现），旧版本前端/CLI 不读这个字段也完全不受影响，
  纯粹是新增能力，不改变既有响应体的必需字段。
- `_cmd_add_goal()` 新增的 `paths` 参数带默认值 `None`，其它调用方
  （如果存在）不传这个参数时行为等同于跳过 C2 提示，不会报错。
- C3/C4/R3 三项新增配置字段（`next_action_momentum_enabled`/
  `research_quality_gate_enabled`）均带默认值且默认为 `False`/不影响
  既有行为，`AppConfig`/`DigestAdvisorConfig`/`GoalExecutionSpecConfig`
  均为 dataclass，旧的 `agent_config.json`（不含这些新键）加载时会
  用默认值兜底，不需要迁移脚本。
