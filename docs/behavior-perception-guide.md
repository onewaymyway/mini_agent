# 用户行为感知系统设计与使用指南

## 目的

自动感知用户在电脑/浏览器/手机上的行为，聚合成"工作与生活画像"日报，
供 agent 主动理解用户当下在做什么、辅助决策（要不要提醒、要不要主动
帮忙）。**这不是一个默认开启的监控系统**：总开关和每一个采集器都默认
`False`，需要用户显式打开。

## 整体架构

```
采集层（本机常驻线程 / 外部系统 HTTP 上报）
   ├── 桌面本机线程采集器：ActiveWindowCollector / IdleCollector /
   │                        NowPlayingCollector / AppLifecycleCollector
   ├── 专用调试浏览器（CDP）：CDPBrowserCollector（另一种浏览器行为方案）
   └── 外部上报源（复用同一个 HTTP 接口，kind 区分来源）：
        browser  — 浏览器插件（日常浏览器 + 插件）
        git      — git hook（commit/checkout）
        terminal — shell hook（命令级，双重脱敏）
        mobile   — 手机端 Tasker/快捷指令/Android 伴侣 App
                              │
                              ▼
                    BehaviorEventStore（按天分文件的 JSONL）
                              │
                              ▼
                    分析层 analyzer.py
     每天定时（或手动 /behavior report）把原始事件聚合成
     "工作画像 + 生活画像"结构化摘要，落盘 .json + .md
```

配置文件是 `<project_root>/behavior_config.json`，跟 `agent_config.json`
放在同一级目录，方便一起查看/编辑/纳入 `.gitignore`；读写逻辑仍然完全
独立于 `config/loader.py` 那套 `AppConfig` 加载流程，不会被 `agent_config.json`
里的字段覆盖。采集到的原始事件和分析摘要则仍落盘在 `~/.agent/behavior/`
（跨项目共享，因为"用户在做什么"这件事本来就不该按项目切分），只有
开关配置这一份跟着项目走。

## 隐私边界（这是设计的核心，务必保持）

- **不采集聊天软件的消息内容**：聊天类 App（微信/QQ/Slack 等）只当普通
  前台窗口处理——只知道"用户在用微信"，不解析、不读取消息正文。
- **不做按键内容记录**：只用"距上次输入时长"判断在场/空闲，不是 keylogger。
- **剪贴板只记录"发生了复制"这一事实**，不落地剪贴板内容。
- **CDP 浏览器方案只取 URL/标题**：不用 `Page.captureScreenshot`、
  `Network.*`、`Runtime.evaluate`，即使技术上能拿到也不拿。
- **终端命令双重脱敏**：客户端 hook 先过滤一遍，服务端 `manager.report_external`
  再兜底过滤一遍，命中 `password`/`token`/`secret`/`-p <pwd>` 等特征的
  整条命令直接丢弃，不是打码后存，是完全不落盘。
- **手机端只允许地理围栏标签**（"home"/"work"/"other"），绝不接受原始
  经纬度——地理位置判断必须在手机本地完成，服务端会强制剔除
  `lat`/`lon`/`latitude`/`longitude`/`gps`/`coordinates` 等字段。
- **健康数据只要日聚合数字**（步数、睡眠时长），不采集心率曲线、
  GPS 运动轨迹等细粒度数据。
- **不读通知正文、不读短信/聊天内容**（手机端同样适用这条边界）。
- **所有开关默认关闭**，需要用户在 CLI 或 HTTP API 里显式打开。

## 配置项（`<project_root>/behavior_config.json`）

由 `perception/behavior/config.py` 里的 `BehaviorConfig` 定义，全部字段
默认关闭/保守：

| 字段 | 默认值 | 说明 |
|---|---|---|
| `enabled` | `False` | 总开关，关闭时其它子开关全部不生效 |
| `active_window_enabled` | `False` | 前台窗口/程序采集 |
| `idle_enabled` | `False` | 空闲/在场检测 |
| `browser_report_enabled` | `False` | 接受浏览器插件的 HTTP 上报 |
| `mobile_report_enabled` | `False` | 接受手机端的 HTTP 上报 |
| `clipboard_meta_enabled` | `False` | 仅记录"发生了复制"及来源程序 |
| `cdp_browser_enabled` | `False` | 专用调试浏览器方案（不会随总开关自动启动，必须 `/behavior browser start`） |
| `git_activity_enabled` | `False` | 接受 git hook 上报 |
| `terminal_command_enabled` | `False` | 接受终端命令上报 |
| `now_playing_enabled` | `False` | 媒体"正在播放"元数据 |
| `app_lifecycle_enabled` | `False` | 应用启动/退出 |
| `daily_analysis_enabled` | `False` | 是否每天自动生成一次画像摘要 |
| `daily_analysis_hour` | `22` | 自动生成摘要的时间点（24 小时制） |
| `poll_interval_sec` | `2.0` | 前台窗口轮询间隔 |
| `idle_threshold_sec` | `120.0` | 空闲判定阈值 |
| `redact_window_title` | `True` | 只记录 App 名，不记录窗口标题原文 |
| `redact_url_path` | `True` | 浏览器上报只保留域名，不保留完整路径/查询参数 |
| `retention_days` | `30` | 事件保留天数，超期自动清理 |
| `cdp_debug_port` / `cdp_browser_path` / `cdp_user_data_dir` / `cdp_headless` | — | CDP 方案参数 |

> **配置文件位置解析规则**：`get_manager(project_root=...)` 不传时默认用
> `Path.cwd()`，跟 `config/loader.py::load_config()` 里 `root = project_root
> or Path.cwd()` 的默认规则一致。CLI（`/behavior ...`）会自动传入
> `agent.cfg.project_root`；HTTP API 从 `request.app.state.project_root`
> 里取。`get_manager()` 是进程内单例，只有第一次调用时的 `project_root`
> 生效，之后调用即使传入不同的路径也会复用已创建的实例。

## 采集器一览

### 桌面本机线程

| 采集器 | 文件 | 说明 |
|---|---|---|
| `active_window` | `collectors/active_window.py` | 跨平台前台窗口/程序，Windows(pywin32)/macOS(AppKit 或 osascript)/Linux(xdotool，Wayland 下大多不可用) |
| `idle` | `collectors/idle.py` | 空闲/在场检测（`GetLastInputInfo` / `CGEventSourceSecondsSinceLastEventType` / `xprintidle`） |
| `now_playing` | `collectors/now_playing.py` | 媒体"正在播放"标题/艺术家（Windows winsdk / macOS osascript / Linux playerctl） |
| `app_lifecycle` | `collectors/app_lifecycle.py` | 应用启动/退出（psutil 差分快照，只记进程名） |

### 浏览器行为（两种独立方案，二选一或都开）

1. **浏览器插件方案**：`browser_extension_example/`，MV3 插件，只上报域名+
   停留时长，插件自身有开关，需要手动填 token。上报走 `kind="browser"`。
2. **专用调试浏览器（CDP）方案**：`collectors/cdp_browser.py` +
   `collectors/browser_launcher.py`，`/behavior browser start` 拉起一个
   独立 `--user-data-dir` 的浏览器实例（不接管日常浏览器），订阅
   `Target.*` 事件拿 URL/标题变化。

两种方案事件语义一致（`source` 分别是 `browser_ext` / `cdp_browser`，
`event_type="page_visit"`），分析层不用关心具体走的是哪条路径。

### Git / 终端（外部上报，通过 hook 脚本）

- `collectors/external_hooks.py` 生成 git `post-commit`/`post-checkout`
  hook（只报分支名/commit 概要，不报 diff 内容）和 shell hook 片段
  （bash `PROMPT_COMMAND` / zsh `precmd`）。
- 终端命令脱敏两层：客户端 hook 里含敏感关键字的整条命令直接不发送；
  服务端 `manager.report_external` 再兜底过滤一遍。

### 手机端（外部上报，`kind="mobile"`）

三条接入路径，任选：

1. **Tasker/MacroDroid**（Android）：`/behavior mobile android` 打印
   JSON body 模板，需要配合 AutoTools 这类插件读 `UsageStatsManager`。
2. **iOS 快捷指令**：`/behavior mobile ios` 打印"个人自动化 → 获取 URL
   内容"动作的配置模板，地理围栏场景用固定 label，不传坐标。
3. **Android 伴侣 App**（`android_companion_app/`）：一个独立的 Kotlin
   工程，比 Tasker 方案更稳定、更完整，覆盖：
   - App 使用统计（`UsageStatsWorker`，需要用户去系统设置单独授权）
   - 屏幕解锁/息屏（`ScreenEventReceiver`）
   - 地理围栏标签（`GeofenceHelper`，坐标只存本地 SharedPreferences，
     从不上传，`ReportClient` 也会兜底剔除）
   - 健康日聚合：步数/睡眠（`HealthConnectWorker`，Health Connect API）

   详见 `android_companion_app/README.md`（**注意**：这份源码没有在
   Android SDK 环境里编译验证过，需要用户在 Android Studio 里跑一遍，
   README 里列了几个大概率要调整的依赖版本/权限字符串）。

## 分析层（`analyzer.py`）

把原始事件聚合成结构化日报，落盘 `~/.agent/behavior/analysis/<date>.json`
和同名 `.md`：

- **工作画像**：活跃时段、前台窗口切换次数（碎片化指标）、Top App/
  网站时长、Git 提交、终端命令数、后台新启动程序、工作 vs 娱乐时长估算
- **生活画像**：媒体播放累计时长/曲目、手机端 App 使用时长、手机解锁
  次数、地点标签切换序列（home→work→home 这类）、健康日聚合数字

分类用的是写死在 `analyzer.py` 里的简单关键字启发式（`_WORK_APP_HINTS` /
`_ENTERTAINMENT_APP_HINTS` 等），没有做成配置项，需要调整直接改这几个
tuple 即可。

`daily_analysis_enabled=True` 时，`manager.py` 里有一个后台线程每分钟
检查一次，到 `daily_analysis_hour` 这个整点就跑一次当天的摘要。

## CLI 速查

```
/behavior status                 总开关/各采集器状态
/behavior on / off                打开/关闭总开关
/behavior enable <collector>      打开某个采集器
/behavior disable <collector>     关闭某个采集器
/behavior token                   查看/生成外部上报 token
/behavior recent [n]              最近 n 条事件
/behavior clear                   清空所有事件

/behavior browser start           启动专用调试浏览器（CDP）
/behavior browser stop [--kill]   停止采集（可选关闭浏览器进程）
/behavior browser status          查看 CDP 连接状态

/behavior git install <repo>      安装 git commit/checkout 上报 hook
/behavior terminal show           打印 shell hook 片段
/behavior terminal install        追加到 ~/.bashrc 或 ~/.zshrc

/behavior mobile android          打印 Android(Tasker) 接入模板
/behavior mobile ios              打印 iOS 快捷指令接入模板

/behavior report [today|<date>]   查看/生成工作与生活画像日报
```

采集器名称：`active_window` / `idle` / `browser_report` / `mobile_report` /
`clipboard_meta` / `cdp_browser` / `git_activity` / `terminal_command` /
`now_playing` / `app_lifecycle` / `daily_analysis`

## HTTP API 速查

详见 `docs/http-api-guide.md` 的"用户行为感知"小节，端点前缀都是
`/v1/perception/*`：`status` / `toggle` / `report` / `events`
(GET+DELETE) / `browser/start` / `browser/stop` / `browser/status` /
`git/install-hooks` / `summary`。

`/v1/perception/report` 的 body 结构：

```jsonc
{
  "source": "browser_ext",   // 事件来源标识，自由文本
  "kind": "browser",         // "browser" | "git" | "terminal" | "mobile"
  "token": "...",            // 来自 /behavior token
  "events": [
    { "event_type": "page_visit", "domain": "github.com", "duration_sec": 30 }
  ]
}
```

## 已知局限

- Linux + Wayland 下前台窗口/空闲检测大多拿不到（依赖 X11 工具链），
  这是合成器层面的限制，不是本系统能绕开的。
- 沙盒/CI 环境里没有真实浏览器/Android SDK，`cdp_browser` 和
  `android_companion_app` 都只做到了逻辑正确性验证，没有做过真实设备
  上的端到端联调，建议实际使用前自行验证一遍。
- 手机端和跑 mini_agent 的电脑必须在同一局域网，否则需要用户自己搭
  内网穿透。

## 相关文档

- [HTTP API 使用指南](http-api-guide.md) — `/v1/perception/*` 端点详情
- [命令与工具参考](commands-and-tools-reference.md) — `/behavior` 全部子命令
- `browser_extension_example/` — 浏览器插件源码
- `android_companion_app/README.md` — Android 伴侣 App 构建与隐私说明
