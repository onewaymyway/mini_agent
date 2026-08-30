"""
cli/commands/agent_value_profile_cmd.py — /agent_value_profile 命令处理

子命令：
  /agent_value_profile         — 展示当前 agent 自身价值观（读 wiki/agent_value_profile.md）
  /agent_value_profile update  — 触发一次归纳（依赖 LLM，见
                                  evolution/agent_value_profile_builder.py）

对应 next_doc/self_awareness_identity_evolution_plan.md §2.1（阶段一）。
与 `/decision_profile`（cli/commands/profile_cmd.py）是姊妹命令：那边归纳
的是用户的决策画像，这里归纳的是 agent 自己的历史选择行为。
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


def handle_agent_value_profile_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    if args and args[0] == "update":
        from mini_agent.evolution.agent_value_profile_builder import generate_agent_value_profile

        llm_helper = getattr(agent, "llm_helper", None) if agent else None
        if llm_helper is None:
            R.print_warning(
                "Agent 自身价值观归纳需要 LLM 辅助，当前 agent 未提供 llm_helper，跳过。"
            )
            return
        cfg = getattr(agent, "cfg", None) if agent else None
        digest_advisor_cfg = getattr(cfg, "digest_advisor", None) if cfg is not None else None
        min_evidence_count = (
            digest_advisor_cfg.agent_value_profile_min_evidence_count
            if digest_advisor_cfg is not None else 3
        )
        state = generate_agent_value_profile(
            paths, llm_helper=llm_helper, min_evidence_count=min_evidence_count
        )
        if state is None:
            R.print_info("自我修改记录不足或本轮未归纳出满足证据数量要求的模式，未生成画像。")
            return
        R.print_info(f"已更新 Agent 自身价值观：{paths.agent_value_profile_path}")
        return

    p = paths.agent_value_profile_path
    if not p.exists():
        R.print_info("还没有 Agent 自身价值观画像，执行 /agent_value_profile update 生成一次（需要足够的自我修改历史记录）。")
        return
    R.console.print(p.read_text(encoding="utf-8"))
