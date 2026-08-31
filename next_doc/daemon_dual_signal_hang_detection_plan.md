# Daemon 卡死判定重构：调度心跳磁盘旁路 + 双信号判定矩阵

> 聚焦范围：`evolution/scheduler_heartbeat.py`（新增磁盘旁路写入）、
> `cli/daemon_supervisor.py`（判定逻辑改为双信号矩阵）、`cli/daemon.py`
> （新增旁路文件路径/读取辅助函数）、`api/server.py`（HTTP 忙碌度计数）、
> `api/routes.py::get_self_execution_model_status`（新增 http_busy 字段）、
> `streamlit_app/`（看板"🧠 自我状态"区块拆分展示）。
>
> 触发背景：`daemon_hang_detection_and_alert_escalation_plan.md` 阶段一
> 实现的卡死判定完全依赖 `GET /v1/health` 单一信号——该端点与看板其它
> 接口共享同一个 asyncio event loop，一旦有接口内部存在未经
> `run_blocking()` 包装的同步阻塞调用，`/v1/health` 会被一起拖慢，
> 造成"daemon 只是在忙、并没有卡死"却被 supervisor 强杀的**误判**；
> 反过来，`SchedulerHeartbeat`（`daemon_execution_model_and_scheduler_
> heartbeat_improvement_plan.md` 阶段二 + P3 看门狗）已经能独立检测"自
> 主任务调度是否卡在某次 tick() 里"，但这个信号只通过
> `/v1/self/execution_model_status` 这个 HTTP 端点暴露，同样会被
> event loop 阻塞挡住——真正发生"核心调度卡死但 HTTP 还能勉强应答"时，
> 现状反而**完全检测不到**（漏判，比误判更危险：用户看到"daemon 运行
> 正常"，实际自主任务早已停摆）。

---

## 0. 结论先行

| # | 问题 | 现状 | 本方案 |
|---|---|---|---|
| 1 | 卡死判定只看 `/v1/health`，把"HTTP 层忙"和"真卡死"混为一谈 | 单信号 | 拆成 HTTP 忙碌信号 + 核心调度心跳信号两路，独立判定 |
| 2 | 核心调度心跳（`SchedulerHeartbeat.suspected_stuck`）只能通过 HTTP 端点读取，event loop 卡住时读不到 | 依赖 event loop | 心跳看门狗线程直接写磁盘旁路文件，supervisor 直接读文件，不经过 HTTP/asyncio |
| 3 | "核心调度已死但 HTTP 还能应答"这种最危险的场景现状检测不到 | 漏判 | 判定矩阵以核心调度心跳为主信号，HTTP 响应只作为辅助/降级依据 |
| 4 | 看板"🧠 自我状态"里 HTTP 服务和核心调度的"忙碌"混在一起看不出区别 | 无区分 | 新增 HTTP in-flight 请求计数，与 `scheduler_heartbeat` 字段分开展示 |
| 5 | 未开启 `scheduler_heartbeat_enabled`（默认关闭）的部署没有心跳信号可用 | — | 自动退化为纯 HTTP 判定（阶段一原有行为），完全向后兼容 |

分两个阶段实施：**阶段 B（核心）** 做信号解耦 + 判定矩阵；**阶段 C** 做看板可视化。均为默认开启的观测/判定升级，不新增需要用户手动打开的开关（沿用"卡死检测默认开启"的既有产品定位），但 B 阶段新旁路写入本身只在 `scheduler_heartbeat_enabled=True` 时才发生，未开启时零额外开销。

---

## 1.【阶段 B】核心调度心跳磁盘旁路

### 1.1 为什么不能直接读 `/v1/self/execution_model_status`

`SchedulerHeartbeat` 的看门狗线程已经能独立判断"是否卡在某次未返回的
tick() 里"（`_check_stuck()`，与被监控的调度线程本身解耦，不会因为
调度线程卡住而一起卡住）。但这份判定结果目前只存在于 `HttpServer`
进程内存里，对外只通过一个 `async def` 路由暴露——如果 event loop
本身被别的请求堵住，这个路由和 `/v1/health` 一样读不出来。**要让
supervisor（外部进程）能在 event loop 完全瘫痪的情况下仍然读到核心
调度的真实状态，数据必须走一条完全不经过 HTTP/asyncio 的旁路。**

### 1.2 落地方式：状态文件

复用项目里"进程间用 `.agent/` 下的小文件通信"的既有约定（
`daemon_run_state.json`、`daemon_supervisor.pid`、
`daemon_crash_history.jsonl` 都是这个模式），新增：

```
<project_root>/.agent/scheduler_heartbeat_status.json
```

写入方：`SchedulerHeartbeat._check_stuck()`（看门狗线程，每次轮询后，
不管本轮是否触发 tick 都写一次，写入操作本身要快、要能容错，绝不能
因为写文件失败影响看门狗线程存活）。写入内容：

```json
{
  "written_at": 1735689600.123,
  "last_tick_started_at": 1735689595.0,
  "last_tick_finished_at": 1735689596.2,
  "last_tick_duration_seconds": 1.2,
  "tick_interval_seconds": 60.0,
  "suspected_stuck": false,
  "pid": 16040
}
```

- `written_at`：看门狗本次落盘时间，供 supervisor 二次校验"旁路文件
  自己是不是也不新鲜了"（比如整个进程假死、连看门狗线程所在的
  Python 解释器都调度不动——极端场景下这也能被 `written_at` 过期
  反映出来，是比"文件内容"更底层的一道保险）。
- `pid`：写入时的进程 pid，重启后新进程覆盖旧文件时天然核对一致性
  （避免读到上一轮进程遗留的陈旧文件产生误判——配合 `written_at`
  新鲜度判断即可，不需要额外加锁）。
- 写文件用"临时文件 + 原子 rename"，避免 supervisor 在写入过程中
  读到半截 JSON。

读取方：`daemon_supervisor.py::_wait_child()`，每轮探测循环里除了
现有的 `client.health_check()`，同时读一次这个文件（纯本地磁盘 IO，
无网络往返，代价极小）。文件不存在（未开启心跳/心跳未启动过）时
视为"无该信号"，判定逻辑退化为原有纯 HTTP 路径。

### 1.3 判定矩阵

`hang_consecutive_failures` 次连续 HTTP 探测失败后，不再直接判定卡死，
而是先看能不能读到新鲜的心跳旁路文件（`now - written_at` 在
`tick_interval_seconds * stuck_threshold_multiplier` 量级以内视为新鲜）：

| HTTP 探测 | 核心调度心跳 | 判定 | 动作 |
|---|---|---|---|
| 正常 | 新鲜且 `suspected_stuck=False` | 健康 | 不告警 |
| 连续超时 | 新鲜且 `suspected_stuck=False` | HTTP 层局部阻塞，daemon 根本功能正常 | **不强杀**，记一条 `http_busy` 级别的告警/日志，继续观察，不计入卡死重启预算 |
| 正常/超时均可 | 存在但 `suspected_stuck=True`，或旁路文件本身已过期（看门狗线程也调度不动） | 核心调度真卡死 | 判定为卡死，走原有的抓栈快照 + 强杀 + `restart_decision` 流程；新增 `hang_reason` 文案区分"核心调度心跳超时"与"HTTP 无响应" |
| 连续超时 | 文件不存在（未开启心跳 or 从未启动过） | 退化为阶段一原有纯 HTTP 判定 | 沿用现有强杀逻辑不变 |

`record_daemon_crash()` 新增一个可选字段 `hang_signal`
（`"scheduler_heartbeat"` / `"http_only"`），随崩溃记录落盘，方便事后
从 `daemon_crash_history.jsonl` 直接区分根因，不用回头翻栈快照猜。

### 1.4 影响面与兼容性

- `scheduler_heartbeat_enabled=False`（默认关闭）的部署：无旁路文件，
  行为与今天完全一致，零风险。
- `scheduler_heartbeat_enabled=True` 的部署：判定会变得**更宽容**（HTTP
  慢不再直接杀）也**更严格**（核心调度真卡死时，即使 HTTP 还能应答也
  会被杀）——这是本方案的核心目的，符合"daemon 存在的意义是自主调度"
  这个定位。
- 不改变现有 `daemon_hang_check_interval_seconds` /
  `daemon_hang_check_timeout_seconds` / `daemon_hang_consecutive_failures`
  三个配置项的语义，只是在"连续失败达到阈值"之后插入一次旁路文件裁决，
  向后兼容。

---

## 2.【阶段 C】看板忙碌状态可视化

### 2.1 HTTP 服务忙碌度（新增）

`HttpServer` 增加一个进程内计数器（`threading.Lock` 保护的
in-flight 计数 + 最早未完成请求的开始时间），通过一个 ASGI 中间件
在请求进入/退出时自增自减，不需要额外线程。汇总到
`get_self_execution_model_status()` 新增字段：

```json
"http_busy": {
  "in_flight_count": 3,
  "oldest_in_flight_seconds": 12.4
}
```

### 2.2 核心调度心跳状态（复用已有字段，改展示方式）

`scheduler_heartbeat` 字段已经存在（`last_tick_started_at` /
`last_tick_finished_at` / `suspected_stuck` 等），阶段 C 只改看板展示，
不改后端结构：在"🧠 自我状态"tab 里把 HTTP 忙碌度和调度心跳状态拆成
两个并列的小卡片，而不是像现在这样混在一段文字里，并直接展示
"距上次 tick 完成已过 Xs"这种人可读的相对时间，不需要用户自己心算。

### 2.3 不做的事

- 不引入"event loop 调度延迟自测"（定期调度一个协程测量实际唤醒延迟）
  这类额外机制——阶段 B 的磁盘旁路已经能解决"HTTP 卡不代表核心卡死"
  的判定问题，这类自测只是锦上添花的诊断信息，本次不做，避免范围扩散。
- 不强制审计/重写所有 243 个路由确保 100% 使用 `run_blocking()`——
  阶段 B 的判定矩阵已经让"HTTP 层偶发阻塞"不再触发误杀，这项审计降级
  为日常代码规范（新增路由要过一遍 `run_blocking` 检查），不作为本
  方案的强依赖。

---

## 3. 实施顺序

1. `scheduler_heartbeat.py`：新增旁路文件写入（`_check_stuck()` 内）
2. `cli/daemon.py`：新增旁路文件路径辅助函数 + 读取/新鲜度判断辅助函数
3. `cli/daemon_supervisor.py`：`_wait_child()` 接入判定矩阵，
   `record_daemon_crash()` 调用点补上 `hang_signal`
4. `api/server.py`：HTTP in-flight 计数中间件
5. `api/routes.py`：`get_self_execution_model_status` 补充 `http_busy` 字段
6. `streamlit_app/`：看板"🧠 自我状态"区块拆分展示
7. 补充/调整单元测试
8. 更新 `docs/daemon-crash-recovery-guide.md`、
   `docs/autonomous-daemon-design.md`、
   `next_doc/daemon_hang_detection_and_alert_escalation_plan.md`
   （标注本方案对阶段一判定逻辑的替换关系）

阶段 B（1-3 步 + 对应测试/文档）完成后打包一次；阶段 C（4-6 步 +
对应测试/文档）完成后再打包一次。
