---
name: reminder-generator
description: 从当前对话中提取可复用经验并生成 reminder 文件。当用户说"生成reminder"、"总结reminder"、"保存这个经验"、"把这个记录成reminder"时使用。
triggers: 生成reminder, 总结reminder, 保存经验, reminder, 记录经验, 提取reminder, save reminder
---

# Reminder Generator Skill

将当前对话中的解决经验、错误处理技巧等，生成结构化的 reminder 文件，
供 `ReminderManager` 在未来类似情境下自动注入提示。

## Reminder 文件格式

每个 reminder 是一个 `.md` 文件，放在 reminder 目录下：

```markdown
---
name: snake_case_unique_name        # 唯一标识，小写下划线
trigger_event: tool_error           # 触发事件类型（见下方）
condition:
  tool_name: "bash"                 # 可选：工具名（正则）
  error_pattern: "Permission denied" # 可选：错误内容（正则）
inject_as: user                     # user | assistant
priority: 80                        # 0-100，越高越优先
enabled: true
---

**[Reminder] 标题：**

1. 具体可执行的建议
2. 第二条建议
```

## trigger_event 类型

| 类型 | 触发时机 | 常用 condition 字段 |
|------|----------|---------------------|
| `tool_error` | 工具出错时 | `tool_name`, `error_pattern` |
| `post_tool` | 工具成功后（基于输出内容）| `tool_name`, `output_pattern` |
| `user_intent` | 用户消息进入时 | `keyword`, `intent_pattern` |
| `pattern` | assistant 输出后 | `text_pattern` |

## 生成 Reminder 的步骤

### 步骤 1：分析对话，识别可提取内容

从对话历史中寻找：
- 工具调用出错 → 如何解决的过程（适合 `tool_error` reminder）
- 特定工具输出规律 → 该注意什么（适合 `post_tool` reminder）
- 用户的特定操作意图 → 该提前提醒什么（适合 `user_intent` reminder）
- 反复遇到的同类问题

### 步骤 2：构造 reminder 草稿

- `name`：snake_case，描述性强，如 `pip_break_system_packages`
- `trigger_event`：选最匹配的类型
- `condition`：尽量精准，避免过于宽泛导致误触发
- `priority`：严重错误类 80-90，一般提示类 50-70，低优先 30-50
- `inject_as`：通常用 `user`；引导 assistant 思考时可用 `assistant`
- 正文：简洁、可操作，3-8 条为佳，用 Markdown 列表

### 步骤 3：询问用户确认目录

生成草稿后，**必须询问用户**将文件写入哪个目录：

```
选项 A：系统默认目录
  src/mini_agent/prompts/reminders/<name>.md
  （所有用户共享，随项目分发）

选项 B：自定义目录
  用户通过 --reminders-dir 指定的目录
  （个人专属，优先级更高）

选项 C：取消，仅查看草稿
```

### 步骤 4：写入文件

根据用户选择，使用 `write_file` / `create_file` 工具写入对应路径。
写入后提示用户重启 agent 或执行 `reminder_mgr.reload()` 使其生效。

## 自动提取模式

任务完成后，如果以下条件满足，可以**主动提案**（不自动写入）：
1. 对话中出现过工具调用出错并成功解决
2. 用户明确表示"这个方法很有用"、"记住这个"等

提案格式：
```
我注意到本次对话中解决了 [问题描述]，
可以将其保存为 reminder，在未来遇到类似问题时自动提示。

是否生成 reminder？（yes/no）
```

## condition 字段正则建议

- 匹配宜用「或」语法：`"ModuleNotFoundError|ImportError|No module named"`
- 避免过于简短的关键词（误触发率高），至少 6 个字符
- tool_name 若要匹配所有工具，**留空即可**（空字段 = 匹配所有）

## 已有系统默认 reminder（勿重复创建）

| name | 触发场景 |
|------|---------|
| `bash_permission_error` | bash Permission denied |
| `python_import_error` | ModuleNotFoundError / ImportError |
| `file_not_found` | No such file or directory |
| `command_not_found` | command not found |
| `network_error` | 网络超时/连接拒绝/SSL错误 |
| `syntax_error` | SyntaxError / IndentationError |
| `disk_space` | No space left on device |
| `write_large_file` | 用户意图：写入/创建文件 |
