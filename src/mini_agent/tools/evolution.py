"""
tools/evolution.py — 自我演化工具（Stage 3.1 / Phase C）

对应 self_evolution_implementation_plan.md Stage 3.1：
  新增工具 skill_propose(name, content, source_lessons)——内部调用
  StateRepo.apply() 在独立的 evolve/<date>-skill-<name> 分支上写
  skills/<name>/SKILL.md，tier 固定 T1。

设计取舍：
  - 与 spawn_agent/spawn_named_agent 一样，本工具是模块级 @tool 装饰器注册的
    无状态函数，没有直接access 到调用它的 Agent 实例。需要知道"当前项目根目录"
    才能构造 StateRepo——复用 Phase E（3.3）已经建立的 thread-local provider
    模式（tools/orchestration.py 的 set_active_skills_provider 同款写法），
    而不是简单 fallback 到 Path.cwd()：project_root 可以通过 --project 显式
    指定、与 Path.cwd() 不一致，SubAgent 在后台线程里也不应该依赖进程级 cwd。
  - tier 固定为 T1（设计文档明确写明），不接受调用方传入的 tier 参数——
    "skill 提案"这个动作本身的风险等级是确定的，不应该被 prompt injection
    或模型的自由发挥改变成 T0（绕过加载校验）或更高（不必要地卡审批）。
    如果新 skill 的路径恰好命中 scripts/protected_paths.py 的红线（理论上
    不会，因为固定写在 skills/ 目录下），StateRepo.apply() 仍会按其内部
    规则强制升级到 T3，这一层不受本工具影响。
  - 写入路径固定为 .claude/skills/<name>/SKILL.md（与 config/prompt_builder.py
    的 _resolve_skills_dir() 候选路径列表第一项一致），保证新提案的 skill
    被 merge 进主分支后无需额外配置即可被 SkillLoader 发现。
  - 【关键】提案不直接 commit 到当前 checkout 的分支（通常是 main/master），
    而是用 Stage 2.3 的 EvolutionWorkspace 创建一个独立的 evolve/<date>-
    skill-<name> 分支 + worktree，在那个隔离环境里 apply()，main 分支
    完全不受影响。这是设计文档 4.4 节"分支与合并：evolve 分支取代 pending
    目录"的直接落地：
      - 审核 = git diff main..evolve/xxx（即 /evolution diff <commit>）
      - 批准 = merge（人工操作，本工具不提供"自动合并到 main"的能力——
        skill_propose 的职责到"产生一个可审核的分支"为止）
      - 拒绝 = 删分支（/evolution revert <commit> 或直接删除分支）
    worktree 内的 commit 通过共享的 git 对象库，从主仓库（包括 /evolution
    系列命令）天然可见，不需要任何额外同步机制。
"""

from __future__ import annotations

import json
import re
import threading as _threading
from pathlib import Path
from typing import Callable, Optional

from . import tool

# ── 模块级"当前项目根目录"提供者（thread-local，同 orchestration.py 的
#    active-skills provider 写法）──────────────────────────────────────────

_project_root_local = _threading.local()


def set_project_root_provider(provider: Optional[Callable[[], Path]]) -> None:
    """由 Agent.__init__ 调用，为当前线程注册一个返回 cfg.project_root 的回调。"""
    _project_root_local.provider = provider


def _get_project_root() -> Optional[Path]:
    provider = getattr(_project_root_local, "provider", None)
    if provider is None:
        return None
    try:
        return provider()
    except Exception:
        return None


# 合法 skill 名称：小写字母数字+连字符，与现有项目里 skill 目录命名风格一致
# （例如 bash-rm-safety、code-review），避免路径穿越或非法文件名。
_VALID_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,63}$")


@tool(
    name="skill_propose",
    description=(
        "Propose a new skill by committing skills/<name>/SKILL.md to a NEW dedicated "
        "evolve/<date>-skill-<name> git branch (never the currently checked-out branch) "
        "through the self-evolution safety net (StateRepo.apply(), tier=T1: schema + load "
        "validation, never committed if validation fails). The proposal is NOT active until "
        "a human reviews and merges that branch — this tool only creates the branch and commit. "
        "Use this only after evidence from multiple lessons supports the proposal — do not "
        "propose speculative or single-occurrence skills. The skill content must include YAML "
        "frontmatter with name and description fields, matching the existing SKILL.md "
        "convention used elsewhere in this project."
    ),
    schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Skill name (lowercase, hyphen-separated, e.g. 'bash-rm-safety'). "
                    "Becomes the directory name under skills/ and part of the evolve branch name."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "Full SKILL.md content including YAML frontmatter "
                    "(--- name: ... \\n description: ... \\n ---) followed by the skill body."
                ),
            },
            "source_lessons": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "entry_id values of the lesson(s) this proposal is based on. "
                    "Recorded in the commit message for traceability."
                ),
            },
            "reason": {
                "type": "string",
                "description": "Brief explanation of why this skill is being proposed.",
            },
        },
        "required": ["name", "content", "source_lessons"],
    },
    requires_approval=False,  # 真正的把关在 _RISKY_TOOLS（--sandbox 拦截）+ StateRepo 的 T1 校验流水线 + 提案落在独立分支等人工 merge
)
def _get_memory_backend_for_outcome_tracking(project_root: Path):
    """
    为 outcome_tracker.record_commit_baseline() 构造一个只读用途的
    MemoryBackend。skill_propose 是模块级无状态工具函数，没有直接持有
    调用它的 Agent 实例的 memory backend（同一份顾虑见文件头部关于
    project_root provider 的说明），这里独立构造一份轻量实例专供基线
    统计使用——只调用 all_entries()，不写入，构造/使用成本可忽略。
    失败返回 None，调用方（record_commit_baseline）对 None 会优雅降级
    （baseline 记为 0，不阻断记录流程）。
    """
    try:
        from mini_agent.config.loader import load_config
        from mini_agent.perception.memory_factory import create_memory_backend

        cfg = load_config(project_root=project_root)
        return create_memory_backend(cfg)
    except Exception:
        return None


def skill_propose(
    name: str,
    content: str,
    source_lessons: list,
    reason: str = "",
) -> str:
    project_root = _get_project_root()
    if project_root is None:
        return json.dumps({
            "ok": False,
            "error": "project_root provider not registered (skill_propose must be called "
                     "from within an Agent session; see set_project_root_provider).",
        }, ensure_ascii=False)

    if not _VALID_SKILL_NAME_RE.match(name):
        return json.dumps({
            "ok": False,
            "error": f"invalid skill name {name!r}: must be lowercase letters/digits/hyphens, "
                     "2-64 chars, starting with a letter or digit.",
        }, ensure_ascii=False)

    from mini_agent.evolution.state_repo import StateRepo, StateRepoError
    from mini_agent.evolution.validators import validators_for_tier
    from mini_agent.evolution.workspace import EvolutionWorkspace, EvolutionWorkspaceError

    try:
        main_repo = StateRepo(project_root)
    except StateRepoError as e:
        return json.dumps({"ok": False, "error": f"failed to open StateRepo: {e}"}, ensure_ascii=False)

    # [设计文档 4.4 节 "分支与合并：evolve 分支取代 pending 目录"]
    # 一次"进化尝试" = 创建分支 evolve/<date>-skill-<name>，在该分支对应的
    # worktree 里 apply()，不直接写当前 checkout 的分支（通常是 main/master）。
    # 审核 = git diff main..evolve/xxx（/evolution diff）；批准 = merge（人工，
    # 工具层不提供"自动 merge"操作）；拒绝 = 删分支（/evolution revert 或
    # 直接删除分支）。main 分支在这个过程中完全不受影响。
    branch_name = _make_evolve_branch_name(name)

    try:
        ws = EvolutionWorkspace.create(main_repo, branch=branch_name)
    except EvolutionWorkspaceError as e:
        return json.dumps({
            "ok": False,
            "error": f"failed to create evolve workspace for branch {branch_name!r}: {e}",
        }, ensure_ascii=False)

    rel_path = f".claude/skills/{name}/SKILL.md"

    try:
        ws_repo = StateRepo(ws.path)
        result = ws_repo.apply(
            changes={rel_path: content},
            message=f"Propose skill: {name}",
            meta={
                "source": "skill_propose",
                "source_lessons": list(source_lessons or []),
                "proposed_by": "evolution-agent",
                "reason": reason,
            },
            tier="T1",
            validators=validators_for_tier("T1"),
        )
    except Exception as e:
        # apply() 内部异常（git 子进程崩溃等）：清理 worktree + 删分支
        # （没有任何有意义的 commit 产生，留着只会污染 git branch 列表），
        # 然后把错误原样报告给调用方，而不是让 worktree 静默残留在 /tmp 下。
        ws.destroy(delete_branch=True)
        return json.dumps({
            "ok": False,
            "error": f"unexpected error while applying skill proposal: {e}",
        }, ensure_ascii=False)

    if not result.ok:
        ws.destroy(delete_branch=True)
        return json.dumps({
            "ok": False,
            "tier": result.tier,
            "forced_tier": result.forced_tier,
            "validation_errors": result.validation_errors,
            "message": (
                f"Proposal for skill '{name}' failed validation and was NOT committed. "
                "Fix the issues below and call skill_propose again."
            ),
        }, indent=2, ensure_ascii=False)

    ws.destroy(delete_branch=False)

    # [方案三，见 next_doc/priority_improvements_implementation_plan.md]
    # 自我进化"用户真实反馈"闭环：记录本次 commit 的效果回填基线。
    # source_lessons 元素约定为 lesson_review.py::LessonGroup.key（evolution-agent
    # 从 /evolve review 拿到的 lessons_payload 里的 group_key 字段透传而来）。
    # 失败静默：记录失败完全不影响本次 skill_propose 的返回结果。
    try:
        from mini_agent.evolution import outcome_tracker
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root)
        for group_id in (source_lessons or []):
            if not isinstance(group_id, str) or not group_id:
                continue
            outcome_tracker.record_commit_baseline(
                paths,
                _get_memory_backend_for_outcome_tracking(project_root),
                commit_id=result.commit,
                lesson_group_id=group_id,
                commit_summary=f"skill_propose: {name} ({reason[:80]})" if reason else f"skill_propose: {name}",
            )
    except Exception:
        pass

    return json.dumps({
        "ok": True,
        "commit": result.commit,
        "branch": branch_name,
        "tier": result.tier,
        "path": rel_path,
        "message": (
            f"Skill '{name}' proposed on branch '{branch_name}' ({result.commit[:8]}). "
            f"This is NOT yet active — review with /evolution show {result.commit[:8]} or "
            f"/evolution diff {result.commit[:8]}, then merge the branch manually to apply it, "
            f"or /evolution revert {result.commit[:8]} to discard."
        ),
    }, indent=2, ensure_ascii=False)


def _make_evolve_branch_name(skill_name: str) -> str:
    """生成 evolve/<date>-skill-<name> 格式的分支名（设计文档 4.4 节约定）。"""
    import datetime
    date_str = datetime.date.today().isoformat()
    return f"evolve/{date_str}-skill-{skill_name}"


__all__ = ["skill_propose", "set_project_root_provider"]
