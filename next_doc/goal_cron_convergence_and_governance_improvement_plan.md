# Goal/Cron 长期任务执行机制收敛与治理改进方案

> 状态：**四个 Track 已全部实施完成**（Track 1：统一调度层 Goal 通道
> 接入；Track 2：文档状态栏核对；Track 3：复查触发信号；Track 4：一次性
> Goal 覆盖原则显式化）。各 Track 详细实施记录见对应小节（§1.4/§2.2/
> §3.4/§4.3）。
>
> 触发背景：对项目 goal/cron 相关的十余份 next_doc 方案文档（绑定/触发、
> 公平调度、执行阶段、诊断+调优、主动巡检+健康总览、产出目录规范）做了
> 一次整体梳理后发现——**这条线本身已经不缺功能**，六层机制基本都已经
> 落地；真正影响"可控、可用"的，是"统一"和"一致性"这两件事还没有完全
> 做完。本方案聚焦收敛这两个维度，不新增任何面向用户的新能力。
>
> 前置阅读：`docs/mini-agent-philosophy-and-roadmap.md`（本方案 Track 1 直接
> 回应"能力增长以减少用户认知负担为北极星"——统一调度层做完之后，减少
> 的是维护者/未来自己理解系统行为的认知负担，逻辑上与"减少用户显式交代"
> 同源）；`goal_cron_unified_scheduler_improvement_plan.md`（Track 1 的
> 直接前置）；`goal_cron_task_optimization_holistic_plan.md`（Track 3 的
> 直接前置，§5 记录了两项"评估后决定暂不实现"）；
> `goal_cron_output_directory_convention_plan.md`（Track 2 的具体触发
> 案例）；`goal_execution_fairness_improvement_plan.md` /
> `goal_cron_output_directory_convention_plan.md` §7（Track 4 的具体
> 触发案例）。

## 0. 背景：为什么是这四个方向

对 goal/cron 相关方案文档做交叉核对后，识别出的问题不是"缺什么机制"，
而是三类具体现象：

1. **调度决策入口没有真正统一**——`goal_cron_unified_scheduler_
   improvement_plan.md` P5 第 4 步里，cron/goal_cycle 两条通道的
   `execute()` 已经是真正的委托派发，但 `ObjectiveChannelAdapter.
   execute()` 仍然 `raise NotImplementedError`，Goal 通道（三条通道里
   权重最大的一条，长期任务的主体）依然游离在统一层之外，实际派发逻辑
   散落在 `AutonomousLoop._tick_maintenance()` 内部。
2. **文档状态栏与代码实际状态出现过脱节**——核对
   `goal_cron_output_directory_convention_plan.md` 时发现，该文档标题栏
   写"设计草案 / 待评审（尚未实施）"，但正文 §6"实施记录"完整记录了
   全部 Track 已经落地、测试通过、甚至有一次后续调整（顶层目录改名）。
   这不是孤立的疏忽——`next_doc/` 下有 120+ 篇方案文档，状态栏滞后于
   实施记录的情况大概率不止这一处，是一类需要系统性核对的问题，而不是
   改一份文档就能解决的问题。
3. **已识别但刻意搁置的方向，缺一个"什么时候该重新评估"的触发条件**——
   `goal_cron_task_optimization_holistic_plan.md` §5 记录了两项暂不
   实现的方向（阶段感知资源估算接入执行侧、跨 Goal explore 并发软
   上限），理由都是"目前没有真实数据支撑"。这个判断在当时是对的，但
   现在 `cycle_patrol`（主动巡检 + 健康总览）已经上线并持续积累数据，
   具备了重新评估这两项判断是否依然成立的条件，只是目前没有人/机制
   去做这件"定期回看搁置决策"的事。

四个 Track 分别对应：Track 1 解决第 1 点，Track 2 解决第 2 点，
Track 3 解决第 3 点，Track 4 是核对过程中顺带发现的第四类问题（部分
机制默认只覆盖 recurring Goal，覆盖范围的取舍原则目前是逐个机制各自
决定，没有显式写下来）。

## 1. Track 1：完成统一调度层的 Goal 通道接入

### 1.1 现状与目标

`unified_task_scheduler.py` 模块头部已经把不做的原因写得很清楚：
Goal 通道的实际派发（公平排序 / per-Goal 并发上限 / `paused` 状态检查 /
`resume_fairness` 等）深度耦合 `AutonomousLoop` 自身持有的运行时状态
（`fairness_paused_objective_ids`、`user_paused_objective_ids` 等），
在没有一个类似 `CronScheduler.trigger_job_now()` 那样"把触发 + 记账
封装成一次安全调用"的公开入口之前，贸然实现 `execute()` 要么重新拼一份
简化版调度逻辑（引入不一致风险），要么需要先重构 `AutonomousLoop`。

本 Track 的目标就是把"缺一个安全入口"这个前置条件补上，思路是
**从 `AutonomousLoop._tick_maintenance()` 里抽取，而不是重新实现**：

1. 把 `_tick_maintenance()` 中"从排序结果里选中一个 Objective 后，
   具体怎么启动/恢复它"这一段逻辑（`resume_fairness()` 分支 + 全新
   `start()` 分支 + 记账 `mark_scheduled()`/`_record_digest()`）抽成
   `AutonomousLoop` 上的一个新公开方法，例如
   `trigger_objective_now(objective_id) -> bool`，语义与
   `CronScheduler.trigger_job_now(job_id) -> bool` 对齐（成功触发返回
   `True`，因并发上限/暂停状态等原因未触发返回 `False`，不是异常）。
2. `ObjectiveChannelAdapter.execute()` 改为委托调用这个新方法，不再
   `raise NotImplementedError`。
3. **`_tick_maintenance()` 现有的调用路径本身不改**——`trigger_
   objective_now()` 是从原地逻辑抽出来的等价实现，`_tick_maintenance()`
   内部直接调用重构后的同一份逻辑（行为完全不变），`ObjectiveChannelAdapter`
   只是多了一个"外部也能调用这份逻辑"的入口，不是两套并行实现。

### 1.2 明确不做的事（延续 P5 一贯的克制原则）

- **不在本 Track 里把 `scheduler.unified_dispatch_enabled` 的默认值
  从 `False` 改成 `True`**——Goal 通道能被安全调用是一回事，"三条通道
  默认都走统一入口"是否要成为新的默认行为是另一回事，后者涉及面更广，
  留给观察期后单独评审（与 `goal_cron_unified_scheduler_improvement_
  plan.md` §3 第 3 条"P5 允许长期分阶段推进"的既有原则一致）。
- **不改变 per-Goal 并发上限、公平轮询、老化补偿这些排序/仲裁算法
  本身**——`ObjectiveChannelAdapter.poll_due()` 已经在复用
  `active_objectives_fair_ranked()`，本 Track 只补 `execute()`，排序
  逻辑不动。
- **不处理 `resource_estimate`（阶段感知资源估算）真正参与槽位分配
  的问题**——那是 Track 3 讨论的范围，本 Track 只关心"Goal 通道能不能
  被统一入口安全调用"，不关心"调用时该给多少资源"。

### 1.3 验收标准

1. `ObjectiveChannelAdapter.execute()` 不再抛 `NotImplementedError`，
   对一个当前排在公平排序候选前列的 Objective 调用它，行为与
   `_tick_maintenance()` 原地触发完全一致（同样的并发上限检查、同样的
   `resume_fairness`/`start` 分支选择、同样的记账副作用）。
2. 新增单测：直接调用 `trigger_objective_now()`（不经过完整的
   `_tick_maintenance()` 循环）验证三类场景——正常启动新 Objective、
   从 `fairness_paused` 恢复、因 per-Goal 并发上限已满返回 `False`。
3. 现有 `test_goal_execution_fairness.py`、
   `test_unified_task_scheduler*.py` 全部保持通过，`_tick_maintenance()`
   本身的行为不因本次重构产生任何回归（用现有的
   `test_autonomous_loop*.py` 系列验证）。

### 1.4 实施记录

**Track 1 已实施完成。**

- `evolution/autonomous_loop.py`：从 `_tick_maintenance()` 排序循环体
  抽取 `_trigger_objective_candidate(obj, *, fairness_paused_ids,
  user_paused_ids) -> bool`（resume_fairness/start 分支选择 +
  mark_scheduled 记账 + digest 记录，与抽取前逐行等价），
  `_tick_maintenance()` 循环体改为调用它，行为不变。新增公开方法
  `trigger_objective_now(objective_id) -> bool`：独立完成 is_running /
  user_paused / can_start_new / per-Goal 并发上限四项检查后，复用同一个
  `_trigger_objective_candidate()` 触发，任何内部异常兜底捕获并返回
  `False`（不向上抛出）。
- `evolution/unified_task_scheduler.py`：`ObjectiveChannelAdapter.
  __init__()` 新增 `autonomous_loop` 参数；`execute()` 不再
  `raise NotImplementedError`，改为委托
  `self._autonomous_loop.trigger_objective_now(task.task_id)`，未注入
  或调用异常时返回 `False`（与 `CronChannelAdapter.execute()` 既有异常
  处理风格一致）。`build_default_scheduler()` 同步新增 `autonomous_loop`
  参数并透传。模块头部文档字符串同步更新为"P5 第 4 步三个通道均已完成"。
- `api/routes.py`：`get_self_unified_scheduler_preview()` 里
  `build_default_scheduler()` 调用透传已有的 `al`（`http_server.
  autonomous_loop`）变量。
- 测试：新增 `tests/test_autonomous_loop_trigger_objective_now.py`（11
  个用例，覆盖 §1.3 验收标准三类场景 + 异常兜底 + fairness 恢复失败
  等边界情况）；`tests/test_unified_task_scheduler.py` 里断言"execute()
  抛 NotImplementedError"的旧用例替换为三个新用例（未注入返回 False /
  委托调用并透传返回值 / 异常兜底）。`test_goal_execution_fairness*.py`
  /`test_autonomous_loop_decommission_hook.py`/`test_consolidation.py`
  等既有回归套件（约 130 个相关用例）全部保持通过，未发现
  `_tick_maintenance()` 行为回归。
- **仍未做的**（不在本 Track 范围内，符合 §1.2 边界）：
  `scheduler.unified_dispatch_enabled` 默认值未改动，`_tick_
  maintenance()` 依然是当前唯一的实际触发入口；`ObjectiveChannelAdapter.
  execute()` 目前"可以安全调用，但还没有人在调用"，与 `CronChannelAdapter`
  /`GoalCycleChannelAdapter.execute()` 现状一致。

## 2. Track 2：goal/cron 相关方案文档状态栏核对

### 2.1 现状与目标

`goal_cron_output_directory_convention_plan.md` 是一次具体的核对
发现：状态栏写"设计草案 / 待评审（尚未实施）"，但正文完整记录了实施
过程。这类脱节的风险是双向的——可能让人误以为"没做"而重新设计一遍
（浪费），也可能反过来让人误以为"已经上线"而在此基础上做决策（更
危险，尤其是涉及配置默认值、API 契约这类会被下游直接依赖的内容）。

本 Track 不是"重写这些文档"，只是做一次系统性核对 + 修正，工作量
可控、风险极低（纯文档改动，不涉及代码）：

1. 对本文档背景中提到、以及本次梳理中识别出的全部 goal/cron 相关
   `next_doc/*.md`（约 25-30 篇，见 §0 涉及的文档清单和上一轮全景
   梳理时列出的文件名）逐篇核对：状态栏描述的完成度是否与正文"实施
   记录"小节一致，是否与对应源码里的实际实现一致（用文档里提到的
   具体函数名/配置项去代码里做存在性核对，不是通读全部代码）。
2. 发现不一致时，就地修正状态栏，不改变正文其它内容（正文的实施
   记录本身通常是准确及时的，问题主要出在最上面那行"总览性"的状态
   描述没有跟着更新）。
3. 顺带核对一次"文档互相引用的状态描述是否一致"——比如 A 文档说
   "B 文档的某个 Track 已完成"，实际去 B 文档核对是否真的如此（上一轮
   全景梳理已经用这种方式发现了 `judge_profile_unification_migration_
   plan.md`、`workflow_mechanism_improvement_plan.md` 主文档仍是设计稿
   的情况，属于正确案例，本 Track 是把这种核对方式做得更系统）。

### 2.2 交付物

- 核对记录：`next_doc/goal_cron_docs_status_audit_record.md`（29 篇文档
  逐一核对结论）。
- 对识别出"状态栏需要修正"的文档提交修正——本轮核对发现 1 处：
  `goal_cron_output_directory_convention_plan.md` 状态栏已修正为
  "已实施完成"，并同步修正 §4 小标题中"本文档暂不实施"的自相矛盾表述。

**Track 2 已实施完成。** 核对结论：29 篇文档中仅发现上述 1 处不一致，
其余状态描述与实施记录/代码实际情况一致，详见审计记录文档"结论"小节。
基于这个结果，不投入自动化核对工具（§2.3 的判断依据成立），后续按需
人工抽查即可。

### 2.3 明确不做的事

- **不重新评审这些文档的技术方案本身**——本 Track 只核对"状态描述是否
  准确"，不重新判断"当初的设计决策是否依然合理"（那是每份文档各自的
  后续迭代该做的事，不在本轮范围）。
- **不建立自动化的状态核对机制**（比如脚本扫描代码里是否存在文档提到
  的函数名）——先靠一次性人工核对建立准确的基线，是否值得投入自动化
  工具留待观察这类脱节的出现频率再决定，避免为了一个目前只发现一例的
  问题就上一套持续维护的检查工具。

## 3. Track 3：用主动巡检数据重新评估两项搁置决策

### 3.1 现状与目标

`goal_cron_task_optimization_holistic_plan.md` §5 记录了两项"评估后
决定本轮不实现"的方向，理由都是"目前没有真实数据支撑，贸然实现容易
引入没校准的策略"：

1. **阶段感知资源估算真正接入执行侧**——`ObjectiveChannelAdapter.
   poll_due()` 已经能算出每个 Goal 当前的 `resource_estimate`（按
   explore/converge/stable/tidy 阶段换算的相对倍率），但目前只出现在
   `/self/unified_scheduler_preview` 只读预览里，没有任何调用方据此
   真正调整超时/重试预算或参与槽位分配。
2. **跨 Goal 探索期并发治理**——多个 recurring Goal 同时处于 explore
   阶段时，`ResourceArbiter` 目前没有"同时处于 explore 的 Goal 数量"
   软上限规则。

这两项当时"不做"的判断本身是合理的（没有真实压力信号就上一套未校准
的策略确实是风险），但"以后要不要做"目前没有一个明确的复查节点——
容易变成永久搁置，即便情况已经变化。现在
`goal_cron_cycle_proactive_patrol_and_health_overview_plan.md` 的
Stage 1/2/3（能力 C + 能力 D + 本轮的去重/优先级排序改进）已经上线，
持续在积累"每个 recurring Goal 当前处于什么阶段、命中什么信号"的
数据，具备了拿真实数据回看这两项判断的条件。

### 3.2 设计：不是直接实现，是先建立"复查触发条件"

本 Track **不直接实现** §5 记录的这两项方向本身（依然缺乏足够的真实
使用规模），而是设计一个轻量的复查机制，把"有没有到该重新评估的时候"
这件事变成可衡量、可自动检测的，而不是"想起来才回头看一眼"：

1. **复查信号 1（对应方向 A：阶段感知资源估算）**——统计口径：
   `cycle_patrol_state.json` 的 `overview.goals` 里，`execution_
   phase_mode == "explore"` 且伴随非空 `recent_health_alerts` 的 Goal
   占全部 recurring Goal 的比例，若连续 N 轮巡检（建议 N=4，对应约
   1 天，按 `cycle_patrol.interval_hours` 默认 6 小时折算）该比例超过
   阈值（建议 30%，具体数值留待有第一批真实数据后校准），说明"explore
   阶段的 Goal 消耗了不成比例的资源却总是不稳定"这个现象已经具备统计
   显著性，值得启动方向 A 的实施评审。
2. **复查信号 2（对应方向 D：跨 Goal explore 并发治理）**——统计口径：
   `overview.goals` 里同时处于 `execution_phase_mode == "explore"` 的
   Goal 数量占全部 recurring Goal 的比例，若持续（同上 N 轮）超过阈值
   （建议 50%），说明"多个 Goal 同时探索"已经从"理论上可能"变成
   "实际经常发生"，值得启动方向 D 的实施评审。
3. 两个信号都是**只读统计**，复用 `cycle_patrol.py` 已经在维护的
   `overview` 快照，不新增采集面（与理念文档"警惕采集先于消费"一致——
   这里连"消费"都还没资格谈，是"复查是否具备开始设计的前提数据"这一
   更早的阶段）。命中阈值时的动作是**在看板健康总览区块追加一行提示
   文案**（"检测到 N% 的周期性 Goal 长期处于 explore 阶段，可以评估是否
   需要启动阶段感知资源分配"），不自动触发任何调度行为变更——是否真正
   立项实施，仍然是人工决策。

### 3.3 明确不做的事

- **不在本 Track 里实现方向 A/D 本身**——只建立"什么时候该重新评估"
  的可观测触发条件，实施与否是后续独立评审的事。
- **不引入新的定时任务**——复查逻辑挂在 `cycle_patrol.run_cycle_
  patrol()` 已有的巡检节奏里顺带计算（读一次 `overview.goals` 列表，
  规则聚合，零 LLM 成本），不新增一个 cron job。
- **阈值（30%/50%/连续 N 轮）是初始猜测值，不是精确校准的结果**——
  实施时在配置里暴露成可调项，观察第一批真实数据后再决定是否需要
  调整，这本身也是"数据消费先于精确调参"的体现。

### 3.4 实施记录

**Track 3 已实施完成。**

- `config/models.py::CyclePatrolConfig` 新增 5 个配置项
  （`review_trigger_enabled`/`review_trigger_min_recurring_goals`/
  `review_trigger_explore_alert_ratio`/`review_trigger_explore_
  concurrency_ratio`/`review_trigger_consecutive_rounds`），阈值与
  §3.2 设计一致（30% / 50% / 连续 4 轮 / 最小样本 5）。
- `evolution/cycle_patrol.py` 新增 `_compute_review_trigger_ratios()`
  （纯规则统计）+ `_update_review_triggers()`（持久化连续命中轮数到
  `cycle_patrol_state.json` 的 `review_triggers` 字段）+
  `_review_trigger_messages()`（转成看板提示文案）。
- `run_cycle_patrol()` 每轮巡检顺带更新 `review_triggers`（持久化，
  支持连续轮数累积）；`build_overview_live()`（无快照兜底路径）只报告
  即时比例，`active` 恒为 `False`（无状态文件无法跨 tick 累积轮数，
  用 `consecutive_rounds_tracked: False` 标注这一限制）。
- 看板 `_render_cycle_health_overview()` 在健康总览区块顶部展示
  `active=True` 的提示文案（`st.info`，🔎 开头），不影响任何调度行为。
- 详见 `docs/goal-cycle-patrol-guide.md` "复查触发信号"小节（用户可见
  文档）。测试见 `tests/test_cycle_patrol.py::TestReviewTriggers`（9 个
  用例：比例计算、样本量门槛、连续命中累积/清零、消息生成、持久化路径
  与现算路径的 `active` 语义差异、总开关关闭时跳过计算）。

## 4. Track 4：显式化"一次性 Goal 是否套用某机制"的判断原则

### 4.1 现状与目标

梳理下来能看到一个反复出现的模式：不少机制默认只覆盖
`recurring=True` 的 Goal——

- `cycle_patrol`（主动巡检）明确写"只巡检 recurring=True 的 Goal，
  一次性 Goal 没有跨轮次健康状态这个概念"。
- 产出目录规范最初设计时也只覆盖 recurring 场景，后来在 §7 补充
  评审后改为"也套用"（理由：一次性 Goal 拆解出多个子 Objective 时，
  同样存在"后一个子任务想接着用前一个子任务产出"的需求）。

这两个案例的结论不同（一个保持"只覆盖 recurring"，一个后来"扩展到
覆盖一次性"），且**各自的判断依据是分别推导的，没有沉淀成一条可以
直接复用的原则**——如果不把这条原则写下来，未来每新增一个类似机制，
都要重新把这个问题想一遍。

### 4.2 设计：把已有的两个判例归纳成一条可复用原则

不新增机制，只是把 Track 4 触发案例中已经隐含的判断逻辑显式写出来，
建议记录在 `docs/goal-cron-binding-guide.md`（或类似的 goal/cron 总览
性文档）里，作为后续新机制设计时的检查清单：

> **判断某个 goal/cron 相关机制是否需要覆盖一次性（非 recurring）
> Goal 时，问一个问题：这个机制的价值是否依赖"跨轮次"这个前提？**
>
> - 如果价值本质上来自"比较多轮之间的变化"（例如健康趋势判断、阶段
>   状态机的 explore→converge→stable 演进），一次性 Goal 天然不存在
>   "多轮"，不需要覆盖——`cycle_patrol` 属于这一类。
> - 如果价值来自"让后续步骤能接上前面步骤的产出/进度"，这件事在
>   recurring 的"下一轮 cycle"和一次性 Goal 的"下一个子 Objective"
>   之间是同构的（都是"链表指针指向上一个节点"），应该覆盖——产出
>   目录规范属于这一类，`goal_cron_output_directory_convention_plan.md`
>   §7 的结论正是这个道理的一个具体实例。

### 4.3 交付物

- 在 `docs/goal-cron-binding-guide.md` 新增 §14，把判断原则写清楚，
  列出 `cycle_patrol`（不覆盖）和产出目录规范（覆盖）作为两个对照案例。
- 复查现有 goal/cron 相关机制清单，逐项核对与原则是否一致：

| 机制 | 覆盖范围 | 是否符合原则 |
|---|---|---|
| `cycle_patrol`（主动巡检） | 仅 `recurring=True` | 符合（价值依赖跨轮趋势） |
| 产出目录规范 | recurring + 一次性 Goal 均覆盖 | 符合（价值来自子任务间衔接，与是否周期性无关） |
| `execution_phase`（执行阶段状态机） | 不做 `recurring` 硬性限制，任意 Goal 调用 `load_phase()`/`save_phase()` 均可用 | 符合——阶段状态机概念上依赖"多轮"，但实现上不强行拒绝一次性 Goal 调用，一次性 Goal 由于本身只跑一轮，天然停留在初始阶段，不会产生错误的阶段判断，不需要额外加限制 |
| `cycle_diagnostics`（单 Goal 诊断） | 对 recurring/一次性 Goal 都可生成报告，仅 `cron_health` 字段在非 recurring 时为空 | 符合——诊断报告本身的价值（展示当前状态）不依赖跨轮，只有其中 `cron_health` 这一个子字段依赖 cron 触发历史，按需为空即可，不需要整体限制 |

复查结论：现有机制的覆盖范围与本 Track 归纳的原则**均一致**，未发现
需要单独立项调整的不一致项。

**Track 4 已实施完成。**

### 4.4 明确不做的事

- **不强制要求所有现有机制立刻按这条原则调整覆盖范围**——已经存在的
  设计选择（比如 `cycle_patrol` 明确不覆盖一次性 Goal）如果本身符合
  这条原则，保持不变；如果复查发现不一致，单独记录、单独评审，不在
  本 Track 里顺带改代码。

## 5. 四个 Track 的依赖关系与建议顺序

四个 Track 彼此独立，不存在强依赖，可以并行或按任意顺序推进，但建议
顺序：

1. **Track 2（文档状态核对）优先**——工作量最小、风险最低（纯文档），
   且核对结果可能会影响后续对其它 Track 现状描述的准确性（比如如果
   核对发现某个被认为"未完成"的机制其实已经做完，会改变 Track 1/3
   的实际范围判断）。
2. **Track 1（统一调度层 Goal 通道）** ——四个 Track 里唯一涉及代码
   重构、且改动的是长期运行的核心调度路径，建议单独排期、充分测试，
   不与其它 Track 的改动混在同一批提交里。
3. **Track 3（复查机制）** ——依赖 `cycle_patrol` 已经稳定运行一段
   时间产出的真实数据分布来校准初始阈值，建议在 `cycle_patrol` 默认
   开启（如果尚未默认开启）并观察一段时间后再排期。
4. **Track 4（原则显式化）** ——纯文档性工作，可以随时插入，不阻塞
   也不被其它 Track 阻塞。

## 6. 风险与开放问题

1. **Track 1 抽取 `trigger_objective_now()` 时，如何保证与
   `_tick_maintenance()` 原地逻辑行为完全等价**——这是本方案里唯一
   涉及代码重构的部分，需要在实施时对 `_tick_maintenance()` 里"选中
   Objective 后触发它"这一段逻辑做逐行核对式的抽取（而不是重写），
   并靠现有 `test_autonomous_loop*.py`/`test_goal_execution_
   fairness.py` 系列做行为不变性验证，必要时先补充覆盖当前逻辑分支
   的测试用例，再做抽取重构（先固化行为基线，再重构，不是同时做）。
2. **Track 3 的阈值设置是否会因为 recurring Goal 数量本身较少而产生
   噪音（比如只有 2 个 recurring Goal，1 个进 explore 就是 50%）**——
   实施时应该同时设一个最小样本量门槛（比如 recurring Goal 总数
   `< 5` 时不触发复查提示，避免小样本下比例数字失真），具体门槛值
   同样留待观察真实部署规模后再校准。
3. **Track 4 复查现有机制时，如果发现的"不一致"数量较多**——按 §4.3
   的设计，这些发现应该拆成独立待办分别评审，可能会派生出一批新的
   next_doc 方案文档，这在预期之内，不代表 Track 4 本身失败（Track 4
   的目标是"把原则和现状差异找出来"，不是"消灭所有差异"）。
