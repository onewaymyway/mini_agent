"""
evolution/proposal_risk.py — 进化提案风险分级（看板与自主性改进方案 Track I）

对应 next_doc/kanban_and_autonomy_improvement_plan.md Track I：

    低风险：只改文档/注释/lesson 规则一类不涉及代码执行路径的变更，且
    T0~T3 全绿、eval 对比无回归 → 允许"一键合并"按钮，仍需人点，但不需要
    逐行审 diff。
    中/高风险：涉及核心逻辑改动 → 维持现状全人工审核。

设计取舍：
  - "T0~T3 全绿"这句话核实后不需要在本模块重新校验——`StateRepo.apply()`
    本身就是"校验失败则不落盘、不 commit"（见 state_repo.py 头部说明），
    一个提案分支上能看到 commit，就意味着这些 commit 在 apply() 时已经
    通过了各自 tier 对应的校验器。本模块要做的不是重新跑校验，而是读出
    这些 commit 的 tier，本身是否已经足够"低"（T2/T3 = 触碰核心逻辑/受
    保护路径，直接判高风险；只有 T0/T1 才有资格进一步判定是否低风险）。
  - 方案原文的"只改文档/注释"用改动路径模式判断（`*.md`、`next_doc/`、
    lesson 相关文件），不尝试对 diff 做语义级"是否只改了注释"分析——那
    需要解析每种语言的语法，成本远超本 Track 的收益，路径模式已经能覆盖
    方案原文举例的主要场景（文档/规则调整）。
  - eval 对比无回归：只在 `EvolutionWorkspace` 产出的 `eval_result.json`
    存在时才纳入判断（大多数轻量提案，比如纯文档改动，本来就不会跑
    eval），文件不存在时不阻塞"低风险"判定，只是这一项检查视为"未提供
    数据"而不计入不利因素——与仓库里其余"数据缺失不阻塞"的风格一致。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.evolution.state_repo import CommitInfo, StateRepo


# ── 低风险路径模式 ────────────────────────────────────────────────────────────

# 只要改动涉及的全部文件都匹配以下任一模式，才有资格被判定为"低风险"——
# 任何一个文件不匹配（哪怕只有一个），整个提案就退回"高风险"（保守：
# 宁可让一个混合了代码改动的提案走全人工审核，也不允许因为大部分文件是
# 文档就整体放行）。
_LOW_RISK_SUFFIXES = (".md", ".txt")
_LOW_RISK_PATH_PREFIXES = (
    "next_doc/",
    "docs/",
    ".agent/lessons",
    ".claude/skills/",  # SKILL.md 本身已经是 .md 后缀，这里额外覆盖同目录下
                         # 可能存在的非 .md 辅助文件（如 references/*.txt）
)
_LOW_RISK_EXACT_NAMES = ("CLAUDE.md", "Agent.md", "README.md")

# commit subject 里的 tier 标记：`[T0]`/`[T1]`/`[T2]`/`[T3]`（见
# StateRepo._build_commit_message()）。
_TIER_SUBJECT_RE = re.compile(r"^\[(T[0-3])\]")

_TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3}


def _is_low_risk_path(path: str) -> bool:
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    if name in _LOW_RISK_EXACT_NAMES:
        return True
    if p.startswith(_LOW_RISK_PATH_PREFIXES):
        return True
    return p.endswith(_LOW_RISK_SUFFIXES)


def _extract_tier(subject: str) -> Optional[str]:
    m = _TIER_SUBJECT_RE.match(subject.strip())
    return m.group(1) if m else None


@dataclass
class ProposalRisk:
    """一次风险分级结果，字段均为"如实记录"，不隐藏判断依据。"""
    branch: str
    risk: str                      # "low" | "high"
    reasons: list[str] = field(default_factory=list)
    max_tier: Optional[str] = None
    changed_paths: list[str] = field(default_factory=list)
    commit_count: int = 0
    eval_regression: Optional[bool] = None   # None = 无 eval 数据可判断

    def to_dict(self) -> dict:
        return {
            "branch": self.branch,
            "risk": self.risk,
            "reasons": self.reasons,
            "max_tier": self.max_tier,
            "changed_paths": self.changed_paths,
            "commit_count": self.commit_count,
            "eval_regression": self.eval_regression,
        }


def _check_eval_regression(eval_result_path: Path) -> Optional[bool]:
    """读取 EvolutionWorkspace 产出的 eval_result.json（结构见
    eval_runner.py::EvalReport.to_dict()），判断 with_skill 相对
    without_skill 是否有回归。

    判断口径（保守，任一项变差就算回归）：
      - tool_failure_rate 升高
      - scenarios_ok（跑通场景数）减少

    读取失败/字段缺失/文件不存在：返回 None（"无数据可判断"，不计入
    不利因素，也不视为"确认无回归"——调用方需要按 None 单独处理）。
    """
    try:
        if not eval_result_path.exists():
            return None
        data = json.loads(eval_result_path.read_text(encoding="utf-8"))
        summary = data.get("summary") or {}
        with_s = summary.get("with_skill") or {}
        without_s = summary.get("without_skill") or {}
        if not with_s or not without_s:
            return None
        failure_rate_regressed = with_s.get("tool_failure_rate", 0.0) > without_s.get("tool_failure_rate", 0.0)
        scenarios_regressed = with_s.get("scenarios_ok", 0) < without_s.get("scenarios_ok", 0)
        return bool(failure_rate_regressed or scenarios_regressed)
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.evolution.proposal_risk._check_eval_regression')
        return None


def classify_proposal_risk(
    repo: "StateRepo",
    branch: str,
    base: Optional[str] = None,
    eval_result_path: Optional[Path] = None,
) -> ProposalRisk:
    """
    对一个进化提案分支（`branch`）做风险分级，供看板/API 决定是否展示
    "一键合并"按钮。

    Args:
        repo: 目标项目的 StateRepo。
        branch: 待分级的提案分支名。
        base: 对比基准，默认使用 `repo.current_branch()`（通常是 main）。
        eval_result_path: 可选，`EvolutionWorkspace` 跑过 eval 后产出的
            `eval_result.json` 路径；不提供或文件不存在时，`eval_regression`
            字段为 `None`（无数据），不影响 risk 判定（见模块 docstring）。

    Returns:
        ProposalRisk，`risk` 字段为 `"low"` 或 `"high"`。
    """
    base_ref = base or repo.current_branch() or "HEAD"
    commits = repo.commits_on_branch(branch, base=base_ref)

    reasons: list[str] = []

    if not commits:
        # 分支相对 base 没有独有 commit：可能是空分支/已经合并过，没有
        # 可供分级的改动，保守判高风险（不应该出现"一键合并"按钮去合并
        # 一个什么都没改的分支，那本身就说明状态不对，需要人工确认）。
        return ProposalRisk(
            branch=branch, risk="high",
            reasons=["分支相对基准分支没有独有 commit，无法分级，需人工确认"],
            commit_count=0,
        )

    tiers = [t for t in (_extract_tier(c.subject) for c in commits) if t is not None]
    max_tier = max(tiers, key=lambda t: _TIER_RANK[t]) if tiers else None

    changed_paths: list[str] = []
    seen = set()
    for c in commits:
        for f in c.files:
            if f not in seen:
                seen.add(f)
                changed_paths.append(f)

    # 规则 1：tier 达到 T2/T3 → 直接高风险（核心逻辑改动/命中受保护路径）。
    if max_tier in ("T2", "T3"):
        reasons.append(f"包含 {max_tier} 级改动，涉及核心逻辑或受保护路径")
        return ProposalRisk(
            branch=branch, risk="high", reasons=reasons, max_tier=max_tier,
            changed_paths=changed_paths, commit_count=len(commits),
        )

    # 规则 2：改动路径必须全部匹配"低风险"模式（文档/lesson 规则等）。
    non_low_risk_paths = [p for p in changed_paths if not _is_low_risk_path(p)]
    if non_low_risk_paths:
        preview = ", ".join(non_low_risk_paths[:5])
        more = f" 等共 {len(non_low_risk_paths)} 个" if len(non_low_risk_paths) > 5 else ""
        reasons.append(f"包含非文档/规则类改动：{preview}{more}")
        return ProposalRisk(
            branch=branch, risk="high", reasons=reasons, max_tier=max_tier,
            changed_paths=changed_paths, commit_count=len(commits),
        )

    # 规则 3：eval 对比（若有数据）不能有回归。
    eval_regression: Optional[bool] = None
    if eval_result_path is not None:
        eval_regression = _check_eval_regression(eval_result_path)
        if eval_regression:
            reasons.append("eval 对比显示存在回归（tool_failure_rate 升高或可跑通场景数减少）")
            return ProposalRisk(
                branch=branch, risk="high", reasons=reasons, max_tier=max_tier,
                changed_paths=changed_paths, commit_count=len(commits),
                eval_regression=eval_regression,
            )

    reasons.append("全部改动为文档/lesson 规则类文件，tier ≤ T1，且无 eval 回归数据或数据显示无回归")
    return ProposalRisk(
        branch=branch, risk="low", reasons=reasons, max_tier=max_tier,
        changed_paths=changed_paths, commit_count=len(commits),
        eval_regression=eval_regression,
    )


__all__ = [
    "ProposalRisk",
    "classify_proposal_risk",
]
