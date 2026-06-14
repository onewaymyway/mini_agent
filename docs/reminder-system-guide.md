# Reminder 系统

mini_agent 内置动态 Reminder 注入机制：在特定情境下（工具出错、特定工具输出、
用户意图识别、assistant 输出模式），将预设的提示内容追加到对话历史中，
帮助模型更好地处理当前问题。

与 system prompt 不同，reminder **不会**拉长系统提示，而是以 `user` 或 `assistant`
消息的形式在恰当时机插入对话历史，模型在下次推理时即可感知。

---

## 目录结构

```
src/mini_agent/prompts/reminders/     # 系统默认 reminder 目录
    bash_permission_error.md
    python_import_error.md
    file_not_found.md
    command_not_found.md
    network_error.md
    syntax_error.md
    disk_space.md
    write_large_file.md

<自定义目录>/                          # 用户自定义 reminder（--reminders-dir 指定）
    my_reminder.md
    bash_permission_error.md          # 同名文件会覆盖系统默认

src/mini_agent/reminders/             # Reminder 系统代码
    loader.py                         # 扫描目录，解析 .md 文件
    matcher.py                        # 条件匹配引擎
    manager.py                        # 对外统一入口，agent 持有
    generator.py                      # reminder 生成辅助工具

.claude/skills/reminder-generator/   # 从对话提取 reminder 的 skill
    SKILL.md
```

---

## Reminder 文件格式

每个 reminder 是一个 `.md` 文件，头部用 YAML frontmatter 描述触发条件，正文是提示内容。

```markdown
---
name: bash_permission_error          # 唯一标识（小写下划线），同名时自定义优先
trigger_event: tool_error            # 触发事件类型（见下表）
condition:
  tool_name: "bash"                  # 可选：限定工具名（正则）
  error_pattern: "Permission denied" # 可选：匹配错误内容（正则）
inject_as: user                      # user | assistant
priority: 85                         # 0-100，同场景多条时取最高的几条
enabled: true
---

**[Reminder] bash 权限错误处理建议：**

1. 检查目标文件权限：`ls -la <path>`
2. 安装 Python 包时使用 `pip install --break-system-packages`
```

### trigger_event 类型

| 类型 | 触发时机 | 常用 condition 字段 |
|------|----------|---------------------|
| `tool_error` | 工具调用返回错误时 | `tool_name`、`error_pattern` |
| `post_tool` | 工具调用成功后（基于输出内容）| `tool_name`、`output_pattern` |
| `user_intent` | 用户消息进入时 | `keyword`、`intent_pattern` |
| `pattern` | assistant 输出文本后 | `text_pattern` |

### condition 字段说明

所有字段均为**正则表达式**，匹配时忽略大小写。

| 字段 | 适用事件 | 说明 |
|------|----------|------|
| `tool_name` | `tool_error`、`post_tool` | 匹配工具名，留空则匹配所有工具 |
| `error_pattern` | `tool_error` | 匹配错误内容，留空则所有该工具错误都触发 |
| `output_pattern` | `post_tool` | 匹配工具成功输出内容 |
| `keyword` | `user_intent` | 匹配用户消息中的关键词 |
| `intent_pattern` | `user_intent` | 更复杂的用户消息模式 |
| `text_pattern` | `pattern` | 匹配 assistant 输出文本 |

> `keyword` 和 `intent_pattern` 同时设置时，满足其一即匹配。  
> 两者均未设置的 `user_intent` reminder 不会触发（避免每条消息都注入）。

### inject_as 说明

| 值 | 效果 |
|----|------|
| `user`（默认）| 以 `[Reminder: <name>]\n<content>` 追加为用户消息 |
| `assistant` | 以 `[Note]\n<content>` 追加为 assistant 消息，适合引导模型自我提示 |

---

## 优先级与截断

- 同一情境下多条 reminder 均可匹配，按 `priority` 降序排列
- 每个 turn 最多注入 `max_per_turn`（默认 3）条，超出部分丢弃
- `priority` 建议范围：严重错误类 80-95，一般提示类 50-75，低优先 20-49

---

## 双目录加载规则

系统同时加载**系统默认目录**和**用户自定义目录**：

- 不同 `name` 的 reminder 全部保留，形成完整 reminder 池
- **相同 `name` 的 reminder，用户自定义目录优先**（完全覆盖，系统默认被屏蔽）
- 用户自定义目录的 reminder 会被标记 `is_custom=True`

---

## CLI 参数

```bash
# 指定用户自定义 reminder 目录
mini-agent --reminders-dir ~/my_reminders/

# 禁用 reminder 系统
mini-agent --no-reminders

# 调试模式：打印每次 reminder 匹配和注入详情
mini-agent --reminder-verbose
```

---

## 配置文件（agent_config.json）

```json
{
  "reminder": {
    "enabled": true,
    "custom_dir": "/path/to/my_reminders",
    "tool_error_enabled": true,
    "post_tool_enabled": true,
    "user_intent_enabled": true,
    "pattern_enabled": true,
    "max_per_turn": 3,
    "verbose": false
  }
}
```

各开关可精细控制各类触发源，例如只启用工具错误触发：

```json
{
  "reminder": {
    "tool_error_enabled": true,
    "post_tool_enabled": false,
    "user_intent_enabled": false,
    "pattern_enabled": false
  }
}
```

---

## 系统内置 Reminder 列表

| name | trigger_event | 触发场景 | priority |
|------|---------------|----------|----------|
| `disk_space_full` | `tool_error` | No space left on device | 90 |
| `bash_permission_error` | `tool_error` | Permission denied / EPERM | 85 |
| `command_not_found` | `tool_error` | command not found | 82 |
| `python_import_error` | `tool_error` | ModuleNotFoundError / ImportError | 80 |
| `network_error` | `tool_error` | 网络超时 / 连接拒绝 / SSL 错误 | 78 |
| `syntax_error` | `tool_error` | SyntaxError / IndentationError | 76 |
| `file_not_found` | `tool_error` | No such file or directory | 75 |
| `write_large_file` | `user_intent` | 用户意图：写入/创建文件 | 50 |

---

## 生成自定义 Reminder

### 方式一：手动编写

在自定义目录下创建 `.md` 文件，按上述格式填写 frontmatter 和正文即可。
无需重启，下次启动时自动加载；若需热重载，可在对话中触发 `reminder_mgr.reload()`。

### 方式二：使用 reminder-generator skill

对话中说「生成 reminder」、「把这个经验保存为 reminder」等，skill 会：

1. 分析当前对话，提取可复用的解决经验
2. 自动生成 frontmatter + 正文草稿展示给你确认
3. 询问写入路径（系统默认目录 or 自定义目录）
4. 写入文件

### 方式三：代码调用（高级）

```python
from mini_agent.reminders.generator import build_reminder_draft, save_reminder
from pathlib import Path

draft = build_reminder_draft(
    name="git_merge_conflict",
    trigger_event="tool_error",
    condition_dict={
        "tool_name": "bash",
        "error_pattern": "CONFLICT|merge conflict",
    },
    content="**Git merge conflict 处理：**\n1. `git status` 查看冲突文件\n2. 解决后 `git add . && git commit`",
    priority=75,
)

ok, path = save_reminder(draft, name="git_merge_conflict", target_dir=Path("~/my_reminders").expanduser())
print(f"已保存: {path}")
```

---

## 与 Hooks 的区别

| | Hooks | Reminder |
|--|-------|---------|
| 作用层 | Shell 命令层（工具调用前后执行外部脚本）| LLM 层（向对话历史注入提示） |
| 触发方式 | 事件驱动，执行 shell 脚本 | 条件匹配，追加上下文消息 |
| 适用场景 | 审计、拦截危险操作、格式化 | 提示模型解决思路、注意事项 |
| 能否影响模型行为 | 间接（通过 stdout 返回 JSON）| 直接（注入对话历史） |

两者互补，可同时使用。
