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

`agent_core.md` 里除了这条规则的文字陈述，还紧跟着一组正反例（✅ 正确 / ❌ 错误的
对话片段），用来对比"先 `skill_list` 再 `skill_activate`"与"`skill_list` 之后转头
用 `bash`/`read_file` 直接读 `SKILL.md`"两种走向——只写规则对长对话里的一致性帮助
有限，配合具体范例效果更稳定。

### 2.1 三类实际观察到的不稳定表现与对应修复（2026-07 新增）

在实践中，仅靠上面这条 system prompt 规则会出现三类典型的不稳定行为，下面逐一说明
现在是怎么修的、修复代码在哪：

**（a）该激活而没激活，模型自己直接探索。**
过去完全依赖模型自己在一大段 Available Skills 目录里判断"要不要激活"，这是纯软约束，
长对话里容易被忽略。现在改为在 `SkillLoader` 之上加一层确定性匹配：`run_turn()`
（`agent/turn_loop.py`）每轮用户消息入队后，调用
`agent/reminders_correction.py` 里的 `_inject_reminders_for_skill_candidates()`，
它内部调用 `SkillLoader.find_inactive_candidates(query)`——复用
`auto_activate()` 同一套 `trigger_words`/`activation_conditions`/资源级
triggers 匹配逻辑，但**只匹配、不激活**，也不受 `_auto_activate_blocked` 屏蔽
影响。命中则合成一条 reminder，点名具体的候选 skill 名字和描述，要求模型先
`skill_list`/`skill_activate` 确认再动手。

这与关键词自动激活（`keyword_activation_enabled`，见第 3.3 节）是两回事：
自动激活是直接帮模型把 skill 塞进 active 列表，模型可能都没意识到；这里只是把
"建议你现在检查一下 X"这句话摆到模型面前，激活与否仍由模型自己决定——因此该机制
**独立于** `keyword_activation_enabled` 开关，由单独的
`SkillConfig.candidate_reminder_enabled`（默认 `true`）控制。

**（b）不通过 `skill_activate`，而是直接读取 `SKILL.md` 文件内容。**
根因是 `SkillLoader.get_catalog()`（即 `skill_list` 工具的返回值）过去无论 skill
是否激活都会带上 `location`（skill 所在目录）字段，模型拿到路径后很容易图省事直接
`read_file`/`bash` 去 cat 文件，绕开 `skill_activate`。现在 `get_catalog()` 只对
**已激活**的 skill 返回 `location`（已激活 skill 的正文里常有相对路径引用，需要
这个目录才能拼出绝对路径，属于合法需求）；**未激活**的 skill 不再返回路径，改为
一句 `note`，把"怎么看正文"重新引导回 `skill_activate`。同时 `skill_list` /
`skill_activate` 两个工具的 description（`tools/skill_manager.py`）里也明确写明
"即使你已经知道或能猜到路径，也不允许直接读取文件"。

**（c）已激活，但仍去读文件，而不是用 context 里已经注入的内容。**
这通常不是模型故意不守规矩，而是它不确定 context 里的内容是否完整——尤其是开启
`skill_chunking_enabled` 时，注入的只是按 query 相关性挑出的最多 3 个章节，模型
合理怀疑"是不是还有没看到的部分"，于是转去读原文件核对。现在 `build_context()`
为每个已激活 skill 的注入内容新增一段边界声明：明确说明这段内容是"完整正文"还是
"节选章节"，并明确禁止再用 `read_file`/`view`/`grep`/`bash` 重新读取该 skill 目录
下的文件（磁盘上的文件可能因章节筛选、历史压缩等原因与当前展示版本不一致），同时
给出正规的替代路径——内容不够时应在回复里说明还缺什么，或调用
`skill_resource_list`/`skill_resource_load` 获取已登记的子资源。
`prompts/system/active_skills.md` 模板里也追加了同样的声明，作为全局层面的强调。

三处修复都停留在 system prompt / 工具 schema / 确定性匹配代码层面，没有引入运行时
文件系统拦截（例如 hook 级别的路径黑名单）——如果未来发现这三条仍不够稳定，可以
在 `hooks/runner.py` 里加一个 pre-tool-use hook，拦截对 `skills_dirs` 路径的直接
文件读取作为兜底，但目前的判断是先看 prompt 层面的效果。

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
| `skill_type` | （阶段七新增）留空 = 普通静态 skill（默认）；`generative-capability` = 领域功能包，见第 3.8 节 |
| `category_summary` | （阶段七新增）仅 `skill_type: generative-capability` 使用，`build_context()` 对这类 skill 只注入这一行摘要，不整段注入正文 |

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
| `capability_call` | （阶段七新增）调用 `skill_type: generative-capability` 的 skill，见第 3.8 节，与上面几个工具服务的"普通静态 skill"是完全不同的一类 |

这些工具让模型可以先查看目录，再按任务阶段加载或卸载相关 skill（或 skill 下的子资源）。

> **`skill_list` 返回字段说明（2026-07 变更）**：`SkillLoader.get_catalog()` 现在
> 只对**已激活**的 skill 返回 `location`（skill 所在目录，用于解析正文里的相对
> 路径）；**未激活**的 skill 不再返回 `location`，改为返回一句 `note`，引导模型
> 调用 `skill_activate`。这是第 2.1 节「问题1」修复的一部分——目的是不给模型一个
> 可以绕开 `skill_activate` 直接读文件的现成路径。若有代码依赖 `get_catalog()` 里
> 未激活 skill 的 `location` 字段，需要相应调整（目前项目内所有消费方——
> `context_builder.py`、`cli/commands/skills.py` 等——都只用 `name`/`description`/
> `active`，不受影响）。

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

> **卸载过的 skill 不参与关键词重新命中（2026-07）**：`auto_activate()` 会跳过
> 当前处于"卸载屏蔽"状态的 skill——即被 `deactivate()`（CLI/工具调用或 6.5 节
> 的 compact 自动卸载）卸载过、还没有被显式 `skill_activate` 重新拉起的 skill。
> 可以通过 `loader.auto_activate_blocked` 查看当前被屏蔽的名单。这是为了避免
> "模型/用户/自动垃圾回收刚判断不需要，下一句用户消息里的同一批关键词又把它
> 立刻拉回来"这种抖动；显式重新 `activate()` 会清除该 skill 的屏蔽状态。

> **与「候选 skill 提醒」（2.1 节「问题0」修复）的区别**：本节说的关键词自动
> 激活默认关闭，命中后**直接把 skill 加入 active 列表**，模型可能都没意识到
> 发生了什么；2.1 节新增的 `_inject_reminders_for_skill_candidates()` /
> `SkillLoader.find_inactive_candidates()` 默认**开启**（受
> `SkillConfig.candidate_reminder_enabled` 控制），但只是**匹配、不激活**——
> 命中后注入一条点名具体 skill 的 reminder，是否激活仍由模型自己决定。两套
> 机制可以同时开启，互不冲突：前者省一次工具调用但可能误激活无关 skill，后者
> 更保守但多一轮"模型自己确认"的过程。

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

> **与 `deactivate()` 的最新区别（2026-07）**：`deactivate(name)`（无论是
> CLI `/skill off`、`skill_deactivate` 工具调用，还是 6.5 节的 compact 自动
> 卸载）现在也会屏蔽该 skill 的关键词自动激活，但 skill 条目本身仍留在
> `_all` 里——区别在于**能否被显式 `skill_activate` 重新拉起**：
> `deactivate()` 之后仍然可以，`exclude()` 之后彻底不可以（条目已被删除）。
> 简单说：`deactivate` = "先收起来，模型/用户想用时还能显式拿回来"；
> `exclude` = "这次会话/评测里这个 skill 根本不存在"。

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

### 3.8 `generative-capability` skill：按需调用的领域能力包（阶段七新增）

本节说的是本文档目前唯一的第二类 skill——前面所有小节讲的都是"静态 skill"
（`skill_type` 留空，正文整段可能被 `build_context()` 注入 context，
`skill_activate`/`skill_resource_load` 都是围绕它设计的）。`generative-
capability` 是完全不同的另一类：**skill 内部是一批"成员"（member），每个
成员对应一个具体的、可执行的小能力（如某个网站的抓取脚本、某个模板的文档
生成脚本），成员数量可能长期持续增长（几十到几百个），主 context 里永远
不展开这份清单，只暴露一行 `category_summary`。**

#### 什么时候用这个而不是普通 skill

| | 普通静态 skill | `generative-capability` skill |
|---|---|---|
| 适用场景 | 成员少、变化慢、通用性强（如 docx/pptx 处理规范） | 成员多、长尾、持续增长（如按站点定制的抓取能力、按公司模板定制的文档生成） |
| 主 context 占用 | 激活后正文（或分片）整段注入 | 仅 `category_summary` 一行 |
| 内容怎么变多 | 人工编辑 `SKILL.md`/`references/` | 人工预置成员脚本，或由探索子agent自动补全生成 |
| 调用方式 | `skill_activate` + 之后正常对话 | `capability_call(skill_name, request)` 工具，每次都是一次独立调用，不进入"激活状态" |

不要把普通 skill 硬塞成 `generative-capability`（会白白损失掉正文对模型的
直接指导价值），也不要把该拆成"探索式领域能力包"的东西硬写成一份越写越长
的普通 `SKILL.md`（迟早会撑爆 context 或需要频繁人工维护）。判断标准就是
上表第二行："这个能力的具体清单，模型需不需要提前看到"——需要就是普通
skill，不需要（模型只需要知道"有这个能力，给个目标就能拿到结果或明确失败
原因"）就是 `generative-capability`。

#### 声明方式

```yaml
---
name: browser-site-scraper
skill_type: generative-capability
category_summary: 针对具体网站的网页抓取能力，支持自动扩展新网站
description: （给普通 skill 消费方/未识别 skill_type 的旧代码路径用的兜底文案，写法与静态 skill 一致）
---
```

`_parse_skill()` 解析出 `skill_type`/`category_summary` 两个字段（缺失时
`skill_type` 默认为空字符串，等价于普通静态 skill，**旧格式 `SKILL.md`
不受任何影响**）；`Skill.is_generative_capability` 是这两个字段的便捷判断
属性。

#### `build_context()` 的特殊处理

对 `is_generative_capability` 为真的 skill，`build_context()` 不走"整段
注入正文（或按 3.6 节分片）"这条路径，而是短路成：

```
## Skill: browser-site-scraper  [generative-capability]

针对具体网站的网页抓取能力，支持自动扩展新网站

这是一个按需检索的领域功能包，内部成员清单不在此处展开。
需要用到该能力时，调用 `capability_call(skill_name="browser-site-scraper",
request={...})` 工具——传入你的目标与期望的数据结构，会得到结果或明确的
失败原因，不需要（也不应该）自己去读 <skill目录> 目录下的 members/ 内容。
```

即使 skill 作者不小心在 `SKILL.md` 正文里写了成员清单细节，这条短路逻辑
也保证它们不会泄漏进主 context——因为正文根本没有被读取用于注入，只用了
`category_summary`/`description`。`get_catalog()`（`skill_list` 工具的数据
来源）也会给这类 skill 附带 `skill_type`/`category_summary` 字段，方便模型
在列目录时就能分辨"这个该用 `skill_activate` 还是该用 `capability_call`"。

#### `capability_call` 工具

`src/mini_agent/tools/capability_call.py::register_capability_tools()` 在
`Agent.__init__` 里与其他 skill 工具一起注册（同样遵循 `override=True` 约定，
兼容 SubAgent 持有独立 `skill_loader` 的场景）。工具接受 `skill_name` +
`request`（领域自定义的请求体，如 `{"text": "...", "target": {"url":
"..."}, "query": "..."}`），内部构造一个 `mini_agent.skills.
generative_capability.CapabilityEngine` 实例并调用其 `call(request)`，
按 `resolve → execute → explore → distill` 的标准流程处理（流程细节、
成员生命周期状态机、安全边界见
[generative-capability-skill-plan.md](../next_doc/generative-capability-skill-plan.md)
第 6–8 节，这是引擎侧的通用行为，本文档不重复展开）。

工具对两类误用会明确拒绝而不是静默尝试：

- `skill_name` 对应的是普通静态 skill（`skill_type` 未声明为
  `generative-capability`）→ 返回错误，提示应改用 `skill_activate`。
- `skill_name` 不存在 → 返回错误，并附上当前所有 `generative-capability`
  类型 skill 的名称列表，方便模型自行改正而不是瞎猜。

**已知限制（阶段十二/十四已部分解决，三档 SKILL 档接线缺口已于后续订正修复，
如实告知当前状态）**：`capability_call` 默认注入
`build_llm_resolver(current_llm_helper)`（第二级检索裁决），
`current_llm_helper` 通过 `tools/orchestration.py::get_current_llm_helper()`
拿到当前 `Agent.llm_helper`（跟随 `/model` 切换，见下方"检索裁决/探索子agent
的 LLM 调用"一节）；**阶段十二起也默认注入 `explore_runner`/`tool_executor`**
（`build_default_tool_executor(skill_dir=skill_dir)`，见
`generative-capability-skill-plan.md` 阶段十二/十四实施记录）；**下面"三档
member 执行机制"一节提到的 `playbook_repo`/`skill_runner`（SKILL 档）此前
一直未被注入，`generative_capability_three_tier_improvement_plan.md` 第5节
已修复，现在同样默认注入**，见该小节末尾说明。真正执行
底层操作原语的代码，一部分是项目内置的纯逻辑实现（`text-core`），另一部分
按 `capability.yaml -> explorer.base_tools` 声明动态加载各静态 skill 自带
的 `impl/tools_impl.py`（`browser-core` 已提供，`doc-core` 仍是占位）。这
意味着：命中已有 trusted/probation 成员并执行成功的路径完全可用；命中失败
或未命中、需要触发探索的路径，如果目标领域声明的底层原语都已经有真实实现
（如 `browser-core`），探索本身会真的跑（能否成功取决于目标站点/环境，比如
反爬拦截），失败时得到 `status: not_implemented` 并在 `note` 字段据实说明
——阶段十八之前 `note` 是一句不区分原因的固定文案，容易被误读成"没接线"，
阶段十八改为先检测该 skill 声明的工具是否真的都有实现，再据此生成准确的
提示（详见方案文档阶段十八）。仍未提供 `impl/tools_impl.py` 的领域（目前
只有 `doc-core`）会得到"确实未接入真实执行器"的提示，这才是真正意义上的
已知遗留，不是伪造成功。排查"为什么这次探索/执行失败了"，除了看
`capability_call` 返回的 `error`/`note` 字段，也可以打开上面提到的
`debug.capability_enabled` 调试日志，直接看到 registry 桥接、每个工具
dispatch 命中/未接线的完整链路，不需要靠肉眼读命令行输出猜。

#### 检索裁决/探索子agent 的 LLM 调用（阶段九）

`llm_resolver.py`（第二级检索裁决）与 `explorer_runtime.py`（探索子agent
运行时）此前各自用 `urllib` 直连 Anthropic Messages API，是引擎里仅有的
两处没有走框架统一 LLM 调用基础设施
（[`LLMHelper`](llm-helper-guide.md)，见 `llm/service.py`）的地方——固定
写死 `provider=anthropic`，不跟随 `/model` 切换，不复用 `LLMClientPool`
的多 key/多配置 fallback 与统一 `RetryPolicy`。阶段九改为：

- `build_llm_resolver(llm_helper=None, *, cfg=None, override_model=None,
  override_provider=None, max_retries=2)`：内部改用 `helper.ask(...)`；
- `build_llm_explorer(tool_executor, llm_helper=None, *, cfg=None,
  override_model=None, override_provider=None, max_retries=2)`：手写多轮
  决策循环版本，内部改用 `helper.chat(messages=, system=, tools=, ...)`。
  **阶段二十起为遗留实现**，不再是默认接线路径，保留仅供已有调用方/测试
  继续工作。

`build_llm_resolver` 目前仍是检索裁决的默认实现；探索这一环节的默认实现
在阶段二十切换为 `build_subagent_explorer(base_cfg, *, tool_executor=None,
session_id=None, session_dir=None, shared_tool_cache=None,
override_model=None, override_provider=None)`——不再手写决策循环，而是
构造一个真实 `orchestrator.sub_agent.SubAgent`（独立 context/session，
装配系统已注册通用工具的一个子集——见下方"探索子agent实际拿到的工具集"，
加上按 `capability.yaml` 声明桥接进来的领域底层原语如 `browser_navigate`），跑
一次真实 `agent.run_turn()`。这样阶段九"接入 `LLMHelper` 而非自拼 API"的
成果被自然继承（`SubAgent`→`Agent` 链路本来就走统一 LLM 调用基础设施），
且不再需要单独维护一套"provider 无关的消息历史格式"。

安全边界（此前是 `capability.yaml -> explorer.tool_allowlist` 自造的一份
平行白名单）改为交给系统统一的 `PermissionGuard`/`sandbox`；工具集范围本
应该也交给 `task.allowed_tools`/`task.allowed_tool_groups` 表达，但
`build_subagent_explorer()` 构造的 `Task` 目前并未设置这两个字段（如实
记录这处已知的文档与实现落差，非本次改动范围），实际生效的是阶段二十二
新增的 `_EXPLORER_EXCLUDED_TOOLS` 黑名单（见下方说明）；步数预算（此前是
手写 `max_steps`/`max_seconds` 计时循环）改为复用 `task.max_turns`。`finish`/`report_failure` 两个决策元工具
的语义约束未变，只是从手写循环里的特判分支改为动态注册到探索用
`Agent.registry` 上的真实工具；`finish` 新增可选 `script_source` 字段，
供探索子agent自行判断"这段解法是否可参数化复用"并一并提交（见
`distiller.py` 的 script_source 优先蒸馏路径）。完整问题分析、方案设计、
分阶段实施记录见独立文档
[generative_capability_explorer_rearch_plan.md](../next_doc/generative_capability_explorer_rearch_plan.md)，
本文档不重复展开。

**领域原语从哪来（`depends_skills` 自动派生，取代手写 `tool_allowlist.
json`）**：`capability.yaml -> explorer.depends_skills`（`base_tools` 的
新别名，语义更贴近"这个领域依赖哪些静态 skill 提供的底层原语"，两者兼容）
声明的每个静态 skill 名，会按约定路径 `<skills_root>/<name>/impl/
tools_impl.py::TOOL_IMPLEMENTATIONS` 自动加载出真正有实现的原语名单
（`explorer_runtime.py::_auto_derive_domain_tool_names()`，复用
`real_tools.py::load_skill_local_tool_implementations()` 既有的动态加载
机制）——这是"这个原语是否真的能跑"的唯一权威数据源，跟
`build_default_tool_executor()` 最终真正派发时用的是同一份字典，不会再
出现手写清单和真实实现脱节的情况。`explorer/tool_allowlist.json`（或
`capability.yaml -> explorer.tool_allowlist`）两种历史写法仍然兼容，但
语义从"唯一原语来源"降级为"在自动派生集合基础上做交集收窄"——只有当
某个领域想屏蔽掉依赖 skill 提供的某些原语时才需要继续写这个文件；若
自动派生结果为空（依赖 skill 尚无 `impl/tools_impl.py`，如目前的
`doc-core`/`text-core`），安静退回旧行为直接使用该文件声明的列表，不影响
存量 skill。解析优先级完整顺序（`explorer_runtime.py::
_resolve_domain_tool_names()`）：`capability.yaml -> explorer.
allowed_tools`（内联手写，最高优先级）> 自动派生 > `tool_allowlist.json`
收窄/兜底 > `capability.yaml -> explorer.base_tools` 名字兜底（占位领域
的最后一道保险）。

**蒸馏产物不要求全程只调用领域原语**：`distiller.py` 的
`script_source`/`llm_synthesized` 两条蒸馏路径（见下一段）产出的脚本，
`tool_runtime.get_tool_executor()` 只用于真正需要驱动浏览器等外部系统的
步骤；过滤空结果、判断验证码/登录墙关键词、正则清洗文本、重试这类纯逻辑
处理，直接写标准 Python 就好，不需要也不应该硬套进 `executor()` 调用——
脚本主体是完全自由的 Python，领域原语只是其中可选的一类调用。这一点
体现在 `_LLM_SYNTHESIZE_SYSTEM_PROMPT` 的硬性要求与探索子agent
system_extra 提示里，供 LLM/探索子agent写脚本时参考。

**三条蒸馏路径与各自定位**：探索成功后，`distiller.py::distill()` 依次
尝试三条路径，命中第一条能通过"沙箱自测 + intent_schema 校验"的就用它，
落盘产物 `meta.json` 的 `distill_source_kind` 字段记录实际命中的是哪条：

1. `script_source`（优先级最高）：探索子agent在调用 `finish` 时自己判断
   这次解法可以整理成一个不依赖具体探索过程的 `run(input) -> dict`
   脚本，直接把源码一并提交（`finish` 的 `script_source` 字段结构性
   必填，只能显式提交源码或哨兵值 `"SKIP"`）。这是探索子agent自己对
   "这段解法是否具备可复用形状"做出的判断，最贴近这次踩过的坑（验证码/
   选择器），优先级最高。
2. `llm_synthesized`（次优先级）：`script_source` 为空且调用方注入了
   `llm_helper` 时，蒸馏器把完整探索 trace 交给 LLM，读懂"探索实际做对
   了什么"（跳过探测性死胡同，只提炼真正走通的路径），总结成参数化脚本。
   同样是完全自由的 Python，不强制只调用领域原语。
3. `trace_replay`（最后兜底）：前两条都不可用时，把 trace 中每一步
   `(tool, input)` 参数化后固化为一份顺序重放序列——这条路径结构性地
   只能顺序调工具，没有 if/else、没有重试、无法对中间结果做二次加工，
   遇到探索过程中混入的探测性失败调用（如先试 `bash curl` 失败才转向
   `browser_*` 工具）会直接判定"重放步骤失败"，整个蒸馏产物被丢弃。
   明确将其定位为"实在没辙的应急网"而非"第二种正常产出方式"：落盘时
   会在 `registry.json` 里写入比另外两条路径更保守的
   `probation_success_threshold_override`（默认领域门槛的两倍，见
   `capability_engine.py::_apply_lifecycle()`），要求验证更多次成功才能
   转正为 `trusted`；`degrade_failure_threshold`（降级速度）不受影响。
   评估过给 trace-replay 加"内联处理原语"（在重放序列里插一段脚本处理
   中间结果）的方案，结论是这类结构增强会让 trace-replay 长成
   `llm_synthesized` 的简化版，边际价值被后者覆盖，因此未采纳，工程投入
   优先给前两条路径。

完整方案与分阶段实施记录见独立文档
[generative_capability_trace_replay_and_allowlist_plan.md](../next_doc/generative_capability_trace_replay_and_allowlist_plan.md)。

**三档 member 执行机制（script → skill → explore，本次订正补充）**：`execute()`
命中 member 但脚本执行失败后，`call()` 不会直接判定该 member 失败就进入全新
`explore()`，中间插入了一档更便宜的手段——`_try_skill()`：如果该 member 在
`playbook_repo`（`.claude/skills/<name>/playbooks/<member_id>/`）下有一份
`active` 的 `playbook.md`（人类可读的步骤说明，而非确定性代码），就用
`skill_runner`（`skill_tier.build_skill_runner()`，内部是
`hybrid_exec.PlaybookRunner`）跑一次参照该 playbook 执行的轻量 Agent，产出
同样必须经过与 `execute()` 完全相同的 `intent_schema` 校验才算成功。三档的
定位——`script`（确定性代码，最省成本但最脆）／`skill`（LLM 参照 playbook
执行，成本居中、能应对页面细节变化）／`explore`（全新自由探索，最贵但泛化
能力最强）——与项目里另一套独立的分级降级状态机 `hybrid_exec`
（`ExecutionTier.SCRIPT/SKILL/AGENT`，见
[hybrid-exec-guide.md](hybrid-exec-guide.md)）在语义上完全对应，`SKILL` 这一
tier 正是为了给两者合并铺路而新增的。

三档之间目前已打通**双向流转**，不只是单向降级：

- **降级方向**：`distill()` 的三条脚本蒸馏路径（`script_source`/
  `llm_synthesized`/`trace_replay`）全部失败，但探索本身成功且数据通过了
  `intent_schema` 校验时，不再直接判"本次探索无沉淀"，而是把 trace 整理成
  一份 `playbook.md` 落盘（`distill_source_kind: "playbook"`，
  `registry.json` 标记 `execution_tier: "skill_only"`），供下次命中同一个
  `member_id` 时 `_try_skill()` 自动使用——这就打通了"探索失败但不是一无
  所获，至少留下一份步骤说明"这条路径。
- **升级方向**：`_try_skill()` 每次成功后，如果开启了
  `enable_skill_upgrade=True`（**默认开启**，`generative_capability_
  three_tier_improvement_plan.md` 第5节修复前默认是关闭，见下方
  "SKILL 档接线缺口修复"说明）且该 playbook 累计成功次数达到
  `skill_upgrade_success_threshold`（默认 3），会尝试用 LLM 把 playbook 文本
  + 一次真实执行样例蒸馏成 `script.py`（`distiller.py::attempt_skill_upgrade()`，
  复用与 `distill()` 完全一致的沙箱自测/schema 校验/合理性检查/原子落盘），
  `distill_source_kind` 记为 `"skill_upgraded"`。升级失败不影响本次调用已经
  拿到的成功结果，也不影响 playbook 本身的成败统计——纯粹是"锦上添花"。

**当前实现边界（需注意，本次订正）**：上述 `_try_skill()`/playbook 落盘/
升级三条逻辑，都是**在 `CapabilityEngine` 现有的 `resolve→execute→explore→
distill` 链路里直接插入的**，而不是把 `capability_engine` 整体委托给
`HybridExecutor.run()` 执行——`registry.json` 里 script 那一档的
`probation/trusted/degraded/dead` 状态机与 `playbooks/<member_id>/` 下
playbook 的成败统计仍是两套完全独立的计数（互不影响，也互不感知对方的
状态），`degraded` 判定目前仍只看 script 那一档的连续失败次数，不考虑
"script 挂了但 skill 档还能用"这种情况。这是刻意收窄的范围（先验证"三档
顶用不用"，把"要不要整体迁移到 `HybridExecutor`"作为独立评估留给后续），
不是遗漏。完整设计动机、与 `hybrid_exec` 的对应关系表、分阶段实施记录见
独立文档
[generative_capability_raw_result_and_hybrid_merge_plan.md](../next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md)
第 3 节。

在上述边界之内，[generative_capability_three_tier_improvement_plan.md](../next_doc/generative_capability_three_tier_improvement_plan.md)
已经补上三处小改进（均为可选/默认不改变既有行为）：

- **`available_tiers` 信息性字段**（阶段一）：`registry.json` 每个 member
  条目新增 `available_tiers` 字段（如 `["script"]`/`["skill"]`/
  `["script", "skill"]`），记录该 member 当前实际具备哪些执行手段，供人工
  排查使用。纯只读计算 + 写入，**不参与**任何现有决策逻辑（`_apply_
  lifecycle`/`_try_skill`/`explore` 的触发条件均不读取这个字段）——不是
  上面提到的那个真正统一的状态机，只是让状态可见。
- **skill 升级尝试冷却期**（阶段二）：`_maybe_upgrade_skill_to_script`
  升级失败后会在 playbook 的 `meta.json` 里记录
  `last_upgrade_attempt_at`（`PlaybookRepository.record_upgrade_attempt()`），
  距上次失败尝试未超过 `capability.yaml -> lifecycle -> skill_upgrade_
  retry_cooldown_seconds`（默认 3600 秒，可覆盖）时跳过本次升级检查，
  避免升级持续失败时每次 `_try_skill` 成功都重新触发一次 LLM 调用。
  不影响 playbook 自身的 `success_count`/`fail_count` 统计。
- **`call()` 支持 `allow_tiers`**（阶段三）：`CapabilityEngine.call(request,
  allow_tiers=None)` 新增可选参数，值为 `{"script", "skill", "explore"}`
  的子集，让调用方能主动跳过明知会失败的档位（例如已知该 member 刚被标记
  `degraded`，跳过必然失败的 script 档、直接尝试 skill）。默认 `None`
  时行为与此前完全一致；被跳过的档位不产生任何执行尝试，不计入
  `registry.json`/playbook 的成败统计。这是"改进方向 #4"里提到的
  `hybrid_exec.TaskSpec.allow_tiers` 式精细控制在 `capability_engine`
  这一侧的最小实现，`resolve()` 的检索逻辑本身不受影响。

**SKILL 档接线缺口修复（`three_tier_improvement_plan.md` 第5节）**：以上
`_try_skill()`/playbook 落盘/升级三条逻辑此前虽然代码已实现，但
`tools/capability_call.py` 从未把 `playbook_repo`/`skill_runner` 注入进
`CapabilityEngine`（`skill_tier.build_skill_runner()` 的 `max_turns` 没
有默认值、必须显式传入，接线的人一直没法确定该传什么数字），导致 SKILL
档在真实对话场景里从未被触发过，只在单元测试桩环境里生效。现已修复：

- 新增全局配置块 `AppConfig.generative_capability`
  （`config/models.py::GenerativeCapabilityConfig`），可在
  `agent_config.json` 里配置：

  ```json
  {
    "generative_capability": {
      "skill_tier_max_turns": 40,
      "skill_upgrade_enabled": true,
      "skill_upgrade_success_threshold": 3,
      "skill_upgrade_retry_cooldown_seconds": 3600
    }
  }
  ```

  不配置时按上面这份默认值生效——`skill_tier_max_turns` 默认 **40**，
  `skill_upgrade_enabled` 默认 **True**（此前 `enable_skill_upgrade` 参数
  的既有默认值是 `False`，随本次修复一并调整为默认开启）。
- `capability_call.py` 构造 `CapabilityEngine` 前，先读取该 skill 自己
  `capability.yaml -> skill_tier`（可选，`max_turns`/`enable_upgrade`/
  `upgrade_success_threshold`，skill 级覆盖优先于上面的全局默认值）合并
  出最终生效值，`max_turns<=0` 视为该 skill 显式关闭 SKILL 档（保留退出
  通道，等价于修复前的表现）。
- 影响范围仅限"SKILL 档此前完全不可达"这一点，`_try_skill()`/
  `distill()` 内部逻辑、`registry.json`/playbook 各自独立计数的既有边界
  （见上面"当前实现边界"）均未改动。
- 真人对话场景下如何验证，见
  [test_cases/browser-site-scraper-three-tier-testing-guide.md](../test_cases/browser-site-scraper-three-tier-testing-guide.md)。

**探索子agent实际拿到的工具集（阶段二十二订正）**：并不是"系统全部已注册
通用工具"——`build_subagent_explorer()` 内部把 `agent.registry` 换成私有
副本时，会先按 `_EXPLORER_EXCLUDED_TOOLS` 黑名单排除掉一批工具，再桥接进
`capability.yaml` 声明的领域底层原语（如 `browser_navigate`）。这个黑名单
是通用机制（定义在 `explorer_runtime.py`，跟具体 skill 无关），覆盖四类：

- 元编排/自我调度类：`capability_call` 本身、`skill_list`/`skill_activate`
  等——**这条是关键**：早期实现里探索子agent的工具集包含 `capability_call`
  自己，一旦探索过程中再调用一次 `capability_call`，会递归构造出下一层
  探索子agent，即"嵌套探索"，理论上没有深度上限；同时探索子agent会自己
  重新走一遍 `skill_list → skill_activate` 的技能发现仪式，浪费探索预算
  （这些信息本该已经通过下面的 `system_extra` 直接告知）。
- 二次 agent 派生类：`spawn_agent`/`spawn_named_agent`/`run_ensemble_llm` 等。
- 面向用户交互类：`ask_user`/`ask_user_confirm`/`ask_user_choice`（探索
  子agent是 `auto_approve=True` 的后台任务，不应该卡在等用户交互上）。
- workflow 生成/执行/自我修改类：`generate_workflow`/`run_workflow`/
  `agent_patch` 等，跟"探索一条可复用操作路径"无关。

`bash`/`read_file`/`write_file`/`grep`/`glob`/`web_search` 等通用文件与
脚本类工具、`finish`/`report_failure`、领域桥接原语不受影响。

**控制台输出（阶段二十三）**：探索子agent自己的 `Agent` 实例，`cfg.verbose`
会被强制设为 `True`（`orchestrator/sub_agent.py::SubAgent._build_agent()`
默认构造 `verbose=False`，是给"顶层主agent是否开了 `--verbose`"用的通用
刷屏保护，跟"排查探索子agent这次到底传了什么参数"是两个不同的诉求，两者
分开处理），因此探索子agent在命令行里调用工具时会打印完整 `tool_input`，
不再只看得到结果看不到参数。这只影响探索子agent自己的控制台详略程度，
不影响顶层主agent或其它类型 SubAgent 任务。

**排查探索失败的专用调试日志（阶段二十一）**：`agent_config.json` 新增
`debug.capability_enabled`（默认 `false`，兼容环境变量
`CAPABILITY_DEBUG`），打开后 `capability_call`→`CapabilityEngine.call()`
→`explore()`→领域底层原语 dispatch 全链路会详细记录到
`~/.agent/logs/capability_debug.jsonl`（与全局 `error.jsonl` 同目录），
包括 resolve/execute 每一步的结果、探索子agent的 registry 是否被正确替换
为私有副本（`_tool_executor.registry` 是否同步）、每个领域工具的注册结果、
上面提到的黑名单实际排除了哪些工具名（`excluded_tools_stripped` 字段）、
探索步骤序列与终态。这是项目通用机制
（`mini_agent.skills.generative_capability.capability_debug`），各 skill
的 `impl/*.py` 实现代码也可以直接 `import capability_debug_log` 调用写
自己的调试信息，不需要自己判断开关状态或拼日志路径（`browser-core/
impl/browser_core_impl.py` 已有示范）。完整背景与实施记录见
`generative-capability-skill-plan.md` 阶段二十一/二十二/二十三。

两者都优先用传入的 `llm_helper`（`capability_call` 工具通过
`get_current_llm_helper()` 拿到当前 Agent 实例），否则退化为
`LLMHelper.from_config(cfg)`，与 `ensemble/judge.py::judge_llm(llm_helper=...)`
的既有约定一致（`build_subagent_explorer` 不直接消费 `llm_helper`，而是
通过其内部构造的 `SubAgent`/`Agent` 间接走同一套 LLM 调用链路，语义等价）。
`build_llm_resolver` 未传 `llm_helper` 时在调用时抛出 `RuntimeError`（与此前
"未配置 API key"抛异常的语义一致）；`build_subagent_explorer` 探索失败时
返回 `ExploreTrace(success=False, stop_reason=...)`（`"step_budget"` /
`"llm_error"` 等，探索循环里"失败是一等公民"的既有约定未变）。

#### 引擎代码在哪

`mini_agent.skills.generative_capability`（`src/mini_agent/skills/
generative_capability/`）是**平台内置代码**，跨所有 `generative-capability`
skill 复用，不因领域不同而重写——这与本文档前面所有小节讲的"skill 内容
就是 `SKILL.md` 本身"是不同的模型：`generative-capability` skill 目录下
（如 `.claude/skills/browser-site-scraper/`）只放**声明式配置和运行时
数据**（`SKILL.md`、`capability.yaml`、`explorer/`、`_index.json`、
`registry.json`、`members/`），调度骨架/状态机/检索逻辑的实现代码住在
主项目里，理由与验证过程见
[generative-capability-skill-plan.md](../next_doc/generative-capability-skill-plan.md)
阶段七实施记录。

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

> **内容边界声明（2026-07 新增，「问题2」修复）**：每个 `## Skill: <name>` 块的
> 正文前面，`build_context()` 现在还会插入一段边界声明，明确两件事：
> 1. 这段内容是"完整正文"还是（chunking 模式下）"与本轮问题最相关的节选章节"；
> 2. 明确禁止再用 `read_file`/`view`/`grep`/`bash` 重新读取该 skill 目录下的
>    文件，并给出正规的替代路径——内容不够时应在回复里说明还缺什么，或调用
>    `skill_resource_list`/`skill_resource_load` 获取已登记的子资源。
>
> 动机：开启 chunking 时模型只能看到部分章节，容易怀疑 context 内容不完整，
> 转而自己去读磁盘上的 `SKILL.md` 核对——但磁盘文件可能因章节筛选、历史压缩
> 等原因与当前展示版本不一致。`prompts/system/active_skills.md` 模板里也追加
> 了同样的声明，作为全局层面的强调（不止针对单个 skill）。

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
2. 调用 `_auto_unload_idle_skills()` 卸载长期未用的 active skill（见 6.5 节）。
3. 调用 `_build_skill_compact_block(exclude_names=...)` 生成 skill 重附块——
   本轮刚被步骤 2 卸载的 skill 会被排除在外，不会再混进这次重附内容。
4. 将历史替换为摘要消息。
5. 如果有重附块，将其作为新的 user 消息写入历史。
6. 保存 session。

### 6.5 自动卸载长期未用 skill（compact 时的垃圾回收，2026-07 新增）

**动机**：在这个功能之前，compact 只是"按 LRU/预算截断这次重附给多少内容"，
`SkillLoader._active` 本身不变——意味着即便某个 skill 已经很久没被真正用到，
它依然会在**之后每一轮** `build_context()` 里把全文塞进 system prompt，白白
占用上下文。

**实现**：`SkillLoader.auto_unload_idle(idle_seconds, protect=None)` 在
compact 时扫描所有 active skill，满足以下任一条件即调用 `deactivate()` 真正
卸载：

1. 自激活以来，`tracker` 从未记录过一次实际调用；
2. 有调用记录，但 `last_called` 距今超过 `idle_seconds`。

`Agent._auto_unload_idle_skills()` 是接入点，受两个配置项控制：

| 配置 | 默认值 | 含义 |
|------|--------|------|
| `skill_auto_unload_enabled` | `true` | 是否在 compact 时执行自动卸载 |
| `skill_auto_unload_idle_seconds` | `1800` | 判定「长期未用」的空闲时间阈值（秒） |

**当轮生效**：`_build_skill_compact_block()` 会把本次卸载掉的 skill 名字
通过 `exclude_names` 传给 `build_compact_context()`，让它们不参与本轮重附内容
的候选竞争（默认 `include_inactive=True` 本来会把"曾用过但已卸载"的 skill
重新拉回摘要——如果不排除，卸载效果要等下一次 compact 才能体现出来）。

**卸载后如何重新拿回**：卸载不等于`exclude()`那种彻底移除（见 3.4 节），
skill 条目仍在 `_all` 里；但为了避免"刚判断不需要就被同一批关键词立刻拉回来"
的抖动，卸载会屏蔽该 skill 的关键词自动激活（`auto_activate()` 会跳过它，
见 3.3 节），只能通过显式 `skill_activate` 工具或 `/skill on <name>` 重新
激活；显式激活成功后会自动解除这个屏蔽状态。

```python
# 卸载（工具调用/CLI/compact 自动卸载都走这一个方法）
loader.deactivate("docx")
loader.auto_activate("帮我处理这个 word 文档")   # → 不会命中 docx，被屏蔽
loader.activate("docx")                          # 显式激活 → 解除屏蔽

# 查看当前被屏蔽的名单
loader.auto_activate_blocked   # -> ['docx', ...]
```

如果需要关闭这个行为（例如希望 compact 只做内容截断、不动 `_active` 状态），
在配置里设置 `"skill_auto_unload_enabled": false` 即可。

---

## 8. 推荐维护准则

1. **Skill 内容应聚焦**：把可执行步骤、约束和示例放在 `SKILL.md` 中，避免泛泛而谈。
2. **Description 要短而清晰**：它会出现在 CLI、工具结果和 system prompt 目录里。
3. **Trigger words 不应过宽**：过宽会导致无关任务自动加载 skill，浪费上下文。
4. **激活不等于使用**：调试 LRU 或压缩问题时优先查看 `/skill stats`，确认是否有实际使用记录。
5. **长 skill 应分章节**：使用 `##` 划分章节可以让 chunking 模式更有效。
6. **阶段结束及时卸载**：模型和用户都可以主动卸载不再需要的 skill 以节省
   上下文；compact 时也会按 `skill_auto_unload_idle_seconds` 自动卸载长期未用
   的 skill（见 6.5 节），但不应完全依赖自动机制——空闲阈值内的"半死不活"
   skill 仍会占用预算，主动卸载依然是更省心的做法。
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
| `src/mini_agent/skills/__init__.py` | `Skill`、`SkillResource`、`SkillLoader`、发现、解析、激活/卸载、`auto_unload_idle()` 自动卸载 GC、`_auto_activate_blocked` 屏蔽集合、`find_inactive_candidates()`（2026-07 新增，问题0 候选匹配）、`get_catalog()`（2026-07 变更，问题1 未激活不返回路径）、上下文构建（含问题2 边界声明）、渐进式资源加载、压缩上下文入口 |
| `src/mini_agent/agent/compaction.py` | `compact_with_skills()`、`_auto_unload_idle_skills()`、`_build_skill_compact_block()`：压缩流程、自动卸载接入点、skill 重附 |
| `src/mini_agent/agent/reminders_correction.py` | `_inject_reminders_for_user_intent()`、`_inject_reminders_for_skill_candidates()`（2026-07 新增，问题0 修复：基于 `find_inactive_candidates()` 生成候选激活提醒） |
| `src/mini_agent/agent/turn_loop.py` | `run_turn()`：关键词自动激活接入点、`_inject_reminders_for_skill_candidates()` 调用点（2026-07 新增） |
| `src/mini_agent/skills/usage_detector.py` | 显式声明和指纹匹配的双轨使用检测 |
| `src/mini_agent/skills/tracker.py` | LRU 使用追踪与压缩重附预算实现（skill 级与资源级共用） |
| `src/mini_agent/tools/skill_manager.py` | `skill_list`、`skill_activate`、`skill_deactivate`、`compact_history`、`skill_stats`、`skill_resource_list`、`skill_resource_load`、`skill_resource_unload` 工具注册；`skill_list`/`skill_activate` description 已补充禁止绕过读文件的说明（2026-07，问题1） |
| `src/mini_agent/config/models.py` | `SkillConfig`：`candidate_reminder_enabled`（2026-07 新增，问题0 开关） |
| `src/mini_agent/prompts/system/active_skills.md` | active skill 注入到 system prompt 的模板；已补充内容边界声明段落（2026-07，问题2） |
| `src/mini_agent/prompts/system/agent_core.md` | Agent 核心行为准则；「先查 Skill 再探索」规则旁已补充禁止直接读 `SKILL.md` 的说明与正反例（2026-07，问题0/问题1） |
| `src/mini_agent/agent/core.py` | `Agent.__init__`：skill 工具注册、`set_active_skills_provider` 等装配逻辑（注：文档历史版本里写的 `src/mini_agent/agent.py` 已拆分为 `agent/` 包下的多个 mixin 文件，如 `core.py`/`turn_loop.py`/`compaction.py`/`reminders_correction.py` 等，此处一并更正） |
| `src/mini_agent/cli/repl.py` | `/skills` 与 `/skill ...` CLI 命令实现 |
| `src/mini_agent/tools/evolution.py` | `skill_propose` 工具：lesson → SKILL.md 提案（2026-06 新增，Stage 3.1） |
| `src/mini_agent/evolution/eval_runner.py` | `mini-agent eval` 调用 `SkillLoader.exclude()` 做严格对比（2026-06 新增，Stage 3.2） |
| `src/mini_agent/perception/hot_reload.py` | `HotReloader`：mtime 轮询热重载，`SkillLoader.rediscover()` 作为回调 |
| `.claude/skills/skill-generator/SKILL.md` | 生成新 skill 的规范与流程，含单文件/分层结构判断标准（2026-07 更新） |
| `.claude/skills/skill-generator/references/progressive-loading.md` | 渐进式加载机制详解（该 skill 自身的可加载子资源，也是分层结构的自举示例） |
| `.claude/skills/skill-generator/references/examples.md` | 单文件/分层/browse_paths 三种完整示例 |
| `src/mini_agent/skills/generative_capability/` | （阶段七新增）`generative-capability` skill 的通用调度引擎（`CapabilityEngine`/`distiller`/`explorer_runtime`/`health_patrol`/`llm_resolver`/`schema_validator`/`tool_runtime`），跨所有该类型 skill 复用，见第 3.8 节 |
| `src/mini_agent/tools/capability_call.py` | （阶段七新增）`capability_call` 工具注册，第 3.8 节详述 |
| `.claude/skills/browser-site-scraper/`、`.claude/skills/doc-template-generation/` | 两个已落地的 `generative-capability` skill 示例，仅含声明式配置与运行时数据 |
| `next_doc/generative-capability-skill-plan.md` | `generative-capability` skill 完整设计方案与各阶段实施记录，第 3.8 节只讲"static skill 系统这一侧需要知道什么"，引擎内部机制以此文档为准 |

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

> 最后更新：2026-08（generative_capability_three_tier_improvement_plan.md
> 阶段一/二/三：`registry.json` 新增只读 `available_tiers` 字段、skill
> 升级尝试加冷却期节流、`CapabilityEngine.call()` 新增可选 `allow_tiers`
> 参数，均为可选/默认不改变既有行为，详见该文档实施记录）；
> 此前更新：2026-08（补充 generative-capability 三档 member 执行机制
> script→skill→explore 的双向流转说明：`_try_skill()`/playbook 落盘兜底/
> 升级蒸馏为 script.py，及其与 `HybridExecutor` 尚未整体合并的边界，详见
> `next_doc/generative_capability_raw_result_and_hybrid_merge_plan.md` 第3节）；
> 此前更新：2026-08（generative-capability 三条蒸馏路径定位调整 +
> 领域原语来源自动化：新增 `explorer.depends_skills`（`base_tools` 新
> 别名），领域原语名单不再要求手写 `tool_allowlist.json`，改为自动从
> 依赖 skill 的 `impl/tools_impl.py::TOOL_IMPLEMENTATIONS` 派生
> （`_auto_derive_domain_tool_names()`），`tool_allowlist.json` 降级为
> 可选的交集收窄声明；`_LLM_SYNTHESIZE_SYSTEM_PROMPT`/探索子agent
> system_extra 提示明确纯逻辑处理可直接写标准 Python，不必套
> `tool_runtime`；`trace_replay` 蒸馏产物在 `registry.json` 写入更保守的
> `probation_success_threshold_override`（默认领域门槛两倍），明确其
> "最后兜底、更难转正"定位，评估后未采纳给它加"内联处理原语"的结构增强
> 方案；三条路径 `script_source > llm_synthesized > trace_replay` 既有
> 优先级不变，同步补充第 3.8 节相关小节，详见
> `next_doc/generative_capability_trace_replay_and_allowlist_plan.md`）；
> 此前更新：2026-08（阶段二十一/二十二/二十三：新增
> `debug.capability_enabled` 全链路调试日志开关，落盘到
> `~/.agent/logs/capability_debug.jsonl`，skill 的 `impl/*.py` 也可以直接
> 调用；修复"嵌套探索"——探索子agent此前通过 `registry.filtered()` 拿到
> 全部已注册工具（含 `capability_call` 自己），新增
> `_EXPLORER_EXCLUDED_TOOLS` 黑名单排除元编排/二次派生/用户交互/workflow
> 自我修改四类工具；探索子agent自己的 `cfg.verbose` 强制置为 `True`，
> 命令行能看到完整 `tool_input`；同步更正第 3.8 节"安全边界交给
> `task.allowed_tools`"这处与实现不符的历史描述、更新探索子agent实际工具集
> 的说明，详见 `next_doc/generative-capability-skill-plan.md` 对应阶段）；
> 此前更新：2026-08（`generative_capability_explorer_rearch_plan.md` 阶段
> 二十：探索器默认实现从 `build_llm_explorer`（手写决策循环，现为遗留实现）
> 切换为 `build_subagent_explorer`（构造真实 SubAgent 驱动，隔离性/安全
> 边界/预算复用系统既有 `SubAgent`/`PermissionGuard`/`task.max_turns` 基础
> 设施）；`finish` 新增可选 `script_source` 字段，`distiller.py` 新增探索者
> 自带脚本的优先蒸馏路径；同步更新第 9 节相关段落；
> 此前更新：2026-08（阶段九：`generative-capability` 引擎的第二级检索裁决
> `llm_resolver.py`、探索子agent决策循环 `explorer_runtime.py` 改接框架统一
> `LLMHelper`——不再自行拼 `urllib` 直连 Anthropic，跟随 `/model` 切换、复用
> `LLMClientPool` 多 key/fallback 与统一 `RetryPolicy`；`capability_call`
> 工具改用 `tools/orchestration.get_current_llm_helper()` 取当前
> `Agent.llm_helper`，详见 `next_doc/generative-capability-skill-plan.md`
> 阶段九实施记录，同步更新第 3.8 节）
> 此前更新：2026-08（新增第 3.8 节「`generative-capability` skill：按需调用的
> 领域能力包」——`Skill` 新增 `skill_type`/`category_summary` 字段与
> `is_generative_capability` 属性，`build_context()` 对此类 skill 只注入一行
> 摘要不整段注入正文，新增 `capability_call` 工具；引擎代码
> `mini_agent.skills.generative_capability` 迁入主项目正常子包，`generative-
> capability` skill 目录下只保留声明式配置与运行时数据，详见
> `next_doc/generative-capability-skill-plan.md` 阶段七实施记录；同步更新
> 第 2.3/3.2/9 节；
> 此前更新：2026-07（新增第 2.1 节「三类实际观察到的不稳定表现与对应修复」：
> (a) `SkillLoader.find_inactive_candidates()` + `_inject_reminders_for_skill_candidates()`
> 生成候选 skill 激活提醒（问题0：该激活而没激活）；(b) `get_catalog()` 不再对
> 未激活 skill 返回磁盘路径，`skill_list`/`skill_activate` 工具 description 补充
> 禁止绕过说明，`agent_core.md` 补充正反例（问题1：绕开 `skill_activate` 直接读
> `SKILL.md`）；(c) `build_context()` 与 `active_skills.md` 补充内容边界声明
> （问题2：已激活仍去读文件而非用 context 内容）；同步更新第 3.2/3.3/4.1 节与
> 第 9 节关键代码索引，并更正历史遗留的 `agent.py` 单文件描述为现在的 `agent/`
> 包拆分结构；此前更新：修正第 3 节「Skill 文件格式与发现」：明确 `rglob` 为任意深度
> 递归发现、补充 `skills_dirs` 优先级解析（项目级 `.claude/skills` 与全局级
> `~/.agent/skills` 互斥候选）、补全 `requires`/`conflicts_with`/`activation_conditions`
> 等 Stage 7 字段及其对 `activate()` 的实际影响、`triggers`/`trigger_words` 字段名
> fallback 关系、整合 `.claude/skills/skill-generator` 给出的推荐新 skill 格式规范；
> 此前更新：新增渐进式加载机制：`resources`/`browse_paths` 字段、
> `skill_resource_list/load/unload` 工具、资源级关键词自动加载、卸载后再加载的
> 行为约定；更早更新：热重载 `rediscover()`、`patch_file_simple` 工具、隐私保护、raw-output 模式）