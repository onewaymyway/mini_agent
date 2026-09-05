# 个人助手体验改进方向盘点（目标树 / 成长顾问 / 能力学习 / 通知）

- **版本**: v1.2——方向 1（IM webhook 通知渠道）、方向 2（profile
  接入 situational_relevance）、第 4 节开放问题（跨系统"不感兴趣"
  信号，只读标注版本）已实施，详见文末"实施记录"；方向 3（三个已知
  收尾项）尚未实施。
- **背景**: 用户要求跳出单点 bug 修复，围绕"目标树、成长顾问、能力
  学习"等主动性子系统，思考还有哪些方向能让整体更好地服务用户、更像
  一个真正的个人助手。
- **方法**: 先通读现有 next_doc（201 份文档）里跟"主动性/个人助手"
  直接相关的设计，确认哪些已经做了，避免重复造轮子；再结合代码实际
  读取情况，找真正还没被覆盖的缺口。

## 0. 先说结论：这块地基比预想的成熟得多

在动手盘点新方向之前，必须先如实说明：`next_doc/
initiative_systems_unification_plan.md` 已经系统性地把"目标树 / Goal
系统 / 成长顾问 / 能力学习"四个主动性子系统的整合做完了，而且是**四个
阶段全部已完成**（代码和测试都核实过）：

1. **统一收件箱**（`perception/initiative_inbox.py`）：三条候选管线
   （soft_goal_deriver / growth_advisor / capability_learning）在看板
   上收敛成一个带分类筛选的"📥 主动建议" tab，不是三个互不相关的 tab。
2. **采纳后统一接入目标树执行**：`capability_learning` 补齐了
   `adopt_topic_as_goal()`，跟 `growth_advisor` 的 `adopt-goal` 对称；
   新增 `perception/initiative_push_budget.py` 做跨系统共享的"今日
   主动推送预算"总闸（growth_advisor 和 capability_learning 已接入，
   默认关闭，opt-in）。
3. **共享调研服务 + 双向知识沉淀**：抽出 `evolution/research_service.py`
   给未来新调用方复用；用户"认真采纳"一份成长顾问报告后可以自动回写
   进 wiki（`promote_growth_report_to_wiki()`，opt-in）。
4. **处境模型驱动的候选排序**：`perception/situational_relevance.py`
   用当前 active 的 WorkThread + GoalNode 计算候选跟用户当下处境的
   相关度，接入统一收件箱做排序参考。

`growth_advisor` 自己的忽略反馈回路也做得比预期精细：不是简单"忽略过
就不再推"，而是按**主题类别**统计历史采纳/忽略次数，做加权冷却、
区分"确实采纳过只是没空推进"和"真的不感兴趣"、甚至有单独的"报告质量
差导致的 dismiss"信号防止被错误合并进方向层面的负反馈。

所以下面列的不是"从零发现的大方向"，而是**在这套已经很扎实的地基上，
经过代码核实、明确"目前确实还没做"的具体缺口**，按价值/成本排序。

## 1. 缺口一：主动产出的"最后一公里"投递渠道太单薄

### 现状（代码核实）

`notification/dispatcher.py` 提供了可扩展的 `NotificationChannel`
注册机制，但目前只有两个已注册渠道：
`notification/channels/{email.py, kanban.py}`。也就是说，成长顾问的
调研报告、能力学习的通知、统一收件箱里的新建议——用户如果不主动打开
看板、不勤查邮箱，就完全感知不到。

`next_doc/weixin_mini_agent_design.md` 已经有一份完整的微信小程序
接入设计（身份映射、斜杠指令、流式回复、权限审批、跨机部署），但检索
代码（`grep -rn "weixin\|wechat"`）确认**没有对应实现**，只是文档
存在。对于一个"个人助手"定位的产品，这是最直接影响"用户能不能感知到
Agent 在主动帮他做事"的一环——本次反馈的起点（画像看板 20 天没更新
才被发现）某种程度上也是这个问题的一个侧面：用户需要主动打开看板才能
发现异常，而不是异常本身会主动找到用户。

### 建议方向

不需要照搬完整的微信小程序方案（那是一整套独立工程），成本最低、
收益最直接的是先给 `NotificationChannel` 加一个**轻量 IM webhook
渠道**（企业微信群机器人 / Server 酱 / Bark / Telegram Bot 任选一到
两个，架构上是同一种"POST 一条 JSON 到某个 URL"的模式，跟
`EmailChannel` 的接入成本相当）。这样成长顾问周报、能力学习通知、
（未来的）画像刷新异常提醒，都能在用户手机上"响一下"，而不是需要
用户想起来去看。

## 2. 缺口二：`situational_relevance` 没有用上刚补的 `profile`

### 现状（代码核实）

`perception/situational_relevance.py` 的处境信号只有两类：active
的 `WorkThread` 和 active 的 `GoalNode`。上一轮我们刚打通的
`profile.derived`（`summary`/`tech_stack`/`habits`，现在还接入了
目标树快照）完全没有参与候选排序——也就是说，"用户是谁、擅长什么、
一贯的工作习惯是什么"这一层信息，目前只用来给看板"Agent 对你的了解"
板块做展示，没有反过来影响"哪些候选建议应该被排得更靠前"。

### 建议方向

把 `profile.derived.tech_stack`/`habits` 作为 `situational_relevance`
的第三类信号源（跟 WorkThread/GoalNode 同样的 bigram+Jaccard 打分
方式，不需要新算法），一个候选标题如果跟用户画像里的技术栈/习惯高度
相关，也应该获得处境相关度加成——这本质上是让"个人画像"从一个纯展示
组件，变成真正参与决策的基础设施，跟本次改进的方向二（目标树接入
画像）是同一条思路的延续：**让分散的"关于用户的理解"互相打通，而不是
各自为政**。

## 3. 缺口三：`initiative_systems_unification_plan.md` 里明确标注的三个收尾项

这三条不是我新发现的，是那份方案文档自己在阶段二/三实施记录里写明
"留待后续"的，列在这里是为了不遗漏、也方便你判断要不要现在收口：

1. **watchlist 通知节流未接入 `initiative_push_budget` 总闸**——
   growth_advisor/capability_learning 已经接入跨系统推送预算总闸，
   watchlist（关注对象/热点抓取那条线）还是独立节流，理论上用户某天
   可能同时收到"总闸允许的 3 条"+"watchlist 独立节流允许的 N 条"，
   总量控制不完全统一。
2. **`POST /v1/growth/candidates/{id}/adopt_goal` API 路由未读取
   `cfg`**——导致这个入口触发的"认真采纳"不会触发回写 wiki（CLI 和
   自动采纳两个入口已经接好，只有这个 API 路由用 `cfg=None`）。
3. **`make_web_search_retriever()`/`make_agent_retriever()` 未委托给
   `research_service`**——两边逻辑重复但功能一致，收益是消除重复
   代码，风险是这两个函数在 cron 无人值守路径上，改动要谨慎。

## 4. 值得关注但暂不建议现在做的方向（开放问题，先记录）

- **跨系统的"不感兴趣"信号共享**——**已实施（只读标注版本，见文末
  实施记录）**。原本的顾虑（growth_advisor 面向用户成长、
  capability_learning 面向 Agent 知识，两边"不感兴趣"语义不完全
  等价，贸然合并可能错误压低有效候选）通过"只标注、不抑制"的方式
  规避：不改变任何候选的 confidence/排序/生成逻辑，只在收件箱里
  提示"这条候选跟另一个系统里被反复忽略过的某条历史候选很相似"，
  是否要因此忽略仍然由用户自己判断。
- **微信小程序完整落地**：`weixin_mini_agent_design.md` 方案本身完整，
  但是一整套独立工程（身份映射、斜杠指令路由、跨机部署），不建议
  跟本轮的"轻量 IM webhook 渠道"（第 1 节）混在一起——后者是"让通知
  发得出去"，前者是"让用户能在微信里直接对话/审批"，是两个不同量级
  的投入，建议先做前者验证"即时触达"本身的价值，再评估要不要投入
  完整的小程序工程。

## 5. 优先级建议

| 方向 | 价值 | 成本 | 建议 |
|---|---|---|---|
| 1. IM webhook 通知渠道 | 高——直接解决"用户感知不到 Agent 在主动做事"的核心体验问题 | 低——复用现成的 `NotificationChannel` 扩展点 | **优先** |
| 2. profile 接入 situational_relevance | 中——让画像从展示组件变成决策输入，是本轮改进的自然延伸 | 低——复用 bigram+Jaccard 打分逻辑 | **优先** |
| 3. 三个已知收尾项 | 中——补齐一致性，风险各不相同 | 低（第1、2条）/ 中（retriever 委托，涉及 cron 敏感路径） | 可选，建议先做前两条 |
| 4. 跨系统不感兴趣信号 / 完整微信小程序 | 待观察 | 高 | 暂不做，记录为开放问题 |

以上待你确认要推进哪几条，再展开到实现级设计文档。

## 实施记录

### 方向 1：轻量 IM webhook 通知渠道

- 新增 `src/mini_agent/notification/channels/webhook.py`：
  `WebhookChannel`，只用标准库 `urllib.request`，不引入新依赖。支持
  `template` 配置区分请求体格式：`generic`（默认，JSON
  `{title,body,url}`，Bark v2 API 也用这个格式）、`wecom`（企业微信
  群机器人文本消息）、`server_chan`（Server 酱表单字段）。缺 `url`
  直接返回 `False`；请求异常/非 2xx 状态码同样返回 `False`，不向上
  抛异常，跟 `EmailChannel` 的失败处理风格一致。
- `src/mini_agent/notification/__init__.py`：新增
  `from ... import webhook as _webhook_channel  # noqa: F401`，跟
  `email`/`kanban` 两个既有渠道同样的注册方式（`@register_channel`
  装饰器在模块导入时生效）。
- `.agent/notification/config.yaml.example`：新增 `webhook` 渠道的
  配置样例（默认 `enabled: false`，opt-in）。
- 测试：`tests/test_notification_webhook_channel.py`（8 用例：渠道
  已注册、缺 url 不发起请求、三种 template 各自的请求体格式校验、
  非 2xx 状态返回 False、`urlopen` 异常返回 False 不传播、接入
  `NotificationDispatcher` 端到端验证）。
- **未做的部分**：没有做 `weixin_mini_agent_design.md` 里完整的微信
  小程序方案（身份映射、斜杠指令路由、跨机部署）——如方案第 4 节所述，
  这是两个不同量级的投入，本次只做"让通知发得出去"这一层。

### 方向 2：profile 接入 situational_relevance

- `src/mini_agent/perception/situational_relevance.py`：
  `load_situational_context()` 新增第三类信号来源——`UserProfileManager
  .load().derived` 里的 `tech_stack`/`habits`，每条独立作为一个
  `SituationalSignal`（`kind="profile_tech_stack"`/`"profile_habit"`），
  跟 WorkThread/Goal 用同一套 `_tokens()`/`_jaccard()` 打分逻辑，不
  引入新算法；读取异常或没有画像时静默返回空列表，不影响另外两路
  信号。
- 测试：`tests/test_initiative_stage4_situational_relevance.py` 新增
  `TestProfileSignalInSituationalContext`（3 用例：无画像不报错、
  tech_stack/habits 正确转换成信号、相关文本能打出非零分且命中的信号
  来源正确标记为 `profile_tech_stack`）。
- **设计取舍**：没有改 `initiative_inbox.py` 的调用方逻辑——
  `score_relevance()` 的接口不变，`load_situational_context()` 内部
  多了一路信号来源，调用方完全无感知，这是纯粹的信号源扩展而不是
  接口变更。

### 验证

在方向 1、2 涉及的模块上运行的回归测试（`test_profile.py`、
`test_goal_tree_report.py`、`test_memory_backfill.py`、
`test_initiative_stage4_situational_relevance.py`、
`test_initiative_inbox.py`、`test_notification_webhook_channel.py`）
共 **61 个用例全部通过**。另确认 `test_notification_dispatcher.py`
里一个失败用例（`test_kanban_writes_alert_record`，`FileNotFoundError`）
是改动前就存在的既有失败（脱离本次新增代码单独运行同样失败），
与本次改动无关。

### 遗留/待观察

- webhook 渠道目前只覆盖了三种最常见的个人推送服务模板，如果后续
  需要接入其它服务（比如 Telegram Bot API 的请求格式跟这三种都不
  一样），照 `_build_request()` 里的模式加一个新的 `template` 分支
  即可，不需要改动 `WebhookChannel` 类结构本身。
- `situational_relevance` 里 profile 信号目前只用了
  `tech_stack`/`habits`，没有用 `summary` 整段文本（`summary` 是
  一段完整叙述，不是"一条一条的事实清单"，直接整段参与 Jaccard 相似度
  计算容易因为文本过长、话题过杂而稀释信号，暂不处理，等有具体的
  "summary 里提到的内容排序不准"案例再评估要不要单独处理）。

## 实施记录（跨系统"不感兴趣"信号——只读标注版本）

用户已明确要求推进第 4 节里原本标记为"暂不建议"的开放问题。原本的
顾虑（growth_advisor 面向用户成长、capability_learning 面向 Agent
知识，两边"不感兴趣"语义不完全等价，贸然合并可能错误压低有效候选）
通过"只标注、不抑制"的方式规避。

- 新增 `src/mini_agent/perception/cross_system_dismiss_signal.py`：
  - `load_cross_system_dismiss_signals(paths, min_count=2)`：聚合
    `GrowthBacklog` 里 `status="dismissed"` 的候选（按
    `dedupe_key()` 归一化计数）和 `CapabilityOutlineSuggestionStore`
    里 `status="dismissed"` 的建议（按 `normalize_title_key()`
    归一化计数），只保留达到 `min_count` 次的标题——单次 dismiss
    可能只是"这次报告质量不好"，不代表方向层面不感兴趣（呼应
    growth_advisor 自己已有的"报告质量差单独计数"设计）。
  - `find_cross_system_match(text, own_system, signals,
    min_similarity=0.5)`：复用 `situational_relevance.py` 已经验证
    过的字符级 bigram + Jaccard 算法（直接导入 `_tokens`/`_jaccard`，
    不重新实现），**只匹配跟 `own_system` 不同来源**的信号——同系统
    内部的 dismiss 冷却已经在各自模块里正常工作，这里刻意不重复。
  - `min_similarity` 默认 0.5，比 `situational_relevance` 的处境
    相关度更保守——那边是排序参考，这边是提醒用户，误报的打扰成本
    更高，宁可漏标不错标。
- `src/mini_agent/perception/initiative_inbox.py`：`InitiativeItem`
  新增 `cross_dismiss_similarity`/`cross_dismiss_source_title`/
  `cross_dismiss_source_system` 三个可选字段（默认 `None`，同
  `situational_relevance` 字段一样只在有值时才出现在 `to_dict()`
  输出里，向后兼容旧调用方）；`initiative_inbox_snapshot()` 新增
  `annotate_cross_dismiss` 参数（默认 `True`），行为约定跟已有的
  `annotate_relevance` 完全一致（异常降级为不标注、传 `False` 跳过
  读取、不改变 `items` 排序）。
- **关键设计取舍**：这是**标注层**，不是**过滤/抑制层**——候选照常
  生成、照常展示、照常参与已有的 confidence/排序计算，唯一的变化是
  多了三个展示字段。用户在看板上看到"跟你之前忽略过的《XX》很像"这行
  提示后，自己决定要不要因此忽略，Agent 不替用户下这个判断——这是为了
  规避方案原文提出的风险（两个系统的"不感兴趣"语义不完全等价）而做的
  刻意保守设计，不是实现上偷懒。

### 验证

新增 `tests/test_cross_system_dismiss_signal.py`（9 用例：空状态/
单次 dismiss 不计入/达到 min_count 才计入/两个系统的信号来源分别
正确聚合/`find_cross_system_match` 只匹配跨系统不匹配同系统/低于
相似度阈值返回 None/收件箱集成——匹配项被正确标注、
`annotate_cross_dismiss=False` 跳过标注、标注异常不影响收件箱其余
展示）。连同此前两个方向的回归测试，累计 **69 个用例全部通过**。

### 遗留/待观察

- 目前只对比了 `GrowthBacklog`（候选标题）和
  `CapabilityOutlineSuggestionStore`（大纲建议标题）两类历史 dismiss
  记录，没有纳入 `CapabilityQuestionStore` 里被 dismiss 的问题
  （`CapabilityQuestion.question` 是完整问句，不是"主题标题"，跟
  候选标题的文本形态差异较大，直接拿来做 bigram 相似度可能噪音偏多，
  暂不处理，等有需要再评估怎么从问句里提炼出可比较的主题词）。
- `min_similarity=0.5`/`min_count=2` 是凭经验给的初始值，不是精确
  调出来的——如果观察到误报（提示了但用户觉得完全不是一回事）或漏报
  （明显该提示但没提示）比较多，可以调整这两个参数，不需要改动算法
  结构本身。
