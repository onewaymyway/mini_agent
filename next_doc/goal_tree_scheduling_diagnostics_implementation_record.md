# 目标树调度诊断面板 — 实施记录

> 起因：用户反馈"目标看板的目标树里所有目标都是暂停或已完成状态，没有
> 任何进行中的"，排查过程中发现这不是单一 bug，而是四个独立问题叠加：
> ① 诊断信息本来就无处可看；② 存在"扁平 Goal 系统"和"🌳 目标树层级
> 体系"两份数据结构，前者 active 但后者的 `get_tree()` 遍历不到；
> ③ `_tick_maintenance()` 刻意不 derive 新 Goal，backlog 清空后没有
> 任何主动性；④ 目标树自己的自动分解 cron（`sys:goal_tree_decompose_
> scan`，见 `goal_tree_system_phase3_implementation_record.md`）虽然
> 已经实现并接线，但停滞判定阈值默认 14 天，用户在看板上完全等不到
> 反馈。本记录对应 `next_doc/goal_tree_system_plan.md` 之外的一次独立
> 迭代，不改动该文档已完成的四个阶段，只是在其基础上补一层"诊断 + 兜底
> 主动性"。

## 一、新增文件

- **`src/mini_agent/perception/scheduling_diagnostics.py`**：只读诊断
  快照模块，风格对齐 `fairness_diagnostics.py`。核心函数
  `scheduling_diagnostics_snapshot(goal_backlog, objective_executor,
  paths, autonomous_loop=None)`，任何异常都吞掉返回空结构而不抛异常。
  返回字段：
  - `scheduling_paused` / `scheduling_paused_reason` / `scheduling_
    paused_at`：全局调度开关（看板"停止调度"对应的
    `operating_state.scheduling_paused`）。
  - `autonomous_loop_running` / `autonomous_loop_last_tick_at` /
    `autonomous_loop_tick_count`：**不是**探测一个不存在的
    `is_running()`/`_running` 属性（`AutonomousLoop` 类根本没有这两个
    成员，早期版本这里恒为 `False`，是本次修的一个诊断代码自身 bug）
    ——而是读 `get_digest_status()` 的 `last_tick_at`/
    `tick_interval_seconds`，用"最近一次 tick 距今是否明显超过一个正常
    tick 间隔（`max(tick_interval*3, 300s)`）"判断循环是否还在正常
    心跳。
  - `user_paused_objectives` / `fairness_paused_objectives`：区分
    `ObjectiveExecutor` 里"需要用户显式 `resume_user_pause()`"和
    "调度器会自动 `resume_fairness()`"两类暂停，各自带 `goal_id`
    （从 `parent_id` 取，供 UI 跳转）。
  - `goals_missing_objective`：`GoalBacklog.goals_missing_objective()`
    的直接透出——active 但没有 active Objective 子节点的 Goal。
  - `orphaned_active_goals`：**目标树/扁平 Goal 两套数据结构不一致**的
    检测——扫描 `get_tree(None)` 从 `ultimate` 根节点能遍历到的节点集合
    `reachable`，找出 `is_active and is_goal and id not in reachable`
    的节点。根因是 `GoalBacklog.add_goal()`（CLI `/agent goals add`、
    看板"新建目标"表单）创建的 Goal 从不设置 `parent_id`、不挂到
    `ultimate` 树下，是一条独立于"🌳 目标树"（`ultimate`/`domain`/
    `stage` 层级，靠 `add_node()`/`GoalTreeDecomposer` 建立）之外的
    扁平列表——这些 Goal 明明 active、也在被 `AutonomousLoop` 正常调度，
    但 `get_tree()` 永远遍历不到，表现为"目标树里全是旧的暂停/已完成
    节点，看不到任何进行中的"，而真正进行中的工作其实在"📋 列表/看板
    视图"里。
  - `pending_goal_proposals`：backlog 耗尽后由
    `AutonomousLoop._maybe_propose_goals_on_exhaustion()`（见下）写入的
    `status="draft"` 候选 Goal。
  - `tree_expansion_candidates` / `tree_pending_decompose_candidates`：
    目标树扩展相关，见下"三、目标树扩展建议"。
  - `has_blocking_issue`：以上任一非空，或 `active_goal_count ==
    active_objective_count == 0`，即为 `True`，供 UI 决定是否弹面板。

## 二、API：`GET /v1/goals/scheduling_diagnostics`

`src/mini_agent/api/routes.py` 新增，跟 `/self/fairness_diagnostics`
同一种取舍（`_require_owner` 鉴权、异常吞掉返回空快照而不是 500）。从
`http_server.autonomous_loop` 拿 `_paths`/`_objective_executor`，独立
`load_goal_backlog(paths)` 读最新 backlog（不复用 loop 内部持有的引用，
避免读到 loop 生命周期以外的状态）。

## 三、目标树扩展建议（核心诉求）

用户最初的反馈落点是"扁平新 Goal"，追问后明确澄清**诉求是目标树本身
该怎么继续扩展，不是扁平列表**。排查发现 `goal_tree_decomposer.py`
早已按 `goal_tree_system_plan.md` §4.2/阶段三完整实现了自动分解机制
（`find_stale_nodes_for_scan()` + `find_parent_needing_decompose_after_
completion()` + `sys:goal_tree_decompose_scan` cron，见
`goal_tree_system_phase3_implementation_record.md`），且已经在
`server.py` 里正确接线、每 24 小时自动巡检——**不是没有机制，是机制被
两道等待闸门挡住**：① 停滞判定要求节点超过 `STALE_DAYS_DEFAULT` 天没
被 touch；② 还要等下一次 cron 巡检。用户在看板上想立刻看到"接下来该往
哪扩展"，等不起。

两处改动：

1. **`STALE_DAYS_DEFAULT`：14 → 2**（`goal_tree_decomposer.py`）。
   直接影响 `sys:goal_tree_decompose_scan` 的实际判定口径，代码常量，
   进程重启后立即对下一次巡检生效，不需要迁移已注册的 cron job 记录
   （`CronScheduler.ensure_job()` 是"缺失才补注册"语义，不会覆盖已存在
   job 的 `schedule`/`description` 字段，但判定逻辑读的是运行时常量，
   跟 job 元数据无关）。`goal_tree_system_phase2_implementation_record.
   md`/`phase3_implementation_record.md` 里原始的 `stale_days=14` 记录
   保留不追溯改写，各自补了一行指回本文档的说明。
2. **看板面板新增"立即扫描"入口**，不必等 cron：
   `scheduling_diagnostics_snapshot()` 复用同一套 `find_stale_nodes_
   for_scan()`/`find_parent_needing_decompose_after_completion()`，但
   把 `stale_days` 传 `0`（不再要求达到阈值天数，只看"活着但没有非终态
   子节点"这个结构性条件本身）、完成态联动的回看窗口从 cron 默认的
   `COMPLETION_LINK_LOOKBACK_SECONDS_DEFAULT`（25 小时）放宽到扫全部
   `completed` 节点，得到 `tree_expansion_candidates`：目标树里此刻
   结构性地"没有下文"、值得继续往下拆的节点全集。同时把已经生成、
   还没被 accept/reject 的 `decompose_candidates` 单独汇总成
   `tree_pending_decompose_candidates`（可能是上次 cron 已经生成但用户
   没注意到的）。

面板对每个 `tree_expansion_candidate` 提供「🌳 生成扩展建议」按钮，走
`async_job_ui.start_async_job`/`run_async_job` 异步任务模式调用
`client.decompose_goal_node(node_id, force=True)`——`force=True` 跳过
`should_decompose()` 的节奏治理（间隔/已有候选检查），因为用户是主动
点击要求"现在就生成"；产出仍然是写进 `decompose_candidates` 字段的
候选，不会绕过"用户 accept/reject 才创建真实节点"这层确认，跟 cron
巡检产出候选的安全语义完全一致。

## 四、backlog 清空后的兜底建议（扁平层）

`_tick_maintenance()` 文档原话是"不 derive 新 Goal……这是与 autonomous
档位的边界——不会凭空产生新意图"，`SoftGoalDeriver` 因此只在
`autonomy_level == "autonomous"` 时被 `_tick_autonomous()` 调用。用户
系统跑在 `maintenance` 档位，这个能力被刻意关闭——不是 bug。

新增 `AutonomousLoop._maybe_propose_goals_on_exhaustion()`
（`evolution/autonomous_loop.py`），在 `_tick_maintenance()` 末尾调用，
只在以下条件都满足时触发：`autonomy_level == "maintenance"` 且
`goal_backlog` 的 `active_goals()`/`active_objectives()` 都为空。复用
`SoftGoalDeriver` 的信号分析（`derive_candidates()`），但**只处理
`other_candidates`**（workthread/lesson 类信号，没有 `Exploration
Sandbox` 验证也相对安全），跳过需要探索实验验证的 `capability` 类
候选（那是 `autonomous` 档位的专属能力，本方法不代为验证）。生成的
Goal 一律 `status="draft"`（`goal_draft_flow_plan.md` 定义的既有语义：
`is_active` 为 `False`，不会被 `active_goals()`/调度器碰到），不是
`autonomous` 档位 `commit_goals()` 直接写的 `active`——是"建议"而不是
"行动"，遵守 `_tick_maintenance()` 开头"不凭空产生新意图"的边界。用户
需要通过面板「✅ 采纳」（等价于 CLI `_cmd_accept`：`status="active"` +
`agent_derived` 来源默认把优先级提到 50）或「🗑️ 忽略」（`status=
"abandoned"`）显式处理。节奏治理复用 `SoftGoalDeriver.should_derive()`
的冷却窗口，`agent_derived` + `draft` 的候选数量单独统计，不占用
`autonomous` 档位 `MAX_PENDING_DERIVED`（active 候选）的名额，两个池子
互不影响彼此的上限。

已用一段独立脚本模拟"backlog 清空 → 触发 → 生成 draft Goal → digest
记录"全流程验证通过。

## 五、看板 UI

只改 `apps/mini_agent_kanban/app.py`（Streamlit 版，**不是**
`apps/mini_agent_kanban_x`——项目实际使用的是前者，本记录第一版曾经
改错目标误改了 kanban_x，已废弃那部分改动，不再赘述）：

- `client.py` 新增 `goals_scheduling_diagnostics()`，对应新接口。
- `app.py` 新增 `_render_goal_scheduling_diagnostics_panel(client)`，
  挂在 `render_kanban_tab()` 顶部（列表/树两种视图切换之前，因此两种
  视图下都能看到）。只有 `has_blocking_issue` 为真时才渲染，避免正常
  状态下占用视线。请求失败（最常见原因：后端 API 进程没有随代码一起
  重启，新路由还没生效）会展开一条可折叠的错误提示而不是静默不显示
  ——早期版本这里是静默 `return`，看起来跟"功能没生效"没有区别，是本次
  一并修的另一个问题。
- 面板内容分五块，按 `scheduling_diagnostics_snapshot()` 的字段各自
  独立渲染：全局调度状态（含心跳 tick 计数）、user/fairness 暂停执行、
  可继续拆解的 Goal（复用既有 `goals_missing_objective` + 手动拆解
  按钮）、目标树相关（`tree_expansion_candidates` 的立即扩展按钮 +
  `tree_pending_decompose_candidates` 的数量提示）、backlog 清空后的
  draft 建议（采纳/忽略按钮）、目标树数据结构不一致的孤儿 Goal（挂载
  到已有树节点的下拉框 + 按钮，调用既有 `reparent_goal_tree_node()`）。

## 六、验证方式

未接入自动化测试套件（本次改动范围内没有新增/修改 `tests/`），验证
手段是针对 `scheduling_diagnostics_snapshot()`/`_maybe_propose_goals_
on_exhaustion()` 各写过一段一次性脚本，用临时目录构造的真实
`GoalBacklog` 走完整读写路径验证（而不是 mock），具体场景：

1. 扁平 Goal 全 paused/completed → `has_blocking_issue=True`。
2. 目标树 `stage` 节点因子 `goal` 完成而失去 active 子节点 →
   `tree_expansion_candidates` 命中该 `stage`。
3. backlog 清空 → `_maybe_propose_goals_on_exhaustion()` 生成一条
   `status="draft"` 的 `agent_derived` Goal 并记入 digest。

后续如果这套诊断面板本身出现回归风险，可以把这三段脚本整理成
`tests/test_scheduling_diagnostics.py`，目前先以实施记录里保留的脚本
片段作为回归验证依据。
