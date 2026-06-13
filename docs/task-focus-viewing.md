# Task 日志实时查看与切换

## 概述

mini-agent 支持在 Task 运行时通过快捷键实时查看和切换不同任务的日志输出。当多个 Task 并发执行时，可以通过方向键快速切换到任意任务的实时日志视图。

## 功能特性

- **实时日志查看**：进入任务焦点模式后，实时显示该任务的最新日志输出
- **快速切换**：通过方向键在不同任务之间切换
- **状态栏展示**：底部状态栏显示所有任务的状态和名称
- **跨平台支持**：支持 Windows 和 Unix 系统

## 快捷键操作

| 按键 | 功能 |
|------|------|
| `→` 或 `↓` | 进入/切换到下一个任务的日志视图 |
| `←` 或 `↑` | 切换到上一个任务的日志视图 |
| `ESC` | 退出当前任务的焦点模式，返回主输出视图 |

## 状态栏显示

### 任务状态图标

- `●` (青色) - RUNNING：任务正在执行
- `○` (黄色) - PENDING：任务在队列中等待
- `✓` (绿色) - DONE：任务完成
- `✗` (红色) - FAILED：任务失败
- `–` (灰色) - CANCELLED：任务已取消

### 任务 Tab 栏

当有任务存在时，状态栏底部会显示任务 Tab 栏：

```
  ● task-name 12s │ ○ waiting-task │ ✓ completed
```

- 当前焦点任务会高亮显示（亮色 + 下划线）
- 非焦点任务显示为暗灰色
- 显示任务名称和执行耗时

## 使用场景

### 场景 1：监控多个并发任务

当多个 Task 同时运行时，状态栏会显示所有任务的状态概要。使用方向键可以快速切换到任意任务的详细日志视图，查看执行细节。

### 场景 2：排查任务问题

发现某个任务状态为 `FAILED` 时，立即切换到该任务查看详细错误日志。

### 场景 3：对比任务进度

在多个相关任务执行时，来回切换对比各任务的执行进度。

## 实战示例

### 启动多个并发任务

```bash
# 启动 mini-agent
python -m mini_agent

# 输入：创建 3 个并发任务
task("Task 1: Sleep 10s", async=True, delay=10)
task("Task 2: Sleep 8s", async=True, delay=8)
task("Task 3: Sleep 6s", async=True, delay=6)
```

### 切换查看任务日志

1. 任务启动后，状态栏底部显示任务 Tab 栏
2. 按 `→` 或 `↓` 切换到第一个任务，查看实时日志
3. 继续按 `→` 或 `↓` 切换到下一个任务
4. 按 `←` 或 `↑` 返回上一个任务
5. 按 `ESC` 退出焦点模式，返回主输出视图

## 实战示例

### 启动多个并发任务

```bash
# 启动 mini-agent
python -m mini_agent

# 输入：创建 3 个并发任务
task("Task 1: Sleep 10s", async=True, delay=10)
task("Task 2: Sleep 8s", async=True, delay=8)
task("Task 3: Sleep 6s", async=True, delay=6)
```

### 切换查看任务日志

1. 任务启动后，状态栏底部显示任务 Tab 栏
2. 按 `→` 或 `↓` 切换到第一个任务，查看实时日志
3. 继续按 `→` 或 `↓` 切换到下一个任务
4. 按 `←` 或 `↑` 返回上一个任务
5. 按 `ESC` 退出焦点模式，返回主输出视图

## 内部机制

### 架构组件

```
┌─────────────────────────────────────────────────────────┐
│                    Terminal                              │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │Render Thread│  │Refresh Thread│  │Focus Controller│  │
│  └─────────────┘  └──────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────┘
                          ▲
                          │
                  ┌───────┴───────┐
                  │  RawKeyListener│ (后台线程监听键盘)
                  └───────┴───────┘
                          │
                  ┌───────┴───────┐
                  │ TaskManager   │ (任务调度)
                  └───────────────┘
```

### 工作流程

1. **键盘监听**：`RawKeyListener` 在后台线程持续监听方向键输入
2. **焦点切换**：调用 `Terminal.set_task_focus(task_id)` 设置焦点
3. **日志投递**：`Refresh Thread` 每个周期查询焦点任务的最新日志
4. **渲染输出**：`Render Thread` 将新日志行增量打印到屏幕

### 关键文件

- `src/mini_agent/ui/terminal.py` - Terminal 类，负责焦点控制和日志渲染
- `src/mini_agent/ui/raw_key_listener.py` - 跨平台键盘监听器
- `src/mini_agent/orchestrator/status_bar.py` - 状态栏和任务 Tab 显示
- `src/mini_agent/orchestrator/task_manager.py` - 任务调度和日志管理

## 开发指南

### 添加新的任务焦点快捷键

如需添加其他快捷键（如 `T` 键切换焦点模式），可在 `raw_key_listener.py` 的 `_handle` 方法中添加键位映射：

```python
def _handle(self, seq: bytes) -> None:
    if seq == b"\x03":
        self._dispatch("sigint")
    elif seq in (self._CSI_RIGHT, self._CSI_DOWN,
                 self._SS3_RIGHT, self._SS3_DOWN):
        self._dispatch("next")
    # 新增：T 键切换焦点
    elif seq == b"t":
        self._dispatch("toggle_focus")
```

然后在 `Terminal` 类中添加对应的切换方法。

### 调试键盘事件

设置环境变量启用键盘事件调试日志：

```bash
# Windows
$env:MINI_AGENT_KEY_DEBUG=1

# Unix/Linux
export MINI_AGENT_KEY_DEBUG=1
```

调试日志会写入 `.agent/mini_agent_keys.log` 文件，记录所有键盘事件和动作派发。

## REPL 命令

除了快捷键，也可以使用 `/tasks` 命令管理任务：

```bash
/tasks focus <task_id>    # 指定切换到某个任务
/tasks unfocus            # 退出焦点模式
/tasks dashboard          # 显示任务概览
/tasks log <task_id>      # 查看任务日志
/tasks cancel <task_id>   # 取消任务
```

## 注意事项

1. **焦点模式下主输出静默**：进入任务焦点后，Agent 主输出的日志会被暂存，不会干扰当前任务的日志查看。焦点退出后，所有日志仍完整保留在会话历史中。

2. **日志自动增量**：无需手动刷新，刷新线程每个周期自动检查并投递新增日志行。

3. **线程安全**：焦点状态变更通过消息队列机制，确保在渲染线程内安全执行，避免竞态条件。

4. **跨平台兼容**：
   - Unix 系统：使用 `/dev/tty` + `termios.setraw()` + `select()`
   - Windows 系统：使用 `msvcrt.kbhit()` + `getwch()`

## 相关文档

- [系统概览](./system-overview.md) - 整体架构介绍
- [任务与规划指南](./plan-and-task-guide.md) - Task 使用详解
- [终端 I/O 指南](./terminal-io-guide.md) - 终端渲染机制
- [子 Agent 机制](./subagent-mechanism.md) - SubAgent 实现细节
