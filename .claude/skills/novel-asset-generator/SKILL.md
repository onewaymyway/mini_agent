---
name: novel-asset-generator
description: 小说转视频 v2 流程的第四步。读取 global/characters.json、global/locations.json 和各 macro_scene_*/scene_detail.yaml，按需生成角色/地点定妆图（含新增实体的增量补齐），并按 content_blocks 给每个小场景的旁白+角色对话分别配音（角色对话按 voice_profile 选用不同音色），回填每个小场景的真实 duration_sec。当 novel-scene-detail-planner 已经把所有大场景都规划到 status:planned 后使用。
triggers: 角色定妆图, 旁白配音, 角色配音, TTS配音, cosyvoice, edge-tts, content_blocks配音, 小说转视频素材
---

# 角色/场景素材 + 差异化配音生成 (Novel Asset Generator v2)

## 概述

本 skill 是"小说转视频 v2"六段流程的第四步：

```
novel-entity-extractor
  → novel-macro-scene-planner
  → novel-scene-detail-planner
  → novel-asset-generator（本 skill）
  → novel-scene-video-generator
  → novel-video-composer
```

方案文档：`next_doc/novel_video_generator_plan_v2.md`（第 5 节）。

相对 v1 的变化：
1. **按需触发**：不再是"全量跑一次"，支持只给新增的角色/地点补生成
   素材（配合 `novel-scene-detail-planner` 的引用回补循环）；
2. **角色差异化配音**：不再是整段旁白配一次音，改成按 `content_blocks`
   逐块配音，`dialogue` 块按说话人角色的 `voice_profile` 选用不同音色；
3. **产物路径改变**：音频不再是全局 `audio/`，而是各
   `macro_scene_<后缀>/audio/` 下；不再产出 `scene_plan.yaml`，回填目标
   变成各 `macro_scene_<后缀>/scene_detail.yaml` 里每个小场景的
   `duration_sec`。

**依赖 skill**：`gen_image_with_text`（定妆图生成，必须）。

**依赖脚本**（本 skill 目录下，v2 新增）：
- `scripts/voice_mapping.py`：把角色 `voice_profile` 文字描述映射到具体
  TTS 音色（edge-tts 内置音色名 / CosyVoice 说话人名，规则式关键词匹配）；
- `scripts/synthesize_scene_audio.py`：批量给全部（或 `--macro-id` 指定
  的）大场景下所有小场景的 `content_blocks` 配音，回填 `duration_sec`；
- `scripts/check_assets_and_audio_v2.py`：校验素材+配音是否都已就绪，
  必须跑，不通过不能进入 `novel-scene-video-generator`。

（v1 遗留脚本 `scripts/tts_engine.py` 被本 skill 复用不变；
`scripts/check_assets_and_audio.py`/`scripts/synthesize_narration.py`
是 v1 专用，只服务旧的 `narration_script.yaml`/`scene_plan.yaml` 契约，
v2 流程不再调用，保留在目录里仅供历史参考。）

**外部依赖**：同 v1（CosyVoice 可选+`novel_tts_env`、edge-tts 兜底、
ffmpeg/ffprobe、`AGNES_API_KEY`），不再重复列出。

## ⚠️ 产物文件强制保存规范

同其余各 skill：先生成立刻写文件；每次重新生成都要覆盖；写入失败必须
报告错误并停止。

产物路径：

```
novel_output/小说名_20260911/
├── global/
│   ├── characters.json           # 本 skill 回填 asset_path
│   ├── locations.json            # 本 skill 回填 asset_path
│   └── assets/
│       ├── character_*.png       # 本 skill 产物
│       └── location_*.png        # 本 skill 产物
└── macro_scene_XX/
    ├── scene_detail.yaml         # 本 skill 回填每个 micro_scene 的 duration_sec
    └── audio/
        ├── narration_seg_<micro_id>_<block序号>.wav
        └── dialogue_<角色id>_<micro_id>_<block序号>.wav
```

## 流程规范

### Step 1：角色/地点定妆图生成（按需）

扫描全部 `global/characters.json`/`global/locations.json`，找出
`asset_path` 仍为 `null` 的条目（可能是新项目首次跑、也可能是
`novel-scene-detail-planner` 回补新增的实体），对每一项调用
`gen_image_with_text`：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<description_en>" --size 2K --ratio <novel_project.json 里的 aspect_ratio> \
  --save-path <output_dir>/global/assets/character_<id>.png
```

地点同理，落到 `global/assets/location_<id>.png`。生成后**立即回填**
`global/characters.json`/`global/locations.json` 里对应的 `asset_path`
字段。已有 `asset_path` 的条目跳过，不重复生成（除非用户明确要求
重新生成某个角色的定妆图）。

封面图（可选）逻辑同 v1，落到 `global/assets/cover.png`。

### Step 2：按 content_blocks 批量配音

```bash
python .claude/skills/novel-asset-generator/scripts/synthesize_scene_audio.py \
  <output_dir>
```

- 遍历所有 `macro_scene_*/scene_detail.yaml`，对每个 `micro_scene` 的每
  个非空 `content_blocks[*].text` 分别配音：
  - `type: narration` → 用项目默认音色（`novel_project.json.tts.voice`，
    未指定时按引擎各自的默认值）；
  - `type: dialogue` → 读取 `speaker` 对应角色的 `voice_profile`，用
    `voice_mapping.py` 解析出具体音色（edge-tts 内置音色名或 CosyVoice
    说话人名），不同角色自动配不同声音；
- 已存在且非空的音频默认跳过（断点续跑，读取其真实时长参与求和），需要
  强制重新生成全部时加 `--force`；
- 只想处理某一个大场景时加 `--macro-id macro_01`（配合
  `novel-scene-detail-planner` 的回补循环，只重新配新增/修改过的部分，
  不需要全量重跑）；
- 同一个 `micro_scene` 内全部 `content_blocks` 配音完成后，脚本自动把
  它们的真实时长**求和**（多段音频按顺序首尾相接播放，不叠加），回填
  该 `micro_scene` 的 `duration_sec`，直接覆盖写回 `scene_detail.yaml`；
- 终端会打印本次跑下来 `engine_usage`（多少段用了 `cosyvoice`，多少段
  降级到了 `edge-tts`），一次性看清楚降级情况。

**时长超限处理**：跑完后如果某个 `micro_scene.duration_sec > 12`
（`check_assets_and_audio_v2.py` 会报错），**不允许忽略**——回
`novel-scene-detail-planner` 把该小场景的 `content_blocks` 拆成两个
`micro_scene`（分别给新 id），然后只重新跑受影响的这一个大场景：
```bash
python .claude/skills/novel-asset-generator/scripts/synthesize_scene_audio.py \
  <output_dir> --macro-id macro_01
```
低于 4 秒只是 warning（建议与相邻小场景合并，不强制）。

### Step 3：校验交付

```bash
python .claude/skills/novel-asset-generator/scripts/check_assets_and_audio_v2.py \
  <output_dir>
```

检查：角色/地点定妆图完整性（所有被引用的都有 `asset_path`）、每个
`content_block` 是否都有对应音频文件、每个 `micro_scene.duration_sec`
是否已回填且落在 4-12 秒范围内。**退出码非 0 不允许交付给
`novel-scene-video-generator`**，按报错逐条修复（补生成缺失定妆图、回
Step 2 用 `--macro-id`/`--force` 定向重跑），重新跑校验直到通过。

**Agent 需向用户展示的中间内容**：
- 定妆图生成结果（本次新生成了多少个角色/地点，是否有失败需要重试的）；
- 配音引擎使用情况（`engine_usage` 汇总）；
- 校验脚本通过结果 + 各大场景/小场景的总时长。

**产物**：`global/assets/*.png`、各 `macro_scene_*/audio/*.wav`、各
`macro_scene_*/scene_detail.yaml` 全部回填 `duration_sec` 且校验通过。

## 常见问题

同 v1（CosyVoice 降级排查、edge-tts 网络问题、ffmpeg/ffprobe 依赖），
不再重复列出，参见 v1 `SKILL.md`（已归档保留在同目录，仅脚本被 v2
取代，排障方法不变）。

## 已知限制

- `voice_mapping.py` 目前是纯关键词规则映射，同一分类（比如"青年男声"）
  下所有角色会用同一个 edge-tts 音色，无法做到"每个角色独一无二"；
  CosyVoice 零样本克隆理论上可以做到，但依赖参考音频采集入口（本版
  未实现），留作后续版本；
- 音色映射规则只覆盖性别+年龄段两个维度，"清亮/沙哑/沉稳"这类更细的
  音质描述目前不参与音色选择。

## 下一步

完成本 skill 后，进入 `novel-scene-video-generator`，为每个
`micro_scene` 生成视频 clip，并在每个大场景内合成出
`macro_scene_XX.mp4`。
