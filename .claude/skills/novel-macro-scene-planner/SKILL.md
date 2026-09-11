---
name: novel-macro-scene-planner
description: 小说转视频 v2 流程的第二步。在 novel-entity-extractor 抽取全局角色/地点之后，按"剧情转折/时间地点跳变"把小说全文切分成若干个大场景（macro_scene，每个≤1000字/预估≤120秒），原文全文保留落盘到 macro_scenes.yaml，供 novel-scene-detail-planner 逐个大场景做详细规划。当用户已经跑完 novel-entity-extractor（或已有 global/characters.json、global/locations.json）后，紧接着进入本 skill。
triggers: 大场景切分, macro scene, 剧情切分, novel macro scene planner
---

# 小说大场景切分 (Novel Macro Scene Planner)

## 概述

本 skill 是"小说转视频 v2"六段流程的第二步：

```
novel-entity-extractor
  → novel-macro-scene-planner（本 skill）
  → novel-scene-detail-planner
  → novel-asset-generator
  → novel-scene-video-generator
  → novel-video-composer
```

方案文档：`next_doc/novel_video_generator_plan_v2.md`（第 3 节）。

本 skill 只做"粗切"——把全文按剧情节奏切成几个大段落（大场景），**不涉及
镜头级别的细节规划**（那是 `novel-scene-detail-planner` 的工作），也不
生成任何素材或音频。

**依赖**：无外部 API/模型依赖，纯 Agent 推理 + 文件读写。

**依赖脚本**：`scripts/check_macro_scenes.py`——校验字数/时长硬约束、
角色地点引用完整性、原文覆盖率，写完必须跑，不通过不能交付给下游 skill。

## ⚠️ 产物文件强制保存规范

1. 先生成内容，立刻写文件；
2. 一次性把全部大场景切分完再整体写入 `macro_scenes.yaml`（不同于
   `novel-entity-extractor` 的"边处理边写"，因为切分需要通盘考虑剧情
   节奏，切一半写一半容易导致大场景边界不一致）；
3. 写入失败必须报告错误并停止。

产物路径：

```
novel_output/小说名_20260911/
├── novel_project.json        # 已存在（novel-entity-extractor 产出）
├── global/                   # 已存在
└── macro_scenes.yaml         # 本 skill 产物
```

## 流程规范

### Step 1：通读全文，识别切分点

结合已有的 `global/characters.json`/`global/locations.json`，重新通读
全文，标记"剧情转折/时间跳变/地点切换"的位置作为候选切分点（比如：
场景从客栈转到山道、时间从当晚跳到次日清晨、叙事视角切换、一场冲突
从爆发到结束）。

### Step 2：按硬约束确定最终切分

每个候选大场景必须同时满足：
- **原文字数 ≤ 1000 字**；
- **预估口播时长 ≤ 120 秒**（按 4.5 字/秒估算，即字数 ≤ 540 字时长上限
  更紧，两个约束取更严格的那个：`min(1000字, 540字)` 实际上字数约束
  本身已经比时长约束更松，所以主要看字数约束，但仍需在校验脚本里两个
  都算一遍，因为语速估算和字数不是严格线性——比如大段对话朗读节奏和
  叙述节奏不同，脚本按统一语速估算即可，不追求精确）；

若某个候选大场景超限，在其内部继续寻找次一级的转折点（哪怕转折不如
主转折点明显，比如一段对话内部的语气转变、一个动作的起止），强制二次
细分，直到每个大场景都满足约束。

超短的大场景（比如不到 100 字）不强制合并，保留其独立性优先于凑数，
只要不产生数量爆炸（个位数到十几个大场景是合理范围，几十上百个说明
切分粒度出了问题，需要 Agent 自查是否过度细分）。

### Step 3：写入 `macro_scenes.yaml`

每个大场景记录：
```yaml
macro_scenes:
  - id: macro_01
    title: "青石客栈初遇"        # 简短概括，便于人工检索，不是口播文案
    source_span: "第1章 第1-3段"
    raw_text: "...(该大场景对应的原文全文，逐字保留，不做任何改写)"
    summary: "一两句话剧情梗概"
    estimated_duration_sec: 95
    char_count: 480
    uses_characters: [char_01, char_02]
    uses_locations: [loc_01]
    status: pending              # novel-scene-detail-planner 处理完回写 planned
```

- `raw_text` 必须是原文逐字复制（不改写、不概括），因为下游
  `novel-scene-detail-planner` 要从这里摘录对话原文，改写过的文本会
  导致对话校验失败；
- `char_count` = `len(raw_text)`（简单字符计数，不需要排除标点，脚本
  统一按这个口径算）；
- `estimated_duration_sec` = `char_count / 4.5`（四舍五入到整数）；
- `uses_characters`/`uses_locations` 引用的 id 必须已存在于
  `global/characters.json`/`global/locations.json`；如果这个大场景涉及
  的角色/地点在全局库里还没有，**先调用 `novel-entity-extractor` 的
  模式 B（单点补抽取）**，把这段 `raw_text` 交给它补充抽取，拿到新 id
  后再继续写本大场景的记录，不要在 `macro_scenes.yaml` 里引用不存在
  的 id。

### Step 4：校验

写完后跑：

```bash
python .claude/skills/novel-macro-scene-planner/scripts/check_macro_scenes.py \
  <output_dir> --novel-text-file <原文文件路径>
```

不通过（超限、引用不存在的 id、原文覆盖率异常）则回 Step 2/3 调整，
重新跑校验，直到通过才能交付给下游 `novel-scene-detail-planner`。

`--novel-text-file` 参数可选——不传时脚本跳过"原文覆盖率"检查，只做
字数/时长/引用完整性检查（适合原文没有单独文件、只在对话里粘贴的场景）；
传了则额外检查所有 `raw_text` 拼接后的总字数与原文总字数的比例是否
在合理范围（默认 ±15%，允许合理取舍但不能大段遗漏或重复）。

**Agent 需向用户展示的中间内容**：切分出的大场景清单（标题+梗概+字数+
预估时长一览表）、校验通过结果。

**产物**：`macro_scenes.yaml`（全部强制落盘，校验通过）。

## 已知限制

- 覆盖率检查是粗粒度的字数比例检查，不是逐字比对，无法发现"选取了错误
  的原文片段但总字数凑巧接近"这类问题，仍需 Agent 人工核对切分是否
  合理；
- 大场景数量没有硬性上限，超长篇小说切出几十个大场景是允许的，但下游
  每个大场景都要走完整的 detail-planner→asset-generator→
  scene-video-generator 链路，数量越多整体耗时越长，建议长篇处理时
  提前和用户确认预期的大场景数量级。

## 下一步

完成本 skill 后，进入 `novel-scene-detail-planner`，**每次处理
`macro_scenes.yaml` 里的一个大场景**（按 `status: pending` 逐个来），
不是一次性处理全部大场景。
