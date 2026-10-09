# tick 只派发、不执行：调度心跳去阻塞改造方案

> 状态：**方案已确认，待实施**（阶段 0～4 均未开始）。
> 背景来源：`SchedulerHeartbeat` 卡死栈快照（`.agent/` 下 `stuck_20261009_100807_1.txt`、
> `stuck_20261009_104039_1.txt`、`stuck_20261009_104328_1.txt`）分析。用户确认的方向：
> **tick 只负责判断与派发，所有可能阻塞的执行都在独立线程里完成，不应卡住 tick。**
> 关联文档：
> `daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md`（心跳独立化、
> "tick 内部只做决策+提交"的原始约束）、
> `daemon_task_hang_recovery_and_watchdog_hardening_plan.md`（看门狗、cron/Worker 卡死回收）、
> `daemon_dual_signal_hang_detection_plan.md`（状态文件旁路）。
> 关联代码：`src/mini_agent/evolution/scheduler_heartbeat.py`、`autonomous_loop.py`、
> `objective_executor.py`、`cron_scheduler.py`、`cron_job_runner.py`、
> `unified_task_scheduler.py`、`goal_cron_bridge.py`、`src/mini_agent/llm/service.py`、
> `llm/retry.py`、`llm/providers/_base_mixin.py`、`orchestrator/concurrency.py`、
> `api/server.py`、`external_input/goal_relevance.py` 等。

## 0. 结论先行

三份快照里 heartbeat 线程的入口各不相同，末端却完全一致：**持有 `sched_lock` 的 tick 线程，
同步等待一个 LLM 请求，阻塞在 `sock.recv`**。这不是网络偶发问题，而是对心跳模块文档里
"tick() 只做决策+提交，不做耗时调用"这条约束的系统性违反——约束只写在注释里，没有任何机制保证，
调用点又散落在多条链路上，所以会反复出现。

本方案不逐点打补丁，而是把约束变成**执行模型**：

1. tick 里只允许做"读状态 → 判断 → 派发（立即返回）"。
2. 新增统一的后台派发器 `TickDispatcher`，承接所有原本在 tick 线程里同步执行的慢操作。
3. 凡是"提交前要做慢操作"的流程（典型是 step 提交前的 LLM 路径声明），改成**状态机**：
   tick 只推进状态，慢操作在 worker 里完成后回写状态，下一轮 tick 读取结果。
4. 用机制强制：tick 线程里发起 LLM 调用会被检测并告警，测试里可设为严格模式直接失败。
5. 兜底：后台轻量 LLM 调用也要有总 deadline，不能无限占用线程与并发槽位。

## 1. 背景与现象

### 1.1 三份快照

| 快照 | 抓取时间 | tick 内路径 | 同步 LLM 调用点 |
|---|---|---|---|
| #1 | 10:08:07 | `_tick_maintenance → reap_stale_steps → _attempt_redecompose → _submit_step → _declare_step_paths` | `_default_declare_paths → llm_helper.ask` |
| #2 | 10:40:39 | `_tick_maintenance → _trigger_objective_candidate → ObjectiveExecutor.start → _submit_step → _declare_step_paths` | 同上 |
| #3 | 10:43:28 | `_tick_maintenance → _tick_passive → dispatch_due_cron_jobs → CronScheduler._fire → local_handler` | `goal_relevance` 的 `llm_helper.ask` |

三次抓取时"其中等锁 0s"：不是在等别人的锁，而是**自己持有 `sched_lock` 的同时在等网络**。
快照是在 tick 超过 `tick_interval × stuck_threshold_multiplier`（60s×2）时抓的，只代表发现时刻，
实际阻塞时长更长。

### 1.2 并发槽位被占满（快照 #3，推断）

快照 #3 里有 8 个线程同时停在 `openai.py::_do_chat`（含 heartbeat 自己），正好等于
`init_concurrency(max_llm_calls=8)`；另有一个 obj-worker 排在 `CountingSemaphore._wait_and_acquire`，
`cron-job-sys:workdir_sync` 排在 `CronJobRunner._acquire_slot`。栈里请求都经过 `http_proxy`。
说明上游同时在变慢或挂起。**这一点是推断，栈本身不能证明**，所以本方案把"槽位预留"留到
派发器上线后再看实测数据（见 §8）。

## 2. 根因

1. **同步 LLM 调用被放在 tick 线程里**：`ObjectiveExecutor._submit_step` 在提交前同步调
   `_declare_step_paths`（Track C 路径互斥）；`start()`、`retry_blocked_steps()`、
   `_attempt_redecompose()`、`on_turn_done()` 都会走到它。
2. **cron `local_handler` 同步执行**：`CronScheduler._fire()` 对 `job_runner` 路径是"提交即返回"，
   但 `local_handler` 分支直接调用。仓库里注册了 20 多个 local handler，其中
   `goal_relevance`、`novelty_judge`、`knowledge_extractor`、`tech_radar_search` 等会调 LLM。
   `CronJobRunner` 文件头明确写了"耗时 cron job 不能占用调度线程"，local handler 绕过了这条设计。
3. **单次 `LLMHelper.ask()` 最坏阻塞没有上限**：按默认值，单次请求超时 120s（`LLMConfig.timeout`）、
   `ask()` 默认 `max_retries=3`、`FixedBackoff(10s)`，最坏约 `4×120+3×10≈510s`，
   `ClientPool` 有 fallback 时还要再乘。而 `declare_paths` 本质只是个辅助判断。
4. **持锁范围过大**：`_maybe_tick` 在 `with self._lock:` 内整段调用 `tick()`。tick 一旦卡住，
   同一把 `sched_lock` 上的 `on_turn_done()` 等状态更新也被拖住。
5. **约束没有机制保证**：只靠注释与看门狗事后发现，新代码很容易再次违反。

## 3. 目标与非目标

### 3.1 目标

- G1：任何情况下（上游 LLM 完全挂死），tick 都能在**秒级**返回，`last_tick_duration_seconds`
  不再随上游延迟增长。
- G2：慢操作统一由有界、可去重、可超时回收的后台派发器执行，结果回写状态，不丢失既有记账语义。
- G3：step 提交流程改为状态机，路径互斥（Track C）语义不变。
- G4：tick 线程里出现 LLM 调用时可检测、可告警、可在测试中强制失败。
- G5：后台轻量 LLM 调用有总 deadline，最坏占用时长可预期。
- G6：向后兼容，新行为由配置开关控制，可一键回退。

### 3.2 非目标

- 不改 `AgentRunner` 主循环结构，不引入 asyncio 重写调度。
- 不改 LLM provider 的流式/非流式实现。
- 不做 LLM 并发槽位预留（见 §8，等派发器上线后依实测再定）。
- 不改变 Objective/cron 的业务语义（何时触发、触发什么）。

## 4. 设计理念

1. **tick = 判断 + 派发**。判断依赖的是本地状态（文件/内存），派发是"把任务交给后台并立即返回"。
   tick 里不等待任何外部结果。
2. **异步靠状态机，不靠返回值**。后台任务的结果通过状态字段回写（step.status、job 记账字段、
   落盘文件），tick 下一轮读状态做决策。
3. **派发要有边界**：线程数有上限；同一个 key 同时只允许一个在跑（去重）；每个任务有存活期限，
   超时后由回收逻辑释放记账（沿用 `CronJobRunner.reap_stale_jobs` 的 token 机制，迟到线程不会重复释放）。
4. **失败要降级而不是阻塞**：后台任务超时/异常时走既有的降级路径（如路径声明失败退化为哨兵路径），
   不让调度因为一个辅助调用失败而停摆。
5. **约束机制化**：把"tick 不许跑慢调用"变成可检测的运行时事实，而不是文档约定。
6. **兼容优先**：遵循项目约定"配置开关默认保持非破坏行为"，新机制逐阶段落地、各自可开关。

## 5. tick 阻塞点审计（阶段 0 已完成，2026-10-09）

"证据"列分三种可信度：`栈已证实`（快照里直接出现）、`代码确认`（代码里确实同步调 LLM，
但快照没抓到）、`排除`（审计后确认无 LLM/长阻塞，不需要卸载）。

| # | tick 内位置 | 阻塞内容 | 证据 | 处理阶段 |
|---|---|---|---|---|
| 1 | `_submit_step → _declare_step_paths → _default_declare_paths` | LLM 猜路径 | 栈已证实（#1、#2） | 阶段 2 |
| 2 | `start() → _decompose`（`_llm_decompose_fn`） | LLM 拆解 Objective | 代码确认 | 阶段 2 |
| 3 | `reap_stale_steps → _attempt_redecompose → _llm_redecompose_fn` | LLM 重拆解 | 代码确认 | 阶段 2 |
| 4 | `retry_blocked_steps → _submit_step` | 同 #1 | 代码确认 | 阶段 2 |
| 5 | `_tick_passive → dispatch_due_cron_jobs/CronScheduler.tick → _fire → local_handler` | 20+ handler，含多个 LLM 调用 | 栈已证实（#3） | **阶段 1** |
| 6 | `_fire` 的 `_goal_cycle_fn` 分支 → `goal_cron_bridge._fire_goal_cycle` | 内部 `objective_executor.start()`（同 #1/#2）；`compute_progress_trend_signal`/`compute_routine_stability_signal` 在 `progress_trend_llm_enabled=True` 时调 LLM | 代码确认（LLM 部分条件触发） | 阶段 2（`start()` 非阻塞后自然收敛）+ 阶段 4（deadline）；**阶段 1 不动**：goal_cycle 的返回值语义是"确保该 Goal 下有一轮 Objective 在推进"，异步化会改变 cron 记账含义 |
| 7 | `_ensure_goal_objectives → _goal_decompose_fn`（`api/server.py:2024` 注入） | LLM 拆解 Goal（注释写"锁外调用"，但仍在 tick 线程） | 代码确认 | 阶段 2 |
| 8 | `reap_finished_cycles → _check_pursuit_saturation → process_pursuit_cycle_completion(llm_helper)` | LLM 复核（取决于 pursuit 配置） | 代码确认（条件触发） | 阶段 2 |
| 9 | `_tick_autonomous → SoftGoalDeriver.*` | `soft_goal_deriver.py` 内无 LLM 调用，规则 + 文件 I/O | **排除** | — |
| 10 | `_tick_passive` 内联的 `run_ingestion_policy_once / run_watchlist_matcher_once / run_novelty_candidate_once` | 规则式 + 文件 I/O，无 LLM | **排除**（不卸载；风险只是文件 I/O 量级，由阶段 4 的 tick 分段耗时观测兜底） | 阶段 4 观测 |
| 11 | `check_persistent_attention_mismatch` | 函数体内无 LLM | **排除** | — |
| 12 | `cron_scheduler is None` 降级路径（`run_consolidation` / `_run_workdir_consolidation`） | 同步整合 | 代码确认存在，但 daemon 生产路径恒注入 `cron_scheduler`，仅非 daemon/测试场景走到 | 不做（记录在案） |
| 13 | `on_turn_done()` / `on_turn_failed()` 持 `sched_lock` 的调用点 | `api/server.py` AgentRunner 两处（~619、~660）和 `objective_agent_bridge.py` 的 obj-worker 线程（~428/436/452）持 `sched_lock` 调 `on_turn_done → _submit_step → _declare_step_paths` | **代码确认**（调用链走查） | 阶段 2 |

补充发现：

- #13 比快照显示的更严重。持 `sched_lock` 调 `on_turn_done` 的线程除了 obj-worker，还有
  **AgentRunner 主循环线程**——它同时负责 dequeue 用户消息，因此这条路径一旦卡住，
  不仅 heartbeat 等锁（"waiting_lock"阶段），用户输入也会被一并堵住。
- `ExecutionStep.status` 的外部读取点很少：`api/routes.py:7844/7886` 原样透传字符串、
  `api/server.py:1769` 读 `current_step.description`，其余都在 `objective_executor.py` 内部。
  新增 `preparing/decomposing` 状态影响面小，主要是看板展示文案。
- 已有先例：`_tick_autonomous` 里的能力探索已经改成 `_start_capability_exploration_bg`（异步），
  注释里标注为"阶段二 违规修复"——说明项目内部早已认同这条原则，本方案是把它推广为统一机制。

## 6. 方案

### 6.1 `TickDispatcher`（新增 `evolution/tick_dispatcher.py`）

```python
class TickDispatcher:
    def dispatch(self, key: str, fn: Callable[[], Any], *, timeout: float,
                 on_done: Optional[Callable[[DispatchResult], None]] = None,
                 label: str = "") -> bool: ...
    def is_running(self, key: str) -> bool: ...
    def reap_stale(self, now: float | None = None) -> list[str]: ...
    def stats(self) -> dict: ...
```

语义：

- `dispatch()` **立即返回**：key 已在跑或池已满返回 `False`（语义同 `CronJobRunner.submit` 返回 `False`）。
- 有界线程池（`max_workers`），不用无界线程。
- 每次派发生成唯一 token；`reap_stale()` 对超过 `timeout + grace` 的任务回收记账，
  迟到线程收尾时发现 token 已失效则跳过释放，不与回收互相踩踏（与 `CronJobRunner` 一致）。
- `on_done(result)` 在 worker 线程里回调，用于把结果写回状态（`ok/exception/timeout`）。
- `stats()` 暴露在跑数、排队数、累计超时回收数、孤儿线程数，接入 `execution_model_status`。
- `reap_stale()` 由 `_tick_maintenance` 调用（只读状态+释放记账，不阻塞）。

与 `CronJobRunner` 的关系：不合并。`CronJobRunner` 处理的是"一个完整 cron job 的 agent 运行"，
有 workspace/资源仲裁等重逻辑；`TickDispatcher` 是轻量的"tick 慢操作卸载"通用设施。
借鉴其 token/回收设计，不共用代码，避免相互牵连。

### 6.2 cron `local_handler` 异步化（阶段 1）

`CronScheduler._fire()` 中 local_handler 分支改为：

```python
ok = dispatcher.dispatch(key=f"cron:{job.id}", fn=lambda: local_handler(job),
                         timeout=..., on_done=self._on_local_handler_done(job))
```

- 派发成功即返回 `True`，`_trigger_and_record` 照常更新 `last_run_at/run_count/next_run_at`——
  **与 `job_runner.submit()` 路径语义一致**（提交成功即视为触发成功）。
- key 已在跑时 `dispatch` 返回 `False`，记 skip reason `local_handler_already_running`
  （新增到 `cron_skip_reasons.py`，归入 `retry` 类，沿用现有告警与退避）。
- `local_handler` 的返回值 `False`、异常，改在 `on_done` 里写入 `job.last_skip_reason`/`last_skip_detail`
  并落盘，kanban 里仍可见失败原因；**语义变化**：失败不再回滚本次的 `last_run_at`，
  与 `job_runner` 路径一致。
- 开关：`scheduler.async_local_handlers_enabled`（遵循约定默认 `False`，上线验收后翻转默认，见 §10）。
- `goal_cycle` 分支（#6）：阶段 0 审计结论是**阶段一不动**——它的返回值语义是"确保该 Goal 下有一轮
  Objective 在推进"，异步化会改变 cron 记账含义；其内部的 `objective_executor.start()` 在阶段 2
  非阻塞化后自然收敛，LLM 进展判断部分由阶段 4 的 deadline 兜底。
- 无 `cron_scheduler` 的降级路径（#12）：审计确认仅非 daemon/测试场景走到，不做。
- 实现要点：handler 拿 `CronJob` 浅拷贝；后台线程只把结果放进线程安全 deque，由 tick 线程在
  `drain_async_handler_results()` 里消费（先 `reap_stale()` 再补记），避免 worker 线程改 tick 线程独占的
  job 状态；连续失败用独立 `_async_fail_streak` 累计，因为派发成功会把 `consecutive_skip_count` 清零。

### 6.3 step 状态机（阶段 2）

`ExecutionStep.status` 新增 `preparing`（现为 `pending|running|done|failed|blocked`）。

`_submit_step(ex, idx)` 改造为非阻塞的两段式：

1. **tick/持锁线程侧**（快）：若 `step.paths` 已缓存，直接走原有"冲突检测 → 提交"；
   否则置 `step.status = "preparing"`，`dispatch(key=f"prepare:{ex.execution_id}:{idx}", ...)` 后返回 `False`
   （调用方已能区分"暂时排队"与失败，沿用 `blocked` 的既有处理分支，扩展为 `blocked/preparing` 都不判失败）。
2. **worker 侧**（慢）：调 `_declare_step_paths`（LLM，带 §6.6 的 deadline），回写 `step.paths`，
   在**独立的小锁**下做冲突检测并占位 `_active_step_paths`（原先由 tick 线程隐式串行保证的原子性，
   改为显式小锁保证）；无冲突 → 真正提交；冲突 → `blocked`，交给 `retry_blocked_steps` 重试。

关键点：

- `_active_step_paths` 的读写、冲突检测与占位必须在同一把小锁内完成（check-then-act 原子）。
- 声明失败/超时 → 退化为哨兵路径（既有逻辑），不阻塞。
- **持久化与恢复**：`status` 会随执行状态落盘（`d.get("status", "pending")`），daemon 重启后
  `preparing` 必须回退为 `pending`（恢复流程里统一处理），避免永久悬挂。
- `reap_stale_steps` 需识别 `preparing` 超时：dispatcher 超时回收后回退为 `pending` 并按哨兵路径提交，
  不计入 `retry_count`（它不是 step 执行失败）。
- 同类改造：`_decompose`（#2）、`_attempt_redecompose`（#3，先 `redecompose` 再提交，状态新增
  `decomposing`）、`_ensure_goal_objectives`（#7）、`reap_finished_cycles`（#8）、
  `SoftGoalDeriver`（#9，视审计结论）——统一模式：**tick 置状态+派发，worker 做 LLM，
  结果落盘/回写，下一轮 tick 消费**。
- `on_turn_done()` 持锁路径（#13）：先核对 `api/server.py` 中持锁调用链，保证持锁线程里不再同步调 LLM，
  必要时 `on_turn_done` 只更新状态并派发"准备下一步"。
- 开关：`autonomy.async_step_prepare_enabled`（默认 `False`）。

### 6.4 tick 线程告警与严格模式（阶段 4）

- `SchedulerHeartbeat._maybe_tick` 进入 `tick()` 前设置 thread-local 标记，退出时清除
  （新增 `evolution/tick_thread_guard.py`，提供 `in_tick_thread()`）。
- `ProviderMixin._traced_chat`（`_base_mixin.py`）在发起请求前检测：若 `in_tick_thread()`，
  记录带调用栈的 warning，累加计数，写入 `scheduler_heartbeat_status.json` 新字段
  `tick_thread_llm_calls`（供看板/supervisor 读取，无需新增采集通道）。
- 严格模式 `scheduler.tick_thread_llm_strict`（默认 `False`）下直接抛 `TickThreadBlockingError`；
  测试套件默认开启，防止新代码回退。
- 默认只告警不抛：直接抛异常会让部分降级路径（如 redecompose 失败判 Objective failed）产生副作用，
  需要先让阶段 1、2 清理完存量调用点再收紧。

### 6.5 持锁范围收缩

`_maybe_tick` 里 `with self._lock:` 包裹整个 `tick()` 的做法保留（兼容既有加锁约定），
但在阶段 1～2 完成后 tick 本身毫秒级返回，持锁时间自然缩短。是否进一步拆分锁粒度，
留到阶段 4 视实测再定，**本方案不主动改锁结构**。

### 6.6 后台轻量调用的 deadline 兜底（阶段 4）

- `LLMHelper.ask/chat` 新增 `timeout`（覆盖单次请求超时）和 `deadline`（总墙钟上限）参数。
- `RetryPolicy.call_with_retry` 在每次重试/退避前检查 deadline，超出则不再重试直接抛
  `LLMTimeoutError`；单次请求通过对 SDK 调用传 `timeout` 保证不会超过剩余时间。
- 为轻量后台调用设默认值（配置项 `llm.background_call_timeout_seconds` 默认 30、
  `llm.background_call_max_retries` 默认 1），`declare_paths`、`goal_relevance`、`novelty_judge` 等
  后台判定调用使用；Agent 主对话与 step 执行不受影响。
- 注意：Python 无法强杀已阻塞的线程，deadline 靠 SDK 层超时 + 重试层预算双重保证。

## 7. 实施计划

### 阶段 0：只读审计（不改代码）

- 目标：把 §5 里"待审计"全部落实为"确认/排除"，补齐 #6、#9、#10、#11、#13，输出审计附录并更新本文档。
- 内容：逐行走查 `_tick_passive/_tick_maintenance/_tick_autonomous` 调用链；对每个步骤标注
  是否可能阻塞（LLM / subprocess / 网络 / 大文件 I/O / 锁等待）；核对 `api/server.py` 里
  `sched_lock` 的两个持有点；确认 `ExecutionStep.status` 在恢复/看板/路由中所有使用位置
  （为新增 `preparing/decomposing` 评估影响面）。
- 验收：审计表无"待审计"项；列出需要新增的状态值及受影响的读取点。

### 阶段 1：`TickDispatcher` + cron `local_handler` 异步化

- 改动：新增 `tick_dispatcher.py`；`cron_scheduler.py::_fire`、`cron_skip_reasons.py`；
  配置 `scheduler.async_local_handlers_enabled` 等；`execution_model_status` 增加 dispatcher 统计。
- 测试：派发立即返回；key 去重；超时回收后迟到线程不重复释放；`on_done` 回写 skip reason；
  假 LLM 永久挂起时 `tick()` 在秒级返回；开关关闭时与现状行为一致。
- 验收：用"永不返回"的假 handler 注册 cron job，heartbeat 单次 tick 耗时 < 1s，
  且 dispatcher 在 `timeout+grace` 后回收。

### 阶段 2：step 状态机及其他同类调用点

- 改动：`objective_executor.py`（`_submit_step`、`start`、`_attempt_redecompose`、
  `retry_blocked_steps`、`reap_stale_steps`、恢复流程）、`autonomous_loop.py`（`_ensure_goal_objectives`）、
  `goal_cron_bridge.py::reap_finished_cycles`、按审计结论处理 `SoftGoalDeriver`；
  `api/server.py` 持锁路径；看板/路由里对 step 状态的展示补 `preparing/decomposing`。
- 测试：路径冲突语义不变（原有 Track C 测试全部通过）；check-then-act 并发测试；
  重启恢复 `preparing → pending`；声明超时退化为哨兵路径；redecompose 异步后的失败判定语义不变。
- 验收：构造"LLM 永远挂起"，Objective 能进入 `preparing`，tick 不受影响，超时后降级继续推进。

### 阶段 3（并入阶段 2 的收尾）

- 审计中新增的零散调用点（如 #10、#11 若被判定有风险）统一按同一模式卸载。

### 阶段 4：tick 线程告警 + deadline 兜底

- 改动：`tick_thread_guard.py`、`_base_mixin.py`、`scheduler_heartbeat.py`（status 文件新增字段）、
  `llm/service.py`、`llm/retry.py`、`config/models.py`。
- 测试：tick 线程内 LLM 调用被计数/告警；严格模式抛异常；`ask()` 带 deadline 时总耗时受限；
  不带 deadline 的既有调用行为不变。
- 验收：整个测试套件在严格模式下通过（证明存量 tick 内 LLM 调用已清零）；
  `tick_thread_llm_calls` 在生产运行一段时间后仍为 0。

## 8. 明确不做 / 延后

- **LLM 并发槽位预留**（从 `max_llm_calls=8` 里留 1～2 个给后台轻量调用）：会改变全局并发语义，
  先看派发器上线后的槽位占用实测，再决定是否立项。
- 不把调度改成 asyncio；不拆分 `sched_lock` 锁粒度（§6.5）。
- 不改 `CronJobRunner` 的现有行为。

## 9. 风险与取舍

| 风险 | 缓解 |
|---|---|
| step 状态机引入新状态，遗漏某处 `status` 判断导致悬挂 | 阶段 0 全量列举读取点；恢复流程统一回退；测试覆盖 |
| 路径冲突检测原子性（原先靠 tick 串行隐式保证） | 显式小锁，check-then-act 在同一临界区；并发测试 |
| cron local_handler 失败不再回滚 `last_run_at` | 与 `job_runner` 路径语义一致；失败原因仍写 skip reason 并告警 |
| dispatcher 线程池被慢任务占满 | 有界池 + 超时回收 + 孤儿线程计数；重要任务与轻量任务可分池（阶段 1 视实测） |
| 严格模式误伤降级路径 | 默认只告警；存量清零后才在测试中开严格 |
| 孤儿线程累积 | `stats()` 暴露计数，沿用已有观测思路 |

## 10. 配置项与回滚

| 配置项 | 默认 | 说明 |
|---|---|---|
| `scheduler.async_local_handlers_enabled` | `False` | cron local_handler 走 dispatcher（§6.2） |
| `autonomy.async_step_prepare_enabled` | `False` | step 准备阶段异步（§6.3） |
| `scheduler.tick_dispatcher_max_workers` | `4` | 派发器线程数 |
| `scheduler.tick_dispatcher_timeout_seconds` | `300` | 默认任务存活期限 |
| `scheduler.tick_dispatcher_grace_seconds` | `30` | 超过期限后再宽限多久才回收 |
| `scheduler.tick_thread_llm_strict` | `False` | tick 线程内 LLM 调用直接抛异常 |
| `llm.background_call_timeout_seconds` | `30` | 后台轻量调用单次超时 |
| `llm.background_call_max_retries` | `1` | 后台轻量调用重试次数 |

遵循项目约定"配置开关默认保持非破坏行为"，新开关默认保持旧行为；在 `agent_config.json` 示例里开启，
**各阶段验收通过后再决定是否翻转默认值**（需用户确认）。回滚方式：关闭对应开关即可回到现状，
不涉及数据迁移（`preparing` 状态在恢复时统一回退为 `pending`，旧版本读到也按 `pending` 处理）。

## 11. 总体验收

1. 构造"所有 LLM 请求永远挂起"的环境：heartbeat 的 `last_tick_duration_seconds` 保持在秒级，
   看门狗不再触发 `suspected_stuck`。
2. 路径互斥、cron 记账、Objective 推进的既有测试全部通过。
3. 严格模式测试套件通过；生产状态文件 `tick_thread_llm_calls` 为 0。
4. 相关文档同步更新：`daemon_task_hang_recovery_and_watchdog_hardening_plan.md` 交叉引用、
   `docs/` 下 daemon/cron 指南补充"tick 只派发"的执行模型说明。

## 12. 处理状态

- 阶段 0（只读审计）：**已完成**（2026-10-09，结果见 §5）
- 阶段 1（TickDispatcher + cron local_handler）：**已完成**（2026-10-09）
  - 新增 `evolution/tick_dispatcher.py`；`cron_scheduler.py`（`set_tick_dispatcher` /
    `_fire_local_handler_async` / `drain_async_handler_results`，`is_job_running` 感知派发器）；
    `cron_skip_reasons.py`（3 个新原因码）；`config/models.py::SchedulerConfig` 新增 4 个字段；
    `api/server.py` 装配；`autonomous_loop._tick_passive` 先 drain 再派发；
    `GET /v1/self/execution_model_status` 新增 `tick_dispatcher`。
  - 测试：`tests/test_tick_dispatcher.py`（8）+ `tests/test_cron_scheduler_async_local_handler.py`（11，
    含"handler 永不返回时 heartbeat 单次 tick < 1s"验收）；cron/heartbeat/unified scheduler 相关回归
    137 个全部通过。
  - `agent_config.json` 的 `scheduler` 块已设 `async_local_handlers_enabled: true`；代码默认值仍为 `false`。
  - 文档：`docs/daemon-execution-model-guide.md` 新增 §6，`cron-dedicated-execution-guide.md` /
    `unified-scheduler-guide.md` 补充说明。
- 阶段 2（step 状态机 + 同类调用点）：**未开始**
- 阶段 4（tick 线程告警 + deadline 兜底）：**未开始**
