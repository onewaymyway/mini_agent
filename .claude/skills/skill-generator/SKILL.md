---
name: skill-generator
description: 帮助用户创建符合 mini_agent 项目规范的新 SKILL.md 技能文件。当用户说"帮我写一个skill"、"创建一个技能"、"生成SKILL.md"时使用。
triggers: skill, skill.md, 技能, 创建skill, 生成skill
---

# Skill Generator

用于创建符合本项目 `SkillLoader`（`src/mini_agent/skills/__init__.py`）解析规范的
`SKILL.md` 文件。

## 文件位置

- 项目级：`.claude/skills/<skill-name>/SKILL.md`（本项目当前使用的目录，
  `_resolve_skills_dir` 优先匹配 `<project_root>/.claude/skills`）
- 全局级：`~/.agent/skills/<skill-name>/SKILL.md`

每个 skill 一个独立子目录，目录名建议与 `name` 字段一致。

## 文件格式

```markdown
---
name: <skill-name>
description: <一句话描述，说明这个skill做什么、什么时候用它>
triggers: <逗号分隔的触发词列表，全部小写>
---

# <Skill 标题>

<正文：具体的知识/规范/checklist/示例代码，会在被激活时整段注入 system prompt>
```

### Frontmatter 字段说明

| 字段 | 是否必填 | 说明 |
|---|---|---|
| `name` | 推荐填写 | 缺省取目录名（若文件名为 `SKILL.md`）或文件名（去扩展名）。建议显式填写，kebab-case |
| `description` | 推荐填写 | 缺省取正文第一行非空非标题文本（截断120字符）。这是**最重要**的字段——决定该 skill 是否被自动激活，要清楚说明"什么场景下用" |
| `triggers` | 可选 | 逗号分隔的关键词，出现在用户输入中会触发 `auto_activate`。缺省会从 `name`+`description` 中匹配内置词表（`_TRIGGER_VERBS`，主要是文档/数据相关词），**强烈建议显式填写**，否则可能匹配不到 |

> 注意：旧字段名 `trigger_words` 也被兼容解析，但新建 skill 统一用 `triggers`。

## 正文写作规范

正文会被**整段**注入 system prompt（受 `skill_compact_per_skill` / `skill_compact_budget`
token 预算限制，默认每 skill 约 5000 token，总预算 25000 token），所以：

1. 直接给"可执行的规范/知识"，不要写"我将帮你创建一个skill"之类的元描述
2. 用 Markdown 二级标题（`##`）分节，每节聚焦一个子话题（风格规范/常见陷阱/示例代码等）
3. 代码示例要精炼、可直接复用，避免大段无关样板
4. 如果正文超长，优先精简而不是依赖截断——`SkillLoader` 会在预算超限时截断正文

## 创建流程（生成此 skill 时遵循）

1. 向用户确认：
   - skill 的核心用途（一句话 description）
   - 触发场景关键词（中英文都列，因为用户可能中英混用）
   - skill 名称（kebab-case，作为目录名）
2. 创建目录 `.claude/skills/<name>/`
3. 写入 `SKILL.md`，frontmatter 三个字段齐全
4. 正文按"## 小节"组织实际知识/规范内容
5. 创建完成后提示用户：可以用 `/skills` 或 `/skills list` 查看是否被正确发现
   （若 CLI 提供该命令；具体以 `cli/commands/skills.py` 中已实现的子命令为准）

## 示例

```markdown
---
name: fastapi-conventions
description: FastAPI 项目的代码规范与最佳实践，包括路由组织、依赖注入、错误处理。当用户编写或审查 FastAPI 代码、路由、Pydantic 模型时使用。
triggers: fastapi, pydantic, 路由, dependency injection, api endpoint
---

# FastAPI Conventions

## 路由组织
- 按资源拆分 `APIRouter`，统一在 `app/api/v1/__init__.py` 汇总注册
- 路径统一使用复数名词：`/users`, `/orders`

## 依赖注入
- 数据库 session 通过 `Depends(get_db)` 注入，不要在路由函数里直接 `SessionLocal()`
- 鉴权统一用 `Depends(get_current_user)`，不要在每个路由里重复写 token 解析

## 错误处理
- 业务异常抛 `HTTPException`，统一在 `app/core/exceptions.py` 定义错误码常量
- 不要裸抛 `Exception`，FastAPI 会返回 500 且暴露堆栈信息
```

参考已有示例：`.claude/skills/python-expert/SKILL.md`、
`.claude/skills/iching_oracle/SKILL.md`。
