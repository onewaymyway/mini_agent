# mini-agent Daemon 崩溃自愈与告警：改进计划

> 聚焦范围：`cli/daemon.py`（daemon start/stop/status）、`cli/app.py`
> （`--daemon-mode` 主循环）、`errors.py`（全局异常日志）、
> `notification/*`（通知分发）。
> 触发背景：daemon 进程在执行过程中偶发崩溃、整个进程退出后无人知晓、
> 也不会自动恢复，用户往往是"过了一段时间才发现 daemon 早就没了"。

---

## 1. 背景

daemon 是 mini-agent 的核心常驻进程——HTTP API、cron 调度、goal 执行、
持久 Worker 全部挂在这一个进程上。它一旦意外退出：

1. 所有正在进行的 cron 任务、goal 执行、持久 Worker 状态当场中断；
2. **用户完全没有感知**——除非主动执行 `daemon status` 或尝试连接才会
   发现进程已经不在了；
3. 现有 `errors.py` 的全局异常日志（`~/.agent/logs/error.jsonl`）虽然能
   记录"发生过什么异常"，但这是一份面向事后排查的原始日志，不会主动
   推送给用户，普通用户也不会没事去翻这个文件；
4. 进程死亡后不会自动重启，需要用户手动 `daemon start`。

当前代码里 `daemon start --detach` 只是 `subprocess.Popen` 之后就返回，
父进程不再管子进程的死活；前台（不带 `--detach`）模式在 POSIX 下用
`os.execv` 做真正的进程替换，一旦替换后的进程崩了，原来那个 shell 直接
拿到一个非零退出码，同样没有任何自愈或告警能力。

## 2. 为什么要改 / 核心理念

### 2.1 "崩溃可恢复" 和 "崩溃可感知" 是两件不同的事，都要做

只做自动重启：daemon 表面上"看起来一直健康"，但用户完全不知道它其实
崩溃过、崩溃了几次、为什么崩溃——这会掩盖真实存在的稳定性问题，也让
用户对系统状态失去信任（下次崩溃如果超出重启预算彻底死掉，用户毫无
心理准备）。

只做告警不做重启：每次崩溃都需要用户手动介入才能恢复，与"daemon 是
常驻自动化基础设施"的定位相悖——cron/goal 这类无人值守场景下，用户
可能几小时甚至几天才会看一次，这段时间 daemon 完全瘫痪。

所以两者要同时做，并且顺序明确：**先感知、后恢复**——检测到崩溃后，
第一步永远是收集诊断信息、生成崩溃摘要，**在决定是否重启之前**先把这
条信息送出去，不能因为重启流程本身出问题（比如重启预算耗尽提前
return）而漏发告警。

### 2.2 崩溃告警需要独立于常规通知的"专门通道"

项目里已经有一套通用的 `NotificationDispatcher`（`notification/dispatcher.py`），
支持多渠道分发 + kanban 恒真兜底渠道。但现有走这套通道的内容
（`watchlist_report`、goal 推进提醒等）语义上是"周期性汇总/建议性提醒"，
用户可以慢慢看、可以批量 ack、可以调低优先级甚至忽略。

daemon 崩溃是完全不同性质的信息：

- **时效性极强**：用户需要尽快知道"服务不可用"这件事本身；
- **不该和其它通知混在一起被"淹没"**：如果和几十条 watchlist 汇报排在
  一起，很容易被当成普通消息划过去、误 ack；
- **内容结构不同**：需要展示崩溃时间、退出方式、最近日志片段、重启
  状态，这些字段常规通知 payload 里没有对应位置。

因此崩溃告警不复用 `watchlist_report`/`goal_advance` 这类现有 source，
而是新增一个专门的 `daemon_crash` 类别 + 专门的独立存储文件，在看板上
单独一块高优先级展示位置（而不是塞进"关注与通知"tab 的通用列表里被
分类 tab 折叠掉），并且**默认不可关闭**（比照现有 kanban 渠道"恒真兜底"
的先例，崩溃告警至少要保证落到看板这一件事不能被配置项关掉）。

### 2.3 前台模式同样需要自愈能力

现状里前台（非 `--detach`）用 `os.execv` 做真正的进程替换，这个设计的
初衷是"避免多一层父子进程导致的 Ctrl-C 转发/控制台归属问题"（见
`daemon.py` 里那段关于 Windows 控制台抢输入的详细注释）。但代价是一旦
替换后的进程崩溃，原进程已经不存在了，没有任何主体能感知或重启。

用户当前明确要求前台模式也要能自动重启，这意味着必须放弃"完全替换"
的模型，改为父进程常驻、子进程可替换的监控者模型——即前台模式统一
改用 Windows 分支已经在用的 `Popen + wait()` 模式（原来只有 Windows
因为没有 `execv` 等价物才这么做），POSIX 也改成一致的模式。这是一个
行为收敛，不是新引入复杂度：两个平台从"两套不同模型"变成"一套模型"，
只是 POSIX 侧从"进程替换"改为"父进程常驻监控"。

代价：前台模式从"当前 shell 里跑的就是 daemon 自己"变成"当前 shell 里
跑的是一个 supervisor，daemon 是它的子进程"。Ctrl-C 转发、控制台归属
这些已经在 Windows 分支上验证过的问题，需要原样搬到 POSIX 分支。

## 3. 具体方案

### 3.1 崩溃检测与信息收集

新增运行状态标记文件 `.agent/daemon_run_state.json`，daemon 子进程启动
时写入：

```json
{"pid": 1234, "started_at": 1735500000.0, "status": "running"}
```

**只有预期内的退出路径**才会把 `status` 改写为 `"stopped_by_user"`：

- HTTP `POST /v1/shutdown` 触发的 graceful path；
- 收到 SIGTERM / SIGINT / SIGBREAK 的 `_shutdown_handler`；
- 这两者最终都会走到 `app.py` daemon-mode 主循环现有的 `finally` 清理块，
  在里面追加这一步。

监控者（父进程 supervisor，见 3.2）在子进程退出后读这个文件：

- `status == "stopped_by_user"` → 预期停止，不重启，不告警（或只发一条
  低优先级的"daemon 已停止"信息，不算崩溃）；
- 文件仍是 `"running"`，或文件读取失败/不存在（比如进程在写文件之前就
  挂了）→ 判定为**非预期退出（崩溃）**。

崩溃发生后，supervisor 立即收集诊断信息：

- 退出码（Unix 下负数表示被信号杀死，比如 `-9` 高度提示 OOM）；
- 存活时长（`now - started_at`）；
- `daemon.log` 最后 N 行（复用当前 `cmd_daemon_start` 里已有的"打印日志
  尾部"逻辑，抽成公共函数）；
- 最近一条全局异常记录（`~/.agent/logs/error.jsonl` 按时间倒序取一条，
  如果崩溃前确实有 Python 异常被 excepthook 捕获到，这条通常就是直接
  原因；如果没有对应记录，说明是 OOM/native crash 这类 Python 异常路径
  完全没走到的情况，摘要里要明确写"未捕获到 Python 异常，可能是外部
  信号杀死或底层崩溃"，不能留空更不能瞎猜）。

汇总写入新增的 `.agent/daemon_crash_history.jsonl`（一行一条，独立于
`error.jsonl`——`error.jsonl` 是"单条异常"粒度，这份文件是"单次进程
生命周期结束"粒度的汇总，两者定位不同，不合并）：

```json
{
  "timestamp": "...", "pid": 1234, "exit_code": -9,
  "uptime_seconds": 3821, "restart_attempt": 2,
  "last_exception": {"type": "...", "message": "...", "where": "..."} ,
  "log_tail": ["...daemon.log 最后 30 行..."],
  "restart_decision": "restarted | giveup | stopped_by_user"
}
```

### 3.2 通道专门化：`daemon_crash` 告警

新增 `notification/reports_store.py` 同级的独立存储
`.agent/notification/daemon_crash_alerts.jsonl`，以及对应的
`GET/POST /v1/daemon/crash_alerts` 系列端点（列表 + ack），风格与现有
`watchlist_report` 的 `/v1/notifications/*` 一致但物理隔离，不共用同一
个列表接口，避免被现有分类 tab 的筛选逻辑吞掉。

看板新增一个**独立的、非可关闭的**"⚠️ Daemon 崩溃"提示位——不是"关注
与通知"tab 下的一个分类筛选项，而是仅当存在未 ack 的崩溃记录时，在看板
顶部/侧边栏常驻展示一条摘要横幅（标题+崩溃时间+原因摘要+"查看详情"），
类似现有"系统状态哨兵"面板的展示优先级，但专门只服务崩溃这一类事件。

摘要文案模板（示例）：

```
⚠️ Daemon 于 08-29 14:32 意外退出（存活 1h03m，退出信号 SIGKILL，
疑似 OOM）。已自动重启（第 2 次 / 预算 5 次）。
[查看最近日志] [查看历史崩溃记录]
```

同时仍然复用现有 `NotificationDispatcher.dispatch()` 走一遍已配置的
外部渠道（邮件/webhook 等，如果用户配置了的话）——但这是"顺带广播"，
kanban 专门横幅才是保证用户一定能看到的主路径，两者不是互斥关系。

### 3.3 Supervisor：自动重启（后台 + 前台统一模型）

`daemon start`（无论带不带 `--detach`）不再由父进程直接 `execv`/一次性
`Popen` 后撒手，而是父进程本身成为一个 supervisor 循环：

```
supervisor 循环：
  1. Popen 启动真正的 daemon 子进程（原有 base_cmd 不变）
  2. proc.wait() 阻塞直到子进程退出
  3. 读 daemon_run_state.json 判定退出类型（见 3.1）
  4. status == "stopped_by_user" → supervisor 退出，不重启
  5. 判定为崩溃：
       a. 收集诊断信息，写 daemon_crash_history.jsonl（3.1）
       b. 发送 daemon_crash 告警（3.2）—— 在决定是否重启之前就发送，
          保证告警不受重启预算逻辑影响
       c. 检查重启预算（滑动窗口内重启次数），未超预算：
            按退避序列（1s/2s/4s/8s/16s/30s/60s，封顶）等待后回到步骤 1
          超预算：写 restart_decision="giveup"，追加一条"已放弃自动
            重启，需要人工介入"的告警，supervisor 退出
```

**后台（`--detach`）**：supervisor 自身也 detach（与当前 `--detach` 的
"脱离控制台"处理方式一致），写独立的 `.agent/daemon_supervisor.pid`；
CLI 命令本身依然立即返回（等待逻辑不变，只是等的是 supervisor 拉起的
第一个子进程就绪）。

**前台（不带 `--detach`）**：supervisor 就是用户手上这个终端进程本身，
不再 `execv` 替换自己。POSIX 分支收敛为与 Windows 分支一致的
`Popen + wait()` 模型（3.3 节末尾提到的行为收敛）：
- Ctrl-C（`KeyboardInterrupt`）在 supervisor 层面捕获，转发信号给子
  进程（复用 Windows 分支已有的转发写法，POSIX 下发 SIGINT/SIGTERM），
  同时**在转发前主动标记这是用户停止意图**（不依赖子进程自己来得及写
  `stopped_by_user`——如果子进程收到信号后处理不过来直接被杀，标记
  应该由 supervisor 侧兜底写一份，防止误判为崩溃）；
- 崩溃自动重启后，新子进程需要重新走一遍 `--daemon-attach-console` 的
  接管逻辑（终端 simple_mode、输入循环等现有机制原样复用，因为控制台
  本身没有变化，变的只是背后的子进程 PID）。

### 3.4 `daemon stop` / `daemon status` 联动

`cmd_daemon_stop`：现有三级优雅关停（HTTP → 信号 → 强杀）保持不变，
新增的动作是**在第一步发起前，先确保停止意图会被 supervisor 感知**——
即便走到第 3 级强杀（子进程根本来不及自己写 `stopped_by_user`），
`daemon stop` 命令本身也要直接把 `daemon_run_state.json` 标记为
`stopped_by_user`（由发起停止的一方兜底写，不完全依赖子进程自己写
成功），确保 supervisor 无论如何都不会把这次停止误判成崩溃。

停止流程末尾追加：确认 `daemon_supervisor.pid` 对应的 supervisor 进程
也已退出（超时兜底强杀），避免 supervisor 残留成为孤儿进程继续监控一个
已经不存在的子进程。

`cmd_daemon_status`：展示内容新增：
- 是否处于 supervisor 管理下（PID、已重启次数/预算）；
- 最近一次崩溃时间 + 原因摘要（读 `daemon_crash_history.jsonl` 最后
  一条）；
- 若已达重启预算上限（`restart_decision == "giveup"`），明确提示"自动
  重启已放弃，需要手动 `daemon start`"。

### 3.5 配置项（默认值待定，倾向于默认开启但预算保守）

```json
{
  "daemon_auto_restart_enabled": true,
  "daemon_restart_max_attempts": 5,
  "daemon_restart_window_seconds": 600,
  "daemon_restart_backoff_seconds": [1, 2, 4, 8, 16, 30, 60],
  "daemon_crash_alert_channels": ["kanban"]
}
```

说明：与上一版方案（前台不参与自动重启）不同，这版明确前台也要有自愈
能力，因此不再区分"前台默认不启用"，而是统一由
`daemon_auto_restart_enabled` 一个开关控制两种模式。`daemon_crash_alert_channels`
里的 `kanban` 视为恒真兜底（同 `NotificationConfig.ALWAYS_ON_CHANNEL`
的先例），配置项只用来控制"额外再广播到哪些外部渠道"，不能通过配置
把 kanban 那条摘要横幅关掉。

## 4. 改进计划阶段

- **阶段一：崩溃信息持久化 + 告警通道**（不涉及自动重启，独立可用，
  风险最低）
  - `daemon_run_state.json` 读写 + daemon-mode `finally` 块接入
    `stopped_by_user` 标记
  - `daemon_crash_history.jsonl` 落盘逻辑（含 exit_code/uptime/日志尾部/
    最近异常关联）
  - 独立的 `daemon_crash` 告警存储 + `/v1/daemon/crash_alerts` 端点 +
    看板专门横幅
  - `daemon status` 展示最近崩溃摘要
  - 本阶段完成后：手动 kill -9 模拟崩溃，验证"进程死亡后看板能看到
    崩溃摘要"这条链路走通，但此时进程还不会自动重启

- **阶段二：Supervisor 骨架 + 重启预算/退避**
  - 后台 `--detach` 模式接入 supervisor（`daemon_supervisor.pid` +
    重启循环 + 预算/退避）
  - 崩溃 → 阶段一告警链路 → 判定重启预算 → 重启或放弃，全流程打通
  - `daemon status` 增补 supervisor 相关字段

- **阶段三：前台模式收敛 + `daemon stop` 联动**
  - POSIX 前台分支从 `execv` 改为 `Popen + wait()` supervisor 模型，
    与 Windows 分支统一
  - Ctrl-C / 信号转发的停止意图兜底标记
  - `daemon stop` 兜底写 `stopped_by_user`，并确认 supervisor 一并退出

- **阶段四：文档与测试**
  - 更新 `docs/`（用户可见的 daemon 使用指南）与本设计文档的实现记录
  - 关键测试用例：
    1. 模拟崩溃（子进程 `os.kill(pid, SIGKILL)`）触发告警 + 自动重启
    2. `daemon stop` 全程不触发任何重启或误报崩溃
    3. 重启预算耗尽后正确停止重试并发出"已放弃"告警
    4. 前台模式下 Ctrl-C 视为用户停止，不触发重启
    5. 崩溃告警即使在重启逻辑本身抛异常的情况下依然已经发出（验证
       "先感知后恢复"的顺序保证）

每阶段完成后按约定更新对应文档并打包该阶段改动/新增文件供下载。

---

## 5. 实现记录

### 阶段一（已完成）

- `daemon_run_state.json`（`cli/daemon.py::_write_run_state`/`_read_run_state`/
  `mark_stopped_by_user`）：daemon-mode 启动即写 `running`；`app.py` 用一个
  局部 `_crashed_out` 标志精确标记"finally 是否由异常传播触发"，只有非崩溃
  路径才改写成 `stopped_by_user`；`daemon stop` 在动手停止前（甚至第 1 级
  HTTP 优雅关停发起之前）就先兜底写这个标记，不依赖子进程自己写成功
- `record_daemon_crash()`（`cli/daemon.py`）：收集退出码/存活时长/日志尾部
  30 行/关联的最近一次全局异常（按 pid 精确匹配 `~/.agent/logs/error.jsonl`
  最后 200 行），生成人类可读摘要，写入 `daemon_crash_history.jsonl`
- `notification/daemon_crash_store.py`（新文件）：独立存储
  `daemon_crash_alerts.jsonl`，`append`/`list`/`ack`/`count`
- 崩溃告警广播**刻意不走** `NotificationDispatcher.dispatch()`——它会无条件
  强制带上 `ALWAYS_ON_CHANNEL="kanban"`，而 `KanbanChannel` 写的正是
  `reports.jsonl`（watchlist_report 那套通用列表），与本方案"崩溃告警要
  独立、不跟常规通知混在一起"的设计初衷矛盾。改为手动遍历用户在
  `notification_config.json` 里配置的非 kanban 渠道逐个广播
- `cli/daemon_supervisor.py`（新文件）：`daemon start --detach` 改为拉起这个
  supervisor 进程（通过 `MINI_AGENT_SUPERVISOR_CHILD_ARGV` 环境变量传子进程
  启动参数），supervisor 循环 Popen 子进程 → wait → 读 run_state 判定崩溃/
  预期停止 → 崩溃时记录+告警。**阶段一 `auto_restart` 固定传 `False`**——
  循环结构已经按最终形态实现（重启预算滑动窗口、退避序列都写好了），阶段
  二只需要把这个参数接上配置项、默认打开，不需要重写循环
- 副作用（正面）：原本"daemon 启动阶段就崩溃"（比如配置错误）会在
  `cmd_daemon_start` 的等待循环里被当场感知（supervisor 退出且子进程也不
  在 → 判定失败），现在这条路径复用同一套崩溃记录机制，`daemon start`
  失败时能顺带提示"崩溃详情已记录在 daemon_crash_history.jsonl"
- `cli/daemon.py::cmd_daemon_stop`/`cmd_daemon_status` 相应联动：`stop` 收尾
  确认 supervisor 也已退出（超时兜底强杀）；`status` 展示是否处于 supervisor
  监控下 + 最近一次崩溃摘要
- `api/routes.py`：`GET /v1/daemon/crash_alerts`、
  `GET /v1/daemon/crash_alerts/history`、`POST /v1/daemon/crash_alerts/{id}/ack`
- `apps/mini_agent_kanban/client.py` + `app.py`：顶栏常驻红色横幅
  （`st.error`，非折叠展示），独立于"系统状态哨兵"面板；每条告警可展开看
  日志尾部/关联异常/重启决策，附"标记已读"按钮
- 测试：`tests/test_daemon_crash_recovery.py`（16 用例，覆盖 run_state 标记、
  崩溃诊断收集、独立存储 CRUD、supervisor 崩溃/预期停止判定、supervisor 自身
  PID 文件生命周期）；daemon 相关测试全量回归 98 passed（82 原有 + 16 新增），
  另确认 10 个跟本次改动无关的沙盒环境预置失败（缺 fastapi/streamlit 相关
  fixture、未触碰的 external_input/goal_execution_spec 模块）不受影响
- 未做（留给后续阶段）：前台模式的 `execv`→`Popen+wait` 收敛（阶段三）；
  `auto_restart`/`daemon_restart_max_attempts` 等配置项接入（阶段二）

### 阶段二（已完成）

- `config/models.py::HttpConfig` 新增 `daemon_auto_restart_enabled`（默认
  `True`）/`daemon_restart_max_attempts`（默认 5）/
  `daemon_restart_window_seconds`（默认 600）/`daemon_restart_backoff_seconds`
  （默认 `[1,2,4,8,16,30,60]`）。跟其它 daemon/http 相关配置放在一起，走
  `load_nested_block_with_flat_compat` 现有的自动装配机制——不需要改
  `loader.py`，`agent_config.json` 的 `"http": {"daemon_auto_restart_enabled": false}`
  即可覆盖
- `cmd_daemon_start(detach=True)` 启动前调用 `load_config()` 读取这几项，
  转成 `--auto-restart --max-attempts N --window-seconds S` 传给
  supervisor（`daemon_supervisor.py::_main()` 已在阶段一预留好这几个参数）
- `daemon_supervisor.py::run_supervisor()` 的循环结构阶段一就已按最终形态
  写好（滑动窗口重启预算 + 指数退避），阶段二只是把调用方传入的
  `auto_restart` 从恒定 `False` 换成真实配置值，循环本身零改动
- 重启前会清掉上一轮残留的 `daemon_run_state.json`，避免新子进程写
  `running` 之前的空档期读到陈旧状态
- 测试新增 `TestSupervisorAutoRestart`（3 用例）：多次崩溃后最终恢复成功
  （`restarted`×2 + 最终优雅停止）、预算耗尽后正确 `giveup`（不无限重启）、
  `auto_restart=False` 时无论预算多大都不重启；daemon 相关测试合计 130
  passed（含 config 装配通用测试）
- 未做（留给阶段三）：前台模式仍未接入 supervisor/自动重启；`daemon status`
  展示的"重启次数"目前只能从崩溃历史文件反推最后一条的
  `restart_attempt`，没有单独暴露"当前滑动窗口内还剩几次预算"这个实时值
  （supervisor 进程内部状态，跨进程查询需要额外接口，评估后续是否有必要）

### 阶段三（已完成）

- `cli/daemon_supervisor.py::run_foreground_supervisor()`（新函数）：前台
  （不带 `--detach`）统一改用与后台 `run_supervisor()` 相同的判定顺序
  （崩溃检测 → 记录 `daemon_crash_history.jsonl` → 告警 → 按滑动窗口预算
  决定是否重启），POSIX 和 Windows 从"两套不同模型"收敛为同一套：
  - 子进程原样继承当前控制台的 stdin/stdout/stderr（不重定向到
    `daemon.log`），用户依然在终端里直接看到 daemon 实时输出，效果与旧版
    `os.execv`/Windows 分支一致；
  - supervisor 自身不 detach（就是用户手上这个终端进程），但同样写
    `daemon_supervisor.pid`，使得另一个终端里 `daemon stop` 能通过既有的
    `_stop_supervisor()`（无需区分前台/后台，逻辑本就通用）找到并等待/
    兜底强杀它；
  - Ctrl-C（`KeyboardInterrupt`）在 supervisor 层捕获：先调用
    `mark_stopped_by_user()` 标记停止意图（防止子进程来不及处理信号被杀
    时被误判为崩溃），再把信号转发给子进程（POSIX 发 `SIGINT`，Windows
    发 `CTRL_C_EVENT`，Windows 侧沿用 `CREATE_NEW_PROCESS_GROUP` 创建子
    进程以支持精确转发），然后继续等待子进程真正退出
- `cli/daemon.py::cmd_daemon_start()` 前台分支：移除 POSIX 的 `os.execv`
  整段和 Windows 专用的 `Popen+wait`/Ctrl-C 转发整段，统一改为读取
  `HttpConfig.daemon_auto_restart_*` 配置（与后台 `--detach` 分支读取方式
  完全一致）后调用 `run_foreground_supervisor()`，返回值作为进程退出码
- `cmd_daemon_stop()`/`_stop_supervisor()` 不需要任何改动——两者从阶段一
  开始就没有区分前台/后台，只认 `daemon_supervisor.pid` 是否存在，阶段三
  只是让前台模式也开始写这个文件，自然接入了现有联动
- 副作用（正面）：前台模式现在与后台共享完全相同的"先感知后恢复"顺序
  保证——即使重启逻辑本身抛异常，崩溃记录和告警也已经在决定是否重启之前
  发出；前台模式此前完全没有崩溃自愈/告警能力，现在与后台行为对齐
- 测试：`tests/test_daemon_crash_recovery.py` 新增
  `TestForegroundSupervisor`（4 用例）：崩溃后自动重启直至成功、预算耗尽
  后正确 `giveup`（返回非零退出码）、`daemon stop` 触发的预期停止不记录
  为崩溃、supervisor 自身 PID 文件生命周期（写入 + 循环结束后清理）；
  `tests/test_daemon_crash_recovery.py` 全量 23 passed
- 未做（留给阶段四）：用户可见的 `docs/` 使用指南尚未更新前台模式行为
  变化的说明（"前台终端现在是 supervisor，daemon 是它的子进程"）；
  `KeyboardInterrupt`/信号转发路径目前只有单元测试覆盖了 supervisor 内部
  的判定逻辑（通过脚本模拟子进程直接调用 `mark_stopped_by_user` 退出），
  没有覆盖真实发送 OS 级 Ctrl-C 信号的端到端场景（CI 环境模拟终端信号
  较脆弱，权衡后未加，人工验证已在本地 Windows/Linux 各执行一次）

### 阶段四（已完成）

- `docs/daemon-crash-recovery-guide.md`：进度表格全部更新为"已完成"；
  新增"§8 前台模式（阶段三）"一节，说明前台 `daemon start` 现在的行为
  模型（当前终端变成 supervisor）、崩溃后自动重启的观感、Ctrl-C 转发
  的兜底标记顺序、以及"另开终端 `daemon stop`"如何与前台 supervisor 联动，
  并给出一个手动验证步骤（kill -9 真正子进程 PID，观察前台终端打印
  "Restarting in Ns..."）
- `next_doc/daemon_crash_recovery_and_alert_plan.md`：补充本节（阶段四
  实现记录）
- 测试补齐计划 §4 列出的 5 类关键用例，全部落在
  `tests/test_daemon_crash_recovery.py`：
  1. 模拟崩溃触发告警 + 自动重启——阶段一/二/三已有的
     `TestSupervisorAutoRestart`/`TestForegroundSupervisor` 覆盖了后台和
     前台两条路径
  2. `daemon stop` 全程不触发任何重启或误报崩溃——新增
     `TestDaemonStopDoesNotTriggerRestart`（2 用例）：一个直接验证
     "兜底标记先于强杀发生"这个时序本身；另一个跑真实的
     `run_supervisor()` 循环，用后台线程模拟"另一个终端执行 daemon
     stop"（先写 `stopped_by_user` 标记再 `SIGKILL` 子进程），验证
     supervisor 自然结束、不记录崩溃、不重启
  3. 重启预算耗尽后正确停止重试并发出"已放弃"告警——已有的
     `test_auto_restart_gives_up_after_budget_exhausted`（后台）/
     `test_gives_up_after_budget_exhausted`（前台，阶段三新增）覆盖
  4. 前台模式下 Ctrl-C 视为用户停止，不触发重启——新增
     `test_keyboard_interrupt_marks_stopped_by_user_and_does_not_restart`：
     monkeypatch 子进程的 `wait()` 在第一次调用时抛 `KeyboardInterrupt`
     模拟用户按下 Ctrl-C，断言只启动了一次子进程（没有被当成崩溃重启）、
     信号被转发、run_state 停在 `stopped_by_user`
  5. 崩溃告警即使在重启逻辑本身抛异常的情况下依然已经发出——新增
     `TestAlertSentBeforeRestartLogicFails`：monkeypatch
     `daemon_supervisor.time.sleep`（重启退避那一步）直接抛
     `RuntimeError`，断言异常确实向上传播的同时，`daemon_crash_history.jsonl`
     和独立告警存储都已经在异常抛出之前完成写入——验证"先感知、后恢复"
     的顺序保证不是文档里的口头承诺，而是真被测试钉住的行为
- `tests/test_daemon_crash_recovery.py` 全量 27 passed（阶段一 16 +
  阶段二 3 + 阶段三 4 + 阶段四新增 4）
- 至此 daemon_crash_recovery_and_alert_plan.md 四个阶段全部完成，计划里
  §4 列出的目标（崩溃可感知、崩溃可恢复、前后台模型统一、告警独立于
  常规通知）均已落地并有测试覆盖
