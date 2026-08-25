# docs/ 索引

> `docs/` 存放面向读者的稳定功能指南——一旦某个功能定型，对应设计从
> `next_doc/` "毕业"到这里，此后随功能演进持续原地更新（不是历史快照）。
> 与 `next_doc/`（在研设计/计划/实施记录）的区分规则见
> [文档治理规范](documentation-guidelines.md)。`next_doc/` 的按主线索引见
> [next_doc/README.md](../next_doc/README.md)。
>
> 本索引只给超过 5 篇同前缀的主题群加导读；单篇或 2-3 篇的零散文档直接
> 按文件名在目录里查找即可，不重复列出。

---

## goal-*（Goal 与 Cron 相关，10 篇）

Goal（长期目标）与其执行、绑定、公平调度、目录约定相关的稳定指南。

| 文档 | 说明 |
|------|------|
| [goal-mode-guide.md](goal-mode-guide.md) | 入口：Goal 模式整体说明，建议第一篇读 |
| [goal-cron-binding-guide.md](goal-cron-binding-guide.md) | Goal 与 Cron 绑定机制 |
| [goal-execution-phase-guide.md](goal-execution-phase-guide.md) | 执行阶段划分 |
| [goal-execution-spec-guide.md](goal-execution-spec-guide.md) | 执行 spec 生成 |
| [goal-execution-fairness-config.md](goal-execution-fairness-config.md) | 公平调度配置 |
| [goal-output-directory-guide.md](goal-output-directory-guide.md) | 输出目录约定 |
| [goal-provenance-guide.md](goal-provenance-guide.md) | 溯源信息 |
| [goal-cycle-diagnostics-guide.md](goal-cycle-diagnostics-guide.md) | 周期诊断 |
| [goal-cycle-patrol-guide.md](goal-cycle-patrol-guide.md) | 主动巡检 |
| [goal-cycle-tuning-guide.md](goal-cycle-tuning-guide.md) | 交互式调优 |

对应 `next_doc/` 里的在研设计/计划见
[next_doc/README.md](../next_doc/README.md) 的 "goal_cron" 与 "goal" 分组。

## self-evolution-*（自我演化，7 篇）

Lesson → Skill 提案安全网、巩固循环、效果回填、各阶段（Stage）演进。

| 文档 | 说明 |
|------|------|
| [memory-and-self-evolution-complete-reference.md](memory-and-self-evolution-complete-reference.md) | **入口**：记忆与自我演化完整参考，覆盖全局脉络，建议先读这篇 |
| [self-evolution-stage2-guide.md](self-evolution-stage2-guide.md) | Stage2 |
| [self-evolution-stage3-1-guide.md](self-evolution-stage3-1-guide.md) | Stage3.1 |
| [self-evolution-stage3-2-guide.md](self-evolution-stage3-2-guide.md) | Stage3.2 |
| [self-evolution-stage3-3-guide.md](self-evolution-stage3-3-guide.md) | Stage3.3 |
| [self-evolution-stage4-5-guide.md](self-evolution-stage4-5-guide.md) | Stage4-5 |
| [self-evolution-stage9-guide.md](self-evolution-stage9-guide.md) | Stage9：自主运行时（daemon/cron 相关内容的另一入口） |
| [self-evolution-consolidation-guide.md](self-evolution-consolidation-guide.md) | 巩固循环（`sys:consolidation`） |
| [self-evolution-outcome-tracking-guide.md](self-evolution-outcome-tracking-guide.md) | 效果回填/追踪 |

## daemon-*（常驻守护进程，4 篇）

| 文档 | 说明 |
|------|------|
| [autonomous-daemon-design.md](autonomous-daemon-design.md) | 自主 Daemon 实现说明（`AutonomousLoop`、CronScheduler） |
| [daemon-execution-model-guide.md](daemon-execution-model-guide.md) | 执行模型 |
| [daemon-multi-client-guide.md](daemon-multi-client-guide.md) | 多客户端/多用户 |
| [daemon-autonomous-state-recovery-guide.md](daemon-autonomous-state-recovery-guide.md) | 自主状态恢复 |

## wiki / 记忆相关（4 篇，注意入口关系）

| 文档 | 说明 |
|------|------|
| [memory-and-self-evolution-complete-reference.md](memory-and-self-evolution-complete-reference.md) | **入口文档**：记忆系统 + 自我演化的完整参考，其余三篇是其展开 |
| [memory-management-guide.md](memory-management-guide.md) | 记忆管理（存储/检索/后端） |
| [library-index-guide.md](library-index-guide.md) | 图书馆式知识索引 |
| [wiki-knowledge-base-guide.md](wiki-knowledge-base-guide.md) | Wiki 式知识库 |

（`memory-backfill-guide.md` 是记忆回填单独功能指南，不属于这组"入口 + 展开"
关系，按需单独查阅。）

---

## 拆分文档（原单篇超长，已拆为"稳定骨架 + 演进日志"）

以下两组文档原为单篇超长文档，按
[growth_advisor_docs_reorganization_and_system_state_review.md §2.2](../next_doc/growth_advisor_docs_reorganization_and_system_state_review.md#22-建议拆分--保留时间线索引而不是简单加索引层)
的拆分原则一并处理：

| 骨架（稳定核心机制/配置/参考） | 演进日志（按批次记录各能力方向何时因何落地） |
|---|---|
| [growth-advisor-guide.md](growth-advisor-guide.md) | [growth-advisor-directions-history.md](growth-advisor-directions-history.md) |
| [workflow-guide.md](workflow-guide.md) | [workflow-directions-history.md](workflow-directions-history.md) |

想了解"现在是什么样的"看骨架；想了解"某个具体能力当初为什么这么设计、
是哪个方案落地的"看演进日志。

## 汇总型文档（跨主题引用，改动需同步多处）

以下文档的部分内容是从别处复制/汇总的摘要，修改其中一份时必须同步检查
其余几份，否则会出现漂移（`next_doc/goal_cron_docs_status_audit_record.md`
记录过一次因遗漏同步导致的真实漂移案例）：

- **内置 cron job 清单**：[cron-jobs-reference.md](cron-jobs-reference.md)（权威完整版）
  ←→ [commands-and-tools-reference.md](commands-and-tools-reference.md)（简化版）
  ←→ [self-evolution-stage9-guide.md](self-evolution-stage9-guide.md)（简化版）

## 其他导航

- 项目总入口：根目录 [README.md](../README.md)
- 代码结构：[code-structure-guide.md](code-structure-guide.md)
- 命令与工具总参考：[commands-and-tools-reference.md](commands-and-tools-reference.md)
- 文档治理规范（命名规则、`docs/`/`next_doc/`/`release_logs/` 各自定位）：
  [documentation-guidelines.md](documentation-guidelines.md)
