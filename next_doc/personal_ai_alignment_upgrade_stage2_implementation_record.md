# Personal AI 架构对齐升级 —— 阶段二实施记录

> 对应方案：`next_doc/personal_ai_alignment_upgrade_plan.md` §4.2 / §6
> 阶段二（Personal State Snapshot）。

## 1. 做了什么

新增 `src/mini_agent/perception/personal_state_snapshot.py`，只读聚合
四类已落盘数据源，产出一份"现在是什么"的物化快照：

1. **当前活跃 Goal 及其状态** —— 读 `GoalBacklog.active_goals()`，按
   优先级降序取前 20 条，只暴露 `id/title/level/priority/source/
   last_touched_at` 六个字段（快照是摘要视图，不是完整 goals.json 的
   替代）。
2. **当前进度 vs 计划的偏差** —— 对每个活跃 Goal 读取
   `execution_phase.py::ExecutionPhaseState`，摘出 `mode`/
   `cycles_in_mode`/是否已有未冷却的 `last_health_alert_kind`；再叠加
   `goal_stuck_stats.stuck_stats_summary()` 的全局 `recent_stuck_count`/
   `stuck_ratio`。
3. **当前待处理的主动建议数量与紧急度** —— 直接消费已有的
   `initiative_inbox.initiative_inbox_snapshot()`（传
   `annotate_relevance=False, annotate_cross_dismiss=False` 跳过阶段
   四之前不需要的标注计算），聚合出 `total`/`by_domain`/
   `urgent_count`（confidence < 0.4 的候选计数，语义是"AI 自己也不
   确定，需要用户来判断"，不是时间紧急度）。
4. **当前 Personal Model 中标记为 active 的约束摘要** —— 读阶段一新增
   的 `UserProfileManager.list_constraints()`，只暴露 `text`/
   `last_confirmed_at` 两个字段。

快照本身**不落盘、不追加历史**——`personal_state_snapshot(paths)` 每次
调用都是从源数据实时重新计算的结果，这是与 Memory 的关键区别（方案
§1 第二条核心理念）。任一子聚合异常都不影响其它子聚合，各自
try/except，最终兜底 `_empty_snapshot()`，与 `fairness_diagnostics.py`/
`initiative_inbox.py` 等既有只读聚合模块同一容错风格。

### API 路由

新增 `GET /v1/self/personal_state`（`src/mini_agent/api/routes.py`），
与 `/self/fairness_diagnostics`、`/self/initiative_inbox` 完全同构：
解析 `paths` → 调用聚合函数 → 异常兜底为空结构。供后续 Kanban 面板或
阶段三 Context Pack 组装器直接调用。

## 2. 为什么这样设计（对齐方案 §5 的划分理由）

- 只读聚合，不新增采集点：四类信号全部来自已经在跑的模块
  （`goal_backlog`/`execution_phase`/`goal_stuck_stats`/
  `initiative_inbox`/阶段一的 `profile.py`），符合方案"不新建顶层
  状态存储"的既定原则（§2 第三条）。
- 依赖阶段一的 `constraints` 字段——本阶段能独立完成正是因为阶段一
  已经就绪，验证了方案 §5"4.2 依赖 4.1 提供的部分字段"的判断。
- 不重新触发 `resolve_effective_mode()`/`check_phase_health()` 的完整
  判定链路（见 §3 已知限制）：这两个函数依赖 cfg/routine 等更多上下文，
  在只读快照里重新跑一遍容易得出和 AutonomousLoop 主循环不一致的判断
  结果，风险大于收益，本阶段只读取已经存在的阶段状态字段。

## 3. 已知限制（如实记录）

- **进度偏差信号是"阶段状态摘要"而非"健康判定"**：只暴露
  `mode`/`cycles_in_mode`/`last_health_alert_kind` 三个已落盘字段，不
  重新计算 `check_phase_health()` 的判定结果本身。如果某个 Goal 的
  健康问题尚未被主循环的健康检查触发过一次（`last_health_alert_kind`
  从未被写入），快照里也不会体现——这是"读已有信号"与"重新判定"之间
  刻意选择的取舍，不是遗漏。
- **`urgent_count` 的语义是"低置信度"而非"高紧急度"**：账本/主动性
  候选目前没有独立的"紧急程度"字段，用 `confidence < 0.4` 近似"需要
  用户确认"这层含义，命名沿用方案 §4.4 Daily Digest 草图的"需要你
  决定"口径，避免阶段四再发明一套不同标准，但严格来说这不是真正的
  时间紧急度，如果后续有独立的紧急度信号源，应该替换掉这个近似。
- **`constraints` 没有独立的 active/inactive 状态**：阶段一
  `add_constraint()`/`remove_constraint()` 只有"存在"与"不存在"两态，
  没有"暂停生效"的中间态，因此本阶段"标记为 active 的约束摘要"实际
  返回的是"当前全部已记录的 constraints"，与方案原文字面表述有一处
  简化，如实记录。

## 4. 改动文件清单

```
src/mini_agent/perception/personal_state_snapshot.py          新增
src/mini_agent/api/routes.py                                   修改（新增 GET /v1/self/personal_state）
tests/test_personal_state_snapshot.py                           新增
next_doc/personal_ai_alignment_upgrade_plan.md                  修改（标注阶段二已完成）
next_doc/personal_ai_alignment_upgrade_stage2_implementation_record.md  新增（本文档）
```

## 5. 测试情况

```
tests/test_personal_state_snapshot.py  — 6 项（新增，含空项目/异常容错/
                                          优先级排序/阶段一数据联动用例）
```

本地执行 `python -m pytest tests/test_profile.py tests/test_evidence_pattern.py
tests/test_user_signal_profile_builder.py tests/test_agent_value_profile_builder.py
tests/test_personal_state_snapshot.py` 共 42 项全部通过。`GET
/self/personal_state` 路由未编写 HTTP 层测试——与同构的
`/self/fairness_diagnostics`/`/self/initiative_inbox` 两个既有路由的
测试覆盖现状一致（仓库里也没有为它们编写路由级测试），仅通过语法检查 +
函数级单测覆盖聚合逻辑本身。

## 6. 阶段三预告（尚未开始）

方案 §4.3 的 `build_context_pack()` 需要本阶段的快照摘要（`Current
State` 字段）与阶段一的证据分级结果（`Current Evidence` 字段），两者
均已就绪，可以开始阶段三。阶段三涉及改动 Prompt 组装路径，风险高于
前两项，方案要求"先在小范围试点验证不引入回归"，实现时需要额外谨慎。
