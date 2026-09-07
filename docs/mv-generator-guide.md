# MV（歌词同步视频）生成指南

本文档介绍 mini_agent 的 MV 生成技能（`mv-generator`），从 mp3 + 歌词到输出歌词同步显示、画面贴合歌词内容的最终 MV（mp4）的全流程。

---

## 概述

`mv-generator` 是一个**编排型技能**：ASR/对齐/合成三个机械步骤由脚本
完成，"歌词内容理解→场景怎么切→画面怎么描述"这类创造性判断由 Agent
根据 `SKILL.md` 中的规范完成，模式与 `comic-4panel` 一致（多步骤 + 每步
产物强制落盘 + 关键节点向用户确认）。

核心流程：

```
ASR 粗识别 → 歌词对齐校正 → 场景规划(需用户确认) → 定妆图生成
→ 分场景视频生成 → 拼接+烧字幕+混音 → 最终 mv.mp4
```

**特点**：
- 歌词时间戳来自本地 `faster-whisper` 识别 + 与标准歌词的字符级对齐校正，
  离线运行，不依赖云端 ASR
- 字幕以 hardsub 形式烧录进画面，不依赖播放器对软字幕的支持
- 复用项目已有的 `gen_image_with_text`/`gen_video_with_text` 两个 skill
  做素材与视频生成，通过统一使用同一张"定妆图"作为 reference 尽量减少
  多个片段间的人物形象漂移（但不能完全消除，这是已知限制）

---

## 前置条件

| 依赖 | 用途 | 是否必须 |
|---|---|---|
| `gen_image_with_text` skill | 定妆图/场景图生成 | ✅ 必须 |
| `gen_video_with_text` skill | 分场景视频生成 | ✅ 必须 |
| `AGNES_API_KEY` 环境变量 | 图片/视频生成 API 密钥 | ✅ 必须 |
| `faster-whisper`（Python 包） | 本地语音识别 | ✅ 必须（Step 1） |
| `ffmpeg`（系统命令） | 拼接/烧字幕/混音 | ✅ 必须（Step 6） |

### 设置 API 密钥

Windows PowerShell：
```powershell
$env:AGNES_API_KEY = "sk-your-api-key-here"
```

### 安装 faster-whisper

```bash
pip install faster-whisper
```

首次运行 `asr_transcribe.py` 时会自动下载识别模型（默认 `small`），
根据网络情况可能需要几分钟；中文歌曲若识别效果不理想，可以用
`--model-size medium` 或更大的规格重试。

### 安装 ffmpeg（Windows）

```powershell
winget install ffmpeg
```

或从 [ffmpeg 官网](https://ffmpeg.org/download.html) 下载后手动加入系统
PATH。**安装完成后请新开一个终端窗口**，确保 PATH 生效，然后用
`ffmpeg -version` 确认安装成功。

---

## 使用方式

在对话中提供 mp3 文件路径和歌词文本（逐行，与演唱顺序一致），说
"帮我生成这首歌的MV"（或类似表述）即可触发。Agent 会依次：

1. 检查依赖是否齐全（`AGNES_API_KEY`/`faster-whisper`/`ffmpeg`）
2. 运行 ASR 识别 + 歌词对齐，产出逐句时间戳
3. 规划场景分镜（`scene_plan.yaml`），**向你展示并等待确认**
4. 生成定妆图、分场景视频
5. 最终合成 `mv.mp4` 并告知产物路径

### 手动分步调用（调试/自定义时使用）

**Step 1 语音识别：**
```bash
python .claude/skills/mv-generator/scripts/asr_transcribe.py \
  song.mp3 --model-size small --language zh \
  --save-path mv_output/asr_raw.json
```

**Step 2 歌词对齐校正：**
```bash
python .claude/skills/mv-generator/scripts/align_lyrics.py \
  mv_output/asr_raw.json lyrics.txt \
  --save-path mv_output/lyrics_timed.json \
  --save-srt mv_output/lyrics.srt
```

**Step 6 最终合成**（Step 3-5 需要 Agent 交互完成场景规划与素材/视频生成，
无法脱离对话单独跑）：
```bash
python .claude/skills/mv-generator/scripts/compose_mv.py \
  --clips-dir mv_output/clips \
  --lyrics-timed mv_output/lyrics_timed.json \
  --audio song.mp3 \
  --output mv_output/mv.mp4
```

---

## 输出结构

```
mv_output/歌曲名_20260907/
├── asr_raw.json          # ASR 原始识别结果（词/行级时间戳）
├── lyrics_timed.json     # 对齐校正后的逐句歌词时间戳
├── lyrics.srt            # 人工核对用字幕文件
├── scene_plan.yaml        # 场景规划（含每段画面 prompt、复用的定妆图）
├── assets/                # 角色/场景定妆图
├── clips/                 # 分场景视频片段（scene_01.mp4, scene_02.mp4, ...）
└── mv.mp4                 # 最终 MV
```

---

## 已知限制

1. **单场景片段最长 12 秒**：`gen_video_with_text` 底层 API 硬限制，一首
   3-4 分钟的歌通常会拆成 15-25 个场景，对应 15-25 次视频生成调用，
   耗时和调用成本相对可观。
2. **人物一致性非像素级**：`reference` 模式基于参考图片做"风格接近"的
   生成，多个片段之间同一角色的形象可能有细微漂移，无法做到同一
   seed 级别的完全一致。
3. **ASR 识别准确率受背景音乐影响**：本步骤主要提供时间戳锚点，最终
   歌词文字以用户提供的标准歌词为准（Step 2 对齐校正会覆盖 ASR 的
   文字识别错误）。
4. **视频与音频总时长的匹配依赖场景规划的准确性**：`compose_mv.py`
   在两者时长不一致时以较短者为准做兜底截断，正常情况下 Step 3 的
   场景时长规划应已让总时长贴合原曲时长。

---

## 常见问题排查

| 现象 | 可能原因 | 处理方式 |
|---|---|---|
| `asr_transcribe.py` 报错找不到 `faster_whisper` | 未安装依赖 | `pip install faster-whisper` |
| `compose_mv.py` 提示未检测到 ffmpeg | ffmpeg 未安装或不在 PATH | 按上文安装步骤操作，安装后新开终端 |
| 对齐结果里某几句时间戳挤在一起 | ASR 识别质量不佳 | 换更大的 `--model-size` 重新识别 |
| 某个场景生成视频调用报错时长超限 | 场景规划阶段拆分不到位 | 检查 `scene_plan.yaml` 里对应场景是否 > 12 秒，手动再拆分 |
| 最终 MV 总时长与原曲差异明显 | 各场景 `--seconds` 取整累积误差 | 检查各场景实际生成时长之和，必要时微调场景时长分配 |

---

## 与其他 skill 的关系

```
mv-generator (编排型 skill)
  ├── 依赖 gen_image_with_text  （生成定妆图/场景图）
  ├── 依赖 gen_video_with_text  （生成分场景视频片段）
  └── 自带脚本：
        ├── asr_transcribe.py   （本地语音识别）
        ├── align_lyrics.py     （歌词对齐校正）
        └── compose_mv.py       （ffmpeg 拼接/烧字幕/混音）
```
