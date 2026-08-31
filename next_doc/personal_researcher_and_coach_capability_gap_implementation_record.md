# 实施记录：Personal Researcher / Personal Coach 缺口改进（R1 + C1）

> 对应计划文档：`personal_researcher_and_coach_capability_gap_plan.md`。
> 本记录只覆盖该计划第 3 节优先级表里标为"高"的两项——R1、C1；
> R2/C2/C3/C4/R3 按计划文档的依赖关系说明暂未启动，留待观察 R1/C1
> 实际使用效果后再评估。

## 1. R1：通用持续调研模板 `research_topic`

### 1.1 做了什么

- 新增 `src/mini_agent/perception/goal_execution_spec_templates/research_topic.json`。
  与 `growth_pursuit.json` 共享同一套骨架（追加式产出物 + 三个跨轮次
  `handoff_fields`：`covered_subtopics`/`open_questions`/
  `last_source_urls` + "本轮必须有实质性增量"的 `per_cycle_criteria`），
  唯一区别是去掉了"必须来自成长顾问候选"的前提，`keywords` 换成了
  通用调研场景的词（"持续研究"/"持续调研"/"持续追踪"/"跟踪进展"/
  "长期关注"/"调研主题"），产出物路径改为 `wiki/research/<topic-slug>.md`。
- 未新增任何判定逻辑——模板匹配复用了
  `goal_execution_spec.py::suggest_template()` 已有的关键词粗筛机制
  （对 title+description 做子串计数，命中最多的模板胜出），`research_topic`
  和 `growth_pursuit` 是同一套匹配路径下的两个平行候选，不会互相冲突
  （两者关键词集合没有重叠）。
- `src/mini_agent/storage/paths.py` 新增 `wiki_research_dir` 属性和
  `research_topic_path(slug)` 方法，与已有的 `wiki_growth_dir` /
  `growth_report_path()` 对称，供未来需要直接拼路径的调用方使用
  （目前 agent 执行时是从模板的 `naming_pattern` 字段读取路径规则，
  这两个 helper 是为了保持代码风格一致，不是当前链路的必需依赖）。

### 1.2 用户如何使用

- `/agent goals add "持续追踪 XX 项目的社区讨论进展"`（或对话里表达
  类似意图）创建 Goal 后，在生成执行规范时会命中 `research_topic`
  模板（前提是标题/描述里出现了模板 `keywords` 列表中的词，命中规则
  与其它模板一致，用户也可以在 UI 里手动改选）。
- 后续绑定周期性执行（如 `/agent goals recur <goal_id> "interval:86400"`），
  每轮会往 `wiki/research/<topic-slug>.md` 追加新内容，不会重复讲已经
  覆盖过的子话题。

### 1.3 没做的部分（对应计划 R2/R3，按建议延后）

- 信源可信度标注（R2）——依赖 R1 先跑起来积累实际使用情况。
- 交叉验证质量门（R3）——计划文档明确不建议默认开启，需要先观察
  R1 落地后是否真的出现"事实性错误堆积"的问题。

## 2. C1：Goal 的"长期方向"分组

### 2.1 数据模型

- `src/mini_agent/perception/goal_backlog.py`：
  - 新增 `Direction` dataclass（`id`/`title`/`created_at`/`description`），
    独立于 `GoalNode`，不参与任何执行/判定逻辑，纯展示聚合。
  - `GoalNode` 新增可选字段 `direction_id: Optional[str] = None`，已
    接入 `to_dict()`/`from_dict()`，不影响 `GoalNode.level` 的既有
    两值约束（`"goal"`/`"objective"`），也不参与 GoalJudge 判定。
  - `GoalBacklog` 新增 `_directions: dict[str, Direction]`，随
    `load()`/`save()` 一起持久化在同一份 `goals.json` 里（顶层新增
    `"directions"` 数组），跟着 `_locked()` 走同一把跨进程文件锁，
    没有引入新的锁/新的存储文件。
  - 新增方法：`add_direction` / `list_directions` / `get_direction` /
    `rename_direction` / `delete_direction` / `assign_direction` /
    `goals_by_direction`。`delete_direction` 只清空关联 Goal 的
    `direction_id`（置 None），不会级联删除 Goal 或影响其执行状态，
    与既有"删除父节点不破坏子节点执行状态"的取舍一致。

### 2.2 API

新增于 `src/mini_agent/api/routes.py`：

| Method | Path | 说明 |
|---|---|---|
| GET | `/v1/directions` | 列出全部长期方向 |
| POST | `/v1/directions` | 新建（`{"title","description"?}`） |
| PATCH | `/v1/directions/{id}` | 重命名/改备注 |
| DELETE | `/v1/directions/{id}` | 删除（关联 Goal 自动清空分组） |
| POST | `/v1/goals/{goal_id}/direction` | 关联/取消关联（`{"direction_id": str｜null}`） |

`GET /v1/goals` 的返回值新增 `directions` 字段（与 `goals`/
`objectives` 平级），看板一次请求即可拿到全部数据，不需要为分组视图
单独发请求。

### 2.3 看板 UI

- `apps/mini_agent_kanban/client.py` 新增对应的
  `directions()`/`add_direction()`/`update_direction()`/
  `delete_direction()`/`assign_goal_direction()` 方法。
- `apps/mini_agent_kanban/app.py` 新增 `_render_goal_direction_overview()`，
  在"📌 目标看板"tab 里以折叠区块"🧭 按长期方向聚合"展示（复用
  `_render_cycle_health_overview` / 成长主题地图同款"默认折叠 + 不
  额外发请求，直接用已经拿到手的 goals_data"的模式）：
  - 新建方向（表单）
  - 每个方向卡片：标题、关联 Goal 数、关联 Goal 列表（标题+状态）、
    删除按钮
  - 未分组 Goal → 选择目标 + 选择方向 → 关联按钮

### 2.4 没做的部分（对应计划 C2/C3/C4，按建议延后）

- `decision_profile` 参与 Goal 创建提示（C2）——依赖用户已手动开启
  `sys:decision_profile_update` 并积累样本，这个前置条件本身默认关闭，
  C1 的分组落地不改变这个依赖关系。
- `next_action_advisor` 复用成长顾问证据走势规则（C3）——计划建议先
  观察 C1/C2 效果再评估是否投入。
- 全部 Goal 综合月报（C4）——明确依赖 C1 落地且需要先看分组是否被
  用户实际使用起来，C1 刚落地，暂不启动。
- CLI（`mini_agent goals ...`）目前没有补 direction 相关子命令，
  只能通过看板 UI 或直接调用 API 操作；如果后续证明分组是高频操作，
  可以补一版 CLI。

## 3. 兼容性说明

- `goals.json` 新增的 `"directions"` 顶层键、`GoalNode.direction_id`
  字段对旧数据完全向后兼容：`Direction.from_dict()`/
  `GoalNode.from_dict()` 均用 `.get()` 兜底默认值（`directions` 缺失时
  兜底空列表，`direction_id` 缺失时兜底 `None`），旧版本写入的
  `goals.json` 可以被新代码直接加载，不需要迁移脚本。
- `research_topic.json` 是纯新增文件，不改变任何已有模板/已有 Goal
  的行为；`growth_pursuit` 模板本身未做任何修改。
