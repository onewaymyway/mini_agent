# 灵感构思工作室

> 设计：`next_doc/novel_brainstorm_studio_design.md`
> 当前进度：**B0 已完成**（工作目录、卡片校验读写、定版快照、回退、列表）。B1 起的交互与 UI 尚未实现。

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
