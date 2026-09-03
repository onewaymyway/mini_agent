# 目标树 × 自主调研 阶段一（绑定对象迁移）实施记录

> 对应设计文档：`next_doc/goal_tree_research_and_action_recommendation_plan.md`
> §4.1 + 分阶段规划"阶段一"。

## 一、跟设计文档原文的出入说明

设计文档 §4.1 原本假设 `growth_advisor.py`/`external_input/
goal_relevance.py` 是以 `direction_id` 为主键绑定调研对象，需要"迁移到
树节点 id"。实际动手核查代码后发现：

- `external_input/goal_relevance.py`（P4 阶段既有的 `GoalRelevanceEngine`
  Stage①）本来就直接消费 `GoalBacklog` 的 `GoalNode`，没有任何
  `direction_id` 依赖——它是通用的、按节点扫描的。
- `growth_advisor.py` 里唯一的 `direction_id` 用法（`_group_pursuits_by_
  direction()` 附近）只是"按方向分组做展示统计"，不是调研触发的绑定
  主键，不需要为本方案迁移。

真正的缺口不是"绑定错了对象"，而是 `run_goal_relevance_candidate_once()`
的扫描范围写死用 `goal_backlog.active_goals()`——只看 `level=goal` 且
`status=active` 的叶子节点，完全没有考虑"现阶段焦点（`current_focus_
ids`）恰好停在 `domain`/`stage` 这种结构节点上、下面还没细化出具体
`goal`"的情况。这种情况下用户/系统最需要调研信息帮忙想清楚"这个领域
下一步该往哪个方向细化"，但旧扫描范围完全看不到这类节点。

因此阶段一的实际改动是：**扩大 `goal_relevance` 的扫描范围**，而不是
"迁移绑定主键"。设计文档 §4.1 原文保留不回改（记录当时的分析过程），
后续阶段按这里记录的实际情况继续推进。

## 二、改动内容

### 1. `src/mini_agent/perception/goal_backlog.py`

新增 `GoalBacklog.focus_research_nodes()`：

- 复用 `active_goals()` 的结果（叶子 Goal，保持完全向后兼容）；
- 额外扫描所有 active 的结构节点（`domain`/`stage`）的
  `current_focus_ids`，把其中同样是 active 结构节点的子节点并入结果
  （比如某个 `domain` 的焦点恰好是它下面一个还没细化的 `stage`）；
- 已经在叶子 Goal 集合里的 id 不重复计入；
- 结构节点没有跟叶子 Goal 混排优先级的语义，统一排在叶子 Goal 之后，
  自身按 `priority` 降序。

只读、无副作用，不改变 `active_goals()` 原有行为，现有调用方不受影响。

### 2. `src/mini_agent/external_input/goal_relevance.py`

`run_goal_relevance_candidate_once()` 里 `goal_backlog.active_goals()`
改为 `goal_backlog.focus_research_nodes()`，同步更新模块顶部 docstring
和函数 docstring 里的描述。候选写入逻辑（`goal.id`/`goal.title`/
`goal.description` 等字段读取）本来就是通用的 `GoalNode` 读取，无需
额外改动即可兼容结构节点。

### 3. 测试

新增 `tests/test_goal_focus_research_nodes.py`（4 个用例）：

- 无焦点结构节点时，结果与 `active_goals()` 完全一致；
- `current_focus_ids` 指向的 `domain` 节点被正确并入；未被引用的结构
  节点不会被误并入；
- 非 active 的焦点结构节点被正确排除；
- 焦点 id 恰好是叶子 Goal 时不重复计入。

验证：`tests/test_goal_focus_research_nodes.py`、
`tests/test_goal_relevance_candidate.py`、
`tests/test_goal_relevance_judge.py`、
`tests/test_goal_tree_phase1/2/3.py`、`tests/test_goal_backlog.py`
全部通过（123 passed）。

## 三、后续阶段的影响

- 阶段二（`FocusResearchTrigger`）设计文档 §4.2 提到的"焦点变化触发
  调研"，触发对象集合可以直接复用 `focus_research_nodes()`，不需要
  再额外写一套"焦点节点"收集逻辑。
- 阶段三/四不受本次范围调整影响，按原设计继续推进。
