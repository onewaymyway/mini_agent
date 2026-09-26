# Phase 9：把 Self Evolution 接入统一 Experience —— 可执行计划

> 对应原方案 §42、§26、§27。前置条件：Phase 3 的 Experience Layer 已
> 有 Analyzer 雏形（聚合统计），Phase 8 的 Runtime 已能持续运行。

## 目标

把现有 67 个 evolution 模块的核心闭环，重新接到统一的 Experience 上，
形成原文 §42 的完整链路：

```text
Experience → Pattern → Problem → Hypothesis → Experiment →
Evaluation → Evolution Proposal → Sandbox → Validation →
Deploy → Observe → Promote / Rollback
```

**关键原则**：`StateRepo / EvolutionWorkspace / Validators / EvalRunner`
这些现有的安全设施（原文 §28、§43）**必须继续保留**，Self Evolution
的风险控制不能因为重构而削弱。

## 现状盘点

先梳理现有 67 个 evolution 模块里，哪些是"安全设施"（沙盒、验证、回滚），
哪些是"决策逻辑"（何时触发 evolution、如何生成 proposal）。这个盘点
本身就是本 Phase 最重要的前置工作，因为盲目改动安全设施风险极高。

产出：`docs/architecture_v2/phase9-evolution-inventory.md`，明确标注
每个模块属于哪一类，以及哪些**绝对不能动**。

## Sprint 9-1（1.5 周）：Pattern 检测接入 Experience Analyzer

| 任务 | 产出 |
|---|---|
| 复用 Phase 3 Analyzer | Phase 3 已有的"同类失败聚合统计"雏形，扩展为正式的 `experience/patterns.py`：识别"重复出现的问题模式" |
| 接入现有 Pattern 逻辑 | 现有 evolution 模块里如果已有类似的 pattern 检测，做 Adapter 对接，而不是重写 |

**验收标准**：Analyzer 能从 Phase 3-8 累积的真实 Experience 数据中，
识别出至少一种重复出现的问题模式，并生成结构化的 `Problem` 记录。

## Sprint 9-2（2 周）：Hypothesis → Experiment → Evaluation

| 任务 | 产出 |
|---|---|
| `evolution/proposals.py` | 基于 `Problem`，生成 `Hypothesis`（一个可能的改进方案）和对应的 `EvolutionProposal` |
| 对接现有 Validators/EvalRunner | Proposal 生成后，复用现有的验证和评估机制，**不重新实现**，只是把输入从旧格式改为新的 `EvolutionProposal` 结构 |

**验收标准**：一个真实的问题模式（Sprint 9-1 产出）能走到生成 Proposal
并被现有 Validators 接受评估，全程不需要绕开安全设施。

## Sprint 9-3（1.5 周）：Sandbox → Validation → Deploy 闭环打通

| 任务 | 产出 |
|---|---|
| 对接现有 `EvolutionWorkspace` / `StateRepo` | Proposal 通过评估后，走现有的 sandbox 部署和 `StateRepo` 版本管理流程，**不修改这部分实现**，只是让触发入口统一为新的 Experience 驱动方式 |
| Promote / Rollback 验证 | 用一次真实（或模拟）的失败场景，验证 Rollback 机制在新链路下仍然生效 |

**验收标准**：完整走一次 `Experience → Pattern → Problem → Hypothesis
→ Experiment → Evaluation → Proposal → Sandbox → Validation → Deploy
→ Observe → Promote/Rollback`，且 Rollback 场景经过真实验证（这是
底线要求：evolution 的安全网如果没验证过，不能算完成）。

## 完成标志

- [ ] 至少一次真实问题模式走完了完整的 evolution 闭环
- [ ] Rollback 机制在新链路下经过真实验证（不是"理论上应该没问题"）
- [ ] 现有 `StateRepo/Validators/EvalRunner` 等安全设施未被修改，
      只是接入方式改变
- [ ] `phase9-evolution-inventory.md` 中标注的"绝对不能动"的模块
      在整个 Phase 过程中确实没有被修改（可用 git diff 核对）
