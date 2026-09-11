# novel-entity-extractor

小说转视频 v2 流程第 1 步：全局角色/地点抽取（取代 v1 的
`novel-scene-planner` 里对应部分）。详细规范见 `SKILL.md`；整体架构见
`next_doc/novel_video_generator_plan_v2.md`。

## 依赖

无外部 API/模型依赖，纯 Agent 推理 + 文件读写；校验脚本只依赖 Python
标准库（不需要 pyyaml，因为本 skill 只读写 JSON）。

## 输入 / 输出速查

| | 路径 | 说明 |
|---|---|---|
| 输入 | 小说全文（用户给的文件路径或粘贴文本） | 长/短篇均可 |
| 输出 | `<output_dir>/novel_project.json` | 全局配置 |
| 输出 | `<output_dir>/global/characters.json` | 角色库，含 `voice_profile` |
| 输出 | `<output_dir>/global/locations.json` | 地点库 |

## 两种运行模式

- **模式 A（全文抽取）**：新建项目时用，长篇分块通读+增量合并；
- **模式 B（单点补抽取）**：`novel-scene-detail-planner` 发现大场景引用
  了库里没有的角色/地点时回调本 skill，只处理指定文本片段，不重跑全文。

## 脚本用法

```bash
python .claude/skills/novel-entity-extractor/scripts/check_entities.py \
  <output_dir>
```

退出码 0 = 通过，1 = 有问题（详见 stdout 的 `errors`/`warnings`）。

## 与相邻 skill 的交接条件

- 上游：无（本 skill 是流程起点，或由 `novel-scene-detail-planner` 以
  模式 B 回调）；
- 下游：`novel-macro-scene-planner` 读取 `novel_project.json` +
  `global/characters.json`/`global/locations.json` 开始大场景切分；只有
  `check_entities.py` 通过后才能交接。
