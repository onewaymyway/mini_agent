"""
role_agents — 多角色 Agent 协作系统

提供两类角色：
  EvaluatorAgent  对主 Agent 输出进行质检和评分，支持循环修订
  CoachAgent      在特定工具调用后提供策略建议

使用方式：
  在 .agent/agents/xxx.md 中设置 role_type 字段：
    role_type: evaluator   # 或 coach / custom
    trigger_on: output     # 或 tool_use:bash / turn_end

  初始化：
    from mini_agent.role_agents import init_role_agent_system
    role_sys = init_role_agent_system(cfg, profile_loader)

  在 run_turn 后触发：
    feedback = role_sys.trigger("output", main_output, context)
"""

from .dispatcher import RoleAgentDispatcher, init_role_agent_system, get_dispatcher

__all__ = ["RoleAgentDispatcher", "init_role_agent_system", "get_dispatcher"]
