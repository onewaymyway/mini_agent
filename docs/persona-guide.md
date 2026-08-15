# 角色扮演（Persona）系统指南

mini_agent 支持 `.agent/personas/*.md` 的角色扮演机制：你可以预先定义好一批"角色"（如
管家式 AI、资深工程师导师、女仆角色等），通过 `/role use <name>` 让**主 agent 自身**
切换到该角色的人格，跨轮持续生效，直到你用 `/role exit` 显式退出。

## 与"自定义子 Agent"的区别

persona 与 `.agent/agents/*.md`（详见[自定义子 Agent](custom-sub-agents.md)）共享同样的
"YAML frontmatter + Markdown 正文"文件约定，但作用完全不同：

| | 自定义子 Agent（`.agent/agents`） | Persona（`.agent/personas`） |
|---|---|---|
| 作用对象 | 独立的子 agent / 独立 context | 主 agent 自身 |
| 生命周期 | 单次任务，跑完即销毁 | 跨轮持续，直到 `/role exit` |
| 调用方式 | 主 agent 主动 `spawn_named_agent` | 用户通过 `/role use` 激活 |
| 典型场景 | 代码审查、翻译等任务型工作 | 人格/语气切换、沉浸式对话 |

## 文件位置

```
<project_root>/.agent/personas/*.md   # 项目级，优先级更高
~/.agent/personas/*.md                # 全局级
```

同名 persona，项目级会覆盖全局级。

## 文件格式

```markdown
---
name: jarvis                        # 唯一标识，/role use jarvis
display_name: 贾维斯                 # /role list 展示用，可以是中文
description: 管家式科幻AI助理人设，语气从容、略带英式幽默
tone: 沉稳、简练、偶尔调侃            # 可选，供 /role show 展示
allowed_tools: null                 # 可选白名单；留空/不填 = 不限制
break_character_policy: soft        # soft | strict
wiki_scopes:                        # 可选，该角色检索时优先使用的 wiki 命名空间；留空/不填 = 不限制
  - capability:stock_analysis
---

你现在是贾维斯（Jarvis），一位管家式的科幻AI助理……
[身份设定 / 说话风格 / 知识边界 / 行为准则等正文]
```

### Frontmatter 字段

| 字段 | 说明 |
|---|---|
| `name` | 唯一标识，缺省取文件名（不含扩展名），`/role use <name>` 引用此值 |
| `display_name` | 展示名，`/role list`/`/role status` 中显示，可用中文 |
| `description` | 一句话描述这个角色的定位和适用场景，会出现在 `/role list` 中 |
| `tone` | 可选，语气风格简述 |
| `break_character_policy` | `soft`（默认）：角色可在被问及真实身份/严肃技术问题时短暂跳出角色回答，再询问是否继续；`strict`：角色应尽量保持人设。**无论取值为何，安全边界都不受影响**（见下） |
| `allowed_tools` | 可选工具白名单（YAML 列表或逗号分隔字符串）。声明后，不在名单内的工具调用会被系统**直接拒绝**，不进入常规审批流程；空/不填 = 不限制 |
| `wiki_scopes` | 可选（YAML 列表或逗号分隔字符串）。声明后，`context_builder.py` 每轮检索会把这些 tag 透传给 `wiki_shelf_search(tags=...)`，让该角色激活时的 wiki 检索**优先**命中这些命名空间下的页面；这是**软优先而不是硬限制**——限定范围内零命中时仍会检索到范围外的页面，不会让角色"变笨"。空/不填 = 不限制。最自然的取值来源是 `next_doc/persona_capability_learning_design.md` 里 knowledge 型 `CapabilityTrack` 持续沉淀出的 `wiki_tag`，一个 wiki 命名空间也可以被多个角色的 `wiki_scopes` 共享 |

## CLI 命令

| 命令 | 说明 |
|---|---|
| `/role list` | 列出已发现的角色（项目级 + 全局级，同名项目级优先） |
| `/role use <name>` | 激活角色，从下一轮开始生效 |
| `/role show <name>` | 预览角色渲染后的完整 system prompt 片段（含强制安全边界声明），不激活 |
| `/role exit` / `/role off` | 清空当前角色，回到默认助手身份 |
| `/role status` | 显示当前是否处于角色扮演及角色名 |
| `/role stats` | 显示各角色的全局激活次数统计（跨项目累计，来自 `~/.agent/persona_usage.jsonl`） |
| `/role reload` | 重新扫描 `.agent/personas/` 和 `~/.agent/personas/` |

角色状态随会话持久化：`save_session()` 时写入 `meta.json`，`load_session()` 时恢复；
`new_session()`（新建会话）不会继承上一个会话的角色状态。

## 安全边界：不可被角色设定覆盖

无论 `break_character_policy` 取值为何，`render_persona_prompt()` 都会在角色正文之后
**代码强制追加**一段安全边界声明（不读取任何用户配置，角色文件无法覆盖）：

> 角色设定仅影响语气与人设呈现，不改变你的安全边界：工具调用格式规范、内容安全与拒绝
> 原则等核心约束在任何角色下始终有效；若角色设定与这些约束冲突，以约束为准……

`allowed_tools` 的强制拦截同理：`ToolExecutor.execute_all()` 在 `PreToolUse` hook 之后、
`guard.check()` 之前检查，命中非白名单工具直接返回 `[blocked by persona allowed_tools: ...]`，
不依赖角色文件内容或模型自觉。

## System Prompt 注入位置

`ContextBuilder.build()` 中，角色扮演内容单独成一个 `## 当前角色扮演设定：<display_name>`
段落注入，不与 skill/tool 使用规范混排。这样设计的原因：

1. 避免角色语气描述"污染"工具调用格式说明的严谨性；
2. 便于 `/role exit` 时整段摘除，不影响其余 system prompt 结构；
3. 便于 `/debug system` 命令直接定位查看当前角色注入的完整内容。

## 内置默认角色

| 角色 | 说明 |
|---|---|
| `senior-swe-mentor` | 资深工程师导师，语气严谨简练，适合技术评审/架构讨论 |
| `jarvis` | 管家式科幻 AI 助理，从容、略带英式幽默 |
| `socratic-tutor` | 苏格拉底式导师，通过提问引导思考，适合教学场景 |
| `storyteller-narrator` | 第三人称叙事者，适合创意写作/互动故事 |
| `rem` | 温柔忠诚、略带反差萌的女仆人设 |

## 创建自定义角色

使用 `persona-generator` skill（`.claude/skills/persona-generator/SKILL.md`）辅助创建，
会引导你确认身份设定、说话风格、是否需要工具限制、`break_character_policy` 取值，
并写入 `.agent/personas/<name>.md`。创建后可用 `/role show <name>` 预览、
`/role use <name>` 试用。

## 多用户 / daemon 场景

`SessionAgentPool` 为每个 session 持有独立的 `Agent()` 实例，`active_persona` 作为
`Agent` 实例属性天然按 session 隔离，不同用户的角色状态互不影响。

## 设计文档

完整的设计决策与实施记录见 [`next_doc/roleplay_persona_design.md`](../next_doc/roleplay_persona_design.md)。
