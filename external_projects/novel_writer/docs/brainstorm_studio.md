# 灵感构思工作室

> 设计：`next_doc/novel_brainstorm_studio_design.md`
> 当前进度：**B0–B3 已完成**。B0：工作目录、卡片校验读写、定版快照、回退、列表；B1：题材卡、灵感池、发散与采纳，独立页面「💡 灵感构思」；B2：对话区与提议区；B3：世界观与角色编辑、质检。剧情骨架与备选方案在 B4。

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

## B2：对话与提议

页面第一个标签是「对话」。模式目前有「追问」（默认）和「自由」。

### 对话如何工作

- 每轮提示词包含：模式说明、当前卡片摘要（题材、命题、保留的灵感、世界观条目数、角色、剧情幕、未解决的问题）、最近 4 轮对话，以及作者的新话。
- 会话续接：通过 mini_agent 的 Agent，`load_session` 续接上一轮的会话 id（保存在 `_studio/agent_session.json`），因此模型保留完整历史。
- 为稳健起见，最近几轮对话总是附在提示词里。即使会话续接失败，模型也能接上上下文。
- 「新开对话」会丢弃会话 id，之后的第一轮不再记得之前的内容。对话记录（`_studio/session.jsonl`）保留。

### 提议：确认之前不落卡

- AI 的修改建议以 ```proposals 代码块附在回复末尾，格式为 `[{module, op, target?, value, reason}]`。解析时，正文与提议分开保存。
- 无效的提议（缺字段、模块或操作不被支持）会被计数并丢弃，不会生效。
- 提议保存在 `_studio/proposals.json`，状态为待确认、已采纳或已拒绝。
- 只有点击「采纳」才会写入卡片；采纳前会重新校验，失败则保持待确认，并记录错误原因。
- 「拒绝」只改状态，卡片不变。已处理的提议不能再次处理。

### 允许的提议

| 模块 | 操作 | 说明 |
|---|---|---|
| premise | set | 替换核心命题 |
| brief | set | 更新部分题材字段 |
| ideas | add / set | 新增灵感（来源记为 AI），或按 id 修改文字、标签、状态 |
| open_questions | add / set | 新增问题，或按 id 修改文字、关闭状态 |
| world | add | 向 rules / locations / factions / timeline / taboos 之一追加条目 |
| cast | add / set | 新增角色（需要 name），或按 id 更新字段 |
| plot | set | 替换 acts / twists / endings 中给出的键 |

### 接口

`chat_send`、`chat_history`、`chat_new`、`list_proposals`、`accept_proposal`（可带修改后的值）、`reject_proposal`，均在 `novel_writer/brainstorm/api.py`。

## 已知限制（B2）

- 默认运行器未经真实模型验证。会话续接依赖框架 Agent 的 `_session` 属性来读取会话 id，这是非公开接口，升级框架时需要留意。
- 没有快捷回复按钮，AI 的提问只能通过自由输入回答。
- 「修改后采纳」目前只有接口，界面上只有直接采纳和拒绝。
- 质检、收敛模式尚未加入（B3、B4）。

## B3：世界观、角色、质检

### 世界观与角色（直接编辑）

- 页面「世界观」标签：按分类（规则、地点、势力、时间线、禁忌）增删条目。规则可以填写代价。
- 页面「角色」标签：新增角色（名字、定位、想要、需要、缺陷），展开后可编辑全部字段，或删除角色。
- 这些编辑是作者本人的操作，立即生效；与 AI 的提议不同，不需要再确认。
- 关系写作「角色id:说明」每行一条，例如 `char_abc123:弟弟`。质检会检查 id 是否存在。

### 质检（只报告，不改卡片）

- **结构检查**（确定性，不调用模型）：命题为空；角色没有定位；关系指向不存在的角色；角色重名；规则没有代价；幕没有转折点；仍有未关闭的开放问题。
- **AI 审阅**（可选，调用一次模型）：检查设定之间的矛盾、动机是否站得住、说教风险、含糊之处。每次最多 8 条。
- 结果保存在 `_studio/checks/<id>.json`，记录内容哈希。质检前后卡片内容完全一致（测试中有专门的断言）。
- 发现项只是建议。若要修改，请在世界观或角色标签中手动调整，或通过对话提议。

### 接口

`world_add`、`world_remove`、`cast_add`、`cast_update`、`cast_remove`、`run_check`（参数 `use_llm`）、`check_history`，均在 `novel_writer/brainstorm/api.py`。

## 已知限制（B3）

- AI 审阅的默认生成器未经真实模型验证（与 B1、B2 相同）。
- 结构检查只做了几项明确的规则，没有检查剧情与世界观之间的语义矛盾，那部分要靠 AI 审阅。
- 剧情骨架的编辑界面在 B4 加入。
