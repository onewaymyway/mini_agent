# MV（歌词同步视频）生成 Skill 设计与实施计划

> **这篇文档管什么**：新增一个 `.claude/skills/mv-generator` skill，输入一首
> 歌的 mp3 音频 + 歌词文本，输出一个歌词与画面同步、画面内容贴合歌词语义
> 的 MV（mp4）。
>
> **不管什么**：不新增 workflow 引擎能力，不新增外部项目机制能力，本 skill
> 是在现有 `gen_image_with_text`、`gen_video_with_text` 两个 skill 之上，
> 参照 `comic-4panel`（多步编排 + 产物强制落盘）的模式做的一层编排与新增
> 的 ASR/对齐/合成脚本，不改动 mini_agent 框架本身。

## 1. 背景与选型结论

用户想要：输入 mp3 + 歌词，生成一支歌词同步显示、画面贴合歌词内容的 MV。

对比三种落地方式：

| 方式 | 结论 | 原因 |
|---|---|---|
| 外部项目（`external-project-manager`） | 不用 | 定位是"持续运行、有独立生命周期"的领域系统（如 `stock_watch` 长期监控），MV 生成是一次性/按需内容创作任务，不需要独立注册和 daemon 生命周期。 |
| 纯 workflow.yaml | 不单独用 | workflow 引擎擅长结构固定的 DAG，但"歌词怎么切场景""哪些角色/场景要保持一致""每段画面怎么描述"是需要语义理解和创造性判断的步骤，不适合硬编码进 YAML 的 condition/step；workflow 的 `foreach`/`script`/`python_step` 可以在 skill 内部被当工具用于批量视频生成这类结构化重复步骤，但不作为顶层入口。 |
| skill（仿 `comic-4panel`） | **采用** | `comic-4panel` 已验证"多步骤 + 每步产物强制落盘 + 关键节点向用户确认 + 内部调用其他 skill"的模式，与本任务的流程完全匹配。 |

## 2. 已确认的技术选型（与用户确认结果）

| 决策点 | 选择 |
|---|---|
| ASR 方案 | 本地 `faster-whisper`（离线、免费） |
| 字幕形式 | 烧录进画面（hardsub，ffmpeg `subtitles` filter） |
| 成本预期 | 接受一首歌拆 15–25 个场景片段、15–25 次 `gen_video` 调用 |
| 人物一致性 | 接受 `reference` 模式"风格接近但非像素级一致"的现实上限 |
| 运行环境 | 用户本机 Windows PowerShell，需要用户自行确认/安装 ffmpeg |

## 3. 目标与非目标

**目标**：
- 新增 `.claude/skills/mv-generator` skill，产出：
  1. 歌词逐句时间戳（ASR + 对齐校正）
  2. 场景规划（agent 创造性步骤，需用户确认）
  3. 角色/场景定妆图（复用 `gen_image_with_text`）
  4. 分场景视频片段（复用 `gen_video_with_text` 的 `reference`/`keyframe` 模式）
  5. 最终合成的 `mv.mp4`（拼接 + 烧录字幕 + 混入原始音轨）
- 新增底层脚本（ASR、对齐、合成），风格与现有 `gen_image.py`/`gen_video.py` 一致
- 更新 `docs/commands-and-tools-reference.md`、`docs/system-overview.md`、
  新增 `docs/mv-generator-guide.md`

**非目标**：
- 不追求人物像素级一致性（已与用户确认接受现实上限）
- 不做云端 ASR 集成（本期只做本地 faster-whisper，云端留作后续可选扩展点）
- 不在本期做“自动重试/人工筛选素材质量”的复杂机制，先跑通主流程

## 4. 整体流程

```
Step 0  输入：mp3 路径 + 歌词文本（纯文本，逐行）
Step 1  ASR 粗识别（scripts/asr_transcribe.py，faster-whisper）
        → asr_raw.json（词/行级时间戳草稿）
Step 2  歌词对齐校正（scripts/align_lyrics.py，difflib 序列对齐 + 时间戳插值）
        → lyrics_timed.json（逐句准确歌词 + 校正后 start/end 时间戳）
Step 3  场景规划（agent 创造性步骤，SKILL.md 指令驱动，非脚本）：
        按歌词内容与情绪切分场景（单段 ≤ 12s，超过则再拆），
        为每段写画面描述，标注需要保持一致性的角色/场景
        → scene_plan.yaml，展示给用户确认
Step 4  定妆图生成：复用 gen_image_with_text，对重复出现的角色/场景先生成
        锚点图（复杂场景用多图合成）→ assets/*.png
Step 5  分场景视频生成：复用 gen_video_with_text 的 reference 模式
        （传入定妆图 + 该段 prompt），必要时用 keyframe 模式做转场衔接
        → clips/scene_XX.mp4
Step 6  合成（scripts/compose_mv.py，ffmpeg）：
        拼接所有 clip → 按 lyrics_timed.json 生成 .srt 并烧录字幕
        → 混入原始 mp3 音轨 → mv.mp4
Step 7  校验交付：检查总时长是否与音频对齐，展示给用户
```

## 5. 新增文件清单

```
.claude/skills/mv-generator/
├── SKILL.md                    # 编排型 skill 主文档（仿 comic-4panel 风格）
├── scripts/
│   ├── asr_transcribe.py       # faster-whisper 本地语音识别，输出词/行级时间戳
│   ├── align_lyrics.py         # 用标准歌词校正 ASR 结果，输出逐句时间戳 JSON
│   └── compose_mv.py           # ffmpeg 拼接 + 烧录字幕 + 混音，输出最终 mv.mp4
docs/
└── mv-generator-guide.md       # 使用指南（仿 comic-4panel-guide.md）
next_doc/
└── mv_generation_skill_plan.md # 本文档
```

## 6. 需要更新的既有文档

- `docs/commands-and-tools-reference.md`：技能列表新增 `mv-generator` 一行
- `docs/system-overview.md`：技能列表新增 `mv-generator` 一行，并链接到使用指南
- `requirements.txt`：新增 `faster-whisper`（作为可选依赖，注释说明仅
  `mv-generator` skill 需要，遵循"新功能保守默认、显式开启"的约定——
  不默认强制安装，SKILL.md 里会提示用户按需 `pip install faster-whisper`）

## 7. 风险与依赖说明

1. **faster-whisper 依赖较重**（模型下载 + 依赖库），本期不写入
   `requirements.txt` 的强制安装区，而是作为注释建议，避免影响不需要该
   功能的用户的常规安装流程（符合"保守默认"的约定）。
2. **ffmpeg 依赖外部系统安装**，`compose_mv.py` 运行前会检测 `ffmpeg`
   是否在 PATH 中，找不到时给出清晰的安装提示（Windows 下建议
   `winget install ffmpeg` 或官网下载），不在脚本里静默失败。
3. **单 clip 最长 12 秒的限制**是 `gen_video_with_text` 底层 API 的硬限制，
   场景规划阶段必须显式校验每段时长，超限自动拆分，避免生成失败到最后
   一步才发现。
4. **人物一致性漂移**是已知限制，`SKILL.md` 中会提示 agent 尽量复用同一张
   定妆图作为 reference，减少（但不能消除）漂移。

## 8. 实施顺序

1. 本计划文档（当前文件）
2. `.claude/skills/mv-generator/scripts/asr_transcribe.py`
3. `.claude/skills/mv-generator/scripts/align_lyrics.py`
4. `.claude/skills/mv-generator/scripts/compose_mv.py`
5. `.claude/skills/mv-generator/SKILL.md`
6. 更新 `docs/commands-and-tools-reference.md`、`docs/system-overview.md`
7. 新增 `docs/mv-generator-guide.md`
8. 打包所有新增/修改文件（diff-only zip）供下载
