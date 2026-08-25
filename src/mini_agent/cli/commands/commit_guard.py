"""
cli/commands/commit_guard.py — /commit-guard slash 命令处理

/commit-guard                    — 默认等同于 status
/commit-guard status             — 显示配置 + 账本摘要
/commit-guard on                 — 打开总开关（默认即为打开）
/commit-guard off                — 关闭总开关
/commit-guard scan               — 立即做一次撤销核对（忽略节流间隔），
                                    命中的撤销会写入 revert_record lesson
/commit-guard install-hooks [repo]  — 安装 post-checkout/post-merge/
                                    post-rewrite 哨兵 hook（默认当前项目仓库）
/commit-guard ledger [n]         — 查看账本最近 n 条记录（默认 20）
/commit-guard clear              — 清空账本（不影响已经生成的 lesson/reminder）

背景见 next_doc/agent_commit_undo_guard_plan.md 与
perception/agent_commit_guard.py 模块头部注释。
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def _project_root(agent):
    return getattr(agent.cfg, "project_root", None) if agent else None


def handle_commit_guard_cmd(args: list[str], agent=None) -> None:
    from mini_agent.perception import agent_commit_guard as guard
    from pathlib import Path

    root = _project_root(agent) or Path.cwd()
    sub = (args[0] if args else "status").lower()

    if sub == "on":
        cfg = guard.load_config(root)
        cfg.enabled = True
        guard.save_config(cfg, root)
        R.print_success("agent_commit_guard 已开启（写入 agent_commit_guard_config.json）。")
        return

    if sub == "off":
        cfg = guard.load_config(root)
        cfg.enabled = False
        guard.save_config(cfg, root)
        R.print_success(
            "agent_commit_guard 已关闭。已有的账本/lesson 记录仍保留，"
            "重新 on 后即可继续工作，不会丢失历史。"
        )
        return

    if sub == "clear":
        ledger_path = guard._ledger_path(root)
        if ledger_path.exists():
            ledger_path.unlink()
            R.print_success(f"已清空账本: {ledger_path}")
        else:
            R.print_info("账本本来就是空的，无需清空。")
        return

    if sub == "install-hooks":
        target = Path(args[1]) if len(args) > 1 else root
        written = guard.install_undo_scan_git_hooks(target)
        R.print_success(
            f"已在 {target}/.git/hooks/ 下安装/确认 {len(written)} 个哨兵 hook "
            f"(post-checkout / post-merge / post-rewrite)。"
        )
        R.console.print(
            "  [dim]这些 hook 只会 touch 一个空文件，不采集/不上报任何内容；"
            "`git reset` 本身不会触发任何 git hook，仍然依赖机会性节流核对 / "
            "SessionStart 兜底覆盖。[/dim]"
        )
        return

    if sub == "scan":
        cfg = guard.load_config(root)
        if not cfg.enabled:
            R.print_warning("agent_commit_guard 当前处于关闭状态，/commit-guard on 后再 scan。")
            return
        memory_sink = getattr(agent, "_memory", None) if agent else None
        events = guard.scan_for_undo(root, cfg, via="manual_scan")
        if not events:
            R.print_info("本次核对未发现新的撤销事件（所有已记账的 commit 都仍在历史里，或没有待核对的记录）。")
            return
        R.print_success(f"发现 {len(events)} 条新确认的撤销事件：")
        for ev in events:
            R.console.print(f"  [yellow]{ev.commit_hash[:8]}[/yellow]  {ev.subject or '(no subject)'}")
            if ev.files:
                R.console.print(f"    files: {', '.join(ev.files[:10])}")
            if memory_sink is not None:
                guard.record_undo_lesson(
                    memory_sink=memory_sink,
                    session_id=getattr(agent, "session_id", "") or "",
                    model=getattr(agent.cfg, "model", ""),
                    commit_hash=ev.commit_hash,
                    files=ev.files,
                    subject=ev.subject,
                )
        if memory_sink is None:
            R.console.print(
                "  [dim]当前没有可用的 memory 后端（未在 agent 会话内运行），"
                "已确认撤销但未写入 lesson。在 REPL 会话里执行本命令可自动写入。[/dim]"
            )
        return

    if sub == "ledger":
        n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 20
        entries = guard.CommitLedger(root).load_all()
        entries.sort(key=lambda e: -e.created_at)
        entries = entries[:n]
        if not entries:
            R.print_info("账本为空。")
            return
        import time as _time
        for e in entries:
            state = "undone" if e.undone else ("resolved" if e.resolved else "pending")
            color = {"undone": "red", "resolved": "green", "pending": "yellow"}[state]
            date_str = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(e.created_at))
            R.console.print(
                f"  [{color}]{state:<8}[/{color}] {e.commit_hash[:8]}  {date_str}  "
                f"{e.subject or '(no subject)'}"
            )
        return

    # status（默认）
    cfg = guard.load_config(root)
    all_entries = guard.CommitLedger(root).load_all()
    pending = [e for e in all_entries if not e.resolved]
    undone = [e for e in all_entries if e.undone]

    R.console.print("\n[bold]Agent Commit Guard[/bold]")
    R.console.print(
        f"  Status              : "
        f"{'[green]enabled (default)[/green]' if cfg.enabled else '[dim]disabled[/dim]'}"
    )
    R.console.print(f"  Config file         : {root}/agent_commit_guard_config.json")
    R.console.print(f"  Ledger file         : {guard._ledger_path(root)}")
    R.console.print(f"  Total commits logged: {len(all_entries)}  (pending: {len(pending)}, undone: {len(undone)})")
    R.console.print(
        f"  Immediate undo check: {'on' if cfg.immediate_undo_check else 'off'}  "
        f"Opportunistic scan  : {'on' if cfg.opportunistic_scan_enabled else 'off'} "
        f"(interval {cfg.opportunistic_scan_interval_sec:.0f}s)  "
        f"SessionStart scan   : {'on' if cfg.scan_on_session_start else 'off'}"
    )
    if undone:
        R.console.print(
            f"\n  [dim]{len(undone)} 次自动提交已被确认撤销，相关 revert_record lesson "
            f"应该已经生成，可用 /commit-guard ledger 查看明细。[/dim]"
        )
    if not cfg.enabled:
        R.console.print("\n  [dim]开关处于关闭状态：不会记账，也不会核对撤销。/commit-guard on 开启。[/dim]")
