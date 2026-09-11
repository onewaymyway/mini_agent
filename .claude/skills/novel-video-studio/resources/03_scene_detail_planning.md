# 阶段3：单大场景详细规划 (micro_scene)

**每次只处理 `macro_scenes.yaml` 里的一个大场景**（按 `status: pending`
挑一条，通常按顺序来），这样每个大场景可以独立重跑、断点续跑。

**依赖**：无外部 API，纯 Agent 推理 + 文件读写；引用缺失时会回调阶段1
模式B、阶段4，这两个是外部调用，不是本阶段自己实现。
**校验脚本**：`scripts/check_scene_detail.py`。

产物路径（以处理 `macro_01` 为例）：
```
novel_output/小说名_20260911/
├── macro_scenes.yaml       # 本阶段处理完把对应条目 status 从 pending 改成 planned
├── global/                 # 可能被本阶段追加更新（回补角色/地点）
└── macro_scene_01/         # 本阶段新建
    └── scene_detail.yaml
```

## Step 1：取出待处理的大场景

从 `macro_scenes.yaml` 挑一条 `status: pending` 的记录（通常按 id 顺序，
用户也可以指定 `macro_id`），读取 `raw_text`/`uses_characters`/
`uses_locations`。新建工作目录 `<output_dir>/macro_scene_<id后缀>/`。

## Step 2：切分小场景（micro_scene）

把该大场景的 `raw_text` 按镜头/画面切换点切成若干小场景，**每个小场景
对应最终一支独立生成的视频 clip**，口播时长控制在 **4-12 秒**。

## Step 3：区分旁白/对话，写 content_blocks

每个小场景内部，按原文顺序拆成 `content_blocks` 列表：

```yaml
content_blocks:
  - type: narration
    text: "夜色渐浓，林然推门走进客栈"   # 旁白，可以改写成适合口播的转述
    speaker: null
  - type: dialogue
    text: "客官打尖还是住店？"           # 必须原文逐字摘录，不允许改写/补写
    speaker: char_02                       # 说这句话的角色 id
```

`speaker`：`narration` 恒为 `null`；`dialogue` 必须填 `uses_characters`
里的角色 id（说话人不明确时结合上下文判断，实在无法判断宁可处理成
`narration`（转述"有人说……"）也不要瞎归属）。

**对话严禁改写或补写**——哪怕原文对话很简短或语气不适合直接口播，也
只能摘录原文，不能"优化"，校验脚本会做子串匹配，改写过的文本会被判定
为疑似臆造直接打回。

## Step 4：标注引用与镜头描述

```yaml
micro_scenes:
  - id: micro_01
    macro_id: macro_01
    uses_characters: [char_01, char_02]
    uses_locations: [loc_01]
    visual_hint: "客栈门口，夜晚，林然推门而入，暖光从门内透出"
    content_blocks: [...]
    duration_sec: null       # 本阶段不填，等阶段4跑完TTS回填
    status: pending           # 阶段5处理完回写 done/failed
```

## Step 5：引用完整性校验与回补循环

检查每条 `uses_characters`/`uses_locations`（以及每条 `dialogue.speaker`）
是否都能在全局库里找到，**且已有 `asset_path`**：

- 引用了全局库里完全不存在的角色/地点：
  1. 调用阶段1模式B，把本大场景的 `raw_text` 交给它补抽取，拿到新增
     角色/地点 id；
  2. 调用阶段4（只处理这几个新增角色/地点）补生成定妆图，回填
     `asset_path`；
  3. 回到 Step 2-4，重新调整涉及这些新增实体的小场景规划；
- 角色/地点 id 已存在但 `asset_path` 还是 `null`：只需调用阶段4补生成，
  不需要重新调用阶段1。

这个回补循环**必须在本阶段内部闭环完成**，不能把"引用了不存在的资源"
遗留到阶段4/5才发现。

## Step 6：写入产物 + 跑校验

写入 `<output_dir>/macro_scene_<id后缀>/scene_detail.yaml`，然后：

```bash
python .claude/skills/novel-video-studio/scripts/check_scene_detail.py \
  <output_dir> <macro_id>
```

不通过 → 回 Step 2-5 调整，重新跑校验，直到通过。通过后，把
`macro_scenes.yaml` 对应条目的 `status` 从 `pending` 改成 `planned`。

## 校验脚本说明 `check_scene_detail.py`

1. 每个 `micro_scene` 的引用是否都能在全局库找到，且 `asset_path` 非空；
2. 每个 `dialogue` 的 `speaker` 是否在该 `micro_scene` 的
   `uses_characters` 列表里；
3. **对话真实性校验**：每个 `dialogue.text`（去空白标点后）是否能在该
   大场景 `raw_text`（同样去空白标点）里找到子串匹配，找不到判定为
   疑似臆造对话，报错；
4. 每个小场景至少有一个非空 `content_blocks`；
5. `micro_scenes` 的 `id` 在整个项目范围内（不只是本大场景）不重复。

退出码非 0 时不允许把状态标记为 `planned`，也不允许交付给下游。

**向用户展示**：本大场景切出的小场景数量、每个小场景的旁白+对话摘要、
新回补的角色/地点（如果有）、校验通过结果。

## 已知限制

- 对话真实性校验是子串匹配，无法识别"原文对话被拆成两半分别摘录"是否
  合理，仍需自查拆分是否保持了原文语义完整；
- `speaker` 判断依赖 Agent 对上下文的理解，脚本只能校验"speaker 在不
  在 uses_characters 里"，无法校验"这句话是不是真的这个角色说的"。

## 下一步

单个大场景处理完（`status: planned`）后，回到 `macro_scenes.yaml` 检查
是否还有其他 `pending` 的大场景，有则继续处理下一个；也可以对已
`planned` 的大场景直接推进阶段4（不需要等全部大场景都规划完）。
