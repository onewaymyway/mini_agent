---
name: mv-generator
description: 输入一首歌的 mp3 音频 + 歌词文本，生成歌词同步显示、画面贴合歌词内容的 MV 视频。当用户说"生成MV"、"做一个歌词视频"、"给这首歌配个视频"、"歌词同步视频"时使用。
triggers: MV生成, 生成MV, 歌词视频, 歌词同步, mv video, lyric video, 音乐视频
---

# MV（歌词同步视频）生成 (MV Generator)

## 概述

本 skill 用于把一首歌（mp3 + 歌词文本）生成为一支歌词与画面同步、画面
内容贴合歌词语义的 MV。整体流程：

```
ASR 粗识别 → 歌词对齐校正 → 场景规划(需用户确认) → 定妆图生成
→ 分场景视频生成 → 拼接+烧字幕+混音 → 最终 mv.mp4
```

**依赖 skill**：
- `gen_image_with_text`（必须，生成角色/场景定妆图）
- `gen_video_with_text`（必须，生成分场景视频片段）

**依赖脚本**（本 skill 目录下）：
- `scripts/asr_transcribe.py`：本地语音识别（faster-whisper）
- `scripts/compose_mv.py`：ffmpeg 最终合成

**外部依赖**：
- `faster-whisper`（Python 包，未安装时先提示用户 `pip install faster-whisper`）
- `ffmpeg`（系统命令，未安装时先提示用户按 `docs/mv-generator-guide.md`
  里的说明安装；Windows 推荐 `winget install ffmpeg`）
- `AGNES_API_KEY` 环境变量（`gen_image_with_text`/`gen_video_with_text` 都需要）

## ⚠️ 产物文件强制保存规范（最高优先级，与 comic-4panel 一致）

**每个步骤完成后，必须立即将产物写入文件，不得仅停留在对话输出中。**

1. 先生成内容，立刻写文件——不要等用户确认后再写，确认前就要写入
2. 文件写入是步骤完成的标志——没写入文件 = 步骤未完成，不能进入下一步
3. 每次重新生成都要覆盖/追加文件，确保产物文件始终是最新状态
4. 写入失败必须报告错误并停止，不能跳过产物保存

推荐的产物目录结构（`output_dir` 默认为 `./mv_output/{歌曲名}_{timestamp}`）：

```
mv_output/歌曲名_20260907/
├── asr_raw.json          # Step 1 产物
├── lyrics_timed.json     # Step 2 产物
├── lyrics.srt            # Step 2 产物（供人工核对，最终合成时会重新生成）
├── scene_plan.yaml        # Step 3 产物
├── assets/                # Step 4 产物：角色/场景定妆图
│   ├── character_A.png
│   └── scene_park.png
├── clips/                 # Step 5 产物：分场景视频片段（文件名需可排序）
│   ├── scene_01.mp4
│   ├── scene_02.mp4
│   └── ...
└── mv.mp4                 # Step 6/7 最终产物
```

## 前置环境检查（进入 Step 1 之前先做）

1. 检查 `AGNES_API_KEY` 环境变量是否已设置（`gen_image_with_text`/
   `gen_video_with_text` 都依赖它），未设置则提示用户设置后再继续。
2. 检查 `faster-whisper` 是否已安装（可以直接尝试 `python -c "import faster_whisper"`），
   未安装则提示用户 `pip install faster-whisper` 并等待确认后再继续。
3. 检查 `ffmpeg` 是否在 PATH 中（`ffmpeg -version`），不可用则提示用户
   按 `docs/mv-generator-guide.md` 安装，Step 6 之前必须解决，前面几步
   不依赖 ffmpeg 可以先做。
4. 确认输入：mp3 文件路径是否存在、歌词文本是否已提供（逐行纯文本，
   与音频中演唱顺序一致）。歌词行数、音频时长会影响后续场景数量的
   预估，可以先做一个粗略播报，让用户对成本（15–25 次视频生成调用）
   有心理预期。

## 流程规范

### Step 1: ASR 粗识别

```bash
python .claude/skills/mv-generator/scripts/asr_transcribe.py \
  <mp3路径> --model-size small --language zh \
  --save-path <output_dir>/asr_raw.json
```

- 中文歌曲背景音乐较吵时，识别准确率可能一般，这是预期内的，因为
  下一步会用标准歌词校正文字，本步骤主要提供"大致的时间戳锚点"。
- 若识别耗时较长（大文件 + cpu 模式），提前告知用户预计耗时。

**产物**：`asr_raw.json`（强制落盘）

### Step 2: 歌词对齐校正（LLM直接处理）

**不要使用对齐脚本**，而是让Agent直接使用LLM能力进行歌词对齐。ASR结果
往往有很多错别字，脚本很难处理，但LLM可以通过分析相近读音、语义上下文
来准确对齐。

**执行步骤**：

1. 先把用户提供的歌词文本保存为 `<output_dir>/lyrics.txt`（逐行一句，
   去掉用户输入里可能带的行号/装饰符号）

2. 读取 `asr_raw.json` 和 `lyrics.txt`，由Agent直接进行分析对齐：
   - 读取ASR结果（包含时间戳和识别文字）
   - 读取标准歌词文本
   - **分析每句ASR识别结果与标准歌词的对应关系**，考虑：
     * 同音字/近音字的映射（如"爱"→"碍"、"在"→"再"）
     * 漏字/多字的修正
     * 断句位置的调整
   - 生成带时间戳的歌词对齐结果

3. 将对齐结果保存为 `lyrics_timed.json`（格式见下方）

4. 根据 `lyrics_timed.json` 生成 `lyrics.srt` 供人工核对

**Agent需输出的中间内容**（展示给用户）：
- 说明对齐逻辑（如何处理了ASR错别字）
- 显示关键几行的时间戳对齐结果供确认
- 如有明显的匹配困难或歧义，向用户说明

**产物**：`lyrics_timed.json`、`lyrics.srt`（强制落盘）

**`lyrics_timed.json` 格式示例**：
```json
[
  {"line": 0, "text": "歌词第一句", "start": 0.0, "end": 8.2},
  {"line": 1, "text": "歌词第二句", "start": 8.2, "end": 14.5}
]
```

### Step 3: 场景规划（创造性步骤，需要 Agent 判断）

这是整个流程里唯一需要"创造性理解歌词内容"的步骤，不能用固定脚本，
必须由 Agent 结合歌词语义、情绪、叙事逻辑来完成：

1. 通读 `lyrics_timed.json` 里的全部歌词，理解整首歌的主题、情绪基调、
   有没有叙事线索（比如"回忆—现实—展望"的结构）。
2. 把歌词行分组成"场景段落"：
   - 每个场景段落对应一个或多个连续歌词行
   - **每个场景段落的时长（对应歌词行的 end - start）必须 ≥ 4 秒且 ≤ 12 秒**
     （`gen_video_with_text` 单 clip 硬限制），超过 12 秒的段落要再拆分，
     不足 4 秒的段落建议合并到相邻场景
   - 全曲所有场景时长之和应等于（或接近）音频总时长，避免最终视频过短或过长
   - 若某首歌有多段重复的歌词（如副歌重复），对应的视频片段可以复用同一
     个 clip，节省 `gen_video` API 调用次数
   - 情绪/画面内容有明显转折的地方，即使歌词行时长很短，也可以单独
     成一个场景段落，让画面切换贴合歌词节奏
3. 识别出全曲中会反复出现的角色/场景（比如"主唱形象""某个固定场景"），
   为它们规划"定妆图"，后续所有相关场景片段都以该定妆图作为
   `gen_video_with_text` 的 reference 输入，尽量减少人物形象漂移
   （但要向用户说明：这只能减少、不能完全消除漂移，这是已知限制）。
4. 为每个场景段落写画面描述 prompt（建议英文，效果更好），描述要包含：
   镜头/构图、场景环境、人物动作与情绪、光线氛围，并注明引用哪个定妆图。

产出格式示例（`scene_plan.yaml`）：

```yaml
song_meta:
  duration: 210.5
  total_scenes: 18

recurring_assets:
  - id: character_A
    description_zh: "主唱形象：黑色长发，白色连衣裙，忧郁气质"
    description_en: "Female singer, long black hair, white dress, melancholic mood"
    asset_path: assets/character_A.png   # Step 4 生成后回填

scenes:
  - id: scene_01
    lyric_lines: [0, 1]          # 对应 lyrics_timed.json 里的行号
    start: 0.0
    end: 8.2
    uses_assets: [character_A]
    prompt_en: "Female singer standing alone on a rainy street at night, neon lights reflecting on wet pavement, cinematic wide shot, melancholic atmosphere"
    video_mode: reference         # reference / keyframe / text
  - id: scene_02
    lyric_lines: [2]
    start: 8.2
    end: 14.5
    uses_assets: [character_A]
    prompt_en: "..."
    video_mode: reference
    split_note: "原歌词行跨度 9.8s，超过 12s 上限，已拆成 scene_02a/scene_02b"
```

**必须做的事**：Step 3 完成后立即写入 `scene_plan.yaml`，然后展示给
用户确认（可以摘要展示场景数量、总时长核对、关键角色定妆图规划），
**等用户确认或提出修改意见后再进入 Step 4**，这是本流程里唯一的强制
确认点（仿 comic-4panel 在关键节点向用户确认的做法）。

### Step 4: 定妆图生成

对 `scene_plan.yaml` 里 `recurring_assets` 列出的每个角色/场景，调用
`gen_image_with_text`：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<description_en>" --size 2K --ratio 16:9 \
  --save-path <output_dir>/assets/<asset_id>.png
```

- 复杂场景（比如需要固定角色出现在不同环境里）可以用 `edit` 多图合成，
  参考 `gen_image_with_text` 的 SKILL.md。
- 生成后立即回填 `scene_plan.yaml` 里对应 `asset_path` 字段。

**产物**：`assets/*.png`（强制落盘），并同步更新 `scene_plan.yaml`

### Step 5: 分场景视频生成

**重要：必须串行生成，不可并行。** 逐个场景调用 `gen_video_with_text`，
等待上一个场景完成后再生成下一个，避免 API 并发超限。

对 `scene_plan.yaml` 里的每个 scene，按顺序执行：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_video_with_text/gen_video.py \
  reference "<prompt_en>" \
  --images <output_dir>/assets/<asset_id>.png \
  --seconds "<该场景时长，取整到4-12之间>" \
  --aspect-ratio 16:9 \
  --save-path <output_dir>/clips/<scene_id>.mp4
```

- 场景之间如果需要更平滑的转场（比如上一场景结尾画面要自然过渡到
  下一场景开头），可以改用 `keyframe` 模式，把上一段生成结果的末帧
  截图作为下一段的 `first_frame`。
- 每生成完一个 clip 立即检查文件是否存在且时长基本符合预期，失败要
  重试或报告给用户，不要静默跳过导致最终拼接时缺片段。
- 如果某个 clip 的实际时长短于 scene_plan.yaml 中规划的时长，这是正常现象
  （gen_video API 无法精确控制时长）。后续在 Step 6 拼接时会对短片慢放
  补齐到目标时长。
- 文件命名必须保证按播放顺序可排序（`scene_01.mp4`、`scene_02.mp4` ...），
  `compose_mv.py` 是按文件名排序拼接的。

**产物**：`clips/scene_XX.mp4`（每个都强制落盘）

### Step 6: 最终合成

```bash
python .claude/skills/mv-generator/scripts/compose_mv.py \
  --clips-dir <output_dir>/clips \
  --lyrics-timed <output_dir>/lyrics_timed.json \
  --scene-plan <output_dir>/scene_plan.yaml \
  --audio <mp3路径> \
  --output <output_dir>/mv.mp4 \
  --title "歌名" \
  --title-pos top-right
```

该脚本会自动：
1. **自动修复歌词时间戳**：去除歌词间的空隙（每条歌词的 end = 下一条的 start），最后一行的 end = mp3 时长
2. 按 scene_plan.yaml 规划时长，每个 scene 独立缩放（clip 比规划短时慢放，长时快放）
3. 用 concat demuxer 拼接所有缩放后的 clip
4. 用 PIL 渲染逐帧字幕 PNG（5722帧 @ 24fps），直接 overlay 到主视频（避免二次编码质量损失）
5. 叠加歌名水印（可选，支持四个角位置）
6. 混入原始 mp3 音轨（先 `-an` 去掉 clips 自带音轨，再 `-shortest` 以短者为准）

**产物**：`mv.mp4`（最终交付物）

#### 关于视频时长补齐

gen_video API 返回的视频总时长通常短于 mp3 音频时长。
`compose_mv.py` 采用**逐场景独立缩放**策略：每个 scene 内的 clip 按
`目标时长/clip数/实际时长` 计算 scale，用 `setpts=SCALE*PTS` 单独处理。
拼接后总时长可能略短于 mp3，脚本在 overlay 后不做全局慢放。

#### 关于歌词时间戳修复

`compose_mv.py` 在 Step 6 开始时会**自动修复**歌词时间戳：
- 每条歌词的 `end` 设为下一条的 `start`（去除空隙）
- 最后一行的 `end` 设为 mp3 总时长
- 修复后直接覆盖 `--lyrics-timed` 指定的文件

#### 关于字幕渲染

字幕使用 **5722 帧**（238s × 24fps），逐帧渲染 PNG 再 overlay 到主视频。
这比逐句渲染更流畅，但耗时较长（约 2-3 分钟）。如需加速可降 fps 到 15。

#### 关于视频质量

overlay 步骤是关键质量瓶颈：
- 源 clip 通常 6Mbps，最终输出目标 4-6Mbps
- 使用 `-crf 14` + `-preset slow` 确保高质量编码
- 如果输出小于 10MB，检查 bitrate 是否正常

#### 歌名水印

- `--title`：歌名文本，如 `"进化再论"`
- `--title-pos`：水印位置，可选 `top-left`、`top-right`（默认）、`bottom-left`、`bottom-right`
- 水印使用半透明背景，避免遮挡画面内容
- 水印会叠加在所有场景上，贯穿全片

### Step 7: 校验交付

- 用 `ffprobe`（随 ffmpeg 一起安装）检查 `mv.mp4` 的总时长，和原始
  mp3 时长做对比，差异明显（比如超过 2 秒）要向用户说明原因（通常是
  Step 3 场景时长规划有累积误差）。
- 向用户展示最终产物路径，简要说明场景数量、总时长、是否有已知的
  人物一致性漂移片段需要用户留意。

## 常见错误与故障排除

1. **faster-whisper 未安装**：`asr_transcribe.py` 会给出清晰的
   `pip install faster-whisper` 提示，不会裸抛 ImportError。
2. **ffmpeg 未安装/不在 PATH**：`compose_mv.py` 使用硬编码路径
   `C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffmpeg.exe`，无需依赖 PATH。
3. **对齐结果时间戳明显异常**：通常是 ASR 识别质量太差（背景音乐过响、
   `--model-size` 太小）导致匹配率低，尝试换更大的模型规格重新识别。
4. **单个场景超过 12 秒**：`gen_video_with_text` 的硬限制，Step 3 场景
   规划阶段必须显式拆分，不要留到 Step 5 调用失败才发现。
5. **人物形象在不同 clip 间有明显差异**：这是 `reference` 模式的已知
   限制（非同一 seed 级别的像素一致），已在方案确认阶段与用户对齐过
   预期；可以尝试让同一角色的所有场景都引用完全相同的定妆图文件，
   减少（但不能消除）漂移。
6. **视频与音频总时长对不上**：检查 Step 3 场景规划里各场景时长之和
   是否等于（或接近）音频总时长，累积误差通常来自多个场景分别取整
   `--seconds` 参数（4-12 的整数）导致的舍入误差。compose_mv.py 采用
   **整体慢放**策略（concat + setpts），无累积误差，输出时长精确等于 mp3 时长。
7. **歌词时间戳有空隙**：如果 `lyrics_timed.json` 里两条歌词之间有较大
   空白（如前一条 end=4s，后一条 start=7s），字幕会"消失"几秒。
   `compose_mv.py` 会自动修复：每条歌词的 end = 下一条的 start。
8. **Windows 下字幕不显示/视频变黑屏**：确保字幕视频用 `overlay=0:0`
   合成时保留 alpha 通道（PIL PNG RGBA → concat demuxer → overlay）；
   输出前务必检查文件大小（正常应为几十 MB，若只有几 MB 说明 overlay 失败）。
8. **Windows 下 drawtext 水印路径报错**：字体路径中的冒号（`C:`）会被
   drawtext 当作分隔符，需用反斜杠转义（`C\:`）；或改用 PIL watermark
   方案（生成全帧 PNG 后 overlay）。
9. **lyrics_timed.json 格式**：必须是 `{"duration": float, "lines": [{"start","end","text"}]}`
   结构，`lines` 字段为逐句歌词的时间戳列表。

## 提示

1. **成本预期**：一首 3-4 分钟的歌通常会拆成 15-25 个场景，对应
   15-25 次 `gen_video` 调用，请提前让用户知晓耗时和调用量。
2. **Prompt 语言**：与 `gen_image_with_text`/`gen_video_with_text` 一致，
   英文 prompt 效果通常更好。
3. **中文歌词识别**：`asr_transcribe.py` 默认 `--language zh`，若测试
   下来准确率不理想，可以尝试 `--model-size medium` 或更大。
4. **Windows 环境**：ffmpeg 安装后需要新开终端窗口让 PATH 生效；
   环境变量设置用 `$env:AGNES_API_KEY="..."`。
5. **字幕样式可调**：可通过 `--font-size`（默认28）和 `--overlay-y-offset`
   （默认80，字幕距底部偏移像素）调整字幕大小和位置。
6. **歌名水印**：通过 `--title "歌名"` 和 `--title-pos`（可选 top-left/top-right/bottom-left/bottom-right，默认 top-right）添加。水印使用半透明黑底白字，贯穿全片。
7. **视频时长补齐**：当 gen_video 返回的 clip 比规划时长短时，compose_mv.py 采用**整体慢放**策略：concat 所有 clips 后统一应用 `setpts=SCALE*PTS`，无累积误差，输出时长精确等于 mp3 时长。
