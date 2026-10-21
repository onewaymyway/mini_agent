---
name: chapter-review
description: 完章核查技能。每章写完后必须执行的强制闭环：核查内容是否符合大纲规划、发现问题并驱动修改、生成章节概述并归档、更新项目全局状态。当章节写作完成、需要检查章节是否符合要求、需要生成章节摘要、需要更新项目状态时必须使用。触发词：章节完成、完章检查、章节核查、生成摘要、更新项目状态、章节归档。本技能由 chapter-writer 写作完成后自动触发，不需要用户手动调用。
---

# 完章核查技能（Chapter Review）

## 职责

每章写完后的**强制闭环**。不可跳过，不可简化。执行四个阶段：

```
阶段一  核查     检验章节内容是否符合大纲规划和质量标准
阶段二  修改     发现问题时驱动 chapter-writer 修改，循环至通过
阶段三  归档     生成章节概述，保存到项目目录
阶段四  同步     更新项目全局状态（剧情线索、角色、世界、进度）
```

**执行原则**：阶段一有硬错误时，必须完成阶段二修改后才能进入阶段三/四。阶段三/四不依赖修改结果，只要通过核查即可执行。

---

## 阶段一：完章核查

### 核查来源

核查基准从以下位置读取，优先级从高到低：

```
1. novel-planner 的 chapters[n] 规划（最高优先级）
   - chapter_type · word_budget · key_scenes
   - plot_points · foreshadowing · ends_with · hook_type
   - pov · tension_level · designer_note

2. plot-tracker 的章前情节清单
   - 本章待兑现伏笔列表
   - 本章应推进的故事线
   - 本章建议钩子类型

3. character-manager 的本章出场角色档案
   - behavior_guide.wont_do（硬性行为边界）
   - current_state（写作前的状态基准）

4. world-builder 的设定规则
   - 力量体系边界
   - 地理/旅行规则

5. style-guardian 的风格档案
   - 禁用词列表
   - 文风基准
```

---

### 核查维度与判定标准

#### A. 大纲符合性核查（与规划对照）

```
A1 章节功能完成度
   □ novel-planner 规定的 plot_points 是否全部在本章发生？
   □ 章节 function 字段描述的核心任务是否完成？
   判定：每条 plot_point 标记 [完成/未完成/替代完成]

A2 关键场景覆盖
   □ key_scenes 中的每个场景是否都有对应内容？
   □ 缺失的场景是被遗漏还是被合并处理了？
   判定：每个场景标记 [存在/合并/缺失]

A3 伏笔执行
   □ 本章规划埋设的伏笔是否在正文中出现？
   □ 本章规划兑现的伏笔是否真正兑现？
   □ 兑现方式是否与 plot-tracker 的 reveal_content 一致？
   判定：每条伏笔标记 [正常/未执行/偏差执行]

A4 章末钩子
   □ 章末是否有钩子？
   □ 钩子类型是否与规划的 hook_type 匹配（允许合理替换）？
   □ 钩子力度是否足够（能否让读者想继续翻下一页）？
   判定：[达标/类型偏差/力度不足/缺失]

A5 字数执行
   □ 实际字数是否在计划字数的 ±20% 以内？
   （超出 ±20% 需要额外说明理由）
   判定：[达标/偏差率X%]
```

#### B. 内容质量核查（与标准对照）

```
B1 人物行为一致性（对比 behavior_guide）
   □ 是否有行为违反了 wont_do 列表？
   □ 角色说话方式是否符合 speech_pattern？
   □ 压力下的反应是否符合 stress_response？
   判定：[一致/违反，标注具体位置]

B2 事实准确性（对比各知识库）
   □ 旅行时间/距离是否符合 world-builder 的 travel_rules？
   □ 角色使用的能力是否在当前境界范围内？
   □ 已死亡角色是否还出现？
   □ 角色不该知道的信息是否无意中表现出"知道"？
   判定：[无误/发现X处错误]

B3 叙事质量（基础标准）
   □ 是否有超过连续3段的"描写无事件"区域（信息密度陷阱）？
   □ 是否存在 style-guardian 禁用词（每出现一次记一次）？
   □ 对话中是否有角色语言失去辨识度（所有人说话方式趋同）？
   判定：[达标/发现X处问题]

B4 主题与弧线一致性
   □ 本章主角的行为/状态是否与当前 arc_stage 匹配？
   □ 本章情感基调是否与该幕的 emotional_arc 相符？
   判定：[一致/偏差（属于软提醒，不阻塞）]
```

---

### 核查报告格式

```
完章核查报告：第n章《[章名]》
核查时间：[时间]
总体评级：🟢 通过 | 🟡 有瑕疵（可通过后修改）| 🔴 有硬错误（必须修改后才能归档）

══════════════════════════════════
A. 大纲符合性
══════════════════════════════════

A1 情节任务完成度：3/3 ✅
  ✓ 内门考核完成
  ✓ 晋升身份确认
  ✓ 师傅异样表现出现

A2 关键场景：2存在 / 1合并 / 0缺失 ✅
  ✓ 考核对战场景（存在）
  ✓ 晋升仪式（合并进考核尾段，合理）
  ✓ 师傅独处场景（存在）

A3 伏笔执行：✅
  ✓ fs_001（伤疤）：正文第4段出现，读者感知度提升 ✓
  ✗ fs_003（信物）：本章计划轻推，但正文中未出现
    → 建议：在下章补入，或在本章末段对话中自然提及

A4 章末钩子：🟡 力度偏弱
  现状：最后一句"王道长转过身，走了进去"
  规划：悬念钩子
  问题：当前结尾平静，未制造悬念感
  修改建议：改为"王道长转过身，走了进去，没有解释那句'好孩子'
            究竟是对他说的，还是对另一个人。"

A5 字数：实际 4820字 / 计划 4500字（+7.1%）✅

══════════════════════════════════
B. 内容质量
══════════════════════════════════

B1 人物行为：🔴 发现1处硬错误
  第7段：李凌云在对手嘲讽后立刻爆发并出手
  wont_do 记录：不会轻易暴露情绪，会隐忍
  位置：第7段第3句
  修改建议：将"他猛地出手"改为"他深吸一口气，
            随即出手——不是因为愤怒，而是因为时机到了"

B2 事实准确性：✅ 无问题

B3 叙事质量：🟡 发现2处
  ① 第2段"突然感觉到"（禁用词）→ 改为具体感官描写
  ② 对手和王道长在第9段对话中说话方式趋近
     → 对手应更冲动直接，王道长应更迂回

B4 主题与弧线：✅（软提醒）
  本章展示主角用实力赢得认可，符合当前弧线阶段

══════════════════════════════════
总结
══════════════════════════════════
硬错误：1处（B1，必须修改后才能归档）
建议修改：3处（A3-伏笔提醒、A4-钩子强化、B3-禁用词+对话）
软提醒：0处

→ 下一步：将硬错误和建议修改发送给 chapter-writer，完成修改后重新核查B1
```

---

## 阶段二：修改驱动

### 修改优先级

```
P1 硬错误（必须修改，阻塞归档）
   → 立即发给 chapter-writer，明确指出位置和修改方案
   → 修改完成后重新核查该维度
   → 通过后继续阶段三

P2 建议修改（强烈建议，但不阻塞归档）
   → 一次性列出所有建议
   → 如果作者确认跳过某条，记录在核查报告中注明"作者已知晓，决定保留"
   → 不重新核查，直接进入阶段三

P3 软提醒（记录即可，不要求修改）
   → 写入核查报告存档
   → 在幕末深度审稿时统一复盘
```

### 修改循环

```
chapter-writer 完成修改
    ↓
chapter-review 重新核查修改涉及的维度（不全量重查）
    ↓
硬错误是否清零？
    ├─ 是 → 进入阶段三
    └─ 否 → 再次发给 chapter-writer 修改（最多3轮）
              第3轮仍未通过 → 通知作者人工介入
```

### 最大修改轮次说明

```
设置3轮上限的原因：
  如果3轮内无法修复，说明问题可能在规划层面（而非执行层面）
  需要人工判断：是修改章节，还是调整大纲，还是接受当前状态

超出3轮时的输出：
  "本章在以下维度经过3轮修改仍未完全通过：[X]
   建议人工介入，可能需要：
   A. 直接修改正文（接受当前质量）
   B. 重新规划本章（回到 novel-planner 调整大纲）
   C. 调整核查标准（如果规划本身需要修改）"
```

---

## 阶段三：章节概述归档

核查通过后，生成并保存章节概述到项目目录。

### 文件路径规范

```
project/
├── summaries/                    ← 章节概述目录（本阶段写入）
│   ├── ch_001_概述.md
│   ├── ch_002_概述.md
│   └── ...
├── state/                        ← 项目状态目录（阶段四写入）
│   ├── plot_threads.json         剧情线索当前状态
│   ├── characters_current.json   角色当前状态快照
│   ├── foreshadowing_status.json 伏笔状态总览
│   └── progress.json             整体进度记录
└── 蓝图/_index.md + 每章一个文件  大纲蓝图（novel-planner 管理）
```

### 章节总结格式（保存到 {WORKSPACE}/章节总结/第{nn}章_总结.md）

总结文件有两个用途：
1. **人类快速回顾**：作者在10秒内了解这章发生了什么
2. **chapter-writer 加载参考**：下一章开始写作前必须读取，防止剧情矛盾

总结应**简短精准**，重点突出关键信息，不超过500字。

```markdown
# 第n章 《章名》 总结

**类型**：conflict | **字数**：4820字 | **完成**：[日期]
**核查**：✅ 通过（修改1处硬错误）

---

## 一句话总结

李凌云在内门考核中碾压三名对手，晋升内门弟子，师傅王道长流露出认识其父亲的迹象。

---

## 关键事件（供下一章参考）

1. 考核对战：以炼气七层三招全胜三名外门弟子
2. 身份晋升：正式成为内门弟子，分配青云峰住所
3. 关键信号：王道长说"好孩子"时神情复杂，似乎想起另一个人

---

## 章末状态（下一章必读）

| 项目 | 状态 |
|------|------|
| 主角位置 | 青云峰内门区域 |
| 主角身份 | 内门弟子，轻伤已愈 |
| 主角情绪 | 略感希望，仍保持警惕 |
| 主角已知 | 体质特殊；师傅可能认识父亲（猜测）|
| 章末钩子 | 悬念钩子：王道长那句"好孩子"究竟是对谁说的 |
| 当前张力 | 5 |

---

## 伏笔动态

| 伏笔 | 操作 |
|------|------|
| fs_001 左手旧伤疤 | 加深（打斗中再次出现，读者感知度提升）|
| fs_003 父亲信物 | 延后（原计划本章轻推，已推至第9章）|
| fs_007（新增）王道长的"另一个人" | 埋设（本章首次出现）|

---

## 故事线进度

| 故事线 | 本章变化 |
|--------|---------|
| 身世之谜（主线）| 张力4→5；获得"师傅认识父亲"线索 |
| 门派权争（副线）| 本章未涉及，维持5 |
```

---

## 阶段四：项目状态同步

归档完成后，更新 `project/state/` 下的所有状态文件。这是让整个项目"活着"的关键步骤。

### 4.1 更新剧情线索状态（plot_threads.json）

```json
{
  "last_updated_chapter": 8,
  "last_updated_time": "[时间]",
  "threads": {
    "thread_001": {
      "name": "身世之谜主线",
      "status": "active",
      "current_progress": "第8章：发现师傅认识其父（猜测阶段）",
      "latest_event": "王道长神情异样，暗示认识李凌云父亲",
      "next_planned_development": "第12章：获得父亲留下的信物",
      "current_tension": 5,
      "linked_foreshadowing_pending": ["fs_001", "fs_002", "fs_007"]
    },
    "thread_002": {
      "name": "门派权争副线",
      "status": "active",
      "current_progress": "第6章：发现师兄对主角的针对",
      "latest_event": "本章未涉及",
      "current_tension": 5
    }
  }
}
```

### 4.2 更新角色当前状态快照（characters_current.json）

```json
{
  "snapshot_chapter": 8,
  "characters": {
    "char_001": {
      "name": "李凌云",
      "current_location": "青云峰内门区域",
      "current_status": "内门弟子",
      "current_emotional": "略感希望，仍保持警惕",
      "current_level": "炼气第七层",
      "known_secrets": ["体质特殊", "师傅曾认识其父（猜测）"],
      "active_goals": ["突破炼气期", "调查父亲失踪"],
      "arc_stage": "扭曲信念期：强大=价值，尚未触发成长转折",
      "relationship_changes_this_chapter": "与王道长关系：陌生→微妙"
    },
    "char_002": {
      "name": "王道长",
      "current_location": "青云峰",
      "latest_action": "第8章神情异样，暗示认识主角父亲",
      "mystery_level": "high"
    }
  }
}
```

### 4.3 更新伏笔状态总览（foreshadowing_status.json）

```json
{
  "snapshot_chapter": 8,
  "summary": {
    "total_registered": 8,
    "planted": 6,
    "partially_revealed": 0,
    "resolved": 1,
    "planned_not_yet_planted": 1
  },
  "active_foreshadowing": [
    {
      "id": "fs_001",
      "content": "左手旧伤疤",
      "status": "planted",
      "reader_awareness": "medium（本章提升）",
      "planned_reveal_progress": "72%",
      "overdue": false
    },
    {
      "id": "fs_007",
      "content": "王道长说的'另一个人'是谁",
      "status": "planted",
      "planted_chapter": 8,
      "reader_awareness": "high（本章新埋，读者明显感知）",
      "planned_reveal_progress": "40%"
    }
  ],
  "overdue_foreshadowing": [],
  "resolved_this_chapter": []
}
```

### 4.4 更新整体进度（progress.json）

```json
{
  "last_updated_chapter": 8,
  "total_chapters_planned": 40,
  "chapters_completed": 8,
  "completion_rate": 0.20,
  "current_act": 1,
  "act_progress": "第一幕进行中（8/10章完成）",
  "total_words_written": 38600,
  "total_words_target": 300000,
  "word_completion_rate": 0.129,
  "current_tension_level": 5,
  "next_chapter_plan": {
    "chapter": 9,
    "chapter_type": "setup",
    "word_budget": 4000,
    "key_focus": "消化第8章晋升，开始追查父亲线索",
    "pending_foreshadowing": ["fs_003需要本章轻推"]
  },
  "flags": {
    "act_1_deep_review_pending": false,
    "style_fingerprint_established": true,
    "major_blueprint_change_pending": false
  }
}
```

### 4.5 触发下游模块更新

状态同步完成后，触发以下模块的对应更新：

```
→ character-manager
  更新所有出场角色的 current_state 和 state_log
  触发：章后状态更新操作

→ plot-tracker
  更新已兑现/新增伏笔的 status 字段
  更新相关 plot_threads 的 current_progress
  更新 chapter_hooks 中本章实际钩子内容

→ world-builder
  更新本章涉及地点/规则的 use_count 和 last_used_chapter

→ word-count-controller
  更新 chapter_budgets 中本章的 actual / deviation / status

→ style-guardian
  更新 word_frequency（本章高频词统计）
  记录本章优秀范本（如有）

→ memory-manager（最后执行）
  以 project/summaries/ch_n_概述.md 内容为基础
  生成结构化摘要 summaries/ch_n.json
  更新 search_index.json
  （memory-manager 的摘要存储在 consistency-checker 通过后才执行）
```

---

## 完整执行顺序

```
chapter-writer 完成写作并输出正文 + 写作备注
    ↓
chapter-review 阶段一：完章核查
    ↓
    ├─ 有硬错误 → 阶段二：驱动 chapter-writer 修改
    │                ↓
    │            重新核查（最多3轮）
    │                ↓
    │            硬错误清零
    ↓
chapter-review 阶段三：生成章节概述
  → 写入 project/summaries/ch_n_概述.md
    ↓
chapter-review 阶段四：项目状态同步
  → 更新 project/state/ 下4个状态文件
  → 触发6个下游模块更新
  → memory-manager 最后生成 JSON 摘要
    ↓
完章流程结束，输出"第n章已完成并归档"确认
```

---

## 完章确认输出

每章完成全部四个阶段后，输出：

```
✅ 第n章《[章名]》完章归档完成

核查结果：通过（修改1处硬错误，采纳3条建议）
概述文件：project/summaries/ch_n_概述.md ✓
状态更新：
  · 剧情线索：thread_001 推进（张力4→5）
  · 角色状态：李凌云晋升内门弟子，新增已知信息
  · 伏笔动态：fs_001 加深，fs_007 新增注册
  · 整体进度：8/40章（20%）
  · 总字数：38,600 / 300,000字（12.9%）

→ 可以开始第9章写作
→ 第一幕还剩2章，幕末深度审稿将在第10章完成后触发
```

---

## 工作目录文件说明

所有文件保存到 {WORKSPACE}（用户指定的工作目录）。路径通过 workspace-manager 查询。

```
{WORKSPACE}/
├── 大纲规划/              ← 规划阶段写入，后续只读（除非变更）
├── 正文/                  ← chapter-writer 写入
├── 一致性检查/            ← chapter-review 阶段一写入
├── 修订记录/              ← chapter-review 阶段二写入
├── 章节总结/              ← chapter-review 阶段三写入（供下一章加载）
└── 项目状态/              ← chapter-review 阶段四写入
```

### 全书章节索引格式（{WORKSPACE}/章节总结/全书章节索引.md）

每章归档后同步更新此文件，是快速查看全书进度的入口：

```markdown
# 全书章节索引

| 章节 | 章名 | 类型 | 一句话总结 | 完成日期 |
|------|------|------|-----------|---------|
| 第01章 | 外门弟子 | opening | 李凌云初入青云宗，发现天赋被低估，获得神秘古籍 | 01/01 |
| 第02章 | 内外有别 | setup | 了解门派规则，与师兄矛盾初现，古籍显示第一个功法 | 01/02 |
| 第08章 | 初露锋芒 | conflict | 考核碾压三人晋升内门弟子，师傅流露认识父亲的迹象 | 01/08 |
```

文件编号统一两位数（不足补零），超过99章改三位数。

---

## 与其他模块交接

- **触发来源**：chapter-writer（写作完成后自动调用，不可跳过）
- **核查依据**：novel-planner · plot-tracker · character-manager · world-builder · style-guardian
- **驱动修改**：chapter-writer（阶段二）
- **归档目标**：project/summaries/ 目录
- **状态更新**：character-manager · plot-tracker · world-builder · word-count-controller · style-guardian · memory-manager（阶段四）
