---
name: novel-scene-detail-planner
description: 小说转视频 v2 流程的第三步。针对 macro_scenes.yaml 里的单个大场景（status:pending），把它拆成若干小场景（micro_scene，每个对应最终一支4-12秒的视频clip），区分旁白/角色对话（content_blocks），对话必须是原文逐字摘录；校验角色/地点引用是否都在全局库里存在，缺失时回调 novel-entity-extractor 补抽取+novel-asset-generator 补生成素材。当 novel-macro-scene-planner 已产出 macro_scenes.yaml 后，逐个大场景调用本 skill。
triggers: 小场景规划, scene detail, 对话抽取, content_blocks, novel scene detail planner
---

# 单大场景详细规划 (Novel Scene Detail Planner)

## 概述

本 skill 是"小说转视频 v2"六段流程的第三步，也是**除 Skill1 抽取之外
最需要 Agent 精细判断的一步**：

```
novel-entity-extractor
  → novel-macro-scene-planner
  → novel-scene-detail-planner（本 skill）
  → novel-asset-generator
  → novel-scene-video-generator
  → novel-video-composer
```

方案文档：`next_doc/novel_video_generator_plan_v2.md`（第 4 节）。

**每次只处理 `macro_scenes.yaml` 里的一个大场景**（按 `status: pending`
挑一条，通常按顺序来），不是一次性处理全部大场景——这样每个大场景可以
独立重跑、断点续跑，互不影响。

**依赖**：无外部 API/模型依赖，纯 Agent 推理 + 文件读写；引用缺失时会
调用 `novel-entity-extractor`（模式 B）和 `novel-asset-generator` 补齐
资源，这两个是外部调用，不是本 skill 自己实现。

**依赖脚本**：`scripts/check_scene_detail.py`——校验引用完整性、对话
原文摘录真实性、小场景时长区间，写完必须跑，不通过不能交付给下游 skill。

## ⚠️ 产物文件强制保存规范

1. 先生成内容，立刻写文件；
2. 每个大场景的产物独立存放在自己的工作目录，不与其他大场景混存；
3. 写入失败必须报告错误并停止。

产物路径（以处理 `macro_01` 为例）：

```
novel_output/小说名_20260911/
├── macro_scenes.yaml              # 已存在，本 skill 处理完把对应条目
│                                    status 从 pending 改成 planned
├── global/                        # 可能被本 skill 追加更新（回补角色/地点）
└── macro_scene_01/                # 本 skill 新建的大场景工作目录
    └── scene_detail.yaml          # 本 skill 产物
```

## 流程规范

### Step 1：取出待处理的大场景

从 `macro_scenes.yaml` 里挑一条 `status: pending` 的记录（通常按 id 顺序，
用户也可以指定处理某个特定 `macro_id`），读取它的 `raw_text`/
`uses_characters`/`uses_locations`。

新建工作目录 `<output_dir>/macro_scene_<id后缀>/`（比如 `macro_01` →
`macro_scene_01/`）。

### Step 2：切分小场景（micro_scene）

把该大场景的 `raw_text` 按镜头/画面切换点切成若干小场景，**每个小场景
对应最终一支独立生成的视频 clip**，口播时长控制在 **4-12 秒**（同 v1
`gen_video_with_text` 的单 clip 硬限规则）。

### Step 3：区分旁白/对话，写 content_blocks

每个小场景内部，按原文顺序把内容拆成 `content_blocks` 列表，每块标
`type: narration`（旁白，Agent 可以改写成适合口播的转述）或
`type: dialogue`（角色对话，**必须是原文逐字摘录，不允许改写、不允许
补写原文没有的台词**，哪怕原文的对话很简短或者语气不适合直接口播，
也只能摘录原文，不能"优化"）：

```yaml
content_blocks:
  - type: narration
    text: "夜色渐浓，林然推门走进客栈"
    speaker: null
  - type: dialogue
    text: "客官打尖还是住店？"        # 原文逐字摘录
    speaker: char_02                   # 说这句话的角色 id
```

`speaker` 字段：`narration` 恒为 `null`；`dialogue` 必须填一个
`uses_characters` 里的角色 id（说话人不明确时，结合上下文和该角色的
`relations`/`first_appear` 判断，实在无法判断则不要强行归属，宁可把
这段处理成 `narration`（转述"有人说……"）也不要瞎归属说话人）。

### Step 4：标注引用与镜头描述

每个小场景标注：
```yaml
micro_scenes:
  - id: micro_01
    macro_id: macro_01
    uses_characters: [char_01, char_02]
    uses_locations: [loc_01]
    visual_hint: "客栈门口，夜晚，林然推门而入，暖光从门内透出"
    content_blocks: [...]
    duration_sec: null       # 本 skill 不填，等 novel-asset-generator 跑完TTS回填
    status: pending           # novel-scene-video-generator 处理完回写 done/failed
```

### Step 5：引用完整性校验与回补循环

写完全部 `micro_scenes` 后，检查每条 `uses_characters`/`uses_locations`
（以及每条 `dialogue.speaker`）是否都能在 `global/characters.json`/
`global/locations.json` 里找到，**且已有 `asset_path`**（不只是存在 id，
还要已经生成过定妆图/素材）：

- 若发现引用了全局库里完全不存在的角色/地点（Skill1/Skill2 阶段遗漏）：
  1. 调用 `novel-entity-extractor` 模式 B，把本大场景的 `raw_text` 交
     给它补抽取，拿到新增的角色/地点 id；
  2. 调用 `novel-asset-generator`（只处理新增的这几个角色/地点）补生成
     定妆图，回填 `asset_path`；
  3. 回到 Step 2-4，重新生成/调整涉及这些新增实体的小场景规划；
- 若角色/地点 id 已存在但 `asset_path` 还是 `null`（比如 Skill1 抽取
  完但 Skill4 素材还没跑到这个角色）：只需调用 `novel-asset-generator`
  补生成，不需要重新调用 Skill1。

这个回补循环**必须在本 skill 内部闭环完成**，不能把"引用了不存在的
资源"这种问题遗留到下游 `novel-asset-generator`/`novel-scene-video-generator`
才发现。

### Step 6：写入产物 + 跑校验

写入 `<output_dir>/macro_scene_<id后缀>/scene_detail.yaml`：
```yaml
macro_id: macro_01
micro_scenes:
  - id: micro_01
    ...(同 Step 4)
```

跑校验：
```bash
python .claude/skills/novel-scene-detail-planner/scripts/check_scene_detail.py \
  <output_dir> <macro_id>
```

校验内容见下方"校验脚本说明"。不通过 → 回 Step 2-5 调整，重新跑校验，
直到通过。

通过后，把 `macro_scenes.yaml` 对应大场景条目的 `status` 从 `pending`
改成 `planned`。

## 校验脚本说明 `check_scene_detail.py`

对指定 `macro_id` 的 `scene_detail.yaml` 检查：
1. 每个 `micro_scene` 的 `uses_characters`/`uses_locations` 是否都能在
   `global/characters.json`/`global/locations.json` 里找到，且对应条目
   的 `asset_path` 非空；
2. 每个 `dialogue` block 的 `speaker` 是否在该 `micro_scene` 的
   `uses_characters` 列表里；
3. **对话真实性校验**：每个 `dialogue.text`（去除空白和标点后）是否
   能在该大场景的 `raw_text`（同样去除空白标点后）里找到子串匹配，
   找不到判定为"疑似臆造对话"，报错；
4. 每个小场景至少有一个 `content_blocks`，且不能全是空文本；
5. `micro_scenes` 的 `id` 在整个项目范围内（不只是本大场景内）不重复
   （脚本会读取 `macro_scene_*/scene_detail.yaml` 下所有已存在的
   micro_scene id 做全局去重检查，防止不同大场景之间 id 冲突）。

退出码非 0 时不允许把 `macro_scenes.yaml` 对应条目标记为 `planned`，
也不允许交付给下游。

**Agent 需向用户展示的中间内容**：本大场景切出的小场景数量、每个小
场景的旁白+对话摘要、新回补的角色/地点（如果有）、校验通过结果。

**产物**：`macro_scene_<id后缀>/scene_detail.yaml`（强制落盘，校验通过），
`macro_scenes.yaml` 对应条目 `status` 更新为 `planned`，可能追加更新的
`global/characters.json`/`global/locations.json`。

## 已知限制

- 对话真实性校验是子串匹配，无法识别"原文对话被拆成两半分别摘录"这种
  情况是否合理（只要两半分别能在 raw_text 里找到就会通过），仍需 Agent
  自查拆分是否保持了原文语义完整；
- `speaker` 判断依赖 Agent 对上下文的理解，脚本只能校验"speaker 在不在
  uses_characters 里"，无法校验"这句话是不是真的这个角色说的"，误判
  需要人工复核。

## 下一步

单个大场景处理完（`status: planned`）后，回到 `macro_scenes.yaml` 检查
是否还有其他 `status: pending` 的大场景，有则继续调用本 skill 处理下
一个；全部大场景都变成 `planned` 后，进入 `novel-asset-generator`（跑
TTS 配音，回填每个小场景的真实 `duration_sec`）。
