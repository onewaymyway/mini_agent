"""
evolution — 自我演化安全网（Stage 2）

对应 next_doc/self_evolution_implementation_plan.md Stage 2 /
next_doc/self_evolution_design.md 第 4 节"安全网设计"。

公共 API：
    from mini_agent.evolution import StateRepo, validators_for_tier, EvolutionWorkspace

三层结构：
    StateRepo            — 所有自我修改的唯一写入入口（apply/log/diff/revert）
    validators_for_tier  — 按 T0~T3 风险分级选取对应的校验函数集合
    EvolutionWorkspace    — 基于 git worktree 的进程级隔离验证环境

快速使用：
    from pathlib import Path
    from mini_agent.evolution import StateRepo, EvolutionWorkspace

    repo = StateRepo(Path("/path/to/project"))
    result = repo.apply(
        changes={"skills/foo/SKILL.md": "..."},
        message="Add foo skill",
        meta={"proposed_by": "evolution-agent"},
        tier="T1",
        auto_validators=True,
    )

    with EvolutionWorkspace.create(repo, branch="evolve/try-foo") as ws:
        result = ws.smoke_boot()
"""

from mini_agent.evolution.state_repo import (
    StateRepo,
    StateRepoError,
    ValidationResult,
    CommitInfo,
    ApplyResult,
    VALID_TIERS,
)
from mini_agent.evolution.validators import (
    validate_t0_schema,
    validate_t1_load,
    validate_t2_lint,
    validate_t2_existing_tests,
    validate_t3,
    validators_for_tier,
    TIER_VALIDATORS,
)
from mini_agent.evolution.workspace import (
    EvolutionWorkspace,
    EvolutionWorkspaceError,
    SmokeBootResult,
)

__all__ = [
    # state_repo
    "StateRepo",
    "StateRepoError",
    "ValidationResult",
    "CommitInfo",
    "ApplyResult",
    "VALID_TIERS",
    # validators
    "validate_t0_schema",
    "validate_t1_load",
    "validate_t2_lint",
    "validate_t2_existing_tests",
    "validate_t3",
    "validators_for_tier",
    "TIER_VALIDATORS",
    # workspace
    "EvolutionWorkspace",
    "EvolutionWorkspaceError",
    "SmokeBootResult",
]
