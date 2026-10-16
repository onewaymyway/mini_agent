---
name: novel-director
description: 长篇小说生成总控技能。管理和协调所有小说创作相关skill的调用顺序、数据流转、异常处理和整体进度。当用户要开始创作一部小说、需要了解创作流程、需要知道当前应该调用哪个模块、或整个创作系统需要协调时必须使用。触发词：开始写小说、创作小说、小说系统、如何开始、下一步做什么、总控、创作流程、系统协调。
---

# 长篇小说生成总控技能（Novel Director）

## 职责

整个小说创作系统的调度中心。不直接生成内容，而是：
- 告诉用户当前处于哪个阶段、下一步该调用哪个 skill
- 维护系统状态，确保各模块数据同步
- 在模块间的数据断链或异常时介入协调
- 提供整体进度视图

---

## 系统全景：23个 Skill 的角色定位

```
┌─────────────────────────────────────────────────────────┐
│  基础设施层（一切的前提）                                │
│  workspace-manager  工作目录初始化，统一管理所有文件路径  │
├─────────────────────────────────────────────────────────┤
│  决策层（想清楚再写）                                    │
│  novel-planner    全书架构师，系统起点，输出蓝图          │
│  plot-simulator   沙盘推演师，唯一前瞻性模块（含决策日志）│
├─────────────────────────────────────────────────────────┤
│  知识库层（世界的记忆）                                   │
│  character-designer  人设创作，含关系对设计              │
│  character-manager   人物管理，追踪状态变化               │
│  world-builder       世界圣经，含设定使用率追踪           │
│  plot-tracker        情节账本，相对进度+伏笔依赖管理      │
├─────────────────────────────────────────────────────────┤
│  枢纽层（信息的调度中心）                                 │
│  memory-manager   组装上下文包，动态token预算+摘要校验    │
├─────────────────────────────────────────────────────────┤
│  设计层（在写作前把章节想清楚）                           │
│  hook-designer    全书钩子系统（章末钩子+章首衔接+前三章）│
│  scene-architect  场景内部结构设计，戏剧性问题+小弧线     │
│  theme-architect  主题设计与表达，防止说教               │
│  pacing-architect 跨章节节奏编排，三章单元+幕次规划      │
│  ending-designer  结局设计，故事线收束+最后一句话打磨     │
│  idea-catcher     灵感捕捉与整合，防止好想法被遗忘       │
├─────────────────────────────────────────────────────────┤
│  执行层（生产内容）                                       │
│  chapter-writer   唯一内容生产者，7种chapter_type模板    │
│  chapter-review   完章闭环：核查→修改→归档→状态同步      │
│  dialogue-crafter 对话专项，含冲突对话+群体对话模块      │
├─────────────────────────────────────────────────────────┤
│  质检层（守护质量）                                       │
│  consistency-checker  事实/逻辑/世界规则/主题校验        │
│  style-guardian       文风一致性，含幕间风格对比          │
│  revision-editor      修订编辑，含主动巡检（8维度）       │
│  reader-simulator     读者体验模拟，注意力曲线+可信度     │
│  word-count-controller 字数控制，配额分配+偏差处理       │
└─────────────────────────────────────────────────────────┘
```

---

## 完整创作工作流

### 阶段零：工作目录初始化（所有步骤的前提）

**任何创作工作开始前，必须先完成此阶段。**

```
步骤 0.1  workspace-manager
  任务：向用户询问工作目录路径
  提示："请输入本项目的工作目录路径（如：/Users/张三/小说/《青云传》）"
  操作：
    - 验证路径合法性
    - 创建标准目录结构（如目录不存在则自动创建）：
        {WORKSPACE}/大纲规划/（含人物设定/世界观设定/线索设定/其他设定/）
        {WORKSPACE}/正文/
        {WORKSPACE}/一致性检查/（含幕次健康报告/）
        {WORKSPACE}/修订记录/
        {WORKSPACE}/章节总结/
        {WORKSPACE}/项目状态/（含memory/）
    - 保存配置到 {WORKSPACE}/项目状态/workspace_config.json
  完成标志：工作目录就绪，输出初始化确认

接续已有项目的情况：
  如果用户提供的目录已存在 workspace_config.json
  → workspace-manager 读取现有进度，输出项目状态摘要
  → 跳过阶段一，直接从当前章节的单章循环（阶段三）继续
```

**路径规范（所有 skill 遵守）**：

| 内容类型 | 保存路径 |
|---------|---------|
| 小说蓝图 | `{WORKSPACE}/大纲规划/小说蓝图.json` |
| 章节大纲（可读）| `{WORKSPACE}/大纲规划/章节大纲.md` |
| 人物设定 | `{WORKSPACE}/大纲规划/人物设定/characters.json` |
| 人设卡 | `{WORKSPACE}/大纲规划/人物设定/人设卡/{角色}.md` |
| 世界观设定 | `{WORKSPACE}/大纲规划/世界观设定/world_bible.json` |
| 线索设定 | `{WORKSPACE}/大纲规划/线索设定/plot_tracker.json` |
| 伏笔注册表（可读）| `{WORKSPACE}/大纲规划/线索设定/伏笔注册表.md` |
| 风格指纹 | `{WORKSPACE}/大纲规划/其他设定/style_profile.json` |
| 字数档案 | `{WORKSPACE}/大纲规划/其他设定/word_count.json` |
| 正文（每章）| `{WORKSPACE}/正文/第{nn}章 {标题}.md` |
| 检查报告（每章）| `{WORKSPACE}/一致性检查/第{nn}章_检查报告.md` |
| 修订记录（每章）| `{WORKSPACE}/修订记录/第{nn}章_修订记录.md` |
| 章节总结（每章）| `{WORKSPACE}/章节总结/第{nn}章_总结.md` |
| 全书章节索引 | `{WORKSPACE}/章节总结/全书章节索引.md` |
| 整体进度 | `{WORKSPACE}/项目状态/progress.json` |
| 记忆摘要 | `{WORKSPACE}/项目状态/memory/ch_{n}.json` |

---

### 阶段一：全局规划（写作开始前）

```
步骤 1.1  novel-planner
  任务：收集创作意图，输出蓝图文件
  输出文件：
    {WORKSPACE}/大纲规划/小说蓝图.json       （主蓝图，JSON格式）
    {WORKSPACE}/大纲规划/章节大纲.md          （章节大纲，人类可读）
  完成标志：用户确认蓝图摘要

步骤 1.2  character-designer（并行，每个主要角色一次）
  触发条件：novel-planner 识别出主要角色（出现≥3章）
  任务：设计完整人设卡
  输出给：character-manager（建档）

步骤 1.3  character-manager
  任务：将 character-designer 的人设卡转为 JSON 档案
  输出文件：
    {WORKSPACE}/大纲规划/人物设定/characters.json
    {WORKSPACE}/大纲规划/人物设定/behavior_index.json

步骤 1.4  world-builder
  任务：初始化世界圣经
  输入：{WORKSPACE}/大纲规划/小说蓝图.json
  输出文件：
    {WORKSPACE}/大纲规划/世界观设定/world_bible.json
    {WORKSPACE}/大纲规划/世界观设定/world_changelog.md

步骤 1.5  plot-tracker
  任务：注册所有初始伏笔，建立张力曲线基准
  输入：{WORKSPACE}/大纲规划/小说蓝图.json
  输出文件：
    {WORKSPACE}/大纲规划/线索设定/plot_tracker.json
    {WORKSPACE}/大纲规划/线索设定/伏笔注册表.md（人类可读）
    {WORKSPACE}/大纲规划/线索设定/故事线状态.md（人类可读）

步骤 1.6  word-count-controller
  任务：接收全书字数配额
  输入：{WORKSPACE}/大纲规划/小说蓝图.json
  输出文件：{WORKSPACE}/大纲规划/其他设定/word_count.json

步骤 1.7  plot-simulator（建议，可选）
  任务：快进模拟验证整体可行性
  输入：幕级骨架 + protagonist_arc + foreshadowing_index
  关注：伏笔能否全部兑现 / 成长节点是否合理 / 叙事断裂点

步骤 1.8  style-guardian（初始化）
  任务：建立风格指纹
  方式A：从用户提供的参考文本提取
  方式B：等前3章完成后用户确认质量，手动触发

阶段一完成标志：novel_blueprint.json 存在 · characters.json 建立 ·
                world_bible.json 建立 · plot_tracker.json 建立 ·
                word_count.json 建立
```

---

### 阶段二：前三章（特殊处理阶段）

前三章是决定读者是否继续追书的关键，需要额外的钩子设计：

```
步骤 2.1  hook-designer
  任务：为前三章设计钩子系统
  输入：novel-planner（全书主题/结构）· character-designer（主角人设卡）
  输出：前三章的开篇建议 · 章末钩子设计 · 三章连贯性方案

步骤 2.2  执行前三章的单章循环（见阶段三）
  特别注意：前三章的 chapter_type 通常为 opening + setup + conflict
  hook-designer 的输出作为 chapter-writer 的额外写作指导

步骤 2.3  style-guardian 风格指纹建立（如采用模式B）
  触发条件：用户确认前三章质量满意
  任务：从已写三章提取风格指纹，建立 style_profile.json
```

---

### 阶段三：单章写作循环（第n章）

每章写作都遵循这个完整循环：

```
──── 写作前 ────────────────────────────────────────────

3.1  word-count-controller
     发出本章字数配额 · 场景分配建议 · 是否需要消化上章偏差

3.2  plot-tracker
     发出本章情节任务清单（待埋伏笔 · 待兑现伏笔 · 故事线推进要求）

3.3  plot-simulator（可选，建议在关键章节前触发）
     触发时机：幕次转折章 · 重大逆转章 · 高潮章 · 感觉"写不下去"时
     任务：推演当前情节分支，给出最优路径建议

3.4  memory-manager
     组装本章写作上下文包
     从 character-manager / world-builder / plot-tracker / style-guardian 拉取数据
     根据 chapter_type 动态分配 token 预算

3.5  style-guardian
     发出本章风格提示（当前幕次基调 · 避免高频词 · 推荐签名技法）

──── 写作中 ────────────────────────────────────────────

3.6  chapter-writer
     执行写作，输出正文 + 写作备注
     遇到重要对话场景时 → 调用 dialogue-crafter 协作

──── 写作后（强制完章闭环）────────────────────────────

3.7  chapter-review（强制，不可跳过）
     统一接管所有写作后工作，按四阶段执行：

     阶段一 完章核查
       · 核查大纲符合性（对照 novel-planner 的 chapters[n]）
         - 情节任务完成度、关键场景覆盖、伏笔执行、章末钩子、字数
       · 核查内容质量
         - 人物行为一致性（对照 character-manager 的 wont_do）
         - 事实准确性（对照 world-builder）
         - 叙事质量（信息密度、禁用词、对话辨识度）
         - 主题与弧线一致性
       · 🔴 硬错误 → 阻塞，进入阶段二修改循环
       · 🟡 建议修改 → 不阻塞，一次性列出后继续

     阶段二 修改驱动（有硬错误时执行）
       · 将修改任务发回 chapter-writer，最多3轮
       · 第3轮仍未通过 → 通知作者人工介入

     阶段三 章节概述归档
       · 生成 project/summaries/ch_n_概述.md
         内容：一句话总结、主要事件、人物状态变化、
               伏笔动态、故事线进度、章末状态
       · 更新 project/summaries/index.md 总索引

     阶段四 项目状态同步
       · 更新 project/state/plot_threads.json（剧情线索）
       · 更新 project/state/characters_current.json（角色快照）
       · 更新 project/state/foreshadowing_status.json（伏笔总览）
       · 更新 project/state/progress.json（整体进度）
       · 触发下游模块：character-manager · plot-tracker ·
                       world-builder · word-count-controller ·
                       style-guardian · memory-manager

3.8  reader-simulator（可选，建议每3-5章触发一次）
     由 chapter-review 完成后，可选触发
     评估本章注意力曲线 · 理解度 · 情感连接 · 可信度
     输出读者体验报告，反馈给 revision-editor

──── 幕末额外步骤 ─────────────────────────────────────

3.14 consistency-checker（幕末深度审稿）
     跨章节时间线 · 角色成长轨迹 · 情感弧线实现 · 伏笔健康 · 主题连贯 · 张力曲线
     输出"幕次健康报告"

3.15 style-guardian（幕间风格对比）
     有意调整 vs 有害漂移的区分报告
     从 novel-planner 读取下一幕 emotional_arc，更新 act_level_tones

3.16 plot-simulator（建议，下一幕开始前）
     中层推演（4-10章影响范围）
     评估下一幕规划的可行性，发现潜在断裂点

3.17 memory-manager（全局快照，每10章）
     生成 snapshot_ch{n}.json
```

---

### 阶段四：修订循环（随时可触发）

当已写内容需要修改时：

```
4.1  revision-editor
     诊断问题等级（L1/L2/L3/L4）
     输出分级修改方案

4.2  chapter-writer（执行修改）

4.3  根据修改等级触发对应模块更新：
     L1：style-guardian
     L2：style-guardian
     L3：character-manager · plot-tracker · memory-manager · consistency-checker
     L4：novel-planner（蓝图更新）· 所有模块

4.4  reader-simulator（可选）
     验证修改后的读者体验是否改善
```

---

## 模块间数据流图

```
novel-planner ──────────────────────────────────┐
    │ blueprint.json                             │
    ├──→ character-designer ──→ character-manager│
    ├──→ world-builder                           │
    ├──→ plot-tracker                            │
    ├──→ word-count-controller                   │
    └──→ plot-simulator（验证）                  │
                                                 │
hook-designer（前三章专用）                       │
    ↓                                            │
memory-manager ←─── 所有知识库模块               │
    │ context_package                            │
    ↓                                            │
chapter-writer ←─── dialogue-crafter（按需）     │
    │ 章节文本                                   │
    ├──→ word-count-controller（字数核算）        │
    ├──→ consistency-checker（校验）             │
    │       ↓ 通过后                             │
    ├──→ character-manager（状态更新）           │
    ├──→ plot-tracker（进度更新）                │
    ├──→ style-guardian（风格更新）              │
    └──→ memory-manager（存储摘要）              │
                                                 │
reader-simulator ←── 按需触发 ──────────────────┘
revision-editor  ←── 按需触发
```

---

## 各模块关键依赖关系

```
模块                  依赖（读取数据）              被依赖（输出数据给）
────────────────────────────────────────────────────────────────────
novel-planner        用户输入                      所有模块（蓝图）
character-designer   novel-planner                character-manager
character-manager    character-designer            chapter-writer · consistency-checker
world-builder        novel-planner                chapter-writer · consistency-checker
plot-tracker         novel-planner                chapter-writer · consistency-checker · memory-manager
memory-manager       所有知识库 + 章节摘要          chapter-writer
word-count-controller novel-planner · chapter-writer  chapter-writer（配额）
hook-designer        novel-planner · character-designer  chapter-writer（前三章）
chapter-writer       memory-manager · 所有知识库   consistency-checker · 所有知识库（更新）
dialogue-crafter     character-manager             chapter-writer
consistency-checker  所有知识库 · novel-planner    memory-manager（触发摘要）· plot-tracker（新伏笔）
style-guardian       chapter-writer · novel-planner  chapter-writer（风格提示）· memory-manager
revision-editor      所有知识库 · consistency-checker  chapter-writer · 受影响模块
reader-simulator     novel-planner · plot-tracker  revision-editor · chapter-writer
plot-simulator       所有知识库 · novel-planner    novel-planner（建议更新）· chapter-writer
```

---

## 系统状态追踪

维护 `system_state.json` 追踪当前进度：

```json
{
  "current_phase": "writing",
  "current_chapter": 9,
  "current_act": 1,
  "completed_chapters": [1,2,3,4,5,6,7,8],
  "pending_actions": [
    "chapter_9_consistency_check",
    "chapter_9_style_check"
  ],
  "blocked_by": null,
  "modules_initialized": {
    "novel-planner": true,
    "character-manager": true,
    "world-builder": true,
    "plot-tracker": true,
    "word-count-controller": true,
    "style-guardian": false,
    "memory-manager": true
  },
  "blueprint_version": "v1",
  "last_snapshot": "ch_000",
  "flags": {
    "style_fingerprint_established": false,
    "act_1_deep_review_done": false
  }
}
```

---

## 注意事项与常见陷阱

### 1. 启动顺序必须严格遵守

novel-planner 必须第一个运行，它的输出是所有其他模块的初始化数据源。跳过或乱序会导致各模块数据不一致。

### 2. consistency-checker 是强制关卡

🔴 硬错误出现时，严禁继续写下一章。原因：硬错误会被 memory-manager 存入摘要，污染后续所有章节的上下文。一个时间线矛盾如果不修复，它会在接下来的每一章中被当作事实重复使用。

### 3. memory-manager 摘要的存储时机

memory-manager 只在 consistency-checker **明确通过后**存储本章摘要。如果章节被修改后重新校验，必须更新摘要，不能使用旧摘要。

### 4. style-guardian 风格指纹的建立时机

不要在前3章质量未确认时自动建立风格指纹。错误的基准比没有基准更糟糕——它会把文风问题固化为"正确标准"。

### 5. word-count-controller 的偏差累积

单章偏差可以消化，但不要让偏差跨越幕次边界。每幕结束时必须将字数偏差归零，否则全书总字数会失控。

### 6. plot-simulator 不产生最终决策

plot-simulator 的推演结果是建议，不是命令。最终路径选择由作者决定，选定后需由 novel-planner 更新蓝图并通知相关模块。

### 7. character-designer 和 character-manager 的边界

character-designer 负责"创造"，character-manager 负责"追踪"。不要让 character-manager 去做人设设计的工作（如修改核心欲望/恐惧），这些变更应通过 character-designer 重新设计后再更新到档案。

### 8. 多线叙事的视角管理

当小说存在多视角时，style-guardian 需要为每个视角建立独立的语言指纹。chapter-writer 在切换视角前必须从 style-guardian 获取对应视角的风格提示。

### 9. 蓝图变更的传播

novel-planner 的蓝图一旦发生中等及以上变更，必须通知所有受影响模块。变更日志（blueprint_version_log）是唯一的真实来源，各模块数据必须与当前版本蓝图保持一致。

### 10. reader-simulator 与 plot-simulator 的分工

reader-simulator 关注"已写内容的读者体验"（细粒度，段落级）；plot-simulator 关注"未来情节的可行性"（粗粒度，章节级）。两者互补，不要用其中一个替代另一个。

---

## 快速决策指南

```
遇到问题时，找哪个 skill：

想不清楚故事怎么写         → novel-planner（重新规划）
                              plot-simulator（推演分支，含决策日志）
主角不知道怎么设计          → character-designer（含关系对设计）
某个角色行为感觉不对        → character-manager（查档案）
                              revision-editor（诊断修改）
世界设定有问题/冗余         → world-builder（含使用率追踪）
伏笔忘了/乱了/顺序错了     → plot-tracker（含依赖关系管理）
上下文记不住了/写串了       → memory-manager（检索）
开头不够吸引人             → hook-designer（全书钩子系统）
某章结尾平淡               → hook-designer（章末钩子设计）
章与章之间衔接断裂          → hook-designer（章首衔接设计）
某个场景写不出来/太平淡     → scene-architect（场景结构设计）
主题太说教/感受不到深度     → theme-architect
整体节奏不对/一直累或一直平 → pacing-architect（跨章节节奏）
不知道怎么写结局            → ending-designer
最后一句话写不好            → ending-designer（打磨流程）
有个好想法不知道放在哪里    → idea-catcher
这章感觉不对（说不清）      → reader-simulator（诊断）
                              revision-editor（主动巡检+修改）
对话太假/冲突对话没张力     → dialogue-crafter（含冲突/群体模块）
文风不一致                 → style-guardian
字数超出/不足              → word-count-controller
有前后矛盾                 → consistency-checker
                              revision-editor
章节写完后该做什么         → chapter-review（自动触发，完章闭环）
下一步该做什么             → novel-director（本技能）
```

---

## 阶段完成标志

```
阶段一完成：
  ✓ novel_blueprint.json 存在且自检通过
  ✓ characters.json 建立（主要角色完整档案）
  ✓ world_bible.json 建立（力量体系 + 主要地点 + 基础历史）
  ✓ plot_tracker.json 建立（所有初始伏笔已注册）
  ✓ word_count.json 建立（全书配额已分配）

阶段二完成：
  ✓ 前三章正文已写
  ✓ hook-designer 的三章钩子方案已实施
  ✓ 前三章均通过 consistency-checker
  ✓ style_profile.json 已建立

每章完成：
  ✓ 章节正文已写
  ✓ consistency-checker 通过（无硬错误）
  ✓ memory-manager 摘要已存储
  ✓ 所有知识库状态已更新

全书完成：
  ✓ 所有章节通过 consistency-checker
  ✓ 伏笔兑现率 ≥ 90%
  ✓ 全书实际字数在目标字数 ±5% 范围内
  ✓ 幕末深度审稿全部完成
```
