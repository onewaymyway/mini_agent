# Raw Output 模式说明

> `--raw-output` 模式下，工具调用结果不做任何截断，同时终端显示也不截断。

---

## 1. 背景

默认情况下，mini-agent 在两个层面对工具输出做长度限制：

| 层面 | 默认限制 | 控制字段 |
|------|----------|----------|
| **传给 LLM 的内容** | 按工具类型分策略截断（bash 默认数千字符） | `cfg.tool_trim` |
| **终端显示** | 2000 字符，超出显示 `…[truncated]` | `print_tool_result(truncate=2000)` |

这两个限制在大多数场景下有益——防止超长输出消耗上下文窗口或刷屏。但在调试、数据提取、查看完整日志等场景下会造成信息丢失。

---

## 2. 开启方式

**CLI 启动参数**：

```bash
mini-agent --raw-output
```

**REPL 中切换**（运行时开关）：

```
/raw-output
```

再次执行同一命令即可关闭（toggle 语义）。

---

## 3. 行为变化

开启 `raw_output` 后：

### 3.1 LLM 接收完整工具结果

`ToolExecutor._trim_result()` 检测到 `cfg.raw_output == True` 时，跳过所有截断策略，直接返回完整字符串。

该检查优先级高于 `tool_result_trim_enabled`，即无论 trim 总开关状态如何，raw 模式始终不截断。

### 3.2 终端显示完整工具结果

`renderer.print_tool_result()` 接收 `truncate=None` 时跳过 `…[truncated]` 的添加，完整输出所有内容。

---

## 4. 实现细节

### tool_executor.py — `_trim_result()`

```python
def _trim_result(self, tool_name: str, result: str) -> str:
    # raw_output 模式下跳过所有截断
    if getattr(self.cfg, "raw_output", False):
        return result
    if not self.cfg.tool_result_trim_enabled:
        return result
    # ... 各工具截断策略 ...
```

### renderer.py — `print_tool_result()`

```python
def print_tool_result(tool_name: str, result: str, truncate: Optional[int] = 2000) -> None:
    display = result if (truncate is None or len(result) <= truncate) \
              else result[:truncate] + "\n…[truncated]"
```

### tool_executor.py — 调用侧

```python
R.print_tool_result(
    tc.name, result_str,
    truncate=None if getattr(self.cfg, "raw_output", False) else 2000,
)
```

---

## 5. 修改文件

| 文件 | 改动 |
|------|------|
| `tool_executor.py` | `_trim_result()` 开头加 raw_output 短路；`print_tool_result` 调用传 `truncate=None` |
| `ui/renderer.py` | `print_tool_result` 的 `truncate` 参数类型改为 `Optional[int]`，支持 `None` 表示不截断 |
