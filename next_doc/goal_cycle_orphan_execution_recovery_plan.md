# 周期性 Goal 因孤儿执行记录永久停摆 —— 自愈与告警改进方案

> 状态：**2.1（核心：孤儿执行记录自愈）已实施完成；2.2（连续跳过告警
> 改为重复提醒）待实施**。
> 触发背景：用户反馈一个已经正常跑了 172 轮的 recurring Goal，看板显示
> "下次触发：now / overdue"，但实际已经连续很多天没有真正触发过，也没有
> 任何持续的告警提醒用户。
>
> **实施记录（2.1）**：`ObjectiveExecutor` 新增
> `reconcile_orphaned_executions()`，在 `load()` 之后由
> `api/server.py` 紧接着调用一次；把冷启动时仍处于
> `running`/`paused_for_fairness` 的记录标记为 `failed`（复用
> `_sync_goal_status()` 同步对应 Objective 终态），不依赖可选的
> `is_active_fn`，默认共享队列部署形态同样生效。新增测试
> `tests/test_objective_executor_orphan_reconcile.py`（6 用例全过），
> 与既有 `test_objective_executor_*`/`test_goal_mode.py` 回归测试
> 一并跑过无回归（`test_goal_mode.py` 中 6 个跟本方案无关的用例因本地
> 环境未装 `anthropic` SDK 失败，属既有环境问题，非本次改动引入）。
> 已更新 `docs/goal-cron-binding-guide.md` §3。

## 0. 结论先行

排查定位到两个会导致"周期性 Goal 永久停摆、且用户长期无感知"的独立
问题，互不依赖，建议一起修：

| # | 问题 | 现象 | 根因 | 严重程度 |
|---|---|---|---|---|
| 1 | daemon 崩溃/重启后遗留的\"孤儿执行记录\"永久阻塞该 Goal 的后续触发 | 该 Goal 一直显示 overdue，`cycle_count` 不再增长 | `ObjectiveExecutor.load()` 把磁盘上 `status="running"` 的记录原样恢复，没有做\"这条记录是否还有真实进程在支撑\"的核实；`_goal_has_active_cycle()` 据此永远判定\"上一轮还没跑完\" | 🔴 高 |
| 2 | 连续跳过告警只在跨过阈值那一刻发一次，之后彻底沉默 | 用户完全不知道 Goal 已经停摆很久 | `cron_scheduler.py::_maybe_alert_consecutive_skip()` 用 `consecutive_skip_count != threshold` 做"恰好命中才发"的判断，不会重复提醒 | 🟡 中 |

两者叠加，就是"停了很多天、系统自己不会恢复、用户也没被持续提醒"这个
现象的完整成因。

## 1. 现状代码走查

### 1.1 触发链路里"上一轮是否还在跑"的判断

`goal_cron_bridge.py`：

```python
def _fire_goal_cycle(job, goal_backlog, objective_executor, ...):
    ...
    if _goal_has_active_cycle(goal, goal_backlog, objective_executor):
        # 上一轮还没跑完，本轮跳过，不叠加并发。
        return False
    ...

def _goal_has_active_cycle(goal, goal_backlog, objective_executor):
    for child_id in goal.children_ids:
        child = goal_backlog.get(child_id)
        if child is None or not child.is_objective:
            continue
        if child.status == "active" and objective_executor.is_running(child.id):
            return True
    return False
```

`ObjectiveExecutor.is_running()`：

```python
def is_running(self, objective_id: str) -> bool:
    return any(
        ex.objective_id == objective_id and ex.status == "running"
        for ex in self._executions.values()
    )
```

`_executions` 来自 `load()`，daemon 启动时从 `objective_executions.json`
原样恢复，**不做任何"这条 running 记录背后是否还有真实进程/线程在支撑"
的核实**：

```python
def load(self) -> None:
    ...
    for ed in data.get("executions", []):
        ex = ObjectiveExecution.from_dict(ed)
        if ex.execution_id:
            self._executions[ex.execution_id] = ex   # status 原样恢复
```

`save()` 只排除 `completed/failed/cancelled`，`running`/
`paused_for_fairness` 等非终态记录会被持久化下来，重启后原样加载回来。

**结果**：daemon 在某次 tidy/running 轮执行过程中异常退出（崩溃、被
systemd/supervisor 杀掉、手动重启升级……），磁盘上留下一条
`status="running"` 的执行记录。daemon 重新拉起后，这条记录被原样加载
回内存，但支撑它的那次真正的模型调用/线程早已随旧进程一起消失——从此
`is_running()` 对它永远返回 `True`，`_goal_has_active_cycle()` 永远返回
`True`，`_fire_goal_cycle()` 永远返回 `False`。

`cron_scheduler.py::tick()` 的既有约定：

```python
# handler 返回 False：视为"这次没真正触发成功"，
# 不推进 last_run_at/next_run_at，下次 tick 会再次尝试。
```

于是这个 Goal 的 `next_run_at` 早已到期，但每次 tick 都因为"上一轮还没
跑完"而跳过，且这个"没跑完"永远不会变成"跑完了"——看板上就会一直显示
"下次触发：now / overdue"，`cycle_count` 却纹丝不动。

### 1.2 已有的"孤儿检测"信号：只用于看板展示，没有接回调度判断

代码里其实已经有一份现成的孤儿判断信号，只是没有被用在正确的地方——
`ObjectiveExecutor.__init__` 的 `is_active_fn` 参数：

```python
is_active_fn — (execution_id) -> bool，判断该 execution 当前是否真的有
    进程内 worker 在跑（例如 ObjectivePersistentRunner.has_worker()）。
    提供后，get_status_summary() 会给每条 status=="running" 的记录额外
    算出 is_stale 字段：is_active_fn 返回 False 即代表"落盘状态是
    running，但进程内找不到对应 worker"——多半是 daemon 崩溃/重启后
    遗留的孤儿记录。
```

`get_status_summary()` 里：

```python
is_stale = False
if ex.status == "running" and self._is_active_fn is not None:
    is_stale = not self._is_active_fn(ex.execution_id)
```

问题是：`is_stale` 只喂给了看板展示（"正在执行"列表打个标记，让用户自己
去手动清理），`is_running()`/`_goal_has_active_cycle()` 完全不看这个
字段——即使孤儿记录已经被正确识别出来，调度层面依然认为"上一轮还在
跑"，不会自动解除阻塞。而且 `is_active_fn` 本身只在开启"目标级持久
Worker"（`objective_persistent_runner`）的部署形态下才会被传入；共享
队列（默认 submit_fn 走 `bridge.input_queue`）路径下 `_is_active_fn`
恒为 `None`，`is_stale` 永远算不出来，这个问题在默认部署形态下完全没有
任何检测手段。

### 1.3 连续跳过告警只响一次

`cron_scheduler.py::_maybe_alert_consecutive_skip()`：

```python
if threshold <= 0 or job.consecutive_skip_count != threshold:
    return
# 恰好等于 threshold（默认 5）那一次才发通知，之后不再重复
```

这条告警本身没问题（是为了不刷屏而做的"只提醒一次"设计），但没有配套
的"持续沉默太久需要升级"机制——如果用户漏看了第 5 次跳过时的那一条
通知，接下来跳过 172 次也不会再收到任何提醒。对比之下，`goal_node_
retry.py` 里连续失败重试的告警是"每 threshold 的整数倍提醒一次"，是
更合理的折中（既不刷屏，又不会永久沉默），本方案的第 2 项直接复用同一
个模式。

## 2. 修复方案

### 2.1 【核心】daemon 启动加载时，识别并回收孤儿执行记录

在 `ObjectiveExecutor.load()`（或紧随其后的一个新方法
`reconcile_orphaned_executions()`，由调用方 `server.py` 在
`objective_executor.load()` 之后立即调用一次）里，对刚加载回内存、状态
仍是非终态（`running`/`paused_for_fairness`）的执行记录做一次性回收：

- **判据**：`load()` 本身就是"daemon 刚启动、这个进程之前没有任何这些
  execution 的记忆"这一事实——凡是这一刻还处于 `running`/
  `paused_for_fairness` 的记录，只可能来自"上次进程退出前没有正常收尾"
  这一种情况（正常收尾的记录早就被转成终态或者根本不会以这个状态落盘
  等待下次加载），不需要依赖 `is_active_fn` 才能判断，冷启动本身就是
  充分信号。**有 `is_active_fn` 时可以顺带交叉核实一次**（虽然刚启动时
  它必然返回 False），但缺 `is_active_fn` 的部署形态（默认共享队列
  路径）同样能正确处理，不像现有 `is_stale` 那样依赖可选参数。
- **动作**：把这些记录标记为 `status="failed"`，`progress_notes` 写清楚
  "daemon 重启后发现的孤儿执行记录，判定为异常中断，已自动回收"，并调用
  已有的 `ObjectiveExecutor._sync_goal_status()` 同步路径把对应
  `GoalNode`/子 Objective 的 `status` 也一并置为 `failed`（复用现有终态
  收尾逻辑，不新增一条平行的状态流转分支）。
- **效果**：
  - `_goal_has_active_cycle()` 立刻能正确判断"上一轮已经不算在跑"，
    下一次 cron tick 能正常触发新一轮，不需要人工介入。
  - 子 Objective 进入 `failed` 终态后，`reap_finished_cycles()` 会按
    既有逻辑正常计入 `cycle_count`、触发 `_notify_cycle_failed()`
    通知——孤儿记录第一次有了对用户可见的收尾，而不是无声消失。
  - 如果这次孤儿回收恰好发生在 tidy 轮，上一版本实现的 tidy 通知
    （`_notify_tidy_cycle_result()`）里读取到的纸条会照常被消费、发出
    通知，只是内容会如实反映"本轮状态：failed"。
- **边界**：只处理"目标树下的 Objective 执行"（`ObjectiveExecutor`
  自己的记账），不改动 `CronJobRunner`/`CronJobExecutor` 那一条独立的
  孤儿 job 回收逻辑（那条链路在
  `daemon_task_hang_recovery_and_watchdog_hardening_plan.md` 里已经
  单独处理过，属于另一套记账，不属于本方案范围）。

### 2.2 【配套】连续跳过告警改为"每 N 次跳过重复提醒一次"

`cron_scheduler.py::_maybe_alert_consecutive_skip()` 的判断条件从：

```python
if threshold <= 0 or job.consecutive_skip_count != threshold:
    return
```

改为（与 `goal_node_retry.py` 里"每 threshold 整数倍提醒一次"同一个
模式）：

```python
if threshold <= 0 or job.consecutive_skip_count % threshold != 0:
    return
```

即第 5、10、15…次连续跳过各发一次通知，而不是只在第 5 次发一次。字段
`consecutive_skip_count` 本身在成功触发一次后会清零（既有逻辑不变），
所以正常工作的 Goal 完全不受影响，只有真正长期卡住的才会被反复提醒。

配合 2.1 的自愈修复后，正常情况下孤儿记录第一次冷启动加载就会被回收，
不会再积累到"连续跳过 172 次"这种量级；这一条改动是双保险——覆盖
2.1 覆盖不到的其它导致长期跳过的原因（比如 Goal 被误置为非法状态、
`_goal_has_active_cycle` 判断本身之外的其它跳过分支等）。

## 3. 非目标 / 不在本方案范围

- 不改变 `_fire_goal_cycle()` 里 `abandoned`/`paused`/`skip_next_cycle`
  这几种"用户主动意图"导致的正常跳过——那些不是 bug，本方案的回收逻辑
  只处理 2.1 定义的"孤儿执行记录"这一种异常场景，不影响用户主动暂停的
  语义。
- 不实现"daemon 自动检测到某个 Goal 长期停摆就自动重新触发"这类更激进
  的自愈——2.1 已经从根上让"上一轮还没跑完"这个判断恢复正确，后续
  tick 会按既有节奏自然重新触发，不需要额外的补偿性触发逻辑。
- 不改动 `is_active_fn`/`is_stale` 在看板展示层面的既有用途，两者继续
  并存——看板展示面向"用户手动检查"，2.1 的回收面向"系统自动恢复"，
  是互补关系。

## 4. 涉及文件（待实施）

- `src/mini_agent/evolution/objective_executor.py`：`load()` 之后新增
  孤儿执行记录回收逻辑（新方法或内联在 `load()` 收尾处）。
- `src/mini_agent/api/server.py`：`objective_executor.load()` 调用点
  紧随其后调用回收逻辑（如果做成独立方法）。
- `src/mini_agent/evolution/cron_scheduler.py`：
  `_maybe_alert_consecutive_skip()` 判断条件改为取模。
