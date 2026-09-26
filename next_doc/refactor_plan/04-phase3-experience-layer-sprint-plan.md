# Phase 3：建立 Experience Layer —— 可执行计划

> 对应原方案 §36。前置条件：Phase 2 的 Event 总线已跑通，
> `ExperienceCreated` 事件已能在 Goal 链路上被发布。
>
> 注意：`02-executable-sprint-plan.md` 的 Sprint 2 已经做过一版最小
> Experience store（用于 quick win 演示）。本 Phase 是把它做扎实、
> 并把现有 `history / lesson / decision / failure_pattern / outcome`
> 真正统一接进来，而不是重新起炉灶。

## 现状盘点

先扫描代码库里已经存在的"经验类"数据结构，避免重复定义：

| 现有位置 | 内容 | 处理方式 |
|---|---|---|
| `history_manager.py` | 对话/操作历史 | 逐步作为 Experience 的 `context` 来源之一 |
| `evolution/` 下的 lesson/pattern 相关文件 | 已结构化的经验教训 | 优先适配，因为字段和 `Experience.lesson` 高度重合 |
| `wiki/experience_*` | 已有的经验类 wiki 条目 | 作为人类可读表达层，保留，底层数据逐步迁到 Experience store |

产出：`docs/architecture_v2/phase3-experience-inventory.md`。

## 现状盘点执行记录（2026-09-26）

- 已产出 `docs/architecture_v2/phase3-experience-inventory.md`：
  盘点了 `history_manager.py`（对话/操作历史，定位为 `context` 字段
  素材来源而非迁移对象）、`entry_type="lesson"` 的 `MemoryEntry`
  （分布在 `agent/reflection.py`/`agent/reminders_correction.py`/
  `evolution/outcome_tracker.py`/`evolution/failure_pattern_store.py`
  至少 4 个写入点，**澄清了"lesson"实际不是独立类而是 `MemoryEntry`
  的一个 `entry_type` 取值**，仍维持"作为第一条 Adapter 接入链路"的
  最高优先级建议）、`wiki/experience_writer.py`（正面经验 wiki 页面，
  确认保留不迁移，仅作字段命名参考）、以及原计划未点名但一并盘点到的
  `evolution/failure_pattern_store.py::FailurePattern`（聚合统计层，
  作为 Sprint 3-2 Analyzer 雏形参考）、
  `evolution/decision_recall.py::DecisionRecallResult`（检索结果包装层，
  作为 Sprint 3-2 Retriever 参考，可复用 `wiki_shelf_search()` 检索
  通路）、`evolution/decision_profile_builder.py::ValuePattern`
  （判断不属于本次迁移范围）。
- 产出的迁移优先级表已可直接支撑 Sprint 3-1（"lesson"作为第一条
  Adapter 接入链路）与 Sprint 3-2（Analyzer/Retriever 复用现有聚合/
  检索基础设施）的启动，Sprint 3-1/3-2 本身（实际代码改动）留待下一次
  推进，不在本次盘点范围内一并做掉。

## Sprint 3-1（1.5 周）：ExperienceRecorder + Store 扎实化

| 任务 | 产出 |
|---|---|
| `experience/recorder.py` | 订阅 Phase 2 的 `ExperienceCreated` 事件，落地写入 store，替代 Sprint 2 里手写脚本式的记录方式 |
| `experience/store.py` 升级 | 从 quick-win 阶段的 JSON 文件升级为 SQLite（字段对应原文 §7 的 yaml 结构：`context/state_before/goal/action/reason/prediction/outcome/state_after/evidence/lesson/causal_hypothesis/confidence`） |
| 数据迁移脚本 | 把 Sprint 2 阶段已经产生的 Experience 数据迁移进新 store（一次性脚本，跑完即弃） |

**验收标准**：一个 Goal 执行结束后，Experience 自动落库（不需要手动调用
CLI），字段完整覆盖原文 §7 定义的 yaml 结构。

## Sprint 3-2（1.5 周）：ExperienceRetriever + Analyzer

| 任务 | 产出 |
|---|---|
| `experience/retrieval.py` | 按"目标相似度"检索历史 Experience（第一版可以用简单的关键词/embedding 相似度，不需要复杂算法） |
| `experience/patterns.py`（Analyzer 雏形）| 对同类失败的 Experience 做简单聚合统计（比如"过去 5 次同类 Goal 失败原因分布"），为 Phase 9 的 Self Evolution 打基础 |
| 接入 Goal 规划 | 在 `goal_mode/runner.py` 规划阶段，读取相关 Experience 作为上下文的一部分注入 LLM prompt |

**验收标准**（对应原文 §36 的验收标准，细化）：
1. 一个 Goal 执行结束后，可以生成结构化 Experience。
2. 下一个类似 Goal 执行时，Retriever 能检索到它，并且这个检索结果
   真实出现在了传给 LLM 的 context 里（不是"检索到了但没用上"）。
3. 旧的 `history/lesson/decision` 相关测试仍然通过（说明没有破坏
   旧系统，只是新增了一条并行链路）。

## Sprint 3-1 执行记录（2026-09-26）

- `core/experience.py::Experience` 扩展补齐原方案 §7 yaml 结构剩余字段：
  `id`/`context`/`state_before`/`action`/`reason`/`prediction`/
  `state_after`/`evidence`/`lesson`/`causal_hypothesis`/`confidence`。
  沿用 `core/events.py` 扩展 `Event` 字段集时定下的先例：保留旧字段名
  （`goal_text`/`status`/`final_report`/`created_at`）不变，新增字段
  全部给默认值，`goal_adapter.py`/`experience_store.py`/
  `experience_cmd.py` 的旧调用点不改一行也能继续工作。新增
  `Experience.from_dict()`，对缺失新增字段的旧数据宽容处理（回退默认
  值而不是抛异常），为迁移脚本铺路。
- `core/experience_store.py` 从 Sprint 2 的 JSONL 升级为 SQLite（单表
  `experiences`）。选择这么做的原因见 Sprint 3-1 任务表：Sprint 3-2 即将
  加检索/聚合统计，继续用"整份读进内存再过滤"的方式会随记录量增长而
  失效。对外 `append`/`all`/`search` API 与构造函数签名（`path` 可选）
  保持不变，现有调用方（`goal_mode/runner.py`、
  `cli/commands/experience_cmd.py`）与既有测试
  （`tests/test_core_experience_store.py`）不需要改一行。
- 新增 `core/experience_recorder.py::ExperienceRecorder`：订阅
  `ExperienceCreated` 事件后自动落库，接入模式（`ensure_experience_
  recorder_subscribed()` 幂等挂载、订阅是纯旁路）与 Phase 2 Sprint 2-2
  的 `core/event_log_store.py::ensure_event_log_subscribed()` 完全一致。
  `goal_mode/runner.py::run()` 在挂载 `EventLogStore` 订阅的同一位置，
  新增挂载 `ExperienceRecorder` 订阅；`_finish()` 删除了原来手写的
  `ExperienceStore(...).append(...)` 调用——不再需要"publish 一次 +
  手写落盘一次"两段独立逻辑，只保留 publish，落盘由订阅者自动完成，
  避免两处逻辑漂移。
- `core/goal_adapter.py::goal_run_result_to_experience()` 补充填充新增
  字段里能可靠拿到的部分：`action="goal_mode.run"`、`reason`（来自
  `goal_spec.acceptance_criteria`）、`evidence`（来自
  `replan_proposal`）、`lesson`（终止状态为 stuck/max_rounds_exhausted/
  failed 时取 `final_report`）。`state_before`/`state_after`/
  `prediction`/`causal_hypothesis`/`confidence` 因为 `GoalRunResult`
  这一层目前拿不到对应的中间状态快照，如实保留默认值，不臆造——
  这些字段的真正填充需要等 Phase 4（统一 State）落地后才有数据来源。
- 新增一次性迁移脚本 `scripts/migrate_experience_jsonl_to_sqlite.py`：
  读取 Sprint 2 阶段遗留的 `.agent/experience_store.jsonl`，用
  `Experience.from_dict()` 宽容解析每一行，逐条写入新的 SQLite store
  （`.agent/experience_store.db`）。脚本本身不在 `AgentPaths` 属性读取
  时自动触发，需要手动运行一次，跑完确认数据无误后建议手动删除旧文件。
- 验证：新增 `tests/test_phase3_experience_recorder.py`（5 用例全过，
  覆盖新字段完整性、SQLite round-trip、订阅落库、订阅幂等性、旧数据
  宽容解析）；`tests/test_core_experience_store.py`（Sprint 2 遗留测试，
  13 用例全过，证明 SQLite 升级没有破坏对外行为）；
  `tests/test_phase2_event_log_and_cli.py`、`tests/test_goal_mode.py`
  等相关回归测试全过（仅剩 Sprint 0 已记录、与本次改动无关的
  `test_build_from_history_*` 系列 + 浏览器 profile 相关既有失败）；
  `pyflakes` 核对新增/改动文件无未用 import。
- 验收标准（"一个 Goal 执行结束后，Experience 自动落库，不需要手动调用
  CLI，字段完整覆盖原文 §7 定义的 yaml 结构"）已满足：`run()` 挂载
  `ExperienceRecorder` 订阅后，`_finish()` 只需 `publish()`，落库是
  订阅者的自动行为；`Experience.to_dict()` 覆盖 §7 全部字段。
- Sprint 3-1 完整完成三项任务（recorder / store 升级 / 迁移脚本），
  可进入 **Sprint 3-2（ExperienceRetriever + Analyzer，接入 Goal
  规划阶段）**，留待下一次推进。

## 完成标志

- [ ] Experience 的记录、存储、检索三个环节都已用真实 Goal 执行验证过
- [ ] `phase3-experience-inventory.md` 里列出的旧结构，至少有一种
      （建议选 lesson）已经通过 Adapter 接入新 Experience，其余的
      迁移计划写入 `MIGRATION_STATUS.md`
- [ ] Analyzer 产出的聚合统计已经有雏形，可以在 Phase 9 直接复用
