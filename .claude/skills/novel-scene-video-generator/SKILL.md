---
name: novel-scene-video-generator
description: 小说转视频流程的第五步。v2（当前实施依据）：读取 novel-asset-generator 回填过 duration_sec 的各 macro_scene_XX/scene_detail.yaml，批量或定向生成 micro_scene 视频 clip，再在每个大场景内合成出 macro_scene_XX.mp4（clip拼接+配音混合+字幕）。v1（已归档）：读取 scene_plan.yaml 生成分场景视频。当 novel-asset-generator 已跑完、需要生成小场景视频并合成大场景视频时使用。
triggers: 单场景视频生成, 小场景视频生成, 分场景视频, generate_scene_videos_v2, compose_macro_scene, 大场景合成, 小说转视频clips
---

# 单场景视频生成 + 大场景内合成 (Novel Scene Video Generator)

## 概述

本 skill 是"小说转视频"流程的第五步：

- **v2（六段流程，当前实施依据）**：输入是 `novel-asset-generator`
  已经按 `content_blocks` 配完音、回填过 `duration_sec` 的各
  `macro_scene_XX/scene_detail.yaml`。分两段完成：① 逐个 `micro_scene`
  调用 `gen_video_with_text` 生成视频 clip；② 单个大场景内全部小场景
  clip 就绪后，在本 skill 内先合成出该大场景的
  `macro_scene_XX/macro_scene_XX.mp4`（clip 拼接 + 配音混合 + 字幕），
  并把 `macro_scenes.yaml` 对应大场景 `status` 回写为 `done`。方案
  文档：`next_doc/novel_video_generator_plan_v2.md` 第 6 节。
- **v1（四段流程，已归档）**：输入是 `novel-asset-generator` 产出的
  **完整**（`prompt_en`/`video_mode` 均已填好、通过
  `check_assets_and_audio.py` 校验）的 `scene_plan.yaml`，逐场景调用
  `gen_video_with_text` 生成视频片段，不做大场景内合成（v1 没有大
  场景概念）。方案文档：`next_doc/novel_video_generator_plan.md` 第 3 节。

**新项目请直接使用 v2**，v1 内容保留在下方"v1 版本"一节仅供历史参考。

---

## v2 版本：`generate_scene_videos_v2.py` + `compose_macro_scene.py`（当前使用）

### 输入

各 `macro_scene_XX/scene_detail.yaml`（`micro_scenes` 已通过
`check_scene_detail.py` 校验，且 `novel-asset-generator` 已回填
`duration_sec`）+ `global/characters.json`/`global/locations.json`
（供 `video_mode: reference` 解析参考图）。

### 前置：Agent 手写 `prompt_en`/`video_mode`

`novel-scene-detail-planner` 产出的 `scene_detail.yaml` 里
**没有** `prompt_en` 字段（那一步只负责内容/引用规划，不负责画面
prompt）。在跑生成脚本之前，Agent 需要为每个 `micro_scene` 结合
`visual_hint` + `novel_project.json.art_style` + 引用到的角色/地点
`description_en` 手写 `prompt_en`（英文，供 `gen_video_with_text`
使用），并按需设置 `video_mode`（`reference`/`keyframe`/`text`，
规则同 v1），直接写回对应 `scene_detail.yaml` 的 `micro_scenes[*]`
条目。**本脚本不代为生成 prompt**——`prompt_en` 为空时会直接把该
小场景标记为失败并给出明确错误，不会用空 prompt 调用视频接口。

### Step 1：批量生成小场景视频

```bash
python .claude/skills/novel-scene-video-generator/scripts/generate_scene_videos_v2.py \
  <output_dir> --aspect-ratio <novel_project.json 里的 aspect_ratio>
```

⚠️ **调用 bash 工具执行本命令时，`timeout` 参数必须传 `-1`**，理由
同 v1：单场景视频生成常常要几分钟。

- 不传 `--macro-id`/`--micro-id` 时处理全部大场景下的全部小场景；
  `--macro-id macro_01 macro_03` 只处理指定大场景；`--micro-id
  micro_02` 进一步只处理指定小场景（两者可组合使用）；
- 已存在且非空的 `clips/<micro_id>.mp4` 默认跳过（断点续跑），
  `--force` 强制全部重新生成；
- 单场景失败自动重试 3 次，一轮跑完仍有未成功场景自动从头再跑一轮
  （每个大场景独立计轮次），直到全部成功或判定为持续性失败；
- 每个小场景处理完，把对应 `scene_detail.yaml` 里该 `micro_scene`
  的 `status` 回写为 `done`/`failed`。

产物：`macro_scene_XX/clips/<micro_id>.mp4` + 回写状态后的
`scene_detail.yaml`。

### Step 2：定向重跑（按需）

```bash
python .claude/skills/novel-scene-video-generator/scripts/generate_scene_videos_v2.py \
  <output_dir> --macro-id macro_01 --micro-id micro_03 --force
```

用法与 v1 一致：补齐失败场景不需要 `--force`；对某个小场景的画面
不满意想重新生成才需要 `--force`。

### Step 3：校验小场景 clip

```bash
python .claude/skills/novel-scene-video-generator/scripts/check_clips_v2.py \
  <output_dir>
```

- 校验每个 `micro_scene` 是否都有非空的 `clips/<id>.mp4`，
  `status` 字段与磁盘状态是否一致；
- 若某个大场景已经合成过 `macro_scene_XX.mp4`，顺带校验其时长是否
  约等于该大场景所有 `micro_scene.duration_sec` 之和；
- 不通过 → 回 Step 2 用 `--micro-id` 补齐，重新跑校验直到通过。

### Step 4：大场景内合成

小场景 clip 全部就绪（Step 3 通过）后，**逐个大场景**跑：

```bash
python .claude/skills/novel-scene-video-generator/scripts/compose_macro_scene.py \
  <output_dir> macro_01
```

- 把该大场景全部 `micro_scene` clip 按顺序硬切拼接，配音（每个
  `micro_scene` 的 `content_blocks` 对应若干段旁白/对话 wav，按顺序
  首尾相接）混入，按每个 `micro_scene` 拼出的字幕文案（旁白原样、
  对话用「」包裹）渲染字幕；
- 非 Windows 环境需要 `--font-path <本地中文字体路径>`（同 v1）；
- 缺 clip 时默认拒绝合成，`--allow-missing-clips` 才允许借用相邻
  小场景画面强制拉伸填补（同 v1，仅用于明确知情的场景）；
- 合成并校验通过后，自动把 `macro_scenes.yaml` 里该大场景的
  `status` 从 `planned` 回写为 `done`；**校验不通过则不回写**，
  保持 `planned`，避免下游误以为已完成；
- 产物：`macro_scene_XX/macro_scene_XX.mp4`。

对每个 `status: planned` 的大场景重复 Step 1-4，直到 `macro_scenes.yaml`
里所有大场景都变成 `status: done`，再进入 `novel-video-composer`
做最终拼接。

**Agent 需向用户展示的中间内容**：每个大场景的小场景生成成功/失败
数量、大场景合成结果（时长/分辨率）、持续性失败场景的可能原因。

**产物**：全部 `macro_scene_XX/clips/*.mp4`（非空）+ 全部
`macro_scene_XX/macro_scene_XX.mp4` + `macro_scenes.yaml` 全部
`status: done`。

### v2 常见问题

同 v1 三条（prompt 触发审核、`reference` 模式缺参考图自动降级、
`timeout` 忘记传 `-1`），处理方式一致，只是命令换成
`generate_scene_videos_v2.py` 并按需加 `--macro-id`/`--micro-id`。
额外一条：**`compose_macro_scene.py` 报错"缺少配音"**——说明
`novel-asset-generator` 的 `synthesize_scene_audio.py` 还没跑完该
大场景，回去补跑（可用 `--macro-id` 只跑这一个大场景）。

---

## v1 版本：`generate_scene_videos.py`（已归档，仅供历史参考）

**依赖 skill**：`gen_video_with_text`（必须）

**依赖脚本**（本 skill 目录下，均改造自 `mv-generator` 的同名脚本，
主要差异是字段名从 mv 的 `start`/`end`/`uses_assets` 换成 novel 的
`duration_sec`/`uses_characters`+`uses_locations`，且新增了
`--scene-id` 定向重跑支持）：
- `scripts/generate_scene_videos.py`：批量/定向生成场景视频，内置
  key 池自动切换 + 失败重试 3 次 + 断点续跑，生成完成后把
  `scene_plan.yaml` 对应场景的 `status` 回写为 `done`/`failed`
- `scripts/check_clips.py`：校验所有场景是否都已生成非空的
  `clips/<scene_id>.mp4`，本 skill 结束前必须跑，不通过不能交付给
  `novel-video-composer`

**外部依赖**：`AGNES_API_KEY`/`AGNES_API_KEYS` 环境变量（同
`gen_video_with_text`）。

## 流程规范

### 前置检查

进入本 skill 前，确认 `novel-asset-generator` 的
`check_assets_and_audio.py` 已经通过（退出码 0）——本 skill 不重复做这项
检查，`generate_scene_videos.py` 遇到 `prompt_en` 为空的场景会直接把
该场景标记为失败并给出明确错误，而不是尝试用空 prompt 调用视频接口。

### Step 1：批量生成

```bash
python .claude/skills/novel-scene-video-generator/scripts/generate_scene_videos.py \
  <output_dir> --aspect-ratio <novel_project.json 里的 aspect_ratio>
```

⚠️ **调用 bash 工具执行本命令时，`timeout` 参数必须传 `-1`（不限时）**，
理由同 `mv-generator`：单场景视频生成（含排队+轮询+下载）常常要几分钟，
一次批量生成动辄超过 300 秒的默认超时。

- 已存在且非空的 `clips/<scene_id>.mp4` 默认跳过（断点续跑）；需要强制
  全部重新生成时加 `--force`；
- 每个场景开始生成前会打印进度和关键信息（`duration_sec`/
  `uses_characters`/`uses_locations`/`prompt_en`），方便观察当前卡在
  哪个场景；
- 单场景失败自动重试 3 次；一轮跑完仍有未成功场景会自动从头再跑一轮，
  直到全部成功或某一轮完全没有新增成功（判定为持续性失败，停止自动
  重试并汇报剩余失败的 scene id，交给 Agent 判断——常见原因见下方
  「常见问题」）；
- 每个场景处理完会把 `scene_plan.yaml` 对应条目的 `status` 回写为
  `done`/`failed`，方便随时 `cat scene_plan.yaml` 查看整体进度。

### Step 2：定向重跑（按需）

如果 Step 1 汇报有场景失败、或者用户对某个场景的画面不满意想重新生成，
**不需要重跑全部场景**，只需要：

```bash
python .claude/skills/novel-scene-video-generator/scripts/generate_scene_videos.py \
  <output_dir> --scene-id scene_03 scene_07 --force
```

（不满意重新生成必须加 `--force`，否则脚本会因为 clip 文件已存在而跳过；
单纯补齐失败场景不需要 `--force`，直接重跑即可，已成功的场景不受影响。）

### Step 3：校验交付

```bash
python .claude/skills/novel-scene-video-generator/scripts/check_clips.py \
  <output_dir>
```

- **通过（退出码 0）**：所有场景都有非空的 `clips/<scene_id>.mp4`，可以
  交付给 `novel-video-composer`；
- **不通过（退出码 1）**：stdout 打印 `missing_scene_ids`/`empty_scene_ids`
  结构化清单。**不要跳过检查直接进入下一个 skill**，正确做法是回到
  Step 2 用 `--scene-id` 原样补生成清单里列出的场景，重新跑本检查，
  如此循环直到通过为止。

**Agent 需向用户展示的中间内容**：
- 最终成功/失败场景数量；
- 若有持续性失败的场景，说明可能原因（prompt 违规、参考图缺失、账号
  额度耗尽等）和已尝试的重试次数。

**产物**：`clips/<scene_id>.mp4`（全部非空）+ 状态已回写为 `done` 的
`scene_plan.yaml`。

## 常见问题

1. **某个场景反复重试仍失败**：先看脚本汇报的最后一次错误信息——常见
   原因是 prompt 触发内容审核（改写 `prompt_en` 里可能敏感的措辞）、
   `uses_characters`/`uses_locations` 引用的 `asset_path` 路径错误
   （回 `novel-asset-generator` 检查）、或账号所有 key 都被限流/额度
   耗尽（等待冷却或换 key）。修正后用 `--scene-id` 定向重跑，不需要
   动其它已成功的场景。
2. **`video_mode: reference` 但仍然生成失败并提示"没有可用的参考图片"**：
   说明 `characters.json`/`locations.json` 里对应条目缺少 `asset_path`
   ——脚本会自动降级为 `text` 模式继续生成（不会因此整体失败），但画面
   一致性会打折扣，建议回 `novel-asset-generator` 补生成缺失的定妆图，
   再用 `--scene-id --force` 重新生成这个场景。
3. **`timeout` 忘记传 `-1` 导致命令被提前杀掉**：已成功的场景不会丢失
   （断点续跑机制），直接重新执行同一条命令（这次记得加
   `timeout: -1`）即可从中断处继续。
