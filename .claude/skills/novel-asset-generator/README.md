# novel-asset-generator

"小说转视频"四段流程的第二步：角色/地点定妆图 + 旁白配音（TTS），并据此
生成正式版 `scene_plan.yaml`（含精确时长）。详细流程规范见 `SKILL.md`，
整体架构/文件契约见 `next_doc/novel_video_generator_plan.md`。

## 依赖

**依赖 skill**：`gen_image_with_text`（必须，生成定妆图）。

**Python 依赖**：

```bash
pip install pyyaml
pip install edge-tts --break-system-packages   # TTS 兜底方案，几乎总能装上
```

**可选（推荐）——本地 CosyVoice**：需要专用 conda 环境（建议
`novel_tts_env`，避免和 `mv-generator` 的 `mv_env` 混装），安装
`cosyvoice` 及其依赖（`torch`/`torchaudio` 等）+ 下载预训练模型权重到
`pretrained_models/CosyVoice-300M`（或用 `COSYVOICE_MODEL_DIR` 环境变量
指定其它路径）。**没装/装不上不阻塞流程**——`scripts/tts_engine.py` 会
自动降级到 edge-tts，仅在终端打印一行降级提示。

**外部依赖**：
- `ffmpeg`/`ffprobe`（系统命令，读取音频真实时长）；
- `AGNES_API_KEY` 环境变量（`gen_image_with_text` 需要）。

## 输入 / 输出

| | 内容 |
|---|---|
| 输入 | `novel-scene-planner` 产出的整个 `output_dir`（`characters.json`/`locations.json`/`narration_script.yaml`/`novel_project.json`） |
| 输出 | `assets/character_*.png`/`location_*.png`/`cover.png`（可选）、`audio/segment_*.wav`、正式版 `scene_plan.yaml`、回填后的 `characters.json`/`locations.json` |

## 脚本

- `scripts/tts_engine.py`：TTS 引擎封装（CosyVoice 优先 + edge-tts 降级），
  被 `synthesize_narration.py` 调用，一般不需要单独执行；
- `scripts/synthesize_narration.py`：批量给旁白配音，生成正式版
  `scene_plan.yaml`；
  ```bash
  python .claude/skills/novel-asset-generator/scripts/synthesize_narration.py <output_dir> \
    [--segment-id seg_01 seg_02 ...] [--force]
  ```
- `scripts/check_assets_and_audio.py`：校验定妆图/配音是否齐全、时长
  是否在 4–12 秒范围内，`SKILL.md` Step 3 结束必须跑：
  ```bash
  python .claude/skills/novel-asset-generator/scripts/check_assets_and_audio.py <output_dir>
  ```

退出码 0 才能交付给 `novel-scene-video-generator`；非 0 时按 stdout
结构化清单回本 skill 内部相应环节修复，重跑校验直到通过。
