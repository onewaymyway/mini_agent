# Goal 执行公平性调度 改进计划

- **版本**: v1.3
- **变更记录**:
  - v1.0：初版，规划 P1-P4（外加 P5 可视化/配置化），均未实现。
  - v1.1：P1/P2/P3 已实现（P4 按计划留待观察数据后再决定，P5 仅完成
    配置文档说明，看板可视化留待后续）：
    - P1：`ObjectiveExecutor.running_count_for_goal()` +
      `AutonomousLoop._tick_maintenance()` 内的按 Goal 分组并发上限检查；
      新增配置 `autonomy.max_concurrent_objectives_per_goal`（默认 1）。
    - P2：`GoalBacklog.active_objectives_fair_ranked()` + `mark_scheduled()`
      + `GoalNode.last_scheduled_at` 字段；新增配置
      `autonomy.goal_scheduling_strategy`（默认 `"fair_round_robin"`，
      可设为 `"priority"` 回退旧行为）。
    - P3：`goal_backlog.compute_aging_boost()`，接入
      `active_objectives_fair_ranked()` 的组内排序 key；新增配置
      `autonomy.fairness_aging_boost_per_day`（默认 1.0/天）、
      `autonomy.fairness_aging_boost_max_days`（默认 14.0 天）。
    - 新增测试：`tests/test_goal_execution_fairness.py`（10 个用例，覆盖
      本文档 P1-P3 各自的验收标准）。
    - 配置说明新增文档：`docs/goal-execution-fairness-config.md`。
  - v1.2：**P5 看板可视化补齐**（P4 仍按计划留空，未实施）：
    - 新增只读端点 `GET /v1/self/goal_fairness`
      （`src/mini_agent/api/routes.py`）：返回当前调度策略 + 每个 active
      Goal 的 priority/老化加成/effective_priority/last_scheduled_at/
      last_touched_at/objective_count。
    - 看板"🧠 自我状态"tab 新增"⚖️ 执行公平性"折叠区块
      （`apps/mini_agent_kanban/app.py::_render_goal_execution_fairness()`），
      按 `last_scheduled_at` 升序展示（最久没轮到的排最前，与实际调度顺序
      一致），复用"🩺 自诊断信号闭环"的展示风格，作为独立区块（未与其合并，
      见 §4 待讨论问题 4 的结论——数据量不大，先独立展示，观察实际使用后
      再考虑是否合并信息架构）。
    - `apps/mini_agent_kanban/client.py` 新增 `AgentClient.goal_fairness()`。
    - 新增测试：`tests/test_goal_fairness_routes.py`（4 个用例，覆盖端点
      空态/老化加成反映/排序顺序/策略字段）。
  - v1.3：**P4 执行时间片化已实现**（至此 P1-P5 全部完成）：
    - `ObjectiveExecution` 新增 `fairness_slice_started_at` /
      `fairness_slice_start_step` 两个持久化字段，记录当前"执行片段"的
      起点（`start()` 时初始化，每次 `resume_fairness()` 后重置）。
    - `ObjectiveExecutor._should_yield_for_fairness()`：跑满
      `autonomy.fairness_yield_after_steps` 步或 `autonomy.
      fairness_yield_after_seconds` 秒，且按 P2 公平排序确实存在另一个
      "未在运行"的 Goal 排在自己前面时才让出，只有一个 active Goal 时不
      让出（避免无意义的暂停/恢复）。
    - `on_turn_done()` 接入该检查：满足让出条件时，execution 状态置为
      新状态 `paused_for_fairness`（区别于 Track J 已有的"资源门控暂停"，
      那是被动触发；这里是主动让出），断点停在 `current_step_idx`，不计入
      `running_count()`。
    - 新增 `ObjectiveExecutor.fairness_paused_objective_ids()` /
      `resume_fairness()`：后者从断点续跑，不重新拆解 Objective、不丢失
      已完成 step 的进度。
    - `AutonomousLoop._tick_maintenance()` 的调度循环：候选命中
      `fairness_paused_objective_ids()` 时走 `resume_fairness()`，而不是
      当成全新 Objective 再走一次 `start()`。
    - 新增配置（`AutonomyConfig`）：`fairness_time_slicing_enabled`
      （默认 `False`，灰度关闭）、`fairness_yield_after_steps`（默认 3）、
      `fairness_yield_after_seconds`（默认 900 秒）。
    - 新增测试：`tests/test_goal_execution_fairness_p4.py`（5 个用例，
      覆盖默认关闭时行为不变、跑满阈值且有 Goal 排队时让出、只有一个
      Goal 时不让出、断点续跑正确性、`resume_fairness()` 在非暂停态时
      返回 `False`）。
    - 配置文档 `docs/goal-execution-fairness-config.md` 补充 P4 章节。
- **背景**：Goal 是长期任务，理想情况下应该"雨露均沾"，让所有 active Goal 都能持续
  获得推进；但代码复核发现，当前 `AutonomousLoop`/`ObjectiveExecutor`/`GoalBacklog`
  的调度模型是"贪心 + 静态优先级 + 一次启动跑到底"，天然容易导致同一个（或同一批）
  高优先级 Goal 长期垄断执行资源，其余 Goal 长期原地不动。本计划针对"执行资源如何
  在多个 Goal 之间公平分配"这一具体问题做改进，不涉及 Goal/Objective 本身的产生、
  拆解、执行内容等其它机制。
- **设计边界（明确写出以防后续误解）**：
  1. 不改变 `GoalNode.priority` 的用户语义——它仍然是用户/上游设置的"基础优先级"，
     本计划新增的"老化加成"等机制只影响调度侧计算出的**有效优先级**，不覆盖、不
     持久化改写用户设置的原始值。
  2. 不引入 LLM 调用——本计划所有新增判断都是规则化计算（时间戳比较、计数器、
     排序权重），零 LLM 成本，符合项目对调度类改动的一贯要求。
  3. 不改变 Objective 内部的拆解/执行逻辑本身（`_decompose()`/`_submit_step()` 等），
     只改变"调度器在什么时候、把执行资源分配给哪个 Goal/Objective"这一层。
  4. 默认行为变化需要可灰度控制——新增配置项默认开启新策略，但保留切回旧的纯优先级
     排序策略的开关，避免对已有自动化部署造成不可预期的行为突变。
- **关联文档**：
  - `next_doc/kanban_and_autonomy_improvement_plan.md`（Track B/C/D/J/K 已经在
    `ObjectiveExecutor`/`AutonomousLoop` 上做过并发/门控相关改造，本计划是同一模块
    上的延伸，不重复其已解决的问题）
  - `docs/memory-and-self-evolution-complete-reference.md`

---

## 1. 现状复盘：调度模型里缺的两样东西

代码复核结论（详见下方"证据"小节）：调度单位是 Objective 而不是 Goal，选取算法是
纯静态优先级排序，且一旦 Objective 启动就会连续执行到彻底完工才释放资源——这意味着：

1. **横向缺公平性**：`GoalBacklog.active_objectives()` 只是把所有 active Objective
   按 `priority` 降序排成一个扁平列表，完全没有"这个 Objective 属于哪个 Goal"的分组
   概念。一个 Goal 若被拆成多个 Objective（默认最多 3 个，见 `auto_objective_max_per_goal`），
   这些 Objective 优先级相同，会一起挤占全局仅有的并发槽位（默认 `MAX_CONCURRENT_
   OBJECTIVES = 2`，资源门控降级时可能只剩 1）。极端情况下一个 Goal 自己就能把所有
   并发槽位占满。
2. **纵向缺抢占/时间片**：`ObjectiveExecutor.on_turn_done()` 每完成一步立即自动提交
   下一步，直到该 Objective 全部 step 完成/失败/取消才释放槽位，中途没有任何"是否该
   把机会让给别的 Goal"的检查点。如果一个 Objective 被拆成 10 步、每步跑 20 分钟，
   对应的槽位就会连续被占 3 个多小时，期间调度器完全不会重新评估。
3. **同优先级下系统性偏袒**：Python `sorted()` 是稳定排序，`priority` 相同的节点会
   保持原有相对顺序（通常是创建顺序）。多个 Goal 优先级相同（如都用默认值 50）时，
   每次 tick 排序结果完全一样，靠前的永远排前面，后面的会系统性地一直排不上——这不是
   随机的偶发问题，是确定性的结构性问题。
4. **已有停滞检测但不反馈进调度**：`next_action_advisor.py::_find_stale_active_goals()`
   已经能检测"高优先级但超过 `STALE_DAYS`（默认 7 天）没有 `last_touched_at` 更新"的
   Goal，但这条信号目前只用于生成晨报/主动推送提醒（"提醒用户去看一眼"），完全不会
   反过来影响 `active_objectives()` 的排序或调度决策——系统"知道"某个 Goal 被冷落了，
   但不会自己纠正。

### 证据（代码位置）

- `src/mini_agent/perception/goal_backlog.py:285-291`
  ```python
  def active_objectives(self) -> list[GoalNode]:
      ...
      return sorted(objs, key=lambda n: n.priority, reverse=True)
  ```
- `src/mini_agent/evolution/autonomous_loop.py:318-339`（`_tick_maintenance()` 内，
  从上面排好序的扁平列表里从头挑，塞满并发槽位为止，没有按 Goal 去重/限流）。
- `src/mini_agent/evolution/objective_executor.py:48`：`MAX_CONCURRENT_OBJECTIVES = 2`。
- `src/mini_agent/config/models.py:1119`：`auto_objective_max_per_goal: int = 3`。
- `src/mini_agent/evolution/objective_executor.py:510-562`（`on_turn_done()`）：一步
  完成立即自动提交下一步，无中途让出槽位的逻辑。
- `src/mini_agent/evolution/next_action_advisor.py:68-101`
  （`_find_stale_active_goals()`）：已有停滞检测，但只喂给
  `generate_next_actions()` 做展示排序，不写回 `GoalBacklog`/不影响调度。

## 2. 分阶段实施计划

### P1 —— Goal 粒度并发上限（改动最小，见效最直接）✅ 已实现（v1.1）

- **目标**：新增规则"同一 Goal 同时最多占用 N 个执行槽位"（默认 N=1），从根源上
  杜绝"一个 Goal 自己吃满所有并发槽位"这种最极端的情况。
- **设计**：
  - `GoalNode` 需要能够从 Objective 反查所属 Goal（`GoalNode.parent_id`，
    `goal_backlog.py` 已有父子关系维护，`add_objectives_for_goal()` 写入时会
    设置该字段，可直接复用，无需新增数据结构）。
  - 在 `_tick_maintenance()` 挑选新 Objective 的循环里（`autonomous_loop.py:324-338`），
    新增一次"该 Objective 所属 Goal 当前已占用槽位数"检查：统计 `ObjectiveExecutor`
    里 `status == "running"` 且 `parent_id` 相同的 execution 数，达到上限则跳过本次
    循环，继续看排序里的下一个候选（而不是直接 break，避免因为一个 Goal 顶到上限就
    让本轮调度提前结束）。
  - 新增配置 `autonomy.max_concurrent_objectives_per_goal`（默认 1）。设为 0 或负数
    视为不限制（等价于关闭本项，向后兼容旧行为）。
- **不做**：不改变 Objective 拆解数量（`auto_objective_max_per_goal` 仍是 3），只限制
  "同时在跑"的数量——多出来的 Objective 仍然会排队，只是排队顺序交给 P2 处理。
- **验收标准**：
  1. 构造一个并发上限=2、`max_concurrent_objectives_per_goal=1` 的场景，Goal A 有
     2 个可执行 Objective、Goal B 有 1 个：调度结果应是 Goal A 占 1 个槽位、Goal B
     占 1 个槽位，而不是 Goal A 占满 2 个。
  2. `max_concurrent_objectives_per_goal` 设为 0 时，行为与改造前完全一致（回归测试）。
- **工作量**：小。只是在已有循环里加一次计数检查，不涉及新的持久化字段或状态机改动。

### P2 —— 调度从"纯优先级排序"改为"公平轮询" ✅ 已实现（v1.1）

- **目标**：让 Objective 的挑选顺序不再只看静态 `priority`，而是优先照顾"最近一段
  时间没获得过执行机会"的 Goal，从根本上解决"总在执行同一批 Goal"的问题。
- **设计**：
  - `GoalNode` 新增一个调度专用字段 `last_scheduled_at`（区别于已有的
    `last_touched_at`——后者是"内容/进度有更新"的时间戳，前者专门记录"这个 Goal
    上次被分配到执行槽位（Objective 被 `start()`）的时间"）。`ObjectiveExecutor.start()`
    成功后，通过已有的 `goal_backlog` 引用（Track B 已接入）回写所属 Goal 的这个字段。
  - 新增 `GoalBacklog.active_objectives_fair_ranked()`（与现有
    `active_objectives()` 并存，不改动/不废弃后者，保持向后兼容——旧调用方或
    `goal_scheduling_strategy="priority"` 时仍用原方法）：
    1. 按 `parent_id` 分组，同一 Goal 内部仍按 `priority` 降序，取本组内排第一的
       Objective 作为该 Goal 本轮的"代表候选"；
    2. Goal 之间按 `last_scheduled_at` 升序排列（`None`/从未被调度过的排最前）作为
       主排序键，`priority` 降序作为同一时间桶内的次级排序键；
    3. 一轮只从每个 Goal 里取一个代表候选放入结果列表最前部分；若某个 Goal 在本轮
       未被选中（因为并发槽位已经被更久未调度的 Goal 占满），下一轮排序时它的
       `last_scheduled_at` 仍是旧值，自然会排到更前面——不需要额外的"补偿"逻辑，
       排序本身就是自我修正的。
  - 新增配置 `autonomy.goal_scheduling_strategy`：`"fair_round_robin"`（新默认）|
    `"priority"`（旧行为，供灰度回退）。
- **不做**：不改变 `priority` 字段本身的读写语义，不做跨 Goal 的"优先级归一化"之类
  更复杂的公平性算法（如 Weighted Fair Queuing 的严格数学模型）——先用"最久未调度
  优先 + 组内按 priority"这种简单直观、易于验证和解释的规则上线，观察效果后再考虑
  是否需要更精细的算法。
- **验收标准**：
  1. 构造 3 个优先级相同的 Goal，各 1 个 Objective，并发上限=1：连续多轮 tick 后，
     3 个 Goal 应该轮流获得执行机会，而不是每次都选同一个（旧的稳定排序行为）。
  2. 构造优先级不同的 2 个 Goal：在"从未被调度过"这个起点相同的前提下，第一轮应该
     优先选高优先级的（priority 仍然在同批候选间起作用，不是被完全忽略）；但那个
     Goal 被调度过一次后，即使 priority 仍然更高，下一轮也应该轮到另一个 Goal（因为
     `last_scheduled_at` 更新后排序权重变了）。
  3. `goal_scheduling_strategy="priority"` 时，行为与改造前完全一致（回归测试）。
- **工作量**：中。涉及新增一个持久化字段（`last_scheduled_at`，走已有的
  `update_fields()`/落盘机制，不需要新的存储基础设施）和一个新的排序函数，需要
  仔细测试"排序自我修正"这条性质（不能引入需要额外状态维护的补偿逻辑）。

### P3 —— 优先级老化（防饥饿兜底） ✅ 已实现（v1.1）

- **目标**：P2 的公平轮询已经能防止"总在执行同一个 Goal"，但如果某个 Goal 因为
  `max_concurrent_objectives_per_goal` 限制、或者它的 Objective 一直因为路径冲突
  （Track C）/资源门控降级等原因迟迟起不来，仍可能长期没有实际进展。P3 作为兜底：
  对连续停滞的 Goal 临时提升其调度优先级，直到它重新获得一次执行机会。
- **设计**：
  - 复用现有 `next_action_advisor.py::_find_stale_active_goals()` 的判定逻辑
    （`STALE_DAYS`/`STALE_PRIORITY_FLOOR`，或读取 `cfg.next_action_stale_priority_floor`
    等既有配置，不重新发明一套阈值），但新增一个供调度侧调用的轻量函数
    `compute_aging_boost(node, now) -> int`：`days_since_touched >= stale_days` 时
    返回一个随停滞天数线性增长、有上限的加成值（如 `min(days_since - stale_days, 14) * 1`），
    否则返回 0。
  - `active_objectives_fair_ranked()`（P2 新增的排序函数）在计算组内排序 key 时，
    使用 `effective_priority = node.priority + compute_aging_boost(node, now)`
    代替原始 `priority`——**只影响排序计算，不写回、不持久化** `node.priority` 本身。
  - Goal 一旦重新被调度（`last_scheduled_at` 更新），下一次读取时
    `days_since_touched` 会随 `last_touched_at`（执行产生的进展自然会更新它）一起
    归零，`aging_boost` 自动回到 0——不需要额外的"清零"写入逻辑。
- **不做**：不修改 `next_action_advisor.py` 本身的通知/推送行为——P3 只是新增一个
  给调度侧复用的纯函数，晨报/主动推送那条链路保持不变，两边共享同一份"什么算停滞"
  的判定标准即可，不需要合并成同一套代码路径。
- **验收标准**：
  1. 构造一个停滞 10 天、priority=30 的 Goal 和一个 priority=50、刚被调度过的 Goal：
     加了老化加成后前者的 `effective_priority` 应该反超后者，排序结果体现出来。
  2. Goal 重新被调度一次后，`aging_boost` 应在下一次计算时降为 0（因为
     `last_touched_at` 已更新，`days_since_touched` 归零）。
- **工作量**：小。是在 P2 排序函数基础上新增一个计算维度，不涉及新的持久化字段。

### P4（较大改动，建议观察 P1-P3 效果后再决定是否需要）—— 执行时间片化 ✅ 已实现（v1.3）

- **目标**：P1-P3 解决的是"槽位空出来的那一刻该分配给谁"，但如果单个 Objective 的
  步骤本身很长（例如被拆成 10 步、每步跑 20 分钟），槽位可能几个小时才空一次，
  再公平的排序在这段时间内也无能为力。P4 是真正的抢占式时间片：让执行中的 Objective
  也能中途让出槽位。
- **设计（草案，细节留待开工前进一步讨论，见 §4 待讨论问题 1）**：
  - `ObjectiveExecution` 新增状态 `paused_for_fairness`（区别于 Track J 已有的
    "资源门控暂停"，那是全局性、被动触发的；这里是"轮到别人了，主动让出"）。
  - `on_turn_done()` 里，除了现有"是否还有下一步"的判断，新增一个检查点：如果
    `已连续完成的 step 数 >= K` 或 `本次 execution 已运行时长 >= T`，且此刻公平排序
    算出来的"下一个该被调度的 Goal"不是自己所属的 Goal，则不立即提交下一步，转为
    `paused_for_fairness`，释放槽位（不计入 `running_count()`）；下次轮到它时从
    `current_step_idx` 断点续跑，已完成的 step 进度不丢失。
  - K/T 阈值建议做成配置项（如 `autonomy.fairness_yield_after_steps`/
    `autonomy.fairness_yield_after_seconds`），避免步数极少的 Objective 被无意义地
    打断。
- **实际实现（v1.3，与草案的差异见下）**：
  - 按草案原样实现了 `paused_for_fairness` 状态、
    `on_turn_done()` 里的让出检查点、`fairness_yield_after_steps`/
    `fairness_yield_after_seconds` 两个阈值配置（K=3 步 / T=900 秒）。
  - 额外新增一个草案未提及的总开关 `autonomy.
    fairness_time_slicing_enabled`（默认 `False`）——P4 相比 P1-P3 是更
    激进的行为变化（会主动打断本可以连续跑完的 Objective），按本计划
    "默认行为变化需要可灰度控制"的设计边界，选择默认关闭、按需开启，而
    不是像 P1-P3 那样默认直接切换新行为。
  - `_should_yield_for_fairness()` 增加了一条草案未明确写出、但符合其
    精神的判断：只有当排序结果里确实存在另一个"未在运行"的 Goal 排在
    自己前面时才让出；只有一个 active Goal（没有其它 Goal 排队）时，即使
    跑满阈值也不让出——避免无意义的暂停/恢复开销。
  - 断点续跑通过新增的 `resume_fairness()` 实现：从 `current_step_idx`
    重新提交，不重新拆解 Objective、不丢失已完成 step 的进度，并重置该
    execution 的"执行片段"计时起点（`fairness_slice_started_at`/
    `fairness_slice_start_step`，新增的持久化字段）。
  - §4 待讨论问题 1 提到的"当前 step 本身就是长耗时单个 LLM 调用、无法在
    step 内部抢占"的边界情况仍然存在——P4 的时间片粒度天花板确实是"一个
    step 的耗时"，这一点在实现后没有变化，也不在本轮改动范围内解决。
- **工作量**：大。涉及执行状态机的新增分支和"断点续跑"的正确性验证，建议单独排期，
  不与 P1-P3 一起上线。（实际实现中因为复用了已有的排序函数/持久化机制，
  未涉及新的存储基础设施，工作量比预估略小，但状态机分支和断点续跑仍是本次
  改动里最复杂的部分。）

### P5 —— 看板可视化 + 配置文档 ✅ 已实现（v1.2）

- **目标**：把"哪些 Goal 最近获得了执行机会、哪些被冷落"变得肉眼可见，而不是只能
  靠翻 `objective_executions.json`/`activity_digest.jsonl` 猜。
- **设计**：
  - 复用上一轮"自诊断信号闭环"看板集成的模式（`GET /v1/self/diagnosis_feedback`
    的姊妹端点，或直接扩展同一个端点新增一个字段），新增只读接口返回：每个 active
    Goal 的 `last_scheduled_at`、最近 N 天获得的执行次数、当前 `effective_priority`
    （含老化加成）。
  - 看板"🧠 自我状态"tab 新增一个"⚖️ 执行公平性"折叠区块，用简单的表格/列表形式
    展示上面这些字段，按 `last_scheduled_at` 升序排（最久没轮到的排最前，跟实际
    调度顺序一致，方便核对"系统的调度决策是否符合预期"）。
  - `docs/` 下补一份配置项说明（`max_concurrent_objectives_per_goal`/
    `goal_scheduling_strategy`/停滞老化相关配置），沿用现有配置文档的组织方式。
- **不做**：不提供"手动调整调度顺序"之类的交互式操作——本计划的边界是"让自动调度
  更公平"，不是"把调度决策权转移给人工微调"，人工干预仍然通过已有的"改优先级/改
  状态"这些通用手段进行。
- **工作量**：小。纯读取展示，复用已有的看板改造模式。

## 3. 明确不做的事（写清楚边界，避免后续误解）

- 不改变 Goal/Objective 的产生、拆解逻辑（`SoftGoalDeriver`/`_decompose()` 等）——
  本计划只管"已经存在的多个 active Goal 之间，执行资源怎么分"，不管"该不该产生
  新 Goal"或"一个 Goal 该拆成几步"。
- 不改变用户手动设置的 `priority` 字段的语义和存储值——所有"老化加成"类机制都只
  影响调度侧临时计算出的有效优先级，不会覆盖用户主动做的优先级判断。
- 不引入任何 LLM 调用，纯规则化调度。
- P4（时间片抢占）已实现（v1.3），但默认关闭（`autonomy.
  fairness_time_slicing_enabled=False`），需要显式开启才会生效——按需灰度，
  不强制所有已有部署一起切换到抢占式行为。

## 4. 待讨论问题（留空，实施前需要确认）

1. ~~P4 的 K（步数阈值）/T（时长阈值）具体取值~~ ——v1.3 已按草案给出默认值
   （K=3 步、T=900 秒，`autonomy.fairness_yield_after_steps`/
   `fairness_yield_after_seconds`），先用这组保守默认值上线（默认整体关闭，
   需显式开启），未来可再根据实际 step 耗时分布调整。"当前 step 本身就是
   长耗时单个 LLM 调用、无法在 step 内部抢占"这一边界情况仍然存在——P4 的
   时间片粒度天花板确实就是"一个 step 的耗时"，v1.3 未尝试解决这个更深的
   问题，留作后续观察。
2. P1 的 `max_concurrent_objectives_per_goal` 默认值是否应该是 1，还是应该允许
   在总并发数较大（比如未来 `MAX_CONCURRENT_OBJECTIVES` 上调）时按比例放宽——
   本计划先按当前并发上限普遍很小（2，甚至降级到 1）的现状定为固定值 1，待并发
   上限本身的策略演进后再重新评估是否需要动态化。
3. P2 的"公平轮询"和 P3 的"老化加成"两者的排序权重如何精确组合（比如老化加成
   是否应该有一个远高于 priority 正常取值范围的上限，以保证"停滞够久"最终一定能
   反超任何 priority），需要在实施时结合 `priority` 字段的实际取值范围（当前看板
   UI 里 slider 是 0-100）来标定具体系数，而不是一开始就精调。
4. P5 看板展示是否要跟已有的"🩺 自诊断信号闭环"面板合并成一个统一的"调度健康度"
   tab，还是保持在"🧠 自我状态"tab 内作为独立区块——留待 P1-P3 落地、实际有数据
   可展示时再决定信息架构，不提前拍板。
