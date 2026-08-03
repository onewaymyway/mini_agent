# daemon 任务卡死回收 + 调度自愈可观测性 硬化方案

> 状态：**规划阶段，尚未实施**（本文档先完整记录问题分析与分阶段方案，
> 实施记录会在各阶段小节的"处理状态"里逐步更新）。
> 背景来源：完成
> `daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md` §7.5
> （持久 Worker 卡死后重试排队死锁修复）之后，用户要求"聚焦相关功能，
> 思考还有哪些可以改进的地方"，据此对 daemon 里所有"任务可能异常结束/
> 卡死时，daemon 能不能正常继续工作"这个问题做的一次更完整的走查，覆盖
> Objective 执行、cron 执行、调度心跳自身三条链路，不止 §7.5 已经处理的
> Objective 持久 Worker 一条。
> 关联代码：`src/mini_agent/evolution/cron_job_runner.py`、
> `src/mini_agent/evolution/cron_job_executor.py`、
> `src/mini_agent/evolution/cron_scheduler.py`、
> `src/mini_agent/evolution/cron_job_workspace.py`、
> `src/mini_agent/evolution/scheduler_heartbeat.py`、
> `src/mini_agent/evolution/autonomous_loop.py`、
> `src/mini_agent/evolution/objective_executor.py`、
> `src/mini_agent/config/models.py`

## 0. 结论先行：一张表

| # | 问题 | 影响范围 | 严重程度 | 本方案阶段 |
|---|---|---|---|---|
| 1 | cron job 卡死后永久占用 `_running_job_ids`/semaphore 许可，job 再也无法被触发；攒够 `max_concurrent_jobs` 个后全部 cron 功能瘫痪 | 全部 cron job（含 Goal⇄Cron 周期性任务） | 🔴 高 | 阶段一 |
| 2 | `SchedulerHeartbeat` 自身若卡在 `tick()`/`should_tick()` 里不返回，`is_alive()` 仍显示线程存活，但心跳已经假死，`stop()` 无法打断 | 开启了 `scheduler_heartbeat_enabled` 的部署 | 🟡 中 | 阶段二 |
| 3 | `reap_stale_steps()`（§7.5 已修复死锁本身）的回收事件完全没有日志/计数，运维无法感知"卡死回收发生过、发生了多少次" | Objective 持久 Worker + 隔离 runner 路径 | 🟡 中 | 阶段三 |
| 4 | `DEFAULT_STEP_TIMEOUT_SECONDS`（600s）硬编码，不接入 `cfg.autonomy`，无法按项目调整卡死判定阈值 | 同上 | 🟡 中 | 阶段三 |
| 5 | `ObjectiveIsolatedRunner` 共享线程池（默认 4 worker）没有对称的"discard 单个卡死 slot"能力，一次卡死永久吃掉 1/4 共享并发 | 开启了 `objective_isolated_context_enabled` 的部署 | 🟢 低 | 暂不实施（见 §5） |
| 6 | 被 discard 的孤儿线程（§7.5 的持久 Worker、以及本方案阶段一的 cron）完全没有计数，长期运行下看不出"到底攒了多少个僵死线程" | 持久 Worker + cron | 🟢 低 | 阶段三顺带做 |

下面逐条展开。

## 1. 【🔴 高优先级】cron job 卡死后，job 永久无法再被触发，攒够
`max_concurrent_jobs` 个后全局瘫痪

### 1.1 现状代码走查

`CronJobRunner`（`cron_job_runner.py`）已经把 cron job 的实际执行搬到独立
后台线程（`_run_job_thread`），这本身是对的——避免了"cron 任务卡住会堵住
`AgentRunner` 主循环"这个更严重的问题（这是它的设计初衷，见文件顶部注释）。

但它对"job 是否正在跑"和"并发许可"的记账，完全依赖线程**正常返回**：

```python
def submit(self, job: "CronJob") -> bool:
    with self._lock:
        if job.id in self._running_job_ids:
            return False          # 已经在跑，拒绝重复触发
        self._running_job_ids.add(job.id)
    t = threading.Thread(target=self._run_job_thread, args=(job,), daemon=True)
    ...
    t.start()
    return True

def _run_job_thread(self, job: "CronJob") -> None:
    self._sem.acquire()
    try:
        ...
        outcome = executor.run_job(job, submit_step_fn=step_fn, default_config=default_config)
        if self._on_finished is not None:
            ...
    except Exception as _mini_agent_exc:
        ...  # 已有兜底：标记 workspace 为 needs_review
    finally:
        with self._lock:
            self._running_job_ids.discard(job.id)
            self._threads.pop(job.id, None)
        self._sem.release()
```

`CronJobExecutor.run_job()` 自己的文档明确写着"同步阻塞调用"，内部的墙钟
超时检查（`if time.time() >= deadline: break`）只在**两次 step 之间**生效：

```python
deadline = time.time() + cfg.timeout_seconds
while ...:
    if time.time() >= deadline:
        break
    result = submit_step_fn(...)   # 如果这一步本身卡住不返回，上面的
                                    # deadline 检查永远没有机会执行
```

如果 `submit_step_fn()`（本质是一次 `agent.run_turn()`）遇到网络请求挂起、
工具调用阻塞在某个系统调用上等情况，**既不返回也不抛异常**，那么：

1. `_run_job_thread()` 永远停在 `executor.run_job(...)` 这一行，`finally`
   块永远不会执行。
2. `self._running_job_ids` 里的 `job.id` 永久保留 —— `CronScheduler._fire()`
   每次到期都会调用 `self._job_runner.submit(job)`，而 `submit()` 一看
   `job.id in self._running_job_ids` 就直接 `return False`，**这个 job
   之后所有的定时触发都会被静默丢弃**，`CronScheduler.tick()` 的返回值和
   `_fire()` 都没有对这个 `False` 做任何记录/告警（`_fire()` 只是把
   `submit()` 的布尔值原样透传给上一层，没人订阅这个信号）。
3. `self._sem`（`threading.Semaphore(max_concurrent=2)`，默认只有 2 个
   许可）对应的许可永久不释放 —— 攒够 `max_concurrent_jobs` 个卡死 job
   后，**其它所有 cron job 提交后台线程都会永久阻塞在
   `self._sem.acquire()` 上排队**，`is_running()` 显示它们"没在跑"（因为
   还没进 `_running_job_ids`？不对——实际上 `_running_job_ids.add(job.id)`
   是在 `submit()` 里、线程启动**之前**就做的，所以 `is_running()` 会显示
   `True`，但线程其实卡在 acquire 排队而不是真的在执行），从看板上完全
   看不出这是"卡在排队"还是"正常在跑一个耗时任务"，**cron 功能实质性
   全局瘫痪**，且没有任何日志能提示原因。

这与 `daemon_execution_model_and_scheduler_heartbeat_improvement_plan.md`
§7.5 是同一类根因（"卡死线程占着资源槽位不放，且没有外部超时回收
机制"），但严重程度更高：§7.5 的持久 Worker 一次卡死只影响**一个**
Objective；这里一次卡死会消耗**一个全局共享许可**，攒够
`max_concurrent_jobs`（默认只有 2）个就会让**所有** cron job 停摆——
而 cron 目前承载的不只是用户手动配置的定时任务，还包括
`goal_cron_bridge.py`（周期性 Goal 推进）和
`report_tiers.py`（watchlist 分级报告）这些系统内建机制，波及面比表面看
起来大得多。

### 1.2 修复方向

给 `CronJobRunner` 加一个与 `ObjectiveExecutor.reap_stale_steps()` 对称
的存活性回收方法 `reap_stale_jobs()`，由 `AutonomousLoop._tick_maintenance()`
在每次 tick 里调用（与 `reap_stale_steps()` 相邻、同样不受资源仲裁
early-return 门控影响，理由完全一致——回收动作不该依赖"当前是否允许发起
新自主任务"这个跟它无关的门控）。

**关键设计问题：如何避免"外部强制回收"和"线程自己迟到的正常收尾"两者
发生二次释放（把 semaphore 许可数撑大）或互相踩踏？**

用一个每次 `submit()` 生成的唯一 token 来判定"谁是这个 job 当前合法的
执行者"：

- `submit()` 时生成 `token = uuid4().hex`，与 `job.id` 一起记录：
  `self._tokens[job.id] = token`，连同 `started_at`。
- 线程体 `_run_job_thread(job, token)` 的 `finally` 块收尾前先检查
  `self._tokens.get(job.id) == token`：
  - 相等 → 我还是这个 job 当前合法的执行者（没有被外部回收过），正常清理
    `_running_job_ids`/`_threads`/`_started_at`/`_tokens`，**释放一次
    semaphore**（与改造前行为一致）。
  - 不相等 → 说明这个线程已经被 `reap_stale_jobs()` 判定为卡死并强制
    回收过（`_tokens[job.id]` 要么被清空、要么已经是**下一轮**重新提交
    时生成的新 token），我是一个"迟到的孤儿"——**不释放 semaphore**（外部
    回收时刻已经代为释放过一次了，这里不能重复释放），也不touch任何
    共享状态（可能已经属于新一轮执行）。
- `reap_stale_jobs(now)`：扫描 `_running_job_ids`，对每个 `job_id` 计算
  "有效超时阈值" = 该 job 自己 `.agent/cron_jobs/<id>/config.json` 里的
  `timeout_seconds`（读不到则回退 `cfg.cron.default_timeout_seconds`）+
  一个配置化的 grace 余量（`cfg.cron.stale_job_watchdog_grace_seconds`，
  默认 5 分钟——给 `build_cron_agent`/executor 自身开销、以及"卡在两次
  step 之间的内部 deadline 检查"留出正常的调度延迟空间，避免跟真实但
  略微超时的正常执行误判）。超过 `started_at + 有效阈值` 才判定为卡死：
  清空该 `job_id` 的 `_running_job_ids`/`_threads`/`_started_at`/`_tokens`
  记录、**代替永远不会执行到的 `finally` 释放一次 semaphore**、把该 job
  的 workspace 状态标记为 `STATUS_NEEDS_REVIEW`（`cron_job_workspace.py`
  里已经有这个常量，和"未预期异常"的兜底路径用的是同一个状态，语义上
  一致：都是"需要人工看一眼"），并返回被回收的 `job_id` 列表供上层
  日志/计数。

这样清理之后，`job.id` 立刻从 `_running_job_ids` 移除，下一次
`CronScheduler.tick()` 判断到期就可以正常重新 `submit()`，不再永久卡住；
`sem.release()` 恰好执行一次（要么被线程自己的 finally 执行，要么被
watchdog 代为执行，二者互斥），不会出现许可数被撑大的问题。真正卡死的
旧线程本身依然会在后台作为孤儿线程运行（Python 无法强制杀死线程），但
不再阻塞任何后续提交——与 §7.5 持久 Worker 的处理方式完全对称。

### 1.3 需要新增的配置

`config/models.py::CronConfig` 新增一个字段：

```python
# 外部存活性回收（reap_stale_jobs）判定"job 已卡死"的宽限期：
# 有效超时阈值 = job 自己的 timeout_seconds（或全局 default_timeout_seconds）
# + 这个宽限期。之所以要在 job 自己的超时之上再加一段宽限期，是因为
# CronJobExecutor.run_job() 内部的墙钟检查只在两次 step 之间生效，
# 允许最后一步本身多跑一段时间才被外部判定为真正卡死，避免跟"最后一步
# 恰好比较慢但最终会正常返回"的情况混淆。
stale_job_watchdog_grace_seconds: int = 5 * 60
```

### 1.4 验收标准

- 新增测试：`CronJobRunner` 单元测试覆盖：
  1. `reap_stale_jobs()` 判定超时后，`is_running(job_id)` 变回 `False`，
     可以重新 `submit()`。
  2. 被回收之后，原来卡住的"孤儿线程"如果模拟真的返回了，不会再次释放
     semaphore（用一个可以手动控制返回时机的 fake `run_job` 验证）。
  3. 未超时的正常运行中 job 不会被误回收。
  4. 没有配置 job 自己的 `timeout_seconds`（用全局默认）时，阈值计算
     正确回退。
  5. `reap_stale_jobs()` 内部异常不影响其它 job 的回收（每个 job 独立
     try/except）。
- `AutonomousLoop._tick_maintenance()` 新增调用，紧邻
  `objective_executor.reap_stale_steps()`，同样包一层 try/except +
  `log_exception`，不影响本次 tick 其余步骤。
- 回归：现有 cron 相关测试（`test_cron_job_runner.py` 等，如果存在）
  全部通过。

---

## 2. 【🟡 中优先级】`SchedulerHeartbeat` 自身可能假死

### 2.1 现状

```python
def _maybe_tick(self) -> None:
    with self._lock:
        try:
            if self._autonomous_loop.should_tick():
                self._autonomous_loop.tick()
        except Exception as exc:
            ...  # 已捕获异常
```

`_maybe_tick()` 只处理了"抛异常"这一种失败模式。如果 `tick()`（或它
间接调用到的任何一段代码，比如本方案阶段一修复前的 `reap_stale_jobs`
潜在阻塞点、或者未来任何新增的同步 IO）**卡住不返回而不抛异常**，
心跳线程会永久停在 `with self._lock: ...` 里出不来：

- `SchedulerHeartbeat.stop()` 目前的实现只是 `self._stop_evt.set()` +
  等待线程 `join(timeout=...)`——对一个已经阻塞在业务逻辑里、根本没有
  在检查 `stop_evt` 的线程没有任何作用，`join()` 会超时返回，调用方
  目前也没有对"join 超时说明线程没有真的停下来"做任何后续处理/告警。
- `execution_model_status` 目前只报告线程对象的 `is_alive()`——这个值
  在"心跳线程卡死但对象还没被销毁"的情况下仍然是 `True`，**看起来一切
  正常，实际上心跳早就停摆了**，是一个比"线程真的死了"更隐蔽、更难
  排查的故障模式。

### 2.2 修复方向

给 `SchedulerHeartbeat` 加一个 `last_tick_started_at`/`last_tick_finished_at`
时间戳（`with self._lock:` 保护，`_maybe_tick()` 进入/退出时更新），暴露在
`execution_model_status` 接口里。运维/看板可以用
`now - last_tick_finished_at > 2 * tick_interval_seconds`（或类似阈值）
判断"心跳线程虽然 `alive=True`，但已经不再产生新的 tick"，作为心跳假死
的间接信号——这是**观测**层面的加固，不是"能不能真的打断一个已经阻塞的
同步调用"（Python 线程本身做不到这一点，与 §7.5 的结论一致：卡死之后
只能"发现 + 记录"，不能"强制中断"）。

### 2.3 验收标准

- `execution_model_status` 新增 `last_tick_started_at`/
  `last_tick_finished_at`/`last_tick_duration_seconds` 三个字段。
- 新增测试：正常 tick 后三个字段被正确更新；`tick()` 抛异常时
  `last_tick_finished_at` 依然会被更新（放在 `finally` 里，异常场景下
  也要能看出"心跳还在正常轮转，只是这一次业务失败了"，与"心跳彻底停摆"
  区分开）。

---

## 3. 【🟡 中优先级】`reap_stale_steps()` 回收事件无日志/计数 +
`DEFAULT_STEP_TIMEOUT_SECONDS` 硬编码

### 3.1 现状

```python
if self._objective_executor is not None:
    try:
        self._objective_executor.reap_stale_steps()
    except Exception as _mini_agent_exc:
        ...
```

`reap_stale_steps()` 的返回值（被回收的 `execution_id` 列表）在
`autonomous_loop.py` 里被直接丢弃——运维除了事后去翻某个具体 Objective
的 `progress_notes` 之外，完全无法感知"卡死回收"这件事发生过、发生了
多少次、是不是同一个 Objective 反复卡死（这本身可能提示某个工具/某类
任务描述有系统性问题，值得被看到）。

同时 `DEFAULT_STEP_TIMEOUT_SECONDS = 600` 是模块级常量，
`autonomous_loop.py` 调用 `reap_stale_steps()` 时没有传参，永远用这个
硬编码值，无法通过 `cfg.autonomy` 按项目调整（有些任务本来就该跑更久，
比如涉及大型代码库分析的 step；有些则应该更敏感地判定卡死）。

### 3.2 修复方向

- `ObjectiveExecutor` 增加一个进程内计数器
  `self._stale_step_reap_count`（以及可选的最近一次回收时间/
  execution_id），在 `reap_stale_steps()` 内部每回收一个就 `+=1` 并
  `log.warning`（复用 `mini_agent.errors` 里的日志基础设施，不新增
  依赖），计数通过一个只读 property 暴露给
  `execution_model_status`/相关 API。
- `config/models.py::AutonomyConfig` 新增
  `objective_step_stale_timeout_seconds: Optional[int] = None`
  （`None` 时回退模块默认值 `DEFAULT_STEP_TIMEOUT_SECONDS`，保持
  向后兼容），`autonomous_loop.py` 调用处改为
  `reap_stale_steps(timeout_seconds=cfg.autonomy.objective_step_stale_timeout_seconds)`。
- 顺带做（对应 §0 表格 #6）：`ObjectivePersistentRunner.release()` 和
  本方案阶段一的 `CronJobRunner` 强制回收路径，各自维护一个简单的进程内
  计数器（`discarded_worker_count`/`reaped_job_count`），一并暴露在
  `execution_model_status`，让"卡死回收发生的频率"整体可观测——频繁
  发生本身就是需要关注的信号，不管具体是哪条链路。

### 3.3 验收标准

- 新增/扩展测试：`reap_stale_steps()` 回收后计数器正确递增；
  `objective_step_stale_timeout_seconds` 配置生效（用一个很短的自定义
  阈值验证提前触发）；未配置时向后兼容（行为与硬编码 600s 一致）。
- `execution_model_status` 响应体新增字段，配套的既有路由测试
  （`test_execution_model_status_routes.py`）扩展断言覆盖新字段存在。

---

## 4. 优先级与实施顺序

1. **阶段一（cron 卡死回收）**——最高优先级，影响面最大、此前完全没有
   任何保护，且是本次走查中唯一"完全没有事后可诊断线索"的一类故障
   （persistent worker 好歹卡在某一个 Objective 上还能从 progress_notes
   看出来；cron 卡死是全局静默瘫痪，连"哪个 job 是元凶"都要翻线程栈
   才能确认）。
2. **阶段二（心跳自愈可观测性）**——中优先级，属于"给已经存在的机制补
   一双眼睛"，改动量小，风险低，可以和阶段一并行/紧随其后做。
3. **阶段三（`reap_stale_steps` 可观测性 + 超时可配置化）**——中优先级，
   同样是"补眼睛 + 补一个配置口子"，不改变任何既有行为默认值，风险
   最低，适合放在最后收尾。

## 5. 明确不做的事

- 不处理 `ObjectiveIsolatedRunner` 共享线程池的"单 worker discard"
  （§0 表格 #5）——它的默认 `max_workers=4`，一次卡死只影响 1/4 共享
  容量，风险敞口远小于 cron 的"2 个许可、一次卡死损失 50%"；且它本身
  默认关闭（`objective_isolated_context_enabled=False`），真实使用面
  也小于持久 Worker 和 cron。如果未来需要处理，应该是"隔离 runner 也
  接一个可选的、面向整个共享池的健康检查（比如统计有多少个 worker
  连续超过 N 倍 `DEFAULT_STEP_TIMEOUT_SECONDS` 没有产出，超过阈值就
  整体重建线程池）"，改动面比"按 execution 精细回收"大得多，值得单独
  立项评估，不在本方案范围内。
- 不改变任何现有超时/重试相关参数的默认值（`DEFAULT_STEP_TIMEOUT_SECONDS`、
  `MAX_STEP_RETRIES`、`cron.default_timeout_seconds`、
  `cron.max_concurrent_jobs` 等）——本方案只做"检测 + 回收 + 可观测"，
  不重新调整这些数值本身，避免在同一次改动里引入两类不相关的变量。
- 不尝试用任何机制真正"强制终止"一个已经卡死的 Python 线程——这在
  CPython 里没有安全的官方 API（`ctypes` 注入异常等 hack 有破坏解释器
  状态的风险），与 §7.5 的结论一致：只能做到"发现 + 不再引用它 + 让后续
  流程绕开它"，孤儿线程本身会作为已知的、有限的资源占用一直运行到进程
  退出或（极小概率下）自己真的返回。
