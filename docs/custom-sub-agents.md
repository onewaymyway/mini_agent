# 自定义子 Agent（Custom Sub-Agents）

mini_agent 支持 `.agent/agents/*.md` 的预设子 agent 机制：
你可以预先定义好一批"专家角色"（如 code-reviewer、test-runner、translator），
主 agent 在合适的时候通过 `spawn_named_agent` 调用它们，并传入结构化参数和
自由文本上下文。

## 文件位置

```
<project_root>/.agent/agents/*.md   # 项目级，优先级更高
~/.agent/agents/*.md                # 全局级
```

同名 profile，项目级会覆盖全局级。

## 文件格式

每个 `.md` 文件由 YAML frontmatter + system prompt 模板正文组成：

```markdown
---
name: code-reviewer
description: 审查指定文件的代码改动... (用于主 agent 判断何时调用)
model: qwen/qwen3.5-122b-a10b      # 可选，子 agent 使用的模型
provider: nvidia          # 可选
tools: read_file, grep, bash # 可选，限制可用工具（逗号分隔或 YAML 列表）
tool_groups: [fs, web]        # 可选，限制可用工具分组
inputs:
  - name: files
    type: array
    description: 需要审查的文件路径列表
    required: true
  - name: focus
    type: string
    description: 审查重点
    required: false
    default: "general correctness"
---
你是一名代码审查者。

本次审查重点：{focus}
待审查文件：{files}

{context}
```

### Frontmatter 字段

| 字段 | 说明 |
|---|---|
| `name` | profile 名称，缺省取文件名（不含扩展名） |
| `description` | 一句话描述，会注入主 agent 的 system prompt，供其判断何时调用 |
| `model` / `provider` | 该子 agent 使用的模型/provider，缺省继承主 agent 配置 |
| `tools` | 允许使用的工具名列表（白名单）。不填表示不限制 |
| `tool_groups` | 允许使用的工具分组列表，与 `tools` 取并集 |
| `inputs` | 参数 schema，每项含 `name/type/description/required/default` |
| `hooks` | （可选）该 profile 自带的 hooks 配置，结构同 `hooks.json`，spawn 时可挂载 |
| `platforms` / `tags` | （可选）限制该 profile 只在特定平台/tag 策略下才会被发现，不满足条件时不会出现在 `/agents list` 或 `spawn_named_agent` 候选中；详见 [平台与 Tag 过滤指南](platform-tag-loading-guide.md) |

### 正文占位符

- `{参数名}`：替换为调用方传入的对应 `inputs` 值（未传则用 `default`，否则空字符串）
- `{context}`：替换为调用方传入的自由文本上下文；若模板里没有 `{context}`，
  会自动在末尾追加 `# Additional Context` 段落

## 调用方式

主 agent 可见两个相关工具：

- `list_agent_profiles`：列出所有可用 profile 及其 inputs schema
- `spawn_named_agent(agent_type, inputs, context, name?, depends_on?, tags?)`：
  渲染 prompt 并提交为后台 Task，返回 `task_id`；用 `get_task_status` /
  `get_task_result` 查看进度和结果

```python
spawn_named_agent(
    agent_type="code-reviewer",
    inputs={"files": ["src/a.py", "src/b.py"], "focus": "security"},
    context="刚才修改了认证逻辑，新增了 token 校验...",
)
```

子 agent 运行时：
- 若 profile 指定了 `model`，覆盖默认模型
- 若 profile 指定了 `tools`/`tool_groups`，子 agent 只能使用这些工具
  （通过 `ToolRegistry.filtered()` 构造受限 registry）

## CLI 调试命令

- `/agents` 或 `/agents list`：列出所有 profile
- `/agents show <name>`：查看某个 profile 的详细信息（含完整 system prompt 模板）
- `/agents reload`：重新扫描 `.agent/agents/` 和 `~/.agent/agents/`

## 与 spawn_agent 的区别

| | `spawn_agent` | `spawn_named_agent` |
|---|---|---|
| 适用场景 | 临时、自由形式的子任务 | 复用固定流程的"专家角色" |
| system prompt | 主 agent 临时编写 | 预设模板 + 结构化参数渲染 |
| 工具限制 | 无 | 可通过 profile 限制 |
| 模型 | 默认继承 | 可在 profile 中固定 |

## 示例

仓库内已附带一个示例 profile：`.agent/agents/code-reviewer.md`，
定义了一个使用 `claude-haiku-4-5`、仅限 `read_file/grep/bash` 的代码审查子 agent。
