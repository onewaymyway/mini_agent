# Daemon 调度统一化 + 看板可观测性改进方案

**日期**：2026-08-04
**背景**：对 `evolution/cron_scheduler.py`、`cron_job_runner.py`、`goal_cron_bridge.py`、
`objective_executor.py`、`resource_arbiter.py`、`autonomous_loop.py`、
`apps/mini_agent_kanban/app.py` 逐一读码后确认的具体问题，不是泛泛的架构建议。
每一条都标注了确认方式（读的哪个文件/哪一行为准）和优先级。

---

## 0. 核心发现：三条执行通道，只有一条真正接了资源仲裁

读码确认的调用关系：

```
AutonomousLoop.tick()
 ├─ _tick_passive()  ← 每个 tick 都执行，不受任何门控
 │    └─ cron_scheduler.tick()
 │         └─ 到期 job → _fire(job)
 │              ├─ run_mode="goal_cycle" → self._goal_cycle_fn(job)  [goal_cron_bridge 注册]
 │              ├─ local_handler（sys:watchlist_* 等，进程内直跑，不走 LLM）
 │              ├─ job_runner.submit(job)  ← cron_job_runner.py，独立线程池
 │              │     └─ 并发控制：threading.Semaphore(max_concurrent=2)  【只看并发数，不看资源/预算/用户状态】
 │              └─ submit_fn(...)  ← 旧路径，塞进 InputQueue 和用户消息共用主线程
 │
 └─ _tick_maintenance()  ← 仅 daemon 模式的 autonomous 分支执行
      └─ 提交自主任务前先查 ResourceArbiter.gating_state()  ← 只有这条路径真正做了仲裁
           └─ full / degraded / blocked 三态
```

`objective_executor.py` 内部（Goal 展开出的每个 Step 提交前）也会调用
`ResourceArbiter.can_run_autonomous()`（见 objective_executor.py:16, 228, 322, 557, 607）。

**但 `cron_job_runner.py` 从头到尾没有 import 或调用 `resource_arbiter`**（`grep -n
"resource_arbiter\|ResourceArbiter" cron_job_runner.py` 零命中）。它自己的并发闸门只是一个
`threading.Semaphore(max_concurrent)`，默认 2（cron_job_runner.py:68）。也就是说：

- 用户正在活跃使用（`ResourceArbiter` 的用户优先规则）→ autonomous 任务会 PAUSED，
  但同一时刻 cron job（包括用户自己绑定的 `run_mode=goal_cycle` job）**照常触发**；
- 每日 token 预算耗尽 → autonomous/objective 路径会被挡，**cron job 不受影响**，
  会继续消耗预算，导致"预算硬限制"这条规则名存实亡；
- `_tick_passive()` 里 `cron_scheduler.tick()` 是每个 tick **无条件**执行的
  （autonomous_loop.py:113-124），没有任何前置门控检查。

这不是"三条仲裁规则还需要加强"的问题，是**有一整条执行通道完全没接入仲裁**的问题。
下面 P1 就是修这个。

---

## P1（高优先级）：把 cron 执行通道接入 ResourceArbiter 【已完成】

**处理状态：已修复。**

- `src/mini_agent/evolution/cron_job_runner.py::submit()`：在拿到 semaphore 之前、
  记账之前，新增一段仲裁检查——`if not job.is_system:` 构造 `ResourceArbiter`
  调用 `gating_state()`，`state == "blocked"` 时不触发（返回 False，累加
  `arbiter_skipped_count`，不占用 semaphore/不记账）；`degraded` 不阻断；
  仲裁模块本身异常时保守放行。
- **实现时修正了原方案的一处错误假设**：原方案打算用 `job.initiator != "cron"`
  区分"用户自定义 job"，但读码确认 `CronScheduler.add_job()`（用户创建 job
  的唯一入口）把 `initiator` 硬编码成 `"cron"`——这个字段实际语义是"提交
  给下游时打的来源标签"，不代表"谁创建的这条 job"，用它做门控条件会导致
  检查永远不生效。改为只用 `job.is_system`（`job_id` 是否 `sys:` 前缀）
  判断，`goal_cron_bridge` 绑定的 `run_mode="goal_cycle"` job 走
  `CronScheduler._fire()` 里独立的 `_goal_cycle_fn` 分支，根本不会到达
  `job_runner.submit()`，因此 `is_system` 已经能完整覆盖到达这里的所有
  用户自定义 message 类 job。
- 新增 `CronJobRunner.arbiter_skipped_count` 只读属性，进程内累计"因仲裁被
  跳过"次数，供 P3 看板展示使用。
- 测试：`tests/test_cron_job_runner_resource_arbiter.py`（6 个用例）：blocked
  跳过且计数、full/degraded 放行、sys: job 无论仲裁状态如何都不受影响
  （且不调用 arbiter，验证零开销）、仲裁模块抛异常时保守放行。
  `tests/test_cron_job_runner.py`（既有 13 个用例，默认 `initiator="cron"`
  路径）全部无回归，两个文件合计 19 个测试全部通过。

### 原方案设计（供对照，已被上面的"实际实现"覆盖）

### 目标
让 `job_runner.submit()` 触发前也过一遍资源仲裁，行为与 objective_executor 的
Step 提交前检查对齐，而不是自成一套。

### 设计要点

1. **区分"系统维护类 cron"和"用户/goal 驱动类 cron"**：
   `sys:` 前缀的内置维护任务（sys:digest_trim、sys:session_cleanup 等）本身就是低频、
   轻量、幂等的清理动作，不应该被用户在场就挡住——这些不需要接仲裁。
   需要接的是 `initiator != "cron"` 或 `run_mode == "goal_cycle"` 的用户自定义/
   goal 驱动 job，因为它们和 autonomous objective 一样会调用 Agent 做实质性工作、
   消耗预算、可能碰用户文件。

2. 在 `CronScheduler._fire()` 里，`goal_cycle` 分支已经天然会走到
   `objective_executor` 的 Step 提交检查（因为 goal_cycle_fn 最终调用的是
   objective_executor 的派生逻辑）——**这条其实已经间接受仲裁保护，读码确认无需改动**。
   真正的缺口是 `job_runner.submit()` 这条路径，对应用户自定义的
   `run_mode="message"` 类 cron job。

3. 在 `cron_job_runner.py::submit()` 开头新增一次轻量检查：
   ```python
   if job.initiator != "cron":  # 用户自定义 job，非内置系统维护
       from mini_agent.evolution.resource_arbiter import ResourceArbiter
       arbiter = ResourceArbiter(self._paths, self._cfg)
       state = arbiter.gating_state()["state"]
       if state == "blocked":
           # 不触发，等同于"这次没触发成功"，与 job_runner 已有的
           # "已有一次执行在跑返回 False"语义一致，next tick 重试
           return False
       # state == "degraded" 时不阻断，但可以考虑降低 max_concurrent
       # 的有效值（复用 objective_executor 已有的 adaptive_concurrency 思路）
   ```
   `sys:` 前缀 job 不检查，直接沿用现状——避免维护类任务因为用户在用电脑就永远跑不上，
   这些任务本身就设计成不打扰用户（低频、只读扫描为主）。

4. **不做**：不改 `sys:` 内置 job 的行为；不改 goal_cycle 分支（已经间接受保护）；
   不改 local_handler 分支（进程内确定性任务，不调用 LLM，不消耗预算，无需仲裁）。

### 测试
新增 `tests/test_cron_job_runner_resource_arbiter.py`：
1. `initiator="cron"`（sys job）在 blocked 状态下仍然触发——不受影响的回归验证
2. `initiator="user"` 的自定义 job 在 blocked 状态下 `submit()` 返回 False，
   不占用 semaphore
3. `initiator="user"` 的自定义 job 在 degraded 状态下仍然触发（当前先不做并发收紧，
   留到 P1.1 再做，避免一次改动混两个行为）
4. arbiter 检查本身抛异常时 `submit()` 仍按"未接入仲裁前"的行为继续（保守放行，
   不能因为仲裁模块异常导致所有 cron job 停摆）

---

## P2（高优先级）：CronJob 优先级字段 + tick() 触发顺序 【已完成】

**处理状态：已修复。**

- `src/mini_agent/evolution/cron_scheduler.py`：
  - `CronJob` 新增 `priority: int = 0` 字段，`to_dict`/`from_dict` 对应更新，
    旧 `cron_jobs.json` 反序列化后 `priority` 缺省为 0，行为等同于改造前。
  - `CronScheduler.tick()` 改为两阶段：先收集本轮到期的 job（`due_jobs`），
    按 `priority` 降序 `sort()`（Python `sort()` 稳定排序，priority 相同时
    保持原有插入顺序）后再逐个 `_fire()`——不做抢占，只影响"同一个 tick
    内谁先被提交"。
  - `load()` 里内置 `sys:` job 统一赋 `priority = 5`（`_BUILTIN_JOBS` 字典
    本身不逐条改，加载时统一补上）。
  - `add_job()` 新增可选 `priority` 参数：不传时按 `run_mode` 给默认值——
    `run_mode="goal_cycle"` 默认 10（高于系统维护），普通 `"message"` 类
    默认 0（低于系统维护，避免大量一次性用户任务挤占系统维护 job）。
- 测试：`tests/test_cron_scheduler_priority.py`（8 个用例）覆盖排序、
  稳定性、旧数据兼容、`add_job()` 默认值、内置 job 优先级；连同既有
  `test_cron_agent_bridge.py`/`test_cron_job_runner*.py`/
  `test_cron_job_workspace_and_executor.py`/`test_cron_schedule_validation.py`/
  `test_cron_scheduler_local_handler.py`/`test_cron_scheduler_reap_stale_jobs.py`/
  `test_goal_cron_bridge.py` 全量 cron 相关测试合计 101 个全部通过，无回归。
- 文档：本文件"待确认项"里关于 `cron_job_runner.submit()` semaphore 满时
  行为的疑问已通过读码确认——`submit()` 立即返回，真正的并发限制发生在
  后台线程内部的 `semaphore.acquire()`，会阻塞排队而不是拒绝（见
  cron_job_runner.py 现有注释）。因此 P2 的"不做抢占"设计成立：优先级
  只影响"提交顺序"，而提交顺序决定了谁先排到 semaphore 队列前面。

### 原方案设计（供对照，已被上面的"实际实现"覆盖）

### 现状确认
`CronScheduler.tick()`（cron_scheduler.py:541-577）遍历 `self._jobs.values()`
即 dict 插入顺序，逐个判断到期、逐个 `_fire()`，**没有任何优先级概念**。
`_jobs` 是 `dict[str, CronJob]`，job 的触发顺序完全由"注册先后"决定——内置
`sys:` job 因为在 `load()` 里先注入，永远排在用户自定义 job 前面；多个用户自定义
job 之间的顺序则取决于创建时间，没有语义。

同一个 tick 里如果有 5 个 job 同时到期，且 `cron_job_runner` 的
`max_concurrent=2`，后 3 个会因为拿不到 semaphore 而在 `submit()` 内部...
（需确认：读 cron_job_runner.py 确认 submit() 在拿不到许可时的行为，见下方"确认项"）。

### 设计要点

1. `CronJob` 新增 `priority: int = 0` 字段（数值越大优先级越高），`to_dict`/
   `from_dict` 对应更新，缺省值 0 保证旧数据反序列化后行为不变。
2. `tick()` 里，收集本轮到期的 job 后先按 `priority` 降序排序，再依次 `_fire()`：
   ```python
   due_jobs = [j for j in self._jobs.values() if j.enabled and j.next_run_at > 0 and now >= j.next_run_at]
   due_jobs.sort(key=lambda j: j.priority, reverse=True)
   for job in due_jobs:
       ...
   ```
3. 内置 `_BUILTIN_JOBS` 里的 sys:job 默认给一个中等优先级（比如 5），用户通过
   goal_cron_bridge 绑定的 `goal_cycle` job 默认优先级和用户创建时机相关，
   建议默认给 10（比系统维护任务优先），普通用户自定义 message 类 job 默认 0。
4. `/cron` 相关的 CLI 命令和 kanban 的 cron_jobs_tab 增加 priority 的展示和编辑入口。

### 需要先确认再实现的一点
`cron_job_runner.submit()` 在 semaphore 满时的具体行为（是阻塞等待还是立即返回
False）直接决定"优先级排序"有没有意义——如果是阻塞等待，那先 `_fire()` 的 job
会占住 worker，后面高优先级的 job 反而要排队等低优先级的跑完，优先级排序只在
"谁先进队列"层面生效，不能抢占正在执行的低优先级 job。这属于设计取舍，
本方案默认**不做抢占**（正在跑的 job 不会被打断），高优先级只影响"当多个 job
同时到期、worker 有空位时先给谁"。抢占式调度复杂度和风险明显更高，本轮不做。

### 测试
`tests/test_cron_scheduler_priority.py`：多个同时到期 job 按优先级排序触发；
`priority` 缺省字段的旧 cron_jobs.json 加载后全部为 0，行为等同于现状（无回归）；
`priority` 相同时退回当前的插入顺序（稳定排序，不引入随机性）。

---

## P3（中优先级）：`ResourceArbiter.gating_state()` 全局可见 + 看板常驻状态卡 【已完成】

**处理状态：已修复。**

- 后端确认/补齐：`/v1/autonomous/status` 早已把 `ResourceArbiter.diagnose()`
  透出为 `gating` 字段（api/routes.py::get_autonomous_status，读码确认无需
  改动，原方案"待确认项"到此已解答）。补齐的是 P1 新增的
  `CronJobRunner.arbiter_skipped_count`：透传到
  `/v1/self/execution_model_status` 的 `cron.arbiter_skipped_count`
  （与既有的 `cron.reaped_job_count` 同一处，风格一致）；`cron_jobs`
  列表也补充了 `priority`（P2 新增字段，`to_dict()` 已包含，路由无需
  额外改动即可透出）。
- 前端（`apps/mini_agent_kanban/app.py`）：
  - `_render_topbar_body()` 顶栏新增常驻"仲裁"徽标（🟢空闲可执行 /
    🟡降级运行 / 🔴已暂停），取自 `autonomous_status()` 的 `gating.gating_state`，
    不再需要先找到某个具体 Goal 才能看到。
  - 新增 `_render_gating_detail()`：非 `full` 状态时在顶栏下方展开一个
    expander，逐条展示 `diagnose()` 的三条规则（预算/挫败感/用户在场）
    通过情况，并额外展示 cron 通道因本仲裁被跳过触发的累计次数
    （`execution_model_status()` 的 `cron.arbiter_skipped_count`），
    把 P1 新增的"cron 通道也受仲裁约束"这件事对用户可见。
  - `render_cron_jobs_tab()`：job 卡片头部展示 `priority`；新增"🔢 调整
    优先级"expander（`update_cron_job(job_id, priority=...)`）；"新建
    cron job"表单新增可选 priority 输入框。
- 后端配套改动（为支撑上面的编辑入口）：`CronScheduler.update_priority()`
  新增；`PUT /cron/jobs/{job_id}` 支持 `priority` 字段；`POST /cron/jobs`
  支持可选 `priority` 字段（不传时沿用 `add_job()` 的默认值规则）；
  `apps/mini_agent_kanban/client.py::add_cron_job()` 新增可选
  `priority` 参数。
- 测试：`tests/test_execution_model_status_routes.py` 新增 2 个用例
  （`arbiter_skipped_count` 透传、无 job_runner 时默认 0）；
  `tests/test_cron_scheduler_priority.py` 新增 `update_priority()` 的
  2 个用例（更新并落盘、job 不存在时返回 False）。看板前端本身沿用
  项目现状（无自动化 UI 测试基础设施），未新增前端测试，通过手工读码 +
  `py_compile` 语法检查确认改动正确。
- 全量 cron/仲裁相关测试（`test_cron_*.py` 8 个文件 +
  `test_execution_model_status_routes.py` + `test_goal_cron_bridge.py`）
  合计 119 个测试全部通过，无回归。

### 原方案设计（供对照，已被上面的"实际实现"覆盖）

### 现状确认
kanban `app.py` 里目前对 `ResourceArbiter` 诊断结果的展示只在 kanban_tab 内、
针对单个 Goal 的检查场景出现（app.py:2296-2298，`st.success("✅ 资源仲裁...")`），
是"点开某个 Goal 时临时查一次"，不是常驻状态。用户如果想知道"为什么现在没有
自主任务在跑"，需要先找到一个具体 Goal 才能触发这段诊断逻辑；如果问题出在
cron 侧（P1 修完后 cron 也会被 arbiter 挡），则完全没有入口能看到。

### 设计要点
1. 复用已有的 `ResourceArbiter.diagnose()`（resource_arbiter.py，四条规则逐条
   `passed`/`reason`/关键数值，已经是为看板设计的返回结构，无需新增后端逻辑），
   通过既有的 `/v1/autonomous/status` 路由暴露（需确认该路由是否已经把
   `diagnose()` 结果透出，若未透出则补一行 `"arbiter_diagnosis": arbiter.diagnose()`）。
2. 在 kanban **顶栏**（`render_topbar`，而非某个 tab 内部）新增一个常驻的仲裁状态
   徽标：`🟢 空闲可执行` / `🟡 降级运行` / `🔴 已暂停（原因）`，点击展开显示
   `diagnose()` 的四条规则详情。这样无论用户停在哪个 tab 都能看到，
   不需要先找到具体 Goal。
3. 展开详情里除了现有的 budget/frustration/presence 三条，P1 上线后还应加一条
   "cron 通道仲裁状态"，说明是否有用户自定义 cron job 因为仲裁被跳过本次触发
   （需要 job_runner 或 cron_scheduler 记录一个"因仲裁跳过"的计数/原因，
   而不只是静默 `return False`）。

### 测试
前端逻辑为主，建议走现有的 kanban 手工验收流程（该项目 kanban 目前似乎没有
自动化 UI 测试基础设施，未在 tests/ 下发现 streamlit 相关测试），后端
`diagnose()` 本身已有测试覆盖，只需为新增的"cron 因仲裁跳过"计数字段补
1-2 个单测。

---

## P4（中优先级）：goal 的 recurring 周期与 cron schedule 语义合并 【已完成（最小版本）】

**处理状态：已按"本轮只做最小一步"的原方案完成，未做数据结构合并（按原方案
明确不建议本轮做）。**

- 读码确认：kanban 里 recurring Goal 卡片此前**完全不展示"下次触发时间"**
  （不是"两套系统各自算了一遍导致不一致"，而是根本没有这个展示项，
  只显示"已完成 N 轮"和绑定的 cron job id）——比原方案预想的"两个数据源
  冲突"风险更小，直接补一个单一数据源的展示即可。
- `apps/mini_agent_kanban/app.py`：
  - `render_kanban_tab()` 复用已经在渲染 Objective 执行进度时取过的
    `client.autonomous_status()` 结果，从其中的 `cron_jobs` 列表（P3 已
    确认自带 `next_run_str`）构建 `cron_job_id -> next_run_str` 的映射，
    不新增任何 API 调用。
  - `_render_goal_card()` 新增可选参数 `cron_next_run_by_id`，recurring
    Goal 卡片正文和"⏰ 周期性设置"展开面板里各自按
    `n["recurrence_cron_job_id"]` 查这个映射，展示"下次触发：{next_run_str}"，
    查不到（比如绑定的 cron job 已被删除）时不展示这一项，不报错。
  - 未改动 `GoalNode`/`goal_mode/state.py`/`goal_cron_bridge.py` 的任何
    数据结构或计算逻辑——`next_run_at` 的唯一计算来源仍然是
    `CronScheduler`，Goal 侧不重复计算。
- 未做（按原方案）：把 `GoalNode` 的周期字段改造成内嵌 CronJob 这类结构性
  合并——本轮只做展示层最小改动，观察项维持原方案的记录方式（如果后续
  `goal_cycle` 类型 cron job 数量明显增多，再评估是否值得做数据结构合并）。
- 纯前端展示改动，无新增后端逻辑，未新增测试，通过 `py_compile` 语法检查
  确认改动正确；`recurrence_cron_job_id` 缺失或未命中映射时的降级路径
  （不展示该行）已在代码里显式处理。

### 现状确认
`GoalNode` 的 `recurring`/`cycle_count` 描述目标的周期性，`CronJob.schedule`
（interval/cron 表达式）描述触发时机，两者通过 `goal_cron_bridge.py` 做**绑定**
（一个 CronJob 的 `goal_id` 指向一个 Goal，`run_mode="goal_cycle"`）。这是两套
独立的数据结构靠外键关联，而不是同一套时间原语的两种视图。

### 设计要点（本轮只做文档记录 + 最小一步，不做大改）
1. 不建议本轮把 GoalNode 的周期字段直接改造成内嵌 CronJob——改动面太大，
   会牵扯 goal_mode/state.py、goal_cron_bridge.py、kanban 的 Goal 卡片渲染等
   多处，风险和收益不成比例。
2. 本轮只做一件小事：在 kanban 的 Goal 卡片展示"下次触发时间"时，直接读取
   绑定的 CronJob 的 `next_run_str()`（cron_scheduler.py 已有此方法），
   而不是 Goal 自己再算一遍周期——确保用户看到的"下次执行"数字只有一个数据源，
   避免两套系统各自计算导致显示不一致。
3. 把"合并两套时间原语"列为观察项：如果后续 goal_cycle 类型的 cron job
   数量明显多于普通 message 类 job，再评估是否值得做数据结构层面的合并。

---

## P5（低优先级，可选）：kanban 新增"全局日程" tab 【已完成】

在 P2（优先级字段）和 P3（仲裁状态可见）落地之后，可以再加一个独立 tab，
把以下三类信息按时间顺序合并展示在一条时间线里：
- 未来 24 小时内到期的 cron job（含 priority）
- 有 recurring goal 绑定的下一次触发（复用 P4 的单一数据源）
- 最近一次仲裁状态变化的时间点（何时从 full 变成 degraded/blocked，何时恢复）

这一项依赖前面几项先完成，且是纯展示层工作，不涉及后端调度逻辑改动。

### 实现说明

前两类信息（cron job 到期时间、recurring goal 下次触发）在 P2/P3/P4 落地后
已经能从 `/v1/autonomous/status` 里拿到，属于纯展示层，直接复用即可。
第三类"仲裁状态变化时间线"此前完全没有持久化——`ResourceArbiter.diagnose()`
每次都是即时计算，没有任何地方记录"历史上什么时候变化过"，因此新增了一个
很薄的记录层：

1. `AgentPaths.gating_history_path`（`<project_root>/.agent/gating_history.jsonl`）
   ——新增路径属性，和其他 `.agent/*.json(l)` 一样走同一套 workdir 目录。
2. `resource_arbiter.py::record_gating_transition(paths, state, reason)`
   ——只在这次的 `gating_state` 和历史文件里最后一条不一样时才追加一行，
   避免把"每次轮询"都记成一条历史（顶栏每几秒轮询一次 `/autonomous/status`，
   如果不去重，时间线会变成"轮询日志"而不是"状态变化时间线"）。文件不存在/
   损坏/写入失败时静默忽略，不能因为这个锦上添花的功能影响主状态查询。
   同时限制最多保留 200 条，防止无限增长。
3. `resource_arbiter.py::read_gating_history(paths, limit=50)` ——读取最近
   `limit` 条，按时间正序（旧→新）返回，损坏的行会被跳过而不是抛异常。
4. 复用现成的 `GET /v1/autonomous/status` 路由：该路由本来就会调用一次
   `ResourceArbiter.diagnose()`，在拿到结果后顺手调用一次
   `record_gating_transition()`，不新增独立的轮询/后台任务。
5. 新增 `GET /v1/autonomous/gating_history?limit=50` 路由，供看板读取历史。
6. `apps/mini_agent_kanban/client.py` 新增 `gating_history(limit=50)` 方法。
7. `apps/mini_agent_kanban/app.py` 新增 `render_global_schedule_tab()`，
   对应新 tab "🗓️ 全局日程"：
   - 顶部展示当前仲裁状态（和顶栏同款徽标语义）；
   - 区块 1：未来 24 小时内到期的 cron job（按 `next_run_in` 排序，展示
     `priority`/`run_count`）；
   - 区块 2：绑定了 `recurring` 的 Goal，展示下次触发时间（数据源和
     P4 的 Goal 卡片完全一致，都是 `cron_jobs` 列表里对应 job 的
     `next_run_str`，没有引入第二套计算逻辑）；
   - 区块 3：仲裁状态变化时间线，从新读取的 `gating_history` 渲染，最新的
     排最上面。
   新 tab 插在"⏰ Cron 任务"和"🔌 外部输入"之间。

### 已知取舍
- 状态变化的记录时机绑定在 `/autonomous/status` 被轮询上，如果看板/daemon
  长时间没有任何客户端轮询这个接口，状态变化不会被记录下来（因为没有触发
  `diagnose()` 的调用）。这属于"用现有轮询顺便记一笔"的最小实现，不新增
  后台常驻任务；如果后续需要"哪怕没人看仪表盘也要记录"，需要在
  `autonomous_loop` 内部主动调用一次，作为独立的后续优化项。

### 测试
`tests/test_gating_history.py`（9 个用例）：
- `record_gating_transition`：首次写入、状态不变不重复记录、多次状态变化
  按序记录、无历史文件时读取返回空列表、`limit` 截断、历史文件损坏时读取
  不崩溃且能继续正常写入。
- `GET /v1/autonomous/gating_history`：空历史返回空列表、记录的状态变化能
  被正确读取、`autonomous_loop` 不存在时返回空列表而不是报错。

全量回归：`test_cron_scheduler_priority.py` + `test_cron_job_runner_resource_arbiter.py`
+ `test_cron_job_runner.py` + `test_execution_model_status_routes.py` +
`test_goal_cron_bridge.py` + `test_gating_history.py` 合计 69 个测试全部通过，
无回归。

---

## 实施顺序与理由（P1-P5 已全部按此顺序完成）

1. **P1 先做**：这是正确性缺口（预算/用户优先规则被绕过），不是体验问题，
   风险最高，应该最先修。
2. **P2 次之**：P1 做完之后，cron 通道会因为仲裁产生"部分 job 被跳过"的情况，
   这时候更需要优先级排序来保证重要 job（比如用户绑定的 goal_cycle）优先
   拿到执行机会，而不是被排在它后面创建的低优先级 job 抢跑。P2 依赖 P1
   先把仲裁接上，否则单纯排序意义有限（反正都会跑）。
3. **P3 可以和 P1/P2 并行**：纯展示层，不依赖前两项的具体实现细节，
   但如果 P1/P2 先做完，P3 展示的信息会更完整（能看到"因仲裁跳过"和
   "因优先级排队"两类原因）。
4. **P4/P5 放最后**：都是锦上添花。P4 只做了最小改动；P5 原计划本轮不
   实现，后续作为独立任务补上（见下方"实施记录汇总"）。

## 待确认项（实现前需要先读码确认，不在本次分析范围内做了假设）
- ~~`cron_job_runner.submit()` 在 semaphore 满时是阻塞还是立即返回 False~~
  【已确认，见 P2 完成说明】：`submit()` 立即返回，真正的并发限制在后台
  线程内部阻塞排队。
- ~~`/v1/autonomous/status` 路由当前是否已经透出 `ResourceArbiter.diagnose()`
  的结果~~【已确认，见 P3 完成说明】：早已透出为 `gating` 字段，无需改动。

## 实施记录汇总（P1-P5 全部完成后）

修改的文件：
- `src/mini_agent/evolution/cron_job_runner.py`（P1：接入 ResourceArbiter）
- `src/mini_agent/evolution/cron_scheduler.py`（P2：priority 字段/排序/
  `update_priority()`）
- `src/mini_agent/evolution/resource_arbiter.py`（P5：新增
  `record_gating_transition()` / `read_gating_history()`）
- `src/mini_agent/storage/paths.py`（P5：新增 `gating_history_path` 属性）
- `src/mini_agent/api/routes.py`（P2/P3：`priority` 透出、
  `arbiter_skipped_count` 透出、`PUT`/`POST /cron/jobs` 支持 `priority`；
  P5：`/autonomous/status` 顺带记录仲裁状态变化、新增
  `GET /autonomous/gating_history` 路由）
- `apps/mini_agent_kanban/app.py`（P3：顶栏仲裁徽标 + 详情展开、cron job
  卡片 priority 展示/编辑；P4：recurring Goal 卡片"下次触发"展示；
  P5：新增"🗓️ 全局日程" tab）
- `apps/mini_agent_kanban/client.py`（P3：`add_cron_job()` 支持可选
  `priority` 参数；P5：新增 `gating_history()` 方法）
- `tests/test_execution_model_status_routes.py`（P3：新增 2 个用例）

新增的文件：
- `tests/test_cron_job_runner_resource_arbiter.py`（P1，6 个用例）
- `tests/test_cron_scheduler_priority.py`（P2，10 个用例）
- `tests/test_gating_history.py`（P5，9 个用例）

全量回归：`test_cron_*.py`（8 个文件）+ `test_execution_model_status_routes.py`
+ `test_goal_cron_bridge.py` + `test_gating_history.py` 合计 128 个测试全部
通过，无回归。

P1-P5 至此全部完成，本方案文档中列出的改进项已无遗留项。
