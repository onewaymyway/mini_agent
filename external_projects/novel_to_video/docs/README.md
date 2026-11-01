# novel_to_video 使用说明

把 `novel_writer` 导出的小说转换为竖屏短剧视频的工作台。当前版本（Phase 0–2）完成导入、项目创建、全书级素材（角色/地点的视觉设定、声音设定与定妆图），以及单章流水线（剧本 → 镜头 → 配音 → 小场景视频 → 逐镜审核 → 章节合成）。全书合成与封面转场属于 Phase 4。

## 环境

```bash
cd external_projects/novel_to_video
pip install -r requirements.txt
python entrypoints/health.py        # 输出 ok 即健康
```

## 1. 导入导出目录

导出目录是 `novel_writer` 生成的 `小说_YYYYMMDD_HHMMSS/`，需要包含 `正文/`、`设定/人物/角色/`、`README.md`。

### 界面

```bash
streamlit run app.py
```

1. 打开「导入导出目录」，填写导出目录路径；
2. 填写书名（导出包不含书名）；
3. 章节范围留空即全部章节，也可填 `1-10,12,20-`；
4. 点击「检查导出目录」，查看章节数、角色数、地点数与问题清单；
5. 确认无阻断错误后点击「创建项目」。

检查过程只读，不会修改导出目录，也不会创建任何文件。

### 命令行

```bash
# 只检查，不创建项目
python entrypoints/import_novel.py "D:/novels/小说_20261101_101309" --dry-run

# 创建项目（默认写入 novel_output/）
python entrypoints/import_novel.py "D:/novels/小说_20261101_101309" --title "铁城" --chapters "1-20"
```

输出为 UTF-8 JSON。导入存在阻断错误时退出码为 1，且不会创建任何目录。

## 2. 问题等级

| 等级 | 含义 | 是否阻断 |
|---|---|---|
| error（阻断） | 缺少正文目录、章节号重复、角色 id 重复、导出目录不存在等 | 是 |
| warning（提示） | 章节号不连续、角色缺性别、章节过短、导出清洗提示等 | 否，但需要关注 |
| info（信息） | 没有章节总结、没有全书合稿等 | 否 |

## 3. 项目目录

创建后得到 `novel_output/<书名>_<时间戳>/`，内容见 `PROJECT.md` 的「目录约定」。

- `project.json` 记录导入时的配置；之后修改它会影响后续阶段，请只在明确需要时手动编辑；
- `chapters/index.yaml` 中的 `selected` 决定哪些章节进入后续流程；
- `PROGRESS.md` 只追加，用于记录决策与反馈，跨 session 续接时先读它；
- 源目录被修改后，项目页会提示指纹不一致，此时应重新导入，而不是继续使用旧项目。

## 4. 全书级素材（G-2 / G-3）

### 配置

- 图像：设置环境变量 `AGNES_API_KEYS`（逗号分隔，可配多把 key，遇限流自动切换）或 `AGNES_API_KEY`。key 只从环境变量读取，不写入项目文件。
- 文本：使用 mini_agent 的 LLM 配置（与 world_simulator、novel_writer 相同的 `providers.json` 体系）。
- 离线演示：设置 `NOVEL_TO_VIDEO_PROVIDERS=stub`，图像生成占位图，文本使用规则兜底。此模式不消耗额度，但结果不可用于正式制作。

### 命令行

```bash
# 1) 补全视觉锚点、声音设定、缺失性别（只补缺失项）
python entrypoints/build_assets.py novel_output/<项目> --stage text

# 2) 为已补全设定的实体生成定妆图（已有有效图的跳过）
python entrypoints/build_assets.py novel_output/<项目> --stage portrait

# 3) 只重生成某几个实体的定妆图（生成 _v2 等新版本）
python entrypoints/build_assets.py novel_output/<项目> --stage portrait --ids char_001 --regenerate

# 也可以一次做完 1 和 2：--stage all（默认）
```

输出为 JSON，包含本次成功/失败/跳过的实体与当前审核状态。存在失败项时退出码为 1，失败原因写在 `global/review.json` 的 `last_error` 中，修正后重跑即可，已成功的实体不会重复生成。

### 界面

「全书素材」页：点击按钮执行 G-2 或 G-3，然后在画廊中逐条审核：

- 「通过设定」：确认视觉锚点、声音设定与性别无误（缺失字段时不可通过）；
- 「通过定妆图」：确认图片可用于后续章节；
- 「驳回并重生成」：生成新版本图片；
- 「查看与编辑设定」：人工修改 visual_anchor_en、voice_profile、性别。改动视觉字段后，已通过的定妆图会标记为「需重生成」。

所有角色与地点的设定与定妆图都通过后，页面会显示「可以进入章节生产」，这是 Phase 2 的前置门禁。

### 来源标记

每个被生成或人工修改的字段都带有 `*_source` 标记：`miniagent`（真实 LLM）、`rules`（规则兜底，需审核）、`manual`（人工编辑）。导出包原有内容没有来源标记。

## 5. 章节生产（C-1 … C-9）

### 配置

- 视频：`AGNES_API_KEYS` 或 `AGNES_API_KEY`（与图像共用同一套 key）。
- 配音：默认 edge-tts，需要 `pip install edge-tts` 并能访问微软语音服务；未安装时会提示错误。离线测试可用 `NOVEL_TO_VIDEO_PROVIDERS=stub`。
- ffmpeg：需要在 PATH 中，或设置 `FFMPEG_BIN` / `FFPROBE_BIN`。
- 单章目标时长在 `project.json` 的 `target_chapter_duration_sec` 中设置（默认 90 秒）。剧本会按 `时长 × 4.2 字/秒` 压缩口播字数，超过目标 1.6 倍即校验失败。

### 运行

```bash
# 运行全部已选中章节（可重入，已完成的阶段会跳过）
python entrypoints/run_chapter.py novel_output/<项目>

# 只运行部分章节；--force-script 强制重新生成剧本
python entrypoints/run_chapter.py novel_output/<项目> --chapters 1-3
```

输出 JSON，每章的 status 为：

| status | 含义 | 下一步 |
|---|---|---|
| `waiting_assets` | 镜头里出现了库中没有的角色，或缺少定妆图 | 到「全书素材」页确认补建角色的文本设定与定妆图，再重新运行 |
| `waiting_review` | 小场景视频已全部生成 | 到「章节生产」页逐镜审核，再重新运行 |
| `failed` | 某阶段失败，`message` 说明原因 | 修正后重新运行 |
| `done` | 章节成片 `chapters/NNN/chapter_NNN.mp4` 已生成 | — |

### 界面

「章节生产」页：选择章节 → 运行本章 → 查看阶段状态与剧本 → 逐个小场景预览视频与口播音频，然后：

- 「通过」：确认该镜头；
- 「驳回并重生成」：填写意见后删除旧视频，下次运行时重新生成；
- 「编辑镜头内容」：修改英文画面描述、中文描述或口播文字。对白必须仍能在原文中逐字找到。保存后已通过的审核会变为「需重审」，需要重新运行本章。

### 补建角色

剧本中出现的新说话人会被自动起草为角色（`source: auto_added`，`auto_added_chapter` 为出现的章节号）。在全书素材页确认其文本设定和定妆图之前，本章不会进入配音和视频生成。

## 6. 导入之后要做什么

导入报告中的提示需要人工确认：

1. 核对「清洗警告」列出的章节（第 8、43、47 章是删除工具残留，第 21 章是疑似重复结尾）；
2. 角色性别缺失可在 G-2 由真实模型推断，或在「全书素材」页人工补全；
3. 书名如不正确，可在项目目录的 `project.json` 中修改 `title`（仅影响显示）。
