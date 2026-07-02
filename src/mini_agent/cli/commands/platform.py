"""
cli/commands/platform.py — /platform slash 命令处理

/platform            — 显示当前平台标签、tag 策略摘要、各类可加载对象统计
/platform status     — 同上（默认子命令）
/platform filtered   — 列出本次运行中被平台/tag 规则过滤掉的对象（kind/name/reason）
/platform reload      — 重新读取 <project_root>/platform_policy.json，并触发一次热重载
                        （使新策略对 skill / agent profile 立即生效；tool / hook 是启动时
                        一次性注册的，策略变化对它们不会自动生效，需要重启进程）
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_platform_cmd(args: list[str], agent=None) -> None:
    from mini_agent.platform_filter import get_load_policy, KNOWN_PLATFORMS

    sub = args[0] if args else "status"

    if sub == "reload":
        project_root = agent.cfg.project_root if agent is not None else None
        from mini_agent.platform_filter import init_load_policy
        init_load_policy(project_root)
        R.print_success("Platform policy reloaded from platform_policy.json.")

        # 让新策略立即对 skill / agent profile 生效（它们的 discover 是幂等重扫描）。
        # tool / hook 是启动时一次性注册的，这里无法撤销已注册的对象，
        # 需要重启进程才能让策略变化对它们生效——在提示里说明清楚。
        if agent is not None and getattr(agent, "_hot_reloader", None) is not None \
                and agent._hot_reloader.has_watches:
            reports = agent._hot_reloader.force_reload()
            for r in reports:
                if r.has_changes:
                    R.print_success(f"[reload:{r.category}] {r.summary()}")
            agent._cached_system = None
        R.print_info(
            "注意：tool 与 hook 是启动时一次性注册的，策略变化对已注册的 "
            "tool/hook 不会自动生效（skill / agent profile 已随本次 reload 重新生效），"
            "如需让 tool/hook 侧也生效请重启 mini-agent。"
        )
        sub = "status"

    policy = get_load_policy()

    if sub == "status":
        active = sorted(policy.active_platforms)
        R.console.print("\n[bold]Platform Policy[/bold]")
        R.console.print(f"  Active platforms : {', '.join(active) if active else '(none detected)'}")
        R.console.print(f"  Known platforms  : {', '.join(sorted(KNOWN_PLATFORMS))}")
        R.console.print(f"  Config file      : {policy.config_path} "
                         f"({'found' if policy.config_path.is_file() else 'not found — no restrictions'})")
        R.console.print(f"  Tag deny list    : {sorted(policy._deny_tags) or '(empty)'}")
        R.console.print(f"  Tag allow list   : {sorted(policy._allow_tags) or '(empty, allow-all)'}")

        filtered = policy.filtered_log
        if filtered:
            R.console.print(f"\n  [dim]{len(filtered)} object(s) filtered this run "
                             f"— use /platform filtered for detail[/dim]")
        R.console.print()

    elif sub == "filtered":
        filtered = policy.filtered_log
        if not filtered:
            R.print_info("No skill/agent/hook/tool has been filtered out this run.")
            return

        from rich.table import Table
        from rich import box as rbox

        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Kind", style="cyan", min_width=6)
        t.add_column("Name", min_width=16, max_width=40)
        t.add_column("Reason", min_width=20)

        for entry in filtered:
            t.add_row(entry["kind"], entry["name"], entry["reason"])

        R.console.print("\n[bold]Filtered by platform/tag policy[/bold]")
        R.console.print(t)
        R.console.print()

    else:
        R.print_error("Usage: /platform [status|filtered|reload]")
