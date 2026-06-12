---
name: agent-generator
description: 帮助用户创建符合 mini_agent 项目规范的自定义子 agent (.agent/agents/*.md)，可通过 spawn_named_agent 调用。当用户说"帮我写一个子agent"、"创建一个自定义agent"、"做一个xxx专家agent"时使用。
triggers: subagent, sub-agent, custom agent, 子agent, 自定义agent, spawn_named_agent, agent profile
---

# Custom Sub-Agent Generator

用于创建符合本项目 `AgentProfileLoader`
（`src/mini_agent/orchestrator/agent_profiles.py`）解析规范的自定义子 agent
profile，主 agent 可通过 `spawn_named_agent` 调用它们。

## 文件位置

- 项目级：`<project_root>/.agent/agents/<name>.md`（优先级更高，同名覆盖全局）
- 全局级：`~/.agent/agents/<name>.md`

文件名建议与 `name` 字段一致（不强制，缺省取文件名去掉 `.md`）。

## 文件格式

```markdown
---
name: <agent-name>
description: <一句话描述：这个子agent做什么、什么场景下主agent应该调用它>
model: <可选，如 claude-haiku-4-5，缺省继承主agent配置>
provider: <可选>
tools: <可选，逗号分隔或YAML列表，限制可用工具白名单>
tool_groups: <可选，YAML列表，限制可用工具分组，与tools取并集>
inputs:
  - name: <参数名>
    type: <string|array|object|number|boolean，仅用于文档展示>
    description: <参数说明>
    required: <true|false>
    default: <可选默认值，required=false时建议提供>
---
<system prompt 模板正文，用 {参数名} 引用 inputs 里的字段，用 {context} 接收调用方传入的自由文本上下文>
```

## Frontmatter 字段详解

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 推荐 | profile 唯一标识，主 agent 通过此名调用 `spawn_named_agent(agent_type=name, ...)` |
| `description` | **必填** | 会注入主 agent 的 system prompt（`available_subagents.md`），是主 agent 判断"何时该调用这个子agent"的唯一依据，务必写清楚触发场景，可用中文 |
| `model` | 可选 | 子 agent 使用的模型。轻量/重复性任务建议用更便宜的模型（如 `claude-haiku-4-5`） |
| `provider` | 可选 | 同上，配合 model 一起指定 |
| `tools` | 可选 | 白名单工具名列表。**安全原则**：按子agent实际需要的最小工具集授权，例如纯分析类只给 `read_file, grep`，不要给 `bash`/`write_file` |
| `tool_groups` | 可选 | 按工具分组授权（与 `tools` 取并集），分组定义见 `ToolRegistry` |
| `inputs` | 可选但推荐 | 结构化参数 schema。`required: true` 的参数缺失时 `spawn_named_agent` 会直接报错拒绝执行，避免子agent在缺关键信息时瞎跑 |

## 正文（system prompt 模板）写作规范

1. **角色设定**：开头一两句明确这个子agent的身份/专长
2. **参数占位符**：每个 `inputs` 里声明的参数，若需要在正文中体现，用 `{参数名}` 引用；
   - 调用方未传且无 `default` → 替换为空字符串，所以重要参数尽量 `required: true` 或给合理 `default`
   - 调用方也可以传入未在 `inputs` 中声明的额外占位符，模板里同名 `{xxx}` 也会被替换（用于灵活扩展，但建议优先在 inputs 里声明以便主agent知晓schema）
3. **`{context}` 占位符**：用于接收调用方传入的自由文本（文件片段、前序发现等）。
   - 若模板里写了 `{context}`，会被替换为传入内容
   - 若没写，且调用方传了 context，会自动在末尾追加 `# Additional Context` 段落
4. **输出格式约束**：明确要求子agent按什么结构输出结果（便于主agent解析），尤其是会被
   `get_task_result`/`get_task_status` 取回结果的场景
5. **避免冗余指令**：不要重复主 agent 的通用规则（如工具使用方式），子agent会继承基础能力，
   只需写"这个角色特有"的指令

## 创建流程（生成此 agent profile 时遵循）

1. 向用户确认：
   - 子agent的职责（一句话 description，要写清楚"何时调用"）
   - 需要哪些结构化输入参数（哪些必填/可选/默认值）
   - 需要哪些工具权限（按最小权限原则筛选 `tools`/`tool_groups`）
   - 是否需要指定 model（轻量任务建议用便宜模型）
2. 写入 `.agent/agents/<name>.md`，frontmatter + 模板正文齐全
3. 提醒用户可用以下方式验证：
   - `/agents list` 查看是否被发现
   - `/agents show <name>` 查看完整渲染前的模板和工具限制
   - 主 agent 调用 `list_agent_profiles` 应能看到该 profile
4. 给出一个 `spawn_named_agent` 调用示例，方便用户/主agent直接测试

## 示例：测试用例生成子agent

```markdown
---
name: test-writer
description: 为指定的 Python 模块生成 pytest 单元测试。当用户要求"写测试"、"补充单测"、"给xxx加测试"时使用。
model: claude-haiku-4-5
tools: read_file, write_file, bash
inputs:
  - name: module_path
    type: string
    description: 需要生成测试的模块文件路径
    required: true
  - name: test_path
    type: string
    description: 测试文件输出路径
    required: false
    default: "tests/test_generated.py"
  - name: style
    type: string
    description: 测试风格偏好，如 'unit'（默认）或 'integration'
    required: false
    default: "unit"
---
你是一名 pytest 测试工程师，专注于 {style} 测试。

请阅读 {module_path}，为其中的公开函数/类编写 pytest 测试，写入 {test_path}。

要求：
- 使用 `pytest.mark.parametrize` 覆盖边界条件
- 对外部依赖用 `pytest-mock` 的 `mocker` fixture mock 掉
- 完成后运行 `pytest {test_path} -q` 确认通过，并在结果中报告通过/失败的用例数

{context}
```

调用示例：

```python
spawn_named_agent(
    agent_type="test-writer",
    inputs={"module_path": "src/mini_agent/permissions.py", "style": "unit"},
    context="刚才在 permissions.py 里给 requires_approval=False 的工具加了直通逻辑，重点测这部分。",
)
```

参考已有示例：`.agent/agents/code-reviewer.md`，
以及文档 `docs/custom-sub-agents.md`。
