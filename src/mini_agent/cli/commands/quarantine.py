"""
cli/commands/quarantine.py — /quarantine slash 命令处理

/quarantine              — 默认等同于 status
/quarantine status       — 显示 auto_quarantine 总开关状态 + 所有记录摘要
/quarantine list         — 只列出已被自动屏蔽（quarantined=true）的对象
/quarantine remove <kind>:<name>  — 手动解除单个（如 /quarantine remove tool:xlsx_export）
/quarantine clear        — 清空所有记录（含未拉黑的失败计数）
/quarantine reload       — 重新读取 runtime_quarantine.json（手动改过文件后热更新）
/quarantine enable       — 打开 auto_quarantine 总开关（写回 platform_policy.json）
/quarantine disable      — 关闭 auto_quarantine 总开关（默认即为关闭）

关于总开关：mini_agent.auto_quarantine 这一整套"运行时自动屏蔽"机制默认关闭，
必须显式开启（本命令或直接编辑 platform_policy.json 的 auto_quarantine.enabled）
才会真正记录失败次数、触发自动拉黑、在加载阶段拦截。
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def handle_quarantine_cmd(args: list[str], agent=None) -> None:
    from mini_agent.auto_quarantine import get_quarantine_store
    from mini_agent.platform_filter import get_load_policy

    sub = args[0] if args else "status"
    policy = get_load_policy()
    store = get_quarantine_store()

    if sub == "enable":
        policy.set_auto_quarantine_enabled(True)
        R.print_success(
            "auto_quarantine 已开启（写入 platform_policy.json）。"
            "工具/skill/agent 反复出现环境不兼容错误时将被自动屏蔽。"
        )
        return

    if sub == "disable":
        policy.set_auto_quarantine_enabled(False)
        R.print_success(
            "auto_quarantine 已关闭（写入 platform_policy.json）。"
            "既有的运行时黑名单记录仍保留在 runtime_quarantine.json 里，"
            "重新 enable 后会立即生效，不会丢失历史计数。"
        )
        return

    if sub == "reload":
        store.reload()
        R.print_success("Runtime quarantine reloaded from runtime_quarantine.json.")
        sub = "status"

    if sub == "clear":
        store.clear()
        R.print_success("Runtime quarantine cleared (all records removed).")
        return

    if sub == "remove":
        if len(args) < 2 or ":" not in args[1]:
            R.print_error("Usage: /quarantine remove <kind>:<name>  (e.g. tool:some_tool)")
            return
        kind, _, name = args[1].partition(":")
        if store.unquarantine(kind, name):
            R.print_success(f"Removed from quarantine: {kind}:{name}")
        else:
            R.print_error(f"Not found in quarantine records: {kind}:{name}")
        return

    if sub == "status":
        enabled = policy.auto_quarantine_enabled
        threshold = policy.auto_quarantine_fail_threshold
        all_records = store.list_all()
        quarantined = [r for r in all_records if r["quarantined"]]

        R.console.print("\n[bold]Auto Quarantine[/bold]")
        R.console.print(
            f"  Status           : "
            f"{'[green]enabled[/green]' if enabled else '[dim]disabled (default)[/dim]'}"
        )
        R.console.print(f"  Fail threshold   : {threshold}")
        R.console.print(f"  Config file      : {policy.config_path}")
        R.console.print(f"  Quarantine file  : {store.config_path}")
        R.console.print(f"  Total records    : {len(all_records)}  "
                         f"(quarantined: {len(quarantined)})")
        if not enabled:
            R.console.print(
                "\n  [dim]开关处于关闭状态：即使 runtime_quarantine.json 里已有历史记录，"
                "也不会拦截任何 skill/tool/agent 的加载。使用 /quarantine enable 开启。[/dim]"
            )
        elif quarantined:
            R.console.print(f"\n  [dim]{len(quarantined)} object(s) currently quarantined "
                             f"— use /quarantine list for detail[/dim]")
        R.console.print()
        return

    if sub == "list":
        quarantined = store.list_quarantined()
        if not quarantined:
            R.print_info("No skill/tool/agent is currently quarantined.")
            return

        from rich.table import Table
        from rich import box as rbox

        t = Table(box=rbox.SIMPLE, show_header=True, header_style="bold dim")
        t.add_column("Kind", style="cyan", min_width=6)
        t.add_column("Name", min_width=16, max_width=32)
        t.add_column("Fails", justify="right", min_width=5)
        t.add_column("Reason", min_width=20, max_width=50)
        t.add_column("Platform", min_width=10)

        for rec in quarantined:
            t.add_row(
                rec["kind"], rec["name"], str(rec["fail_count"]),
                rec["last_reason"], ", ".join(rec["platform_tags"]),
            )

        R.console.print("\n[bold]Quarantined objects[/bold]")
        R.console.print(t)
        R.console.print()
        return

    R.print_error("Usage: /quarantine [status|list|remove <kind>:<name>|clear|reload|enable|disable]")
