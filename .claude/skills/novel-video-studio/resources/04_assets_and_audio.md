# 阶段4：素材 + 差异化配音生成

**依赖 skill**：`gen_image_with_text`（定妆图生成，必须）。
**依赖脚本**（本 skill `scripts/` 下）：
- `voice_mapping.py`：把角色 `voice_profile` 文字描述映射到具体 TTS
  音色（edge-tts 内置音色名 / CosyVoice 说话人名，规则式关键词匹配）；
- `synthesize_scene_audio.py`：批量给全部（或 `--macro-id` 指定的）
  大场景下所有小场景的 `content_blocks` 配音，回填 `duration_sec`；
- `check_assets_and_audio_v2.py`：校验素材+配音是否就绪，必须跑，不
  通过不能进入阶段5。

**外部依赖**：CosyVoice（可选，配 `novel_tts_env`）、edge-tts（兜底）、
ffmpeg/ffprobe、`AGNES_API_KEY`（定妆图）。API 失败处理见
`error_handling.md`。

产物路径：
```
novel_output/小说名_20260911/
├── global/
│   ├── characters.json / locations.json   # 本阶段回填 asset_path
│   └── assets/character_*.png / location_*.png
└── macro_scene_XX/
    ├── scene_detail.yaml   # 本阶段回填每个 micro_scene 的 duration_sec
    └── audio/
        ├── narration_seg_<micro_id>_<block序号>.wav
        └── dialogue_<角色id>_<micro_id>_<block序号>.wav
```

本阶段支持**按需触发**：不要求全量重跑，可以只给新增的角色/地点补
生成素材（配合阶段3的引用回补循环），也可以用 `--macro-id` 只给某个
大场景配音。

## Step 1：角色/地点定妆图生成（按需）

扫描全部 `global/characters.json`/`locations.json`，找出 `asset_path`
仍为 `null` 的条目（新项目首次跑，或阶段3回补的新增实体），对每一项
调用：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<description_en>" --size 2K --ratio <novel_project.json 里的 aspect_ratio> \
  --save-path <output_dir>/global/assets/character_<id>.png
```

地点同理，落到 `global/assets/location_<id>.png`。生成后**立即回填**
`global/characters.json`/`locations.json` 里对应的 `asset_path` 字段。
已有 `asset_path` 的条目跳过，除非用户明确要求重新生成某个角色定妆图
（此时用 `invalidate.py --global-entity <id>` 先清空再重新生成，见
`revision_and_rollback.md`）。

封面图（可选）逻辑同角色定妆图，落到 `global/assets/cover.png`，供
阶段6可选叠加。

## Step 2：按 content_blocks 批量配音

```bash
python .claude/skills/novel-video-studio/scripts/synthesize_scene_audio.py \
  <output_dir>
```

- 遍历所有 `macro_scene_*/scene_detail.yaml`，对每个 `micro_scene` 的
  每个非空 `content_blocks[*].text` 分别配音：
  - `narration` → 项目默认音色（`novel_project.json.tts.voice`）；
  - `dialogue` → 读取 `speaker` 对应角色的 `voice_profile`，用
    `voice_mapping.py` 解析出具体音色，不同角色自动配不同声音；
- 已存在且非空的音频默认跳过（断点续跑，读取真实时长参与求和），需要
  强制重新生成全部时加 `--force`；
- 只想处理某一个大场景时加 `--macro-id macro_01`（配合阶段3的回补
  循环，只重新配新增/修改过的部分，不需要全量重跑）；
- 同一个 `micro_scene` 内全部 `content_blocks` 配音完成后，脚本自动
  把真实时长**求和**（多段音频首尾相接，不叠加），回填该 `micro_scene`
  的 `duration_sec`，覆盖写回 `scene_detail.yaml`；
- 终端会打印 `engine_usage`（多少段用了 `cosyvoice`，多少段降级到了
  `edge-tts`），一次性看清降级情况。

**时长超限处理**：跑完后若某个 `micro_scene.duration_sec > 12`
（`check_assets_and_audio_v2.py` 会报错），**不允许忽略**——回阶段3
把该小场景的 `content_blocks` 拆成两个 `micro_scene`（分别给新 id），
用 `invalidate.py --macro-id macro_01 --level detail` 清理旧状态后，
只重新跑受影响的这一个大场景。低于 4 秒只是 warning（建议与相邻小
场景合并，不强制）。

## Step 3：校验交付

```bash
python .claude/skills/novel-video-studio/scripts/check_assets_and_audio_v2.py \
  <output_dir>
```

检查：角色/地点定妆图完整性（所有被引用的都有 `asset_path`）、每个
`content_block` 是否都有对应音频文件、每个 `micro_scene.duration_sec`
是否已回填且落在 4-12 秒范围内。**退出码非 0 不允许交付阶段5**，按
报错逐条修复（补生成缺失定妆图、回 Step 2 用 `--macro-id`/`--force`
定向重跑），重新跑校验直到通过。

**向用户展示**：定妆图生成结果（本次新生成多少角色/地点，是否有失败
需要重试）、配音引擎使用情况（`engine_usage` 汇总）、校验通过结果 +
各大场景/小场景总时长。

## 已知限制

- `voice_mapping.py` 目前是纯关键词规则映射，同一分类（如"青年男声"）
  下所有角色会用同一个 edge-tts 音色，无法做到"每个角色独一无二"；
  CosyVoice 零样本克隆理论上可以，但依赖参考音频采集入口（未实现）；
- 音色映射规则只覆盖性别+年龄段两个维度，"清亮/沙哑/沉稳"这类更细的
  音质描述暂不参与选择。

## 下一步

进入阶段5，为每个 `micro_scene` 生成视频 clip，并在每个大场景内合成
出 `macro_scene_XX.mp4`。
