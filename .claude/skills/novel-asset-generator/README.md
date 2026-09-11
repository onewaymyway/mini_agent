# novel-asset-generator (v2)

"小说转视频 v2"六段流程第 4 步：角色/地点定妆图（按需/增量）+ 按
`content_blocks` 分别给旁白/角色对话配音（角色差异化音色）。详细流程
规范见 `SKILL.md`，整体架构/文件契约见
`next_doc/novel_video_generator_plan_v2.md`。

## 依赖

**依赖 skill**：`gen_image_with_text`（必须，生成定妆图）。

**Python 依赖**：

```bash
pip install pyyaml
pip install edge-tts --break-system-packages   # TTS 兜底方案，几乎总能装上
```

**可选（推荐）——本地 CosyVoice**：同 v1，见 `SKILL.md` 外部依赖说明。

**外部依赖**：`ffmpeg`/`ffprobe`、`AGNES_API_KEY` 环境变量。

## 输入 / 输出

| | 内容 |
|---|---|
| 输入 | `global/characters.json`/`locations.json` + 全部 `macro_scene_*/scene_detail.yaml`（`novel-scene-detail-planner` 产出，需全部 `status:planned`） |
| 输出 | `global/assets/character_*.png`/`location_*.png`（回填 `asset_path`）、各 `macro_scene_*/audio/*.wav`、各 `macro_scene_*/scene_detail.yaml` 回填 `duration_sec` |

## 脚本（v2 新增）

- `scripts/voice_mapping.py`：`voice_profile` 描述 → edge-tts 音色名 /
  CosyVoice 说话人名的关键词映射；
- `scripts/synthesize_scene_audio.py`：批量按 `content_blocks` 配音，回填
  `duration_sec`：
  ```bash
  python .claude/skills/novel-asset-generator/scripts/synthesize_scene_audio.py \
    <output_dir> [--macro-id macro_01] [--force] [--engine cosyvoice|edge-tts]
  ```
- `scripts/check_assets_and_audio_v2.py`：校验素材+配音是否齐全、
  `duration_sec` 是否在 4-12 秒范围内：
  ```bash
  python .claude/skills/novel-asset-generator/scripts/check_assets_and_audio_v2.py <output_dir>
  ```

`scripts/tts_engine.py` 被 v2 复用不变。`scripts/check_assets_and_audio.py`/
`scripts/synthesize_narration.py` 是 v1 遗留脚本，只服务旧的
`narration_script.yaml`/`scene_plan.yaml` 契约，v2 流程不调用，保留仅供
历史参考。

退出码 0 才能交付给 `novel-scene-video-generator`；非 0 时按 stdout
结构化清单回本 skill 内部相应环节修复（补素材 / `--macro-id`/`--force`
定向重跑），重跑校验直到通过。

## 与相邻 skill 的交接条件

- 上游：`novel-scene-detail-planner`，要求全部大场景 `status:planned`；
- 下游：`novel-scene-video-generator`，要求 `check_assets_and_audio_v2.py`
  退出码为 0。
