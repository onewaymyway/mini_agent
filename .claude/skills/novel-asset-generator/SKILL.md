---
name: novel-asset-generator
description: 小说转视频流程的第二步。读取 novel-scene-planner 产出的 characters.json / locations.json / narration_script.yaml，生成角色/地点定妆图，并给每段旁白配音（本地 CosyVoice 优先，装不上自动降级 edge-tts），用真实配音时长生成正式版 scene_plan.yaml。当 novel-scene-planner 已经跑完、需要接着生成素材和配音时使用。
triggers: 角色定妆图, 旁白配音, TTS配音, cosyvoice, edge-tts, scene_plan, 小说转视频素材
---

# 角色/场景素材 + 旁白配音生成 (Novel Asset Generator)

## 概述

本 skill 是"小说转视频"四段流程的第二步，承接 `novel-scene-planner` 的
产出（`characters.json`/`locations.json`/`narration_script.yaml`），完成：

1. 角色/地点定妆图生成（复用 `gen_image_with_text`）；
2. 每段旁白的 TTS 配音（本地 CosyVoice 优先，自动降级 edge-tts）；
3. 用真实配音时长，把草稿版 `narration_script.yaml` 升级成正式版
   `scene_plan.yaml`（供 `novel-scene-video-generator` 使用）。

方案文档：`next_doc/novel_video_generator_plan.md` 第 2 节。

**依赖 skill**：`gen_image_with_text`（必须，生成定妆图）

**依赖脚本**（本 skill 目录下）：
- `scripts/tts_engine.py`：TTS 引擎封装，`synthesize()` 单条合成入口，
  内部处理 CosyVoice→edge-tts 的自动降级，一般不需要单独调用，被
  `synthesize_narration.py` 复用
- `scripts/synthesize_narration.py`：批量给全部（或 `--segment-id` 指定的）
  旁白段落配音，生成/更新正式版 `scene_plan.yaml`，Step 2 用它
- `scripts/check_assets_and_audio.py`：校验定妆图 + 配音是否都已就绪，
  Step 3 结束必须跑，不通过不能进入 `novel-scene-video-generator`

**外部依赖**：
- 本地 CosyVoice（可选但推荐）：需要专用环境（建议新建 conda 环境
  `novel_tts_env`，避免和 `mv-generator` 的 `mv_env` 混装），安装
  `cosyvoice` 及其依赖（`torch`/`torchaudio` 等）+ 下载预训练模型权重到
  `pretrained_models/CosyVoice-300M`（或用 `COSYVOICE_MODEL_DIR` 环境
  变量/`--cosyvoice-model-dir` 指定其它路径）。**没装/装不上不阻塞
  流程**——`tts_engine.py` 会自动降级到 edge-tts，只是终端会打印一行
  降级提示；
- `edge-tts`（兜底，几乎总能装上）：`pip install edge-tts
  --break-system-packages`，纯在线服务，不需要本地模型/GPU；
- `ffmpeg`/`ffprobe`（系统命令，读取/转码音频时长用，`mv-generator` 已
  要求装过，这里复用同一份环境依赖）；
- `AGNES_API_KEY` 环境变量（`gen_image_with_text` 需要）。

## ⚠️ 产物文件强制保存规范（同 novel-scene-planner）

先生成立刻写文件；文件写入是步骤完成的标志；每次重新生成都要覆盖；
写入失败必须报告错误并停止。产物追加到 `novel-scene-planner` 已建好的
`output_dir` 里：

```
novel_output/小说名_20260911/
├── ...(novel-scene-planner 已有产物)
├── assets/
│   ├── character_*.png       # 本 skill 产物
│   └── location_*.png        # 本 skill 产物
├── audio/
│   └── segment_*.wav         # 本 skill 产物
└── scene_plan.yaml           # 本 skill 产物：narration_script.yaml 的正式版
```

## 流程规范

### 前置检查

进入 Step 1 之前，先确认 `novel-scene-planner` 的产物已通过校验
（`check_narration_draft.py` 退出码为 0）；本 skill 不重复做这项检查，
但如果读到的 `characters.json`/`narration_script.yaml` 明显不完整
（比如角色列表为空但 `narration_script.yaml` 引用了角色 id），应主动
提醒用户回 `novel-scene-planner` 补齐，而不是硬着头皮往下跑。

### Step 1：角色/地点定妆图生成

对 `characters.json`/`locations.json` 里的每一项调用
`gen_image_with_text`，`description_en` 末尾已经在 `novel-scene-planner`
阶段融入了 `art_style`，直接用即可：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<description_en>" --size 2K --ratio <novel_project.json 里的 aspect_ratio> \
  --save-path <output_dir>/assets/character_<id>.png
```

地点同理，落到 `assets/location_<id>.png`。生成后**立即回填**
`characters.json`/`locations.json` 里对应的 `asset_path` 字段（直接用
`str_replace`/重写 JSON 文件即可，不需要额外脚本）。

角色若需要人脸参考照片（`face_reference_id` 非空）——**本版
`novel-scene-planner` 尚未实现这个功能的采集入口**，所以当前
`face_reference_id` 恒为 `null`，本 skill 按纯文生图流程处理即可，不需要
判断这个分支（留作后续版本，见方案文档「已知限制」）。

封面图（可选）：若需要，同 `mv-generator` 的做法单独生成一张
`assets/cover.png`，构图为标题留白。本版不强制要求，`novel-video-composer`
没有 `assets/cover.png` 时直接跳过封面效果。

### Step 2：旁白配音 + 生成正式版 scene_plan.yaml

```bash
python .claude/skills/novel-asset-generator/scripts/synthesize_narration.py \
  <output_dir>
```

- 依次给 `narration_script.yaml` 每个 segment 配音，写入
  `audio/segment_<id>.wav`，并据此生成/更新 `<output_dir>/scene_plan.yaml`；
- **已存在且非空的音频默认跳过**（断点续跑），需要强制重新生成全部时加
  `--force`；
- **终端会打印每段实际用的引擎**（`cosyvoice` 还是降级后的
  `edge-tts`），CosyVoice 不可用时会带上具体原因，一次性看清楚本次
  跑下来有多少段用了兜底引擎；
- 脚本退出码为 1 且 `warnings` 里出现"时长超过 12s 上限"时：**不允许
  忽略直接进入 Step 3**——回 `narration_script.yaml` 把对应 segment 的
  文案拆成两段（分别给一个新的 segment id），然后：
  ```bash
  python .claude/skills/novel-asset-generator/scripts/synthesize_narration.py \
    <output_dir> --segment-id <新拆出的两个 segment id>
  ```
  只重新生成受影响的场景，不需要全量重跑（已生成好的其它段落的音频/
  记录会原样保留）；
- 时长低于 4s 下限只是 warning（建议合并相邻场景，不强制），如果暂时
  不想处理，可以先带着这条 warning 继续，但 Step 3 的校验脚本会再次
  报出来，最终仍然需要处理才能进入 `novel-scene-video-generator`
  （该脚本对 4–12s 范围是 error 级别，不是 warning）。
- **`prompt_en`/`video_mode` 字段本 skill 不负责填**（脚本产出里是
  `null`）——这两个字段依赖"这个场景该用 reference/keyframe/text 哪种
  模式生成视频"的判断，留给下一个 skill（`novel-scene-video-generator`）
  或 Agent 在进入下一个 skill 前手动补充（见 Step 3）。

### Step 3：补充 prompt_en / video_mode，校验交付

Step 2 生成的 `scene_plan.yaml` 里 `prompt_en`/`video_mode` 是空的，
Agent 需要**在本 skill 收尾时补上**（不留到下一个 skill 再做，保证交付
给 `novel-scene-video-generator` 的是一份完整可用的场景规划）：

- `prompt_en`：结合 `scene.visual_hint` + `art_style` 写成完整英文画面
  prompt（同 `mv-generator` Step 3 的写法要求：镜头/构图、环境、人物
  动作与情绪、光线氛围）；
- `video_mode`：
  - 场景引用的角色/地点都已有 `asset_path`（Step 1 已生成）→
    `reference`；
  - 该场景不引用任何反复出现的角色/地点（比如纯环境空镜）→ `text`；
  - 本版不规划 `keyframe` 模式（留给 `novel-scene-video-generator` 或
    后续版本按需处理首尾帧衔接）。

写完后跑校验：

```bash
python .claude/skills/novel-asset-generator/scripts/check_assets_and_audio.py \
  <output_dir>
```

该脚本检查角色/地点定妆图完整性、场景引用完整性、每段音频存在且时长在
4–12 秒范围内、总时长是否贴合 `target_duration_sec`。**退出码非 0 不允许
交付给 `novel-scene-video-generator`**，按报错逐条修复（补生成缺失定妆图、
回 Step 2 重新配音超限/过短的场景），重新跑校验直到通过。

**Agent 需向用户展示的中间内容**：
- 定妆图生成结果（数量、是否有生成失败需要重试的）；
- 配音引擎使用情况（多少段用了 CosyVoice，多少段降级到了 edge-tts）；
- 校验脚本通过结果 + 总时长 vs 目标时长。

**产物**：`assets/*.png`、`audio/*.wav`、完整（`prompt_en`/`video_mode`
均已填好）且校验通过的 `scene_plan.yaml`。

## 常见问题

1. **CosyVoice 一直降级到 edge-tts**：检查 `novel_tts_env`（或你自定义的
   环境）里 `cosyvoice`/`torch`/`torchaudio` 是否装好、
   `pretrained_models/CosyVoice-300M` 权重是否已下载；`tts_engine.py`
   报的 `cosyvoice_error` 字段会带具体异常信息，照着排查即可。edge-tts
   本身完全可用、产出的旁白质量足够交付，只是音色/克隆能力不如本地方案，
   不是必须解决的阻塞问题。
2. **edge-tts 也失败**：多数是网络问题（`speech.platform.bing.com`
   连不上），检查网络环境；短期内无法解决时，只能等网络恢复，本 skill
   没有第三条降级路径。
3. **`ffmpeg`/`ffprobe` 未安装**：`tts_engine.py` 依赖它们读取/转码音频
   时长，装法同 `mv-generator` 文档说明。
