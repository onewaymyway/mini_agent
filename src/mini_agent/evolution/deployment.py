"""evolution/deployment.py — Sandbox → Validation → Deploy → Observe →
Promote / Rollback（Phase 9 Sprint 9-3）

见 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-3，对应原方案 §42 闭环的后半段：

    ... Evolution Proposal → **Sandbox → Validation → Deploy → Observe →
    Promote / Rollback**

前半段由 Sprint 9-1（Problem）与 Sprint 9-2（`proposals.py`：Hypothesis /
EvolutionProposal / dry-run Evaluation）产出。

═══ 与安全设施（`phase9-evolution-inventory.md` 第二节）的关系 ═══

本文件只**新增调用方**，不修改 `state_repo.py` / `workspace.py` /
`validators.py` / `eval_runner.py` 一行：

  - Sandbox：`EvolutionWorkspace.create()`（git worktree 隔离）。
  - Validation：在沙盒里 `StateRepo(ws.path).apply(..., auto_validators=True)`，
    校验函数按 *生效* tier 由 `validators_for_tier()` 选取；校验失败则不
    落盘不 commit，沙盒与分支一并清理。
  - 风险分级：`classify_proposal_risk()`（既有 Track I 逻辑，不重写）。
  - Deploy：`StateRepo.merge_branch()`。
  - Rollback：`StateRepo.revert()`（见下方“Rollback 的实现方式”）。

═══ 触发入口 ═══

原计划要求“让触发入口统一为新的 Experience 驱动方式”。本文件的入口是
`deploy_proposal()`，其输入 `EvolutionProposal` 来自 Sprint 9-2，而后者的
输入 `Problem` 来自 Experience（Sprint 9-1）。Observe 环节同样读
Experience（`make_experience_observer()`）。旧的 `skill_propose` 工具与
`/evolution` CLI 保持原状，不受影响；本文件产出的 `evolve/*` 分支与它们
使用同一套分支约定，因此 `pending_approval` 状态下的分支可以直接被既有
`/evolution merge` / `/evolution revert` 命令接手。

═══ 关键设计：不自动合并，人审门保持 ═══

既有设计（`proposal_risk.py` 头部、`cli/commands/evolution.py::_handle_merge`）
是“低风险也需要人点一下，高风险全人工审核”。新链路不能因为由 Experience
驱动就绕开这道门，因此 **`deploy_proposal()` 在没有显式注入 `approve`
回调时，永远停在 `pending_approval`：沙盒已验证、分支已保留，但不合并**。
`approve` 收到 `(proposal, risk)`，返回 True 才合并；返回 False 视为拒绝，
删除分支（与既有“拒绝 = 删分支”语义一致）。

═══ Rollback 的实现方式（真实验证得出，非“理论上应该没问题”）═══

`StateRepo.merge_branch()` 使用 `--no-ff`，产生一个合并提交；而
`StateRepo.revert()` 调用 `git revert --no-edit <commit>` 且不带 `-m`，
对合并提交会直接失败（`is a merge but no -m option was given`，测试
`test_reverting_a_merge_commit_directly_fails` 固化了这个事实）。同时
`merge_branch(delete_after=True)` 合并后会删除分支，事后无法再从分支
查出它包含哪些 commit。

因此本文件在**合并前**用 `commits_on_branch()` 记下分支上全部 commit 的
hash（`DeployRecord.applied_commits`），Rollback 时按逆序对每个 commit
调用公开的 `StateRepo.revert()`。不修改冻结的 `state_repo.py`。合并提交
本身保留在历史里（“试过、效果不好、已回退”本身是历史的一部分，见
`state_repo.py` `revert()` 文档）。

═══ Observe 的判定（保守，三态）═══

`Observation.verdict`：
  - `"improved"`：观察窗口内样本数达标且同类失败为 0 → Promote。
  - `"persists"`：观察窗口内同类失败次数达到 Problem 的重复阈值 → 同一个
    问题在部署后仍在重复出现，说明该改动没有解决问题 → Rollback。
  - `"inconclusive"`：样本不足或介于两者之间 → **不 Promote 也不 Rollback**，
    保持 `observing`，等待更多数据。宁可多观察，也不在证据不足时动仓库。

TODO(持久化)：`DeployRecord` 只提供 `to_dict()/from_dict()`，尚未落盘。
Observe 通常发生在部署之后的若干次运行之后（可能跨进程），接入
`AgentRuntime` 的 `learn` 步骤时需要决定存储位置（不应绕过 `StateRepo`
直接写受 git 管理的路径）。当前尚无运行时调用方，见 Sprint 计划文档。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.evolution.proposal_risk import ProposalRisk, classify_proposal_risk
from mini_agent.evolution.proposals import (
    EvolutionProposal,
    ProposalEvaluation,
    evaluate_proposal,
)
from mini_agent.evolution.state_repo import StateRepo, StateRepoError
from mini_agent.evolution.workspace import EvolutionWorkspace, EvolutionWorkspaceError

if TYPE_CHECKING:
    from mini_agent.core.experience_patterns import Problem
    from mini_agent.core.experience_store import ExperienceStore
    from mini_agent.evolution.workspace import SmokeBootResult

# DeployResult.status 取值
STATUS_EVALUATION_REJECTED = "evaluation_rejected"
STATUS_SANDBOX_ERROR = "sandbox_error"
STATUS_VALIDATION_FAILED = "validation_failed"
STATUS_SMOKE_FAILED = "smoke_failed"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_REJECTED = "rejected"
STATUS_DEPLOYED = "deployed"
STATUS_MERGE_FAILED = "merge_failed"

# DeployRecord.state 取值
STATE_OBSERVING = "observing"
STATE_PROMOTED = "promoted"
STATE_ROLLED_BACK = "rolled_back"
STATE_ROLLBACK_FAILED = "rollback_failed"

_VERDICTS = ("improved", "persists", "inconclusive")


# ─────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────

@dataclass
class DeployRecord:
    """一次已合并部署的凭据，Observe / Promote / Rollback 的输入。

    `applied_commits` 按分支上的时间顺序（旧 → 新）记录，Rollback 时逆序
    回退。`branch` 在合并后已被删除，仅作追溯标识。
    """

    proposal_id: str
    hypothesis_id: str
    problem_id: str
    expected_effect: str
    branch: str
    merge_commit: str
    applied_commits: list = field(default_factory=list)
    deployed_at: float = field(default_factory=time.time)
    state: str = STATE_OBSERVING
    revert_commits: list = field(default_factory=list)
    settle_reason: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "DeployRecord":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class DeployResult:
    """`deploy_proposal()` 的返回值，字段如实记录停在哪一步、为什么。"""

    proposal_id: str
    status: str
    branch: str = ""
    evaluation: Optional[ProposalEvaluation] = None
    risk: Optional[ProposalRisk] = None
    record: Optional[DeployRecord] = None
    errors: list = field(default_factory=list)

    @property
    def deployed(self) -> bool:
        return self.status == STATUS_DEPLOYED


@dataclass
class Observation:
    """一次 Observe 的结论（三态，见模块 docstring）。"""

    verdict: str
    detail: str = ""
    sample_count: int = 0
    failure_count: int = 0

    def __post_init__(self) -> None:
        if self.verdict not in _VERDICTS:
            raise ValueError(f"verdict 必须是 {_VERDICTS} 之一，实际：{self.verdict!r}")


# ─────────────────────────────────────────────────────────────────────
# Sandbox → Validation → (人审) → Deploy
# ─────────────────────────────────────────────────────────────────────

def _branch_name_for(proposal: EvolutionProposal, now: Optional[float] = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    slug = proposal.proposal_id.removeprefix("prop:")
    return f"evolve/{stamp}-exp-{slug}"


def deploy_proposal(
    proposal: EvolutionProposal,
    repo: StateRepo,
    approve: Optional[Callable[[EvolutionProposal, ProposalRisk], bool]] = None,
    eval_fn: Optional[Callable[[EvolutionProposal], dict]] = None,
    run_smoke_boot: bool = False,
    workspace_root: Optional[Path] = None,
) -> DeployResult:
    """把一个 `EvolutionProposal` 走完 Sandbox → Validation → Deploy。

    流程（任一步失败即停，并清理该步之前创建的沙盒/分支）：
      1. `evaluate_proposal()`（Sprint 9-2 的 dry-run 评估）不通过 → 停。
      2. 创建 `EvolutionWorkspace`，在沙盒内 `apply(auto_validators=True)`；
         校验失败 → 销毁沙盒并删分支。
      3. 可选 `run_smoke_boot`：副本进程冒烟启动，失败 → 销毁并删分支。
      4. `classify_proposal_risk()` 分级。
      5. 没有 `approve` → 停在 `pending_approval`（分支保留，等既有
         `/evolution merge` 或人工处理）；`approve` 返回 False → 删分支；
         返回 True → `merge_branch()`，得到 `DeployRecord`。

    合并前记录分支上的 commit hash（合并后分支会被删除，见模块 docstring）。
    """
    result = DeployResult(proposal_id=proposal.proposal_id, status="")

    # 1. dry-run 评估门
    evaluation = evaluate_proposal(proposal, repo, eval_fn=eval_fn)
    result.evaluation = evaluation
    if not evaluation.accepted:
        result.status = STATUS_EVALUATION_REJECTED
        result.errors = list(evaluation.validation_errors) or list(evaluation.reasons)
        return result

    # 2. Sandbox + Validation
    branch = _branch_name_for(proposal)
    result.branch = branch
    try:
        ws = EvolutionWorkspace.create(repo, branch=branch, workspace_root=workspace_root)
    except EvolutionWorkspaceError as e:
        result.status = STATUS_SANDBOX_ERROR
        result.errors = [f"创建沙盒失败：{e}"]
        return result

    try:
        try:
            applied = StateRepo(ws.path).apply(**proposal.apply_kwargs(), auto_validators=True)
        except Exception as e:  # git 子进程崩溃等：不留下半途沙盒
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.evolution.deployment.deploy_proposal")
            ws.destroy(delete_branch=True)
            result.status = STATUS_SANDBOX_ERROR
            result.errors = [f"沙盒内 apply 异常：{e}"]
            return result

        if not applied.ok:
            ws.destroy(delete_branch=True)
            result.status = STATUS_VALIDATION_FAILED
            result.errors = list(applied.validation_errors)
            return result

        # 3. 可选 smoke boot
        if run_smoke_boot:
            smoke: "SmokeBootResult" = ws.smoke_boot()
            if not smoke.ok:
                ws.destroy(delete_branch=True)
                result.status = STATUS_SMOKE_FAILED
                result.errors = [smoke.reason or "smoke boot 失败", smoke.stderr[-500:]]
                return result
    except BaseException:
        # 任何未预期异常都不能留下孤儿 worktree
        ws.destroy(delete_branch=True)
        raise

    # 沙盒使命完成；分支保留（评审/合并都需要它）
    ws.destroy(delete_branch=False)

    # 4. 风险分级
    risk = classify_proposal_risk(repo, branch)
    result.risk = risk

    # 5. 人审门
    if approve is None:
        result.status = STATUS_PENDING_APPROVAL
        return result

    if not approve(proposal, risk):
        try:
            repo.delete_branch(branch, force=True)
        except StateRepoError as e:
            result.errors = [f"拒绝后删除分支失败（分支残留，需手动清理）：{e}"]
        result.status = STATUS_REJECTED
        return result

    return _merge_and_record(proposal, repo, branch, result)


def _merge_and_record(
    proposal: EvolutionProposal, repo: StateRepo, branch: str, result: DeployResult,
) -> DeployResult:
    base = repo.current_branch() or "HEAD"
    # 必须在合并前取：merge_branch 默认会删除分支
    applied_commits = [c.commit for c in reversed(repo.commits_on_branch(branch, base=base))]
    try:
        merge_commit = repo.merge_branch(branch)
    except StateRepoError as e:
        result.status = STATUS_MERGE_FAILED
        result.errors = [str(e)]
        return result

    hyp = proposal.hypothesis
    result.record = DeployRecord(
        proposal_id=proposal.proposal_id,
        hypothesis_id=hyp.hypothesis_id,
        problem_id=hyp.problem_id,
        expected_effect=hyp.expected_effect,
        branch=branch,
        merge_commit=merge_commit,
        applied_commits=applied_commits,
    )
    result.status = STATUS_DEPLOYED
    return result


# ─────────────────────────────────────────────────────────────────────
# Observe
# ─────────────────────────────────────────────────────────────────────

def make_experience_observer(
    problem: "Problem",
    store: "ExperienceStore",
    min_samples: int = 3,
    persist_threshold: int = 2,
) -> Callable[[DeployRecord], Observation]:
    """用真实 Experience 数据构造 Observe 函数。

    只看 `record.deployed_at` **之后**产生的、与 `problem.category` 同类
    （用与 Sprint 9-1 完全相同的归一化）的 Experience：
      - 失败次数 >= `persist_threshold`（默认 2，与 Sprint 9-1 里“重复”的
        含义一致）→ `persists`，问题部署后仍在重复。
      - 样本数 >= `min_samples` 且失败为 0 → `improved`。
      - 其余 → `inconclusive`。
    """
    from mini_agent.core.experience_patterns import _FAILURE_STATUSES, _normalize_category

    def observe(record: DeployRecord) -> Observation:
        post = [
            e for e in store.all()
            if e.created_at > record.deployed_at
            and _normalize_category(e.goal_text) == problem.category
        ]
        failures = [e for e in post if e.status in _FAILURE_STATUSES]
        n, f = len(post), len(failures)
        if f >= persist_threshold:
            return Observation("persists", f"部署后同类任务 {n} 次中仍失败 {f} 次", n, f)
        if n >= min_samples and f == 0:
            return Observation("improved", f"部署后同类任务 {n} 次均未失败", n, f)
        return Observation("inconclusive", f"部署后同类样本 {n} 个、失败 {f} 次，证据不足", n, f)

    return observe


# ─────────────────────────────────────────────────────────────────────
# Promote / Rollback
# ─────────────────────────────────────────────────────────────────────

def rollback_deployment(record: DeployRecord, repo: StateRepo) -> DeployRecord:
    """逆序对部署带入的每个 commit 调用 `StateRepo.revert()`。

    任一 revert 失败即停并把 `state` 记为 `rollback_failed`（已成功的
    revert commit 仍保留在 `revert_commits`），**不吞异常、不静默声称成功**：
    半回退状态必须让调用方看得见。
    """
    for commit in reversed(record.applied_commits):
        try:
            record.revert_commits.append(repo.revert(commit))
        except StateRepoError as e:
            # `StateRepo.revert()` 遇到冲突时只抛异常，不会中止进行中的
            # `git revert`，仓库会停在“回退进行中”的冲突状态（真实测试
            # `test_rollback_failure_is_reported_not_swallowed` 复现过）。
            # `merge_branch()` 内部对合并冲突做了同样的 `--abort`；`revert()`
            # 没有对应处理，且 state_repo.py 属冻结文件不能改，因此在调用方
            # 收尾。用的是 `_run_git`（私有），这是已知代价，见 Sprint 计划文档。
            repo._run_git(["revert", "--abort"], check=False)
            record.state = STATE_ROLLBACK_FAILED
            record.settle_reason = f"回退 {commit[:8]} 失败：{e}"
            return record
    record.state = STATE_ROLLED_BACK
    return record


def settle_deployment(
    record: DeployRecord,
    repo: StateRepo,
    observe: Callable[[DeployRecord], Observation],
) -> tuple[DeployRecord, Observation]:
    """Observe 之后决定 Promote / Rollback / 继续观察。

    只处理仍在 `observing` 的记录，已定论（promoted/rolled_back）的记录
    原样返回，防止重复回退同一批 commit。
    """
    if record.state != STATE_OBSERVING:
        return record, Observation("inconclusive", f"记录已处于 {record.state}，不再处理")

    obs = observe(record)
    if obs.verdict == "improved":
        record.state = STATE_PROMOTED
        record.settle_reason = obs.detail
    elif obs.verdict == "persists":
        rollback_deployment(record, repo)
        if record.state == STATE_ROLLED_BACK:
            record.settle_reason = obs.detail
    # inconclusive：保持 observing，不动仓库
    return record, obs


__all__ = [
    "DeployRecord", "DeployResult", "Observation",
    "deploy_proposal", "make_experience_observer",
    "rollback_deployment", "settle_deployment",
    "STATUS_EVALUATION_REJECTED", "STATUS_SANDBOX_ERROR", "STATUS_VALIDATION_FAILED",
    "STATUS_SMOKE_FAILED", "STATUS_PENDING_APPROVAL", "STATUS_REJECTED",
    "STATUS_DEPLOYED", "STATUS_MERGE_FAILED",
    "STATE_OBSERVING", "STATE_PROMOTED", "STATE_ROLLED_BACK", "STATE_ROLLBACK_FAILED",
]
