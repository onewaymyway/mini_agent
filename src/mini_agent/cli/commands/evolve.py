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
    elif sub in ("phase-g", "phase_g", "phaseg"):
        _handle_phase_g(rest, agent)
    else:
        R.print_error(
            "Usage: /evolve [review [--global] [--tier T1|T2] | "
            "list [--global] [--tier T1|T2] | phase-g [--dry-run]]"
        )


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


def _handle_phase_g(rest: list[str], agent) -> None:
    """
    [Stage 8 / 8.1] /evolve phase-g — 手动触发 Phase G 后台循环扫描。

    子命令选项：
      --dry-run   只展示报告，不写入节奏治理记录（方便反复测试）
      --force     忽略时间门控，强制运行（即使 24h 内已运行过）
    """
    if agent is None:
        R.print_error("No active agent context for /evolve phase-g.")
        return

    dry_run = "--dry-run" in rest
    force   = "--force"   in rest

    try:
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.evolution.phase_g import run_phase_g, should_run_phase_g

        paths = AgentPaths(agent.cfg.project_root)

        if not force and not should_run_phase_g(paths):
            R.print_info(
                "[phase-g] 24h 内已运行过，跳过（使用 --force 强制运行）。"
            )
            return

        R.print_info("[phase-g] 开始扫描…")

        knowledge_llm_call = None
        _pool = getattr(agent, "_client_pool", None)
        if _pool is not None:
            from mini_agent.perception.memory_factory import build_llm_call
            knowledge_llm_call = lambda prompt: build_llm_call(_pool.current_client)(prompt)

        report = run_phase_g(
            paths,
            skill_loader=getattr(agent, "skill_loader", None),
            memory_backend=getattr(agent, "_memory", None),
            knowledge_llm_call=knowledge_llm_call,
        )

        _print_phase_g_report(report)

        if dry_run:
            R.print_info("[phase-g] --dry-run 模式，节奏治理记录未写入。")
    except Exception as e:
        R.print_error(f"[phase-g] 运行失败：{e}")


def _print_phase_g_report(report) -> None:
    """格式化输出 Phase G 报告。"""
    from rich.table import Table
    from rich import box as rbox

    # ── 8.2 剪枝候选 ──
    if report.prune_candidates:
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Skill", min_width=20)
        t.add_column("Reason", min_width=40)
        t.add_column("Last Used (days)", min_width=16)
        for c in report.prune_candidates:
            t.add_row(c.name, c.reason[:60], f"{c.last_used_days_ago:.0f}d")
        R.console.print("\n[bold yellow]⚠  剪枝候选[/bold yellow]")
        R.console.print(t)
    else:
        R.console.print("[dim]  ✓ 无剪枝候选[/dim]")

    # ── 8.3 能力地图 ──
    if report.capability_map:
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Domain", min_width=18)
        t.add_column("Confidence", min_width=12)
        t.add_column("✓ / ✗", min_width=8)
        for e in sorted(report.capability_map, key=lambda x: -x.confidence):
            bar = "▓" * int(e.confidence * 10) + "░" * (10 - int(e.confidence * 10))
            t.add_row(e.domain, f"{bar} {e.confidence:.0%}", f"{e.success_count}/{e.failure_count}")
        R.console.print("\n[bold blue]📊 能力地图（已写入 memory）[/bold blue]")
        R.console.print(t)
    else:
        R.console.print("[dim]  ─ 无任务历史可统计（能力地图为空）[/dim]")

    # ── 8.4 Scope 晋升候选 ──
    if report.promotion_candidates:
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Pattern", min_width=24)
        t.add_column("Projects", min_width=8)
        t.add_column("Confidence", min_width=10)
        t.add_column("Suggested Skill", min_width=20)
        for c in report.promotion_candidates:
            t.add_row(
                c.description[:40],
                str(c.observed_in_projects),
                f"{c.confidence:.0%}",
                c.suggested_skill_name,
            )
        R.console.print("\n[bold green]🚀 跨项目晋升候选[/bold green]")
        R.console.print(t)
        R.console.print("[dim]  提示：用 /evolve review 触发 evolution-agent 将候选转为 skill 提案[/dim]")
    else:
        R.console.print("[dim]  ✓ 无 Scope 晋升候选（跨项目模式数据不足或未达门槛）[/dim]")

    # ── 8.6 知识巩固（图书馆式索引）──
    kc = getattr(report, "knowledge_consolidation", None)
    if kc:
        R.console.print("\n[bold magenta]📚 知识巩固（分类树 / 实体摘要）[/bold magenta]")
        R.console.print(
            f"[dim]  新增分类节点 {kc.get('new_categories', 0)} 个，"
            f"仍待归类候选 {kc.get('remaining_unclassified', 0)} 条，"
            f"重写实体摘要 {kc.get('entities_summarized', 0)} 个[/dim]"
        )

    R.console.print(f"\n[dim]Phase G 完成，共发现 {len(report.prune_candidates)} 个剪枝候选、"
                    f"{len(report.promotion_candidates)} 个晋升候选[/dim]\n")
