# Phase 7（Decision + Simulation）现状盘点

> 对应 `next_doc/refactor_plan/08-phase7-decision-simulation-sprint-plan.md`
> "现状盘点"任务产出。方法论同 Phase 6：先阅读已实现模块源码，再评估
> 调用关系，最后确认是否完成 Sprint 7-1/7-2 的验收标准。

## 一、新增模块盘点

| 模块 | 位置 | 角色 |
|---|---|---|
| `SimulationScenario` | `core/simulation.py` | 一次 Simulation 的输入协议 |
| `SimulationResult` | `core/simulation.py` | 一次候选 Action 的 Simulation 输出 |
| `SimulationEngine` | `simulation/engine.py` | Candidate Actions 生成 + 最小 Simulation 引擎 |
| `DecisionEngine` | `cognition/decision.py` | 从候选中选出一个 Action，产出可读 trace |
| `DecisionTrace` | `cognition/decision.py` | 决策过程的可读自然语言记录 |

> **全部是新增模块**，不是旧模块迁移。无 Adapter 双向转换需求。

## 二、核心设计要点

### 2.1 与 Phase 5 Gap 检测的衔接

`simulation/engine.py::generate_candidate_actions()` 接收 `(gap_item, goal_text, constraints)` 三个参数，对应 Phase 5 `goals/gap.py::detect_gap()` 的输出——Gap 是触发 Simulation 的入口，两者形成流水线：

```
detect_gap() → candidate actions → simulation → decision → ActionSpec
```

### 2.2 与 Phase 3 Experience Retriever 的衔接

`simulation/engine.py::simulate_candidates()` 在遍历候选时，对每个候选独立调用
`core/experience_retrieval.py::retrieve_similar_experiences()` 检索相似历史经验，再用
`render_experiences_as_context()` 渲染为上下文文本传给 LLM——确保
`SimulationResult.experience_refs` 可核验、`tradeoffs` 不纯凭空生成。

### 2.3 LLM 注入模式（全链一致）

三个模块均遵循"不内置默认 LLM 实现、调用方注入 Callable"的统一风格：

| 模块 | 注入点 | 类型 |
|---|---|---|
| `simulation/engine.py` | `llm_generate` | `CandidateActionGenerator` |
| `simulation/engine.py` | `llm_narrate` | `ScenarioNarrator` |
| `cognition/decision.py` | `llm_select` 或 `human_confirm` | `LLMSelector` / `HumanConfirmer` |

### 2.4 红线遵守情况

Phase 7 文档明确禁止：
- ❌ 复杂因果树 / Counterfactual 推演 → ✅ 未实现，`possible_futures` 是扁平自然语言列表
- ❌ 数值化打分系统 → ✅ `SimulationResult` 无任何 score/confidence 字段
- ✅ 只做 LLM scenario generation + 规则约束 + 历史经验检索

## 三、Phase 7 红线判断依据（"何时需要更复杂 Causal Tree"）

见 `08-phase7-decision-simulation-sprint-plan.md` "后续判断依据"节，当前三条触发条件均未出现：

1. 候选之间无强依赖/互斥关系（Sprint 7-1/7-2 测试场景均为独立候选）
2. 多步骤连锁影响未在目标场景中出现
3. Experience 检索质量满足要求（Sprint 7-2 特征测试验证了引用链路可核验）

## 四、与主循环的集成状态

**当前状态**：`DecisionEngine` 和 `SimulationEngine` **尚未接入** `goal_mode/runner.py` 主循环。

`cognition/decision.py` 文档注释明确写："未接入 `goal_mode/runner.py` 主循环——显式范围决策"。这是 Sprint 7-2 执行记录的有意决策：第一版先验证单步链路（Gap → Candidate → Simulate → Decision），再考虑是否接入主循环。

接入点应在 `goal_mode/runner.py::run()` 内 Gap 检测成功后、Action 执行前的位置，需要额外评估对主循环控制流的影响，留给后续 Sprint。

## 五、测试覆盖

`sprints/test_phase7_simulation_decision.py`（309 行）覆盖：
- `generate_candidate_actions`：空候选 Generator、数量裁剪、ValueError
- `simulate_candidates`：正常流程、检索为空时的降级处理、ValueError
- `DecisionEngine.select`：LLM 模式、人工确认模式、空候选拒绝、越界下标拒绝
- `DecisionTrace.to_text`：可读性、含候选摘要、含 gap/goal 信息
- 端到端链路：`generate_candidate_actions → simulate_candidates → DecisionEngine.select` 三件套串联

回归测试：`pytest tests/ -k "phase or goal_mode or events or experience or action or simulation or decision"` → 594 passed / 6 failed（6 个失败为既有失败，与本次改动无关）。

## 六、结论

Phase 7 Sprint 7-1（Simulation 骨架）和 Sprint 7-2（DecisionEngine + Trace）均已按原计划完成，所有验收标准均已达成。核心设计点（LLM 注入模式、与 Phase 3/5 的衔接、红线遵守）均已落地并被测试覆盖。

未接入 `goal_mode/runner.py` 主循环是有意为之，属于 Sprint 7-2 执行记录的"显式范围决策"，留给后续 Sprint 评估。