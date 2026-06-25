# 命令行 I/O 机制说明

本文档描述 mini-agent 中命令行输出和输入的统一管理方式。

**补充阅读**：
- [Task 日志实时查看与切换](task-focus-viewing.md) — 方向键切换查看任务日志机制
- [终端显示机制深度解析](terminal-display-internals.md) — 状态栏、流式输出、光标控制的完整内部实现
- [RawKeyListener 跨平台键盘监听](../src/mini_agent/ui/raw_key_listener.py) — 方向键监听实现

---

## 1. 整体架构

```
┌────────────────────────────────────────────────────────────────────┐
│                   Terminal 类（ui/terminal.py）                     │
│                                                                    │
│  外部调用方                      内部结构                           │
│  ─────────────────────           ─────────────────────────         │
│  ui/renderer.py                  _render_queue: Queue             │
│  permissions.py        ──────→   _render_thread: 串行消费队列      │
│  cli/repl.py                     _refresh_thread: 定时推状态栏     │
│  orchestrator/*.py               _statusbar_lines: 当前状态栏内容  │
│  llm/debug_logger.py             _bar_drawn: 已绘行数              │
│                                  _streaming: 流式输出中标志        │
│                                  _bar_suspended: prefix 后暂停标志 │
│                                  _bar_below_prefix: LLM等待标志   │
│                                                                    │
│  输出通道                输入通道                                   │
│  ─────────────────        ──────────────────────                   │
│  term.print()             term.prompt_user()  ← 阻塞，主线程专用   │
│  term.rule()              term.confirm()      ← 权限审批           │
│  term.panel()                                                      │
│  term.syntax()                                                     │
│  term.markdown()                                                   │
│  term.stream_token()                                               │
│  term.stream_end()                                                 │
│  term.force_end_stream()                                           │
│  term.update_statusbar()                                           │
│  term.redraw_statusbar()                                           │
└────────────────────────────────────────────────────────────────────┘
```

**核心原则：`Terminal` 是整个进程唯一写屏幕的地方。** 其他所有模块通过调用 `terminal.term` 的方法输出，不直接操作 `sys.stdout` 或 `Console`。

---

## 2. 为什么需要统一管理

重构前存在多个独立输出路径：

| 路径 | 写到哪里 | 问题 |
|------|----------|------|
| `renderer.py` 的 `Console()` | stdout | — |
| `permissions.py` 的 `input()` | stdout | — |
| `status_bar.py` 后台线程 | stderr | 与 stdout 内容交错 |
| `debug_logger.py` | stderr | 同上 |
| `repl_input.py` 的 prompt_toolkit | stdout | 与状态栏光标冲突 |

多个写者同时写不同流，ANSI 光标控制码打到错误位置。  
**重构后：所有内容写 stdout，由 `Terminal` 的渲染线程串行执行，完全消除竞态。**

---

## 3. Terminal 类详解

### 3.1 两个后台线程

**渲染线程（render_thread）**  
从队列取消息，按顺序渲染到 stdout，是唯一写屏幕的线程。

```
主线程/工作线程 ──put()──→ Queue ──消费──→ render_thread ──write()──→ stdout
```

**状态栏刷新线程（refresh_thread）**  
每 250ms 向队列投递 `("_refresh", None)` 消息，渲染线程处理时执行状态栏重绘。刷新频率完全受队列串行化保护。

### 3.2 状态栏管理

通过 ANSI 控制码覆写实现局部刷新：

```
输出前：
  if self._bar_drawn > 0:
      stdout.write(f"\x1b[{N}A\x1b[0J")   # 上移 N 行并清除到底
      self._bar_drawn = 0

输出内容

输出后：
  for line in statusbar_lines:
      stdout.write(line + "\n")
  self._bar_drawn = len(statusbar_lines)
```

渲染顺序：`_erase_bar()` → 输出内容 → `_draw_bar()`

### 3.3 等待 LLM 响应时的状态栏（`_bar_below_prefix`）

agent 输出前缀（`orzooo ❯ `）时带 `end=""`，光标停在行中。这期间若状态栏直接擦写，会连同 prefix 一起清除。

为此引入三阶段状态机（详见 [终端显示机制深度解析](terminal-display-internals.md) 第三章）：

```
阶段 1  print_assistant_prefix() 输出 "orzooo ❯ "（end=""）
        → _bar_suspended = True，状态栏暂停重绘

阶段 2  _refresh 收到，_bar_suspended=True 但还未流式
        → 输出 \n 把光标推到下一行
        → 正常绘制状态栏（显示在 prefix 下方）
        → _bar_below_prefix = True

        终端效果：
          orzooo ❯
          ⚡ Tasks [████] 2/4  2 running

阶段 3  首个 stream token 到来
        → ESC[{bar_drawn+1}A ESC[0J  上移多行并清除
        → 光标回到 "orzooo ❯ " 末尾
        → stream 内容紧接 prefix 输出
```

### 3.4 输入时的精确同步

`prompt_user()` 和 `confirm()` 在主线程阻塞执行，需要暂停渲染线程：

```python
def _enter_input_mode(self):
    # 1. 告知刷新线程停止投递 _refresh
    self._refresh_paused.set()
    # 2. 投入哨兵消息，等待其被消费 = 队列完全清空
    self._q.put(_Msg("_noop", None))
    self._q.join()
    # 3. 此时渲染线程空闲，安全直接写屏幕
    self._erase_bar_direct()
```

使用哨兵消息（`_noop`）精确同步，而非 `time.sleep`，消除原实现中的竞态窗口。

### 3.5 流式状态异常恢复

`_StreamCtx.__exit__` 在发生异常时调用 `force_end_stream()` 而非 `stream_end()`：

```python
class _StreamCtx:
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._t.force_end_stream()   # 强制重置 _streaming 标志
        else:
            self._t.stream_end()
        return False
```

`cli/repl.py` 的 `KeyboardInterrupt` 和 `Exception` 处理也均调用 `_term.force_end_stream()`，确保中断后输入提示符正常显示。

---

## 4. 输出通道

### 4.1 普通输出 `term.print()`

支持所有 Rich markup：

```python
term.print("hello [bold]world[/bold]")
term.print(f"[green]✓[/green]  {msg}")
```

### 4.2 流式输出

```python
# 直接调用
for token in llm.stream():
    term.stream_token(token)
term.stream_end()

# 上下文管理器（推荐，异常安全）
with term.streaming() as write:
    for token in llm.stream():
        write(token)
```

内置 `<tool_use>...</tool_use>` 过滤——原始工具调用 JSON 块不显示给用户。

可通过 `--raw-output` CLI 参数、`MINI_AGENT_RAW_OUTPUT=1` 环境变量、运行期
`/raw-output` slash 命令，或直接调用 `term.set_raw_output(True)` 关闭这层
过滤，原样显示模型的全部流式输出（包括 `<tool_use>` 标签本身），用于调试
模型实际产出内容。

### 4.3 结构化输出

```python
term.rule("标题")
term.panel(content, title="面板")
term.syntax(code, "python")
term.markdown("**markdown** 文本")
```

### 4.4 状态栏

```python
term.update_statusbar(lines)   # 更新内容（不立刻重绘）
term.redraw_statusbar()        # 立刻触发重绘
```

---

## 5. 输入通道

### 5.1 用户 REPL 输入 `term.prompt_user()`

- 使用 `prompt_toolkit`（如已安装）：命令历史、方向键编辑、Tab 补全 slash 命令
- 降级方案：`sys.stdin.readline()` + ANSI 彩色提示符（`_ptk_failed` 标志避免重复失败）
- 自动管理状态栏：读取前擦除，读取后重绘

### 5.2 权限审批 `term.confirm()`

```python
choice = term.confirm(
    prompt_lines=[],
    choices="(y)es  (a)lways  (n)o  (d)eny-always",
    default="y",
)
```

- 使用 `sys.stdout.write` + flush 显示选项提示符（确保可见）
- 使用 `sys.stdin.readline()` 读取输入（行为确定，回显可靠）
- `KeyboardInterrupt` / `EOFError` 时默认返回 `"n"`

---

## 6. 各模块角色

| 模块 | 角色 |
|------|------|
| `ui/terminal.py` | **唯一写屏幕的地方**，渲染队列、状态栏、输入读取、Task 焦点控制 |
| `ui/renderer.py` | 适配层：历史 API 映射到 `terminal.term` |
| `ui/raw_key_listener.py` | 跨平台键盘监听（Unix: `/dev/tty` + `termios` / Windows: `msvcrt`） |
| `orchestrator/status_bar.py` | 构建状态栏内容，向 Terminal 注册回调，不直接写屏 |
| `orchestrator/plan_display.py` | 构建 plan 状态栏行和 Rich 树，通过 `terminal.term` 输出 |
| `permissions.py` | 通过 `term.print()` 和 `term.confirm()` 处理权限审批 |
| `llm/debug_logger.py` | 通过 `term.debug()` 输出调试信息 |
| `cli/repl.py` | 通过 `term.prompt_user()` 读取用户输入 |

### 禁止的做法

```python
# ❌ 不允许绕过 Terminal 直接写屏幕
print("hello")
sys.stdout.write("hello")
Console().print("hello")

# ✅ 统一通过 terminal
from mini_agent.ui.terminal import term
term.print("hello")
```

---

## 7. 数据流示意图

```
                   ┌─────────────────────────────────────────────┐
                   │              Queue（消息队列）               │
                   │                                             │
 agent.py          │  ("print", ...)        ("stream", token)   │
 ui/renderer.py ───→  ("rule", ...)         ("stream_end", _)   │
 permissions.py    │  ("statusbar", lines)  ("_noop", _)        │
 status_bar.py     │  ("syntax", ...)       ("_refresh", _)     │
                   │                   ("_force_end_stream", _)  │
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

## 8. 权限审批完整流程

```
agent 请求执行工具
        │
        ▼
permissions.py 拦截
        │
        ├── requires_approval=False → 直接通过
        ├── always_allow 列表 → 直接通过
        ├── deny_always 列表 → 直接拒绝
        └── 需要询问 → _prompt()
                │
                ├── term.print("🔧 Tool request: ...") ← 通过队列
                └── term.confirm("(y)es (a)lways (n)o (d)eny-always")
                        │    ← _enter_input_mode() 哨兵同步
                        │    ← sys.stdin.readline() 阻塞读取
                        │
                        ├── y / yes    → 本次允许
                        ├── a / always → 加入 always_allow
                        ├── n / no     → 本次拒绝
                        └── d / deny   → 加入 deny_always
                        └── Ctrl-C     → 默认拒绝（返回 "n"）
```

---

## 9. 可配置项

| 参数 | 说明 |
|------|------|
| `--verbose` / `-v` | 显示工具调用原始 JSON |
| `--no-stream` | 禁用流式输出 |
| `--debug-llm` | 启用 LLM 调试日志（`.claude/logs/` 目录） |
| `--debug-llm-console` | 同时在控制台打印调试信息 |
| `--simple-mode` | 简化显示模式（见 9.1）：关闭所有 ANSI 光标定位/擦除操作，状态栏完全不显示 |

| 环境变量 | 说明 |
|----------|------|
| `LLM_DEBUG` | 启用调试日志 |
| `LLM_DEBUG_CONSOLE` | 控制台调试输出 |
| `LLM_DEBUG_LOG_DIR` | 调试日志目录 |
| `MINI_AGENT_SIMPLE_MODE=1` | 等价于 `--simple-mode`，适合写进 Termux 的 shell 启动脚本固定开启 |

### 9.1 `--simple-mode`：精简终端降级显示

本文第 3.2～3.4 节描述的"擦除再重绘"机制依赖终端正确支持
`\x1b[NA`（光标上移）/ `\x1b[0J`（清除到屏底）这两条 ANSI 控制序列。
在 Termux 等光标控制支持不完整的环境下，这套机制会导致状态栏堆叠、
内容错位等排版问题，比普通日志式的顺序滚动输出体验更差。

`--simple-mode` 关闭本文第 3 章描述的全部机制——不再有 `_erase_bar()`
/ `_draw_bar()` / `_bar_below_prefix` 三阶段状态机，状态栏也不再以任何
形式显示（不是退化为"内容变化时打印一行"，是彻底不显示）；其余输出
（assistant 回复、工具调用信息、权限提示等）仍按正常顺序打印，只是
不再被状态栏的擦除/重绘逻辑包裹。

完整设计动机、消息分发逻辑、双重防御保证（确保 simple-mode 下任何
路径都不会触发擦除）、以及一个相关根因排查（`RawKeyListener` 曾经
意外破坏过同一终端设备的 `\n`→`\r\n` 自动转换），见
[终端显示机制深度解析](terminal-display-internals.md) 第九章。

---

*最后更新：2026-06（新增 Task 焦点模式、等待 LLM 时状态栏下移机制、`_bar_below_prefix` 三阶段状态机）*
