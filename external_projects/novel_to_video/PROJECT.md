# novel_to_video — 小说转短剧外部项目

> 遵循 `external_projects` 标准结构，自包含，可独立于 daemon 运行。
> 完整设计方案：`next_doc/novel_to_video_external_project_plan.md`（实施进度与偏差记录见其第 14 节）。
> 使用说明：[`docs/README.md`](./docs/README.md)。

## 目标

把 `novel_writer` 导出的成书，按「全书级公共素材 → 章节级小场景视频（逐镜确认）→ 章节合成 → 全书合成」的流程，转换为一部竖屏短剧视频。

- 全书级设定（角色、地点、公共素材）一次准备、跨章复用；
- 章节是生产与交付的节奏单位，章内以小场景为审核单位；
- 片头封面与转场统一放在最后的全书合成阶段处理；
- 完全独立实现，LLM 步骤沿用 mini_agent 的 `skill_agent` 调用模式，图像/视频/TTS 沿用 novel-video-studio 的 key 池机制。

## 里程碑进度

| 期 | 状态 | 内容 |
|---|---|---|
| Phase 0 脚手架与导入 | **已完成（2026-11-01）** | 只读导入 novel_writer 导出目录（章节/角色/地点/问题清单/指纹）；原子写入的项目目录创建；章节范围选择；dry-run 检查；项目列表与导出变更检测；Streamlit 导入页与项目页；入口 `import_novel` / `list_projects` / `health`；测试 40 项通过 |
| Phase 1 全书级素材 | 未开始 | G-2 视觉与声音补全（含性别补全）、G-3 角色/地点定妆图生成与逐条审核 |
| Phase 2 单章端到端 | 未开始 | C-1 至 C-9：剧本、分镜、补建分支、配音、提示词与一致性核查、小场景视频、章节合成 |
| Phase 3 多章循环与挡位 | 未开始 | 章节排队、G0–G3 挡位、预算、修改分级回退与 stale 传播 |
| Phase 4 全书合成与收尾 | 未开始 | F-1 至 F-4：章节检查、封面、全书拼接、成片校验、G4/G5 |
| Phase 5 加固 | 未开始 | 桩全链路测试、真实 API 冒烟、崩溃恢复与 key 切换演练 |

## 当前能力（Phase 0）

- `source_import.py`：读取导出目录，产出章节清单（章号、章名、字数、总结文件）、角色库（映射自 `设定/人物/角色/char_*.md`，补充人设卡「外在呈现」）、地点库（映射自 `设定/世界观/地点详情/*.md`）、问题清单（error / warning / info）与目录指纹。**不写任何文件。**
- `project_store.py`：项目目录 `<书名>_<时间戳>`，原子写入 JSON/YAML，章节范围解析（`all`、`1-10`、`20-`、`3,7,9-12`），PROGRESS.md 只追加。
- `tool_api.py`：UI/CLI/daemon 统一接口，返回 `{ok, data, error}`。
- 入口：`entrypoints/import_novel.py`、`list_projects.py`、`health.py`。
- 界面：`streamlit run app.py`，导入页（检查 → 确认报告 → 创建）与项目页（章节表、导出变更提示、进度日志）。

## 已知限制

- **导出包没有性别字段**：21 个角色的 `gender` 全部为 null，导入时以 `MISSING_GENDER` 警告提示，需在 G-2 补全（会标记 `source: generated`）。
- **导出包没有书名**：默认取目录名去掉时间戳（如 `小说`），导入页可修改；建议每次导入都填写真实书名。
- **导出包自带清洗提示**：第 8、43、47 章残留工具标签或截断行已被删除，第 21 章在写作备注后有 14 行疑似重复结尾。这些以 `EXPORT_CLEANUP_NOTE` 警告形式保留，**需人工核对**，导入不会自动修正正文。
- **角色关系未导入**：`relations` 目前为空数组，后续从人设卡「关系」段落补充。
- **年龄段阈值**为经验值：<13 child，<18 teen，<35 youth，<60 middle_aged，其余 elderly。
- 章节目录统一三位补零（`chapters/001/`），与 `设计方案` 中的 `NN` 有偏差，原因见设计文档第 14 节。
- 目前没有文件锁，同一项目只应由一个进程写入（界面与 CLI 同时写同一项目未做保护）。
- Phase 0 不调用任何外部 API，视频/图像/TTS 的 key 池尚未接入。

## 目录约定

```
external_projects/novel_to_video/   # 代码（本目录）
novel_output/                       # 项目数据（默认位置，已加入 .gitignore）
└── <书名>_<YYYYmmdd_HHMMSS>/
    ├── project.json                # 全局配置（时长、横竖屏、TTS、挡位、预算）
    ├── PROGRESS.md                 # 决策与反馈日志（追加）
    ├── source/manifest.json        # 导出目录路径、指纹、导入统计与问题
    ├── global/characters.json      # 角色库
    ├── global/locations.json       # 地点库
    ├── global/review.json          # 全书级素材审核状态（Phase 1 使用）
    ├── global/assets/              # 定妆图（Phase 1 起）
    ├── chapters/index.yaml         # 章节清单与选中状态
    ├── chapters/001/ ...           # 章节工作目录（选中章节创建）
    ├── exports/                    # 全书成片（Phase 4 起）
    └── logs/
```
