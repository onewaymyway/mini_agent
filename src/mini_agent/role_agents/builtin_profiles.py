"""
role_agents/builtin_profiles.py — 内建判官 profile 合成

[判官接线统一 阶段六] GoalJudge / TurnJudge 原先各自硬编码触发路径，不经过
RoleAgentDispatcher 的注册表。本模块把它们"合成"为普通的 AgentProfile，
使其可以像 evaluator/coach 一样被 dispatcher 统一注册、被
`role_agent.allow`/`role_agent.block` 精细化过滤。

注意：
  - 这里合成的 profile **不**写入磁盘 `.md` 文件，纯内存对象，每次
    `RoleAgentDispatcher._discover()` 调用时按当前 cfg 重新合成（开销可
    忽略）。
  - `system_prompt` 留空：`judge_factory.spawn_judge_agent` 在
    `system_prompt` 为空时会继续 fallback 到
    `prompts/system/goal_judge.md` / `turn_judge.md`，行为与升级前完全
    一致。
  - 只有对应子系统（`cfg.goal_mode.enabled` / `cfg.turn_judge.enabled`）
    开启时，对应 profile 才会出现在返回列表里——这是"零迁移成本"的
    关键：未开启对应子系统的用户，`get_builtin_profiles` 根本不会合成
    出对应 profile，行为与升级前一致。
  - 磁盘上如果存在同名的自定义 profile（`.agent/agents/goal_judge.md`/
    `turn_judge.md`），dispatcher._discover() 里会优先用磁盘版本，跳过
    这里合成的内建版本（见 dispatcher.py 的覆盖规则）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mini_agent.orchestrator.agent_profiles import AgentProfile

if TYPE_CHECKING:
    from mini_agent.config import AppConfig


def get_builtin_profiles(cfg: "AppConfig") -> list["AgentProfile"]:
    """按当前配置合成内建判官 profile（goal_judge / turn_judge）。

    只有对应子系统 enabled 时才会被合成到列表里；system_prompt 留空，
    这样 judge_factory.spawn_judge_agent 会继续 fallback 到
    prompts/system/goal_judge.md / turn_judge.md（除非磁盘上存在同名
    的自定义 profile 文件，覆盖规则由调用方 dispatcher._discover() 处理）。
    """
    profiles: list["AgentProfile"] = []

    gm_cfg = cfg.goal_mode
    if gm_cfg.enabled:
        profiles.append(AgentProfile(
            name="goal_judge",
            role_type="goal_judge",
            trigger_on="goal_review",
            model=gm_cfg.judge_model,
            provider=gm_cfg.judge_provider,
            tools=list(gm_cfg.judge_allowed_tools) if gm_cfg.judge_tools_enabled else [],
            tool_groups=list(gm_cfg.judge_allowed_tool_groups) if gm_cfg.judge_tools_enabled else [],
        ))

    tj_cfg = cfg.turn_judge
    if tj_cfg.enabled:
        profiles.append(AgentProfile(
            name="turn_judge",
            role_type="turn_judge",
            trigger_on="turn_end_review",
            model=tj_cfg.judge_model,
            provider=tj_cfg.judge_provider,
        ))

    return profiles
