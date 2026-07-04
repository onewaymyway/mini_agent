# Skill 系统说明

本文档说明 mini-agent 中当前 Skill 系统的发现、激活、注入、使用追踪与压缩重附机制，便于维护者理解「技能」如何影响 system prompt。

---

## 1. 设计目标

Skill 是一组可按需加载到 system prompt 的领域说明。它的核心目标是：

1. **按需增强能力**：只有任务需要某类专业流程时才加载对应说明，避免长期占用上下文。
2. **模型可自主管理**：Agent 可以通过工具查看、激活或卸载技能，不完全依赖静态关键词。
3. **可追踪实际使用**：区分「已加载」与「真正被回复使用」，只有实际使用才更新 LRU 记录。
4. **压缩后可恢复关键上下文**：对话历史压缩时，根据最近使用顺序和预算重新附加 skill 内容。

---

## 2. System Prompt 强制规则：先查 Skill，再临时探索

`prompts/system/agent_core.md` 中包含一条对模型的硬性要求（"Check for a matching skill
first"）：在从零探索一个任务（读代码、搜索、试错）之前，模型必须先检查是否已有 Skill 覆盖
该任务：

1. 优先检查当前 system prompt 中**已加载**的 skill（这些具有最高优先级）。
2. 如果已加载的 skill 都不匹配，调用 `skill_list` 查看还有哪些可用。
3. 如果找到匹配但尚未加载的 skill，调用 `skill_activate` 加载并按其指引操作。
4. 只有在**没有任何 skill（已加载或可用）适用**，或匹配的 skill 指引明显不足以完成当前任务时，
   才允许进行临时探索。

这条规则本身不属于 Skill 发现/加载机制的代码逻辑（不在 `SkillLoader` /
`SkillActivationTool` 中），而是写死在 system prompt 里的行为约束，目的是减少模型
"绕过已有 Skill 直接读代码摸索" 的情况，提升一致性并降低 token 消耗。

## 3. Skill 文件格式与发现

### 2.1 支持的目录布局

`SkillLoader` 会从一个或多个技能目录中发现 `SKILL.md`：

```text
skills/
  docx/
    SKILL.md
  pdf/
    SKILL.md
  image.md          # 扁平布局也支持
```

发现规则：

- 递归查找 `*/SKILL.md`。
- 同时读取技能目录根部的 `*.md` 文件，但会跳过根部同名 `SKILL.md`。
- 同名 skill 后发现的会覆盖先发现的，调用侧应避免重名。

### 2.2 元数据解析

每个 skill 会被解析成 `Skill` 对象，核心字段包括：

| 字段 | 含义 |
|------|------|
| `name` | skill 名称，工具调用和 CLI 命令都使用该名称 |
| `description` | 简短说明，用于目录展示和 system prompt 目录注入 |
| `location` | `SKILL.md` 文件路径 |
| `content` | 完整 `SKILL.md` 文本 |
| `trigger_words` | 用于关键词辅助自动激活的触发词 |

`SKILL.md` 可以使用 frontmatter 声明元数据；若缺失，系统会从正文和文件名推断名称、描述和触发词。

frontmatter 还支持可选的 `platforms` / `tags` 字段，用于限制该 skill 只在特定平台或
tag 策略下才会被发现/加载（不满足条件时连描述都不会注入 system prompt）；详见
[Skill/Agent/Hook/Tool 平台与 Tag 过滤指南](platform-tag-loading-guide.md)。

---

## 4. 激活与卸载入口

Skill 有三类管理入口：CLI、Agent 工具、关键词辅助激活。

### 3.1 CLI 命令

REPL 中提供以下命令（实现位于 `src/mini_agent/cli/commands/skills.py`）：

| 命令 | 作用 |
|------|------|
| `/skills` | 列出全部可用技能、激活状态和粗略 token 成本 |
| `/skill on <name> [...]` | 激活一个或多个技能 |
| `/skill off <name> [...]` | 卸载一个或多个技能 |
| `/skill info <name>` | 查看某个技能全文和状态 |
| `/skill stats` | 查看使用追踪、LRU 顺序和压缩预算预览 |
| `/skill reset` | 卸载所有当前激活技能 |

CLI 命令只改变 `SkillLoader.active` 状态；真正的「使用记录」由回复后的检测流程更新。

### 3.2 Agent 工具

Agent 初始化时，如果传入了 `skill_loader`，会注册以下技能管理工具：

| 工具 | 作用 |
|------|------|
| `skill_list` | 返回所有技能的名称、描述、激活状态和汇总信息 |
| `skill_activate` | 按名称激活一个或多个技能，并要求提供原因 |
| `skill_deactivate` | 按名称卸载一个或多个技能，并要求提供原因 |
| `compact_history` | 触发带 skill 重附逻辑的历史压缩 |
| `skill_stats` | 返回技能使用追踪和预算状态 |

这些工具让模型可以先查看目录，再按任务阶段加载或卸载相关 skill。

### 3.3 关键词辅助激活

每轮 `run_turn()` 开始时，若关键词激活功能已启用，`SkillLoader.auto_activate(user_message)` 会根据 `trigger_words` 对用户输入做启发式匹配。匹配成功的技能会自动加入 active 列表，并在终端打印已加载提示。

**默认关闭。** 关键词匹配基于静态触发词，可能因词语歧义造成 skill 被意外拉起、多余地占用 context token。推荐的方式是让模型通过 `skill_list` + `skill_activate` 工具按需显式加载。

#### 开启方式

**静态（`agent_config.json`）**

```json
{
  "skill_keyword_activation_enabled": true
}
```

**运行时 toggle（REPL 命令）**

| 命令 | 作用 |
|------|------|
| `/skill autoload` | 查看当前开关状态 |
| `/skill autoload on` | 开启关键词自动激活 |
| `/skill autoload off` | 关闭关键词自动激活 |

**代码**

```python
cfg.skill.keyword_activation_enabled = True   # 开启
cfg.skill.keyword_activation_enabled = False  # 关闭
```

### 3.4 `exclude()`：彻底排除（2026-06 新增，Stage 3.2）

`SkillLoader.exclude(name)` 与 `deactivate(name)` 的区别是把 skill 从
`_all` 中**整体删除**，而不只是从 `_active` 列表移除——意味着排除后既不能
被 `skill_activate` 显式激活，也不会被 `auto_activate()` 的关键词命中
重新拉起。这是 [`mini-agent eval --without-skill`](self-evolution-stage3-2-guide.md)
严格对比实验的基础：要保证"排除某个 skill"是真正的"完全不参与"，而不是
"默认不激活但仍可能被关键词触发"。

```python
loader.exclude("docx")   # docx 从 _all 中被 del，对本次 SkillLoader 实例彻底不可见
```

### 3.5 `skill_propose`：让 agent 自己生成新 skill（2026-06 新增，Stage 3.1）

除了人工编写 SKILL.md，agent 也可以调用 `skill_propose` 工具把
`memory.jsonl` 中沉淀的 lesson 提炼为一份新的 SKILL.md 提案。提案不会
直接出现在主分支或当前工作目录——它通过自我演化安全网（`StateRepo.apply()`，
tier 固定 T1）写到一个独立的 `evolve/<date>-skill-<name>` git 分支上，
等待人工 `/evolution show|diff` 审核后手动 `git merge`，合并后才会被
`SkillLoader` 在下次启动时发现。详见
[自我演化 lesson → skill 闭环指南（Stage 3.1）](self-evolution-stage3-1-guide.md)。

---

## 5. System Prompt 注入流程

### 4.1 Active skills 正文注入

构建 system prompt 时，`Agent._build_system()` 会读取当前 active skill，并调用 `SkillLoader.build_context()` 生成正文：

```text
## Active skills

The following skills are currently active and provide additional instructions:

- skill-a
- skill-b

---

## Skill: skill-a

...SKILL.md content...
```

如果未激活任何技能，则不会注入 active skills 块。

### 4.2 Skill chunking

当 `cfg.skill_chunking_enabled` 为真且存在历史消息时，系统会用最近一条用户消息作为 query，只从每个 active skill 中挑选最相关的最多 3 个 `##` 章节注入。

这是一种上下文节省策略：

- skill 内容较短或章节数不多时，直接注入全文。
- skill 内容较长时，按 query 词重叠给章节打分，返回 top-N 章节。

### 4.3 Available Skills 目录注入

只要存在可用 skill，system prompt 还会追加一个工具管理目录：

- 告诉模型可以调用 `skill_list`、`skill_activate`、`skill_deactivate`。
- 列出当前 active skill 及说明。
- 列出尚未加载的 skill 及说明。
- 提醒模型在相关阶段激活 skill、阶段结束后卸载以节省上下文。

当存在 active skill 时，目录还会附加使用声明约定：如果回复确实应用了某个 active skill 的指导，在最终回复末尾追加 `<skill_used>name</skill_used>`；多个 skill 用逗号分隔。

---

## 6. 实际使用检测

Skill 系统刻意区分：

- **加载 / 激活**：skill 进入 active 列表，内容可能被注入 prompt。
- **实际使用**：assistant 回复被判定为应用了该 skill 的指导。

只有「实际使用」会更新 `SkillUsageTracker`，进而影响 LRU 排序和压缩重附优先级。

### 5.1 Track A：显式声明

如果 assistant 回复中包含：

```text
<skill_used>docx</skill_used>
<skill_used>docx,pdf</skill_used>
```

检测器会认为对应 active skill 被使用。标签会在输出清理流程中移除，避免污染用户可见内容。

### 5.2 Track B：指纹匹配

系统也会为每个 skill 构建指纹，用来捕捉未显式声明但明显使用了 skill 指导的情况：

- 高价值词：CamelCase、API 名、CLI flag、常量、文件扩展名等，命中权重更高。
- 普通技术词：从正文中提取的高频技术词，过滤常见停用词。
- 默认阈值：命中分数达到 0.15 即认为使用。

### 5.3 更新时机

在 `_agentic_loop()` 中，LLM 回复被追加到历史后，如果 `response.text` 非空，系统调用：

```python
used = skill_loader.record_usage(response.text)
```

`record_usage()` 只检查当前 active skill；检测成功后调用 `tracker.record(name)` 更新调用次数和最近使用时间。

---

## 7. Skill 使用追踪与压缩重附

### 6.1 LRU 记录

`SkillUsageTracker` 为每个实际使用过的 skill 维护：

| 字段 | 含义 |
|------|------|
| `name` | skill 名称 |
| `last_called` | 最近实际使用时间 |
| `call_count` | 实际使用次数 |

`records` 和 `recent_names()` 都按最近使用时间降序返回，最新使用的 skill 排在前面。

### 6.2 压缩上下文预算

压缩重附使用两层预算：

| 配置 | 默认值 | 含义 |
|------|--------|------|
| `skill_compact_per_skill` | `5000` | 单个普通 skill 最多贡献的 token 数 |
| `skill_compact_budget` | `25000` | 所有普通 skill 共享的 token 总预算 |

Token 估算采用粗略规则：`1 token ≈ 4 字符`。

### 6.3 保护规则

压缩时，当前 active skill 会作为 protected 集合传入 tracker：

1. 当前 active skill 不受单 skill 截断限制。
2. 当前 active skill 不受总预算限制，即使预算耗尽也强制写入。
3. 如果候选集合中只有一个 skill，也会全文保留，避免单 skill 被无意义截断。

### 6.4 压缩触发路径

带 skill 重附的压缩可以通过以下路径触发：

- CLI `/compact`。
- Agent 工具 `compact_history`。
- 代码直接调用 `agent.compact_with_skills()`。

压缩流程：

1. 用 LLM 生成历史摘要。
2. 调用 `_build_skill_compact_block()` 生成 skill 重附块。
3. 将历史替换为摘要消息。
4. 如果有重附块，将其作为新的 user 消息写入历史。
5. 保存 session。

---

## 8. 推荐维护准则

1. **Skill 内容应聚焦**：把可执行步骤、约束和示例放在 `SKILL.md` 中，避免泛泛而谈。
2. **Description 要短而清晰**：它会出现在 CLI、工具结果和 system prompt 目录里。
3. **Trigger words 不应过宽**：过宽会导致无关任务自动加载 skill，浪费上下文。
4. **激活不等于使用**：调试 LRU 或压缩问题时优先查看 `/skill stats`，确认是否有实际使用记录。
5. **长 skill 应分章节**：使用 `##` 划分章节可以让 chunking 模式更有效。
6. **阶段结束及时卸载**：模型和用户都可以卸载不再需要的 skill，以减少后续 system prompt 体积。

---

## 9. 关键代码索引

| 文件 | 说明 |
|------|------|
| `src/mini_agent/skills/__init__.py` | `Skill`、`SkillLoader`、发现、解析、激活、上下文构建、压缩上下文入口 |
| `src/mini_agent/skills/usage_detector.py` | 显式声明和指纹匹配的双轨使用检测 |
| `src/mini_agent/skills/tracker.py` | LRU 使用追踪与压缩重附预算实现 |
| `src/mini_agent/tools/skill_manager.py` | `skill_list`、`skill_activate`、`skill_deactivate`、`compact_history`、`skill_stats` 工具注册 |
| `src/mini_agent/prompts/system/active_skills.md` | active skill 注入到 system prompt 的模板 |
| `src/mini_agent/agent.py` | 自动激活、system prompt 拼装、回复后记录使用、压缩重附 |
| `src/mini_agent/cli/repl.py` | `/skills` 与 `/skill ...` CLI 命令实现 |
| `src/mini_agent/tools/evolution.py` | `skill_propose` 工具：lesson → SKILL.md 提案（2026-06 新增，Stage 3.1） |
| `src/mini_agent/evolution/eval_runner.py` | `mini-agent eval` 调用 `SkillLoader.exclude()` 做严格对比（2026-06 新增，Stage 3.2） |
| `src/mini_agent/perception/hot_reload.py` | `HotReloader`：mtime 轮询热重载，`SkillLoader.rediscover()` 作为回调 |

---

## 10. 热重载

`SkillLoader` 支持运行时**无重启热重载**：

```python
# HotReloader 在 Agent.__init__ 中自动注册，无需手动调用
# 每个 turn 开始时自动轮询；也可通过 /reload 命令手动触发
```

新增 API `SkillLoader.rediscover(dirs)`：

- 重新扫描 `_dirs` 中的所有 `SKILL.md` 和 `*.md`
- 新增 skill → 加入 `_all`
- 修改的 skill → 重新解析并覆盖（已激活状态保留）
- 删除的 skill → 从 `_all` 和 `_active` 中移除
- 重建 `SkillUsageDetector` 指纹

详见 [热重载机制说明](hot-reload-guide.md)。

---

> 最后更新：2026-06（新增热重载 `rediscover()`、`patch_file_simple` 工具、隐私保护、raw-output 模式）
