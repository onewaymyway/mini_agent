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
方向选择(横屏/竖屏，写入配置) → 人声分离 → ASR 粗识别
→ 歌词对齐校正(强制对齐优先/ASR+模糊匹配兜底) → 场景规划(需用户确认)
→ 定妆图生成(含封面图) → 分场景视频生成 → 拼接+封面+烧字幕+混音 → 最终 mv.mp4
```

**封面效果**：默认会额外生成一张"浓缩全曲氛围"的封面图（`assets/cover.png`），
并在最终合成时把它做成一段轻微推近的短片，用来**替换**（不是插入）第一个
场景的前几秒画面，让 MV 一开场就有"封面感"，同时不改变视频总时长、不影响
后续任何场景的时间轴对齐。用户不需要就可以跳过，见下方 Step 0。

**支持横屏（16:9，默认）和竖屏（9:16，短视频/竖版）两种模式**，在
Step 0 由用户选择一次，选择结果写入 `<output_dir>/mv_config.json`，
后续所有步骤（定妆图生成、分场景视频生成、最终合成）都必须读取这个
配置文件，按对应模式传参，不能各步骤各自硬编码 `16:9`。详见下方
「Step 0：视频方向选择」和「不同方向模式的参数对照表」。

**依赖 skill**：
- `gen_image_with_text`（必须，生成角色/场景定妆图）
- `gen_video_with_text`（必须，生成分场景视频片段）

**依赖脚本**（本 skill 目录下）：
- `scripts/separate_vocals.py`：Step 1 用 Demucs 分离人声/伴奏，提升
  后续识别与对齐精度（推荐，跑不了可跳过退化用原始 mp3）
- `scripts/asr_transcribe.py`：本地语音识别（faster-whisper），支持
  `--lyrics-hint-file` 用标准歌词做解码提示
- `scripts/align_lyrics_forced.py`：歌词对齐**方案 A（优先）**，CTC 强制
  对齐，标准歌词文本已知，只需要对齐时间，不做自由识别，精度和鲁棒性
  都优于方案 B，尤其是有重复段落的歌
- `scripts/align_lyrics_v3.py`：歌词对齐**方案 B（兜底）**，当方案 A
  因为环境限制装不上/跑不通时用这个。归一化精确匹配 + 覆盖率报告 + 拼音
  模糊匹配（装了 `pypinyin` 才启用，能救回同音字/近音字 ASR 错误导致的
  整句不匹配；没装会自动退化成和 `align_lyrics_v2.py` 完全一样的行为，
  不会报错）。`align_lyrics_v2.py`/`align_lyrics.py`/`align_lyrics_llm.py`
  均已不推荐使用，保留仅为兼容
- `scripts/check_scene_plan.py`：校验 `scene_plan.yaml`（单场景时长范围、
  时间轴连续性、是否覆盖音频总时长），Step 3 写完必须跑，不通过不能进入 Step 4
- `scripts/check_assets.py`：校验 Step 4 生成的定妆图是否都已落盘（路径
  已回填、文件存在且非空、场景引用无悬空），Step 4 做完必须跑，不通过
  不能进入 Step 5
- `scripts/generate_scene_videos.py`：批量生成分场景视频，Step 5 用它代替
  逐个手动调用 `gen_video_with_text`，内置 key 池自动切换 + 失败重试 + 断点续跑
- `scripts/compose_mv.py`：ffmpeg 最终合成（逐 scene 独立缩放 + PIL 字幕/水印 +
  可选封面片段拼接，见下方「封面（Cover）效果」）
- `scripts/fix_lyrics.py`：修复歌词时间戳空隙（前一条end=后一条start）

**外部依赖**：
- `faster-whisper`（Python 包，未安装时先提示用户 `pip install faster-whisper`）
- `demucs`（Python 包，Step 1 人声分离用，推荐但非强制；
  `pip install demucs --break-system-packages`，首次运行需联网下载模型权重）
- `ctc-forced-aligner`（Python 包，Step 2 方案 A 强制对齐用，推荐但非
  强制；`pip install ctc-forced-aligner --break-system-packages`，首次
  运行需联网从 huggingface.co 下载模型，网络不通/装不上时自动退回方案 B）
- `pypinyin`（Python 包，Step 2 方案 B 的拼音模糊匹配用，纯 Python 无
  重依赖，安装几乎不会失败；`pip install pypinyin --break-system-packages`，
  没装也不影响方案 B 跑通，只是退化成没有拼音兜底的效果）
- `ffmpeg`（系统命令，未安装时先提示用户按 `docs/mv-generator-guide.md`
  里的说明安装；Windows 推荐 `winget install ffmpeg`）
- `AGNES_API_KEY` 环境变量（`gen_image_with_text`/`gen_video_with_text` 都需要）

## 🐍 Python 环境规范：统一使用 conda 的 `mv_env`

**本 skill 涉及的所有 Python 脚本（`separate_vocals.py`/`asr_transcribe.py`/
`align_lyrics_forced.py`/`align_lyrics_v3.py`/`check_scene_plan.py`/
`check_assets.py`/`generate_scene_videos.py`/`compose_mv.py` 等）默认统一
运行在一个专用的 conda 环境 `mv_env` 里，不要直接装进系统 Python 或其他
项目共用的环境**（Demucs/ctc-forced-aligner 等依赖体积大、版本要求特殊，
容易和其他项目的依赖冲突）。`compose_mv.py` 里硬编码的 ffmpeg 兜底路径
也是 `...\.conda\envs\mv_env\Library\bin\ffmpeg.exe`，与这个约定一致。

**进入 Step 1 之前，先检查并准备好这个环境**：

1. 检查 `mv_env` 是否已存在：`conda env list`，看输出里有没有 `mv_env`。
2. **没有则新建**（Python 版本建议 3.10/3.11，与 faster-whisper/demucs/
   ctc-forced-aligner 的兼容性较好）：
   ```bash
   conda create -n mv_env python=3.10 -y
   ```
3. **有就直接在现有环境上补装缺的包**，不要重建、不要用 `--force`
   之类会破坏已有环境的操作——`mv_env` 大概率是之前跑过本 skill 留下的，
   重建会丢失已经装好的大体积依赖（尤其是联网下载过的模型权重缓存）：
   ```bash
   conda run --no-capture-output -n mv_env pip install faster-whisper pyyaml pillow imageio-ffmpeg --break-system-packages
   conda run --no-capture-output -n mv_env pip install demucs ctc-forced-aligner pypinyin --break-system-packages
   ```
   （后一条是推荐但非强制的依赖，装不上不阻塞流程，见上方「外部依赖」
   说明；两条都用 `conda run --no-capture-output -n mv_env pip install ...` 而不是先手动
   `conda activate` 再 `pip install`，避免在非交互式脚本执行环境里
   `activate` 不生效导致包装进了错误的环境。）
4. **本 skill 下所有 `python .claude/skills/mv-generator/scripts/xxx.py ...`
   命令，都要用 `mv_env` 里的 Python 执行**，即在 SKILL.md 后续各 Step
   给出的命令前加 `conda run --no-capture-output -n mv_env`，例如：
   ```bash
   conda run --no-capture-output -n mv_env python .claude/skills/mv-generator/scripts/separate_vocals.py \
     <mp3路径> --output-dir <output_dir>
   ```
   下文各 Step 为了阅读简洁，示例命令里省略了这个前缀，实际执行时都要
   补上（除非用户明确说明要用系统默认 Python 环境）。
5. 如果 `conda` 命令本身不可用（未安装 Anaconda/Miniconda），提示用户
   安装后再继续，或征询用户是否接受直接用系统 Python 环境（此时按上面
   「外部依赖」里的普通 `pip install ... --break-system-packages` 方式
   安装，不再涉及 `conda run`）。

## ⚠️ 产物文件强制保存规范（最高优先级，与 comic-4panel 一致）

**每个步骤完成后，必须立即将产物写入文件，不得仅停留在对话输出中。**

1. 先生成内容，立刻写文件——不要等用户确认后再写，确认前就要写入
2. 文件写入是步骤完成的标志——没写入文件 = 步骤未完成，不能进入下一步
3. 每次重新生成都要覆盖/追加文件，确保产物文件始终是最新状态
4. 写入失败必须报告错误并停止，不能跳过产物保存

推荐的产物目录结构（`output_dir` 默认为 `./mv_output/{歌曲名}_{timestamp}`）：

```
mv_output/歌曲名_20260907/
├── mv_config.json         # Step 0 产物：横屏/竖屏等全局配置，最先写入
├── vocals/                # Step 1 产物：人声分离结果
│   └── htdemucs/<歌名>/
│       ├── vocals.wav     # 纯人声，Step 1 ASR、Step 2 强制对齐都用它
│       └── no_vocals.wav  # 纯伴奏，暂时用不上
├── asr_raw.json          # Step 1 产物
├── lyrics_timed.json     # Step 2 产物
├── lyrics.srt            # Step 2 产物（供人工核对，最终合成时会重新生成）
├── scene_plan.yaml        # Step 3 产物
├── assets/                # Step 4 产物：角色/场景定妆图 + 封面图
│   ├── character_A.png
│   ├── scene_park.png
│   └── cover.png          # 封面图（默认生成，见「封面（Cover）效果」）
├── clips/                 # Step 5 产物：分场景视频片段（文件名需可排序）
│   ├── scene_01.mp4
│   ├── scene_02.mp4
│   └── ...
└── mv.mp4                 # Step 6/7 最终产物
```

## 前置环境检查（进入 Step 1 之前先做）

0. 按上方「Python 环境规范：统一使用 conda 的 `mv_env`」检查/创建好
   `mv_env` 环境，后续本节的安装检查都在这个环境里做。
1. 检查 `AGNES_API_KEY` 环境变量是否已设置（`gen_image_with_text`/
   `gen_video_with_text` 都依赖它），未设置则提示用户设置后再继续。
2. 检查 `faster-whisper` 是否已安装（可以直接尝试 `python -c "import faster_whisper"`），
   未安装则提示用户 `pip install faster-whisper` 并等待确认后再继续。
3. 检查 `demucs`、`ctc-forced-aligner`、`pypinyin` 是否已安装
   （`python -c "import demucs"` / `python -c "import ctc_forced_aligner"` /
   `python -c "import pypinyin"`）。前两个是 Step 1/2 方案 A（人声分离 +
   强制对齐）用到的，没装不阻塞流程；`pypinyin` 是方案 B 的拼音模糊匹配
   用的，纯 Python 小包，基本不会装失败，**即使方案 A 两个重依赖都装不
   上，也建议至少装上这个**。提示用户
   `pip install demucs ctc-forced-aligner pypinyin --break-system-packages`
   一次性尝试，装不上的部分不影响能装上的部分生效，各自独立降级。
4. 检查 `ffmpeg` 是否在 PATH 中（`ffmpeg -version`），不可用则提示用户
   按 `docs/mv-generator-guide.md` 安装，Step 6 之前必须解决，前面几步
   不依赖 ffmpeg 可以先做。
5. 确认输入：mp3 文件路径是否存在、歌词文本是否已提供（逐行纯文本，
   与音频中演唱顺序一致）。歌词行数、音频时长会影响后续场景数量的
   预估，可以先做一个粗略播报，让用户对成本（15–25 次视频生成调用）
   有心理预期。

## 流程规范

### Step 0: 视频方向选择（横屏 / 竖屏，最先做，只做一次）

**这是整个流程里第一个要做的事**，必须在 Step 1（人声分离+ASR）之前完成，且
在同一个 `output_dir` 内只做一次——后续所有涉及"生成视频/图片尺寸"的
步骤都依赖这一步写入的配置文件，不允许中途改变，也不允许某个步骤
自己临时决定用别的比例。

1. **向用户询问**（一次性问清楚，不要拆成多轮）：
   - 视频是要横屏（16:9，适合 YouTube/传统 MV）还是竖屏（9:16，适合
     抖音/快手/视频号/Shorts/Reels 等竖版短视频场景）。**用户不回答或
     没有明确偏好时，默认横屏（16:9）**，直接采用默认值继续，不要为此
     阻塞流程。
   - **是否需要"封面效果"**（MV 开头几秒替换成一张浓缩全曲氛围的封面图，
     效果类似专辑封面/短视频封面）。**用户不回答或没有明确偏好时，默认
     开启**（`cover_enabled: true`），因为这项改动对最终观感提升明显、
     成本只是多一次定妆图生成调用；用户明确说不需要时才关闭。
2. **选定后立即写入配置文件** `<output_dir>/mv_config.json`（先
   `mkdir -p <output_dir>`），内容示例：

   横屏（默认）：
   ```json
   {
     "orientation": "landscape",
     "aspect_ratio": "16:9",
     "gen_video_aspect_ratio": "16:9",
     "gen_video_size": "720P",
     "image_ratio": "16:9",
     "compose_target_size": "1280:720",
     "compose_font_size": 28,
     "compose_overlay_y_offset": 80,
     "cover_enabled": true,
     "cover_duration_sec": 3.0
   }
   ```

   竖屏：
   ```json
   {
     "orientation": "portrait",
     "aspect_ratio": "9:16",
     "gen_video_aspect_ratio": "9:16",
     "gen_video_size": "720P",
     "image_ratio": "9:16",
     "compose_target_size": "720:1280",
     "compose_font_size": 22,
     "compose_overlay_y_offset": 140,
     "cover_enabled": true,
     "cover_duration_sec": 3.0
   }
   ```

   `cover_enabled`/`cover_duration_sec` 是本次新增字段：`cover_enabled`
   为 `false` 时，Step 4 跳过封面图生成、Step 6 不传 `--cover-image`，
   其余流程完全不受影响；`cover_duration_sec` 是期望的封面展示时长，
   实际生效值会在 Step 6 被 clamp 到第一个场景规划时长的 50% 以内
   （clamp 逻辑见「封面（Cover）效果」一节）。

   可以直接用 `create_file`/`str_replace` 写这个 JSON 文件，不需要
   额外脚本；字段含义见下方「不同方向模式的参数对照表」。

3. **写完必须回显给用户确认一次**（"已选择横屏/竖屏，配置已保存到
   `mv_config.json`，后续所有分场景视频、定妆图、最终合成都会按这个
   比例生成"），避免用户后知后觉发现方向选错但已经生成了一堆素材。
4. **后续每个用到 `--aspect-ratio` / `--ratio` / `--target-size` 等
   与画幅相关参数的步骤（Step 4、Step 5、Step 6），执行前都必须先
   读取 `mv_config.json`，把对应字段的值代入命令**，不能照抄 SKILL.md
   里示例命令中写死的 `16:9`/`1280:720`——示例命令里的具体数值只是
   给横屏模式打的比方，遇到竖屏配置要相应替换成 `9:16`/`720:1280`。
5. 若用户中途要求"改成竖屏"/"改成横屏"，视为对已有配置的修改：
   更新 `mv_config.json` 里的字段，并提醒用户——**已经生成的 `assets/`
   定妆图和 `clips/` 视频片段是按旧方向生成的尺寸，不会自动适配新
   方向，必须重新生成**（Step 4、Step 5 需要重新跑）。

#### 不同方向模式的参数对照表

| 配置字段 | 横屏 `landscape`（默认） | 竖屏 `portrait` | 用在哪一步 / 对应命令参数 |
|---|---|---|---|
| `orientation` | `landscape` | `portrait` | 仅供人读，标识当前模式 |
| `aspect_ratio` / `gen_video_aspect_ratio` | `16:9` | `9:16` | Step 5 `generate_scene_videos.py --aspect-ratio <值>`；实际输出像素约 `1280x704`（横）/ `720x1280`（竖），由 `gen_video_with_text` 服务端决定 |
| `gen_video_size` | `720P` | `720P` | Step 5，`gen_video_with_text` 当前只支持 `720P`，横竖屏都一样，不随方向变化 |
| `image_ratio` | `16:9` | `9:16` | Step 4 `gen_image.py gen ... --ratio <值>`（配合 `--size 2K` 等档位使用） |
| `compose_target_size` | `1280:720` | `720:1280` | Step 6 `compose_mv.py --target-size <值>`（注意是英文冒号分隔的 `W:H`，不是 `WxH`） |
| `compose_font_size` | `28` | `22` | Step 6 `compose_mv.py --font-size <值>`，竖屏画布更窄，字号建议调小，避免长句歌词溢出画面 |
| `compose_overlay_y_offset` | `80` | `140` | Step 6 `compose_mv.py --overlay-y-offset <值>`，竖屏画面更高，字幕距底部的留白建议加大，避免和短视频 App 自带的点赞/评论按钮区域重叠 |

**Step 3 场景规划也要感知方向**（不需要改脚本，是 Agent 写 prompt 时
的注意事项）：竖屏画面更窄更高，构图和横屏不同——写 `prompt_en` 时，
横屏可以用大远景/宽幅构图（wide shot），竖屏建议更多用人像构图/
中近景（medium shot / close-up、vertical framing、centered subject），
避免大远景在竖屏画布里主体过小、两侧留白过多。可以在 `scene_plan.yaml`
的 prompt 里显式加一句方向提示，比如竖屏加
`"vertical 9:16 framing, subject centered, medium close-up"`。

### Step 1: 人声分离 + ASR 粗识别

**先做人声分离，这是提升对齐效果收益最大的一步，不要跳过**：歌曲里的
背景音乐/和声/混响是 ASR 识别和后续对齐效果差的主要干扰源，Whisper 和
强制对齐模型的声学模型都是在接近纯人声/纯语音的数据上训练的，直接喂
带伴奏的原始 mp3 效果会明显打折扣。

```bash
python .claude/skills/mv-generator/scripts/separate_vocals.py \
  <mp3路径> --output-dir <output_dir>
```

产物是 `<output_dir>/vocals/htdemucs/<歌名>/vocals.wav`（纯人声，后续
步骤都用这个文件，不要再用原始 mp3）和 `no_vocals.wav`（纯伴奏，暂时用
不上）。首次运行需要联网下载 Demucs 模型权重，见脚本内注释里的排错说明；
如果当前环境确实无法联网下载，可以跳过这一步直接用原始 mp3 继续（对齐
效果会打折扣，但流程仍然能跑通）。

**ASR 粗识别**（即使后面走强制对齐方案，这一步的产物也建议保留，用于
Step 2 里对强制对齐结果做交叉验证，以及低置信度行的人工复核参考）：

```bash
python .claude/skills/mv-generator/scripts/asr_transcribe.py \
  <output_dir>/vocals/htdemucs/<歌名>/vocals.wav \
  --model-size medium --language zh \
  --lyrics-hint-file <output_dir>/lyrics.txt \
  --save-path <output_dir>/asr_raw.json
```

- **输入用人声轨，不用原始 mp3**（对应上一步产物）。
- `--model-size` 建议至少 `medium`：唱歌场景对声学模型要求比说话场景
  高，`small` 经常不够；`--lyrics-hint-file` 把标准歌词喂给 Whisper 做
  解码提示（`initial_prompt`），能明显提高识别文字和标准歌词的吻合度，
  是几乎零成本的改进，不要漏传。
- 若识别耗时较长（大文件 + cpu 模式），提前告知用户预计耗时。

**产物**：`vocals/htdemucs/<歌名>/vocals.wav`、`asr_raw.json`（强制落盘）

**ASR 结果必须立即质检**（不可跳过，直接进 Step 2 会被对齐脚本严重破坏）：

1. 读 `asr_raw.json`，逐段检查 ASR segments 的 `text` 和 `start/end`。
2. 将 ASR 文本与 `lyrics.txt`（或用户提供的歌词文件）做粗略对比：
   - 歌词内容是否大致能对应上（相同/近音字即可，不要求逐字一致）？
   - ASR segments 的时间是否连续无大的断层或倒序？
   - 是否覆盖了整首歌曲（首尾是否有明显空白未被识别）？
3. **如果效果太差（歌词内容完全对不上、大部分 segment 为空、时间轴严重错乱等）**：
   - **第一步：先用 `--model-size medium` 配合完整歌词作为 initial prompt**：调整 `--lyrics-hint-chars` 为 2000（默认只取前 200 字），把完整歌词喂给 Whisper 做解码提示，对中文歌曲识别率提升显著；命令示例：
     ```bash
     python .claude/skills/mv-generator/scripts/asr_transcribe.py \
       <vocals.wav> --model-size medium --language zh \
       --lyrics-hint-file <lyrics.txt> --lyrics-hint-chars 2000 \
       --save-path <output_dir>/asr_raw.json
     ```
   - **如果 medium 仍不理想：依次尝试所有小尺寸模型（tiny/base/small/medium），用效果最好的那个**。记录每次结果，对比 ASR 文本覆盖率和时间轴质量后选择最优配置。
   - 每次重新生成后都要再次做上述质检，直到结果可用。
   - **注意：`large-v3` 模型体积过大，本地 CPU 环境通常无法运行，不要使用。**
4. 只有确认 ASR 结果可用后，才能进入 Step 2。

### Step 2: 歌词对齐校正（ASR+模糊匹配方案 B 为主）

**必须使用 `align_lyrics_v3.py`**，不要用 `align_lyrics_forced.py` 或已废弃的 `align_lyrics_v2.py`。

`align_lyrics_v3.py` 是当前推荐的对齐脚本，相比旧版本有重大改进（见下方说明）。强制对齐方案 A（`align_lyrics_forced.py`）依赖 `ctc-forced-aligner` 和 huggingface.co 模型下载，在当前环境中经常因网络/依赖问题失败，不作为首选。

```bash
python .claude/skills/mv-generator/scripts/align_lyrics_v3.py \
  <output_dir>/asr_raw.json <output_dir>/lyrics.txt \
  --save-path <output_dir>/lyrics_timed.json \
  --save-srt <output_dir>/lyrics.srt
```

**重要：每次修改或重新生成 `lyrics_timed.json` 后，必须立即重新生成 `scene_plan.yaml`**。原因：场景规划直接基于歌词的时间戳区间（`start`/`end`），时间轴一旦变化，场景划分的时长和边界都会改变，沿用旧的 `scene_plan.yaml` 会导致画面切换与歌词不同步。这个重跑规则适用于所有情况，包括但不限于：
- ASR 重新识别后时间戳变化
- 手动修正了某行的 start/end
- 从方案 A 切换到方案 B（或反过来）
- 任何导致 `lyrics_timed.json` 内容更新的场景

**这一步决定整个 MV 字幕/画面切换的时间精度，是最容易出效果问题的环节。**

**必须使用 `align_lyrics_v3.py`（方案 B）**作为首选对齐脚本。该脚本在当前环境中可靠运行，而强制对齐方案 A（`align_lyrics_forced.py`）依赖 huggingface.co 模型下载，经常因网络问题失败。

```bash
python .claude/skills/mv-generator/scripts/align_lyrics_v3.py \
  <output_dir>/asr_raw.json <output_dir>/lyrics.txt \
  --save-path <output_dir>/lyrics_timed.json \
  --save-srt <output_dir>/lyrics.srt
```

**重要：每次修改或重新生成 `lyrics_timed.json` 后，必须立即重新生成 `scene_plan.yaml`**。原因：场景规划直接基于歌词的时间戳区间（`start`/`end`），时间轴一旦变化，场景划分的时长和边界都会改变，沿用旧的 `scene_plan.yaml` 会导致画面切换与歌词不同步。这个重跑规则适用于所有情况，包括但不限于：
- ASR 重新识别后时间戳变化
- 手动修正了某行的 start/end
- 从方案 A 切换到方案 B（或反过来）
- 任何导致 `lyrics_timed.json` 内容更新的场景

> 注：`align_lyrics_forced.py`（方案 A）保留为备选，仅在用户明确需要且环境允许时尝试。

**强烈建议先装上 `pypinyin`**（纯 Python 小包，无 C 扩展/无需联网下载
模型，安装几乎不会失败，即使 `ctc-forced-aligner`/`demucs` 都装不上的
环境通常也能装上）：
```bash
pip install pypinyin --break-system-packages
```

```bash
python .claude/skills/mv-generator/scripts/align_lyrics_v3.py \
  <output_dir>/asr_raw.json <output_dir>/lyrics.txt \
  --save-path <output_dir>/lyrics_timed.json \
  --save-srt <output_dir>/lyrics.srt
```

`align_lyrics_v3.py` 相比更早版本的关键改进：
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
- **拼音模糊匹配（v3 新增，需要 `pypinyin`）**：上面这条"整句级别模糊
  匹配"原本只比较汉字，中文 ASR 出错很大一部分是同音字/近音字替换
  （比如把"科技的窍门"识别成"可惜的窗门"），字形上完全不匹配但读音
  一样，纯汉字比较会直接判定"整句不相似"退化成插值。v3 额外把这一行
  和候选 ASR 片段都转成拼音序列再比一次相似度，取汉字/拼音两者的较大
  值，同音字错误也能命中，明显减少退化成插值的行数。未装 `pypinyin`
  时自动跳过这一条，行为等价于 `align_lyrics_v2.py`，不会报错，可以
  放心直接切到 `align_lyrics_v3.py`，不需要先判断装没装拼音库。
- **单调窗口约束**：引入时间游标 `cursor_time`，确保对齐结果在时间上
  单调不减，避免重复段落导致的张冠李戴问题（但本质是在缓解症状，效果
  上限不如方案 A，能用方案 A 就优先用方案 A）。
- **自动跳过歌词首行的歌名 + 更宽松的段落标记识别**：默认会自动丢弃
  歌词第一条非空、非段落标记的行（当成歌名），以及所有 `[Intro]`
  `[Verse 1]` `【副歌】` `(Bridge):` 这类中英文括号段落标记行，这些
  都不会被唱出来，不参与对齐。极少数歌词第一行本来就是要唱的正文
  （没写歌名）时，用 `--no-skip-title` 关掉这个行为。
- **窗口匹配加入字数对齐 + 更明确的匹配优先级**：窗口模糊匹配阶段，
  候选片段的有效字数（中文按汉字数、英文按字符数）和歌词行字数差得
  越多，得分会被按比例打折，避免"文字很像但长度明显对不上"的碎片被
  误选中；如果歌词是中文但 ASR 识别失败输出了英文/拼音（双方字数单位
  根本不可比），会自动识别并放宽这个惩罚。同时匹配不再是"汉字/拼音
  相似度取较大值"，而是分三档明确优先级：**汉字也能对上的候选 >
  只有拼音（读音）能对上的候选 > 都对不上、退化成插值瞎猜**，
  `method` 字段里会标出具体命中的是 `hanzi` 还是 `pinyin` 档，方便
  复核。
- **修复了实测暴露的大面积退化成插值的故障**：一行没有真实对应 ASR
  文本的歌词（纯语气词/和声/间奏），过去可能会因为"放宽窗口后随便
  抓一个弱匹配就采信"而把时间游标带偏几十上百秒，连带拖垮后面一长串
  本该能对上的行。现在放宽窗口找到的候选需要过一个明显更高的门槛
  （`--wide-match-threshold`）才会被采信；同时给拼音相似度加了一个
  "汉字相似度下限"（候选片段本身是中文时才检查），避免两句内容完全
  不相关的中文歌词仅凭拼音偶然相似就被误选中。用一首真实歌曲的完整
  ASR 结果（`是的那些都是复杂的问题`，83 段 ASR segment、75 行歌词）
  实测验证过，之前会导致约 40 行退化成插值、末尾时间戳超出音频总
  时长；修复后全曲对齐紧跟真实演唱进度，只有极少数确实没有清晰 ASR
  对应文本的行（纯语气词、结尾大段静音/和声）被正确标记为低置信度，
  交给人工复核。

**第二步（两个方案通用）：Agent 只复核脚本报告里置信度低的行**

1. 查看脚本 stderr 输出的低置信度/低覆盖率行清单。
2. 对这些行，Agent 结合上下文（前后已对齐好的行的时间戳区间、该行在
   `asr_raw.json` 原始 segments 里同一时间窗口的内容）用语义/读音相似性
   判断合理的时间区间，直接修改 `lyrics_timed.json` 里对应行的
   `start`/`end`。**只改这些被标记的行，不要重新处理整首歌**——其余行
   已经是高置信度结果，重新跑一遍 LLM 全量对齐反而可能把已经对的行改错
   （尤其是歌词有重复段落时）。
3. 修改后重新生成 `lyrics.srt`（或让 Agent 直接按新的 `lyrics_timed.json`
   内容手写覆盖）。

**Agent需输出的中间内容**（展示给用户）：
- 用的是方案 A 还是方案 B，为什么（比如"强制对齐模型下载失败，已退回
  ASR+模糊匹配方案"）
- 整体置信度情况（比如"38 行里 32 行高置信度，6 行需要人工复核"）
- 被复核过的行，展示复核前后的时间戳对比
- 如有明显的匹配困难或歧义（比如疑似 lyrics.txt 漏写了某段重复歌词），
  向用户说明

**产物**：`lyrics_timed.json`、`lyrics.srt`（强制落盘）

**`lyrics_timed.json` 格式示例**（两个对齐脚本产物格式一致，下游步骤
不需要关心具体是哪个方案产出的）：
```json
{
  "audio_path": "<output_dir>/vocals/htdemucs/歌名/vocals.wav",
  "duration": 239.04,
  "lines": [
    {"index": 0, "text": "歌词第一句", "start": 0.0, "end": 8.2,
     "anchor_coverage": 1.0, "method": "forced-align(score=-0.85)"},
    {"index": 1, "text": "歌词第二句", "start": 8.2, "end": 14.5,
     "anchor_coverage": 1.0, "method": "forced-align(score=-0.62)"}
  ]
}
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
5. **正确设置每个场景的 `video_mode`（reference / keyframe / text），不要
   默认全部填 `reference`**——这是三种模式各自的硬性前提，Agnes 接口不满足
   前提会直接 400 报错：
   - `video_mode: reference` 仅当该场景 `uses_assets` 非空、且引用的定妆图
     在 `recurring_assets` 里已有（或将有）`asset_path` 时才能用。
   - `video_mode: keyframe` 仅当该场景规划了 `first_frame` 和/或
     `last_frame` 时才能用。
   - **没有定妆图可引用的场景（比如开场空镜、纯环境过渡镜头、一次性出现
     不需要保持形象一致的镜头）必须用 `video_mode: text`**，不要为了"看起来
     统一"就填成 reference——那样会因为没有 images/audios/videos 而必然
     400 失败。
   Step 3 写完 `scene_plan.yaml` 后，下面的校验脚本会检查这一点并报错，
   但最好写的时候就按上述规则一次填对。

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

# 封面（cover_enabled 为 true 时才需要规划，见 Step 0 mv_config.json）
# 不是复用某个具体场景的定妆图，而是单独构图，浓缩全曲氛围，
# 构图上要给"歌名文字"留白（主体偏一侧，避免叠字盖住关键内容）
cover:
  description_zh: "封面：雨夜霓虹街头，主唱背影，大片天空留白用于叠歌名"
  description_en: "Album-cover style composition: female singer from behind on a rainy neon street at night, wide negative space at top for title text overlay, moody cinematic lighting"
  asset_path: assets/cover.png     # Step 4 生成后回填
  duration_sec: 3.0                # 封面展示时长，实际生效值会被 clamp（见 Step 6）

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

**必须做的事**：Step 3 完成后立即写入 `scene_plan.yaml`。

**写完之后、展示给用户确认之前，必须先跑校验脚本**（这是本次改进新增
的强制检查，避免场景时长超出 `gen_video_with_text` 的硬限制、或场景
规划没覆盖完整首歌，等到 Step 5 调用失败或 Step 7 才发现问题）：

```bash
python .claude/skills/mv-generator/scripts/check_scene_plan.py \
  <output_dir>/scene_plan.yaml \
  --audio <mp3路径>
```

该脚本会检查：
1. **每个场景时长必须在 4-12 秒范围内**——不在范围内会报出具体是哪个
   场景、当前时长、需要拆分还是合并/延长。
2. **场景时间轴连续无缺口无重叠**——从 0 秒开始、场景之间首尾相接、
   最后一个场景结束点覆盖到音频总时长，任何缝隙/重叠都会连同具体的
   场景 id 和秒数一起报出来。
3. **场景总时长是否覆盖 mp3 实际时长**（脚本用 `ffprobe` 读取 `--audio`
   指定的 mp3 得到真实时长，不依赖 `song_meta.duration` 是否手填准确）——
   不够长（缺尾）或超出太多都会报错并给出具体缺口/超出的秒数。

脚本退出码非 0（或输出 `"ok": false`）时：
- **不允许直接进入用户确认环节**，必须先根据 errors 列表逐条修改
  `scene_plan.yaml`（拆分超长场景、合并过短场景、补齐时间轴缺口、
  调整场景边界让总时长对齐音频等），改完后**重新运行本脚本**，
  如此循环直到 `"ok": true` 为止。
- 修改场景边界/拆分场景时注意同步更新 `lyric_lines`、`prompt_en`
  等关联字段，不要只改 `start`/`end` 数字。

**第三步（新增）：场景规划与对齐歌词交叉验证**

场景规划写完并通过 `check_scene_plan.py` 校验后，**必须再进行一轮
交叉验证**——将场景规划结果与 `lyrics_timed.json` 对齐后的歌词进行对比，
确保每个场景的 `start`/`end` 时间点与歌词的实际演唱时间吻合。

交叉验证的具体检查项：

1. **时间戳覆盖检查**：每个 scene 的 `start`/`end` 必须在 `lyrics_timed.json`
   的时间范围内（不能超出歌词结束时间，也不能在歌词开始前就出现画面）。
   若歌曲有纯音乐前奏/间奏/尾奏，这些段落可以在歌词范围外，但需在
   `scene_plan.yaml` 中用 `split_note` 标注说明（如"前奏纯音乐"
   "间奏过渡""尾奏淡出"）。

2. **歌词-场景对齐检查**：每个歌词行（`lyrics_timed.json` 中的每一行）
   都应被某个场景覆盖——检查每行歌词的 `start`/`end` 是否落在某个
   scene 的 `[start, end]` 区间内。若有歌词行没有被任何场景覆盖，
   说明场景规划有遗漏，需要补充场景。

3. **场景边界合理性检查**：场景切换点（scene_XX 的 `end` 等于
   scene_XX+1 的 `start`）是否合理——切换点不应出现在一句歌词的中间，
   理想情况下应在歌词行的边界处或情绪转折点处。

4. **低置信度行重点核查**：对于 `lyrics_timed.json` 中标记为低覆盖率的
   歌词行（`anchor_coverage < 0.34`），需特别确认其对应的场景规划
   是否准确。如果这些行的时间戳本身就有问题，场景规划可能需要调整。

交叉验证通过后，才能向用户展示场景规划结果请求确认。

**只有场景规划时间戳校验通过、且与对齐歌词交叉验证均无问题时，
才能进入 Step 4。如果交叉验证发现问题，需返回修改 `scene_plan.yaml`，
直到两者完全匹配。**

### Step 4: 定妆图生成

对 `scene_plan.yaml` 里 `recurring_assets` 列出的每个角色/场景，调用
`gen_image_with_text`：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<description_en>" --size 2K --ratio <mv_config.json 里的 image_ratio> \
  --save-path <output_dir>/assets/<asset_id>.png
```

**`--ratio` 必须取自 Step 0 写入的 `mv_config.json` 里的 `image_ratio`
字段**（横屏为 `16:9`，竖屏为 `9:16`），不要照抄示例里的 `16:9` 就
不管方向配置了；定妆图的画幅要和最终视频画幅一致，否则 `reference`
模式生成视频时容易出现主体裁切/构图不协调。

- 复杂场景（比如需要固定角色出现在不同环境里）可以用 `edit` 多图合成，
  参考 `gen_image_with_text` 的 SKILL.md。
- 生成后立即回填 `scene_plan.yaml` 里对应 `asset_path` 字段。

**封面图生成**（`mv_config.json` 里 `cover_enabled` 为 `true` 时，紧接着
上面的定妆图生成一起做，不是单独一轮）：

```bash
AGNES_API_KEY="..." python .claude/skills/gen_image_with_text/gen_image.py \
  gen "<scene_plan.yaml 里 cover.description_en>" --size 2K \
  --ratio <mv_config.json 里的 image_ratio> \
  --save-path <output_dir>/assets/cover.png
```

- `--ratio` 同样取自 `mv_config.json` 的 `image_ratio`，与定妆图、最终
  视频画幅保持一致。
- 封面不是随便挑一张定妆图当封面用，而是**单独按 `scene_plan.yaml` 里
  `cover.description_en` 生成一张新图**，构图要求和普通场景定妆图不同：
  突出"浓缩全曲氛围/情绪基调"，且要给歌名文字留白（主体人物/焦点偏
  画面一侧或下方，画面上方或另一侧留出干净背景），因为最终合成时会在
  封面片段上叠加大号歌名文字（复用 `compose_mv.py` 里歌名水印的 PIL
  渲染逻辑，但字号/位置更像"封面标题"而不是角标水印，细节见 Step 6）。
- 生成后立即回填 `scene_plan.yaml` 里 `cover.asset_path` 字段。
- 若 `cover_enabled` 为 `false`，跳过这一步，`scene_plan.yaml` 也不需要
  `cover` 字段（或保留字段但不生成图片，Step 6 不传 `--cover-image`）。

**产物**：`assets/*.png` + `assets/cover.png`（强制落盘），并同步更新
`scene_plan.yaml`

**全部定妆图生成完并回填 `asset_path` 后，进入 Step 5 之前必须先跑校验
脚本**，确认真的都生成成功了（避免某张图生成失败/超时但 Agent 没注意到，
或者忘记回填 `asset_path`，直到 Step 5 调用视频生成时才因为参考图缺失
而报错）：

```bash
python .claude/skills/mv-generator/scripts/check_assets.py \
  <output_dir>/scene_plan.yaml \
  --output-dir <output_dir>
```

该脚本会检查：
1. **`recurring_assets` 每一项是否都已回填 `asset_path`**——生成完图片
   却忘记回填字段是常见疏漏，会直接报出具体是哪个 asset id。
2. **`asset_path` 指向的文件是否真实存在且非空**——生成失败但留下了
   空文件/占位文件的情况也会被抓出来，不会被误判为"已完成"。
3. **所有 scene 的 `uses_assets` 引用是否都能在 `recurring_assets` 里
   找到对应项**——引用了没规划过的 id 或拼写错误，会连同具体 scene id
   一起报出来，而不是等 Step 5 生成时才发现参考图缺失。
4. **`cover.asset_path` 是否已回填、文件是否存在且非空**（`scene_plan.yaml`
   里有 `cover` 字段时才检查；`cover_enabled` 为 `false`、`scene_plan.yaml`
   本来就没写 `cover` 字段时会给一条 warning 而不是 error，不阻塞流程）。

脚本退出码非 0 时，**不允许进入 Step 5**，需要按 errors 列表逐条处理
（回到 Step 4 补生成缺失/为空的定妆图，或修正 `scene_plan.yaml` 里的
`asset_path`/`uses_assets` 引用），改完重新运行本脚本，直到通过为止。

### Step 5: 分场景视频生成

**改为用脚本批量生成，不再由 Agent 逐个手动调用命令。** 这样可以让
生成过程更稳定：脚本内部串行执行（不并行，避免 API 并发超限）、遇到
rate limit 自动切换到下一把可用 key（复用 `gen_video_with_text` 自带
的 `agnes_key_pool.py`，key 池的冷却状态在整个批量过程中持续保留）、
单场景失败自动重试。

```bash
python .claude/skills/mv-generator/scripts/generate_scene_videos.py \
  <output_dir>/scene_plan.yaml \
  --output-dir <output_dir> \
  --aspect-ratio <mv_config.json 里的 gen_video_aspect_ratio>
```

**`--aspect-ratio` 必须取自 `mv_config.json` 的 `gen_video_aspect_ratio`
字段**（横屏 `16:9`，竖屏 `9:16`），不要沿用脚本自身 `--aspect-ratio`
参数的默认值 `16:9`——那只是脚本在没有传参时的兜底默认，本 skill 里
永远要显式传值，来源是 Step 0 的配置，不是脚本默认值。`--size`（即
`gen_video_size`）横竖屏都固定传 `720P`，这是 `gen_video_with_text`
服务当前唯一支持的档位，不随方向变化。

（需要先设置好 `AGNES_API_KEY` 或 `AGNES_API_KEYS` 环境变量，或在
`providers.json` 里配置好 agnes 的 `api_keys`，脚本会自动加载。）

**⚠️ 调用 bash 工具执行上面这条命令时，`timeout` 参数必须传 `-1`
（不限时），不要用默认的 300 秒。** 一首歌通常有 15-25 个场景，每个
场景视频生成（含排队+轮询+下载）常常就要几分钟，加上失败重试、多轮
补跑，整个批量生成过程动辄超过半小时，300 秒的默认超时会在脚本还在
正常工作时就把它强制杀掉，导致已经成功的场景也可能因为进程被杀而
来不及汇总/白跑。`timeout=-1` 表示不设超时上限，命令会一直运行到
`generate_scene_videos.py` 自己跑完（成功或判定为持续性失败）为止，
不会被 bash 工具的看门狗提前终止。 Step 1 的 `asr_transcribe.py`
（大文件 + cpu 模式）如果实测经常超过 300 秒，同样建议用 `timeout=-1`。

**调用示例（system-prompt 模式工具调用格式，直接照抄，只替换 `<output_dir>`）**：

```
<tool_use>
{"name": "bash", "input": {"command": "python .claude/skills/mv-generator/scripts/generate_scene_videos.py <output_dir>/scene_plan.yaml --output-dir <output_dir> --aspect-ratio <gen_video_aspect_ratio，读取自mv_config.json，横屏16:9/竖屏9:16>", "timeout": -1}}
</tool_use>
```

（这是 `llm/system_tool_call.py` 里定义的 `<tool_use>{"name":..,"input":..}</tool_use>`
协议；若走的是原生 function-calling 的 provider，则等价于对 `bash` 工具传入
`{"command": "...", "timeout": -1}` 这个 `input`/`tool_input`。）不要省略
`timeout: -1` 这一项，也不要照搬其他 skill 里"timeout 用默认值就行"的
写法——本步骤是本 skill 里唯一必须显式传 `-1` 的调用，其余步骤
（`asr_transcribe.py` 如果实测经常超过 300 秒除外）沿用默认 300 秒即可。

**关于实时进度输出**：`generate_scene_videos.py` 内部已经把 stdout/stderr
强制设为行缓冲（每打印一行立刻 flush），bash 工具也会给子进程注入
`PYTHONUNBUFFERED=1`，两层保障下终端应能实时看到"正在生成场景 XX"
这类进度打印。如果仍然发现长时间没有任何输出（只看到 urllib3 的
`InsecureRequestWarning` 之类 warning，看不到脚本自己的 print），大概率
是卡在某个场景的视频生成/轮询上（本身就要等几分钟），而不是输出没有
被实时打印——可以对照脚本打印的"[第N轮 i/j] 正在生成场景 xxx"确认当前
卡在哪个场景，而不是怀疑是流式输出坏了。

**脚本行为说明**：
1. 依次读取 `scene_plan.yaml` 里的每个 scene，根据 `video_mode` 字段
   自动选择 `reference`/`keyframe`/`text` 模式调用生成接口，`reference`
   模式会自动把 `uses_assets` 对应的定妆图路径（`recurring_assets` 里
   回填的 `asset_path`）传进去；`seconds` 自动取 `end - start` 并夹到
   4-12 秒范围内（Step 3 的校验已保证这个范围本身没问题）。**兜底**：
   如果某个场景标了 `video_mode: reference` 却没有可用的定妆图（
   `uses_assets` 为空或对应素材缺 `asset_path`），或标了 `keyframe` 却没
   `first_frame`/`last_frame`，脚本会打印提示并自动降级为 `text` 模式
   生成，而不是重试 3 次同样必然 400 的调用——但这只是兜底，Step 3 的
   `check_scene_plan.py` 应该已经在规划阶段拦下这类问题。
2. **每开始生成一个场景前会打印进度和该场景的关键信息**（第几轮/第几个、
   scene id、lyric_lines、start/end、使用的定妆图、prompt），方便观察
   当前在生成什么、卡在哪一步。
3. **单个场景失败会自动重试最多 3 次**（每次重试间隔递增），3 次都失败
   就先跳过，继续生成下一个场景，不阻塞整体进度。
4. **一轮跑完所有场景后，如果还有未成功的场景，会自动从头再跑一轮**，
   只处理"尚未生成成功"的场景，如此循环，直到全部场景都生成成功；
   如果某一轮完全没有任何新增成功（说明剩下的大概率是持续性问题，
   比如 prompt 违规、参数错误、账号额度耗尽而非临时限流），脚本会
   停止自动重试并汇报剩余失败的 scene id 列表，交给 Agent/用户判断。
5. **限流自动切换 key**：这一层复用 `gen_video_with_text` 已有的
   `AgnesKeyPool`（HTTP 429 或响应文本命中限流关键字时触发），本脚本
   只是把这个能力从"单次调用"扩展到"整个批量生成过程复用同一个 key
   池实例"，冷却状态更准确，不会因为每个场景单独起进程而丢失。
6. **支持断点续跑**：已存在且非空的 `clips/<scene_id>.mp4` 默认直接
   跳过（不重复生成），中断后重新运行本脚本即可从未完成的场景继续；
   如果需要强制全部重新生成，加 `--force`。
7. 脚本结束时会打印成功/失败汇总，并以退出码区分（0=全部成功，
   1=仍有场景失败）。若退出码为 1，Agent 需要检查失败的 scene（常见
   原因：prompt 含违禁词、定妆图路径错误、账号额度耗尽），修正后
   重新运行本脚本（断点续跑，只会处理仍缺失的场景）。
8. 场景之间如果需要更平滑的转场（比如上一场景结尾画面要自然过渡到
   下一场景开头），可以在 `scene_plan.yaml` 里把该 scene 的 `video_mode`
   设为 `keyframe` 并填好 `first_frame`/`last_frame` 字段（`first_frame`
   可以用上一段生成结果的末帧截图），脚本会按字段自动处理。
- 如果某个 clip 的实际时长短于 scene_plan.yaml 中规划的时长，这是正常现象
  （gen_video API 无法精确控制时长）。后续在 Step 6 拼接时会对短片慢放
  补齐到目标时长。
- 文件命名与 scene id 一致（`scene_01.mp4`、`scene_02.mp4` ...），
  务必保证 `scene_plan.yaml` 里的 scene id 本身按播放顺序可排序，
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
  --title-pos top-right \
  --target-size <mv_config.json 里的 compose_target_size> \
  --font-size <mv_config.json 里的 compose_font_size> \
  --overlay-y-offset <mv_config.json 里的 compose_overlay_y_offset> \
  --cover-image <output_dir>/assets/cover.png \
  --cover-duration <mv_config.json 里的 cover_duration_sec>
```

**`--cover-image`/`--cover-duration` 只在 `mv_config.json` 的
`cover_enabled` 为 `true` 时传**；为 `false` 时这两个参数整体不传，
`compose_mv.py` 会按普通流程合成，不做任何封面相关处理。

**`--target-size`/`--font-size`/`--overlay-y-offset` 三个都必须取自
`mv_config.json`**（横屏依次为 `1280:720`/`28`/`80`，竖屏依次为
`720:1280`/`22`/`140`），不要沿用 `compose_mv.py` 参数自身的默认值
（`--target-size` 默认 `1280:720`，只对应横屏；竖屏必须显式传
`720:1280`，否则最终视频会被强行 pad/裁剪成横屏画布，画面主体两侧
出现大片黑边）。`--target-size` 格式是英文冒号分隔的 `宽:高`（例如
`720:1280`），不要写成 `720x1280` 或反过来写成 `1280:720` 当竖屏用。

该脚本会自动：
1. **修复歌词时间戳空隙**：将前一条歌词的 end 设置为后一条的 start，
   最后一条的 end 设置为 mp3 音频时长，确保字幕连续显示无闪烁。
2. **逐场景独立缩放**（不是整体慢放）：按 `scene_plan.yaml` 里每个
   scene 的规划时长（`end - start`）与该 scene 实际 clip 时长的比例，
   各自计算 `setpts=SCALE*PTS`。clip 比规划短则慢放，比规划长则快放。
   这样每个画面片段出现的时间点严格贴合 `scene_plan.yaml` 的规划，
   不会因为整体拉伸导致画面节奏和歌词/场景规划错位。
3. **按歌词分句渲染字幕 PNG**：每句歌词（含句间空白）只渲染一张 PNG，
   相同文本复用同一张图。一首歌几十句歌词只渲染几十张 PNG，大幅提速。
4. **PIL 渲染歌名水印**：不应用 drawtext 滤镜（Windows 下字体路径冒号
   解析易出错），改用 PIL 加载字体渲染成透明 PNG，再用 overlay 叠加。
5. **字幕+水印合并 overlay**：同一次 `filter_complex` 完成，避免多次
   编码导致的质量损失。
6. **混入原始 mp3 音轨**：先 `-an` 去掉 clips 自带音轨，再 `-shortest`
   以短者为准。

**产物**：`mv.mp4`（最终交付物）

#### 关于视频质量的关键要点

**严重警告**：overlay 步骤极易导致视频质量崩溃（比特率从 6Mbps 降至 19kbps）。
原因和解决方案：

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 视频质量暴跌 | overlay 步骤未指定 `-b:v`，ffmpeg 使用极低默认值 | compose_mv.py 已修复，使用 `-preset slow -crf 14` |
| 字幕断续闪烁 | 歌词时间戳有空隙 | 脚本自动修复：前一条end=后一条start |
| 歌名显示方框 | drawtext 字体路径冒号解析错误 | 改用 PIL 渲染 PNG 水印 |

**验证方法**：合成完成后用 ffprobe 检查视频比特率，正常应为 2-6 Mbps：
```bash
ffprobe -v quiet -print_format json -show_streams mv.mp4 | jq '.streams[0].bit_rate'
```

如果比特率 < 100 kbps，说明 overlay 失败，需检查 compose_mv.py 的版本。

#### 关于视频节奏对齐（逐 scene 独立缩放，不做整体慢放）

早期版本用"整体慢放"（拼接后统一 `setpts=SCALE*PTS`）：这种做法虽然
能保证总时长精确等于 mp3 时长，但会把每个 scene 的实际出现时刻和
`scene_plan.yaml` 里规划的时刻拉开（比如规划里 8s 处该切到 scene_02，
整体慢放后实际可能变成 11s 才切换），画面节奏和场景规划、歌词情绪点对不上。现在的做法是**逐 scene 独立缩放**：每个 scene 按自己的
`target_dur`（scene_plan.yaml 里的 `end - start`）单独计算 SCALE，
clip 短了慢放、长了快放，拼接后每个 scene 的起止时刻天然和规划一致。
唯一的前提是 Step 3 场景规划阶段要保证所有 scene 时长之和等于（或
接近）音频总时长——如果规划阶段本身有明显偏差，逐 scene 缩放也无法
凭空修正总时长的系统性误差，Step 7 校验交付时要重点核对这一点。

#### 封面（Cover）效果

**做法是"替换/挤压"，不是"插入"**：如果简单在开头插入一段封面片段，
视频总时长会变长，破坏「逐 scene 独立缩放 + 总时长贴合音频」这个不变量，
后面所有场景的绝对时间点、字幕时间戳都得跟着平移，改动面大也容易出 bug。
所以 `compose_mv.py` 的实现是：把第一个场景（按 `start` 排序后的
`scenes[0]`）缩放后 clip 的**前 N 秒**，用封面短片替换掉，N 秒之后无缝
接回该场景原本的画面内容——`trim 前N秒(封面) + trim 剩余部分(scene_01)`
拼接成一个新的、时长跟原来完全一样的片段，顶替原来的第一个 clip 参与
后续 concat。这样：
- 不影响总时长、不影响任何后续场景的时间轴/字幕对齐；
- 如果这首歌本来就有纯音乐前奏（第一句歌词不是从 0 秒开始唱），封面
  时长会自然落在这段"本来也没具体画面要求"的空当里，效果最自然；
- 如果没有前奏（一上来就唱），就是从第一个场景时长里"借"这几秒，
  效果会打折扣但不会出错；
- `--cover-duration` 会被 clamp 到第一个场景规划时长的 50% 以内（同时
  也不会超过该 clip 的实际时长），避免极短场景被封面完全吃光——
  `check_scene_plan.py` 在 Step 3 阶段已经做过同样的比例校验，正常
  情况下 Step 6 这里不会再触发 clamp，只是留一道兜底。

封面短片本身不是死板静帧：`compose_mv.py` 用 `ffmpeg -loop 1 -i cover.png`
配合轻微 `zoompan` 缓慢推近，模拟"呼吸感"，而不是硬切一张纯静图。

如果想让封面片段上叠加大号歌名文字（比单纯的"下一步字幕"更像正式的
"封面标题"），可以在 Step 4 生成 `cover.png` 时就直接把歌名画在图里
（让 `gen_image_with_text` 的 prompt 里包含歌名文字排版要求），这样不
依赖 `compose_mv.py` 额外的水印叠加逻辑，效果也更可控。

### Step 7: 校验交付

- 用 `ffprobe`（随 ffmpeg 一起安装）检查 `mv.mp4` 的总时长，和原始
  mp3 时长做对比，差异明显（比如超过 2 秒）要向用户说明原因（通常是
  Step 3 场景时长规划有累积误差）。
- 检查视频比特率是否正常（应 > 1 Mbps），否则说明 overlay 失败。
- **检查最终视频的宽高是否和 `mv_config.json` 里的 `compose_target_size`
  一致**（`ffprobe -v quiet -print_format json -show_streams mv.mp4 |
  jq '.streams[0].width, .streams[0].height'`）：横屏应为 `1280x720`，
  竖屏应为 `720x1280`。如果对不上，说明 Step 6 合成时 `--target-size`
  没有正确读取配置文件（很可能是照抄了示例命令里的横屏默认值）。
- 向用户展示最终产物路径，简要说明场景数量、总时长、是否有已知的
  人物一致性漂移片段需要用户留意。

## 常见错误与故障排除

0. **忘记做 Step 0 方向选择，或某一步没读配置就照抄示例命令**：
   本 skill 所有示例命令里出现的 `16:9`/`1280:720`/`28`/`80` 都是
   "横屏模式下的示例值"，不是可以无脑照抄的固定参数。每次执行
   Step 4/5/6 前，先 `cat <output_dir>/mv_config.json` 确认当前方向
   和对应字段值，再把命令里的占位符替换成配置里的真实值。如果
   `output_dir` 里还没有 `mv_config.json`，说明 Step 0 被跳过了，
   必须先补做 Step 0（询问用户方向、写入配置）再继续。
1. **faster-whisper 未安装**：`asr_transcribe.py` 会给出清晰的
   `pip install faster-whisper` 提示，不会裸抛 ImportError。
2. **ffmpeg 未安装/不在 PATH**：`compose_mv.py` 使用硬编码路径
   `C:\Users\onewa\.conda\envs\mv_env\Library\bin\ffmpeg.exe`，无需依赖 PATH。
3. **对齐结果时间戳明显异常/两边字数对不上（走的是方案 B `align_lyrics_v3.py`）**：
   先确认没有跳过归一化步骤（不要直接用更早期的 `align_lyrics.py`，
   后者没有简繁/大小写归一化，中文 ASR 输出繁体时会导致大量本该精确
   匹配的字符被判定为不匹配，从而错误地退化成整段线性插值）。跑完
   `align_lyrics_v3.py` 后看 stderr 的覆盖率报告：如果**大面积**行都
   覆盖率很低，通常是 ASR 识别质量太差（背景音乐过响、`--model-size`
   太小、没做人声分离）导致的真实幻听（比如把"科技的窍门"识别成
   "可惜的窗门"），这种情况脚本兜底也救不回来——**优先考虑换成方案 A
   强制对齐**（从根本上不依赖 ASR 识别质量），退而求其次是先补做 Step 1
   的人声分离/换更大的 `--model-size` 重新识别；如果只是**零星几行**
   覆盖率低，直接走 Step 2 第二步的人工复核流程即可，不需要重新识别
   整首歌。
3b. **强制对齐 `align_lyrics_forced.py` 报错下载模型失败**：说明当前
   网络访问不到 `huggingface.co`。按脚本报错提示处理（加白名单 / 换网络
   环境预下载模型再拷贝过来 / `--model-path` 指定已有模型文件）；如果
   短时间内无法解决，直接退回方案 B `align_lyrics_v3.py` 继续流程，不要
   在这里卡住整个任务。
3c. **强制对齐报错"字符数对不上"**：说明 `lyrics.txt` 里有 uroman 音译
   处理不了的字符（生僻字、emoji、罗马数字、特殊符号等），把这些字符
   替换成常见汉字/标点后重跑；如果歌词里确实需要保留这些字符，退回
   方案 B。
4. **单个场景超过 12 秒/不足 4 秒/时间轴有缺口**：`gen_video_with_text`
   的硬限制是每个 clip 4-12 秒；`check_scene_plan.py` 会在 Step 3 阶段
   就把这些问题连同具体场景 id 一起报出来，必须改到校验通过再进入
   Step 4，不要留到 Step 5 调用失败或最终成片缺画面才发现。
4b. **`generate_scene_videos.py` 跑完仍有场景失败**：先看脚本汇总打印
   的失败 scene id 列表和 `--output-dir` 下 `clips/` 里缺的文件，常见
   原因是 prompt 触发内容审核、`recurring_assets` 里 `asset_path` 路径
   错误（Step 4 忘记回填或路径拼写错误）、或所有 key 都被限流/额度耗尽。
   修正 `scene_plan.yaml` 或环境变量后，直接重新运行同一条命令即可
   （已成功的场景会被跳过，只补齐缺失的）。
4c. **Step 4 定妆图生成后没检查就直接进入 Step 5**：用 `check_assets.py`
   在 Step 4 结束后强制检查一遍，常见疏漏是图片生成失败但 Agent 没注意
   （留下空文件）、或生成成功但忘记回填 `scene_plan.yaml` 里的
   `asset_path`——这两种情况不检查的话，Step 5 批量生成时对应场景会因为
   参考图缺失/无效而失败，且不容易第一时间定位到根因是"定妆图没生成好"
   而不是视频生成本身的问题。
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
10. **视频质量崩溃（比特率从 6Mbps 降至 19kbps）**：overlay 步骤未正确
    指定编码参数导致。确保使用最新的 `compose_mv.py`（已修复为
    `-preset slow -crf 14`），并用 ffprobe 验证输出视频的比特率。
11. **封面没生效/开头看不出封面效果**：先确认 `mv_config.json` 里
    `cover_enabled` 是否为 `true`；再确认 Step 6 命令是否真的传了
    `--cover-image`（很容易在照抄命令时漏传，尤其是从别的、没开启
    封面的 output_dir 复制命令过来时）；再确认 `assets/cover.png`
    是否存在且非空（`check_assets.py` 应该已经拦截过，但如果是手动
    跳过校验直接跑 Step 6，这里可能是根因）；最后确认第一个场景本身
    的规划时长（`scene_plan.yaml` 里 `scenes[0]` 的 `end - start`）
    是否 >= 6 秒左右——如果第一个场景本身很短（接近 4 秒下限），
    `--cover-duration` 会被 clamp 到很小的值（不到 2 秒），效果不明显，
    属于预期行为，不是 bug。

## 提示

0. **横屏/竖屏只在 Step 0 选一次，全程复用**：选择结果落在
   `<output_dir>/mv_config.json`，Step 4（定妆图 `--ratio`）、
   Step 5（分场景视频 `--aspect-ratio`）、Step 6（合成 `--target-size`
   / `--font-size` / `--overlay-y-offset`）都从这个文件取值，不要在
   某一步单独跟用户确认或改用别的比例，保持全程一致。
0b. **对齐效果不理想时的排查顺序**：先看有没有做 Step 1 人声分离
   （最大单项改进，不依赖任何重依赖，Demucs 装不上就先解决这个）→
   再看 Step 2 能不能用方案 A 强制对齐（比方案 B 上限更高，尤其是重复
   段落多的歌）→ 方案 A 装不上/跑不通时，至少确保方案 B 装了
   `pypinyin`（几乎不会装失败，同音字场景能明显提高覆盖率）→ 最后才是
   调 `align_lyrics_v3.py` 的 `--min-anchor`/`--window-seconds` 等参数
   （这些参数只能优化"模糊匹配怎么退化"，救不了识别质量本身差的问题）。
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
9. **视频质量保障**：overlay 步骤必须使用高 CRF 低质量（`-crf 14` 或更低），否则视频比特率会暴跌。合成完成后务必用 ffprobe 检查输出视频的比特率，正常应为 2-6 Mbps。
10. **歌词空隙修复**：compose_mv.py 会自动修复歌词时间戳的空隙（前一条end=后一条start），但如果原始对齐结果错误过大，建议先手动检查 `lyrics_timed.json`。
11. **Python 环境统一用 `mv_env`**：本 skill 所有 Python 脚本默认跑在
    conda 的 `mv_env` 环境里（没有就新建，有就在原有基础上补装依赖，
    不要重建），详见前面「Python 环境规范」一节；实际执行命令时记得
    在示例命令前加 `conda run --no-capture-output -n mv_env`。
12. **封面效果默认开启**：Step 0 默认 `cover_enabled: true`，做法是
    "替换第一个场景前几秒"而不是"插入新片段"，不影响总时长；用户不需要
    封面效果时，Step 0 就要问清楚并把 `cover_enabled` 设为 `false`，
    这样 Step 4 会跳过封面图生成、Step 6 不传 `--cover-image`，其余流程
    完全不受影响。
