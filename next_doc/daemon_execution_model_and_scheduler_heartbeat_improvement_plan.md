# daemon 执行模型 + 调度心跳解耦 改进方案

> 状态：**阶段一（目标级持久 Worker）、阶段二（调度心跳解耦）均已完成并通过
> 测试；事后复查发现的 §7.1 锁覆盖缺口已修复（见第 7.1 节"处理状态"）**
> 背景来源：与用户的一轮架构走查对话（聚焦"daemon 进程如何更好地执行、更合理地
> 调度、cron、全局 Goal 的执行"），走查了 `autonomous_loop.py` /
> `objective_executor.py` / `objective_agent_bridge.py` / `resource_arbiter.py` /
> `api/server.py` / `api/session_pool.py` 实际代码（不是只看设计文档），发现两个
> 此前所有调度类方案（`goal_execution_fairness_improvement_plan.md` 等）都没有
> 触及的地基性问题。本方案只聚焦这两个问题，不重复已有方案的内容。
> 关联代码：`src/mini_agent/evolution/objective_executor.py`、
> `src/mini_agent/evolution/objective_agent_bridge.py`、
> `src/mini_agent/evolution/autonomous_loop.py`、`src/mini_agent/api/server.py`、
> `src/mini_agent/config/models.py`

## 0. 背景：两个被公平调度算法掩盖的地基问题

`goal_execution_fairness_improvement_plan.md` 的 P1-P5 在"该轮到谁"这个问题上
做得已经很精细（公平轮询、老化加成、抢占式时间片）。但走查实际执行链路后发现，
这套精细的排序算法，管理的很可能只是一个**假象**：

**问题一：默认路径下"并发"只是排队顺序，不是真并行。**
`ObjectiveExecutor._submit_step()` 默认把每个 step 提交进
`bridge.input_queue`——和用户交互对话共享同一个单线程 FIFO 队列
（`AgentRunner._main_loop` 里 `iq.dequeue()` 是严格串行处理的）。
`max_concurrent_objectives_per_goal`/公平轮询排序控制的是"允许多少个 Objective
同时处于'已提交、等结果'的挂起状态"，不是"真的有多少个 Objective 在同时被计算"
——任意时刻只有一个 turn 在真正执行。

代码库里已经预见到这个问题、给出了一个解法：
`objective_agent_bridge.py::ObjectiveIsolatedRunner`，用
`ThreadPoolExecutor`（默认 4 worker）实现真并行。但它默认关闭
（`autonomy.objective_isolated_context_enabled=False`），而且代价是**每个 step
都在一个专属后台线程里构建全新 Agent、跑完立刻丢弃**——不复用、不保留任何跨
step 的会话/工具调用状态，"上一步做到哪了"完全靠纯文本摘要拼接传递。

这是一个不上不下的二选一陷阱：
- 共享队列路径（默认）：有会话连续性，但没有真并行——公平调度算法在管理一个
  假象。
- 隔离 runner 路径（可选）：有真并行，但每个 step 都是"失忆"的一次性 agent，
  且每次都要重新构建 Agent（工具发现、system prompt 构建等固定开销）。

**问题二：调度心跳耦合在主对话循环里，是协作式调度。**
`AutonomousLoop.tick()` 靠 `AgentRunner._main_loop` 里
`iq.dequeue(timeout=0.5)` 超时之后"顺带"检查触发（`server.py` 行 374-376）。
如果这一刻主循环正卡在处理一个耗时很长的 turn（`bridge.agent.run_turn()` 可能
跑几十秒到几分钟），tick 会被顺延同样长的时间——公平调度、cron 触发、资源仲裁
全部一起延迟，且延迟量不可预测（取决于当前在跑的 turn 有多长）。这是典型的
协作式（cooperative）调度问题：调度决策和被调度的工作抢同一个执行线程。

## 1. 理想状态

- "调度决策"（该不该跑、该轮到谁）和"真正执行"（跑 LLM turn）应该是解耦的
  两个系统：前者应该有独立、不被阻塞的心跳；后者应该能真并行，且并行单元的
  粒度应该是"一个 Objective 的完整生命周期"，而不是"一次性的单个 step"——
  这样多个 Objective 之间能真并行，同一个 Objective 内部的多个 step 又能保留
  连续的会话/工具状态，不需要在"真并行"和"有上下文"之间二选一。
- 调度心跳不应该因为某一次长 turn 的执行而被无限期推迟，响应粒度应该只取决于
  心跳自己的轮询间隔，不取决于当前主循环在忙什么。

## 2. 本方案的两个改动

### 阶段一 —— 目标级持久 Worker（Objective-level Persistent Worker）

**目标**：让"真并行"和"跨 step 上下文连续性"不再互斥。

**设计**：
- 新增 `ObjectivePersistentRunner`（`evolution/objective_agent_bridge.py`），
  与现有 `ObjectiveIsolatedRunner` 接口完全一致（可直接替换
  `ObjectiveExecutor._submit_fn`，不需要改动 `objective_executor.py` 的 step
  提交/状态机逻辑本身），但内部实现不同：
  - 每个 `execution_id`（一次 Objective 执行）独占一个**专属单线程**
    `ThreadPoolExecutor(max_workers=1)`，惰性创建（第一个 step 提交时才建）。
  - 该 execution 的 Agent 实例在第一个 step 时构建一次，**缓存在
    `execution_id -> Agent` 的映射里**，同一 execution 后续所有 step 都复用
    这一个 Agent 实例、在同一条专属线程上执行——这与
    `build_objective_agent()` docstring 里强调的"Agent 的 thread-local 状态
    只在构造它的那条线程上安全"这一前提严格对齐：因为该 execution 的所有
    step 永远只在它自己的专属线程上跑，不会跨线程复用 Agent。
  - 不同 `execution_id` 之间各自的专属线程互相独立，因此天然并行——某一时刻
    存在几个活跃 execution，就有几条线程在真正同时执行，不再需要"排队等
    共享队列轮到自己"。真正的并发数上限仍然由 `ObjectiveExecutor` 既有的
    `max_concurrent_objectives_cap`/`adaptive_concurrency_*` 机制约束，本类
    不新增独立的并发上限判断，只负责"某个 execution 该在哪条线程、哪个 Agent
    实例上跑"。
  - 释放时机：`ObjectiveExecutor` 新增可选的 `release_worker_fn` 回调，在
    Objective 到达终止状态（`completed`/`failed`/`cancelled`，对应现有的
    `_on_objective_completed()`/`_on_objective_failed()`/
    `_on_objective_cancelled()` 三个既有的集中收尾方法）时调用，立即关闭该
    execution 的专属线程、丢弃 Agent 实例。三个收尾方法本来就是所有终止路径
    的唯一出口，这里只加一行调用，不改变既有终止判定逻辑本身。
  - 兜底：额外做一个 idle TTL 清理（默认 1800 秒未使用则回收），覆盖
    "daemon 异常重启导致某次终止回调没触发到、专属线程/Agent 变成孤儿"这类
    边界情况，不依赖 `release_worker_fn` 一定会被调用到。
- 新增配置 `autonomy.objective_persistent_worker_enabled`（默认 `False`，
  同项目现有的灰度开关哲学一致）+
  `autonomy.objective_persistent_worker_idle_ttl_seconds`（默认 `1800.0`）。
- 与已有的 `objective_isolated_context_enabled` 的关系：两者语义不同
  （一次性失忆 vs 持久复用），**互斥**，持久 Worker 优先——`server.py` 里
  先判断 `objective_persistent_worker_enabled`，为 `True` 则不再检查
  `objective_isolated_context_enabled`。
- **不做**：不改变 `_submit_step()` 拼接的"前序步骤结果/产出文件"文本摘要
  机制——即使 Agent 实例本身保留了会话历史，仍然保留这个结构化摘要注入
  （双重保险，且看板/日志里展示的 `step.result_summary` 仍然需要这份数据），
  不依赖"模型自己记得"作为唯一信息来源。

**验收标准**：
1. 构造一个 3-step 的 Objective，开启 `objective_persistent_worker_enabled`：
   验证同一 execution 的 3 个 step 确实复用同一个 Agent 实例（同一 `id()`），
   且都在同一条线程上执行。
2. 构造 2 个并行的 Objective：验证两者的专属线程不同，且不需要互相等待
   （用可控的 fake `run_turn` 验证两者可以真正同时处于"运行中"状态，而不是
   一个跑完另一个才开始）。
3. Objective 到达 `completed`/`failed`/`cancelled` 后，对应的专属线程和 Agent
   实例应被立即释放（`release()` 被调用，内部映射不再持有引用）。
4. `objective_persistent_worker_enabled=False`（默认值）时，行为与改造前完全
   一致（回归测试）。

**工作量**：中。新增一个模块 + `ObjectiveExecutor` 三处收尾方法各加一行回调
调用，不改变既有状态机分支本身。

### 阶段二 —— 调度心跳独立化（Scheduler Heartbeat Decoupling）

**目标**：让 `AutonomousLoop.tick()` 的触发时机不再受"当前主循环是否正忙于
处理一个长 turn"影响。

**设计**：
- 新增 `SchedulerHeartbeat`（`evolution/scheduler_heartbeat.py`）：独立的
  后台线程，按自己的轮询间隔（默认 5 秒，配置
  `autonomy.scheduler_heartbeat_poll_interval_seconds`）检查
  `autonomous_loop.should_tick()`，到期则调用 `autonomous_loop.tick()`。
- 线程安全边界（这是本阶段设计的核心，不能只是"另起一个线程调用 tick()"
  就完事）：
  - `AutonomousLoop.tick()` 内部只做"决策 + 提交"（判断该不该跑、调用
    `submit_fn` 把 step 交给执行层），不做真正耗时的 LLM 调用本身，因此
    持锁时间很短。
  - 真正耗时的 `agent.run_turn()`（无论是用户交互还是自主任务的 step）
    **不持有这把锁**——锁只在 `AgentRunner._main_loop` 处理完一个 turn、
    回调 `objective_executor.on_turn_done()`/`on_turn_failed()`（`server.py`
    现有的两处调用点）那一小段状态更新代码上短暂持有。
  - 因此 `SchedulerHeartbeat` 线程和 `AgentRunner` 主循环之间只在"状态更新"
    这个短暂窗口互斥，不会因为一次长 turn 而让心跳整体停摆——这正是本方案
    要解决的问题。共享的 `threading.Lock` 由 `HttpServer` 构造时创建，同时
    传给 `AgentRunner`（新增可选参数 `sched_lock`）和 `SchedulerHeartbeat`。
  - 开启心跳模式后，`AgentRunner._main_loop` 里原有的"dequeue 超时后顺带
    tick"逻辑必须关闭（新增参数 `heartbeat_owns_tick`），避免同一个
    `tick_interval` 周期内被两个地方各触发一次。
- 新增配置 `autonomy.scheduler_heartbeat_enabled`（默认 `False`，与项目现有
  的灰度开关哲学一致——这是比"公平调度算法本身"更底层的执行模型变化，默认
  关闭，观察一段时间后再考虑是否默认开启）+
  `autonomy.scheduler_heartbeat_poll_interval_seconds`（默认 `5.0`）。
- **不做**：不改变 `AutonomousLoop.tick()`/`_tick_passive()`/
  `_tick_maintenance()`/`_tick_autonomous()` 内部的档位判断逻辑本身——本阶段
  只解决"谁来调用 tick()、什么时候调用"，不改变 tick() 内部做什么。

**验收标准**：
1. 开启心跳模式后，人为让主循环"卡"在一个模拟的长 turn 上（fake
   `run_turn` sleep 一段较长时间），验证 `SchedulerHeartbeat` 仍然按自己的
   轮询间隔正常触发 `tick()`，不受主循环阻塞影响。
2. 验证心跳线程调用 `tick()` 期间，如果主循环恰好要执行
   `on_turn_done()`/`on_turn_failed()`，两者通过共享锁正确互斥（不会看到
   `ObjectiveExecutor` 内部字典出现并发写入导致的不一致状态）。
3. `scheduler_heartbeat_enabled=False`（默认值）时，行为与改造前完全一致——
   `AgentRunner` 仍然走原来的"dequeue 超时后顺带 tick"路径（回归测试）。
4. `stop()` 能让心跳线程干净退出，daemon 关闭流程不会因为这条新线程而挂起。

**工作量**：中。新增一个模块 + `AgentRunner`/`HttpServer` 少量接线改动
（新增可选参数，默认值保持原行为不变）。

## 3. 两个阶段的关系与顺序

两者相对独立，可以分开验证、分开灰度：阶段一解决"并发是否真实"，阶段二解决
"调度响应是否及时"。按依赖关系和风险大小，建议先做阶段一（新增模块 + 三处
收尾方法各加一行回调，风险更集中、更容易独立验证），再做阶段二（涉及跨线程
锁语义，需要在阶段一验证过的执行模型基础上做，避免同时引入两个并发相关的
新变量）。

## 4. 明确不做的事

- 不改变公平调度算法本身（P1-P5 的排序/老化/时间片逻辑）——本方案只解决
  "调度决策管理的是不是真实并发"、"调度决策是否被及时触发"这两个更底层的
  问题，不重新设计排序算法。
- 不引入跨用户/跨 session 的全局资源仲裁（这是走查对话里提到的第三个更大的
  缺口，改动面明显更大，需要单独立项）。
- 不改变 cron 触发机制本身（interval-only，不支持依赖/事件触发）——同样是
  更大的改动，留给后续单独评估。
- 两个新配置开关默认都是 `False`，不强制任何已有部署一起切换到新执行模型，
  遵循项目一贯的"默认行为不变，按需灰度"原则。

## 5. 实施记录

### 阶段一 —— 目标级持久 Worker（已完成）

- `src/mini_agent/evolution/objective_agent_bridge.py`：
  - `build_objective_agent()` 新增 `persistent: bool = False` 参数，仅影响
    注入的 `system_extra` 文案（持久模式如实告知"会话历史会在多个 step 间
    保留"，非持久模式保持原有"独立会话、不要假设记得任何未在消息中出现
    的内容"）。
  - 新增 `ObjectivePersistentRunner` 类：每个 `execution_id` 惰性创建一个
    专属 `ThreadPoolExecutor(max_workers=1)` + 缓存一个 Agent 实例，跨
    step 复用；`release(execution_id)` 立即回收；`_evict_idle_locked()`
    做 idle TTL 兜底清理；`shutdown()` 供 daemon 退出时调用。
- `src/mini_agent/evolution/objective_executor.py`：
  - `__init__` 新增可选参数 `release_worker_fn`，默认 `None`（向后兼容）。
  - 新增 `_release_worker(execution_id)` 辅助方法（吞异常，不影响终止流程
    本身），并在 `_on_objective_completed`/`_on_objective_failed`/
    `_on_objective_cancelled` 三个既有的集中收尾方法末尾各加一行调用——
    没有改动这三个方法内部原有的任何判定逻辑。
- `src/mini_agent/config/models.py`：`AutonomyConfig` 新增
  `objective_persistent_worker_enabled: bool = False` 和
  `objective_persistent_worker_idle_ttl_seconds: float = 1800.0`。
- `src/mini_agent/api/server.py`：`_build_autonomous_loop()` 里原有的
  `ObjectiveIsolatedRunner` 接线改成 if/elif 结构——先判断
  `objective_persistent_worker_enabled`，命中则接入
  `ObjectivePersistentRunner` 并同时设置 `_submit_fn`/`_release_worker_fn`；
  否则保留原有的 `objective_isolated_context_enabled` 分支，行为不变。
  `stop()` 里新增对 `_objective_persistent_runner` 的 `shutdown(wait=False)`
  调用，与既有的 `isolated_runner` 关停逻辑对称。
- 测试：新增 `tests/test_objective_persistent_runner.py`（6 个用例，全部
  通过）：
  1. 同一 execution 的多个 step 复用同一个 Agent 实例（`_instances_built`
     恒为 1）。
  2. 两个不同 execution 真并行（用 sleep 验证总耗时接近单次耗时，远小于
     两次耗时之和）。
  3. `release()` 后重新提交同一 execution_id 会重新构建 Agent 实例。
  4. `shutdown()` 后拒绝新提交（`submit()` 返回 `None`）。
  5. Agent 构建失败时正确调用 `on_failed` 而不是 `on_done`。
  6. `persistent=True/False` 只改变 `system_extra` 文案，不影响
     `registry`/`skill_loader`/`tool_cache`/`is_subagent` 等其它字段。
- 回归验证：`test_objective_executor_kanban_tracks*.py`（4 个文件）+
  `test_objective_executor_adaptive_concurrency.py` 全部通过（补装环境缺失
  的 `python-multipart` 依赖后，共 57 个测试全部通过，无回归）。
- `objective_persistent_worker_enabled` 默认 `False`，未开启时代码路径与
  改造前完全一致（`_submit_fn`/`_release_worker_fn` 均保持原状）。

### 阶段二 —— 调度心跳独立化（已完成）

- `src/mini_agent/evolution/scheduler_heartbeat.py`（新增文件）：
  `SchedulerHeartbeat(threading.Thread)`——按 `interval_seconds`（默认 5s）
  轮询 `autonomous_loop.should_tick()`，到期则在持有传入的共享
  `threading.Lock` 情况下调用 `autonomous_loop.tick()`；`tick()`/
  `should_tick()` 内部异常都被捕获记录，不会导致心跳线程整体退出；
  `stop()` 通过 `threading.Event.wait()` 实现可立即中断的等待，不用等满
  一个轮询间隔。
- `src/mini_agent/api/server.py`：
  - `AgentRunner.__init__` 新增 `sched_lock=None`、
    `heartbeat_owns_tick=False` 两个可选参数；新增 `_maybe_sched_lock()`
    辅助方法（`sched_lock` 为 `None` 时返回 `contextlib.nullcontext()`，
    行为与改造前完全一致）。
  - `_main_loop()` 里原有的"dequeue 超时后顺带 tick"逻辑加了
    `not self._heartbeat_owns_tick` 前置条件——心跳线程接管后主循环不再
    自己触发。
  - 两处 `_obj_exec.on_turn_done(...)`/`_obj_exec.on_turn_failed(...)` 调用
    分别用 `with self._maybe_sched_lock():` 包裹，与心跳线程持锁调用
    `tick()` 互斥。
  - `HttpServer.__init__`（多用户 daemon 路径）里：读取
    `cfg.autonomy.scheduler_heartbeat_enabled`，为 `True` 时创建共享锁 +
    构造 `SchedulerHeartbeat` 并 `start()`，同时把锁和
    `heartbeat_owns_tick=True` 传给 `AgentRunner`；为 `False`（默认）时
    `self._sched_lock` 保持 `None`，`AgentRunner` 走原有行为。
  - `stop()` 里新增对 `_scheduler_heartbeat.stop()` 的调用（不 `join()`，
    与既有的 isolated/persistent runner 关停风格一致，非阻塞）。
  - 新增顶层 `import contextlib`。
- 测试：新增 `tests/test_scheduler_heartbeat.py`（5 个用例，全部通过）：
  1. `should_tick()==True` 时按轮询间隔正常触发 `tick()`。
  2. `should_tick()==False` 时不触发。
  3. `tick()` 内部抛异常不影响线程存活、下一轮仍正常触发。
  4. `stop()` 能让线程在远小于轮询间隔的时间内退出（用 `interval=10s` 验证
     `stop()` 不需要等满这 10 秒）。
  5. 主循环模拟长时间持锁时，心跳线程会等锁而不是跳过或卡死，锁释放后立刻
     追上继续 `tick()`——验证的正是本方案要解决的"长 turn 不再无限期推迟
     调度决策"这件事，只是把"阻塞时长"从"一整个长 turn"缩短成"状态更新
     那一小段代码的持锁时间"。
- 回归验证：
  - `python3 -c "import mini_agent.api.server"` 正常导入，无运行时错误。
  - `tests/test_autonomous_loop_decommission_hook.py` +
    `tests/test_objective_persistent_runner.py` +
    `tests/test_scheduler_heartbeat.py` 共 14 个测试全部通过。
  - `pytest tests/ --collect-only` 全量收集 2883 个测试用例，仅有的 2 个
    收集错误是环境缺 `browser_launch` 模块这一预置问题（与浏览器扩展相关
    脚本，非 pip 包），与本次改动完全无关；本次涉及的模块均无导入级别
    错误。
- `scheduler_heartbeat_enabled` 默认 `False`，未开启时 `AgentRunner` 的
  `_sched_lock`/`heartbeat_owns_tick` 均为空/False，`_main_loop()` 与
  `on_turn_done`/`on_turn_failed` 的行为与改造前逐字节一致。

### 看板集成 + 配套文档（已完成）

- `src/mini_agent/api/routes.py`：新增 `GET /v1/self/execution_model_status`
  只读端点，汇总两个开关的生效状态（`objective_execution_mode`/
  `persistent_worker`/`isolated_runner`/`scheduler_heartbeat` 四个字段）。
  顺手发现并修复了一个与本方案无关的既有 bug：`GET /self/config` 路由
  缺失 `@router.get(...)` 装饰器，从未被真正注册过（对比原始交付物确认
  是老问题，不是本轮改动引入的）。
- `apps/mini_agent_kanban/client.py` 新增 `execution_model_status()`。
- `apps/mini_agent_kanban/app.py` 新增 `_render_execution_model_status()`
  面板（"⚙️ 执行模型"），接在"🔗 系统关联性"面板之后，纯只读展示，不提供
  开关切换按钮（开关切换需要改 `agent_config.json` 并重启 daemon）。
- 测试：新增 `tests/test_execution_model_status_routes.py`（6 个用例）。
  连带修复的 `/self/config` bug 让 `test_kanban_config_routes.py` 此前
  失败的 3 个用例也转为通过。本轮相关测试共 22 个全部通过。
- 文档：
  - 新建 `docs/daemon-execution-model-guide.md`（面向使用者的完整指南：
    是什么、怎么开、怎么在看板上观测、怎么回退、两者能否同时开启）。
  - `docs/http-api-guide.md` 新增 `/v1/self/execution_model_status` 端点
    说明（含响应示例）。
  - `docs/kanban-dashboard-guide.md`："🧠 自我状态 Tab"一节补充新面板
    说明，`AgentClient` API 端点表新增一行。
  - `docs/config-guide.md` 的"`AutonomyConfig` 还承载了另外几组彼此独立
    的字段"提示段落，新增第四组指向新文档。

## 6. 后续可以观察的点（不在本轮范围内）

- 目前 `scheduler_heartbeat_poll_interval_seconds`（默认 5s）与
  `tick_interval_seconds`（默认 60s）的比例是拍脑袋定的，建议开启一段时间
  后观察实际的"tick 延迟分布"，据此调整默认轮询间隔。
- `ObjectivePersistentRunner` 目前对"同时存在的 execution 数量"没有独立
  上限（依赖 `ObjectiveExecutor` 既有的并发上限间接约束），如果未来
  `max_concurrent_objectives_cap` 被调大很多，需要重新评估"专属线程数量
  是否会过多"这个问题。
- 两个开关（`objective_persistent_worker_enabled`、
  `scheduler_heartbeat_enabled`）目前互相独立，尚未在同一次真实 daemon
  运行里同时开启验证过组合效应，建议先分别灰度观察，确认各自稳定后再考虑
  同时开启。

## 7. 事后复查发现的问题（待处理）

实施完两个阶段后回头复查交互场景（尤其是"两个开关同时打开"这个第 6 节
里标记为"尚未验证"的组合），发现一处需要立刻正视的实现缺口，以及几个
值得记录的长线方向。

### 7.1 【需要修复】共享锁没有覆盖到持久 Worker 的回调路径（正确性问题）

**问题**：阶段二给 `AgentRunner._main_loop` 里两处
`on_turn_done()`/`on_turn_failed()` 调用加了 `_maybe_sched_lock()`
保护，但 `ObjectivePersistentRunner._run_step()`（阶段一新增）是在自己的
专属线程里**直接**调用 `self._on_done(...)`/`self._on_failed(...)`（也就是
`objective_executor.on_turn_done`/`on_turn_failed`），完全没有经过这把锁
——因为构造 `ObjectivePersistentRunner` 时根本没有把 `sched_lock` 传给它
（`ObjectiveIsolatedRunner` 同样没有）。

**后果**：如果 `objective_persistent_worker_enabled` 和
`scheduler_heartbeat_enabled` **同时打开**：`SchedulerHeartbeat` 线程持锁
调用 `tick()` 的同时，某个 Objective 专属线程可能正在不持锁地调用
`on_turn_done()`，两者同时读写 `ObjectiveExecutor` 内部状态字典
（`self._executions` 等），锁形同虚设，存在真实的数据竞争。第 6 节里写的
"两者组合效应尚未验证"这句话掩盖了问题的性质——这不是"没测过"，而是
**设计上就没接对**：`ObjectivePersistentRunner`/`ObjectiveIsolatedRunner`
压根不知道这把锁的存在。

**修复方向**：
- `ObjectivePersistentRunner.__init__`/`ObjectiveIsolatedRunner.__init__`
  新增可选的 `sched_lock: Optional[threading.Lock] = None` 参数；
- `_run_step()` 里回调 `on_done`/`on_failed`（即现有的
  `_safe_on_done()`/`_safe_on_failed()`）时，用
  `with self._sched_lock:`（为 `None` 时用 `contextlib.nullcontext()`，
  和 `AgentRunner._maybe_sched_lock()` 同一套模式）包一层；
- `server.py` 构造 `ObjectivePersistentRunner`/`ObjectiveIsolatedRunner`
  时，把已经创建好的 `self._sched_lock`（如果心跳模式开启）一并传入。
- 这个改动应该在"两个开关同时开启"被正式支持/建议之前完成——目前
  文档里"两者可以同时开启吗"一节的答案需要改成"暂不建议同时开启，
  存在已知的锁覆盖缺口，见本节"，直到修复完成。

**优先级**：高——这是正确性问题，不是"值得观察的长线方向"，且用户已经
明确要求先记录下来，待下一轮实施。

**处理状态：已修复。**

- `src/mini_agent/evolution/objective_agent_bridge.py`：
  - `ObjectiveIsolatedRunner.__init__`/`ObjectivePersistentRunner.__init__`
    均新增可选参数 `sched_lock: Optional[threading.Lock] = None`，各自新增
    `_maybe_sched_lock()` 辅助方法（与 `AgentRunner._maybe_sched_lock()`
    同一套 `contextlib.nullcontext()` 兜底模式，`sched_lock=None` 时行为与
    改造前完全一致）。
  - 两个类的 `_run_step()` 里所有回调 `on_done`/`on_failed`（含"Agent 构建
    失败""`run_turn()` 抛异常""正常完成"三条路径）都改成
    `with self._maybe_sched_lock(): self._safe_on_xxx(...)` 包裹。
- `src/mini_agent/api/server.py`：
  - `HttpServer.__init__` 里 `self._sched_lock` 的创建时机从"构建
    `AutonomousLoop` 之后"提前到"构建之前"——只要
    `scheduler_heartbeat_enabled=True` 就先创建好锁对象（不再依赖
    `self._autonomous_loop is not None` 这个此前才能满足的条件）。
  - `_build_autonomous_loop()` 新增可选参数 `sched_lock`，把它原样传给
    内部构造的 `ObjectivePersistentRunner`/`ObjectiveIsolatedRunner`。
  - `SchedulerHeartbeat` 的构造挪到 `AutonomousLoop` 构建完毕之后，复用
    同一个已经创建好的 `self._sched_lock`，不再各自持有不同的锁对象。
- 测试：新增 `tests/test_objective_runner_sched_lock.py`（5 个用例）：
  1. 两个 runner 在 `sched_lock=None` 时行为与改造前一致（不报错、不死锁）。
  2. `ObjectivePersistentRunner` 的 `on_done` 回调会等共享锁释放之后才
     完成（用一个"先持锁 sleep 再放锁"的场景验证 happens-before 关系，
     这个用例在修复前会失败——回调会绕开锁提前跑完）。
  3. Agent 构建失败/`run_turn()` 抛异常两条路径的 `on_failed` 回调也经过
     同一把锁。
  4. `ObjectiveIsolatedRunner` 同样验证回调会等共享锁释放。
  - 回归验证：`test_objective_persistent_runner.py`（6 个）+
    `test_scheduler_heartbeat.py`（5 个）+
    `test_autonomous_loop_decommission_hook.py`（3 个）+
    `test_objective_executor_kanban_tracks*.py`（4 个文件）+
    `test_objective_executor_adaptive_concurrency.py` +
    `test_execution_model_status_routes.py` +
    `test_kanban_config_routes.py`，共 82 个测试全部通过，无回归。
- 文档：`docs/daemon-execution-model-guide.md` 第 5 节"两者可以同时开启吗"
  更新为"锁覆盖缺口已修复"的说明；第 2 节补充持久 Worker 连续性是纯内存态
  （对应 §7.2）、daemon 重启即丢失的边界说明。

### 7.2 持久 Worker 的连续性是进程内存态的，daemon 重启即丢失

`ObjectivePersistentRunner` 缓存的 Agent 实例是纯内存对象。如果 daemon 在
某个 Objective 执行到一半时重启，恢复执行时会重新构建一个全新 Agent——
之前积累的"记得自己做过什么"这个优势在重启这一刻就没了，退化回和隔离
runner 完全一样的效果（每步都是失忆的新 agent）。这不是 bug，是这个
方案的固有边界：内存态持久化换来的是"进程存活期内"的连续性，不是
"跨进程重启"的连续性。目前只在设计推理里隐含，没有在
`docs/daemon-execution-model-guide.md` 里明确写出来，应该补一句说明，
避免使用者误以为持久 Worker 能扛住 daemon 重启。

### 7.3 持久 Worker 的会话历史没有上限，理论上可能撑爆 context window

隔离 runner 每个 step 都是一次性 agent，会话历史天然归零；持久 Worker
反过来——一个 step 很多、跑得很久的 Objective，它专属 Agent 的对话历史
会一直累积。目前没有任何"历史长度超过阈值时做一次内部压缩/摘要"的机制。
这是用连续性换来的新代价，此前的方案设计里没有预见到，需要单独评估
（可能的方向：复用主 Agent 已有的 compact/压缩机制，或者给
`ObjectivePersistentRunner` 加一个"历史 token 数超过阈值时重置 Agent、
只保留一段结构化摘要重新开始"的降级路径）。

### 7.4 心跳轮询间隔与 tick_interval 的比例仍是拍脑袋定的

与第 6 节里已经记录的"老化权重系数需要真实运行数据校准"是同一类问题，
`scheduler_heartbeat_poll_interval_seconds`（默认 5s）vs
`tick_interval_seconds`（默认 60s）目前也只能先上线观察一段时间的"tick
延迟分布"，再决定要不要调整默认值。