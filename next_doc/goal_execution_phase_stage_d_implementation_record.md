# Goal 执行阶段 Stage D 实施记录

对应 `next_doc/goal_execution_phase_improvement_plan.md` §6 Stage D。
Stage A/B/C 均已在此前完成（数据模型、CLI、prompt 接入、规则自动判定、
看板可视化）；本记录只覆盖 Stage D 新增内容。

## 背景

Stage A-C 的自动判定完全依赖"文件层面"的信号：spec 是否确认、是否最近
被 revise、轻量核对（文件名/key 匹配）miss_streak。这些信号有一个共同
盲区——即使全部满足"稳定"条件，也不代表这一轮的**内容**真的有实质推进；
一个 recurring Goal 完全可能连续多轮都产出命名合规的文件，但内容近乎
复制粘贴。

## 已实现

1. **`perception/execution_phase.py::compute_progress_trend_signal()`**
   - 输入：`goal_backlog`、`goal_id`、可选 `window`（默认 3）、
     `similarity_threshold`（默认 0.85）。
   - 读取 `GoalNode.reaped_cycle_child_ids` 末尾 `window` 个已完成周期子
     节点的 `progress_notes`，用 `difflib.SequenceMatcher` 两两比较相邻
     轮次的文本相似度。
   - 全部相邻对相似度 ≥ 阈值 → 返回 `True`（疑似伪进展）；任意一对低于
     阈值 → 返回 `False`（有实质差异）；历史不足 `window` 轮、缺少
     `progress_notes`、`goal_backlog`/`goal_id` 为空、或任何异常 → 返回
     `None`（不参与判定，语义上等价于该信号关闭）。

2. **`resolve_effective_mode()` 新增可选参数 `progress_trend_stuck`**
   - 仅在原规则会判定为 `stable` 且 `progress_trend_stuck is True` 时，
     把 target 降级为 `converge`；不影响 `explore`/`converge` 分支本身的
     判定逻辑，也不影响 `locked`/非 `auto` 的直通路径。
   - 默认值 `None`，调用方不传时行为与 Stage D 之前完全一致。

3. **`evolution/goal_cron_bridge.py::_append_execution_phase_context()`**
   新增可选 `goal_backlog` 关键字参数（`_fire_goal_cycle` 调用处已传入其
   本来就持有的 `goal_backlog` 实例），内部调用
   `compute_progress_trend_signal()` 算出信号后传给
   `resolve_effective_mode()`。`goal_backlog=None`（默认）时行为不变。

4. **独立 cron 场景**：明确评估后决定**不实现**，原因见方案文档 Stage D
   条目——阶段概念依赖"同一 Goal 连续多轮"的语境，独立 cron job 没有 Goal
   归属，是设计层面的决定而非实现遗漏。

## 测试

`tests/test_execution_phase.py` 新增：
- `compute_progress_trend_signal()` 单测：历史不足返回 None、雷同文本返回
  True、差异文本返回 False、缺失 progress_notes 返回 None、
  backlog/goal_id 为空返回 None（共 5 例）。
- `resolve_effective_mode()` 新增 `progress_trend_stuck` 参数的 4 个场景：
  降级 stable→converge、健康信号维持 stable、未提供信号（None）维持
  stable、信号不影响 explore 判定。
- `goal_cron_bridge._append_execution_phase_context()` 新增 `goal_backlog`
  参数的联通性测试（不崩溃、正常拼接 description）。

全量 `tests/test_execution_phase.py`（33 例）+ `tests/test_goal_cron_bridge.py`
（19 例，与本次改动同目录、验证无回归）合计 52 例全部通过。
`tests/test_execution_phase_kanban_routes.py` 因预置环境缺少 `fastapi` 无法
收集，与本次改动无关（沿用既有已知环境缺口）。

## 兼容性

- 新增均为可选参数（默认 `None`），未显式传入时行为与 Stage D 之前完全
  一致；不修改任何既有数据结构或存储格式。
- `compute_progress_trend_signal()` 任何异常都返回 `None`（不判定），不
  会因为这个新信号导致 Goal 触发主流程报错。

## 文档

- `docs/goal-execution-phase-guide.md`：新增"自动判定规则"第 5 条（进展
  趋势信号说明）；"生效范围"一节改写，明确独立 cron 不接入是设计决定。
- `next_doc/goal_execution_phase_improvement_plan.md`：Stage D 从"后续"
  改为"已实现，范围有调整"，记录范围调整的理由（goal_judge 不适用于
  跨周期场景，改用 `progress_notes` 历史）。
