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

一个直观的验证方法（模拟卡死而不是崩溃——进程存活但故意不响应 HTTP，
比较取巧的办法是临时把 `daemon_hang_check_interval_seconds`/
`daemon_hang_consecutive_failures` 调小、再用调试器/断点手段让 daemon
主线程卡住）：观察 `daemon.log`/`daemon status` 是否在约
`interval × consecutive_failures` 秒内出现 `hang_killed` 记录，而不是
被误判为普通崩溃或者完全没反应。

### 尚未覆盖（留给后续阶段，见计划文档 §2/§3）

- 重启预算目前仍是内存态，supervisor 自身异常退出后预算归零重来；
- 重启后只是"子进程没有立刻退出"就算成功，不会主动验证 HTTP 服务真的
  起来了；
- 崩溃/卡死告警发出后没有"确保被看到"的超时升级机制；
- `daemon_crash_history.jsonl`/`daemon_crash_alerts.jsonl` 仍然只追加，
  没有按条数/时间跨度做轮转清理。
