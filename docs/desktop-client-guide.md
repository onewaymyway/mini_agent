# Windows 桌面常驻客户端指南（Desktop Client）

> 设计与分阶段计划见 [`next_doc/windows_desktop_resident_plan.md`](../next_doc/windows_desktop_resident_plan.md)。
> 当前已实现：**S1 系统托盘**。快速面板/全局热键（S2）、Toast 与审批（S3）、自启动与加固（S4）尚未实现。
> 代码位置：`apps/mini_agent_desktop/`。

## 1. 它是什么

一个常驻系统托盘的**纯 HTTP/SSE 客户端**：只通过 daemon 的 `/v1/*` 接口与 mini_agent 通信，不 import mini_agent 核心包，也不改变 daemon 的任何行为。关闭它不影响后台 daemon。

托盘能告诉你：

- daemon 是否在线、是否正在执行任务；
- 有多少事项在等你处理（权限审批、交互请求、cron 异步提问、未读汇报、失败目标、崩溃告警）；
- daemon 崩溃后，即使 HTTP 已不可用也能提示（直接读崩溃告警文件）。

## 2. 安装与启动

```powershell
pip install -r apps/mini_agent_desktop/requirements.txt     # PySide6-Essentials + httpx
cd <mini_agent 项目根目录>
python -m pip install -e .                                   # 如果 daemon 还没装好
# 在 apps/mini_agent_desktop 目录下，或把它加入 PYTHONPATH：
cd apps\mini_agent_desktop
pythonw -m mini_agent_desktop --project-root D:\path\to\mini_agent     # 无控制台窗口
python  -m mini_agent_desktop --project-root D:\path\to\mini_agent     # 调试时带控制台
```

前置条件：mini_agent 的 daemon 需要先在该项目根目录下运行（`mini-agent daemon start --detach`）。要让托盘能**即时**感知新增汇报/提问，还需在 `agent_config.json` 打开（默认关闭，改后重启 daemon），详见 [desktop-attention-events-guide.md](desktop-attention-events-guide.md)：

```json
{ "http": { "desktop_attention_events_enabled": true } }
```

不打开也能用：托盘退回按 `poll_interval_seconds`（默认 30 秒）兜底轮询摘要。

### 命令行参数

| 参数 | 说明 |
|---|---|
| `--project-root DIR` | 要连接的项目根目录；缺省读配置文件，再缺省用当前目录 |
| `--port N` | daemon 端口；缺省读 `<project_root>/.agent/daemon_info.json`，再缺省读配置（8765） |
| `--auto-start-daemon` | daemon 未运行时自动拉起（默认关闭） |
| `--check` | **无界面自检**后退出（不需要 PySide6），排障首选 |

`--check` 退出码：`0` 正常，`2` daemon 未运行，`3` 认证失败（token 无效/缺失），`4` 其它错误。

## 3. 配置文件（可选）

路径：`%LOCALAPPDATA%\mini_agent\desktop.json`（其它系统 `~/.config/mini_agent/desktop.json`；环境变量 `MINI_AGENT_DESKTOP_CONFIG` 可覆盖）。文件不存在或损坏时一律使用默认值。

```json
{
  "project_root": "D:\\path\\to\\mini_agent",
  "http_port": 8765,
  "console_url": "http://localhost:8501",
  "poll_interval_seconds": 30,
  "auto_start_daemon": false
}
```

**默认值全部保守**：`auto_start_daemon` 默认 `false`——daemon 没运行时，托盘菜单会出现手动的"启动 Agent"，而不是自动拉起。

## 4. 托盘图标与菜单

| 图标颜色 | 含义 |
|---|---|
| 绿 | 在线、空闲 |
| 蓝（中心白点） | 正在执行任务 |
| 黄 | 有事项需要你处理（右下角红色数字角标 = 待处理总数，>9 显示 `9+`） |
| 红 | 有未确认的 daemon 崩溃告警（在线或离线均会显示） |
| 灰 | 连不上 daemon |

菜单：状态行 / 当前任务数 / 待处理事项数（均为信息行）、**打开管理控制台**（打开 `console_url`）、**启动 Agent**（仅在"daemon 未运行"时出现）、**重新连接**、**退出桌面端**。

- 鼠标悬停显示各类待处理数量明细。
- **退出桌面端不会停止 daemon**（菜单文案已注明）。
- 左键单击弹出菜单（S2 起改为唤起快速面板）。
- 同一用户只允许运行一个桌面端实例（`QLockFile`），重复启动会提示并退出。

## 5. 连接行为

1. 先 `health` 检查 → 取 `/v1/status` 与 `/v1/desktop/summary` **快照** → 再订阅 `/v1/stream`（不回放历史）。
2. 收到 `turn_start`/`turn_done` 等事件立即刷新状态；`permission_req`/`interaction_req`/`attention`/`cron_job_finished` 立即刷新待处理计数。
3. SSE 心跳（约 20 秒）顺带检查：距上次摘要 ≥ `poll_interval_seconds` 则兜底刷新（覆盖别的客户端已读汇报、事件开关未开的情况）。
4. 断线后指数退避重连（1/2/4/8/15/30 秒封顶），连上一次即重置；"重新连接"/"启动 Agent"会跳过等待立即重试。
5. 区分离线原因：`daemon 未运行`（可手动启动）、`认证失败，请检查 token`（拉起 daemon 没用，菜单不提供启动）、`连接中断`。
6. token 自动读取 `<project_root>/.agent/agent_api.key`，不存在则尝试多用户模式的 `.agent/users/tokens/owner.key`；只连接 `127.0.0.1`。

## 6. 排障

```powershell
python -m mini_agent_desktop --project-root D:\path\to\mini_agent --check
```

输出端点、token 是否找到、离线崩溃告警数、health/status/summary 结果。常见情形：

- `health: 失败` → daemon 没运行或端口不对（看 `.agent/daemon_info.json`）；
- `HTTP 401/403` → token 与 daemon 不一致（daemon 是否用了别的项目根目录/配置了 `api_token`）；
- 托盘图标不见 → Windows 可能把它折叠进了"隐藏的图标"区，拖出来即可。

## 7. Windows 手动验证清单（S1）

沙盒是 Linux，只能验证非 UI 逻辑与离屏 Qt。请在 Windows 上确认：

- [ ] `--check` 在 daemon 运行/未运行/token 错误三种情况下退出码为 0/2/3
- [ ] 启动后托盘出现图标，daemon 运行时为绿色
- [ ] 在 CLI 发起一个长任务，图标变蓝，结束后变回绿
- [ ] 触发一次权限审批（或 `ask_user_async`），图标变黄且角标数字正确
- [ ] `daemon stop` 后图标变灰，菜单出现"启动 Agent"，点击后能恢复
- [ ] 强杀 daemon 进程后，图标变红（有崩溃告警时）
- [ ] 退出桌面端后 daemon 仍在运行（`mini-agent daemon status`）
- [ ] 重复启动第二个实例会提示"已经在运行"

## 8. 代码位置

- `apps/mini_agent_desktop/mini_agent_desktop/config.py`：桌面端配置
- `discovery.py`：端点/token/崩溃告警发现、拉起 daemon（Windows 下 `CREATE_NO_WINDOW`）
- `api.py`：同步 httpx 客户端 + SSE 解析器
- `state.py`：托盘状态模型（图标优先级、菜单文案、事件→刷新集合）
- `controller.py`：后台线程（快照→订阅→重连退避），不依赖 Qt
- `tray.py`：PySide6 托盘（图标程序绘制，不带图片资源）
- `app.py`：入口、`--check`、单实例锁
- 测试：`tests/test_desktop_s1.py`（含起真实路由 + 真实 `AgentBridge` 的端到端集成测试与离屏 Qt 冒烟）
