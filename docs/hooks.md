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
  "PostToolBatch": [
    {"command": "python3 .agent/hooks/batch_done.py"}
  ],
  "UserPromptSubmit": [
    {"command": "python3 .agent/hooks/inject_context.py"}
  ],
  "SessionStart": [
    {"command": "python3 .agent/hooks/on_session_start.py"}
  ],
  "SessionEnd": [
    {"command": "python3 .agent/hooks/on_session_end.py"}
  ],
  "TurnEnd": [
    {"command": "python3 .agent/hooks/turn_end_notify.py", "timeout": 5}
  ],
  "TaskCreated": [
    {"command": "python3 .agent/hooks/on_task_created.py"}
  ],
  "TaskCompleted": [
    {"command": "python3 .agent/hooks/on_task_completed.py"}
  ],
  "SubagentStart": [
    {"command": "python3 .agent/hooks/on_subagent_start.py"}
  ],
  "SubagentStop": [
    {"command": "python3 .agent/hooks/on_subagent_stop.py"}
  ],
  "Stop": [
    {"command": "python3 .agent/hooks/on_stop.py"}
  ],
  "PreCompact": [
    {"command": "python3 .agent/hooks/pre_compact.py"}
  ],
  "PostCompact": [
    {"command": "python3 .agent/hooks/post_compact.py"}
  ]
}
```

字段说明：
- `command`：要执行的 shell 命令（跨平台解析：Windows 下使用 `posix=False`，其他平台使用标准 POSIX 解析）
- `matcher`：仅 `PreToolUse` / `PostToolUse` / `PostToolUseFailure` 有效。`"*"` 或省略表示匹配所有工具；
  也可用 `|` 分隔多个工具名，如 `"bash|write_file"`
- `timeout`：超时秒数，默认 30

## 支持的事件

### Session 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `SessionStart` | session 初始化完成后（`_init_session` 完成时） | ❌ | `{"session_id": "...", "model": "...", "provider": "..."}` |
| `SessionEnd` | REPL 真正退出时（`EOFError` / `exit` / `quit` / `/exit` / `/quit`） | ❌ | `{"session_id": "...", "tool_stats": {...}, "turns": N, "input_tokens": N, "output_tokens": N}` |

### Prompt 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `UserPromptSubmit` | 每轮用户输入被处理前 | ✅ | `{"prompt": "..."}` |

### Tool 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `PreToolUse` | 每次工具调用前 | ✅ | `{"tool_name": "...", "tool_input": {...}}` |
| `PostToolUse` | 每次工具调用成功后 | ❌ | `{"tool_name": "...", "tool_input": {...}, "tool_result": "..."}` |
| `PostToolUseFailure` | 工具调用抛出异常时 | ❌ | `{"tool_name": "...", "tool_input": {...}, "error": "..."}` |
| `PostToolBatch` | 一批工具全部执行完成后（`execute_all` 返回前） | ❌ | `{"tool_names": [...], "results": [...], "error_count": N}` |

> **`PostToolUseFailure` vs `PostToolUse`**：`PostToolUse` 在工具成功返回时触发
> （包括返回 `[tool error: ...]` 格式错误字符串的情况，即注册函数执行完但结果是错误）；
> `PostToolUseFailure` 只在工具函数本身**抛出未捕获异常**时触发。两者不重叠。

> **`PostToolBatch`**：每次 LLM 返回一批 tool_calls 并全部执行完后触发一次，
> 不是每个工具单独触发。可用于"批量完成后记录日志"等场景。

### Subagent 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `SubagentStart` | SubAgent 进入 RUNNING 状态时 | ❌ | `{"task_id": "...", "task_name": "...", "prompt": "...（前200字）"}` |
| `SubagentStop` | SubAgent 进入终态（DONE / FAILED / CANCELLED）时 | ❌ | `{"task_id": "...", "status": "done|failed|cancelled", "error": "..."}` |

### Task 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `TaskCreated` | `TaskManager.submit()` 提交任务时 | ❌ | `{"task_id": "...", "task_name": "...", "prompt": "...（前200字）", "tags": [...]}` |
| `TaskCompleted` | 任务进入终态（DONE / FAILED / CANCELLED）时 | ❌ | `{"task_id": "...", "task_name": "...", "status": "...", "error": "..."}` |

> `TaskCompleted` 覆盖三种终态（DONE / FAILED / CANCELLED），
> 通过 payload 中的 `status` 字段区分。

### Stop 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `Stop` | agentic loop 中 LLM 无工具调用、准备结束本轮输出前 | ❌（结果可注入） | `{"text": "本轮输出文本", "turn": N}` |

> **`Stop` 的 context 注入**：若 hook 返回 `{"context": "..."}` ，
> 该文本会以 `[stop hook context] ...` 形式作为 user 消息追加进 history。
> 适合"收尾检查"类场景（如让 agent 再核实一遍某个条件）。
> `blocked` 字段对 Stop 事件无效，不能阻止本轮结束。

### Context Compact 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `PreCompact` | `_auto_compress_history()` 执行前 | ✅ | `{"history_len": N, "strategy": "auto_compress"}` |
| `PostCompact` | `_auto_compress_history()` 执行后 | ❌ | `{"history_len": N, "strategy": "auto_compress", "summary": "..."}` |

> **`PreCompact` 阻止**：hook 返回 exit code 2 或 `{"decision": "block"}` 可阻止本次压缩。
> 典型用途：当前 turn 正在执行重要任务时，临时禁止压缩以保留完整上下文。

### Ensemble 生命周期

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `EnsembleJudged` | 一次 ensemble（Best-of-N）运行完成评判/合并后 | ❌ | `{"final_content": "...", "chosen_idx": N\|null, "judge_strategy": "...", "granularity": "llm_call\|subagent", "execution": "serial\|parallel", "judge_reason": "...", "early_stopped": bool, "candidates": [...]}` |

> 同一份 payload 也会落盘到 `<session_dir>/ensemble/<时间戳>_<粒度>.json`。详见 [多结果合并取优指南](ensemble-best-of-n-guide.md)。

### mini_agent 扩展

| 事件 | 触发时机 | 可阻止 | payload |
|---|---|---|---|
| `TurnEnd` | 每轮 Agent 回复完成、等待下一次用户输入之前 | ❌ | `{"assistant_output": "...", "history": [...]}` |

---

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

- **退出码为 2**：视为 `block`（无论 stdout 是什么），常用于"一句话拒绝"场景
- **`decision: "block"`**（仅 `PreToolUse` 和 `PreCompact`）：阻断操作，
  工具调用结果替换为 `[blocked by hook: <reason>]`，或压缩跳过
- **`context`**：
  - `UserPromptSubmit` 时：以 `[hook context]\n...` 形式追加到用户消息后
  - `PostToolUse` 时：以 `[hook note] ...` 形式追加到工具结果后
  - `Stop` 时：以 `[stop hook context] ...` 形式作为 user 消息追加进 history
- **`input`**（仅 `PreToolUse`）：修改本次工具调用的参数（多个 hook 依次合并）
- **`user_input`**（仅 `TurnEnd`）：替代真实用户输入，直接驱动下一轮对话
- stdout 非 JSON 或为空：不阻断，非空内容当作 `context` 处理
- hook 执行报错/超时：不阻塞主流程，仅记录在内部 `error` 字段

---

## 事件触发时序（一轮完整对话流）

```
UserPromptSubmit
  │
  └─→ agentic loop
        │
        ├─→ LLM 调用
        │
        ├─→ [有工具调用]
        │     ├─→ PreToolUse (×N，每个工具)
        │     ├─→ 工具执行
        │     │     ├─→ PostToolUse (成功)
        │     │     └─→ PostToolUseFailure (抛异常)
        │     └─→ PostToolBatch (本批全部结束)
        │
        └─→ [无工具调用]
              └─→ Stop
                    │
                    └─→ TurnEnd
                          │
                          └─→ SessionEnd（REPL 退出时）
```

SubAgent 并发路径：

```
TaskCreated → SubagentStart → [SubAgent 内部 loop] → SubagentStop → TaskCompleted
```

---

## TurnEnd 事件详解

### 触发时机

每轮对话完成后，Agent 输出已渲染到终端、session 已保存，**正要等待用户下一次
输入之前**触发。

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

### stdout 返回协议

| 返回内容 | 效果 |
|---|---|
| `{}` 或不返回 | 不做任何事，继续等待真实用户输入 |
| `{"context": "提示文本"}` | 向 hook runner 记录，继续等待用户输入 |
| `{"user_input": "..."}` | **替代真实用户输入**，直接驱动下一轮 |

### `user_input` 替代机制

当 `TurnEnd` hook 返回 `{"user_input": "..."}` 时：

1. REPL 跳过 `prompt_user()`（不等待键盘输入）
2. 在终端以灰色 `dim` 样式打印注入的输入行（`You ❯ <注入内容>`）
3. 直接调用 `agent.run_turn(注入内容)` 驱动下一轮
4. 下一轮结束后再次触发 `TurnEnd`，直到 hook 返回 `{}` 为止

多个 `TurnEnd` hook 并存时，取**最后一个**返回 `user_input` 的值。

---

## 典型用例

### 阻止删除特定文件（PreToolUse）

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

### 批量工具完成后发通知（PostToolBatch）

```python
#!/usr/bin/env python3
import json, sys

payload = json.load(sys.stdin)
names = payload.get("tool_names", [])
errs  = payload.get("error_count", 0)
print(f"[batch done] {len(names)} tools, {errs} errors: {names}", file=sys.stderr)
print("{}")
```

### 监控 SubAgent 完成（SubagentStop）

```python
#!/usr/bin/env python3
import json, sys

payload = json.load(sys.stdin)
status = payload.get("status", "")
task_name = payload.get("task_name", "")
error = payload.get("error", "")

if status == "failed":
    print(f"⚠️  SubAgent failed: {task_name}\n{error[:200]}", file=sys.stderr)
elif status == "done":
    print(f"✅ SubAgent done: {task_name}", file=sys.stderr)
print("{}")
```

### 阻止无关时机的历史压缩（PreCompact）

```python
#!/usr/bin/env python3
import json, sys, os

payload = json.load(sys.stdin)
# 例如：环境变量标记当前正在跑重要任务，禁止压缩
if os.environ.get("MINI_AGENT_NO_COMPACT"):
    print(json.dumps({"decision": "block", "reason": "no-compact flag is set"}))
    sys.exit(0)

print("{}")
```

### Agent-to-Agent 接管（TurnEnd）

`.agent/hooks/turn_end_auto_reply.py`：

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

print("{}")
```

---

## 跨平台注意事项

hook runner（`hooks/runner.py`）已针对 Windows 做以下兼容处理：

- **编码**：stdin/stdout/stderr 全部使用二进制模式 + 显式 UTF-8 编解码
- **命令解析**：Windows 下使用 `shlex.split(cmd, posix=False)`，避免反斜杠路径被截断

hook 脚本建议：

```python
import sys, json
payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
```

---

## CLI 调试命令

- `/hooks` 或 `/hooks list`：列出当前加载的所有 hook（按事件分组，含来源）
- `/hooks reload`：重新加载 `.agent/hooks.json` 和 `~/.agent/hooks.json`

---

## 与 Skill / 自定义子 Agent 的联动（动态注册）

`HookManager` 提供 `register_dynamic_from_dict(hooks_dict, source)` /
`unregister_source(source)`，允许 skill 或自定义子 agent profile
（frontmatter 中的 `hooks` 字段）在被激活时临时挂载专属 hook，
停用/任务结束时再移除。

---

## 相关文档

- [记忆管理指南](memory-management-guide.md) — `SessionEnd` 触发后的反思 LLM 调用如何生成 lesson
- [history 类型化设计](history-typed-design.md) — `is_turn_boundary()` 如何为反思调用截取用户意图轮次
- [SubAgent 机制](subagent-mechanism.md) — SubagentStart / SubagentStop 的运行时上下文
- [Plan & Task 指南](plan-and-task-guide.md) — TaskCreated / TaskCompleted 的任务生命周期
- [多结果合并取优指南](ensemble-best-of-n-guide.md) — `EnsembleJudged` 事件的完整 payload 与落盘格式

---

*最后更新：2026-06（新增事件：`PostToolUseFailure`、`PostToolBatch`、`SubagentStart`、`SubagentStop`、`TaskCreated`、`TaskCompleted`、`Stop`、`PreCompact`、`PostCompact`；`SessionStart` 从预留升级为已接入；补充完整事件时序图和各事件用例）*
