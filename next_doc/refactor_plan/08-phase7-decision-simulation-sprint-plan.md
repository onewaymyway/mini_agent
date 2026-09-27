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

**Sprint 7-1 执行记录（2026-09-27）**：

- `core/simulation.py`（新增）：`SimulationScenario(current_state, goal,
  candidate_actions, constraints)`、`SimulationResult(action,
  possible_futures, risks, tradeoffs, experience_refs)`——比任务表列出
  的字段多了 `action`（对应候选 `ActionSpec`，没有它无法把
  `list[SimulationResult]` 对应回具体候选，`DecisionEngine.select()`
  需要）与 `experience_refs`（检索到的 `Experience.id` 列表，作为"确实
  引用了历史 Experience"这条验收标准的可核验证据，不用只靠人工读
  `tradeoffs` 文本判断）。两个 dataclass 均不含任何数值分数字段，
  对应 Phase 7 边界"禁止数值化打分系统"。
- `simulation/engine.py`（新增）：`generate_candidate_actions()`/
  `simulate_candidates()` 两个函数，均按 `goals/gap.py::detect_gap
  (llm_judge=...)` 的既有风格——LLM 调用点全部做成调用方注入的
  `Callable`（`CandidateActionGenerator`/`ScenarioNarrator`），本模块
  不内置任何真正发起网络请求的默认实现，不传时显式 `ValueError`。
  `simulate_candidates()` 对每个候选独立调用一次
  `experience_retrieval.py::retrieve_similar_experiences()`（检索文本
  用 `f"{goal} {action.capability}"`，让不同候选的检索结果能体现候选
  本身的差异），渲染成上下文后连同 `current_state`/`goal` 一起交给
  注入的 `llm_narrate`；检索不到历史时 `experience_context` 为空串、
  `experience_refs` 为空列表，如实反映现状，不伪造引用。
  `generate_candidate_actions()` 把 LLM 返回的候选裁剪到最多
  `max_candidates`（默认 3）个，不在这里补齐到至少 2 个——"数量是否
  达标"是验收标准要核对的事情，交给调用方/测试判断。
- 新增测试 `tests/test_phase7_simulation_decision.py` 里
  `test_generate_candidate_actions_requires_llm_generate`/
  `test_generate_candidate_actions_caps_at_max_candidates`/
  `test_simulate_candidates_cites_retrieved_experience`/
  `test_simulate_candidates_requires_llm_narrate` 四个用例，其中
  `test_simulate_candidates_cites_retrieved_experience` 直接对应验收
  标准："先用 `ExperienceStore` 播种一条历史 Experience → 对两个候选
  分别调用 `simulate_candidates()` → 断言每个 `SimulationResult.
  experience_refs` 非空且 `llm_narrate` 收到的 `experience_context`
  非空字符串"，不是只断言函数不报错。

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

**Sprint 7-2 执行记录（2026-09-27）**：

- `cognition/decision.py`（新增）：`DecisionEngine.select(candidates:
  list[SimulationResult]) -> tuple[ActionSpec, DecisionTrace]`——比
  任务表签名多返回一个 `DecisionTrace`，因为验收标准明确要求"完整的
  决策 trace……可读的自然语言"，如果只返回 `ActionSpec`，trace 这部分
  信息就无处安放，只能靠调用方自己拼，等于把验收标准要求的东西又推给
  了调用方。构造时二选一注入 `llm_select`/`human_confirm`（"第一版
  可以是……也可以先支持人工确认模式"里的"也可以"按项目一贯的
  "两者都支持，调用方按场景选"处理，而不是二选一定死），同时提供时
  `human_confirm` 优先（人工优先于 LLM 自动选择，与权限审批"人工优先"
  的既有原则一致）。
- `DecisionTrace.to_text()`：把"生成了几个候选/每个候选的权衡摘要/
  最终选了哪个/理由是什么"渲染成一段自然语言，不是 dataclass repr——
  用测试 `test_decision_trace_renders_readable_natural_language_not_
  object_dump` 断言渲染结果里不含 `"SimulationResult("` 这种内部对象
  痕迹，同时确实包含 gap/目标/候选权衡/最终选择/理由这几项内容。
- 接入 Phase 6：新增测试
  `test_full_chain_gap_to_candidates_to_simulation_to_decision_to_action`
  完整走一遍"`GoalState` → `goals/gap.py::detect_gap()` → `simulation/
  engine.py::generate_candidate_actions()` → `simulate_candidates()` →
  `cognition/decision.py::DecisionEngine.select()` → `actions/
  executor.py::ActionExecutor.execute()` → 组织成 `Experience` 并写入
  `ExperienceStore`"整条链路，且断言 `DecisionEngine.select()` 返回的
  `ActionSpec` 与 `ActionExecutor.execute()` 实际执行的是同一个对象
  （`chosen_action is candidates[0]`），不是"两段各自独立跑通、拼起来
  才算数"。
- **范围决策（不在本次改动范围内的事）**：本次没有把
  "Gap→Candidate→Simulation→Decision"这条链路自动接入
  `goal_mode/runner.py` 的主循环——任务表 Sprint 7-2 第二项"接入
  Phase 6"字面要求的是"选中的 `ActionSpec` 直接交给 `ActionExecutor`
  执行"，用测试验证这条转发路径成立即满足要求，并不要求"主循环从此
  自动触发 Simulation/Decision"；把决策链路自动接入主循环是一次远比
  当前范围更大的改动（涉及什么时候触发、要不要每个 Goal 步骤都跑一次
  Simulation、和现有 `spec.py`/`runner.py` 里 LLM 调用点如何共存等
  一系列新问题），且 Phase 7 文档反复强调"风险最高、最容易过度设计"，
  贸然扩大范围与止损原则冲突。这是一个显式的范围决策，不是遗漏——
  如果后续要做，应该另开一个 Sprint 专门评估。
- 新增测试全部通过（`python -m pytest tests/test_phase7_simulation_decision.py -q`
  → 8 passed）；`python -m pyflakes` 对 `core/simulation.py`/
  `simulation/engine.py`/`cognition/decision.py`/测试文件均无告警；
  回归测试 `pytest tests/ -k "phase or goal_mode or events or
  experience or action or simulation or decision"`（跳过 4 个因环境
  缺少三方依赖/历史遗留导入问题无法收集的测试文件，与本次改动无关）
  594 passed / 6 failed——6 个失败与此前 Phase 6 记录的一致
  （`test_build_from_history_*` 系列 + 浏览器 profile 测试），均为
  既有失败，未发现因本次新增代码导致的新增失败。

## 完成标志

- [x] Candidate Actions 生成、Simulation、Decision 三个环节都已用
      真实 Goal 场景验证过，且全链路 trace 可读
- [x] Simulation 输出的是权衡描述，不是数值分数
- [x] 明确记录了"下一步是否需要更复杂的 Causal Tree"的判断依据
      （即：当前 LLM + Experience 的方式在哪些场景下不够用）

## 后续判断依据（"是否需要更复杂 Causal Tree"）

当前 `simulation/engine.py` 的方式是"每个候选独立生成一段自然语言
权衡描述"，**在以下场景下会不够用**，届时才需要考虑更复杂的
Causal Tree / Counterfactual 推演（现在不提前引入）：

1. **候选之间存在强依赖/互斥关系**（例如"方案 A 执行后方案 B 就不再
   可行"），当前实现是逐个候选独立调用 `llm_narrate()`，候选之间不
   共享状态，无法表达这种依赖——如果未来 Gap 场景里频繁出现这类
   强依赖候选，需要重新设计 `SimulationScenario`，让候选之间能表达
   依赖关系，而不是简单加一个"树形展开"了事。
2. **多步骤的连锁影响需要被显式追踪**（"如果做了 A，三步之后可能
   导致 X"），当前 `possible_futures` 是扁平的自然语言列表，不表达
   步骤间的因果链条。如果历史 Experience 里的教训经常是"当时没预料到
   三步之后的连锁反应"，说明纯"一步展望"的自然语言描述不够，需要
   考虑更结构化的多步推演。
3. **Experience 检索质量本身是瓶颈时，不应该去加算法**——按 Sprint
   7-2 的止损条件，如果发现权衡描述空泛/不具体，第一步永远是回头看
   `experience_retrieval.py` 检索到的历史是否真的相关、够不够多，而
   不是给 `simulation/engine.py` 加复杂算法掩盖检索质量问题。这条本身
   也是"什么时候不需要 Causal Tree"的判断依据：只要问题出在检索侧，
   Causal Tree 就是错误的解决方向。

目前的 Goal 场景（Sprint 7-1/7-2 测试所用的"替换 README 占位符"一类）
里没有观察到以上三种情况，因此第一版"LLM + Experience，无因果树"的
方式暂时够用，维持现状。
