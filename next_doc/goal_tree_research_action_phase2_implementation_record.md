# 目标树 × 自主调研 阶段二（焦点驱动调研）实施记录

> 对应设计文档：`next_doc/goal_tree_research_and_action_recommendation_plan.md`
> §4.2 + 分阶段规划"阶段二"。前置阅读：
> `next_doc/goal_tree_research_action_phase1_implementation_record.md`
> （阶段一，`GoalBacklog.focus_research_nodes()` 的由来）。

## 一、跟设计文档原文的出入说明

设计文档 §4.2 把 `FocusResearchTrigger` 设想成"调用 `external_input` 现有
的调研能力（`watchlist.py`/`tech_radar_search.py`/`knowledge_extractor.py`
视节点内容匹配现有哪类信息源）"，产出"素材追加到该节点关联的素材流"。
实际动手核查 `growth_advisor.py` 后发现更合适的落点：

- `growth_advisor.py` 已经有一套成熟的"候选 → 用户确认 → 自动持续调研"
  全链路（`GrowthBacklog.add_or_merge()` 生成候选 → 用户 accept →
  `auto_pursue_candidate()` 落地成 Goal + 绑定 `growth_pursuit` 周期
  执行规范），并且这条链路**本来就是按 `GoalNode.id` 运作**（`linked_
  goal_id` 直接指向树上的 Goal），不是按 `direction_id`。
- 与其新开一条"素材流"直接调用底层信息源（`watchlist`/`tech_radar_
  search` 等），不如让 `FocusResearchTrigger` 复用 `GrowthBacklog.
  add_or_merge()` 这一层——好处是自动继承现有的字面去重、语义判重
  （`llm_helper` 传入时）、dismissed 冷却期、pending 数量上限、以及
  用户 accept 之后"自动持续调研"的完整下游能力，而不需要重新实现一遍
  节奏治理和素材追加逻辑。

因此阶段二的实际改动是：**新增 `FocusResearchTrigger`，把焦点节点包装成
一条 `origin="focus_research"` 的 `GrowthCandidate` 喂给现有
`GrowthBacklog`**，而不是直接对接底层信息源。§4.2 原文保留不回改（记录
当时的方案设想），信息源层面的"针对该节点具体查什么"仍然是
`generate_growth_report()`/后续调研流程的既有职责，本阶段不改动。

## 二、改动内容

### 1. 新增 `src/mini_agent/evolution/focus_research_trigger.py`

`FocusResearchTrigger` 类：

- `should_trigger(node)`：节奏治理判断，结构节点（`domain`/`stage`）最小
  触发间隔 7 天，叶子 `goal` 最小触发间隔 2 天（对应设计文档 §4.4"分层
  节奏，越往叶子越贴近执行"），上次触发时间记在
  `<workdir>/goal_tree_focus_research_state.json`（跟
  `GoalTreeDecomposer` 的 `goal_tree_decompose_state.json` 同一种存储
  方式，按 `node_id` 存时间戳）。
- `trigger(node_id, *, cfg=None, llm_helper=None, force=False)`：节点
  存在性检查 + 节奏治理检查（`force=True` 可跳过）→ 记录触发时间 → 拼
  一句结构化 rationale（带祖先链，格式"「事业 → 换工作」目前是你的现阶段
  焦点（目标），建议主动了解相关信息…"）→ 调用
  `GrowthBacklog.add_or_merge()`，`evidence_refs=["goal_tree:<node_id>"]`
  （只需 1 条占位证据，因为触发动机是"树的结构信号"而不是"外部证据
  数量"，语义上跟 `soft_goal_deriver`/`goal_relevance` 的证据数量门槛
  不是一回事）、`origin="focus_research"`。
- 独立函数 `find_newly_focused_nodes(backlog, previous_focus_ids)`：对比
  `focus_research_nodes()` 前后两次结果，返回新增部分——供阶段四接入
  `sys:goal_tree_focus_recompute` 巡检后调用，本阶段只实现这个纯函数，
  不接自动巡检（跟设计文档"阶段二先用 CLI 手动触发验证，不接自动巡检"
  的分工一致）。

### 2. `src/mini_agent/cli/commands/goals.py`

新增子命令 `/agent goals research <id> [--force]`：

- 节点不存在 / 节奏治理跳过时给出明确提示（跟 `decompose` 子命令的
  `--force` 交互风格一致）；
- 成功生成/合并候选后提示 `candidate_id`，并说明要用
  `/agent growth accept|dismiss <candidate_id>` 处理（不是
  `/agent goals candidates`——目标树的分解候选和成长顾问的调研候选是
  两套独立队列，命令入口分开，避免用户搞混）；
- 顶层 `Available:` 提示列表和 usage 分发逻辑同步补上 `research`。

### 3. 测试

新增 `tests/test_focus_research_trigger.py`（7 个用例）：

- `trigger()` 正确生成 `origin="focus_research"` 的候选，落进
  `GrowthBacklog`；
- 节点不存在时返回 `None`；
- 节奏治理：间隔内第二次触发被跳过，`force=True` 时绕开间隔但命中
  `GrowthBacklog` 自身的字面去重（不会生成重复候选）；
- 结构节点确实使用比叶子 Goal 更长的最小间隔；
- `find_newly_focused_nodes()` 只返回新增部分，空差集时返回空列表。

验证：`tests/test_focus_research_trigger.py`、
`tests/test_goal_focus_research_nodes.py`（阶段一）、
`tests/test_goal_relevance_candidate.py`、`tests/test_goal_relevance_judge.py`、
`tests/test_goal_tree_phase1/2/3/4.py`、`tests/test_goal_backlog.py`、
`tests/test_growth_advisor.py`、`tests/test_growth_advisor_auto_pursue.py`
全部通过（326 passed）。

## 三、后续阶段的影响

- 阶段三（`next_action_advisor` 新增 `focus_next_step` 候选类型）可以
  直接读 `GrowthBacklog.pending()` 里 `origin == "focus_research"` 的
  候选，判断"这个焦点节点有没有待处理的调研候选"，作为 §4.3 里"有新
  调研素材待查看"这一类建议的数据来源，不需要额外的数据结构。
- 阶段四把 `find_newly_focused_nodes()` 接入
  `sys:goal_tree_focus_recompute` 巡检时，需要额外决定
  `previous_focus_ids` 的持久化位置（比如复用 `FocusResearchTrigger`
  同一个 state 文件里新增一个 `_last_focus_snapshot` 字段，或独立一个
  小文件）——本阶段函数签名已经把这个决定留给调用方，不在
  `find_newly_focused_nodes()` 内部读写状态，阶段四实施时再定具体存储
  位置。
