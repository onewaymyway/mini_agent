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
