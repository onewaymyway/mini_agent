---
name: plot-tracker
description: 小说情节线索追踪技能。专门负责管理伏笔、悬念、情节线索的埋设与兑现，追踪多条故事线的进展，控制情节节奏和冲突升级曲线。当需要注册新伏笔、查询待兑现伏笔、检查悬念密度、管理多线叙事、规划冲突升级时使用。触发词：伏笔管理、情节线索、悬念追踪、故事线、冲突升级、情节节奏、伏笔兑现、多线叙事。
---

# 情节追踪技能（Plot Tracker）

## 存储约定（第二、三阶段）

伏笔账本（`大纲规划/线索设定/伏笔/` 下的 `F*.md` / `fs_*.md` 与 `_index.md`）**完全由系统（Python）生成和维护，你不要创建、修改或删除该目录下的任何文件**——包括 `mkdir 伏笔`。格式有严格的文件头校验，手写的文件一律会被判为损坏。

- **注册伏笔（register_plot 步骤）**：只写 `大纲规划/线索设定/plot_register.json`（格式见下文「plot_register.json 格式」）以及可读文档；账本由后续的 `plot_import` 步骤自动生成。
- **章后同步**：只输出本章增量 `plot_delta`（planted 埋设 / advanced 推进 / resolved 兑现），每个动作引用已有伏笔 id 或新建 id，由 `plot_merge` 合并。

## plot_register.json 格式（注册伏笔时的唯一机器可读产出）

顶层是一个 JSON 对象，伏笔列表必须放在 **`foreshadowing` 数组**里（不是以 id 为键的对象，也不要用 `foreshadowing_registry`）。每条伏笔：

```json
{
  "foreshadowing": [
    {
      "id": "fs_001",
      "name": "义眼异常代码",
      "type": "主线伏笔",
      "status": "planted",
      "plant_chapter": 1,
      "resolve_chapter": 38,
      "progress": [],
      "content": "埋设了什么",
      "planted_method": "外貌描写中一笔带过",
      "reveal_content": "兑现时揭示什么",
      "depends_on": [],
      "enables": ["fs_002"],
      "urgency": "non-urgent"
    }
  ]
}
```

- `id`（只含字母、数字、下划线、连字符）、`name`、`status`（planted / advanced / resolved）、`plant_chapter`（整数）、`resolve_chapter`（整数或 null，计划兑现章）、`progress`（数组，可为空）为必需字段，其余字段可选。
- 章号必须是整数（不要写"第1章"）。
- 写完后运行 `check_step.py --step register_plot` 自检，直到输出 OK。

## 职责

做小说的"情节账本"——每条埋下的伏笔都必须有去处，每个承诺给读者的悬念都必须兑现。防止遗忘线索、情节断头、节奏失控。

**本次改进**：伏笔兑现节点改用相对进度（幕次/百分比）而非绝对章节号，避免章节调整后大量手动更新；新增伏笔依赖关系字段，管理必须按顺序兑现的伏笔链。

---

## 设计参考：完整数据结构（概念模型，非文件格式）

> 下面的 JSON 只是说明字段含义的概念模型（含故事线、张力曲线等）。**伏笔列表的实际文件格式以上面的 `plot_register.json` 为准**，不要把 `foreshadowing_registry` 这种以 id 为键的写法写进任何文件。

```json
{
  "foreshadowing_registry": {
    "fs_001": {
      "id": "fs_001",
      "type": "主线伏笔",
      "content": "李凌云左手的旧伤疤——来历不明",
      "planted_chapter": 1,
      "planted_act": 1,
      "planted_progress_pct": 0.03,
      "planted_method": "外貌描写中一笔带过",
      "planned_reveal_act": 3,
      "planned_reveal_progress_pct": 0.72,
      "reveal_content": "婴儿时被追杀留下，与身世有关",
      "depends_on": [],
      "enables": ["fs_002"],
      "status": "planted",
      "reader_awareness": "low",
      "urgency": "non-urgent",
      "reinforcement_notes": "需在50%进度前轻推一次，唤起读者记忆"
    },
    "fs_002": {
      "id": "fs_002",
      "content": "父亲失踪的真相",
      "planned_reveal_act": 4,
      "planned_reveal_progress_pct": 0.88,
      "depends_on": ["fs_001"],
      "enables": [],
      "status": "planned",
      "notes": "必须在fs_001兑现后才能兑现——fs_001的伤疤是身世线索的入口"
    }
  },

  "plot_threads": {
    "thread_001": {
      "id": "thread_001",
      "name": "身世之谜主线",
      "type": "main",
      "status": "active",
      "started_act": 1,
      "planned_resolution_act": 4,
      "planned_resolution_progress_pct": 0.88,
      "current_progress": "第8章：发现师傅认识其父",
      "next_development": "下一个推进节点约在25-30%进度处",
      "tension_level": 3,
      "max_tension": 10,
      "linked_foreshadowing": ["fs_001", "fs_002"],
      "linked_characters": ["char_001", "char_002"]
    }
  },

  "tension_curve": {
    "checkpoints": [
      {"progress_pct": 0.05,  "tension": 2, "note": "平静开篇"},
      {"progress_pct": 0.15,  "tension": 5, "note": "第一个冲突高峰"},
      {"progress_pct": 0.25,  "tension": 3, "note": "喘息期"},
      {"progress_pct": 0.50,  "tension": 7, "note": "中段危机"},
      {"progress_pct": 0.75,  "tension": 9, "note": "最大危机前夕"},
      {"progress_pct": 0.85,  "tension": 10,"note": "高潮决战"},
      {"progress_pct": 0.95,  "tension": 4, "note": "收尾"}
    ]
  },

  "chapter_hooks": {
    "ch_008": {
      "ending_hook": "师傅无意间说出父亲的名字",
      "hook_type": "信息悬念",
      "next_chapter_promise": "李凌云决定追查父亲下落"
    }
  }
}
```

### 相对进度的计算方式

```
progress_pct = 当前章节号 / 总章节数

示例：40章的小说，当前第10章
  current_progress_pct = 10/40 = 0.25（25%进度）

伏笔兑现触发条件：
  current_progress_pct >= planned_reveal_progress_pct
  → 触发提醒（而不是 current_chapter >= planned_reveal_chapter）

优势：
  章节数从40改为45章后，各伏笔的百分比节点自动适配
  不需要手动更新20+条伏笔的兑现章节号
```

---

## 伏笔依赖关系管理

### 依赖链结构

```
depends_on：本伏笔兑现前，哪些伏笔必须先兑现
enables：本伏笔兑现后，哪些伏笔才能兑现

示例依赖链：
  fs_001（伤疤）→ 兑现后 enables → fs_002（身世真相）
  fs_003（信物）→ 兑现后 enables → fs_004（父亲下落）
  fs_002 + fs_004 同时兑现 → 触发 thread_001（身世主线）的最终解决
```

### 依赖链检验

每次兑现伏笔前，自动检查：

```
⚠️ 依赖检查：fs_002
  depends_on: [fs_001]
  fs_001 当前状态: planted（尚未兑现）
  
  警告：fs_002 依赖 fs_001 先兑现
  fs_001 计划兑现进度：72%，当前进度：20%
  建议：按顺序先兑现 fs_001
```

---

## 核心操作

### 1. 注册伏笔

```
id: fs_XXX（自动生成）
类型: [主线伏笔/副线伏笔/人物伏笔/世界伏笔]
内容: 埋设了什么
埋设方式: [直接描写/侧面暗示/对话带过/道具出现]
埋设章节/幕次/进度百分比:
计划兑现幕次/进度百分比:
兑现内容:
depends_on: [前置伏笔ID列表，无则为空]
紧迫程度: [urgent/non-urgent]
```

### 2. 章前情节清单

```
📋 第[n]章情节清单（当前进度：X%）

⚡ 本章需兑现的伏笔：
  - [fs_003] 进度已达计划兑现点，本章安排揭示

🔔 本章可轻推的伏笔（读者已超过阈值未见）：
  - [fs_001] 距上次出现已过15%进度，建议用细节暗示

🔗 依赖检查通过：
  - fs_003 的 depends_on [fs_001] 已兑现 ✓

📌 本章应推进的故事线：
  - thread_001：按计划第4次推进，张力应从4→6

⚠️ 情节健康提醒：
  - 此地点"血月城"已过10%进度未再出现，建议在对话中自然带出
```

### 3. 章后情节更新

每章完成后记录：伏笔状态更新（planted/reinforced/partially_revealed/resolved）、故事线推进内容、实际张力水平、实际钩子类型。

### 4. 情节健康度报告

```
🏥 情节健康度报告（当前进度：X%）

伏笔状态：
  ✅ 已兑现：8/12（67%）
  ⚠️ 超期未兑现（已过计划兑现点）：
    - fs_005：计划70%兑现，当前已82%，建议立即安排
  ⚠️ 依赖阻塞：
    - fs_007 depends_on fs_006（fs_006尚未兑现），需先处理fs_006

故事线状态：
  ⚠️ thread_003 已过12%进度未更新（断头风险）

张力曲线：
  实际 vs 规划偏差：当前45%进度处实际张力8，规划为5
  建议：安排一个喘息场景

伏笔密度：当前活跃伏笔7条（正常范围5-8条）✓
```

---

## 情节节奏原则

**张力曲线**：不允许连续15%进度张力都在7以上；不允许连续10%进度张力都在3以下；每个幕次至少有一个张力峰值。

**伏笔密度**：同时活跃伏笔数5-8条；每25%进度至少兑现2条中等以上伏笔；主线伏笔埋设到兑现的进度间隔：25%-50%（太近仓促，太远读者忘了）。

**多线叙事**：主线每8%-10%进度推进一次；重要副线每12%推进一次；次要副线每20%-25%出现一次。同一章节最多并进2条线。

---

## 文件管理

所有文件保存到工作目录（{WORKSPACE}）：

- 伏笔注册结果（机器可读，唯一来源）：`{WORKSPACE}/大纲规划/线索设定/plot_register.json`
- 伏笔注册表（可读）：`{WORKSPACE}/大纲规划/线索设定/伏笔注册表.md`
- 故事线状态（可读）：`{WORKSPACE}/大纲规划/线索设定/故事线状态.md`
- 张力曲线：`{WORKSPACE}/大纲规划/线索设定/tension_curve.md`
- 决策日志：`{WORKSPACE}/大纲规划/线索设定/decision_log.json`（plot-simulator 使用）

**禁止写入** `{WORKSPACE}/大纲规划/线索设定/伏笔/` 目录（含 `_index.md`）：该目录是系统生成的账本，手写文件会被自动隔离并判定本步骤产出不合格。

---

## 与其他模块交接

- 接收：novel-planner（初始伏笔列表 + 章节规划）
- 输出：chapter-writer（章前情节清单）· consistency-checker（伏笔兑现校验）
- 反馈：novel-planner（重大情节调整时更新蓝图）
