"""
cli/commands/evolve.py — /evolve slash 命令处理（Stage 3.1 / Phase C）

对应 self_evolution_implementation_plan.md Stage 3.1：

/evolve review [--global] [--tier T1|T2]
    扫描 memory.jsonl（workdir 级，--global 时改为扫描 ~/.agent/memory.jsonl）中
    的 lesson，按设计文档 6.7 节的证据门槛分组判定，对达标的分组逐一 spawn
    evolution-agent（.agent/agents/evolution-agent.md）去审查并提案。

/evolve list
    只做扫描 + 列出达标分组，不 spawn evolution-agent——用于人工预览"现在
    有哪些 lesson 已经达到提案门槛"，不消耗 LLM 调用。

本命令是设计文档 6.1 节"角色分离"提到的两种触发方式之一（另一种是
SessionEnd hook 里"待处理 lesson 数量超过阈值"时异步 spawn，留待后续接入）。
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_evolve_cmd(args: list[str], agent=None) -> None:
    sub = args[0] if args else "review"
    rest = args[1:]

    if sub == "review":
        _handle_review(rest, agent, spawn=True)
    elif sub == "list":
        _handle_review(rest, agent, spawn=False)
    else:
        R.print_error("Usage: /evolve [review [--global] [--tier T1|T2] | list [--global] [--tier T1|T2]]")


def _parse_review_args(rest: list[str]) -> tuple[bool, str]:
    use_global = "--global" in rest
    tier = "T1"
    if "--tier" in rest:
        idx = rest.index("--tier")
        if idx + 1 < len(rest):
            candidate = rest[idx + 1].upper()
            if candidate in ("T1", "T2", "T3"):
                tier = candidate
    return use_global, tier


def _handle_review(rest: list[str], agent, spawn: bool) -> None:
    if agent is None:
        R.print_error("No active agent context for /evolve.")
        return

    use_global, tier = _parse_review_args(rest)

    backend = agent._global_memory if use_global else agent._memory
    if backend is None:
        scope_label = "global" if use_global else "project"
        R.print_error(
            f"No {scope_label} memory backend available "
            "(memory may be disabled; check /memory or config.memory.enabled)."
        )
        return

    from mini_agent.perception.lesson_review import scan_for_proposals

    entries = backend.all_entries()
    groups = scan_for_proposals(entries, tier=tier)

    if not groups:
        R.print_info(
            f"No lesson groups currently meet the {tier} evidence threshold "
            f"(scanned {len(entries)} memory entries)."
        )
        return

    _print_groups_table(groups, tier)

    if not spawn:
        R.print_info("Use '/evolve review' (without 'list') to spawn evolution-agent on these groups.")
        return

    _spawn_evolution_agent(agent, groups)


def _print_groups_table(groups, tier: str) -> None:
    from rich.table import Table
    from rich import box as rbox

    t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
    t.add_column("Group", min_width=24, max_width=48)
    t.add_column("Occurrence", min_width=10)
    t.add_column("Sessions", min_width=8)
    t.add_column("Human FB", min_width=8)
    t.add_column("Entries", min_width=7)

    for g in groups:
        t.add_row(
            g.key[:48],
            str(g.total_occurrence),
            str(len(g.session_ids)),
            "yes" if g.has_human_feedback else "no",
            str(len(g.entries)),
        )

    R.console.print(f"\n[bold]Lesson groups meeting {tier} threshold[/bold]  "
                     f"[dim]({len(groups)} found)[/dim]")
    R.console.print(t)
    R.console.print()


def _spawn_evolution_agent(agent, groups) -> None:
    from mini_agent.orchestrator.agent_profiles import get_profile_loader

    loader = get_profile_loader()
    if loader is None or loader.get("evolution-agent") is None:
        R.print_error(
            "evolution-agent profile not found. Expected at "
            ".agent/agents/evolution-agent.md (project) or ~/.agent/agents/evolution-agent.md (global)."
        )
        return

    from mini_agent.tools.orchestration import get_task_manager, spawn_named_agent

    mgr = get_task_manager()
    if mgr is None:
        R.print_error("Task manager not running; cannot spawn evolution-agent.")
        return

    existing_skills: list[str] = []
    if agent.skill_loader is not None:
        existing_skills = list(agent.skill_loader.available)

    lessons_payload = [g.to_dict() for g in groups]

    import json
    result_raw = spawn_named_agent(
        agent_type="evolution-agent",
        inputs={"lessons": lessons_payload, "existing_skills": existing_skills},
        context=f"Triggered by /evolve review ({len(groups)} lesson group(s) above threshold).",
        name="evolve-review",
        tags=["evolution", "auto-triggered"],
    )

    try:
        result = json.loads(result_raw)
    except Exception:
        R.print_error(f"Failed to spawn evolution-agent: {result_raw}")
        return

    if "error" in result:
        R.print_error(f"Failed to spawn evolution-agent: {result['error']}")
        return

    task_id = result.get("task_id", "?")
    R.print_success(
        f"evolution-agent spawned (task {task_id}) to review {len(groups)} lesson group(s). "
        f"Use /tasks log {task_id} to follow progress."
    )
