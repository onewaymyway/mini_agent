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
  office/
    excel/
      SKILL.md       # 嵌套多层同样能被发现
  image.md          # 扁平布局也支持
```

发现规则（对应 `SkillLoader._discover()`）：

- 对每个技能目录调用 `d.rglob("SKILL.md")`，这是**任意深度**的递归查找，不局限于
  `skills/<name>/SKILL.md` 这一层——`SKILL.md` 嵌在几层子目录下都能被发现。上面
  `office/excel/SKILL.md` 就是两层嵌套的例子。
- 同时读取技能目录根部的 `*.md` 文件（非递归，只看根部这一层），但会跳过根部同名
  `SKILL.md`。
- 同名 skill 后发现的会覆盖先发现的（`self._all[skill.name] = skill`），调用侧应
  避免重名。

> 注意区分：这里的递归发现针对的是 `SKILL.md` 本身。第 3.6 节「渐进式加载」里
> 登记在 `resources`/`browse_paths` 下的子资源文件（如 `references/*.md`）**不会**
> 被 `_discover()` 扫描到——它们只在对应 `SKILL.md` 被解析、且在 frontmatter 中
> 显式登记路径后才被感知，不登记就永远不会被加载机制发现（见第 8 节维护准则第 9 条）。

### 2.2 技能目录从哪来：`skills_dirs` 的解析优先级

`SkillLoader.__init__` 接收的 `skills_dirs: list[Path]` 本身支持传入多个目录，但
默认情况下（不额外传 `--skills-dir` CLI 参数）这个列表**通常只有一个元素**，来自
`_resolve_skills_dir()`（`config/prompt_builder.py`）按优先级挑选出的**第一个存在的
目录**，而不是把项目级和全局级目录都合并进来：

```python
candidates = [
    root / ".claude" / "skills",           # 项目级，旧路径，兼容保留
    paths.global_skills_dir,               # ~/.agent/skills，全局级，新路径
    Path.home() / ".claude" / "skills",    # 旧全局路径，兼容保留
]
# 按顺序取第一个 is_dir() 为真的候选，其余候选即使存在也不会被使用
```

也就是说：如果项目根目录下有 `.claude/skills/`，就只会用它，`~/.agent/skills/`
里的 skill **不会**被自动一起加载；只有项目级目录不存在时才会退到全局级。

CLI (`cli/app.py`) 在此基础上，如果用户额外传了 `--skills-dir <path>`，会把它
**追加**到列表里，这时才真正出现"多个目录同时生效"的情况：

```python
skill_dirs = [cfg.skills_dir] if cfg.skills_dir else []   # 上面优先级解析出的那一个
if args.skills_dir:
    skill_dirs.append(Path(args.skills_dir).expanduser()) # 手动追加的第二个
```

`skill-generator` 里提到的"项目级 `.claude/skills/<name>/SKILL.md`" 和 "全局级
`~/.agent/skills/<name>/SKILL.md`" 是**互斥候选关系**（谁存在用谁，项目级优先），
不是两处都会被扫描；理解这一点很重要，否则容易误以为把 skill 放进全局目录也会
被同一个项目自动捡到。

### 2.3 元数据解析

每个 skill 会被解析成 `Skill` 对象，核心字段包括：

| 字段 | 含义 |
|------|------|
| `name` | skill 名称，工具调用和 CLI 命令都使用该名称 |
| `description` | 简短说明，用于目录展示和 system prompt 目录注入 |
| `location` | `SKILL.md` 文件路径 |
| `content` | 完整 `SKILL.md` 文本 |
| `trigger_words` | 用于关键词辅助自动激活的触发词 |
| `requires` | （Stage 7 / 14.2 新增）依赖的其他 skill 名称列表；依赖不存在时 `activate()` 只打印警告，**不阻塞激活** |
| `conflicts_with` | （Stage 7 / 14.2 新增）互斥 skill 名称列表；其中任一 skill 已激活时，`activate()` 会**拒绝激活**当前 skill 并打印警告 |
| `activation_conditions` | （Stage 7 / 14.2 新增）正则表达式列表，`matches_query()` 里作为 `trigger_words` 之外的第二种自动匹配条件，命中任一正则即视为匹配 |
| `confidence_score` | （Stage 7 / 14.3 新增）0.0–1.0，影响注入 skill 内容时的语气强度；默认 1.0 |
| `positive_count` / `negative_count` | （Stage 7 / 14.3 新增）正向印证 / 反例计数，用于调整 `confidence_score` |
| `platforms` / `tags` | 限制该 skill 只在特定平台或 tag 策略下才会被发现/加载（不满足条件时连描述都不会注入 system prompt）；详见 [Skill/Agent/Hook/Tool 平台与 Tag 过滤指南](platform-tag-loading-guide.md) |
| `resources` | （2026-07 新增）结构化子资源列表，见第 3.6 节「渐进式加载」 |
| `browse_paths` | （2026-07 新增）纯提示性子资源库路径，不受任何加载机制管理，见第 3.6 节 |

`SKILL.md` 可以使用 frontmatter 声明元数据；若缺失，系统会从正文和文件名推断名称、描述和触发词。

frontmatter 还支持可选的 `platforms` / `tags` 字段，用于限制该 skill 只在特定平台或
tag 策略下才会被发现/加载（不满足条件时连描述都不会注入 system prompt）；详见
[Skill/Agent/Hook/Tool 平台与 Tag 过滤指南](platform-tag-loading-guide.md)。

`resources`/`browse_paths` 是纯新增字段，旧格式 `SKILL.md`（不含这两个 key）解析结果
为空列表，行为与之前完全一致，无需迁移。`requires`/`conflicts_with`/
`activation_conditions` 同样是纯新增字段，旧格式 `SKILL.md`（frontmatter 不含对应
key）解析结果为空列表/默认值，行为同样与升级前完全一致，无需迁移。

### 2.4 `requires` / `conflicts_with` 对激活流程的实际影响

这三个字段不只是元数据，而是直接参与 `SkillLoader.activate()` 的判断逻辑，容易被
忽略，单独说明一下：

- **`conflicts_with` 是硬约束**：`activate(name)` 时，若 `conflicts_with` 里任一
  skill 已在 `_active` 列表中，直接返回 `False`（拒绝激活），并打印警告，不会加载
  该 skill 的内容到 system prompt。
- **`requires` 只是软提示**：`activate(name)` 时，若 `requires` 里的 skill 不在
  `_all`（未被发现/不存在），只打印警告，**仍然继续激活**当前 skill——不会因为依赖
  缺失而失败。
- **`activation_conditions` 只影响自动匹配**，不影响 `skill_activate` 工具或
  `/skill on` 命令的手动激活——手动激活始终生效（除非撞上 `conflicts_with`）。
- 这三者与渐进式加载（3.6 节）的 `resources`/`browse_paths` 是两套独立机制：前者
  管的是"skill 之间能不能共存/怎么被自动命中"，后者管的是"单个 skill 内部的内容
  怎么分层加载"，两者可以同时使用，互不影响。

### 2.5 frontmatter 触发词字段名：`triggers` 优先，`trigger_words` 兼容旧名

`_parse_skill()` 解析触发词时的取值顺序是：

```python
triggers_raw = fields.get("triggers", fields.get("trigger_words", ""))
```

即 frontmatter 里写 `triggers:` 会被优先采用；只有没写 `triggers` 时才 fallback
读取旧字段名 `trigger_words:`。两个字段名对应的是同一个东西——解析后都存进
`Skill.trigger_words`（Python 对象属性名固定用这个），只是**文件里怎么写**允许两种
拼法。新写 `SKILL.md` 一律用 `triggers:`（`.claude/skills/skill-generator` 也是这么
推荐的），`trigger_words:` 仅为兼容遗留文件保留，不要在新文件里混用两个字段名
（同时写会导致 `trigger_words:` 被忽略，因为 `triggers` 已经命中）。

### 2.6 推荐的新 skill 格式（参考 `.claude/skills/skill-generator`）

项目内置的 `.claude/skills/skill-generator/SKILL.md` 本身就是一个分层 skill 的
自举示例，它给出的是"按体量二选一"的格式规范，而不是单一固定格式：

**第一步：先判断体量，再选格式**

| 体量特征 | 推荐格式 | 做法 |
|---|---|---|
| 预估 < 150 行、内容边界单一 | **单文件** | 全部正文直接写进 `SKILL.md`，不要为了"看起来规范"硬拆 `references/` |
| 预估 > 150 行，或明显能拆成独立子话题（如"基础用法"+"高级配置"+"错误排查表"） | **分层结构** | 主文件只留高频必读内容 + 索引，细节移到 `references/*.md`，在 frontmatter `resources` 里登记 |
| 内容是"一个库"而不是"一份文档"（完整 API 手册、按语言分文件夹的 SDK 文档、几十个示例），具体用哪段取决于当次任务 | **`browse_paths`** | 不注册进 `resources`，只留提示，让 agent 自己 `view`/`grep`/`bash find` 检索定位 |

**分层格式的目录约定**：

```
.claude/skills/<skill-name>/
├── SKILL.md
├── references/
│   ├── advanced.md          ← 登记在 resources 里，可被关键词/工具双通道加载
│   ├── troubleshooting.md
│   └── rare-case.md         ← triggers 留空，只能靠 agent 主动用 skill_resource_load 拉取
└── references/full-docs/    ← 登记在 browse_paths 里，不进 resources，agent 自行检索
```

**技能目录位置**：项目级放 `.claude/skills/<skill-name>/SKILL.md`，全局级放
`~/.agent/skills/<skill-name>/SKILL.md`，两者是 2.2 节说的互斥候选关系（项目级
存在则只用项目级），每个 skill 独立一个子目录，目录名建议与 `name` 字段一致。

**正文写作规范**（区别于"字段格式对不对"，这是"内容写得好不好"）：

1. 直接给可执行的规范/知识/checklist，不要写"我将帮你创建一个 skill"之类的元描述。
2. 用 `##` 二级标题分节，每节聚焦一个子话题，方便 3.6 节提到的 skill chunking
   按章节打分选取。
3. 代码示例精炼、可直接复用，避免堆砌无关样板。
4. 分层结构下，主文件正文只保留高频内容；细节挪到 `references/`，不要图省事把
   所有内容都堆回主文件，否则和单文件没区别，白白多了一层目录结构。

**创建后的自检 checklist**：确认每个 `references/*.md` 都在 `resources` 或
`browse_paths` 里有登记（对应 2.1 节末尾的提醒——递归发现只找 `SKILL.md`，子资源
文件必须显式登记才会被感知），然后用 `skill_list` / `skill_resource_list` 验证是否
被正确发现。

完整示例（单文件 / 分层 / `browse_paths` 三种）见
`.claude/skills/skill-generator/references/examples.md`；渐进式加载的机制细节见
`.claude/skills/skill-generator/references/progressive-loading.md`（与本文档 3.6
节讲的是同一套机制，互为补充）。

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
| `skill_resource_list` | （2026-07 新增）列出某个已激活 skill 下的子资源、加载状态与历史使用次数 |
| `skill_resource_load` | （2026-07 新增）主动加载指定子资源，不依赖关键词命中 |
| `skill_resource_unload` | （2026-07 新增）主动卸载子资源，释放 context，使用记录不清零 |

这些工具让模型可以先查看目录，再按任务阶段加载或卸载相关 skill（或 skill 下的子资源）。

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

### 3.6 渐进式加载：`resources` 与 `browse_paths`（2026-07 新增）

背景：一份 `SKILL.md` 如果塞入所有细节，一旦激活就整段占用 context，且大部分内容
在具体任务里往往用不上。渐进式加载在"skill 级激活"之上增加了"资源级加载"这一层，
让 skill 的主文件只保留高频必需内容，长尾细节按需拉取。

#### 数据结构

`Skill` 新增两个字段：

- `resources: list[SkillResource]` —— 结构化、可整段加载的子文档，每项含
  `id`/`path`/`description`/`triggers`（`triggers` 可留空）。
- `browse_paths: list[dict]` —— 纯提示性的大型文档库路径（`path`/`description`），
  **不接入任何加载机制**，agent 应该用 `view`/`grep`/`bash find` 自己检索，
  不计入 token 预算，也不在 tracker 里留痕。

两者在 frontmatter 中声明：

```yaml
resources:
  - id: advanced
    path: references/advanced.md
    description: 复杂参数组合与边界情况处理
    triggers: 高级用法, edge case
  - id: rare-case
    path: references/rare-case.md
    description: 极少见场景，只应由 agent 主动判断加载
    # triggers 留空 → 不参与关键词自动加载
browse_paths:
  - path: references/full-docs/
    description: 完整参考手册，体量大，请自行检索具体片段
```

如何选择 `resources` 还是 `browse_paths`：内容是"一份边界清晰、体量可控（建议
<300 行）、大概率要整份消化的子文档" → `resources`；内容是"一个库"，
"哪一段有用取决于具体任务" → `browse_paths`。

#### 两条加载通道

1. **关键词自动通道**：`SkillLoader.auto_activate_resources(text)` 在每轮
   `auto_activate(user_message)` 之后紧接着调用（`agent.py` 里的调用点），对所有
   **已激活 skill** 名下、`triggers` 非空的资源做匹配，命中且未加载则自动加载。
   `triggers` 留空的资源不参与此通道。
2. **Agent 主动通道**：`skill_resource_load(skill_name, resource_id, reason)` /
   `skill_resource_unload(...)` 工具（见 3.2 节），agent 可以不依赖关键词命中，
   自主判断当前任务是否需要某个子资源。这是为了覆盖"关键词覆盖不到，但 agent 从
   已有上下文能判断出需要"的场景。

两条通道共享同一份加载状态和同一个 `SkillUsageTracker`（key 为
`"{skill_name}/{resource_id}"`），重复加载是幂等的，且会从磁盘重新读取（支持热编辑）。

#### 注入形态

skill 激活后，`build_context()` 会在其正文之后追加：

1. **资源清单**（永远展示，很轻量）：id、说明、加载状态（`● 已加载` /
   `○ 未加载`）、历史使用次数。
2. **已加载资源的完整内容**（受独立的 `per_resource_tokens` 预算截断，默认 3000）。
3. **`browse_paths` 提示**（纯文字，提醒 agent 自行检索，不要整段加载）。

#### 卸载与再加载的行为

- **资源级卸载**（`skill_resource_unload` 或后续可能的预算挤出）：只清 context
  内容，清单条目**不消失**，状态回到"未加载"；`SkillUsageTracker` 里的调用次数/
  最近使用时间**保留不清零**，下次 `skill_resource_list` 仍能看到历史使用信号。
- **父 skill 整体 `deactivate`**：其下所有已加载资源随之清出 context（资源内容
  本来挂在父 skill 的上下文块下），清单重置为全部"未加载"，但 tracker 统计不清零。
- **父 skill 再次 `activate`**：清单重新展示，**默认不自动恢复**之前加载过的资源
  （避免激活时无脑复活旧内容导致 context 膨胀），清单上会标注历史使用次数供 agent
  参考决定是否立即重新加载。

#### 向后兼容

旧格式 `SKILL.md`（不含 `resources`/`browse_paths`）解析结果为空列表，`build_context()`
不会追加任何资源相关内容，行为与升级前完全一致，无需迁移。

### 3.7 `skill_propose`：让 agent 自己生成新 skill（2026-06 新增，Stage 3.1）

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
7. **体量大就分层**：正文预估超过 ~150 行或能拆出独立子话题时，把细节挪到
   `references/*.md` 并在 `resources` 里登记，不要都堆进主文件。
8. **文档库用 `browse_paths`，不要用 `resources`**：大型多文件文档集合应该让 agent
   自己检索定位，整段加载既浪费预算又不聚焦。
9. **别漏登记**：每个 `references/*.md` 都必须出现在 `resources` 或 `browse_paths`
   里，否则永远不会被加载机制发现。

---

## 9. 关键代码索引

| 文件 | 说明 |
|------|------|
| `src/mini_agent/skills/__init__.py` | `Skill`、`SkillResource`、`SkillLoader`、发现、解析、激活、上下文构建、渐进式资源加载、压缩上下文入口 |
| `src/mini_agent/skills/usage_detector.py` | 显式声明和指纹匹配的双轨使用检测 |
| `src/mini_agent/skills/tracker.py` | LRU 使用追踪与压缩重附预算实现（skill 级与资源级共用） |
| `src/mini_agent/tools/skill_manager.py` | `skill_list`、`skill_activate`、`skill_deactivate`、`compact_history`、`skill_stats`、`skill_resource_list`、`skill_resource_load`、`skill_resource_unload` 工具注册 |
| `src/mini_agent/prompts/system/active_skills.md` | active skill 注入到 system prompt 的模板 |
| `src/mini_agent/agent.py` | 自动激活（skill 级 + 资源级）、system prompt 拼装、回复后记录使用、压缩重附 |
| `src/mini_agent/cli/repl.py` | `/skills` 与 `/skill ...` CLI 命令实现 |
| `src/mini_agent/tools/evolution.py` | `skill_propose` 工具：lesson → SKILL.md 提案（2026-06 新增，Stage 3.1） |
| `src/mini_agent/evolution/eval_runner.py` | `mini-agent eval` 调用 `SkillLoader.exclude()` 做严格对比（2026-06 新增，Stage 3.2） |
| `src/mini_agent/perception/hot_reload.py` | `HotReloader`：mtime 轮询热重载，`SkillLoader.rediscover()` 作为回调 |
| `.claude/skills/skill-generator/SKILL.md` | 生成新 skill 的规范与流程，含单文件/分层结构判断标准（2026-07 更新） |
| `.claude/skills/skill-generator/references/progressive-loading.md` | 渐进式加载机制详解（该 skill 自身的可加载子资源，也是分层结构的自举示例） |
| `.claude/skills/skill-generator/references/examples.md` | 单文件/分层/browse_paths 三种完整示例 |

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

> 最后更新：2026-07（修正第 3 节「Skill 文件格式与发现」：明确 `rglob` 为任意深度
> 递归发现、补充 `skills_dirs` 优先级解析（项目级 `.claude/skills` 与全局级
> `~/.agent/skills` 互斥候选）、补全 `requires`/`conflicts_with`/`activation_conditions`
> 等 Stage 7 字段及其对 `activate()` 的实际影响、`triggers`/`trigger_words` 字段名
> fallback 关系、整合 `.claude/skills/skill-generator` 给出的推荐新 skill 格式规范；
> 此前更新：新增渐进式加载机制：`resources`/`browse_paths` 字段、
> `skill_resource_list/load/unload` 工具、资源级关键词自动加载、卸载后再加载的
> 行为约定；更早更新：热重载 `rediscover()`、`patch_file_simple` 工具、隐私保护、raw-output 模式）