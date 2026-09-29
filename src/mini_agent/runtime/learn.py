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

**“建议回退”的用户出口**（Phase 9 收尾之后追加，见 10 号文档 Sprint 9-4 局限 §2）：
  - `LearnReport.render_notice()`：纯函数，产出给人看的中文提示；`/goal` 结束时
    由 CLI 打印。只读、无副作用，不受任何开关控制（learn 本身已是 opt-in）。
  - 看板通知：`run_learn_step(notify=True)`（`goal_mode.runtime_learn_notify_enabled`，
    默认关闭）时，经 `NotificationDispatcher` 发一条通知。同一个部署只提醒一次
    （`DeployRecord.rollback_notified_at` 去重）——`persists` 的记录会一直保持
    `observing`，不去重就会每次运行都发。
  - `collect_deploy_overview()`：`/evolution deploys` 用的只读盘点。
"""

from __future__ import annotations

import logging
import time
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
    # 下面三个字段供“用户出口”（提示/通知）使用；默认值保证旧调用方不受影响。
    applied_commits: list = field(default_factory=list)  # 分支上的提交，旧 → 新
    problem_category: str = ""
    expected_effect: str = ""


# 看板通知的 source（需与 notification/reports_store.py::_SOURCE_CATEGORY_MAP 一致）
SOURCE_ROLLBACK_RECOMMENDED = "learn_rollback_recommended"
SOURCE_ROLLBACK_FAILED = "learn_rollback_failed"


def _short(commit: str) -> str:
    return (commit or "")[:8]


def _describe(o: "ObservedDeployment") -> list:
    """一个部署的说明行（终端提示与看板通知共用同一份文案）。"""
    lines = [f"{o.proposal_id}：{o.detail}（同类任务样本 {o.sample_count}，失败 {o.failure_count}）"]
    if o.expected_effect:
        lines.append(f"预期效果：{o.expected_effect}")
    if o.action == ACTION_ROLLBACK_RECOMMENDED:
        if o.applied_commits:
            # `/evolution revert` 一次只接一个 commit，且对 --no-ff 合并提交会失败，
            # 所以给出分支上各提交的逆序（先新后旧）。
            order = "，再 ".join(f"/evolution revert {_short(c)}" for c in reversed(o.applied_commits))
            lines.append(f"回退（先新后旧，逐个执行）：{order}")
        else:
            lines.append("该记录没有 commit 信息，请用 /evolution log 找到对应提交后再回退。")
    return lines


@dataclass
class LearnReport:
    observed: list = field(default_factory=list)   # list[ObservedDeployment]
    problems: list = field(default_factory=list)   # list[Problem]
    errors: list = field(default_factory=list)     # list[str]
    notified: list = field(default_factory=list)   # 本次已发出看板通知的 proposal_id

    def render_notice(self) -> str:
        """给人看的提示；没有需要人处理的事时返回空串。纯函数，无副作用。"""
        recommended = self.rollback_recommended
        failed = [o for o in self.observed if o.action == ACTION_ROLLBACK_FAILED]
        blocks: list = []
        if recommended:
            head = (f"[自我演化] {len(recommended)} 个已部署的改动在部署后，同类任务仍在失败，"
                    "建议回退（系统没有自动回退）：")
            body = [f"  · {lines[0]}" + "".join(f"\n    {x}" for x in lines[1:])
                    for lines in (_describe(o) for o in recommended)]
            blocks.append("\n".join([head, *body, "  完整列表：/evolution deploys"]))
        if failed:
            head = (f"[自我演化] {len(failed)} 个改动自动回退失败，仓库可能停在半途状态，"
                    "请先检查 git status 再处理：")
            body = [f"  · {_describe(o)[0]}" for o in failed]
            blocks.append("\n".join([head, *body]))
        return "\n\n".join(blocks)

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
    notify: bool = False,
) -> LearnReport:
    """执行一次 learn：Observe 已部署改动 + 汇总重复问题。永不抛异常。

    `notify=True` 时，对需要人处理的结果（建议回退 / 自动回退失败）向看板发通知，
    见模块文档。默认 False：不写任何通知文件。
    """
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

    # ── 3. Notify（可选）─────────────────────────────────────────────
    if notify:
        try:
            _notify_kanban(report, paths, record_store)
        except Exception as e:  # 通知是旁路的旁路，绝不能影响 learn 的其它产出
            _log(e, "notify")
            report.errors.append(f"发送通知失败：{e}")

    return report


def _notify_kanban(report: LearnReport, paths: "AgentPaths", record_store) -> None:
    """把需要人处理的结果发到通知渠道（看板 kanban 渠道恒真，其它渠道按用户配置）。

    - 建议回退：同一个部署只发一次。发送成功（kanban 渠道写入成功）后在
      `DeployRecord.rollback_notified_at` 记时间戳；发送失败则不记，下一轮再试。
    - 回退失败：记录状态已变成 `rollback_failed`，不会再出现在下一轮的 observing 里，
      所以天然只出现一次，不需要去重标记。
    """
    from mini_agent.notification.dispatcher import NotificationDispatcher, NotificationMessage

    dispatcher = NotificationDispatcher(paths)
    for o in report.observed:
        if o.action == ACTION_ROLLBACK_RECOMMENDED:
            source = SOURCE_ROLLBACK_RECOMMENDED
            title = "自我演化：建议回退一个已部署的改动"
        elif o.action == ACTION_ROLLBACK_FAILED:
            source = SOURCE_ROLLBACK_FAILED
            title = "自我演化：自动回退失败，需要人工处理"
        else:
            continue
        try:
            record = record_store.get(o.proposal_id)
            if source == SOURCE_ROLLBACK_RECOMMENDED and record is not None \
                    and getattr(record, "rollback_notified_at", 0.0):
                continue  # 已经提醒过
            lines = _describe(o)
            body = "\n\n".join(lines)
            if source == SOURCE_ROLLBACK_FAILED:
                body += "\n\n仓库可能停在半途状态，请先检查 `git status` 再决定如何处理。"
            results = dispatcher.dispatch(NotificationMessage(
                title=title,
                body=body,
                source=source,
                meta={
                    "proposal_id": o.proposal_id,
                    "verdict": o.verdict,
                    "sample_count": o.sample_count,
                    "failure_count": o.failure_count,
                    "applied_commits": list(o.applied_commits),
                },
            ))
            if not results.get("kanban"):
                report.errors.append(f"看板通知未写入成功：{o.proposal_id}")
                continue
            report.notified.append(o.proposal_id)
            if source == SOURCE_ROLLBACK_RECOMMENDED and record is not None:
                record.rollback_notified_at = time.time()
                record_store.save(record)
        except Exception as e:
            _log(e, f"notify:{o.proposal_id}")
            report.errors.append(f"通知 {o.proposal_id} 失败：{e}")


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
        applied_commits=list(record.applied_commits or []),
        problem_category=record.problem_category,
        expected_effect=record.expected_effect,
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


@dataclass
class DeployOverview:
    rows: list = field(default_factory=list)     # list[dict]，按部署时间升序
    errors: list = field(default_factory=list)   # list[str]


def collect_deploy_overview(
    paths: "AgentPaths", *, min_samples: int = 3, persist_threshold: int = 2,
) -> DeployOverview:
    """`/evolution deploys` 的只读盘点。永不抛异常，**不写任何文件、不构造 StateRepo**。

    对仍在 `observing` 的记录现算一次 Observe 判断（与 `learn` 同口径），让用户不必
    等下一次 `/goal` 结束就能看到“建议回退”；已结算的记录只展示其已落盘的状态。
    """
    out = DeployOverview()
    try:
        from mini_agent.evolution.deploy_record_store import DeployRecordStore

        records = DeployRecordStore(paths.workdir_deploy_records).all()
    except Exception as e:
        _log(e, "overview_open")
        out.errors.append(f"读取 DeployRecord 失败：{e}")
        return out

    observe = None
    for r in records:
        row = {
            "proposal_id": r.proposal_id, "state": r.state, "deployed_at": r.deployed_at,
            "problem_category": r.problem_category, "expected_effect": r.expected_effect,
            "applied_commits": list(r.applied_commits or []),
            "settle_reason": r.settle_reason,
            "verdict": "", "detail": "", "sample_count": 0, "failure_count": 0,
            "suggestion": "", "notified": bool(getattr(r, "rollback_notified_at", 0.0)),
        }
        if r.state == "observing":
            try:
                if observe is None:  # 有 observing 记录才打开 Experience 库
                    from mini_agent.core.experience_store import ExperienceStore
                    from mini_agent.evolution.deployment import make_category_observer

                    observe = make_category_observer(
                        ExperienceStore(path=paths.workdir_experience_store),
                        min_samples=min_samples, persist_threshold=persist_threshold,
                    )
                obs = observe(r)
                row.update(verdict=obs.verdict, detail=obs.detail,
                           sample_count=obs.sample_count, failure_count=obs.failure_count)
                if obs.verdict == "persists":
                    row["suggestion"] = ACTION_ROLLBACK_RECOMMENDED
            except Exception as e:
                _log(e, f"overview:{r.proposal_id}")
                out.errors.append(f"观察 {r.proposal_id} 失败：{e}")
        out.rows.append(row)
    return out


def _log(exc: Exception, where: str) -> None:
    try:
        from mini_agent.errors import log_exception

        log_exception(exc, where=f"mini_agent.runtime.learn.{where}")
    except Exception:  # 日志本身出错也不能影响主流程
        _logger.debug("learn step error at %s: %s", where, exc)


__all__ = [
    "LearnReport", "ObservedDeployment", "run_learn_step",
    "DeployOverview", "collect_deploy_overview",
    "SOURCE_ROLLBACK_RECOMMENDED", "SOURCE_ROLLBACK_FAILED",
    "ACTION_NONE", "ACTION_PROMOTED", "ACTION_ROLLED_BACK",
    "ACTION_ROLLBACK_RECOMMENDED", "ACTION_ROLLBACK_FAILED",
]
