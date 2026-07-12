---
name: skill-generator
description: 帮助用户创建符合 mini_agent 项目规范的新 SKILL.md 技能文件，支持"主文件 + 渐进式加载子资源"的分层结构。当用户说"帮我写一个skill"、"创建一个技能"、"生成SKILL.md"时使用。
triggers: skill, skill.md, 技能, 创建skill, 生成skill
resources:
  - id: progressive-loading
    path: references/progressive-loading.md
    description: 渐进式加载机制详解（resources/browse_paths 字段、双通道加载、卸载后再加载的行为、独立token预算）
    triggers: 渐进式加载, resources字段, 子资源, browse_paths, 分层skill, 大型skill
  - id: examples
    path: references/examples.md
    description: 完整示例：单文件小型skill、带resources的分层skill、带browse_paths的大型文档库skill
    triggers: 给个例子, 完整示例, 参考示例
---

# Skill Generator

用于创建符合本项目 `SkillLoader`（`src/mini_agent/skills/__init__.py`）解析规范的
`SKILL.md` 文件。支持两种体量：**单文件**（内容不多，正文全部塞进 `SKILL.md`）和
**分层结构**（内容较多，拆成主文件 + 按需加载的 `references/*.md`）。

## 文件位置

- 项目级：`.claude/skills/<skill-name>/SKILL.md`（`_resolve_skills_dir` 优先匹配
  `<project_root>/.claude/skills`）
- 全局级：`~/.agent/skills/<skill-name>/SKILL.md`

每个 skill 一个独立子目录，目录名建议与 `name` 字段一致。

## 第一步：判断体量，决定单文件还是分层

先问自己（或问用户）：这个 skill 的知识总量，写成 Markdown 大概多少行？

- **预估 < 150 行、内容边界单一** → 用**单文件**结构，直接把知识写进 `SKILL.md` 正文，
  不要过度设计出一堆 `references/`。
- **预估 > 150 行，或明显能拆成几个独立子话题**（如"基础用法" + "高级配置" +
  "错误排查表"）→ 用**分层结构**：主文件只放高频必需内容 + 索引，其余拆到
  `references/*.md`，通过 frontmatter 的 `resources` 字段登记为可加载子资源。
- **内容是"一个库"而不是"一份文档"**（如完整 API 手册、按语言分文件夹的 SDK 文档、
  几十个示例代码），"用得到的往往只是其中一小段，具体哪段取决于当次任务" →
  不要注册为 `resources`，改用 `browse_paths` 纯提示，让 agent 自己用
  `view`/`grep`/`bash find` 检索定位，不要整份加载。

拿不准时，调用/查看本 skill 的 `progressive-loading` 子资源了解详细判断标准和机制原理。

## 单文件格式

```markdown
---
name: <skill-name>
description: <一句话描述，说明这个skill做什么、什么时候用它>
triggers: <逗号分隔的触发词列表，全部小写>
---

# <Skill 标题>

<正文：具体的知识/规范/checklist/示例代码，会在被激活时整段注入 system prompt>
```

## 分层格式（新增 `resources` / `browse_paths`）

```markdown
---
name: <skill-name>
description: <一句话描述>
triggers: <触发词>
resources:
  - id: advanced
    path: references/advanced.md
    description: 复杂参数组合与边界情况处理
    triggers: 高级用法, edge case, 自定义配置
  - id: troubleshooting
    path: references/troubleshooting.md
    description: 常见错误排查表
    triggers: 报错, 失败, 不生效
  - id: rare-case
    path: references/rare-case.md
    description: 极少见场景，只应由 agent 主动判断加载
    # triggers 留空 → 不参与关键词自动加载，只能被 agent 调用
    # skill_resource_load 主动加载
browse_paths:
  - path: references/full-docs/
    description: 完整参考手册，体量大，请自行用 view/grep 检索具体片段
---

# <Skill 标题>

## 核心用法（必读，永远随 skill 激活注入，覆盖 80% 场景）
...

## 索引说明（给人看的，agent 会看到 frontmatter 自动生成的清单，这里可以再提示一句）
需要复杂配置时用 skill_resource_load 加载 `advanced`；报错优先看 `troubleshooting`。
```

目录结构对应：

```
.claude/skills/<skill-name>/
├── SKILL.md
├── references/
│   ├── advanced.md
│   ├── troubleshooting.md
│   └── rare-case.md
└── references/full-docs/   （browse_paths 指向的大型文档库，不需要在 resources 里登记）
```

### Frontmatter 字段说明

| 字段 | 是否必填 | 说明 |
|---|---|---|
| `name` | 推荐填写 | 缺省取目录名或文件名。建议显式填写，kebab-case |
| `description` | 推荐填写 | **最重要字段**——决定该 skill 是否被自动激活，要清楚说明"什么场景下用" |
| `triggers` | 可选 | 逗号分隔关键词，命中会自动激活 skill 本体。**强烈建议显式填写** |
| `resources` | 可选 | 结构化子资源列表，每项 `id`/`path`/`description`/`triggers`（`triggers` 可留空） |
| `browse_paths` | 可选 | 纯提示性子资源库，每项 `path`/`description`，**不受任何加载机制管理** |

> 旧字段名 `trigger_words` 仍兼容；没有 `resources`/`browse_paths` 的旧 SKILL.md
> 行为不变（这两个字段是纯新增，向后兼容）。

## 正文写作规范

1. 直接给"可执行的规范/知识"，不要写"我将帮你创建一个skill"之类的元描述
2. 用 Markdown 二级标题（`##`）分节，每节聚焦一个子话题
3. 代码示例要精炼、可直接复用，避免大段无关样板
4. 分层结构下，主文件正文只保留高频内容；细节挪到 `references/`，靠 `resources`
   索引和 agent 的判断按需拉取，不要图省事把所有内容都堆回主文件

## 创建流程（生成此 skill 时遵循）

1. 向用户确认：
   - skill 的核心用途（一句话 description）
   - 触发场景关键词（中英文都列）
   - skill 名称（kebab-case，作为目录名）
   - **预估内容体量** → 决定单文件 / 分层 / 需要 browse_paths
2. 若分层：进一步确认每个子话题的 `id`/`description`，以及是否需要 `triggers`
   （不确定就留空，交给 agent 主动判断）
3. 创建目录 `.claude/skills/<name>/`（分层时同时建 `references/`）
4. 写入 `SKILL.md`：frontmatter 字段齐全，正文按体量选择对应格式
5. 分层时，逐个写入 `references/*.md` 文件内容
6. **Checklist**：确认每个 `references/*.md` 文件都在 `resources` 或 `browse_paths`
   里有登记——漏登记的文件永远不会被加载机制发现，只能靠 agent 偶然 `view` 到
7. 创建完成后提示用户：可以用 `skill_list` / `skill_resource_list` 工具查看是否被
   正确发现

## 参考

- 渐进式加载机制原理、双通道加载、卸载后再加载的行为 → 本 skill 的
  `progressive-loading` 子资源（`references/progressive-loading.md`）
- 完整示例（单文件/分层/browse_paths 三种） → 本 skill 的 `examples` 子资源
  （`references/examples.md`）
- 已有单文件示例：`.claude/skills/python-expert/SKILL.md`、
  `.claude/skills/iching_oracle/SKILL.md`
