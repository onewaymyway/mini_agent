# mini-agent Daemon 卡死检测与告警升级：改进计划

> 聚焦范围：`cli/daemon_supervisor.py`（supervisor 循环）、`cli/daemon.py`
> （`daemon_run_state.json`/`daemon_crash_history.jsonl`/`cmd_daemon_status`）、
> `notification/daemon_crash_store.py`、`config/models.py::HttpConfig`。
> 触发背景：完成
> `daemon_crash_recovery_and_alert_plan.md`（daemon 进程**退出**后的感知+
> 自愈）之后，从实际使用角度复盘还有哪些场景覆盖不到，梳理出这份后续
> 计划，聚焦"进程没退出但已经不可用"和"告警发出后没人看到"这两类此前
> 方案的盲区。

---

## 0. 结论先行：一张表

| # | 问题 | 影响范围 | 严重程度 | 本方案阶段 |
|---|---|---|---|---|
| 1 | supervisor 只认"进程退出"为异常，daemon 主进程卡死（事件循环死锁、被同步阻塞调用长期占住）但没退出时，`proc.wait()` 永远不返回，不会记录、不会告警、不会重启 | 全部部署（尤其是长期无人值守场景） | 🔴 高 | 阶段一 |
| 2 | 重启预算是纯内存状态（`restart_timestamps` 列表），supervisor 自己异常退出后下次 `daemon start` 预算归零重来，掩盖"持续性崩溃"这类真实稳定性问题 | 配了自动重启且长期运行的部署 | 🟡 中 | 阶段二 |
| 3 | supervisor 判定"重启成功"只看子进程没有立刻退出，不代表 HTTP 服务真的起来——新进程若卡在初始化阶段，要等它自己再崩一次才会触发下一轮判定，期间用户完全不知情 | 配了自动重启的部署 | 🟡 中 | 阶段二 |
| 4 | 崩溃告警是一次性写入，用户没打开看板、没配外部渠道时可能几天都不会看到——尤其自动重启让 daemon "看起来一直健康"，用户更没有主动去查的动机 | 未及时查看看板/未配置外部渠道的部署 | 🟡 中 | 阶段三 |
| 5 | `daemon_crash_history.jsonl`/`daemon_crash_alerts.jsonl` 只追加不清理，长期运行且偶发崩溃的部署会无限增长 | 长期运行的部署 | 🟢 低 | 阶段三顺带做 |
| 6 | `last_exception` 只按 pid 倒序取全局异常日志最后一条，崩溃前若有级联失败，可能不是根因 | 排查崩溃原因时 | 🟢 低 | 暂不实施（见 §5） |

下面逐条展开。

---

## 1. 【🔴 高优先级】"卡死"和"崩溃"是两种性质不同的故障，现在只覆盖了后者

### 1.1 现状

`daemon_crash_recovery_and_alert_plan.md` 建立的整套机制（`daemon_run_state.json`
标记 + supervisor `proc.wait()` 判定 + 崩溃记录/告警/自动重启）全部建立在
一个前提上：**子进程会退出**。但现实中更常见、也更隐蔽的故障是子进程
**没有退出，但已经完全无法工作**：

- HTTP 服务的事件循环卡在某个同步阻塞调用里（比如某个第三方 SDK 的网络
  请求没有设超时），`/v1/health` 也不再响应；
- 主线程死锁（比如两把锁交叉等待）；
- 某个后台线程抛了未捕获异常导致解释器状态异常，但主线程仍在运行、
  进程仍然"活着"。

这些场景下，`daemon_run_state.json` 会永远停留在 `"running"`（进程根本
没走到任何退出路径，无论是优雅关停还是异常退出），supervisor 的
`proc.wait()` 也会永远不返回——**整个崩溃自愈链路完全不会被触发**，用户
看到的是"进程存在、PID 文件正常、但请求全部超时"，比进程直接消失更难
排查（至少进程消失时 `daemon status` 能立刻看出来）。

注：仓库里已有 `daemon_task_hang_recovery_and_watchdog_hardening_plan.md`，
但那份方案覆盖的是 cron job / Objective 持久 Worker / 调度心跳线程**各自**
卡死的场景（业务执行层面），依赖的是这些子系统各自的心跳/超时机制；它不
覆盖"daemon 主进程对外完全无响应"这种更底层的场景——两者互补，不重叠。

### 1.2 方案：supervisor 主动探活，而不是被动等待退出

在 `run_supervisor()`/`run_foreground_supervisor()` 的主循环里，`proc.wait()`
从"一直阻塞直到子进程退出"改为**带超时的轮询等待**（`proc.wait(timeout=N)`
+ 捕获 `TimeoutExpired` 循环），每次超时窗口内顺带做一次轻量健康检查：

- 优先探 HTTP：`GET /v1/health`（daemon-mode 已有的健康检查端点，
  `DaemonClient.health_check()` 现成可用），超时时间要短（比如 2s），
  避免健康检查本身卡住 supervisor；
- HTTP 探测连续失败达到阈值（比如连续 3 次，每次间隔 10s，即 30s 内
  完全无响应）才判定为"疑似卡死"，不能偶尔一次慢响应就误判——正常场景下
  daemon 忙于处理长耗时任务时短暂延迟属于正常现象。

判定为卡死后：

1. **先诊断，再动手**（延续"先感知后恢复"的顺序）：记录当前状态——存活
   时长、最近一次成功健康检查的时间、`daemon.log` 最后 N 行、（如果
   Python 层面还能响应，一个更理想的手段是通过信号触发子进程自己转储
   一份线程栈快照，见 §1.3 的可选增强）；
2. 用一个新的 `restart_decision` 值 `"hang_killed"`（区别于崩溃场景的
   `"restarted"`/`"giveup"`）写入 `daemon_crash_history.jsonl`，`summary`
   文案明确写"进程存活但连续 N 次健康检查无响应，判定为卡死，已强制终止"，
   不能和"进程自己退出"的场景混为一谈——运维排查时这是两类完全不同的
   根因（一个要查"为什么会退出"，一个要查"为什么会卡住"）；
3. 强制终止子进程（Unix `SIGKILL`；Windows `TerminateProcess`），因为
   进程本身已经不响应正常信号（如果连 HTTP 都无响应，`SIGTERM` 大概率
   也无法被正常处理，直接跳过优雅关停尝试，不做无意义等待）；
4. 走和普通崩溃完全相同的告警 + 重启预算判定（§3.3 的判定顺序不变，
   只是"如何发现异常"这一步从"被动等待退出"变成了"主动探测无响应"）。

### 1.3 可选增强（评估后决定是否本阶段一并做）：卡死时的线程栈快照

Python 层面卡死（死锁、无限循环）时，如果子进程还能响应信号处理器，可以
在判定为"疑似卡死"但**强杀之前**，先发一个信号（比如 Unix 下用
`SIGUSR1`，Windows 没有等价信号，跳过）触发子进程注册的处理器调用
`faulthandler.dump_traceback()` 或手动遍历 `sys._current_frames()`，把所有
线程当前的调用栈写到一个专门文件（比如 `daemon_hang_snapshot.txt`）。这个
信号处理器需要足够轻量、不依赖任何可能本身就卡住的锁，否则处理器自己也
会挂起，等于没做。

如果子进程已经连信号都不响应（比如死锁在持有 GIL 的 C 扩展调用里），这一步
自然拿不到快照，直接跳过，不影响主流程——这一节是"锦上添花"，不是
判定卡死/强杀链路的必要环节。

---

## 2. 【🟡 中优先级】重启预算应该跨 supervisor 生命周期生效，重启后要验证真的可用

### 2.1 预算落盘，而不是只存在内存里

现状 `restart_timestamps` 是 `run_supervisor()` 函数内的局部变量，supervisor
进程本身如果因为某种原因异常退出（宿主机重启、被系统 OOM killer 一并
带走等，概率不高但不为零），下次 `daemon start` 又是全新的 supervisor 实例，
预算窗口从零开始。如果这是一个持续性问题（比如某次发版引入的 bug 导致
固定几分钟崩一次），用户看到的会是"好像一直在正常重启"，而不是"预算耗尽、
已放弃、需要人工介入"这条本该触发的告警，掩盖了真实的稳定性问题。

方案：预算判断不再只看内存里的 `restart_timestamps`，而是**每次判断前先
从 `daemon_crash_history.jsonl` 读最近 `window_seconds` 内 `restart_decision`
为 `"restarted"`/`"hang_killed"` 的记录数量**，与内存计数取较大值（内存
计数覆盖"本次 supervisor 生命周期内、文件还没来得及落盘的极端竞态"，
文件计数覆盖"跨 supervisor 生命周期"）。这样不需要新增文件，`daemon_crash_history.jsonl`
本来就是按次追加的完整历史，直接复用即可。

### 2.2 重启后追加一次健康验证，而不是傻等下一次退出

现状 supervisor 判定"这轮重启是否成功"的唯一依据是"新子进程有没有在
`proc.wait()` 里立刻返回"——不代表 HTTP 服务真的起来了。如果新进程卡在
初始化阶段（比如配置加载抛异常但被某处 `except Exception: pass` 吞掉、
或者某个初始化步骤本身就在等一个不会返回的资源），会呈现"新进程一直
存在但从未真正提供服务"的状态，且这个状态不会被当前逻辑感知到，要等到
它自己真的退出或者被 §1 的卡死探测抓到才会触发下一轮判断——期间用户
完全不知情，`daemon status` 显示"运行中"，但服务其实不可用。

方案：每次 Popen 新子进程后，除了现有的"等待退出"轮询，增加一个一次性的
启动健康检查（复用 `cmd_daemon_start(detach=True)` 现有的等待+health_check
逻辑，抽成公共函数两处共用）——如果子进程存活但在合理时间内（比如 30s，
和现有 `cmd_daemon_start` 等待逻辑一致）health check 一直不通过，直接按
§1.2 的"卡死"路径处理（记录 + 判定预算 + 决定是否再次重启），不必真的
等到超时窗口的多轮探测。这本质上是把"启动阶段的卡死"和"运行期间的卡死"
统一到同一套判定逻辑里，不需要单独再写一套。

---

## 3. 【🟡 中优先级】告警发出后要有"确保被看到"的兜底，而不是发出即结束

### 3.1 现状问题

崩溃告警模型是"写入独立存储一次 + 看板横幅展示未确认记录"。如果用户
当时没打开看板、也没在 `notification_config.json` 里配置外部渠道
（邮件/webhook 等），这条告警会安静地躺在 `daemon_crash_alerts.jsonl`
里，可能几天都不会被看到——尤其是配置了自动重启之后，daemon 表面看起来
"一直健康"，用户主动去查的动机反而更低了（这正是
`daemon_crash_recovery_and_alert_plan.md` §2.1 提到的"只做重启不做告警会
掩盖问题"的担忧，但即使做了告警，如果告警本身没人看到，效果是一样的）。

### 3.2 方案：未确认告警超时升级 + 交互时顺带提示

两个互相独立、成本都不高的手段：

1. **超时升级重复推送**：supervisor（或者一个独立的轻量后台检查，不一定
   要占用 supervisor 主循环）定期（比如每小时）扫一次未确认的崩溃告警，
   如果存在"创建时间超过 `daemon_crash_alert_escalation_hours`（默认比如
   1 小时）仍未 ack"的记录，重新广播一次到用户已配置的外部渠道（不是
   kanban——kanban 横幅本身是"恒真常驻"的，没有 ack 就会一直显示，不需要
   重复；这里升级的是"额外渠道"部分，让用户即使没开看板也能被动收到
   第二次提醒）。避免每小时都炸一遍：只在"第一次超时"这个时间点升级一次，
   而不是持续每小时重复骚扰，具体升级次数上限可配置（默认 1 次，即"发现
   超时未读，多提醒一次"，而非无限重复）。
2. **交互时顺带提示**：任何一次用户与 daemon 的正常交互入口（HTTP API
   收到普通请求时、`daemon connect` 建立连接时、REPL 里执行任意命令时）
   如果存在未确认的崩溃告警，顺带打印/返回一句轻量提示（"⚠️ 有 N 条未读
   的 daemon 崩溃记录，运行 `daemon status` 查看"），不打断正常流程，
   只是让用户在下一次自然接触系统时就能看到，不需要专门去查。这个提示
   本身也要做"每个会话/每次连接只提示一次"的节流，不能每次请求都刷一遍。

### 3.3 顺带清理：崩溃历史/告警文件的轮转

`daemon_crash_history.jsonl` 和 `daemon_crash_alerts.jsonl` 目前只追加不
清理。长期运行且偶发崩溃的部署（尤其是接入了 §1 的卡死探测后，理论上
触发频率会比纯"进程退出"场景更高）会让这两个文件无限增长。方案：
读取时如果文件超过一定行数/一定时间跨度（比如超过 1000 条或最早记录
超过 90 天），保留最近的窗口，旧记录做一次性归档（另存一份
`daemon_crash_history.archive.jsonl` 或直接截断，不需要保留完整历史，
排查用途上"最近 N 条"已经足够）。写入路径依然是简单追加，只在读取/展示
或者达到阈值时顺手做一次滚动清理，不引入额外的定时任务。

---

## 4. 【🟢 低优先级】崩溃诊断信息的颗粒度（暂不实施）

`_find_last_global_exception()` 目前按 pid 精确匹配、倒序取全局异常日志
最后一条。如果崩溃前短时间内发生了多次级联失败，只留最后一条可能不是
真正的根因（比如异常 A 触发了异常 B，B 才是最终导致进程退出的那一个，
但 A 才是根因）。理想情况下应该收集"崩溃前 N 秒内"的全部相关异常，或者
至少在摘要里提示"还有 K 条更早的异常记录，可能是根因链路的一部分"。

**暂不实施的原因**：这需要改动 `errors.py` 的日志结构（补充 unix
时间戳字段，当前只有 iso 字符串，见 `_find_last_global_exception()` 里
`since_ts` 参数暂时未使用的注释）才能做可靠的时间窗口过滤，改动面超出
本计划聚焦的"卡死检测 + 告警升级"范围，且实际价值（级联失败场景下
"看到最后一条异常"已经能覆盖大多数排查需求，多条异常的边际收益有限）
不足以justify单独为此改一份全局日志格式。留作独立的后续计划评估。

---

## 5. 配置项（新增，默认值倾向保守）

```json
{
  "daemon_hang_detection_enabled": true,
  "daemon_hang_check_interval_seconds": 10,
  "daemon_hang_check_timeout_seconds": 2,
  "daemon_hang_consecutive_failures": 3,
  "daemon_post_restart_health_check_seconds": 30,
  "daemon_crash_alert_escalation_hours": 1,
  "daemon_crash_history_max_entries": 1000
}
```

说明：

- `daemon_hang_detection_enabled`：卡死探测总开关，跟自动重启
  （`daemon_auto_restart_enabled`）是正交的两个开关——可以只开崩溃自愈
  不开卡死探测（比如担心健康检查本身引入额外的资源开销/误判风险，先
  只保留现有的"进程退出"感知能力），反之同理；
- `daemon_hang_check_interval_seconds`/`daemon_hang_check_timeout_seconds`/
  `daemon_hang_consecutive_failures`：探活的轮询间隔、单次超时、判定为
  卡死所需的连续失败次数，三者共同决定"最坏情况下卡死到被发现之间的
  延迟"（默认配置下约 30s：10s 间隔 × 3 次）；
- `daemon_post_restart_health_check_seconds`：重启后等待新进程 health
  check 通过的超时（§2.2），复用现有 `cmd_daemon_start` 等待逻辑的默认值
  （30s）保持一致；
- `daemon_crash_alert_escalation_hours`：未确认告警多久后升级重推一次
  （§3.2）；
- `daemon_crash_history_max_entries`：崩溃历史文件保留的最大条数（§3.3）。

与 `daemon_crash_recovery_and_alert_plan.md` 的 `daemon_restart_max_attempts`
等配置项放在一起，同样挂在 `HttpConfig` 下，走现成的
`load_nested_block_with_flat_compat` 自动装配。

---

## 6. 改进计划阶段

- **阶段一：卡死检测（不涉及重启预算/告警升级改动，独立可用）**
  - `proc.wait(timeout=N)` 轮询改造（`run_supervisor`/
    `run_foreground_supervisor` 两处）
  - 健康检查探测（复用 `DaemonClient.health_check()`）+ 连续失败计数
  - 新增 `restart_decision == "hang_killed"` 分支：诊断收集 + 强杀（跳过
    优雅关停尝试）+ 复用现有崩溃记录/告警落盘逻辑
  - `daemon status` 展示最近一次是否为"卡死判定"（区分于普通崩溃的
    文案）
  - 验证：手动模拟一个卡死子进程（比如一个只 `time.sleep()` 且不响应
    HTTP 的假子进程，或者真实子进程里临时加一个死循环触发点），确认能在
    预期时间窗口内被判定+强杀+记录，且不会被正常的长耗时任务（比如一次
    很慢的 LLM 调用）误判

- **阶段二：重启预算落盘 + 重启后健康验证**
  - 预算判断改为"内存计数与 `daemon_crash_history.jsonl` 回溯计数取较大值"
  - `cmd_daemon_start(detach=True)` 现有等待+health_check 逻辑抽成公共
    函数，supervisor 重启后复用
  - 重启后 health check 不通过时按 §1.2 卡死路径处理，不必等到探活轮询
    的多轮判定
  - 验证：模拟 supervisor 跨生命周期的预算延续（先手动写几条历史崩溃
    记录到 `daemon_crash_history.jsonl`，再跑一次新的 supervisor，确认
    预算判断把历史记录也算进去）；模拟"重启后新进程卡在初始化"场景，
    确认不会傻等到超时窗口才发现

- **阶段三：告警升级 + 历史文件轮转**
  - 未确认告警超时升级重推（一次性，非持续骚扰）
  - 交互入口（HTTP/REPL/connect）顺带提示未读崩溃告警数量，按会话节流
  - `daemon_crash_history.jsonl`/`daemon_crash_alerts.jsonl` 按条数/时间
    跨度做一次性归档清理
  - 验证：模拟一条超过 `daemon_crash_alert_escalation_hours` 仍未 ack 的
    告警，确认只升级一次不重复骚扰；模拟超过 `daemon_crash_history_max_entries`
    的历史文件，确认读取路径正确截断/归档且不影响现有 `daemon status`/
    看板展示逻辑

- **阶段四：文档与测试**
  - 更新 `docs/daemon-crash-recovery-guide.md`（补充卡死检测/告警升级
    章节）与本计划文档的实现记录
  - 关键测试用例：
    1. 子进程存活但 HTTP 完全无响应，达到连续失败阈值后被判定为卡死、
       强杀、记录（`restart_decision == "hang_killed"`），且不影响正常
       长耗时请求不被误判
    2. 重启预算的跨生命周期延续：预置历史崩溃记录，新 supervisor 实例
       据此判断预算已耗尽，直接 giveup，不再重启
    3. 重启后新进程 health check 一直不过，在超时时间内被判定为需要
       再次处理，而不是无限期等待
    4. 未确认告警超时升级只触发一次，不会每次检查都重复推送
    5. 崩溃历史文件超过配置的最大条数后，读取/展示逻辑正确截断，不影响
       `daemon status`/看板功能

每阶段完成后按约定更新对应文档并打包该阶段改动/新增文件供下载。

---

## 7. 实现记录

### 阶段一（已完成）

- `config/models.py::HttpConfig` 新增 4 个卡死探测配置项：
  `daemon_hang_detection_enabled`（默认 `True`）/
  `daemon_hang_check_interval_seconds`（默认 10.0）/
  `daemon_hang_check_timeout_seconds`（默认 2.0）/
  `daemon_hang_consecutive_failures`（默认 3）。与 `daemon_restart_*` 放在
  一起，走现成的 `load_nested_block_with_flat_compat` 自动装配，不需要改
  `loader.py`
- `cli/daemon.py::DaemonClient.health_check()` 新增 `timeout` 参数（默认
  仍是 3s，保持原有启动等待场景不变；supervisor 卡死探测会传更短的
  `daemon_hang_check_timeout_seconds`）
- `cli/daemon.py::record_daemon_crash()` 新增 `hang_reason` 参数：非
  `None` 时表示这条记录是"存活但无响应、被强杀"，跳过"进程退出原因"的
  推断逻辑（`_find_last_global_exception`/退出码符号判断对这种场景没有
  意义——进程是被外部强制终止的，不是自己抛异常退出），摘要文案明确写
  "判定为卡死"，与"意外退出"的崩溃摘要区分开，避免运维排查时混为一谈；
  `last_exception` 字段固定为 `None`
- `cli/daemon.py::_force_kill_process()`（新函数）：跨平台强杀（Unix
  `SIGKILL`/Windows `TerminateProcess`），跳过优雅关停尝试——进程已经对
  HTTP 无响应，`SIGTERM` 大概率也处理不了
- `cli/daemon_supervisor.py::_wait_child()`（新函数）：把原来"一直阻塞
  直到子进程退出"的 `proc.wait()` 改造为"带超时的轮询等待 + 探活"：
  - `http_port` 为 `None` 或 `hang_detection_enabled=False` 时退化为原
    行为（纯 `proc.wait()`），保证阶段一之前的调用方（比如没能读到端口
    配置的场景）完全不受影响；
  - 否则每轮 `proc.wait(timeout=interval)` 超时后调用
    `DaemonClient.health_check(timeout=...)` 探测一次，探测成功则把
    连续失败计数清零（避免正常场景下偶尔一次慢响应被误判），连续失败
    达到阈值后判定为卡死，调用 `_force_kill_process` 强杀并返回
    `(None, True, reason)` 供调用方走"卡死"分支
- `run_supervisor()`/`run_foreground_supervisor()` 均已接入
  `_wait_child()`：非 `was_hang` 分支的行为与阶段一之前完全一致；
  `was_hang` 分支复用与崩溃场景相同的重启预算/退避判定，只是
  `restart_decision` 用 `"hang_killed"` 而不是 `"no_restart"`
  （预算耗尽时两者统一走 `"giveup"`，保持 `daemon status`/`_print_crash_
  summary` 的既有展示逻辑不需要区分来源）；前台模式的打印文案（"Crashed"
  vs "Hung"）据此区分
- `_main()` CLI 参数打通：`--http-port`/`--hang-detection`/
  `--hang-check-interval`/`--hang-check-timeout`/
  `--hang-consecutive-failures`
- `cli/daemon.py::cmd_daemon_start()` 前台/后台两个分支均读取
  `HttpConfig.daemon_hang_*` 配置并传给 supervisor（前台直接函数调用
  传参；后台通过命令行参数传给 supervisor 子进程）
- `cli/daemon.py::cmd_daemon_status()`：`Supervised: yes` 那一行补充
  "卡死探测已关闭"提示（仅当配置关闭时显示）；`_print_crash_summary()`
  区分展示 `Last crash`/`Last hang (killed)`
- 测试：`tests/test_daemon_crash_recovery.py` 新增 `TestHangDetection`
  （4 用例）：
  1. 子进程存活但健康检查持续失败，被判定为卡死、强杀，
     `restart_decision == "hang_killed"`，`exit_code`/`last_exception`
     均为空，摘要文案包含"卡死"与"健康检查无响应"
  2. 健康检查间歇性成功（每 3 次成功 1 次）时连续失败计数被正确清零，
     不会被误判为卡死——用后台线程跑 supervisor，主线程验证在一段时间
     内没有产生任何崩溃记录
  3. `hang_detection_enabled=False` 时完全退化为原有"纯等待退出"行为，
     不会因为 `health_check` mock 返回失败就被误杀
  4. `http_port=None` 时同样退化为不探活，不因为拿不到端口而报错/误判
  - 全量 `tests/test_daemon_crash_recovery.py` 31 passed（阶段一~四共 27
    + 卡死检测阶段一新增 4）
  - 额外验证：`pytest -k "daemon or config"`（排除环境本身缺 `rich`/
    `starlette`/`json_repair`/`_flock` 等依赖导致的收集错误后）266
    passed，确认本次改动对 daemon/config 相关测试无回归；仅有的 5 个
    失败均为 `ModuleNotFoundError: No module named 'json_repair'`，与本
    次改动无关（`role_agents/verdict.py` 的既有依赖缺失，沙盒环境未装）
- 未做（留给后续阶段）：§1.3 的线程栈快照（可选增强，本阶段未做，跳过
  信号处理器方案的原因见 §1.3 本身"锦上添花，不是必要环节"）；§2 重启
  预算落盘/重启后健康验证；§3 告警升级/历史文件轮转

### 阶段二（已完成）

- `cli/daemon.py::count_recent_restart_events()`（新函数）：从
  `daemon_crash_history.jsonl` 回溯统计最近 `window_seconds` 内
  `restart_decision` 为 `"restarted"`/`"hang_killed"` 的记录数量（这两个
  值代表"确实又拉起过一次新进程"，`giveup`/`no_restart` 不算）。读取/
  解析失败时兜底返回 0，不阻断主流程——预算判断退化为只看内存计数，
  等价于阶段二之前的行为
- `cli/daemon_supervisor.py::run_supervisor()`/`run_foreground_supervisor()`
  §2.1：预算判断从"只看内存里的 `restart_timestamps`"改为"内存计数与
  `count_recent_restart_events()` 回溯计数取较大值"（`file_count + 1`，
  `+1` 补上"这次事件本身"——文件计数统计发生在这次事件写入历史文件
  之前，天然不包含它，而内存计数在这一步已经 `append` 过了）。这样
  supervisor 自身如果异常退出后重新拉起（预算内存归零），只要历史文件
  还在，跨生命周期的持续性崩溃/卡死仍然会被正确识别为"预算已耗尽"，
  不会被"看起来一直在正常重启"掩盖
- `cli/daemon_supervisor.py::_wait_child()` 新增
  `post_restart_health_check_seconds` 参数（§2.2）：每次 Popen 新子进程
  后，在进入常规探活轮询之前，先给它这么长时间证明自己真的把 HTTP 服务
  起来了（复用同一个 `DaemonClient.health_check()`）：
  - 窗口期内子进程自己退出（比如配置错误直接崩了）→ 按正常"进程退出"
    处理，不算卡死——强杀语义只适用于"进程还活着但不响应"的场景；
  - 窗口期内探测到一次健康检查成功 → 正常进入下面的常规探活轮询；
  - 窗口期耗尽仍未通过 → 按卡死处理（`restart_decision="hang_killed"`，
    摘要文案区别于常规卡死："重启后 Ns 内未通过健康检查（新进程可能卡在
    初始化阶段）"），复用与常规卡死完全相同的记录+告警+预算判定逻辑，
    不需要单独再写一套判定分支
  - 函数级默认值刻意设为 `0.0`（关闭），而不是配置项的真实默认值
    30.0——避免阶段一写的既有测试（显式传了 `hang_detection_enabled=True`
    但没有显式传这个新参数）意外触发这项新检查、拖慢/改变原有断言；
    `HttpConfig.daemon_post_restart_health_check_seconds`（默认 30.0，与
    `cmd_daemon_start` 现有启动等待逻辑的默认超时保持一致）才是用户实际
    会用到的默认值，由 `cmd_daemon_start`/`_main()` 显式传入
- `config/models.py::HttpConfig` 新增 `daemon_post_restart_health_check_seconds`
  （默认 30.0），跟其它 `daemon_hang_*`/`daemon_restart_*` 配置项放在一起
- `cli/daemon.py::cmd_daemon_start()` 前台/后台两个分支均读取新配置项并
  传给 supervisor；`_main()` CLI 新增 `--post-restart-health-check-seconds`
  参数（默认 30.0，与配置项默认值一致）
- 测试：`tests/test_daemon_crash_recovery.py` 新增
  `TestRestartBudgetPersistence`（3 用例：`count_recent_restart_events()`
  只统计 `restarted`/`hang_killed` 且遵守窗口范围、预算跨 supervisor
  生命周期正确延续（预置历史记录后新实例直接 giveup，不会先重启一次再
  耗尽）、历史文件缺失时预算判断退化为纯内存计数不受影响）与
  `TestPostRestartHealthCheck`（4 用例：新进程持续不健康被判定为卡死并
  强杀、健康检查一次成功后正常进入常规轮询、窗口期内子进程自己退出按
  普通"进程退出"处理而非卡死、函数默认值 0.0 完全跳过这项检查不拖慢
  既有阶段一测试）
  - 全量 `tests/test_daemon_crash_recovery.py` 38 passed（阶段一~四共 27
    + 卡死检测阶段一 4 + 阶段二新增 7）
  - `pytest -k "daemon or config"`（同样排除环境本身缺依赖导致的收集
    错误后）273 passed，无回归；仍是同样的 5 个 `json_repair` 缺失导致
    的失败，与本次改动无关
- 未做（留给阶段三）：§3 告警发出后的"确保被看到"升级机制（未确认告警
  超时重推 + 交互入口顺带提示）；`daemon_crash_history.jsonl`/
  `daemon_crash_alerts.jsonl` 的按条数/时间跨度轮转清理

### 阶段三（已完成）

- `notification/daemon_crash_store.py` 新增三个函数：
  - `list_stale_unacknowledged_alerts(paths, escalation_hours, max_escalations)`：
    筛选"创建超过 escalation_hours 仍未确认、且升级次数还没到上限"的告警；
    `escalation_count` 字段不存在时按 0 处理（阶段一~二写入的老数据天然
    符合"还没升级过"）
  - `mark_escalated(paths, alert_id)`：`escalation_count` 加 1 并记录
    `last_escalated_at`，整体重写文件，跟 `acknowledge_crash_alert` 的
    处理方式一致
  - `rotate_crash_alerts_if_needed(paths, max_entries)`（§3.3）：超过上限
    时保留最近 N 条，返回本次裁剪掉的条数；跟 `append_crash_alert` 内部
    固定 500 条的兜底截断不冲突，谁先命中谁生效
- `cli/daemon.py::broadcast_crash_alert_to_external_channels()`（新函数，
  从 `record_daemon_crash()` 里抽出来的公共部分）：把"跳过 kanban、只广播
  已启用的其它外部渠道"这段逻辑独立出来，供崩溃发生时的首次广播和升级
  重推共用，不需要重写一遍
- `record_daemon_crash()`：
  - 历史文件截断从写死的 500 条改为读 `HttpConfig.daemon_crash_history_
    max_entries`（默认 1000），读取失败时退化回 500，不阻断崩溃记录本身
    必须尽力落盘成功这一主线；
  - 告警落盘后顺带调用 `rotate_crash_alerts_if_needed()`，同样按配置的
    最大条数轮转
- `notification/daemon_crash_escalation.py`（新模块）：
  - `check_and_escalate_crash_alerts(project_root, escalation_hours,
    max_escalations)`：扫描一次未确认告警，超时的重新广播到外部渠道并
    打上升级标记，返回本次实际升级的条数；读取/广播失败不抛异常
  - `CrashAlertEscalationThread`：独立后台线程（daemon=True），按固定
    轮询间隔调用上面的检查函数；轮询间隔取
    `min(1800, max(300, escalation_hours * 3600 / 2))`（escalation_hours
    的一半，下限 5 分钟、上限 30 分钟），不单独开一个"轮询间隔"配置项——
    用户只关心"多久没读会被提醒"这一个语义；与
    `evolution/scheduler_heartbeat.py::SchedulerHeartbeat` 是同一种
    "独立于主循环"模式，跟 supervisor/卡死探测是否启用完全无关
- `api/server.py::HttpServer`：`__init__` 里构造完 `SchedulerHeartbeat`
  之后紧接着构造并启动 `CrashAlertEscalationThread`
  （`daemon_crash_alert_escalation_hours <= 0` 时不启动，等价于关闭这项
  功能）；`stop()` 里对称地调用 `.stop()`
- `api/routes.py` 新增只读端点 `GET /v1/daemon/crash_alerts/pending_count`
  （§3.2"交互时顺带提示"用）：只返回未确认崩溃/卡死告警条数，不返回
  具体内容——具体内容走 `daemon status`（本地直读文件）或看板横幅；
  `cli/daemon.py::DaemonClient.get_pending_crash_alerts_count()` 对接
- `cli/daemon.py::_print_pending_crash_alert_notice_connected()` /
  `cli/repl.py::_print_startup_digest_and_advisor()` 内新增一段：`daemon
  connect` 建立连接时（走 HTTP 端点）/ 本地 REPL 启动时（直接读文件）
  分别顺带打印一句"有 N 条未读的 daemon 崩溃/卡死记录"提示，天然按
  "每次连接/每次启动只提示一次"节流（提示只在连接/启动那一刻打印一次，
  不在后续交互里重复调用），失败静默跳过不影响正常启动/连接流程
- `config/models.py::HttpConfig` 新增 `daemon_crash_alert_escalation_hours`
  （默认 1.0）、`daemon_crash_history_max_entries`（默认 1000）
- 测试：`tests/test_daemon_crash_recovery.py` 新增
  `TestCrashAlertEscalationStore`（5 用例：`list_stale_unacknowledged_
  alerts` 正确遵守时间窗口和已确认过滤、`mark_escalated` 递增计数且达到
  上限后不再被选中、升级未知 alert_id 返回 False、`rotate_crash_alerts_
  if_needed` 保留最近 N 条、未超限时返回 0）、`TestCrashHistoryRotation`
  （1 用例：`record_daemon_crash` 遵守配置的历史文件最大条数而不是写死
  500）、`TestCrashAlertEscalationCheck`（3 用例：`check_and_escalate_
  crash_alerts` 只升级超时未读的告警并正确打标记、没有待升级告警时返回
  0、`CrashAlertEscalationThread` 按轮询间隔真的触发检查且能被干净地
  停止）
  - 全量 `tests/test_daemon_crash_recovery.py` 47 passed（阶段一~四共 27
    + 卡死检测阶段一 4 + 阶段二 7 + 阶段三新增 9）
  - `pytest -k "daemon or config or server or routes or repl"`（排除环境
    本身缺依赖导致收集错误的几个测试文件后）额外发现 21 个失败，逐一
    抽查确认均为环境本身缺失依赖（`anthropic` SDK 未装）或与本次改动
    完全无关的既有测试基础设施问题（比如 `test_goal_execution_spec_
    kanban_routes.py` 依赖 `request.app.state.async_jobs` 但测试 fixture
    没有设置，属于该测试文件自身的既有 bug）——用"临时摘除本次新增的
    `/v1/daemon/crash_alerts/pending_count` 端点后重跑同一个失败用例"的
    方式验证过，摘除前后失败结果完全一致，确认与本次改动无关；445
    passed，daemon/config/server/routes/repl 相关的核心测试无回归
- 未做（留给阶段四）：`daemon status` 展示 `escalation_count`（目前只
  展示未确认数，不单独展示"这条已经升级提醒过几次"）；`daemon status`
  展示崩溃/告警文件的轮转历史（比如"最近一次因为超过 1000 条被裁剪掉了
  N 条"）——都是锦上添花的展示细节，不影响核心的升级/轮转机制本身是否
  生效

