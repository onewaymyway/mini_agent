# novel_to_video 使用说明

把 `novel_writer` 导出的小说转换为竖屏短剧视频的工作台。当前版本（Phase 0–1）完成导入、项目创建与全书级素材（角色/地点的视觉设定、声音设定与定妆图）；章节视频生成尚未接入（Phase 2）。

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

## 5. 导入之后要做什么

导入报告中的提示需要人工确认：

1. 核对「清洗警告」列出的章节（第 8、43、47 章是删除工具残留，第 21 章是疑似重复结尾）；
2. 角色性别缺失可在 G-2 由真实模型推断，或在「全书素材」页人工补全；
3. 书名如不正确，可在项目目录的 `project.json` 中修改 `title`（仅影响显示）。
