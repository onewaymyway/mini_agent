"""
role_agents/model_resolution.py — 角色 Agent 模型/provider 解析公共函数

背景：goal_judge / turn_judge 等"判官类"内部 Agent 都需要决定自己该用哪个
model/provider 跑，此前每处各写一份一模一样的三层优先级逻辑，属于重复代码，
且容易在新增角色时漏做（例如 ensemble 的 judge 就曾经只支持 judge_model，
没有对应的 judge_provider 覆盖）。

统一优先级（从高到低）：
  1. AgentProfile.model / .provider   —— 用户在 .agent/agents/*.md 里为该
                                          角色显式声明的模型，最具体，优先级最高
  2. 角色专属配置块的 judge_model / judge_provider（如 cfg.goal_mode.judge_model、
     cfg.turn_judge.judge_model、cfg.ensemble.judge_model）—— 该角色的默认覆盖
  3. base_cfg.model / base_cfg.llm_provider —— 主 Agent 的模型，最终兜底

用法：
    model, provider = resolve_role_model(profile, role_cfg_block, base_cfg)
"""

from __future__ import annotations

from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.config import AppConfig
    from mini_agent.orchestrator.agent_profiles import AgentProfile


def resolve_role_model(
    profile: Optional["AgentProfile"],
    role_cfg_block: Any,
    base_cfg: "AppConfig",
) -> tuple[str, str]:
    """按统一三层优先级解析某个角色 Agent 应该使用的 (model, provider)。

    Args:
        profile: 该角色对应的 AgentProfile（可能为 None，或 model/provider 字段为空）
        role_cfg_block: 角色专属配置块（如 cfg.goal_mode / cfg.turn_judge / cfg.ensemble），
                         需具有 judge_model / judge_provider 属性（缺失时按 None 处理，
                         不强制要求两个字段都存在，兼容 ensemble 这类历史上只有
                         judge_model 的配置块）。
        base_cfg: 主 AppConfig，提供最终兜底的 model / llm_provider。

    Returns:
        (model, provider) 二元组，均为非空字符串。
    """
    profile_model = getattr(profile, "model", None) if profile is not None else None
    profile_provider = getattr(profile, "provider", None) if profile is not None else None

    role_model = getattr(role_cfg_block, "judge_model", None)
    role_provider = getattr(role_cfg_block, "judge_provider", None)

    model = profile_model or role_model or base_cfg.model
    provider = profile_provider or role_provider or base_cfg.llm_provider
    return model, provider
