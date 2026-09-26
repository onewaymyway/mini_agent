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

## 完成标志

- [ ] Experience 的记录、存储、检索三个环节都已用真实 Goal 执行验证过
- [ ] `phase3-experience-inventory.md` 里列出的旧结构，至少有一种
      （建议选 lesson）已经通过 Adapter 接入新 Experience，其余的
      迁移计划写入 `MIGRATION_STATUS.md`
- [ ] Analyzer 产出的聚合统计已经有雏形，可以在 Phase 9 直接复用
