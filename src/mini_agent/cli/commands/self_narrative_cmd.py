"""
cli/commands/self_narrative_cmd.py — /self_narrative 命令处理

子命令：
  /self_narrative          — 展示最近一条自我叙事（读 self_narrative_log.jsonl 最后一条）
  /self_narrative history  — 展示最近多条叙事日志（追加式存档，不覆盖旧版本）
  /self_narrative update   — 触发一次生成（依赖 LLM，见 evolution/self_narrative.py）

对应 next_doc/self_awareness_identity_evolution_plan.md §2.2（阶段二）。
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


def handle_self_narrative_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    from mini_agent.evolution.self_narrative import (
        generate_self_narrative,
        load_self_narrative_history,
    )

    if args and args[0] == "update":
        llm_helper = getattr(agent, "llm_helper", None) if agent else None
        if llm_helper is None:
            R.print_warning("自我叙事生成需要 LLM 辅助，当前 agent 未提供 llm_helper，跳过。")
            return
        entry = generate_self_narrative(paths, llm_helper=llm_helper)
        if entry is None:
            R.print_info("现有自我认知数据不足以支撑一段有内容的叙事，本轮未生成。")
            return
        R.print_info(f"已追加一条自我叙事：{paths.self_narrative_log_path}")
        R.console.print(entry["narrative"])
        return

    if args and args[0] == "history":
        history = load_self_narrative_history(paths, limit=20)
        if not history:
            R.print_info("还没有自我叙事记录。")
            return
        for entry in history:
            ts = entry.get("at", 0.0)
            import time as _time
            date_str = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(ts)) if ts else "?"
            R.console.print(f"[{date_str}] {entry.get('narrative', '')}")
        return

    history = load_self_narrative_history(paths, limit=1)
    if not history:
        R.print_info("还没有自我叙事，执行 /self_narrative update 生成一次（需要足够的自我认知数据积累）。")
        return
    R.console.print(history[0].get("narrative", ""))
