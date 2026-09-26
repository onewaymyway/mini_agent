# Phase 7：建立 Decision + Simulation —— 可执行计划

> 对应原方案 §40、§11。前置条件：Phase 6 的 `ActionExecutor` 已能统一
> 执行 Tool/Workflow/SubAgent，Phase 5 的 Gap 检测已能产出候选问题。
>
> 这是原文强调"真正开始改变 Agent 思考方式"的一步，也是风险最高、
> 最容易过度设计的一个 Phase，需要严格控制第一版的复杂度。

## 目标与边界（务必先读）

原文 §11 明确说"不需要给出'架构价值=87'这种分数，而要给出 A/B/C 方案
各自的权衡"。第一版 Simulation **禁止**：

- 复杂因果树 / Counterfactual 推演
- 数值化打分系统

**只做**：`LLM scenario generation + 规则约束 + 历史经验（Phase 3 的
Experience Retriever）`，输出自然语言权衡描述，而不是分数。

## Sprint 7-1（2 周）：Candidate Actions 生成 + 最小 Simulation

| 任务 | 产出 |
|---|---|
| `core/simulation.py` | 定义 `SimulationScenario(current_state, goal, candidate_actions, constraints)`、`SimulationResult(possible_futures, risks, tradeoffs)` |
| Candidate Actions 生成 | 基于 Phase 5 的 gap，用 LLM 生成 2-3 个候选 `ActionSpec`（不是无限枚举） |
| `simulation/engine.py` 第一版 | 对每个候选 Action，调用 LLM + 检索 Phase 3 的相关 Experience，生成"如果这么做，可能发生什么"的自然语言描述（对应原文 §11 的 A/B/C 权衡示例） |

**验收标准**：给定一个真实 Goal 的 gap，系统能生成至少 2 个候选 Action，
并为每个候选生成一段包含"短期成本/长期影响/风险"的自然语言描述，
且这段描述里确实引用了 Phase 3 检索到的历史 Experience（不是纯凭空生成）。

## Sprint 7-2（1.5 周）：Decision Engine 接入

| 任务 | 产出 |
|---|---|
| `cognition/decision.py` | `DecisionEngine.select(candidates: list[SimulationResult]) -> ActionSpec`，第一版可以是"把权衡描述交给 LLM 做最终选择"，也可以先支持人工确认模式 |
| 接入 Phase 6 | 选中的 `ActionSpec` 直接交给 `ActionExecutor` 执行 |
| 全链路打通 | `Gap → Candidate Actions → Simulation → Decision → Action → Outcome → Experience`（原文 §40 完整流程） |

**验收标准**：一次真实 Goal 执行中，能看到完整的决策 trace：为什么
生成了这几个候选、每个候选的权衡是什么、最终为什么选了其中一个。
这个 trace 应该是可读的自然语言，而不是一堆内部对象的 dump。

**止损条件**：如果 Simulation 生成的权衡描述质量差（比如空泛、
不具体），先不要急着加复杂算法，应回头检查 Phase 3 的 Experience
检索质量——原文强调 Simulation 的价值很大程度上来自"历史经验"，
而不是模型本身的推演能力。

## 完成标志

- [ ] Candidate Actions 生成、Simulation、Decision 三个环节都已用
      真实 Goal 场景验证过，且全链路 trace 可读
- [ ] Simulation 输出的是权衡描述，不是数值分数
- [ ] 明确记录了"下一步是否需要更复杂的 Causal Tree"的判断依据
      （即：当前 LLM + Experience 的方式在哪些场景下不够用）
