# 小说转视频（Novel → Video）流程测试指南

## 概述

测试 `novel-scene-planner` → `novel-asset-generator` →
`novel-scene-video-generator` → `novel-video-composer` 四个 skill
串联起来的完整"小说转视频"流程，验证：

- 每个 skill 的产物文件是否按方案文档
  （`next_doc/novel_video_generator_plan.md`）约定的格式落盘；
- 每个 skill 自带的校验脚本能否正确判断通过/不通过；
- 断点续跑、定向重跑（`--scene-id`/`--segment-id`）等机制是否生效；
- 最终 `video.mp4` 的时长/分辨率/字幕是否符合预期。

**测试输入**：`test_cases/inputs/novel_video_sample_novel.txt`
（约 700 字的武侠短篇《青石客栈的一夜》，包含 2 个主要角色——林然、
沈婉，1 个反复出现的地点——青石客栈/将军府，情节包含"深夜探查+打斗+
真相揭露"，足够验证角色/地点抽取、取舍浓缩、动作场景 prompt 生成等
各环节，但篇幅和目标时长都刻意压得很短（60 秒），方便快速跑通全流程
不需要长时间等待）。

**用户测试输入**：`test_cases/novel_video_generator_test.txt`
（直接把这个文件的内容作为一条消息发给 Agent，即可触发完整四段流程）。

---

## 前置条件

1. 已完成 `next_doc/novel_video_generator_plan.md` 里 4 个 skill 的
   实现（`novel-scene-planner`/`novel-asset-generator`/
   `novel-scene-video-generator`/`novel-video-composer` 四个目录下
   `SKILL.md`/`scripts/`/`README.md` 均已就位）；
2. Python 依赖：`pip install pyyaml pillow edge-tts --break-system-packages`
   （本地 CosyVoice 为可选依赖，见
   `.claude/skills/novel-asset-generator/README.md`）；
3. 系统依赖：`ffmpeg`/`ffprobe` 已安装并在 PATH 中（或按
   `compose_novel_video.py`/`generate_scene_videos.py` 里的约定路径
   放好）；
4. 环境变量：`AGNES_API_KEY`（或 `AGNES_API_KEYS`），
   `gen_image_with_text`/`gen_video_with_text` 两个 skill 需要；
5. （可选）本地 CosyVoice 环境 `novel_tts_env` 已配置——没配置也不
   影响测试，`tts_engine.py` 会自动降级到 edge-tts，只是终端会打印
   一行降级提示，测试时留意这行提示是否出现、是否符合预期。

---

## Stage 1: novel-scene-planner（场景拆分 + 角色提取）

### 触发方式

把 `test_cases/novel_video_generator_test.txt` 的内容发给 Agent（或
直接摘要复述其要求），Agent 应识别出这是"小说转视频"需求，进入
`novel-scene-planner`。

### 预期产物

`test_result/novel_video/qingshi_inn_test/`：

| 文件 | 预期内容要点 |
|---|---|
| `novel_project.json` | `target_duration_sec: 60`、`aspect_ratio: "16:9"`、`orientation: "landscape"`、`bgm_enabled: false`、`tts.engine: "cosyvoice"`、`tts.fallback: "edge-tts"`、`art_style` 非空字符串 |
| `characters.json` | 至少识别出 `林然`、`沈婉` 两个角色（id 任意，`names` 数组里能找到这两个名字），`asset_path`/`face_reference_id` 均为 `null` |
| `locations.json` | 至少识别出 1 个地点（"青石客栈"和/或"将军府"，是否合并为一条视 Agent 理解而定，只要出现即可），`asset_path` 为 `null` |
| `narration_script.yaml` | 若干 `segments`，每段 `text` 是改写后的口播文案（不是原文逐句照抄），`uses_characters`/`uses_locations` 引用上面两个文件里存在的 id |

### 校验命令

```bash
python .claude/skills/novel-scene-planner/scripts/check_narration_draft.py \
  test_result/novel_video/qingshi_inn_test
```

**预期结果**：退出码 `0`，stdout JSON 里 `"ok": true`。

- 若因为篇幅估算超出 `target_duration_sec`（60 秒）的 ±30% 区间报错
  （60 秒对应约 45 字预算，一段 700 字的短篇必然需要大幅浓缩），属于
  **预期内的正常报错**——用于验证"取舍浓缩不足"这一校验路径确实生效，
  Agent 应回 Step 3 重新压缩旁白文案后再跑一次本命令，直到通过。

---

## Stage 2: novel-asset-generator（定妆图 + 旁白配音）

### 预期产物

在同一 `output_dir` 下新增：

| 文件/目录 | 预期内容要点 |
|---|---|
| `assets/character_*.png` | 每个 `characters.json` 条目各一张，非空文件 |
| `assets/location_*.png` | 每个 `locations.json` 条目各一张，非空文件 |
| `audio/segment_*.wav` | 每个 `narration_script.yaml` segment 各一条，非空文件 |
| `scene_plan.yaml`（正式版） | 每个 scene 含 `duration_sec`（4~12 秒范围内）、`narration_audio`、`text`、`prompt_en`、`video_mode`、`status: pending` |
| `characters.json`/`locations.json`（回填后） | `asset_path` 均已非 `null` |

### 校验命令

```bash
python .claude/skills/novel-asset-generator/scripts/check_assets_and_audio.py \
  test_result/novel_video/qingshi_inn_test
```

**预期结果**：退出码 `0`。

### 需要观察的关键日志

- 终端应出现 TTS 引擎使用情况的提示（`engine_used: cosyvoice` 或
  `engine_used: edge-tts`）；未配置本地 CosyVoice 时**预期看到明确的
  降级提示**，这是本 skill 的核心容错路径，测试时应确认提示信息
  清晰可读，不是静默降级；
- 若某个 segment 音频时长超过 12 秒或低于 4 秒，脚本只报告不自动
  处理——预期能在 stdout 看到结构化提示，Agent 需要回
  `narration_script.yaml` 调整文案后用 `--segment-id` 定向重跑
  `synthesize_narration.py`，验证局部重跑不影响其它已成功的 segment。

---

## Stage 3: novel-scene-video-generator（分场景视频生成）

### 预期产物

| 文件 | 预期内容要点 |
|---|---|
| `clips/<scene_id>.mp4` | 每个 scene 各一个，非空文件 |
| `scene_plan.yaml` | 每个 scene 的 `status` 回写为 `done`（或个别持续性失败为 `failed`，需人工介入） |

### 校验命令

```bash
python .claude/skills/novel-scene-video-generator/scripts/check_clips.py \
  test_result/novel_video/qingshi_inn_test
```

**预期结果**：退出码 `0`。若有场景反复重试仍失败，用
`--scene-id <失败的id> --force` 定向重跑，**验证不需要重跑其它已成功
场景**——检查已成功场景的 `clips/*.mp4` 文件修改时间应保持不变。

⚠️ 执行 `generate_scene_videos.py` 时 `timeout` 参数应传 `-1`。

---

## Stage 4: novel-video-composer（最终合成）

### 命令

```bash
python .claude/skills/novel-video-composer/scripts/compose_novel_video.py \
  test_result/novel_video/qingshi_inn_test
```

### 预期产物与校验点

`test_result/novel_video/qingshi_inn_test/video.mp4`，脚本末尾会自动
打印如下结构的校验 JSON（`"ok": true` 视为通过）：

```json
{
  "ok": true,
  "errors": [],
  "warnings": [],
  "summary": {
    "duration_sec": 60.0,
    "expected_duration_sec": 60.0,
    "bitrate_bps": "...",
    "resolution": "1280x720",
    "file_size_mb": ...
  }
}
```

具体核对项：

| 校验项 | 预期结果 |
|---|---|
| 退出码 | `0` |
| `summary.duration_sec` | 与 `summary.expected_duration_sec`（即全部旁白拼接后的真实总时长）之差 ≤ 2 秒 |
| `summary.resolution` | `1280x720`（对应 `novel_project.json` 里 `aspect_ratio: "16:9"`） |
| `summary.bitrate_bps` | 换算后 > 1 Mbps（`warnings` 里不应出现"比特率偏低"提示） |
| 字幕 | 用播放器打开 `video.mp4`，逐场景应能看到 `scene_plan.yaml` 里对应 `text` 字段的中文字幕，且随场景切换同步出现/消失，没有断续闪烁 |
| 封面（若 `assets/cover.png` 存在） | 视频开头 1~3 秒应是封面图的缓慢推近效果，之后无缝接回第一个场景画面，总时长不受影响 |

---

## 端到端通过标准

四个 Stage 的校验脚本全部退出码为 `0`，且最终 `video.mp4` 满足上表
全部核对项，即视为本次"小说转视频"全流程测试通过。

## 离线冒烟测试（不需要 API Key，仅验证 novel-video-composer 环境）

如果暂时没有 `AGNES_API_KEY` 或不想为了验证环境跑完整流程（Stage
1-3 涉及真实的图片/视频生成 API 调用，耗时且耗费额度），可以先用
纯本地合成素材跑一次 `novel-video-composer` 的离线冒烟测试，只验证
`ffmpeg`/`pillow`/字体等本地环境是否配置正确：

```bash
python test_cases/novel_video_composer_smoke_test.py
```

该脚本会用 `ffmpeg testsrc`/`anullsrc` 生成 2 个假场景（含假 clip、
假旁白 wav、假封面图），自动跑一遍 `compose_novel_video.py` 的完整
合成流程（含 `--allow-missing-clips` 分支的抽样验证），并断言最终
`video.mp4` 的时长/分辨率/退出码符合预期。**通过本脚本只能说明本地
合成环境没问题，不能替代 Stage 1-4 的真实端到端测试**——真实测试
仍需要走一遍上面四个 Stage、使用真实的 `AGNES_API_KEY`。

---

## 常见问题排查索引

| 现象 | 可能原因 / 排查方向 |
|---|---|
| Stage 1 校验报"篇幅超出预算" | 属预期内报错（见 Stage 1 说明），回 Step 3 重新浓缩 |
| Stage 2 一直用 edge-tts，从未尝试 CosyVoice | 本地未配置 `novel_tts_env`，属预期行为，不算失败；如果需要验证 CosyVoice 路径，需先按 `.claude/skills/novel-asset-generator/README.md` 配好本地环境 |
| Stage 3 某场景反复失败 | 参考 `novel-scene-video-generator/SKILL.md` 「常见问题」：prompt 审核、参考图缺失、账号限流 |
| Stage 4 校验 `"ok": false`，分辨率不对 | 检查 `novel_project.json.aspect_ratio` 是否被误改；或本地 ffmpeg 版本对 `pad` 滤镜支持异常 |
| Stage 4 字幕方框/乱码 | 非 Windows 环境需要用 `--font-path` 显式指定本地可用中文字体（见下方"跨平台注意事项"） |

## 跨平台注意事项

`compose_novel_video.py` 默认字体路径是 Windows 的
`C:\Windows\Fonts\msyh.ttc`；非 Windows 环境（比如 CI、Linux 测试机）
需要额外传 `--font-path` 指向本地实际可用的中文字体，例如：

```bash
python .claude/skills/novel-video-composer/scripts/compose_novel_video.py \
  test_result/novel_video/qingshi_inn_test \
  --font-path /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc
```

`generate_scene_videos.py`/`compose_novel_video.py` 里探测 `ffmpeg`/
`ffprobe` 路径时也优先尝试系统 PATH（通过 `imageio_ffmpeg` 包，若已
安装），找不到才退回 Windows 专用候选路径，Linux/macOS 环境建议装好
`imageio_ffmpeg` 或确保 `ffmpeg`/`ffprobe` 本身在 PATH 中。
