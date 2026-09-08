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
- `scripts/align_lyrics_v2.py`：歌词对齐第一步，归一化精确匹配 + 覆盖率报告
  （旧版 `align_lyrics.py`/`align_lyrics_llm.py` 已不推荐使用，保留仅为兼容）
- `scripts/compose_mv.py`：ffmpeg 最终合成（逐 scene 独立缩放 + PIL 字幕/水印）

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

### Step 2: 歌词对齐校正（脚本精确锚点 + Agent 语义兜底，混合方案）

**不要单纯依赖字数对齐，也不要让 LLM 从零对齐整首歌**（成本高、容易在
歌词有重复段落——如副歌重复——时张冠李戴）。正确做法是两步混合：

**第一步：跑脚本拿到"精确匹配锚点 + 覆盖率报告"**

```bash
python .claude/skills/mv-generator/scripts/align_lyrics_v2.py \
  <output_dir>/asr_raw.json <output_dir>/lyrics.txt \
  --save-path <output_dir>/lyrics_timed.json \
  --save-srt <output_dir>/lyrics.srt
```

`align_lyrics_v2.py` 相比旧版 `align_lyrics.py` 的关键改进（这就是本
skill 之前"字数对不上"问题的根因修复）：
- **匹配前先归一化**：忽略大小写，并把繁体字统一转成简体再比较
  （faster-whisper 中文识别经常输出繁体，歌词文本通常是简体，"總"
  和"总"这种字之前会被判定为不匹配，导致大量本该精确匹配的字符退化
  成粗略插值）。若环境装了 `opencc-python-reimplemented`，简繁转换会
  更准更全，脚本会自动使用；没装则退化用内置的高频字对照表。
- **精确匹配优先，其余部分再插值**：先在归一化后的字符序列上找连续
  匹配块（长度 >= `--min-anchor`，默认 2，避免"的"/"了"这类高频单字
  造成噪声锚点）作为高置信度锚点，锚点之间的空隙才做线性插值，而不是
  整句/整段粗暴按字数分摊。
- **逐行覆盖率**：每行输出 `anchor_coverage`（0~1，锚点字符占比）。
  覆盖率 < 34% 的行，脚本会自动再尝试"整句级别"模糊匹配兜底（拿该行
  跟附近 ASR segment 整体做相似度比较），仍然拿不到可信结果的行会在
  stderr 里列出来，供下一步人工/Agent 复核。

**第二步：Agent 只复核脚本报告里覆盖率低的行**

1. 查看脚本 stderr 输出的低覆盖率行清单（通常是背景音乐过响、ASR
   整段幻听导致的片段，比如把"科技的窍门"识别成"可惜的窗门"这种
   语义/字面都对不上的情况，脚本自身无法可靠处理）。
2. 对这些行，Agent 结合上下文（前后已对齐好的行的时间戳区间、该行在
   ASR 原始 segments 里同一时间窗口的内容）用语义/读音相似性判断合理
   的时间区间，直接修改 `lyrics_timed.json` 里对应行的 `start`/`end`。
   **只改这些被标记的行，不要重新处理整首歌**——其余行已经是精确锚点
   或高覆盖率插值结果，可信度高，重新跑一遍 LLM 全量对齐反而可能把
   已经对的行改错（尤其是歌词有重复段落时）。
3. 修改后重新生成 `lyrics.srt`（或让 Agent 直接按新的 `lyrics_timed.json`
   内容手写覆盖）。

**Agent需输出的中间内容**（展示给用户）：
- 脚本报出的锚点覆盖率整体情况（比如"38 行里 32 行覆盖率 > 70%，6 行
  需要人工复核"）
- 被复核过的行，展示复核前后的时间戳对比
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
1. **逐 scene 独立缩放**（不是整体慢放）：按 `scene_plan.yaml` 里每个
   scene 的规划时长（`end - start`）与该 scene 实际 clip 时长的比例，
   各自计算 `setpts=SCALE*PTS`。clip 比规划短则慢放，比规划长则快放。
   这样每个画面片段出现的时间点严格贴合 `scene_plan.yaml` 的规划，
   不会因为整体拉伸导致画面节奏和歌词/场景规划错位。
2. 用 concat demuxer 拼接所有已各自缩放好的 clip（此时长度已等于
   `scene_plan.yaml` 里各 scene 时长之和）。
3. 字幕严格按 `lyrics_timed.json`（如 `lyrics_timed_v2.json`）里的
   **绝对时间**显示，这个时间是和 mp3 对齐好的时间，**不受 clip 缩放
   影响、也不按 scene 时间段来对齐**——because 歌词的同步基准是音频，
   不是画面。按"歌词分句"渲染 PNG（每句一张图，句间空隙也是一张
   空白图），用 concat demuxer 的逐图 `duration` 控制显示时长，
   相同文本复用同一张图，避免逐帧渲染。
4. 歌名水印用 PIL 渲染成透明 PNG（不用 `drawtext`，见下方"中文水印
   显示异常"排查项），和字幕图一起在同一次 `filter_complex` 里
   `overlay` 完成，不再是独立的一次编码。
5. 混入原始 mp3 音轨（先 `-an` 去掉 clips 自带音轨，再 `-shortest` 以短者为准）。

**产物**：`mv.mp4`（最终交付物）

#### 关于视频节奏对齐（逐 scene 独立缩放，不做整体慢放）

早期版本用"整体慢放"（拼接后统一 `setpts=SCALE*PTS`）：这种做法虽然
能保证总时长精确等于 mp3 时长，但会把每个 scene 的实际出现时刻和
`scene_plan.yaml` 里规划的时刻拉开（比如规划里 8s 处该切到 scene_02，
整体慢放后实际可能变成 11s 才切换），画面节奏和场景规划、歌词情绪点
对不上。现在的做法是**逐 scene 独立缩放**：每个 scene 按自己的
`target_dur`（scene_plan.yaml 里的 `end - start`）单独计算 SCALE，
clip 短了慢放、长了快放，拼接后每个 scene 的起止时刻天然和规划一致。
唯一的前提是 Step 3 场景规划阶段要保证所有 scene 时长之和等于（或
接近）音频总时长——如果规划阶段本身有明显偏差，逐 scene 缩放也无法
凭空修正总时长的系统性误差，Step 7 校验交付时要重点核对这一点。

#### 关于合成速度

早期版本对字幕采用"逐帧渲染 PNG"（3-4 分钟视频、24fps 下就是几千张
图 + 几千行 concat 列表），并且多编码了一份从未被实际使用的中间字幕
视频，是合成阶段慢的主要原因。现在改为**按歌词分句渲染**：一首歌几十
句歌词只渲染几十张 PNG（相同文本复用同一张），用 concat demuxer 每张
图各自的 `duration` 控制显示时长；字幕叠加和歌名水印叠加合并进同一次
`filter_complex`。如果合成仍然慢，优先检查：
- `--preset-scale`（默认 `veryfast`，逐 clip 缩放阶段）和
  `--preset-final`（默认 `medium`，最终叠加输出阶段）是否被改成了
  `slow`/`veryslow`——追求速度可以先用 `ultrafast`/`veryfast` 出预览版，
  确认无误后再用更高质量 preset 重新跑最终成片。
- clip 数量是否远超歌词行数规划（Step 3 场景规划超发，比如把 12s
  硬限制的场景又拆得过碎），每多一个 clip 就多一次独立编码。

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
3. **对齐结果时间戳明显异常/两边字数对不上**：先确认没有跳过 Step 2
   第一步的 `align_lyrics_v2.py`（不要直接用旧的 `align_lyrics.py`，
   后者没有简繁/大小写归一化，中文 ASR 输出繁体时会导致大量本该精确
   匹配的字符被判定为不匹配，从而错误地退化成整段线性插值）。跑完
   `align_lyrics_v2.py` 后看 stderr 的覆盖率报告：如果**大面积**行都
   覆盖率很低，通常是 ASR 识别质量太差（背景音乐过响、`--model-size`
   太小）导致的真实幻听（比如把"科技的窍门"识别成"可惜的窗门"），
   这种情况脚本兜底也救不回来，需要换更大的模型规格重新识别，或者
   直接交给 Agent 按 Step 2 第二步的方法人工复核那几行；如果只是
   **零星几行**覆盖率低，直接走 Step 2 第二步的人工复核流程即可，
   不需要重新识别整首歌。
4. **单个场景超过 12 秒**：`gen_video_with_text` 的硬限制，Step 3 场景
   规划阶段必须显式拆分，不要留到 Step 5 调用失败才发现。
5. **人物形象在不同 clip 间有明显差异**：这是 `reference` 模式的已知
   限制（非同一 seed 级别的像素一致），已在方案确认阶段与用户对齐过
   预期；可以尝试让同一角色的所有场景都引用完全相同的定妆图文件，
   减少（但不能消除）漂移。
6. **视频与音频总时长对不上**：检查 Step 3 场景规划里各场景时长之和
   是否等于（或接近）音频总时长。compose_mv.py 现在采用**逐 scene 独立
   缩放**（不是整体慢放），每个 scene 严格按自己的规划时长缩放，不存在
   多个场景取整 `--seconds` 导致的舍入误差累积问题；如果最终总时长
   仍然和 mp3 明显不符，说明是 Step 3 场景规划阶段本身时长之和就没对
   齐音频总时长，需要回到 Step 3 修正 `scene_plan.yaml`，而不是指望
   合成脚本兜底。
7. **Windows 下字幕不显示/视频变黑屏**：确保字幕 PNG 是 RGBA（PIL 生成）
   且通过 concat demuxer 直接喂给 `filter_complex` 的 `overlay`（不要
   再中间编码成 libx264 视频——libx264 不支持 alpha 通道，会把透明背景
   变成黑底，这也是之前"多编码一份从未使用的中间字幕视频"遗留的坑）。
   输出前检查文件大小（正常应为几十 MB，若只有几 MB 说明 overlay 失败）。
8. **歌名/字幕中文显示为方框（tofu）**：这是 `drawtext` 滤镜的已知坑
   ——Windows 下字体文件路径带盘符冒号（`C:`），drawtext 的滤镜参数
   解析器会把冒号当分隔符，转义稍有差错 ffmpeg 就会静默回退到内置的
   无 CJK 字形字体，中文全部显示成方框。**compose_mv.py 已经不再用
   drawtext 做歌名水印**，改成和歌词字幕一样用 PIL 渲染成透明 PNG 再
   `overlay`，从根源上避免这个问题；如果还遇到方框，先确认
   `FONT_PATH`（默认 `C:\Windows\Fonts\msyh.ttc`）在目标机器上确实存在
   且是支持中文的字体，必要时换成 `simhei.ttf`/`msyhbd.ttc` 等其他
   中文字体路径。
9. **lyrics_timed.json 格式**：必须是 `{"duration": float, "lines": [{"start","end","text"}]}`
   结构，`lines` 字段为逐句歌词的时间戳列表；`compose_mv.py` 每次运行
   会读取该文件并按 mp3 时长重写空隙/末行 `end`，字幕显示的时间基准
   始终是这个文件里的绝对时间，与画面 clip 的缩放无关。

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
6. **歌名水印**：通过 `--title "歌名"` 和 `--title-pos`（可选 top-left/top-right/bottom-left/bottom-right，默认 top-right）添加。水印用 PIL 渲染成半透明黑底白字的 PNG 再 overlay，不用 drawtext，不存在中文方框问题。
7. **视频节奏对齐**：当 gen_video 返回的 clip 比规划时长短/长时，compose_mv.py 对每个 scene **独立**计算 `setpts=SCALE*PTS`（而不是整体统一慢放），保证每个 scene 出现的时刻严格贴合 `scene_plan.yaml` 的规划；总时长是否精确等于 mp3 时长取决于 Step 3 场景规划本身的时长之和是否对齐音频总时长。
8. **合成速度**：字幕按歌词分句渲染 PNG（而不是逐帧渲染），字幕/水印合并成一次 `filter_complex`；可通过 `--preset-scale`/`--preset-final` 调整 ffmpeg 编码速度与质量的取舍，先用 `veryfast`/`ultrafast` 出预览版是推荐做法。
