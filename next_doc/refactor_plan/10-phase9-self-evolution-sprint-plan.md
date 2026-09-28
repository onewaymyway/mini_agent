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

## Sprint 9-1 执行记录

`core/experience_patterns.py`（Phase 3 Sprint 3-2 已有的
`FailurePatternSummary`/`summarize_failures()` 所在文件）新增：

- `Problem` dataclass：`problem_id`/`source`/`category`/`description`/
  `occurrence_count`/`evidence`，对应原方案 §42 闭环第二环。
- `detect_problems_from_experience()`：按归一化后的 `goal_text` 类别对
  Experience 记录分组，失败状态出现次数达到 `min_occurrence` 才生成一条
  `Problem`（任务表第一项"复用 Phase 3 Analyzer，识别重复出现的问题
  模式"）。
- `problem_from_failure_pattern()` + `detect_problems_from_failure_
  pattern_store()`：Adapter 对接既有 `evolution/failure_pattern_
  store.py`（复用其 `load_failure_patterns()` 公开只读接口，**不重新
  实现**扫描 `objective_executions.json`/`goal_state.json` dead_ends/
  TurnJudge stuck 事件那套聚合逻辑，也不修改该文件一行），任务表第二项
  "接入现有 Pattern 逻辑，做 Adapter 对接而不是重写"。
- `detect_problems()`：合并两路来源，`paths=None` 时只用 Experience 路径
  （不因为没有真实 `AgentPaths` 落盘目录而报错），两路命中同一类别时
  不合并计数（保留"两种独立证据都指向同一问题"这一更强信号，供
  Sprint 9-2 判断 Hypothesis 优先级）。

验收标准（"Analyzer 能从真实 Experience 数据中识别出至少一种重复出现的
问题模式，并生成结构化的 `Problem` 记录"）已用新增测试
`tests/test_phase9_sprint9_1_problem_detection.py`（6 用例，覆盖单独
Experience 路径识别、`min_occurrence` 阈值、`FailurePattern → Problem`
字段转换不丢信息、真实跑一次 `run_failure_pattern_aggregation_once()`
后能读出 Problem、两路合并不重复计数、`paths=None` 时优雅降级）验证，
全部通过；回归测试（`test_phase3_experience_retrieval_and_patterns.py`/
`test_core_experience_store.py`/`test_phase3_experience_recorder.py`/
`test_phase3_experience_retrieval_injection.py`/
`test_failure_pattern_interception.py`/`test_failure_pattern_store.py`
共 40 用例全部通过，`failure_pattern_store.py` 本身未被修改一行）；
`pyflakes` 无告警；`scripts/dep_graph.py --module core.experience_
patterns` inbound=1（`core/__init__.py`）/outbound=0（对
`evolution.failure_pattern_store` 的引用是函数内部延迟 import，未被
静态依赖图工具计入，属已知情况，不影响止损评估——这条依赖本身就是
刻意设计成的单向 Adapter 依赖，方向与耦合面均可控），未触发止损阈值。

可进入 **Sprint 9-2（Hypothesis → Experiment → Evaluation）**（下一次
对话的任务）。

## Sprint 9-2 执行记录

新增 `src/mini_agent/evolution/proposals.py`（任务表第一项的指定路径），
只**新增调用方**，不修改任何安全设施文件：

- `Hypothesis` / `EvolutionProposal` / `ProposalEvaluation` 三个 dataclass。
  `EvolutionProposal` 的字段（`changes`/`message`/`meta`/`tier`/
  `initiator`）与 `StateRepo.apply()` 入参一一对应，并提供
  `apply_kwargs()`，Sprint 9-3 可直接 `repo.apply(**proposal.apply_kwargs())`，
  中间不需要再做格式转换。
- `generate_hypothesis(problem, llm_propose=None)`：LLM 调用点沿用
  `goals/gap.py::detect_gap(llm_judge=...)`、`simulation/engine.py` 的
  既有风格——调用方注入 `Callable`，本文件不内置网络实现。未注入时走
  规则模板：为该类任务生成一份 `.agent/lessons/<slug>.md` 规则文件
  （文档类改动，T1 级）。文件名 = ASCII 可读片段 + `problem_id` 的 sha1
  前 8 位，同一 `Problem` 每次生成同一个文件名（幂等），中文/冒号/斜杠
  等不安全字符不会进入文件名。LLM 返回的目标路径若是绝对路径或含 `..`，
  在生成阶段就抛 `ProposalError`，不拖到落地阶段才暴露。
- `build_proposal(hypothesis, tier=None, initiator="autonomous")`：默认
  `initiator="autonomous"`——本链路由 Experience 驱动而非用户显式发起，
  `StateRepo.resolve_tier()` 的 initiator 上浮规则（T0→T1）对它同样生效，
  不会因为走了新入口而绕过既有留痕要求。请求 tier 只是下限（文档类路径
  → T1，其它 → T2），真正生效的 tier 仍由安全设施决定。
- `evaluate_proposal(proposal, repo, eval_fn=None)`：调用
  `StateRepo.resolve_tier()` 算生效 tier（命中受保护路径强制 T3），再用
  `validators_for_tier(生效 tier)` 取现有校验函数，以与 `apply()` 落盘前
  **完全相同的调用方式**（`validator(repo.root, changes)`）逐个执行，收集
  全部失败原因；可选 `eval_fn` 返回 `EvalReport.to_dict()` 形状的 dict，
  回归（`tool_failure_rate` 升高或 `scenarios_ok` 减少）则不接受。全程
  **不落盘、不 commit、不建分支**。
- `propose_and_evaluate(problem, repo, ...)`：Problem → Hypothesis →
  Proposal → Evaluation 便捷入口。

### 范围说明（非计划变更，逐条如实记录）

1. **本 Sprint 的 “Experiment” = dry-run 校验 + 可选 eval 对比**，不含
   真实 sandbox 试跑。原方案里 Experiment 指“放到隔离环境真实试一次”，
   那一步依赖 `EvolutionWorkspace`，而 Sprint 9-3 任务表已明确承接
   “对接现有 `EvolutionWorkspace`/`StateRepo`”，因此这是对两个 Sprint
   边界的澄清而非计划调整，未走 `## 变更记录` 流程。`proposals.py` 模块
   docstring 中已标注 `TODO(Sprint 9-3)`。
2. **评估不复用 `apply()` 来“试跑”**：`apply()` 校验通过即写盘并 commit，
   无法表达“只评估、不落地”。校验函数本身与调用方式与 `apply()` 相同，
   因此“评估通过”等价于“`apply()` 的校验环节会通过”，但不等价于“部署
   一定成功”（例如落盘后 git 层面的失败不在评估范围内）。
3. **eval 回归判定口径是镜像而非 import**：`proposal_risk.
   _check_eval_regression()` 是私有函数且只接受文件路径，`proposals.py`
   里 `_eval_regressed()` 按同一口径对 dict 实现了一份。**代价**：两处
   口径若日后一方修改，另一方不会自动跟随。作为对照，测试
   `test_eval_regression_rejects_even_when_validators_pass` 固定了当前口径。
4. **`proposals.py` 私有 import 了 `proposal_risk._is_low_risk_path`**，
   目的是让“什么算文档/规则类低风险路径”在整个包里只有一个定义（
   `proposal_risk.py` 属决策逻辑而非冻结的安全设施，且同属 `evolution/`
   包内）。代价同上：该私有函数改名会让 `proposals.py` 导入失败——这会
   在测试里立刻暴露，不是静默错位。
5. **默认规则模板假设是通用的**（“沉淀一条 lesson 规则”），本 Sprint
   只证明“链路走得通、安全设施没被绕开”，**不证明**该规则真的能降低
   失败率——这要靠 Sprint 9-3 的 Observe → Promote/Rollback 环节验证。
   `expected_effect` 字段已写明“若之后仍反复失败应回滚并重新提假设”。
6. **与 Sprint 9-1 相同，尚未接入任何运行时调用方**（`AgentRuntime.run_once()`
   的 `learn` 步骤仍为空）。接入时机留给 Sprint 9-3 打通闭环后统一评估。
7. `evolution/__init__.py` **未**新增导出：`proposal_risk` 等同类决策逻辑
   模块也不在包出口里，保持一致，避免为一个尚无调用方的模块扩大公共 API。

### 验收标准核对

| 验收标准 | 结果 | 证据 |
|---|---|---|
| 真实问题模式（Sprint 9-1 产出）能走到生成 Proposal | 达成 | `test_real_problem_flows_to_proposal_and_is_accepted_by_existing_validators`：`Problem` 由 `detect_problems_from_experience()` 从真实 `ExperienceStore` 识别，非手写假对象 |
| 被现有 Validators 接受评估 | 达成 | 同上：`validators_run == ["validate_t0_schema", "validate_t1_load"]`（现有 T1 校验函数，非替身），`accepted is True` |
| 全程不需要绕开安全设施 | 达成 | `test_evaluation_is_dry_run_no_commit_no_files`（评估前后 HEAD 与工作区文件集不变）；反例 `test_validator_failure_rejects_proposal`、`test_invalid_json_data_rejected_by_t0_schema`、`test_protected_path_is_forced_to_t3_and_syntax_error_is_rejected`、`test_autonomous_initiator_lifts_t0_to_t1` 证明校验/强制升级/上浮规则真实生效 |

### 验证结果

- 新增测试 `tests/test_phase9_sprint9_2_proposals.py`：17 用例全部通过
  （含 `parametrize` 展开的 4 个越权路径用例）。
- 回归：evolution 与 Phase 3/9 相关测试文件共 356 用例，354 通过，2 失败
  （`test_evolution_cli.py::test_revert_writes_lesson_with_revert_record_source`
  期望 `confidence == 0.9` 实际 0.85；`test_revert_memory_failure_does_not_raise`）。
  **这 2 个失败在未修改的原始压缩包里同样复现**（干净解压后单独运行
  `test_evolution_cli.py`：2 failed, 20 passed），属既有失败，与本 Sprint 无关；
  本次未去修它们（不在 Sprint 9-2 范围内，且需要先确认 0.85/0.9 哪个才是
  预期值——这是产品层判断）。
- `pyflakes` 对 `proposals.py` 与新测试文件无告警。
- `scripts/dep_graph.py --module evolution.proposals`：inbound=0（尚无调用方）、
  outbound=0（对 `state_repo`/`proposal_risk` 的依赖在 `evolution/` 包内，
  对 `validators` 为函数内延迟 import，均不计入包外统计），未触发止损阈值。
- 冻结模块核对（完成标志第 4 条的阶段性核对）：本次交付使用的压缩包不含
  `.git`，无法用 `git diff`，改为对 `state_repo.py`/`workspace.py`/
  `validators.py`/`eval_runner.py` 四个安全设施，以及 `failure_pattern_store.py`/
  `proposal_risk.py` 两个被对接的决策逻辑模块，逐个与原始压缩包做 sha256
  比对，均一致；`diff -rq src/` 相对原包只多出 `proposals.py` 一个文件。
  **Phase 收尾时仍需在真实仓库里用 `git diff` 再核对一次**，本次结论只针对
  当前交付的压缩包。

### 止损条件与前置条件

- 未触发任何止损条件（无设计假设不成立、无 inbound 超阈值）。
- Sprint 9-3 的前置条件不变；补充一条实现提示：9-3 落地时应以本 Sprint
  的 `evaluate_proposal()` 通过作为部署前置门槛，但**不能**以它替代
  `EvolutionWorkspace` 里的 sandbox 校验（见上文范围说明第 1、2 条）。

`## 完成标志` 四项均属整个 Phase 9 的收尾判定，Sprint 9-2 完成后仍全部
保持未勾选，待 Sprint 9-3 走完完整闭环并验证 Rollback 后再勾选。

可进入 **Sprint 9-3（Sandbox → Validation → Deploy 闭环打通）**（下一次
对话的任务）。
