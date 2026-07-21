"""
cli/commands/profile_cmd.py — /profile 命令处理

子命令：
  /profile           — 展示当前决策画像（读 wiki/user_value_profile.md）
  /profile update     — 触发一次归纳（依赖 LLM，见 evolution/decision_profile_builder.py）

对应设计方案第 4.4 节（阶段三，默认 cron job 关闭，需用户主动 /cron enable
sys:decision_profile_update 或手动执行本命令）。
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


def handle_profile_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    if args and args[0] == "update":
        from mini_agent.evolution.decision_profile_builder import generate_decision_profile

        llm_helper = getattr(agent, "_llm_helper", None) if agent else None
        if llm_helper is None:
            R.print_warning(
                "决策画像归纳需要 LLM 辅助，当前 agent 未提供 llm_helper，跳过。"
            )
            return
        state = generate_decision_profile(paths, llm_helper=llm_helper)
        if state is None:
            R.print_info("决策记录不足或本轮未归纳出满足证据数量要求的模式，未生成画像。")
            return
        R.print_info(f"已更新决策画像：{paths.user_value_profile_path}")
        return

    p = paths.user_value_profile_path
    if not p.exists():
        R.print_info("还没有决策画像，执行 /profile update 生成一次（需要足够的历史决策记录）。")
        return
    R.console.print(p.read_text(encoding="utf-8"))
