---
name: novel-scene-video-generator
description: 小说转视频流程的第三步。读取 novel-asset-generator 产出的正式版 scene_plan.yaml（含精确时长、prompt_en、video_mode），批量或定向调用 gen_video_with_text 生成每个场景的视频片段。当 novel-asset-generator 已经跑完（定妆图+配音+scene_plan.yaml 都就绪）、需要接着生成分场景视频时使用。
triggers: 单场景视频生成, 分场景视频, generate_scene_videos, 小说转视频clips
---

# 单场景视频生成 (Novel Scene Video Generator)

## 概述

本 skill 是"小说转视频"四段流程的第三步，输入是 `novel-asset-generator`
产出的**完整**（`prompt_en`/`video_mode` 均已填好、通过
`check_assets_and_audio.py` 校验）的 `scene_plan.yaml`，逐场景调用
`gen_video_with_text` 生成视频片段。

方案文档：`next_doc/novel_video_generator_plan.md` 第 3 节。

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
