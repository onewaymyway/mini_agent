# Hooks 机制

mini_agent 支持类似 Claude Code 的 hooks：在关键事件点自动执行用户配置的
shell 命令，用于审计、拦截危险操作、自动格式化、注入额外上下文等。

## 配置文件位置

```
<project_root>/.agent/hooks.json   # 项目级
~/.agent/hooks.json                # 全局级
```

两者都会被加载；同一事件下，全局 hooks 先执行，项目 hooks 后执行。

## 配置格式

```json
{
  "PreToolUse": [
    {
      "matcher": "bash|write_file|patch_file",
      "command": "python3 .agent/hooks/check_dangerous.py",
      "timeout": 10
    }
  ],
  "PostToolUse": [
    {
      "matcher": "*",
      "command": "python3 .agent/hooks/log_tool_call.py"
    }
  ],
  "UserPromptSubmit": [
    {"command": "python3 .agent/hooks/inject_context.py"}
  ],
  "TurnEnd": [
    {"command": "python3 .agent/hooks/turn_end_notify.py", "timeout": 5}
  ]
}
```

字段说明：
- `command`：要执行的 shell 命令（跨平台解析：Windows 下使用 `posix=False`，其他平台使用标准 POSIX 解析）
- `matcher`：仅 `PreToolUse` / `PostToolUse` 有效。`"*"` 或省略表示匹配所有工具；
  也可用 `|` 分隔多个工具名，如 `"bash|write_file"`
- `timeout`：超时秒数，默认 30

## 支持的事件

| 事件 | 触发时机 | payload |
|---|---|---|
| `UserPromptSubmit` | 每轮用户输入被处理前 | `{"prompt": "..."}` |
| `PreToolUse` | 每次工具调用前 | `{"tool_name": "...", "tool_input": {...}}` |
| `PostToolUse` | 每次工具调用后 | `{"tool_name": "...", "tool_input": {...}, "tool_result": "..."}` |
| `PreCompact` | 历史压缩前 | （预留，尚未接入触发点） |
| `SessionStart` | 会话开始 | （预留，尚未接入触发点） |
| `SessionEnd` | 会话真正结束（REPL 退出：`EOFError` / `exit` / `quit` / `/exit` / `/quit`） | `{"session_id": "...", "tool_stats": {...}, "turns": N, "input_tokens": N, "output_tokens": N}` |
| `TurnEnd` | 每轮 Agent 回复完成、等待下一次用户输入之前 | `{"assistant_output": "...", "history": [{"role": "...", "content": "..."}, ...]}` |

> **2026-06 更新（Stage 1.3）**：`SessionEnd` 已从"预留未接"升级为真正接入。
> 触发点是 `agent.trigger_session_end()`，由 `cli/repl.py` 在进程退出前的两处
> 真实退出路径调用。`SessionStart` 仍是预留状态。
>
> `SessionEnd` 触发后，agent 还会紧接着跑一次轻量 LLM 反思调用，基于
> `tool_stats` + 最后若干轮用户意图轮次生成结构化 lesson 候选并写入记忆——
> 这是 hook 触发之外的**额外行为**，不依赖 hook 配置是否存在，由
> `cfg.memory.enabled` 控制是否执行。详见
> [记忆管理指南](memory-management-guide.md#lesson-memory) 中 SessionEnd 反思一节。

> **2026-06 更新**：新增 `TurnEnd` 事件。详见下方专节说明。

## Hook 如何与主流程交互

hook 命令通过 **stdin** 接收 JSON payload，可选地向 **stdout** 输出 JSON 来表达决策：

```json
{"decision": "block", "reason": "禁止删除 .env 文件"}
{"decision": "allow"}
{"context": "额外提示信息，会拼接到结果/prompt 后面"}
{"input": {"path": "safe/path.txt"}}
{"user_input": "继续执行下一步"}
```

行为规则：

- **退出码为 2**：视为 `block`（无论 stdout 是什么），常用于"一句话拒绝"场景，
  类似 `exit(2)` + 打印理由到 stdout/stderr
- **`decision: "block"`**（仅 `PreToolUse`）：阻断本次工具调用，
  结果会被替换为 `[blocked by hook: <reason>]`，工具不会真正执行
- **`context`**：
  - `UserPromptSubmit` 时，会以 `[hook context]\n...` 形式追加到用户消息后
  - `PostToolUse` 时，会以 `[hook note] ...` 形式追加到工具结果后
- **`input`**（仅 `PreToolUse`）：用于修改本次工具调用的参数（多个 hook 依次合并）
- **`user_input`**（仅 `TurnEnd`）：替代真实用户输入，直接驱动下一轮对话（详见下方）
- stdout 非 JSON 或为空：不阻断，非空内容当作 `context` 处理
- hook 执行报错/超时：不阻塞主流程，仅记录在内部 `error` 字段

## TurnEnd 事件详解

### 触发时机

每轮对话完成后，Agent 输出已渲染到终端、session 已保存，**正要等待用户下一次
输入之前**触发。对应代码路径：`agent.py::run_turn()` 末尾，在 `save_session()`
之后、`repl.py` 的 `prompt_user()` 之前。

### Payload

```json
{
  "assistant_output": "本轮 Agent 的最终回复文本",
  "history": [
    {"role": "user",      "content": "用户的问题"},
    {"role": "assistant", "content": "Agent 的回复"},
    "..."
  ]
}
```

`history` 为当前完整对话历史的浅拷贝（`role` + `content` 字段），
可用于分析对话上下文、决策是否需要接管。

### stdout 返回协议

| 返回内容 | 效果 |
|---|---|
| `{}` 或不返回 | 不做任何事，继续等待真实用户输入 |
| `{"context": "提示文本"}` | 向 hook runner 记录（当前不注入对话），继续等待用户输入 |
| `{"user_input": "..."}` | **替代真实用户输入**，直接驱动下一轮（见下方） |

### `user_input` 替代机制

当 `TurnEnd` hook 返回 `{"user_input": "..."}` 时：

1. REPL 跳过 `prompt_user()`（不等待键盘输入）
2. 在终端以灰色 `dim` 样式打印注入的输入行（`You ❯ <注入内容>`），与真实输入视觉区分
3. 直接调用 `agent.run_turn(注入内容)` 驱动下一轮
4. 下一轮结束后，再次触发 `TurnEnd` hook，若仍返回 `user_input` 则循环继续，
   直到 hook 返回 `{}` 为止，才回到正常 `prompt_user()` 等待

多个 `TurnEnd` hook 并存时，取**最后一个**返回 `user_input` 的值。

### 典型应用场景

**场景 1：简单通知**（项目内置示例 `.agent/hooks/turn_end_notify.py`）

每轮结束后向终端打印一行提示，不接管输入：

```python
#!/usr/bin/env python3
import json, sys

payload = json.load(sys.stdin)
history = payload.get("history", [])
turn_count = sum(1 for m in history if m.get("role") == "user")
print(f"\n✅ [Turn {turn_count} 结束] Agent 已回复", file=sys.stderr)
print("{}")
```

**场景 2：Agent-to-Agent 接管**（示例 `.agent/hooks/turn_end_auto_reply.py`）

外部 orchestrator 进程把下一条指令写入队列文件，本 hook 消费并注入：

```python
#!/usr/bin/env python3
import json, os, sys

QUEUE_FILE = os.environ.get("MINI_AGENT_AUTO_REPLY_QUEUE", "/tmp/mini_agent_auto_reply.txt")

payload = json.load(sys.stdin)

if os.path.isfile(QUEUE_FILE):
    lines = open(QUEUE_FILE).readlines()
    if lines:
        next_input = lines[0].rstrip("\n")
        open(QUEUE_FILE, "w").writelines(lines[1:]) if lines[1:] else os.remove(QUEUE_FILE)
        if next_input.strip():
            print(json.dumps({"user_input": next_input}))
            sys.exit(0)

print("{}")  # 队列为空，回到正常等待
```

使用方式：在另一个终端向队列写入指令，本 agent 自动消费：

```bash
echo "请总结一下刚才的对话" >> /tmp/mini_agent_auto_reply.txt
```

**场景 3：自动化测试**

用 `MINI_AGENT_AUTO_TURNS` 环境变量让 agent 自动跑 N 轮，无需人工干预：

```bash
MINI_AGENT_AUTO_TURNS=3 mini-agent
```

详见 `.agent/hooks/turn_end_auto_reply.py` 中的完整实现。

### 启用示例

`.agent/hooks.json`：

```json
{
  "TurnEnd": [
    {"command": "python3 .agent/hooks/turn_end_notify.py", "timeout": 5}
  ]
}
```

## 示例：阻止删除特定文件

`.agent/hooks/check_dangerous.py`：

```python
#!/usr/bin/env python3
import json, sys

payload = json.load(sys.stdin)
if payload["tool_name"] in ("delete_file", "bash"):
    inp = payload.get("tool_input", {})
    target = str(inp.get("path") or inp.get("command") or "")
    if ".env" in target or "rm -rf /" in target:
        print(json.dumps({"decision": "block", "reason": f"禁止操作敏感目标: {target}"}))
        sys.exit(0)

print(json.dumps({"decision": "allow"}))
```

`.agent/hooks.json`：

```json
{
  "PreToolUse": [
    {"matcher": "*", "command": "python3 .agent/hooks/check_dangerous.py"}
  ]
}
```

## 跨平台注意事项

hook runner（`hooks/runner.py`）已针对 Windows 做以下兼容处理：

- **编码**：stdin/stdout/stderr 全部使用二进制模式 + 显式 UTF-8 编解码，
  避免 Windows 系统默认编码（GBK 等）导致含 emoji / 中文的 payload 报
  `UnicodeEncodeError`
- **命令解析**：Windows 下使用 `shlex.split(cmd, posix=False)`，
  避免反斜杠路径（如 `C:\agent\hooks\x.py`）被当作转义符截断

hook 脚本本身需注意：`sys.stdin` 读取时同样建议显式指定 UTF-8：

```python
import sys, json
# Python 3.7+：用 sys.stdin.buffer 确保 UTF-8
payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
```

或直接用 `json.load(sys.stdin)`（在 runner 传 bytes 的情况下，Python 会自动处理）。

## CLI 调试命令

- `/hooks` 或 `/hooks list`：列出当前加载的所有 hook（按事件分组，含来源）
- `/hooks reload`：重新加载 `.agent/hooks.json` 和 `~/.agent/hooks.json`

## 与 Skill / 自定义子 Agent 的联动（动态注册）

`HookManager` 提供 `register_dynamic_from_dict(hooks_dict, source)` /
`unregister_source(source)`，允许 skill 或自定义子 agent profile
（frontmatter 中的 `hooks` 字段）在被激活时临时挂载专属 hook，
停用/任务结束时再移除。当前 loader/profile 已解析该字段，
動態挂载的接线点留给具体业务按需调用。

## 相关文档

- [记忆管理指南](memory-management-guide.md) — `SessionEnd` 触发后的反思 LLM 调用如何生成 lesson
- [history 类型化设计](history-typed-design.md) — `is_turn_boundary()` 如何为反思调用截取用户意图轮次

---

*最后更新：2026-06（新增 `TurnEnd` 事件：一轮结束 hook，支持终端通知、agent-to-agent 接管、自动化测试；`runner.py` 跨平台编码修复）*
