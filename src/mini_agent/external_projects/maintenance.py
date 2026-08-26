"""
external_projects/maintenance.py — 大管家对外部项目的"深度介入"：以目标项目
自己的 Workspace 为根，触发一次独立的提案-验证-落地流程

对应 `next_doc/external_projects_workspace_plan.md` 原则四 / 阶段 5 第一项。

【评估结论】（原则四要求"评估是否需要为外部项目场景做适配，git worktree
是否适用需要先验证"）：`StateRepo`/`EvolutionWorkspace`（`evolution/
state_repo.py`、`evolution/workspace.py`）从设计上就只依赖"传入的 root
是/能成为一个 git 仓库"，完全不假设 root 是 mini_agent 自身仓库的一
部分——`StateRepo._ensure_initialized()` 在 root 下没有 `.git` 时会
自动 `git init`，`EvolutionWorkspace.create()` 只调用
`repo.list_branches()`/`repo._run_git()`，同样不依赖 mini_agent 仓库
结构。因此 git worktree 隔离机制对外部项目"开箱即用"，不需要任何新的
隔离逻辑或适配层——本模块只是把"以外部项目 Workspace 为根"这件事显式
包一层，直接复用这两个既有类，不重新实现任何一部分。

流程（与 `tools/evolution.py::skill_propose` 同构，但不限定写入路径/tier，
因为外部项目的维护对象通常是任意脚本，不是 mini_agent 的声明式资产）：

  1. `StateRepo(target_root)` —— 若目标项目还没有 `.git`，这里会自动
     `git init` 一个（首次维护时的兜底，呼应 fresh-repo 场景）。
  2. `EvolutionWorkspace.create(repo, branch="evolve/<date>-fix-<slug>")`
     —— 独立 worktree，不触碰目标项目当前 checkout 的分支。
  3. 在 worktree 内 `StateRepo(ws.path).apply(changes, tier=...)`。
  4. 校验失败 → `destroy(delete_branch=True)`，返回失败原因，不落盘、
     不 commit（`StateRepo.apply()` 本身的既有保证）。
  5. 校验通过 → `destroy(delete_branch=False)`，分支和 commit 保留在
     目标项目自己的仓库里，等待人工 review + merge——本模块**不**提供
     "自动合并"能力，这正是原则四"daemon 的角色始终是触发者/协调者，
     不是执行者本身"的直接体现。真正落地（合并）由 `land_maintenance_
     fix()` 单独、显式地完成，调用方（人 / CLI）决定何时调用它，本模块
     不会在 `propose_maintenance_fix()` 内部自动调用。

tier 默认 `"T2"`（lint + 目标项目自己 `tests/` 全过，若有），不是
`skill_propose` 固定使用的 `"T1"`——`T1` 的"声明式资产加载校验"
（`evolution/validators.py::validate_t1_load`）是针对 SKILL.md / subagent
profile 等 mini_agent 特有资产设计的，对外部项目的任意脚本文件不适用；
调用方需要更严格/更宽松的校验时可以显式传其它 tier。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from mini_agent.evolution.state_repo import ChangeSet, StateRepo, StateRepoError
from mini_agent.evolution.validators import validators_for_tier
from mini_agent.evolution.workspace import EvolutionWorkspace, EvolutionWorkspaceError


class MaintenanceError(Exception):
    """propose_maintenance_fix() 在无法开始一次提案时抛出（目标目录不存在、changes 为空等）。"""


@dataclass
class MaintenanceProposalResult:
    """一次维护提案的结果。ok=False 时不产生任何 commit（未落盘）。"""

    ok: bool
    branch: str = ""
    commit: str = ""
    tier: str = ""
    forced_tier: bool = False
    validation_errors: List[str] = field(default_factory=list)
    error: str = ""
    change_type: str = "fix"


_SLUG_RE = re.compile(r"[^a-z0-9\-]+")


def _make_evolve_branch_name(slug: str) -> str:
    """生成 `evolve/<date>-fix-<slug>` 格式的分支名，与 skill_propose 的
    `evolve/<date>-skill-<name>` 约定同构（设计文档 4.4 节"分支与合并"）。"""
    import datetime

    date_str = datetime.date.today().isoformat()
    safe = _SLUG_RE.sub("-", slug.lower()).strip("-") or "fix"
    return f"evolve/{date_str}-fix-{safe}"


def propose_maintenance_fix(
    project_root: Path,
    changes: ChangeSet,
    message: str,
    *,
    slug: str = "",
    reason: str = "",
    tier: str = "T2",
    change_type: str = "fix",
) -> MaintenanceProposalResult:
    """
    以 `project_root`（某个外部项目自己的根目录，即它的 `Workspace.root`）
    为根，在一个新的隔离分支上尝试落一次改动。

    Args:
        project_root: 目标外部项目的根目录（不是 mini_agent 自身仓库）。
        changes: 改动集合，语义与 `StateRepo.apply()` 的 `changes` 完全
            相同（路径相对 `project_root`，值为 None 表示删除该文件）。
        message: commit message 主题。
        slug: 用于生成分支名的短标识，默认从 `message` 派生。
        reason: 记入 commit message body，说明为什么提出这次改动。
        tier: 风险分级，默认 "T2"，决定使用哪一组校验函数
            （见 `evolution/validators.py::validators_for_tier`）。
        change_type: "fix"（默认，纠错——有客观失败信号、能被
            health_check/entrypoint 退出码验证是否解决）或
            "enhancement"（优化——没有硬失败信号，效果好坏是主观权衡，
            见 `next_doc/stock_watch_continuous_improvement_plan.md`
            第 2 节）。纯附加字段，只透传进返回结果供调用方（daemon/
            CLI/大管家）展示不同的风险提示，不影响本函数内部任何校验
            逻辑——`change_type="enhancement"` 不会放宽或收紧 tier
            校验，两者是正交的两个维度。**约定**（不由代码强制）：
            `change_type="enhancement"` 的提案，`land_maintenance_fix`
            只能由人工在核对过证据后手动调用，禁止任何自动化脚本或
            agent 自主调用——校验通过只代表"没有引入已知的回归错误"，
            不代表"这个改动值得采纳"，两者是两件事。

    Returns:
        `MaintenanceProposalResult`。`ok=True` 时分支和 commit 已经留在
        目标项目自己的仓库里，尚未合并；`ok=False` 时什么都没有落盘。
    """
    root = Path(project_root)
    if not root.is_dir():
        raise MaintenanceError(f"目标外部项目目录不存在: {root}")
    if not changes:
        raise MaintenanceError("changes 不能为空——没有改动就不需要发起一次维护提案")
    if change_type not in ("fix", "enhancement"):
        raise MaintenanceError(
            f"change_type 必须是 'fix' 或 'enhancement'，得到 '{change_type}'"
        )

    try:
        main_repo = StateRepo(root)
    except StateRepoError as e:
        return MaintenanceProposalResult(ok=False, error=f"无法打开目标项目的 git 仓库: {e}")

    branch_name = _make_evolve_branch_name(slug or message)

    try:
        ws = EvolutionWorkspace.create(main_repo, branch=branch_name)
    except EvolutionWorkspaceError as e:
        return MaintenanceProposalResult(ok=False, error=f"创建隔离 worktree 失败: {e}")

    try:
        ws_repo = StateRepo(ws.path)
        result = ws_repo.apply(
            changes=changes,
            message=message,
            meta={
                "source": "external_project_maintenance",
                "proposed_by": "daemon-maintenance-agent",
                "reason": reason,
            },
            tier=tier,
            validators=validators_for_tier(tier),
        )
    except Exception as e:
        # apply() 内部异常（git 子进程崩溃等）：没有任何有意义的 commit 产生，
        # 清理 worktree + 删分支，不留下孤儿分支污染目标项目自己的仓库
        # （同 tools/evolution.py::skill_propose 的取舍）。
        from mini_agent.errors import log_exception

        log_exception(e, where="mini_agent.external_projects.maintenance.propose_maintenance_fix")
        ws.destroy(delete_branch=True)
        return MaintenanceProposalResult(ok=False, error=f"apply 过程中出现异常: {e}", change_type=change_type)

    if not result.ok:
        ws.destroy(delete_branch=True)
        return MaintenanceProposalResult(
            ok=False,
            tier=result.tier,
            forced_tier=result.forced_tier,
            validation_errors=result.validation_errors,
            error="校验失败，提案未提交（未落盘、未 commit）",
            change_type=change_type,
        )

    ws.destroy(delete_branch=False)
    return MaintenanceProposalResult(
        ok=True,
        branch=branch_name,
        commit=result.commit,
        tier=result.tier,
        forced_tier=result.forced_tier,
        change_type=change_type,
    )


def land_maintenance_fix(project_root: Path, branch: str) -> str:
    """
    人工 review 通过后，把提案分支合并进目标项目当前分支。

    `StateRepo.merge_branch()` 的薄封装，供 CLI / 显式调用方在决定"批准"
    之后调用——`propose_maintenance_fix()` 本身**不会**自动调用它，落地
    必须是一次显式、独立的动作（原则四）。

    Returns:
        合并后目标分支 HEAD 的 commit hash。
    """
    repo = StateRepo(Path(project_root))
    return repo.merge_branch(branch)


__all__ = [
    "MaintenanceError",
    "MaintenanceProposalResult",
    "propose_maintenance_fix",
    "land_maintenance_fix",
]
