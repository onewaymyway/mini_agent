"""
cli/commands/sessions.py — /session slash 命令处理

/session              — 显示当前 session 信息
/session list [n]     — 列出最近 n 个 session
/session save         — 立即保存当前 session
/session resume <id>  — 加载一个旧 session
/session new          — 清空历史，开始新 session
/session delete <id>  — 删除一个 session 文件
/session dir          — 显示 session 目录
/session search <q>   — 关键词搜索 session（需 --session-search）
/session pin <id>     — 置顶保护一个 session，cleanup 时永不删除
/session unpin <id>   — 取消置顶保护
/session cleanup [--dry-run] [--keep-days N] [--keep-count N] [--extract-first]
                       — 批量清理长期不用的旧 session（见 evolution/session_cleanup.py）
"""

from __future__ import annotations

from mini_agent.agent import Agent
from mini_agent.prompts import pm
import mini_agent.ui.renderer as R


def handle_session_cmd(args: list[str], agent: Agent) -> None:
    mgr = agent.session_manager
    if mgr is None:
        R.print_warning("Session saving is disabled (--no-save-session).")
        return

    sub = args[0].lower() if args else "info"

    if sub in ("info", "status", ""):
        sid = agent.session_id or "(none)"
        sf  = agent.session_file or "(not saved yet)"
        R.console.print("\n[bold]Current session:[/bold]")
        R.console.print(f"  ID   : [cyan]{sid}[/cyan]")
        R.console.print(f"  File : [dim]{sf}[/dim]")
        R.console.print(
            f"  Turns: {agent.stats.turns}  "
            f"Tokens: {agent.stats.input_tokens}↑ {agent.stats.output_tokens}↓"
        )

    elif sub == "list":
        limit = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
        metas = mgr.list_sessions(limit=limit)
        if not metas:
            R.console.print("[dim]No sessions found.[/dim]")
            return
        from rich.table import Table
        from rich import box as rbox
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("ID",      style="cyan",  width=10)
        t.add_column("Title",   min_width=28, max_width=40)
        t.add_column("Age",     width=12)
        t.add_column("Model",   width=20)
        t.add_column("Turns",   width=6,  justify="right")
        t.add_column("Tokens",  width=12, justify="right")
        for m in metas:
            t.add_row(
                m.id, m.title, m.age_str, m.model[:20],
                str(m.turns), f"{m.input_tokens}/{m.output_tokens}",
            )
        R.console.print(t)

    elif sub == "save":
        path = agent.save_session()
        if path:
            R.print_success(f"Saved → {path}")
        else:
            R.print_error("Save failed or nothing to save.")

    elif sub == "resume" and len(args) >= 2:
        sid = args[1]
        if agent.load_session(sid):
            R.print_success(
                f"Resumed [{agent.session_id}] — {len(agent.history)} messages loaded"
            )
        else:
            R.print_error(f"Session '{sid}' not found.")

    elif sub == "new":
        if agent.new_session():
            R.print_success("New session started.")
        else:
            R.print_error("Failed to start new session.")

    elif sub == "delete" and len(args) >= 2:
        sid = args[1]
        ok = mgr.delete(sid)
        if ok:
            R.print_success(f"Deleted session '{sid}'.")
        else:
            R.print_error(f"Session '{sid}' not found.")

    elif sub == "dir":
        R.console.print(f"Session directory: [cyan]{mgr.session_dir}[/cyan]")

    elif sub == "search" and len(args) >= 2:
        if not getattr(agent.cfg, "session_search_enabled", False):
            R.print_warning("Session search is disabled. Start with --session-search to enable.")
            return
        query = " ".join(args[1:])
        results = mgr.search(query)
        if not results:
            R.console.print(f"[dim]No sessions found for '{query}'.[/dim]")
            return
        from rich.table import Table
        from rich import box as rbox
        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("ID",      style="cyan", width=10)
        t.add_column("Title",   min_width=24, max_width=36)
        t.add_column("Summary", min_width=30, max_width=50)
        t.add_column("Age",     width=12)
        for m in results:
            summary = (m.summary[:60] + "…") if len(m.summary) > 60 else m.summary
            t.add_row(m.id, m.title, summary, m.age_str)
        R.console.print(t)

    elif sub == "pin" and len(args) >= 2:
        sid = args[1]
        ok = mgr.set_pinned(sid, True)
        if ok:
            R.print_success(f"Pinned session '{sid}' — 不会被 /session cleanup 删除。")
        else:
            R.print_error(f"Session '{sid}' not found.")

    elif sub == "unpin" and len(args) >= 2:
        sid = args[1]
        ok = mgr.set_pinned(sid, False)
        if ok:
            R.print_success(f"Unpinned session '{sid}'.")
        else:
            R.print_error(f"Session '{sid}' not found.")

    elif sub == "cleanup":
        _handle_session_cleanup(args[1:], agent, mgr)

    else:
        R.print_error(
            "Usage: /session | /session list [n] | /session save | "
            "/session resume <id> | /session new | /session delete <id> | "
            "/session dir | /session search <query> | "
            "/session pin <id> | /session unpin <id> | "
            "/session cleanup [--dry-run] [--keep-days N] [--keep-count N] "
            "[--extract-first] [--include-orphans] [--orphan-min-age-hours N]"
        )


def _handle_session_cleanup(rest: list[str], agent: Agent, mgr) -> None:
    """/session cleanup — 批量清理长期不用的旧 session。

    默认真正执行删除；加 --dry-run 只报告不删除，建议第一次先跑 dry-run 看看
    会删哪些。--extract-first 会对"候选删除但还没抽取过知识"的 session 先
    补跑一次离线抽取（消耗一次 LLM 调用/session），成功后再删。
    """
    from mini_agent.evolution.session_cleanup import (
        cleanup_sessions,
        format_report_lines,
        DEFAULT_KEEP_RECENT_DAYS,
        DEFAULT_KEEP_RECENT_COUNT,
        DEFAULT_MIN_TURNS_FOR_EXTRACTION,
        DEFAULT_ORPHAN_MIN_AGE_HOURS,
    )

    dry_run = "--dry-run" in rest
    extract_first = "--extract-first" in rest
    include_orphans = "--include-orphans" in rest
    keep_days = DEFAULT_KEEP_RECENT_DAYS
    keep_count = DEFAULT_KEEP_RECENT_COUNT
    orphan_min_age_hours = DEFAULT_ORPHAN_MIN_AGE_HOURS
    if "--keep-days" in rest:
        try:
            keep_days = float(rest[rest.index("--keep-days") + 1])
        except (ValueError, IndexError):
            R.print_error("--keep-days 需要一个数字参数")
            return
    if "--keep-count" in rest:
        try:
            keep_count = int(rest[rest.index("--keep-count") + 1])
        except (ValueError, IndexError):
            R.print_error("--keep-count 需要一个整数参数")
            return
    if "--orphan-min-age-hours" in rest:
        try:
            orphan_min_age_hours = float(rest[rest.index("--orphan-min-age-hours") + 1])
        except (ValueError, IndexError):
            R.print_error("--orphan-min-age-hours 需要一个数字参数")
            return

    llm_client = getattr(agent, "_llm", None) if extract_first else None
    cfg = agent.cfg if extract_first else None
    exclude_ids = {agent.session_id} if agent.session_id else set()

    report = cleanup_sessions(
        mgr,
        getattr(agent.cfg, "project_root", None),
        exclude_ids=exclude_ids,
        keep_recent_days=keep_days,
        keep_recent_count=keep_count,
        min_turns_for_extraction=DEFAULT_MIN_TURNS_FOR_EXTRACTION,
        extract_first=extract_first,
        llm_client=llm_client,
        cfg=cfg,
        dry_run=dry_run,
        include_orphans=include_orphans,
        orphan_min_age_hours=orphan_min_age_hours,
    )
    for line in format_report_lines(report):
        R.console.print(line)
