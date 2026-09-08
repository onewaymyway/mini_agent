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
- `scripts/check_scene_plan.py`：校验 `scene_plan.yaml`（单场景时长范围、
  时间轴连续性、是否覆盖音频总时长），Step 3 写完必须跑，不通过不能进入 Step 4
- `scripts/check_assets.py`：校验 Step 4 生成的定妆图是否都已落盘（路径
  已回填、文件存在且非空、场景引用无悬空），Step 4 做完必须跑，不通过
  不能进入 Step 5
- `scripts/generate_scene_videos.py`：批量生成分场景视频，Step 5 用它代替
  逐个手动调用 `gen_video_with_text`，内置 key 池自动切换 + 失败重试 + 断点续跑
- `scripts/compose_mv.py`：ffmpeg 最终合成（逐 scene 独立缩放 + PIL 字幕/水印）
- `scripts/fix_lyrics.py`：修复歌词时间戳空隙（前一条end=后一条start）

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
- **单调窗口约束**：引入时间游标 `cursor_time`，确保对齐结果在时间上
  单调不减，避免重复段落导致的张冠李戴问题。

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
{
  "duration": 239.04,
  "lines": [
    {"line": 0, "text": "歌词第一句", "start": 0.0, "end": 8.2},
    {"line": 1, "text": "歌词第二句", "start": 8.2, "end": 14.5}
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

只有校验脚本通过之后，才展示给用户确认（可以摘要展示场景数量、总
时长核对、关键角色定妆图规划），**等用户确认或提出修改意见后再进入
Step 4**，这是本流程里唯一的强制确认点（仿 comic-4panel 在关键节点
向用户确认的做法）。

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
  --aspect-ratio 16:9
```

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
   4-12 秒范围内（Step 3 的校验已保证这个范围本身没问题）。
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
  --title-pos top-right
```

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

### Step 7: 校验交付

- 用 `ffprobe`（随 ffmpeg 一起安装）检查 `mv.mp4` 的总时长，和原始
  mp3 时长做对比，差异明显（比如超过 2 秒）要向用户说明原因（通常是
  Step 3 场景时长规划有累积误差）。
- 检查视频比特率是否正常（应 > 1 Mbps），否则说明 overlay 失败。
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
9. **视频质量保障**：overlay 步骤必须使用高 CRF 低质量（`-crf 14` 或更低），否则视频比特率会暴跌。合成完成后务必用 ffprobe 检查输出视频的比特率，正常应为 2-6 Mbps。
10. **歌词空隙修复**：compose_mv.py 会自动修复歌词时间戳的空隙（前一条end=后一条start），但如果原始对齐结果错误过大，建议先手动检查 `lyrics_timed.json`。
