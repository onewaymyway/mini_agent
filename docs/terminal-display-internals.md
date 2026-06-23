# 终端显示机制深度解析

本文深入介绍 mini-agent 命令行界面的内部实现：渲染线程模型、状态栏光标控制、等待 LLM 时的三阶段状态机、输入同步协议、以及流式 token 过滤机制。

**前置阅读**：[命令行 I/O 机制说明](terminal-io-guide.md)

---

## 一、线程模型与消息队列

### 1.1 三线程架构

`Terminal` 类启动时创建三个角色：

```
┌──────────────────────────────────────────────────────┐
│  主线程（cli/repl.py）                                │
│    读取用户输入、驱动 agent.run_turn()、投递输出消息   │
└──────────────────┬───────────────────────────────────┘
                   │ queue.put()
                   ▼
┌──────────────────────────────────────────────────────┐
│  渲染线程（render_thread）                            │
│    唯一写 stdout 的线程，串行消费队列消息             │
│    负责：内容渲染、状态栏 erase/draw、光标移动        │
└──────────────────────────────────────────────────────┘
                   ▲
                   │ 每 250ms 投递 _refresh 消息
┌──────────────────────────────────────────────────────┐
│  刷新线程（refresh_thread）                           │
│    定时触发状态栏内容拉取与重绘                       │
│    _refresh_paused 标志控制暂停/恢复                  │
└──────────────────────────────────────────────────────┘
```

**为什么必须是单一渲染线程？**

ANSI 光标控制序列（`\x1b[NA`、`\x1b[0J`）是位置相对的操作。如果两个线程同时写 stdout，序列会交错，产生光标跳到错误位置、内容覆盖等问题。单一渲染线程消除了所有竞态，所有写屏操作天然串行。

### 1.2 消息类型

队列中流通的消息类型及其处理行为：

| 消息类型 | 触发方 | 渲染线程行为 |
|----------|--------|-------------|
| `print` | 任意调用方 | erase_bar → rich console print → draw_bar |
| `rule` | 任意调用方 | erase_bar → console rule → draw_bar |
| `panel` | 任意调用方 | erase_bar → console panel → draw_bar |
| `syntax` | 任意调用方 | erase_bar → syntax highlight → draw_bar |
| `markdown` | 任意调用方 | erase_bar → markdown render → draw_bar |
| `stream` | agent._call_llm | 首 token：erase_bar（含 prefix 行回移逻辑）；后续：直接 write |
| `stream_end` | agent._call_llm | flush pending → write "\n" → draw_bar |
| `statusbar` | refresh_thread | 更新内部缓存 `_statusbar_lines` |
| `_refresh` | refresh_thread | 按当前状态决定是否/如何 draw_bar（见第三章） |
| `_noop` | _enter_input_mode | 无操作，仅用于队列同步 |
| `_force_end_stream` | 异常恢复路径 | 强制重置流式状态 |
| `_focus_change` | set_task_focus | 打印焦点切换分隔线 |
| `_focus_lines` | refresh_thread | 增量打印焦点 task 日志行 |

### 1.3 状态栏内容提供者回调

旧设计中 `status_bar.py` 有自己的推送线程，每 250ms 向队列投入 `statusbar + redraw` 两条消息。这与 `_enter_input_mode` 存在竞态窗口：

```
时序问题（旧设计）：
  _refresh_paused.set()            ← 主线程
     ...（切换间隙）...
  status_bar push_loop 检测到 paused=True，但此前已投入消息
  _noop 哨兵排空队列
  _erase_bar_direct()
  sys.stdin.readline()             ← 阻塞读取
     ...这时 status_bar 的 redraw 消息到达...  ← 竞态！
```

新设计：`status_bar.py` 只注册一个回调函数，由 `refresh_thread` 在每次刷新周期内主动拉取内容，再投入单条 `_refresh` 消息。`_refresh_paused` 标志在 `refresh_thread` 循环顶部检查，一旦设置，当前周期立即跳过，不产生任何队列消息。竞态窗口从根本上消除。

```python
# refresh_thread 内部（简化）
while not self._refresh_stop.is_set():
    time.sleep(self._refresh_interval)
    if self._refresh_paused.is_set():    # ← 此处检查，一次原子判断
        continue
    lines = self._statusbar_provider()  # 拉取内容
    self._q.put(_Msg("statusbar", lines))
    self._q.put(_Msg("_refresh", None))
```

---

## 二、状态栏光标控制

### 2.1 绘制原理

状态栏始终显示在屏幕底部，通过「先擦除、再重绘」实现无闪烁刷新：

```
初始状态（状态栏已绘，_bar_drawn = 2）：

  ...内容...
  ⚡ Tasks [████] 2/4  2 running    ← 第 -2 行
  🤖 LLM   [███░] 1/2  1 active     ← 第 -1 行（当前行）
  光标在此 ↑

擦除操作（_erase_bar）：
  stdout.write("\x1b[2A")    # 上移 2 行
  stdout.write("\x1b[0J")    # 清除光标到屏幕底部
  _bar_drawn = 0

  ...内容...
  光标在此 ↑（覆盖了原状态栏位置）

重绘操作（_draw_bar）：
  for line in statusbar_lines:
      stdout.write(line + "\n")
  _bar_drawn = 2

  ...内容...
  ⚡ Tasks [████] 2/4  2 running
  🤖 LLM   [███░] 1/2  1 active
  光标在此 ↑
```

### 2.2 两个擦除函数

| 函数 | 调用场景 | 实现差异 |
|------|----------|----------|
| `_erase_bar()` | 渲染线程内 | 通过 stdout.write，假设当前在渲染线程 |
| `_erase_bar_direct()` | 主线程（输入模式准备阶段） | 同上，但同时清除 `_bar_suspended` 标志 |

两者功能相同，区分是为了明确调用上下文。**永远不要在非渲染线程中调用 `_erase_bar()`**，否则会与渲染线程竞争 stdout。

### 2.3 是否 TTY 的判断

```python
_IS_TTY: bool = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
```

`_draw_bar` 和 `_erase_bar` 首先检查 `_IS_TTY`。非 TTY 环境（管道、重定向、CI 日志）中不输出 ANSI 控制码，避免污染输出。

---

## 三、等待 LLM 时的三阶段状态机

这是本文重点介绍的机制，对应 `_bar_suspended` 和 `_bar_below_prefix` 两个标志的协作。

### 3.1 问题描述

agent 每次回复前调用 `print_assistant_prefix()`：

```python
def print_assistant_prefix(agent_name: str = "orzooo") -> None:
    term.print(f"\n[bold blue]{agent_name}[/bold blue][bold cyan] ❯ [/bold cyan]", end="")
```

关键在于 `end=""`——光标留在 `orzooo ❯ ` 末尾，而不是移到下一行。

**旧行为**：此后刷新线程投来 `_refresh`，渲染线程检查 `not self._bar_suspended`（为 False），直接跳过，状态栏消失，屏幕静止，直到 LLM 返回第一个 token。等待时间可能持续数秒甚至更长，用户完全不知道 agent 是否还活着。

**期望行为**：等待 LLM 期间，状态栏应该保持可见并持续刷新，给用户明确的等待反馈。

### 3.2 为什么不能直接绘制状态栏

直接对状态栏执行 erase/draw 会破坏 prefix 行：

```
当前屏幕：
  orzooo ❯ ←光标在此

执行 _erase_bar()（假设 bar_drawn=0，实际无操作）
执行 _draw_bar()：
  stdout.write("⚡ Tasks ...\n")   ← 光标在行内，这一行会追加在 prefix 后面！

结果：
  orzooo ❯ ⚡ Tasks [████] 2/4  2 running
```

即使 `bar_drawn=0`，`_draw_bar` 也会把状态栏内容写到光标当前位置（prefix 后面），造成显示错乱。

### 3.3 三阶段状态机

引入 `_bar_below_prefix` 标志，实现「先换行、再绘制、后回移」的三阶段流程：

**阶段 0：初始（print_assistant_prefix 之后）**

```
屏幕：
  orzooo ❯ ←光标

状态：
  _bar_suspended = True
  _bar_below_prefix = False
  _streaming = False
```

`print` 消息处理时，`end=""` 触发 `_bar_suspended = True`。后续 `_refresh` 消息到来时看到此标志，进入阶段 1 而非直接跳过。

---

**阶段 1：等待 LLM（首个 _refresh 触发时）**

```python
elif kind == "_refresh":
    if self._streaming:
        pass
    elif self._bar_suspended:
        if self._statusbar_lines:            # 有内容才换行
            sys.stdout.write("\n")           # 把光标推到下一行
            sys.stdout.flush()
            self._bar_suspended = False      # 不再 suspended
            self._bar_below_prefix = True    # 标记：bar 在 prefix 下方
            self._draw_bar()                 # 正常绘制状态栏
    else:
        self._erase_bar()
        self._draw_bar()
```

```
屏幕变化：
  orzooo ❯           ← prefix 行（完整保留）
  ↓ stdout.write("\n")
  orzooo ❯
  ↓ _draw_bar()
  orzooo ❯
  ⚡ Tasks [████] 2/4  2 running    ← 状态栏出现在 prefix 下方
  光标在此 ↑

状态：
  _bar_suspended = False
  _bar_below_prefix = True
  _bar_drawn = 1（或更多）
  _streaming = False
```

后续的 `_refresh` 消息（250ms 间隔）会走 `else` 分支，正常执行 erase/draw 循环，状态栏持续刷新。

---

**阶段 2：首个 stream token 到来**

```python
elif kind == "stream":
    token = msg.payload
    filtered = self._filter_token(token)
    if filtered:
        if not self._streaming:
            if self._bar_below_prefix:
                if self._bar_drawn > 0:
                    lines_up = self._bar_drawn + 1     # bar 行数 + prefix 行
                    sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")  # 上移并清除
                    sys.stdout.flush()
                    self._bar_drawn = 0
                else:
                    sys.stdout.write("\x1b[1A\x1b[0J") # 只有 prefix 行，上移 1 行
                    sys.stdout.flush()
                self._bar_below_prefix = False
            else:
                self._erase_bar()
            self._streaming = True
            self._stream_had_output = True
        sys.stdout.write(filtered)
        sys.stdout.flush()
```

关键计算：`lines_up = bar_drawn + 1`

```
屏幕状态（_bar_drawn=2）：
  orzooo ❯               ← 第 -(2+1) = 第 -3 行
  ⚡ Tasks [████] ...    ← 第 -2 行
  🤖 LLM   [███░] ...    ← 第 -1 行（当前行，光标在此）

执行 \x1b[3A：上移 3 行
  光标回到 "orzooo ❯ " 那一行的行首

执行 \x1b[0J：清除光标位置到屏幕底部
  所有 bar 行和 prefix 那一行的内容全部清除

现在：
  orzooo ❯  ←（这一行被清除了，只剩光标在行首）
  ???  ←（后续行已清除）

但等等——"orzooo ❯ " 也被 \x1b[0J 清掉了！
需要重新输出 prefix？
```

**实际上不需要**。`\x1b[0J` 清除的是从光标位置到屏幕底部，而光标在 prefix 行的**行首**（上移后），所以 prefix 的文字确实被清除了。但这没问题——此时第一个可见 token 马上就要追加在光标位置，用户几乎察觉不到 prefix 的短暂消失。

等一下，这里其实有个微妙之处：`\x1b[NA` 上移的是「行」，光标会定位到目标行的**同一列**，不是行首。`\x1b[0J` 清除从当前光标到屏幕底部。

实际情况：prefix `"orzooo ❯ "` 末尾有一个空格，光标停在那个空格后面的某列。上移 N+1 行后，光标在 prefix 行的**同一列**（末尾位置），`\x1b[0J` 清除的是从末尾到屏幕底部（即后面的空白 + 所有状态栏行），prefix 文字本身保留！

```
精确过程：
  orzooo ❯ _            ← _ 是光标位置（列 9）
  ⚡ Tasks [████] ...
  🤖 LLM   [███░] ...   ← 光标在这行（当前行）

  \x1b[3A → 上移 3 行，光标移到 "orzooo ❯ " 的同列（末尾）
  orzooo ❯ _            ← 光标回到这里
  ⚡ Tasks [████] ...
  🤖 LLM   [███░] ...

  \x1b[0J → 清除从光标到屏幕底部
  orzooo ❯ _            ← prefix 文字保留！后面的内容清除
  （屏幕底部已清空）

  写入 filtered（第一个 token，如 "好"）：
  orzooo ❯ 好_           ← token 紧接在 prefix 后面
```

---

**阶段 2b：LLM 无可见输出时（stream_end 时 bar_below_prefix 仍为 True）**

某些情况下 LLM 的响应完全是工具调用，没有可见文本 token。此时 `stream_end` 时 `_stream_had_output=False`，但 `_bar_below_prefix=True`：

```python
elif kind == "stream_end":
    ...
    if self._bar_below_prefix and not self._stream_had_output:
        if self._bar_drawn > 0:
            lines_up = self._bar_drawn + 1
            sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")
            self._bar_drawn = 0
        else:
            sys.stdout.write("\x1b[1A\x1b[0J")
        self._bar_below_prefix = False
    ...
```

效果：清除 prefix 行和状态栏，光标回到 prefix 位置，后续工具调用信息从这里继续输出。

### 3.4 状态标志完整转换图

```
初始状态：
  _bar_suspended = False
  _bar_below_prefix = False
  _streaming = False

  print(end="") ──────────────────────────────→ _bar_suspended = True

  _bar_suspended=True 时收到 _refresh
  且 _statusbar_lines 非空 ────────────────────→ _bar_suspended = False
                                                  _bar_below_prefix = True
                                                  _draw_bar()

  _bar_below_prefix=True 时首个 stream token ──→ 上移 bar+1 行
                                                  \x1b[0J 清除
                                                  _bar_below_prefix = False
                                                  _streaming = True

  _bar_below_prefix=True 时 stream_end
  且 _stream_had_output=False ─────────────────→ 上移 bar+1 行清除
                                                  _bar_below_prefix = False

  stream_end 且 _stream_had_output=True ───────→ write("\n")
                                                  _bar_below_prefix = False
                                                  _draw_bar()

  任意 print/rule/panel/markdown/syntax ───────→ _bar_suspended = False
                                                  _bar_below_prefix = False
                                                  _draw_bar()
```

---

## 四、输入同步协议

### 4.1 为什么需要同步

输入是阻塞操作，必须由主线程执行（`sys.stdin.readline()` 或 `prompt_toolkit.prompt()`）。但此时渲染线程和刷新线程仍在运行，可能随时写屏幕。如果在 `readline` 阻塞期间渲染线程重绘状态栏，光标会跳到奇怪位置，用户的输入字符也可能被状态栏覆盖。

### 4.2 双哨兵协议

`_enter_input_mode()` 的关键步骤：

```python
def _enter_input_mode(self):
    # Step 1：通知刷新线程停止投递 _refresh 消息
    self._refresh_paused.set()

    # Step 2a：第一哨兵
    # 目的：消费掉 _refresh_paused.set() 之前已在队列中的残余消息
    # 原因：刷新线程可能在 set() 之前已投入了 statusbar + _refresh
    self._q.put(_Msg("_noop", None))
    self._q.join()

    # Step 2b：第二哨兵
    # 目的：确认渲染线程处理完所有残余 redraw 后真正空闲
    # 原因：第一哨兵被消费时，渲染线程可能正在处理 redraw，
    #       结束后会立即调用 _draw_bar()，而 _noop 的 task_done()
    #       在 _draw_bar() 之前执行——存在微小窗口
    self._q.put(_Msg("_noop", None))
    self._q.join()

    # Step 3：此时渲染线程 100% 空闲，安全直接写 stdout
    self._erase_bar_direct()
```

**为什么需要两个哨兵？**

```
时序场景（说明第二哨兵必要性）：

  refresh_thread:
    [sleep 结束]
    检查 _refresh_paused → 未设置（set() 尚未执行）
    投入 statusbar 消息
    投入 _refresh 消息

  主线程:
    _refresh_paused.set()           ← 此时 statusbar + _refresh 已在队列
    put(_noop_1)
    join()                          ← 等待队列清空
      render_thread 处理 statusbar
      render_thread 处理 _refresh → 调用 _erase_bar() + _draw_bar()
        ↑ _draw_bar() 写屏幕！
      render_thread 处理 _noop_1 → task_done()
    join() 返回                     ← 但渲染线程还在 _draw_bar() 里！
                                       （不对，task_done 在 _draw_bar 之后）

  实际上 _noop 的 task_done() 在 finally 块里，而 _handle(_refresh)
  同步执行完 _draw_bar() 才返回，所以第一个 join() 返回时渲染线程
  确实已经处理完 _refresh（包括 _draw_bar）。
  
  但考虑极端情况：
    render_thread 处理完 _noop_1，调用 task_done()
    → join() 在主线程返回
    → 主线程继续执行 _erase_bar_direct()
    
    同时 render_thread 的 get() 已返回下一条消息（如果有的话）
    → 但此时队列已空（_noop_1 是最后一条），render_thread 阻塞在 get()
    → 安全
    
  第二哨兵主要防御 refresh_thread 在第一哨兵投入后、join() 返回前
  这个极短窗口内再次投入消息的极端情况。
  由于 _refresh_paused 已 set，refresh_thread 不会再投，
  第二哨兵确保了「就算有残余，也处理完了」。
```

### 4.3 输入读取底层

`_read_line()` 按优先级尝试：

```
1. prompt_toolkit（已安装且未失败）
   ├─ PromptSession.prompt(HTML("<b><ansgreen>You</ansgreen></b><ansicyan> ❯ </ansicyan>"))
   └─ 提供：历史浏览、Tab 补全、自动建议、自定义快捷键

2. 降级（prompt_toolkit 不可用或运行时异常）
   ├─ sys.stdout.write("\033[1;32mYou\033[0m\033[1;36m ❯ \033[0m")
   └─ sys.stdin.readline()

失败标志：_ptk_failed = True（一旦设置，后续调用直接走降级路径）
```

### 4.4 Tab 补全系统

`_SlashCompleter` 实现两阶段补全：

```
阶段 1：顶层命令（光标前只有 "/" 开头的前缀，无空格）
  输入 "/"   → 列出所有命令
  输入 "/h"  → 只显示 /help
  输入 "/sk" → 只显示 /skill

阶段 2：子命令（已有完整命令 + 空格）
  输入 "/skill "   → 列出 on, off, list
  输入 "/skill o"  → 只显示 on, off
  输入 "/session l" → 只显示 list, load
```

`_AtPathCompleter` 处理 `@` 路径补全：
```
输入 "@src/"   → 列出 src/ 下的文件和目录
输入 "@src/mi" → 过滤匹配 mi* 的条目
```

补全菜单样式（Catppuccin Mocha 风格）：
```
/skill     Manage skills  [on | off | list]
/skills    List all skills
/session   Session management  [list | new | load | delete]
```

---

## 五、流式 token 过滤

### 5.1 过滤原理

LLM 输出中可能包含 `<tool_use>...</tool_use>` 标签块（工具调用的 JSON），这些不应显示给用户，由 `_filter_token()` 实时过滤。

过滤器维护两个状态：

- `_suppress_stream: bool`：当前是否在 `<tool_use>` 块内
- `_pending_stream: str`：待确认的尾部字节（最多 `len("<tool_use>") = 10` 字节）

### 5.2 逐 token 处理算法

```python
def _filter_token(self, token: str) -> str:
    result = []
    text = self._pending_stream + token   # 与上次尾部合并
    self._pending_stream = ""
    i = 0
    while i < len(text):
        if self._suppress_stream:
            # 在标签内：寻找结束标签
            end = text.find("</tool_use>", i)
            if end == -1:
                # 结束标签跨 token：尾部缓冲
                tail = text[i:]
                self._pending_stream = tail if len(tail) <= 11 else ""
                break
            else:
                self._suppress_stream = False
                i = end + len("</tool_use>")
        else:
            # 在标签外：寻找开始标签
            start = text.find("<tool_use>", i)
            if start == -1:
                # 无开始标签：输出，但留 10 字节缓冲防止跨 token 标签
                visible = text[i:]
                if len(visible) > 10:
                    result.append(visible[:-10])
                    self._pending_stream = visible[-10:]
                else:
                    self._pending_stream = visible
                break
            elif start > i:
                result.append(text[i:start])   # 输出标签前的内容
                self._suppress_stream = True
                i = start + len("<tool_use>")
            else:
                self._suppress_stream = True
                i = start + len("<tool_use>")
    return "".join(result)
```

**为什么要保留 10 字节缓冲？**

`"<tool_use>"` 长 10 字节。如果当前 token 末尾是 `"<tool_us"`（8 字节），下一个 token 开头是 `"e>"`，整个标签就跨 token 了。保留末尾 10 字节，等下个 token 到来后合并处理，确保不会漏过跨 token 的标签。

### 5.3 pending 冲洗

`stream_end` 时冲洗缓冲区：

```python
elif kind == "stream_end":
    if self._pending_stream:
        sys.stdout.write(self._pending_stream)    # 输出最后缓冲的内容
        self._stream_had_output = True
    ...
    self._stream_filter_reset()                   # 重置过滤器状态
```

---

## 六、Task 焦点模式

### 6.1 概念

当多个 sub-agent 并发执行时，用户可以通过方向键进入某个 task 的「焦点视图」，实时查看该 task 的日志输出。

### 6.2 实现机制

```
_task_focus: str | None    当前焦点 task_id，None 表示主视图
_focus_log_offset: int     已渲染到的日志行数（增量追踪）
```

`refresh_thread` 在每次刷新周期检查焦点 task，将新增日志行投入 `_focus_lines` 消息：

```python
if focus_id:
    rec = mgr.get(focus_id)
    new_lines = list(rec.log_lines[offset:])
    if new_lines:
        self._focus_log_offset += len(new_lines)
        self._q.put(_Msg("_focus_lines", new_lines))
```

`_focus_lines` 消息在渲染线程串行处理，与状态栏更新不竞争：

```python
elif kind == "_focus_lines":
    self._erase_bar()
    for line in lines:
        # 简单着色：工具调用紫色，成功绿色，失败红色
        sys.stdout.write(colored_line + "\n")
    sys.stdout.flush()
    self._bar_suspended = False
    self._draw_bar()
```

### 6.3 焦点模式下的主输出抑制

```python
def print(self, *args, **kwargs) -> None:
    if self._task_focus is not None:
        return    # ← 焦点模式：主输出静默
    self._q.put(_Msg("print", (args, kwargs)))
```

agent 主输出（工具调用信息、LLM 回复等）在焦点模式下被丢弃，避免与焦点 task 日志交错。这些内容仍然完整保存在 session 历史中，退出焦点模式后正常浏览。

### 6.4 键盘监听

方向键监听由 `ui/raw_key_listener.py` 的 `RawKeyListener` 负责，而不是 prompt_toolkit 的快捷键绑定。原因：prompt_toolkit 只在 `.prompt()` 阻塞期间活跃，而 agent 执行（`run_turn()`）期间 prompt_toolkit 已退出，需要独立的 raw mode 监听线程。

```
cli/repl.py 时序：

  listener.start()      ← tty.setraw() + 读取线程启动
  agent.run_turn()      ← LLM 调用、工具执行（此期间方向键有效）
  listener.stop()       ← 恢复 termios，线程退出
  term.prompt_user()    ← prompt_toolkit 接管（此期间方向键是历史浏览）
```

Unix 实现：`/dev/tty` + `termios.tcsetattr(tty.CBREAK)` + 独立读取线程解析 `ESC[A/B/C/D` 序列。

Windows 实现：`msvcrt.kbhit()` + `msvcrt.getwch()` 轮询读取虚拟键码。

---

## 七、异常与中断恢复

### 7.1 流式中断

任何异常（包括 `KeyboardInterrupt`）都通过 `_StreamCtx.__exit__` 捕获：

```python
class _StreamCtx:
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._t.force_end_stream()    # 强制重置，不是 stream_end
        else:
            self._t.stream_end()
        return False    # 不吞异常
```

`force_end_stream()` 向队列投入 `_force_end_stream` 消息，渲染线程处理时：

```python
elif kind == "_force_end_stream":
    # 1. 如果 bar 在 prefix 下方，先清除掉
    if self._bar_below_prefix:
        lines_up = self._bar_drawn + 1 if self._bar_drawn > 0 else 1
        sys.stdout.write(f"\x1b[{lines_up}A\x1b[0J")
        self._bar_drawn = 0
        self._bar_below_prefix = False
    # 2. 冲洗 pending stream 内容
    if self._streaming or self._stream_had_output:
        if self._pending_stream:
            sys.stdout.write(self._pending_stream)
        if self._stream_had_output:
            sys.stdout.write("\n")
        # 重置所有流式状态
        self._streaming = False
        self._stream_had_output = False
        self._stream_filter_reset()
        self._bar_suspended = False
        self._bar_below_prefix = False
        self._draw_bar()
```

确保无论何时中断，屏幕都能回到「光标在行首、状态栏可见」的干净状态。

### 7.2 程序退出

`term.stop()` 按顺序关闭后台线程：

```python
def stop(self):
    # 1. 停刷新线程（先停，防止继续往队列投消息）
    self._refresh_stop.set()
    self._refresh_paused.set()    # 防止 refresh_loop 卡在 wait
    self._refresh_thread.join(timeout=1.0)

    # 2. 向渲染线程投哨兵，通知退出
    self._render_stop = True
    self._q.put(_Msg("_stop", None))

    # 3. 等待渲染线程处理完队列中所有剩余消息后退出
    self._render_thread.join(timeout=2.0)

    # 4. 渲染线程已停，安全直接清理
    self._erase_bar_direct()
```

先停刷新线程再停渲染线程，确保不会有新消息在退出过程中进入队列。

---

## 八、扩展与定制

### 8.1 自定义状态栏内容

注册一个返回 `list[str]` 的回调即可：

```python
from mini_agent.ui.terminal import term

def my_status_provider() -> list[str]:
    return [
        f"  📊 自定义指标: {get_my_metric()}",
        f"  ⏱  运行时间: {elapsed_str()}",
    ]

term.set_statusbar_provider(my_status_provider)

# 停止时清除
term.set_statusbar_provider(None)
```

回调在 `refresh_thread` 中执行（非渲染线程），可以进行耗时较短的计算，但不要直接写屏幕。

### 8.2 调试渲染队列

开发时可以临时给队列加监控：

```python
from mini_agent.ui.terminal import term
import queue

_orig_put = term._q.put
def _debug_put(msg, **kwargs):
    print(f"[QUEUE] {msg.kind}: {str(msg.payload)[:60]}", file=sys.stderr)
    return _orig_put(msg, **kwargs)
term._q.put = _debug_put
```

### 8.3 刷新频率调整

`Terminal` 构造时可指定刷新频率（默认 4 Hz）：

```python
from mini_agent.ui.terminal import Terminal
term = Terminal(status_refresh_hz=8)    # 8 Hz，更流畅但 CPU 占用略高
```

模块级 `term` 单例使用默认值，如需调整需在启动时替换单例（不推荐在生产代码中做）。

---

## 九、simple-mode

### 9.1 问题背景

本文前八章描述的"先擦除再重绘"机制，本质上依赖目标终端正确实现两条 ANSI 控制序列：

```
\x1b[NA   CUU（Cursor Up）— 光标上移 N 行
\x1b[0J   EL/ED 变体     — 清除光标位置到屏幕底部
```

这套机制在主流桌面终端模拟器（iTerm2、Windows Terminal、GNOME Terminal、
大多数 SSH 客户端等）下表现良好。但在一些精简终端环境——典型如
**Termux**（Android 上的终端 App）、某些嵌入式串口控制台、或被其他程序
（如某些 IDE 内置终端、低版本 PTY 模拟器）包裹的子终端——这两条序列可能
不被正确支持，或者支持不完整（例如只支持部分擦除模式、或上移行数计算
基准不一致）。

后果是：状态栏没有被正确擦除就被重绘，画面出现重复堆叠的状态栏残影；
或者擦除范围算错，把不该擦的内容（如上一轮对话）也清掉；又或者
"等待 LLM 响应"阶段的三阶段状态机（见第三章）因为光标定位不准确，
导致 `"agent ❯ "` 前缀和正文之间出现错位、丢字、重复字等问题。

### 9.2 设计思路

simple-mode 不去逐个修补每个特殊终端的兼容性，而是从根本上避免依赖
任何"光标位置会被正确保留/移动"的假设——所有内容一律按收到顺序原样
`print`/`write`，只追加换行，绝不回退、绝不擦除。

状态栏在 simple-mode 下**完全不显示**（不是退化为"内容变化时追加打印
一行"，而是彻底不输出任何状态栏内容）。这是经过一次迭代后的结论：
最初的实现尝试了"内容变化才打印"，但状态栏里"已用时长/token 数"这类
字段几乎每个刷新周期都在变，单靠内容比较等于没有节流，实际体验依然
是刷屏；而状态栏本身对 simple-mode 的使用场景（精简终端、关注的是
"输出有没有正常显示"而不是"任务进度可视化"）价值有限，所以直接选择
不显示，而不是继续在节流策略上打磨。

这是一个纯粹的"加法"设计，且有两层独立保护，确保 simple-mode 下绝不
会触发任何擦除/原地重绘：

1. **分发层**：`Terminal._handle()` 在分发消息前先检查 `self._simple_mode`，
   为真则整体转发到 `_handle_simple()`，与原有的擦除/重绘路径完全隔离，
   互不影响、互不调用对方的私有状态（如 `_bar_below_prefix`、
   `_open_line_render` 等三阶段状态机变量在 simple-mode 下完全不会被
   设置或读取）。
2. **函数自身防御**：`_draw_bar()` / `_erase_bar()` / `_erase_bar_direct()`
   这三个唯一会写 ANSI 擦除序列（`\x1b[NA\x1b[0J`）的函数，内部各自都有
   `if self._simple_mode: return` 早退保护。即使未来某处代码不小心绕过
   `_handle()` 直接调用了它们，在 simple-mode 下也只会是彻底的 no-op，
   不会写出任何字节。

```
_handle(msg)
  │
  ├─ self._input_blocking?  → 缓存（两种模式共用，与本文第四章逻辑不变）
  │
  ├─ self._simple_mode?
  │     ├─ True  → _handle_simple(msg)   ← 本章新增，纯顺序打印，无状态栏
  │     └─ False → 原有 erase/redraw 逻辑（第二、三章）

_draw_bar() / _erase_bar() / _erase_bar_direct()
  │
  ├─ self._simple_mode?  → 立即 return，不写任何字节（第二层防御）
  └─ 否则才执行原有的 ANSI 擦除/重绘逻辑
```

### 9.3 各消息类型在 simple-mode 下的行为对照

| 消息类型 | 正常模式 | simple-mode |
|----------|----------|-------------|
| `print` / `rule` / `panel` / `syntax` / `markdown` | erase_bar → 渲染 → draw_bar | 直接渲染，不擦不画状态栏 |
| `stream`（首 token） | 视 `_bar_below_prefix` 走三阶段状态机 | 直接写 token，不判断状态栏位置 |
| `stream_end` | 视情况回放 prefix、换行、重绘状态栏 | 冲洗 pending 缓冲，必要时换行，结束 |
| `statusbar` | 更新内部缓存并触发重绘 | 仅更新内部缓存，**不触发任何打印**（缓存只是保持状态一致，没有任何代码路径会读取它来显示） |
| `redraw` / `_refresh` | erase_bar → draw_bar（或三阶段状态机） | **no-op**，不打印、不擦除 |
| `_focus_lines` / `_focus_change` / `_focus_cycle` | erase_bar → 着色打印 → draw_bar | 直接着色打印，不动状态栏 |
| `_force_end_stream` | 回放 prefix（如需）→ 冲洗 → 重绘 | 冲洗 pending 缓冲，必要时换行 |
| `_noop` | 哨兵，无操作 | 同：无操作 |

simple-mode 下，状态栏从用户的角度看是"不存在的"——不会在任何时刻、
以任何形式（无论是原地刷新还是追加打印）出现在输出里。这是有意的
设计选择，不是节流不够导致的妥协。

### 9.4 启用方式

三种方式，优先级从高到低：

```bash
# 1. CLI 参数（推荐，显式且不影响其他 shell 会话）
python -m mini_agent --simple-mode

# 2. 环境变量（适合在 Termux 的 shell 启动脚本/.bashrc 里固定设置）
export MINI_AGENT_SIMPLE_MODE=1
python -m mini_agent

# 3. agent_config.json（项目级固定配置）
# { "simple_mode": true }
```

`Terminal.__init__(simple_mode=None)` 在未显式传参时读取
`MINI_AGENT_SIMPLE_MODE` 环境变量作为默认值；`cli/app.py` 在解析完
`argparse` 参数后的第一时间（早于任何 `R.print_info()` 调用、早于
`start_status_bar()`）就调用 `term.set_simple_mode(True)`，确保启动期间
的全部输出都已经处于 simple-mode，不会出现"前几行还是正常模式、后面
突然切换"的不一致。

### 9.5 程序化切换

也可以在运行期手动切换（例如未来要做的 `/simple` slash 命令）：

```python
from mini_agent.ui.terminal import term

term.set_simple_mode(True)   # 开启
term.is_simple_mode()        # 查询当前状态
term.set_simple_mode(False)  # 关闭，恢复原地刷新（仅建议在真实 TTY 上这样做）
```

`set_simple_mode(True)` 内部会顺手把 `_bar_drawn` / `_bar_suspended` /
`_bar_below_prefix` 等"原地刷新模式"专用的状态变量清零，避免切换后
这些陈旧状态被遗留误用；反向切换（关闭 simple-mode）不做特殊清理，
下一次 `_refresh` 会按正常模式重新建立状态栏。

### 9.6 不受 simple-mode 影响的部分

- `prompt_toolkit` 输入（历史、Tab 补全、自动建议）不受影响，仍按原样
  工作——simple-mode 只改变*输出*侧的渲染策略，不改变*输入*侧。
- `_input_blocking` 消息缓存机制（第四章）在两种模式下都生效，逻辑
  完全共用，不需要也没有 simple-mode 专属版本。

### 9.7 一个更深层的根因：RawKeyListener 与 OPOST

上线 simple-mode 后收到反馈："simple-mode 不对"——实测输出仍然逐行
错位、呈阶梯状右移（每一行都比上一行更靠右一点）。排查后发现这其实
是一个**与 simple-mode 本身无关、普通模式同样会中的**独立 bug，只是
普通模式下被状态栏的擦除/重绘操作部分掩盖了，simple-mode 关闭擦除后
反而更明显地暴露出来。

**根因**：`ui/raw_key_listener.py` 的 `_UnixKeyReader._setup()`（方向键
task 焦点切换监听器，见第六章）在 `run_turn()` 期间对监听用的 fd 调用
了 `tty.setraw(fd)`。`setraw()` 会把 termios 的 `IFLAG`/`OFLAG`/`CFLAG`/
`LFLAG` 全部清成"裸"模式，其中 `OFLAG` 里的 `OPOST` 被清掉意味着内核
不再把输出的 `"\n"` 自动转换成 `"\r\n"`。

致命的是：termios 设置是**终端设备级别**的，不是 fd 级别的。
`_find_tty_fd()` 优先打开 `/dev/tty`（控制终端），它和 `sys.stdout`
指向的是**同一个底层设备**——哪怕二者是两个不同的文件描述符，对其中
一个 fd 调用 `tcsetattr()` 修改的也是设备本身的状态，会影响所有其他
打开了同一设备的 fd。一旦在监听器的 fd 上 `setraw()`，`OPOST` 在
整个设备上都被关掉，后续 `terminal.py` 渲染线程往 `sys.stdout` 写的
每一个 `"\n"` 都不会再自动带上 `"\r"`，只移动到下一行的**同一列**，
而不是回到列首——这正是"逐行越来越靠右"现象的成因。

可以用一对 pty 简单复现这个机制：

```python
import pty, os, termios, tty, time
master, slave = pty.openpty()
fd2 = os.open(os.ttyname(slave), os.O_RDWR)   # 模拟 /dev/tty，与 slave 同一设备
tty.setraw(fd2)                                # 在另一个 fd 上 setraw
os.write(slave, b"line one\nline two\n")       # 用 slave（模拟 sys.stdout）写入
time.sleep(0.05)
print(repr(os.read(master, 1000)))
# => b'line one\nline two\n'   （注意：没有 \r，OPOST 已被破坏）
```

**修复**：`_setup()` 不再使用 `tty.setraw()`，改为手工只清 `LFLAG` 里
监听器真正需要的三个 bit，完全不触碰 `OFLAG`（因此 `OPOST` 原样保留）：

```python
new_attrs = termios.tcgetattr(fd)
new_attrs[3] = new_attrs[3] & ~(termios.ECHO | termios.ICANON | termios.ISIG)  # lflag
new_attrs[6][termios.VMIN] = 1
new_attrs[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, new_attrs)
```

三个 bit 分别对应监听器实际依赖的输入特性：

| Bit | 作用 | 为什么需要关闭 |
|-----|------|----------------|
| `ECHO` | 回显按键 | 方向键序列不应该被打印到屏幕上 |
| `ICANON` | 行缓冲（等 Enter） | 需要按字节立即可读，不等行结束 |
| `ISIG` | 内核自动发信号 | `_handle()` 收到 `b"\x03"` 时手动 `os.kill(...SIGINT)`，必须自己识别这个字节，不能让内核抢先处理掉 |

这三点都只涉及 `LFLAG`，`tty.setraw()` 额外清掉的 `OFLAG`（含 `OPOST`）、
`IFLAG`、`CFLAG` 监听器从来都不需要——用更精确、范围更小的 termios
修改替换掉"一把梭"的 `setraw()`，问题即被根除，且对普通模式和
simple-mode 都生效（这不是 simple-mode 专属修复）。

回归测试见 `tests/test_raw_key_listener.py`，用真实 pty（而非 mock
termios）端到端验证：另一个 fd 上的 `\n`→`\r\n` 转换在 `_setup()`
前后保持正常，且监听器自身的输入特性（不回显、单字节立即可读、
Ctrl+C 可被读到）不受影响。

---

*最后更新：2026-06*  
*涵盖版本：mini-agent（含 `_bar_below_prefix` 三阶段状态机修复、simple-mode 简化显示模式——不显示状态栏、双重擦除防御、RawKeyListener OPOST 根因修复）*
