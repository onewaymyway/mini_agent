"""
cli/commands/protected_cmd.py — `/agent protected` slash 命令处理（阶段 4）

对应 next_doc/protected_files_manifest_and_delete_guard_plan.md 阶段 4：
在阶段 3（定期备份 + 缺失告警）基础上，提供手动恢复入口。**不做全自动
闭环**——发现缺失只告警，是否恢复、恢复到哪个版本，始终由用户显式决定。

子命令：
  /agent protected status               — 当前生效的受保护清单 + 最近一次
                                           备份/缺失核对概况
  /agent protected list                 — 列出所有可用快照（generation_id +
                                           时间 + 本次打包的路径数）
  /agent protected list <generation_id> — 列出某一份快照具体打包了哪些路径
  /agent protected restore <generation_id> [path] [--force]
                                         — 从指定快照恢复：不给 path 时恢复
                                           该快照打包的全部路径；给 path 时
                                           只恢复这一个路径。不加 --force 时
                                           只打印将要覆盖哪些路径、不执行，
                                           需要显式重新加 --force 才真正
                                           写盘（与 /evolution merge 的
                                           --force 惯例一致），避免手滑
                                           覆盖新数据。
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def _get_paths(agent):
    paths = getattr(agent, "_paths", None)
    if paths is not None:
        return paths
    from mini_agent.storage.paths import AgentPaths
    return AgentPaths(agent.cfg.project_root)


def handle_protected_cmd(args: list[str], agent=None) -> None:
    if agent is None:
        R.print_error("此命令需要在 agent 会话内运行。")
        return

    paths = _get_paths(agent)
    sub = args[0] if args else "status"
    rest = args[1:]

    if sub == "status":
        _cmd_status(paths)
    elif sub == "list":
        _cmd_list(paths, rest)
    elif sub == "restore":
        _cmd_restore(paths, rest)
    else:
        R.print_error(f"Unknown /agent protected subcommand: {sub!r}")
        R.print_info("Available: status, list [generation_id], restore <generation_id> [path]")


def _cmd_status(paths) -> None:
    from scripts.protected_files import ProtectedFilesGuard
    from mini_agent.evolution.protected_files_backup import _backup_root, _list_generations

    guard = ProtectedFilesGuard(paths.project_root)
    entries = guard.list_entries()

    R.console.print("\n[bold]Protected files[/bold]")
    if not entries:
        R.console.print("  [dim]当前没有声明任何受保护路径"
                         "（未找到 protected_files.txt）。[/dim]\n")
        return

    for e in entries:
        suffix = "/" if e.is_dir else ""
        R.console.print(f"  {e.path}{suffix}")

    generations = _list_generations(_backup_root(paths.project_root))
    R.console.print(f"\n  Snapshots        : {len(generations)}")
    if generations:
        latest = generations[-1].name
        R.console.print(f"  Latest snapshot  : {latest}")
        R.console.print(f"  [dim]/agent protected list {latest} 查看详情，"
                         f"/agent protected restore {latest} 可恢复。[/dim]")
    else:
        R.console.print("  [dim]尚无快照——sys:protected_files_backup 还没跑过第一轮，"
                         "或该 cron job 未启用。[/dim]")
    R.console.print()


def _cmd_list(paths, rest: list[str]) -> None:
    from mini_agent.evolution.protected_files_backup import (
        _backup_root, _list_generations, _snapshot_manifest,
    )

    backup_root = _backup_root(paths.project_root)
    generations = _list_generations(backup_root)

    if not generations:
        R.print_info("尚无任何快照。")
        return

    if rest:
        gen_id = rest[0]
        gen_dir = backup_root / gen_id
        if not gen_dir.is_dir():
            R.print_error(f"Snapshot not found: {gen_id!r}")
            return
        manifest = sorted(_snapshot_manifest(gen_dir))
        R.console.print(f"\n[bold]Snapshot {gen_id}[/bold]  ({len(manifest)} path(s))")
        for p in manifest:
            R.console.print(f"  {p}")
        R.console.print()
        return

    R.console.print("\n[bold]Available snapshots[/bold]")
    for gen_dir in generations:
        manifest = _snapshot_manifest(gen_dir)
        R.console.print(f"  {gen_dir.name}  ({len(manifest)} path(s))")
    R.console.print(f"\n  [dim]/agent protected list <generation_id> 查看某一份快照的详情[/dim]\n")


def _cmd_restore(paths, rest: list[str]) -> None:
    from mini_agent.evolution.protected_files_backup import (
        _backup_root, _snapshot_manifest, restore_from_snapshot,
    )

    force = "--force" in rest
    positional = [a for a in rest if a != "--force"]

    if not positional:
        R.print_error("Usage: /agent protected restore <generation_id> [path] [--force]")
        return

    gen_id = positional[0]
    target_path = positional[1] if len(positional) > 1 else None
    gen_dir = _backup_root(paths.project_root) / gen_id

    if not gen_dir.is_dir():
        R.print_error(f"Snapshot not found: {gen_id!r}. Use /agent protected list to see options.")
        return

    manifest = sorted(_snapshot_manifest(gen_dir))
    if not manifest:
        R.print_error(f"Snapshot {gen_id!r} 没有可恢复的内容（manifest 为空）。")
        return

    if target_path and target_path not in manifest:
        R.print_error(f"{target_path!r} 不在快照 {gen_id!r} 的清单里。")
        R.print_info("使用 /agent protected list " + gen_id + " 查看该快照包含的路径。")
        return

    to_restore = [target_path] if target_path else manifest

    if not force:
        R.console.print(f"\n[yellow]即将从快照 {gen_id} 恢复 {len(to_restore)} 处路径，"
                         f"这会覆盖当前同名文件/目录（如果存在）：[/yellow]")
        for p in to_restore:
            R.console.print(f"  {p}")
        R.console.print(f"\n[dim]确认无误后加 --force 重新执行：/agent protected restore "
                         f"{gen_id}" + (f" {target_path}" if target_path else "") + " --force[/dim]\n")
        return

    summary = restore_from_snapshot(paths.project_root, gen_id, paths=to_restore)
    if summary.restored:
        R.print_success(f"已恢复 {len(summary.restored)} 处路径。")
    if summary.errors:
        for err in summary.errors:
            R.print_error(err)


__all__ = ["handle_protected_cmd"]
