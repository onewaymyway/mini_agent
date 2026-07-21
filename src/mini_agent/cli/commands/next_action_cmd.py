"""
cli/commands/next_action_cmd.py — /next 命令处理

子命令：
  /next                — 查看当前推荐（不重新计算）
  /next refresh        — 重新扫描候选并排序（规则层，默认不接 LLM，
                          见 evolution/next_action_advisor.py 阶段划分）

对应设计方案第 4.2 节。
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


def _render(data: dict) -> str:
    if not data or not data.get("items"):
        return "当前没有需要特别提醒的事情。"
    lines = ["当前推荐（按优先级排序）："]
    for item in data["items"]:
        lines.append(f"  {item['rank']}. [{item['kind']}] {item['title']}")
        lines.append(f"     理由：{item['reason']}")
        lines.append(f"     证据：{', '.join(item['evidence_refs'])}")
    return "\n".join(lines)


def handle_next_action_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    from mini_agent.evolution.next_action_advisor import (
        generate_next_actions,
        load_pending_next_actions,
    )
    import json

    if args and args[0] == "refresh":
        data = generate_next_actions(paths)
        if data is None:
            R.print_info("没有发现值得提醒的停滞目标或注意力错配，跳过本次生成。")
            return
        R.console.print(_render(data))
        return

    p = paths.next_actions_path
    if not p.exists():
        R.print_info("还没有推荐记录，执行 /next refresh 生成一次。")
        return
    data = json.loads(p.read_text(encoding="utf-8"))
    R.console.print(_render(data))
