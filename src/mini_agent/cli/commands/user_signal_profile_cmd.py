"""
cli/commands/user_signal_profile_cmd.py — /user_signal_profile 命令处理

子命令：
  /user_signal_profile                    — 展示当前 values/risk_preference/constraints
  /user_signal_profile update             — 触发一次 values/risk_preference 归纳（依赖 LLM）
  /user_signal_profile constraint add <text>    — 显式记录一条用户约束（source=user_stated）
  /user_signal_profile constraint remove <text> — 移除一条约束（按文本归一化匹配）
  /user_signal_profile constraint list          — 只列出 constraints

对应 next_doc/personal_ai_alignment_upgrade_plan.md 阶段一。与
`/agent_value_profile`（cli/commands/agent_value_profile_cmd.py）是姊妹
命令：那边归纳的是 agent 自己的历史选择行为，这里归纳/记录的是用户侧的
Personal Model（我是谁、我看重什么、我的边界是什么）。
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


def _print_evidence_items(title: str, items: list[dict]) -> None:
    R.console.print(f"[bold]{title}[/bold]")
    if not items:
        R.console.print("  （暂无）")
        return
    for it in items:
        badge = "【推测】" if it.get("source") == "ai_inference" else (
            "【观察】" if it.get("source") == "ai_observation" else "【用户明确表示】"
        )
        conf = it.get("confidence")
        conf_text = f"（置信度 {conf:.2f}）" if isinstance(conf, (int, float)) else ""
        R.console.print(f"  - {badge} {it.get('text', '')} {conf_text}")


def handle_user_signal_profile_cmd(args: list[str], agent=None) -> None:
    paths = _get_paths(agent)
    if paths is None:
        R.print_error("Cannot access project paths (agent not initialized).")
        return

    from mini_agent.profile import UserProfileManager

    manager = UserProfileManager(paths)

    if args and args[0] == "update":
        from mini_agent.evolution.user_signal_profile_builder import generate_user_signal_profile

        llm_helper = getattr(agent, "llm_helper", None) if agent else None
        if llm_helper is None:
            R.print_warning(
                "用户侧 values/risk_preference 归纳需要 LLM 辅助，当前 agent 未提供 llm_helper，跳过。"
            )
            return
        result = generate_user_signal_profile(paths, llm_helper=llm_helper)
        if result is None:
            R.print_info("采纳/拒绝历史记录不足，或本轮未归纳出满足证据数量要求的模式，未更新。")
            return
        R.print_info("已更新用户侧 values/risk_preference。")
        return

    if args and args[0] == "constraint":
        sub_args = args[1:]
        if not sub_args or sub_args[0] == "list":
            _print_evidence_items("用户约束（constraints）", manager.list_constraints())
            return
        if sub_args[0] == "add":
            text = " ".join(sub_args[1:]).strip()
            if not text:
                R.print_error("用法：/user_signal_profile constraint add <约束内容>")
                return
            manager.add_constraint(text)
            R.print_info(f"已记录约束：{text}")
            return
        if sub_args[0] == "remove":
            text = " ".join(sub_args[1:]).strip()
            if not text:
                R.print_error("用法：/user_signal_profile constraint remove <约束内容>")
                return
            if manager.remove_constraint(text):
                R.print_info(f"已移除约束：{text}")
            else:
                R.print_warning(f"未找到匹配的约束：{text}")
            return
        R.print_error("用法：/user_signal_profile constraint [list|add <text>|remove <text>]")
        return

    profile = manager.load()
    derived = profile.derived or {}
    _print_evidence_items("Values（决策取向）", derived.get("values") or [])
    _print_evidence_items("Risk Preference（风险偏好）", derived.get("risk_preference") or [])
    _print_evidence_items("Constraints（用户约束）", derived.get("constraints") or [])
