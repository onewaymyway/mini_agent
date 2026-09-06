# Personal AI 架构对齐升级 —— 阶段四实施记录

> 对应方案：`next_doc/personal_ai_alignment_upgrade_plan.md` §4.4 / §6
> 阶段四（Daily Digest，最后一个阶段）。至此方案 §6 划分的四个阶段全部
> 完成。

## 1. 做了什么

新增 `src/mini_agent/perception/daily_digest.py::daily_digest(paths)`，
纯只读聚合视图，合成方案 §4.4 定义的四段式简报：

```
今天最重要的事：<Top N 活跃 Goal 的下一步动作>
AI 已完成：<近期成功执行的 Goal / 建议采纳记录>
需要你决定：<initiative_inbox 中 confidence 较低的候选>
风险：<进度偏差项>
```

四段数据全部来自已经在跑的模块，不新增任何采集点：

1. **今天最重要的事** —— 直接取阶段二
   `personal_state_snapshot()` 已经按优先级降序排好的 `active_goals`
   前 `top_n` 条（默认 5），不重新读 `goal_backlog`、不重新排序，避免
   与快照口径分叉。
2. **AI 已完成** —— 读 `goal_backlog.py::GoalBacklog.all_nodes()`，筛出
   `status == "completed"` 的节点，按 `last_touched_at` 降序取最近
   `recent_completed_limit` 条（默认 5）。"建议采纳记录"这一半（方案
   原文提到的"Goal/建议采纳记录"）本阶段未落地，见 §3 已知限制。
3. **需要你决定** —— 消费已有的
   `initiative_inbox.py::initiative_inbox_snapshot()`，从全量候选里挑出
   `confidence < urgent_confidence_threshold`（默认 0.4，与阶段二
   `personal_state_snapshot.py` 的 `urgent_count` 同一阈值语义，不发明
   第二套标准）的候选，按置信度升序（越不确定越靠前）取前 `top_n` 条。
4. **风险** —— 直接取 `personal_state_snapshot()` 的 `progress` 字段
   （健康告警列表 + 卡住比例），只做展示形态转换，不重新计算。

`daily_digest()` 本身**不落盘、不追加历史**——与阶段二的
`personal_state_snapshot()` 同一"State 而非 Memory"风格，每次调用都是
从源数据实时重新计算的结果；四段各自 try/except，任一子聚合异常不影响
其它子聚合，最终兜底 `_empty_digest()`。不提供任何写操作（接受/拒绝/
确认候选仍然要去 `initiative_inbox` 对应的原生 tab 操作），与方案
"纯合成展示层，不侵入任何现有模块"的要求一致。

### API 路由

新增 `GET /v1/self/daily_digest`（`src/mini_agent/api/routes.py`），与
`/self/personal_state`、`/self/initiative_inbox` 完全同构：解析 `paths`
→ 调用聚合函数 → 异常兜底为空结构。载体沿用方案原文"具体载体留到实现
阶段评估"的开放选项——本阶段先落地只读聚合 + API，作为 Kanban 新 tab
或首页改造的数据来源，前端具体呈现（新 tab vs 首页卡片）留给后续按
当时 Kanban 整体布局评估决定，不在本次改动范围内。

## 2. 为什么这样设计（对齐方案 §5 的划分理由）

- 纯展示合成，不重复采集：四段数据全部来自阶段二快照 + 已有的
  `initiative_inbox_snapshot()` + `goal_backlog.py` 已落盘状态，符合
  方案 §2"不新建推送/展示框架，Daily Digest 直接消费已有数据，只做
  合成，不重复采集"的既定原则。
- 依赖前三阶段但改动面最集中在展示层：本阶段没有修改任何既有模块的
  行为，只新增一个纯读函数 + 一个只读路由，验证了方案 §5"4.4 放最后
  即可，不阻塞前面三项的独立上线"的判断——事实上本阶段完全没有触碰
  阶段一/二/三的任何代码。
- "需要你决定"复用阶段二已经确定的置信度阈值语义，而不是另起一套
  "紧急度"标准，避免同一个"该不该提醒用户"的判断在两个只读聚合层
  各自维护、后续容易口径漂移。

## 3. 已知限制（如实记录）

- **"AI 已完成"目前只包含已完成的 Goal，不包含"建议采纳记录"**：
  方案原文是"近期成功执行的 Goal/建议采纳记录"两者并列，但
  `suggestion_feedback_ledger.py::all_categories()` 目前只按 category
  聚合 accepted/rejected 的计数，没有保留具体建议的标题/内容，无法
  可靠还原成"用户采纳了哪条具体建议"这样的展示条目。本阶段选择如实
  只呈现 Goal 完成这一半，不用计数臆造具体的"建议采纳"展示内容——如果
  后续 `suggestion_feedback_ledger.py` 补上了逐条建议的标题字段，应该
  在这里补上第二个数据源，而不是现在就编造。
- **"AI 已完成"不区分"用户主动完成"与"AI 自主执行完成"**：
  `GoalNode.status == "completed"` 不记录是谁把状态改成 completed 的
  （用户手动标记 vs `ObjectiveExecutor` 判定通过后自动回写），本阶段
  统一按"已完成"呈现，与方案原文"AI 已完成"字面表述有一处不完全精确
  的简化，如实记录。如果后续需要严格区分，需要在 `GoalNode` 或状态
  变更记录里补上"谁改的"这个字段，本阶段不新增。
- **`recent_completed_limit`/`top_n`/`urgent_confidence_threshold` 均为
  硬编码默认值，未接入配置系统**：现阶段作为固定参数传入函数签名，
  尚未在 `cfg` 里开放可调项——与其它只读聚合模块（`fairness_diagnostics`
  的部分参数、`personal_state_snapshot` 的 `urgent_confidence_threshold`）
  现状一致，如果后续需要按用户个性化调整再统一收敛到配置里。
- **前端呈现载体未落地**：本阶段只完成聚合函数 + API 路由，Kanban 新
  tab 或首页改造留待后续评估——与方案 §6 阶段四原文"具体载体在本阶段
  开始时结合当时的 Kanban 整体布局评估决定"的表述略有出入：本次评估
  的结论是"数据就绪，前端呈现形式的决策进一步留到有实际 UI 改动需求
  时再做"，避免在没有真实使用场景反馈前就仓促定型某种前端布局。

## 4. 改动文件清单

```
src/mini_agent/perception/daily_digest.py                     新增
src/mini_agent/api/routes.py                                   修改（新增 GET /v1/self/daily_digest + 路由列表注释）
tests/test_daily_digest.py                                     新增
next_doc/personal_ai_alignment_upgrade_plan.md                 修改（标注四个阶段全部完成）
next_doc/personal_ai_alignment_upgrade_stage4_implementation_record.md  新增（本文档）
```

## 5. 测试情况

```
tests/test_daily_digest.py  — 7 项（新增，含 paths=None/空项目兜底、
                                Top N 优先级排序、已完成 Goal 列表、
                                低置信度候选筛选排序、风险信号转述、
                                _empty_digest 结构校验用例）
```

本地执行 `python -m pytest tests/test_daily_digest.py
tests/test_context_pack.py tests/test_personal_state_snapshot.py
tests/test_decision_consumption.py tests/test_profile.py
tests/test_user_signal_profile_builder.py
tests/test_agent_value_profile_builder.py` 共 59 项全部通过。`GET
/self/daily_digest` 路由未编写 HTTP 层测试——与同构的
`/self/personal_state`/`/self/fairness_diagnostics`/
`/self/initiative_inbox` 三个既有路由的测试覆盖现状一致（仓库里也没有
为它们编写路由级测试），仅通过语法检查 + 函数级单测覆盖聚合逻辑本身。

## 6. 方案全貌回顾

至此 `next_doc/personal_ai_alignment_upgrade_plan.md` §6 划分的四个
阶段全部完成：

- 阶段一（Personal Model 证据分级扩展）—— 见
  `personal_ai_alignment_upgrade_stage1_implementation_record.md`
- 阶段二（Personal State Snapshot）—— 见
  `personal_ai_alignment_upgrade_stage2_implementation_record.md`
- 阶段三（Context Pack 组装器，试点接入）—— 见
  `personal_ai_alignment_upgrade_stage3_implementation_record.md`
- 阶段四（Daily Digest，本文档）

四个阶段共同的已知限制模式（值得在后续巡检中统一关注）：
本方案多处"如实记录"了"数据源本身尚未积累/尚未细化"导致的功能性空缺
（Relevant Experience/World Context 可能长期为空、建议采纳记录无法
还原标题、约束没有 active/inactive 中间态等）——这些不是本方案实现
阶段的缺陷，而是上游数据源（`experience_writer.py`/`world_writer.py`/
`suggestion_feedback_ledger.py`）本身的成熟度问题，如果后续要让 Context
Pack/Daily Digest 的信息量更丰富，应该优先去补齐这些上游数据源，而不是
在聚合层用近似值掩盖。
