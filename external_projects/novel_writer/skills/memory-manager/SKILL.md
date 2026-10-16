---
name: memory-manager
description: 小说分层记忆管理技能。解决大模型上下文窗口有限的核心问题，通过三级存储（长期记忆/章节摘要/滑动窗口）让模型在写作任意章节时都能获得准确的历史信息。当需要存储章节摘要、检索历史事件、构建写作上下文包、压缩历史信息时必须使用。触发词：记忆管理、上下文压缩、章节摘要、历史检索、写作上下文构建、记忆检索。
---

# 分层记忆管理技能（Memory Manager）

## 职责

系统的信息枢纽：通过三级分层存储，在写任意章节时动态组装出最相关的历史上下文，而不是盲目塞入全文。根据 chapter_type 动态调整 token 预算分配。

---

## 三级存储架构

```
L1  滑动窗口（即时上下文）
    最近2-3章完整摘要 + 当前章节规划
    固定构成，每次必含

L2  章节摘要缓存（按需检索）
    每章压缩为结构化摘要，按关键词索引
    写 reveal 章时优先检索伏笔埋设原文

L3  长期知识库（按需拉取）
    character-manager / world-builder / plot-tracker / style-guardian 当前状态
    根据 chapter_type 决定拉取哪些部分、拉取多少
```

---

## 动态 Token 预算（按 chapter_type，总上限 20000）

| 组件 | opening | setup | conflict | climax | transition | reveal | closing |
|------|---------|-------|----------|--------|------------|--------|---------|
| L1 即时上下文 | 5000 | 5000 | 5000 | 5000 | 5000 | 5000 | 5000 |
| 角色当前状态 | 3500 | 3000 | 3000 | 4000 | 2500 | 3000 | 3000 |
| 情节任务清单 | 1500 | 1500 | 2000 | 2000 | 1000 | 2000 | 1500 |
| 相关历史章节 | 2000 | 3000 | 3000 | 4000 | 2000 | 4500 | 2500 |
| 世界设定参考 | 3000 | 2000 | 2500 | 3000 | 1000 | 1500 | 1000 |
| 风格提示     | 800  | 500  | 500  | 800  | 500  | 500  | 1000 |

分配逻辑：climax 角色/规则细节最重要；reveal 历史检索最重要（需找回伏笔埋设原文）；opening 世界设定最重要；closing 风格预算最高。

裁剪优先级：L1 > 角色状态 > 情节任务 > 相关历史 > 世界设定 > 风格提示

---

## 章节摘要格式（L2 单元）

consistency-checker 通过后生成，存储为 summaries/ch_{n}.json：

```json
{
  "chapter": 8,
  "chapter_type": "conflict",
  "word_count": 4800,
  "one_line_summary": "李凌云在考核中展示真实实力，晋升内门弟子，师傅流露认识其父的迹象。",
  "key_events": [
    "考核对战：李凌云以炼气七层碾压三名外门弟子",
    "晋升：正式成为内门弟子",
    "关键信号：王道长神情复杂，似乎想起某人"
  ],
  "character_changes": {
    "char_001": "外门→内门弟子；压抑→初现自信；弧线：首次对自身实力产生信心",
    "char_002": "关键动作：神情异样，暗示认识主角父亲"
  },
  "location_changes": {"char_001": "外门宿舍→青云峰内门住所"},
  "foreshadowing_updates": {
    "planted": [],
    "reinforced": ["fs_001：打斗中再次出现，读者感知度提升"],
    "resolved": []
  },
  "chapter_end_state": {
    "protagonist_location": "青云峰内门",
    "tension_level": 4,
    "hook_type": "悬念钩子",
    "hook_content": "王道长的异样神情，主角起疑"
  },
  "keywords": ["考核", "晋升", "内门", "王道长", "父亲线索", "fs_001"]
}
```

---

## 上下文包组装流程

```
输入：章节号 n，chapter_type，本章规划

步骤：
1. 读 chapter_type → 确定各组件 token 预算
2. L1：前2章摘要 + 本章规划
3. L2：分析本章 key_scenes 关键词，检索相关历史摘要
   reveal 章额外检索：所有 status=planted 伏笔的埋设章节摘要
4. L3：从各知识库模块拉取对应数据
5. 计算总量，超出则按优先级裁剪
6. 输出 context_package（临时文件）
```

---

## 历史事件检索

```
输入：自然语言查询
步骤：关键词匹配 summaries/*.json 的 keywords 字段
      返回最相关1-3章的 key_events + chapter_end_state
```

---

## 全局快照（每10章）

```json
{
  "snapshot_at_chapter": 10,
  "story_so_far": "（≤500字总结）",
  "all_characters_current_state": {},
  "all_active_threads": {},
  "all_unresolved_foreshadowing": {},
  "arc_progress_summary": "主角仍处于扭曲信念期"
}
```

---

## 记忆衰减

```
前2章：完整摘要（L1）
3-10章前：key_events + character_changes + chapter_end_state
10章以前：仅 one_line_summary + keywords（按需展开）
```

---

## 文件管理

所有文件保存到工作目录（{WORKSPACE}）：

```
{WORKSPACE}/项目状态/memory/
├── ch_{n}.json              每章结构化摘要
├── snapshots/               每10章全局快照
│   └── snapshot_ch{n}.json
├── context_packages/        临时文件，写完即删
└── search_index.json        关键词→章节号索引
```

---

## 摘要质量校验（新增）

### 问题背景

一个错误的摘要（遗漏关键事件、错误记录角色状态）会在后续所有章节的上下文包中持续传递错误信息。必须在摘要存储前进行质量检查。

### 校验流程

在章节摘要生成后、存储前执行：

```
输入：生成的摘要 + 原始章节文本（或写作备注）

检查项：
  □ one_line_summary 是否包含本章最重要的事件？
  □ key_events 是否覆盖了所有写作备注中标注的"完成情节任务"？
  □ character_changes 中是否包含所有出场角色的状态变化？
  □ foreshadowing_updates 是否与写作备注中的"埋设/兑现伏笔ID"一致？
  □ chapter_end_state 的 protagonist_location 是否与章节最后的场景一致？
  □ keywords 是否包含了本章所有新出现的地点、人物、重要道具？
```

### 校验报告

```
摘要质量校验：第n章

✅ 通过项（5/6）：
  - one_line_summary 覆盖主要事件 ✓
  - key_events 包含全部3个情节任务 ✓
  - foreshadowing_updates 与写作备注一致 ✓
  - chapter_end_state 位置正确 ✓
  - keywords 完整 ✓

⚠️ 发现问题（1项）：
  - character_changes 遗漏了 char_003（对手在本章有重要的情绪变化，
    但摘要中未记录）
  
  自动修复：补充 char_003 的变化记录
  "char_003: 在考核失败后表现出异常的愤怒，不同于以往的轻蔑"

修复后：摘要质量通过，正在存储...
```

### 摘要索引一致性检查

存储摘要后，更新 search_index.json 并验证：

```
□ 新章节的所有 keywords 已加入索引
□ 本章中首次出现的角色/地点已建立索引条目
□ 被更新状态的伏笔（reinforced/resolved）已在索引中标记
```
