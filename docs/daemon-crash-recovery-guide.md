# Daemon 崩溃自愈与告警指南

> 设计与实现记录见
> [`next_doc/daemon_crash_recovery_and_alert_plan.md`](../next_doc/daemon_crash_recovery_and_alert_plan.md)；
> 本文档是面向使用者的说明（是什么、怎么看、怎么用），不重复设计推理过程。

## 1. 解决的问题

daemon 进程偶尔会在执行过程中崩溃（未捕获异常、被外部信号杀死、OOM、
底层 native crash 等），此前崩溃后进程直接消失，既不会主动通知用户，也
不会自动恢复，需要用户碰巧执行 `daemon status` 或尝试连接才会发现。

## 2. 目前的实现进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 崩溃信息持久化 + 独立告警通道 | ✅ 已完成 |
| 阶段二 | 后台（`--detach`）自动重启（预算/退避） | ✅ 已完成 |
| 阶段三 | 前台模式自愈 + `daemon stop` 联动收敛 | ✅ 已完成 |
| 阶段四 | 文档完善 + 更多测试覆盖 | ✅ 已完成 |

**默认行为变更（阶段二起）**：`daemon start`（无论带不带 `--detach`）现在
**默认自动重启**（`daemon_auto_restart_enabled=true`）——崩溃后 supervisor
会在指数退避（1s/2s/4s/8s/16s/30s/60s）后自动拉起新的子进程，10 分钟滑动
窗口内最多重试 5 次，超出预算就放弃并发一条"需要人工介入"的告警，不会
无限重启。不想要自动重启可以在 `agent_config.json` 里设置：
```json
{"http": {"daemon_auto_restart_enabled": false}}
```

**行为变更（阶段三起）**：前台模式（不带 `--detach`）现在与后台共享同一套
崩溃自愈能力，见下面第 8 节的详细说明。

## 3. 怎么看崩溃信息

### 3.1 看板顶栏（推荐）

打开看板后，如果存在未确认的崩溃记录，顶栏会出现一条红色常驻横幅：
「⚠️ 检测到 N 次 Daemon 崩溃，尚未确认」，点开可以看到：
- 崩溃时间、存活时长
- 崩溃原因（关联到的最近一次未捕获异常，或"疑似 OOM/外部信号杀死"）
- `daemon.log` 最后 30 行
- "标记已读" 按钮

这块横幅是独立于"关注与通知"tab 的通用列表的，也不受
`notification_config.json` 里渠道开关的影响（详见下面"为什么要独立"）。

### 3.2 命令行

```
mini-agent daemon status
```
会额外展示：
- 是否处于 supervisor 监控下
- 最近一次崩溃的时间 + 原因摘要

### 3.3 原始文件（排查用）

- `<project_root>/.agent/daemon_run_state.json` —— 当前运行状态标记
  （`running` / `stopped_by_user`）
- `<project_root>/.agent/daemon_crash_history.jsonl` —— 每次崩溃一条记录
  （退出码/存活时长/日志尾部/关联异常/重启决策）
- `<project_root>/.agent/notification/daemon_crash_alerts.jsonl` —— 崩溃
  告警的独立存储（看板横幅读的就是这份）
- `<project_root>/.agent/daemon_supervisor.pid` —— supervisor 自身的 PID

## 4. 为什么崩溃告警走独立通道，不是常规通知

`watchlist_report` 这类通知语义上是"周期性汇总/建议性提醒"，可以慢慢看、
可以批量已读；崩溃是"服务不可用"这种高时效性事件，混在一起容易被当成
普通消息划过去。因此：
- 存储物理隔离（`daemon_crash_alerts.jsonl` 而不是 `reports.jsonl`）
- 看板展示位置独立（顶栏常驻横幅，不是"关注与通知"tab 下的分类筛选项）
- 不经过 `NotificationDispatcher` 的 kanban 渠道（那个渠道写的正是
  `reports.jsonl`），但仍然会广播给用户在 `notification_config.json` 里
  配置的其它外部渠道（邮件/webhook 等）

## 5. 崩溃后怎么判断是不是"未捕获到 Python 异常"

`daemon_crash_history.jsonl` 每条记录都带 `last_exception` 字段：
- 有值：说明找到了一条 pid 匹配的全局异常记录（`~/.agent/logs/error.jsonl`），
  这通常就是崩溃的直接原因
- `null`：说明没找到对应记录——要么是被外部信号（如 `kill -9`）直接杀死，
  要么是 OOM，要么是底层 native 库崩溃，这些都不会经过 Python 异常路径，
  日志里天然找不到

`exit_code` 为负数时（Unix），`-N` 表示被信号 `N` 杀死，`-9` 高度提示 OOM。

## 6. 手动验证（模拟崩溃）

```bash
mini-agent daemon start --detach
# 找到真正的 daemon 子进程 PID（不是 supervisor 的）
cat .agent/daemon.pid
kill -9 <该 PID>
# 等几秒后：
mini-agent daemon status   # 应该能看到"最近一次崩溃"摘要
# 打开看板，应该能看到顶栏红色横幅
```

`daemon stop` 不会触发这套崩溃记录/告警——`daemon stop` 在动手停止前会先
把 `daemon_run_state.json` 标成 `stopped_by_user`，supervisor 据此判断这是
预期停止，不算崩溃。

## 7. 自动重启配置项

在 `agent_config.json` 的 `http` 块下可调：

```json
{
  "http": {
    "daemon_auto_restart_enabled": true,
    "daemon_restart_max_attempts": 5,
    "daemon_restart_window_seconds": 600,
    "daemon_restart_backoff_seconds": [1, 2, 4, 8, 16, 30, 60]
  }
}
```

- `daemon_auto_restart_enabled`：总开关，关掉后行为等同阶段一（只记录+
  告警，不重启）
- `daemon_restart_max_attempts` / `daemon_restart_window_seconds`：滑动窗口
  预算——`window_seconds` 内重启次数超过 `max_attempts` 就放弃，避免"崩溃
  →重启→秒崩"的重启风暴
- `daemon_restart_backoff_seconds`：每次重启前的等待时间序列，超出数组
  长度后固定用最后一个值（默认封顶 60s）

预算耗尽后 `daemon status` 会提示"Auto-restart budget exhausted — manual
`daemon start` required"，看板告警详情里也会标注 `restart_decision:
"giveup"`。

## 8. 前台模式（阶段三）

`mini-agent daemon start`（不带 `--detach`）现在的行为模型和后台完全一致：
当前终端进程本身变成一个 supervisor，真正的 daemon 是它的子进程。

- **看起来没变**：daemon 的实时输出依然直接打印在你这个终端里，Ctrl-C
  依然能停止它——这两点跟以前一样；
- **实际变了什么**：以前（POSIX 下用 `os.execv` 做进程替换）当前终端跑的
  就是 daemon 自己，一旦它崩溃，终端里只会看到一个非零退出码，没有任何
  自愈或告警。现在当前终端跑的是 supervisor，daemon 子进程崩溃后：
  1. supervisor 立即按第 5、6 节描述的方式记录崩溃 + 发送告警（看板横幅
     一样会出现）；
  2. 如果 `daemon_auto_restart_enabled=true`（默认），supervisor 会按退避
     序列自动拉起一个新的子进程，继续在同一个终端里输出，你不需要手动
     重新执行 `daemon start`；
  3. 如果预算耗尽，supervisor 打印"Auto-restart budget exhausted, giving
     up"并退出，此时才需要你手动重新执行 `daemon start`。
- **Ctrl-C 行为**：按下 Ctrl-C 时，supervisor 会先把这次停止标记为
  "用户主动停止"（不会被当成崩溃），再把信号转发给子进程，等它真正退出
  后 supervisor 才退出——和以前"按一下 Ctrl-C 就能退出"的体感一致，只是
  内部多了一层转发。
- **另开一个终端执行 `daemon stop`**：跟后台模式完全一样地工作——
  `daemon stop` 会先兜底标记 `stopped_by_user`，然后按 HTTP → 信号 → 强杀
  三级尝试停止子进程，并等待/兜底强杀前台那个 supervisor 终端进程。

一个直观的验证方法：

```bash
mini-agent daemon start
# 在另一个终端：
cat .agent/daemon.pid   # 记下真正 daemon 子进程的 PID（不是前台终端本身）
kill -9 <该 PID>
# 回到第一个终端，应该能看到类似：
#   [daemon] Crashed (exit=-9). Restarting in 1s (attempt 1/5)...
# 随后子进程自动重新拉起，daemon.log/看板横幅同样会出现这次崩溃记录
```

## 9. 卡死检测（daemon_hang_detection_and_alert_escalation_plan.md 阶段一）

前面几节覆盖的都是"子进程退出"这一类故障。但更隐蔽的情况是**子进程没有
退出，但已经完全无法工作**——比如事件循环卡在某个同步阻塞调用里、主线程
死锁。这种情况下 `daemon status` 会显示"运行中"，但 HTTP 请求全部超时，
且不会触发上面几节的任何自愈/告警链路（`proc.wait()` 永远不返回）。

从这个阶段开始，supervisor 不再是"一直阻塞等子进程退出"，而是**带超时的
轮询等待 + 主动探活**：每隔一段时间（默认 10s）如果子进程还没退出，就顺带
探一次 `GET /v1/health`（超时 2s）。连续探测失败达到阈值（默认 3 次，即约
30s 内完全无响应）后，判定为"卡死"，直接强杀（跳过优雅关停尝试，因为连
HTTP 都不响应，`SIGTERM` 大概率也处理不了），然后走跟"崩溃"完全相同的
记录 + 告警 + 重启预算判定流程——只是 `daemon_crash_history.jsonl` 里的
`restart_decision` 会是 `"hang_killed"` 而不是 `"restarted"`/`"giveup"`，
摘要文案也会明确写"判定为卡死"，跟"进程自己意外退出"区分开（两者排查
方向完全不同）。

`daemon status` 展示上的区别：

```
[daemon] Supervised: yes (supervisor PID=1234, crash 自动检测已启用)
[daemon] Last hang (killed): 08-29 15:02:11 — Daemon（PID=5678）进程存活但无响应，
判定为卡死，存活 0h42m，连续 3 次健康检查无响应（每次间隔 10s，超时 2s），已强制终止
```

（普通崩溃则显示为 `Last crash: ... — Daemon（PID=...）意外退出，...`。）

### 配置项

```json
{
  "http": {
    "daemon_hang_detection_enabled": true,
    "daemon_hang_check_interval_seconds": 10,
    "daemon_hang_check_timeout_seconds": 2,
    "daemon_hang_consecutive_failures": 3
  }
}
```

- `daemon_hang_detection_enabled`：总开关，跟 `daemon_auto_restart_enabled`
  是正交的两个开关——可以只开崩溃自愈不开卡死探测，反之同理。关掉后
  supervisor 退化为阶段一~三的纯 `proc.wait()` 行为，不做任何探活；
- `daemon_hang_check_interval_seconds`/`daemon_hang_check_timeout_seconds`/
  `daemon_hang_consecutive_failures`：探活的轮询间隔、单次超时、判定为
  卡死所需的连续失败次数，三者共同决定"最坏情况下卡死到被发现之间的
  延迟"（默认配置下约 30s）；健康检查偶尔成功一次会把连续失败计数清零，
  避免正常场景下一次慢响应被误判为卡死。

前台模式（`daemon start`，不带 `--detach`）与后台 `--detach` 共用完全相同
的卡死探测逻辑，行为一致。

### 9.1 双信号判定（daemon_dual_signal_hang_detection_plan.md 阶段B）

上面描述的判定只看 `GET /v1/health` 一个信号，存在两个问题：

- **HTTP 层忙 ≠ 真卡死**：`/v1/health` 和看板其它接口共享同一个
  asyncio event loop，如果某个接口内部有未经 `run_blocking()` 包装的
  同步阻塞调用，会把 `/v1/health` 一起拖慢，daemon 明明只是在忙、并
  没有卡死，却会被强杀；
- **HTTP 层正常 ≠ 核心调度真活着**：daemon 存在的根本意义是自主任务
  调度（cron/goal 执行），如果调度线程死锁但 HTTP 服务本身还能应答
  `/v1/health`，阶段一的判定完全检测不到这种更危险的场景——用户看到
  "daemon 运行正常"，实际自主任务早已停摆。

当 `scheduler_heartbeat_enabled=true`（见
`docs/daemon-execution-model-guide.md`）时，`SchedulerHeartbeat` 的
看门狗线程会把"核心调度是否还在正常 tick"的观测状态，独立于 HTTP/
asyncio 之外，直接写到磁盘旁路文件：

```
<project_root>/.agent/scheduler_heartbeat_status.json
```

HTTP 连续无响应达到 `daemon_hang_consecutive_failures` 阈值后，
supervisor 不再直接强杀，而是先读这个文件裁决：

| HTTP 探测 | 核心调度心跳（该文件） | 判定 |
|---|---|---|
| 连续超时 | 新鲜、未被看门狗判定为疑似卡死 | daemon 根本功能正常，HTTP 层只是暂时忙碌——**不强杀**，重置失败计数继续观察 |
| 连续超时 | 过期，或 `suspected_stuck=true` | 核心调度真卡死——判定为卡死并强杀，即使 HTTP 碰巧还能应答也一样 |
| 连续超时 | 文件不存在（未开启心跳，或还没有第一轮数据） | 退化为阶段一原有的纯 HTTP 判定（向后兼容） |

对应地，`daemon_crash_history.jsonl` 里 `hang_killed` 记录新增一个
`hang_signal` 字段：`"scheduler_heartbeat"`（核心调度心跳判定为卡死，
当前最权威的信号）或 `"http_only"`（心跳信号不可用，退化判定）。摘要
文案也会区分成"判定为核心任务调度卡死"和沿用原来的"连续 N 次健康检查
无响应"两种，事后从历史记录里就能直接看出根因，不用回头翻栈快照猜。

`scheduler_heartbeat_enabled` 目前默认 `true`（见
[执行模型指南](daemon-execution-model-guide.md)），也就是说这套双信号
判定对大多数部署默认生效；显式关掉该开关的部署没有旁路文件，行为退化
为 9 节描述的阶段一纯 HTTP 判定，零改动。

一个直观的验证方法（模拟卡死而不是崩溃——进程存活但故意不响应 HTTP，
比较取巧的办法是临时把 `daemon_hang_check_interval_seconds`/
`daemon_hang_consecutive_failures` 调小、再用调试器/断点手段让 daemon
主线程卡住）：观察 `daemon.log`/`daemon status` 是否在约
`interval × consecutive_failures` 秒内出现 `hang_killed` 记录，而不是
被误判为普通崩溃或者完全没反应。

### 尚未覆盖（留给后续阶段，见计划文档 §3）

- 崩溃/卡死告警发出后没有"确保被看到"的超时升级机制；
- `daemon_crash_history.jsonl`/`daemon_crash_alerts.jsonl` 仍然只追加，
  没有按条数/时间跨度做轮转清理。

## 10. 重启预算落盘 + 重启后健康验证（阶段二）

第 9 节的卡死探测解决了"运行期间卡死"的感知问题，但还有两个相关的盲区：

**预算落盘**：重启预算原来只是内存里的一个滑动窗口列表，如果 supervisor
自身因为某种原因异常退出（概率很低，但不为零——比如宿主机重启、被系统
OOM killer 一并带走），下一次 `daemon start` 是全新的 supervisor 实例，
预算窗口从零开始。如果这是一个持续性问题（比如某次发版引入的 bug 导致
固定几分钟崩一次），本来应该在预算耗尽后触发"已放弃，需要人工介入"的
告警，却会被"看起来一直在正常重启"掩盖。从这个阶段开始，预算判断改为
"内存计数与 `daemon_crash_history.jsonl` 里最近 `window_seconds` 内
`restarted`/`hang_killed` 记录数取较大值"，不需要额外的配置项，直接复用
已有的崩溃历史文件。

**重启后健康验证**：原来判定"这轮重启是否成功"只看"新子进程有没有立刻
退出"，不代表 HTTP 服务真的起来了——如果新进程卡在初始化阶段（比如配置
加载抛异常但被吞掉），会呈现"新进程一直存在但从未真正提供服务"的状态，
要等它自己再崩一次，或者等第 9 节常规探活轮询走完多轮判定才会被发现。
从这个阶段开始，supervisor 每次拉起新子进程后，会先给它一个固定窗口期
（默认 30s，与 `daemon start` 本身启动等待的默认超时一致）证明自己真的
可用；窗口期内子进程自己退出（配置错误直接崩了）按正常"进程退出"处理，
窗口期耗尽仍未通过健康检查则按卡死处理（`restart_decision: "hang_killed"`，
摘要文案会明确写"重启后 Ns 内未通过健康检查"，与运行期间的常规卡死判定
区分开）。

### 配置项

```json
{
  "http": {
    "daemon_post_restart_health_check_seconds": 30
  }
}
```

跟其它 `daemon_hang_*`/`daemon_restart_*` 配置项放在一起，走同一套自动
装配机制。重启预算跨生命周期延续不需要单独的配置项——直接复用现有的
`daemon_crash_history.jsonl`。

### 尚未覆盖（留给阶段三）

- 崩溃/卡死告警发出后没有"确保被看到"的超时升级机制；
- `daemon_crash_history.jsonl`/`daemon_crash_alerts.jsonl` 仍然只追加，
  没有按条数/时间跨度做轮转清理。

## 11. 告警升级 + 历史文件轮转（阶段三）

崩溃/卡死发生时已经会写入独立告警存储（看板横幅恒真展示未确认记录）并
广播一次外部渠道。但如果当时没打开看板、也没配置外部渠道（或者配置了
但那次广播因为网络问题失败了），这条告警可能安静地躺在
`daemon_crash_alerts.jsonl` 里几天都不会被看到——尤其是配置了自动重启
之后，daemon 表面看起来"一直健康"，用户主动去查的动机反而更低。

从这个阶段开始有两个独立的兜底手段：

1. **超时升级重推**：daemon 进程里有一条独立的后台线程，定期扫一次未
   确认的崩溃/卡死告警。存在"创建超过 `daemon_crash_alert_escalation_
   hours`（默认 1 小时）仍未确认"的记录时，重新广播一次到已配置的外部
   渠道（邮件/webhook 等；不会重复推到 kanban——kanban 横幅本身恒真常驻，
   不需要重复）。同一条告警只升级一次，不会持续骚扰；
2. **交互时顺带提示**：`daemon connect` 建立连接、或本地 `mini-agent`
   REPL 启动时，如果存在未确认的崩溃/卡死告警，会顺带打印一句
   `⚠️ 有 N 条未读的 daemon 崩溃/卡死记录，运行 daemon status 查看`，
   不打断正常流程，只在连接/启动那一刻打印一次。

同时，`daemon_crash_history.jsonl`/`daemon_crash_alerts.jsonl` 现在会按
配置的最大条数轮转，超过上限时只保留最近的记录（旧记录直接丢弃，不做
归档——排查用途上"最近 N 条"已经足够）。

### 配置项

```json
{
  "http": {
    "daemon_crash_alert_escalation_hours": 1,
    "daemon_crash_history_max_entries": 1000
  }
}
```

- `daemon_crash_alert_escalation_hours`：未确认告警多久后升级重推一次；
  设为 `0`（或负数）关闭这项功能——后台线程不会启动；
- `daemon_crash_history_max_entries`：崩溃历史/告警文件保留的最大条数，
  两个文件共用同一个配置项（语义上是"这个项目要保留多久/多少条崩溃排查
  记录"，没必要拆成两个数字）。

升级重推的后台线程轮询间隔不单独提供配置项——固定取
`escalation_hours` 的一半（下限 5 分钟、上限 30 分钟），用户只需要关心
"多久没读会被提醒"这一个语义。

### 尚未覆盖（留给后续阶段）

- `daemon status` 目前只展示未确认告警数量，不单独展示"这条已经升级
  提醒过几次"；
- `daemon status` 不展示崩溃/告警文件的轮转历史（比如"最近一次因为超过
  1000 条被裁剪掉了 N 条"）——都是展示细节，不影响升级/轮转机制本身
  是否生效。

## 12. 卡死前全线程栈快照（阶段四）

第 9 节的卡死判定只解决了"感知到卡死了"，但判定成立、强杀之后，
`daemon_crash_history.jsonl` 里那条记录一直有个天生的盲区：进程没有
抛异常、也没有退出，`last_exception`/`log_tail` 对这种场景来说本来就是
空的——过去只知道"卡死了"，完全不知道卡在哪个函数、是不是被某把锁
卡住了。

这个阶段引入 `notification/hang_dump.py`：daemon 子进程启动时用标准库
`faulthandler.register(signal.SIGUSR1, all_threads=True)` 注册一个信号
触发的全线程栈转储处理器（正常运行时零开销，完全不会被触发）。
supervisor 判定卡死、真正 `SIGKILL` 强杀之前，先给子进程发一次
`SIGUSR1`，等一小段时间（默认 3s）把转储文件读回来，随崩溃记录一起
落盘到新增的 `hang_stack_dump` 字段，再继续原有的强杀流程——多了一步
诊断，不影响强杀本身的时效性。

之所以用 `faulthandler` 而不是等子进程自己打日志：它是直接从信号处理器
里用 `os.write()` 往文件描述符写 C 级别的帧信息，不需要拿到 GIL、不
需要目标线程主动让出控制权——这正是"event loop 被某个同步阻塞调用
独占，或者被跨线程死锁卡住"这类最常见的卡死场景下，仍然有很大概率能
拿到东西的原因。

```
$ daemon status
...
[daemon] Last hang (killed): 08-31 22:14:05 — Daemon（PID=9008）进程存活但无响应，
判定为卡死，存活 10h41m，连续 3 次健康检查无响应（每次间隔 10s，超时 2s），
已强制终止，已抓取强杀前全线程栈快照（见 hang_stack_dump 字段）
```

`daemon_crash_history.jsonl` 对应记录新增的字段：

```json
{
  "...": "...",
  "hang_reason": null,
  "hang_stack_dump": "Thread 0x... (active):\n  File \"...\", line ..., in ...\n..."
}
```

（非卡死场景 `hang_stack_dump` 恒为 `null`；卡死但没能拿到内容时是一段
以 `[未获取到栈快照]` 开头的说明文字，不是 `null`——方便区分"这次没
尝试/不支持"和"尝试了但没拿到"。）

### 配置项

```json
{
  "http": {
    "daemon_hang_stack_dump_enabled": true,
    "daemon_hang_stack_dump_wait_seconds": 3
  }
}
```

- `daemon_hang_stack_dump_enabled`：总开关，默认开启。关掉后强杀前不会
  发 `SIGUSR1`、不等待，行为退化到本阶段之前（`hang_stack_dump` 恒为
  `null`）——如果担心 `SIGUSR1` 这个信号语义跟其它组件冲突，可以显式
  关掉；
- `daemon_hang_stack_dump_wait_seconds`：发出 `SIGUSR1` 后最多等待读回
  结果的时间，默认 3s，不建议调得太大——毕竟这是"强杀前顺手多做一步"，
  不应该明显拖延卡死恢复本身的时效性。

### 已知限制

- **Windows 不支持**：`faulthandler.register()` 的自定义信号回调在
  Windows 上不可用（标准库文档明确写了 "Not available on Windows"），
  这个功能在 Windows 上直接跳过，`hang_stack_dump` 会是一段说明性文字
  （"Windows 平台不支持 faulthandler 的信号栈转储"），不是静默 `null`；
- **子进程没走到注册那行代码就已经卡死**（比如卡在 import 阶段）：
  `SIGUSR1` 送过去后，Python 对未注册信号的默认处置是终止进程——
  效果上等价于提前触发了紧接着的 `SIGKILL`，不会有副作用，只是转储
  自然拿不到内容；
- 转储内容超过 40,000 字符会被截断（保留开头部分 + 提示信息），避免
  线程/协程数量异常多时把单条崩溃记录撑得过大；崩溃历史文件本身仍然
  按 `daemon_crash_history_max_entries` 做条数轮转。

### 尚未覆盖（留给后续阶段）

- `daemon status` 目前只把 `hang_stack_dump` 原样落在 JSONL 里，还没有
  在终端摘要里对栈内容做进一步的高亮/聚合展示（比如自动标出哪个线程
  持有哪把锁）；
- 没有对多次卡死的栈快照做跨事件对比（比如"这次和上次是不是卡在同一
  行"），目前需要人工比对。
