# 命令行 I/O 机制说明

本文档描述 mini-agent 中所有命令行输出和输入的统一管理方式。

---

## 1. 整体架构

```
┌────────────────────────────────────────────────────────────────────┐
│                         Terminal 类（terminal.py）                   │
│                                                                    │
│  外部调用方                    内部结构                              │
│  ─────────────────────         ──────────────────────────          │
│  renderer.py                   _render_queue: Queue               │
│  permissions.py      ──────→   _render_thread: 串行消费队列         │
│  main.py                       _refresh_thread: 定时推状态栏        │
│  orchestrator/*.py             _statusbar_lines: 当前状态栏内容     │
│  llm/debug_logger.py           _bar_drawn: 已绘行数                │
│                                _streaming: 流式输出中标志           │
│                                                                    │
│  输出通道              输入通道                                      │
│  ─────────────         ──────────────────                          │
│  term.print()          term.prompt_user()  ← 阻塞，主线程专用      │
│  term.rule()           term.confirm()      ← 权限审批              │
│  term.panel()                                                      │
│  term.syntax()                                                     │
│  term.markdown()                                                   │
│  term.stream_token()                                               │
│  term.stream_end()                                                 │
│  term.debug()                                                      │
│  term.update_statusbar()                                           │
│  term.redraw_statusbar()                                           │
└────────────────────────────────────────────────────────────────────┘
```

**核心原则：`Terminal` 是整个进程唯一写屏幕的地方。** 其他所有模块都通过调用 `terminal.term` 的方法输出，不直接操作 `sys.stdout`、`sys.stderr` 或 `Console`。

---

## 2. 为什么需要统一管理

在统一之前，系统中有多个独立的输出路径：

| 路径 | 写到哪里 | 问题 |
|------|----------|------|
| `renderer.py` 的 `Console()` | stdout | ─ |
| `permissions.py` 的 `Console()` + `input()` | stdout | ─ |
| `status_bar.py` 后台线程 | stderr | 与 stdout 内容交错 |
| `debug_logger.py` 的 `Console(stderr=True)` | stderr | 同上 |
| `repl_input.py` 的 `prompt_toolkit` | stdout | 与状态栏光标冲突 |

多个写者同时写不同流，ANSI 光标控制码（`\x1b[NA`上移 N 行）打到错误位置，导致输出混乱。

统一后：**所有内容都写 stdout，由 `Terminal` 的渲染线程串行执行，完全消除竞态。**

---

## 3. Terminal 类详解

### 3.1 两个线程

**渲染线程（render_thread）**

从队列取消息，按顺序渲染到 stdout。这是唯一写屏幕的线程，保证所有输出完全串行。

```
主线程/工作线程 ──put()──→ Queue ──消费──→ render_thread ──write()──→ stdout
```

**状态栏刷新线程（refresh_thread）**

每 250ms 向队列投递一个 `("_refresh", None)` 消息。渲染线程处理时执行状态栏重绘，同样是串行的，不存在与其他输出的竞态。

### 3.2 状态栏管理

状态栏是屏幕底部的固定区域，用 ANSI 控制码覆写实现：

```
渲染内容前：
  if self._bar_drawn > 0:
      stdout.write(f"\x1b[{N}A\x1b[0J")   ← 上移 N 行并清除到底
      self._bar_drawn = 0

输出内容

渲染内容后：
  for line in statusbar_lines:
      stdout.write(line + "\n")
  self._bar_drawn = len(statusbar_lines)
```

每次有内容输出（`print`/`panel`/`syntax` 等）时，渲染顺序是：
1. 擦除状态栏（`_erase_bar()`）
2. 输出内容
3. 重绘状态栏（`_draw_bar()`）

流式输出期间（`stream_token` 消息）：
- 第一个可见 token 前擦状态栏，之后只追加写，不重绘
- `stream_end` 消息到来时重绘状态栏

### 3.3 输入时的状态栏管理

`prompt_user()` 和 `confirm()` 是阻塞调用，在主线程执行，需要特殊处理：

```python
def prompt_user(self) -> str:
    self._flush_queue()         # 等待所有队列消息处理完
    self._pause_refresh()       # 暂停刷新线程，等 render_thread 空闲
    self._erase_bar_direct()    # 直接擦除状态栏（不通过队列）
    try:
        return self._read_line()   # 阻塞等待用户输入
    finally:
        self._resume_refresh()  # 恢复刷新，重绘状态栏
```

`_flush_queue()` + `_pause_refresh()` 确保渲染线程不会在 `input()` 阻塞期间意外写入屏幕。

---

## 4. 输出通道说明

### 4.1 普通输出 `term.print()`

支持所有 Rich markup 和参数，内部调用 `Console.print()`。

```python
term.print("hello [bold]world[/bold]")
term.print(f"[green]✓[/green]  {msg}")
term.print("[dim](empty)[/dim]", end="")
```

### 4.2 流式输出 `term.stream_token()` / `term.stream_end()`

用于 LLM 流式响应，逐 token 写入，中间不重绘状态栏（避免闪烁）。

```python
# 直接调用
for token in llm.stream():
    term.stream_token(token)
term.stream_end()

# 上下文管理器
with term.streaming() as write:
    for token in llm.stream():
        write(token)
```

内置 `<tool_use>...</tool_use>` 过滤——原始 tool call JSON 块不会显示给用户。

### 4.3 结构化输出

```python
term.rule("标题")                      # 分隔线
term.panel(content, title="面板")      # Rich Panel
term.syntax(code, "python")           # 代码高亮
term.markdown("**markdown** 文本")    # Markdown 渲染
```

### 4.4 调试输出 `term.debug()`

```python
term.debug("LLM request sent")             # dim 样式
term.debug("raw JSON", prefix="[API]")     # 自定义前缀
```

### 4.5 状态栏 `term.update_statusbar()` / `term.redraw_statusbar()`

状态栏内容由 `orchestrator/status_bar.py` 构建并推送：

```python
term.update_statusbar(lines)   # 更新内容（不立刻重绘）
term.redraw_statusbar()        # 立刻触发重绘
```

`status_bar.py` 的后台线程每 250ms 构建一次 ANSI 字符串列表并推送，`Terminal` 的渲染线程负责实际写入。

---

## 5. 输入通道说明

### 5.1 用户 REPL 输入 `term.prompt_user()`

```python
user_input = term.prompt_user()
```

- 使用 `prompt_toolkit`（如已安装）：支持命令历史、方向键编辑、Tab 补全 slash 命令
- 降级方案：普通 `input()` + ANSI 彩色提示符
- 自动管理状态栏：读取前擦除，读取后重绘

### 5.2 权限审批 `term.confirm()`

```python
choice = term.confirm(
    message="",
    choices="(y)es  (a)lways  (n)o  (d)eny-always",
    default="y",
)
```

- 阻塞等待用户输入
- 返回小写的选择字符串
- 自动管理状态栏

---

## 6. 各模块的角色

| 模块 | 角色 |
|------|------|
| `terminal.py` | **唯一写屏幕的地方**，渲染队列、状态栏管理、输入读取 |
| `renderer.py` | 适配层：将历史 API（`print_tool_call` 等）映射到 `terminal.term` |
| `orchestrator/status_bar.py` | 构建状态栏内容，推送给 `terminal.term`，不直接写屏幕 |
| `orchestrator/plan_display.py` | 构建 plan 状态栏行和 Rich 树，通过 `terminal.term` 输出 |
| `permissions.py` | 通过 `terminal.term.print()` 和 `term.confirm()` 处理权限审批 |
| `llm/debug_logger.py` | 通过 `terminal.term.debug()` 输出调试信息 |
| `main.py` | 通过 `terminal.term.prompt_user()` 读取用户输入 |

### 禁止的做法

```python
# ❌ 不允许直接写屏幕
print("hello")
sys.stdout.write("hello")
Console().print("hello")
Console(stderr=True).print("hello")

# ✅ 统一通过 terminal
from terminal import term
term.print("hello")
```

---

## 7. 数据流示意图

```
                   ┌─────────────────────────────────────────────┐
                   │              Queue（消息队列）               │
                   │                                             │
 agent.py          │  ("print", ...)        ("stream", token)   │
 renderer.py  ─────→  ("rule", ...)         ("stream_end", _)   │
 permissions.py    │  ("statusbar", lines)  ("redraw", _)       │
 status_bar.py     │  ("syntax", ...)       ("_refresh", _)     │
                   │                                             │
                   └──────────────────┬──────────────────────────┘
                                      │ 串行消费
                                      ▼
                             render_thread
                                      │
                    ┌─────────────────┼──────────────────┐
                    │                 │                  │
               _erase_bar()    写内容到 stdout      _draw_bar()
               上移N行+清除                         重绘状态栏
                    │                 │                  │
                    └─────────────────┴──────────────────┘
                                      │
                                   stdout
                                      │
                               终端（用户看到）
```

---

## 8. 状态栏内容来源

```
status_bar.py（每 250ms）
    │
    ├── concurrency_snapshot()   ← orchestrator/concurrency.py
    │       ⚡ Tasks [████] 2/4  2 running
    │       🤖 LLM   [██] 1/8   1 running
    │
    └── build_plan_status_lines()  ← orchestrator/plan_display.py
            📋 Plan  [████░░] 2/3  1 running
               ✓ [t1]  分析架构  12s
               ◉ [t2]  生成文档  → after t1
               ○ [t3]  生成详设  → after t2
    │
    ▼
term.update_statusbar(lines)
term.redraw_statusbar()
    │
    ▼
Queue → render_thread → stdout
```

---

## 9. CLI 参数和 Slash 命令

### 启动参数（main.py）

| 参数 | 说明 |
|------|------|
| `[prompt]` | 单条 prompt 非交互模式 |
| `--model / -m` | 指定模型名 |
| `--provider` | LLM provider（anthropic / openai / ollama / nvidia 等） |
| `--system / -s` | 附加 system prompt 文本 |
| `--project / -p` | 项目根目录 |
| `--verbose / -v` | 显示原始工具 JSON |
| `--yes / -y` | 自动同意所有工具调用 |
| `--sandbox` | 沙箱模式（禁止破坏性操作） |
| `--no-stream` | 禁用流式输出 |
| `--max-turns` | 每轮最大 agentic turns |
| `--system-tool-call` | 使用 system prompt 工具调用模式（最大兼容性） |
| `--workers` | 最大并发 sub-agent 数（默认 4） |
| `--max-llm-calls` | 最大并发 LLM 调用数（默认 8） |
| `--debug-llm` | 开启 LLM 请求/响应调试日志 |
| `--debug-llm-console` | 同时打印调试信息到控制台 |
| `--session-dir` | session 文件存储目录 |
| `--resume` | 恢复指定 session |
| `--agent-name` | Agent 显示名称 |
| `--base-url` | 自定义 API endpoint |

### REPL Slash 命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清空对话历史 |
| `/plan` | 显示当前执行计划（Rich 树形） |
| `/plan clear` | 清除执行计划 |
| `/plan summary` | 打印完成摘要表格 |
| `/tasks` | 显示所有 sub-agent 任务 |
| `/tasks dashboard` | 实时任务看板（等待全部完成） |
| `/tasks log <id>` | 查看某任务的输出日志 |
| `/tasks cancel <id>` | 取消某任务 |
| `/tasks workers <n>` | 动态调整最大并发数 |
| `/skills` | 列出所有可用 skills |
| `/skill on <name>` | 激活某 skill |
| `/skill off <name>` | 停用某 skill |
| `/stats` | 显示会话统计 |
| `/verbose` | 切换详细工具输出模式 |
| `/model <name>` | 中途切换模型 |
| `/compact` | 压缩历史（summary）释放 context |
| `/prompts` | 列出所有托管 prompt 文件 |
| `/concurrency` | 显示并发状态 |
| `/concurrency tasks <n>` | 设置最大并发任务数 |
| `/concurrency llm <n>` | 设置最大并发 LLM 调用数 |
| `/provider` | 显示当前 provider |
| `/provider list` | 列出所有已注册 providers |
| `/provider switch <name>` | 切换 provider |
| `/session` | 显示当前 session 信息 |
| `/session list` | 列出所有历史 session |
| `/session new` | 开始新 session |
| `exit` / `quit` | 退出 |

---

## 10. 权限审批流程

当 agent 请求执行需要审批的工具时：

```
agent 调用工具
    │
    ▼
permissions.py 拦截
    │
    ├── requires_approval=False → 直接通过
    ├── always_allow 列表 → 直接通过
    ├── deny_always 列表 → 直接拒绝
    └── 需要询问 → _prompt()
            │
            ├── term.print("🔧 Tool request: ...")    ← 通过队列，安全
            └── term.confirm("(y)es (a)lways (n)o")  ← 暂停状态栏，等 input()
                    │
                    ├── y / yes    → 本次允许
                    ├── a / always → 加入 always_allow，后续不再询问
                    ├── n / no     → 本次拒绝
                    └── d / deny   → 加入 deny_always，后续永远拒绝
```
