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

- [x] 至少一次真实问题模式走完了完整的 evolution 闭环（Sprint 9-3：
      `test_full_loop_ends_in_real_rollback` / `..._promote_...`。**口径**：
      Experience 数据是测试里灌入的，但 `ExperienceStore`（SQLite）、git
      worktree 沙盒、`merge_branch()`、`revert()` 全是真实组件；**尚未**
      拿生产环境的真实 Experience 数据跑过，见 Sprint 9-3 执行记录）
- [x] Rollback 机制在新链路下经过真实验证（不是"理论上应该没问题"）
      （断言的是磁盘文件真的消失、git 历史里真的有 revert commit，且验证
      过回退冲突的失败路径；过程中发现并处理了两个真实问题，见 Sprint 9-3
      执行记录）
- [x] 现有 `StateRepo/Validators/EvalRunner` 等安全设施未被修改，
      只是接入方式改变（**2026-09-28 由所有者在真实仓库核对，已勾**：
      `python scripts/check_frozen_evolution_modules.py --base
      5c16de19939b1d076d275d351cca9f81af061e9a`，4 个文件、0 个被改动，退出码 0。
      **口径**：本次核对未加 `--also-adapted`，只覆盖了本条与下条明确列出的
      4 个 FROZEN 文件；Sprint 9-1/9-2 承诺"只新增调用方、不修改"的
      `failure_pattern_store.py`/`proposal_risk.py`（ADAPTED 列表）尚未在真实
      仓库核对，与本两条完成标志的字面范围无关，不影响勾选，但建议后续找机会
      补跑一次 `--also-adapted` 存档）
- [x] `phase9-evolution-inventory.md` 中标注的"绝对不能动"的模块
      在整个 Phase 过程中确实没有被修改（可用 git diff 核对）（**2026-09-28
      由所有者在真实仓库核对，已勾**：同上，基线 commit
      `5c16de19939b1d076d275d351cca9f81af061e9a`，
      `state_repo.py`/`workspace.py`/`validators.py`/`eval_runner.py`
      四个文件均 unchanged）

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

## Sprint 9-3 执行记录

新增 `src/mini_agent/evolution/deployment.py`，只**新增调用方**，不修改
任何安全设施文件。流程：

    evaluate_proposal（9-2 dry-run 门）
      → EvolutionWorkspace.create（git worktree 沙盒）
      → 沙盒内 StateRepo(ws.path).apply(auto_validators=True)（第二道校验）
      → 可选 smoke_boot
      → classify_proposal_risk（既有 Track I 分级）
      → 人审门（approve 回调）
      → merge_branch（Deploy）→ DeployRecord
      → make_experience_observer（Observe，读真实 Experience）
      → settle_deployment（Promote / Rollback / 继续观察）

### 关键设计决策

1. **不自动合并，人审门保持**。既有设计是"低风险也要人点一下，高风险全人工
   审核"。新链路不能因为由 Experience 驱动就绕开这道门：`deploy_proposal()`
   **未注入 `approve` 时永远停在 `pending_approval`**（沙盒已验证、分支保留
   不合并）。`approve(proposal, risk)` 返回 True 才合并，False 则删分支（与
   既有"拒绝 = 删分支"一致）。`pending_approval` 的分支与旧 `skill_propose`
   使用同一套 `evolve/*` 约定，可被既有 `/evolution merge` 直接接手（测试
   `test_without_approve_stops_at_pending_and_keeps_branch` 验证）。
2. **Rollback 逐 commit 逆序回退，而不是回退合并提交**。真实探针与测试
   `test_reverting_a_merge_commit_directly_fails` 证实：`merge_branch()` 用
   `--no-ff` 产生合并提交，而 `StateRepo.revert()` 不带 `-m`，对合并提交
   直接失败（`is a merge but no -m option was given`）；且
   `merge_branch(delete_after=True)` 合并后分支即被删，事后查不出分支含哪些
   commit。因此**合并前**用 `commits_on_branch()` 记下全部 commit hash
   （`DeployRecord.applied_commits`），Rollback 时逆序逐个调用公开的
   `StateRepo.revert()`。原 commit 与合并提交保留在历史中（回退不改写历史）。
3. **Observe 保守三态**：`improved`（样本 ≥ `min_samples` 且失败为 0 →
   Promote）、`persists`（部署后同类失败 ≥ 阈值 → Rollback）、
   `inconclusive`（证据不足 → **既不 Promote 也不 Rollback**，保持
   `observing`）。只看 `deployed_at` 之后、与 `Problem.category` 同类（复用
   9-1 的同一归一化）的 Experience。已定论（promoted/rolled_back）的记录再次
   `settle` 不会重复回退。

### 本 Sprint 发现并处理的两个真实问题

- **回退冲突会把仓库留在"回退进行中"的半途状态**：`StateRepo.revert()`
  遇冲突只抛异常，不会 `git revert --abort`（对比 `merge_branch()` 内部对
  合并冲突做了 `--abort`）。测试 `test_rollback_failure_is_reported_not_
  swallowed` 首次运行即因工作区残留 `UD` 未合并条目而失败，证实了这一点。
  `state_repo.py` 属冻结文件不可改，故在 `rollback_deployment()` 里捕获异常
  后调用 `repo._run_git(["revert","--abort"])` 收尾，并把记录标为
  `rollback_failed`、如实写明原因，**不吞异常、不静默声称成功**。
- **沙盒有两道独立校验**：`evaluate_proposal()` 与沙盒内 `apply()` 用的是
  同一套校验函数，理论上后者是冗余的。测试
  `test_sandbox_validation_is_an_independent_second_gate` 强行放行第一道后，
  确认第二道仍能拦截坏提案并清理沙盒/分支——避免日后有人以"重复"为由删掉
  其中一道。

### 范围说明与已知局限（逐条如实记录）

1. **`repo._run_git` 私有访问**（仅用于上述 `revert --abort`），代价：
   `StateRepo` 若重命名该私有方法，`deployment.py` 会在回退冲突路径上出错。
   更好的长期做法是让 `StateRepo.revert()` 自己在冲突时 abort——但这需要
   修改冻结的安全设施，应走 `## 变更记录` 流程由人决定，本 Sprint 未擅自做。
2. **`DeployRecord` 未持久化**，只提供 `to_dict()/from_dict()`。Observe
   通常发生在部署之后若干次运行之后（可能跨进程），接入运行时前必须决定
   存储位置（不应绕过 `StateRepo` 直接写受 git 管理的路径）。当前无运行时
   调用方，`AgentRuntime` 的 `learn` 步骤仍为空。**这意味着闭环目前只能被
   显式调用驱动，还不是"自动运转"的。**
3. **`smoke_boot` 默认关闭**（`run_smoke_boot=False`）：它会启动子进程，成本
   高；测试里用 monkeypatch 验证了"开启且失败则阻断并清理"及"默认不调用"，
   **没有**在测试里真实拉起一次副本进程。T2/T3 提案建议开启，由调用方决定。
4. **Observe 的阈值（`min_samples=3`、`persist_threshold=2`）是经验默认值**，
   没有数据依据；样本很少时 `inconclusive` 会占多数，这是刻意的保守取舍。
   它也不区分"失败是否与本次改动有关"，只看同类任务后续是否仍失败，
   属于相关性而非因果判断。
5. **`_normalize_category` / `_FAILURE_STATUSES` 是 9-1 模块的私有符号**，
   Observe 直接 import 以保证与 Problem 检测口径一致，代价同 §1。
6. **回退冲突场景下的半回退**：多 commit 部署时若第 N 个 revert 冲突，前
   N-1 个已成功的 revert commit 保留，记录 `rollback_failed` 与
   `revert_commits`，需要人工处理；没有自动"回退上一次回退"。
7. `evolution/__init__.py` 仍未新增导出（理由同 9-2）。

### 验收标准核对

| 验收标准 | 结果 | 证据 |
|---|---|---|
| 完整走一次 Experience → … → Deploy → Observe → Promote/Rollback | 达成（口径见完成标志第 1 条） | `test_full_loop_ends_in_real_rollback`、`test_full_loop_ends_in_promote_and_repo_untouched` |
| Rollback 场景经过真实验证 | 达成 | 上述测试断言：文件从磁盘消失、`git status` 干净、原 commit 与 revert commit 均在 `git log`、HEAD 前进；另有多 commit 逆序回退、回退冲突、重复 settle 三条 |
| 安全设施未被绕开 | 达成 | 无 `approve` 时 HEAD 不变且不合并；评估拒绝/沙盒校验失败/smoke 失败三条路径均断言"无残留分支、`git worktree list` 只剩主仓库、HEAD 不变" |

### 验证结果

- 新增测试 `tests/test_phase9_sprint9_3_deployment.py`：17 用例全部通过。
- 回归：evolution 与 Phase 3/9 相关测试文件共 373 用例，371 通过，2 失败，
  与 Sprint 9-2 记录的是**同一个**既有失败（`test_evolution_cli.py` 的
  revert 相关两条，原始压缩包中同样复现），与本 Sprint 无关，未处理。
- `pyflakes` 对 `deployment.py` 与新测试文件无告警。
- `scripts/dep_graph.py --module evolution.deployment`：inbound=0，
  未触发止损阈值。
- 冻结模块核对：`state_repo.py`/`workspace.py`/`validators.py`/
  `eval_runner.py`/`failure_pattern_store.py`/`proposal_risk.py` 与原始压缩包
  sha256 一致；`diff -rq src/` 相对原包仅多出 `proposals.py`（9-2）与
  `deployment.py`（9-3）两个新文件。**仍需在真实仓库里用 `git diff` 核对**
  （见完成标志第 4 条）。

Sprint 9-3 是 Phase 9 的最后一个 Sprint。**Phase 9 尚不能整体宣布完成**：
完成标志第 3、4 条待在真实仓库核对后勾选，第 1 条建议再用一次真实 Experience
数据跑通，并需决定 `DeployRecord` 的持久化与运行时接入方式（局限 §2）。

## 变更记录

### 变更记录 2026-09-28（新增 Sprint 9-4：Phase 9 收尾）
- 触发条件：本文档 Sprint 9-3 执行记录末尾的判断——“Phase 9 尚不能整体宣布完成”——
  以及 `11-phase10-legacy-decommission-plan.md` “变更记录 2026-09-28”里的 D4（Phase 10
  前置条件“Phase 1-9 全部完成”未满足，需要补齐或明确豁免）。不是止损条件触发，
  是 Sprint 9-3 自己记录的两条已知局限（§2 `DeployRecord` 未持久化；`AgentRuntime` 的
  `learn` 步骤仍空）需要有个 Sprint 来承接。
- 原计划：Phase 9 由 Sprint 9-1 ～ 9-3 三个 Sprint 构成，任务表里没有“运行时接入”，
  也没有“持久化”。
- 实际情况：9-3 打通的闭环只能被显式调用驱动——`DeployRecord` 不落盘，Observe 无法
  跨进程；Runtime 里没有任何位置会去 Observe 已部署的改动。
- 调整后方案：新增 Sprint 9-4，**只承接上述两条局限**，验收标准（可验证）：
  1. `DeployRecord` 落盘后，新的 Store 实例（模拟另一个进程）能读出并继续 Observe；
  2. `learn` 默认关闭，关闭时 `AgentRuntime.run_once()` 的行为与 `RuntimeCycleCompleted`
     事件 payload 与 Sprint 8-1 完全一致；
  3. 开启后：`improved` 只改记录不动仓库；`persists` 默认只建议回退、不动仓库；
     `persists` + `auto_rollback` 才真的回退，且断言的是磁盘与 git 历史；
  4. `learn` 任何失败都不改变 Goal 的执行结果；
  5. 四个冻结安全设施与原包逐字节一致。
  止损条件：若要实现上述任何一条必须修改冻结安全设施，停止并回到本节走变更流程。
- 影响范围：不改动 9-1 ～ 9-3 的任务表与验收标准；**不改变** Phase 9 完成标志第 3、4
  条的待勾状态；对 Phase 10 前置条件 D4 只是“部分推进”（见
  `11-phase10-legacy-decommission-plan.md` 前置条件下的更新提示），不构成豁免。

## Sprint 9-4 执行记录

### 做了什么

| 任务 | 产出 |
|---|---|
| `DeployRecord` 持久化 | 新增 `evolution/deploy_record_store.py::DeployRecordStore`（append-only JSONL，同一 `proposal_id` 以最后一行为准）、`AgentPaths.workdir_deploy_records`（`.agent/deploy_records.jsonl`，已加入 `.gitignore`）；`deploy_proposal(record_store=...)` 合并成功后立即落盘 |
| Observe 可脱离 `Problem` 对象 | `DeployRecord` 与 `Hypothesis` 新增 `problem_category`（均带默认值，旧数据可读）；新增 `make_category_observer()`，`make_experience_observer()` 改为它的包装（9-3 行为不变，有对照测试） |
| `AgentRuntime` 的 `learn` 步骤 | 新增 `runtime/learn.py::run_learn_step()`；`AgentRuntime.__init__` 新增 `enable_learn_stage` / `learn_auto_rollback`（缺省读 `goal_mode.runtime_learn_enabled` / `runtime_learn_auto_rollback`，均默认 `False`）；`AgentRuntimeResult.learn_report`；仅启用时 `RuntimeCycleCompleted.payload` 多一个 `learn` 摘要键 |
| 冻结核对工具 | 新增 `scripts/check_frozen_evolution_modules.py`（git 模式 / manifest 模式，“无法核对”退出码 2，不当作通过）+ 基线 `docs/architecture_v2/phase9-frozen-modules.sha256` |

### 关键设计决策

1. **`learn` 只接“观察”和“汇总”，不接“提案”和“部署”。** 让 Agent 每跑一轮都有机会
   自动生成并合并对自己的修改，与 Sprint 9-3“不自动合并、人审门保持”的决策相冲突，
   所以没有做。这意味着**生产中目前仍没有任何路径会自动产生 `DeployRecord`**——
   `deploy_proposal()` 依旧只有测试和显式调用方。`learn` 现在能观察的是“被显式部署
   过并传入 `record_store` 的改动”。这是有意的范围，不是遗漏。
2. **回退需要第二个开关。** `persists` 时默认只在报告里标 `rollback_recommended`，
   记录保持 `observing`（下一轮仍会再次提示）；只有 `runtime_learn_auto_rollback=True`
   才执行 `git revert`。理由：回退是对受 git 管理的项目仓库的写操作，与合并一样应由
   人决定；而且 `learn` 紧跟在 Goal 执行之后运行，此刻工作区可能带着 Goal 刚做的、
   尚未提交的改动，自动 `git revert` 有与之冲突的风险（见下方局限 §3）。
3. **持久化不走 `StateRepo`、不进 git。** 它记录的是“部署发生过”这个运行时事实，
   不是对 Agent 行为的自我修改；真正改变行为的文件仍只能经 `StateRepo.apply()`。
   与 `events.jsonl` 同类处理。
4. **落盘失败不改写已成功的部署状态。** 仓库确实已经合并，把状态改写成失败比留下
   一条“需人工补录”的 `errors` 更危险。
5. **`StateRepo` 延迟构造。** `StateRepo(root)` 在目标目录没有 `.git` 时会 `git init`；
   观察阶段（`improved`/`inconclusive`/仅建议）不应有这种副作用，只在真要回退时才构造
   （测试 `test_learn_observation_never_creates_a_git_repo`）。

### 过程中发现并修复的真实问题

- **崩溃留下的残缺末行会连带损坏下一条记录。** 首版 `save()` 直接追加；测试
  `test_store_skips_corrupt_and_truncated_lines` 第一次运行即失败：末行没有换行符时，
  新记录被接在它后面，两者一起变成无法解析的一行，导致新记录丢失。已改为追加前检查
  文件是否以换行结尾，否则先补一个。

### 验收标准核对

| 验收标准 | 结果 | 证据 |
|---|---|---|
| 1. 落盘后新 Store 实例能读出并继续 Observe | 达成 | `test_store_survives_across_instances`、`test_deploy_persists_record_with_category`；端到端 `test_end_to_end_runtime_learn_reverts_a_bad_deployment` 里 Observe 使用的是从磁盘读回的记录 |
| 2. 关闭时行为与 payload 与 Sprint 8-1 一致 | 达成 | `test_learn_is_off_by_default_and_changes_nothing`（断言未调用 `run_learn_step`、payload 无 `learn` 键）、`test_config_defaults_are_conservative`；Phase 8 的 14 个既有用例未改动仍通过 |
| 3. improved 不动仓库 / persists 默认只建议 / persists+开关才回退 | 达成 | `test_learn_improved_promotes_record_and_leaves_repo_alone`、`test_learn_persists_without_auto_rollback_only_recommends`（断言 HEAD 不变、文件仍在、记录仍 `observing`）、`test_learn_persists_with_auto_rollback_really_reverts`（断言文件从磁盘消失、原 commit 与 revert commit 均在 `git log`、第二轮不重复回退）；另有 inconclusive、回退冲突（`rollback_failed` 被如实报告并持久化、仓库无半途状态、不会被无限重试） |
| 4. learn 失败不改变 Goal 结果 | 达成 | `test_learn_failure_never_changes_goal_result`、`test_learn_never_raises_and_still_reports_other_parts`、`test_learn_one_bad_record_does_not_block_the_others` |
| 5. 冻结安全设施未改 | 达成（口径见下） | 四个安全设施与 `failure_pattern_store.py`/`proposal_risk.py` 对原压缩包 `cmp` 逐字节一致；`test_shipped_manifest_matches_current_tree`。**口径：只对本次交付的压缩包成立，不等于完成标志第 4 条要求的真实仓库 `git diff`** |

### 验证结果

- 新增测试 44 个全部通过：`tests/test_phase9_sprint9_4_learn_and_persistence.py`（30）、
  `tests/test_phase9_sprint9_4_frozen_check.py`（14）。
- **回归范围说明（未跑全量）**：全量测试在本环境单核下预计数小时，启动后在约 2%
  处中止。改为定向回归——文件名含 `phase|core_|goal_mode|evolution|runtime|paths|config|
  cron_job|state_repo|workspace|validators|failure_pattern|proposal|experience|event` 的
  67 个测试文件。改动后：**977 passed，19 failed**；在未修改的原始压缩包上跑同一批
  （去掉本 Sprint 新增的 2 个文件）：**933 passed，19 failed**，且**失败用例集合逐条
  一致**（`test_browser_core_session_manager.py` 5、`test_evolution_cli.py` 2、
  `test_explorer_runtime_subagent.py` 7、`test_goal_mode.py::test_build_from_history_*` 5），
  均为既有失败，与本 Sprint 无关，未处理。多出的 44 个通过用例即上面的新增测试。
  **其余约 350 个测试文件未运行**，不能据此声称全量测试通过率“持平”。
- `pyflakes` 对全部改动/新增文件无告警；`scripts/lint_no_new_toplevel_concepts.py` 通过。
- `scripts/dep_graph.py`：`evolution.deploy_record_store` inbound=2（`deployment.py`、
  `runtime/learn.py`），`runtime.learn` inbound=2（`runtime/__init__.py`、
  `runtime/runtime.py`），均 outbound=0，未触发止损阈值。

### 已知局限（逐条如实记录）

1. **生产中没有自动产生 `DeployRecord` 的路径**（见设计决策 §1）。`learn` 的 Observe
   目前只对显式部署过的记录有意义；闭环的前半段（Problem → Proposal → Deploy）仍需
   人或调用方驱动。Phase 9 完成标志第 1 条“口径”里的保留意见（Experience 数据是测试灌入的，
   未拿生产真实数据跑过）**仍然成立**。
2. **“建议回退”目前没有面向用户的出口。**（**2026-09-29 已处理**，见文末“Sprint 9-4 收尾补充：建议回退的用户出口”；以下为处理前的原始记录。） `rollback_recommended` 只出现在
   `AgentRuntimeResult.learn_report` 和 `RuntimeCycleCompleted` 事件 payload 里（后者会被
   `EventLogStore` 落盘，可用 `events trace` 查看）。没有通知、没有 CLI 提示。
   开启 `learn` 但不开自动回退的用户，除非主动去看事件日志，否则不会知道有建议。
3. **自动回退与“Goal 刚改过工作区”的交互没有专门验证。** 测试覆盖了“后续提交改了同一
   文件导致冲突”这一路径，但没有覆盖“工作区里有 Goal 刚做的、未提交的改动”。`git revert`
   在这种情况下可能失败（会被记为 `rollback_failed`），也可能在改动不重叠时成功、
   使工作区同时包含回退与未提交改动。这是默认关闭自动回退的直接原因；开启前应先验证。
4. **`learn` 随每次 `run_once()` 执行，包括 `cron.runtime_dispatch_enabled` 的 cron
   路径**（二者共用 `cfg.goal_mode`）。每次都会读取全部 Experience 与 `DeployRecord` 日志，
   规模大时是 O(n) 开销；目前未做增量或节流。
5. **Observe 的阈值（`min_samples=3`、`persist_threshold=2`）仍是经验默认值**，`learn`
   没有把它们做成配置项，沿用 9-3 的取舍；只看“同类任务后续是否仍失败”，是相关性而非因果。
6. **旧记录没有 `problem_category`**（9-3 时期手工构造的记录）会被判为 `inconclusive`，
   永远不会被自动 Promote/Rollback。这是刻意的保守，不去猜类别。
7. **私有访问的已知代价不变**：`deployment.py` 仍私有访问 `StateRepo._run_git`
   （9-3 局限 §1），`learn` 复用 `settle_deployment()`，继承了这一点。

### Phase 9 当前状态

**Phase 9 完成标志四条已全部勾选（2026-09-28）。** 第 1、2 条口径见上；第 3、4 条
由所有者在真实仓库执行 `check_frozen_evolution_modules.py` 核对通过（结果见上，
基线 `5c16de19939b1d076d275d351cca9f81af061e9a`）。9-3 记录的两条运行时局限
（`DeployRecord` 未持久化、`learn` 未接入）已由本 Sprint 处理，但见上面的局限
§1、§2、§3——这些是记录在案的已知代价，不是完成标志要求核对的范围，Phase 9
可以整体宣布完成。

~~Phase 9 内部还有一项可独立推进、未排期的候选：为 `rollback_recommended` 提供
用户可见的出口（局限 §2）。~~ **已于 2026-09-29 完成**，见文末“Sprint 9-4 收尾补充”。Phase 10 的状态见 `11-phase10-legacy-decommission-plan.md`
"变更记录"最新一条——D1/D5 已由所有者决定（维持现状 / 搁置 Sprint 10-2），
Phase 10 的目录收敛工作到此告一段落。

## Sprint 9-4 收尾补充：建议回退的用户出口（2026-09-29）

承接 Sprint 9-4 局限 §2：`rollback_recommended` 只存在于 `LearnReport` 与事件 payload，用户看不到。
本次补三个出口，均为增量改动，**默认行为不变**（`runtime_learn_enabled` 关闭时与此前逐字节一致）。

### 做了什么

| 出口 | 触发 | 副作用 | 开关 |
|---|---|---|---|
| ① `/goal` 结束时的终端提示 | `learn` 开启且本轮有“建议回退/回退失败”时，`_run_goal` 用 `print_warning` 打印 `LearnReport.render_notice()` | 无（只读） | 无（`learn` 本身已是 opt-in） |
| ② 看板“关注与通知”通知 | `run_learn_step(notify=True)`，经 `NotificationDispatcher`（看板恒发，email/webhook 按用户既有通知配置） | 写 `.agent/notification/reports.jsonl`；在 `DeployRecord` 记 `rollback_notified_at` | `goal_mode.runtime_learn_notify_enabled`，**默认 False** |
| ③ `/evolution deploys` | 用户主动输入 | 无：只读，**不构造 `StateRepo`**（不会在无 `.git` 的目录 `git init`），无 observing 记录时不打开 Experience 库 | 无 |

- 通知 `source` 两个：`learn_rollback_recommended`（分类“关注提醒”）、`learn_rollback_failed`（分类“执行失败”），已加入 `notification/reports_store.py::_SOURCE_CATEGORY_MAP`，看板无需改动即可分类展示。
- 文案里的回退命令是 `applied_commits` 的**逆序**（先新后旧）。原因：`/evolution revert` 一次只接一个 commit，且 Sprint 9-3 已实测 `revert` 对 `--no-ff` 合并提交直接失败，所以不能只给合并提交。
- **去重**：`persists` 的部署会一直保持 `observing`，`learn` 每次运行都会再次得出同样的建议；`DeployRecord` 新增 `rollback_notified_at`（默认 0，旧记录按 0 读取），**kanban 渠道写入成功后**才记时间戳，所以同一个部署只提醒一次；发送失败不记，下一轮重试。`rollback_failed` 记录会离开 `observing`，天然只出现一次，不需要标记。
- 新增 `collect_deploy_overview()`（`/evolution deploys` 的数据来源）：对仍在 `observing` 的部署现算一次 Observe（与 `learn` 同口径），让用户不必等下一次 `/goal` 结束。

### 涉及文件

`runtime/learn.py`（`ObservedDeployment` 新增三个带默认值字段、`LearnReport.render_notice()`/`notified`、`notify` 参数与 `_notify_kanban()`、`collect_deploy_overview()`）；`runtime/runtime.py`（`learn_notify` 构造参数 + 读配置）；`evolution/deployment.py`（`DeployRecord.rollback_notified_at`）；`config/models.py`（`runtime_learn_notify_enabled`）；`notification/reports_store.py`（两个 source 的分类）；`cli/commands/goal_mode_cmd.py`（`_run_goal` 末尾提示）；`cli/commands/evolution.py`（`deploys` 子命令）。4 个安全设施文件及 `failure_pattern_store.py`/`proposal_risk.py` 未改动（`check_frozen_evolution_modules.py --check-manifest` 6 个文件 0 不一致）。

### 验证

- 新增 `tests/test_phase9_rollback_user_exit.py`（**25 用例**）：文案（无事可报为空串、逆序命令、无 commit 信息、回退失败）、旧记录兼容、默认不写通知文件、通知内容/分类、**跨轮去重**、发送失败不标记且可重试、通知抛异常不影响 learn、回退失败通知一次、improved/inconclusive 不通知、`AgentRuntime` 开关优先级（参数 > 配置 > 默认）、`/goal` 提示（有/无/提示本身抛异常）、`/evolution deploys`（空、实时建议、已结算、不 `git init`、不创建 `.agent/`、不落盘、盘点不抛异常）。**变异检查**：临时去掉去重条件，`test_notify_dedupes_across_runs_but_still_recommends` 随即失败，恢复后通过。
- Sprint 9-4 既有 30 用例全部通过（未改动）。
- 定向回归（`test_phase*`/`test_core_*`/`test_goal_mode*`/`test_evolution*`/`test_cron*`/`test_notification*`/`test_deploy*` 等 57 个文件，含本次新增之外）：**849 passed，8 failed**。8 个失败均为既有：`test_build_from_history_*` 5 个（Sprint 0 已记录）、`test_evolution_cli.py` revert 相关 2 个、`test_notification_dispatcher.py::test_kanban_writes_alert_record` 1 个（该测试仍读旧的 `alerts.jsonl`，而 `KanbanChannel` 早已改写 `reports.jsonl`），后三项已在**未修改的原始压缩包**上复现。**未跑全量测试**。
- `pyflakes` 对本次改动文件无新增告警（`reports_store.py` 的 `time` 未使用、`goal_mode_cmd.py` 的 `scan_goal_states` 未使用为既有）；`lint_no_new_toplevel_concepts.py` 通过；`dep_graph.py --module runtime.learn`：深度 inbound=3，未触发止损。

### 已知局限（如实记录）

1. **看板通知默认关闭**（保守 opt-in）。只开 `runtime_learn_enabled` 而不开 `runtime_learn_notify_enabled` 时，走 cron 路径（无终端）的用户仍然不会被主动告知——此时只能靠 `/evolution deploys` 或事件日志。想要“看板可见”需要显式打开第二个开关。
2. **终端提示每次 `/goal` 结束都会重复出现**（只要建议仍然成立且用户没处理）。这是有意的：用户当场在场，重复提醒比静默更安全；只有看板通知做了“只提醒一次”。
3. 去重标记落盘失败时，下一轮会重发一条通知（宁可多发不漏发）。
4. `rollback_failed` 的通知只尝试一次：发送失败后没有重试（记录已离开 `observing`）。失败原因会出现在 `LearnReport.errors`，终端提示同一轮仍会显示。
5. **自动回退成功（`rolled_back`）不发通知**：本次范围是“需要人处理的事”；自动改动了用户仓库这件事本身是否要提醒，留待决定。
6. 通知正文里的回退命令未校验 commit 是否仍在当前分支历史里（用户可能已手动处理）；`/evolution revert` 自身会报“Commit not found”。
7. 看板前端（Streamlit）未做任何改动，也未在本环境运行；依赖既有“关注与通知”面板按 `source` 分类展示新增的两个 source，这部分只用纯函数测试（`categorize_report`/`list_pending_reports`）覆盖，**没有浏览器级验证**。
