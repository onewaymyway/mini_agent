# 目标树 × 自主调研 阶段四（自动巡检 + 看板展示 + CLI/API 收尾）实施记录

> 对应设计文档：`next_doc/goal_tree_research_and_action_recommendation_plan.md`
> §4.2（触发时机 1）、§4.5、§4.6 + 分阶段规划"阶段四"（最后一个阶段）。
> 前置阅读：`next_doc/goal_tree_research_action_phase3_implementation_
> record.md`（阶段三，`focus_next_step` 候选类型的由来）。

## 一、跟设计文档原文的出入说明

设计文档 §4.2 把"焦点变化 → 自动触发调研"设想成"正式接入
`sys:goal_tree_focus_recompute` 巡检后的联动触发"，没有明确具体接线
方式。实施时确认了两种做法：（a）新开一个独立 cron job，在
`sys:goal_tree_focus_recompute` 之后串行触发；（b）直接扩展现有
`ensure_goal_tree_focus_recompute_job()` 的 `_handler`，在
`recompute_current_focus_tree()` 之后顺带跑一轮扫描。选择了 (b)——跟
§五 阶段四原文"不用再等新的独立 cron job"的表述一致，也避免了两个 job
之间"谁先跑"的时序依赖；`_handler` 内部读取 `agent_config.json` 里的
`growth_advisor.goal_tree_focus_research_auto_trigger_enabled`（默认
`False`），只有显式开启才会调用新增的 `run_focus_research_scan_cycle()`，
关闭时该 handler 的行为与阶段三之前完全一致（零改动兼容）。

§4.5 原文"树形视图新增'💡 建议'标记"部分，实际做法是先在
`_render_goal_tree_view()` 顶层一次性拉取全部 `focus_next_step` 候选
（`GET /v1/goals/next_steps` 不传 `node_id`），构造出"有待处理建议的
节点 id"集合后再递归传给每个节点渲染函数比对——不是像"📄 相关调研"那样
在每个节点的"⚙️ 管理"折叠区内单独发一次请求，避免节点数量多时产生 N
次网络往返（"💡"标记只是"有没有"的提示，具体建议文案仍然通过已有的
`/next` 展示入口查看，不在树形视图里重复渲染建议正文，这点跟原文一致）。

## 二、改动内容

### 1. `src/mini_agent/evolution/focus_research_trigger.py`

- `FocusResearchTrigger.load_focus_snapshot()`/`save_focus_snapshot()`：
  焦点节点集合快照持久化，记在跟"每节点触发时间戳"同一个 state 文件里
  （`goal_tree_focus_research_state.json`），用保留 key
  `__focus_snapshot__`（不会跟 uuid 生成的 node_id 冲突）区分——阶段二
  实施记录里预告的"具体存储位置留到阶段四实施时再定"，这里给出的答案。
- `run_focus_research_scan_cycle(paths, backlog, *, llm_helper=None,
  cfg=None, max_nodes=DEFAULT_MAX_NODES_PER_SCAN)`：读快照 → 用阶段二
  已有的 `find_newly_focused_nodes()` 求"新进入焦点"的节点差集 → 按
  `max_nodes`（默认 5，`DEFAULT_MAX_NODES_PER_SCAN`）截断后逐个调用
  `trigger()`（不传 `force`，仍受节奏治理约束）→ 把快照整体刷新为"当前
  完整焦点集合"（不是"只记被处理的那几个"，被截断的节点下一轮巡检时仍
  会被判定为差集的一部分，不会漏判）。返回 `FocusResearchScanSummary`
  汇总（跟 `goal_tree_decomposer.DecomposeScanSummary` 同构，`ok` 恒
  `True`，不抛异常）。
- `list_pending_research_candidates(paths, node_id)`：只读查询，供
  `GET /v1/goals/{id}/research` 和看板"📄 相关调研"复用（阶段三实施
  记录预告过这个函数，本阶段落地）。

### 2. `src/mini_agent/perception/goal_backlog.py`

`ensure_goal_tree_focus_recompute_job()` 的 `_handler`：
`recompute_current_focus_tree()` 之后，`try/except` 包裹地读取
`load_config(backlog._paths.project_root)`，若
`cfg.growth_advisor.goal_tree_focus_research_auto_trigger_enabled` 为
`True` 则调用 `run_focus_research_scan_cycle()`（`cfg` 显式传
`growth_cfg`、`max_nodes` 取
`goal_tree_focus_research_auto_trigger_max_nodes`）；任何异常都
`log_exception` 后吞掉，不影响 `recompute_current_focus_tree()` 本身
已经成功这一事实（`_handler` 仍返回 `True`）。

### 3. `src/mini_agent/config/models.py`

`GrowthAdvisorConfig` 新增两个字段：

- `goal_tree_focus_research_auto_trigger_enabled: bool = False`
- `goal_tree_focus_research_auto_trigger_max_nodes: int = 5`

默认关闭，理由见字段旁注释——触发本身零 LLM 成本、且复用
`GrowthBacklog` 现有节流，但"目标树焦点变化就自动生成调研候选"是新引入
的自动化行为，跟 `next_action_momentum_enabled`/
`next_action_focus_next_step_enabled` 同一种"先把机制写出来、默认不
打扰用户"的落地方式。

### 4. `src/mini_agent/evolution/next_action_advisor.py`

新增两个只读辅助函数（供 §4.6 的 API/CLI 复用，避免各自重复实现落盘
文件读取/过滤逻辑）：

- `load_all_next_actions(paths)`：跟已有的 `load_pending_next_actions()`
  区别是不受 `shown_at` 影响，随时能查询当前落盘内容——跟现有
  `GET /v1/next_actions` 端点的读取方式一致。
- `filter_focus_next_step_items(data, node_id=None)`：从 `items` 里挑
  `kind == "focus_next_step"` 的条目，可选按 `ref_id` 前缀进一步过滤到
  某个节点。

### 5. `src/mini_agent/api/routes.py`

新增三个端点（紧跟在 `/goals/{node_id}/reparent` 之后，`/directions`
之前）：

- `GET /v1/goals/{node_id}/research`：返回该节点 pending 调研候选 +
  最近触发时间；节点不存在 404。
- `POST /v1/goals/{node_id}/research/trigger`：手动触发（Body
  `{"force": bool?}`），返回 `{"candidate", "skip_reason"}`；未传
  `force` 且命中节奏治理时提前返回明确的 `skip_reason`，不调用
  `trigger()` 本体（避免节奏治理的"跳过原因"字符串被 `add_or_merge()`
  内部判定覆盖掉）。
- `GET /v1/goals/next_steps?node_id=...`：只读现有落盘的
  `focus_next_step` 候选，可选按节点过滤。

### 6. `src/mini_agent/cli/commands/goals.py`

新增子命令 `/agent goals next-steps [id]`：调用
`next_action_advisor.load_all_next_actions()` +
`filter_focus_next_step_items()`，打印 `[ref_id] title —— reason`
格式；没有匹配项时给出友好提示（可能是规则未开启/还没生成过/节点不在
焦点范围内三种原因之一，不做进一步区分，避免过度诊断）。顶层
`Available:` 列表和 usage 分发同步补上 `next-steps`。

### 7. 看板（`apps/mini_agent_kanban/`）

- `client.py` 新增 `goal_tree_research(node_id)`、
  `trigger_goal_tree_research(node_id, force=False)`、
  `goal_tree_next_steps(node_id=None)` 三个方法，分别对应上面三个新
  端点。
- `app.py`：
  - 新增 `_render_goal_tree_research_section(client, node_id)`：渲染
    "📄 相关调研"子区块（pending 候选列表 + 最近触发时间 +
    "🔍 立即调研"按钮，`force=True`），挂在每个节点"⚙️ 管理"折叠区
    最底部（在现有"📌 现阶段焦点 pin/unpin"区块之后）。
  - `_render_goal_tree_view()` 顶层新增一次 `client.goal_tree_next_
    steps()` 调用，构造 `next_step_node_ids` 集合后传给
    `_render_goal_tree_node()`。
  - `_render_goal_tree_node()` 新增 `next_step_node_ids` 形参，命中时
    在节点标题后追加"💡"标记；递归调用时透传该集合。

### 8. 测试

新增 `tests/test_goal_tree_research_action_phase4.py`（17 个用例）：

- `FocusResearchTrigger` 焦点快照读写、不跟节点自身时间戳 key 冲突；
- `run_focus_research_scan_cycle()`：首轮触发全部当前焦点节点、二次
  巡检无变化时不重复触发、`max_nodes` 截断但快照仍整体刷新、默认截断值
  与配置默认值对齐；
- `list_pending_research_candidates()`：按节点过滤、无候选返回空、
  候选被 accept 后不再出现在 pending 列表；
- `ensure_goal_tree_focus_recompute_job()` 的 handler：默认配置下不
  产生任何调研候选（向后兼容）、显式在 `agent_config.json` 里开启后
  自动触发；
- `next_action_advisor.load_all_next_actions()`/
  `filter_focus_next_step_items()`：文件不存在返回 `None`、不受
  `shown_at` 影响、按 `kind`/`node_id` 过滤、`None` 输入不报错。

验证：`tests/test_goal_tree_research_action_phase4.py`、
`tests/test_focus_research_trigger.py`、
`tests/test_focus_next_step_candidates.py`、
`tests/test_goal_tree_phase1/2/3/4.py`、`tests/test_goal_backlog.py`、
`tests/test_goals_spec_close_check_cli.py`、
`tests/test_goals_spec_generate_cli_mode.py`、
`tests/test_growth_advisor_auto_pursue.py` 全部通过（148 passed）；
另外单独 `import mini_agent.api.routes` 确认新端点已正确挂载到路由表
（`/v1/goals/{node_id}/research`、`/v1/goals/{node_id}/research/trigger`、
`/v1/goals/next_steps` 均在列）。

`tests/test_growth_advisor.py` 里有 1 个既有用例
（`test_compact_health_trend_storage_downsamples_old_points`）在本地
环境下偶发失败（时间戳降采样边界的既有 flaky 用例），与本次改动无关，
未修改该文件。

## 三、至此的整体状态

`goal_tree_research_and_action_recommendation_plan.md` §五"分阶段实施
规划"的四个阶段（绑定对象迁移 → 焦点驱动调研 → 焦点行动建议 → 自动巡检
+ 看板展示 + CLI/API 收尾）全部完成。§六"待实施阶段确认的细节"里剩下
的唯一一项——"`focus_next_step` 候选是否需要区分'给用户看的建议文案'
和'给 Agent 自动执行用的结构化指令'"——按原文约定，留到本方案验证过
一轮真实使用之后再评估，不在本阶段处理。
