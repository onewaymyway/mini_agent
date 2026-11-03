# 灵感构思工作室

> 设计：`next_doc/novel_brainstorm_studio_design.md`
> 当前进度：**B0、B1 已完成**。B0：工作目录、卡片校验读写、定版快照、回退、列表；B1：题材卡、灵感池、发散与采纳，以及独立页面「💡 灵感构思」。对话与提议区在 B2。

## 独立项目

构思项目与小说项目并列，默认放在 `data/brainstorms/<名称>_<时间戳>/`。它不参与小说的步骤流水线。

```
<root>/
  _studio/brainstorm.json   元信息；有这个文件才算构思项目（kind = brainstorm）
  working/                  当前草稿（ideas.json brief.json premise.md world.json cast.json plot.json open_questions.json）
  versions/v<n>/            定版快照，只增不删，含 version.json（n、时间、备注、内容哈希）
  options/                  备选分支（B4）
```

书架索引没有改动：构思项目通过 `_studio/brainstorm.json` 的 `kind` 字段识别，不需要迁移旧数据。

## 卡片

| 模块 | 文件 | 结构 |
|---|---|---|
| ideas | ideas.json | 列表；每条 id、text、tags、source（author/ai）、status（keep/park/drop/used） |
| brief | brief.json | 与新建向导字段一一对应（`intake.INTAKE_KEYS`），只接受这些字段 |
| premise | premise.md | 文字 |
| world | world.json | rules / locations / factions / timeline / taboos；规则类条目需要 text，可选 cost |
| cast | cast.json | 以角色 id 为键；必须有 name；可选 role/want/need/flaw/lie/arc/relations |
| plot | plot.json | acts（每幕 title + beats）、twists、endings |
| open_questions | open_questions.json | 列表；每条 id、text、closed |

所有写入都经过 `cards.put_module`：先校验，再原子写入。不合法的数据会被拒绝，文件保持原样。

## 版本

- `snapshot(ws, note)`：把草稿定版为 v<n>。版本目录已存在时拒绝覆盖。
- `restore(ws, n)`：把草稿恢复为 v<n>，并记为一个新版本（备注「回退到 v<n>」）。历史不删除。
- 版本里不存在的模块文件，回退后会被删除，保证草稿与该版本一致。
- `content_hash(ws)`：按模块文件内容计算哈希，用于之后交接时溯源。

## 已知限制（B0）

- 没有对话、提议区和界面，只能通过代码读写。
- 备选分支目录已预留，但没有创建与对比逻辑（B4）。
- 卡片还没有补丁结构。AI 提议与确认写入在 B2 引入。

## B1：题材卡、灵感池、发散

入口：侧栏「💡 灵感构思」。页面分三个标签：

- **题材卡**：与新建向导的字段一一对应，保存时经过校验。
- **灵感池**：手动添加灵感（可加标签）；每条可设为保留 / 搁置 / 已采用 / 丢弃。丢弃只改状态，不删除记录。
- **发散**：输入一个方向（可空），让 AI 给出 3–10 条候选。候选保存在 `_studio/spark/<id>.json`，**不会直接进入灵感池**；勾选后点「采纳所选」才会写入灵感池，来源记为 AI。已采纳的候选不能重复采纳。

### 发散的实现

- 提示词包含：题材卡中已填的字段、已有灵感（排除已丢弃的，最多 40 条，避免重复）、方向、数量。要求模型只输出 JSON 数组。
- 解析容忍代码块与前后文字；取不到有效候选时报错，不写任何文件。
- 生成器默认走 mini_agent 的受限 Agent（无工具、单轮，复用 `runner._build_restricted_agent`）。测试中替换为桩，不调用真实模型。
- **未经真实模型验证**：默认生成器的调用路径已接通，但我没有在有 API key 的环境里跑过，第一次使用时请留意输出是否正常。

### 对外接口

`novel_writer/brainstorm/api.py`，统一返回 `{ok, data}` 或 `{ok: false, error, error_type}`：
`list_projects`、`create_project`、`get_project`、`save_brief`、`list_ideas`、`add_idea`、`update_idea`、`spark`、`spark_history`、`adopt_candidates`。

## 已知限制（B1）

- 对话与提议区尚未实现（B2）。AI 目前只能通过一次性的发散参与，不能提议对卡片的修改。
- 卡片的编辑只支持题材卡和灵感池；世界观、角色、剧情骨架的界面在 B3 加入，现阶段只能通过代码写入。
- 备选方案分支目录已预留，没有创建与对比逻辑（B4）。
