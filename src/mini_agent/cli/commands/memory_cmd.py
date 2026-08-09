"""
cli/commands/memory_cmd.py — /memory slash 命令处理

/memory                              — 对当前 session 立即触发一次摘要 +
                                        画像刷新（旧行为，不变）
/memory backfill [--dry-run] [--limit N]
                                      — [next_doc/memory_backfill_and_profile_update_plan.md
                                        方向一] 扫描 summary 为空但轮次达标
                                        的存量 session，离线补生成摘要并
                                        写入长期记忆。默认真正执行；加
                                        --dry-run 只报告不写入。
"""

from __future__ import annotations

from mini_agent.agent import Agent
import mini_agent.ui.renderer as R


def handle_memory_cmd(args: list[str], agent: Agent) -> None:
    if args and args[0] == "backfill":
        _handle_memory_backfill(args[1:], agent)
        return
    if args:
        R.print_error("Usage: /memory backfill [--dry-run] [--limit N]")
        return
    agent.trigger_summary_and_profile(force=True)


def _handle_memory_backfill(rest: list[str], agent: Agent) -> None:
    mgr = agent.session_manager
    if mgr is None:
        R.print_warning("Session saving is disabled (--no-save-session)，无法扫描/回填 session。")
        return

    if not getattr(agent.cfg.memory_backfill, "enabled", True):
        R.print_warning("记忆回填功能未开启（memory_backfill.enabled=false）。")
        return

    memory_backend = getattr(agent, "_memory", None)
    if memory_backend is None:
        R.print_warning("记忆功能未开启（memory.enabled=false），无法回填。")
        return

    llm_client = getattr(agent, "_llm", None)
    if llm_client is None:
        R.print_warning("当前没有可用的 LLM 客户端，无法生成摘要。")
        return

    dry_run = "--dry-run" in rest
    limit = agent.cfg.memory_backfill.max_sessions_per_run
    if "--limit" in rest:
        try:
            limit = int(rest[rest.index("--limit") + 1])
        except (ValueError, IndexError):
            R.print_error("--limit 需要一个整数参数")
            return

    from mini_agent.evolution.memory_backfill import backfill_sessions, format_report_lines

    exclude_ids = {agent.session_id} if agent.session_id else set()
    report = backfill_sessions(
        mgr,
        memory_backend=memory_backend,
        llm_client=llm_client,
        model=agent.cfg.model,
        exclude_ids=exclude_ids,
        min_turns_for_backfill=agent.cfg.memory_backfill.min_turns_for_backfill,
        max_sessions_per_run=limit,
        dry_run=dry_run,
    )
    for line in format_report_lines(report):
        R.console.print(line)
