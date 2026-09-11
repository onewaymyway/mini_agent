---
name: novel-scene-planner
description: 小说转视频流程的第一步。输入一段小说文本（长篇节选或完整短篇），抽取全局角色和反复出现的地点，按目标视频时长把内容取舍/浓缩成分段口播旁白文案，写入 novel_output/ 项目目录供后续 skill（novel-asset-generator / novel-scene-video-generator / novel-video-composer）使用。当用户说"把这本小说做成视频"、"小说转视频"、"生成小说解说视频"时，从本 skill 开始。
triggers: 小说转视频, 小说做视频, 小说解说, novel to video, 场景拆分, 角色提取, narration_script
---

# 小说场景拆分与角色提取 (Novel Scene Planner)

## 概述

本 skill 是"小说转视频"四段流程（`novel-scene-planner` →
`novel-asset-generator` → `novel-scene-video-generator` →
`novel-video-composer`）的第一步，也是唯一需要 Agent 创造性理解小说内容
的步骤，不能用固定脚本完成。

方案文档：`next_doc/novel_video_generator_plan.md`（含完整架构、文件契约、
四个 skill 的分工说明，本 skill 只做其中第 1 节的工作）。

**产出是给下游 skill 消费的结构化文件**，不是聊天里的口头总结——下游
skill 只读磁盘文件，不依赖本次对话的上下文，所以本 skill 每一步做完必须
立即落盘。

**依赖**：无外部 API/模型依赖，纯 Agent 推理 + 文件写入。

**依赖脚本**（本 skill 目录下）：
- `scripts/check_narration_draft.py`：校验 `characters.json`/
  `locations.json`/`narration_script.yaml` 的引用完整性和篇幅预估，
  Step 5 写完必须跑，不通过不能交付给下游 skill。

## ⚠️ 产物文件强制保存规范（与 comic-4panel / mv-generator 一致）

1. 先生成内容，立刻写文件——不要等用户确认后再写，确认前就要写入；
2. 文件写入是步骤完成的标志——没写入文件 = 步骤未完成；
3. 每次重新生成都要覆盖文件，确保产物文件始终是最新状态；
4. 写入失败必须报告错误并停止，不能跳过产物保存。

产物目录结构（`output_dir` 默认为 `./novel_output/{小说名}_{timestamp}`）：

```
novel_output/小说名_20260911/
├── novel_project.json        # Step 0 产物：全局配置，最先写入
├── characters.json           # Step 2 产物（本 skill 只写 asset_path=null 的雏形）
├── locations.json            # Step 2 产物（同上）
└── narration_script.yaml     # Step 4 产物：分段旁白文案草稿
```

## 流程规范

### Step 0：篇幅范围 + 目标时长（最先做，只做一次）

向用户问清楚（一次性问，不拆多轮；用户不回答时按下方默认值继续，不阻塞）：
- 处理范围：整本书自动分卷循环跑（本版暂不支持，见下方「已知限制」）、
  还是一个章节/一段用户指定的文字？**当前版本只支持后者**——用户给的
  是长篇小说的某个章节/片段，还是一篇完整短篇，都按"一次处理一段文本"
  的方式跑；
- 目标视频时长（用户不给时默认 3 分钟）。

选定后立即写入 `<output_dir>/novel_project.json`（先 `mkdir -p <output_dir>`）：

```json
{
  "source_title": "小说名（或章节标题）",
  "scope": {"mode": "chapter", "value": "第3章"},
  "target_duration_sec": 180,
  "art_style": "",
  "tts": {"engine": "cosyvoice", "fallback": "edge-tts", "voice": null},
  "bgm_enabled": false,
  "orientation": "landscape",
  "aspect_ratio": "16:9"
}
```

- `art_style` 先留空字符串，Step 1 确定统一美术风格后回填；
- `tts.engine`/`tts.fallback` 固定为 `"cosyvoice"`/`"edge-tts"`（本版方案
  已定稿，不需要向用户询问）；`tts.voice` 留 `null`，由
  `novel-asset-generator` 按引擎默认音色处理；
- `bgm_enabled` 固定为 `false`（本版不做 BGM，见方案文档）；
- `orientation`/`aspect_ratio` 同 `mv-generator` 的横竖屏选择，可以在
  Step 0 一并问一次（用户不回答默认横屏 16:9），后续 `novel-asset-generator`/
  `novel-video-composer` 会读取这两个字段。

写完必须回显给用户确认一次（"已确认处理范围为 XXX，目标时长 3 分钟，
配置已保存到 `novel_project.json`"）。

### Step 1：确定全曲统一美术风格

同 `mv-generator` Step 3 的第 1 条：结合小说的题材/体裁（武侠、都市、
科幻、言情……）、情绪基调、时代/地域背景，提炼一套贯穿全片的视觉风格
描述（画风/媒介、色调基调、光线氛围），写成一段简短英文描述，**立即
回填 `novel_project.json` 的 `art_style` 字段**。后续 Step 2 的角色/地点
`description_en`、Step 4 的镜头描述都必须融入这段风格，保证全片画风统一。

### Step 2：抽取全局角色 + 反复出现的地点

通读全文（或用户给定的章节/片段），完成：

1. **角色抽取**：识别文中出现的人物，**同一角色的不同称呼/别名要合并
   成一个 id**（比如"林然""小林""林公子"是同一人），记录：
   - 外貌描述（`description_zh`/`description_en`，`description_en` 末尾
     融入 `art_style`）
   - 性格特征
   - 首次出场位置（章节/段落，方便后续人工核对）
   - 与其他已识别角色的关系（简要，如 `"char_02:挚友"`）

   只收录**会被可视化到画面里**的角色（有具体外貌/动作描写、会出现在
   至少一个场景里的），一笔带过、纯功能性提及的次要人物不需要单独建档。

2. **地点抽取**：识别反复出现（至少 2 次）的场景/地点，同样合并同名/
   同指代的表述，记录外貌描述。**只抽取"反复出现"的地点**——只出现一次
   的场景不需要单独定妆图，直接在 Step 4 写 `video_mode: text` 场景即可
   （由 `novel-asset-generator`/`novel-scene-video-generator` 处理，本
   skill 不需要关心）。

立即写入：

`<output_dir>/characters.json`：
```json
{
  "characters": [
    {"id": "char_01", "names": ["林然", "小林"],
     "description_zh": "...", "description_en": "...(含 art_style)",
     "first_appear": "第1章 第2段", "relations": ["char_02:挚友"],
     "asset_path": null, "face_reference_id": null}
  ]
}
```

`<output_dir>/locations.json`：
```json
{
  "locations": [
    {"id": "loc_01", "name": "青石客栈",
     "description_zh": "...", "description_en": "...(含 art_style)",
     "asset_path": null}
  ]
}
```

`asset_path`/`face_reference_id` 由 Step 2 统一先置 `null`，交给
`novel-asset-generator` 回填，本 skill 不生成任何图片。

### Step 3：取舍与浓缩

结合 `target_duration_sec`，估算能承载多少内容（经验值：一段口播旁白
中文语速约 4–5 字/秒，`target_duration_sec × 4.5` 约等于旁白文案的
总字数预算）：

1. 判断原文哪些情节是"主线/情绪高点"，必须可视化展开；哪些是铺垫/次要
   支线，可以一笔带过或直接跳过；
2. 不是逐句翻译原文，而是**改写成适合口播的旁白文案**——去掉大段心理
   描写/对话标签这类不适合直接朗读的文风，保留关键情节、对话内容（可
   转述或保留直接引语视场景而定）、情绪转折；
3. 若原文篇幅远超预算（如长篇小说单章数万字、目标只有 3 分钟），要
   诚实地做大幅度浓缩，并在后续向用户展示时说明"已略过 XXX 情节"，
   不要在没有预算空间的情况下强行塞入所有细节。

### Step 4：切分场景段落草稿

把 Step 3 的旁白文案切成若干段（每段对应一个可视化镜头），**不填精确
时长**（时长要等 `novel-asset-generator` 跑完 TTS 才能确定，见方案文档
第 2 节）。每段标注：
- 引用的角色/地点 id（`uses_characters`/`uses_locations`）
- 简要镜头描述（`visual_hint`：构图/镜头运动/情绪氛围的关键词，供下游
  写 `prompt_en` 时参考，不需要写成完整 prompt）
- 对应原文出处（`source_span`，方便人工核对取舍是否合理）

单段口播文案长度建议对应 4–12 秒的朗读时长（即约 18–55 字，参考
`gen_video_with_text` 单 clip 4–12 秒的硬限制，提前把段落切在合理范围，
减少下游因超限被迫二次拆分的情况；实际时长以 TTS 结果为准，这里只是
提前规划的经验值，不是强制校验项）。

立即写入 `<output_dir>/narration_script.yaml`：
```yaml
segments:
  - id: seg_01
    text: "旁白口播文案（改写后，不是原文逐句照读）"
    source_span: "第1章 第2-5段"
    uses_characters: [char_01]
    uses_locations: [loc_01]
    visual_hint: "夜晚客栈门前，林然驻足回望，冷色调，远景转中景"
```

### Step 5：校验

写完 `narration_script.yaml` 后，**必须先跑校验脚本，通过才能向用户展示
结果并交付给下游 skill**：

```bash
python .claude/skills/novel-scene-planner/scripts/check_narration_draft.py \
  <output_dir>
```

该脚本检查：
1. `characters.json`/`locations.json` 里的 id 无重复；
2. `narration_script.yaml` 每个 `uses_characters`/`uses_locations` 引用
   都能在对应文件里找到；
3. 全部 `segments[*].text` 总字数换算成预估朗读时长（按 4.5 字/秒），
   是否落在 `novel_project.json.target_duration_sec` 的 ±30% 区间内——
   超出太多需要回到 Step 3 重新取舍浓缩（篇幅太多）或补充内容（篇幅
   不够，较少见）。

脚本退出码非 0 时，**不允许把结果交付给下游 skill**，按报错逐条修改，
重新跑校验，直到通过为止。

**Agent 需向用户展示的中间内容**：
- 识别出的角色/地点清单（数量和简要身份）；
- 取舍策略概述（保留了哪些主线情节、跳过了哪些支线）；
- 预估的旁白总时长 vs 目标时长；
- 校验脚本通过结果。

**产物**：`novel_project.json`（含 `art_style`）、`characters.json`、
`locations.json`、`narration_script.yaml`（全部强制落盘，校验通过）。

## 已知限制

- **本版只支持"单次处理一段文本"**（一个章节/一段用户给定文字），不
  支持"整本小说自动分卷循环跑"；处理完整长篇小说需要用户按章节多次
  调用本 skill（每次指定不同的 `output_dir`），暂无自动衔接多集的机制；
- 角色人脸参考照片（同 `mv-generator` 的做法）本版**未实现**，所有角色
  按纯文生图流程处理，`face_reference_id` 字段先占位，留待后续版本。

## 下一步

完成本 skill 后，进入 `novel-asset-generator`（角色/地点定妆图生成 +
旁白配音），输入即为本 skill 产出的 `<output_dir>` 整个目录。
