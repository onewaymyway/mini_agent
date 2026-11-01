# novel_to_video 使用说明

把 `novel_writer` 导出的小说转换为竖屏短剧视频的工作台。当前版本（Phase 0）完成导入与项目创建，视频生成尚未接入。

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

## 4. 导入之后要做什么

导入报告中的提示需要人工确认：

1. 核对「清洗警告」列出的章节（第 8、43、47 章是删除工具残留，第 21 章是疑似重复结尾）；
2. 角色性别缺失将在 Phase 1（G-2）补全，届时会标注来源为生成；
3. 书名如不正确，可在项目目录的 `project.json` 中修改 `title`（仅影响显示）。
