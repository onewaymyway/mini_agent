"""
cli/commands/digest_cmd.py — /digest daily 命令处理

子命令：
  /digest daily [YYYY-MM-DD]  — 生成（或重新生成）指定日期的融合日报并展示
                                 不传日期时默认为"昨天"（与 sys:daily_digest
                                 的调度节奏一致，见 evolution/cron_scheduler.py）

对应设计方案第 4.1 节。这里只做命令路由和展示，实际生成逻辑在
evolution/daily_digest.py，保持职责分离。
"""

from __future__ import annotations

import mini_agent.ui.renderer as R


def _get_paths(agent):
    if agent is None:
        return None
    paths = getattr(agent, "_paths", None)
    if paths is not None:
        return paths
    cfg = getattr(agent, "cfg", None)
    if cfg is None:
        return None
    try:
        from mini_agent.storage.paths import AgentPaths
        return AgentPaths(cfg.project_root)
    except Exception:
        return None


def handle_digest_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    if not args or args[0] != "daily":
        R.print_info("用法：/digest daily [YYYY-MM-DD]")
        return

    day = args[1] if len(args) > 1 else None

    from mini_agent.evolution.daily_digest import generate_daily_digest

    data = generate_daily_digest(paths, day=day)
    md_path = paths.daily_report_path(data["day"])
    R.print_info(f"已生成日报：{md_path}")
    R.console.print(md_path.read_text(encoding="utf-8"))
