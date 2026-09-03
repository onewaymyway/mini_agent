# 目标树 × 自主调研 阶段三（焦点行动建议）实施记录

> 对应设计文档：`next_doc/goal_tree_research_and_action_recommendation_plan.md`
> §4.3 + 分阶段规划"阶段三"。前置阅读：
> `next_doc/goal_tree_research_action_phase2_implementation_record.md`
> （阶段二，`FocusResearchTrigger`/`origin="focus_research"` 候选的由来）。

## 一、跟设计文档原文的出入说明

设计文档 §4.3 原文把"有新调研素材待查看"描述成复用`growth_advisor`已有的
"素材参与度信号"设计。核实后发现该信号（`_material_engagement_signal()`
一类逻辑）服务的是"已经生成过学习素材、判断用户有没有看"这个更靠后的场景，
跟本阶段要回答的"这个焦点节点有没有*待处理*的调研候选"不是一回事——后者
只需要读 `GrowthBacklog.pending()` 按 `origin=="focus_research"` +
`evidence_refs` 里的 `goal_tree:<node_id>` 前缀过滤即可，不需要接入更下游
的素材参与度信号。因此本阶段实际做法是：直接查询阶段二产出的 pending
`focus_research` 候选数量，不改动/复用素材参与度信号本身，§4.3 原文保留
不回改。

## 二、改动内容

### 1. `src/mini_agent/evolution/next_action_advisor.py`

新增 `Candidate.kind = "focus_next_step"` 相关逻辑，只读现有数据、不调用
LLM、不新开候选发现机制：

- `_collect_current_focus_node_ids(backlog)`：收集全树里所有 active 结构
  节点 `current_focus_ids` 指向的直接子节点 id（去重）——即"现阶段焦点"
  覆盖到的全部节点集合，与阶段一 `focus_research_nodes()`（服务于调研
  相关性判断的另一种组合）是两个不同的集合。
- `_pending_focus_research_count(paths, node_id)`：只读查询
  `GrowthBacklog.pending()` 里 `origin == "focus_research"` 且
  `evidence_refs` 含 `goal_tree:<node_id>` 的候选数量。
- `_find_focus_next_step_candidates(paths, *, max_nodes=20)`：对每个焦点
  节点按 §4.3 四条规则生成候选（同一节点可以同时命中多条，各自成一条
  候选，`ref_id` 用 `<node_id>:<condition>` 后缀区分）：
  - `goal`/`objective` 且 `execution_spec_confirmed`：`"继续推进"`，
    从 `progress_notes` 最后一行非空内容里摘一句"最近进展"（只读现有
    字段，不重新计算）；
  - `goal`/`objective` 但未确认执行规范：`"先确认执行规范"`；
  - `domain`/`stage` 且 `decompose_candidates` 非空：`"有 N 个待确认的
    分解候选"`；
  - 有 `_pending_focus_research_count() > 0`：`"有 N 条调研素材待查看"`。
- `generate_next_actions()` 新增 `cfg.next_action_focus_next_step_enabled`
  （默认 `False`，跟 `momentum` 规则一样先观察再决定是否默认开启）门控；
  开启时把 `_find_focus_next_step_candidates()` 的结果并入候选池。
- `_rule_based_rank()` 排序优先级更新为
  `stale_goal(0) < focus_next_step(1) < momentum_goal(2) <
  attention_mismatch(3)`——`focus_next_step` 排在 `stale_goal` 之后（后者
  更紧迫：已经停滞），但先于 `momentum_goal`/`attention_mismatch`（前者
  只是"趋势信号"，后者是"可能"，`focus_next_step` 挂在树的现阶段焦点上，
  确定性更高）。

### 2. `src/mini_agent/config/models.py`

`DigestAdvisorConfig` 新增两个字段：

- `next_action_focus_next_step_enabled: bool = False`
- `next_action_focus_next_step_max_nodes: int = 20`（焦点节点采样上限，
  避免目标树增长后单一规则的候选淹没推荐列表）

### 3. 测试

新增 `tests/test_focus_next_step_candidates.py`（6 个用例）：

- 焦点 `goal` 未确认执行规范 → `"确认执行规范"`建议；
- 焦点 `goal` 已确认执行规范 → `"继续推进"`建议，带最近进展摘要；
- 焦点 `domain` 有未处理分解候选 → `"N 个待确认的分解候选"`建议；
- 焦点节点有 `focus_research` pending 候选（复用阶段二
  `FocusResearchTrigger.trigger()` 生成）→ 同时命中 `research`/`spec`
  两条建议；
- 非焦点节点不产出任何候选；
- `generate_next_actions()`：cfg 未开启该规则时返回 `None`（候选池为空，
  沿用既有"克制阈值"），显式 `next_action_focus_next_step_enabled=True`
  后返回结果里包含 `kind == "focus_next_step"` 的条目。

验证：`tests/test_focus_next_step_candidates.py`、
`tests/test_focus_research_trigger.py`、`tests/test_goal_backlog.py`、
`tests/test_growth_advisor.py`、`tests/test_growth_advisor_auto_pursue.py`、
`tests/test_goal_tree_phase1/2/3/4.py` 全部通过（310 passed）。

## 三、后续阶段的影响

- 阶段四要做的"自动巡检 + 看板展示 + CLI/API 收尾"，可以直接调用
  `generate_next_actions(paths, cfg=...)`（`cfg` 显式开启
  `next_action_focus_next_step_enabled`）拿到含 `focus_next_step` 的完整
  推荐列表，不需要再单独接入 `_find_focus_next_step_candidates()`——
  看板"💡 建议"标记、CLI `next-steps` 子命令都读同一份
  `paths.next_actions_path` 落盘结果（`load_pending_next_actions()`）。
- §4.6 提到的"给定 node_id 查询该节点待处理 focus_next_step 建议"，可以在
  落盘的 `items` 里按 `ref_id.split(":")[0] == node_id` 过滤实现，不需要
  额外的数据结构；阶段四实施时如果发现这个过滤要被频繁调用，再评估要不要
  加一个按 `node_id` 索引的辅助函数。
