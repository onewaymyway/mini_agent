# Goal/Cron 任务优化 —— 真实需求梳理与系统理念

> 状态：方向 B（阶段健康告警）、方向 C（下一轮从简执行）、方向 A（阶段
> 感知的归档门禁 + 调度联动子项·阶段感知资源估算）已实施完成；方向 D
> （跨 Goal 探索期并发治理）、方向 A 调度联动子项里"真正接入执行资源
> 分配"的部分，本轮评估后记录方向、暂不实现，见 §5。
> 前置背景：`goal_cron_binding_plan.md`（绑定/触发/回收）、
> `goal_execution_phase_improvement_plan.md`（explore/converge/stable/tidy
> 阶段机制）、`goal_cron_visibility_and_intervention_improvement_plan.md`
> （看板可见性 + 跳过一轮 + 归档 + 失败通知）均已完整实施。本方案不重复
> 造轮子，只补这些机制之上仍然缺失的一层。

## 0. 背景：现状已经解决了什么

在动手之前先盘一遍现状，避免重复建设：

- **触发/绑定机制通了**：Goal 可以 `recurring=True`，由
  `goal_cron_bridge.py` 按 schedule 周期性派生子 Objective，Goal 暂停会
  联动 cron 停摆。
- **agent 行为基调有阶段区分**：`execution_phase.py` 的
  `explore/converge/stable/tidy` 状态机，能让 agent 在"刚起步试错"和
  "跑熟了重复执行"之间切换 prompt 基调，还有 Stage D 的"伪进展"降级信号。
- **看板可见性和基础干预已具备**：Goal 卡片能看到第几轮、下次触发时间、
  一键绑定/解绑；用户能"跳过下一轮"；`goals.json` 有归档机制防止无限
  膨胀；某一轮失败会主动推通知。

这些机制各自都做得比较完整，但放在一起看，还留了三类真实场景没被覆盖。

## 1. 真实场景倒推需求

**场景 1：阶段状态只影响 agent，不影响用户感知**

一个"每天扫 arxiv"的 Goal 卡在 explore 阶段迟迟不收敛（比如任务定义本身
有歧义，或者外部环境一直在变化，agent 每轮都在"重新摸索"）。现有机制里，
`stability_score` 只是看板上一个数字，用户不主动点开看板、不主动对比
历史轮次，根本不会意识到这个 Goal"一直没跑顺"。同样，Stage D 的"伪进展"
降级信号（`stable` 被打回 `converge`）目前只是内部悄悄调整 prompt，用户
毫无感知——但这个信号恰恰是"这个 Goal 值得你看一眼"的强信号，被浪费了。

**场景 2：干预粒度是二元的，缺一个中间态**

现有"跳过下一轮"解决的是"这周太忙，这一轮别跑了"。但真实场景里更常见的
是"这一轮还是要跑，但我不希望它又搞出一堆新东西"——比如用户临时出差、
不方便盯着新方案的产出，只想让 agent 做最基础的同步/巡检。当前只有
"完全不跑"和"正常跑（包括可能触发的 explore/converge 行为）"两个选项，
缺一个"跑，但降级"的中间态。

**场景 3：健康信号分散在各处，没有统一的"值得关注"判断**

`execution_phase.py` 的 `stability_score`、`mode_history`、Stage D 的
"伪进展"信号，加上 growth_advisor 自己的"饱和度"信号，本质上都是同一类
东西——"这个 Goal 的执行状态是否正常"。但它们目前各自为政：有的只展示
不通知（阶段状态），有的通知但范围窄（只对 growth_advisor 标签的 Goal
判断饱和度）。用户没有一个统一的"哪些周期性 Goal 需要我关注"的入口。

## 2. 系统理念

把上面三个场景抽象一下，本方案要补的是一条理念：

> **执行阶段（execution phase）不应该只是 agent 侧的行为调节器，它还应该
> 是系统对外的健康信号源。** 阶段状态的每一次异常变化——长期卡住、反复
> 回退——都应该有机会转化成一条用户能看到的信号，而不是只停留在
> prompt 拼接这一层。

配合这条理念，干预能力也要跟着从"二元开关"细化为"力度可调"：跳过是
一端，正常执行是另一端，中间需要"跑但降级"这种更贴近真实使用习惯的挡位。

## 3. 改进方案

### 方向 B（已实施）：执行阶段健康告警

在 `ExecutionPhaseState` 上新增 `last_health_alert_at`/
`last_health_alert_kind` 两个字段（纯通知层去重状态，不参与阶段判定本身），
并新增 `check_phase_health(state, effective_mode)` 只读判定函数，覆盖两类
问题：

1. **stuck_explore**：`auto` 模式且未锁定，`effective_mode == "explore"`
   且连续轮数达到阈值（默认 6 轮）——用户手动锁定在 explore 是明确意图，
   不算异常，不告警。
2. **phase_flapping**：最近若干次自动判定里，"从 stable/converge 被打回
   converge/explore"的次数达到阈值（默认 8 次窗口内 3 次）——覆盖 Stage D
   "伪进展"信号反复触发的情况。

同一种问题命中冷却期（默认 3 天）内不重复告警；两类问题互不抑制。命中且
不在冷却期时，`goal_cron_bridge._append_execution_phase_context()` 复用
现成的 `NotificationDispatcher`（与已有的 `_notify_cycle_failed()` 同一
套通知网关，kanban 渠道恒真兜底）推送一条通知，不新增渠道实现。

判定函数本身不修改 state，调用方在决定要发送通知后才落盘冷却状态——
保持与项目里其它只读判定函数一致的风格。任何环节异常整体吞掉，不影响
Goal 触发主流程。

### 方向 C（已实施）：下一轮"降级执行"（lightweight）

`GoalNode` 新增 `next_cycle_lightweight: bool` 字段，语义与
`skip_next_cycle` 并列但不同：

| 字段 | 语义 | 这一轮是否触发 |
|---|---|---|
| `skip_next_cycle` | 完全不跑这一轮 | 否 |
| `next_cycle_lightweight` | 跑，但要求从简 | 是 |

`_fire_goal_cycle()` 在拼装子 Objective description 时，若命中该标记，
在 execution phase 提示片段之后追加一段"本轮降级执行"约束（不引入新方案、
不做结构性变更、有异常再汇报），消费后立即清零，只影响这一次触发，不
改变 `ExecutionPhaseState.mode` 本身——降级是"这一轮"的临时决定，不代表
这个 Goal 整体阶段判断变了。

新增 REST 端点 `POST /v1/goals/{goal_id}/lightweight_next_cycle`，看板
"⏰ 周期性设置"折叠区新增"🪶 下一轮从简"按钮，与"跳过下一轮"并列。

### 方向 A（已实施）：阶段感知的归档门禁

在 `execution_phase.py` 新增只读函数 `last_known_effective_mode(state)`：
不重新触发一次完整的规则判定（那是 `resolve_effective_mode` 的职责，
需要 `cycle_no`/`spec_confirmed` 等触发时才有的上下文），只读取"最近一次
已知的有效阶段"——`mode != "auto"` 时直接是该阶段；`mode == "auto"` 时从
`mode_history` 找最近一条 `reason == "rule_based_auto"` 的记录还原阶段名；
完全没有历史记录时保守返回 `"explore"`（不确定就当作还在探索期，不提前
归档/放宽资源控制）。

`goal_cron_bridge.reap_finished_cycles()` 在调用
`goal_backlog.archive_finished_cycle_children()` 之前，先用这个函数读取
当前阶段，只有 `stable`/`tidy`（已收敛）才允许归档；`explore`/`converge`
阶段的早期尝试细节可能还有参考价值，暂缓归档，避免"刚探索完还没收敛就被
清出 `goals.json`，用户想回看当时试过哪些方案时已经找不到"。`paths` 拿不
到或阶段状态读取异常时保守按"允许归档"处理，与本条门禁引入之前的行为
一致，不因为诊断信息缺失而阻塞归档这个主功能。

### 方向 A 调度联动子项（已实施）：阶段感知的资源估算（只读预览）

`归档门禁` 只是"阶段状态影响系统行为"的第一个落点，§5 原本记录了第二个
落点——`UnifiedTaskScheduler` 的资源分配权重（explore 期更宽松、stable
期更收紧）。这一次先落地其中风险最低的一层：`ObjectiveChannelAdapter.
poll_due()` 返回的 `SchedulableTask.resource_estimate` 不再恒为 `1.0`，
改为按该 Goal（对派生的 Objective 而言是其 `parent_id` 指向的 recurring
Goal）的 `execution_phase.last_known_effective_mode()`，经新增的纯函数
`execution_phase.phase_resource_multiplier()` 换算出的相对倍率——explore
1.3、converge 1.15、stable 1.0、tidy 0.85（表在
`execution_phase.DEFAULT_PHASE_RESOURCE_MULTIPLIERS`，改默认值只需要改
这个常量）。`extra["phase_mode"]` 同步带出阶段名，方便看板/诊断展示时
不用额外查一次。

范围边界与 §5 原本的担忧完全一致：这仍然只是 `poll_due()` 这一层的
**只读预览**，`resource_estimate` 目前唯一的消费方是
`/self/unified_scheduler_preview` 诊断端点；真正接管执行资源分配（超时
预算、重试次数等）的那部分仍未实现，也仍是"没有真实使用数据支撑具体
数值该怎么用"——先把"能读到、能看到"这一步做完、观察一段时间效果，再
决定是否/如何让它真正影响执行行为。任何一环读取失败（`paths` 未注入、
阶段状态文件不存在、任何异常）都保守回落到 `1.0`，与引入本子项之前的
行为完全一致，不影响 `poll_due()` 本身的可用性。

## 4. 分阶段落地记录

- **Stage 1（已实施）**：方向 C —— `GoalNode.next_cycle_lightweight` 字段
  + `_fire_goal_cycle()` 消费逻辑 + REST 端点 + 看板按钮 + 单元测试
  （`tests/test_goal_cron_bridge.py::TestLightweightNextCycle`）。
- **Stage 2（已实施）**：方向 B —— `ExecutionPhaseState` 新增冷却字段 +
  `check_phase_health()` + `goal_cron_bridge._notify_phase_health_issue()`
  接入 + 单元测试（`tests/test_execution_phase.py` 新增
  `check_phase_health` 系列用例 + `tests/test_goal_cron_bridge.py` 新增
  通知派发用例）。
- **Stage 3（已实施）**：方向 A —— `execution_phase.last_known_effective_mode()`
  + `goal_cron_bridge.reap_finished_cycles()` 归档调用前置门禁 + 单元测试
  （`tests/test_execution_phase.py` 新增 `last_known_effective_mode` 系列
  用例 + `tests/test_goal_cron_bridge.py` 新增
  `test_reap_skips_archive_while_in_explore_phase`/
  `test_reap_archives_once_phase_reaches_stable`）。
- **Stage 4（已实施）**：方向 A 调度联动子项 —— 
  `execution_phase.phase_resource_multiplier()` +
  `ObjectiveChannelAdapter` 新增 `paths` 参数与阶段感知的
  `resource_estimate`/`extra["phase_mode"]` +
  `build_default_scheduler(paths=...)` 透传 +
  `/self/unified_scheduler_preview` 路由传入 `paths` + 单元测试
  （`tests/test_execution_phase.py` 新增 `phase_resource_multiplier`
  系列用例 + `tests/test_unified_task_scheduler.py` 新增
  `test_resource_estimate_defaults_to_1_without_phase_history`/
  `test_resource_estimate_reflects_stable_phase`/
  `test_resource_estimate_falls_back_to_1_without_paths`）+
  `tests/test_unified_scheduler_preview_route.py` 新增
  `test_goal_channel_resource_estimate_reflects_phase`（确认路由层
  `paths` 透传到位，端到端验证阶段感知的 `resource_estimate` 生效）。

## 5. 评估后决定本轮不实现的方向（记录，供后续排期）

- **方向 A 调度联动子项的剩余部分（阶段感知的资源估算已实施，真正接入
  执行资源分配仍未做）**——`resource_estimate`/`phase_mode` 目前只出现在
  `poll_due()` 的只读预览里，还没有任何调用方据此真正调整执行侧的超时/
  重试预算或参与 `allocate_weighted_slots()` 的槽位分配计算。本轮不做的
  原因不变：这需要改动调度器/执行器的调用约定，影响面比"暴露一个诊断
  字段"大得多，且目前没有真实使用数据支撑"具体倍率该怎么消费、消费到
  哪个环节"，贸然实现容易引入一套没有校准过的策略。留待观察这批阶段感知
  预览数据 + 方向 B 告警的实际命中情况后再排期。
- **方向 D：跨 Goal 探索期并发治理**——多个 recurring Goal 同时处于
  explore 阶段时，理论上系统层面的不确定性会叠加，`ResourceArbiter` 应该
  有一条"同时处于 explore 的 Goal 数量" 软上限规则。本轮不做的原因：当前
  项目里同时运行的 recurring Goal 数量规模尚未出现这类问题的实际信号，
  且这类"跨 Goal 裁决规则"改动涉及 `ResourceArbiter` 的核心调度逻辑，
  影响所有 Goal 而不只是新增功能覆盖的 Goal，风险收益比不划算，先记录
  方向，等实际出现多 Goal 并发探索导致资源紧张的场景再动手。

## 6. 兼容性与风险

- 全部新增字段（`GoalNode.next_cycle_lightweight`、
  `ExecutionPhaseState.last_health_alert_*`）默认值保证向后兼容，
  `to_dict`/`from_dict` 同步补齐，未主动使用的 Goal 行为不变。
- 健康告警是纯诊断增强，判定函数只读、调用方异常整体吞掉，不影响
  Goal 触发主流程本身；告警阈值是启发式的，可能需要后续根据实际使用
  情况调整默认值（`DEFAULT_STUCK_EXPLORE_CYCLES`/`DEFAULT_FLAP_WINDOW`/
  `DEFAULT_FLAP_THRESHOLD`/`DEFAULT_HEALTH_ALERT_COOLDOWN_SECONDS`，均在
  `execution_phase.py` 顶部，改动只需要调整常量）。
- `next_cycle_lightweight` 与 `skip_next_cycle` 是两个独立字段，理论上
  可能被同时设置——`_fire_goal_cycle()` 里 `skip_next_cycle` 的判断在前，
  命中后直接 return，不会走到 `next_cycle_lightweight` 的处理分支，语义
  上"跳过"优先于"降级"，符合直觉（不跑就不存在"跑得简单一点"）。
- 归档门禁只改变 `reap_finished_cycles()` 内部触发归档的**时机**，不改变
  `GoalBacklog.archive_finished_cycle_children()` 本身的行为——该方法仍可
  被直接调用（CLI/看板未来若要提供"立即归档"入口，不受这条门禁限制）。
  对于从未主动使用 execution phase 机制（阶段状态文件不存在或一直是
  `auto` 且没有历史记录）的 Goal，门禁会保守地把它们当作"仍在 explore"，
  在真正积累出规则判定历史之前不做归档；实际使用中 `_fire_goal_cycle()`
  每轮都会调用 `_append_execution_phase_context()`，跑不了几轮阶段历史
  就会出现，不会长期卡在这个保守默认值上。
- `phase_resource_multiplier()` 是纯函数，`ObjectiveChannelAdapter` 新增
  的 `paths` 参数有默认值（`None`，回落到 `goal_backlog._paths`），不
  传/传不到的调用方行为与本子项引入之前完全一致（`resource_estimate`
  恒为 `1.0`）；`build_default_scheduler()` 新增的 `paths` 关键字参数
  同理，是可选追加参数，不影响任何既有调用点的签名兼容性。
