"""
cli/commands/goals.py — /agent goals slash 命令处理（Stage 9 第六节）

子命令：
  /agent goals                     — 列出所有 active goals 和 objectives
  /agent goals add <title>         — 添加 Goal
  /agent goals obj add <title> [--goal <id>] [--thread <id>]
                                   — 添加 Objective
  /agent goals done <id>           — 标记完成
  /agent goals abandon <id>        — 标记放弃
  /agent goals progress <id> <txt> — 更新进展记录
  /agent goals status              — 显示 AutonomousLoop tick 状态
"""

from __future__ import annotations

from typing import Optional

import mini_agent.ui.renderer as R


def handle_goals_cmd(args: list[str], agent=None) -> None:
    """
    入口：处理 `/agent goals` 子命令。
    agent 参数用于获取 paths（与 /evolution 等命令风格一致）。
    """
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    from mini_agent.perception.goal_backlog import load_goal_backlog
    gb = load_goal_backlog(paths)

    if not args or args[0] in ("list", "ls"):
        _cmd_list(gb)
        return

    subcmd = args[0]
    rest = args[1:]

    if subcmd == "add":
        if not rest:
            R.print_error("Usage: /agent goals add <title> [--priority N] [--tag tag1,tag2]")
            return
        _cmd_add_goal(gb, rest)

    elif subcmd == "obj":
        if not rest or rest[0] != "add":
            R.print_error("Usage: /agent goals obj add <title> [--goal <id>] [--thread <id>]")
            return
        _cmd_add_objective(gb, rest[1:])

    elif subcmd == "done":
        if not rest:
            R.print_error("Usage: /agent goals done <id>")
            return
        _cmd_set_status(gb, rest[0], "completed")

    elif subcmd == "abandon":
        if not rest:
            R.print_error("Usage: /agent goals abandon <id>")
            return
        _cmd_abandon(gb, rest[0], paths, agent)

    elif subcmd == "reject":
        # /goals reject <id> — 拒绝 agent_derived Goal（alias: abandon + 记录 rejected）
        if not rest:
            R.print_error("Usage: /agent goals reject <id>")
            return
        _cmd_abandon(gb, rest[0], paths, agent)

    elif subcmd == "accept":
        # /goals accept <id> — 接受 agent_derived Goal（激活，提升 priority）
        if not rest:
            R.print_error("Usage: /agent goals accept <id>")
            return
        _cmd_accept(gb, rest[0])

    elif subcmd == "pause":
        if not rest:
            R.print_error("Usage: /agent goals pause <id>")
            return
        _cmd_set_status(gb, rest[0], "paused")

    elif subcmd == "progress":
        if len(rest) < 2:
            R.print_error("Usage: /agent goals progress <id> <notes>")
            return
        _cmd_progress(gb, rest[0], " ".join(rest[1:]))

    elif subcmd == "status":
        _cmd_loop_status(agent, paths)

    else:
        R.print_error(f"Unknown subcommand: {subcmd!r}")
        R.print_info("Available: list, add, obj add, done, abandon, accept, reject, pause, progress, status")


# ── 子命令实现 ─────────────────────────────────────────────────────────────────

def _cmd_list(gb) -> None:
    """列出所有 Goals 和 Objectives。"""
    goals = gb.active_goals()
    objectives = gb.active_objectives()
    all_nodes = gb.all_nodes()

    if not all_nodes:
        R.print_info("Goal Backlog 为空。用 `/agent goals add <title>` 添加第一个目标。")
        return

    # 显示 active goals
    if goals:
        R.print_info(f"📌 Goals ({len(goals)} active):")
        for g in goals:
            priority_str = f" [priority={g.priority}]" if g.priority else ""
            tags_str = f" [{', '.join(g.tags)}]" if g.tags else ""
            R.print_info(f"  {g.id}  {g.title}{priority_str}{tags_str}")
            if g.children_ids:
                R.print_info(f"    └─ {len(g.children_ids)} objectives")

    # 显示 active objectives
    if objectives:
        R.print_info(f"\n🎯 Objectives ({len(objectives)} active):")
        for o in objectives:
            parent_str = f" [goal:{o.parent_id}]" if o.parent_id else ""
            thread_str = f" [thread:{o.work_thread_ref}]" if o.work_thread_ref else ""
            R.print_info(f"  {o.id}  {o.title}{parent_str}{thread_str}")
            if o.progress_notes:
                R.print_info(f"    ├─ 进展: {o.progress_notes[:80]}")

    # 显示非 active 节点摘要
    inactive = [n for n in all_nodes if not n.is_active]
    if inactive:
        completed = [n for n in inactive if n.status == "completed"]
        abandoned = [n for n in inactive if n.status == "abandoned"]
        parts = []
        if completed:
            parts.append(f"{len(completed)} completed")
        if abandoned:
            parts.append(f"{len(abandoned)} abandoned")
        if parts:
            R.print_info(f"\n（历史：{', '.join(parts)}）")


def _cmd_add_goal(gb, args: list[str]) -> None:
    """添加 Goal：/agent goals add <title> [--priority N] [--tag tag1,tag2]"""
    import argparse
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("title", nargs="+")
    p.add_argument("--priority", type=int, default=0)
    p.add_argument("--tag", default="")
    try:
        parsed = p.parse_args(args)
    except SystemExit:
        R.print_error("Usage: /agent goals add <title> [--priority N] [--tag tag1,tag2]")
        return

    title = " ".join(parsed.title)
    tags = [t.strip() for t in parsed.tag.split(",") if t.strip()] if parsed.tag else []

    node = gb.add_goal(title, source="user", priority=parsed.priority, tags=tags)
    R.print_success(f"Goal 已添加: {node.id} — {node.title}")


def _cmd_add_objective(gb, args: list[str]) -> None:
    """添加 Objective：/agent goals obj add <title> [--goal <id>] [--thread <id>]"""
    import argparse
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("title", nargs="+")
    p.add_argument("--goal", default=None)
    p.add_argument("--thread", default=None)
    p.add_argument("--priority", type=int, default=0)
    try:
        parsed = p.parse_args(args)
    except SystemExit:
        R.print_error("Usage: /agent goals obj add <title> [--goal <id>] [--thread <id>]")
        return

    title = " ".join(parsed.title)

    # 验证 parent goal 存在
    if parsed.goal:
        parent_node = gb.get(parsed.goal)
        if not parent_node:
            R.print_error(f"Goal not found: {parsed.goal!r}")
            return
        if not parent_node.is_goal:
            R.print_error(f"{parsed.goal!r} 是 Objective，不是 Goal（objective 不能嵌套）")
            return

    node = gb.add_objective(
        title,
        parent_id=parsed.goal,
        work_thread_ref=parsed.thread,
        source="user",
        priority=parsed.priority,
    )
    parent_str = f" (under {parsed.goal})" if parsed.goal else ""
    thread_str = f" [thread:{parsed.thread}]" if parsed.thread else ""
    R.print_success(f"Objective 已添加: {node.id} — {node.title}{parent_str}{thread_str}")


def _cmd_set_status(gb, node_id: str, status: str) -> None:
    """更新节点状态。"""
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    old_status = node.status
    gb.set_status(node_id, status)
    emoji = {"completed": "✅", "abandoned": "🗑", "paused": "⏸"}.get(status, "")
    R.print_success(f"{emoji} {node_id} 状态: {old_status} → {status}")


def _cmd_accept(gb, node_id: str) -> None:
    """
    接受 agent_derived Goal：激活并提升优先级到用户 Goal 默认值（50）。
    对非 agent_derived Goal 也有效（等同于把 paused Goal 重新激活）。
    """
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    if node.status == "active":
        R.print_warning(f"{node_id} 已经是 active 状态")
        return
    fields = {"status": "active"}
    if getattr(node, "source", "") == "agent_derived" and node.priority < 50:
        fields["priority"] = 50
    updated = gb.update_fields(node_id, **fields)
    if not updated:
        R.print_error(f"Not found: {node_id!r}")
        return
    R.print_success(f"✅ 已接受 Goal：{updated.title}")
    if getattr(updated, "source", "") == "agent_derived":
        R.print_info("提示：使用 /goals obj add <步骤描述> --goal " + node_id + " 为此 Goal 添加 Objective")


def _cmd_abandon(gb, node_id: str, paths=None, agent=None) -> None:
    """
    放弃/拒绝 Goal：设置 abandoned 状态，若是 agent_derived 则记录 rejected 历史。
    """
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    old_status = node.status
    gb.set_status(node_id, "abandoned")

    # agent_derived Goal 被拒绝时，通知 SoftGoalDeriver 记录 30 天去重
    if getattr(node, "source", "") == "agent_derived":
        try:
            from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver
            cfg = getattr(agent, "cfg", None) if agent else None
            if paths and cfg:
                SoftGoalDeriver(paths, cfg).record_rejected(node.title)
                R.print_success(f"🗑 已拒绝并记录：{node.title}（30 天内不再建议相同主题）")
                return
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.cli.commands.goals')
            pass

    R.print_success(f"🗑 {node_id} 状态: {old_status} → abandoned")


def _cmd_progress(gb, node_id: str, notes: str) -> None:
    """更新进展记录。"""
    node = gb.get(node_id)
    if not node:
        R.print_error(f"Not found: {node_id!r}")
        return
    gb.update_progress(node_id, notes)
    R.print_success(f"进展已更新: {node_id}")


def _cmd_loop_status(agent, paths) -> None:
    """显示 AutonomousLoop tick 状态（连接到 daemon 才有意义）。"""
    # 优先通过 HTTP API 查询 daemon status
    try:
        from mini_agent.cli.daemon import _read_daemon_info, DaemonClient
        info = _read_daemon_info(paths.workdir_dir.parent if hasattr(paths, "workdir_dir") else paths.workdir_dir)
        if not info:
            # 尝试用当前 workdir
            info = _read_daemon_info(paths.workdir_dir)
    except Exception:
        info = None

    if info:
        client = DaemonClient(info["http_port"])
        status = client.get_status()
        if status:
            R.print_info(f"🔄 AutonomousLoop 状态（daemon PID={info['pid']}）:")
            R.print_info(f"  autonomy_level  : {status.get('autonomy_level', 'unknown')}")
            last_tick = status.get("last_autonomous_tick_at")
            if last_tick:
                import time
                ago = time.time() - last_tick
                R.print_info(f"  上次 tick       : {_format_ago(ago)}前")
            R.print_info(f"  tick 次数       : {status.get('tick_count', 0)}")
            return

    # Fallback：读取 activity_digest.jsonl
    try:
        from mini_agent.evolution.resource_arbiter import read_activity_digest
        records = read_activity_digest(paths)
        if records:
            R.print_info(f"📋 activity_digest.jsonl 最近 {len(records)} 条（无 daemon 连接，显示日志）:")
            for r in records[-5:]:
                import time
                ago = time.time() - r.get("at", 0)
                R.print_info(f"  [{_format_ago(ago)}前] {r.get('type', '?')}: "
                             f"{r.get('summary', r.get('task_desc', ''))[:60]}")
        else:
            R.print_info("暂无自主活动记录（daemon 未启动或尚未执行过 tick）")
    except Exception as e:
        R.print_warning(f"无法读取 activity_digest.jsonl: {e}")


def _format_ago(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}m"
    else:
        return f"{seconds/3600:.1f}h"


def _get_paths(agent):
    """从 agent 对象获取 AgentPaths。"""
    if agent is None:
        return None
    paths = getattr(agent, "_paths", None)
    if paths is not None:
        return paths
    # fallback: 从 cfg 推断
    cfg = getattr(agent, "cfg", None)
    if cfg is None:
        return None
    try:
        from mini_agent.storage.paths import AgentPaths
        return AgentPaths(cfg.project_root)
    except Exception:
        return None
