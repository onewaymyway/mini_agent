---
name: workspace-manager
description: 工作目录管理技能。负责初始化小说项目的工作目录结构，管理所有文件的标准化路径，确保各 skill 产生的文件有固定的落地位置。在开始任何创作工作之前必须首先调用。触发词：设置工作目录、初始化项目、项目目录在哪、工作目录、新建项目、项目路径。
---

# 工作目录管理技能（Workspace Manager）

## 职责

所有 skill 产生的文件必须保存到统一的工作目录。本技能负责：
1. 在项目启动时从用户获取工作目录路径
2. 初始化标准目录结构
3. 为所有 skill 提供统一的路径查询接口
4. 维护工作目录配置文件

**规则：任何 skill 写文件之前，必须通过本技能查询路径，不得硬编码路径。**

---

## 第一步：获取工作目录

在创作流程开始时（novel-director 阶段零），向用户询问：

```
请输入本项目的工作目录路径：
示例：/Users/张三/小说/《青云传》
      D:\novels\我的第一部小说
      ~/writing/xianxia_novel

工作目录将作为本项目所有文件的根目录。
如果目录不存在，系统会自动创建。
```

用户输入后，将路径保存为全局变量 `WORKSPACE`，后续所有 skill 均使用此变量构建文件路径。

---

## 标准目录结构

工作目录初始化后，创建以下完整结构：

```
{WORKSPACE}/
│
├── 大纲规划/
│   ├── 蓝图（_index.md、_meta.md、第NNN章.md）              novel-planner 输出的主蓝图
│   ├── 章节大纲.md                人类可读的章节大纲概览
│   ├── 人物设定/
│   │   ├── 角色索引.md        character-manager 主档案
│   │   └── 人设卡/
│   │       ├── 主角_[姓名].md     character-designer 输出的人设卡
│   │       └── 配角_[姓名].md
│   ├── 世界观设定/
│   │   ├── world_bible.json       world-builder 主档案
│   │   ├── world_changelog.md     设定变更日志
│   │   └── 地点详情/
│   │       └── [地点名].md        各地点的详细描述
│   ├── 线索设定/
│   │   ├── 伏笔/（F*.md 与 _index.md）      plot-tracker 主档案
│   │   ├── 伏笔注册表.md          人类可读的伏笔列表
│   │   └── 故事线状态.md          各故事线当前进展
│   └── 其他设定/
│       ├── style_profile.json     style-guardian 风格指纹
│       └── word_count.json        word-count-controller 字数档案
│
├── 正文/
│   ├── 第01章 [章名].md           每章正文，文件名格式固定
│   ├── 第02章 [章名].md
│   └── ...
│
├── 一致性检查/
│   ├── 第01章_检查报告.md         每章完章核查报告
│   ├── 第02章_检查报告.md
│   └── 幕次健康报告/
│       ├── 第一幕_健康报告.md     每幕结束后的深度审稿
│       └── ...
│
├── 修订记录/
│   ├── 第01章_修订记录.md         每章的修改历史
│   ├── 第02章_修订记录.md
│   └── 蓝图变更日志.md            大纲蓝图的版本变更记录
│
├── 章节总结/
│   ├── 第01章_总结.md             每章内容总结（简短，供快速回顾）
│   ├── 第02章_总结.md
│   └── 全书章节索引.md            所有章节的一句话总结索引
│
└── 项目状态/
    ├── progress.json              整体进度快照
    ├── 当前状态概览.md             人类可读的项目当前状态
    └── memory/                    memory-manager 技术摘要（机器读取）
        ├── ch_001.json
        ├── ch_002.json
        ├── snapshots/
        └── search_index.json
```

---

## 路径查询接口

所有 skill 通过以下标准函数查询文件路径，不得硬编码：

```python
# 大纲规划目录
PATHS = {
    # 核心蓝图
    "blueprint":           "{WORKSPACE}/大纲规划/蓝图/_index.md",
    "blueprint_readable":  "{WORKSPACE}/大纲规划/章节大纲.md",

    # 人物设定
    "characters":          "{WORKSPACE}/大纲规划/人物设定/角色/（每角色一个 {id}.md）",
    "character_card":      "{WORKSPACE}/大纲规划/人物设定/人设卡/{name}.md",
    "character_snapshot":  "{WORKSPACE}/大纲规划/人物设定/snapshots/ch_{n}.json",

    # 世界观设定
    "world_bible":         "{WORKSPACE}/大纲规划/世界观设定/world_bible.json",
    "world_changelog":     "{WORKSPACE}/大纲规划/世界观设定/world_changelog.md",
    "location_detail":     "{WORKSPACE}/大纲规划/世界观设定/地点详情/{name}.md",

    # 线索设定
    "plot_tracker":        "{WORKSPACE}/大纲规划/线索设定/伏笔/_index.md",
    "foreshadowing_list":  "{WORKSPACE}/大纲规划/线索设定/伏笔注册表.md",
    "thread_status":       "{WORKSPACE}/大纲规划/线索设定/故事线状态.md",

    # 其他设定
    "style_profile":       "{WORKSPACE}/大纲规划/其他设定/style_profile.json",
    "word_count":          "{WORKSPACE}/大纲规划/其他设定/word_count.json",

    # 正文
    "chapter_text":        "{WORKSPACE}/正文/第{nn}章 {title}.md",

    # 一致性检查
    "review_report":       "{WORKSPACE}/一致性检查/第{nn}章_检查报告.md",
    "act_health_report":   "{WORKSPACE}/一致性检查/幕次健康报告/第{act}幕_健康报告.md",

    # 修订记录
    "revision_log":        "{WORKSPACE}/修订记录/第{nn}章_修订记录.md",
    "blueprint_changelog": "{WORKSPACE}/修订记录/蓝图变更日志.md",

    # 章节总结
    "chapter_summary":     "{WORKSPACE}/章节总结/第{nn}章_总结.md",
    "chapter_index":       "{WORKSPACE}/章节总结/全书章节索引.md",

    # 项目状态
    "progress":            "{WORKSPACE}/项目状态/progress.json",
    "status_overview":     "{WORKSPACE}/项目状态/当前状态概览.md",
    "memory_summary":      "{WORKSPACE}/项目状态/memory/ch_{n}.json",
    "memory_index":        "{WORKSPACE}/项目状态/memory/search_index.json",
}
```

---

## 文件命名规范

### 章节正文文件名

```
格式：第{nn}章 {title}.md
规则：
  - {nn} 为两位数，不足两位补零（01, 02, ... 09, 10, 11...）
  - 章节超过99章时改为三位数（001, 002...）
  - {title} 为章节标题，来自 novel-planner 的 chapters[n].title
  - 不含特殊字符（?, *, :, /, \ 等）

示例：
  第01章 外门弟子.md
  第08章 初露锋芒.md
  第15章 身世之谜.md
```

### 其他文件命名规范

```
检查报告：第{nn}章_检查报告.md     （下划线连接，无空格）
修订记录：第{nn}章_修订记录.md
章节总结：第{nn}章_总结.md
人设卡：  主角_[姓名].md / 配角_[姓名].md / 反派_[姓名].md
```

---

## 初始化流程

```
步骤一：接收用户输入的工作目录路径
步骤二：验证路径合法性（不含非法字符，有写入权限）
步骤三：如果目录不存在，创建目录
步骤四：创建所有子目录（见标准目录结构）
步骤五：创建必要的初始文件：
  - 章节总结/全书章节索引.md（空表头）
  - 项目状态/当前状态概览.md（初始内容：项目刚创建）
  - 修订记录/蓝图变更日志.md（空文件）
步骤六：保存工作目录路径到配置
  - 写入 {WORKSPACE}/项目状态/workspace_config.json
  - 内容：{"workspace": "{用户输入的路径}", "created": "{时间}"}
步骤七：输出初始化确认
```

### 初始化确认输出

```
✅ 工作目录初始化完成

工作目录：/Users/张三/小说/《青云传》

已创建目录：
  ├── 大纲规划/（含人物设定/、世界观设定/、线索设定/、其他设定/）
  ├── 正文/
  ├── 一致性检查/（含幕次健康报告/）
  ├── 修订记录/
  ├── 章节总结/
  └── 项目状态/（含memory/）

→ 工作目录已就绪，可以开始创作规划
→ 所有生成的文件将保存到此目录
```

---

## 项目恢复（接续写作）

如果用户是在已有工作目录中继续写作（不是新建项目），本技能负责：

```
步骤一：读取 {WORKSPACE}/项目状态/workspace_config.json 确认目录有效
步骤二：扫描已有文件，确定当前进度
  - 计算正文/目录下已有几章
  - 读取 项目状态/progress.json 获取整体进度
  - 读取 大纲规划/蓝图/_index.md 获取总章节数
步骤三：输出当前项目状态摘要
步骤四：确认各关键文件存在（如缺失则报警）
```

### 项目状态摘要

```
📖 项目恢复：《青云传》

工作目录：/Users/张三/小说/《青云传》
当前进度：第8章已完成（共40章计划，20%）
总字数：38,600 / 300,000字（12.9%）
当前所在：第一幕（已完成8/10章）

文件检查：
  ✅ 大纲规划/蓝图/_index.md        存在
  ✅ 大纲规划/人物设定/角色/（每角色一个 {id}.md）  存在
  ✅ 大纲规划/世界观设定/world_bible.json  存在
  ✅ 大纲规划/线索设定/伏笔/_index.md  存在
  ✅ 章节总结/ 目录（8个文件）     存在
  ⚠️ 大纲规划/其他设定/style_profile.json  不存在
     （风格指纹尚未建立，建议在第3章后建立）

→ 项目文件完整，可以继续写作第9章
```

---

## 工作目录配置文件

```json
// {WORKSPACE}/项目状态/workspace_config.json
{
  "workspace": "/Users/张三/小说/《青云传》",
  "novel_title": "青云传",
  "created": "2024-01-01",
  "last_accessed": "2024-01-15",
  "total_chapters_planned": 40,
  "chapters_completed": 8,
  "schema_version": "1.0"
}
```

---

## 与其他模块交接

- **调用时机**：novel-director 在任何其他步骤之前（阶段零）
- **输出给**：所有 skill（通过路径查询接口）
- **维护**：每次会话开始时确认工作目录有效
