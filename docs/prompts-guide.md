# Prompt 管理系统指南

> 本文档介绍 `prompts/` 模块的架构、组件和使用方式。

---

## 1. 模块概述

`prompts/` 是 mini_agent 的 **Prompt 统一管理模块**，负责：

- **Prompt 文件加载**：从 `prompts/system/`、`prompts/user/`、`prompts/fragments/` 加载 Markdown 文件
- **变量渲染**：支持 `{{ variable }}` 占位符替换
- **Fragment 管理**：细粒度的 UI 文本片段（key: value 格式）
- **System Prompt 组装**：按固定顺序组合所有片段，生成完整的 system prompt
- **自定义 Prompt 目录**：支持用户自定义目录覆盖默认 prompt

---

## 2. 目录结构

```
prompts/
├── __init__.py              # 模块入口，导出 PromptManager
├── manager.py               # PromptManager 核心类
├── system/                  # system prompt 片段
│   ├── agent_core.md        # Agent 核心身份与行为规则
│   ├── sandbox_mode.md      # 沙箱模式警告
│   ├── project_context.md   # 项目上下文（CLAUDE.md 注入）
│   ├── active_skills.md     # 已激活 Skill 列表
│   ├── cognitive_anchor.md  # 认知锚点
│   ├── compress_summarizer.md # 压缩摘要器
│   ├── current_time.md      # 当前时间
│   ├── orchestration.md     # 编排能力说明
│   ├── plan_mode.md         # Plan 模式能力
│   ├── profile_summarizer.md # 画像摘要器
│   ├── session_reflection.md # 会话反思
│   ├── summarizer.md        # 摘要器
│   ├── timeline_reflection.md # 时间线反思
│   ├── tool_call_protocol.md # 工具调用协议
│   ├── tool_result_summarizer.md # 工具结果摘要器
│   ├── user_profile.md      # 用户画像
│   └── workspace_hygiene.md # 工作区卫生规范
├── user/                    # user 角色预设消息
│   ├── cognitive_anchor_request.md
│   ├── compact_chunk_request.md
│   ├── compact_history.md
│   ├── compact_merge_request.md
│   ├── compress_summary_request.md
│   ├── profile_update_request.md
│   ├── session_reflection_request.md
│   ├── session_summary_request.md
│   ├── timeline_reflection_request.md
│   └── tool_result_summary_request.md
└── fragments/               # 细粒度 UI 文本片段
    ├── cli_messages.md      # CLI 消息文本
    ├── permission_labels.md # 权限标签
    ├── goal_mode.md          # Goal 模式相关文案片段
    └── judge_json_output.md # 判官类 Agent（GoalJudge/TurnJudge）统一 JSON 输出指令
```

---

## 3. PromptManager 核心 API

### 3.1 基本用法

```python
from mini_agent.prompts import PromptManager

pm = PromptManager()  # 默认使用包内 prompts/ 目录
pm = PromptManager("/path/to/custom/prompts")  # 自定义目录

# 渲染 system prompt 文件
text = pm.render("system/agent_core")
text = pm.render("system/project_context", claude_md_content="...")

# 获取 fragment 键值
msg = pm.fragment("cli_messages", "BANNER")
msg = pm.fragment("cli_messages", "REPL_STARTUP_MODEL", model="claude-opus-4-5")

# 构建完整 system prompt
full = pm.build_system_prompt(
    claude_md_content="...",
    active_skills=["skill1", "skill2"],
    skill_context="...",
    sandbox=False,
    current_time="2026-07-04 18:33:57",
)
```

### 3.2 自定义 Prompt 目录

```python
# 设置用户自定义目录（优先于默认目录）
pm.set_custom_dir("/path/to/user/prompts")

# 清除自定义目录，恢复仅使用默认目录
pm.set_custom_dir(None)

# 查看当前自定义目录
print(pm.custom_dir)  # Path("/path/to/user/prompts") 或 None
```

### 3.3 缓存与重载

```python
# 清空所有缓存（用于开发/热重载）
pm.reload()

# 列出所有可用的 prompt 文件
prompts = pm.list_prompts()
# ['system/agent_core', 'system/sandbox_mode', ..., 'user/compact_history']

# 列出 fragment 文件中的所有键
keys = pm.list_fragments("cli_messages")
# ['BANNER', 'REPL_STARTUP_MODEL', ...]
```

---

## 4. System Prompt 构建顺序

`build_system_prompt()` 按以下顺序组装 system prompt：

1. **Agent 核心身份** — `system/agent_core.md`
2. **工作区卫生规范** — `system/workspace_hygiene.md`（可选）
3. **执行计划能力** — `system/plan_mode.md`（可选）
4. **当前时间** — `system/current_time.md`
5. **环境信息** — 直接注入
6. **项目上下文** — `system/project_context.md`（CLAUDE.md 内容）
7. **用户画像** — `system/user_profile.md`（可选）
8. **已激活 Skill** — `system/active_skills.md`
9. **额外系统文本** — 来自 `--system` CLI 参数
10. **编排能力** — `system/orchestration.md`（如果有 TaskManager）
11. **自定义子 Agent** — `system/available_subagents.md`（如果有 AgentProfile）
12. **执行计划上下文** — 当前活跃计划的状态
13. **沙箱模式警告** — `system/sandbox_mode.md`（如果启用）

---

## 5. Fragment 文件格式

Fragment 文件支持两种格式：

### 5.1 单行格式

```
BANNER: mini-agent v0.7.1
REPL_STARTUP_MODEL: 使用模型 {model}
```

### 5.2 多行格式

```
LONG_MESSAGE: |
  这是一段
  多行文本
  会自动去除公共缩进
```

---

## 6. 变量渲染

### 6.1 Prompt 文件中的变量

使用 `{{ variable }}` 语法：

```markdown
# system/agent_core.md
你是 {agent_name}，一个 AI 编程助手。
```

渲染时传入变量：

```python
text = pm.render("system/agent_core", agent_name="orzooo")
```

### 6.2 Fragment 中的变量

使用 `{variable}` 语法（单括号）：

```markdown
# fragments/cli_messages.md
BANNER: mini-agent {version}
```

获取时传入变量：

```python
msg = pm.fragment("cli_messages", "BANNER", version="0.7.1")
```

---

## 7. 相关文档

- [系统设计概述](system-overview.md) — Prompt 管理在整体架构中的位置
- [代码结构指南](code-structure-guide.md) — `prompts/` 包的职责边界
- [热重载机制说明](hot-reload-guide.md) — Prompt 文件热重载

---

*最后更新：2026-07*