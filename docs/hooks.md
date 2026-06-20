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
  ]
}
```

字段说明：
- `command`：要执行的 shell 命令（用 `shlex.split` 解析）
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

> **2026-06 更新（Stage 1.3）**：`SessionEnd` 已从"预留未接"升级为真正接入。
> 触发点是 `agent.trigger_session_end()`，由 `cli/repl.py` 在进程退出前的两处
> 真实退出路径调用。`SessionStart` 仍是预留状态。
>
> `SessionEnd` 触发后，agent 还会紧接着跑一次轻量 LLM 反思调用，基于
> `tool_stats` + 最后若干轮用户意图轮次生成结构化 lesson 候选并写入记忆——
> 这是 hook 触发之外的**额外行为**，不依赖 hook 配置是否存在，由
> `cfg.memory.enabled` 控制是否执行。详见
> [记忆管理指南](memory-management-guide.md#lesson-memory) 中 SessionEnd 反思一节。

## Hook 如何与主流程交互

hook 命令通过 **stdin** 接收 JSON payload，可选地向 **stdout** 输出 JSON 来表达决策：

```json
{"decision": "block", "reason": "禁止删除 .env 文件"}
{"decision": "allow"}
{"context": "额外提示信息，会拼接到结果/prompt 后面"}
{"input": {"path": "safe/path.txt"}}
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
- stdout 非 JSON 或为空：不阻断，非空内容当作 `context` 处理
- hook 执行报错/超时：不阻塞主流程，仅记录在内部 `error` 字段

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

*最后更新：2026-06（`SessionEnd` 从预留升级为真正接入，对应 self_evolution_implementation_plan.md Stage 1.3）*
