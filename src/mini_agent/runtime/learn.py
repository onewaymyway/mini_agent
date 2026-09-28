"""runtime/learn.py — AgentRuntime 的 `learn` 步骤（Phase 9 Sprint 9-4）

见 `next_doc/refactor_plan/10-phase9-self-evolution-sprint-plan.md`
Sprint 9-4。`AgentRuntime.run_once()` 骨架里 `learn` 一步在 Sprint 8-1 留空，
现在接入 Phase 9 已产出的能力，但**只接“观察”和“汇总”，不接“提案”和“部署”**：

  1. Observe / Settle：对 `DeployRecordStore` 里仍在 `observing` 的部署，用
     真实 Experience 判断“同类问题部署后是否仍在重复”。
       - improved      → 标记 promoted（只改记录，不动仓库）
       - inconclusive  → 不动
       - persists      → 默认**只在报告里建议回退**；仅当
                         `auto_rollback=True` 时才对项目仓库执行 `git revert`
  2. Detect：汇总当前重复出现的 `Problem`（Sprint 9-1），写进报告。

**刻意不做的事**（不是遗漏）：
  - 不自动生成 Proposal、不自动 `deploy_proposal()`。“部署”需要 `approve`
    人审门，且 Hypothesis 的生成可能依赖 LLM；把它塞进每次运行的尾巴里，等于
    让 Agent 每跑一轮就有机会改自己，这与 Sprint 9-3 “不自动合并”的决策相冲突。
  - 不修改任何安全设施。回退只调用既有 `settle_deployment()` /
    `StateRepo.revert()`。

**永不抛异常**：`learn` 是旁路，任何失败只记录到 `LearnReport.errors`，
不能影响 Goal 本身的执行结果。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.storage.paths import AgentPaths

_logger = logging.getLogger("mini_agent.core.trace")

# ObservedDeployment.action 取值
ACTION_NONE = "none"                          # inconclusive：不动
ACTION_PROMOTED = "promoted"                  # improved：记录已标记 promoted
ACTION_ROLLED_BACK = "rolled_back"            # persists + auto_rollback：已回退
ACTION_ROLLBACK_RECOMMENDED = "rollback_recommended"  # persists，未开自动回退
ACTION_ROLLBACK_FAILED = "rollback_failed"    # 回退失败，需要人工处理


@dataclass
class ObservedDeployment:
    proposal_id: str
    verdict: str
    detail: str
    action: str
    sample_count: int = 0
    failure_count: int = 0


@dataclass
class LearnReport:
    observed: list = field(default_factory=list)   # list[ObservedDeployment]
    problems: list = field(default_factory=list)   # list[Problem]
    errors: list = field(default_factory=list)     # list[str]

    @property
    def rollback_recommended(self) -> list:
        return [o for o in self.observed if o.action == ACTION_ROLLBACK_RECOMMENDED]

    def summary(self) -> dict:
        """写进 `RuntimeCycleCompleted.payload` 的精简摘要（只含计数与 id）。"""
        return {
            "observed": len(self.observed),
            "promoted": sum(1 for o in self.observed if o.action == ACTION_PROMOTED),
            "rolled_back": sum(1 for o in self.observed if o.action == ACTION_ROLLED_BACK),
            "rollback_recommended": [o.proposal_id for o in self.rollback_recommended],
            "rollback_failed": [o.proposal_id for o in self.observed
                                if o.action == ACTION_ROLLBACK_FAILED],
            "problem_count": len(self.problems),
            "error_count": len(self.errors),
        }


def run_learn_step(
    paths: "AgentPaths",
    *,
    auto_rollback: bool = False,
    min_samples: int = 3,
    persist_threshold: int = 2,
) -> LearnReport:
    """执行一次 learn：Observe 已部署改动 + 汇总重复问题。永不抛异常。"""
    report = LearnReport()

    try:
        from mini_agent.core.experience_store import ExperienceStore
        from mini_agent.evolution.deploy_record_store import DeployRecordStore

        exp_store = ExperienceStore(path=paths.workdir_experience_store)
        record_store = DeployRecordStore(paths.workdir_deploy_records)
    except Exception as e:
        _log(e, "open_stores")
        report.errors.append(f"打开存储失败：{e}")
        return report

    # ── 1. Observe / Settle ─────────────────────────────────────────
    try:
        observing = record_store.list_observing()
    except Exception as e:
        _log(e, "list_observing")
        report.errors.append(f"读取 DeployRecord 失败：{e}")
        observing = []

    # 仓库只在真要回退时才构造（`get_repo` 延迟调用）：`StateRepo(root)` 在
    # 目标目录没有 .git 时会 `git init`，观察阶段不应有这种副作用。
    for record in observing:
        try:
            report.observed.append(_settle_one(
                record, paths, exp_store, record_store,
                auto_rollback=auto_rollback,
                min_samples=min_samples, persist_threshold=persist_threshold,
                get_repo=lambda: _get_repo(paths),
            ))
        except Exception as e:
            _log(e, f"settle:{record.proposal_id}")
            report.errors.append(f"处理 {record.proposal_id} 失败：{e}")

    # ── 2. Detect ───────────────────────────────────────────────────
    try:
        from mini_agent.core.experience_patterns import detect_problems

        report.problems = detect_problems(store=exp_store, paths=paths)
    except Exception as e:
        _log(e, "detect_problems")
        report.errors.append(f"问题模式汇总失败：{e}")

    return report


def _get_repo(paths: "AgentPaths"):
    from mini_agent.evolution.state_repo import StateRepo

    return StateRepo(paths.project_root)


def _settle_one(
    record, paths, exp_store, record_store, *,
    auto_rollback: bool, min_samples: int, persist_threshold: int, get_repo,
) -> ObservedDeployment:
    from mini_agent.evolution.deployment import (
        STATE_PROMOTED,
        STATE_ROLLBACK_FAILED,
        STATE_ROLLED_BACK,
        make_category_observer,
        settle_deployment,
    )

    observe = make_category_observer(
        exp_store, min_samples=min_samples, persist_threshold=persist_threshold,
    )
    obs = observe(record)
    base = dict(
        proposal_id=record.proposal_id, verdict=obs.verdict, detail=obs.detail,
        sample_count=obs.sample_count, failure_count=obs.failure_count,
    )

    if obs.verdict == "inconclusive":
        return ObservedDeployment(action=ACTION_NONE, **base)

    if obs.verdict == "persists" and not auto_rollback:
        # 不动仓库：回退与合并一样，需要人来决定。记录保持 observing，下一轮
        # 仍会再次给出建议，直到人处理或开启自动回退。
        return ObservedDeployment(action=ACTION_ROLLBACK_RECOMMENDED, **base)

    # improved（只改记录）或 persists + auto_rollback（真回退）：
    # 复用 Sprint 9-3 的 settle_deployment，观察结果直接透传，避免重复统计。
    repo = get_repo() if obs.verdict == "persists" else None
    settled, _ = settle_deployment(record, repo, lambda _r: obs)
    record_store.save(settled)
    if settled.state == STATE_PROMOTED:
        return ObservedDeployment(action=ACTION_PROMOTED, **base)
    if settled.state == STATE_ROLLED_BACK:
        return ObservedDeployment(action=ACTION_ROLLED_BACK, **base)
    if settled.state == STATE_ROLLBACK_FAILED:
        base["detail"] = settled.settle_reason or base["detail"]
        return ObservedDeployment(action=ACTION_ROLLBACK_FAILED, **base)
    return ObservedDeployment(action=ACTION_NONE, **base)


def _log(exc: Exception, where: str) -> None:
    try:
        from mini_agent.errors import log_exception

        log_exception(exc, where=f"mini_agent.runtime.learn.{where}")
    except Exception:  # 日志本身出错也不能影响主流程
        _logger.debug("learn step error at %s: %s", where, exc)


__all__ = [
    "LearnReport", "ObservedDeployment", "run_learn_step",
    "ACTION_NONE", "ACTION_PROMOTED", "ACTION_ROLLED_BACK",
    "ACTION_ROLLBACK_RECOMMENDED", "ACTION_ROLLBACK_FAILED",
]
