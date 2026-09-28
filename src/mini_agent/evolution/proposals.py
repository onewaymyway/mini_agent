"""evolution/proposals.py — Hypothesis → Experiment → Evaluation（Phase 9 Sprint 9-2）

见 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-2：

    基于 `Problem`，生成 `Hypothesis`（一个可能的改进方案）和对应的
    `EvolutionProposal`；Proposal 生成后，复用现有的验证和评估机制，
    **不重新实现**，只是把输入从旧格式改为新的 `EvolutionProposal` 结构。

对应原方案 §42 闭环的中间几环：

    Experience → Pattern → Problem → **Hypothesis → Experiment →
    Evaluation → Evolution Proposal** → Sandbox → Validation → Deploy → ...

本文件负责加粗的部分；`Problem` 由 Sprint 9-1
（`core/experience_patterns.py`）产出，Sandbox/Deploy/Promote/Rollback
留给 Sprint 9-3。

═══ 与安全设施（`phase9-evolution-inventory.md` 第二节）的关系 ═══

`state_repo.py` / `workspace.py` / `validators.py` / `eval_runner.py`
是冻结的安全设施，本文件**只新增调用方**，不修改它们一行：

  - 风险分级/强制升级：调用 `StateRepo.resolve_tier()`（受保护路径命中
    时强制升 T3，`initiator` 为 autonomous/scheduled 时 T0→T1），不在
    本文件重新实现一套 tier 判定。
  - 校验：调用 `validators_for_tier(effective_tier)` 取出现有校验函数，
    以与 `StateRepo.apply()` **完全相同的签名与调用方式**
    （`validator(repo.root, changes)`）逐个执行。
  - eval 对比：`eval_fn` 由调用方注入（通常包一层
    `eval_runner.run_eval()` 并返回 `EvalReport.to_dict()`），本文件只读
    其 `summary`，回归判定口径与 `proposal_risk._check_eval_regression()`
    保持一致（tool_failure_rate 升高或 scenarios_ok 减少即算回归）。

═══ “Experiment” 在本 Sprint 的含义（显式范围声明）═══

原方案里的 Experiment 指“把提案放到隔离环境里真实试一次”。那一步依赖
`EvolutionWorkspace`（git worktree 沙盒），属于 Sprint 9-3 的
“Sandbox → Validation → Deploy 闭环打通”。因此本 Sprint 的 Experiment
只做两件事，均**不落盘、不 commit、不产生分支**：

  1. 用现有 Validators 对提案的 `changes` 做 dry-run（与 `apply()` 里
     落盘前的校验环节完全一致，只是校验通过后不写入）。
  2. 可选注入 `eval_fn` 做 eval 对比。

TODO(Sprint 9-3)：Proposal 通过评估后，交给 `EvolutionWorkspace` +
`StateRepo.apply()` 走真实 sandbox 部署；届时 `evaluate_proposal()` 的
dry-run 结果可作为部署前置门槛。

═══ LLM 调用点 ═══

与 `goals/gap.py::detect_gap(llm_judge=...)`、`simulation/engine.py`
的既有风格一致：LLM 生成假设的能力做成调用方注入的 `Callable`
（`llm_propose`），本文件不内置任何网络实现。未注入时走规则模板，产出
一条“把重复失败的教训沉淀成 lesson 规则文件”的保守假设（文档类改动，
T1 级），保证没有 LLM 的环境（测试/离线）也能走通闭环。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TYPE_CHECKING

from mini_agent.evolution.proposal_risk import _is_low_risk_path
from mini_agent.evolution.state_repo import ChangeSet, StateRepoError

if TYPE_CHECKING:
    from mini_agent.core.experience_patterns import Problem
    from mini_agent.evolution.state_repo import StateRepo


class ProposalError(ValueError):
    """Hypothesis/Proposal 的输入不合法（例如 LLM 返回了越权路径）。"""


# ─────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────

@dataclass
class Hypothesis:
    """一个可能的改进方案（原方案 §42 闭环的 Hypothesis 环节）。

    `changes` 是这条假设落地时要写入的文件改动（路径 -> 新内容，语义与
    `StateRepo.apply()` 的 `ChangeSet` 完全一致，`None` 表示删除）。
    `expected_effect` 是可被后续 Observe 环节（Sprint 9-3）对照的预期，
    用自然语言描述，不做数值化打分。
    """

    hypothesis_id: str
    problem_id: str
    statement: str
    rationale: str
    expected_effect: str
    changes: ChangeSet = field(default_factory=dict)
    # 来自 Problem 的证据强度，供 commit meta 追溯（对应
    # `StateRepo._build_commit_message()` 已识别的 occurrence_count）。
    problem_source: str = ""
    problem_occurrence_count: int = 0
    generated_by: str = "rule_template"   # "rule_template" | "llm"
    # Sprint 9-4：Observe 只能按“同类任务”统计部署后的 Experience，而
    # `Problem.problem_id` 里未必带得出 category（`failure_pattern_store:` 来源
    # 的 id 是 pattern_id），所以把 category 一路带到 `DeployRecord`。
    problem_category: str = ""


@dataclass
class EvolutionProposal:
    """可交给现有安全设施评估/落地的进化提案。

    字段与 `StateRepo.apply()` 的入参一一对应（`changes`/`message`/
    `meta`/`tier`/`initiator`），这样 Sprint 9-3 可以直接
    `repo.apply(**proposal.apply_kwargs())`，中间不需要再做格式转换。
    """

    proposal_id: str
    hypothesis: Hypothesis
    changes: ChangeSet
    message: str
    meta: dict
    tier: str
    initiator: str = "autonomous"

    def apply_kwargs(self) -> dict:
        return {
            "changes": self.changes,
            "message": self.message,
            "meta": self.meta,
            "tier": self.tier,
            "initiator": self.initiator,
        }


@dataclass
class ProposalEvaluation:
    """一次评估（Experiment + Evaluation）的结果，字段均如实记录判断依据。"""

    proposal_id: str
    accepted: bool
    requested_tier: str
    effective_tier: str
    forced_tier: bool
    validators_run: list = field(default_factory=list)
    validation_errors: list = field(default_factory=list)
    # None = 未提供 eval_fn 或 eval 数据不足，无法判断（与
    # `proposal_risk.ProposalRisk.eval_regression` 语义一致）。
    eval_regression: Optional[bool] = None
    reasons: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────
# Hypothesis 生成
# ─────────────────────────────────────────────────────────────────────

# lesson 规则文件落在 `.agent/lessons/` 下——`proposal_risk._LOW_RISK_PATH_
# PREFIXES` 已把该前缀认定为“文档/规则类、低风险”路径，本文件生成的默认
# 假设与那边的风险分级口径天然一致。
_LESSON_DIR = ".agent/lessons"

_SLUG_SAFE_RE = re.compile(r"[^0-9A-Za-z_-]+")


def _slug_for(problem: "Problem") -> str:
    """生成稳定、跨平台安全的文件名片段。

    `Problem.category` 可能含中文/空格/冒号，直接当文件名不安全；用
    “ASCII 可读部分 + 内容哈希”既保证可读，又保证同一 Problem 每次生成
    同一个文件名（幂等，重复生成不会堆出多个近似文件）。
    """
    ascii_part = _SLUG_SAFE_RE.sub("-", problem.category).strip("-")[:24]
    digest = hashlib.sha1(problem.problem_id.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part}-{digest}" if ascii_part else digest


def _validate_relative_path(path: str) -> str:
    """Hypothesis 的目标路径必须是仓库内相对路径，不允许绝对路径/`..`。

    `StateRepo.apply()` 自己也会拒绝仓库外的绝对路径，这里提前在生成
    阶段拦截，让 LLM 产出的越权路径在最早的环节就得到明确报错，而不是
    拖到评估/落地阶段才暴露。
    """
    if not isinstance(path, str) or not path.strip():
        raise ProposalError("hypothesis 目标路径为空")
    normalized = path.replace("\\", "/")
    p = Path(normalized)
    if p.is_absolute() or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise ProposalError(f"hypothesis 目标路径必须是仓库内相对路径：{path!r}")
    if ".." in p.parts:
        raise ProposalError(f"hypothesis 目标路径不允许包含 '..'：{path!r}")
    return normalized


def _rule_template_hypothesis(problem: "Problem") -> Hypothesis:
    slug = _slug_for(problem)
    target = f"{_LESSON_DIR}/{slug}.md"
    lessons_hint = ""
    if "最常见教训" in problem.description:
        lessons_hint = problem.description.split("最常见教训：", 1)[-1].strip()

    body_lines = [
        f"# 规则：{problem.category}",
        "",
        f"> 由 Self Evolution 从重复失败模式自动提出（来源：{problem.source}，"
        f"累计 {problem.occurrence_count} 次）。待人工确认后生效。",
        "",
        "## 问题",
        "",
        problem.description,
        "",
        "## 规则",
        "",
        (f"- 处理此类任务前，先回顾：{lessons_hint}" if lessons_hint
         else "- 处理此类任务前，先检索同类历史 Experience，确认上次失败的原因已被规避。"),
        "- 连续两轮无进展时，停止重复同一做法，改为向用户说明卡点。",
        "",
    ]
    return Hypothesis(
        hypothesis_id=f"hyp:{slug}",
        problem_id=problem.problem_id,
        statement=f"为“{problem.category}”类任务沉淀一条 lesson 规则，减少同类反复失败",
        rationale=(
            f"该类任务已重复失败 {problem.occurrence_count} 次"
            f"（证据来源：{problem.source}），说明现有提示/流程没有把上次的"
            "教训带入下一次执行。"
        ),
        expected_effect="同类任务的失败次数下降；若之后仍反复失败，说明规则不足以解决，应回滚并重新提出假设。",
        changes={target: "\n".join(body_lines)},
        problem_source=problem.source,
        problem_occurrence_count=problem.occurrence_count,
        problem_category=problem.category,
        generated_by="rule_template",
    )


def generate_hypothesis(
    problem: "Problem",
    llm_propose: Optional[Callable[["Problem"], dict]] = None,
) -> Hypothesis:
    """基于 `Problem` 生成一条 `Hypothesis`。

    Args:
        problem: Sprint 9-1 产出的结构化问题记录。
        llm_propose: 可选，调用方注入的 LLM 生成函数，返回 dict：
            `{"statement", "rationale", "expected_effect", "changes"}`，
            其中 `changes` 是 `{相对路径: 新内容}`。未注入时走规则模板。

    Raises:
        ProposalError: LLM 返回结构缺字段/路径越权。
    """
    if llm_propose is None:
        return _rule_template_hypothesis(problem)

    raw = llm_propose(problem)
    if not isinstance(raw, dict):
        raise ProposalError(f"llm_propose 应返回 dict，实际是 {type(raw).__name__}")
    for key in ("statement", "changes"):
        if not raw.get(key):
            raise ProposalError(f"llm_propose 返回缺少必填字段：{key}")
    raw_changes = raw["changes"]
    if not isinstance(raw_changes, dict):
        raise ProposalError("llm_propose 返回的 changes 必须是 {路径: 内容} 字典")

    changes: ChangeSet = {}
    for path, content in raw_changes.items():
        if content is not None and not isinstance(content, str):
            raise ProposalError(f"changes[{path!r}] 内容必须是 str 或 None")
        changes[_validate_relative_path(str(path))] = content

    slug = _slug_for(problem)
    return Hypothesis(
        hypothesis_id=f"hyp:{slug}",
        problem_id=problem.problem_id,
        statement=str(raw["statement"]),
        rationale=str(raw.get("rationale", "")),
        expected_effect=str(raw.get("expected_effect", "")),
        changes=changes,
        problem_source=problem.source,
        problem_occurrence_count=problem.occurrence_count,
        problem_category=problem.category,
        generated_by="llm",
    )


# ─────────────────────────────────────────────────────────────────────
# Proposal 构建
# ─────────────────────────────────────────────────────────────────────

def _default_tier(paths: list) -> str:
    """按改动路径给出 *请求* 的 tier（下限）。

    全部是文档/lesson 规则类路径 → T1；含其它路径 → T2。这只是调用方
    请求值：真正生效的 tier 仍由 `StateRepo.resolve_tier()` 决定（命中
    受保护路径会被强制升到 T3，只升不降），本函数不可能把提案降到
    安全设施允许的下限以下。
    """
    return "T1" if all(_is_low_risk_path(str(p)) for p in paths) else "T2"


def build_proposal(
    hypothesis: Hypothesis,
    tier: Optional[str] = None,
    initiator: str = "autonomous",
) -> EvolutionProposal:
    """把 `Hypothesis` 包装成可交给安全设施的 `EvolutionProposal`。

    `initiator` 默认 `"autonomous"`——本链路由 Experience 驱动而非用户
    显式发起，这样 `StateRepo.resolve_tier()` 的 initiator 上浮规则
    （T0→T1）对它同样生效，不会因为走了新入口而绕过原有的留痕要求。
    """
    if not hypothesis.changes:
        raise ProposalError("hypothesis 没有任何改动，无法构建 proposal")

    requested_tier = tier or _default_tier(list(hypothesis.changes.keys()))
    meta = {
        "source": "self_evolution",
        "proposed_by": "experience_driven_evolution",
        "source_lessons": [hypothesis.problem_id],
        "occurrence_count": hypothesis.problem_occurrence_count,
        "hypothesis_id": hypothesis.hypothesis_id,
        "problem_source": hypothesis.problem_source,
        "generated_by": hypothesis.generated_by,
    }
    return EvolutionProposal(
        proposal_id=f"prop:{hypothesis.hypothesis_id.removeprefix('hyp:')}",
        hypothesis=hypothesis,
        changes=dict(hypothesis.changes),
        message=hypothesis.statement,
        meta=meta,
        tier=requested_tier,
        initiator=initiator,
    )


# ─────────────────────────────────────────────────────────────────────
# Experiment + Evaluation：复用现有 Validators / eval 对比
# ─────────────────────────────────────────────────────────────────────

def _eval_regressed(report: dict) -> Optional[bool]:
    """从 `EvalReport.to_dict()` 形状的 dict 里判断是否回归。

    口径与 `proposal_risk._check_eval_regression()` 一致；数据缺失返回
    None（“无数据可判断”，不计入不利因素）。
    """
    summary = (report or {}).get("summary") or {}
    with_s = summary.get("with_skill") or {}
    without_s = summary.get("without_skill") or {}
    if not with_s or not without_s:
        return None
    return bool(
        with_s.get("tool_failure_rate", 0.0) > without_s.get("tool_failure_rate", 0.0)
        or with_s.get("scenarios_ok", 0) < without_s.get("scenarios_ok", 0)
    )


def evaluate_proposal(
    proposal: EvolutionProposal,
    repo: "StateRepo",
    eval_fn: Optional[Callable[[EvolutionProposal], dict]] = None,
) -> ProposalEvaluation:
    """对提案做 dry-run 评估：**不落盘、不 commit、不建分支**。

    流程与 `StateRepo.apply()` 落盘前的环节保持一致：
      1. `repo.resolve_tier()` 算出生效 tier（受保护路径强制 T3、
         autonomous 发起的 T0 上浮 T1）。
      2. `validators_for_tier(生效 tier)` 取现有校验函数，逐个以
         `validator(repo.root, changes)` 执行，收集全部失败原因。
      3. 可选 `eval_fn` 做 eval 对比，回归则不接受。

    这里刻意不复用 `apply()` 本身来“试跑”：`apply()` 校验通过就会写盘
    并 commit，无法表达“只评估、不落地”。校验函数本身与调用方式与
    `apply()` 相同，因此评估通过等价于“`apply()` 的校验环节会通过”。
    """
    from mini_agent.evolution.validators import validators_for_tier

    reasons: list[str] = []

    try:
        effective_tier, forced = repo.resolve_tier(
            list(proposal.changes.keys()), proposal.tier, initiator=proposal.initiator,
        )
    except StateRepoError as e:
        return ProposalEvaluation(
            proposal_id=proposal.proposal_id, accepted=False,
            requested_tier=proposal.tier, effective_tier=proposal.tier,
            forced_tier=False, reasons=[f"tier 非法：{e}"],
        )

    if forced and effective_tier != proposal.tier:
        reasons.append(f"tier 由 {proposal.tier} 强制升级为 {effective_tier}（受保护路径或 autonomous 发起）")

    validators = validators_for_tier(effective_tier)
    validation_errors: list[str] = []
    for validator in validators:
        result = validator(repo.root, proposal.changes)
        if not result.ok:
            validation_errors.append(result.reason)

    eval_regression: Optional[bool] = None
    if eval_fn is not None:
        eval_regression = _eval_regressed(eval_fn(proposal))
        if eval_regression:
            reasons.append("eval 对比显示存在回归（tool_failure_rate 升高或可跑通场景数减少）")

    if validation_errors:
        reasons.append(f"未通过 {effective_tier} 校验（{len(validation_errors)} 项失败）")

    accepted = not validation_errors and eval_regression is not True
    if accepted:
        reasons.append(f"通过 {effective_tier} 全部校验" + ("，eval 无回归" if eval_regression is False else ""))

    return ProposalEvaluation(
        proposal_id=proposal.proposal_id,
        accepted=accepted,
        requested_tier=proposal.tier,
        effective_tier=effective_tier,
        forced_tier=forced,
        validators_run=[getattr(v, "__name__", repr(v)) for v in validators],
        validation_errors=validation_errors,
        eval_regression=eval_regression,
        reasons=reasons,
    )


def propose_and_evaluate(
    problem: "Problem",
    repo: "StateRepo",
    llm_propose: Optional[Callable[["Problem"], dict]] = None,
    eval_fn: Optional[Callable[[EvolutionProposal], dict]] = None,
    tier: Optional[str] = None,
    initiator: str = "autonomous",
) -> tuple[EvolutionProposal, ProposalEvaluation]:
    """Problem → Hypothesis → Proposal → Evaluation 一条龙（便捷入口）。"""
    hypothesis = generate_hypothesis(problem, llm_propose=llm_propose)
    proposal = build_proposal(hypothesis, tier=tier, initiator=initiator)
    return proposal, evaluate_proposal(proposal, repo, eval_fn=eval_fn)


__all__ = [
    "ProposalError",
    "Hypothesis",
    "EvolutionProposal",
    "ProposalEvaluation",
    "generate_hypothesis",
    "build_proposal",
    "evaluate_proposal",
    "propose_and_evaluate",
]
